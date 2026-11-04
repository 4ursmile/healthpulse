# HealthPulse 3.1 — one image for the API and the worker
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HP_DATA_DIR=/srv/data HP_ARTIFACTS_DIR=/srv/artifacts HP_ENV=demo
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY healthpulse ./healthpulse
RUN pip install --upgrade pip && pip install . python-multipart
COPY web ./web
COPY config ./config
RUN useradd --create-home --uid 10001 hp && mkdir -p /srv/data /srv/artifacts && chown -R hp /srv /app
USER hp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:8080/api/v1/health || exit 1
CMD ["uvicorn", "healthpulse.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
