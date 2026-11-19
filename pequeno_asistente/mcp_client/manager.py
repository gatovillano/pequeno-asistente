"""
MCP Client Manager for Pequeño Asistente.
Manages connections to multiple MCP servers (stdio, sse, streamable_http, auto, and the built-in default `pequeno-core` server),
aggregates their tools for LiteLLM, and routes tool calls strictly to active MCP servers.
"""
import os
import re
import sys
import json
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional, Tuple
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client
try:
    from mcp.client._probe import negotiate_auto
except Exception:
    negotiate_auto = None

from pequeno_asistente.mcp_client.registry import RegistryClient
from pequeno_asistente.security.manager import CredentialManager
from pequeno_asistente.mcp_client.default_server import (
    DEFAULT_MCP_SERVER_ID,
    SECURITY_TOOLS_DEFINITIONS,
    AUTOMATION_TOOLS_DEFINITIONS,
    SKILLS_TOOLS_DEFINITIONS,
    MEMORY_TOOLS_DEFINITIONS,
    MEDIA_TOOLS_DEFINITIONS,
    ALL_DEFAULT_TOOLS_DEFINITIONS,
    execute_default_mcp_tool,
    build_default_mcp_tool_objects
)

logger = logging.getLogger("pequeno_asistente.mcp")

REMOTE_TRANSPORTS = ("sse", "streamable_http", "streamable-http", "http", "auto")
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def is_remote_transport(transport: Optional[str]) -> bool:
    """Returns True if the transport string represents a remote HTTP/SSE MCP server."""
    if not transport:
        return False
    return str(transport).strip().lower() in REMOTE_TRANSPORTS


def is_http_url(value: Optional[str]) -> bool:
    """Returns True if value is an http:// or https:// URL."""
    if not value or not isinstance(value, str):
        return False
    val = value.strip().lower()
    return val.startswith("http://") or val.startswith("https://")


def is_remote_server_config(config: Dict[str, Any]) -> bool:
    """Determines whether a server configuration targets a remote HTTP/SSE MCP endpoint."""
    if not isinstance(config, dict):
        return False
    transport = str(config.get("transport") or config.get("type") or "").strip().lower()
    if is_remote_transport(transport):
        return True
    url = config.get("url") or config.get("serverUrl") or config.get("endpoint")
    if is_http_url(url):
        return True
    cmd = config.get("command")
    if is_http_url(cmd) and not config.get("args"):
        return True
    return False


def infer_remote_transport(url: str, raw_transport: Optional[str] = None) -> str:
    """
    Normalizes or infers the remote transport mode ('sse', 'streamable_http', or 'auto').
    """
    t = str(raw_transport or "").strip().lower()
    if t == "sse":
        return "sse"
    if t in ("streamable_http", "streamable-http", "streamablehttp"):
        return "streamable_http"
    if t == "auto":
        return "auto"
    if t == "http":
        # 'http' is commonly used in Cursor/VSCode/Claude configs for Streamable HTTP (or SSE if ending in /sse)
        parsed_path = urlparse(url.strip()).path.rstrip("/").lower() if url else ""
        return "sse" if parsed_path.endswith("/sse") else "streamable_http"

    # If transport was omitted or left as 'stdio' despite having an HTTP URL:
    parsed_path = urlparse(url.strip()).path.rstrip("/").lower() if url else ""
    if parsed_path.endswith("/sse"):
        return "sse"
    return "auto"


def interpolate_env_vars(value: str, env: Dict[str, str]) -> str:
    """Interpolates ${VAR} and $VAR placeholders using the provided environment dictionary."""
    if not value or not isinstance(value, str) or "$" not in value:
        return value

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        if var_name and var_name in env:
            return str(env[var_name])
        return match.group(0)

    return _ENV_VAR_PATTERN.sub(_replacer, value)


