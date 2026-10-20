"""
Folio engine: researches the web and drafts a structured project
(slideshow + one-pager) from an assignment prompt, rubric and task.
"""

import datetime as dt
import json
import re
import threading

import anthropic

import sources

try:
    from ddgs import DDGS
except Exception:  # pragma: no cover
    DDGS = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


# ---------------------------------------------------------------- tools
DDG_TOOL = {
    "name": "duckduckgo_search",
    "description": "Search the web with DuckDuckGo. Returns titles, URLs and snippets. "
                   "Use several different queries to find and cross-check facts and sources.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
}

NEWS_TOOL = {
    "name": "news_search",
    "description": "Search recent news articles (DuckDuckGo News). Returns headline, date, outlet, URL "
                   "and a snippet. Use it to find a real, dated current event about the topic.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

BRAVE_TOOL = {
    "name": "brave_search",
    "description": "Search the web with the Brave Search index. Returns titles, URLs and snippets.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

POINTS = {"type": "array", "items": {"type": "string"}}

SUBMIT_TOOL = {
    "name": "submit_project",
    "description": "Submit the finished project. Call this exactly once, after research is done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "slides": {
                "type": "array",
                "description": "Ordered slides. Vary layouts; don't repeat the same layout more than twice in a row.",
                "items": {
                    "type": "object",
                    "properties": {
                        "layout": {"type": "string", "enum": [
                            "title", "section", "bullets", "two_column",
                            "big_stat", "quote", "timeline", "image", "closing"]},
                        "heading": {"type": "string"},
                        "subheading": {"type": "string"},
                        "points": {**POINTS, "description": "bullets / closing takeaways (max 5, short)"},
                        "left": {"type": "object", "properties": {
                            "heading": {"type": "string"}, "points": POINTS}},
                        "right": {"type": "object", "properties": {
                            "heading": {"type": "string"}, "points": POINTS}},
                        "stat": {"type": "string", "description": "big_stat: the number, e.g. '72%'"},
                        "stat_label": {"type": "string"},
                        "body": {"type": "string", "description": "short supporting sentence(s)"},
                        "quote": {"type": "string"},
                        "attribution": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "object", "properties": {
                            "label": {"type": "string"}, "text": {"type": "string"}}}},
                        "notes": {"type": "string", "description": "speaker notes, natural spoken style"},
                        "image_query": {"type": "string", "description":
                            "2-5 word search for a real photo that fits this slide (e.g. 'Dust Bowl dust "
                            "storm 1935'). Empty if no photo. Required for the 'image' layout."},
                    },
                    "required": ["layout", "heading"],
                },
            },
            "one_pager": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "intro": {"type": "string"},
                    "key_stats": {"type": "array", "items": {"type": "object", "properties": {
                        "value": {"type": "string"}, "label": {"type": "string"}}}},
                    "sections": {"type": "array", "items": {"type": "object", "properties": {
                        "heading": {"type": "string"}, "body": {"type": "string"}, "bullets": POINTS}}},
                    "callout": {"type": "object", "properties": {
                        "label": {"type": "string"}, "text": {"type": "string"}}},
                    "conclusion": {"type": "string"},
                    "image_query": {"type": "string", "description": "optional header photo search"},
                },
            },
            "sources": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "url": {"type": "string"},
                "author": {"type": "string", "description": "person(s) or organization, as shown; empty if none"},
                "publisher": {"type": "string", "description": "website or publication name"},
                "date": {"type": "string", "description": "publication date as shown, e.g. 'March 4, 2023' or '2023'"},
                "year": {"type": "string"}}}},
            "current_event": {
                "type": "object",
                "description": "One real, recent news event that shows the topic matters today. "
                               "Only from an article you actually found with news_search or web search.",
                "properties": {
                    "headline": {"type": "string", "description": "short headline in your own words"},
                    "date": {"type": "string", "description": "exact publication date, e.g. 'September 25, 2026'"},
                    "summary": {"type": "string", "description": "2-3 factual sentences: what happened"},
                    "connection": {"type": "string", "description": "2-3 sentences: how it connects to the topic"},
                    "source_title": {"type": "string"},
                    "source_publisher": {"type": "string"},
                    "source_url": {"type": "string"},
                    "image_query": {"type": "string", "description": "optional photo search for this event"},
                },
            },
            "rubric_check": {
                "type": "array",
                "description": "One entry per rubric criterion: where/how the project meets it.",
                "items": {"type": "object", "properties": {
                    "criterion": {"type": "string"},
                    "where": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "partial", "missing"]},
                    "tip": {"type": "string"}}},
            },
        },
        "required": ["title", "sources", "rubric_check"],
    },
}

DEPTH = {"Quick": 4, "Standard": 8, "Deep": 15}
# the fewest searches a project may be built on, per research depth (the AI is sent back once if it stops early)
MIN_SEARCHES = {"Quick": 3, "Standard": 6, "Deep": 10}


