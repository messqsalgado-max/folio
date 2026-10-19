"""
Folio web app: the same Folio (engine, quality check, slides, rehearse...) served to a phone browser.

Run locally:   py -3.12 webapp.py            (then open http://localhost:7860)
Hosted:        see web/SPACE_README.md (Render free web service, or any Docker host)

Settings come from environment variables (secrets on the host):
  APP_PASSWORD       required: the password to open the app
  GEMINI_API_KEY     and/or ANTHROPIC_API_KEY: the AI key(s), kept on the server
  SECRET_KEY         optional: signs the login cookie (random each start if unset)
  FOLIO_DATA         optional: where projects are stored (default ./web_data)
"""

import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import types

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

OUT = os.path.join(DATA, "Folio Projects")
Folio.DEFAULT_OUT = OUT
SERVER_KEY = "(saved on the server)"
_load = Folio.load_settings


def load_settings():
    """Folio's settings, with the AI keys taken from the host's secrets."""
    s = _load()
    s["out_dir"] = OUT
    if os.environ.get("GEMINI_API_KEY"):
        s["gemini_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        s["api_key"] = os.environ["ANTHROPIC_API_KEY"]
        if not os.environ.get("GEMINI_API_KEY") and not _load().get("provider_chosen"):
            s["provider"] = "Claude"
    return s


Folio.load_settings = load_settings


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
    """Folio's own Api, minus the desktop-only parts, with keys never sent to the browser."""

    def __init__(self):
        super().__init__()
        self._window = EVENTS

    def get_settings(self):
        s = load_settings()
        for k, env in (("gemini_key", "GEMINI_API_KEY"), ("api_key", "ANTHROPIC_API_KEY")):
            if os.environ.get(env):
                s[k] = SERVER_KEY
        s["web"] = True
        return s

    def save_settings(self, data):
        data = {k: v for k, v in (data or {}).items() if v != SERVER_KEY and k != "out_dir"}
        if "provider" in data:
            data["provider_chosen"] = True
        return Folio.Api.save_settings(self, data) and self.get_settings()


EVENTS = Events()
API = WebApi()
ALLOWED = {
    "get_settings", "save_settings", "generate", "cancel", "revise", "undo", "restyle", "recheck",
    "check_sources", "list_projects", "load_project", "thumbs", "edit_slide", "photo_options", "set_photo",
    "quiz", "home", "personal_qs", "open_url",
}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=any(os.environ.get(k) for k in ("SPACE_ID", "RENDER", "FOLIO_HTTPS")),
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, MAX_CONTENT_LENGTH=25 * 1024 * 1024)


def _password():
    return os.environ.get("APP_PASSWORD") or ""


@app.before_request
def gate():
    if request.path in ("/login", "/manifest.json", "/healthz") or request.path.startswith("/icon"):
        return None
    if not _password():
        return Response("Set the APP_PASSWORD secret on the server to use Folio.", 503, mimetype="text/plain")
    if not session.get("ok"):
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
    html = re.sub(r"(?m)^<body>", '<body class="web">', html, count=1)  # the real tag, not a mention in a comment
    with open(_asset("bridge.js"), encoding="utf-8") as f:
        bridge = f.read()
    i = html.rindex("<script>")  # the bridge must exist before Folio's own script runs
    return html[:i] + "<script>" + bridge + "</script>\n" + html[i:]


@app.route("/")
def index():
    return Response(_page(), mimetype="text/html")


@app.post("/api/call/<name>")
def call(name):
    if name not in ALLOWED:
        return jsonify({"error": "That only works in the Windows app."})
    args = request.get_json(silent=True) or []
    try:
        return jsonify(getattr(API, name)(*args))
    except Exception as e:
        return jsonify({"error": str(e)[:300]})


@app.get("/api/events")
def events():
    items, n = EVENTS.since(int(request.args.get("since", 0)))
    return jsonify({"events": items, "next": n, "busy": API.busy})


@app.get("/api/file")
def file():
    path = os.path.abspath(request.args.get("path", ""))
    if not path.startswith(os.path.abspath(OUT) + os.sep) or not os.path.isfile(path):
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
