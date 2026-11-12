"""
Agent Core for Pequeño Asistente.
Pure MCP-driven agent with layered memory, LiteLLM multi-provider gateway routing,
and native Google Antigravity OAuth2 support matching KogniTerm.
"""
import os
import json
import asyncio
import threading
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, AsyncIterator
import litellm

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager, clean_llm_title, clean_thread_title
from pequeno_asistente.agent.antigravity_client import AntigravityClient
from pequeno_asistente.agent.media_processor import (
    is_model_multimodal,
    is_model_audio_multimodal,
    extract_file_paths_from_text,
    process_image,
    process_audio,
    process_document,
    is_image_file,
    is_audio_file,
    is_document_file,
    resolve_local_path,
)

# Configure litellm to avoid debug noise
litellm.drop_params = True
litellm.telemetry = False
litellm.set_verbose = False
litellm.suppress_debug_info = True

logger = logging.getLogger("pequeno_asistente.agent")

BASE_SYSTEM_PROMPT = """Eres Pequeño Asistente, un agente de escritorio ligero, inteligente y proactivo para Linux (verifica qué escritorio).
Tu objetivo es ayudar al usuario de manera eficiente, directa y amigable en español.

REGLAS FUNDAMENTALES Y DISCIPLINA DE HERRAMIENTAS:
1. ARQUITECTURA PURA MCP: Todas tus herramientas operan exclusivamente mediante los servidores MCP conectados. No posees herramientas nativas ocultas.
2. DISCIPLINA ESTRICTA DE HERRAMIENTAS (¡CRÍTICO!):
   - NUNCA ejecutes herramientas ante saludos ("Hola", "¿cómo estás?", "buenos días", etc.), agradecimientos, despedidas, preguntas conceptuales o conversación general. Para tales casos, responde SIEMPRE de forma directa con texto.
   - SÓLO debes invocar una herramienta cuando el usuario pida expresamente una acción sobre su sistema o requiera datos en tiempo real (ej. consultar la hora, listar/leer archivos, capturar pantalla, consultar el clima).
   - ¡NO USES HERRAMIENTAS DE TERMINAL/SCRIPTS PARA ANALIZAR ARCHIVOS DE AUDIO ADJUNTOS!: Si el usuario adjunta o te envía un archivo de audio para que lo escuches o analices, YA TIENES el contenido sonoro real inyectado en tu contexto multimodal. ESTÁ PROHIBIDO ejecutar comandos de terminal, scripts de Python (`wave`, `astats`), o `ffmpeg` para intentar "analizar" el audio por consola. Responde DIRECTAMENTE con tu análisis auditivo multimodal como respuesta final en texto, sin llamar a ninguna herramienta de consola o terminal.
   - NUNCA llames a herramientas irrelevantes o no relacionadas con la solicitud. Si no tienes la herramienta adecuada para cumplir lo que el usuario pide, explícaselo en texto natural con total transparencia.
   - Tras obtener el resultado de una herramienta, sintetiza inmediatamente tu respuesta final al usuario. No invoques más herramientas salvo que sea imprescindible para completar el requerimiento.
3. HABILIDADES Y PROCEDIMIENTOS (SKILLS) — PRIORIDAD Y RIGOR:
   - REGLA DE ORO DE HABILIDADES: Las habilidades procedimentales definidas en <skills> representan la forma correcta, probada y especializada de realizar tareas en este sistema. Tienen PRECEDENCIA ABSOLUTA sobre cualquier conocimiento genérico o improvisación.
   - OBLIGACIÓN DE CONSULTA Y APLICACIÓN:
     * Antes de ejecutar cualquier tarea especializada (ej. manejo de DAW/Bitwig, análisis, testing, instalación de herramientas, etc.), coteja SIEMPRE la solicitud con el catálogo de habilidades en <skills>.
     * Si la tarea coincide con una habilidad del catálogo:
       - Si su procedimiento ya está expandido en la sección de instrucciones detalladas, aplícalo con total rigor paso a paso.
       - Si figura en el catálogo pero sus instrucciones detalladas NO están completamente expandidas en el prompt, invoca de inmediato `skills__get(skill_id="<id>")` ANTES de emitir respuestas o ejecutar comandos a ciegas.
   - GESTIÓN DE SKILLS:
     * Cuentas con las herramientas `skills__list`, `skills__get`, `skills__install`, `skills__uninstall` y `skills__toggle`.
     * Si el usuario te pide instalar una nueva habilidad (desde un repo de GitHub, enlace o descripción), invoca `skills__install` o redacta su contenido con frontmatter.
   - INSTALACIÓN DE SERVIDORES MCP:
     * Si el usuario te pide instalar, compilar o agregar un nuevo servidor MCP (o te comparte un repositorio de GitHub, paquete npm/uvx o URL SSE), consulta y aplica rigurosamente el procedimiento detallado en la sección <skills>.
   - CONFIGURACIÓN:
     * Si cuentas con herramientas de sistema (filesystem o desktop-bash), puedes utilizarlas para inspeccionar o actualizar la configuración en `~/.config/pequeno-asistente/config.json`.
     * Si no posees herramientas de consola o el usuario lo prefiere, proporciónale el bloque JSON exacto listo para que lo copie y pegue en la interfaz de configuración del asistente.
   - NUNCA intentes llamar a herramientas en bucle si una llamada arroja un error o dice que no existe. Si una herramienta falla, detente de inmediato y explica el problema al usuario con alternativas claras.
4. MEMORIA PERSONALIZADA Y COGNITIVA EPISÓDICA:
   - Mantén en cuenta el perfil del usuario, reglas aprendidas y recuerdos episódicos provistos en <user_memory_system>.
   - Cuentas con las herramientas `memory__search_episodes`, `memory__save_episode`, `memory__get_timeline`, `memory__remember_fact` y `memory__stats` para buscar activamente en el historial episódico (por texto, fechas, etiquetas o herramientas usadas), registrar hitos/soluciones importantes o almacenar hechos duraderos.
5. COMANDOS DE CONSOLA / TERMINAL VS COMANDOS SUDO (¡REGLA ESTRICTA DE SELECCIÓN!):
   - PARA COMANDOS GENERALES Y ORDINARIOS DE TERMINAL (NO SUDO):
     * Comandos estándar: `ls`, `pwd`, `cat`, `head`, `tail`, `grep`, `find`, `git`, `python`, `node`, `npm`, `cargo`, `curl`, `wget`, `which`, `ps`, `df`, `free`, scripts de usuario, compilaciones, lectura/escritura en carpetas del usuario (~/Proyectos, ~/Documentos, etc.).
     * DEBES usar SIEMPRE tus herramientas de terminal MCP regulares (por ejemplo las herramientas del servidor `desktop-bash` como `execute_command` o similares).
     * ¡ESTÁ COMPLETAMENTE PROHIBIDO usar `security__execute_sudo` para comandos que no son sudo!
     * Si no tienes conectado un servidor MCP de terminal para esa tarea o el usuario lo prefiere, indícale claramente que puede utilizar la Terminal Interactiva PTY integrada (`Ctrl+T` o botón `[>_]`) o proporciona el comando en un bloque de código Markdown copiable.
   - PARA COMANDOS ADMINISTRATIVOS QUE REQUIEREN PRIVILEGIOS ROOT (SUDO):
     * ÚNICAMENTE cuando una operación exija OBLIGATORIAMENTE permisos de administrador root del sistema:
       - Instalación o actualización de paquetes del sistema (`apt install`, `apt update`, `pacman`, `dnf`).
       - Gestión de servicios del sistema (`systemctl restart`, `systemctl start`, `systemctl stop`).
       - Configuración de cortafuegos o redes del sistema (`ufw`, `iptables`).
       - Modificación de archivos protegidos en `/etc/`, montaje de discos (`mount`, `umount`), o creación de usuarios del sistema.
     * En esos casos y SOLO en esos casos, invoca `security__execute_sudo`. El sistema abrirá automáticamente el diálogo modal seguro solicitando la contraseña al usuario si es necesario.
   - GESTIÓN SEGURA DE CONTRASEÑAS:
     * NUNCA le pidas al usuario que escriba contraseñas, claves privadas ni secretos en texto plano dentro del chat.
     * Si necesitas una contraseña o token para configurar bases de datos, APIs o servicios, invoca `security__request_credential`. Obtendrás un objeto custodiado en memoria (con `credential_id`) para aplicarlo con `security__apply_credential`.
6. AUTOMATIZACIONES Y HEARTBEATS (PULSOS PROACTIVOS):
   - Cuentas con un motor nativo de Automatizaciones y Heartbeats configurable tanto en la interfaz gráfica (`Ctrl+B` o botón de reloj/pulso en la barra superior, y en Configuración ➔ Heartbeats) como mediante las herramientas `automation__list`, `automation__create`, `automation__update`, `automation__delete` y `automation__configure_heartbeat`.
   - Si el usuario te pide programar una tarea periódica, un recordatorio recurrente, un chequeo de sistema (Heartbeat) o actualizar su archivo `HEARTBEAT.md`, utiliza directamente las herramientas `automation__*`.
8. 7. LECTURA DE DOCUMENTOS, IMÁGENES, AUDIO Y ARCHIVOS ADJUNTOS (MCP POR DEFECTO `pequeno-core`):
   - Cuentas con el servidor MCP nativo por defecto `pequeno-core` que expone todas las herramientas base (`memory__*`, `skills__*`, `automation__*`, `security__*` y `media__*`).
   - Cuando el usuario adjunta documentos, imágenes o archivos de audio desde la interfaz o menciona rutas locales en su mensaje, el sistema procesa automáticamente los documentos e inyecta las imágenes y el audio directamente en tu contexto multimodal si tu modelo activo lo soporta.
   - DIRECTIVA CRÍTICA DE ESCUCHA MULTIMODAL DE AUDIO (¡PROHIBIDO ALUCINAR!):
     * Cuando recibes un archivo de audio adjunto o inyectado con `mode="multimodal_audio"`, tu modelo recibe las ondas sonoras reales directamente en sus capas de atención multimodal.
     * DEBES escuchar y analizar exclusivamente lo que suena en el audio (instrumentación real, timbres acústicos o sintetizados, tempo percibido, silencios y dinámica).
     * NUNCA inventes o deduzcas letras, voces, historias, diálogos o instrumentos imaginarios basándote únicamente en el nombre del archivo (ejemplo: si el archivo se llama "membrana.wav", no asumas que trata de una membrana física ni inventes cantantes o géneros estereotipados).
     * Si el audio es instrumental o un stem musical, descríbelo con precisión objetiva como tal.
   - Si necesitas leer, escuchar o inspeccionar cualquier documento, imagen o archivo de audio adicional por su ruta en el sistema de archivos:
     * `media__read_document(path, start_page, end_page, max_chars, search_query)`: Extrae texto estructurado, tablas Markdown y metadatos de archivos PDF, DOCX, ODT, XLSX, PPTX, CSV, TSV, EPUB, HTML, RTF, Markdown, JSON y código fuente.
     * `media__read_image(path, extract_ocr, max_dimension)`: Inspecciona y carga imágenes locales (PNG, JPG, WEBP, GIF, BMP, SVG, TIFF). Si tu modelo es multimodal, al invocar esta herramienta la imagen se inyectará automáticamente en tu contexto visual para que puedas verla y analizarla; además puedes activar `extract_ocr=True` para extraer texto por OCR.
     * `media__read_audio(path)`: Inspecciona y carga archivos de audio locales (WAV, MP3, OGG, OPUS, FLAC, M4A, AAC, WEBM, AIFF). Si tu modelo soporta procesamiento multimodal de audio, al invocar esta herramienta el audio se inyectará directamente en tu contexto multimodal (`input_audio` / `inlineData`) para que puedas escucharlo y analizarlo con tus capacidades multimodales nativas.
     * `media__inspect_file(path)`: Inspecciona metadatos detallados (tamaño, tipo MIME, permisos, páginas, duración/frecuencia o dimensiones) de cualquier ruta local antes de leerla.
8. ESTILO:
   - Respuestas claras, concisas, estructuradas en Markdown legible.
"""

