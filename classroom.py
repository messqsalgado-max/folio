"""
Folio <-> Google Classroom, through a real browser window inside Folio.

The student signs in once in the Classroom window (Folio never sees or types the
password). Folio then reads pages the student could open themselves: the To-do list,
an assignment's details, and its attached Google Docs/Slides. The one change it can
make is typing into a Google Doc that is already in "Your work", and only after the
student has reviewed the text and clicked Write. It never uploads or attaches new files,
never clicks Turn in and never posts comments: turning work in stays the student's click.
"""

import json
import os
import re
import time

import webview

TODO = {"missing": "https://classroom.google.com/a/missing/all",
        "assigned": "https://classroom.google.com/a/not-turned-in/all"}

# To-do rows -> [{title, course, due, href, section}]
JS_TODO = r"""
(() => {
  const out = []; let section = "";
  for (const el of document.querySelectorAll('h2, li[data-role="task-row"]')) {
    if (el.tagName === 'H2') { section = el.innerText.trim(); continue; }
    const a = el.querySelector('a[href*="/details"]');
    const ps = [...el.querySelectorAll('p')].map(p => p.innerText.trim()).filter(Boolean);
    if (!a || !ps.length) continue;
    out.push({title: ps[0], course: ps[1] || "", due: (ps[2] || "").replace(/\s+/g, " "),
              href: new URL(a.getAttribute('href'), location.href).href, section});
  }
  return JSON.stringify({url: location.href, rows: out,
                         ready: out.length > 0 || [...document.querySelectorAll('h2')].length > 0});
})()
"""

# Classroom only renders a section's rows after it's been opened, and hides the rest behind
# "View all", so the To-do reader opens each section in turn.
JS_TOGGLES = r"""
(() => [...document.querySelectorAll('h2')].map(h => {
  let b = h.parentElement;
  for (let i = 0; i < 3 && b && !b.querySelector('button[aria-expanded]'); i++) b = b.parentElement;
  return b && b.querySelector('button[aria-expanded]');
}).filter(Boolean))()
"""
JS_OPEN_SECTION = "(() => { const t = %s; const b = t[%d]; if (!b) return -1;" \
                  " if (b.getAttribute('aria-expanded') === 'false') { b.click(); return 1; } return 0; })()"
JS_VIEW_ALL = r"""
(() => { const v = [...document.querySelectorAll('button')].filter(e => /^View all$/i.test(e.innerText.trim())
           && e.getBoundingClientRect().width > 0); v.forEach(e => e.click()); return v.length; })()
"""
JS_N_SECTIONS = "(%s).length" % JS_TOGGLES.strip()

# Assignment page -> title, header text, teacher attachments, the student's own attachments, comments
JS_DETAILS = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const h1 = [...document.querySelectorAll('h1')].find(h => vis(h) && !/^Classroom\b/.test(h.innerText.trim()));
  if (!h1) return JSON.stringify({url: location.href, ready: false});
  const yw = [...document.querySelectorAll('h2')].find(h => vis(h) && h.innerText.trim() === 'Your work');
  let box = yw;
  for (let i = 0; box && i < 10 && !box.querySelector('a[aria-label^="Attachment"]'); i++) box = box.parentElement;
  const atts = [...document.querySelectorAll('a[aria-label^="Attachment"]')].filter(vis).map(a => {
    const m = (a.getAttribute('aria-label') || '').match(/^Attachment:\s*([^:]+):\s*(.*)$/) || [];
    return {kind: (m[1] || '').trim(), name: (m[2] || a.innerText).trim(), url: a.href,
            mine: !!(box && box.contains(a))};
  });
  let sec = h1; for (let i = 0; i < 6 && sec.parentElement; i++) sec = sec.parentElement;
  let head = sec.innerText.split(/\nYour work\n/)[0];
  const pcH = [...document.querySelectorAll('h2')].find(h => vis(h) && /private comment/i.test(h.innerText));
  let comments = "";
  if (pcH) comments = pcH.parentElement.parentElement.innerText
      .replace(/^.*private comments?\n/i, '').split(/\nAdd private comment/)[0]
      .split('\n').filter(l => !/^Add (private )?comment\b/i.test(l.trim())).join('\n').trim();  // button labels, not comments
  return JSON.stringify({url: location.href, ready: true, title: h1.innerText.trim(), head, atts, comments});
})()
"""

JS_TEXT = "document.body ? document.body.innerText : ''"

# Google Docs editor: 'edit' when typing is possible, 'view' when the doc is read-only
JS_DOC_STATE = r"""
(() => { if (!document.querySelector('.kix-appview-editor')) return '';
  const m = (document.querySelector('#docs-toolbar-mode-switcher') || {}).innerText || '';
  if (/View/i.test(m) || document.querySelector('.docs-ml-promotion-view-only, [aria-label="View only"]')) return 'view';
  return document.querySelector('.docs-texteventtarget-iframe') ? 'edit' : ''; })()
