# Family Finance — HA add-on image (issue #23).
# Multi-stage: Vite SPA build → python runtime serving the API plus
# the bundle over ingress. Built per-arch by release.yml; the add-on
# metadata lives in addon/config.yaml.

# ---------- stage 1: frontend ----------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: runtime ----------
FROM python:3.13-slim AS runtime

ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="Family Finance" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/JoshJames04/family-finance-ha" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FF_DATA_DIR=/data \
    FF_BACKEND_DIR=/app \
    FF_WEB_DIR=/app/web

RUN useradd --system --uid 10001 --home /data ff \
    && mkdir -p /data && chown ff:ff /data

WORKDIR /app
# Deps layer caches until pyproject changes.
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .
# Code layer: reinstall the real wheel — the first pass had no
# package sources yet and produces an empty shell.
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN pip install --no-cache-dir --no-deps --force-reinstall .

# SPA bundle from stage 1 — served by app.main's fallback route.
COPY --from=web /web/dist ./web

# Starts as root so the entrypoint can chown the mounted /data volume
# (the HA Supervisor writes options.json root-owned), then drops to ff.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Strip CR — a CRLF checkout (Windows dev box) would break the shebang.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8099
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=120s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8099/healthz',timeout=4)" || exit 1

CMD ["docker-entrypoint.sh"]