def load_agent_skills(config_manager: Optional[ConfigManager] = None, query: Optional[str] = None) -> str:
    """
    Loads procedural markdown skills via SkillManager, aggregating skills from:
    - User configuration directory (~/.config/pequeno-asistente/skills)
    - Claude Code global & workspace skills (~/.claude/skills, .claude/skills)
    - Open Agent skills standard (~/.agents/skills, .agents/skills)
    - System-wide locations (/etc/agent-skills, /usr/share/agent-skills)
    - Antigravity / Gemini skills
    - Built-in package skills
    """
    try:
        from pequeno_asistente.agent.skills_manager import SkillManager
        mgr = SkillManager.get_instance(config_manager)
        return mgr.format_skills_for_prompt(query=query)
    except Exception as e:
        logger.error(f"Error loading agent skills via SkillManager: {e}")
        return ""

def prepare_provider_call(model_name: str, config_manager: ConfigManager) -> tuple[str, Dict[str, Any]]:
    """
    Translates model_name and provider configuration into proper LiteLLM parameters
    or determines if native AntigravityClient should be used.
    Matches KogniTerm's MultiProviderManager conventions.
    """
    model_lower = model_name.lower()
    provider = config_manager.data["llm"].get("provider", "")

    # 1. Antigravity OAuth2 Native
    if model_lower.startswith("antigravity/") or provider == "antigravity":
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        return "antigravity", {
            "model": pure,
            "temperature": config_manager.data["llm"].get("temperature", 0.7)
        }

    kwargs: Dict[str, Any] = {
        "model": model_name,
        "temperature": config_manager.data["llm"].get("temperature", 0.7),
        "max_tokens": config_manager.data["llm"].get("max_tokens", 4096),
        "stream": True
    }

    # 2. KiloCode Gateway (OpenAI-compatible)
    if model_lower.startswith("kilocode/") or provider == "kilocode":
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        kwargs["model"] = f"openai/{pure}"
        kwargs["api_base"] = "https://api.kilo.ai/api/gateway/v1"
        kwargs["custom_llm_provider"] = "openai"
        key = config_manager.get_api_key("kilocode")
        if key:
            kwargs["api_key"] = key

    # 3. Inception Labs Gateway (OpenAI-compatible)
    elif model_lower.startswith("inception/") or provider == "inception" or "mercury" in model_lower:
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        kwargs["model"] = f"openai/{pure}"
        kwargs["api_base"] = "https://api.inceptionlabs.ai/v1"
        kwargs["custom_llm_provider"] = "openai"
        key = config_manager.get_api_key("inception")
        if key:
            kwargs["api_key"] = key

    # 4. OpenCode Zen Gateway (OpenAI-compatible)
    elif model_lower.startswith(("opencode/", "zen/")) or provider == "opencode":
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        kwargs["model"] = f"openai/{pure}"
        kwargs["api_base"] = "https://opencode.ai/zen/v1"
        kwargs["custom_llm_provider"] = "openai"
        key = config_manager.get_api_key("opencode")
        if key:
            kwargs["api_key"] = key

    # 5. Ollama Cloud (OpenAI-compatible endpoint)
    elif model_lower.startswith("ollama_cloud/") or provider == "ollama_cloud":
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        kwargs["model"] = f"openai/{pure}"
        kwargs["api_base"] = "https://ollama.com/v1"
        kwargs["custom_llm_provider"] = "openai"
        key = config_manager.get_api_key("ollama_cloud")
        if key:
            kwargs["api_key"] = key

    # 6. Ollama Local
    elif model_lower.startswith("ollama/") or provider == "ollama":
        pure = model_name.split("/", 1)[1] if "/" in model_name else model_name
        kwargs["model"] = f"ollama/{pure}"
        kwargs["api_base"] = config_manager.data["llm"].get("ollama_base_url", "http://localhost:11434")
        kwargs["custom_llm_provider"] = "ollama"

    # 7. OpenRouter
    elif model_lower.startswith("openrouter/") or provider == "openrouter":
        pure = model_name.split("/", 1)[1] if model_name.startswith("openrouter/") else model_name
        kwargs["model"] = f"openrouter/{pure}"
        key = config_manager.get_api_key("openrouter")
        if key:
            kwargs["api_key"] = key
        kwargs["headers"] = {
            "HTTP-Referer": "https://github.com/gatovillano/KogniTerm",
            "X-Title": "Pequeño Asistente"
        }

    # 8. Vercel AI Gateway (OpenAI-compatible)
    elif model_lower.startswith("vercel/") or provider in ("vercel", "vercel_ai_gateway"):
        pure = model_name.split("/", 1)[1] if model_name.startswith("vercel/") else model_name
        kwargs["model"] = f"openai/{pure}"
        kwargs["api_base"] = "https://ai-gateway.vercel.sh/v1"
        kwargs["custom_llm_provider"] = "openai"
        key = config_manager.get_api_key("vercel")
        if key:
            kwargs["api_key"] = key

    # 9. Google Gemini
    elif model_lower.startswith("gemini/") or provider == "google":
        pure = model_name.split("/", 1)[1] if model_name.startswith("gemini/") else model_name
        kwargs["model"] = f"gemini/{pure}"
        kwargs["custom_llm_provider"] = "gemini"
        key = config_manager.get_api_key("google")
        if key:
            kwargs["api_key"] = key
            os.environ["GEMINI_API_KEY"] = key

    # 9. OpenAI
    elif provider == "openai":
        kwargs["model"] = model_name
        key = config_manager.get_api_key("openai")
        if key:
            kwargs["api_key"] = key

    # 10. Anthropic
    elif provider == "anthropic":
        kwargs["model"] = model_name
        key = config_manager.get_api_key("anthropic")
        if key:
            kwargs["api_key"] = key

    return "litellm", kwargs


async def _stream_sync_generator(gen) -> AsyncIterator[Any]:
    """Wraps a synchronous generator into an asynchronous iterator via a thread queue."""
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker():
        try:
            for item in gen:
                loop.call_soon_threadsafe(queue.put_nowait, (False, item))
            loop.call_soon_threadsafe(queue.put_nowait, (True, None))
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, (True, e))

    threading.Thread(target=worker, daemon=True).start()

    while True:
        is_done, item = await queue.get()
        if is_done:
            if isinstance(item, Exception):
                raise item
            break
        yield item