def build_remote_headers(config: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Builds HTTP headers for a remote MCP server, resolving environment variables and
    injecting Authorization: Bearer <token> when api_key / auth_token / bearer_token is set.
    """
    merged_env = dict(os.environ)
    if isinstance(config.get("env"), dict):
        for k, v in config["env"].items():
            if v is not None and str(v).strip():
                merged_env[str(k)] = str(v)
    if env:
        merged_env.update(env)

    headers: Dict[str, str] = {}
    raw_headers = config.get("headers")
    if isinstance(raw_headers, dict):
        for hk, hv in raw_headers.items():
            if hk and hv is not None:
                val_str = interpolate_env_vars(str(hv).strip(), merged_env)
                if val_str:
                    headers[str(hk).strip()] = val_str

    # Check explicit token fields or api_key_env
    token = (
        config.get("api_key")
        or config.get("auth_token")
        or config.get("bearer_token")
        or ""
    )
    if isinstance(token, str):
        token = interpolate_env_vars(token.strip(), merged_env)
    else:
        token = ""

    if not token and config.get("api_key_env"):
        env_key = str(config["api_key_env"]).strip()
        token = merged_env.get(env_key, "").strip()

    if token:
        has_auth = any(k.lower() == "authorization" for k in headers.keys())
        if not has_auth:
            if token.lower().startswith(("bearer ", "basic ", "token ")):
                headers["Authorization"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"

    return headers


def normalize_mcp_server_config(raw: Dict[str, Any], fallback_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Normalizes an MCP server dictionary (from Claude Desktop, Cursor, VS Code, Smithery, or UI)
    into Pequeño Asistente's unified server config schema supporting both local (stdio) and remote (SSE / Streamable HTTP) servers.
    """
    if not isinstance(raw, dict):
        raw = {}

    name = str(raw.get("name") or fallback_name or raw.get("id") or "MCP Server").strip()
    raw_url = str(raw.get("url") or raw.get("serverUrl") or raw.get("endpoint") or "").strip()
    raw_cmd = str(raw.get("command") or "").strip()
    raw_transport = str(raw.get("transport") or raw.get("type") or "").strip().lower()

    if not raw_url and is_http_url(raw_cmd):
        raw_url = raw_cmd

    args_raw = raw.get("args", [])
    if isinstance(args_raw, str):
        args = [a for a in args_raw.split(" ") if a.strip()]
    elif isinstance(args_raw, list):
        args = [str(a) for a in args_raw if str(a).strip()]
    else:
        args = []

    env_raw = raw.get("env", {})
    env = dict(env_raw) if isinstance(env_raw, dict) else {}

    headers_raw = raw.get("headers", {})
    headers = {str(k).strip(): str(v) for k, v in headers_raw.items() if k and v is not None} if isinstance(headers_raw, dict) else {}

    api_key = str(raw.get("api_key") or raw.get("auth_token") or raw.get("bearer_token") or "").strip()

    is_remote = bool(raw_url) or is_remote_transport(raw_transport)
    if is_remote:
        transport = infer_remote_transport(raw_url, raw_transport)
        url = raw_url
        command = raw_cmd if (raw_cmd and not is_http_url(raw_cmd)) else url
    else:
        transport = "stdio"
        url = ""
        command = raw_cmd

    normalized: Dict[str, Any] = {
        "name": name,
        "command": command,
        "args": args,
        "transport": transport,
        "env": env,
        "disabled": bool(raw.get("disabled", False)),
    }
    if url:
        normalized["url"] = url
    if headers:
        normalized["headers"] = headers
    if api_key:
        normalized["api_key"] = api_key
    if raw.get("timeout"):
        try:
            normalized["timeout"] = float(raw["timeout"])
        except Exception:
            pass
    if raw.get("builtin"):
        normalized["builtin"] = True

    return normalized


def extract_servers_from_json(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Parses JSON configuration text and extracts a list of (server_id, normalized_config) tuples.
    Supports:
    - {"mcpServers": {"id": {...}}} (Claude Desktop / Cursor / Windsurf)
    - {"servers": {"id": {...}}} (VS Code MCP)
    - {"mcp_servers": {"id": {...}}} (Pequeño Asistente config.json)
    - Direct single server object {"command": ...} or {"url": ...}
    - Top-level map {"id": {"command" | "url": ...}}
    - Array of server objects [{"id": ..., "url" | "command": ...}]
    """
    data = json.loads(text)
    extracted: List[Tuple[str, Dict[str, Any]]] = []

    def _is_valid_server_dict(d: Any) -> bool:
        if not isinstance(d, dict):
            return False
        return any(
            bool(d.get(k))
            for k in ("command", "url", "serverUrl", "endpoint")
        ) or is_remote_transport(d.get("transport") or d.get("type"))

    container = None
    if isinstance(data, dict):
        for key in ("mcpServers", "servers", "mcp_servers"):
            if key in data and isinstance(data[key], dict):
                container = data[key]
                break

    if container is not None:
        for sid, scfg in container.items():
            if _is_valid_server_dict(scfg):
                extracted.append((str(sid), normalize_mcp_server_config(scfg, fallback_name=str(sid))))
    elif isinstance(data, dict):
        if _is_valid_server_dict(data):
            sid = str(data.get("id") or data.get("name") or "custom-mcp")
            extracted.append((sid, normalize_mcp_server_config(data, fallback_name=sid)))
        else:
            for sid, scfg in data.items():
                if _is_valid_server_dict(scfg):
                    extracted.append((str(sid), normalize_mcp_server_config(scfg, fallback_name=str(sid))))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if _is_valid_server_dict(item):
                sid = str(item.get("id") or item.get("name") or f"custom-{idx}")
                extracted.append((sid, normalize_mcp_server_config(item, fallback_name=sid)))

    return extracted


class MCPServerInstance:
    def __init__(self, server_id: str, config: Dict[str, Any], quiet_stderr: bool = False):
        self.server_id = server_id
        self.config = config
        self.quiet_stderr = quiet_stderr
        self.exit_stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.tools: List[Any] = []
        self.status: str = "disconnected"  # "connected", "error", "connecting"
        self.error_message: Optional[str] = None
        self.active_transport: Optional[str] = None

    async def _initialize_and_list_tools(self, session: ClientSession, timeout: float = 15.0) -> List[Any]:
        """Initializes the MCP session (with fallback to modern negotiate_auto if needed) and lists tools."""
        try:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
        except Exception as init_err:
            if negotiate_auto is not None and "UnsupportedProtocolVersion" in type(init_err).__name__ or "-32022" in str(init_err):
                await asyncio.wait_for(negotiate_auto(session), timeout=timeout)
            else:
                raise
        tool_resp = await asyncio.wait_for(session.list_tools(), timeout=max(10.0, timeout))
        return tool_resp.tools or []

    async def _connect_remote_single(
        self,
        mode: str,
        url: str,
        headers: Dict[str, str],
        timeout: float,
        sse_read_timeout: float
    ) -> Tuple[AsyncExitStack, ClientSession, List[Any]]:
        """Establishes a single remote MCP connection using either 'streamable_http' or 'sse'."""
        stack = AsyncExitStack()
        try:
            if mode == "streamable_http":
                import httpx2
                http_client = create_mcp_http_client(
                    headers=headers if headers else None,
                    timeout=httpx2.Timeout(timeout, read=sse_read_timeout)
                )
                await stack.enter_async_context(http_client)
                read_stream, write_stream = await stack.enter_async_context(
                    streamable_http_client(url, http_client=http_client)
                )
            elif mode == "sse":
                read_stream, write_stream = await stack.enter_async_context(
                    sse_client(
                        url,
                        headers=headers if headers else None,
                        timeout=timeout,
                        sse_read_timeout=sse_read_timeout
                    )
                )
            else:
                raise ValueError(f"Unsupported remote transport mode: {mode}")

            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            tools = await self._initialize_and_list_tools(session, timeout=timeout)
            return stack, session, tools
        except BaseException:
            try:
                await stack.aclose()
            except BaseException:
                pass
            raise

    async def connect(self):
        self.status = "connecting"
        self.error_message = None
        self.active_transport = None
        self.exit_stack = AsyncExitStack()

        raw_transport = str(self.config.get("transport") or self.config.get("type") or "stdio").strip().lower()
        cmd = str(self.config.get("command") or "").strip()
        raw_url = str(self.config.get("url") or self.config.get("serverUrl") or self.config.get("endpoint") or "").strip()
        args = self.config.get("args", [])
        custom_env = self.config.get("env", {})
        timeout = float(self.config.get("timeout") or 15.0)
        sse_read_timeout = float(self.config.get("sse_read_timeout") or 300.0)

        # Merge environment with system PATH and env
        env = dict(os.environ)
        home = os.path.expanduser("~")
        extra_paths = [f"{home}/.local/bin", f"{home}/.cargo/bin", "/usr/local/bin", "/usr/bin"]
        env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")

        # Inject Pequeño Asistente ASKPASS for secure sudo graphical prompts
        try:
            askpass_path = CredentialManager.get_instance().get_askpass_path()
            env["SUDO_ASKPASS"] = askpass_path
            env["SSH_ASKPASS"] = askpass_path
        except Exception as e:
            logger.debug(f"Could not configure askpass helper in env: {e}")

        if isinstance(custom_env, dict):
            for k, v in custom_env.items():
                if v is not None and str(v).strip():
                    env[str(k)] = str(v)

        is_remote = is_remote_server_config(self.config)
        transport = infer_remote_transport(raw_url or cmd, raw_transport) if is_remote else "stdio"

        try:
            if transport == "stdio":
                if not cmd:
                    raise ValueError(f"No command specified for stdio server {self.server_id}")

                params = StdioServerParameters(command=cmd, args=args, env=env)
                if self.quiet_stderr:
                    try:
                        log_path = Path.home() / ".config" / "pequeno-asistente" / "mcp_servers.log"
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        errlog_file = self.exit_stack.enter_context(open(log_path, "a", encoding="utf-8", buffering=1))
                    except Exception:
                        errlog_file = self.exit_stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                    read_stream, write_stream = await self.exit_stack.enter_async_context(
                        stdio_client(params, errlog=errlog_file)
                    )
                else:
                    read_stream, write_stream = await self.exit_stack.enter_async_context(stdio_client(params))
                self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
                self.tools = await self._initialize_and_list_tools(self.session, timeout=timeout)
                self.active_transport = "stdio"
            else:
                url = interpolate_env_vars(raw_url or cmd, env).strip()
                if not url:
                    raise ValueError(f"No URL specified for remote MCP server {self.server_id}")

                headers = build_remote_headers(self.config, env=env)

                # Determine connection attempt order
                if transport == "sse":
                    modes = ["sse"]
                    if self.config.get("auto_fallback", False):
                        modes.append("streamable_http")
                elif transport == "streamable_http":
                    modes = ["streamable_http"]
                    if self.config.get("auto_fallback", True):
                        modes.append("sse")
                else:  # "auto"
                    parsed_path = urlparse(url).path.rstrip("/").lower()
                    if parsed_path.endswith("/sse"):
                        modes = ["sse", "streamable_http"]
                    else:
                        modes = ["streamable_http", "sse"]

                await self.exit_stack.aclose()
                self.exit_stack = None

                last_err: Optional[BaseException] = None
                for attempt_mode in modes:
                    try:
                        stack, session, tools = await self._connect_remote_single(
                            mode=attempt_mode,
                            url=url,
                            headers=headers,
                            timeout=timeout,
                            sse_read_timeout=sse_read_timeout
                        )
                        self.exit_stack = stack
                        self.session = session
                        self.tools = tools
                        self.active_transport = attempt_mode
                        last_err = None
                        break
                    except BaseException as err:
                        last_err = err
                        logger.debug(
                            f"Remote MCP '{self.server_id}' attempt with '{attempt_mode}' failed: {err}"
                        )

                if last_err is not None:
                    raise last_err

            self.status = "connected"
            logger.info(
                f"Connected to MCP '{self.server_id}' ({self.active_transport or transport}) with {len(self.tools)} tools."
            )
        except asyncio.TimeoutError:
            self.status = "error"
            self.error_message = f"Tiempo de espera agotado al conectar o listar herramientas (timeout {int(timeout)}s)"
            logger.error(f"Timeout connecting to MCP '{self.server_id}'")
            if self.exit_stack:
                try:
                    await self.exit_stack.aclose()
                except Exception:
                    pass
                self.exit_stack = None
                self.session = None
        except Exception as e:
            self.status = "error"
            self.error_message = str(e)
            logger.error(f"Failed to connect to MCP '{self.server_id}': {e}")
            if self.exit_stack:
                try:
                    await self.exit_stack.aclose()
                except Exception:
                    pass
                self.exit_stack = None
                self.session = None

    async def disconnect(self):
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
            except BaseException as e:
                logger.debug(f"Disconnect note for '{self.server_id}': {e}")
            self.exit_stack = None
            self.session = None
        self.status = "disconnected"
        self.tools = []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if not self.session or self.status != "connected":
            raise RuntimeError(f"Server '{self.server_id}' is not connected.")

        res = await self.session.call_tool(tool_name, arguments)
        output_parts = []
        if res.content:
            for item in res.content:
                if hasattr(item, "text"):
                    output_parts.append(item.text)
                elif hasattr(item, "data"):
                    output_parts.append(f"[Binary/Media Data: {type(item).__name__}]")
                else:
                    output_parts.append(str(item))

        result_text = "\n".join(output_parts)
        is_err = getattr(res, "is_error", getattr(res, "isError", False))
        if is_err:
            return f"ERROR from {self.server_id}/{tool_name}:\n{result_text}"
        return result_text if result_text else "Tool executed successfully with no output."


class DefaultMCPServerInstance(MCPServerInstance):
    """
    Built-in default MCP server instance (`pequeno-core`) that comes connected out of the box
    with Pequeño Asistente, exposing Memory, Media (Documents & Images), Skills, Automations,
    and Security tools.
    """
    def __init__(self, server_id: str = DEFAULT_MCP_SERVER_ID, config: Optional[Dict[str, Any]] = None, config_manager: Any = None):
        default_cfg = config or {
            "name": "Pequeño Asistente Core (Por Defecto)",
            "command": sys.executable,
            "args": ["-m", "pequeno_asistente.mcp_client.default_server"],
            "transport": "stdio",
            "disabled": False,
            "builtin": True
        }
        super().__init__(server_id, default_cfg)
        self.config_manager = config_manager
        self.tools = build_default_mcp_tool_objects()
        self.status = "connected"

    async def connect(self):
        self.error_message = None
        self.tools = build_default_mcp_tool_objects()
        self.status = "connected"
        logger.info(f"Connected built-in default MCP '{self.server_id}' with {len(self.tools)} native tools.")

    async def disconnect(self):
        self.status = "disconnected"
        self.tools = []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if self.status != "connected":
            raise RuntimeError(f"Default MCP Server '{self.server_id}' is not connected.")
        return await execute_default_mcp_tool(tool_name, arguments, config_manager=self.config_manager)


class MCPManager:
    def __init__(
        self,
        config_manager=None,
        registry_client: Optional[RegistryClient] = None,
        quiet_stderr: bool = False,
    ):
        self.config_manager = config_manager
        self.registry_client = registry_client or RegistryClient()
        self.quiet_stderr = quiet_stderr
        self.instances: Dict[str, MCPServerInstance] = {}
        self.tool_map: Dict[str, Tuple[str, str]] = {}  # namespaced_name -> (server_id, tool_name)
        self.alias_map: Dict[str, str] = {}  # alias -> namespaced_name
        self._sync_lock = asyncio.Lock()

        # Connect built-in default MCP server (`pequeno-core`) on initialization
        self._ensure_default_server_instance()
        self._rebuild_tool_map()

    def _ensure_default_server_instance(self, servers_config: Optional[Dict[str, Any]] = None):
        """Ensures the default built-in MCP server (`pequeno-core`) is connected unless explicitly disabled."""
        cfg = None
        if servers_config and DEFAULT_MCP_SERVER_ID in servers_config:
            cfg = servers_config[DEFAULT_MCP_SERVER_ID]
            if cfg.get("disabled", False):
                if DEFAULT_MCP_SERVER_ID in self.instances:
                    del self.instances[DEFAULT_MCP_SERVER_ID]
                return
        elif self.config_manager:
            all_srvs = self.config_manager.get_mcp_servers()
            if DEFAULT_MCP_SERVER_ID in all_srvs:
                cfg = all_srvs[DEFAULT_MCP_SERVER_ID]
                if cfg.get("disabled", False):
                    if DEFAULT_MCP_SERVER_ID in self.instances:
                        del self.instances[DEFAULT_MCP_SERVER_ID]
                    return

        inst = self.instances.get(DEFAULT_MCP_SERVER_ID)
        if not isinstance(inst, DefaultMCPServerInstance) or inst.status != "connected":
            self.instances[DEFAULT_MCP_SERVER_ID] = DefaultMCPServerInstance(
                server_id=DEFAULT_MCP_SERVER_ID,
                config=cfg,
                config_manager=self.config_manager
            )
        else:
            inst.config_manager = self.config_manager

    async def sync_servers(self, servers_config: Dict[str, Any], force: bool = False):
        """
        Starts active servers and stops disabled/removed servers.
        Always keeps the default `pequeno-core` MCP server connected unless explicitly disabled.
        """
        async with self._sync_lock:
            # Ensure default built-in MCP server is in servers_config unless disabled
            effective_config = dict(servers_config or {})
            if DEFAULT_MCP_SERVER_ID not in effective_config:
                effective_config[DEFAULT_MCP_SERVER_ID] = {
                    "name": "Pequeño Asistente Core (Por Defecto)",
                    "command": sys.executable,
                    "args": ["-m", "pequeno_asistente.mcp_client.default_server"],
                    "transport": "stdio",
                    "disabled": False,
                    "builtin": True
                }

            # Stop servers no longer in config or newly disabled
            for server_id, instance in list(self.instances.items()):
                cfg = effective_config.get(server_id)
                if not cfg or cfg.get("disabled", False) or (force and server_id != DEFAULT_MCP_SERVER_ID):
                    await instance.disconnect()
                    del self.instances[server_id]

            # Sync default server immediately
            self._ensure_default_server_instance(effective_config)
            self._rebuild_tool_map()

            async def _connect_worker(inst: MCPServerInstance):
                try:
                    await inst.connect()
                except Exception as e:
                    logger.error(f"Error in connect worker for '{inst.server_id}': {e}")
                finally:
                    self._rebuild_tool_map()

            # Start or restart external servers
            tasks = []
            for server_id, cfg in effective_config.items():
                if cfg.get("disabled", False) or server_id == DEFAULT_MCP_SERVER_ID:
                    continue

                instance = self.instances.get(server_id)
                if force or not instance or instance.status not in ("connected", "connecting"):
                    if instance:
                        await instance.disconnect()
                    new_inst = MCPServerInstance(server_id, cfg, quiet_stderr=self.quiet_stderr)
                    self.instances[server_id] = new_inst
                    tasks.append(_connect_worker(new_inst))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            self._rebuild_tool_map()

    def get_server_status(self, server_id: str) -> Dict[str, Any]:
        inst = self.instances.get(server_id)
        if not inst:
            return {"status": "disconnected", "tools_count": 0, "error": None, "transport": None}
        return {
            "status": inst.status,
            "tools_count": len(inst.tools),
            "error": inst.error_message,
            "transport": inst.active_transport or inst.config.get("transport", "stdio")
        }

    @staticmethod
    async def test_server_connection(config: Dict[str, Any], server_id: str = "test-mcp") -> Dict[str, Any]:
        """
        Tests connecting to an MCP server (local stdio or remote SSE / Streamable HTTP),
        discovers its tools, and cleanly disconnects.
        """
        normalized = normalize_mcp_server_config(config, fallback_name=server_id)
        inst = MCPServerInstance(server_id, normalized)
        try:
            await inst.connect()
            if inst.status == "connected":
                tool_names = [getattr(t, "name", str(t)) for t in inst.tools]
                return {
                    "ok": True,
                    "status": "connected",
                    "transport": inst.active_transport or normalized.get("transport", "stdio"),
                    "tools_count": len(tool_names),
                    "tools": tool_names,
                    "error": None
                }
            else:
                return {
                    "ok": False,
                    "status": inst.status,
                    "transport": inst.active_transport or normalized.get("transport", "stdio"),
                    "tools_count": 0,
                    "tools": [],
                    "error": inst.error_message or "No se pudo conectar al servidor MCP."
                }
        except Exception as e:
            return {
                "ok": False,
                "status": "error",
                "transport": normalized.get("transport", "stdio"),
                "tools_count": 0,
                "tools": [],
                "error": str(e)
            }
        finally:
            await inst.disconnect()

    def _rebuild_tool_map(self):
        self.tool_map.clear()
        self.alias_map.clear()

        # 1. Register tools from Default Core MCP (`pequeno-core`)
        core_inst = self.instances.get(DEFAULT_MCP_SERVER_ID)
        if core_inst and core_inst.status == "connected":
            for tool_def in ALL_DEFAULT_TOOLS_DEFINITIONS:
                fn_name = tool_def["function"]["name"]
                orig_name = fn_name.split("__", 1)[1] if "__" in fn_name else fn_name
                self.tool_map[fn_name] = (DEFAULT_MCP_SERVER_ID, fn_name)

                # Aliases: single underscore, bare name, and server-prefixed name
                self.alias_map[fn_name.replace("__", "_")] = fn_name
                self.alias_map[f"pequeno_core__{fn_name}"] = fn_name
                self.alias_map[f"pequeno_core__{orig_name}"] = fn_name

                if fn_name.startswith("skills__"):
                    self.alias_map[f"{orig_name}_skill"] = fn_name
                    self.alias_map[f"{orig_name}_skills"] = fn_name
                elif fn_name == "media__read_document":
                    self.alias_map["read_document"] = fn_name
                    self.alias_map["extract_document"] = fn_name
                    self.alias_map["leer_documento"] = fn_name
                elif fn_name == "media__read_image":
                    self.alias_map["read_image"] = fn_name
                    self.alias_map["analyze_image"] = fn_name
                    self.alias_map["leer_imagen"] = fn_name
                elif fn_name == "media__inspect_file":
                    self.alias_map["inspect_file"] = fn_name
                    self.alias_map["inspect_media"] = fn_name

                if orig_name not in self.alias_map:
                    self.alias_map[orig_name] = fn_name

        # 2. Register tools from external active MCP servers
        for server_id, inst in self.instances.items():
            if server_id == DEFAULT_MCP_SERVER_ID:
                continue
            if inst.status == "connected":
                for t in inst.tools:
                    safe_srv = "".join(c if c.isalnum() or c == "_" else "_" for c in server_id)
                    safe_tool = "".join(c if c.isalnum() or c == "_" else "_" for c in t.name)
                    namespaced_name = f"{safe_srv}__{safe_tool}"
                    self.tool_map[namespaced_name] = (server_id, t.name)

                    single_und = f"{safe_srv}_{safe_tool}"
                    self.alias_map[single_und] = namespaced_name

                    if t.name not in self.alias_map:
                        self.alias_map[t.name] = namespaced_name

    def get_litellm_tools(self) -> List[Dict[str, Any]]:
        # Ensure tool_map reflects any instances that might have connected
        connected_servers = {
            sid for sid, inst in self.instances.items()
            if sid != DEFAULT_MCP_SERVER_ID and inst.status == "connected" and inst.tools
        }
        mapped_servers = {
            sid for sid, _ in self.tool_map.values()
            if sid != DEFAULT_MCP_SERVER_ID
        }
        if connected_servers != mapped_servers or (
            DEFAULT_MCP_SERVER_ID in self.instances and not any(sid == DEFAULT_MCP_SERVER_ID for sid, _ in self.tool_map.values())
        ):
            self._rebuild_tool_map()

        tools = []

        # 1. Add Default Core MCP Tools (if `pequeno-core` is connected)
        core_inst = self.instances.get(DEFAULT_MCP_SERVER_ID)
        if core_inst and core_inst.status == "connected":
            tools.extend(ALL_DEFAULT_TOOLS_DEFINITIONS)

        # 2. Add tools from external active MCP servers
        for namespaced_name, (server_id, original_tool_name) in self.tool_map.items():
            if server_id == DEFAULT_MCP_SERVER_ID:
                continue
            inst = self.instances.get(server_id)
            if not inst:
                continue

            tool_def = next((t for t in inst.tools if t.name == original_tool_name), None)
            if not tool_def:
                continue

            desc = tool_def.description or f"MCP tool from {server_id}"
            schema = tool_def.inputSchema if hasattr(tool_def, "inputSchema") and tool_def.inputSchema else {"type": "object", "properties": {}}

            tools.append({
                "type": "function",
                "function": {
                    "name": namespaced_name,
                    "description": f"[{server_id}] {desc}",
                    "parameters": schema
                }
            })

        return tools

    async def call_tool(self, namespaced_name: str, arguments: Dict[str, Any]) -> str:
        # Resolve alias if exact name is not directly in tool_map
        if namespaced_name not in self.tool_map and namespaced_name in self.alias_map:
            namespaced_name = self.alias_map[namespaced_name]

        # If still not found, try refreshing tool map just in case a server connected recently
        if namespaced_name not in self.tool_map:
            self._rebuild_tool_map()
            if namespaced_name not in self.tool_map and namespaced_name in self.alias_map:
                namespaced_name = self.alias_map[namespaced_name]

        if namespaced_name not in self.tool_map:
            return f"Error: Tool '{namespaced_name}' not found on any active MCP server."

        server_id, original_name = self.tool_map[namespaced_name]

        inst = self.instances.get(server_id)
        if not inst:
            return f"Error: MCP Server '{server_id}' is not running."

        try:
            return await inst.call_tool(original_name, arguments)
        except Exception as e:
            return f"Execution error in {server_id}/{original_name}: {str(e)}"

    async def shutdown(self):
        for inst in self.instances.values():
            await inst.disconnect()
        self.instances.clear()
        self.tool_map.clear()

    def get_status_summary(self) -> Dict[str, Any]:
        return {
            "total_configured": len(self.instances),
            "connected": sum(1 for inst in self.instances.values() if inst.status == "connected"),
            "total_tools": len(self.tool_map),
            "servers": {
                sid: {
                    "status": inst.status,
                    "tools_count": len(inst.tools),
                    "error": inst.error_message
                }
                for sid, inst in self.instances.items()
            }
        }
