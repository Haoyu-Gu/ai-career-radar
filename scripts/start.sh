#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --frozen
if [ ! -d frontend/node_modules ]; then
  npm --prefix frontend ci
fi
if [ ! -f frontend/dist/index.html ]; then
  npm --prefix frontend run build
fi

exec uv run uvicorn app.main:app --app-dir backend --host "${APP_HOST:-127.0.0.1}" --port "${APP_PORT:-8787}"

