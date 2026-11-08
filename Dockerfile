# syntax=docker/dockerfile:1.7
#
# Nasiya Pro — backend image: FastAPI API + aiogram bot (same process, BOT_MODE).
#
#   docker build -t nasiya-pro-backend .
#
# Build context is the repository root (see .dockerignore).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.8

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- build: resolve the locked dependencies into /app/.venv --------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first: this layer is reused until pyproject.toml / uv.lock change.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

# DejaVu: Unicode TTF for Cyrillic / Uzbek text in the Akt-sverka PDFs.
# tini: PID 1 that forwards SIGTERM so uvicorn (and bot polling) shut down cleanly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core tini tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app --chmod=0755 deploy/backend-entrypoint.sh /usr/local/bin/backend-entrypoint

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC \
    ENVIRONMENT=production \
    PDF_FONT_REGULAR_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
    PDF_FONT_BOLD_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    RUN_MIGRATIONS=1

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/api/v1/health/live\", timeout=4)"]

ENTRYPOINT ["tini", "--", "backend-entrypoint"]
