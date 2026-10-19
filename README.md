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

## Without Classroom: Render (free)

1. On render.com: **New → Web Service**, pick this repository, Language **Docker**, Instance type **Free**.
2. Add the same three **Environment Variables**.

The free instance sleeps after 15 minutes unused and takes about a minute to wake up.
Projects live on temporary disk and disappear when it restarts or redeploys, so download your files.