def _too_early(opts, done):
    need = MIN_SEARCHES.get(opts.get("depth", "Standard"), 6)
    if opts.get("revise") or done >= need:
        return ""
    return (f"Not yet. You've only run {done} searches, and the student chose {opts.get('depth', 'Standard')} "
            f"research, which needs at least {need}. Run {need - done} more searches on different parts of the "
            "assignment and rubric (verify key dates and numbers in a second source), then call submit_project "
            "again with the complete project.")

AVOID = ("delve, tapestry, testament, realm, embark, navigate the complexities, "
         "in today's fast-paced world, it's important to note, plays a crucial role, "
         "a rich tapestry, ever-evolving, landscape (figurative), unlock, harness, "
         "seamless, pivotal, multifaceted, in conclusion, moreover, furthermore")


def build_system(opts):
    parts = [
        "You are Folio, an expert researcher, writer and presentation designer.",
        "You turn a student's assignment (prompt, rubric, task) into a well-researched "
        "slideshow and/or one-pager.",
        "",
        "PROCESS",
        "1. Read the rubric carefully and list (privately) every criterion to satisfy.",
        "2. Research with the search tools available. Use several different queries, prefer "
        "reputable sources (.gov, .edu, established news, journals, museums), and cross-check "
        "important numbers across at least two sources. Never invent facts, statistics, "
        "quotes or sources; only cite URLs you actually saw in search results.",
        "3. Plan the structure so every rubric criterion is clearly covered.",
        "4. Call submit_project once with the finished content.",
        "",
        "SLIDES",
        f"- About {opts.get('slide_count', 10)} slides including title and closing. "
        "Do not add a sources slide; the app adds one automatically from `sources`.",
        "- Keep on-slide text short (bullets under ~12 words, max 5 per slide); "
        "put explanation in speaker notes.",
        "- Vary layouts (bullets, two_column, big_stat, quote, timeline, section) so the deck "
        "feels designed by a person, not a template. Use big_stat only for real numbers you found.",
        "",
        "WRITING VOICE",
        f"- Reading level: {opts.get('reading_level', 'High school')}. "
        f"Tone: {opts.get('tone', 'Balanced')}.",
        "- Write like a thoughtful person, not a press release: vary sentence length, use "
        "concrete examples and specific details, plain words, active voice, occasional "
        "first-person plural if natural. No filler, no hype.",
        f"- Avoid these overused phrases: {AVOID}. Use em dashes sparingly.",
    ]
    photos = opts.get("photos", "Some")
    if photos == "None":
        parts.append("- Photos are turned off: leave image_query empty and don't use the image layout.")
    else:
        share = "about a third of" if photos == "Some" else "most"
        parts += [
            f"- Photos: give {share} the slides an image_query (title slide included) for a real, "
            "specific photo: people, places, objects, events. Name the concrete thing, not an idea "
            "('Hoover Dam construction 1933', not 'progress'). Show the topic itself (the people, documents and "
            "events the slide is about), not generic landmarks like the Capitol or White House. The app finds a freely licensed "
            "photo; you never invent image URLs. Use the 'image' layout (one big photo, heading, "
            "one-sentence caption in body) once or twice when a picture tells the story best.",
            "- one_pager.image_query: one header photo search, or empty.",
        ]
    if not opts.get("notes", True):
        parts.append("- Speaker notes are turned off: leave notes empty.")
    if opts.get("agenda"):
        parts.append("- Put an 'Agenda' slide (bullets layout) right after the title slide.")
    if opts.get("questions"):
        parts.append("- After the closing slide, add a final 'Questions?' slide (section layout).")
    thoughts = [t for t in opts.get("student_thoughts") or [] if (t.get("q") or "").strip()]
    if thoughts:
        parts += ["", "THE STUDENT'S OWN THOUGHTS",
                  "- The assignment asks for the student's personal view. Use THEIR answers below for those parts, "
                  "keeping their words and opinions (fix only spelling and grammar; you may tighten slightly). "
                  "Never swap in a different opinion."]
        for t in thoughts:
            a_ = (t.get("a") or "").strip()
            parts.append(f"Q: {t['q']}\nA: {a_}" if a_ else
                         f"Q: {t['q']}\nA: (the student skipped this: write a short, balanced draft they can rewrite)")
    rules = sources.rules_from(opts)
    level_text = {
        "Trusted only": "Cite ONLY .gov, .edu and .mil sites, museums, libraries, encyclopedias like Britannica, and "
                        "major news outlets (AP, Reuters, NPR, PBS, BBC, major newspapers). No blogs, no company sites.",
        "Balanced": "Prefer .gov/.edu, museums, libraries and established news; avoid blogs, forums and content farms.",
        "Anything relevant": "Any relevant, reliable-looking source is fine, but still never forums or homework sites.",
    }[rules["level"]]
    parts += ["", "SOURCES", f"- {level_text}",
              "- Wikipedia may be cited." if rules["wiki_ok"] else
              "- Never cite Wikipedia (you may read it to find better sources it links to)."]
    if rules["blocked"]:
        parts.append("- Never cite these sites: " + ", ".join(rules["blocked"]) + ".")
    if rules["max_age"]:
        parts.append(f"- Only cite sources published in the last {rules['max_age']} years (historical primary "
                     "documents are fine to mention but cite a recent source about them).")
    focus = opts.get("focus", "Balanced")
    parts += ["", "FOCUS", {
        "Strict": "- Stay strictly on the assignment: every slide and section must directly answer the prompt or a "
                  "rubric criterion. No extra background, side topics or fun facts.",
        "Balanced": "- Cover everything the assignment asks first; a little background is fine where it helps.",
        "Explore": "- Cover everything the assignment asks, then add interesting context, background and "
                   "connections that make the project stand out.",
    }.get(focus, "- Cover everything the assignment asks first; a little background is fine where it helps.")]
    cite = opts.get("citation", "Simple")
    if cite in ("MLA", "APA", "Chicago"):
        intext = {"MLA": "(Author) or (Author page)", "APA": "(Author, Year)",
                  "Chicago": "(Author Year)"}[cite]
        parts += [
            "",
            "CITATIONS",
            f"- The teacher wants {cite} style. For every source fill author, publisher and date "
            "exactly as the page shows them (leave a field empty rather than guessing).",
            f"- In the one-pager, put short {cite} in-text citations like {intext} after facts, "
            "numbers and quotes. Use a short title in quotes if there is no author.",
        ]
    sample = (opts.get("voice_sample") or "").strip()
    if sample:
        parts += [
            "- Match the vocabulary, sentence rhythm and level of formality of this writing "
            "sample from the student (do not copy its content):",
            "<writing_sample>", sample[:4000], "</writing_sample>",
        ]
    out = opts.get("output", "Both")
    if out == "Slideshow":
        parts.append("\nOUTPUT: slides only (one_pager may be omitted).")
    elif out == "One-pager":
        parts.append("\nOUTPUT: one_pager only (slides may be omitted). Keep it to what fits on "
                     "one printed page: ~350-450 words, 3-4 sections, 2-4 key stats.")
    else:
        parts.append("\nOUTPUT: both slides and one_pager. One-pager ~350-450 words, "
                     "3-4 sections, 2-4 key stats.")
    if opts.get("current_event", True):
        parts += [
            "",
            "CURRENT EVENT",
            f"- Today is {dt.date.today():%B %d, %Y}. Use news_search to find ONE real news event from "
            "about the last two years that shows why this topic matters now. Prefer established news "
            "outlets (AP, Reuters, NPR, PBS, BBC, major newspapers) over blogs or entertainment sites.",
            "- Fill current_event with its exact date, a factual summary, and a clear connection to the "
            "topic. Use only an article you actually saw in results, and add it to sources too.",
            "- The summary must say what specifically happened: who did or said what, where, and when. "
            "Not a general trend like 'experts continue to debate'. If results are vague, search again.",
            "- Don't write a news slide yourself: the app adds an 'In the News' slide and one-pager box "
            "from current_event. Refer to it in rubric_check where a criterion asks for a current event.",
        ]
    parts.append("\nrubric_check: one entry per rubric criterion, honest status, and a tip "
                 "for anything partial or missing. Mark a criterion 'met' only if the project "
                 "really contains it (e.g. a current-event criterion needs a real, dated current_event; "
                 "a visuals criterion needs image_query photos).")
    return "\n".join(parts)


