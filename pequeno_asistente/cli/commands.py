"""
Interactive Slash Command Processor for Pequeño Asistente CLI.
"""
import os
import json
import shlex
import getpass
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from pequeno_asistente.agent.providers import PROVIDERS
from pequeno_asistente.agent.skills_manager import SkillManager
from pequeno_asistente.agent.model_fetcher import fetch_provider_models, get_cached_models
from pequeno_asistente.log_stream import LOG_FILE_PATH


SLASH_COMMANDS: List[str] = [
    "/help",
    "/new",
    "/threads",
    "/thread",
    "/rename",
    "/autotitle",
    "/delete",
    "/clear",
    "/history",
    "/attach",
    "/attachments",
    "/clear-attach",
    "/model",
    "/models",
    "/provider",
    "/login",
    "/mcp",
    "/skills",
    "/memory",
    "/automations",
    "/heartbeat",
    "/config",
    "/thinking",
    "/agent",
    "/workspace",
    "/export",
    "/logs",
    "/exit",
    "/quit",
]


class CommandResult:
    """Result of executing a slash command."""

    def __init__(
        self,
        handled: bool = True,
        should_exit: bool = False,
        forward_prompt: Optional[str] = None,
    ):
        self.handled = handled
        self.should_exit = should_exit
        self.forward_prompt = forward_prompt


