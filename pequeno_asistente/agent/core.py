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
from typing import Dict, List, Any, Optional, Callable, AsyncIterator
import litellm

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager
from pequeno_asistente.agent.antigravity_client import AntigravityClient

# Configure litellm to avoid debug noise
litellm.drop_params = True
litellm.telemetry = False
litellm.set_verbose = False
litellm.suppress_debug_info = True

logger = logging.getLogger("pequeno_asistente.agent")

BASE_SYSTEM_PROMPT = """Eres Pequeño Asistente, un agente de escritorio ligero, inteligente y proactivo para Linux (Hyprland/Wayland).
Tu objetivo es ayudar al usuario de manera eficiente, directa y amigable en español.

REGLAS FUNDAMENTALES Y DISCIPLINA DE HERRAMIENTAS:
1. ARQUITECTURA PURA MCP: Todas tus herramientas operan exclusivamente mediante los servidores MCP conectados. No posees herramientas nativas ocultas.
2. DISCIPLINA ESTRICTA DE HERRAMIENTAS (¡CRÍTICO!):
   - NUNCA ejecutes herramientas ante saludos ("Hola", "¿cómo estás?", "buenos días", etc.), agradecimientos, despedidas, preguntas conceptuales o conversación general. Para tales casos, responde SIEMPRE de forma directa con texto.
   - SÓLO debes invocar una herramienta cuando el usuario pida expresamente una acción sobre su sistema o requiera datos en tiempo real (ej. consultar la hora, listar/leer archivos, capturar pantalla, consultar el clima).
   - NUNCA llames a herramientas irrelevantes o no relacionadas con la solicitud. Si no tienes la herramienta adecuada para cumplir lo que el usuario pide, explícaselo en texto natural con total transparencia.
   - Tras obtener el resultado de una herramienta, sintetiza inmediatamente tu respuesta final al usuario. No invoques más herramientas salvo que sea imprescindible para completar el requerimiento.
3. HABILIDADES Y PROCEDIMIENTOS (SKILLS):
   - NUNCA intentes llamar a herramientas en bucle si una llamada arroja un error o dice que no existe. Si una herramienta falla, detente de inmediato y explica el problema al usuario con alternativas claras.
   - Si el usuario te pide instalar, compilar o agregar un nuevo servidor MCP (o te comparte un repositorio de GitHub, paquete npm/uvx o URL SSE), consulta y aplica rigurosamente el procedimiento detallado en la sección <skills>.
   - Si cuentas con herramientas de sistema (filesystem o desktop-bash), puedes utilizarlas para inspeccionar o actualizar la configuración en `~/.config/pequeno-asistente/config.json`.
   - Si no posees herramientas de consola o el usuario lo prefiere, proporciónale el bloque JSON exacto listo para que lo copie y pegue en la interfaz de configuración del asistente.
4. MEMORIA PERSONALIZADA:
   - Mantén en cuenta el perfil del usuario y el contexto a largo plazo provisto en <user_memory_system>.
5. ESTILO:
   - Respuestas claras, concisas, estructuradas en Markdown legible.
"""

