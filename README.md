# Folio (web)

Research studio for slideshows and one-pagers, on your phone.

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
