"""
Rich Terminal Renderer for Pequeño Asistente CLI.
Supports Rich Markdown streaming, raw/pipe output, structured JSON output,
and clean tables for threads, MCP servers/tools, skills, memory, and automations.
"""
import sys
import json
import threading
from typing import Dict, List, Any, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner
from rich import box


class CLIRenderer:
    """Handles all terminal output formatting for Pequeño Asistente CLI."""

    def __init__(
        self,
        raw: bool = False,
        json_mode: bool = False,
        quiet: bool = False,
        show_thinking: bool = False,
    ):
        self.raw = raw
        self.json_mode = json_mode
        self.quiet = quiet
        self.show_thinking = show_thinking
        self.console = Console(stderr=False, highlight=True)
        self.err_console = Console(stderr=True, highlight=False)
        self._live: Optional[Live] = None
        self._live_lock = threading.Lock()

    def pause_live(self):
        with self._live_lock:
            if self._live is not None:
                try:
                    self._live.stop()
                except Exception:
                    pass

    def resume_live(self):
        with self._live_lock:
            if self._live is not None:
                try:
                    self._live.start()
                except Exception:
                    pass

    def info(self, msg: str):
        if self.raw or self.json_mode or self.quiet:
            return
        self.console.print(f"[cyan]ℹ[/cyan] {msg}")

    def success(self, msg: str):
        if self.raw or self.json_mode or self.quiet:
            return
        self.console.print(f"[green]✓[/green] {msg}")

    def warning(self, msg: str):
        if self.json_mode:
            return
        if self.raw:
            print(f"AVISO: {msg}", file=sys.stderr)
            return
        self.err_console.print(f"[yellow]⚠[/yellow] {msg}")

    def error(self, msg: str):
        if self.json_mode:
            return
        if self.raw:
            print(f"ERROR: {msg}", file=sys.stderr)
            return
        self.err_console.print(f"[red]✗[/red] {msg}")

    def print_banner(
        self,
        provider: str,
        model: str,
        thread_title: str,
        thread_id: str,
        mcp_connected: int,
        mcp_total: int,
        tool_count: int,
        skills_enabled: int,
        episode_count: int,
        workspace_name: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ):
        if self.raw or self.json_mode or self.quiet:
            return
        grid = Table.grid(expand=True)
        grid.add_column(style="bold cyan", ratio=1)
        grid.add_column(justify="right", style="dim")

        grid.add_row(
            "🤖 Pequeño Asistente CLI",
            f"Hilo: [white]{thread_title}[/white] ({thread_id[:8]})",
        )
        grid.add_row(
            f"[dim]Proveedor:[/dim] [green]{provider}[/green]  [dim]Modelo:[/dim] [yellow]{model}[/yellow]",
            f"[dim]MCP:[/dim] {mcp_connected}/{mcp_total} ({tool_count} tools) · [dim]Skills:[/dim] {skills_enabled} · [dim]Memorias:[/dim] {episode_count}",
        )
        if workspace_name or workspace_path:
            ws_label = f"[magenta]{workspace_name}[/magenta]" if workspace_name else ""
            path_label = f"[dim]({workspace_path})[/dim]" if workspace_path else ""
            sep = " " if (ws_label and path_label) else ""
            grid.add_row(
                f"[dim]Workspace:[/dim] {ws_label}{sep}{path_label}",
                "",
            )
        panel = Panel(
            grid,
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
            subtitle="[dim]Escribe /help para comandos · @archivo para adjuntar · Ctrl+C aborta · Ctrl+D sale[/dim]",
        )
        self.console.print(panel)

    def create_turn_session(self) -> "TurnStreamDisplay":
        return TurnStreamDisplay(self)

    def print_threads_table(self, threads: List[Dict[str, Any]], active_id: Optional[str]):
        if not threads:
            self.info("No hay hilos de conversación guardados.")
            return
        table = Table(title="💬 Hilos de Conversación", box=box.ROUNDED, show_lines=False)
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("ID", style="cyan", width=10)
        table.add_column("Título", style="bold white")
        table.add_column("Msgs", justify="right", style="green", width=5)
        table.add_column("Actualizado", style="dim", width=14)

        from pequeno_asistente.agent.thread_manager import format_relative_timestamp

        for idx, th in enumerate(threads, 1):
            tid = th.get("id", "")
            is_active = tid == active_id
            marker = "▶ " if is_active else ""
            title = f"{marker}{th.get('title', 'Nueva conversación')}"
            if is_active:
                title = f"[bold green]{title}[/bold green]"
            table.add_row(
                str(idx),
                tid[:8],
                title,
                str(th.get("message_count", 0)),
                format_relative_timestamp(th.get("updated_at", "")),
            )
        self.console.print(table)

    def print_mcp_table(self, servers_config: Dict[str, Any], status_summary: Dict[str, Any]):
        table = Table(title="🔌 Servidores MCP", box=box.ROUNDED)
        table.add_column("ID", style="cyan")
        table.add_column("Nombre", style="white")
        table.add_column("Transporte", style="dim")
        table.add_column("Estado", width=14)
        table.add_column("Tools", justify="right", style="green", width=6)

        servers_map = status_summary.get("servers", {}) if isinstance(status_summary, dict) else {}
        for sid, cfg in servers_config.items():
            st = servers_map.get(sid, {})
            disabled = cfg.get("disabled", False)
            status_val = st.get("status", "disconnected")
            err = st.get("error")
            if disabled:
                state_str = "[dim]Desactivado[/dim]"
            elif status_val == "connected":
                state_str = "[green]● Conectado[/green]"
            elif status_val == "connecting":
                state_str = "[yellow]◌ Conectando...[/yellow]"
            elif err or status_val == "error":
                state_str = f"[red]✗ Error[/red]"
            else:
                state_str = "[yellow]○ Pendiente[/yellow]"

            table.add_row(
                sid,
                cfg.get("name", sid),
                cfg.get("transport", "stdio"),
                state_str,
                str(st.get("tools_count", 0)),
            )
        self.console.print(table)

    def print_mcp_tools(self, tools: List[Dict[str, Any]], server_filter: Optional[str] = None):
        table = Table(title="🛠 Herramientas MCP Disponibles", box=box.ROUNDED)
        table.add_column("Herramienta", style="cyan")
        table.add_column("Descripción", style="white")
        count = 0
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            if server_filter and not name.startswith(f"{server_filter}__"):
                continue
            desc = (fn.get("description") or "").replace("\n", " ").strip()
            if len(desc) > 100:
                desc = desc[:97] + "..."
            table.add_row(name, desc)
            count += 1
        if count == 0:
            self.info("No se encontraron herramientas MCP para ese filtro.")
        else:
            self.console.print(table)

    def print_skills_table(self, skills: List[Any]):
        if not skills:
            self.info("No se detectaron habilidades (skills).")
            return
        table = Table(title="⚡ Habilidades (Skills)", box=box.ROUNDED)
        table.add_column("ID", style="cyan")
        table.add_column("Nombre", style="bold white")
        table.add_column("Origen", style="dim")
        table.add_column("Estado", width=12)
        table.add_column("Descripción", style="white")

        for sk in sorted(skills, key=lambda s: s.id):
            state = "[green]Activa[/green]" if sk.enabled else "[dim]Inactiva[/dim]"
            desc = (sk.description or "").replace("\n", " ").strip()
            if len(desc) > 70:
                desc = desc[:67] + "..."
            table.add_row(sk.id, sk.name, sk.source_label, state, desc)
        self.console.print(table)

    def print_models_table(self, models: List[Dict[str, Any]], active_model: str, limit: int = 40):
        if not models:
            self.info("No hay modelos disponibles para mostrar.")
            return
        table = Table(title=f"🧠 Modelos ({min(len(models), limit)}/{len(models)})", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("ID del Modelo", style="cyan")
        table.add_column("Detalles", style="dim")

        for idx, m in enumerate(models[:limit], 1):
            mid = m.get("id", "")
            label = f"[bold green]▶ {mid}[/bold green]" if mid == active_model else mid
            table.add_row(str(idx), label, m.get("description", ""))
        self.console.print(table)

    def print_automations_table(self, automations: List[Dict[str, Any]], hb_cfg: Dict[str, Any]):
        hb_state = "[green]Activo[/green]" if hb_cfg.get("enabled") else "[dim]Inactivo[/dim]"
        self.console.print(
            Panel(
                f"Estado Heartbeat Global: {hb_state} · Intervalo: {hb_cfg.get('interval_minutes', 30)} min · "
                f"Horario activo: {hb_cfg.get('active_start', '08:00')} - {hb_cfg.get('active_end', '23:00')} · "
                f"Último estado: [cyan]{hb_cfg.get('last_status', 'idle')}[/cyan]",
                title="💓 Heartbeat Proactivo",
                box=box.ROUNDED,
            )
        )
        if not automations:
            self.info("No hay automatizaciones programadas.")
            return
        table = Table(title="⏱ Automatizaciones Programadas", box=box.ROUNDED)
        table.add_column("ID", style="cyan", width=10)
        table.add_column("Nombre", style="bold white")
        table.add_column("Tipo", style="dim", width=10)
        table.add_column("Estado", width=10)
        table.add_column("Último run", style="dim", width=16)

        for a in automations:
            st = "[green]Activa[/green]" if a.get("enabled", True) else "[dim]Pausada[/dim]"
            last_run = (a.get("last_run") or "Nunca")[:16].replace("T", " ")
            table.add_row(
                a.get("id", "")[:8],
                a.get("name", "Sin nombre"),
                a.get("schedule_type", "interval"),
                st,
                last_run,
            )
        self.console.print(table)

    def print_history(self, messages: List[Dict[str, Any]], limit: int = 20):
        if not messages:
            self.info("El hilo actual está vacío.")
            return
        slice_msgs = messages[-limit:] if limit > 0 else messages
        for msg in slice_msgs:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if role == "user":
                self.console.print(Panel(Text(str(content)), title="Tú", border_style="blue", box=box.ROUNDED))
            elif role == "assistant":
                if content:
                    self.console.print(
                        Panel(Markdown(str(content)), title="Pequeño Asistente", border_style="green", box=box.ROUNDED)
                    )
            elif role == "tool":
                name = msg.get("name", "tool")
                preview = str(content)[:160].replace("\n", " ")
                self.console.print(f"[dim]  ⚙ {name} → {preview}[/dim]")


class TurnStreamDisplay:
    """Manages live streaming output for a single agent turn."""

    def __init__(self, renderer: CLIRenderer):
        self.renderer = renderer
        self.text_parts: List[str] = []
        self.thinking_parts: List[str] = []
        self.tool_events: List[Dict[str, Any]] = []
        self.active_tool: Optional[str] = None
        self.error_msg: Optional[str] = None
        self._live: Optional[Live] = None
        self._lock = threading.Lock()
        self._raw_thinking_started = False

    def start(self):
        if self.renderer.raw or self.renderer.json_mode:
            return
        self._live = Live(
            self._build_renderable(),
            console=self.renderer.console,
            refresh_per_second=12,
            transient=False,
        )
        with self.renderer._live_lock:
            self.renderer._live = self._live
        self._live.start()

    def _build_renderable(self):
        items = []
        thinking_text = "".join(self.thinking_parts).strip()
        if self.renderer.show_thinking and thinking_text:
            items.append(
                Panel(
                    Text(thinking_text, style="dim italic"),
                    title="[dim]💭 Razonamiento[/dim]",
                    border_style="dim",
                    box=box.ROUNDED,
                )
            )

        for ev in self.tool_events:
            if ev["done"]:
                items.append(Text(f"  ✓ {ev['name']}", style="dim green"))
            else:
                items.append(
                    Group(
                        Spinner("dots", text=Text(f" Ejecutando {ev['name']}{ev['args_preview']}...", style="yellow"))
                    )
                )

        body = "".join(self.text_parts)
        if body:
            items.append(Markdown(body))
        elif not self.tool_events and not (self.renderer.show_thinking and thinking_text):
            items.append(Spinner("dots", text=Text(" Pensando...", style="cyan")))

        return Group(*items) if items else Text("")

    def _refresh(self):
        if self._live is not None:
            try:
                self._live.update(self._build_renderable(), refresh=True)
            except Exception:
                pass

    def on_token(self, token: str):
        with self._lock:
            self.text_parts.append(token)
            if self.renderer.raw:
                if self._raw_thinking_started:
                    sys.stdout.write("\n</thinking>\n")
                    self._raw_thinking_started = False
                sys.stdout.write(token)
                sys.stdout.flush()
            elif not self.renderer.json_mode:
                self._refresh()

    def on_thinking(self, chunk: str):
        with self._lock:
            self.thinking_parts.append(chunk)
            if self.renderer.raw and self.renderer.show_thinking:
                if not self._raw_thinking_started:
                    sys.stdout.write("<thinking>\n")
                    self._raw_thinking_started = True
                sys.stdout.write(chunk)
                sys.stdout.flush()
            elif not self.renderer.json_mode and self.renderer.show_thinking:
                self._refresh()

    def on_tool_start(self, name: str, args: Dict[str, Any]):
        with self._lock:
            preview = ""
            if isinstance(args, dict) and args:
                kv = ", ".join(f"{k}={repr(v)[:30]}" for k, v in list(args.items())[:2])
                preview = f"({kv})"
            self.tool_events.append({"name": name, "args": args, "args_preview": preview, "done": False})
            self.active_tool = name
            if self.renderer.raw and not self.renderer.quiet:
                print(f"\n[tool:start] {name}{preview}", file=sys.stderr, flush=True)
            elif not self.renderer.json_mode:
                self._refresh()

    def on_tool_end(self, name: str, result: str):
        with self._lock:
            for ev in reversed(self.tool_events):
                if ev["name"] == name and not ev["done"]:
                    ev["done"] = True
                    ev["result_preview"] = str(result)[:200]
                    break
            self.active_tool = None
            if self.renderer.raw and not self.renderer.quiet:
                print(f"[tool:end] {name}", file=sys.stderr, flush=True)
            elif not self.renderer.json_mode:
                self._refresh()

    def on_complete(self, final_text: str):
        with self._lock:
            if not self.text_parts and final_text:
                self.text_parts.append(final_text)
                if self.renderer.raw:
                    sys.stdout.write(final_text)
            if self.renderer.raw:
                if self._raw_thinking_started:
                    sys.stdout.write("\n</thinking>\n")
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif not self.renderer.json_mode:
                self._refresh()

    def on_error(self, err: str):
        with self._lock:
            self.error_msg = err
            if self.renderer.raw:
                print(f"\nERROR: {err}", file=sys.stderr, flush=True)

    def finish(self):
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            with self.renderer._live_lock:
                self.renderer._live = None
            self._live = None
        if self.error_msg and not self.renderer.json_mode and not self.renderer.raw:
            self.renderer.error(self.error_msg)

    def to_json_dict(self, thread_id: str, model: str) -> Dict[str, Any]:
        return {
            "ok": self.error_msg is None,
            "thread_id": thread_id,
            "model": model,
            "response": "".join(self.text_parts),
            "thinking": "".join(self.thinking_parts) if self.renderer.show_thinking else "",
            "tools": [
                {"name": t["name"], "args": t["args"], "done": t["done"]}
                for t in self.tool_events
            ],
            "error": self.error_msg,
        }