def build_user(opts):
    if opts.get("revise"):
        rv = opts["revise"]
        return (
            "<current_project>\n" + json.dumps(rv["spec"], ensure_ascii=False) + "\n</current_project>\n\n"
            "<change_request>\n" + rv["instruction"].strip() + "\n</change_request>\n\n"
            "This is a project you already made for this assignment:\n"
            "<assignment_prompt>\n" + (opts.get("prompt") or "").strip() + "\n</assignment_prompt>\n"
            "<rubric>\n" + ((opts.get("rubric") or "").strip() or "(none given)") + "\n</rubric>\n\n"
            "Apply the change request. Change only what it asks for (plus anything that must change "
            "to stay consistent) and keep everything else exactly as it is, including image_query "
            "values and sources. Search only if the change needs new facts. Then call submit_project "
            "with the COMPLETE updated project and an updated rubric_check."
        )
    return (
        "<assignment_prompt>\n" + (opts.get("prompt") or "").strip() + "\n</assignment_prompt>\n\n"
        "<rubric>\n" + ((opts.get("rubric") or "").strip() or "(none given)") + "\n</rubric>\n\n"
        "<task>\n" + ((opts.get("task") or "").strip() or "(none given)") + "\n</task>\n\n"
        + (("<existing_file kind=\"" + opts["existing_kind"] + "\">\n" + opts["existing_text"] +
            "\n</existing_file>\nFolio will ADD your content to this existing Google file. Build on what is "
            "already there: don't repeat it, match its topic and level, and skip a title slide if it "
            "already has one.\n\n") if opts.get("existing_text") else "")
        + "Research the topic, then call submit_project."
    )


# ---------------------------------------------------------------- search backends
def ddg_search(query, n=6):
    if DDGS is None:
        return {"error": "DuckDuckGo search is unavailable (ddgs not installed)."}
    try:
        with DDGS() as d:
            res = list(d.text(query, max_results=n))
        return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                for r in res]
    except Exception as e:
        return {"error": f"DuckDuckGo search failed: {e}"}


