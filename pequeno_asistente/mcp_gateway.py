"""
Linux MCP Gateway for Pequeño Asistente.

Exposes the local Linux MCP servers configured in Pequeño Asistente
(e.g., hypruse, desktop-bash, filesystem, bitwig, pequeno-core) over a single
authenticated Streamable HTTP (`/mcp`) and legacy SSE (`/sse`) endpoint so
remote MCP clients on Android (RikkaHub, Kelivo, or a custom app) can control
the Linux machine securely over Tailscale or Cloudflare Tunnel.
"""
import asyncio
import contextlib
import logging
import os
import secrets
from typing import Any, Dict, List, Optional, Set

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.mcp_client.manager import (
    DEFAULT_MCP_SERVER_ID,
    MCPManager,
    is_remote_server_config,
)

logger = logging.getLogger("pequeno_asistente.mcp_gateway")


def _parse_csv_set(raw: Optional[str]) -> Set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def filter_gateway_servers(
    servers_cfg: Dict[str, Any],
    allow_servers: Optional[Set[str]] = None,
    deny_servers: Optional[Set[str]] = None,
    include_remote: bool = False,
) -> Dict[str, Any]:
    """Filters configured MCP servers to avoid loops and enforce allow/deny lists."""
    filtered: Dict[str, Any] = {}
    allow = allow_servers or set()
    deny = deny_servers or set()

    for server_id, cfg in (servers_cfg or {}).items():
        if not isinstance(cfg, dict):
            continue
        if server_id in deny:
            continue
        if allow and server_id not in allow:
            continue
        if not include_remote and is_remote_server_config(cfg):
            # Avoid re-exposing remote servers (e.g., Android MCP) back to Android
            continue
        filtered[server_id] = cfg

    return filtered