"""
JS_DOC_FOCUS = ("(() => { const f = document.querySelector('.docs-texteventtarget-iframe');"
                " const e = f && f.contentDocument.querySelector('[contenteditable]'); if (e) e.focus(); return !!e; })()")
JS_FIND_COUNT = (r"([...document.querySelectorAll('*')].find(e => e.children.length === 0 && "
                 r"/^\d+ of \d+$/.test((e.innerText || '').trim()) && e.getBoundingClientRect().width > 0) "
                 r"|| {}).innerText || ''")


JS_LOCK = r"""
(() => { if (document.getElementById('folio-lock')) return true;
  const d = document.createElement('div'); d.id = 'folio-lock';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(20,22,40,.18);cursor:wait;' +
    'display:flex;align-items:flex-start;justify-content:center;padding-top:14px';
  d.innerHTML = '<div style="background:rgba(20,22,40,.85);color:#fff;font:600 14px Segoe UI,sans-serif;' +
    'padding:10px 18px;border-radius:14px">Folio is typing in your doc. Please don\'t click here until it finishes.</div>';
  d.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
  document.body.appendChild(d); return true; })()
"""
JS_UNLOCK = "(() => { const d = document.getElementById('folio-lock'); if (d) d.remove(); return true; })()"


# Google Slides editor
JS_SLIDES_STATE = r"""
(() => { if (!document.querySelector('g.punch-filmstrip-thumbnail')) return '';
  const m = (document.querySelector('#docs-toolbar-mode-switcher') || {}).innerText || '';
  if (/View/i.test(m)) return 'view';
  return document.querySelector('.docs-texteventtarget-iframe') ? 'edit' : ''; })()
"""
JS_SLIDE_COUNT = "document.querySelectorAll('g.punch-filmstrip-thumbnail').length"
JS_LAST_THUMB = r"""
(() => { const t = [...document.querySelectorAll('g.punch-filmstrip-thumbnail')]; const g = t[t.length - 1];
  if (!g) return null; g.scrollIntoView({block: 'nearest'}); const r = g.getBoundingClientRect();
  return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; })()
"""
# centre of the visible empty placeholder whose prompt text matches (e.g. "Click to add title")
JS_PLACEHOLDER = r"""
(() => { const re = new RegExp(%s, 'i');
  const t = [...document.querySelectorAll('text, tspan, div')].filter(e => e.children.length === 0 &&
    re.test(e.textContent || '') && e.getBoundingClientRect().width > 0);
  if (!t.length) return null; const r = t[t.length - 1].getBoundingClientRect();
  return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]; })()
