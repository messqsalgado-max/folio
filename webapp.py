"""
Folio web app: the same Folio (engine, quality check, slides, rehearse...) served to a phone browser.

Run locally:   py -3.12 webapp.py            (then open http://localhost:7860)
Hosted:        see web/SPACE_README.md (Render free web service, or any Docker host)

Host secrets and per-connection settings:
  APP_PASSWORD       required: the password to open the app
  SECRET_KEY         optional: signs the login cookie (random each start if unset)
  FOLIO_DATA         optional: where projects are stored (default ./web_data)
  API keys            entered per signed-in browser connection in Folio Settings
  FOLIO_CLASSROOM    1 = run a real Chrome on the server for the Classroom features (needs Playwright;
                     the student signs in through the live view). Set up for GitHub Codespaces in .devcontainer
"""

import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sys
import threading
import time
import types
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("FOLIO_DATA") or os.path.join(ROOT, "web_data"))
os.makedirs(DATA, exist_ok=True)
os.environ["APPDATA"] = DATA  # Folio keeps its settings under APPDATA/Folio

# pywebview only exists in the Windows app; the web app never opens windows
try:
    import webview  # noqa: F401
except Exception:
    stub = types.ModuleType("webview")
    stub.FOLDER_DIALOG = stub.OPEN_DIALOG = 0
    stub.windows = []

    def _desktop_only(*a, **k):
        raise RuntimeError("That only works in the Windows app.")
    stub.create_window = stub.start = _desktop_only
    sys.modules["webview"] = stub

from flask import Flask, Response, abort, jsonify, redirect, request, send_file, session  # noqa: E402

import Folio  # noqa: E402

USERS_DIR = os.path.join(DATA, "users")
os.makedirs(USERS_DIR, exist_ok=True)
Folio.DEFAULT_OUT = os.path.join(USERS_DIR, "_default", "Folio Projects")
SAVED_KEY = "(saved for this connection)"
KEY_MARKERS = (SAVED_KEY, "(saved on the server)")  # preserve pages opened before this update
CLOUD_CR = os.environ.get("FOLIO_CLASSROOM") == "1"
# opt-in personal browser. It uses its own Chrome process and profile, separate from Classroom.
WEBTAB = CLOUD_CR and os.environ.get("FOLIO_BROWSER_TAB") == "1"

# Each device that signs in gets its own space — its own projects, settings, Classroom sign-in profile,
# and personal-browser profile — so devices never share browser sessions or work.
_ctx = threading.local()
DEFAULTS = {"provider": "Gemini", "api_key": "", "gemini_key": "", "gemini_model": "gemini-3.8-flash",
            "brave_key": "", "model": "claude-sonnet-5-5", "theme": "Midnight"}


def _uid():
    return getattr(_ctx, "uid", None) or "_default"


def _user_dir(uid=None):
    d = os.path.join(USERS_DIR, uid or _uid())
    os.makedirs(d, exist_ok=True)
    return d


