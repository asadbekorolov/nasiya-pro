#!/bin/sh
# Container entrypoint for the backend image.
#
# 1. Applies database migrations (RUN_MIGRATIONS=1, the default). Alembic takes a
#    PostgreSQL lock while upgrading, so only one replica migrates at a time.
# 2. Starts uvicorn. The Telegram bot runs inside the same process (BOT_MODE).
#    BOT_MODE=polling must run in exactly ONE process: keep WEB_CONCURRENCY=1 then.
#    BOT_MODE=webhook (recommended in production) works with any number of workers.
set -eu

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "entrypoint: applying database migrations"
    alembic upgrade head
fi

if [ "$#" -gt 0 ]; then
    # Any explicit command (e.g. `docker compose run backend python -m ...`) wins.
    exec "$@"
fi

if [ "${BOT_MODE:-off}" = "polling" ] && [ "${WEB_CONCURRENCY:-1}" != "1" ]; then
    echo "entrypoint: BOT_MODE=polling needs WEB_CONCURRENCY=1 (two pollers conflict)" >&2
    exit 64
fi

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --proxy-headers \
    --forwarded-allow-ips "*" \
    --no-server-header \
    --timeout-graceful-shutdown 20
