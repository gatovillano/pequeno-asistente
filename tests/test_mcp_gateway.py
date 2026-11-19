import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from starlette.testclient import TestClient

from pequeno_asistente.mcp_gateway import (
    create_gateway_app,
    filter_gateway_servers,
)


class FakeMCPManager:
    """Minimal in-memory stand-in for MCPManager used to test the Linux MCP Gateway."""

    def __init__(self):
        self.instances = {
            "hypruse": SimpleNamespace(status="connected", tools=[]),
            "secret_srv": SimpleNamespace(status="connected", tools=[]),
        }
        self.tool_map = {
            "hypruse__desktop": ("hypruse", "desktop"),
            "secret_srv__wipe": ("secret_srv", "wipe"),
        }
        self.alias_map = {"desktop": "hypruse__desktop"}
        self.call_tool = AsyncMock(return_value="active_window: Kitty")

    def get_litellm_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "hypruse__desktop",
                    "description": "[hypruse] Inspect Hyprland desktop state",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "secret_srv__wipe",
                    "description": "[secret_srv] Blocked tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


class TestLinuxMCPGateway(unittest.IsolatedAsyncioTestCase):
    def test_filter_gateway_servers_skips_remote_and_denied(self):
        raw = {
            "hypruse": {"command": "python3", "args": ["hypruse.py"]},
            "android": {"transport": "streamable_http", "url": "http://100.66.1.2:8765/mcp"},
            "bitwig": {"command": "uvx", "args": ["bitwig-mcp"]},
        }
        filtered = filter_gateway_servers(raw, deny_servers={"bitwig"})
        self.assertEqual(list(filtered.keys()), ["hypruse"])

    def test_gateway_auth_and_health(self):
        fake_mgr = FakeMCPManager()
        app = create_gateway_app(
            mcp_manager=fake_mgr,
            auth_token="super-secret-token",
            deny_servers={"secret_srv"},
            auto_sync_on_startup=False,
        )

        with TestClient(app) as client:
            res = client.get("/health")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["connected_servers"], ["hypruse"])
            self.assertEqual(body["tool_count"], 1)

            res_unauth = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            self.assertEqual(res_unauth.status_code, 401)

            res_wrong = client.post(
                "/mcp",
                headers={"Authorization": "Bearer wrong"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            self.assertEqual(res_wrong.status_code, 401)

    async def test_gateway_streamable_http_list_and_call_tools(self):
        fake_mgr = FakeMCPManager()
        app = create_gateway_app(
            mcp_manager=fake_mgr,
            auth_token="test-token-123",
            deny_servers={"secret_srv"},
            auto_sync_on_startup=False,
        )

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={
                    "Authorization": "Bearer test-token-123",
                    "Accept": "application/json, text/event-stream",
                },
            ) as client:
                init_resp = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "android-test", "version": "1.0"},
                        },
                    },
                )
                self.assertEqual(init_resp.status_code, 200)
                init_data = init_resp.json()
                self.assertEqual(
                    init_data["result"]["serverInfo"]["name"],
                    "pequeno-asistente-linux-gateway",
                )

                list_resp = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                self.assertEqual(list_resp.status_code, 200)
                tools = list_resp.json()["result"]["tools"]
                self.assertEqual([t["name"] for t in tools], ["hypruse__desktop"])

                call_resp = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "hypruse__desktop", "arguments": {"action": "status"}},
                    },
                )
                self.assertEqual(call_resp.status_code, 200)
                call_data = call_resp.json()["result"]
                self.assertFalse(call_data["isError"])
                self.assertEqual(call_data["content"][0]["text"], "active_window: Kitty")
                fake_mgr.call_tool.assert_awaited_once_with("hypruse__desktop", {"action": "status"})

                blocked_resp = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "secret_srv__wipe", "arguments": {}},
                    },
                )
                self.assertEqual(blocked_resp.status_code, 200)
                self.assertTrue(blocked_resp.json()["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
