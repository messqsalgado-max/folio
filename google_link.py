"""
Folio <-> Google: sign in with a Google account (in the normal browser),
browse Docs / Slides / Sheets, create files, and add Folio's work to them.
"""

import base64
import json
import os
import re
import uuid

from render import cite_heading, citations

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/presentations",
]

DOC = "application/vnd.google-apps.document"
SLIDES = "application/vnd.google-apps.presentation"
SHEET = "application/vnd.google-apps.spreadsheet"
KINDS = {DOC: "doc", SLIDES: "slides", SHEET: "sheet"}

SIGN_IN_TIMEOUT = 180  # seconds
SCHOOL_HINT = ("If Google said “Access blocked” or that your admin needs to review the app, "
               "your school doesn't allow outside apps. Use “Use without signing in” instead — "
               "Folio saves the files and you drop them into Drive yourself.")


def u16(s):
    """Length in UTF-16 code units (what the Docs API counts)."""
    return len(s.encode("utf-16-le")) // 2


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


class GoogleLink:
    def __init__(self, folder):
        self.folder = folder
        self.client_file = os.path.join(folder, "google_client.json")
        self.token_file = os.path.join(folder, "google_token.json")
        self._creds = None
        self._session = None
        self.profile = None

    # ------------------------------------------------------------ auth
    def has_client(self):
        return os.path.isfile(self.client_file)

    def import_client(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "installed" not in data:
            if "web" in data:
                raise ValueError("That's a 'Web application' client. Create a 'Desktop app' client instead.")
            raise ValueError("That doesn't look like a Google OAuth client file.")
        os.makedirs(self.folder, exist_ok=True)
        with open(self.client_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load(self):
        if self._session:
            return True
        if not os.path.isfile(self.token_file):
            return False
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request, AuthorizedSession
        try:
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    self._save(creds)
                else:
                    return False
        except Exception:
            # refresh token expired (testing-mode apps expire after 7 days)
            return False
        self._creds = creds
        self._session = AuthorizedSession(creds)
        return True

    def _save(self, creds):
        os.makedirs(self.folder, exist_ok=True)
        with open(self.token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    def sign_in(self):
        """Opens Google's normal sign-in page in the default browser and waits."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import AuthorizedSession
        if not self.has_client():
            raise RuntimeError("Finish the one-time Google setup first.")
        flow = InstalledAppFlow.from_client_secrets_file(self.client_file, SCOPES)
        try:
            # School / work accounts often get stopped on Google's page ("Access blocked",
            # "admin needs to review") and never come back, so don't wait forever.
            creds = flow.run_local_server(
                port=0, open_browser=True, prompt="consent", access_type="offline",
                authorization_prompt_message="", timeout_seconds=SIGN_IN_TIMEOUT,
                success_message="You're signed in to Folio. You can close this tab and go back to the app.")
        except Exception as e:
            msg = str(e).lower()
            if "timed out" in msg or type(e).__name__ == "WSGITimeoutError":
                raise RuntimeError(SCHOOL_HINT) from e
            if "access_denied" in msg or "admin_policy" in msg or "org_internal" in msg:
                raise RuntimeError("Google didn't allow the sign-in. " + SCHOOL_HINT) from e
            raise
        self._save(creds)
        self._creds = creds
        self._session = AuthorizedSession(creds)
        self.profile = None
        return self.me()

    def sign_out(self):
        try:
            if self._creds and self._creds.token:
                import requests
                requests.post("https://oauth2.googleapis.com/revoke",
                              params={"token": self._creds.token}, timeout=10)
        except Exception:
            pass
        for f in (self.token_file,):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
        self._creds = self._session = self.profile = None

    def connected(self):
        return self._load()

    # ------------------------------------------------------------ http
    def _req(self, method, url, **kw):
        if not self._load():
            raise PermissionError("signed_out")
        r = self._session.request(method, url, timeout=60, **kw)
        if r.status_code == 401:
            raise PermissionError("signed_out")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            if "has not been used in project" in msg or "is disabled" in msg:
                api = "Drive"
                for n in ("Docs", "Slides", "Drive"):
                    if n.lower() in url:
                        api = n
                        break
                raise RuntimeError(f"The Google {api} API isn't turned on for your Folio project yet. "
                                   f"Open Settings → Google setup, step 2.")
            raise RuntimeError(f"Google error {r.status_code}: {msg[:240]}")
        return r.json() if r.content else {}

    def me(self):
        if self.profile:
            return self.profile
        d = self._req("GET", "https://www.googleapis.com/oauth2/v2/userinfo")
        self.profile = {"email": d.get("email"), "name": d.get("name") or d.get("email"),
                        "picture": d.get("picture")}
        return self.profile

    # ------------------------------------------------------------ browse
    def list_files(self, kind="all", query=""):
        mimes = {"doc": [DOC], "slides": [SLIDES], "sheet": [SHEET]}.get(kind, [DOC, SLIDES, SHEET])
        q = "(" + " or ".join(f"mimeType='{m}'" for m in mimes) + ") and trashed=false"
        if query:
            safe = query.replace("\\", "\\\\").replace("'", "\\'")
            q += f" and name contains '{safe}'"
        d = self._req("GET", "https://www.googleapis.com/drive/v3/files", params={
            "q": q, "orderBy": "modifiedTime desc", "pageSize": 60,
            "fields": "files(id,name,mimeType,modifiedTime,thumbnailLink,webViewLink,"
                      "owners(displayName,me),shared)"})
        out = []
        for f in d.get("files", []):
            out.append({"id": f["id"], "name": f.get("name", "Untitled"),
                        "kind": KINDS.get(f.get("mimeType"), "other"),
                        "modified": f.get("modifiedTime"), "thumb": f.get("thumbnailLink"),
                        "url": f.get("webViewLink"),
                        "owner": "me" if any(o.get("me") for o in f.get("owners", []))
                        else ", ".join(o.get("displayName", "") for o in f.get("owners", []))})
        return out

    def thumbnail(self, link):
        """Fetch a Drive thumbnail with the user's credentials -> data URI."""
        if not link or not self._load():
            return None
        try:
            r = self._session.get(re.sub(r"=s\d+$", "=s400", link), timeout=20)
            if r.status_code == 200 and r.content:
                ct = r.headers.get("Content-Type", "image/png").split(";")[0]
                return f"data:{ct};base64," + base64.b64encode(r.content).decode()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------ create
    def create(self, kind, title):
        if kind == "slides":
            d = self._req("POST", "https://slides.googleapis.com/v1/presentations", json={"title": title})
            fid = d["presentationId"]
            return {"id": fid, "name": title, "kind": "slides",
                    "url": f"https://docs.google.com/presentation/d/{fid}/edit"}
        d = self._req("POST", "https://docs.googleapis.com/v1/documents", json={"title": title})
        fid = d["documentId"]
        return {"id": fid, "name": title, "kind": "doc",
                "url": f"https://docs.google.com/document/d/{fid}/edit"}

    def upload_convert(self, path, name, kind):
        """Upload a .pptx/.docx and convert it to Google Slides/Docs (keeps the design)."""
        if not self._load():
            raise PermissionError("signed_out")
        target = SLIDES if kind == "slides" else DOC
        src = ("application/vnd.openxmlformats-officedocument.presentationml.presentation"
               if path.endswith(".pptx") else
               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        boundary = "folio" + uuid.uuid4().hex
        meta = json.dumps({"name": name, "mimeType": target}).encode()
        with open(path, "rb") as f:
            media = f.read()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
                f"\r\n--{boundary}\r\nContent-Type: {src}\r\n\r\n".encode() + media +
                f"\r\n--{boundary}--".encode())
        d = self._req("POST", "https://www.googleapis.com/upload/drive/v3/files",
                      params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                      data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        return {"id": d["id"], "name": d.get("name", name), "kind": kind, "url": d.get("webViewLink")}

    # ------------------------------------------------------------ read
    def read_text(self, f, limit=8000):
        """Plain text of a Doc or Slides deck, used as context for Folio."""
        try:
            if f["kind"] == "doc":
                d = self._req("GET", f"https://docs.googleapis.com/v1/documents/{f['id']}")
                parts = []
                for el in d.get("body", {}).get("content", []):
                    for pe in (el.get("paragraph") or {}).get("elements", []):
                        parts.append((pe.get("textRun") or {}).get("content", ""))
                return "".join(parts)[:limit]
            if f["kind"] == "slides":
                d = self._req("GET", f"https://slides.googleapis.com/v1/presentations/{f['id']}")
                out = []
                for i, sl in enumerate(d.get("slides", []), 1):
                    texts = []
                    for el in sl.get("pageElements", []):
                        for te in ((el.get("shape") or {}).get("text") or {}).get("textElements", []):
                            texts.append((te.get("textRun") or {}).get("content", ""))
                    out.append(f"[Slide {i}] " + clean(" ".join(texts)))
                return "\n".join(out)[:limit]
        except Exception:
            return ""
        return ""

    # ------------------------------------------------------------ append: Slides
    def _slide_text(self, s):
        lay = s.get("layout", "bullets")
        title = clean(s.get("heading"))
        lines, bullets = [], False
        if lay in ("title", "section"):
            lines = [clean(s.get("subheading") or s.get("body"))]
        elif lay == "two_column":
            for side in ("left", "right"):
                c = s.get(side) or {}
                if c.get("heading"):
                    lines.append(clean(c["heading"]))
                lines += ["• " + clean(p) for p in c.get("points") or []]
        elif lay == "big_stat":
            lines = [f"{clean(s.get('stat'))} — {clean(s.get('stat_label'))}", clean(s.get("body"))]
        elif lay == "quote":
            lines = [f"“{clean(s.get('quote') or s.get('body'))}”"]
            if s.get("attribution"):
                lines.append("— " + clean(s["attribution"]))
        elif lay == "timeline":
            for st in s.get("steps") or []:
                lines.append(f"{clean(st.get('label'))}: {clean(st.get('text'))}")
            bullets = True
        else:
            lines = [clean(p) for p in s.get("points") or []]
            if s.get("body"):
                lines.append(clean(s["body"]))
            bullets = bool(s.get("points"))
        return title, "\n".join(l for l in lines if l), bullets

    def append_slides(self, pres_id, spec):
        base = f"https://slides.googleapis.com/v1/presentations/{pres_id}"
        pres = self._req("GET", base, params={"fields": "slides(objectId),pageSize"})
        start = len(pres.get("slides", []))
        ps = pres.get("pageSize") or {}
        W = (ps.get("width") or {}).get("magnitude", 9144000)
        H = (ps.get("height") or {}).get("magnitude", 5143500)
        slides = list(spec.get("slides") or [])
        if start > 0 and slides and slides[0].get("layout") == "title":
            slides = slides[1:]  # deck already has its own title slide
        ce = spec.get("current_event") or {}
        if ce.get("summary"):
            slides.append({"layout": "bullets", "heading": "In the News" + (f" ({clean(ce.get('date'))})" if ce.get("date") else ""),
                           "body": f"{clean(ce.get('headline'))}. {clean(ce.get('summary'))} "
                                   f"Why it matters: {clean(ce.get('connection'))}"})
        srcs = citations(spec, 12)
        if srcs:
            slides.append({"layout": "sources", "heading": cite_heading((spec.get("_opts") or {}).get("citation")),
                           "_text": "\n".join(srcs)})
        made = []
        for i, s in enumerate(slides):
            sid = "folio_" + uuid.uuid4().hex[:16]
            tid, bid = sid + "_t", sid + "_b"
            if s.get("layout") == "sources":
                title, body, bullets = s["heading"], s["_text"], False
            else:
                title, body, bullets = self._slide_text(s)
            lay = s.get("layout")
            pre = "TITLE" if (lay == "title" and start == 0 and i == 0) else \
                  "SECTION_HEADER" if lay == "section" else "TITLE_AND_BODY"
            ttype = "CENTERED_TITLE" if pre == "TITLE" else "TITLE"
            btype = "SUBTITLE" if pre == "TITLE" else "BODY"
            reqs = [{"createSlide": {"objectId": sid, "insertionIndex": start + i,
                                     "slideLayoutReference": {"predefinedLayout": pre},
                                     "placeholderIdMappings": [
                                         {"layoutPlaceholder": {"type": ttype, "index": 0}, "objectId": tid}]
                                     + ([{"layoutPlaceholder": {"type": btype, "index": 0}, "objectId": bid}]
                                        if pre != "SECTION_HEADER" else [])}}]
            if title:
                reqs.append({"insertText": {"objectId": tid, "text": title}})
            if body and pre != "SECTION_HEADER":
                reqs.append({"insertText": {"objectId": bid, "text": body}})
                if bullets:
                    reqs.append({"createParagraphBullets": {"objectId": bid, "textRange": {"type": "ALL"},
                                                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
            try:
                self._req("POST", base + ":batchUpdate", json={"requests": reqs})
            except RuntimeError:
                # deck's theme lacks standard layouts -> plain slide with text boxes
                reqs = [{"createSlide": {"objectId": sid, "insertionIndex": start + i}}]
                for oid, y, h, txt, size in ((tid, 0.07, 0.18, title, 28), (bid, 0.28, 0.62, body, 16)):
                    if not txt:
                        continue
                    reqs += [
                        {"createShape": {"objectId": oid, "shapeType": "TEXT_BOX", "elementProperties": {
                            "pageObjectId": sid,
                            "size": {"width": {"magnitude": W * 0.88, "unit": "EMU"},
                                     "height": {"magnitude": H * h, "unit": "EMU"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": W * 0.06,
                                          "translateY": H * y, "unit": "EMU"}}}},
                        {"insertText": {"objectId": oid, "text": txt}},
                        {"updateTextStyle": {"objectId": oid, "textRange": {"type": "ALL"},
                                             "style": {"fontSize": {"magnitude": size, "unit": "PT"},
                                                       "bold": oid == tid},
                                             "fields": "fontSize,bold"}}]
                self._req("POST", base + ":batchUpdate", json={"requests": reqs})
            made.append((sid, s.get("notes")))
        # speaker notes
        notes = [m for m in made if m[1]]
        if notes:
            pres = self._req("GET", base, params={
                "fields": "slides(objectId,slideProperties(notesPage(notesProperties)))"})
            nid = {sl["objectId"]: ((sl.get("slideProperties") or {}).get("notesPage") or {})
                   .get("notesProperties", {}).get("speakerNotesObjectId") for sl in pres.get("slides", [])}
            reqs = [{"insertText": {"objectId": nid[s], "text": clean(n)}} for s, n in notes if nid.get(s)]
            if reqs:
                try:
                    self._req("POST", base + ":batchUpdate", json={"requests": reqs})
                except RuntimeError:
                    pass
        return len(made)

    # ------------------------------------------------------------ append: Docs
    def append_doc(self, doc_id, spec):
        base = f"https://docs.googleapis.com/v1/documents/{doc_id}"
        d = self._req("GET", base, params={"fields": "body(content(endIndex))"})
        end = d["body"]["content"][-1]["endIndex"] - 1
        op = spec.get("one_pager") or {}
        paras = []  # (text, style, bullet)
        fresh = end <= 1
        title = op.get("title") or spec.get("title")
        if title:
            paras.append((clean(title), "TITLE" if fresh else "HEADING_1", False))
        if op.get("subtitle") or spec.get("subtitle"):
            paras.append((clean(op.get("subtitle") or spec.get("subtitle")), "SUBTITLE", False))
        if op.get("intro"):
            paras.append((clean(op["intro"]), "NORMAL_TEXT", False))
        stats = op.get("key_stats") or []
        if stats:
            paras.append(("Key facts", "HEADING_2", False))
            for s in stats:
                paras.append((f"{clean(s.get('value'))} — {clean(s.get('label'))}", "NORMAL_TEXT", True))
        sections = op.get("sections")
        if not sections:  # slideshow-only project -> outline the slides
            sections = [{"heading": s.get("heading"), "body": s.get("body") or s.get("quote") or "",
                         "bullets": s.get("points") or []}
                        for s in spec.get("slides") or [] if s.get("layout") not in ("title",)]
        for sec in sections:
            if sec.get("heading"):
                paras.append((clean(sec["heading"]), "HEADING_2", False))
            if sec.get("body"):
                paras.append((clean(sec["body"]), "NORMAL_TEXT", False))
            for b in sec.get("bullets") or []:
                paras.append((clean(b), "NORMAL_TEXT", True))
        co = op.get("callout") or {}
        if co.get("text"):
            paras.append((clean(co.get("label") or "Why it matters"), "HEADING_3", False))
            paras.append((clean(co["text"]), "NORMAL_TEXT", False))
        ce = spec.get("current_event") or {}
        if ce.get("summary"):
            paras.append(("In the News" + (f" ({clean(ce.get('date'))})" if ce.get("date") else ""), "HEADING_2", False))
            paras.append((f"{clean(ce.get('headline'))}. {clean(ce.get('summary'))}", "NORMAL_TEXT", False))
            paras.append(("Why it matters: " + clean(ce.get("connection")), "NORMAL_TEXT", False))
        if op.get("conclusion"):
            paras.append((clean(op["conclusion"]), "NORMAL_TEXT", False))
        srcs = citations(spec, 15)
        if srcs:
            paras.append((cite_heading((spec.get("_opts") or {}).get("citation")), "HEADING_2", False))
            for c in srcs:
                paras.append((c, "NORMAL_TEXT", False))
        paras = [p for p in paras if p[0]]
        if not paras:
            return 0
        lead = "" if fresh else "\n"
        text = lead + "\n".join(p[0] for p in paras)
        insert_at = max(1, end)
        reqs = [{"insertText": {"location": {"index": insert_at}, "text": text}}]
        pos = insert_at + u16(lead)
        bullet_runs, run = [], None
        for t, style, bullet in paras:
            ln = u16(t) + 1
            rng = {"startIndex": pos, "endIndex": pos + ln}
            reqs.append({"updateParagraphStyle": {"range": rng, "paragraphStyle": {"namedStyleType": style},
                                                  "fields": "namedStyleType"}})
            if bullet:
                run = [run[0], pos + ln] if run else [pos, pos + ln]
            elif run:
                bullet_runs.append(run)
                run = None
            pos += ln
        if run:
            bullet_runs.append(run)
        for a, b in bullet_runs:
            reqs.append({"createParagraphBullets": {"range": {"startIndex": a, "endIndex": b - 1},
                                                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
        self._req("POST", base + ":batchUpdate", json={"requests": reqs})
        return len(paras)
