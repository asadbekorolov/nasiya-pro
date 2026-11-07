#!/usr/bin/env sh
# Nasiya Pro live runner for Linux / macOS.
set -e
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  exec python3 run_live.py "$@"
elif command -v uv >/dev/null 2>&1; then
  exec uv run --no-project python run_live.py "$@"
else
  echo "python3 or uv is required" >&2
  exit 1
fi
