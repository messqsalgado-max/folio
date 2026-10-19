"""
Folio quality check: an honest second look at a finished project.

1. Hard checks Folio counts itself (slides, photos, current event, working sources).
2. Fact check: numbers and dates in the project must appear in at least one cited source.
3. A strict, separate AI grader scores the finished text against the rubric (the AI that wrote
   the project doesn't get to grade itself).
"""

import html as htmllib
import re
from concurrent.futures import ThreadPoolExecutor

import requests

import engine
import images
from sources import UA

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(%?)")
YEAR = re.compile(r"^(1[5-9]\d\d|20\d\d)$")


# ---------------------------------------------------------------- readable project text
def project_text(spec):
    """The project as a teacher would read it, slide by slide."""
    out = []
    for i, s in enumerate(spec.get("slides") or [], 1):
        bits = [s.get("heading"), s.get("subheading"), s.get("stat"), s.get("stat_label"), s.get("body"),
                s.get("quote"), s.get("attribution")]
        bits += s.get("points") or []
        for side in ("left", "right"):
            c = s.get(side) or {}
            bits += [c.get("heading")] + (c.get("points") or [])
        bits += [f"{st.get('label')}: {st.get('text')}" for st in s.get("steps") or []]
        photo = " [has a photo]" if s.get("_image") else ""
        out.append(f"Slide {i} ({s.get('layout')}){photo}: " + " | ".join(b for b in bits if b))
    ce = spec.get("current_event") or {}
    if ce.get("summary"):
        out.append(f"In the News slide ({ce.get('date')}): {ce.get('headline')} | {ce.get('summary')} | "
                   f"Why it matters: {ce.get('connection')}" + (" [has a photo]" if ce.get("_image") else ""))
    op = spec.get("one_pager") or {}
    if op:
        parts = [op.get("intro")] + [f"{s.get('label')}: {s.get('value')}" for s in op.get("key_stats") or []]
        for sec in op.get("sections") or []:
            parts += [sec.get("heading"), sec.get("body")] + (sec.get("bullets") or [])
        parts += [(op.get("callout") or {}).get("text"), op.get("conclusion")]
        out.append("One-pager: " + " | ".join(p for p in parts if p))
    if spec.get("sources"):
        out.append(f"Resource page: {len(spec['sources'])} sources cited.")
    return "\n".join(out)


# ---------------------------------------------------------------- 1. hard checks
def _wanted_photos(text):
    m = re.search(r"at least (\d+|one|two|three|four|five)\s+(?:\w+\s+)?(images?|visuals?|pictures?|photos?)", text, re.I)
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    if m:
        return words.get(m.group(1).lower()) or int(m.group(1))
    return 1 if re.search(r"\b(images?|visuals?|pictures?|photos?)\b", text, re.I) else 0


def hard_checks(spec, inputs, output):
    asked = " ".join(str(inputs.get(k) or "") for k in ("prompt", "rubric", "task"))
    checks = []
    if output in ("Slideshow", "Both"):
        want = int(inputs.get("slide_count") or 0)
        have = len(spec.get("slides") or [])
        ok = not want or have >= want - 1
        checks.append({"id": "slides", "ok": ok, "label": f"{have} slides" + (f" (asked for about {want})" if want else ""),
                       "fix": f"Expand the slideshow to about {want} slides, covering anything the rubric asks for "
                              f"that doesn't have its own slide yet." if not ok else ""})
        want_p = _wanted_photos(asked)
        have_p = len(images.all_images(spec))
        if want_p:
            ok = have_p >= want_p
            checks.append({"id": "photos", "ok": ok, "label": f"{have_p} photos (assignment asks for {want_p}+)",
                           "fix": f"Add image_query photos to more slides so there are at least {want_p} relevant, "
                                  f"specific photos." if not ok else ""})
    if re.search(r"current event|in the news|modern", asked, re.I):
        ce = spec.get("current_event") or {}
        ok = bool(ce.get("summary") and re.search(r"\d{4}", ce.get("date") or ""))
        checks.append({"id": "news", "ok": ok,
                       "label": f"Current event: {ce.get('headline')} ({ce.get('date')})" if ok else "No dated current event",
                       "fix": "Find a real, dated current event with news_search and fill current_event." if not ok else ""})
    th = [t for t in inputs.get("student_thoughts") or [] if (t.get("q") or "").strip()]
    if th:
        skipped = [t for t in th if not (t.get("a") or "").strip()]
        checks.append({"id": "thoughts", "ok": not skipped,
                       "label": (f"Your own thoughts used for {len(th)} personal question{'s' if len(th) > 1 else ''}"
                                 if not skipped else
                                 f"Folio drafted {len(skipped)} personal answer{'s' if len(skipped) > 1 else ''} for "
                                 f"you. Rewrite in your own words: " + "; ".join(t["q"] for t in skipped)),
                       "fix": ""})  # only the student can fix this one
    srcs = spec.get("sources") or []
    live = [s for s in srcs if (s.get("_check") or {}).get("status") in ("ok", "blocked", None)]
    ok = len(live) >= 3
    checks.append({"id": "sources", "ok": ok, "label": f"{len(live)} working sources",
                   "fix": "Research more and cite at least 3 reputable sources you actually found." if not ok else ""})
    if output in ("One-pager", "Both"):
        ok = bool((spec.get("one_pager") or {}).get("sections"))
        checks.append({"id": "onepager", "ok": ok, "label": "One-pager written" if ok else "One-pager missing",
                       "fix": "Write the one_pager." if not ok else ""})
    return checks


