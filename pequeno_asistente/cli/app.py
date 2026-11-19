"""
Main CLI Application for Pequeño Asistente.
Provides interactive REPL with Rich streaming, slash commands, readline completion,
and scriptable one-shot / subcommand modes.
"""
import os
import re
import sys
import json
import signal
import asyncio
import hashlib
import logging
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

try:
    import readline
except ImportError:
    readline = None  # type: ignore

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.log_stream import setup_realtime_logging, LOG_FILE_PATH
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.agent.heartbeat import HeartbeatManager
from pequeno_asistente.agent.skills_manager import SkillManager
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.security.manager import CredentialManager

from pequeno_asistente.cli.renderer import CLIRenderer
from pequeno_asistente.cli.security_prompt import CLICredentialPrompt
from pequeno_asistente.cli.commands import CLICommandHandler, SLASH_COMMANDS

CLI_HISTORY_PATH = Path.home() / ".config" / "pequeno-asistente" / "cli_history"

_AT_PATH_RE = re.compile(r'(?:^|\s)@(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))')


def configure_cli_logging(verbose: bool = False):
    """
    Configures real-time file logging while keeping stderr completely clean during CLI sessions
    unless --verbose is passed. All logs remain available in ~/.config/pequeno-asistente/app.log
    and via `/logs`.
    """
    setup_realtime_logging(level=logging.INFO)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            if verbose:
                handler.setLevel(logging.INFO)
            else:
                root.removeHandler(handler)

    if not verbose:
        # Strip StreamHandlers attached directly to third-party loggers (e.g. LiteLLM)
        # so they don't bypass root and leak onto CLI stderr.
        for obj in list(logging.root.manager.loggerDict.values()):
            if isinstance(obj, logging.Logger):
                for handler in list(obj.handlers):
                    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                        obj.removeHandler(handler)


def _slugify_workspace_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "workspace"


def build_directory_workspace_profile(target_dir: Path) -> Tuple[str, Dict[str, Any]]:
    """Builds a workspace profile for an arbitrary local directory."""
    resolved = target_dir.expanduser().resolve()
    short_hash = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:6]
    ws_id = f"dir:{_slugify_workspace_id(resolved.name or 'root')}-{short_hash}"
    profile: Dict[str, Any] = {
        "id": ws_id,
        "name": resolved.name or str(resolved),
        "description": f"Workspace automático para el directorio {resolved}",
        "folders": [str(resolved)],
        "memory_file": str(resolved / "CONTEXT.md"),
    }
    return ws_id, profile


