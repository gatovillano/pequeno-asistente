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


class ThreadCreateRequest(BaseModel):
    title: Optional[str] = Field(None, description="Initial title for the new thread")


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


# Thread Management Endpoints
@app.get("/api/threads", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def list_threads():
    _, _, th_mgr, _, _ = get_agent_stack()
    threads = th_mgr.list_threads()
    return {"threads": threads, "active_thread_id": th_mgr.active_thread_id}


@app.post("/api/threads", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def create_thread(body: ThreadCreateRequest):
    _, _, th_mgr, _, agent = get_agent_stack()
    title = body.title.strip() if body.title else "Nueva conversación"
    new_th = agent.new_thread(title=title)
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
        "scratchpad": thread_data.get("scratchpad", {})
    }


@app.delete("/api/threads/{thread_id}", dependencies=[Depends(verify_auth)], tags=["Threads"])
async def delete_thread(thread_id: str):
    _, _, th_mgr, _, agent = get_agent_stack()
    success = th_mgr.delete_thread(thread_id)
    agent.delete_session(thread_id)
    if not success:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"deleted": True, "thread_id": thread_id}


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
        on_error=_on_error
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
    from pequeno_asistente.agent.media_processor import is_model_multimodal
    active_m = cfg.get_active_model()
    active_p = cfg.get_active_provider()
    cached = get_cached_models(active_p)
    return {
        "active_model": active_m,
        "active_provider": active_p,
        "is_multimodal": is_model_multimodal(active_m, active_p),
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
            model_size=audio_cfg.get("whisper_model", "tiny"),
            language=audio_cfg.get("language")
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