# ---------------------------------------------------------------- 2. fact check
def _page_text(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code >= 400:
            return ""
        t = r.text[:600000]
        t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", t)
        return htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    except Exception:
        return ""


def _claims(spec):
    out = []

    def add(where, text):
        for sent in re.split(r"(?<=[.!?])\s+", text or ""):
            nums = [n.replace(",", "") + p for n, p in NUM.findall(sent)]
            nums = [n for n in nums if len(n.rstrip("%")) >= 2 or n.endswith("%")]
            if nums:
                out.append({"where": where, "claim": sent.strip()[:220], "nums": nums})
    for i, s in enumerate(spec.get("slides") or [], 1):
        for t in [s.get("body"), s.get("subheading"), f"{s.get('stat') or ''} {s.get('stat_label') or ''}"] + \
                 (s.get("points") or []) + [f"{st.get('label')} {st.get('text')}" for st in s.get("steps") or []]:
            add(f"Slide {i}", t)
    op = spec.get("one_pager") or {}
    for t in [op.get("intro"), op.get("conclusion")] + [f"{k.get('value')} {k.get('label')}" for k in op.get("key_stats") or []] + \
             [sec.get("body") for sec in op.get("sections") or []]:
        add("One-pager", t)
    seen, uniq = set(), []
    for c in out:
        if c["claim"] not in seen:
            seen.add(c["claim"])
            uniq.append(c)
    return uniq[:30]


def _num_forms(n):
    pct = n.endswith("%")
    core = n.rstrip("%")
    forms = {core}
    if core.isdigit() and len(core) > 3 and not YEAR.match(core):
        forms.add(f"{int(core):,}")
    return forms, pct


def source_texts(spec):
    """{url: page text} for the project's live sources (fetched once, shared by both fact checks)."""
    srcs = [s for s in spec.get("sources") or [] if (s.get("_check") or {}).get("status") not in ("dead", "rule")][:8]
    with ThreadPoolExecutor(6) as ex:
        pages = list(ex.map(lambda s: _page_text(s.get("url")), srcs))
    return {s.get("url"): t for s, t in zip(srcs, pages) if t}


def fact_check(spec, progress=None, texts=None):
    claims = _claims(spec)
    if not claims:
        return []
    texts = source_texts(spec) if texts is None else texts
    if progress:
        progress("thought", f"Fact-checking {len(claims)} numbers and dates against {len(texts)} sources…", None)
    corpus = re.sub(r"\s+", " ", " ".join(texts.values()))
    results = []
    for c in claims:
        if not corpus:
            results.append({**c, "status": "unchecked"})
            continue
        missing = []
        for n in c["nums"]:
            forms, _ = _num_forms(n)
            if not any(re.search(r"(?<![\d,.])" + re.escape(f) + r"(?![\d,])", corpus) for f in forms):
                missing.append(n)
        results.append({**c, "status": "unverified" if missing else "supported", "missing": missing})
    return results


# ---------------------------------------------------------------- 2b. claim check (names, events, quotes)
CLAIMS_TOOL = {
    "name": "submit_claims",
    "description": "Check the project's key factual claims against the source excerpts.",
    "input_schema": {"type": "object", "properties": {"claims": {"type": "array", "items": {"type": "object", "properties": {
        "where": {"type": "string", "description": "e.g. 'Slide 3' or 'One-pager'"},
        "claim": {"type": "string", "description": "the claim, quoted or closely paraphrased from the project"},
        "verdict": {"type": "string", "enum": ["supported", "contradicted", "not_found"]},
        "evidence": {"type": "string", "description": "short quote from an excerpt, or what the source says instead"},
        "fix": {"type": "string", "description": "for contradicted: the corrected wording"}},
        "required": ["claim", "verdict"]}}},
        "required": ["claims"]},
}


def _excerpts(spec, texts, per_source=4500):
    """The parts of each source page that share the most words with the project."""
    words = set(re.findall(r"[a-z]{4,}", project_text(spec).lower()))
    out = []
    for url, text in texts.items():
        paras = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n{2,}", text) if len(p.strip()) > 40]
        ranked = sorted(paras, key=lambda p: -len(words & set(re.findall(r"[a-z]{4,}", p.lower()))))
        chunk, n = [], 0
        for p in ranked:
            if n + len(p) > per_source:
                break
            chunk.append(p)
            n += len(p)
        if chunk:
            out.append(f"<source url=\"{url}\">\n" + " ".join(chunk) + "\n</source>")
    return "\n".join(out)


def claim_check(opts, spec, texts):
    """AI check of names, events, quotes and other non-numeric claims against what the sources actually say."""
    if not texts:
        return []
    system = ("You are a careful fact-checker. Pick the 8-15 most important factual claims in <project> (who did "
              "what, when, why, quotes, causes and effects; skip opinions and the student's personal reflections). "
              "For each, decide from <sources> ONLY: 'supported' if a source says it, 'contradicted' if a source "
              "says otherwise, 'not_found' if the excerpts don't cover it. Quote short evidence.")
    user = (f"<project>\n{project_text(spec)[:15000]}\n</project>\n"
            f"<sources>\n{_excerpts(spec, texts)[:30000]}\n</sources>")
    claims = engine.call_tool(opts, system, user, CLAIMS_TOOL, max_tokens=6000).get("claims") or []
    for c in claims:  # a missing or odd verdict counts as "couldn't confirm", never as backed up
        v = str(c.get("verdict") or "").lower().replace(" ", "_")
        c["verdict"] = v if v in ("supported", "contradicted", "not_found") else "not_found"
    return [c for c in claims if c.get("claim")]


# ---------------------------------------------------------------- 3. strict grader
GRADE_TOOL = {
    "name": "submit_grades",
    "description": "Grade the finished project against each rubric criterion.",
    "input_schema": {
        "type": "object",
        "properties": {"grades": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": {"type": "string"},
            "status": {"type": "string", "enum": ["met", "partial", "missing"]},
            "where": {"type": "string", "description": "exact slide numbers / one-pager part, or 'nowhere'"},
            "tip": {"type": "string", "description": "for partial/missing: the concrete fix"}}}},
            "news_ok": {"type": "boolean", "description": "true only if the In the News item is a specific, "
                        "real event clearly about the project's topic (false if unrelated, generic or vague)"},
            "news_problem": {"type": "string", "description": "if news_ok is false: what's wrong with it"},
            "off_topic": {"type": "array", "description": "Only when asked: slides or one-pager parts that don't "
                          "serve the assignment or rubric", "items": {"type": "object", "properties": {
                              "where": {"type": "string"}, "why": {"type": "string"}}}}},
        "required": ["grades"],
    },
}


