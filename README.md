# Folio (web)

Research studio for slideshows and one-pagers, on your phone or Chromebook.

## Run it on a spare PC, open it from a Chromebook (free)

Best setup if you have a Windows PC that can stay on: it runs the full Folio (Classroom included, with a
real Chrome), and a free Cloudflare tunnel gives it a public link your Chromebook opens — nothing to
install on the Chromebook.

1. On the spare PC, install **Python 3.12** from python.org (tick "Add python.exe to PATH").
2. Put these files on the PC (download this repo as a ZIP, or copy the folder over).
3. Double-click **start-folio.bat**. The first run makes a **folio.env** file and opens it — fill in your
   `APP_PASSWORD` and `SECRET_KEY`, save, and run it again.
4. It downloads the tunnel, starts Folio, and prints a link like `https://xxxx.trycloudflare.com`.
   Open that on the Chromebook and sign in with your password. Add your own AI API key in Settings;
   each signed-in browser connection stores its key separately.

Keep the window open (closing it takes Folio offline). By default the link changes each time you start it.
Your projects and Classroom sign-in stay on the PC between runs. If the PC is usually locked, set
`FOLIO_HEADLESS=1` in folio.env so Chrome runs without a window.

**A fixed link that never changes** (optional): register a domain on Cloudflare, then double-click
**setup-domain.bat** once. It signs you in to Cloudflare (a browser opens — pick your domain and Authorize),
creates a tunnel and points your domain at Folio. After that, Folio is always at `https://yourdomain`.

Optional **Browser** tab — a personal browser running on the PC with a separate Chrome session and saved
sign-in from Classroom: set `FOLIO_BROWSER_TAB=1` in folio.env. It's off by default and intended for
personal devices, not for getting around a school device's filter. It launches independently and uses the
`FOLIO_CR_IDLE` timeout before closing an idle session.

## Full Folio with Classroom: GitHub Codespaces (free)

The Codespace runs Folio plus a real Chrome. Open Folio's Classroom tab and tap **Open Classroom**:
you see that Chrome live, sign in to your school account yourself, and Folio then reads your To-do list,
imports assignments and writes into your existing docs, like the Windows app. You click Turn in.

1. **github.com/settings/codespaces → New secret** (give each one access to this repository):
   `APP_PASSWORD` (the password you'll type to open Folio), and `SECRET_KEY` (any long random text).
2. On this repository: **Code → Codespaces → Create codespace on main**. The first start installs
   everything (about 5 minutes).
3. Open **github.com/codespaces**, tap the codespace, then **Ports → 7860 → Open in browser**
   (or the *Folio* link it shows). Add it to your Home Screen.

Free accounts get about 60 hours a month on the 2-core machine. The codespace stops itself when idle;
start it again from github.com/codespaces. Projects and the Classroom sign-in stay in it.

## Render (free) — Classroom included

1. On render.com: **New → Web Service**, pick this repository, Language **Docker**, Instance type **Free**.
2. Add the two **Environment Variables**: `APP_PASSWORD`, `SECRET_KEY`.
3. After opening Folio, each signed-in browser connection adds its own AI API key in Settings.

Classroom works the same way here: open the Classroom tab, sign in through the live view, and Folio reads
your To-do list, imports assignments and writes into your existing docs. You click Turn in.

To fit Render's free 512 MB, each browser runs lean and headless, starts only when opened, and shuts down
after its idle timeout so slide-making can use the memory.
Notes and limits:

- The free instance sleeps after 15 minutes unused (about a minute to wake), and its disk is wiped on
  restart or redeploy, so you sign in to Classroom again after a cold start. Download your files to keep them.
- Signing in to Google the first time from a server can trigger a "verify it's you" step — do it in the live
  view like any sign-in. Once in, the session sticks until the next cold start.
- Tune the idle shutdown with `FOLIO_CR_IDLE` (seconds, default 360). Set `FOLIO_SINGLE_PROCESS=1` to squeeze
  memory harder if you hit out-of-memory, at some cost to stability.
- If Chrome and slide-making ever fight for memory on the free box, the paid step that actually helps is
  Render **Standard** (2 GB); Starter is still 512 MB.
