# The portal as a hosted demo (jury mode), e.g. on Railway. See "Hosting the portal" in the README.
# Needs: AQT_PORTAL_HOSTS + AQT_PORTAL_PASSWORD (the portal refuses to listen publicly without them), AQT_DB for the
# store and AI pages, ANTHROPIC_API_KEY for AI actions. Runs on $PORT (default 5280).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AQT_HOME=/app

WORKDIR /app
COPY . .
RUN pip install ".[ai]"

EXPOSE 5280
# Upgrade the database schema first (only with a database), then serve on all interfaces.
CMD ["sh", "-c", "if [ -n \"$AQT_DB\" ]; then python -m aqt db migrate; fi && exec python -m aqt portal --host 0.0.0.0"]