"""


def _find_text(line):
    """One clean run of a line to search for: Find needs exact spacing, and docs often hold double spaces."""
    parts = [p.strip() for p in re.split(r"\s{2,}|\n", line or "") if p.strip()]
    return (parts[0] if parts and len(parts[0]) >= 15 else max(parts, key=len, default=""))[:120]


def _cdp(win, method, params=None, timeout=30):
    """Send a DevTools command to a pywebview window through WebView2 itself (no debug port)."""
    if hasattr(win, "cdp"):  # the web app's server-side Chrome (cloudbrowser.py)
        return win.cdp(method, params)
    from threading import Semaphore
    from System import Action, Func, Object, String
    from System.Threading.Tasks import Task
    browser = win.native.browser
    out, sem = {}, Semaphore(0)

    def done(task):
        try:
            out["r"] = json.loads(task.Result)
        except Exception as e:  # the command failed inside WebView2
            out["e"] = e
        sem.release()

    browser.webview.Invoke(Func[Object](lambda: browser.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
        method, json.dumps(params or {})).ContinueWith(Action[Task[String]](done), browser.syncContextTaskScheduler)))
    if not sem.acquire(timeout=timeout):
        raise RuntimeError(f"Classroom window didn't answer ({method}).")
    if "e" in out:
        raise RuntimeError(f"{method} failed: {out['e']}")
    return out["r"]


class NotSignedIn(Exception):
    pass


class Classroom:
    def __init__(self, on_closed=None):
        self.win = None
        self.reader = None
        self.on_closed = on_closed

    # ------------------------------------------------------------ windows
    def open(self, x=None, y=None):
        if self.win is not None:
            try:
                self.win.show()
                self.win.restore()
                return
            except Exception:
                self.win = None
        self.win = webview.create_window("Classroom · Folio", TODO["assigned"], width=1100, height=820,
                                         x=x, y=y, text_select=True, zoomable=True)

        def closed():
            self.win = None
            if self.on_closed:
                self.on_closed()
        self.win.events.closed += closed

    def is_open(self):
        return self.win is not None

    def _reader(self):
        """Hidden window for reading Docs/Slides text, so the student's Classroom page stays put."""
        if self.reader is None:
            self.reader = webview.create_window("Folio reader", "about:blank", hidden=True)
            self.reader.events.loaded.wait(15)
        return self.reader

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _eval_json(win, js):
        try:
            r = win.evaluate_js(js)
            return json.loads(r) if isinstance(r, str) else r
        except Exception:
            return None

    def _goto(self, win, url, timeout=25):
        win.load_url(url)
        end = time.time() + timeout
        time.sleep(0.8)
        while time.time() < end:
            try:
                cur = win.get_current_url() or ""
                if "accounts.google.com" in cur:
                    raise NotSignedIn()
                if win.evaluate_js("document.readyState") == "complete" and cur.split("#")[0] != "about:blank":
                    return cur
            except NotSignedIn:
                raise
            except Exception:
                pass
            time.sleep(0.4)
        return win.get_current_url()

    def _need(self):
        if self.win is None:
            raise RuntimeError("Open Classroom first (button at the top of the Classroom tab).")

    # ------------------------------------------------------------ To-do
    def todo(self):
        """{'missing': [...], 'assigned': [...]} or raises NotSignedIn."""
        self._need()
        out = {}
        for key in ("missing", "assigned"):
            rows, data = [], None
            # Classroom sometimes draws a blank list on load; a fresh load usually fixes it
            for attempt in range(3):
                self._goto(self.win, TODO[key] if attempt < 2 else "https://classroom.google.com/")
                if attempt == 2:
                    time.sleep(1.5)
                    self._goto(self.win, TODO[key])
                end = time.time() + 10
                while time.time() < end:
                    data = self._eval_json(self.win, JS_TODO)
                    if data and "accounts.google.com" in data.get("url", ""):
                        raise NotSignedIn()
                    if data and data.get("ready"):
                        break
                    time.sleep(0.6)
                if data and data.get("ready"):
                    time.sleep(0.6)  # let late sections finish rendering, then read once more
                    rows = (self._eval_json(self.win, JS_TODO) or data).get("rows") or []
                    break
            if not rows and data and not data.get("ready"):
                out[key + "_blank"] = True  # Classroom sometimes renders an empty list until clicked
            elif rows:
                rows = self._all_sections(rows)
            out[key] = rows
        return out

    def _all_sections(self, rows):
        """Open every section (and its 'View all') so rows hidden in collapsed sections are read too."""
        found = {r["href"]: r for r in rows}

        def grab():
            for r in (self._eval_json(self.win, JS_TODO) or {}).get("rows") or []:
                found.setdefault(r["href"], r)
        try:
            n = int(self.win.evaluate_js(JS_N_SECTIONS) or 0)
        except Exception:
            n = 0
        for i in range(n):
            try:
                if self.win.evaluate_js(JS_OPEN_SECTION % (JS_TOGGLES.strip(), i)) == 1:
                    time.sleep(0.9)
                for _ in range(5):
                    if not self.win.evaluate_js(JS_VIEW_ALL):
                        break
                    time.sleep(1.0)
                grab()
            except Exception:
                continue
        return list(found.values())

    # ------------------------------------------------------------ one assignment
    def assignment(self, href, progress=None):
        """Everything Folio needs to start a project from one assignment."""
        self._need()
        self._goto(self.win, href)
        d = None
        end = time.time() + 15
        while time.time() < end:
            d = self._eval_json(self.win, JS_DETAILS)
            if d and d.get("ready"):
                break
            time.sleep(0.6)
        if not d or not d.get("ready"):
            raise RuntimeError("Couldn't read that assignment page. Open it in the Classroom window and try again.")
        info = _parse_head(d.get("head", ""), d["title"], [a["name"] for a in d.get("atts", [])])
        docs = []
        for a in d.get("atts", [])[:10]:
            text = ""
            url = _readable_url(a["url"])
            if url:
                if progress:
                    progress(f"Reading “{a['name']}”…")
                try:
                    self._goto(self._reader(), url)
                    time.sleep(0.5)
                    text = (self._reader().evaluate_js(JS_TEXT) or "").strip()
                except NotSignedIn:
                    raise
                except Exception:
                    text = ""
            docs.append({**a, "text": _tidy(text)[:8000], "read": bool(text)})
        return {"title": d["title"], "href": href, **info, "comments": d.get("comments", ""), "docs": docs}

    # ------------------------------------------------------------ helpers for edits
    def _wait_details(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            d = self._eval_json(self.win, JS_DETAILS)
            if d and "accounts.google.com" in d.get("url", ""):
                raise NotSignedIn()
            if d and d.get("ready"):
                return d
            time.sleep(0.6)
        raise RuntimeError("Couldn't open the assignment in the Classroom window.")

    # ------------------------------------------------------------ the student's own Docs
    def your_docs(self, href):
        """Google Docs already under Your work for this assignment (the only files Folio may edit)."""
        self._need()
        self._goto(self.win, href)
        d = self._wait_details()
        return [{"name": a["name"], "url": a["url"], "kind": a["kind"]} for a in d.get("atts", []) if a.get("mine")]

    def read_doc(self, url):
        r = self._reader()
        self._goto(r, _readable_url(url))
        time.sleep(0.5)
        return _tidy(r.evaluate_js(JS_TEXT) or "")

    def write_doc(self, url, edits, progress=None):
        """Type each edit into the Google Doc right after the line it answers.
        edits: [{"after": exact line in the doc ('' = end of doc), "text": "..."}].
        Only adds text; never deletes. Returns {"written": [...], "not_found": [...]}."""
        self._need()
        m = re.search(r"docs\.google\.com/document/d/([\w-]+)", url or "")
        if not m:
            raise RuntimeError("Folio can only write into Google Docs.")
        self._goto(self.win, f"https://docs.google.com/document/d/{m.group(1)}/edit")
        end = time.time() + 30
        while time.time() < end:
            st = self.win.evaluate_js(JS_DOC_STATE) or ""
            if st == "edit":
                break
            if st == "view":
                raise RuntimeError("This doc is view-only for you, so Folio can't write in it.")
            time.sleep(0.7)
        else:
            raise RuntimeError("The doc didn't finish opening in the Classroom window.")
        time.sleep(1.5)
        written, missing = [], []
        self.win.evaluate_js(JS_LOCK)  # cover the doc so a stray click can't move the cursor mid-typing
        try:
            for e in edits:
                text = (e.get("text") or "").strip()
                replace = bool((e.get("replace") or "").strip())
                anchor = _find_text(e.get("replace") if replace else e.get("after"))
                if not text:
                    continue
                if progress:
                    progress(f"Writing {len(written) + 1} of {len(edits)}…")
                self.win.evaluate_js(JS_DOC_FOCUS)
                if anchor:
                    if not self._find(anchor):            # leaves the match selected in the doc
                        missing.append(anchor)
                        continue
                    if not replace:
                        self._key("End", "End", 35)
                else:
                    self._key("End", "End", 35, 2)        # Ctrl+End: end of the doc
                    self._key("Enter", "Enter", 13)
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                for i, line in enumerate(lines):
                    # replacing: the first line types over the selected text. Adding: a soft line break
                    # keeps the answer inside the question's list item, so the numbering survives.
                    if i or not replace:
                        self._key("Enter", "Enter", 13, 8)  # Shift+Enter
                    _cdp(self.win, "Input.insertText", {"text": line})
                written.append(("replaced: " if replace else "") + (anchor or "(end of doc)"))
                time.sleep(0.6)
        finally:
            try:
                self.win.evaluate_js(JS_UNLOCK)
            except Exception:
                pass
        return {"written": written, "not_found": missing}

    def add_slides(self, url, slides, progress=None):
        """Add slides to the end of the student's existing Google Slides deck by typing into it.
        slides: [{"heading", "points": [...], "body", "notes"}]. Only adds; never deletes or uploads.
        Returns {"added": n, "skipped": [...]}."""
        self._need()
        m = re.search(r"docs\.google\.com/presentation/d/([\w-]+)", url or "")
        if not m:
            raise RuntimeError("That isn't a Google Slides deck.")
        self._goto(self.win, f"https://docs.google.com/presentation/d/{m.group(1)}/edit")
        end = time.time() + 30
        while time.time() < end:
            st = self.win.evaluate_js(JS_SLIDES_STATE) or ""
            if st == "edit":
                break
            if st == "view":
                raise RuntimeError("This deck is view-only for you, so Folio can't add slides to it.")
            time.sleep(0.7)
        else:
            raise RuntimeError("The deck didn't finish opening in the Classroom window.")
        time.sleep(1.5)
        added, skipped = 0, []
        for n, s in enumerate(slides, 1):
            if progress:
                progress(f"Adding slide {n} of {len(slides)}…")
            before = int(self.win.evaluate_js(JS_SLIDE_COUNT) or 0)
            box = self.win.evaluate_js(JS_LAST_THUMB)          # select the last slide, so the new one goes after it
            if not box:
                raise RuntimeError("Couldn't find the slides list in the deck.")
            self._click(*box)
            time.sleep(0.6)
            self._key("m", "KeyM", 77, 2)                      # Ctrl+M: new slide
            for _ in range(20):
                time.sleep(0.4)
                if int(self.win.evaluate_js(JS_SLIDE_COUNT) or 0) > before:
                    break
            else:
                skipped.append(s.get("heading") or f"slide {n}")
                continue
            time.sleep(1.0)
            typed = self._type_into(r"Click to add (title|subtitle)", [s.get("heading") or ""])
            lines = [p for p in (s.get("points") or []) if p.strip()]
            if s.get("body"):
                lines.append(s["body"])
            if lines:
                typed += self._type_into(r"Click to add (text|body)", lines)
            if s.get("notes"):
                self._type_into(r"speaker notes", [s["notes"]])
            self._key("Escape", "Escape", 27)
            self._key("Escape", "Escape", 27)
            if typed:
                added += 1
            else:
                skipped.append(s.get("heading") or f"slide {n}")
            time.sleep(0.6)
        return {"added": added, "skipped": skipped}

    def _type_into(self, prompt_re, lines):
        """Double-click the empty placeholder whose prompt matches, then type the lines into it."""
        box = self.win.evaluate_js(JS_PLACEHOLDER % json.dumps(prompt_re))
        if not box or not any(l.strip() for l in lines):
            return 0
        self._click(*box, count=2)
        time.sleep(0.6)
        for i, line in enumerate(l.strip() for l in lines if l.strip()):
            if i:
                self._key("Enter", "Enter", 13)
            _cdp(self.win, "Input.insertText", {"text": line})
        time.sleep(0.4)
        self._key("Escape", "Escape", 27)
        time.sleep(0.3)
        return 1

    def _click(self, x, y, count=1):
        """A click inside the Classroom window only (it doesn't move the real mouse)."""
        for c in range(1, count + 1):
            for t in ("mousePressed", "mouseReleased"):
                _cdp(self.win, "Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": c})
            time.sleep(0.08)

    def _find(self, text):
        """Select `text` in the open doc with Docs' own Find (Ctrl+F). True if it was found."""
        self._key("f", "KeyF", 70, 2)
        time.sleep(0.8)
        self._key("a", "KeyA", 65, 2)                    # replace any old search
        _cdp(self.win, "Input.insertText", {"text": text})
        time.sleep(1.0)
        self._key("Enter", "Enter", 13)
        time.sleep(0.7)
        count = self.win.evaluate_js(JS_FIND_COUNT) or ""
        self._key("Escape", "Escape", 27)
        time.sleep(0.5)
        return bool(re.match(r"[1-9]", count))

    def _key(self, key, code, vk, mods=0):
        for t in ("rawKeyDown", "keyUp"):
            _cdp(self.win, "Input.dispatchKeyEvent",
                 {"type": t, "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": mods})

    def show(self, href):
        self._need()
        self.win.load_url(href)
        self.win.show()


