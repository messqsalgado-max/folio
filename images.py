"""
Folio photos: finds freely licensed photos (Wikimedia Commons, then Openverse)
for slides and the one-pager, downloads them into the project folder and keeps
the credit line each license asks for.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Folio/1.0 (desktop school research app; python-requests)"}
MAX_PX = 1600


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def search_wikimedia(query, n=8):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=20, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": n,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": MAX_PX})
    r.raise_for_status()
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png") or (ii.get("width") or 0) < 700:
            continue
        md = ii.get("extmetadata") or {}
        out.append({"url": ii.get("thumburl") or ii.get("url"), "w": ii.get("width"), "h": ii.get("height"),
                    "title": re.sub(r"^File:|\.\w+$", "", p.get("title", "")),
                    "creator": _strip((md.get("Artist") or {}).get("value")) or "Unknown",
                    "license": _strip((md.get("LicenseShortName") or {}).get("value")) or "see source",
                    "source": ii.get("descriptionurl"), "site": "Wikimedia Commons"})
    return out


def search_openverse(query, n=8):
    r = requests.get("https://api.openverse.org/v1/images/", headers=UA, timeout=20, params={
        "q": query, "page_size": n, "mature": "false"})
    r.raise_for_status()
    out = []
    for i in r.json().get("results", []):
        if (i.get("width") or 1000) < 700:
            continue
        lic = f"CC {i.get('license', '').upper()} {i.get('license_version') or ''}".strip()
        if i.get("license") in ("cc0", "pdm"):
            lic = "Public domain" if i.get("license") == "pdm" else "CC0"
        out.append({"url": i.get("url"), "w": i.get("width"), "h": i.get("height"),
                    "title": i.get("title") or query, "creator": i.get("creator") or "Unknown",
                    "license": lic, "source": i.get("foreign_landing_url") or i.get("url"),
                    "site": "Openverse"})
    return out


STOP = {"the", "a", "an", "of", "in", "on", "and", "at", "for", "to", "with", "from", "by", "photo",
        "image", "picture"}


def _words(s):
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower()) if w not in STOP}


def _relevant(c, query):
    """Search engines sometimes return unrelated files; the photo's title must share a word
    with the query."""
    return bool(_words(query) & _words(c.get("title", "") + " " + c.get("source", "")))


def _candidates(query):
    found = []
    tries = [query]
    loose = re.sub(r"\b\d{3,4}s?\b", "", query).strip()
    if loose and loose != query:
        tries.append(loose)
    for q in tries:
        for fn in (search_wikimedia, search_openverse):
            try:
                found += [c for c in fn(q) if _relevant(c, query)]
            except Exception:
                pass
            if len(found) >= 3:
                break
        if found:
            break
    # best word match first, then landscape (fills slide panels with the least cropping)
    qw = _words(query)
    return sorted(found, key=lambda c: (-len(qw & _words(c.get("title"))),
                                        0 if (c.get("w") or 1) >= (c.get("h") or 1) else 1))


def _download(c, path):
    from PIL import Image
    from io import BytesIO
    r = requests.get(c["url"], headers=UA, timeout=30)
    r.raise_for_status()
    im = Image.open(BytesIO(r.content))
    im = im.convert("RGB")
    im.thumbnail((MAX_PX, MAX_PX))
    im.save(path, "JPEG", quality=86)


def find(query, folder, name, taken):
    """Best photo for `query` saved as images/<name>.jpg; returns its info dict or None."""
    os.makedirs(os.path.join(folder, "images"), exist_ok=True)
    for c in _candidates(query):
        if c["url"] in taken:
            continue
        taken.add(c["url"])
        try:
            _download(c, os.path.join(folder, "images", name + ".jpg"))
        except Exception:
            continue
        return {"file": name + ".jpg", "query": query, "title": c["title"], "creator": c["creator"],
                "license": c["license"], "source": c["source"], "site": c["site"], "url": c["url"]}
    return None


def options(query, n=8):
    """Photo choices for the slide editor's 'Swap photo' (relevant, landscape first)."""
    return [{k: c.get(k) for k in ("url", "title", "creator", "license", "source", "site", "w", "h")}
            for c in _candidates(query)[:n]]


def use(choice, folder, name):
    """Download a photo the student picked and return its info for the spec."""
    os.makedirs(os.path.join(folder, "images"), exist_ok=True)
    _download(choice, os.path.join(folder, "images", name + ".jpg"))
    return {"file": name + ".jpg", "query": choice.get("query", ""), "title": choice.get("title"),
            "creator": choice.get("creator"), "license": choice.get("license"), "source": choice.get("source"),
            "site": choice.get("site"), "url": choice.get("url")}


def thumb_data(path, width=360):
    """Small base64 preview of a local photo for the app's slide strip."""
    import base64
    from io import BytesIO
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((width, width))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def credit(img):
    return f"{img.get('creator') or 'Unknown'} / {img.get('site')} ({img.get('license')})"


VISION_TOOL = {
    "name": "submit_photo_checks",
    "description": "Say whether each photo fits the slide it's meant for.",
    "input_schema": {"type": "object", "properties": {"photos": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer", "description": "photo number, starting at 1"},
        "fits": {"type": "boolean", "description": "true if the photo clearly shows what the slide is about"},
        "why": {"type": "string"}}, "required": ["n", "fits"]}}}, "required": ["photos"]},
}