def load_settings():
    """Read this signed-in connection's settings, including its own provider keys."""
    d = _user_dir()
    s = dict(DEFAULTS)
    try:
        with open(os.path.join(d, "settings.json"), encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    s["out_dir"] = os.path.join(d, "Folio Projects")
    return s


def save_settings(s):
    d = _user_dir()
    out = {k: v for k, v in dict(s).items() if k != "out_dir"}
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


class _PerUserPath:
    """A path that resolves into the current device's folder when opened, keeping Folio's caches separate."""

    def __init__(self, name):
        self.name = name

    def __fspath__(self):
        return os.path.join(_user_dir(), self.name)


Folio.load_settings = load_settings
Folio.save_settings = save_settings
Folio.CR_CACHE = _PerUserPath("classroom_cache.json")


# ---------------------------------------------------------------- events: the page polls for them
class Events:
    def __init__(self):
        self.items, self.lock = [], threading.Lock()

    def evaluate_js(self, js):  # Folio's Api "emits" by calling this, like it does with the desktop window
        prefix = "window.Folio.event("
        if js.startswith(prefix):
            with self.lock:
                self.items.append(json.loads(js[len(prefix):-1]))

    def since(self, n):
        with self.lock:
            return self.items[n:], len(self.items)


class WebApi(Folio.Api):
    """One per device, with separate browser sessions for Classroom and personal browsing."""

    def __init__(self, uid):
        self._uid = uid
        super().__init__()
        self._events = Events()
        self._window = self._events
        self._cr = {"last": 0.0, "inflight": 0, "hash": None}
        self._web = {"last": 0.0, "inflight": 0, "hash": None}
        if CLOUD_CR:
            import cloudbrowser
            self._classroom = cloudbrowser.CloudClassroom(
                profile=os.path.join(_user_dir(uid), "browser"),
                on_closed=lambda: self._emit("cr_closed"))
            if WEBTAB:
                self._personal_browser = cloudbrowser.Browser(
                    profile=os.path.join(_user_dir(uid), "personal_browser"))

    def get_settings(self):
        s = load_settings()
        for k in ("gemini_key", "api_key", "brave_key"):
            if s.get(k):
                s[k] = SAVED_KEY
        s["web"] = True
        s["cloud_cr"] = CLOUD_CR
        return s

    def save_settings(self, data):
        data = dict(data or {})
        current = load_settings()
        for k, value in data.items():
            if k == "out_dir" or value is None:
                continue
            if k in ("gemini_key", "api_key", "brave_key") and value in KEY_MARKERS:
                continue  # the page sends this marker to preserve the connection's existing key
            current[k] = value
        if "provider" in data:
            current["provider_chosen"] = True
        save_settings(current)
        return self.get_settings()


_users_lock = threading.Lock()
USERS = {}


def cur():
    """The Api for the device making this request, created the first time that device is seen."""
    with _users_lock:
        uid = _uid()
        api = USERS.get(uid)
        if api is None:
            api = USERS[uid] = WebApi(uid)
        return api


ALLOWED = {
    "get_settings", "save_settings", "generate", "cancel", "revise", "undo", "restyle", "recheck",
    "check_sources", "list_projects", "load_project", "thumbs", "edit_slide", "photo_options", "set_photo",
    "quiz", "home", "personal_qs", "open_url",
} | ({"cr_open", "cr_status", "cr_list", "cr_import", "cr_link", "cr_docs", "cr_plan", "cr_write",
      "cr_add_slides", "cr_show"} if CLOUD_CR else set())

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=any(os.environ.get(k) for k in ("SPACE_ID", "RENDER", "FOLIO_HTTPS")),
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, MAX_CONTENT_LENGTH=25 * 1024 * 1024)

# WebSocket for the Browser panel: one open pipe pushes frames and carries input — smooth, and it
# survives the Cloudflare tunnel (which buffers plain HTTP streams). Optional dependency.
try:
    from flask_sock import Sock
    sock = Sock(app)
except Exception:
    sock = None


def _password():
    return os.environ.get("APP_PASSWORD") or ""


@app.before_request
def gate():
    _ctx.uid = session.get("uid")  # which device this is (set at login); drives per-device isolation
    if request.path in ("/login", "/manifest.json", "/healthz") or request.path.startswith("/icon"):
        return None
    if not _password():
        return Response("Set the APP_PASSWORD secret on the server to use Folio.", 503, mimetype="text/plain")
    if not session.get("ok") or not _ctx.uid:
        return (jsonify({"error": "login"}), 401) if request.path.startswith("/api/") else redirect("/login")
    return None


LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Folio</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Folio">
<link rel="apple-touch-icon" href="/icon-180.png"><link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0A0C14">
<style>
:root{--ink:#EEF0F8;--muted:#9AA0B8}
*{box-sizing:border-box}html,body{margin:0;height:100%;overflow:hidden;background:#0A0C14;color:var(--ink);font:16px/1.4 -apple-system,"Segoe UI",system-ui,sans-serif}
.bg{position:fixed;inset:0;overflow:hidden;filter:blur(70px);opacity:.9}.bg i{position:absolute;border-radius:50%}
.bg .a{width:60vw;height:60vw;left:0;top:5%;background:radial-gradient(circle,#3D46FF,transparent 68%)}
.bg .b{width:55vw;height:55vw;right:0;top:30%;background:radial-gradient(circle,#C43BFF,transparent 68%)}
.bg .c{width:60vw;height:60vw;left:20%;bottom:-10%;background:radial-gradient(circle,#00B7C9,transparent 66%)}
form{position:relative;max-width:360px;margin:18vh auto 0;width:calc(100% - 32px);padding:28px 24px;border-radius:26px;background:rgba(28,32,48,.5);
  -webkit-backdrop-filter:saturate(180%) blur(22px);backdrop-filter:saturate(180%) blur(22px);border:1px solid rgba(255,255,255,.14)}
.mark{width:52px;height:52px;border-radius:16px;margin:0 auto 14px;display:grid;place-items:center;font-weight:800;font-size:24px;color:#fff;
  background:conic-gradient(from 210deg,#8C96FF,#D07BFF,#3FE0EE,#8C96FF)}
h1{text-align:center;margin:0 0 4px;font-size:26px}p{text-align:center;margin:0 0 20px;color:var(--muted);font-size:14px}
input{width:100%;padding:14px 16px;border-radius:14px;border:1px solid rgba(255,255,255,.15);background:rgba(10,12,22,.5);color:var(--ink);font-size:16px}
button{margin-top:12px;width:100%;padding:14px;border:0;border-radius:16px;font-weight:700;font-size:16px;color:#fff;
  background:linear-gradient(120deg,#8C96FF,#D07BFF 60%,#3FE0EE)}
.err{color:#FF8C84;text-align:center;margin-top:12px;font-size:14px}
</style></head><body><div class="bg"><i class="a"></i><i class="b"></i><i class="c"></i></div>
<form method="post"><div class="mark">F</div><h1>Folio</h1><p>Research studio</p>
<input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
<button>Open Folio</button>%ERR%</form></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if _password() and hmac.compare_digest(request.form.get("password", ""), _password()):
            session.permanent = True
            session["ok"] = True
            if not session.get("uid"):  # first sign-in on this device → its own private space
                session["uid"] = secrets.token_hex(8)
            return redirect("/")
        time.sleep(1.5)  # slows down password guessing
        err = '<div class="err">That password didn\'t work.</div>'
    return LOGIN.replace("%ERR%", err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


def _asset(name):
    """web/<name> in the source folder; next to webapp.py in the flat hosted copy."""
    p = os.path.join(ROOT, "web", name)
    return p if os.path.isfile(p) else os.path.join(ROOT, name)


def _page():
    with open(os.path.join(ROOT, "ui.html"), encoding="utf-8") as f:
        html = f.read()
    head = ('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="apple-mobile-web-app-capable" content="yes">'
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
            '<meta name="apple-mobile-web-app-title" content="Folio"><meta name="theme-color" content="#0A0C14">'
            '<link rel="apple-touch-icon" href="/icon-180.png"><link rel="manifest" href="/manifest.json">')
    html = html.replace("<head>", "<head>" + head, 1)
    cls = "web" + (" cr" if CLOUD_CR else "") + (" webtab" if WEBTAB else "")
    html = re.sub(r"(?m)^<body>", '<body class="%s">' % cls, html, count=1)  # the real tag, not a mention in a comment
    with open(_asset("bridge.js"), encoding="utf-8") as f:
        bridge = f.read()
    i = html.rindex("<script>")  # the bridge must exist before Folio's own script runs
    flags = "window.FOLIO_CR=%s;window.FOLIO_WEBTAB=%s;" % (
        "true" if CLOUD_CR else "false", "true" if WEBTAB else "false")
    return html[:i] + "<script>" + flags + bridge + "</script>\n" + html[i:]


@app.route("/")
def index():
    return Response(_page(), mimetype="text/html")


@app.post("/api/call/<name>")
def call(name):
    if name not in ALLOWED:
        return jsonify({"error": "That only works in the Windows app."})
    args = request.get_json(silent=True) or []
    api = cur()
    cr = name.startswith("cr_")
    if cr:
        _cr_touch(api, +1)
    try:
        return jsonify(getattr(api, name)(*args))
    except Exception as e:
        return jsonify({"error": str(e)[:300]})
    finally:
        if cr:
            _cr_touch(api, -1)


@app.get("/api/events")
def events():
    api = cur()
    items, n = api._events.since(int(request.args.get("since", 0)))
    return jsonify({"events": items, "next": n, "busy": api.busy})


@app.get("/api/file")
def file():
    path = os.path.abspath(request.args.get("path", ""))
    base = os.path.abspath(os.path.join(_user_dir(), "Folio Projects")) + os.sep  # only this device's files
    if not path.startswith(base) or not os.path.isfile(path):
        abort(404)
    types_ = {".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              ".pdf": "application/pdf"}  # so iPhone offers PowerPoint / Word / Files
    return send_file(path, mimetype=types_.get(os.path.splitext(path)[1].lower()),
                     as_attachment=request.args.get("dl") == "1")


@app.post("/api/import")
def import_file():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file."})
    tmp = os.path.join(DATA, "upload_" + secrets.token_hex(6) + os.path.splitext(f.filename or "")[1].lower())
    f.save(tmp)
    try:
        txt = Folio.read_any(tmp).strip()
        return jsonify({"text": txt[:20000], "name": f.filename} if txt else {"error": "Couldn't find any text in that file."})
    except Exception as e:
        return jsonify({"error": f"Couldn't read that file: {e}"})
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- Classroom and personal-browser lifecycles
# Chrome runs only while its feature is in use and is shut down after idle time.
CR_IDLE = int(os.environ.get("FOLIO_CR_IDLE") or 360)
CR_STREAM_QUALITY = 55
_cr_lock = threading.Lock()


def _cr_touch(api, delta=0):
    with _cr_lock:
        api._cr["inflight"] += delta
        api._cr["last"] = time.time()


def _web_touch(api, delta=0):
    with _cr_lock:
        api._web["inflight"] += delta
        api._web["last"] = time.time()


def _cr_watchdog():
    while True:
        time.sleep(30)
        with _users_lock:
            apis = list(USERS.values())
        for api in apis:  # free each device's Chrome once it's been idle, so memory goes back to generating
            try:
                b = getattr(api._classroom, "browser", None)
                if not (b and b.is_running()):
                    continue
                with _cr_lock:
                    idle = api._cr["inflight"] <= 0 and (time.time() - api._cr["last"]) > CR_IDLE
                if idle and not api.busy:
                    b.stop()
            except Exception:
                pass


def _web_watchdog():
    while True:
        time.sleep(30)
        with _users_lock:
            apis = list(USERS.values())
        for api in apis:
            try:
                b = getattr(api, "_personal_browser", None)
                if not (b and b.is_running()):
                    continue
                with _cr_lock:
                    idle = api._web["inflight"] <= 0 and (time.time() - api._web["last"]) > CR_IDLE
                if idle and not api.busy:
                    b.stop()
            except Exception:
                pass


def _browser():
    api = cur()
    b = getattr(api._classroom, "browser", None)
    if not CLOUD_CR or b is None:
        abort(404)
    _cr_touch(api)
    return b


if CLOUD_CR:
    threading.Thread(target=_cr_watchdog, daemon=True).start()
if WEBTAB:
    threading.Thread(target=_web_watchdog, daemon=True).start()


@app.get("/api/cr/frame")
def cr_frame():
    api = cur()
    b = _browser()
    if not b.is_running():
        abort(409)
    try:
        img = b.frame(quality=CR_STREAM_QUALITY, cache_ttl=0)
    except RuntimeError:
        # A temporary Chromium capture stall should leave the existing image in place.
        return ("", 503, {"Retry-After": "1", "Cache-Control": "no-store"})
    h = hashlib.md5(img).hexdigest()
    if h == api._cr["hash"] and request.args.get("f") != "1":
        return ("", 204)  # nothing changed since the last frame — save the bandwidth
    api._cr["hash"] = h
    r = Response(img, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/api/cr/info")
def cr_info():
    b = _browser()
    return jsonify(b.info() if b.is_running() else {"url": "", "title": "", "off": True})


@app.post("/api/cr/input")
def cr_input():
    b = _browser()
    d = request.get_json(silent=True) or {}
    try:
        _cr_do(b, d)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


def _cr_do(b, d):
    """Apply input from the Classroom live view to its isolated browser."""
    kind = d.get("kind")
    if kind == "viewport":
        b.set_live_viewport(d.get("width", 720), d.get("height", 1280))
    elif kind == "tap":
        b.tap(float(d["x"]), float(d["y"]))
    elif kind == "scroll":
        b.scroll(float(d.get("x", 500)), float(d.get("y", 400)), float(d["dy"]))
    elif kind == "type":
        b.type(str(d.get("text", ""))[:2000])
    elif kind == "key":
        b.key(str(d.get("key", "")))
    elif kind == "nav":
        b.nav(str(d.get("what", "")))


# ---------------------------------------------------------------- general browser panel (opt-in; tab "web")
def _web_browser():
    if not WEBTAB:
        abort(404)
    api = cur()
    b = getattr(api, "_personal_browser", None)
    if b is None:
        abort(404)
    _web_touch(api)
    return api, b


@app.post("/api/web/go")
def web_go():
    api, b = _web_browser()
    url = (request.get_json(silent=True) or {}).get("url", "")
    try:
        return jsonify(b.browse(url))
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@app.get("/api/web/frame")
def web_frame():
    api, b = _web_browser()
    if not b.is_running():
        abort(409)
    try:
        img = b.frame()
    except RuntimeError:
        return ("", 503, {"Retry-After": "1", "Cache-Control": "no-store"})
    h = hashlib.md5(img).hexdigest()
    if h == api._web["hash"] and request.args.get("f") != "1":
        return ("", 204)
    api._web["hash"] = h
    r = Response(img, mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/api/web/stream")
def web_stream():
    """A continuous MJPEG stream — far smoother than polling one frame at a time. Only changed frames
    are pushed. Frame capture backs off while the page is still and speeds up again when it changes."""
    api, b = _web_browser()

    def gen():
        last = None
        delay = 0.015
        while True:
            try:
                img = b.shot()  # starts the independent personal-browser process if needed
            except Exception:
                break
            if img and img != last:
                last = img
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(img)).encode() + b"\r\n\r\n" + img + b"\r\n")
                delay = 0.015
            else:
                delay = min(delay * 1.5, 0.12)
            _web_touch(api)
            time.sleep(delay)
    r = Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/api/web/info")
def web_info():
    api, b = _web_browser()
    return jsonify(b.info() if b.is_running() else {"url": "", "title": "", "off": True})


def _web_do(b, d):
    """Apply input to the separate personal-browser session."""
    kind = d.get("kind")
    if kind == "viewport":
        b.set_live_viewport(d.get("width", 1100), d.get("height", 820))
    elif kind == "tap":
        b.tap(float(d["x"]), float(d["y"]))
    elif kind == "scroll":
        b.scroll(float(d.get("x", 500)), float(d.get("y", 400)), float(d["dy"]))
    elif kind == "type":
        b.type(str(d.get("text", ""))[:2000])
    elif kind == "key":
        b.key(str(d.get("key", "")))
    elif kind == "nav":
        b.nav(str(d.get("what", "")))
    elif kind == "go":
        b.browse(str(d.get("url", "")))


@app.post("/api/web/input")
def web_input():
    api, b = _web_browser()
    try:
        _web_do(b, request.get_json(silent=True) or {})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


if sock is not None:
    # Ephemeral multiplayer state: the site lobby is independent from game rooms.
    _social_lock = threading.RLock()
    _chat_clients = {}
    _chat_history = []
    _game_rooms = {}

    def _social_send(ws, payload):
        try:
            ws.send(json.dumps(payload, separators=(",", ":")))
            return True
        except Exception:
            return False

    def _game_snapshot(room):
        return {"type": "state", "game": room["game"], "code": room["code"],
                "players": [room["players"].get(i, {}).get("name", "Waiting…") for i in (0, 1)],
                "board": room["board"], "turn": room["turn"], "winner": room["winner"], "draw": room["draw"],
                "notice": room.get("notice", "")}

    def _game_broadcast(room):
        state = _game_snapshot(room)
        for seat, player in list(room["players"].items()):
            if player.get("ws"):
                _social_send(player["ws"], dict(state, seat=seat))

    def _new_game_room(game):
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
        while code in _game_rooms:
            code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
        board = [""] * 9 if game == "tictactoe" else ([""] * 64 if game == "reversi" else [""] * 42)
        if game == "reversi":
            board[27], board[28], board[35], board[36] = "W", "B", "B", "W"
        room = {"code": code, "game": game, "board": board, "turn": 0, "winner": "", "draw": False,
                "players": {}, "notice": ""}
        _game_rooms[code] = room
        return room

    def _leave_game_room(room, seat):
        if room is None or seat is None:
            return
        room["players"].pop(seat, None)
        if not room["players"]:
            _game_rooms.pop(room["code"], None)
        else:
            _game_broadcast(room)

    def _apply_game_move(room, seat, move):
        if seat != room["turn"] or room["winner"] or room["draw"] or len(room["players"]) < 2:
            return False
        board = room["board"]
        room["notice"] = ""
        if room["game"] == "tictactoe":
            if not isinstance(move, int) or not 0 <= move < 9 or board[move]:
                return False
            board[move] = "X" if seat == 0 else "O"
            lines = ((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
            if any(board[a] and board[a] == board[b] == board[c] for a,b,c in lines):
                room["winner"] = board[move]
        elif room["game"] == "connect4":
            if not isinstance(move, int) or not 0 <= move < 7:
                return False
            idx = next((r * 7 + move for r in range(5, -1, -1) if not board[r * 7 + move]), None)
            if idx is None:
                return False
            token = "R" if seat == 0 else "Y"
            board[idx] = token
            row, col = divmod(idx, 7)
            for dr, dc in ((0,1),(1,0),(1,1),(1,-1)):
                count = 1
                for sign in (-1, 1):
                    rr, cc = row + dr * sign, col + dc * sign
                    while 0 <= rr < 6 and 0 <= cc < 7 and board[rr*7+cc] == token:
                        count += 1; rr += dr * sign; cc += dc * sign
                if count >= 4:
                    room["winner"] = token
                    break
        else:  # Reversi: verify the move brackets opponent discs in at least one direction.
            if not isinstance(move, int) or not 0 <= move < 64 or board[move]:
                return False
            token, opponent = ("B", "W") if seat == 0 else ("W", "B")
            row, col = divmod(move, 8)
            flips = []
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if not (dr or dc):
                        continue
                    rr, cc, ray = row + dr, col + dc, []
                    while 0 <= rr < 8 and 0 <= cc < 8 and board[rr * 8 + cc] == opponent:
                        ray.append(rr * 8 + cc)
                        rr += dr; cc += dc
                    if ray and 0 <= rr < 8 and 0 <= cc < 8 and board[rr * 8 + cc] == token:
                        flips.extend(ray)
            if not flips:
                return False
            board[move] = token
            for idx in flips:
                board[idx] = token

            def has_move(color):
                for index, value in enumerate(board):
                    if value:
                        continue
                    r, c = divmod(index, 8)
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            if not (dr or dc):
                                continue
                            rr, cc = r + dr, c + dc
                            if not (0 <= rr < 8 and 0 <= cc < 8) or board[rr * 8 + cc] != ("W" if color == "B" else "B"):
                                continue
                            while 0 <= rr < 8 and 0 <= cc < 8 and board[rr * 8 + cc] == ("W" if color == "B" else "B"):
                                rr += dr; cc += dc
                            if 0 <= rr < 8 and 0 <= cc < 8 and board[rr * 8 + cc] == color:
                                return True
                return False

            next_seat = 1 - seat
            next_color = "B" if next_seat == 0 else "W"
            if has_move(next_color):
                room["turn"] = next_seat
            elif has_move(token):
                room["turn"] = seat
                room["notice"] = "Opponent has no legal move. Play again."
            else:
                black, white = board.count("B"), board.count("W")
                room["draw"] = black == white
                room["winner"] = "Black" if black > white else "White" if white > black else ""
            return True
        if not room["winner"] and all(board):
            room["draw"] = True
        room["turn"] = 1 - seat
        return True

    @sock.route("/ws/cr-typewriter")
    def ws_cr_typewriter(ws):
        """Pace text into the focused field of this connection's Classroom browser only."""
        if not CLOUD_CR or not session.get("ok") or not session.get("uid"):
            return
        _ctx.uid = session["uid"]
        api = cur()
        browser = getattr(api._classroom, "browser", None)
        if browser is None or not browser.is_running():
            _social_send(ws, {"type": "error", "message": "Open Classroom and click into its answer box first."})
            return
        try:
            config = json.loads(ws.receive() or "{}")
        except (TypeError, ValueError):
            config = {}
        if not isinstance(config, dict) or config.get("type") != "start":
            _social_send(ws, {"type": "error", "message": "No text was sent."})
            return
        content = str(config.get("text", ""))[:50000]
        if not content.strip():
            _social_send(ws, {"type": "error", "message": "Add some text first."})
            return

        def allowed_target():
            try:
                host = (urlparse(browser.info().get("url", "")).hostname or "").lower()
                return host in ("classroom.google.com", "docs.google.com")
            except Exception:
                return False

        if not allowed_target():
            _social_send(ws, {"type": "error", "message": "Open Google Classroom or its Google Doc, then click the text field before typing."})
            return
        try:
            wpm = max(10, min(150, int(config.get("wpm", 52))))
            variation = max(0, min(100, int(config.get("variation", 45)))) / 100
            countdown = max(0, min(15, int(config.get("countdown", 3))))
            natural = bool(config.get("natural", True))
        except (TypeError, ValueError):
            _social_send(ws, {"type": "error", "message": "Invalid typing settings."})
            return
        profile = config.get("profile") if isinstance(config.get("profile"), dict) else {}
        def profile_ms(key, default, maximum):
            try:
                return max(0, min(maximum, int(profile.get(key, default)))) / 1000
            except (TypeError, ValueError):
                return default / 1000
        word_pause = profile_ms("word", 80, 600)
        comma_pause = profile_ms("comma", 250, 1500)
        sentence_pause = profile_ms("sentence", 700, 3000)
        line_pause = profile_ms("line", 1500, 5000)
        try:
            think_chance = max(0, min(15, int(profile.get("think", 3)))) / 100
        except (TypeError, ValueError):
            think_chance = .03

        active, stopped = threading.Event(), threading.Event()
        active.set()
        def read_controls():
            try:
                while not stopped.is_set():
                    raw = ws.receive()
                    if raw is None:
                        stopped.set(); active.set(); return
                    try:
                        action = json.loads(raw).get("type")
                    except (TypeError, ValueError, AttributeError):
                        continue
                    if action == "pause": active.clear()
                    elif action == "resume": active.set()
                    elif action == "stop": stopped.set(); active.set(); return
            except Exception:
                stopped.set(); active.set()

        def wait_while_active(seconds):
            left = seconds
            while left > 0 and not stopped.is_set():
                if not active.is_set():
                    if not paused_reported[0]:
                        _social_send(ws, {"type": "status", "status": "paused"})
                        paused_reported[0] = True
                    time.sleep(.05)
                    continue
                if paused_reported[0]:
                    _social_send(ws, {"type": "status", "status": "resumed"})
                    paused_reported[0] = False
                step = min(.05, left)
                time.sleep(step)
                left -= step

        _cr_touch(api, +1)
        threading.Thread(target=read_controls, daemon=True).start()
        index, paused_reported, last_domain_check = 0, [False], 0
        check_every = max(1, wpm * 5 // 30)
        progress_every = max(1, len(content) // 100)
        try:
            for second in range(countdown, 0, -1):
                if stopped.is_set(): break
                _social_send(ws, {"type": "status", "status": "countdown", "seconds": second})
                wait_while_active(1)
            for index, char in enumerate(content, start=1):
                if stopped.is_set(): break
                while not active.is_set() and not stopped.is_set():
                    wait_while_active(.05)
                if stopped.is_set(): break
                if index == 1 or index - last_domain_check >= check_every:
                    if not allowed_target():
                        raise RuntimeError("Stopped because the Classroom browser left Google Classroom or Docs.")
                    last_domain_check = index
                if char == "\n": browser.key("Enter")
                elif char == "\t": browser.key("Tab")
                elif char != "\r": browser.type(char)
                if index % progress_every == 0 or index == len(content):
                    _social_send(ws, {"type": "status", "status": "typing", "index": index, "total": len(content)})
                delay = 12 / wpm
                if natural:
                    delay = max(delay * .25, delay * (1 + random.uniform(-1, 1) * variation * .8))
                    if char == " ": delay += random.uniform(.6, 1.4) * word_pause
                    if char in ".!?": delay += random.uniform(.6, 1.4) * sentence_pause
                    elif char in ",;:": delay += random.uniform(.6, 1.4) * comma_pause
                    if char == "\n": delay += random.uniform(.6, 1.4) * line_pause
                    if char == " " and random.random() < think_chance: delay += random.uniform(.6, 2.2)
                wait_while_active(delay)
            if stopped.is_set():
                _social_send(ws, {"type": "stopped", "index": index, "total": len(content)})
            else:
                _social_send(ws, {"type": "done", "index": len(content), "total": len(content)})
        except Exception as e:
            _social_send(ws, {"type": "error", "message": str(e)[:200]})
        finally:
            stopped.set(); active.set(); _cr_touch(api, -1)

    @sock.route("/ws/chat")
    def ws_chat(ws):
        if not session.get("ok") or not session.get("uid"):
            return
        client_id = secrets.token_hex(8)
        last_message = 0.0
        with _social_lock:
            _chat_clients[client_id] = ws
            _social_send(ws, {"type": "history", "messages": _chat_history[-50:]})
            presence = {"type": "presence", "count": len(_chat_clients)}
            for client in list(_chat_clients.values()):
                _social_send(client, presence)
        try:
            while True:
                raw = ws.receive()
                if raw is None:
                    break
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if data.get("type") != "message":
                    continue
                name = re.sub(r"[^\w .-]", "", str(data.get("name", "Guest")), flags=re.UNICODE).strip()[:24] or "Guest"
                text = str(data.get("text", "")).strip()[:400]
                now = time.monotonic()
                if not text or now - last_message < 0.5:
                    continue
                last_message = now
                item = {"type": "message", "name": name, "text": text, "time": time.strftime("%H:%M")}
                with _social_lock:
                    _chat_history.append(item)
                    del _chat_history[:-50]
                    for cid, client in list(_chat_clients.items()):
                        if not _social_send(client, item):
                            _chat_clients.pop(cid, None)
        finally:
            with _social_lock:
                _chat_clients.pop(client_id, None)
                presence = {"type": "presence", "count": len(_chat_clients)}
                for client in list(_chat_clients.values()):
                    _social_send(client, presence)

    @sock.route("/ws/games")
    def ws_games(ws):
        if not session.get("ok") or not session.get("uid"):
            return
        room = None
        seat = None
        try:
            while True:
                raw = ws.receive()
                if raw is None:
                    break
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                action = data.get("type")
                if action == "create":
                    game = data.get("game")
                    if game not in ("tictactoe", "connect4", "reversi"):
                        _social_send(ws, {"type": "error", "message": "Choose a supported game."}); continue
                    with _social_lock:
                        if room:
                            _leave_game_room(room, seat)
                        room = _new_game_room(game); seat = 0
                        name = re.sub(r"[^\w .-]", "", str(data.get("name", "Player 1")), flags=re.UNICODE).strip()[:24] or "Player 1"
                        room["players"][seat] = {"ws": ws, "name": name}
                        _game_broadcast(room)
                elif action == "join":
                    code = re.sub(r"[^A-Z0-9]", "", str(data.get("code", "")).upper())[:5]
                    name = re.sub(r"[^\w .-]", "", str(data.get("name", "Player 2")), flags=re.UNICODE).strip()[:24] or "Player 2"
                    with _social_lock:
                        target = _game_rooms.get(code)
                        if not target:
                            _social_send(ws, {"type": "error", "message": "Room not found."}); continue
                        if target["game"] != data.get("game"):
                            _social_send(ws, {"type": "error", "message": "That room is for another game."}); continue
                        if len(target["players"]) >= 2 and ws not in [p.get("ws") for p in target["players"].values()]:
                            _social_send(ws, {"type": "error", "message": "That room is full."}); continue
                        if room is not None and room is not target:
                            _leave_game_room(room, seat)
                        room = target
                        existing = next((i for i,p in room["players"].items() if p.get("ws") is ws), None)
                        seat = existing if existing is not None else next(i for i in (0, 1) if i not in room["players"])
                        room["players"][seat] = {"ws": ws, "name": name}
                        _game_broadcast(room)
                elif action == "move" and room is not None and seat is not None:
                    move = data.get("move")
                    with _social_lock:
                        if _apply_game_move(room, seat, move):
                            _game_broadcast(room)
                elif action == "reset" and room is not None and seat is not None:
                    with _social_lock:
                        if len(room["players"]) == 2:
                            game = room["game"]
                            room["board"] = [""] * 9 if game == "tictactoe" else ([""] * 64 if game == "reversi" else [""] * 42)
                            if game == "reversi":
                                room["board"][27], room["board"][28], room["board"][35], room["board"][36] = "W", "B", "B", "W"
                            room["winner"] = ""; room["draw"] = False; room["turn"] = 1 - room["turn"]; room["notice"] = ""
                            _game_broadcast(room)
        finally:
            if room is not None and seat is not None:
                with _social_lock:
                    _leave_game_room(room, seat)

    @sock.route("/ws/cr")
    def ws_cr(ws):
        # Stream Classroom sign-in frames over one persistent connection instead of HTTP polling.
        if not CLOUD_CR or not session.get("ok") or not session.get("uid"):
            return
        _ctx.uid = session["uid"]
        api = cur()
        b = getattr(api._classroom, "browser", None)
        if b is None:
            return
        stop = threading.Event()
        _cr_touch(api, +1)

        def reader():
            while not stop.is_set():
                try:
                    msg = ws.receive()
                    if msg is None:
                        break
                    _cr_do(b, json.loads(msg))
                except Exception:
                    break
            stop.set()

        threading.Thread(target=reader, daemon=True).start()
        last, last_url, last_title, last_info = None, "", "", 0.0
        frame_interval = 1 / 60  # target 60; capture and transport time set the actual rate
        delay = 0.01
        try:
            while not stop.is_set():
                frame_started = time.perf_counter()
                img = b.shot(quality=CR_STREAM_QUALITY)
                if img and img != last:
                    last = img
                    ws.send(img)
                    delay = max(0, frame_interval - (time.perf_counter() - frame_started))
                else:
                    delay = min(delay * 1.5, 0.12)
                if time.time() - last_info > 1:
                    last_info = time.time()
                    info = b.info()
                    if info.get("url") != last_url or info.get("title") != last_title:
                        last_url, last_title = info.get("url", ""), info.get("title", "")
                        ws.send(json.dumps({"url": last_url, "title": last_title}))
                _cr_touch(api)
                time.sleep(delay)
        except Exception:
            pass
        finally:
            stop.set()
            _cr_touch(api, -1)

    @sock.route("/ws/web")
    def ws_web(ws):
        # one socket: frames (binary) go down, input + address (JSON text) go up; survives the tunnel.
        # input is read on its own thread so sending frames is never held up waiting for a keypress.
        if not WEBTAB or not session.get("ok") or not session.get("uid"):
            return
        _ctx.uid = session["uid"]
        api = cur()
        b = getattr(api, "_personal_browser", None)
        if b is None:
            return
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    msg = ws.receive()
                    if msg is None:
                        break
                    _web_do(b, json.loads(msg))
                except Exception:
                    break
            stop.set()
        threading.Thread(target=reader, daemon=True).start()

        last, last_url, last_info = None, "", 0.0
        frame_interval = 1 / 60
        delay = 0.015
        while not stop.is_set():
            try:
                frame_started = time.perf_counter()
                img = b.shot()  # launches only the personal-browser process
                if img and img != last:
                    last = img
                    ws.send(img)
                    delay = max(0, frame_interval - (time.perf_counter() - frame_started))
                else:
                    delay = min(delay * 1.5, 0.12)
                if time.time() - last_info > 1:  # keep the address bar current
                    last_info = time.time()
                    info = b.info()
                    if info.get("url") != last_url:
                        last_url = info.get("url")
                        ws.send(json.dumps({"url": last_url, "title": info.get("title", "")}))
                _web_touch(api)
                time.sleep(delay)
            except Exception:
                break
        stop.set()


@app.get("/manifest.json")
def manifest():
    return jsonify({"name": "Folio", "short_name": "Folio", "start_url": "/", "display": "standalone",
                    "background_color": "#0A0C14", "theme_color": "#0A0C14",
                    "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]})


@app.get("/icon-<int:size>.png")
def icon(size):
    p = _asset(f"icon-{size}.png")
    return send_file(p) if os.path.isfile(p) else abort(404)


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 7860)), threaded=True)
