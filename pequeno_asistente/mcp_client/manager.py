"""
MCP Client Manager for Pequeño Asistente.
Manages connections to multiple MCP servers (stdio and sse),
aggregates their tools for LiteLLM, and routes tool calls strictly to active MCP servers.
"""
import os
import sys
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from pequeno_asistente.mcp_client.registry import RegistryClient

logger = logging.getLogger("pequeno_asistente.mcp")


class MCPServerInstance:
    def __init__(self, server_id: str, config: Dict[str, Any]):
        self.server_id = server_id
        self.config = config
        self.exit_stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.tools: List[Any] = []
        self.status: str = "disconnected" # "connected", "error", "connecting"
        self.error_message: Optional[str] = None

    async def connect(self):
        self.status = "connecting"
        self.error_message = None
        self.exit_stack = AsyncExitStack()

        transport = self.config.get("transport", "stdio")
        cmd = self.config.get("command", "")
        args = self.config.get("args", [])
        custom_env = self.config.get("env", {})

        # Merge environment with system PATH and env
        env = dict(os.environ)
        home = os.path.expanduser("~")
        extra_paths = [f"{home}/.local/bin", f"{home}/.cargo/bin", "/usr/local/bin", "/usr/bin"]
        env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")
        for k, v in custom_env.items():
            if v is not None and str(v).strip():
                env[k] = str(v)

        try:
            if transport == "stdio":
                if not cmd:
                    raise ValueError(f"No command specified for stdio server {self.server_id}")
                
                params = StdioServerParameters(command=cmd, args=args, env=env)
                read_stream, write_stream = await self.exit_stack.enter_async_context(stdio_client(params))
                self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            elif transport == "sse":
                url = self.config.get("url") or cmd
                if not url:
                    raise ValueError(f"No URL specified for SSE server {self.server_id}")
                read_stream, write_stream = await self.exit_stack.enter_async_context(sse_client(url))
                self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            else:
                raise ValueError(f"Unsupported transport: {transport}")

            await self.session.initialize()
            tool_resp = await self.session.list_tools()
            self.tools = tool_resp.tools or []
            self.status = "connected"
            logger.info(f"Connected to MCP '{self.server_id}' with {len(self.tools)} tools.")
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


class MCPManager:
    def __init__(self, config_manager=None, registry_client: Optional[RegistryClient] = None):
        self.config_manager = config_manager
        self.registry_client = registry_client or RegistryClient()
        self.instances: Dict[str, MCPServerInstance] = {}
        self.tool_map: Dict[str, Tuple[str, str]] = {} # namespaced_name -> (server_id, tool_name)
        self.alias_map: Dict[str, str] = {} # alias -> namespaced_name

    async def sync_servers(self, servers_config: Dict[str, Any]):
        """
        Starts active servers and stops disabled/removed servers.
        """
        # Stop servers no longer in config or newly disabled
        for server_id, instance in list(self.instances.items()):
            cfg = servers_config.get(server_id)
            if not cfg or cfg.get("disabled", False):
                await instance.disconnect()
                del self.instances[server_id]

        # Start or restart servers
        tasks = []
        for server_id, cfg in servers_config.items():
            if cfg.get("disabled", False):
                continue

            instance = self.instances.get(server_id)
            if not instance or instance.status != "connected":
                new_inst = MCPServerInstance(server_id, cfg)
                self.instances[server_id] = new_inst
                tasks.append(new_inst.connect())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._rebuild_tool_map()

    def _rebuild_tool_map(self):
        self.tool_map.clear()
        self.alias_map.clear()
        for server_id, inst in self.instances.items():
            if inst.status == "connected":
                for t in inst.tools:
                    safe_srv = "".join(c if c.isalnum() or c == "_" else "_" for c in server_id)
                    safe_tool = "".join(c if c.isalnum() or c == "_" else "_" for c in t.name)
                    namespaced_name = f"{safe_srv}__{safe_tool}"
                    self.tool_map[namespaced_name] = (server_id, t.name)

                    # Single underscore alias (e.g. if a model or provider collapsed __ into _)
                    single_und = f"{safe_srv}_{safe_tool}"
                    self.alias_map[single_und] = namespaced_name

                    # Original tool name alias (e.g. "read_file" or "execute_command" if not conflicting)
                    if t.name not in self.alias_map:
                        self.alias_map[t.name] = namespaced_name

    def get_litellm_tools(self) -> List[Dict[str, Any]]:
        tools = []
        
        # 1. Add tools from active MCP servers
        for namespaced_name, (server_id, original_tool_name) in self.tool_map.items():
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