def _readable_url(url):
    """Plain-HTML versions of Google files that the signed-in window can read."""
    m = re.search(r"docs\.google\.com/document/d/([\w-]+)", url or "")
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/mobilebasic"
    m = re.search(r"docs\.google\.com/presentation/d/([\w-]+)", url or "")
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/htmlpresent"
    return None  # PDFs, uploads, links: listed by name only


def _tidy(text):
    text = re.sub(r"[�​-]", "", text or "").replace(" ", " ")
    text = re.sub(r"_{4,}", "____", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_head(head, title, att_names):
    skip = {"assignment", "more_vert", "More options", "•", "|", "Google Docs", "Google Slides",
            "Google Sheets", "Google Forms", "PDF", "Microsoft PowerPoint", "Microsoft Word", "Unknown",
            "Image", "YouTube", "Link", "Class comments", "Add comment", "Add class comment",
            "No class comments", title}
    lines = [re.sub(r"\s+", " ", l.replace(" ", " ")).strip() for l in (head or "").split("\n")]
    teacher = points = due = posted = ""
    desc = []
    for i, l in enumerate(lines):
        if not l or l in skip or l in att_names:
            continue
        if re.fullmatch(r"\d+ points?", l):
            points = l
        elif l.startswith("Due "):
            due = l[4:]
        elif re.match(r"^[A-Z][a-z]{2} \d{1,2}( \(Edited .*\))?$", l):
            posted = l
        elif not teacher and i + 1 < len(lines) and lines[i + 1] == "•":
            teacher = l
        else:
            desc.append(l)
    return {"teacher": teacher, "points": points, "due": due, "posted": posted,
            "description": "\n".join(desc).strip()}


def to_project_inputs(a):
    """Turn a read assignment into Folio's New project fields."""
    rub = [d for d in a["docs"] if d["text"] and re.search(r"rubric", d["name"] + d["text"][:300], re.I)]
    for d in a["docs"]:
        if d not in rub and d["text"].count("____") >= 15:  # empty worksheet: keep directions, drop blanks
            d["text"] = d["text"][:500].rsplit("\n", 1)[0] + "\n(blank worksheet, rest left out)"
    rest = [d for d in a["docs"] if d not in rub]
    head = f"{a['title']}" + (f" ({a['points']})" if a.get("points") else "") + \
           (f" — due {a['due']}" if a.get("due") else "")
    prompt = [head]
    if a.get("description"):
        prompt.append(a["description"])
    for d in rest:
        if d["text"] and not d["mine"]:
            prompt.append(f"--- From “{d['name']}” ({d['kind']}) ---\n{d['text']}")
    task = []
    mine = [d for d in rest if d["mine"] and d["text"]]
    for d in mine:
        task.append(f"--- My work so far: “{d['name']}” ---\n{d['text']}")
    if a.get("comments"):
        task.append("--- Teacher's private comments ---\n" + a["comments"])
    unread = [d["name"] for d in a["docs"] if not d["text"]]
    if unread:
        task.append("Attachments Folio couldn't read (open them yourself): " + ", ".join(unread))
    return {"prompt": "\n\n".join(prompt)[:20000],
            "rubric": "\n\n".join(d["text"] for d in rub)[:20000],
            "task": "\n\n".join(task)[:20000]}