class StreamThoughtFilter:
    """
    Filters streamed text chunks to extract <think>...</think> tags and route
    thoughts to on_thinking while sending clean text to on_token.
    """
    def __init__(self, on_token: Callable[[str], None], on_thinking: Callable[[str], None]):
        self.on_token = on_token
        self.on_thinking = on_thinking
        self.in_think = False
        self.buffer = ""

    def process_chunk(self, chunk: str) -> tuple[str, str]:
        self.buffer += chunk
        emitted_text = []
        emitted_thinking = []

        while self.buffer:
            if not self.in_think:
                idx = self.buffer.find("<think>")
                if idx != -1:
                    pre = self.buffer[:idx]
                    if pre:
                        self.on_token(pre)
                        emitted_text.append(pre)
                    self.buffer = self.buffer[idx + 7:]
                    self.in_think = True
                    continue
                # Check for partial start of '<think>' at end of buffer
                partial = False
                for i in range(1, 7):
                    if "<think>"[:i] == self.buffer[-i:]:
                        partial = True
                        safe_len = len(self.buffer) - i
                        if safe_len > 0:
                            self.on_token(self.buffer[:safe_len])
                            emitted_text.append(self.buffer[:safe_len])
                            self.buffer = self.buffer[safe_len:]
                        break
                if partial:
                    break
                else:
                    self.on_token(self.buffer)
                    emitted_text.append(self.buffer)
                    self.buffer = ""
                    break
            else:
                idx = self.buffer.find("</think>")
                if idx != -1:
                    thought_part = self.buffer[:idx]
                    if thought_part:
                        self.on_thinking(thought_part)
                        emitted_thinking.append(thought_part)
                    self.buffer = self.buffer[idx + 8:]
                    self.in_think = False
                    continue
                # Check for partial start of '</think>' at end of buffer
                partial = False
                for i in range(1, 8):
                    if "</think>"[:i] == self.buffer[-i:]:
                        partial = True
                        safe_len = len(self.buffer) - i
                        if safe_len > 0:
                            self.on_thinking(self.buffer[:safe_len])
                            emitted_thinking.append(self.buffer[:safe_len])
                            self.buffer = self.buffer[safe_len:]
                        break
                if partial:
                    break
                else:
                    self.on_thinking(self.buffer)
                    emitted_thinking.append(self.buffer)
                    self.buffer = ""
                    break

        return "".join(emitted_text), "".join(emitted_thinking)

    def flush(self) -> tuple[str, str]:
        emitted_text = ""
        emitted_thinking = ""
        if self.buffer:
            if self.in_think:
                self.on_thinking(self.buffer)
                emitted_thinking = self.buffer
            else:
                self.on_token(self.buffer)
                emitted_text = self.buffer
            self.buffer = ""
        return emitted_text, emitted_thinking