def news_search(query, n=8):
    if DDGS is None:
        return {"error": "News search is unavailable (ddgs not installed)."}
    try:
        with DDGS() as d:
            res = list(d.news(query, max_results=n, timelimit="y"))
            if len(res) < 3:  # nothing this year: widen to anything recent
                res += [r for r in d.news(query, max_results=n) if r not in res]
        return [{"title": r.get("title"), "url": r.get("url"), "date": (r.get("date") or "")[:10],
                 "outlet": r.get("source"), "snippet": r.get("body")} for r in res[:n]]
    except Exception as e:
        return {"error": f"News search failed: {e}"}


def brave_search(query, key):
    if requests is None:
        return {"error": "requests not installed"}
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search",
                         params={"q": query, "count": 8},
                         headers={"X-Subscription-Token": key, "Accept": "application/json"},
                         timeout=20)
        r.raise_for_status()
        items = r.json().get("web", {}).get("results", [])
        return [{"title": i.get("title"), "url": i.get("url"),
                 "snippet": re.sub("<[^>]+>", "", i.get("description", ""))} for i in items]
    except Exception as e:
        return {"error": f"Brave search failed: {e}"}


# ---------------------------------------------------------------- main run
class Cancelled(Exception):
    pass


def run(opts, progress, cancel: threading.Event):
    """Returns the project spec dict. `progress(kind, text, extra=None)` reports status."""
    if opts.get("provider") == "Gemini":
        return run_gemini(opts, progress, cancel)
    return run_claude(opts, progress, cancel)


def run_claude(opts, progress, cancel):
    key = (opts.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("Add your Anthropic API key in Settings first.")
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)
    model = opts.get("model") or "claude-sonnet-5-5"
    engines = opts.get("engines") or {}
    max_searches = DEPTH.get(opts.get("depth", "Standard"), 8)

    tools = []
    search_versions = ["web_search_20260318", "web_search_20250305"]
    if engines.get("claude", True):
        ws = {"type": search_versions[0], "name": "web_search", "max_uses": max_searches}
        rules = sources.rules_from(opts)
        block = rules["blocked"] + ([] if rules["wiki_ok"] else ["wikipedia.org"])
        if block:  # Claude's own search runs server-side, so the student's blocks are passed to it directly
            ws["blocked_domains"] = sorted(set(block))[:64]
        tools.append(ws)
    if engines.get("duckduckgo", True):
        tools.append(DDG_TOOL)
    if engines.get("brave") and opts.get("brave_key"):
        tools.append(BRAVE_TOOL)
    if opts.get("current_event", True) or opts.get("revise"):
        tools.append(NEWS_TOOL)
    tools.append(SUBMIT_TOOL)

    system = build_system(opts)
    messages = [{"role": "user", "content": build_user(opts)}]
    seen_sources = {}
    client_searches = 0
    server_searches = 0
    pushed_back = False
    nudged = False

    progress("stage", "research", None)
    for step in range(40):
        if cancel.is_set():
            raise Cancelled()
        try:
            with client.messages.stream(model=model, max_tokens=20000, system=system,
                                        tools=tools, messages=messages) as stream:
                for ev in stream:
                    if cancel.is_set():
                        raise Cancelled()
                    if ev.type == "content_block_start":
                        cb = ev.content_block
                        if getattr(cb, "type", "") == "tool_use" and cb.name == "submit_project":
                            progress("stage", "write", None)
                resp = stream.get_final_message()
        except anthropic.BadRequestError as e:
            msg = str(e)
            if "web_search_2026" in msg and tools and tools[0].get("type") == search_versions[0]:
                tools[0]["type"] = search_versions[1]  # older API fallback
                continue
            raise
        messages.append({"role": "assistant", "content": resp.content})

        spec = None
        results = []
        for b in resp.content:
            t = getattr(b, "type", "")
            if t == "server_tool_use":
                server_searches += 1
                q = (getattr(b, "input", None) or {}).get("query", "")
                progress("search", f"Web search · {q}", {"engine": "Claude"})
            elif t == "web_search_tool_result":
                content = getattr(b, "content", None)
                if isinstance(content, list):
                    for r in content:
                        url = getattr(r, "url", None)
                        if url and url not in seen_sources:
                            seen_sources[url] = getattr(r, "title", url)
                    progress("sources", str(len(seen_sources)), None)
            elif t == "text" and getattr(b, "text", "").strip():
                progress("thought", b.text.strip()[:220], None)
            elif t == "tool_use":
                if b.name == "submit_project":
                    early = "" if pushed_back else _too_early(opts, client_searches + server_searches)
                    if early:
                        pushed_back = True
                        progress("thought", "Sent it back for more research before writing.", None)
                        progress("stage", "research", None)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": early, "is_error": True})
                    else:
                        spec = b.input
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": "Received."})
                elif b.name in ("duckduckgo_search", "brave_search", "news_search"):
                    q = b.input.get("query", "")
                    client_searches += 1
                    if client_searches > max_searches + 4:
                        out = {"error": "Search budget used up. Write the project now."}
                    elif b.name == "news_search":
                        progress("search", f"News · {q}", {"engine": "News"})
                        out = news_search(q)
                    elif b.name == "duckduckgo_search":
                        progress("search", f"DuckDuckGo · {q}", {"engine": "DuckDuckGo"})
                        out = ddg_search(q, int(b.input.get("max_results", 6)))
                    else:
                        progress("search", f"Brave · {q}", {"engine": "Brave"})
                        out = brave_search(q, opts.get("brave_key"))
                    out = sources.filter_results(out, opts)
                    if isinstance(out, list):
                        for r in out:
                            if r.get("url") and r["url"] not in seen_sources:
                                seen_sources[r["url"]] = r.get("title") or r["url"]
                        progress("sources", str(len(seen_sources)), None)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out)[:12000]})
                else:
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": "Unknown tool", "is_error": True})

        if spec is not None:
            progress("stage", "build", None)
            if not spec.get("sources"):
                spec["sources"] = [{"title": t, "url": u} for u, t in list(seen_sources.items())[:12]]
            spec["_all_seen"] = [{"title": t, "url": u} for u, t in seen_sources.items()]
            return spec

        if resp.stop_reason == "pause_turn":
            continue
        if results:
            if step == 1:
                progress("stage", "research", None)
            messages.append({"role": "user", "content": results})
            continue
        # model stopped without submitting
        if nudged:
            raise RuntimeError("The model finished without producing a project. Try again.")
        nudged = True
        progress("stage", "write", None)
        messages.append({"role": "user", "content": "Research is complete. Call submit_project now "
                                                    "with the full finished project."})
        if resp.stop_reason == "max_tokens":
            continue
    raise RuntimeError("Took too many steps without finishing. Try 'Quick' research depth.")