class CLICommandHandler:
    """Processes interactive slash commands against the Pequeño Asistente backend."""

    def __init__(self, cli_app: Any):
        self.app = cli_app
        self.r = cli_app.renderer

    async def handle(self, raw_line: str) -> CommandResult:
        line = raw_line.strip()
        if not line.startswith("/"):
            return CommandResult(handled=False)

        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()

        if not parts:
            return CommandResult(handled=True)

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/exit", "/quit", "/q"):
            return CommandResult(handled=True, should_exit=True)

        if cmd in ("/help", "/?"):
            self._cmd_help()
            return CommandResult(handled=True)

        if cmd == "/new":
            title = " ".join(args).strip() or None
            tid = self.app.new_thread(title=title)
            self.r.success(f"Nuevo hilo creado: {tid[:8]}")
            return CommandResult(handled=True)

        if cmd in ("/threads", "/list"):
            threads = self.app.thread_manager.list_threads()
            self.r.print_threads_table(threads, self.app.active_thread_id)
            return CommandResult(handled=True)

        if cmd == "/thread":
            await self._cmd_thread(args)
            return CommandResult(handled=True)

        if cmd == "/rename":
            if not args:
                self.r.warning("Uso: /rename <nuevo título>")
                return CommandResult(handled=True)
            new_title = " ".join(args).strip()
            self.app.thread_manager.update_title(self.app.active_thread_id, new_title, is_custom=True)
            self.r.success(f"Hilo renombrado a: {new_title}")
            return CommandResult(handled=True)

        if cmd == "/autotitle":
            title = await self.app.agent.generate_thread_title(self.app.active_thread_id, force=True)
            if title:
                self.r.success(f"Título generado: {title}")
            else:
                self.r.warning("No se pudo generar título (¿hilo vacío?).")
            return CommandResult(handled=True)

        if cmd == "/delete":
            self._cmd_delete_thread(args)
            return CommandResult(handled=True)

        if cmd == "/clear":
            self.app.agent.clear_history(self.app.active_thread_id)
            self.r.success("Historial del hilo actual limpiado.")
            return CommandResult(handled=True)

        if cmd == "/history":
            limit = int(args[0]) if args and args[0].isdigit() else 20
            hist = self.app.agent.get_or_create_session(self.app.active_thread_id).history
            self.r.print_history(hist, limit=limit)
            return CommandResult(handled=True)

        if cmd == "/attach":
            self._cmd_attach(args)
            return CommandResult(handled=True)

        if cmd == "/attachments":
            if not self.app.pending_attachments:
                self.r.info("No hay archivos adjuntos en cola.")
            else:
                for i, p in enumerate(self.app.pending_attachments, 1):
                    self.r.info(f"  {i}. {p}")
            return CommandResult(handled=True)

        if cmd == "/clear-attach":
            self.app.pending_attachments.clear()
            self.r.success("Cola de adjuntos vaciada.")
            return CommandResult(handled=True)

        if cmd == "/model":
            await self._cmd_model(args)
            return CommandResult(handled=True)

        if cmd == "/models":
            await self._cmd_models(args)
            return CommandResult(handled=True)

        if cmd == "/provider":
            self._cmd_provider(args)
            return CommandResult(handled=True)

        if cmd == "/login":
            self._cmd_login(args)
            return CommandResult(handled=True)

        if cmd == "/mcp":
            await self._cmd_mcp(args)
            return CommandResult(handled=True)

        if cmd == "/skills":
            self._cmd_skills(args)
            return CommandResult(handled=True)

        if cmd == "/memory":
            self._cmd_memory(args)
            return CommandResult(handled=True)

        if cmd == "/automations":
            await self._cmd_automations(args)
            return CommandResult(handled=True)

        if cmd == "/heartbeat":
            await self._cmd_heartbeat(args)
            return CommandResult(handled=True)

        if cmd == "/config":
            self._cmd_config(args)
            return CommandResult(handled=True)

        if cmd == "/thinking":
            self._cmd_thinking(args)
            return CommandResult(handled=True)

        if cmd == "/agent":
            self._cmd_agent(args)
            return CommandResult(handled=True)

        if cmd == "/workspace":
            self._cmd_workspace(args)
            return CommandResult(handled=True)

        if cmd == "/export":
            self._cmd_export(args)
            return CommandResult(handled=True)

        if cmd == "/logs":
            self._cmd_logs(args)
            return CommandResult(handled=True)

        # Check if command matches an enabled skill ID (e.g. /skill-name prompt)
        skill_id = cmd[1:]
        mgr = SkillManager.get_instance(self.app.config_manager)
        skill = mgr.get_skill(skill_id)
        if skill:
            extra = " ".join(args).strip()
            prompt = f"/{skill_id} {extra}".strip() if extra else f"Ejecuta la habilidad /{skill_id}"
            return CommandResult(handled=True, forward_prompt=prompt)

        self.r.warning(f"Comando desconocido: {cmd}. Usa /help para ver los comandos disponibles.")
        return CommandResult(handled=True)

    def _cmd_help(self):
        help_md = """
### Comandos Disponibles

**Conversación e Hilos**
- `/new [título]` — Crea un nuevo hilo de conversación
- `/threads` — Lista todos los hilos guardados
- `/thread <#|id|texto>` — Cambia a otro hilo
- `/rename <título>` — Renombra el hilo actual
- `/autotitle` — Genera título automático con IA
- `/delete <#|id>` — Elimina un hilo
- `/history [n]` — Muestra los últimos `n` mensajes
- `/clear` — Limpia los mensajes del hilo actual
- `/export [ruta.md|.json]` — Exporta el hilo actual

**Archivos y Contexto**
- `/attach <ruta>` (o `@ruta` en tu mensaje) — Adjunta imagen, audio o documento
- `/attachments` / `/clear-attach` — Ver o limpiar adjuntos en cola

**Modelos y Proveedores**
- `/model [id]` — Ver o cambiar el modelo activo
- `/models [filtro]` — Listar/actualizar modelos del proveedor activo
- `/provider [nombre]` — Ver o cambiar proveedor (openrouter, google, openai, anthropic, ollama, antigravity, vercel, etc.)
- `/login [antigravity|apikey]` — Iniciar sesión OAuth2 (Antigravity) o guardar API key
- `/thinking [on|off]` — Mostrar/ocultar razonamiento del modelo

**Herramientas, Habilidades y Memoria**
- `/mcp [list|tools [srv]|sync|toggle <srv>]` — Gestionar servidores y tools MCP
- `/skills [list|show <id>|toggle <id>]` — Gestionar habilidades (`/skill-id` para invocar)
- `/memory [stats|search <q>|timeline [YYYY-MM-DD]|remember <hecho>|facts]` — Memoria episódica
- `/automations [list|toggle <id>|run <id>]` — Automatizaciones programadas
- `/heartbeat [status|on|off|run]` — Heartbeat proactivo
- `/agent [list|use <id>|clear]` / `/workspace [status|list|use <id|ruta>|save [nombre]|clear]` — Perfiles y workspace
- `/config [show|set <clave> <valor>]` — Ver o editar configuración
- `/logs [n]` — Ver últimas líneas de `app.log`
- `/exit` / `/quit` — Salir
"""
        self.r.console.print(Panel(Markdown(help_md.strip()), title="📖 Ayuda — Pequeño Asistente CLI", box=box.ROUNDED))

    def _resolve_thread_id(self, selector: str) -> Optional[str]:
        direct = self.app.thread_manager.get_thread(selector)
        if direct:
            return direct["id"]
        threads = self.app.thread_manager.list_threads()
        if selector.isdigit():
            idx = int(selector) - 1
            if 0 <= idx < len(threads):
                return threads[idx]["id"]
        for th in threads:
            if th["id"] == selector or th["id"].startswith(selector):
                return th["id"]
        sel_low = selector.lower()
        for th in threads:
            if sel_low in (th.get("title") or "").lower():
                return th["id"]
        return None

    async def _cmd_thread(self, args: List[str]):
        if not args:
            th = self.app.thread_manager.get_thread(self.app.active_thread_id)
            title = th.get("title", "Sin título") if th else "Sin título"
            self.r.info(f"Hilo activo: [bold]{title}[/bold] ({self.app.active_thread_id})")
            return
        tid = self._resolve_thread_id(" ".join(args))
        if not tid:
            self.r.error(f"No se encontró ningún hilo que coincida con '{' '.join(args)}'.")
            return
        self.app.switch_thread(tid)
        th = self.app.thread_manager.get_thread(tid)
        title = th.get("title", "Conversación") if th else tid[:8]
        self.r.success(f"Cambiado al hilo: {title} ({tid[:8]})")

    def _cmd_delete_thread(self, args: List[str]):
        if not args:
            self.r.warning("Uso: /delete <#|id>")
            return
        tid = self._resolve_thread_id(" ".join(args))
        if not tid:
            self.r.error("Hilo no encontrado.")
            return
        if tid == self.app.active_thread_id:
            self.app.thread_manager.delete_thread(tid)
            self.app.agent.delete_session(tid)
            new_th = self.app.thread_manager.get_or_create_active_thread()
            self.app.switch_thread(new_th["id"])
            self.r.success(f"Hilo {tid[:8]} eliminado. Hilo activo ahora: {new_th['id'][:8]}")
        else:
            self.app.thread_manager.delete_thread(tid)
            self.app.agent.delete_session(tid)
            self.r.success(f"Hilo {tid[:8]} eliminado.")

    def _cmd_attach(self, args: List[str]):
        if not args:
            self.r.warning("Uso: /attach <ruta_al_archivo>")
            return
        ws_dir = getattr(self.app, "workspace_dir", None)
        for raw_path in args:
            candidate = Path(os.path.expanduser(raw_path))
            p = candidate.resolve()
            if (not p.exists() or not p.is_file()) and ws_dir and not candidate.is_absolute():
                alt = (ws_dir / candidate).resolve()
                if alt.exists() and alt.is_file():
                    p = alt
            if not p.exists() or not p.is_file():
                self.r.error(f"Archivo no encontrado: {raw_path}")
                continue
            if str(p) not in self.app.pending_attachments:
                self.app.pending_attachments.append(str(p))
            self.r.success(f"Adjunto en cola: {p.name}")

    async def _cmd_model(self, args: List[str]):
        if not args:
            self.r.info(
                f"Proveedor: [green]{self.app.config_manager.get_active_provider()}[/green] · "
                f"Modelo activo: [yellow]{self.app.config_manager.get_active_model()}[/yellow]"
            )
            return
        model_id = args[0]
        self.app.config_manager.set_active_model(model_id)
        self.r.success(f"Modelo actualizado a: {model_id}")

    async def _cmd_models(self, args: List[str]):
        provider = self.app.config_manager.get_active_provider()
        force = "--refresh" in args or "-r" in args
        filter_terms = [a.lower() for a in args if a not in ("--refresh", "-r")]
        api_key = self.app.config_manager.get_api_key(provider)
        base_url = self.app.config_manager.data["llm"].get("ollama_base_url", "")

        models = [] if force else get_cached_models(provider)
        if not models:
            self.r.info(f"Consultando modelos de {provider}...")
            models, err = await fetch_provider_models(provider, api_key=api_key, base_url=base_url, force_refresh=True)
            if err:
                self.r.warning(f"No se pudo consultar API de {provider}: {err}")

        if not models and provider in PROVIDERS:
            models = [{"id": m, "description": "Sugerido"} for m in PROVIDERS[provider].get("suggested_models", [])]

        if filter_terms:
            models = [m for m in models if all(t in m.get("id", "").lower() for t in filter_terms)]

        self.r.print_models_table(models, self.app.config_manager.get_active_model())

    def _cmd_provider(self, args: List[str]):
        if not args:
            active = self.app.config_manager.get_active_provider()
            table = Table(title="🌐 Proveedores LLM", box=box.ROUNDED)
            table.add_column("ID", style="cyan")
            table.add_column("Nombre", style="white")
            table.add_column("Modelo por defecto", style="dim")
            table.add_column("API Key", width=12)
            for pid, pinfo in PROVIDERS.items():
                key = self.app.config_manager.get_api_key(pid)
                has_key = "[green]Configurada[/green]" if key else "[dim]No[/dim]"
                pid_lbl = f"[bold green]▶ {pid}[/bold green]" if pid == active else pid
                table.add_row(pid_lbl, pinfo.get("name", pid), pinfo.get("default_model", ""), has_key)
            self.r.console.print(table)
            return

        pid = args[0].lower()
        if pid not in PROVIDERS:
            self.r.error(f"Proveedor desconocido '{pid}'. Disponibles: {', '.join(PROVIDERS.keys())}")
            return
        self.app.config_manager.set_llm_provider(pid)
        self.r.success(
            f"Proveedor cambiado a [bold]{pid}[/bold] (modelo: {self.app.config_manager.get_active_model()})"
        )

    def _cmd_login(self, args: List[str]):
        target = args[0].lower() if args else self.app.config_manager.get_active_provider()
        if target == "antigravity":
            from pequeno_asistente.agent.antigravity_client import run_login_flow

            self.r.info("Iniciando flujo OAuth2 de Antigravity...")
            ok = run_login_flow(on_status_update=lambda msg: self.r.info(msg))
            if ok:
                self.r.success("Autenticación Antigravity completada.")
            else:
                self.r.error("No se pudo completar la autenticación Antigravity.")
            return

        if target not in PROVIDERS:
            self.r.error(f"Proveedor desconocido: {target}")
            return
        key = getpass.getpass(f"Introduce API Key para {target}: ").strip()
        if not key:
            self.r.warning("Operación cancelada (clave vacía).")
            return
        self.app.config_manager.set_api_key(target, key)
        self.app.config_manager.apply_env_keys()
        self.r.success(f"API Key guardada para {target}.")

    async def _cmd_mcp(self, args: List[str]):
        sub = args[0].lower() if args else "list"
        if sub == "list":
            self.r.print_mcp_table(
                self.app.config_manager.get_mcp_servers(),
                self.app.mcp_manager.get_status_summary(),
            )
        elif sub == "tools":
            srv = args[1] if len(args) > 1 else None
            self.r.print_mcp_tools(self.app.mcp_manager.get_litellm_tools(), server_filter=srv)
        elif sub == "sync":
            self.r.info("Sincronizando servidores MCP...")
            await self.app.mcp_manager.sync_servers(self.app.config_manager.get_mcp_servers(), force=True)
            self.r.success("Servidores MCP sincronizados.")
        elif sub == "toggle" and len(args) >= 2:
            sid = args[1]
            servers = self.app.config_manager.get_mcp_servers()
            if sid not in servers:
                self.r.error(f"Servidor MCP '{sid}' no existe en config.")
                return
            currently_disabled = servers[sid].get("disabled", False)
            self.app.config_manager.toggle_mcp_server(sid, enabled=currently_disabled)
            await self.app.mcp_manager.sync_servers(self.app.config_manager.get_mcp_servers())
            state = "activado" if currently_disabled else "desactivado"
            self.r.success(f"Servidor MCP '{sid}' {state}.")
        else:
            self.r.warning("Uso: /mcp [list | tools [server] | sync | toggle <server>]")

    def _cmd_skills(self, args: List[str]):
        mgr = SkillManager.get_instance(self.app.config_manager)
        sub = args[0].lower() if args else "list"
        if sub == "list":
            skills = list(mgr.discover_skills(force_refresh=True).values())
            self.r.print_skills_table(skills)
        elif sub == "show" and len(args) >= 2:
            sk = mgr.get_skill(args[1])
            if not sk:
                self.r.error(f"Skill '{args[1]}' no encontrada.")
                return
            self.r.console.print(Panel(Markdown(sk.read_content()), title=f"⚡ {sk.name} ({sk.id})", box=box.ROUNDED))
        elif sub == "toggle" and len(args) >= 2:
            sid = args[1]
            sk = mgr.get_skill(sid)
            if not sk:
                self.r.error(f"Skill '{sid}' no encontrada.")
                return
            mgr.toggle_skill(sid, not sk.enabled)
            self.r.success(f"Skill '{sid}' {'activada' if not sk.enabled else 'desactivada'}.")
        else:
            self.r.warning("Uso: /skills [list | show <id> | toggle <id>]")

    def _cmd_memory(self, args: List[str]):
        mm = self.app.memory_manager
        sub = args[0].lower() if args else "stats"
        if sub == "stats":
            st = mm.get_episodic_stats()
            self.r.console.print(Panel(json.dumps(st, indent=2, ensure_ascii=False), title="🧠 Estadísticas de Memoria", box=box.ROUNDED))
        elif sub == "search" and len(args) >= 2:
            q = " ".join(args[1:])
            res = mm.search_episodes(query=q, limit=8)
            if not res:
                self.r.info("No se encontraron episodios.")
                return
            for ep in res:
                self.r.console.print(
                    Panel(
                        f"{ep.get('content', '')[:400]}",
                        title=f"[{ep.get('timestamp', '')[:16]}] {ep.get('title', 'Episodio')} (score={ep.get('score', 0):.2f})",
                        box=box.ROUNDED,
                    )
                )
        elif sub == "timeline":
            date_str = args[1] if len(args) > 1 else None
            eps = mm.get_timeline(date_str=date_str, limit=12)
            if not eps:
                self.r.info("No hay episodios en la línea de tiempo.")
                return
            for ep in eps:
                self.r.console.print(f"[cyan]{ep.get('timestamp', '')[:16]}[/cyan] · [bold]{ep.get('title', '')}[/bold]")
        elif sub == "remember" and len(args) >= 2:
            fact = " ".join(args[1:])
            mm.append_fact(fact)
            self.r.success(f"Hecho guardado en MEMORY.md: {fact}")
        elif sub == "facts":
            self.r.console.print(Panel(Markdown(mm.get_long_term_memory() or "_Vacío_"), title="📝 MEMORY.md", box=box.ROUNDED))
        else:
            self.r.warning("Uso: /memory [stats | search <query> | timeline [YYYY-MM-DD] | remember <hecho> | facts]")

    async def _cmd_automations(self, args: List[str]):
        sub = args[0].lower() if args else "list"
        if sub == "list":
            self.r.print_automations_table(
                self.app.config_manager.get_automations(),
                self.app.config_manager.get_heartbeat_config(),
            )
        elif sub == "toggle" and len(args) >= 2:
            aid_prefix = args[1]
            autos = self.app.config_manager.get_automations()
            match = next((a for a in autos if a["id"].startswith(aid_prefix)), None)
            if not match:
                self.r.error(f"Automatización '{aid_prefix}' no encontrada.")
                return
            new_state = not match.get("enabled", True)
            self.app.config_manager.update_automation(match["id"], {"enabled": new_state})
            self.r.success(f"Automatización '{match.get('name')}' {'activada' if new_state else 'pausada'}.")
        elif sub == "run" and len(args) >= 2:
            aid_prefix = args[1]
            autos = self.app.config_manager.get_automations()
            match = next((a for a in autos if a["id"].startswith(aid_prefix)), None)
            if not match:
                self.r.error(f"Automatización '{aid_prefix}' no encontrada.")
                return
            self.r.info(f"Ejecutando automatización '{match.get('name')}'...")
            await self.app.heartbeat_manager.run_automation(match["id"], manual=True)
            self.r.success("Ejecución completada.")
        else:
            self.r.warning("Uso: /automations [list | toggle <id> | run <id>]")

    async def _cmd_heartbeat(self, args: List[str]):
        sub = args[0].lower() if args else "status"
        if sub == "status":
            self.r.print_automations_table(
                self.app.config_manager.get_automations(),
                self.app.config_manager.get_heartbeat_config(),
            )
        elif sub in ("on", "enable"):
            self.app.config_manager.set_heartbeat_config({"enabled": True})
            self.r.success("Heartbeat proactivo activado.")
        elif sub in ("off", "disable"):
            self.app.config_manager.set_heartbeat_config({"enabled": False})
            self.r.success("Heartbeat proactivo desactivado.")
        elif sub == "run":
            self.r.info("Ejecutando Heartbeat global ahora...")
            await self.app.heartbeat_manager.run_global_heartbeat(manual=True)
            self.r.success("Heartbeat ejecutado.")
        else:
            self.r.warning("Uso: /heartbeat [status | on | off | run]")

    def _cmd_config(self, args: List[str]):
        sub = args[0].lower() if args else "show"
        if sub == "show":
            sanitized = json.loads(json.dumps(self.app.config_manager.data))
            if "llm" in sanitized and "api_keys" in sanitized["llm"]:
                sanitized["llm"]["api_keys"] = {
                    k: ("***" if v else "") for k, v in sanitized["llm"]["api_keys"].items()
                }
            self.r.console.print(Panel(json.dumps(sanitized, indent=2, ensure_ascii=False), title="⚙ Configuración", box=box.ROUNDED))
        elif sub == "set" and len(args) >= 3:
            key_path, raw_val = args[1], args[2]
            if raw_val.lower() in ("true", "false"):
                val: Any = raw_val.lower() == "true"
            else:
                try:
                    val = int(raw_val)
                except ValueError:
                    try:
                        val = float(raw_val)
                    except ValueError:
                        val = raw_val
            parts = key_path.split(".")
            cur = self.app.config_manager.data
            for p in parts[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts[-1]] = val
            self.app.config_manager.save()
            self.r.success(f"Configuración actualizada: {key_path} = {val!r}")
        else:
            self.r.warning("Uso: /config [show | set <seccion.clave> <valor>]")

    def _cmd_thinking(self, args: List[str]):
        if args and args[0].lower() in ("on", "true", "1"):
            self.r.show_thinking = True
        elif args and args[0].lower() in ("off", "false", "0"):
            self.r.show_thinking = False
        else:
            self.r.show_thinking = not self.r.show_thinking
        self.r.success(f"Visualización de razonamiento (thinking): {'activada' if self.r.show_thinking else 'desactivada'}")

    def _cmd_agent(self, args: List[str]):
        agents = self.app.config_manager.get_agents()
        sub = args[0].lower() if args else "list"
        if sub == "list":
            if not agents:
                self.r.info("No hay perfiles de agente configurados.")
                return
            for aid, adata in agents.items():
                self.r.info(f"• [cyan]{aid}[/cyan]: {adata.get('name', aid)}")
        elif sub == "use" and len(args) >= 2:
            aid = args[1]
            prof = self.app.config_manager.get_agent(aid)
            if not prof:
                self.r.error(f"Agente '{aid}' no encontrado.")
                return
            self.app.thread_manager.set_agent_profile(self.app.active_thread_id, aid, prof)
            self.r.success(f"Perfil de agente '{prof.get('name', aid)}' asignado al hilo actual.")
        elif sub == "clear":
            self.app.thread_manager.set_agent_profile(self.app.active_thread_id, None, None)
            self.r.success("Perfil de agente removido del hilo actual.")
        else:
            self.r.warning("Uso: /agent [list | use <id> | clear]")

    def _cmd_workspace(self, args: List[str]):
        from pequeno_asistente.cli.app import resolve_workspace_for_cli, _slugify_workspace_id

        workspaces = self.app.config_manager.get_workspaces()
        sub = args[0].lower() if args else "status"
        if sub == "status":
            cur = self.app.thread_manager.get_workspace_profile(self.app.active_thread_id) or getattr(self.app, "workspace_profile", None)
            if not cur:
                self.r.info("No hay workspace activo en este hilo.")
                return
            folders = cur.get("folders") or ([cur.get("workspace_path")] if cur.get("workspace_path") else [])
            folders_str = ", ".join(str(f) for f in folders) if folders else "(sin carpetas)"
            mem_file = cur.get("memory_file") or "(ninguno)"
            self.r.info(
                f"Workspace activo: [bold magenta]{cur.get('name', cur.get('id', 'Workspace'))}[/bold magenta] "
                f"([cyan]{cur.get('id', '-')}[/cyan])\n"
                f"  Carpetas: {folders_str}\n"
                f"  Memoria contextual: {mem_file}"
            )
        elif sub == "list":
            if not workspaces:
                self.r.info("No hay workspaces guardados en la configuración.")
                return
            for wid, wdata in workspaces.items():
                folders = wdata.get("folders") or ([wdata.get("workspace_path")] if wdata.get("workspace_path") else [])
                folders_str = ", ".join(str(f) for f in folders)
                self.r.info(f"• [cyan]{wid}[/cyan]: {wdata.get('name', wid)} ({folders_str})")
        elif sub == "use" and len(args) >= 2:
            target = " ".join(args[1:]).strip()
            wid, prof = resolve_workspace_for_cli(
                self.app.config_manager,
                workspace_arg=target,
                use_cwd_workspace=False,
            )
            if not prof:
                self.r.error(f"Workspace o carpeta '{target}' no encontrado.")
                return
            self.app.set_active_workspace(wid, prof)
            folders = prof.get("folders") or []
            folder_hint = f" ({folders[0]})" if folders else ""
            self.r.success(f"Workspace '{prof.get('name', wid)}'{folder_hint} asignado al hilo actual.")
        elif sub == "save":
            cur = self.app.thread_manager.get_workspace_profile(self.app.active_thread_id) or getattr(self.app, "workspace_profile", None)
            if not cur:
                self.r.error("No hay ningún workspace activo para guardar.")
                return
            custom_name = " ".join(args[1:]).strip() if len(args) > 1 else ""
            ws_name = custom_name or cur.get("name") or "Workspace"
            cur_id = cur.get("id") or ""
            ws_id = _slugify_workspace_id(ws_name) if (not cur_id or cur_id.startswith("dir:")) else cur_id
            to_save = dict(cur)
            to_save["id"] = ws_id
            to_save["name"] = ws_name
            self.app.config_manager.save_workspace(ws_id, to_save)
            self.app.set_active_workspace(ws_id, to_save)
            self.r.success(f"Workspace guardado como '{ws_name}' (id: {ws_id}).")
        elif sub == "clear":
            self.app.set_active_workspace(None, None)
            self.r.success("Workspace removido del hilo actual.")
        else:
            self.r.warning("Uso: /workspace [status | list | use <id|ruta> | save [nombre] | clear]")

    def _cmd_export(self, args: List[str]):
        th = self.app.thread_manager.get_thread(self.app.active_thread_id)
        if not th:
            self.r.error("No hay hilo activo para exportar.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(args[0]) if args else Path(f"conversacion_{self.app.active_thread_id[:8]}_{ts}.md")
        if out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(th, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            lines = [f"# {th.get('title', 'Conversación')}\n"]
            for m in th.get("messages", []):
                role = m.get("role", "user").upper()
                content = m.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
                lines.append(f"## {role}\n\n{content}\n")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        self.r.success(f"Hilo exportado a: {out_path.resolve()}")

    def _cmd_logs(self, args: List[str]):
        n = int(args[0]) if args and args[0].isdigit() else 40
        if not LOG_FILE_PATH.exists():
            self.r.info("Archivo de logs aún no existe.")
            return
        lines = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        self.r.console.print(Panel("\n".join(lines[-n:]), title=f"📡 Últimas {n} líneas de {LOG_FILE_PATH}", box=box.ROUNDED))