def grade(opts, spec, inputs, strict=False):
    """{'grades': [...], 'off_topic': [...]} from a separate, strict AI pass (None without a rubric)."""
    rubric = (inputs.get("rubric") or "").strip()
    if not rubric:
        return None
    system = ("You are a strict but fair teacher grading a student project against a rubric. Grade ONLY what is "
              "actually in <project>. A criterion is 'met' only if the project clearly and accurately covers it; "
              "'partial' if it is thin, vague or only implied; 'missing' if it isn't there. Point to exact slides. "
              "Photos only count where the project says [has a photo]. Be specific in tips. Also judge the "
              "'In the News' item (if any): news_ok is true only if it describes a specific event clearly about "
              "the project's topic; a story about something else, or a vague 'debates continue' summary, is false." +
              (" The student asked to stay STRICTLY on the assignment: also list in off_topic every slide or "
               "one-pager part that doesn't directly serve the assignment prompt or a rubric criterion." if strict else ""))
    user = (f"<assignment>\n{(inputs.get('prompt') or '')[:6000]}\n</assignment>\n<rubric>\n{rubric[:8000]}\n</rubric>\n"
            f"<project>\n{project_text(spec)[:20000]}\n</project>")
    return engine.call_tool(opts, system, user, GRADE_TOOL, max_tokens=8000)


