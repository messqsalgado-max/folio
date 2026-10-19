# Folio (web)

Research studio for slideshows and one-pagers, on your phone.

## Hosting on Render (free)

1. Put these files in a GitHub repository.
2. On render.com: **New → Web Service**, pick the repository, Language **Docker**, Instance type **Free**.
3. Add these **Environment Variables**:

| Name | What |
|---|---|
| `APP_PASSWORD` | the password you'll type to open Folio |
| `GEMINI_API_KEY` | your Gemini key from aistudio.google.com (or `ANTHROPIC_API_KEY` for Claude) |
| `SECRET_KEY` | any long random text (keeps you signed in) |

The free instance sleeps after 15 minutes unused and takes about a minute to wake up.
Projects live on temporary disk and disappear when it restarts or redeploys, so download your files.
