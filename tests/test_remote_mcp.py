"""
Tests for Remote MCP Server support (Streamable HTTP, SSE, auto-detection,
Bearer/custom headers, env var interpolation, JSON config import, and live connections).
"""
import os
import json
import socket
import asyncio
import tempfile
import unittest
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from mcp.server.mcpserver import MCPServer

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.mcp_client.manager import (
    MCPManager,
    is_remote_transport,
    is_remote_server_config,
    infer_remote_transport,
    interpolate_env_vars,
    build_remote_headers,
    normalize_mcp_server_config,
    extract_servers_from_json,
)


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RequireBearerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expected_token: str, required_custom_header: str = ""):
        super().__init__(app)
        self.expected_token = expected_token
        self.required_custom_header = required_custom_header

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.expected_token}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if self.required_custom_header:
            if request.headers.get("x-custom-test", "") != self.required_custom_header:
                return JSONResponse({"error": "Missing custom header"}, status_code=403)
        return await call_next(request)


class TestRemoteMCPConfigAndLiveConnections(unittest.IsolatedAsyncioTestCase):
    def test_transport_detection_and_normalization(self):
        self.assertTrue(is_remote_transport("sse"))
        self.assertTrue(is_remote_transport("streamable_http"))
        self.assertTrue(is_remote_transport("streamable-http"))
        self.assertTrue(is_remote_transport("http"))
        self.assertTrue(is_remote_transport("auto"))
        self.assertFalse(is_remote_transport("stdio"))

        self.assertEqual(infer_remote_transport("https://api.example.com/sse", None), "sse")
        self.assertEqual(infer_remote_transport("https://api.example.com/mcp", None), "auto")
        self.assertEqual(infer_remote_transport("https://api.example.com/mcp", "http"), "streamable_http")
        self.assertEqual(infer_remote_transport("https://api.example.com/sse", "http"), "sse")

        cfg = normalize_mcp_server_config({
            "name": "My Remote Server",
            "url": "https://mcp.example.com/v1/mcp",
            "type": "streamable-http",
            "api_key": "secret-123",
            "headers": {"X-Project": "pequeno"}
        })
        self.assertTrue(is_remote_server_config(cfg))
        self.assertEqual(cfg["transport"], "streamable_http")
        self.assertEqual(cfg["url"], "https://mcp.example.com/v1/mcp")
        self.assertEqual(cfg["api_key"], "secret-123")
        self.assertEqual(cfg["headers"], {"X-Project": "pequeno"})

    def test_headers_and_env_interpolation(self):
        cfg = {
            "url": "https://mcp.example.com/mcp",
            "api_key": "${TEST_MCP_TOKEN}",
            "headers": {
                "X-Workspace": "$TEST_WORKSPACE",
                "X-Static": "ok"
            },
            "env": {
                "TEST_MCP_TOKEN": "tok-abc-999",
                "TEST_WORKSPACE": "linux-desktop"
            }
        }
        headers = build_remote_headers(cfg)
        self.assertEqual(headers.get("Authorization"), "Bearer tok-abc-999")
        self.assertEqual(headers.get("X-Workspace"), "linux-desktop")
        self.assertEqual(headers.get("X-Static"), "ok")

    def test_extract_servers_from_json_formats(self):
        # 1. Claude Desktop / Cursor format with both local and remote servers
        raw_json = json.dumps({
            "mcpServers": {
                "local-git": {
                    "command": "uvx",
                    "args": ["mcp-server-git"]
                },
                "remote-streamable": {
                    "url": "https://remote.example.com/mcp",
                    "transport": "streamable_http",
                    "headers": {"Authorization": "Bearer my-key"}
                },
                "remote-sse": {
                    "serverUrl": "https://remote.example.com/sse"
                }
            }
        })
        extracted = dict(extract_servers_from_json(raw_json))
        self.assertEqual(len(extracted), 3)
        self.assertEqual(extracted["local-git"]["transport"], "stdio")
        self.assertEqual(extracted["remote-streamable"]["transport"], "streamable_http")
        self.assertEqual(extracted["remote-streamable"]["url"], "https://remote.example.com/mcp")
        self.assertEqual(extracted["remote-streamable"]["headers"]["Authorization"], "Bearer my-key")
        self.assertEqual(extracted["remote-sse"]["transport"], "sse")
        self.assertEqual(extracted["remote-sse"]["url"], "https://remote.example.com/sse")

        # 2. VS Code "servers" format
        vscode_json = json.dumps({
            "servers": {
                "vscode-remote": {
                    "type": "http",
                    "url": "https://vscode.example.com/mcp"
                }
            }
        })
        extracted_vs = dict(extract_servers_from_json(vscode_json))
        self.assertIn("vscode-remote", extracted_vs)
        self.assertEqual(extracted_vs["vscode-remote"]["transport"], "streamable_http")

    def test_config_manager_persists_remote_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.json"
            mgr = ConfigManager(str(cfg_path))
            mgr.set_mcp_server("remote-test", {
                "name": "Remote Test",
                "url": "https://example.org/mcp",
                "transport": "auto",
                "api_key": "my-secret",
                "headers": {"X-Env": "prod"}
            })

            mgr2 = ConfigManager(str(cfg_path))
            srv = mgr2.get_mcp_servers().get("remote-test")
            self.assertIsNotNone(srv)
            self.assertEqual(srv["url"], "https://example.org/mcp")
            self.assertEqual(srv["transport"], "auto")
            self.assertEqual(srv["api_key"], "my-secret")
            self.assertEqual(srv["headers"]["X-Env"], "prod")

    async def test_live_streamable_http_and_sse_servers_with_auth(self):
        # Build a real MCPServer with two tools
        mcp_srv = MCPServer("remote-demo")

        @mcp_srv.tool(description="Adds two numbers remotely")
        def remote_add(a: int, b: int) -> str:
            return f"sum={a + b}"

        @mcp_srv.tool(description="Echoes a greeting from remote server")
        def remote_greet(name: str) -> str:
            return f"Hola {name} desde MCP remoto!"

        # 1. Start Streamable HTTP server with Bearer + custom header middleware
        http_port = _get_free_port()
        streamable_app = mcp_srv.streamable_http_app()
        streamable_app.add_middleware(
            _RequireBearerMiddleware,
            expected_token="super-secret-token",
            required_custom_header="custom-ok"
        )
        config_http = uvicorn.Config(streamable_app, host="127.0.0.1", port=http_port, log_level="error")
        server_http = uvicorn.Server(config_http)
        http_task = asyncio.create_task(server_http.serve())

        # 2. Start SSE server on another port
        sse_port = _get_free_port()
        mcp_sse_srv = MCPServer("remote-sse-demo")

        @mcp_sse_srv.tool(description="Returns server status over SSE")
        def sse_ping() -> str:
            return "pong-from-sse"

        sse_app = mcp_sse_srv.sse_app()
        config_sse = uvicorn.Config(sse_app, host="127.0.0.1", port=sse_port, log_level="error")
        server_sse = uvicorn.Server(config_sse)
        sse_task = asyncio.create_task(server_sse.serve())

        # Wait for both uvicorn servers to start listening
        for _ in range(50):
            if server_http.started and server_sse.started:
                break
            await asyncio.sleep(0.05)

        try:
            http_url = f"http://127.0.0.1:{http_port}/mcp"
            sse_url = f"http://127.0.0.1:{sse_port}/sse"

            # Verify unauthorized connection fails cleanly
            bad_res = await MCPManager.test_server_connection({
                "url": http_url,
                "transport": "streamable_http",
                "api_key": "wrong-token"
            })
            self.assertFalse(bad_res["ok"])

            # Verify authorized connection via test_server_connection (using 'auto' transport + env var interpolation)
            good_cfg = {
                "name": "Remote Math HTTP",
                "url": http_url,
                "transport": "auto",
                "api_key": "${REMOTE_TOKEN}",
                "headers": {"X-Custom-Test": "custom-ok"},
                "env": {"REMOTE_TOKEN": "super-secret-token"}
            }
            good_res = await MCPManager.test_server_connection(good_cfg, server_id="remote-math")
            self.assertTrue(good_res["ok"], f"Expected ok=True, got: {good_res}")
            self.assertEqual(good_res["transport"], "streamable_http")
            self.assertEqual(good_res["tools_count"], 2)
            self.assertIn("remote_add", good_res["tools"])

            # Connect both remote servers (Streamable HTTP + SSE) simultaneously in MCPManager
            mcp_mgr = MCPManager()
            await mcp_mgr.sync_servers({
                "remote-math": good_cfg,
                "remote-sse": {
                    "name": "Remote SSE Server",
                    "url": sse_url,
                    "transport": "auto"
                }
            })

            math_status = mcp_mgr.get_server_status("remote-math")
            sse_status = mcp_mgr.get_server_status("remote-sse")
            self.assertEqual(math_status["status"], "connected")
            self.assertEqual(math_status["transport"], "streamable_http")
            self.assertEqual(sse_status["status"], "connected")
            self.assertEqual(sse_status["transport"], "sse")

            # Execute tools on both remote servers via MCPManager.call_tool
            add_out = await mcp_mgr.call_tool("remote_math__remote_add", {"a": 19, "b": 23})
            self.assertIn("sum=42", add_out)

            greet_out = await mcp_mgr.call_tool("remote_greet", {"name": "Gato"})
            self.assertIn("Hola Gato desde MCP remoto!", greet_out)

            ping_out = await mcp_mgr.call_tool("remote_sse__sse_ping", {})
            self.assertIn("pong-from-sse", ping_out)

            await mcp_mgr.shutdown()
        finally:
            server_http.should_exit = True
            server_sse.should_exit = True
            await asyncio.gather(http_task, sse_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