# ---------------------------------------------------------------- all together
def review(spec, opts, inputs, output, progress=None):
    """Run every check; store results in spec['_review'] and replace rubric_check with the strict grades."""
    if progress:
        progress("thought", "Checking the work with a separate, strict grader…", None)
    checks = hard_checks(spec, inputs, output)
    texts = source_texts(spec)
    facts = fact_check(spec, progress, texts)
    claims, claim_error = [], ""
    try:
        if texts:
            if progress:
                progress("thought", "Checking names, events and quotes against the sources…", None)
            claims = claim_check(opts, spec, texts)
    except Exception as e:
        claim_error = str(e)[:200]
    grades, grade_error, off_topic = None, "", []
    try:
        strict = ((spec.get("_opts") or {}).get("focus") or opts.get("focus")) == "Strict"
        g = grade(opts, spec, inputs, strict) if strict else grade(opts, spec, inputs)
        grades, off_topic = (g, []) if isinstance(g, list) or g is None else             (g.get("grades") or None, (g.get("off_topic") or []) if strict else [])
        news_check = next((c for c in checks if c["id"] == "news"), None)
        if isinstance(g, dict) and g.get("news_ok") is False and (spec.get("current_event") or {}).get("summary"):
            problem = g.get("news_problem") or "it isn't specifically about the topic"
            fix = (f"Replace the current event ({problem}). Use news_search to find a specific, dated news story "
                   "clearly about this topic, and fill current_event again.")
            if news_check:
                news_check.update(ok=False, label=f"Current event is off-topic: {problem}", fix=fix)
            else:
                checks.append({"id": "news", "ok": False, "label": f"Current event is off-topic: {problem}", "fix": fix})
    except Exception as e:  # grading is a bonus; never lose the project over it
        grade_error = str(e)[:200]
    if grades:
        spec.setdefault("_author_rubric", spec.get("rubric_check"))
        spec["rubric_check"] = grades
    fixes = [c["fix"] for c in checks if not c["ok"] and c["fix"]]
    fixes += [f"Rubric '{g.get('criterion')}': {g.get('tip') or 'cover it clearly'}"
              for g in grades or [] if g.get("status") in ("partial", "missing")]
    if off_topic:
        fixes.append("Stay strictly on the assignment: remove or refocus these parts: " +
                     " ; ".join(f"{o.get('where')} ({o.get('why')})" for o in off_topic[:8]))
    wrong = [c for c in claims if c.get("verdict") == "contradicted"]
    if wrong:
        fixes.append("These claims contradict your sources. Correct them: " +
                     " ; ".join(f"{c.get('where')}: \"{c.get('claim')}\" (source: {c.get('evidence')})"
                                f"{' -> ' + c['fix'] if c.get('fix') else ''}" for c in wrong[:8]))
    bad = [f for f in facts if f["status"] == "unverified"]
    if bad:
        fixes.append("These numbers/dates weren't found in the cited sources. Correct them from your sources or remove "
                     "them: " + " ; ".join(f"{f['where']}: \"{f['claim']}\"" for f in bad[:8]))
    spec["_review"] = {"checks": checks, "facts": facts, "graded": bool(grades), "grade_error": grade_error,
                       "off_topic": off_topic, "claims": claims, "claim_error": claim_error,
                       "fixes": fixes}
    if progress:
        ok = sum(c["ok"] for c in checks)
        progress("thought", f"Checks: {ok}/{len(checks)} passed · {len(bad)} unverified numbers · "
                            f"{sum(1 for g in grades or [] if g.get('status') != 'met')} rubric items need work.", None)
    return spec["_review"]


def fix_request(rev):
    return ("Fix these problems a strict reviewer found. Keep everything that's already good.\n- " +
            "\n- ".join(rev["fixes"])) if rev and rev.get("fixes") else ""
