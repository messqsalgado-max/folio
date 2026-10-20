# Folio — Project Handoff

A context document so another AI (or a future session) can continue this project. Read this top to bottom first.

## What Folio is
A Windows desktop app that researches school assignments and produces **slideshows (.pptx)** and
**one-pagers (.pdf/.docx)** using Gemini or Claude. It also has a **Google Classroom** feature (reads
your To-do list, imports an assignment, and writes into the file already attached to it — never a new
file, never clicks Turn in). There is also a **web version** of the same app for phone/Chromebook.

- Desktop code lives in `C:\Users\jayde\Documents\Folio` (a git repo, branch `main`).
- The web version is published to GitHub: **github.com/messqsalgado-max/folio** (a flat bundle).

## Two things that run

### 1. Desktop app (Windows)
- Entry point: `Folio.py` (a `pywebview` WebView2 window; `Api` class exposes methods to `ui.html`).
- UI: `ui.html` (single file: HTML + CSS + one big `<script>`).
- Engine: `engine.py` (Gemini REST + Anthropic SDK), `review.py` (quality/fact checks), `render.py`
  (builds pptx/pdf/docx), `images.py` (photos), `sources.py` (source filtering), `classroom.py`
  (drives a Classroom browser window via WebView2 CDP), `vault.py` (keys in Windows Credential Manager),
  `reminders.py` (toast notifications), `google_link.py` (Google Drive/Docs API).
- Build: `auto_build.py` (PyInstaller onedir → `Folio.exe` + `Folio.zip` on Desktop).

### 2. Web app (phone / Chromebook) — this is where recent work has been
- `webapp.py` — Flask server that reuses Folio's own `Api` as `WebApi`. Key points:
  - **Per-device isolation**: each device that signs in gets a random `uid` cookie → its own folder
    under `FOLIO_DATA/users/<uid>` (own projects, settings, and its own Classroom Google sign-in / browser
    profile), its own `WebApi` instance + events. Same `APP_PASSWORD` for all; password gates access, uid
    separates data. `load_settings`/`save_settings`/`CR_CACHE` are made per-device via a threadlocal `_ctx.uid`.
  - AI keys come from env (`GEMINI_API_KEY` / `ANTHROPIC_API_KEY`), masked from the browser as
    `"(saved on the server)"`. Login password is `APP_PASSWORD`; `SECRET_KEY` signs the cookie.
  - `ALLOWED` is the method allowlist exposed to the browser.
- `web/bridge.js` — stands in for `window.pywebview.api` so `ui.html` runs unchanged in a browser;
  polls `/api/events`; contains the **live-view component** `makeLive(cfg)` used by both Classroom and
  the Browser tab.
- Classroom on the web uses **`cloudbrowser.py`**: a real Chrome runs on the server (Playwright), the
  student sees it live and signs in themselves; `classroom.py` then drives it. Chrome launches only while
  in use and an idle watchdog (`FOLIO_CR_IDLE`, default 360s) shuts it down to free memory.
- Extra web features added: a **Games** tab (2048, Snake, Memory, Tetris, Minesweeper, Breakout), and an optional **Browser** tab (a
  general web browser running on the PC, for personal devices — OFF by default via `FOLIO_BROWSER_TAB=1`).

## Hosting options (all set up)
1. **Spare mini PC (primary) + Cloudflare tunnel** → fixed domain **folio.gripe**.
   - The user's mini Dell (Ryzen 5 3550H, 16 GB) runs Folio via `start-folio.bat`.
   - `pc_launcher.py` orchestrates: installs Python/deps/Chrome/cloudflared if missing, runs `webapp.py`,
     and runs a **Cloudflare named tunnel** (`setup-domain.bat` does the one-time Cloudflare login + tunnel
     create + DNS route; `start-folio.bat` auto-runs that setup the first time if a domain is set but not
     connected). Falls back to a random `trycloudflare.com` link if no domain is configured.
   - Batch files: `start-folio.bat`, `update-folio.bat` (pulls latest from GitHub, keeps `folio.env`/data),
     `enable-browser.bat` / `disable-browser.bat` (toggle the Browser tab), `setup-domain.bat`.
   - `folio.env` holds `APP_PASSWORD`, `GEMINI_API_KEY`, `SECRET_KEY`, and optional `FOLIO_HEADLESS`,
     `FOLIO_BROWSER_TAB`, `FOLIO_TUNNEL_HOSTNAME`.
2. **Render (free, Docker)** — no PC needed, but 512 MB (Classroom runs lean/headless, one-at-a-time,
   sleeps, re-sign-in after cold start). `web/Dockerfile`, `web/SPACE_README.md`.
3. **GitHub Codespaces** — 8 GB, headful under Xvfb. `web/devcontainer/`.
   (Hugging Face Spaces was dropped — Docker Spaces went paid in 2026.)

