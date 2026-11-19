"""
FastAPI Server for Pequeño Asistente.
Exposes the assistant, MCP tools, memory, threads, and audio transcription
over a secure REST + SSE API for remote mobile and web clients (Cloudflare Tunnel, LAN, Tailscale).
"""
import os
import sys
import json
import uuid
import asyncio
import logging
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, List, Any, Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Header, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
import uvicorn

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager, clean_thread_title
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.security.manager import CredentialManager
from pequeno_asistente.security.credential import SecureCredential

logger = logging.getLogger("pequeno_asistente.server")

# Global instances
_config_manager: Optional[ConfigManager] = None
_memory_manager: Optional[MemoryManager] = None
_thread_manager: Optional[ThreadManager] = None
_mcp_manager: Optional[MCPManager] = None
_agent_core: Optional[AgentCore] = None


def get_agent_stack() -> tuple[ConfigManager, MemoryManager, ThreadManager, MCPManager, AgentCore]:
    """Retrieves or lazily initializes the core assistant stack."""
    global _config_manager, _memory_manager, _thread_manager, _mcp_manager, _agent_core
    if _config_manager is None:
        _config_manager = ConfigManager()
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    if _thread_manager is None:
        _thread_manager = ThreadManager()
    if _mcp_manager is None:
        _mcp_manager = MCPManager(_config_manager)
    if _agent_core is None:
        _agent_core = AgentCore(_config_manager, _mcp_manager, _memory_manager, _thread_manager)
    return _config_manager, _memory_manager, _thread_manager, _mcp_manager, _agent_core


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for FastAPI."""
    logger.info("Initializing Pequeño Asistente FastAPI server...")
    cfg, mem, th_mgr, mcp, agent = get_agent_stack()

    # Connect MCP servers in background without blocking server startup
    async def _init_mcp():
        try:
            logger.info("Connecting MCP servers in background...")
            servers_cfg = cfg.get_mcp_servers()
            await mcp.sync_servers(servers_cfg)
            connected_ids = [k for k, inst in mcp.instances.items() if inst.status == "connected"]
            logger.info(f"Connected {len(connected_ids)} MCP servers: {connected_ids}")
        except Exception as e:
            logger.warning(f"Could not connect all MCP servers on startup: {e}")

    init_task = asyncio.create_task(_init_mcp())

    yield

    # Clean shutdown
    if not init_task.done():
        init_task.cancel()

    # Clean shutdown
    logger.info("Shutting down MCP servers...")
    try:
        await mcp.shutdown()
    except Exception as e:
        logger.error(f"Error disconnecting MCP servers: {e}")


app = FastAPI(
    title="Pequeño Asistente API",
    description="Remote API for Pequeño Asistente Desktop Agent",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for mobile apps, web preview, and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security Dependency
async def verify_auth(authorization: Optional[str] = Header(None)):
    """Validates Bearer token if API key is configured."""
    cfg, _, _, _, _ = get_agent_stack()
    server_cfg = cfg.get_server_config()
    required_key = (server_cfg.get("api_key") or os.environ.get("PEQUENO_API_KEY") or "").strip()

    if not required_key:
        return True  # No auth required

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required (Bearer <token>)",
            headers={"WWW-Authenticate": "Bearer"}
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != required_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired Bearer token"
        )
    return True


# Pydantic Schemas
class ChatRequest(BaseModel):
    message: str = Field(..., description="User query or message")
    thread_id: Optional[str] = Field(None, description="Thread ID. If None or 'new', creates or uses active thread")
    stream: bool = Field(True, description="Whether to stream response via Server-Sent Events (SSE)")
    is_call_mode: bool = Field(False, description="Whether query is running in voice-call mode")
    attachments: Optional[List[str]] = Field(
        None,
        description="Optional list of local file paths (documents or images) attached to the message"
    )


class MediaUploadBase64Request(BaseModel):
    filename: str = Field(..., description="Original filename including extension (e.g. 'informe.pdf' or 'captura.png')")
    data_base64: str = Field(..., description="Base64-encoded file content (or data URI)")


class AbortRequest(BaseModel):
    thread_id: Optional[str] = Field(None, description="Target thread ID to abort")


class CredentialResponseRequest(BaseModel):
    request_id: str = Field(..., description="Unique credential request identifier")
    secret: Optional[str] = Field("", description="Password or credential secret entered by the user")
    username: Optional[str] = Field(None, description="Username if requested")
    cancelled: bool = Field(False, description="True if the user dismissed or cancelled the prompt")


# Pending credential requests for remote clients
pending_credential_requests: Dict[str, asyncio.Future] = {}


class SpecializedAgentProfile(BaseModel):
    """Agent with specialized prompt, reusable across all workspaces."""
    name: Optional[str] = Field(None, description="Nombre visible del agente especializado")
    description: Optional[str] = Field(None, description="Descripción corta del rol del agente")
    system_prompt: Optional[str] = Field(None, description="Prompt de sistema/instrucciones específicas del agente")


class WorkspaceProfile(BaseModel):
    """Workspace/project: one or more folders plus contextual memory for the agent."""
    name: Optional[str] = Field(None, description="Nombre del workspace/proyecto")
    description: Optional[str] = Field(None, description="Descripción del workspace")
    folders: Optional[List[str]] = Field(None, description="Lista de carpetas asociadas al workspace")
    memory_file: Optional[str] = Field(None, description="Ruta al archivo de memoria contextual del workspace")


class ThreadCreateRequest(BaseModel):
    title: Optional[str] = Field(None, description="Initial title for the new thread")
    workspace_id: Optional[str] = Field(None, description="ID de un workspace guardado en configuración")
    workspace_profile: Optional[WorkspaceProfile] = Field(
        None,
        description="Perfil inline de workspace (carpetas + memoria contextual). Si se combina con workspace_id, estos valores tienen prioridad."
    )
    agent_id: Optional[str] = Field(None, description="ID de un agente especializado (prompt) guardado en configuración")
    agent_profile: Optional[SpecializedAgentProfile] = Field(
        None,
        description="Perfil inline del agente especializado (prompt). Si se combina con agent_id, estos valores tienen prioridad."
    )


class ThreadTitleUpdateRequest(BaseModel):
    title: str = Field(..., description="Nuevo título de la conversación")


class ModelSelectRequest(BaseModel):
    model: str = Field(..., description="Model ID (e.g., 'openrouter/meta-llama/llama-3.3-70b-instruct')")
    provider: Optional[str] = Field(None, description="Provider ID")


class MCPServerUpsertRequest(BaseModel):
    id: str = Field(..., description="Unique MCP server identifier")
    name: Optional[str] = Field(None, description="Display name")
    transport: Optional[str] = Field("auto", description="Transport: 'stdio', 'sse', 'streamable_http', or 'auto'")
    url: Optional[str] = Field(None, description="Remote endpoint URL for SSE / Streamable HTTP servers")
    command: Optional[str] = Field(None, description="Command for local stdio servers (or URL)")
    args: Optional[List[str]] = Field(default_factory=list, description="Command arguments for stdio servers")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict, description="Custom HTTP headers for remote servers")
    api_key: Optional[str] = Field(None, description="Bearer token / API key for remote servers")
    env: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables")
    disabled: bool = Field(False, description="Whether the server is disabled")


# Endpoints
@app.get("/", tags=["Health"])
@app.get("/health", tags=["Health"])
async def health_check():
    cfg, _, _, mcp, agent = get_agent_stack()
    server_cfg = cfg.get_server_config()
    req_key = (server_cfg.get("api_key") or os.environ.get("PEQUENO_API_KEY") or "").strip()
    connected_servers = [k for k, inst in mcp.instances.items() if inst.status == "connected"]
    return {
        "status": "online",
        "service": "pequeno-asistente",
        "version": "1.0.0",
        "active_model": cfg.get_active_model(),
        "requires_auth": bool(req_key),
        "connected_mcp_servers": connected_servers
    }


@app.get("/api/status", dependencies=[Depends(verify_auth)], tags=["System"])
async def get_system_status():
    from pequeno_asistente.mcp_client.manager import is_remote_server_config
    cfg, mem, th_mgr, mcp, agent = get_agent_stack()
    threads = th_mgr.list_threads()
    server_cfg = cfg.get_server_config()
    req_key = (server_cfg.get("api_key") or os.environ.get("PEQUENO_API_KEY") or "").strip()

    return {
        "status": "online",
        "active_model": cfg.get_active_model(),
        "active_provider": cfg.get_active_provider(),
        "active_thread_id": th_mgr.active_thread_id,
        "threads_count": len(threads),
        "requires_auth": bool(req_key),
        "running_threads": agent.get_running_thread_ids(),
        "mcp_servers": [
            {
                "id": s_id,
                "name": s_cfg.get("name", s_id),
                "transport": mcp.get_server_status(s_id).get("transport") or s_cfg.get("transport", "stdio"),
                "is_remote": is_remote_server_config(s_cfg),
                "url": s_cfg.get("url", ""),
                "status": mcp.get_server_status(s_id).get("status", "configured"),
                "tools_count": mcp.get_server_status(s_id).get("tools_count", 0),
                "error": mcp.get_server_status(s_id).get("error")
            }
            for s_id, s_cfg in cfg.get_mcp_servers().items()
        ]
    }


@app.get("/api/mcp/servers", dependencies=[Depends(verify_auth)], tags=["MCP"])
async def list_mcp_servers():
    from pequeno_asistente.mcp_client.manager import is_remote_server_config
    cfg, _, _, mcp, _ = get_agent_stack()
    servers = []
    for s_id, s_cfg in cfg.get_mcp_servers().items():
        st = mcp.get_server_status(s_id)
        servers.append({
            "id": s_id,
            "name": s_cfg.get("name", s_id),
            "transport": st.get("transport") or s_cfg.get("transport", "stdio"),
            "is_remote": is_remote_server_config(s_cfg),
            "url": s_cfg.get("url", ""),
            "command": s_cfg.get("command", ""),
            "args": s_cfg.get("args", []),
            "has_auth": bool(s_cfg.get("api_key") or s_cfg.get("headers")),
            "disabled": s_cfg.get("disabled", False),
            "status": st.get("status", "disconnected"),
            "tools_count": st.get("tools_count", 0),
            "error": st.get("error")
        })
    return {"servers": servers}


@app.post("/api/mcp/servers", dependencies=[Depends(verify_auth)], tags=["MCP"])
async def upsert_mcp_server(body: MCPServerUpsertRequest):
    cfg, _, _, mcp, _ = get_agent_stack()
    clean_id = "".join(c if c.isalnum() or c == "-" else "_" for c in body.id.strip().lower())
    if not clean_id:
        raise HTTPException(status_code=400, detail="Server ID cannot be empty")
    raw_cfg = body.model_dump(exclude={"id"}, exclude_none=True)
    cfg.set_mcp_server(clean_id, raw_cfg)
    await mcp.sync_servers(cfg.get_mcp_servers(), force=True)
    return {
        "id": clean_id,
        "config": cfg.get_mcp_servers().get(clean_id),
        "status": mcp.get_server_status(clean_id)
    }


@app.post("/api/mcp/test", dependencies=[Depends(verify_auth)], tags=["MCP"])
async def test_mcp_server_endpoint(body: MCPServerUpsertRequest):
    raw_cfg = body.model_dump(exclude={"id"}, exclude_none=True)
    return await MCPManager.test_server_connection(raw_cfg, server_id=body.id or "test-mcp")


@app.delete("/api/mcp/servers/{server_id}", dependencies=[Depends(verify_auth)], tags=["MCP"])
async def delete_mcp_server_endpoint(server_id: str):
    cfg, _, _, mcp, _ = get_agent_stack()
    if server_id not in cfg.get_mcp_servers():
        raise HTTPException(status_code=404, detail=f"MCP Server '{server_id}' not found")
    cfg.remove_mcp_server(server_id)
    await mcp.sync_servers(cfg.get_mcp_servers())
    return {"deleted": True, "server_id": server_id}


# Skills Management Endpoints
@app.get("/api/skills", dependencies=[Depends(verify_auth)], tags=["Skills"])
async def list_skills(q: Optional[str] = None):
    from pequeno_asistente.agent.skills_manager import SkillManager
    mgr = SkillManager.get_instance()
    skills = mgr.discover_skills()
    if q and q.strip():
        matches = mgr.match_relevant_skills(q.strip(), top_k=10, threshold=0.10)
        return {"skills": [s.to_dict() for s, score in matches]}
    return {"skills": [s.to_dict() for s in skills.values() if s.enabled]}


# Specialized Agents Endpoints (prompt-only, reusable across workspaces)
class AgentUpsertRequest(BaseModel):
    id: Optional[str] = Field(None, description="Agent ID (slug). Si se omite, se genera desde el nombre.")
    name: str = Field(..., description="Nombre visible del agente")
    description: Optional[str] = Field("", description="Descripción corta del rol")
    system_prompt: str = Field("", description="Prompt/instrucciones específicas del agente")


def _slugify_id(text: str, prefix: str = "item") -> str:
    import re, uuid
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", (text or "").strip().lower()).strip("-")
    return base[:40] or f"{prefix}-{uuid.uuid4().hex[:6]}"


def _slugify_agent_id(text: str) -> str:
    return _slugify_id(text, "agent")


@app.get("/api/agents", dependencies=[Depends(verify_auth)], tags=["Agents"])
async def list_agents():
    cfg, _, _, _, _ = get_agent_stack()
    agents = cfg.get_agents()
    items = []
    for aid, data in agents.items():
        item = dict(data or {})
        item.setdefault("id", aid)
        item.pop("workspace_path", None)
        items.append(item)
    items.sort(key=lambda x: (x.get("name") or x.get("id") or "").lower())
    return {"agents": items}


@app.post("/api/agents", dependencies=[Depends(verify_auth)], tags=["Agents"])
async def upsert_agent(body: AgentUpsertRequest):
    cfg, _, _, _, _ = get_agent_stack()
    agent_id = (body.id or "").strip() or _slugify_agent_id(body.name)
    data = {
        "id": agent_id,
        "name": body.name.strip(),
        "description": (body.description or "").strip(),
        "system_prompt": (body.system_prompt or "").strip(),
    }
    cfg.save_agent(agent_id, data)
    return {"agent": data}


@app.get("/api/agents/{agent_id}", dependencies=[Depends(verify_auth)], tags=["Agents"])
async def get_agent(agent_id: str):
    cfg, _, _, _, _ = get_agent_stack()
    agent = cfg.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    out = dict(agent)
    out.setdefault("id", agent_id)
    out.pop("workspace_path", None)
    return out


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(verify_auth)], tags=["Agents"])
async def delete_agent(agent_id: str):
    cfg, _, _, _, _ = get_agent_stack()
    ok = cfg.delete_agent(agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"deleted": True, "agent_id": agent_id}


# Workspaces Endpoints (project folders + contextual memory)
class WorkspaceUpsertRequest(BaseModel):
    id: Optional[str] = Field(None, description="Workspace ID (slug). Si se omite, se genera desde el nombre.")
    name: str = Field(..., description="Nombre del workspace/proyecto")
    description: Optional[str] = Field("", description="Descripción del workspace")
    folders: Optional[List[str]] = Field(default_factory=list, description="Carpetas asociadas al workspace")
    memory_file: Optional[str] = Field("", description="Ruta al archivo de memoria contextual (por defecto CONTEXT.md en la primera carpeta)")


def _prepare_workspace_profile(cfg, raw: Dict[str, Any], ws_id: Optional[str] = None) -> Dict[str, Any]:
    from pathlib import Path as _Path
    data = dict(raw or {})
    if ws_id and not data.get("id"):
        data["id"] = ws_id
    folders_in = data.get("folders") or []
    if not folders_in and data.get("workspace_path"):
        folders_in = [data.get("workspace_path")]
    folders: List[str] = []
    for f in folders_in:
        if not f or not str(f).strip():
            continue
        p = _Path(str(f)).expanduser().resolve()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        folders.append(str(p))
    data["folders"] = folders

    mem_file = (data.get("memory_file") or "").strip()
    if not mem_file and folders:
        mem_file = str(_Path(folders[0]) / "CONTEXT.md")
    if mem_file:
        mp = _Path(mem_file).expanduser()
        if not mp.is_absolute() and folders:
            mp = _Path(folders[0]) / mp
        mp = mp.resolve()
        try:
            mp.parent.mkdir(parents=True, exist_ok=True)
            if not mp.exists():
                ws_name = data.get("name") or "Workspace"
                mp.write_text(f"# Memoria contextual del workspace: {ws_name}\n\nNotas, decisiones y contexto persistente del proyecto.\n", encoding="utf-8")
        except Exception:
            pass
        mem_file = str(mp)
    data["memory_file"] = mem_file
    data.pop("workspace_path", None)
    return data


@app.get("/api/workspaces", dependencies=[Depends(verify_auth)], tags=["Workspaces"])
async def list_workspaces():
    cfg, _, _, _, _ = get_agent_stack()
    workspaces = cfg.get_workspaces()
    items = []
    for wid, data in workspaces.items():
        item = dict(data or {})
        item.setdefault("id", wid)
        items.append(item)
    items.sort(key=lambda x: (x.get("name") or x.get("id") or "").lower())
    return {"workspaces": items}


@app.post("/api/workspaces", dependencies=[Depends(verify_auth)], tags=["Workspaces"])
async def upsert_workspace(body: WorkspaceUpsertRequest):
    cfg, _, _, _, _ = get_agent_stack()
    workspace_id = (body.id or "").strip() or _slugify_id(body.name, "workspace")
    data = _prepare_workspace_profile(cfg, {
        "id": workspace_id,
        "name": body.name.strip(),
        "description": (body.description or "").strip(),
        "folders": body.folders or [],
        "memory_file": (body.memory_file or "").strip(),
    }, workspace_id)
    cfg.save_workspace(workspace_id, data)
    return {"workspace": data}


@app.get("/api/workspaces/{workspace_id}", dependencies=[Depends(verify_auth)], tags=["Workspaces"])
async def get_workspace(workspace_id: str):
    cfg, _, _, _, _ = get_agent_stack()
    ws = cfg.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    out = dict(ws)
    out.setdefault("id", workspace_id)
    return out


@app.delete("/api/workspaces/{workspace_id}", dependencies=[Depends(verify_auth)], tags=["Workspaces"])
async def delete_workspace(workspace_id: str):
    cfg, _, _, _, _ = get_agent_stack()
    ok = cfg.delete_workspace(workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"deleted": True, "workspace_id": workspace_id}


# Thread Management Endpoints
@app.get("/api/threads", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def list_threads():
    _, _, th_mgr, _, _ = get_agent_stack()
    threads = th_mgr.list_threads()
    return {"threads": threads, "active_thread_id": th_mgr.active_thread_id}


@app.post("/api/threads", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def create_thread(body: ThreadCreateRequest):
    cfg, _, th_mgr, _, agent = get_agent_stack()

    # Resolve workspace (project folders + contextual memory)
    resolved_workspace: Optional[Dict[str, Any]] = None
    workspace_id = (body.workspace_id or "").strip() or None
    if workspace_id:
        saved_ws = cfg.get_workspace(workspace_id)
        if saved_ws and isinstance(saved_ws, dict):
            resolved_workspace = dict(saved_ws)
            resolved_workspace.setdefault("id", workspace_id)
    if body.workspace_profile:
        inline_ws = body.workspace_profile.model_dump(exclude_none=True)
        if resolved_workspace:
            resolved_workspace.update({k: v for k, v in inline_ws.items() if v not in (None, "", [])})
        else:
            resolved_workspace = inline_ws
    if resolved_workspace:
        resolved_workspace = _prepare_workspace_profile(cfg, resolved_workspace, workspace_id)
        if workspace_id and not resolved_workspace.get("id"):
            resolved_workspace["id"] = workspace_id

    # Resolve specialized agent profile (prompt-only, reusable across workspaces)
    resolved_agent: Optional[Dict[str, Any]] = None
    agent_id = (body.agent_id or "").strip() or None
    if agent_id:
        saved = cfg.get_agent(agent_id)
        if saved and isinstance(saved, dict):
            resolved_agent = dict(saved)
            resolved_agent.setdefault("id", agent_id)
            resolved_agent.pop("workspace_path", None)
    if body.agent_profile:
        inline = body.agent_profile.model_dump(exclude_none=True)
        inline.pop("workspace_path", None)
        if resolved_agent:
            resolved_agent.update({k: v for k, v in inline.items() if v not in (None, "")})
        else:
            resolved_agent = inline
    if resolved_agent and agent_id and not resolved_agent.get("id"):
        resolved_agent["id"] = agent_id

    default_title = "Nueva conversación"
    if resolved_workspace and resolved_workspace.get("name"):
        default_title = resolved_workspace.get("name")
    elif resolved_agent and resolved_agent.get("name"):
        default_title = resolved_agent.get("name")
    title = body.title.strip() if body.title else default_title

    new_th = agent.new_thread(
        title=title,
        agent_id=agent_id,
        agent_profile=resolved_agent,
        workspace_id=workspace_id or (resolved_workspace.get("id") if resolved_workspace else None),
        workspace_profile=resolved_workspace
    )
    return new_th


@app.get("/api/threads/{thread_id}", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def get_thread(thread_id: str):
    _, _, th_mgr, _, agent = get_agent_stack()
    thread_data = th_mgr.get_thread(thread_id)
    if not thread_data:
        raise HTTPException(status_code=404, detail="Thread not found")
    agent.load_thread(thread_id)
    return {
        "id": thread_id,
        "title": thread_data.get("title", "Conversación"),
        "created_at": thread_data.get("created_at"),
        "updated_at": thread_data.get("updated_at"),
        "messages": thread_data.get("messages", []),
        "scratchpad": thread_data.get("scratchpad", {}),
        "agent_id": thread_data.get("agent_id"),
        "agent_profile": thread_data.get("agent_profile"),
        "workspace_id": thread_data.get("workspace_id"),
        "workspace_profile": thread_data.get("workspace_profile"),
    }


@app.delete("/api/threads/{thread_id}", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def delete_thread(thread_id: str):
    _, _, th_mgr, _, agent = get_agent_stack()
    success = th_mgr.delete_thread(thread_id)
    agent.delete_session(thread_id)
    if not success:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"deleted": True, "thread_id": thread_id}


@app.put("/api/threads/{thread_id}/title", dependencies=[Depends(verify_auth)], tags=["Threads"])
@app.patch("/api/threads/{thread_id}/title", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def update_thread_title(thread_id: str, body: ThreadTitleUpdateRequest):
    _, _, th_mgr, _, agent = get_agent_stack()
    th = th_mgr.get_thread(thread_id)
    if not th:
        raise HTTPException(status_code=404, detail="Thread not found")
    success = th_mgr.update_title(thread_id, body.title, is_custom=True)
    updated = th_mgr.get_thread(thread_id)
    new_title = updated.get("title", body.title) if updated else body.title
    agent._notify_title_updated(thread_id, new_title)
    return {"thread_id": thread_id, "title": new_title, "success": success}


@app.post("/api/threads/{thread_id}/auto-title", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def trigger_auto_title(thread_id: str):
    _, _, th_mgr, _, agent = get_agent_stack()
    th = th_mgr.get_thread(thread_id)
    if not th:
        raise HTTPException(status_code=404, detail="Thread not found")
    generated = await agent.generate_thread_title(thread_id, force=True)
    current = th_mgr.get_thread(thread_id)
    final_title = generated or (current.get("title") if current else "Conversación")
    return {"thread_id": thread_id, "title": final_title, "generated": bool(generated)}


# Chat & Streaming Endpoints
@app.post("/api/chat", dependencies=[Depends(verify_auth)], tags=["Chat"])
async def chat(req: ChatRequest, request: Request):
    cfg, mem, th_mgr, mcp, agent = get_agent_stack()

    target_thread_id = req.thread_id
    if not target_thread_id or target_thread_id == "new":
        new_th = agent.new_thread(title=clean_thread_title(req.message))
        target_thread_id = new_th["id"]
    else:
        # Load thread into agent session if not already loaded
        agent.load_thread(target_thread_id)

    if not req.stream:
        # Synchronous execution
        completion_event = asyncio.Event()
        result_holder: Dict[str, Any] = {"content": "", "error": None}

        callbacks = AgentCallbacks(
            on_complete=lambda fin: (result_holder.update({"content": fin}), completion_event.set()),
            on_error=lambda err: (result_holder.update({"error": err}), completion_event.set())
        )

        await agent.run_turn(
            user_message=req.message,
            callbacks=callbacks,
            is_call_mode=req.is_call_mode,
            thread_id=target_thread_id,
            attachments=req.attachments
        )
        await completion_event.wait()

        if result_holder["error"]:
            raise HTTPException(status_code=500, detail=result_holder["error"])

        return {
            "thread_id": target_thread_id,
            "role": "assistant",
            "content": result_holder["content"]
        }

    # Stream execution via SSE
    queue: asyncio.Queue = asyncio.Queue()

    def _on_token(token: str):
        queue.put_nowait({"type": "token", "content": token})

    def _on_thinking(chunk: str):
        queue.put_nowait({"type": "thinking", "content": chunk})

    def _on_tool_start(name: str, args: Dict[str, Any]):
        queue.put_nowait({"type": "tool_start", "name": name, "args": args})

    def _on_tool_end(name: str, result: str):
        # Truncate large tool outputs in stream for efficiency
        display_res = result if len(result) < 600 else result[:600] + "… (truncado)"
        queue.put_nowait({"type": "tool_end", "name": name, "result": display_res})

    def _on_complete(final_text: str):
        queue.put_nowait({
            "type": "complete",
            "content": final_text,
            "thread_id": target_thread_id
        })

    def _on_error(err_msg: str):
        queue.put_nowait({"type": "error", "error": err_msg})

    def _on_dequeued():
        queue.put_nowait({"type": "dequeued", "thread_id": target_thread_id})

    def _on_title_updated(tid: str, title: str):
        queue.put_nowait({
            "type": "thread_title_updated",
            "thread_id": tid,
            "title": title
        })

    # If target session is currently busy, notify client that message is enqueued
    if agent.is_thread_running(target_thread_id):
        queue.put_nowait({
            "type": "queued",
            "thread_id": target_thread_id,
            "position": agent.get_queue_length(target_thread_id) + 1
        })

    callbacks = AgentCallbacks(
        on_token=_on_token,
        on_thinking=_on_thinking,
        on_tool_start=_on_tool_start,
        on_tool_end=_on_tool_end,
        on_complete=_on_complete,
        on_error=_on_error,
        on_thread_title_updated=_on_title_updated
    )

    active_turn_requests: List[str] = []

    async def _turn_credential_prompt(
        target: str,
        prompt: str,
        username: Optional[str] = None,
        title: Optional[str] = None,
        show_username_field: bool = False
    ) -> SecureCredential:
        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending_credential_requests[req_id] = fut
        active_turn_requests.append(req_id)

        queue.put_nowait({
            "type": "password_request",
            "request_id": req_id,
            "target": target,
            "title": title or ("Autenticación de Superusuario (sudo)" if target == "sudo" else "Autenticación requerida"),
            "prompt": prompt,
            "username": username or "",
            "show_username_field": show_username_field
        })

        try:
            res = await asyncio.wait_for(fut, timeout=120.0)
            if res.get("cancelled"):
                logger.info(f"Credential request {req_id} cancelled by user.")
                return SecureCredential(target=target, cancelled=True)
            return SecureCredential(
                target=target,
                secret=res.get("secret", ""),
                username=res.get("username", username),
                purpose=prompt,
                cancelled=False
            )
        except asyncio.TimeoutError:
            logger.warning(f"Credential request {req_id} timed out waiting for client response.")
            return SecureCredential(target=target, cancelled=True)
        finally:
            pending_credential_requests.pop(req_id, None)
            if req_id in active_turn_requests:
                active_turn_requests.remove(req_id)

    async def turn_worker():
        token = CredentialManager.set_turn_handler(_turn_credential_prompt)
        try:
            await agent.run_turn(
                user_message=req.message,
                callbacks=callbacks,
                is_call_mode=req.is_call_mode,
                thread_id=target_thread_id,
                on_dequeued=_on_dequeued,
                attachments=req.attachments
            )
        except Exception as ex:
            logger.error(f"Error in agent turn execution: {ex}", exc_info=True)
            queue.put_nowait({"type": "error", "error": str(ex)})
        finally:
            CredentialManager.reset_turn_handler(token)
            # Allow background title generation a short window to emit SSE event before closing stream
            sess = agent.get_session(target_thread_id)
            if sess and getattr(sess, "title_task", None) and not sess.title_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(sess.title_task), timeout=2.0)
                except Exception:
                    pass
            for r_id in list(active_turn_requests):
                f = pending_credential_requests.pop(r_id, None)
                if f and not f.done():
                    f.set_result({"cancelled": True})
            queue.put_nowait(None)  # Sentinel to end stream

    # Launch turn worker
    turn_task = asyncio.create_task(turn_worker())

    async def event_generator():
        try:
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    logger.info(f"Client disconnected during turn for thread {target_thread_id}")
                    agent.abort(target_thread_id)
                    for r_id in list(active_turn_requests):
                        f = pending_credential_requests.pop(r_id, None)
                        if f and not f.done():
                            f.set_result({"cancelled": True})
                    break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # Send periodic keep-alive comment so tunnel does not close
                    yield {"event": "ping", "data": ""}
                    continue

                if item is None:
                    break

                yield {
                    "event": "message",
                    "data": json.dumps(item)
                }
        except asyncio.CancelledError:
            agent.abort(target_thread_id)
            for r_id in list(active_turn_requests):
                f = pending_credential_requests.pop(r_id, None)
                if f and not f.done():
                    f.set_result({"cancelled": True})
        finally:
            if not turn_task.done():
                turn_task.cancel()

    return EventSourceResponse(event_generator())


@app.post("/api/chat/abort", dependencies=[Depends(verify_auth)], tags=["Chat"])
async def abort_chat(req: AbortRequest):
    _, _, _, _, agent = get_agent_stack()
    agent.abort(req.thread_id)
    # Cancel any pending credential requests
    for req_id, fut in list(pending_credential_requests.items()):
        if not fut.done():
            fut.set_result({"cancelled": True})
    return {"status": "abort_requested", "thread_id": req.thread_id}


@app.post("/api/security/credential-response", dependencies=[Depends(verify_auth)], tags=["Security"])
async def respond_credential(body: CredentialResponseRequest):
    """Submits a user-entered password/secret in response to a password_request event."""
    future = pending_credential_requests.get(body.request_id)
    if not future or future.done():
        raise HTTPException(
            status_code=404,
            detail="Solicitud de credencial expirada o inexistente."
        )

    future.set_result({
        "secret": body.secret or "",
        "username": body.username,
        "cancelled": body.cancelled
    })
    return {"status": "ok", "request_id": body.request_id}


# Model Management Endpoints
@app.get("/api/models", dependencies=[Depends(verify_auth)], tags=["Models"])
async def list_models():
    cfg, _, _, _, _ = get_agent_stack()
    from pequeno_asistente.agent.model_fetcher import get_cached_models
    from pequeno_asistente.agent.media_processor import is_model_multimodal, is_model_audio_multimodal
    active_m = cfg.get_active_model()
    active_p = cfg.get_active_provider()
    cached = get_cached_models(active_p)
    return {
        "active_model": active_m,
        "active_provider": active_p,
        "is_multimodal": is_model_multimodal(active_m, active_p),
        "is_audio_multimodal": is_model_audio_multimodal(active_m, active_p),
        "cached_models": cached
    }


@app.post("/api/media/upload", dependencies=[Depends(verify_auth)], tags=["Media"])
async def upload_media_base64(body: MediaUploadBase64Request):
    """Uploads an image or document in Base64 and stores it in the local attachments directory."""
    import base64
    from pequeno_asistente.agent.media_processor import inspect_file

    safe_name = os.path.basename(body.filename.strip()) or "adjunto.bin"
    raw_b64 = body.data_base64.strip()
    if raw_b64.startswith("data:") and "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        file_bytes = base64.b64decode(raw_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 payload: {e}")

    upload_dir = Path.home() / ".config" / "pequeno-asistente" / "attachments"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    with open(target_path, "wb") as f:
        f.write(file_bytes)

    return inspect_file(str(target_path))


@app.get("/api/media/inspect", dependencies=[Depends(verify_auth)], tags=["Media"])
async def inspect_local_media(path: str):
    """Inspects metadata of a local document or image file."""
    from pequeno_asistente.agent.media_processor import inspect_file
    info = inspect_file(path)
    if not info.get("exists"):
        raise HTTPException(status_code=404, detail=info.get("error", "File not found"))
    return info


@app.post("/api/models", dependencies=[Depends(verify_auth)], tags=["Models"])
async def select_model(body: ModelSelectRequest):
    cfg, _, _, _, _ = get_agent_stack()
    cfg.set_active_model(body.model)
    if body.provider:
        cfg.set_active_provider(body.provider)
    return {
        "status": "updated",
        "active_model": cfg.get_active_model(),
        "active_provider": cfg.get_active_provider()
    }


# Audio Transcription Endpoint
@app.post("/api/audio/transcribe", dependencies=[Depends(verify_auth)], tags=["Audio"])
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribes an uploaded audio file using the local faster-whisper engine."""
    from pequeno_asistente.audio.transcriber import AudioTranscriber

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        cfg, _, _, _, _ = get_agent_stack()
        audio_cfg = cfg.get_audio_config() if hasattr(cfg, "get_audio_config") else {}
        transcriber = AudioTranscriber(
            model_size=audio_cfg.get("whisper_model", "small"),
            device=audio_cfg.get("device", "cuda"),
            compute_type=audio_cfg.get("compute_type", "float16"),
            language=audio_cfg.get("language", "es")
        )
        text = transcriber.transcribe_file(tmp_path)
        return {"text": text}
    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def run_server(host: Optional[str] = None, port: Optional[int] = None, api_key: Optional[str] = None):
    """Entrypoint to run uvicorn server directly."""
    cfg, _, _, _, _ = get_agent_stack()
    server_cfg = cfg.get_server_config()
    final_host = host or server_cfg.get("host", "0.0.0.0")
    final_port = port or server_cfg.get("port", 8765)
    if api_key:
        cfg.set_server_config(api_key=api_key)

    active_key = server_cfg.get("api_key") or os.environ.get("PEQUENO_API_KEY") or ""
    print(f"🚀 Iniciando Pequeño Asistente FastAPI en http://{final_host}:{final_port}")
    if active_key:
        print(f"🔒 Autenticación activada. Requiere cabecera: Authorization: Bearer {active_key[:4]}••••")
    else:
        print("⚠️  Aviso: Sin API Key configurada. Se recomienda configurar una antes de exponer a internet.")

    uvicorn.run(app, host=final_host, port=final_port, log_level="info")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pequeño Asistente FastAPI Server")
    parser.add_argument("--host", default=None, help="Host address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: 8765)")
    parser.add_argument("--api-key", default=None, help="Set or override Bearer API key")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, api_key=args.api_key)