def load_agent_skills() -> str:
    """
    Loads procedural markdown skills from built-in package directory
    and user configuration directory (~/.config/pequeno-asistente/skills).
    """
    skills_dirs = [
        os.path.join(os.path.dirname(__file__), "skills"),
        os.path.expanduser("~/.config/pequeno-asistente/skills")
    ]
    seen_skills = set()
    skill_blocks = []

    for sdir in skills_dirs:
        if not os.path.exists(sdir):
            continue
        try:
            for fname in sorted(os.listdir(sdir)):
                if fname.endswith(".md") and fname not in seen_skills:
                    seen_skills.add(fname)
                    fpath = os.path.join(sdir, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            skill_blocks.append(content)
        except Exception as e:
            logger.warning(f"Error loading skills from {sdir}: {e}")

    if not skill_blocks:
        return ""

    combined = "\n\n---\n\n".join(skill_blocks)
    return f"<skills>\n# HABILIDADES PROCEDIMENTALES (SKILLS)\n{combined}\n</skills>"

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

    # 8. Google Gemini
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


class AgentCallbacks:
    """Callbacks to stream execution events back to the UI thread."""
    def __init__(
        self,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_tool_end: Optional[Callable[[str, str], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ):
        self.on_token = on_token or (lambda text: None)
        self.on_tool_start = on_tool_start or (lambda name, args: None)
        self.on_tool_end = on_tool_end or (lambda name, res: None)
        self.on_complete = on_complete or (lambda text: None)
        self.on_error = on_error or (lambda err: None)


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
        self.active_thread_id: Optional[str] = None
        self.history: List[Dict[str, Any]] = []
        self._is_running = False
        self._abort_requested = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def load_thread(self, thread_id: str) -> List[Dict[str, Any]]:
        """Loads a conversation thread by ID into memory."""
        if not self.thread_manager:
            return []
        thread_data = self.thread_manager.get_thread(thread_id)
        if thread_data:
            self.active_thread_id = thread_id
            self.thread_manager.active_thread_id = thread_id
            self.history = list(thread_data.get("messages", []))
            return self.history
        return []

    def new_thread(self, title: str = "Nueva conversación") -> Dict[str, Any]:
        """Starts a fresh new conversation thread."""
        self.clear_history()
        if self.thread_manager:
            thread_data = self.thread_manager.create_thread(title=title)
            self.active_thread_id = thread_data["id"]
            return thread_data
        return {"id": "default", "title": title, "messages": []}

    def clear_history(self):
        self.history.clear()
        if self.thread_manager and self.active_thread_id:
            self.thread_manager.save_thread(self.active_thread_id, [])

    def abort(self):
        self._abort_requested = True

    def _build_system_prompt(self) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        memory_prompt = self.memory_manager.get_memory_context_prompt()
        if memory_prompt:
            parts.append(memory_prompt)
        skills_prompt = load_agent_skills()
        if skills_prompt:
            parts.append(skills_prompt)
        return "\n\n".join(parts)

    async def run_turn(self, user_message: str, callbacks: AgentCallbacks):
        if self._is_running:
            callbacks.on_error("El agente ya está procesando una consulta.")
            return

        self._is_running = True
        self._abort_requested = False

        # Apply environment keys for LiteLLM
        self.config_manager.apply_env_keys()

        # Ensure active thread exists
        if self.thread_manager and not self.active_thread_id:
            active_th = self.thread_manager.get_or_create_active_thread()
            self.active_thread_id = active_th["id"]

        # Add user message to history
        self.history.append({
            "role": "user",
            "content": user_message
        })

        if self.thread_manager and self.active_thread_id:
            self.thread_manager.save_thread(self.active_thread_id, self.history)

        model = self.config_manager.get_active_model()
        max_turns = 25
        turn_count = 0
        final_text = ""
        seen_calls = set()
        executed_tool_turns = 0

        try:
            while turn_count < max_turns and not self._abort_requested:
                turn_count += 1
                system_prompt = self._build_system_prompt()

                messages = [{"role": "system", "content": system_prompt}] + self.history

                # Obtain dynamic tools from active MCP servers
                raw_tools = self.mcp_manager.get_litellm_tools()
                tools = raw_tools if raw_tools else None

                # Determine provider dispatch
                call_type, kwargs = prepare_provider_call(model, self.config_manager)

                if call_type == "antigravity":
                    if not AntigravityClient.is_logged_in():
                        raise RuntimeError("Sesión de Google Antigravity no iniciada. Inicia sesión en Configuración ➔ API Keys.")
                    
                    logger.info(f"Antigravity dispatch: model={kwargs['model']}, tools={len(raw_tools)}")
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
                    
                    logger.info(f"LiteLLM dispatch: model={kwargs.get('model')}, api_base={kwargs.get('api_base')}, custom_provider={kwargs.get('custom_llm_provider')}, tools={len(tools) if tools else 0}")
                    response_stream = await litellm.acompletion(**kwargs)

                current_text = ""
                tool_calls_dict: Dict[int, Dict[str, Any]] = {}

                async for chunk in response_stream:
                    if self._abort_requested:
                        break

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Stream text content
                    if delta.content:
                        current_text += delta.content
                        callbacks.on_token(delta.content)

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

                if self._abort_requested:
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

                    self.history.append({
                        "role": "assistant",
                        "content": current_text or None,
                        "tool_calls": tool_calls_list
                    })

                    # Execute each tool call via MCP Manager
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
                            callbacks.on_tool_start(tool_name, parsed_args)
                            tool_result_str = await self.mcp_manager.call_tool(tool_name, parsed_args)
                            callbacks.on_tool_end(tool_name, tool_result_str)

                        # Append tool response
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": tool_name,
                            "content": tool_result_str
                        })

                    if self.thread_manager and self.active_thread_id:
                        self.thread_manager.save_thread(self.active_thread_id, self.history)

                    executed_tool_turns += 1
                    # Continue loop so LLM can process results and execute next tools or synthesize answer
                    continue

                else:
                    # Final assistant response without tool calls
                    final_text = current_text
                    self.history.append({
                        "role": "assistant",
                        "content": final_text
                    })
                    if self.thread_manager and self.active_thread_id:
                        self.thread_manager.save_thread(self.active_thread_id, self.history)
                    callbacks.on_complete(final_text)

                    # Log to daily memory
                    if self.config_manager.data.get("memory", {}).get("auto_log", True):
                        self.memory_manager.log_daily_interaction(user_message, final_text[:300])
                    break

            if not final_text and not self._abort_requested:
                final_text = current_text if current_text else "Operación completada."
                callbacks.on_complete(final_text)

        except Exception as e:
            logger.error(f"Error during agent turn: {e}", exc_info=True)
            callbacks.on_error(f"Error en el modelo ({model}): {str(e)}")
        finally:
            self._is_running = False
            self._abort_requested = False
