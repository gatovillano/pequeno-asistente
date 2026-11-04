"""
TerminalView: Full-featured Interactive PTY Terminal for Pequeño Asistente.
Runs a real Linux pseudo-terminal (PTY) session with bash, allowing full
interactive command execution including sudo with password authentication,
readline navigation, Tab autocompletion, ANSI color rendering, and quick shortcuts.
Inspired by KogniTerm's interactive terminal architecture.
"""
import os
import re
import pty
import fcntl
import termios
import struct
import signal
import shlex
import threading
import logging
from typing import Optional, Callable, Dict, Tuple, List
from pathlib import Path

import pyte
from pyte.screens import Char

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk

logger = logging.getLogger("pequeno_asistente.terminal")

# Catppuccin Mocha / Modern Terminal Palette (Dark)
TERMINAL_COLORS: Dict[str, str] = {
    'black': '#1e1e2e',
    'red': '#f38ba8',
    'green': '#a6e3a1',
    'yellow': '#f9e2af',
    'brown': '#f9e2af',
    'blue': '#89b4fa',
    'magenta': '#f5c2e7',
    'cyan': '#94e2d5',
    'white': '#cdd6f4',
    'brightblack': '#585b70',
    'brightred': '#eba0ac',
    'brightgreen': '#a6e3a1',
    'brightyellow': '#f9e2af',
    'brightblue': '#89dceb',
    'brightmagenta': '#f5c2e7',
    'brightcyan': '#94e2d5',
    'brightwhite': '#ffffff',
}

# Catppuccin Latte / Modern Terminal Palette (Light)
TERMINAL_COLORS_LIGHT: Dict[str, str] = {
    'black': '#4c4f69',
    'red': '#d20f39',
    'green': '#16a34a',
    'yellow': '#d97706',
    'brown': '#b45309',
    'blue': '#1d4ed8',
    'magenta': '#9333ea',
    'cyan': '#0d9488',
    'white': '#1e293b',
    'brightblack': '#64748b',
    'brightred': '#e11d48',
    'brightgreen': '#15803d',
    'brightyellow': '#b45309',
    'brightblue': '#2563eb',
    'brightmagenta': '#7e22ce',
    'brightcyan': '#0f766e',
    'brightwhite': '#0f172a',
}

class PtySession:
    """
    Manages a persistent background Pseudo-Terminal (PTY) running bash.
    Provides non-blocking I/O, window resizing, signal interrupts, and safe lifecycle.
    """
    def __init__(self, cols: int = 80, rows: int = 24, cwd: Optional[str] = None):
        self.cols = cols
        self.rows = rows
        self.cwd = cwd or str(Path.home())
        self.master_fd: Optional[int] = None
        self.proc: Optional[any] = None
        self.is_running = False
        self._reader_thread: Optional[threading.Thread] = None
        self.on_data: Optional[Callable[[str], None]] = None
        self.on_exit: Optional[Callable[[int], None]] = None

    def start(self):
        """Spawns the shell inside a new PTY pair."""
        if self.is_running:
            return

        try:
            self.master_fd, slave_fd = pty.openpty()

            # Set initial winsize
            self._set_winsize(self.master_fd, self.rows, self.cols)

            # Configure slave terminal attributes
            try:
                attrs = termios.tcgetattr(slave_fd)
                attrs[1] = attrs[1] | termios.OPOST | termios.ONLCR
                attrs[3] = attrs[3] | termios.ECHO
                termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
            except Exception as e:
                logger.warning(f"Failed to set termios on slave: {e}")

            # Prepare environment
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["PAGER"] = "cat"
            env["MANPAGER"] = "cat"
            env["LESS"] = "-R"

            # Inject askpass for sudo -A or GUI sudo authentication
            try:
                from pequeno_asistente.security.manager import CredentialManager
                askpass_path = CredentialManager.get_instance().get_askpass_path()
                env["SUDO_ASKPASS"] = askpass_path
                env["SSH_ASKPASS"] = askpass_path
            except Exception as e:
                logger.debug(f"Could not configure askpass in terminal environment: {e}")

            shell_path = os.environ.get("SHELL", "/bin/bash")
            shell_name = os.path.basename(shell_path)

            import subprocess
            self.proc = subprocess.Popen(
                [shell_path, "--login"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid,
                cwd=self.cwd,
                env=env
            )
            os.close(slave_fd)

            # Make master_fd non-blocking
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            self.is_running = True

            # Start background reader thread
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            logger.info(f"PTY session started (PID: {self.proc.pid}, shell: {shell_name})")

        except Exception as e:
            logger.error(f"Error starting PTY session: {e}", exc_info=True)
            self.close()

    def _set_winsize(self, fd: int, rows: int, cols: int):
        try:
            buf = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, buf)
        except Exception:
            pass

    def resize(self, cols: int, rows: int):
        """Updates terminal columns and rows."""
        self.cols = max(20, cols)
        self.rows = max(5, rows)
        if self.master_fd is not None and self.is_running:
            self._set_winsize(self.master_fd, self.rows, self.cols)
            if self.proc and self.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGWINCH)
                except Exception:
                    pass

    def write(self, data: bytes | str):
        """Sends data directly into the PTY master fd."""
        if not self.is_running or self.master_fd is None:
            return

        if isinstance(data, str):
            data = data.encode("utf-8")

        try:
            os.write(self.master_fd, data)
        except Exception as e:
            logger.warning(f"Error writing to PTY: {e}")

    def interrupt(self):
        """Sends SIGINT (Ctrl+C) to the foreground process."""
        self.write(b"\x03")

    def _read_loop(self):
        """Dedicated background thread reading master_fd output chunks."""
        import select
        while self.is_running and self.master_fd is not None:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.04)
                if not r:
                    if self.proc and self.proc.poll() is not None:
                        break
                    continue

                chunk = os.read(self.master_fd, 8192)
                if not chunk:
                    break

                text = chunk.decode("utf-8", errors="replace")
                if self.on_data and text:
                    self.on_data(text)

            except OSError:
                break
            except Exception as e:
                logger.error(f"PTY read error: {e}")
                break

        exit_code = 0
        if self.proc:
            try:
                exit_code = self.proc.poll() or 0
            except Exception:
                pass

        self.is_running = False
        if self.on_exit:
            self.on_exit(exit_code)

    def close(self):
        """Terminates shell process and closes PTY descriptors."""
        self.is_running = False
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                self.proc.terminate()
            except Exception:
                pass

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None


