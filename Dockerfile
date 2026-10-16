FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    XDG_CACHE_HOME=/opt/tectonic-cache \
    HOME=/root

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates util-linux libreoffice-impress poppler-utils fonts-liberation librsvg2-bin \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/README.md /app/backend/
COPY backend/app/ /app/backend/app/

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir /app/backend

COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

RUN groupadd --system --gid 10001 jot \
    && useradd --system --uid 10001 --gid jot --create-home --home-dir /home/jot jot \
    && mkdir -p /data /opt/tectonic /opt/tectonic-cache/tectonic \
    && chown -R 10001:10001 /data /home/jot

ENV HOME=/home/jot \
    APP_ENV=production \
    APP_ORIGINS=https://jotai.tech \
    ALLOW_GUEST_ACCESS=false \
    SIGNUP_MODE=approval \
    LEAN_TOOL_ENABLED=false \
    SQLITE_PATH=/data/screen_tutor.sqlite3 \
    STORAGE_DIR=/data/private_uploads \
    FRONTEND_DIST=/app/frontend/dist \
    STUDY_PDF_EXECUTABLE=/opt/tectonic/tectonic \
    STUDY_PDF_TIMEOUT_SECONDS=120

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=172.19.0.0/16"]