# ================================================================ Gemini
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gschema(sch):
    """Convert our JSON schema into Gemini's OpenAPI-subset schema."""
    out = {}
    t = sch.get("type")
    if t:
        out["type"] = t.upper()
    for k in ("description", "enum"):
        if k in sch:
            out[k] = sch[k]
    if "properties" in sch:
        out["properties"] = {k: _gschema(v) for k, v in sch["properties"].items()}
    if "items" in sch:
        out["items"] = _gschema(sch["items"])
    if sch.get("required"):
        out["required"] = list(sch["required"])
    return out


def _gfunc(tool):
    return {"name": tool["name"], "description": tool["description"],
            "parameters": _gschema(tool["input_schema"])}


class GeminiError(RuntimeError):
    pass


def _gpost(key, model, body, cancel, progress, attempts=6):
    import time
    url = GEMINI_URL.format(model=model)
    for attempt in range(attempts):
        if cancel.is_set():
            raise Cancelled()
        try:
            r = requests.post(url, params={"key": key}, json=body, timeout=300)
        except Exception as e:
            if attempt >= 2:
                raise GeminiError(f"Couldn't reach Gemini: {e}")
            time.sleep(3)
            continue
        if r.status_code == 200:
            return r.json()
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {"message": r.text[:300]}
        msg = err.get("message", "")
        if r.status_code in (429, 500, 503) and attempt < attempts - 1:
            wait = 20
            for d in err.get("details", []) or []:
                rd = d.get("retryDelay")
                if rd:
                    try:
                        wait = float(str(rd).rstrip("s")) + 1
                    except ValueError:
                        pass
            if "per day" in msg.lower() or "PerDay" in msg:
                raise GeminiError("You've hit Gemini's free daily limit. It resets in 24 hours, "
                                  "or you can switch to Claude or a paid Gemini plan in Settings.")
            wait = min(wait, 65)
            progress("thought", f"Gemini rate limit hit — waiting {int(wait)}s (free tier)…", None)
            for _ in range(int(wait)):
                if cancel.is_set():
                    raise Cancelled()
                time.sleep(1)
            continue
        if r.status_code in (400, 403) and ("API key" in msg or "API_KEY" in msg or "permission" in msg.lower()):
            raise GeminiError("Your Gemini API key was rejected. Check it in Settings.")
        raise GeminiError(f"Gemini error {r.status_code}: {msg[:300]}")
    raise GeminiError("Gemini kept rate-limiting. Wait a minute and try again, or use Quick research.")


def _gemini_grounded_brief(key, model, opts, progress, cancel, seen):
    """Optional: a Google-Search-grounded research brief (needs a paid Gemini plan)."""
    body = {
        "contents": [{"role": "user", "parts": [{"text":
            "Research this assignment using Google Search and write a factual research brief "
            "(key facts, dates, statistics with their sources, and notable quotes). "
            "Do not write the project itself.\n\n" + build_user(opts)}]}],
        "tools": [{"google_search": {}}],
    }
    progress("search", "Google · grounded research on the assignment", {"engine": "Google"})
    data = _gpost(key, model, body, cancel, progress)
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    gm = cand.get("groundingMetadata") or {}
    for q in gm.get("webSearchQueries") or []:
        progress("search", f"Google · {q}", {"engine": "Google"})
    for ch in gm.get("groundingChunks") or []:
        w = ch.get("web") or {}
        if w.get("uri") and w["uri"] not in seen:
            seen[w["uri"]] = w.get("title") or w["uri"]
    progress("sources", str(len(seen)), None)
    return text