def _context(target):
    bits = [target.get("heading") or target.get("headline") or target.get("title") or ""]
    bits += (target.get("points") or [])[:2] + [target.get("summary") or target.get("body") or target.get("intro") or ""]
    return " | ".join(b for b in bits if b)[:220]


def vision_check(opts, items):
    """AI looks at each (context, photo path) pair; returns a list of (fits, why). Fails open."""
    if not items or not opts or not (opts.get("gemini_key") if opts.get("provider") == "Gemini" else opts.get("api_key")):
        return [(True, "")] * len(items)
    from io import BytesIO
    from PIL import Image
    import engine
    pics = []
    for _, path in items:
        im = Image.open(path).convert("RGB")
        im.thumbnail((512, 512))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=80)
        pics.append(buf.getvalue())
    lines = "\n".join(f"Photo {i}: meant for: {ctx}" for i, (ctx, _) in enumerate(items, 1))
    system = ("You check photos chosen for a student's school slideshow. A photo fits only if it clearly shows the "
              "person, place, object, document or event the slide is about, or something directly tied to it. "
              "Reject photos of the wrong person, place or era, unrelated scenes, logos, maps of somewhere else, "
              "text-heavy screenshots and generic stock-style images. Use the photo's own caption too: if it "
              "names a different place, person or time than the slide is about, the photo doesn't fit.")
    try:
        res = engine.call_tool(opts, system, lines, VISION_TOOL, max_tokens=2000, images=pics).get("photos") or []
    except Exception:
        return [(True, "")] * len(items)
    verdict = {int(r.get("n", 0)): (r.get("fits") is not False, r.get("why") or "") for r in res}
    return [verdict.get(i, (True, "")) for i in range(1, len(items) + 1)]


def attach_all(spec, folder, progress=None, cancel=None, reuse=None, opts=None):
    """Fill `_image` on slides / one-pager that ask for a photo (`image_query`).
    `reuse` maps query -> earlier image info, so revisions don't re-download.
    With `opts` (AI keys), the AI looks at each new photo and swaps out ones that don't fit."""
    reuse = reuse or {}
    jobs = []
    for i, s in enumerate(spec.get("slides") or []):
        q = (s.get("image_query") or "").strip()
        s.pop("_image", None)
        if q:
            jobs.append((s, q, f"slide{i + 1:02d}"))
    op = spec.get("one_pager") or {}
    op.pop("_image", None)
    if (op.get("image_query") or "").strip():
        jobs.append((op, op["image_query"].strip(), "onepager"))
    ce = spec.get("current_event") or {}
    ce.pop("_image", None)
    if (ce.get("image_query") or "").strip():
        jobs.append((ce, ce["image_query"].strip(), "news"))
    if not jobs:
        return 0
    if progress:
        progress("thought", f"Finding {len(jobs)} freely licensed photos…", None)
    taken = set()
    todo = []
    for target, q, name in jobs:
        old = reuse.get(q)
        if old and os.path.isfile(os.path.join(folder, "images", old["file"])):
            target["_image"] = old
            taken.add(old.get("url"))
        else:
            todo.append((target, q, name))

    def work(job):
        target, q, name = job
        if cancel is not None and cancel.is_set():
            return
        img = find(q, folder, name, taken)
        if img:
            target["_image"] = img
            if progress:
                progress("search", f"Photos · {q}", {"engine": "Photos"})

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(work, todo))
    if opts:
        for attempt in range(2):  # check, then give rejected spots one more photo and check those
            new = [(t, q, n) for t, q, n in todo if t.get("_image") and not t["_image"].get("_seen")]
            if not new:
                break
            if progress and attempt == 0:
                progress("thought", f"Looking at {len(new)} photos to make sure they fit…", None)
            verdicts = vision_check(opts, [(f"{_context(t)} (searched: {q}) || photo's own caption: "
                                            f"{(t['_image'].get('title') or '')[:160]}",
                                            os.path.join(folder, "images", t["_image"]["file"])) for t, q, _ in new])
            rejected = 0
            for (t, q, name), (fits, why) in zip(new, verdicts):
                t["_image"]["_seen"] = True
                if not fits:
                    rejected += 1
                    t.pop("_image", None)
                    if attempt == 0:
                        img = find(q, folder, name + "b", taken)
                        if img:
                            t["_image"] = img
            if progress and rejected:
                progress("thought", f"Swapped out {rejected} photo{'s' if rejected > 1 else ''} that didn't fit.", None)
        for t, _, _ in jobs:
            if t.get("_image"):
                t["_image"].pop("_seen", None)
    got = sum(1 for t, _, _ in jobs if t.get("_image"))
    if progress and got < len(jobs):
        progress("thought", f"Found photos for {got} of {len(jobs)} spots; the rest use text layouts.", None)
    return got


def all_images(spec):
    out = [s["_image"] for s in spec.get("slides") or [] if s.get("_image")]
    for part in (spec.get("one_pager") or {}, spec.get("current_event") or {}):
        if part.get("_image"):
            out.append(part["_image"])
    seen, uniq = set(), []
    for i in out:
        if i["file"] not in seen:
            seen.add(i["file"])
            uniq.append(i)
    return uniq
