#!/usr/bin/env bash
# Inicia Pequeño Asistente FastAPI + Túnel Cloudflare
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR" || exit 1

if [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
else
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" scripts/start_remote.py "$@"