class TerminalView(Gtk.Box):
    """
    Interactive Terminal Widget matching Pequeño Asistente's visual aesthetic.
    Supports real-time typing, ANSI colors, sudo interaction, bash auto-complete,
    quick action chips, clipboard shortcuts, and seamless chat switching.
    """
    def __init__(self, on_back_to_chat: Optional[Callable[[], None]] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("terminal-view-container")
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.on_back_to_chat = on_back_to_chat

        # Virtual Screen State
        self.cols = 70
        self.rows = 24
        self.screen = pyte.HistoryScreen(self.cols, self.rows, history=3000)
        self.stream = pyte.Stream(self.screen)

        # Rendering & Throttling
        self._dirty = False
        self._render_source_id: Optional[int] = None
        self._known_tags: Dict[str, bool] = {}

        # 1. Build Top Action Bar
        self.top_bar = self._build_top_bar()
        self.append(self.top_bar)

        # 2. Build Terminal Scrolled Screen
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_hexpand(True)
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        self.scrolled.add_css_class("terminal-scrolled-window")

        self.tag_table = Gtk.TextTagTable()
        self.text_buffer = Gtk.TextBuffer(tag_table=self.tag_table)
        self._setup_base_tags()

        self.text_view = Gtk.TextView(buffer=self.text_buffer)
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(True)
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_view.set_top_margin(10)
        self.text_view.set_bottom_margin(10)
        self.text_view.add_css_class("terminal-text-view")
        self.scrolled.set_child(self.text_view)
        self.append(self.scrolled)

        # 3. Build Bottom Quick Actions & Sudo Chips
        self.bottom_bar = self._build_bottom_bar()
        self.append(self.bottom_bar)

        # Keyboard controller for terminal interaction
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.text_view.add_controller(key_controller)

        # Global click to focus
        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", lambda _g, _n, _x, _y: self.text_view.grab_focus())
        self.text_view.add_controller(click_gesture)

        # 4. Start PTY Session
        self.session = PtySession(cols=self.cols, rows=self.rows)
        self.session.on_data = self._on_pty_data
        self.session.on_exit = self._on_pty_exit
        self.session.start()

        # Update PID badge
        if self.session.proc:
            self.pid_badge.set_label(f"● bash ({self.session.proc.pid})")
            self.pid_badge.add_css_class("terminal-badge-running")

    def _setup_base_tags(self):
        """Pre-creates standard ANSI and syntax tags."""
        base_colors = {
            "default": "#cdd6f4",
            **TERMINAL_COLORS
        }
        for name, hex_code in base_colors.items():
            # Standard fg
            tag = Gtk.TextTag(name=f"fg_{name}")
            tag.set_property("foreground", hex_code)
            self.tag_table.add(tag)

            # Bold fg
            tag_b = Gtk.TextTag(name=f"fg_{name}_bold")
            tag_b.set_property("foreground", hex_code)
            tag_b.set_property("weight", 700)
            self.tag_table.add(tag_b)

        # Special styles
        tag_bold = Gtk.TextTag(name="bold")
        tag_bold.set_property("weight", 700)
        self.tag_table.add(tag_bold)

        tag_under = Gtk.TextTag(name="underline")
        tag_under.set_property("underline", Pango.Underline.SINGLE)
        self.tag_table.add(tag_under)

        tag_italic = Gtk.TextTag(name="italic")
        tag_italic.set_property("style", Pango.Style.ITALIC)
        self.tag_table.add(tag_italic)

    def set_theme(self, is_light: bool):
        """Updates ANSI and default terminal text tag colors for dark or light mode."""
        palette = TERMINAL_COLORS_LIGHT if is_light else TERMINAL_COLORS
        base_colors = {
            "default": "#1e293b" if is_light else "#cdd6f4",
            **palette
        }
        for name, hex_code in base_colors.items():
            tag = self.tag_table.lookup(f"fg_{name}")
            if tag:
                tag.set_property("foreground", hex_code)
            tag_b = self.tag_table.lookup(f"fg_{name}_bold")
            if tag_b:
                tag_b.set_property("foreground", hex_code)
        self.text_view.queue_draw()

    def _get_tag_name(self, char: Char) -> Optional[str]:
        """Resolves pyte character formatting to a GTK TextTag name."""
        fg = char.fg or "default"
        if fg not in TERMINAL_COLORS and fg != "default":
            # Hex color or fallback
            if isinstance(fg, str) and re.match(r"^[0-9a-fA-F]{6}$", fg):
                custom_name = f"hex_{fg}"
                if custom_name not in self._known_tags:
                    try:
                        ctag = Gtk.TextTag(name=custom_name)
                        ctag.set_property("foreground", f"#{fg}")
                        if char.bold:
                            ctag.set_property("weight", 700)
                        self.tag_table.add(ctag)
                        self._known_tags[custom_name] = True
                    except Exception:
                        pass
                return custom_name
            fg = "default"

        if char.bold:
            return f"fg_{fg}_bold"
        elif fg != "default":
            return f"fg_{fg}"
        elif char.underscore:
            return "underline"
        elif char.italics:
            return "italic"
        return None

    def _build_top_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("terminal-top-bar")

        # Left: Terminal Icon + Title + PID
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_valign(Gtk.Align.CENTER)

        term_icon = Gtk.Label(label="💻")
        title_box.append(term_icon)

        title_lbl = Gtk.Label(label="<b>Terminal Interactiva</b>", use_markup=True)
        title_lbl.add_css_class("terminal-title-label")
        title_box.append(title_lbl)

        self.pid_badge = Gtk.Label(label="● iniciando...")
        self.pid_badge.add_css_class("terminal-pid-badge")
        title_box.append(self.pid_badge)
        bar.append(title_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        # Right: Quick Tool Buttons
        # 1. Interrupt button (Ctrl+C)
        self.interrupt_btn = Gtk.Button()
        ibox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        ibox.append(Gtk.Label(label="⏹"))
        ibox.append(Gtk.Label(label="Ctrl+C"))
        self.interrupt_btn.set_child(ibox)
        self.interrupt_btn.add_css_class("flat")
        self.interrupt_btn.add_css_class("terminal-action-btn")
        self.interrupt_btn.set_tooltip_text("Interrumpir proceso actual (SIGINT)")
        self.interrupt_btn.connect("clicked", lambda _b: self.session.interrupt())
        bar.append(self.interrupt_btn)

        # 2. Paste button
        paste_btn = Gtk.Button()
        pbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pbox.append(Gtk.Label(label="📋"))
        pbox.append(Gtk.Label(label="Pegar"))
        paste_btn.set_child(pbox)
        paste_btn.add_css_class("flat")
        paste_btn.add_css_class("terminal-action-btn")
        paste_btn.set_tooltip_text("Pegar contenido del portapapeles (Ctrl+Shift+V)")
        paste_btn.connect("clicked", lambda _b: self._paste_from_clipboard())
        bar.append(paste_btn)

        # 3. Clear button
        clear_btn = Gtk.Button()
        cbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        cbox.append(Gtk.Label(label="🧹"))
        cbox.append(Gtk.Label(label="Limpiar"))
        clear_btn.set_child(cbox)
        clear_btn.add_css_class("flat")
        clear_btn.add_css_class("terminal-action-btn")
        clear_btn.set_tooltip_text("Limpiar pantalla (Ctrl+L)")
        clear_btn.connect("clicked", lambda _b: self._on_clear_clicked())
        bar.append(clear_btn)

        # 4. Restart Shell button
        restart_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        restart_btn.add_css_class("flat")
        restart_btn.add_css_class("terminal-action-btn")
        restart_btn.set_tooltip_text("Reiniciar sesión de terminal")
        restart_btn.connect("clicked", lambda _b: self.restart_session())
        bar.append(restart_btn)

        # 5. Back to Chat button
        back_btn = Gtk.Button()
        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bbox.append(Gtk.Label(label="💬"))
        bbox.append(Gtk.Label(label="Chat"))
        back_btn.set_child(bbox)
        back_btn.add_css_class("flat")
        back_btn.add_css_class("terminal-back-btn")
        back_btn.set_tooltip_text("Volver a la vista de conversación (Ctrl+T)")
        back_btn.connect("clicked", lambda _b: self._on_back_clicked())
        bar.append(back_btn)

        return bar

    def _build_bottom_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bar.add_css_class("terminal-bottom-bar")

        # Row with quick sudo commands & shortcuts
        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chips_box.set_halign(Gtk.Align.START)

        chips_lbl = Gtk.Label(label="Comandos rápidos:")
        chips_lbl.add_css_class("terminal-quick-label")
        chips_box.append(chips_lbl)

        # Sudo chip
        sudo_chip = self._create_cmd_chip("🛡️ sudo", "sudo ")
        chips_box.append(sudo_chip)

        # Apt update
        apt_chip = self._create_cmd_chip("📦 sudo apt update", "sudo apt update\n")
        chips_box.append(apt_chip)

        # Systemctl
        sys_chip = self._create_cmd_chip("⚙️ systemctl", "sudo systemctl ")
        chips_box.append(sys_chip)

        # Htop
        htop_chip = self._create_cmd_chip("📊 htop", "htop\n")
        chips_box.append(htop_chip)

        # Ls -la
        ls_chip = self._create_cmd_chip("📂 ls -la", "ls -la\n")
        chips_box.append(ls_chip)

        scrolled_chips = Gtk.ScrolledWindow()
        scrolled_chips.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scrolled_chips.set_child(chips_box)
        bar.append(scrolled_chips)

        # Helper info row
        info_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        info_lbl = Gtk.Label(label="💡 Tip: Interactúa directamente escribiendo o autenticándote con sudo.")
        info_lbl.add_css_class("terminal-info-tip")
        info_lbl.set_hexpand(True)
        info_lbl.set_xalign(0)
        info_row.append(info_lbl)

        bar.append(info_row)
        return bar

    def _create_cmd_chip(self, label: str, command: str) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.add_css_class("terminal-quick-chip")
        btn.set_tooltip_text(f"Enviar '{command.strip()}' a la terminal")
        btn.connect("clicked", lambda _b: self.send_input(command))
        return btn

    def _on_clear_clicked(self):
        """Clears virtual screen and terminal display."""
        self.screen.reset()
        self.session.write(b"\x0c")
        self.text_buffer.set_text("")

    def _on_back_clicked(self):
        if self.on_back_to_chat:
            self.on_back_to_chat()

    def send_input(self, text: str):
        """Sends text directly to the PTY and regains focus."""
        self.session.write(text)
        self.text_view.grab_focus()

    def restart_session(self):
        """Restarts the PTY shell process."""
        self.session.close()
        self.screen.reset()
        self.text_buffer.set_text("")
        self.session = PtySession(cols=self.cols, rows=self.rows)
        self.session.on_data = self._on_pty_data
        self.session.on_exit = self._on_pty_exit
        self.session.start()
        if self.session.proc:
            self.pid_badge.set_label(f"● bash ({self.session.proc.pid})")
            self.pid_badge.remove_css_class("terminal-badge-stopped")
            self.pid_badge.add_css_class("terminal-badge-running")
        self.text_view.grab_focus()

    def _paste_from_clipboard(self):
        """Reads text from GTK/Wayland clipboard and forwards it to the PTY."""
        display = Gdk.Display.get_default()
        if not display:
            return
        clipboard = display.get_clipboard()

        def _on_paste_finished(_cb, res):
            try:
                text = clipboard.read_text_finish(res)
                if text:
                    self.session.write(text)
            except Exception as e:
                logger.warning(f"Error reading clipboard: {e}")

        clipboard.read_text_async(None, _on_paste_finished)

    def _on_pty_data(self, text: str):
        """Called from PTY reader thread with new text chunks."""
        # Feed stream in thread-safe manner
        try:
            self.stream.feed(text)
            self._dirty = True
            GLib.idle_add(self._trigger_render)
        except Exception as e:
            logger.error(f"Error feeding terminal stream: {e}")

    def _trigger_render(self):
        """Schedules debounced render on GTK main loop."""
        if not self._render_source_id:
            self._render_source_id = GLib.timeout_add(20, self._render_screen)
        return False

    def _render_screen(self):
        """Re-renders the virtual screen buffer into Gtk.TextBuffer with ANSI tags."""
        self._render_source_id = None
        if not self._dirty:
            return False
        self._dirty = False

        # Gather history + active screen lines
        all_lines: List[Tuple[any, int]] = []

        # 1. History lines (from top scrollback)
        for h_line in self.screen.history.top:
            all_lines.append((h_line, -1))

        # 2. Active screen lines
        for y in range(self.screen.lines):
            line = self.screen.buffer[y]
            all_lines.append((line, y))

        # Trim empty lines at the very bottom
        cursor_abs_y = len(self.screen.history.top) + self.screen.cursor.y
        while len(all_lines) > (cursor_abs_y + 1):
            last_line, last_y = all_lines[-1]
            # Check if empty
            is_empty = True
            for x in range(self.screen.columns):
                c = last_line[x]
                if c.data != ' ' or c.bg != 'default' or c.reverse:
                    is_empty = False
                    break
            if is_empty:
                all_lines.pop()
            else:
                break

        # Wipe TextBuffer and populate with styled runs
        self.text_buffer.set_text("")
        iter_end = self.text_buffer.get_end_iter()

        for line, y_idx in all_lines:
            # Determine line width (trim trailing default blanks)
            max_x = 0
            for x in range(self.screen.columns - 1, -1, -1):
                c = line[x]
                if c.data != ' ' or c.bg != 'default' or c.reverse or c.underscore:
                    max_x = x + 1
                    break

            if y_idx == self.screen.cursor.y:
                max_x = max(max_x, self.screen.cursor.x)

            # Build runs of matching tags
            current_tag = None
            current_text = []

            for x in range(max_x):
                c = line[x]
                tag = self._get_tag_name(c)
                if tag == current_tag:
                    current_text.append(c.data)
                else:
                    if current_text:
                        txt_chunk = ''.join(current_text)
                        if current_tag:
                            self.text_buffer.insert_with_tags_by_name(iter_end, txt_chunk, current_tag)
                        else:
                            self.text_buffer.insert(iter_end, txt_chunk)
                    current_tag = tag
                    current_text = [c.data]

            if current_text:
                txt_chunk = ''.join(current_text)
                if current_tag:
                    self.text_buffer.insert_with_tags_by_name(iter_end, txt_chunk, current_tag)
                else:
                    self.text_buffer.insert(iter_end, txt_chunk)

            self.text_buffer.insert(iter_end, "\n")

        # Position GTK cursor at terminal's virtual cursor
        try:
            ok, cur_iter = self.text_buffer.get_iter_at_line_offset(cursor_abs_y, self.screen.cursor.x)
            if ok:
                self.text_buffer.place_cursor(cur_iter)
        except Exception:
            pass

        # Auto-scroll to bottom so user always sees the active prompt
        GLib.idle_add(self._scroll_to_bottom)
        return False

    def _scroll_to_bottom(self):
        vadj = self.scrolled.get_vadjustment()
        if vadj:
            vadj.set_value(vadj.get_upper() - vadj.get_page_size())
        return False

    def _on_pty_exit(self, exit_code: int):
        """Handles PTY process termination."""
        def _update_ui():
            self.pid_badge.set_label(f"● cerrada ({exit_code})")
            self.pid_badge.remove_css_class("terminal-badge-running")
            self.pid_badge.add_css_class("terminal-badge-stopped")
        GLib.idle_add(_update_ui)

    def _on_key_pressed(self, _controller, keyval, keycode, state) -> bool:
        """Translates keyboard events to PTY control sequences and UTF-8 characters."""
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # 1. Clipboard Copy / Paste Shortcuts
        if is_ctrl and is_shift and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            # Copy selected text
            bounds = self.text_buffer.get_selection_bounds()
            if bounds:
                start, end = bounds
                selected_text = self.text_buffer.get_text(start, end, True)
                display = Gdk.Display.get_default()
                if display and selected_text:
                    display.get_clipboard().set(selected_text)
            return True

        if is_ctrl and is_shift and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self._paste_from_clipboard()
            return True

        # Ctrl+C copy if text selected, otherwise send SIGINT to PTY!
        if is_ctrl and not is_shift and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            bounds = self.text_buffer.get_selection_bounds()
            if bounds:
                start, end = bounds
                selected_text = self.text_buffer.get_text(start, end, True)
                if selected_text:
                    display = Gdk.Display.get_default()
                    if display:
                        display.get_clipboard().set(selected_text)
                    return True
            # No selection: send SIGINT
            self.session.interrupt()
            return True

        # 2. Control Keys (Ctrl+A .. Ctrl+Z)
        if is_ctrl and not is_shift:
            if keyval in (Gdk.KEY_d, Gdk.KEY_D):
                self.session.write(b"\x04")
                return True
            elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
                self._on_clear_clicked()
                return True
            elif keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self.session.write(b"\x1a")
                return True
            elif keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.session.write(b"\x01")
                return True
            elif keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.session.write(b"\x05")
                return True
            elif keyval in (Gdk.KEY_u, Gdk.KEY_U):
                self.session.write(b"\x15")
                return True
            elif keyval in (Gdk.KEY_k, Gdk.KEY_K):
                self.session.write(b"\x0b")
                return True
            elif keyval in (Gdk.KEY_w, Gdk.KEY_W):
                self.session.write(b"\x17")
                return True
            elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
                self.session.write(b"\x12")
                return True

        # 3. Special Keys & Navigation
        key_map = {
            Gdk.KEY_Return: b"\r",
            Gdk.KEY_KP_Enter: b"\r",
            Gdk.KEY_BackSpace: b"\x7f",
            Gdk.KEY_Tab: b"\t",
            Gdk.KEY_ISO_Left_Tab: b"\x1b[Z",
            Gdk.KEY_Escape: b"\x1b",
            Gdk.KEY_Up: b"\x1b[A",
            Gdk.KEY_Down: b"\x1b[B",
            Gdk.KEY_Right: b"\x1b[C",
            Gdk.KEY_Left: b"\x1b[D",
            Gdk.KEY_Home: b"\x1b[H",
            Gdk.KEY_KP_Home: b"\x1b[H",
            Gdk.KEY_End: b"\x1b[F",
            Gdk.KEY_KP_End: b"\x1b[F",
            Gdk.KEY_Page_Up: b"\x1b[5~",
            Gdk.KEY_KP_Page_Up: b"\x1b[5~",
            Gdk.KEY_Page_Down: b"\x1b[6~",
            Gdk.KEY_KP_Page_Down: b"\x1b[6~",
            Gdk.KEY_Delete: b"\x1b[3~",
            Gdk.KEY_KP_Delete: b"\x1b[3~",
            Gdk.KEY_Insert: b"\x1b[2~",
            Gdk.KEY_KP_Insert: b"\x1b[2~",
        }

        if keyval in key_map:
            self.session.write(key_map[keyval])
            return True

        # 4. Standard Unicode input
        unicode_char = Gdk.keyval_to_unicode(keyval)
        if unicode_char and unicode_char != 0 and unicode_char >= 32:
            char_str = chr(unicode_char)
            self.session.write(char_str.encode("utf-8"))
            return True

        return False

    def grab_focus(self):
        """Grabs keyboard focus to the TextView for immediate typing."""
        self.text_view.grab_focus()

    def close(self):
        """Cleans up PTY session on widget destruction."""
        if hasattr(self, "session") and self.session:
            self.session.close()