class AgentCallbacks:
    """Callbacks to stream execution events back to the UI thread."""
    def __init__(
        self,
        on_token: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_tool_end: Optional[Callable[[str, str], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_thread_title_updated: Optional[Callable[[str, str], None]] = None
    ):
        self.on_token = on_token or (lambda text: None)
        self.on_thinking = on_thinking or (lambda text: None)
        self.on_tool_start = on_tool_start or (lambda name, args: None)
        self.on_tool_end = on_tool_end or (lambda name, res: None)
        self.on_complete = on_complete or (lambda text: None)
        self.on_error = on_error or (lambda err: None)
        self.on_thread_title_updated = on_thread_title_updated or (lambda tid, title: None)


class QueuedTurn:
    """Represents a queued turn waiting to be processed by a session."""
    def __init__(
        self,
        user_message: str,
        callbacks: AgentCallbacks,
        is_call_mode: bool = False,
        thread_id: Optional[str] = None,
        on_dequeued: Optional[Callable[[], None]] = None,
        attachments: Optional[List[str]] = None
    ):
        self.user_message = user_message
        self.callbacks = callbacks
        self.is_call_mode = is_call_mode
        self.thread_id = thread_id
        self.on_dequeued = on_dequeued
        self.attachments: List[str] = list(attachments) if attachments else []
        self.future: Optional[asyncio.Future] = None


class SessionState:
    """Represents the execution and conversational state of an isolated session."""
    def __init__(self, thread_id: str, history: Optional[List[Dict[str, Any]]] = None):
        self.thread_id: str = thread_id
        self.history: List[Dict[str, Any]] = list(history) if history is not None else []
        self.is_running: bool = False
        self.abort_requested: bool = False
        self.current_task: Optional[asyncio.Task] = None
        self.queue: List[QueuedTurn] = []
        self.speech_finish_event: Optional[asyncio.Event] = None
        self.title_task: Optional[asyncio.Task] = None

    def clear_queue(self):
        """Cancels and clears all pending turns in the queue."""
        for item in self.queue:
            if item.callbacks and item.callbacks.on_error:
                try:
                    item.callbacks.on_error("Operación o mensaje en cola cancelado.")
                except Exception:
                    pass
            if item.future and not item.future.done():
                item.future.cancel()
        self.queue.clear()


class AgentCore:
    def __init__(
        self,
        config_manager: ConfigManager,
        mcp_manager: MCPManager,
        memory_manager: MemoryManager,
        thread_manager: Optional[ThreadManager] = None
    ):
        self.config_manager = config_manager
        self.mcp_manager = mcp_manager
        self.memory_manager = memory_manager
        self.thread_manager = thread_manager
        self.sessions: Dict[str, SessionState] = {}
        self.active_thread_id: Optional[str] = None
        self._default_history: List[Dict[str, Any]] = []
        self._title_listeners: List[Callable[[str, str], None]] = []

    def add_title_listener(self, fn: Callable[[str, str], None]):
        """Registers a global listener for thread title changes: fn(thread_id, new_title)."""
        if fn not in self._title_listeners:
            self._title_listeners.append(fn)

    def remove_title_listener(self, fn: Callable[[str, str], None]):
        """Removes a registered thread title change listener."""
        if fn in self._title_listeners:
            self._title_listeners.remove(fn)

    def _notify_title_updated(
        self,
        thread_id: str,
        title: str,
        callbacks: Optional[AgentCallbacks] = None
    ):
        """Notifies callbacks and registered global listeners of a title change."""
        if callbacks and callbacks.on_thread_title_updated:
            try:
                callbacks.on_thread_title_updated(thread_id, title)
            except Exception as e:
                logger.error(f"Error calling on_thread_title_updated callback: {e}")

        for fn in list(self._title_listeners):
            try:
                fn(thread_id, title)
            except Exception as e:
                logger.error(f"Error in title listener: {e}")

    @property
    def history(self) -> List[Dict[str, Any]]:
        """Backwards-compatible access to the active thread's history."""
        if self.active_thread_id and self.active_thread_id in self.sessions:
            return self.sessions[self.active_thread_id].history
        return self._default_history

    @history.setter
    def history(self, val: List[Dict[str, Any]]):
        if self.active_thread_id:
            sess = self.get_or_create_session(self.active_thread_id)
            sess.history = list(val)
        else:
            self._default_history = list(val)

    @property
    def is_running(self) -> bool:
        """Checks if the currently active session is running."""
        if self.active_thread_id:
            return self.is_thread_running(self.active_thread_id)
        return any(s.is_running for s in self.sessions.values())

    def is_thread_running(self, thread_id: str) -> bool:
        """Checks if a specific session/thread is currently executing."""
        sess = self.sessions.get(thread_id)
        return bool(sess and sess.is_running)

    def get_running_thread_ids(self) -> List[str]:
        """Returns the list of thread IDs that are currently running concurrently."""
        return [tid for tid, s in self.sessions.items() if s.is_running]

    def is_any_running(self) -> bool:
        """Returns True if at least one session is currently running."""
        return any(s.is_running for s in self.sessions.values())

    def get_session(self, thread_id: Optional[str]) -> Optional[SessionState]:
        if not thread_id:
            return None
        return self.sessions.get(thread_id)

    def get_or_create_session(
        self,
        thread_id: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> SessionState:
        """Retrieves or instantiates session state for a given thread ID."""
        if thread_id not in self.sessions:
            initial_history = history
            if initial_history is None and self.thread_manager:
                th = self.thread_manager.get_thread(thread_id)
                if th:
                    initial_history = list(th.get("messages", []))
            self.sessions[thread_id] = SessionState(thread_id, initial_history or [])
        return self.sessions[thread_id]

    def delete_session(self, thread_id: str):
        """Cleans up in-memory session state when a thread is deleted."""
        sess = self.sessions.get(thread_id)
        if sess:
            if sess.is_running:
                sess.abort_requested = True
            sess.clear_queue()
            del self.sessions[thread_id]
        if self.active_thread_id == thread_id:
            self.active_thread_id = None

    def load_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        """Loads a conversation thread by ID into memory as the active thread."""
        if not self.thread_manager:
            return []

        # Save previous thread messages before switching if it is not running
        if self.active_thread_id and self.active_thread_id != thread_id:
            prev_sess = self.sessions.get(self.active_thread_id)
            if prev_sess and not prev_sess.is_running and prev_sess.history:
                self.thread_manager.save_thread(self.active_thread_id, prev_sess.history)

        session = self.get_or_create_session(thread_id)
        if not session.history:
            th = self.thread_manager.get_thread(thread_id)
            if th:
                session.history = list(th.get("messages", []))

        self.active_thread_id = thread_id
        self.thread_manager.active_thread_id = thread_id
        return session.history

    def new_thread(
        self,
        title: str = "Nueva conversación",
        reuse_empty: bool = False,
        agent_id: Optional[str] = None,
        agent_profile: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
        workspace_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Starts a fresh new conversation thread without interrupting running background threads.
        Can bind to a workspace (project folders + contextual memory) and/or an agent (prompt-only)."""
        if self.thread_manager:
            # If requested and current active thread is already empty and not running, reuse it
            if reuse_empty and self.active_thread_id and not agent_profile and not agent_id and not workspace_profile and not workspace_id:
                cur_sess = self.sessions.get(self.active_thread_id)
                if cur_sess and not cur_sess.is_running and not cur_sess.history:
                    cur_th = self.thread_manager.get_thread(self.active_thread_id)
                    if cur_th and len(cur_th.get("messages", [])) == 0 and not cur_th.get("workspace_profile") and not cur_th.get("agent_profile"):
                        return cur_th

            # Save current thread history before switching if not running
            if self.active_thread_id:
                cur_sess = self.sessions.get(self.active_thread_id)
                if cur_sess and not cur_sess.is_running and cur_sess.history:
                    self.thread_manager.save_thread(self.active_thread_id, cur_sess.history)

            thread_data = self.thread_manager.create_thread(
                title=title,
                agent_id=agent_id,
                agent_profile=agent_profile,
                workspace_id=workspace_id,
                workspace_profile=workspace_profile
            )
            tid = thread_data["id"]
            self.sessions[tid] = SessionState(tid, [])
            self.active_thread_id = tid
            return thread_data

        tid = "default"
        self.sessions[tid] = SessionState(tid, [])
        self.active_thread_id = tid
        data: Dict[str, Any] = {"id": tid, "title": title, "messages": []}
        if agent_id:
            data["agent_id"] = agent_id
        if agent_profile:
            data["agent_profile"] = agent_profile
        if workspace_id:
            data["workspace_id"] = workspace_id
        if workspace_profile:
            data["workspace_profile"] = workspace_profile
        return data

    def clear_history(self, thread_id: Optional[str] = None):
        """Explicitly clears messages in the specified thread (or active thread)."""
        tid = thread_id or self.active_thread_id
        if tid:
            sess = self.sessions.get(tid)
            if sess:
                sess.history.clear()
            if self.thread_manager:
                self.thread_manager.save_thread(tid, [])

    def get_queue(self, thread_id: Optional[str] = None) -> List[QueuedTurn]:
        """Returns pending queued turns for a specific thread."""
        sess = self.get_session(thread_id or self.active_thread_id)
        return list(sess.queue) if sess else []

    def get_queue_length(self, thread_id: Optional[str] = None) -> int:
        """Returns count of queued turns waiting in line for a specific thread."""
        sess = self.get_session(thread_id or self.active_thread_id)
        return len(sess.queue) if sess else 0

    def has_queued_turns(self, thread_id: Optional[str] = None) -> bool:
        """Checks if a session has any turns waiting in its queue."""
        return self.get_queue_length(thread_id) > 0

    def register_speech_in_progress(self, thread_id: Optional[str] = None) -> asyncio.Event:
        """Signals that speech playback (TTS) has started and queued turns should wait for it."""
        target_id = thread_id or self.active_thread_id or "default"
        sess = self.get_or_create_session(target_id)
        ev = asyncio.Event()
        sess.speech_finish_event = ev
        return ev

    def notify_speech_finished(self, thread_id: Optional[str] = None):
        """Signals that speech playback (TTS) has finished, releasing queued turns to proceed."""
        target_id = thread_id or self.active_thread_id
        if target_id and target_id in self.sessions:
            sess = self.sessions[target_id]
            if sess.speech_finish_event:
                sess.speech_finish_event.set()

    def abort(self, thread_id: Optional[str] = None):
        """Requests cancellation for a specific thread, or active thread if omitted."""
        target_id = thread_id or self.active_thread_id
        if target_id and target_id in self.sessions:
            sess = self.sessions[target_id]
            sess.abort_requested = True
            sess.clear_queue()
            if sess.speech_finish_event:
                sess.speech_finish_event.set()
            if sess.current_task and not sess.current_task.done():
                sess.current_task.cancel()
            sess.is_running = False
            logger.info(f"Abort requested and task cancelled for session {target_id}")
        elif not target_id:
            for s in self.sessions.values():
                s.abort_requested = True
                s.clear_queue()
                if s.speech_finish_event:
                    s.speech_finish_event.set()
                if s.current_task and not s.current_task.done():
                    s.current_task.cancel()
                s.is_running = False

    def _read_workspace_context_memory(self, workspace_profile: Dict[str, Any], max_chars: int = 6000) -> str:
        """Reads the contextual memory file associated with a workspace/project."""
        if not workspace_profile:
            return ""
        mem_path = (workspace_profile.get("memory_file") or "").strip()
        if not mem_path:
            return ""
        try:
            p = Path(mem_path).expanduser()
            if not p.is_absolute():
                # resolve relative to first workspace folder if available
                folders = workspace_profile.get("folders") or []
                if not folders and workspace_profile.get("workspace_path"):
                    folders = [workspace_profile.get("workspace_path")]
                if folders:
                    p = Path(folders[0]).expanduser() / p
            p = p.resolve()
            if p.exists() and p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                if len(text) > max_chars:
                    text = text[:max_chars].rstrip() + "\n\n[...memoria contextual del workspace truncada...]"
                return text
        except Exception as e:
            logger.debug(f"Could not read workspace memory file: {e}")
        return ""

    def _build_system_prompt(
        self,
        query: Optional[str] = None,
        is_call_mode: bool = False,
        thread_id: Optional[str] = None
    ) -> str:
        target_tid = thread_id or self.active_thread_id
        agent_profile: Optional[Dict[str, Any]] = None
        workspace_profile: Optional[Dict[str, Any]] = None
        if self.thread_manager and target_tid:
            agent_profile = self.thread_manager.get_agent_profile(target_tid)
            workspace_profile = self.thread_manager.get_workspace_profile(target_tid)

        # Agent: specialized prompt reusable across workspaces
        base_prompt = BASE_SYSTEM_PROMPT
        if agent_profile and isinstance(agent_profile.get("system_prompt"), str):
            custom_prompt = agent_profile.get("system_prompt", "").strip()
            if custom_prompt:
                base_prompt = custom_prompt

        parts = [base_prompt]
        if is_call_mode:
            parts.append(
                "# MODO LLAMADA DE VOZ ACTIVO (VOICE CALL MODE)\n"
                "Estás interactuando por voz en tiempo real con el usuario mediante una llamada telefónica/conversación fluida.\n"
                "1. PRIORIDAD TOTAL A LA VOZ: Sé conversacional, natural, empático y conciso. Responde en 1 a 3 frases claras.\n"
                "2. DISCIPLINA ESTRICTA DE HERRAMIENTAS: NO invoques herramientas de consola, bash o procesos en segundo plano para conversación, dudas o preguntas. SÓLO ejecuta una herramienta si el usuario te pide expresamente una acción concreta sobre su sistema.\n"
                "3. PROHIBICIÓN DE FORMATO: NO utilices Markdown pesado (tablas, bloques de código, listas largas o títulos) porque tu respuesta será leída directamente por un sintetizador de voz (TTS).\n"
                "4. ¡REGLA CRÍTICA ANTE ERRORES!: Si una herramienta falla o no está disponible, NUNCA intentes ejecutar comandos de consola para diagnosticarla. Detén inmediatamente la secuencia de herramientas y dile brevemente al usuario lo ocurrido por voz."
            )

        # Workspace: project folders + contextual memory (independent chat threads per workspace/project)
        if workspace_profile:
            ws_name = (workspace_profile.get("name") or "").strip()
            ws_desc = (workspace_profile.get("description") or "").strip()
            folders = workspace_profile.get("folders") or []
            if not folders and workspace_profile.get("workspace_path"):
                folders = [workspace_profile.get("workspace_path")]
            folders = [str(f).strip() for f in folders if f]
            mem_file = (workspace_profile.get("memory_file") or "").strip()

            ws_lines = ["<workspace_context>"]
            if ws_name:
                ws_lines.append(f"- Workspace / proyecto: {ws_name}")
            if ws_desc:
                ws_lines.append(f"- Descripción: {ws_desc}")
            if folders:
                if len(folders) == 1:
                    ws_lines.append(f"- Carpeta asociada: {folders[0]}")
                else:
                    ws_lines.append("- Carpetas asociadas:")
                    for f in folders:
                        ws_lines.append(f"  • {f}")
                ws_lines.append(
                    "Directivas de workspace: esta conversación pertenece a este workspace/proyecto. "
                    "Prioriza leer, crear y modificar archivos dentro de las carpetas asociadas cuando sea pertinente, "
                    "manteniendo el contexto del proyecto."
                )
            if mem_file:
                ws_lines.append(f"- Archivo de memoria contextual del workspace: {mem_file}")
                mem_content = self._read_workspace_context_memory(workspace_profile)
                if mem_content:
                    ws_lines.append("")
                    ws_lines.append("Memoria contextual del workspace (información persistente del proyecto):")
                    ws_lines.append(mem_content)
                else:
                    ws_lines.append("(El archivo de memoria contextual existe/configurado pero está vacío o no disponible aún.)")
            if len(ws_lines) > 1:
                ws_lines.append("</workspace_context>")
                parts.append("\n".join(ws_lines))

        # Agent prompt specialization (reusable across all workspaces)
        if agent_profile:
            profile_lines = ["<specialized_agent>"]
            name = (agent_profile.get("name") or "").strip()
            desc = (agent_profile.get("description") or "").strip()
            if name:
                profile_lines.append(f"- Nombre del agente: {name}")
            if desc:
                profile_lines.append(f"- Descripción/rol: {desc}")
            profile_lines.append(
                "- Nota: este agente especializado (prompt) puede utilizarse en cualquier workspace; "
                "adapta su comportamiento al contexto del workspace activo si existe."
            )
            if len(profile_lines) > 1:
                profile_lines.append("</specialized_agent>")
                parts.append("\n".join(profile_lines))

        memory_prompt = self.memory_manager.get_memory_context_prompt(current_query=query)
        if memory_prompt:
            parts.append(memory_prompt)

        # Thread working memory / scratchpad (isolated per thread)
        if self.thread_manager and target_tid:
            scratchpad_prompt = self.thread_manager.format_scratchpad_prompt(target_tid)
            if scratchpad_prompt:
                parts.append(scratchpad_prompt)

        skills_prompt = load_agent_skills(self.config_manager, query=query)
        if skills_prompt:
            parts.append(skills_prompt)
        return "\n\n".join(parts)

    def _prepare_multimodal_user_turn(
        self,
        user_message: str,
        attachments: Optional[List[str]],
        model: str,
        provider: str
    ) -> tuple[Any, List[Dict[str, Any]]]:
        """
        Processes explicit user attachments and auto-detected file paths in user_message.
        - If an image is attached/referenced and the active model is multimodal, converts it
          into an OpenAI/LiteLLM/Antigravity-compatible `image_url` data URI content part.
        - If an audio file is attached/referenced and the active model supports multimodal audio,
          converts it into an `input_audio` content part so the model listens to it natively.
        - If a document is attached/referenced, extracts its structured text and metadata
          automatically so the model can read it immediately.
        Returns `(content_payload, attachment_metadata_list)`.
        """
        multimodal = is_model_multimodal(model, provider)
        audio_multimodal = is_model_audio_multimodal(model, provider)
        combined_paths: List[str] = []
        seen_paths = set()

        for p in (attachments or []):
            resolved = str(resolve_local_path(p))
            if resolved and resolved not in seen_paths and os.path.isfile(resolved):
                seen_paths.add(resolved)
                combined_paths.append(resolved)

        # Also auto-detect explicit local image/audio/document paths written in the user prompt
        for p in extract_file_paths_from_text(user_message):
            resolved = str(resolve_local_path(p))
            if resolved and resolved not in seen_paths and os.path.isfile(resolved):
                seen_paths.add(resolved)
                combined_paths.append(resolved)

        if not combined_paths:
            return user_message, []

        attachment_metas: List[Dict[str, Any]] = []
        enriched_text_blocks: List[str] = []
        media_parts: List[Dict[str, Any]] = []

        if user_message and user_message.strip():
            enriched_text_blocks.append(user_message)

        for path in combined_paths:
            if is_image_file(path):
                # Always process image with Base64 payload so multimodal models can inspect it directly.
                # If the active model is not multimodal, the API provider will raise an error accordingly.
                img_info = process_image(
                    path,
                    include_base64=True,
                    extract_ocr=False
                )
                meta = {
                    "path": path,
                    "name": img_info.get("name", os.path.basename(path)),
                    "type": "image",
                    "mime_type": img_info.get("mime_type", "image/png"),
                    "width": img_info.get("width", 0),
                    "height": img_info.get("height", 0),
                    "size_bytes": img_info.get("size_bytes", 0),
                    "size_human": img_info.get("size_human", ""),
                }
                attachment_metas.append(meta)

                dims = f"{meta['width']}x{meta['height']}px" if meta["width"] and meta["height"] else "dimensiones desconocidas"
                if img_info.get("data_url"):
                    media_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img_info["data_url"]}
                    })
                    enriched_text_blocks.append(
                        f"\n<attached_image name=\"{meta['name']}\" path=\"{path}\" "
                        f"dimensions=\"{dims}\" size=\"{meta['size_human']}\" mode=\"multimodal_vision\" />"
                    )
                else:
                    err_msg = img_info.get("error", "No se pudo leer la imagen")
                    enriched_text_blocks.append(
                        f"\n<attached_image name=\"{meta['name']}\" path=\"{path}\" error=\"{err_msg}\" />"
                    )
            elif is_audio_file(path):
                aud_info = process_audio(
                    path,
                    include_base64=audio_multimodal,
                    target_provider=provider,
                    target_model=model
                )
                meta = {
                    "path": path,
                    "name": aud_info.get("name", os.path.basename(path)),
                    "type": "audio",
                    "format": aud_info.get("format", "AUDIO"),
                    "audio_format": aud_info.get("audio_format", "wav"),
                    "mime_type": aud_info.get("mime_type", "audio/wav"),
                    "duration_seconds": aud_info.get("duration_seconds", 0.0),
                    "duration_human": aud_info.get("duration_human", ""),
                    "sample_rate": aud_info.get("sample_rate", 0),
                    "channels": aud_info.get("channels", 0),
                    "size_bytes": aud_info.get("size_bytes", 0),
                    "size_human": aud_info.get("size_human", ""),
                }
                attachment_metas.append(meta)

                dur_attr = f" duration=\"{meta['duration_human']}\"" if meta.get("duration_human") else ""
                sr_attr = f" sample_rate=\"{meta['sample_rate']}Hz\"" if meta.get("sample_rate") else ""
                if audio_multimodal and aud_info.get("input_audio"):
                    media_parts.append({
                        "type": "input_audio",
                        "input_audio": {
                            "data": aud_info["input_audio"]["data"],
                            "format": aud_info["input_audio"]["format"],
                        }
                    })
                    enriched_text_blocks.append(
                        f"\n<attached_audio name=\"{meta['name']}\" path=\"{path}\" "
                        f"format=\"{meta['format']}\"{dur_attr}{sr_attr} size=\"{meta['size_human']}\" mode=\"multimodal_audio\" />"
                    )
                else:
                    enriched_text_blocks.append(
                        f"\n<attached_audio name=\"{meta['name']}\" path=\"{path}\" "
                        f"format=\"{meta['format']}\"{dur_attr}{sr_attr} size=\"{meta['size_human']}\" mode=\"unsupported_audio_model\">\n"
                        f"Nota: El modelo actual ({model}) no soporta procesamiento multimodal directo de audio.\n"
                        f"</attached_audio>"
                    )
            else:
                doc_info = process_document(path)
                meta = {
                    "path": path,
                    "name": doc_info.get("name", os.path.basename(path)),
                    "type": "document",
                    "format": doc_info.get("format", "Text"),
                    "mime_type": doc_info.get("mime_type", "text/plain"),
                    "size_bytes": doc_info.get("size_bytes", 0),
                    "size_human": doc_info.get("size_human", ""),
                    "total_pages": doc_info.get("total_pages"),
                }
                attachment_metas.append(meta)

                doc_content = doc_info.get("content", "")
                trunc_note = " (truncado; usa media__read_document para leer más páginas/secciones)" if doc_info.get("truncated") else ""
                pages_attr = f" pages=\"{meta['total_pages']}\"" if meta.get("total_pages") else ""
                enriched_text_blocks.append(
                    f"\n<attached_document name=\"{meta['name']}\" path=\"{path}\" "
                    f"format=\"{meta['format']}\" size=\"{meta['size_human']}\"{pages_attr}{trunc_note}>\n"
                    f"{doc_content}\n"
                    f"</attached_document>"
                )

        combined_text = "\n".join(enriched_text_blocks).strip()
        if media_parts:
            content_payload: List[Dict[str, Any]] = [{"type": "text", "text": combined_text}]
            content_payload.extend(media_parts)
            return content_payload, attachment_metas

        return combined_text, attachment_metas

    async def run_turn(
        self,
        user_message: str,
        callbacks: AgentCallbacks,
        is_call_mode: bool = False,
        thread_id: Optional[str] = None,
        on_dequeued: Optional[Callable[[], None]] = None,
        attachments: Optional[List[str]] = None
    ):
        target_thread_id = thread_id or self.active_thread_id
        if not target_thread_id:
            if self.thread_manager:
                active_th = self.thread_manager.get_or_create_active_thread()
                target_thread_id = active_th["id"]
            else:
                target_thread_id = "default"
            if not self.active_thread_id:
                self.active_thread_id = target_thread_id

        session = self.get_or_create_session(target_thread_id)

        if session.is_running:
            if is_call_mode:
                logger.info(f"Session {target_thread_id} busy, interrupting prior turn immediately for voice call.")
                if session.current_task and not session.current_task.done():
                    session.current_task.cancel()
                session.abort_requested = True
                session.clear_queue()
                session.is_running = False
                await asyncio.sleep(0.05)
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                queued = QueuedTurn(
                    user_message=user_message,
                    callbacks=callbacks,
                    is_call_mode=is_call_mode,
                    thread_id=target_thread_id,
                    on_dequeued=on_dequeued,
                    attachments=attachments
                )
                queued.future = fut
                session.queue.append(queued)
                logger.info(f"Session {target_thread_id} already executing. Enqueued message (depth: {len(session.queue)}).")
                try:
                    await fut
                except asyncio.CancelledError:
                    logger.info(f"Queued message in session {target_thread_id} was cancelled.")
                return

        session.is_running = True
        session.abort_requested = False
        try:
            session.current_task = asyncio.current_task()
        except Exception:
            session.current_task = None

        # Apply environment keys for LiteLLM
        self.config_manager.apply_env_keys()

        try:
            # 1. Execute initial turn
            await self._execute_single_turn(
                session=session,
                user_message=user_message,
                callbacks=callbacks,
                is_call_mode=is_call_mode,
                target_thread_id=target_thread_id,
                attachments=attachments
            )

            # 2. Process queued turns FIFO
            while session.queue and not session.abort_requested:
                # If assistant is currently speaking via TTS, wait for speech to finish before dequeuing next message
                if session.speech_finish_event and not session.speech_finish_event.is_set():
                    try:
                        logger.info(f"Session {target_thread_id} waiting for speech finish event before dequeuing next message...")
                        await asyncio.wait_for(session.speech_finish_event.wait(), timeout=60.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    finally:
                        session.speech_finish_event = None

                if session.abort_requested or not session.queue:
                    break

                next_turn = session.queue.pop(0)
                logger.info(f"Dequeuing next turn for session {target_thread_id} ({len(session.queue)} remaining in queue)")

                if next_turn.on_dequeued:
                    try:
                        next_turn.on_dequeued()
                    except Exception as e:
                        logger.error(f"Error in on_dequeued callback: {e}")

                try:
                    await self._execute_single_turn(
                        session=session,
                        user_message=next_turn.user_message,
                        callbacks=next_turn.callbacks,
                        is_call_mode=next_turn.is_call_mode,
                        target_thread_id=target_thread_id,
                        attachments=next_turn.attachments
                    )
                    if next_turn.future and not next_turn.future.done():
                        next_turn.future.set_result(True)
                except Exception as e:
                    logger.error(f"Error in dequeued turn: {e}")
                    if next_turn.future and not next_turn.future.done():
                        next_turn.future.set_exception(e)
        except asyncio.CancelledError:
            callbacks.on_error("Operación cancelada.")
            raise
        finally:
            session.is_running = False
            session.abort_requested = False
            session.current_task = None
            session.speech_finish_event = None
            session.clear_queue()
            if self.thread_manager:
                self.thread_manager.save_thread(target_thread_id, session.history)

    async def _execute_single_turn(
        self,
        session: SessionState,
        user_message: str,
        callbacks: AgentCallbacks,
        is_call_mode: bool,
        target_thread_id: str,
        attachments: Optional[List[str]] = None
    ):

        # Detect explicit user corrections / rules and persist to LESSONS.md
        learned_rule = self.memory_manager.detect_and_store_lesson(user_message)
        if learned_rule:
            logger.info(f"Learned and saved new rule into LESSONS.md: {learned_rule}")

        # Track objective in working memory scratchpad
        if self.thread_manager:
            pad = self.thread_manager.get_scratchpad(target_thread_id)
            if not pad.get("objective"):
                obj_txt = user_message[:120].strip() if user_message else "Análisis de archivos adjuntos"
                self.thread_manager.update_scratchpad(target_thread_id, objective=obj_txt)

        model = self.config_manager.get_active_model()
        provider = self.config_manager.get_active_provider()
        multimodal = is_model_multimodal(model, provider)
        audio_multimodal = is_model_audio_multimodal(model, provider)

        # Process attachments & auto-detected paths (documents, multimodal images & multimodal audio)
        content_payload, attachment_metas = self._prepare_multimodal_user_turn(
            user_message=user_message,
            attachments=attachments,
            model=model,
            provider=provider
        )

        # Add user message to session history
        user_msg_entry: Dict[str, Any] = {
            "role": "user",
            "content": content_payload,
            "display_text": user_message
        }
        if attachment_metas:
            user_msg_entry["attachments"] = attachment_metas
        session.history.append(user_msg_entry)

        if self.thread_manager:
            self.thread_manager.save_thread(target_thread_id, session.history)

        max_turns = 2 if is_call_mode else 25
        turn_count = 0
        final_text = ""
        seen_calls = set()
        executed_tool_turns = 0
        turn_tools_used: List[str] = []

        try:
            while turn_count < max_turns and not session.abort_requested:
                turn_count += 1
                system_prompt = self._build_system_prompt(
                    query=user_message,
                    is_call_mode=is_call_mode,
                    thread_id=target_thread_id
                )

                # Clean history for standard API payload (and rehydrate stripped image_url / input_audio parts if needed)
                clean_history = []
                for msg in session.history:
                    clean_msg = {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
                    if (
                        clean_msg.get("role") == "user"
                        and isinstance(clean_msg.get("content"), list)
                    ):
                        # Rehydrate any stripped base64 image/audio parts from disk if they were saved without payload.
                        # Always preserve images so multimodal models receive them directly.
                        rehydrated_parts = []
                        has_valid_image = False
                        has_valid_audio = False
                        for part in clean_msg["content"]:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                url_val = part.get("image_url", {}).get("url", "")
                                if url_val.startswith("data:"):
                                    has_valid_image = True
                                    rehydrated_parts.append(part)
                            elif isinstance(part, dict) and part.get("type") == "input_audio":
                                if audio_multimodal:
                                    audio_data = part.get("input_audio", {}).get("data", "")
                                    if audio_data and audio_data != "[persisted_attachment]":
                                        has_valid_audio = True
                                        rehydrated_parts.append(part)
                            else:
                                rehydrated_parts.append(part)

                        if msg.get("attachments"):
                            if not has_valid_image:
                                for att in msg.get("attachments", []):
                                    if att.get("type") == "image" and att.get("path") and os.path.isfile(att["path"]):
                                        img_res = process_image(att["path"], include_base64=True)
                                        if img_res.get("data_url"):
                                            rehydrated_parts.append({
                                                "type": "image_url",
                                                "image_url": {"url": img_res["data_url"]}
                                            })
                            if audio_multimodal and not has_valid_audio:
                                for att in msg.get("attachments", []):
                                    if att.get("type") == "audio" and att.get("path") and os.path.isfile(att["path"]):
                                        aud_res = process_audio(
                                            att["path"],
                                            include_base64=True,
                                            target_provider=provider,
                                            target_model=model
                                        )
                                        if aud_res.get("input_audio"):
                                            rehydrated_parts.append({
                                                "type": "input_audio",
                                                "input_audio": {
                                                    "data": aud_res["input_audio"]["data"],
                                                    "format": aud_res["input_audio"]["format"]
                                                }
                                            })
                        clean_msg["content"] = rehydrated_parts
                    clean_history.append(clean_msg)

                # In voice call mode, keep payload lightweight to ensure sub-second latency
                if is_call_mode and len(clean_history) > 8:
                    cut_idx = len(clean_history) - 6
                    while cut_idx > 0 and clean_history[cut_idx].get("role") in ("tool",):
                        cut_idx -= 1
                    clean_history = clean_history[cut_idx:]

                messages = [{"role": "system", "content": system_prompt}] + clean_history

                # Obtain dynamic tools from active MCP servers
                raw_tools = self.mcp_manager.get_litellm_tools()
                tools = raw_tools if raw_tools else None

                # Determine provider dispatch
                call_type, kwargs = prepare_provider_call(model, self.config_manager)

                if call_type == "antigravity":
                    if not AntigravityClient.is_logged_in():
                        raise RuntimeError("Sesión de Google Antigravity no iniciada. Inicia sesión en Configuración ➔ API Keys.")
                    
                    dispatch_kwargs = dict(kwargs)
                    model_to_use = dispatch_kwargs.pop("model", model)
                    temp_to_use = dispatch_kwargs.pop("temperature", 0.7)
                    if is_call_mode:
                        dispatch_kwargs["thinking_level"] = "MINIMAL"
                        dispatch_kwargs["thinking_budget"] = 0
                    
                    logger.info(f"Antigravity dispatch: model={model_to_use}, tools={len(raw_tools)}, is_call_mode={is_call_mode}")
                    gen = AntigravityClient.completion(
                        model=model_to_use,
                        messages=messages,
                        tools=tools,
                        stream=True,
                        temperature=temp_to_use,
                        **dispatch_kwargs
                    )
                    response_stream = _stream_sync_generator(gen)
                else:
                    kwargs["messages"] = messages
                    if is_call_mode:
                        kwargs["max_tokens"] = min(kwargs.get("max_tokens", 4096), 350)
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"
                    else:
                        kwargs.pop("tools", None)
                        kwargs.pop("tool_choice", None)
                    
                    logger.info(f"LiteLLM dispatch: model={kwargs.get('model')}, api_base={kwargs.get('api_base')}, custom_provider={kwargs.get('custom_llm_provider')}, tools={len(tools) if tools else 0}")
                    response_stream = await litellm.acompletion(**kwargs)

                current_text = ""
                current_thinking = ""
                thought_filter = StreamThoughtFilter(callbacks.on_token, callbacks.on_thinking)
                tool_calls_dict: Dict[int, Dict[str, Any]] = {}

                async for chunk in response_stream:
                    if session.abort_requested:
                        break

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Stream native reasoning / thinking tokens (Antigravity, Gemini, DeepSeek, LiteLLM)
                    reasoning_chunk = getattr(delta, "reasoning_content", None) or getattr(delta, "thinking", None)
                    if not reasoning_chunk and isinstance(delta, dict):
                        reasoning_chunk = delta.get("reasoning_content") or delta.get("thinking")

                    if reasoning_chunk:
                        current_thinking += reasoning_chunk
                        callbacks.on_thinking(reasoning_chunk)

                    # Stream text content (filtering any embedded <think>...</think> tags)
                    if delta.content:
                        c_add, t_add = thought_filter.process_chunk(delta.content)
                        current_text += c_add
                        current_thinking += t_add

                    # Accumulate streamed tool calls
                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index if tc.index is not None else 0
                            if idx not in tool_calls_dict:
                                tool_calls_dict[idx] = {
                                    "id": tc.id or f"call_{idx}",
                                    "name": tc.function.name or "" if tc.function else "",
                                    "arguments": tc.function.arguments or "" if tc.function else ""
                                }
                            else:
                                if tc.id:
                                    tool_calls_dict[idx]["id"] = tc.id
                                if tc.function and tc.function.name:
                                    tool_calls_dict[idx]["name"] += tc.function.name
                                if tc.function and tc.function.arguments:
                                    tool_calls_dict[idx]["arguments"] += tc.function.arguments

                # Flush any remaining buffer in thought filter
                c_flush, t_flush = thought_filter.flush()
                current_text += c_flush
                current_thinking += t_flush

                if session.abort_requested:
                    callbacks.on_error("Operación cancelada por el usuario.")
                    break

                # Check if model made tool calls
                if tool_calls_dict:
                    # Construct assistant message with tool_calls
                    tool_calls_list = []
                    for idx, tc_data in sorted(tool_calls_dict.items()):
                        tool_calls_list.append({
                            "id": tc_data["id"],
                            "type": "function",
                            "function": {
                                "name": tc_data["name"],
                                "arguments": tc_data["arguments"]
                            }
                        })

                    asst_msg = {
                        "role": "assistant",
                        "content": current_text or None,
                        "tool_calls": tool_calls_list
                    }
                    if current_thinking:
                        asst_msg["reasoning_content"] = current_thinking
                    session.history.append(asst_msg)

                    # Execute each tool call via MCP Manager
                    pending_visual_injections: List[Dict[str, Any]] = []
                    pending_audio_injections: List[Dict[str, Any]] = []
                    for tc in tool_calls_list:
                        call_id = tc["id"]
                        tool_name = tc["function"]["name"]
                        raw_args = tc["function"]["arguments"]

                        try:
                            parsed_args = json.loads(raw_args) if raw_args else {}
                        except Exception:
                            parsed_args = {"raw_input": raw_args}

                        # Check for repeated call signature (loop detection)
                        call_sig = f"{tool_name}:{raw_args}"
                        if call_sig in seen_calls:
                            logger.warning(f"Detected duplicate tool call loop for {tool_name}")
                            tool_result_str = (
                                f"Aviso: La herramienta '{tool_name}' ya fue ejecutada con los mismos parámetros. "
                                "Por favor, utiliza la información ya obtenida o avanza al siguiente paso."
                            )
                        else:
                            seen_calls.add(call_sig)
                            if tool_name not in turn_tools_used:
                                turn_tools_used.append(tool_name)
                            callbacks.on_tool_start(tool_name, parsed_args)
                            tool_result_str = await self.mcp_manager.call_tool(tool_name, parsed_args)
                            callbacks.on_tool_end(tool_name, tool_result_str)

                            # If a model requested media__read_image, inject visual payload for next turn
                            if tool_name in (
                                "media__read_image",
                                "read_image",
                                "pequeno-core__media__read_image",
                            ):
                                req_path = parsed_args.get("path", "") if isinstance(parsed_args, dict) else ""
                                resolved_img = str(resolve_local_path(req_path)) if req_path else ""
                                if resolved_img and os.path.isfile(resolved_img) and is_image_file(resolved_img):
                                    max_dim = int(parsed_args.get("max_dimension", 1568)) if isinstance(parsed_args, dict) else 1568
                                    img_res = process_image(resolved_img, max_dimension=max_dim, include_base64=True)
                                    if img_res.get("data_url"):
                                        pending_visual_injections.append({
                                            "path": resolved_img,
                                            "name": img_res.get("name", os.path.basename(resolved_img)),
                                            "data_url": img_res["data_url"],
                                            "width": img_res.get("width", 0),
                                            "height": img_res.get("height", 0),
                                            "size_human": img_res.get("size_human", "")
                                        })

                            # If an audio-multimodal model requested media__read_audio, inject audio payload for next turn
                            if audio_multimodal and tool_name in (
                                "media__read_audio",
                                "read_audio",
                                "pequeno-core__media__read_audio",
                            ):
                                req_path = parsed_args.get("path", "") if isinstance(parsed_args, dict) else ""
                                resolved_aud = str(resolve_local_path(req_path)) if req_path else ""
                                if resolved_aud and os.path.isfile(resolved_aud) and is_audio_file(resolved_aud):
                                    aud_res = process_audio(
                                        resolved_aud,
                                        include_base64=True,
                                        target_provider=provider,
                                        target_model=model
                                    )
                                    if aud_res.get("input_audio"):
                                        pending_audio_injections.append({
                                            "path": resolved_aud,
                                            "name": aud_res.get("name", os.path.basename(resolved_aud)),
                                            "input_audio": aud_res["input_audio"],
                                            "format": aud_res.get("format", ""),
                                            "duration_human": aud_res.get("duration_human", ""),
                                            "size_human": aud_res.get("size_human", "")
                                        })

                        # Append tool response
                        session.history.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": tool_name,
                            "content": tool_result_str
                        })

                        # Update working memory scratchpad with tool progress
                        if self.thread_manager:
                            clean_res_preview = tool_result_str.strip().replace("\n", " ")[:140]
                            self.thread_manager.update_scratchpad(
                                target_thread_id,
                                current_step=f"Ejecutando {tool_name}",
                                add_step=f"Herramienta {tool_name} ejecutada",
                                add_note=f"{tool_name}: {clean_res_preview}"
                            )

                    if pending_visual_injections:
                        visual_parts: List[Dict[str, Any]] = [
                            {
                                "type": "text",
                                "text": (
                                    "Imagen(es) cargadas visualmente mediante `media__read_image`: "
                                    + ", ".join(f"{v['name']} ({v['path']})" for v in pending_visual_injections)
                                )
                            }
                        ]
                        visual_metas: List[Dict[str, Any]] = []
                        for v in pending_visual_injections:
                            visual_parts.append({
                                "type": "image_url",
                                "image_url": {"url": v["data_url"]}
                            })
                            visual_metas.append({
                                "path": v["path"],
                                "name": v["name"],
                                "type": "image",
                                "width": v["width"],
                                "height": v["height"],
                                "size_human": v["size_human"]
                            })
                        session.history.append({
                            "role": "user",
                            "content": visual_parts,
                            "attachments": visual_metas,
                            "_auto_injected_media": True
                        })

                    if pending_audio_injections:
                        audio_parts: List[Dict[str, Any]] = [
                            {
                                "type": "text",
                                "text": (
                                    "Audio(s) cargados multimodalmente mediante `media__read_audio` para escucha directa: "
                                    + ", ".join(f"{a['name']} ({a['path']})" for a in pending_audio_injections)
                                )
                            }
                        ]
                        audio_metas: List[Dict[str, Any]] = []
                        for a in pending_audio_injections:
                            audio_parts.append({
                                "type": "input_audio",
                                "input_audio": {
                                    "data": a["input_audio"]["data"],
                                    "format": a["input_audio"]["format"]
                                }
                            })
                            audio_metas.append({
                                "path": a["path"],
                                "name": a["name"],
                                "type": "audio",
                                "format": a["format"],
                                "duration_human": a["duration_human"],
                                "size_human": a["size_human"]
                            })
                        session.history.append({
                            "role": "user",
                            "content": audio_parts,
                            "attachments": audio_metas,
                            "_auto_injected_media": True
                        })

                    if self.thread_manager:
                        self.thread_manager.save_thread(target_thread_id, session.history)

                    executed_tool_turns += 1
                    # Continue loop so LLM can process results and execute next tools or synthesize answer
                    continue

                else:
                    # Final assistant response without tool calls
                    final_text = current_text
                    asst_msg = {
                        "role": "assistant",
                        "content": final_text
                    }
                    if current_thinking:
                        asst_msg["reasoning_content"] = current_thinking
                    session.history.append(asst_msg)
                    if self.thread_manager:
                        self.thread_manager.save_thread(target_thread_id, session.history)
                        self.thread_manager.update_scratchpad(
                            target_thread_id,
                            current_step=""
                        )
                    callbacks.on_complete(final_text)

                    # Log to daily & cognitive episodic memory
                    if self.config_manager.data.get("memory", {}).get("auto_log", True):
                        self.memory_manager.log_daily_interaction(
                            user_message,
                            final_text,
                            tools_used=turn_tools_used,
                            thread_id=target_thread_id
                        )

                    # Trigger auto title generation in background if thread not yet titled by LLM
                    if self.thread_manager and not is_call_mode:
                        th = self.thread_manager.get_thread(target_thread_id)
                        if th and not th.get("title_custom") and not th.get("title_auto_generated"):
                            session.title_task = asyncio.create_task(self._auto_generate_title_safe(target_thread_id, callbacks))
                    break

            if not final_text and not session.abort_requested:
                final_text = current_text if current_text else "Operación completada."
                callbacks.on_complete(final_text)
                if self.thread_manager and not is_call_mode:
                    th = self.thread_manager.get_thread(target_thread_id)
                    if th and not th.get("title_custom") and not th.get("title_auto_generated"):
                        session.title_task = asyncio.create_task(self._auto_generate_title_safe(target_thread_id, callbacks))

        except Exception as e:
            logger.error(f"Error during agent turn in session {target_thread_id}: {e}", exc_info=True)
            callbacks.on_error(f"Error en el modelo ({model}): {str(e)}")
        finally:
            if self.thread_manager:
                self.thread_manager.save_thread(target_thread_id, session.history)

    async def _auto_generate_title_safe(
        self,
        thread_id: str,
        callbacks: Optional[AgentCallbacks] = None
    ):
        """Helper to run automatic title generation safely in background without raising."""
        try:
            await self.generate_thread_title(thread_id, force=False, callbacks=callbacks)
        except Exception as e:
            logger.debug(f"Background title generation error for {thread_id}: {e}")

    async def generate_thread_title(
        self,
        thread_id: str,
        force: bool = False,
        callbacks: Optional[AgentCallbacks] = None
    ) -> Optional[str]:
        """
        Generates a concise, high-quality conversation title using the active LLM
        based on the initial user and assistant exchange.
        """
        if not self.thread_manager:
            return None

        th = self.thread_manager.get_thread(thread_id)
        if not th:
            return None

        # If user explicitly customized the title and force is not set, respect user's title
        if not force and th.get("title_custom"):
            return th.get("title")

        # If already auto-generated by LLM and force is not set, avoid duplicate generation
        if not force and th.get("title_auto_generated"):
            return th.get("title")

        # Get messages from in-memory session or disk
        session = self.sessions.get(thread_id)
        messages = list(session.history) if session and session.history else list(th.get("messages", []))
        if not messages:
            return None

        # Extract initial exchange
        first_user = ""
        first_asst = ""
        for m in messages:
            role = m.get("role")
            if role == "user" and not first_user and not m.get("_auto_injected_media"):
                txt = m.get("display_text") or m.get("content")
                if isinstance(txt, list):
                    txt = " ".join(
                        str(p.get("text", "")) for p in txt
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                first_user = str(txt or "").strip()
            elif role == "assistant" and not first_asst:
                txt = m.get("content")
                if isinstance(txt, list):
                    txt = " ".join(
                        str(p.get("text", "")) for p in txt
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                first_asst = str(txt or "").strip()
            if first_user and first_asst:
                break

        if not first_user:
            return None

        user_snippet = first_user[:350].strip()
        asst_snippet = first_asst[:350].strip() if first_asst else ""

        system_instruction = (
            "Eres un asistente que resume hilos de conversación. "
            "Tu tarea es generar un título muy corto, específico y conciso (entre 3 y 5 palabras) "
            "que resuma el tema central de la conversación.\n"
            "Reglas estrictas:\n"
            "1. Responde ÚNICAMENTE con el título en texto plano, sin comillas ni punto final.\n"
            "2. En el mismo idioma de la conversación (habitualmente español).\n"
            "3. NO agregues prefijos como 'Título:', 'Title:', 'Resumen:' ni viñetas.\n"
            "4. Sé directo, informativo y profesional."
        )

        user_prompt = f"Conversación:\nUsuario: {user_snippet}"
        if asst_snippet:
            user_prompt += f"\nAsistente: {asst_snippet}"
        user_prompt += "\n\nTítulo conciso:"

        llm_messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ]

        self.config_manager.apply_env_keys()
        model = self.config_manager.get_active_model()
        call_type, kwargs = prepare_provider_call(model, self.config_manager)

        generated_raw = ""
        try:
            if call_type == "antigravity":
                if not AntigravityClient.is_logged_in():
                    logger.debug("Antigravity not logged in, skipping automatic title generation")
                    return None
                gen = AntigravityClient.completion(
                    model=kwargs["model"],
                    messages=llm_messages,
                    tools=None,
                    stream=True,
                    temperature=0.3,
                    thinking_level="MINIMAL",
                    thinking_budget=0
                )
                chunks_text: List[str] = []
                thought_filter = StreamThoughtFilter(
                    on_token=lambda tok: chunks_text.append(tok),
                    on_thinking=lambda _th: None
                )
                async for chunk in _stream_sync_generator(gen):
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        thought_filter.process_chunk(delta.content)
                thought_filter.flush()
                generated_raw = "".join(chunks_text)
            else:
                kwargs["messages"] = llm_messages
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                kwargs["temperature"] = 0.3
                kwargs["max_tokens"] = 35
                kwargs["stream"] = True
                response_stream = await litellm.acompletion(**kwargs)
                chunks_text: List[str] = []
                thought_filter = StreamThoughtFilter(
                    on_token=lambda tok: chunks_text.append(tok),
                    on_thinking=lambda _th: None
                )
                async for chunk in response_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        thought_filter.process_chunk(delta.content)
                thought_filter.flush()
                generated_raw = "".join(chunks_text)

            clean_title = clean_llm_title(generated_raw)
            if clean_title and len(clean_title) >= 3:
                self.thread_manager.update_title(thread_id, clean_title, is_auto=True)
                self._notify_title_updated(thread_id, clean_title, callbacks)
                logger.info(f"Auto-generated title for thread {thread_id}: '{clean_title}'")
                return clean_title
        except Exception as e:
            logger.warning(f"Could not auto-generate title for thread {thread_id}: {e}")

        return None

    async def run_headless_turn(
        self,
        prompt: str,
        system_extra: str = "",
        max_turns: int = 12
    ) -> Dict[str, Any]:
        """
        Executes an isolated background agent turn (used by Heartbeats & Automations)
        without mutating the interactive conversation history.
        Supports full MCP tool execution.
        """
        self.config_manager.apply_env_keys()
        model = self.config_manager.get_active_model()

        base_sys = self._build_system_prompt(query=prompt, is_call_mode=False)
        if system_extra:
            base_sys = f"{base_sys}\n\n{system_extra}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": base_sys},
            {"role": "user", "content": prompt}
        ]

        turn_count = 0
        final_text = ""
        tools_used: List[str] = []
        seen_calls = set()

        try:
            while turn_count < max_turns:
                turn_count += 1
                raw_tools = self.mcp_manager.get_litellm_tools()
                # Avoid recursive self-triggering of automations during a heartbeat
                tools = [
                    t for t in raw_tools
                    if not t.get("function", {}).get("name", "").startswith("automation__")
                ] if raw_tools else None

                call_type, kwargs = prepare_provider_call(model, self.config_manager)

                if call_type == "antigravity":
                    if not AntigravityClient.is_logged_in():
                        raise RuntimeError("Sesión de Google Antigravity no iniciada.")
                    gen = AntigravityClient.completion(
                        model=kwargs["model"],
                        messages=messages,
                        tools=tools,
                        stream=True,
                        temperature=kwargs.get("temperature", 0.7)
                    )
                    response_stream = _stream_sync_generator(gen)
                else:
                    kwargs["messages"] = messages
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"
                    else:
                        kwargs.pop("tools", None)
                        kwargs.pop("tool_choice", None)
                    response_stream = await litellm.acompletion(**kwargs)

                current_text = ""
                thought_filter = StreamThoughtFilter(lambda _t: None, lambda _th: None)
                tool_calls_dict: Dict[int, Dict[str, Any]] = {}

                async for chunk in response_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue
                    if delta.content:
                        c_add, _ = thought_filter.process_chunk(delta.content)
                        current_text += c_add
                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index if tc.index is not None else 0
                            if idx not in tool_calls_dict:
                                tool_calls_dict[idx] = {
                                    "id": tc.id or f"hb_call_{idx}",
                                    "name": tc.function.name or "" if tc.function else "",
                                    "arguments": tc.function.arguments or "" if tc.function else ""
                                }
                            else:
                                if tc.id:
                                    tool_calls_dict[idx]["id"] = tc.id
                                if tc.function and tc.function.name:
                                    tool_calls_dict[idx]["name"] += tc.function.name
                                if tc.function and tc.function.arguments:
                                    tool_calls_dict[idx]["arguments"] += tc.function.arguments

                c_flush, _ = thought_filter.flush()
                current_text += c_flush

                if tool_calls_dict:
                    tool_calls_list = []
                    for idx, tc_data in sorted(tool_calls_dict.items()):
                        tool_calls_list.append({
                            "id": tc_data["id"],
                            "type": "function",
                            "function": {
                                "name": tc_data["name"],
                                "arguments": tc_data["arguments"]
                            }
                        })
                    messages.append({
                        "role": "assistant",
                        "content": current_text or None,
                        "tool_calls": tool_calls_list
                    })

                    for tc in tool_calls_list:
                        call_id = tc["id"]
                        tool_name = tc["function"]["name"]
                        raw_args = tc["function"]["arguments"]
                        try:
                            parsed_args = json.loads(raw_args) if raw_args else {}
                        except Exception:
                            parsed_args = {"raw_input": raw_args}

                        call_sig = f"{tool_name}:{raw_args}"
                        if call_sig in seen_calls:
                            tool_result_str = f"Aviso: '{tool_name}' ya fue ejecutada con los mismos parámetros."
                        else:
                            seen_calls.add(call_sig)
                            tools_used.append(tool_name)
                            tool_result_str = await self.mcp_manager.call_tool(tool_name, parsed_args)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": tool_name,
                            "content": tool_result_str
                        })
                    continue
                else:
                    final_text = current_text.strip()
                    break

            if not final_text:
                final_text = "HEARTBEAT_OK"

            return {
                "status": "ok",
                "text": final_text,
                "tools_used": tools_used
            }
        except Exception as e:
            logger.error(f"Error in headless automation turn: {e}", exc_info=True)
            return {
                "status": "error",
                "text": f"Error al ejecutar automatización: {str(e)}",
                "tools_used": tools_used
            }
