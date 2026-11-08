# Production image — built per-arch and pushed to GHCR by release.yml.
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="Family Finance" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/JoshJames04/family-finance-ha" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FF_DATA_DIR=/data \
    FF_BACKEND_DIR=/app

RUN useradd --system --uid 10001 --home /data ff \
    && mkdir -p /data && chown ff:ff /data

WORKDIR /app
# Deps layer: install the project shell (deps resolved from pyproject)
# before copying code so this layer caches until dependencies change.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
# Code layer: reinstall the real wheel — the first pass had no package
# sources yet and produces an empty shell.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps --force-reinstall .

USER ff
EXPOSE 8099
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8099/healthz',timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099"]
