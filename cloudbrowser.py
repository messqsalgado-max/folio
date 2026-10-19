"""
A real Chrome on the server for the web app's Classroom features. The student signs in themselves
through a live view on their phone (screenshots out, taps and typing in); Folio's own Classroom code
(classroom.py) then drives it exactly like the Windows app's Classroom window.

Playwright's sync API must stay on one thread, so every browser call goes through a worker thread.
"""

import os
import queue
import re
import sys
import threading
import time

import classroom

PROFILE = os.environ.get("FOLIO_BROWSER_PROFILE") or os.path.join(
    os.environ.get("FOLIO_DATA") or os.path.dirname(os.path.abspath(__file__)), "browser")
VIEW = {"width": 1100, "height": 820}
SIGNIN_VIEW = {"width": 430, "height": 760}
KEY_RE = re.compile(r"^((Control|Alt|Shift)\+)*(Enter|Backspace|Tab|Escape|Delete|Home|End|PageUp|PageDown|"
                    r"Arrow(Up|Down|Left|Right)|[a-z0-9])$")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


class _Events:
    """Stands in for pywebview's window.events (closed += handler; loaded.wait())."""

    class _Hook:
        def __iadd__(self, fn):
            return self

        def wait(self, timeout=None):
            return True

    def __init__(self):
        self.closed, self.loaded = self._Hook(), self._Hook()


class Browser:
    def __init__(self):
        self.jobs = queue.Queue()
        self.thread = None
        self.ctx = None
        self.pages = {}
        self.sessions = {}
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- worker thread
    def _loop(self, ready):
        from playwright.sync_api import sync_playwright
        try:
            pw = sync_playwright().start()
            kw = dict(user_data_dir=PROFILE, viewport=VIEW, user_agent=UA, locale="en-US",
                      ignore_default_args=["--enable-automation"],
                      args=["--disable-blink-features=AutomationControlled", "--no-first-run",
                            "--no-default-browser-check", "--disable-dev-shm-usage"])
            # a visible browser (on a virtual screen on Linux) looks like a person's to Google; headless doesn't
            kw["headless"] = sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
            channel = os.environ.get("FOLIO_BROWSER_CHANNEL") or ("msedge" if os.name == "nt" else "chrome")
            try:
                self.ctx = pw.chromium.launch_persistent_context(channel=channel, **kw)
            except Exception as first:
                try:
                    self.ctx = pw.chromium.launch_persistent_context(**kw)  # Playwright's own Chromium
                except Exception:
                    raise first
            if os.name == "nt":  # testing on the PC: park the window off screen (minimized windows stop drawing)
                try:
                    s = self.ctx.new_cdp_session(self.ctx.pages[0])
                    w = s.send("Browser.getWindowForTarget")["windowId"]
                    x, y = (int(v) for v in (os.environ.get("FOLIO_BROWSER_POS") or "1940,40").split(","))
                    s.send("Browser.setWindowBounds", {"windowId": w, "bounds": {"left": x, "top": y}})
                except Exception:
                    pass
            ready.set()
        except Exception as e:
            self.error = e
            ready.set()
            return
        while True:
            fn, box, done = self.jobs.get()
            try:
                box["r"] = fn()
            except Exception as e:
                box["e"] = e
            done.set()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive() and self.ctx:
                return
            os.makedirs(PROFILE, exist_ok=True)
            self.error, ready = None, threading.Event()
            self.thread = threading.Thread(target=self._loop, args=(ready,), daemon=True)
            self.thread.start()
            ready.wait(90)
            if self.error or not self.ctx:
                self.thread = None
                raise RuntimeError(f"Couldn't start the browser: {self.error or 'timed out'}")

    def run(self, fn, timeout=60):
        self.start()
        box, done = {}, threading.Event()
        self.jobs.put((fn, box, done))
        if not done.wait(timeout):
            raise RuntimeError("The browser didn't answer.")
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def is_running(self):
        return bool(self.thread and self.thread.is_alive() and self.ctx)

    # ---------------------------------------------------------------- pages (worker thread only)
    def _page(self, name):
        p = self.pages.get(name)
        if p is None or p.is_closed():
            p = self.ctx.pages[0] if name == "main" and self.ctx.pages else self.ctx.new_page()
            self.pages[name] = p
            self.sessions[name] = self.ctx.new_cdp_session(p)
        return p

    def _cdp(self, name, method, params=None):
        self._page(name)
        return self.sessions[name].send(method, params or {})

    def window(self, name):
        return Window(self, name)

    # ---------------------------------------------------------------- the live view
    def frame(self):
        """JPEG of what the main page shows right now. Google's sign-in gets a phone-sized page so it's
        readable on the phone; everything else stays desktop-sized, like the Windows app's window."""
        def go():
            p = self._page("main")
            size = SIGNIN_VIEW if "accounts.google.com" in p.url else VIEW
            if p.viewport_size != size:
                p.set_viewport_size(size)
            return p.screenshot(type="jpeg", quality=60)
        return self.run(go, timeout=20)

    def info(self):
        def go():
            p = self._page("main")
            return {"url": p.url, "title": p.title()}
        return self.run(go, timeout=10)

    def tap(self, x, y):
        self.run(lambda: self._page("main").mouse.click(x, y))

    def scroll(self, x, y, dy):
        def go():
            p = self._page("main")
            p.mouse.move(x, y)
            p.mouse.wheel(0, dy)
        self.run(go)

    def type(self, text):
        self.run(lambda: self._cdp("main", "Input.insertText", {"text": text}))

    def key(self, key):
        """One key or shortcut from the live view, e.g. "Enter", "Shift+Tab", "Control+a"."""
        if KEY_RE.match(key or ""):
            self.run(lambda: self._page("main").keyboard.press(key))

    def nav(self, what):
        def go():
            p = self._page("main")
            if what == "back":
                p.go_back(wait_until="commit")
            elif what == "forward":
                p.go_forward(wait_until="commit")
            elif what == "reload":
                p.reload(wait_until="commit")
            elif what == "home":
                p.goto(classroom.TODO["assigned"], wait_until="commit")
        self.run(go, timeout=40)