def _normalize_workspace_profile(wid: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    prof = dict(raw or {})
    prof.setdefault("id", wid)
    folders = prof.get("folders") or []
    if not folders and prof.get("workspace_path"):
        folders = [prof.get("workspace_path")]
    prof["folders"] = [str(Path(str(f)).expanduser().resolve()) for f in folders if f and str(f).strip()]
    prof.pop("workspace_path", None)
    return prof


def _match_configured_workspace_for_dir(
    config_manager: ConfigManager,
    target_dir: Path,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Matches `target_dir` against configured workspaces in config.json.
    Prefers an exact folder match, then the deepest parent folder match.
    """
    resolved = target_dir.expanduser().resolve()
    workspaces = config_manager.get_workspaces() or {}
    best_parent: Optional[Tuple[int, str, Dict[str, Any]]] = None

    for wid, raw in workspaces.items():
        if not isinstance(raw, dict):
            continue
        prof = _normalize_workspace_profile(wid, raw)
        for folder_str in prof.get("folders", []):
            folder_path = Path(folder_str).expanduser().resolve()
            if resolved == folder_path:
                return wid, prof
            try:
                if resolved.is_relative_to(folder_path):
                    depth = len(folder_path.parts)
                    if best_parent is None or depth > best_parent[0]:
                        best_parent = (depth, wid, prof)
            except ValueError:
                continue

    if best_parent is not None:
        return best_parent[1], best_parent[2]
    return None


def resolve_workspace_for_cli(
    config_manager: ConfigManager,
    workspace_arg: Optional[str] = None,
    cwd: Optional[Path] = None,
    use_cwd_workspace: bool = True,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Resolves the workspace to bind for a CLI session.
    1. If `workspace_arg` is provided, checks configured workspace IDs/names first, then treats it as a folder path.
    2. Otherwise, if `use_cwd_workspace` is True, uses `cwd` (or `Path.cwd()`), matching a configured
       workspace if possible or creating an automatic folder workspace profile.
    3. If `use_cwd_workspace` is False and no `workspace_arg` was passed, returns `(None, None)`.
    """
    if workspace_arg:
        workspaces = config_manager.get_workspaces() or {}
        if workspace_arg in workspaces and isinstance(workspaces[workspace_arg], dict):
            return workspace_arg, _normalize_workspace_profile(workspace_arg, workspaces[workspace_arg])

        low = workspace_arg.strip().lower()
        for wid, raw in workspaces.items():
            if not isinstance(raw, dict):
                continue
            if wid.lower() == low or str(raw.get("name", "")).strip().lower() == low:
                return wid, _normalize_workspace_profile(wid, raw)

        candidate = Path(os.path.expanduser(workspace_arg)).resolve()
        if candidate.exists() and candidate.is_dir():
            matched = _match_configured_workspace_for_dir(config_manager, candidate)
            if matched:
                return matched
            return build_directory_workspace_profile(candidate)
        return None, None

    if not use_cwd_workspace:
        return None, None

    target_dir = (cwd or Path.cwd()).expanduser().resolve()
    matched = _match_configured_workspace_for_dir(config_manager, target_dir)
    if matched:
        return matched
    return build_directory_workspace_profile(target_dir)


def extract_inline_attachments(text: str, base_dir: Optional[Path] = None) -> Tuple[str, List[str]]:
    """
    Extracts `@path/to/file` references from user prompt text when they point to
    existing local files. Returns `(cleaned_text, resolved_file_paths)`.
    """
    found: List[str] = []

    def _repl(match: re.Match) -> str:
        raw_p = match.group(1) or match.group(2) or match.group(3) or ""
        candidate = Path(os.path.expanduser(raw_p))
        expanded = candidate.resolve()
        if not (expanded.exists() and expanded.is_file()) and base_dir and not candidate.is_absolute():
            alt = (base_dir / candidate).resolve()
            if alt.exists() and alt.is_file():
                expanded = alt
        if expanded.exists() and expanded.is_file():
            p_str = str(expanded)
            if p_str not in found:
                found.append(p_str)
            return " " if match.group(0).startswith(" ") else ""
        return match.group(0)

    cleaned = _AT_PATH_RE.sub(_repl, text).strip()
    return cleaned or text, found


class PequenoAsistenteCLI:
    """Core orchestrator for Pequeño Asistente CLI."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        renderer: Optional[CLIRenderer] = None,
        sync_mcp: bool = True,
        model_override: Optional[str] = None,
        thread_id: Optional[str] = None,
        enable_heartbeat: bool = False,
        workspace: Optional[str] = None,
        use_cwd_workspace: bool = True,
        cwd: Optional[Path] = None,
    ):
        self.config_manager = ConfigManager(config_path=config_path)
        if model_override:
            self.config_manager.set_active_model(model_override)

        self.memory_manager = MemoryManager()
        self.thread_manager = ThreadManager()
        self.mcp_manager = MCPManager(config_manager=self.config_manager, quiet_stderr=True)
        self._mcp_sync_task: Optional[asyncio.Task] = None
        self.agent = AgentCore(
            self.config_manager,
            self.mcp_manager,
            self.memory_manager,
            thread_manager=self.thread_manager,
        )
        self.heartbeat_manager = HeartbeatManager(
            config_manager=self.config_manager,
            agent=self.agent,
            memory_manager=self.memory_manager,
        )
        self.renderer = renderer or CLIRenderer()
        self.sync_mcp = sync_mcp
        self.enable_heartbeat = enable_heartbeat
        self.pending_attachments: List[str] = []
        self.credential_prompt = CLICredentialPrompt(renderer=self.renderer)

        # Resolve workspace (explicit -w/--workspace or automatic current folder workspace)
        self.workspace_id, self.workspace_profile = resolve_workspace_for_cli(
            self.config_manager,
            workspace_arg=workspace,
            cwd=cwd,
            use_cwd_workspace=use_cwd_workspace,
        )
        if workspace and not self.workspace_profile:
            self.renderer.warning(f"Workspace o carpeta no encontrado: '{workspace}'. Continuando sin workspace.")
        self.workspace_dir: Optional[Path] = self._extract_primary_workspace_dir(self.workspace_profile)

        if thread_id:
            th = self.thread_manager.get_thread(thread_id)
            if th:
                self.active_thread_id = th["id"]
                if self.workspace_profile and (workspace or not self.thread_manager.get_workspace_profile(self.active_thread_id)):
                    self.thread_manager.set_workspace_profile(
                        self.active_thread_id,
                        self.workspace_id,
                        self.workspace_profile,
                    )
            else:
                created = self.thread_manager.create_thread(
                    title=f"CLI ({thread_id[:8]})",
                    workspace_id=self.workspace_id,
                    workspace_profile=self.workspace_profile,
                )
                self.active_thread_id = created["id"]
        elif self.workspace_profile:
            matched_tid = self._find_thread_for_workspace(self.workspace_id, self.workspace_profile)
            if matched_tid:
                self.active_thread_id = matched_tid
            else:
                ws_title = (self.workspace_profile.get("name") or "Workspace").strip()
                created = self.thread_manager.create_thread(
                    title=ws_title,
                    workspace_id=self.workspace_id,
                    workspace_profile=self.workspace_profile,
                )
                self.active_thread_id = created["id"]
        else:
            th = self.thread_manager.get_or_create_active_thread()
            self.active_thread_id = th["id"]

        self.agent.load_thread(self.active_thread_id)
        self.command_handler = CLICommandHandler(self)

    @staticmethod
    def _extract_primary_workspace_dir(workspace_profile: Optional[Dict[str, Any]]) -> Optional[Path]:
        if not workspace_profile:
            return None
        folders = workspace_profile.get("folders") or []
        if not folders and workspace_profile.get("workspace_path"):
            folders = [workspace_profile.get("workspace_path")]
        if folders:
            return Path(str(folders[0])).expanduser().resolve()
        return None

    def _find_thread_for_workspace(
        self,
        workspace_id: Optional[str],
        workspace_profile: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Returns the most recently updated thread bound to the given workspace/folder, if any."""
        target_folders = set()
        if workspace_profile:
            for f in workspace_profile.get("folders") or []:
                if f:
                    target_folders.add(str(Path(str(f)).expanduser().resolve()))

        for th in self.thread_manager.list_threads():
            if workspace_id and th.get("workspace_id") == workspace_id:
                return th["id"]
            if target_folders:
                th_paths = {
                    str(Path(str(p)).expanduser().resolve())
                    for p in (th.get("workspace_paths") or [])
                    if p
                }
                if target_folders & th_paths:
                    return th["id"]
        return None

    def set_active_workspace(
        self,
        workspace_id: Optional[str],
        workspace_profile: Optional[Dict[str, Any]],
    ):
        """Updates the active workspace for both this CLI session and the current thread."""
        self.workspace_id = workspace_id
        self.workspace_profile = workspace_profile
        self.workspace_dir = self._extract_primary_workspace_dir(workspace_profile)
        self.thread_manager.set_workspace_profile(
            self.active_thread_id,
            workspace_id,
            workspace_profile,
        )

    def _get_runtime_mcp_servers(self) -> Dict[str, Any]:
        """
        Returns a runtime copy of configured MCP servers, ensuring any configured
        `@modelcontextprotocol/server-filesystem` server also has access to the active
        workspace folders without mutating config.json.
        """
        servers = self.config_manager.get_mcp_servers() or {}
        ws_folders: List[str] = []
        if self.workspace_profile:
            ws_folders = [str(Path(str(f)).expanduser().resolve()) for f in (self.workspace_profile.get("folders") or []) if f]
        if not ws_folders:
            return servers

        runtime_servers: Dict[str, Any] = {}
        for sid, scfg in servers.items():
            if not isinstance(scfg, dict):
                runtime_servers[sid] = scfg
                continue
            args = list(scfg.get("args") or [])
            if any("server-filesystem" in str(a) for a in args):
                existing_paths: List[Path] = []
                for a in args:
                    a_str = str(a)
                    if a_str.startswith("-") or "server-filesystem" in a_str:
                        continue
                    try:
                        existing_paths.append(Path(os.path.expanduser(a_str)).resolve())
                    except Exception:
                        pass
                for folder in ws_folders:
                    fpath = Path(folder).resolve()
                    covered = False
                    for ep in existing_paths:
                        try:
                            if fpath == ep or fpath.is_relative_to(ep):
                                covered = True
                                break
                        except ValueError:
                            continue
                    if not covered:
                        args.append(str(fpath))
                        existing_paths.append(fpath)
                scfg_copy = dict(scfg)
                scfg_copy["args"] = args
                runtime_servers[sid] = scfg_copy
            else:
                runtime_servers[sid] = scfg
        return runtime_servers

    def new_thread(self, title: Optional[str] = None) -> str:
        default_title = (self.workspace_profile.get("name") if self.workspace_profile else None) or "Nueva conversación"
        th = self.agent.new_thread(
            title=title or default_title,
            workspace_id=self.workspace_id,
            workspace_profile=self.workspace_profile,
        )
        tid = th["id"] if isinstance(th, dict) else str(th)
        self.active_thread_id = tid
        return tid

    def switch_thread(self, thread_id: str):
        self.active_thread_id = thread_id
        self.agent.load_thread(thread_id)

    async def startup(self, background: bool = False):
        if self.sync_mcp:
            runtime_servers = self._get_runtime_mcp_servers()
            if background:
                if self._mcp_sync_task is None or self._mcp_sync_task.done():
                    self._mcp_sync_task = asyncio.create_task(
                        self.mcp_manager.sync_servers(runtime_servers)
                    )
            else:
                await self.mcp_manager.sync_servers(runtime_servers)
        if self.enable_heartbeat:
            self.heartbeat_manager.start(asyncio.get_running_loop())

    async def shutdown(self):
        if self._mcp_sync_task and not self._mcp_sync_task.done():
            self._mcp_sync_task.cancel()
            try:
                await asyncio.wait_for(self._mcp_sync_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        try:
            self.heartbeat_manager.stop()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.mcp_manager.shutdown(), timeout=2.0)
        except Exception:
            pass

    async def execute_turn(
        self,
        prompt: str,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if self._mcp_sync_task and not self._mcp_sync_task.done():
            self.renderer.info("Finalizando sincronización de servidores MCP...")
            try:
                await asyncio.wait_for(asyncio.shield(self._mcp_sync_task), timeout=15.0)
            except (asyncio.TimeoutError, Exception):
                pass
        cleaned_prompt, inline_files = extract_inline_attachments(prompt, base_dir=self.workspace_dir)
        all_attachments: List[str] = []
        for p in list(attachments or []) + list(self.pending_attachments) + inline_files:
            if p not in all_attachments:
                all_attachments.append(p)
        self.pending_attachments.clear()

        display = self.renderer.create_turn_session()
        callbacks = AgentCallbacks(
            on_token=display.on_token,
            on_thinking=display.on_thinking,
            on_tool_start=display.on_tool_start,
            on_tool_end=display.on_tool_end,
            on_complete=display.on_complete,
            on_error=display.on_error,
        )

        token = CredentialManager.set_turn_handler(self.credential_prompt)
        display.start()
        try:
            await self.agent.run_turn(
                user_message=cleaned_prompt,
                callbacks=callbacks,
                thread_id=self.active_thread_id,
                attachments=all_attachments or None,
            )
        except asyncio.CancelledError:
            self.agent.abort(self.active_thread_id)
            display.on_error("Turno cancelado por el usuario.")
        finally:
            display.finish()
            CredentialManager.reset_turn_handler(token)

        return display.to_json_dict(
            thread_id=self.active_thread_id,
            model=self.config_manager.get_active_model(),
        )

    def _setup_readline(self):
        if readline is None:
            return
        CLI_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if CLI_HISTORY_PATH.exists():
            try:
                readline.read_history_file(str(CLI_HISTORY_PATH))
            except Exception:
                pass
        readline.set_history_length(500)

        skill_ids = []
        try:
            mgr = SkillManager.get_instance(self.config_manager)
            skill_ids = [f"/{sid}" for sid in mgr.discover_skills().keys()]
        except Exception:
            pass
        all_cmds = sorted(set(SLASH_COMMANDS + skill_ids))

        def completer(text: str, state: int):
            if text.startswith("/"):
                matches = [c for c in all_cmds if c.startswith(text)]
                return matches[state] if state < len(matches) else None
            return None

        readline.set_completer_delims(" \t\n")
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")

    def _save_readline(self):
        if readline is None:
            return
        try:
            CLI_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(CLI_HISTORY_PATH))
        except Exception:
            pass

    def _read_user_input(self) -> Optional[str]:
        th = self.thread_manager.get_thread(self.active_thread_id)
        title = (th.get("title") if th else None) or self.active_thread_id[:8]
        attach_badge = f" [📎{len(self.pending_attachments)}]" if self.pending_attachments else ""
        prompt_str = f"\n[{title[:22]}]{attach_badge} ❯ "

        first = input(prompt_str)
        # Support explicit multi-line block with """ ... """
        if first.strip().startswith('"""') and not (first.strip().endswith('"""') and len(first.strip()) > 3):
            lines = [first.strip()[3:]]
            while True:
                nxt = input("... ")
                if nxt.strip().endswith('"""'):
                    lines.append(nxt.strip()[:-3])
                    break
                lines.append(nxt)
            return "\n".join(lines).strip()

        # Support trailing backslash continuation
        if first.endswith("\\"):
            lines = [first[:-1]]
            while True:
                nxt = input("... ")
                if nxt.endswith("\\"):
                    lines.append(nxt[:-1])
                else:
                    lines.append(nxt)
                    break
            return "\n".join(lines).strip()

        return first

    async def run_interactive(self):
        self._setup_readline()
        await self.startup(background=True)

        th = self.thread_manager.get_thread(self.active_thread_id)
        title = (th.get("title") if th else None) or "Nueva conversación"
        mcp_servers = self.config_manager.get_mcp_servers()
        mcp_status = self.mcp_manager.get_status_summary()
        connected_count = mcp_status.get("connected", 0)
        tool_count = len(self.mcp_manager.get_litellm_tools())
        skills_enabled = sum(
            1 for sk in SkillManager.get_instance(self.config_manager).discover_skills().values() if sk.enabled
        )
        ep_count = self.memory_manager.get_episodic_stats().get("total_episodes", 0)

        ws_profile = self.thread_manager.get_workspace_profile(self.active_thread_id) or self.workspace_profile
        ws_name = ws_profile.get("name") if isinstance(ws_profile, dict) else None
        ws_folders = (ws_profile.get("folders") or []) if isinstance(ws_profile, dict) else []
        ws_path = str(ws_folders[0]) if ws_folders else None

        self.renderer.print_banner(
            provider=self.config_manager.get_active_provider(),
            model=self.config_manager.get_active_model(),
            thread_title=title,
            thread_id=self.active_thread_id,
            mcp_connected=connected_count,
            mcp_total=len(mcp_servers),
            tool_count=tool_count,
            skills_enabled=skills_enabled,
            episode_count=ep_count,
            workspace_name=ws_name,
            workspace_path=ws_path,
        )

        loop = asyncio.get_running_loop()
        active_turn_task: Optional[asyncio.Task] = None

        def _on_sigint():
            if active_turn_task and not active_turn_task.done():
                self.agent.abort(self.active_thread_id)
                active_turn_task.cancel()
            else:
                raise KeyboardInterrupt()

        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        except NotImplementedError:
            pass

        try:
            while True:
                try:
                    raw_input_text = await loop.run_in_executor(None, self._read_user_input)
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    self.renderer.info("Usa /exit o Ctrl+D para salir.")
                    continue

                if raw_input_text is None:
                    break
                text = raw_input_text.strip()
                if not text:
                    continue

                if text.startswith("/"):
                    res = await self.command_handler.handle(text)
                    if res.should_exit:
                        break
                    if res.forward_prompt:
                        text = res.forward_prompt
                    elif res.handled:
                        continue

                active_turn_task = asyncio.create_task(self.execute_turn(text))
                try:
                    await active_turn_task
                except asyncio.CancelledError:
                    self.renderer.warning("Ejecución interrumpida.")
                finally:
                    active_turn_task = None
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except Exception:
                pass
            self._save_readline()
            await self.shutdown()

    async def run_one_shot(
        self,
        prompt: str,
        attachments: Optional[List[str]] = None,
    ) -> int:
        await self.startup()
        try:
            resolved_attachments: List[str] = []
            for a in attachments or []:
                p = Path(os.path.expanduser(a)).resolve()
                if p.exists() and p.is_file():
                    resolved_attachments.append(str(p))
                else:
                    self.renderer.warning(f"Archivo adjunto no encontrado: {a}")

            result = await self.execute_turn(prompt, attachments=resolved_attachments)
            if self.renderer.json_mode:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok", True) else 1
        finally:
            await self.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pa-cli",
        description="Pequeño Asistente CLI — Agente de escritorio MCP con memoria episódica, skills y multimodelo.",
    )
    parser.add_argument("-p", "--prompt", help="Ejecuta una consulta única (modo no interactivo) y sale.")
    parser.add_argument("-a", "--attach", action="append", default=[], help="Adjunta un archivo (imagen, audio, documento).")
    parser.add_argument("-t", "--thread", help="ID del hilo de conversación a usar o continuar.")
    parser.add_argument("-w", "--workspace", help="Ruta de carpeta o ID de workspace a usar (por defecto: carpeta actual).")
    parser.add_argument("--no-workspace", action="store_true", help="No vincular automáticamente la carpeta actual como workspace.")
    parser.add_argument("-m", "--model", help="Sobrescribe el modelo LLM activo para esta sesión.")
    parser.add_argument("--config", help="Ruta alternativa a config.json.")
    parser.add_argument("--no-mcp", action="store_true", help="No sincronizar servidores MCP externos al iniciar.")
    parser.add_argument("--heartbeat", action="store_true", help="Activar planificador de Heartbeat en modo interactivo.")
    parser.add_argument("--show-thinking", action="store_true", help="Mostrar razonamiento interno del modelo.")
    parser.add_argument("--raw", action="store_true", help="Salida en texto plano sin formato Rich (ideal para pipes).")
    parser.add_argument("--json", dest="json_mode", action="store_true", help="Salida estructurada en JSON (solo modo único).")
    parser.add_argument("-q", "--quiet", action="store_true", help="Ocultar banners y mensajes informativos.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mostrar logs INFO en stderr.")

    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser("chat", help="Inicia el modo interactivo (por defecto).")

    ask_p = subparsers.add_parser("ask", help="Consulta rápida no interactiva.")
    ask_p.add_argument("query", nargs="+", help="Texto de la consulta.")

    th_p = subparsers.add_parser("threads", help="Gestionar hilos de conversación.")
    th_p.add_argument("action", nargs="?", default="list", choices=["list", "new", "delete", "export"])
    th_p.add_argument("target", nargs="?", help="ID o título del hilo.")

    mcp_p = subparsers.add_parser("mcp", help="Inspeccionar o sincronizar servidores MCP.")
    mcp_p.add_argument("action", nargs="?", default="list", choices=["list", "tools", "sync", "toggle"])
    mcp_p.add_argument("target", nargs="?", help="ID del servidor MCP.")

    sk_p = subparsers.add_parser("skills", help="Listar o gestionar habilidades (skills).")
    sk_p.add_argument("action", nargs="?", default="list", choices=["list", "show", "toggle"])
    sk_p.add_argument("target", nargs="?", help="ID de la skill.")

    mem_p = subparsers.add_parser("memory", help="Consultar memoria episódica.")
    mem_p.add_argument("action", nargs="?", default="stats", choices=["stats", "search", "timeline", "remember", "facts"])
    mem_p.add_argument("query", nargs="*", help="Texto de búsqueda, fecha o hecho.")

    cfg_p = subparsers.add_parser("config", help="Ver o modificar configuración.")
    cfg_p.add_argument("action", nargs="?", default="show", choices=["show", "set", "provider", "model", "login"])
    cfg_p.add_argument("args", nargs="*", help="Clave y valor.")

    hb_p = subparsers.add_parser("heartbeat", help="Controlar Heartbeat y automatizaciones.")
    hb_p.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "run"])

    srv_p = subparsers.add_parser("server", help="Iniciar el servidor API FastAPI headless.")
    srv_p.add_argument("--host", default=None)
    srv_p.add_argument("--port", type=int, default=None)

    logs_p = subparsers.add_parser("logs", help="Ver registros en tiempo real.")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Seguir logs en tiempo real (tail -f).")
    logs_p.add_argument("-n", type=int, default=60, help="Número de líneas a mostrar.")

    return parser