def run_gemini(opts, progress, cancel):
    if requests is None:
        raise RuntimeError("The 'requests' package is missing. Re-run Setup.")
    key = (opts.get("gemini_key") or "").strip()
    if not key:
        raise RuntimeError("Add your Gemini API key in Settings first.")
    model = opts.get("gemini_model") or "gemini-3.8-flash"
    engines = opts.get("engines") or {}
    max_searches = DEPTH.get(opts.get("depth", "Standard"), 8)
    seen = {}

    progress("stage", "research", None)
    brief = ""
    if engines.get("claude", False) and not opts.get("revise"):  # in Gemini mode this box means "Google Search"
        try:
            brief = _gemini_grounded_brief(key, model, opts, progress, cancel, seen)
        except Cancelled:
            raise
        except GeminiError as e:
            progress("thought", "Google Search grounding isn't available on this key "
                                "(it needs a paid Gemini plan) — using the other engines.", None)

    funcs = []
    if engines.get("duckduckgo", True):
        funcs.append(_gfunc(DDG_TOOL))
    if engines.get("brave") and opts.get("brave_key"):
        funcs.append(_gfunc(BRAVE_TOOL))
    if opts.get("current_event", True) or opts.get("revise"):
        funcs.append(_gfunc(NEWS_TOOL))
    funcs.append(_gfunc(SUBMIT_TOOL))

    user = build_user(opts)
    if brief:
        user += ("\n\n<research_brief source=\"Google Search\">\n" + brief[:12000] +
                 "\n</research_brief>\nUse this brief plus your own searches.")
    if not engines.get("duckduckgo", True) and not (engines.get("brave") and opts.get("brave_key")):
        user += "\n\nNo search tool is available; rely on the research brief."
    contents = [{"role": "user", "parts": [{"text": user}]}]
    body_base = {
        "systemInstruction": {"parts": [{"text": build_system(opts) +
            "\n\nYou must finish by calling the submit_project function."}]},
        "tools": [{"functionDeclarations": funcs}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 32000},
    }
    client_searches = 0
    pushed_back = False
    for step in range(30):
        if cancel.is_set():
            raise Cancelled()
        force_submit = client_searches >= max_searches
        body = dict(body_base, contents=contents)
        if force_submit:
            progress("stage", "write", None)
            body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY",
                                                           "allowedFunctionNames": ["submit_project"]}}
        data = _gpost(key, model, body, cancel, progress)
        cands = data.get("candidates") or []
        if not cands:
            fb = data.get("promptFeedback", {}).get("blockReason")
            raise RuntimeError(f"Gemini returned nothing{f' ({fb})' if fb else ''}. Try rewording.")
        content = cands[0].get("content") or {"role": "model", "parts": []}
        content["role"] = "model"
        contents.append(content)
        responses = []
        for part in content.get("parts", []):
            if part.get("text") and not part.get("thought"):
                progress("thought", part["text"].strip()[:220], None)
            fc = part.get("functionCall")
            if not fc:
                continue
            name, args = fc.get("name"), fc.get("args") or {}
            if name == "submit_project":
                early = "" if pushed_back else _too_early(opts, client_searches + (1 if brief else 0))
                if early:
                    pushed_back = True
                    progress("thought", "Sent it back for more research before writing.", None)
                    progress("stage", "research", None)
                    fr = {"name": name, "response": {"error": early}}
                    if fc.get("id"):
                        fr["id"] = fc["id"]
                    responses.append({"functionResponse": fr})
                    continue
                progress("stage", "build", None)
                spec = args
                if not spec.get("sources"):
                    spec["sources"] = [{"title": t, "url": u} for u, t in list(seen.items())[:12]]
                spec["_all_seen"] = [{"title": t, "url": u} for u, t in seen.items()]
                return spec
            q = args.get("query", "")
            client_searches += 1
            if name == "duckduckgo_search":
                progress("search", f"DuckDuckGo · {q}", {"engine": "DuckDuckGo"})
                out = ddg_search(q, int(args.get("max_results", 6) or 6))
            elif name == "brave_search":
                progress("search", f"Brave · {q}", {"engine": "Brave"})
                out = brave_search(q, opts.get("brave_key"))
            elif name == "news_search":
                progress("search", f"News · {q}", {"engine": "News"})
                out = news_search(q)
            else:
                out = {"error": "unknown function"}
            out = sources.filter_results(out, opts)
            if isinstance(out, list):
                for r in out:
                    if r.get("url") and r["url"] not in seen:
                        seen[r["url"]] = r.get("title") or r["url"]
                progress("sources", str(len(seen)), None)
            fr = {"name": name, "response": {"results": out} if isinstance(out, list) else out}
            if fc.get("id"):
                fr["id"] = fc["id"]
            responses.append({"functionResponse": fr})
        if responses:
            contents.append({"role": "user", "parts": responses})
        else:
            contents.append({"role": "user", "parts": [{"text": "Call submit_project now with the "
                                                                "complete finished project."}]})
    raise RuntimeError("Gemini took too many steps without finishing. Try 'Quick' research.")