class Window:
    """The bits of a pywebview window that classroom.py uses, backed by one Chrome tab."""

    def __init__(self, browser, name):
        self.b, self.name, self.events = browser, name, _Events()

    def load_url(self, url):
        def go():
            try:
                self.b._page(self.name).goto(url, wait_until="commit", timeout=30000)
            except Exception:
                pass
        self.b.run(go, timeout=40)

    def get_current_url(self):
        return self.b.run(lambda: self.b._page(self.name).url, timeout=10)

    def evaluate_js(self, js):
        def go():
            r = self.b._cdp(self.name, "Runtime.evaluate",
                            {"expression": js, "returnByValue": True, "awaitPromise": True})
            return (r.get("result") or {}).get("value")
        return self.b.run(go, timeout=30)

    def cdp(self, method, params=None):
        return self.b.run(lambda: self.b._cdp(self.name, method, params), timeout=30)

    def show(self):
        pass

    def restore(self):
        pass


class CloudClassroom(classroom.Classroom):
    """classroom.Classroom, with the server's Chrome in place of pywebview windows."""

    def __init__(self, browser=None, on_closed=None):
        super().__init__(on_closed=on_closed)
        self.browser = browser or Browser()

    def open(self, x=None, y=None):
        first = not self.browser.is_running()
        self.browser.start()
        self.win = self.browser.window("main")
        if first or (self.win.get_current_url() or "about:blank").startswith("about:"):
            self.win.load_url(classroom.TODO["assigned"])

    def is_open(self):
        return self.browser.is_running() and self.win is not None

    def _reader(self):
        if self.reader is None:
            self.reader = self.browser.window("reader")
        return self.reader

    def show(self, href):
        self._need()
        self.win.load_url(href)
