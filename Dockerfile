# Folio web app (Docker: Render, ...). Required secrets / environment variables:
#   APP_PASSWORD, SECRET_KEY
FROM python:3.12-slim

# Chromium for the Classroom feature lives here so the non-root user can read it
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

RUN useradd -m -u 1000 user
COPY --chown=user . .
USER user

# FOLIO_CLASSROOM=1 turns on the server's Chrome; it only launches while the student is in Classroom,
# and shuts down a few minutes after they leave, so slide-making gets the memory back (Render free = 512 MB).
ENV PYTHONUNBUFFERED=1 FOLIO_DATA=/tmp/folio HOST=0.0.0.0 PORT=7860 \
    FOLIO_CLASSROOM=1 FOLIO_HEADLESS=1 FOLIO_BROWSER_CHANNEL=none
EXPOSE 7860
# one worker (Folio keeps a project's progress in memory), many threads, no request timeout (projects take minutes)
# the host may pick the port (Render sets PORT); 7860 otherwise
CMD gunicorn -w 1 --threads 16 --timeout 0 -b 0.0.0.0:${PORT:-7860} webapp:app