The flat GitHub bundle is built by `web/make_space.py` → `Desktop/Folio-web` + `Folio-web.zip`. After
any web change: rebuild the bundle, commit in the Folio repo, then copy the bundle over a clone of
`messqsalgado-max/folio` and push.

## The Browser tab's streaming (most recent work, important context)
Goal was a smooth remote browser on the Chromebook. Progression:
1. Started with per-frame **polling** (~14 fps).
2. Tried **MJPEG** (`multipart/x-mixed-replace`) — fast locally but **Cloudflare buffers it**, so it
   showed a broken-image/white screen through folio.gripe.
3. Current solution: **WebSocket** at `/ws/web` (via `flask-sock`). Frames (binary JPEG) go down, input
   (JSON) goes up, one pipe — **this survives the Cloudflare tunnel**. Input is read on a separate thread
   so sending frames isn't blocked; scroll events are coalesced client-side. Falls back to polling if the
   socket fails.
- **Hard limit, well understood**: true 60 fps is NOT achievable over a home tunnel. At ~15 fps the stream
  was already ~14 Mbps; 60 fps ≈ 55+ Mbps upload, which home internet doesn't have. Screenshots are also
  captured/encoded one at a time on a single Playwright worker thread. Realistic ceiling over folio.gripe:
  **~15–30 fps** — smooth for reading/clicking/normal scroll, not for fullscreen video.
- The capture benchmark (`b.shot`) is ~58 fps locally/headless, so capture isn't the remote bottleneck —
  **bandwidth is**.
- An offered-but-not-built next step: a **Quality: Smooth ↔ Sharp** toggle (lower JPEG quality/resolution
  = more fps within bandwidth). `cloudbrowser.shot()` quality is currently 50; frame TTL for polling is
  40 ms on a real PC / 200 ms headless (`FOLIO_FRAME_MS`).

## Key env vars (web)
`APP_PASSWORD` (required), `GEMINI_API_KEY` and/or `ANTHROPIC_API_KEY`, `SECRET_KEY`, `FOLIO_DATA`,
`FOLIO_CLASSROOM=1` (enable server Chrome), `FOLIO_HEADLESS=1` (no window), `FOLIO_BROWSER_CHANNEL`
(`none`=bundled Chromium, else msedge/chrome), `FOLIO_BROWSER_TAB=1` (personal Browser tab),
`FOLIO_TUNNEL_HOSTNAME` (fixed domain), `FOLIO_CR_IDLE` (seconds).

## How to run / test locally
- Tests: `py -3.12 -m unittest tests.test_folio` (36 tests; includes a `WebApp` class). Must stay green.
- Local web server: `py -3.12 webapp.py` then open `http://localhost:7860` (set `APP_PASSWORD`,
  `SECRET_KEY`, `FOLIO_DATA`; add `FOLIO_CLASSROOM=1 FOLIO_BROWSER_TAB=1` to exercise Classroom/Browser).
- JS sanity: `node -e "new Function(require('fs').readFileSync('web/bridge.js','utf8'))"`.
- The repo's `tests/live_run.py` does live generation (uses `LIVE_OPTS`).

## Conventions / gotchas
- Git user: Messiah Salgado <messqsalgado@gmail.com>. **Commit messages end with**
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (model/version varies by session). PR
  descriptions end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- `pywebview` publishes every *public* attribute on `Api` to the page and recurses into it — keep Api
  internals private (leading underscore) or buttons break.
- The web `<body>` tag is rewritten to add classes `web` / `cr` / `webtab`; `bridge.js` is injected just
  before the last `<script>` and sets `window.FOLIO_CR` / `window.FOLIO_WEBTAB`.
- Behavioral rules the user has set: Classroom edits the EXISTING attached file, never makes a new one and
  never turns anything in. Do NOT build anything to bypass a school device's content filter (they use
  Securly); the Browser tab is explicitly for personal devices only, off by default.
- There are two auto-memory files in `~/.claude/.../memory/`: `folio-web-hosting.md` and
  `classroom-edit-existing-files.md` and `open-windows-on-display-2.md` — useful background.

## Current state
Everything above is built, tested (36 unit tests pass), and pushed to `messqsalgado-max/folio`. The user
runs it on the mini Dell at **https://folio.gripe**. Latest change: the Browser tab now uses WebSocket
streaming (works through the tunnel) with a polling fallback.

## Likely next steps (open/offered)
- A **Quality: Smooth ↔ Sharp** toggle for the Browser (bandwidth↔sharpness) — biggest lever for perceived fps.
- Optionally give Classroom the same WebSocket streaming the Browser now uses.
- A status page showing connected devices / letting the owner shut down idle sessions.
- Point `folio.gripe` at Render instead of the PC if the user wants to go PC-free (tradeoffs apply).
