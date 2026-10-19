"""
Folio source checker: opens every cited link, drops the dead ones, and swaps in
working sources the research actually found when it can.
"""

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}

# ok: the page loads. blocked: the site refuses automated checks, so it may still be fine.
# dead: missing page or no such site.
OK, BLOCKED, DEAD = "ok", "blocked", "dead"


# ---------------------------------------------------------------- the student's source rules
# Sites most teachers accept besides .gov/.edu/.mil: major news, reference works, museums and libraries.
TRUSTED = {
    "apnews.com", "reuters.com", "npr.org", "pbs.org", "bbc.com", "bbc.co.uk", "nytimes.com", "washingtonpost.com",
    "wsj.com", "theguardian.com", "usatoday.com", "latimes.com", "cbsnews.com", "nbcnews.com", "abcnews.go.com",
    "cnn.com", "politico.com", "axios.com", "time.com", "theatlantic.com", "economist.com", "bloomberg.com",
    "nationalgeographic.com", "smithsonianmag.com", "si.edu", "britannica.com", "history.com", "loc.gov",
    "archives.gov", "constitutioncenter.org", "oyez.org", "scotusblog.com", "pewresearch.org", "census.gov",
    "nature.com", "science.org", "scientificamerican.com", "nasa.gov", "who.int", "un.org", "worldbank.org",
    "cfr.org", "brookings.edu", "rand.org", "khanacademy.org", "ushistory.org", "metmuseum.org", "nps.gov",
    "mountvernon.org", "monticello.org", "fdrlibrary.org", "trumanlibrary.gov", "millercenter.org",
}
# news often arrives through syndication links (Yahoo, MSN) while the outlet named is trusted
TRUSTED_OUTLETS = re.compile(
    r"\b(associated press|ap news|reuters|npr|pbs|bbc|new york times|washington post|wall street journal|usa today|"
    r"los angeles times|cnn|nbc news|cbs news|abc news|politico|axios|the guardian|bloomberg|time|the atlantic|"
    r"the economist|national geographic|smithsonian|scientific american)\b", re.I)
NEVER_CITE = {"wikipedia.org", "reddit.com", "quora.com", "answers.com", "fandom.com", "tiktok.com",
              "pinterest.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "chegg.com",
              "coursehero.com", "studocu.com", "brainly.com"}
LEVELS = ("Trusted only", "Balanced", "Anything relevant")


def domain(url):
    from urllib.parse import urlparse
    d = (urlparse(url or "").hostname or "").lower()
    return d[4:] if d.startswith("www.") else d


def _in(d, group):
    return any(d == g or d.endswith("." + g) for g in group)


def rules_from(opts):
    opts = opts or {}
    blocked = [domain("https://" + b.strip().split("://")[-1]) for b in
               re.split(r"[,\s]+", opts.get("blocked_sites") or "") if b.strip()]
    level = opts.get("source_level") if opts.get("source_level") in LEVELS else "Balanced"
    try:
        max_age = int(opts.get("max_age") or 0) or None
    except ValueError:
        max_age = None
    return {"level": level, "wiki_ok": bool(opts.get("wiki_ok", False)), "blocked": [b for b in blocked if b],
            "max_age": max_age}


def violation(item, rules):
    """Why a source or search result breaks the student's rules, or None if it's fine."""
    if not rules:
        return None
    d = domain(item.get("url"))
    if not d:
        return None
    if _in(d, rules["blocked"]):
        return "a site you blocked"
    if _in(d, {"wikipedia.org"}) and not rules["wiki_ok"]:
        return "Wikipedia isn't allowed"
    if rules["level"] != "Anything relevant" and _in(d, NEVER_CITE - {"wikipedia.org"}):
        return "not a citable source"
    outlet = str(item.get("outlet") or item.get("publisher") or item.get("source_publisher") or "")
    if rules["level"] == "Trusted only" and not (re.search(r"\.(gov|edu|mil)$", d) or _in(d, TRUSTED)
                                                 or (_in(d, {"wikipedia.org"}) and rules["wiki_ok"])
                                                 or (_in(d, {"yahoo.com", "msn.com"}) and TRUSTED_OUTLETS.search(outlet))):
        return "not on the trusted list"
    if rules["max_age"]:
        m = re.search(r"\b(19\d\d|20\d\d)\b", str(item.get("date") or item.get("year") or ""))
        if m and int(m.group(1)) < dt.date.today().year - rules["max_age"]:
            return f"older than {rules['max_age']} years"
    return None


