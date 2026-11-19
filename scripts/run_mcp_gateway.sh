#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi

HOST="${MCP_GATEWAY_HOST:-0.0.0.0}"
PORT="${MCP_GATEWAY_PORT:-8766}"

if [ -z "${MCP_GATEWAY_TOKEN:-}" ]; then
    MCP_GATEWAY_TOKEN="$("$PYTHON_BIN" -c "import secrets; print(secrets.token_urlsafe(24))")"
    export MCP_GATEWAY_TOKEN
fi

TAILSCALE_IP=""
if command -v tailscale >/dev/null 2>&1; then
    TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
fi

echo "============================================================"
echo " Pequeño Asistente — Linux MCP Gateway"
echo "============================================================"
echo " Local URL:     http://127.0.0.1:${PORT}/mcp"
if [ -n "$TAILSCALE_IP" ]; then
    echo " Tailscale URL: http://${TAILSCALE_IP}:${PORT}/mcp"
    echo " SSE URL:       http://${TAILSCALE_IP}:${PORT}/sse"
fi
echo " Health check:  http://127.0.0.1:${PORT}/health"
echo " Bearer Token:  ${MCP_GATEWAY_TOKEN}"
echo "============================================================"

exec "$PYTHON_BIN" -m pequeno_asistente.mcp_gateway
