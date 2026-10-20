# Folio (web)

Research studio for slideshows and one-pagers, on your phone or Chromebook.

## Run it on a spare PC, open it from a Chromebook (free)

Best setup if you have a Windows PC that can stay on: it runs the full Folio (Classroom included, with a
real Chrome), and a free Cloudflare tunnel gives it a public link your Chromebook opens — nothing to
install on the Chromebook.

1. On the spare PC, install **Python 3.12** from python.org (tick "Add python.exe to PATH").
2. Put these files on the PC (download this repo as a ZIP, or copy the folder over).
3. Double-click **start-folio.bat**. The first run makes a **folio.env** file and opens it — fill in your
   `APP_PASSWORD`, `GEMINI_API_KEY` (from aistudio.google.com) and `SECRET_KEY`, save, and run it again.
4. It downloads the tunnel, starts Folio, and prints a link like `https://xxxx.trycloudflare.com`.
   Open that on the Chromebook and sign in with your password.

Keep the window open (closing it takes Folio offline). The link changes each time you start it. Your
projects and Classroom sign-in stay on the PC between runs. If the PC is usually locked, set
`FOLIO_HEADLESS=1` in folio.env so Chrome runs without a window.

Optional **Browser** tab — a general web browser running on the PC, for your own personal devices: set
`FOLIO_BROWSER_TAB=1` in folio.env. It's off by default. Not for getting around a school device's filter.

## Full Folio with Classroom: GitHub Codespaces (free)

The Codespace runs Folio plus a real Chrome. Open Folio's Classroom tab and tap **Open Classroom**:
you see that Chrome live, sign in to your school account yourself, and Folio then reads your To-do list,
imports assignments and writes into your existing docs, like the Windows app. You click Turn in.

1. **github.com/settings/codespaces → New secret** (give each one access to this repository):
   `APP_PASSWORD` (the password you'll type to open Folio), `GEMINI_API_KEY` (from aistudio.google.com),
   `SECRET_KEY` (any long random text).
2. On this repository: **Code → Codespaces → Create codespace on main**. The first start installs
   everything (about 5 minutes).
3. Open **github.com/codespaces**, tap the codespace, then **Ports → 7860 → Open in browser**
   (or the *Folio* link it shows). Add it to your Home Screen.

Free accounts get about 60 hours a month on the 2-core machine. The codespace stops itself when idle;
start it again from github.com/codespaces. Projects and the Classroom sign-in stay in it.

## Render (free) — Classroom included

1. On render.com: **New → Web Service**, pick this repository, Language **Docker**, Instance type **Free**.
2. Add the three **Environment Variables**: `APP_PASSWORD`, `GEMINI_API_KEY`, `SECRET_KEY`.

Classroom works the same way here: open the Classroom tab, sign in through the live view, and Folio reads
your To-do list, imports assignments and writes into your existing docs. You click Turn in.

To fit Render's free 512 MB, the server's Chrome runs lean and headless and launches only while you're in
Classroom, then shuts down a few minutes after you leave so slide-making gets the memory back.
Notes and limits:

- The free instance sleeps after 15 minutes unused (about a minute to wake), and its disk is wiped on
  restart or redeploy, so you sign in to Classroom again after a cold start. Download your files to keep them.
- Signing in to Google the first time from a server can trigger a "verify it's you" step — do it in the live
  view like any sign-in. Once in, the session sticks until the next cold start.
- Tune the idle shutdown with `FOLIO_CR_IDLE` (seconds, default 360). Set `FOLIO_SINGLE_PROCESS=1` to squeeze
  memory harder if you hit out-of-memory, at some cost to stability.
- If Chrome and slide-making ever fight for memory on the free box, the paid step that actually helps is
  Render **Standard** (2 GB); Starter is still 512 MB.