def filter_results(out, opts):
    """Drop search results the student's rules don't allow, before the AI ever sees them."""
    rules = rules_from(opts)
    if not isinstance(out, list):
        return out
    kept = [r for r in out if not violation(r, rules)]
    if out and not kept:
        return {"error": "Every result broke the student's source rules (e.g. trusted sites only). "
                         "Try another query, for example add 'site:.gov' or a news outlet name."}
    return kept


def check_url(url, timeout=12):
    if not (url or "").startswith(("http://", "https://")):
        return {"status": DEAD, "code": None, "why": "not a web link"}
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True, stream=True)
        code = r.status_code
        r.close()
    except requests.exceptions.Timeout:
        return {"status": BLOCKED, "code": None, "why": "too slow to answer"}
    except requests.exceptions.SSLError:
        return {"status": BLOCKED, "code": None, "why": "security certificate problem"}
    except requests.exceptions.RequestException:
        return {"status": DEAD, "code": None, "why": "site doesn't exist or can't be reached"}
    if code < 400:
        return {"status": OK, "code": code, "why": ""}
    if code in (404, 410):
        return {"status": DEAD, "code": code, "why": "page not found"}
    if code in (401, 403, 405, 406, 429, 451, 999) or code >= 500:
        return {"status": BLOCKED, "code": code, "why": "site blocks automated checks"}
    return {"status": DEAD, "code": code, "why": f"error {code}"}


def check_spec(spec, progress=None, cancel=None, rules=None):
    """Check every source in place. Dead ones, and ones that break the student's source rules,
    move to spec['_removed_sources'] and are replaced by allowed, working links from the research
    (spec['_all_seen']) where possible. Returns {'ok', 'blocked', 'dead', 'rule', 'replaced'}."""
    srcs = [s for s in spec.get("sources") or [] if isinstance(s, dict)]
    ce = spec.get("current_event") or {}
    if ce.get("source_url") and ce["source_url"] not in {s.get("url") for s in srcs}:
        # the news article must be cited too, even if the AI left it out of the list
        srcs.append({"title": ce.get("source_title") or ce.get("headline") or ce["source_url"],
                     "url": ce["source_url"], "publisher": ce.get("source_publisher", ""),
                     "date": ce.get("date", "")})
    removed = list(spec.get("_removed_sources") or [])
    broke = 0
    for s in list(srcs):
        why = violation(s, rules)
        if why:
            s["_check"] = {"status": "rule", "code": None, "why": why}
            removed.append(s)
            srcs.remove(s)
            broke += 1
    if progress:
        progress("thought", f"Checking {len(srcs)} source links…", None)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda s: check_url(s.get("url")), srcs))
    keep = []
    for s, res in zip(srcs, results):
        s["_check"] = res
        (removed if res["status"] == DEAD else keep).append(s)
    replaced = 0
    dead_now = len(srcs) - len(keep) + broke
    if dead_now:
        used = {s.get("url") for s in keep} | {s.get("url") for s in removed}
        spares = [x for x in spec.get("_all_seen") or []
                  if x.get("url") and x["url"] not in used and not violation(x, rules)]
        for cand in spares[:dead_now * 3]:
            if cancel is not None and cancel.is_set():
                break
            if replaced >= dead_now:
                break
            res = check_url(cand["url"])
            if res["status"] == OK:
                keep.append({"title": cand.get("title") or cand["url"], "url": cand["url"],
                             "_check": res, "_replacement": True})
                replaced += 1
    spec["sources"] = keep
    spec["_removed_sources"] = removed
    if ce.get("source_url"):
        ce["_check"] = next((s["_check"] for s in srcs if s.get("url") == ce["source_url"]), None) \
            or check_url(ce["source_url"])
    summary = {k: sum(1 for r in results if r["status"] == k) for k in (OK, BLOCKED, DEAD)}
    summary["replaced"] = replaced
    summary["rule"] = broke
    if progress:
        msg = f"Links: {summary['ok']} working, {summary['blocked']} couldn't be verified"
        if summary["dead"]:
            msg += f", {summary['dead']} dead removed"
        if broke:
            msg += f", {broke} removed for breaking your source rules"
        if replaced:
            msg += f" ({replaced} replaced)"
        progress("thought", msg + ".", None)
    return summary