# ================================================================ writing into the student's own doc
EDITS_TOOL = {
    "name": "submit_edits",
    "description": "Submit the text to add to the student's Google Doc. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "after": {"type": "string", "description":
                                  "A line copied EXACTLY from the doc (character for character, one line, "
                                  "under 120 characters) that this text goes right after. Empty string = end of doc."},
                        "replace": {"type": "string", "description":
                                    "Optional. To FIX existing text instead of adding: the exact existing text "
                                    "(copied from one line, under 120 characters) that 'text' should replace. "
                                    "Leave empty to add after 'after'."},
                        "text": {"type": "string", "description": "What to add (or the replacement). Plain text, no markdown."},
                        "why": {"type": "string", "description": "Very short note for the student: what this adds or fixes"},
                    },
                    "required": ["after", "text"],
                },
            },
        },
        "required": ["edits"],
    },
}


def plan_doc_edits(opts, doc_name, doc_text, spec, instruction, cancel=None):
    """Ask the AI what to add to an existing doc (e.g. answers under each question), using only
    the facts already in the student's Folio project. Returns [{'after', 'text', 'why'}]."""
    cancel = cancel or threading.Event()
    project = {k: v for k, v in (spec or {}).items() if not k.startswith("_")}
    system = "\n".join([
        "You help a student fill in their own copy of a teacher's Google Doc (a worksheet or research "
        "template) using research they already did.",
        "- Only use facts from <project>. Never invent facts, dates, quotes or sources.",
        f"- Reading level: {opts.get('reading_level', 'High school')}. Write like the student, in plain, "
        "specific sentences, first person where the doc asks about their own life.",
        "- Put each answer right after the exact line that asks for it. 'after' must be copied exactly from "
        "one line of <doc>. Skip anything the doc already answers well.",
        "- If existing text is wrong or should be improved, use 'replace' with that exact text instead of adding "
        "a second answer. Never replace the teacher's questions or directions, only the student's own writing.",
        "- Keep answers short: 1-4 sentences each unless the doc asks for more.",
        f"- Avoid these phrases: {AVOID}.",
    ])
    user = (f"<doc name=\"{doc_name}\">\n{doc_text[:15000]}\n</doc>\n\n"
            f"<project>\n{json.dumps(project, ensure_ascii=False)[:20000]}\n</project>\n\n"
            f"<request>\n{instruction.strip() or 'Answer each question in this doc right under the question.'}\n"
            "</request>\n\nCall submit_edits.")
    return call_tool(opts, system, user, EDITS_TOOL, cancel).get("edits") or []


QUESTIONS_TOOL = {
    "name": "list_questions",
    "description": "List each question/prompt in the doc that needs a researched answer, with a web "
                   "search query for it. Call exactly once.",
    "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {
        "type": "object", "properties": {
            "after": {"type": "string", "description": "The exact line from the doc that asks this "
                      "(copied verbatim, one line, under 120 characters)."},
            "query": {"type": "string", "description": "A web search query to research the answer."},
        }, "required": ["after", "query"]}}}, "required": ["questions"]},
}


def research_fill(opts, doc_name, doc_text, assignment, progress=None, cancel=None):
    """Read a worksheet/question doc, research each question fresh on the web, and return answers to
    add under each question. Returns [{'after','text','why'}]. Facts come only from the research."""
    cancel = cancel or threading.Event()
    progress = progress or (lambda *a: None)

    sys1 = ("You read a student's worksheet or question doc and list the questions that need a researched, "
            "factual answer, each with a good web search query. Skip questions the doc already answers, and "
            "skip personal-opinion questions that research can't answer.")
    user1 = (f"<assignment>\n{(assignment or '')[:4000]}\n</assignment>\n"
             f"<doc name=\"{doc_name}\">\n{doc_text[:15000]}\n</doc>\n\nCall list_questions.")
    questions = (call_tool(opts, sys1, user1, QUESTIONS_TOOL, cancel).get("questions") or [])[:12]
    if not questions:  # nothing to research → answer from the doc + assignment alone
        return plan_doc_edits(opts, doc_name, doc_text, {"assignment": assignment}, "", cancel)

    key = (opts.get("brave_key") or "").strip()
    research = []
    for i, q in enumerate(questions, 1):
        if cancel.is_set():
            raise Cancelled()
        query = (q.get("query") or q.get("after") or "").strip()
        if not query:
            continue
        progress("thought", f"Researching {i} of {len(questions)}: {query[:70]}…")
        res = brave_search(query, key) if key else ddg_search(query, 6)
        res = [] if isinstance(res, dict) else res
        try:
            res = sources.filter_results(res, opts) or res
        except Exception:
            pass
        srcs = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet")} for r in res[:3]]
        if res:  # pull one real page for depth beyond the snippet
            try:
                import review
                txt = review._page_text(res[0].get("url") or "")
                if txt:
                    srcs.append({"url": res[0].get("url"),
                                 "excerpt": re.sub(r"\s+", " ", txt).strip()[:1500]})
            except Exception:
                pass
        research.append({"question": q.get("after"), "sources": srcs})

    sys2 = "\n".join([
        "You fill in a student's own copy of a teacher's Google Doc using the web research provided.",
        f"- Reading level: {opts.get('reading_level', 'High school')}. Write in plain, specific sentences, "
        "like the student, first person where the doc asks about their own life.",
        "- Put each answer right after the exact line that asks for it. 'after' must be copied EXACTLY from "
        "one line of <doc>. Never replace the teacher's questions or directions.",
        "- Base every factual claim on <research>; never invent facts, dates, numbers, quotes or sources.",
        "- Keep answers short: 1-4 sentences unless the doc asks for more. Skip anything already answered.",
        f"- Avoid these phrases: {AVOID}.",
    ])
    user2 = (f"<doc name=\"{doc_name}\">\n{doc_text[:15000]}\n</doc>\n\n"
             f"<research>\n{json.dumps(research, ensure_ascii=False)[:24000]}\n</research>\n\nCall submit_edits.")
    return call_tool(opts, sys2, user2, EDITS_TOOL, cancel).get("edits") or []


