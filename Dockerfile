# Folio web app (Docker: Render, Hugging Face Spaces, ...). Secrets / environment variables:
#   APP_PASSWORD, GEMINI_API_KEY (and/or ANTHROPIC_API_KEY), SECRET_KEY
FROM python:3.12-slim

RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user

ENV PYTHONUNBUFFERED=1 FOLIO_DATA=/tmp/folio HOST=0.0.0.0 PORT=7860
EXPOSE 7860
# one worker (Folio keeps a project's progress in memory), many threads, no request timeout (projects take minutes)
# the host may pick the port (Render sets PORT); 7860 otherwise
CMD gunicorn -w 1 --threads 16 --timeout 0 -b 0.0.0.0:${PORT:-7860} webapp:app
