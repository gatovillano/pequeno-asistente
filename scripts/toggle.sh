#!/usr/bin/env bash
# Toggle Pequeño Asistente window or launch if not running

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# Send toggle command to socket
if [ -S "$HOME/.config/pequeno-asistente/asistente.sock" ]; then
    "$PYTHON_BIN" "$BASE_DIR/pequeno_asistente/main.py" --toggle
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        exit 0
    fi
fi

# If socket didn't respond or wasn't running, launch the assistant in background
nohup "$PYTHON_BIN" "$BASE_DIR/pequeno_asistente/main.py" > /dev/null 2>&1 &