async def _dispatch_subcommand(args: argparse.Namespace, cli: PequenoAsistenteCLI) -> int:
    sub = args.subcommand
    cmd = cli.command_handler

    if sub == "threads":
        if args.action == "list":
            await cmd.handle("/threads")
        elif args.action == "new":
            await cmd.handle(f"/new {args.target or ''}".strip())
        elif args.action == "delete":
            await cmd.handle(f"/delete {args.target or ''}".strip())
        elif args.action == "export":
            await cmd.handle(f"/export {args.target or ''}".strip())
        return 0

    if sub == "mcp":
        if args.action in ("tools", "sync"):
            await cli.startup()
        try:
            await cmd.handle(f"/mcp {args.action} {args.target or ''}".strip())
        finally:
            await cli.shutdown()
        return 0

    if sub == "skills":
        await cmd.handle(f"/skills {args.action} {args.target or ''}".strip())
        return 0

    if sub == "memory":
        extra = " ".join(args.query or [])
        await cmd.handle(f"/memory {args.action} {extra}".strip())
        return 0

    if sub == "config":
        extra = " ".join(args.args or [])
        if args.action in ("show", "set"):
            await cmd.handle(f"/config {args.action} {extra}".strip())
        else:
            await cmd.handle(f"/{args.action} {extra}".strip())
        return 0

    if sub == "heartbeat":
        if args.action == "run":
            await cli.startup()
        try:
            await cmd.handle(f"/heartbeat {args.action}")
        finally:
            await cli.shutdown()
        return 0

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_cli_logging(verbose=args.verbose)

    if args.subcommand == "server":
        from pequeno_asistente.server import run_server
        run_server(host=args.host, port=args.port)
        return 0

    if args.subcommand == "logs":
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE_PATH.touch(exist_ok=True)
        if args.follow:
            try:
                subprocess.run(["tail", "-n", str(args.n), "-f", str(LOG_FILE_PATH)])
            except KeyboardInterrupt:
                pass
        else:
            lines = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-args.n:]))
        return 0

    # Check piped stdin when no subcommand/prompt is given
    piped_prompt: Optional[str] = None
    if not args.prompt and not args.subcommand and not sys.stdin.isatty():
        piped_data = sys.stdin.read().strip()
        if piped_data:
            piped_prompt = piped_data

    renderer = CLIRenderer(
        raw=args.raw,
        json_mode=args.json_mode,
        quiet=args.quiet,
        show_thinking=args.show_thinking,
    )

    cli = PequenoAsistenteCLI(
        config_path=args.config,
        renderer=renderer,
        sync_mcp=not args.no_mcp,
        model_override=args.model,
        thread_id=args.thread,
        enable_heartbeat=args.heartbeat,
        workspace=args.workspace,
        use_cwd_workspace=not args.no_workspace,
    )

    if args.subcommand == "ask":
        prompt_text = " ".join(args.query)
        return asyncio.run(cli.run_one_shot(prompt_text, attachments=args.attach))

    if args.prompt or piped_prompt:
        prompt_text = args.prompt or piped_prompt or ""
        return asyncio.run(cli.run_one_shot(prompt_text, attachments=args.attach))

    if args.subcommand and args.subcommand != "chat":
        return asyncio.run(_dispatch_subcommand(args, cli))

    asyncio.run(cli.run_interactive())
    return 0