def call_tool(opts, system, user, tool, cancel=None, max_tokens=16000, images=None, patient=True):
    """One AI call that must answer by calling `tool`; returns the tool's arguments as a dict.
    images: optional list of JPEG bytes shown to the AI before the text, in order."""
    import base64
    cancel = cancel or threading.Event()
    images = images or []
    if opts.get("provider") == "Gemini":
        key = (opts.get("gemini_key") or "").strip()
        if not key:
            raise RuntimeError("Add your Gemini API key in Settings first.")
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [
                    {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(b).decode()}} for b in images
                ] + [{"text": user}]}],
                "tools": [{"functionDeclarations": [_gfunc(tool)]}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool["name"]]}},
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}}
        data = _gpost(key, opts.get("gemini_model") or "gemini-3.8-flash", body, cancel, lambda *a: None,
                      attempts=6 if patient else 1)
        for part in ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts", []):
            fc = part.get("functionCall")
            if fc and fc.get("name") == tool["name"]:
                return fc.get("args") or {}
        raise RuntimeError("The AI didn't answer. Try again.")
    key = (opts.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("Add your Anthropic API key in Settings first.")
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)
    resp = client.messages.create(model=opts.get("model") or "claude-sonnet-5-5", max_tokens=max_tokens,
                                  system=system + f"\n\nAnswer by calling {tool['name']}.", tools=[tool],
                                  tool_choice={"type": "auto"}, messages=[{"role": "user", "content": [
                                      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                                   "data": base64.b64encode(b).decode()}}
                                      for b in images] + [{"type": "text", "text": user}]}])
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use" and b.name == tool["name"]:
            return b.input or {}
    raise RuntimeError("The AI didn't answer. Try again.")


# ================================================================ presentation coach
QUIZ_TOOL = {
    "name": "submit_quiz",
    "description": "Practice questions a teacher or classmate might ask about this project.",
    "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {
        "type": "object", "properties": {
            "q": {"type": "string"}, "a": {"type": "string", "description": "short correct answer from the project"},
            "kind": {"type": "string", "enum": ["fact", "why", "connect"]}}}}}, "required": ["questions"]},
}


def make_quiz(opts, project_text):
    """8 questions that check the student really knows their own project, answers taken from it."""
    system = ("You help a student get ready to present their project and answer questions about it. Write 8 "
              "questions a teacher might ask: about 3 fact questions (dates, names, numbers), 3 'why' questions, "
              "and 2 that connect the topic to today or to the student's life. Answers must come from the project "
              "and be 1-2 sentences.")
    return call_tool(opts, system, f"<project>\n{project_text[:20000]}\n</project>", QUIZ_TOOL,
                     max_tokens=4000).get("questions") or []


# ================================================================ the student's own thoughts
PERSONAL_TOOL = {
    "name": "submit_personal_questions",
    "description": "List the parts of the assignment that ask for the student's own opinion or experience.",
    "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
        "q": {"type": "string", "description": "the question, rephrased simply and addressed to the student"},
        "hint": {"type": "string", "description": "one short idea to get them started, as a question"}},
        "required": ["q"]}}}, "required": ["questions"]},
}


def personal_questions(opts):
    """Questions only the student can answer (opinions, their life, reflections), max 4."""
    text = "\n".join(str(opts.get(k) or "") for k in ("prompt", "rubric", "task")).strip()
    if not text:
        return []
    system = ("Find the parts of this school assignment that ask for the STUDENT'S OWN opinion, experience or "
              "reflection (e.g. 'does this affect your life', 'what do you think', 'reflect on', 'would you'). "
              "Do not include factual research questions. Return at most 4; return none if there are none.")
    # a quick look before starting: if the AI is busy, skip the questions rather than make the student wait
    qs = call_tool(opts, system, text[:12000], PERSONAL_TOOL, max_tokens=1500, patient=False).get("questions") or []
    return [q for q in qs if (q.get("q") or "").strip()][:4]