class BearerAuthMiddleware:
    """ASGI middleware enforcing `Authorization: Bearer <token>` on MCP routes."""

    def __init__(self, app: ASGIApp, token: str, public_paths: Optional[Set[str]] = None):
        self.app = app
        self.token = token.strip()
        self.public_paths = public_paths or {"/health"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "").upper()
        if method == "OPTIONS" or path in self.public_paths or not self.token:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_raw = headers.get(b"authorization", b"").decode("latin-1").strip()
        provided = ""
        if auth_raw.lower().startswith("bearer "):
            provided = auth_raw[7:].strip()

        if not provided or not secrets.compare_digest(provided, self.token):
            response = JSONResponse(
                {"error": "Unauthorized. Provide header: Authorization: Bearer <token>"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_mcp_tools_from_manager(
    mcp_manager: MCPManager,
    allow_servers: Optional[Set[str]] = None,
    deny_servers: Optional[Set[str]] = None,
) -> List[types.Tool]:
    """Converts active tools from MCPManager into MCP SDK `types.Tool` definitions."""
    allow = allow_servers or set()
    deny = deny_servers or set()
    litellm_tools = mcp_manager.get_litellm_tools()

    result: List[types.Tool] = []
    for entry in litellm_tools:
        fn = entry.get("function") or {}
        name = fn.get("name")
        if not name:
            continue

        # Apply server-level allow/deny check via tool_map
        mapped = mcp_manager.tool_map.get(name)
        if mapped:
            server_id = mapped[0]
            if server_id in deny:
                continue
            if allow and server_id not in allow:
                continue

        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}

        result.append(
            types.Tool(
                name=name,
                description=fn.get("description") or f"Linux MCP tool: {name}",
                inputSchema=schema,
            )
        )
    return result


def create_gateway_app(
    mcp_manager: Optional[MCPManager] = None,
    config_manager: Optional[ConfigManager] = None,
    auth_token: Optional[str] = None,
    allow_servers: Optional[Set[str]] = None,
    deny_servers: Optional[Set[str]] = None,
    include_remote: bool = False,
    auto_sync_on_startup: bool = True,
) -> Starlette:
    """Builds the Starlette ASGI app serving Streamable HTTP (`/mcp`) and SSE (`/sse`)."""
    cfg = config_manager or ConfigManager()
    manager = mcp_manager or MCPManager(cfg)

    allow = allow_servers if allow_servers is not None else _parse_csv_set(os.environ.get("MCP_GATEWAY_ALLOW_SERVERS"))
    deny = deny_servers if deny_servers is not None else _parse_csv_set(os.environ.get("MCP_GATEWAY_DENY_SERVERS"))

    if auth_token is None:
        server_cfg = cfg.get_server_config()
        auth_token = (
            os.environ.get("MCP_GATEWAY_TOKEN")
            or server_cfg.get("api_key")
            or os.environ.get("PEQUENO_API_KEY")
            or ""
        ).strip()

    async def handle_list_tools(ctx, params) -> types.ListToolsResult:
        tools = build_mcp_tools_from_manager(manager, allow_servers=allow, deny_servers=deny)
        return types.ListToolsResult(tools=tools)

    async def handle_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool_name = params.name
        arguments = dict(params.arguments or {})

        # Enforce server allow/deny policy before execution
        resolved_name = tool_name
        if resolved_name not in manager.tool_map and resolved_name in manager.alias_map:
            resolved_name = manager.alias_map[resolved_name]
        mapped = manager.tool_map.get(resolved_name)
        if mapped:
            server_id = mapped[0]
            if server_id in deny or (allow and server_id not in allow):
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"Error: Access to server '{server_id}' is blocked by gateway policy.")],
                    isError=True,
                )

        output = await manager.call_tool(tool_name, arguments)
        is_err = isinstance(output, str) and (
            output.startswith("Error: Tool '") or output.startswith("Execution error in ")
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(output))],
            isError=is_err,
        )

    mcp_server = Server(
        name="pequeno-asistente-linux-gateway",
        version="1.0.0",
        instructions=(
            "Linux MCP Gateway for Pequeño Asistente. Provides remote access to the "
            "user's Linux desktop MCP tools (window control, bash, files, Bitwig, etc.)."
        ),
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )

    security_settings = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=True,
        stateless=True,
        security_settings=security_settings,
    )

    sse_transport = SseServerTransport("/messages/", security_settings=security_settings)

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def handle_sse(request: Request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_health(request: Request) -> JSONResponse:
        connected = [
            sid
            for sid, inst in manager.instances.items()
            if inst.status == "connected"
            and sid not in deny
            and (not allow or sid in allow)
        ]
        tools = build_mcp_tools_from_manager(manager, allow_servers=allow, deny_servers=deny)
        return JSONResponse(
            {
                "status": "ok",
                "gateway": "pequeno-asistente-linux-gateway",
                "connected_servers": connected,
                "tool_count": len(tools),
                "endpoints": {
                    "streamable_http": "/mcp",
                    "sse": "/sse",
                },
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        sync_task: Optional[asyncio.Task] = None
        if auto_sync_on_startup:
            async def _sync():
                try:
                    raw_servers = cfg.get_mcp_servers()
                    servers_to_sync = filter_gateway_servers(
                        raw_servers,
                        allow_servers=allow,
                        deny_servers=deny,
                        include_remote=include_remote,
                    )
                    await manager.sync_servers(servers_to_sync)
                    connected = [
                        k for k, inst in manager.instances.items() if inst.status == "connected"
                    ]
                    logger.info("Linux MCP Gateway connected servers: %s", connected)
                except Exception as exc:
                    logger.warning("Gateway MCP sync error: %s", exc)

            sync_task = asyncio.create_task(_sync())

        async with session_manager.run():
            yield

        if sync_task and not sync_task.done():
            sync_task.cancel()
        if auto_sync_on_startup:
            try:
                await manager.shutdown()
            except Exception as exc:
                logger.error("Error shutting down gateway MCP servers: %s", exc)

    class _MCPASGIEndpoint:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await session_manager.handle_request(scope, receive, send)

    mcp_endpoint = _MCPASGIEndpoint()

    routes = [
        Route("/health", endpoint=handle_health, methods=["GET"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=sse_transport.handle_post_message),
        Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        Route("/mcp/", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    ]

    starlette_app = Starlette(routes=routes, lifespan=lifespan)
    starlette_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if auth_token:
        starlette_app.add_middleware(BearerAuthMiddleware, token=auth_token)
    else:
        logger.warning(
            "MCP Gateway started WITHOUT authentication token! Set MCP_GATEWAY_TOKEN before exposing on a network."
        )

    return starlette_app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    host = os.environ.get("MCP_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_GATEWAY_PORT", "8766"))

    cfg = ConfigManager()
    server_cfg = cfg.get_server_config()
    token = (
        os.environ.get("MCP_GATEWAY_TOKEN")
        or server_cfg.get("api_key")
        or os.environ.get("PEQUENO_API_KEY")
        or ""
    ).strip()

    if not token:
        token = secrets.token_urlsafe(24)
        logger.info("Generated ephemeral MCP_GATEWAY_TOKEN: %s", token)

    app = create_gateway_app(config_manager=cfg, auth_token=token)
    logger.info("Starting Linux MCP Gateway on http://%s:%d/mcp (SSE: /sse)", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
