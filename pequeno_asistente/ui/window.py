"""
Main Dropdown Window for Pequeño Asistente.
Built with GTK4 and Libadwaita.
Supports multi-thread persistent conversations with sliding sidebar and instant shortcuts.
"""
import os
import re
import html
import asyncio
import threading
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, Gio, GObject
try:
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
except Exception:
    GdkPixbuf = None

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager, format_relative_timestamp, clean_thread_title
from pequeno_asistente.audio.transcriber import AudioTranscriber
from pequeno_asistente.audio.tts import KokoroTTS, UnifiedTTS
from pequeno_asistente.audio.call_engine import CallEngine
from pequeno_asistente.ui.chat_view import ChatView, set_chat_theme
from pequeno_asistente.ui.call_view import CallView
from pequeno_asistente.ui.terminal_view import TerminalView
from pequeno_asistente.ui.settings_window import SettingsWindow
from pequeno_asistente.ui.automation_dialog import AutomationManagerDialog
from pequeno_asistente.agent.heartbeat import HeartbeatManager, send_desktop_notification
from pequeno_asistente.security.manager import CredentialManager

logger = logging.getLogger("pequeno_asistente.ui")


class ThreadSidebar(Gtk.Box):
    """Sliding sidebar for browsing, switching, creating, and deleting conversation threads."""
    def __init__(self, on_new_thread, on_select_thread, on_delete_thread, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("thread-sidebar")
        self.set_size_request(230, -1)
        self.on_new_thread = on_new_thread
        self.on_select_thread = on_select_thread
        self.on_delete_thread = on_delete_thread
        self.on_close = on_close

        # Header inside sidebar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("thread-sidebar-header")

        title = Gtk.Label(label="<b>Sesiones</b>", use_markup=True, xalign=0)
        title.set_hexpand(True)
        title.add_css_class("thread-sidebar-title")
        header.append(title)

        close_btn = Gtk.Button(icon_name="go-previous-symbolic")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("sidebar-close-btn")
        close_btn.set_tooltip_text("Cerrar lista (Esc)")
        close_btn.connect("clicked", lambda _b: self.on_close())
        header.append(close_btn)
        self.append(header)

        # "Nueva sesión" button
        new_btn = Gtk.Button()
        new_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_btn_box.set_halign(Gtk.Align.CENTER)
        plus_icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        plus_icon.set_pixel_size(14)
        text_lbl = Gtk.Label(label="Nueva sesión")
        text_lbl.add_css_class("new-thread-btn-text")
        new_btn_box.append(plus_icon)
        new_btn_box.append(text_lbl)
        new_btn.set_child(new_btn_box)
        new_btn.add_css_class("new-thread-btn")
        new_btn.set_tooltip_text("Nueva sesión (Ctrl+N)")
        new_btn.connect("clicked", lambda _b: self.on_new_thread())
        self.append(new_btn)

        # Scrolled List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("thread-list")
        scrolled.set_child(self.list_box)
        self.append(scrolled)

    def populate(
        self,
        threads: List[Dict[str, Any]],
        active_thread_id: Optional[str],
        running_thread_ids: Optional[List[str]] = None
    ):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        if not threads:
            empty_lbl = Gtk.Label(label="No hay sesiones activas")
            empty_lbl.add_css_class("dim-label")
            empty_lbl.set_margin_top(20)
            self.list_box.append(empty_lbl)
            return

        running_set = set(running_thread_ids or [])

        for th in threads:
            tid = th["id"]
            is_active = (tid == active_thread_id)
            is_running = (tid in running_set)

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row_box.add_css_class("thread-row")
            if is_active:
                row_box.add_css_class("thread-row-active")
            if is_running:
                row_box.add_css_class("thread-row-running")

            # Selection button (click row body to open thread)
            select_btn = Gtk.Button()
            select_btn.add_css_class("flat")
            select_btn.add_css_class("thread-select-btn")
            select_btn.set_hexpand(True)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            info_box.set_halign(Gtk.Align.START)

            title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            title_txt = th.get("title") or "Conversación"
            title_lbl = Gtk.Label(label=title_txt, xalign=0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_max_width_chars(14 if is_running else 16)
            title_lbl.add_css_class("thread-item-title")
            title_row.append(title_lbl)

            if is_running:
                spinner = Gtk.Spinner()
                spinner.add_css_class("thread-spinner")
                spinner.start()
                title_row.append(spinner)

            info_box.append(title_row)

            date_str = format_relative_timestamp(th.get("updated_at") or th.get("created_at") or "")
            msg_count = th.get("message_count", 0)
            count_str = f"{msg_count} m" if msg_count != 1 else "1 m"
            if is_running:
                sub_lbl = Gtk.Label(label=f"⚡ Ejecutando · {count_str}", xalign=0)
                sub_lbl.add_css_class("thread-item-running-date")
            else:
                sub_lbl = Gtk.Label(label=f"{date_str} · {count_str}", xalign=0)
                sub_lbl.add_css_class("thread-item-date")
            info_box.append(sub_lbl)

            select_btn.set_child(info_box)
            select_btn.connect("clicked", lambda _b, t=tid: self.on_select_thread(t))
            row_box.append(select_btn)

            # Trash / Delete button
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("thread-del-btn")
            del_btn.set_tooltip_text("Eliminar sesión")
            del_btn.connect("clicked", lambda _b, t=tid: self.on_delete_thread(t))
            row_box.append(del_btn)

            self.list_box.append(row_box)


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        config_manager: ConfigManager,
        agent: AgentCore,
        mcp_manager: MCPManager,
        memory_manager: MemoryManager,
        thread_manager: ThreadManager,
        async_loop: asyncio.AbstractEventLoop
    ):
        super().__init__(application=app)
        self.config_manager = config_manager
        self.agent = agent
        self.mcp_manager = mcp_manager
        self.memory_manager = memory_manager
        self.thread_manager = thread_manager
        self.async_loop = async_loop

        # Connect security credential manager to main window
        CredentialManager.get_instance().parent_window_getter = lambda: self

        # Initialize audio transcriber (faster-whisper)
        audio_cfg = self.config_manager.get_audio_config()
        self.transcriber = AudioTranscriber(
            model_size=audio_cfg.get("whisper_model", "tiny"),
            device=audio_cfg.get("device", "cpu"),
            compute_type=audio_cfg.get("compute_type", "int8"),
            language=audio_cfg.get("language")
        )

        # Initialize Unified TTS (Kokoro & ElevenLabs with automatic fallback)
        audio_cfg = self.config_manager.get_audio_config()
        kokoro_cfg = self.config_manager.get_kokoro_config()
        el_cfg = self.config_manager.get_elevenlabs_config()
        self.tts = UnifiedTTS(
            provider=audio_cfg.get("tts_provider", "kokoro"),
            kokoro_url=kokoro_cfg.get("url", "http://localhost:8011"),
            kokoro_voice=kokoro_cfg.get("voice", "es-MX-JorgeNeural"),
            kokoro_speed=float(kokoro_cfg.get("speed", 1.0)),
            kokoro_lang=kokoro_cfg.get("lang", "es"),
            elevenlabs_api_key=el_cfg.get("api_key", ""),
            elevenlabs_voice_id=el_cfg.get("voice_id", "ErXwobaYiN019PkySvjV"),
            elevenlabs_model_id=el_cfg.get("model_id", "eleven_multilingual_v2"),
            elevenlabs_stability=float(el_cfg.get("stability", 0.5)),
            elevenlabs_similarity_boost=float(el_cfg.get("similarity_boost", 0.75)),
            fallback_to_kokoro=el_cfg.get("fallback_to_kokoro", True),
            enabled=kokoro_cfg.get("enabled", True)
        )
        self.active_speaking_bubble = None
        self._is_voice_turn = False
        self.really_quit = False
        self.pending_attachments: List[str] = []

        self.set_title("Pequeño Asistente")
        self.set_default_size(
            self.config_manager.data["ui"].get("window_width", 440),
            self.config_manager.data["ui"].get("window_height", 590)
        )
        self.add_css_class("asistente-window")

        # Load CSS
        self._load_css()

        # Build OverlaySplitView for sliding sidebar
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_collapsed(True)
        self.split_view.set_show_sidebar(False)
        self.split_view.set_sidebar_width_fraction(0.55)
        self.set_content(self.split_view)

        # Build Sidebar
        self.sidebar = ThreadSidebar(
            on_new_thread=self._on_new_thread,
            on_select_thread=self._on_switch_thread,
            on_delete_thread=self._on_delete_thread,
            on_close=lambda: self.split_view.set_show_sidebar(False)
        )
        self.split_view.set_sidebar(self.sidebar)

        # Build UI layout (Main content)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.split_view.set_content(main_box)

        # 1. Header Bar
        self.header = self._build_header()
        main_box.append(self.header)

        # 2. Main Stack (Chat View, Call View & Terminal View)
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_transition_duration(200)
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)

        # Multi-session Chat Stack
        self.chat_views: Dict[str, ChatView] = {}
        self.chat_stack = Gtk.Stack()
        self.chat_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.chat_stack.set_transition_duration(150)
        self.chat_stack.set_vexpand(True)
        self.chat_stack.set_hexpand(True)
        self.view_stack.add_named(self.chat_stack, "chat")

        self.call_view = CallView(
            on_end_call_requested=self._on_end_call,
            on_mute_toggled=self._on_call_mute_toggled,
            on_interrupt_requested=self._on_call_interrupt_requested
        )
        self.view_stack.add_named(self.call_view, "call")

        self.terminal_view = TerminalView(on_back_to_chat=self._on_toggle_terminal)
        self.view_stack.add_named(self.terminal_view, "terminal")
        main_box.append(self.view_stack)

        # 3. Bottom Input Area
        self.input_bar = self._build_input_bar()
        main_box.append(self.input_bar)

        # Voice-to-Voice Call Engine
        self.call_engine = CallEngine(
            agent=self.agent,
            transcriber=self.transcriber,
            tts=self.tts,
            async_loop=self.async_loop,
            on_state_changed=lambda s, d: GLib.idle_add(self.call_view.set_state, s, d),
            on_audio_level=lambda lvl: GLib.idle_add(self.call_view.set_audio_level, lvl),
            on_user_transcript=lambda t: GLib.idle_add(self._on_call_user_transcript, t),
            on_assistant_token=lambda tok: GLib.idle_add(self.call_view.append_assistant_token, tok),
            on_turn_finished=lambda u, a: GLib.idle_add(self._on_call_turn_finished, u, a),
            on_call_ended=lambda: GLib.idle_add(self._on_call_engine_ended)
        )

        # Heartbeat & Automation Manager
        self.heartbeat_manager = HeartbeatManager(
            config_manager=self.config_manager,
            memory_manager=self.memory_manager,
            agent=self.agent,
            async_loop=self.async_loop,
            on_output_callback=self._on_automation_output
        )
        self.heartbeat_manager.start(self.async_loop)
        self.heartbeat_manager.add_listener(lambda: GLib.idle_add(self.update_status_indicators))

        # Global key shortcuts on window
        win_key_controller = Gtk.EventControllerKey()
        win_key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(win_key_controller)

        # Apply configured theme (dark / light / system) and listen for system changes
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.connect("notify::dark", lambda *_: self._sync_effective_theme())
        self._apply_theme(self.config_manager.get_theme())

        # Initial thread restoration or welcome message
        self._init_thread()

        # Update indicators
        self.update_status_indicators()

    def _load_css(self):
        css_path = Path(__file__).parent.parent.parent / "assets" / "style.css"
        if css_path.exists():
            provider = Gtk.CssProvider()
            provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.add_css_class("asistente-header")

        # Left buttons:
        # 1. Toggle threads sidebar
        self.threads_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        self.threads_btn.set_tooltip_text("Historial de conversaciones (Ctrl+H)")
        self.threads_btn.add_css_class("flat")
        self.threads_btn.add_css_class("header-icon-btn")
        self.threads_btn.connect("clicked", lambda _b: self._on_toggle_sidebar())
        header.pack_start(self.threads_btn)

        # 2. New thread button
        self.new_thread_btn = Gtk.Button(icon_name="document-new-symbolic")
        self.new_thread_btn.set_tooltip_text("Nuevo chat (Ctrl+N)")
        self.new_thread_btn.add_css_class("flat")
        self.new_thread_btn.add_css_class("header-icon-btn")
        self.new_thread_btn.connect("clicked", lambda _b: self._on_new_thread())
        header.pack_start(self.new_thread_btn)

        # Center Title & Model pill
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_valign(Gtk.Align.CENTER)

        self.thread_title_label = Gtk.Label(label="<b>Pequeño Asistente</b>", use_markup=True)
        self.thread_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.thread_title_label.set_max_width_chars(18)
        self.thread_title_label.add_css_class("header-title")
        title_box.append(self.thread_title_label)

        self.model_pill = Gtk.Label(label=self.config_manager.get_active_model().split("/")[-1])
        self.model_pill.add_css_class("model-pill")
        title_box.append(self.model_pill)

        header.set_title_widget(title_box)

        # Right side: Single consolidated Menu Button with Popover
        self.menu_btn = Gtk.MenuButton()
        self.menu_btn.set_icon_name("open-menu-symbolic")
        self.menu_btn.set_tooltip_text("Menú de opciones")
        self.menu_btn.add_css_class("flat")
        self.menu_btn.add_css_class("header-icon-btn")
        self.menu_btn.add_css_class("header-menu-btn")

        self.menu_popover = self._build_menu_popover()
        self.menu_btn.set_popover(self.menu_popover)
        header.pack_end(self.menu_btn)

        # Quick Theme Toggle Button (Dark / Light)
        self.theme_btn = Gtk.Button(icon_name="weather-clear-symbolic")
        self.theme_btn.set_tooltip_text("Cambiar a modo claro (Ctrl+D)")
        self.theme_btn.add_css_class("flat")
        self.theme_btn.add_css_class("header-icon-btn")
        self.theme_btn.connect("clicked", lambda _b: self._on_toggle_theme())
        header.pack_end(self.theme_btn)

        # Compact MCP status pill
        self.status_pill = Gtk.Label(label="● MCPs")
        self.status_pill.add_css_class("status-pill")
        header.pack_end(self.status_pill)

        # Running sessions indicator pill (visible when 1+ sessions running concurrently)
        self.running_sessions_btn = Gtk.Button()
        self.running_sessions_btn.add_css_class("flat")
        self.running_sessions_btn.add_css_class("running-sessions-pill")
        self.running_sessions_btn.set_visible(False)
        self.running_sessions_btn.connect("clicked", lambda _b: self._on_toggle_sidebar())
        header.pack_end(self.running_sessions_btn)

        # Compatibility instances for background checks & state persistence
        self.terminal_btn = Gtk.Button(icon_name="utilities-terminal-symbolic")
        self.call_btn = Gtk.Button(icon_name="call-start-symbolic")
        self.heartbeat_btn = Gtk.Button(icon_name="alarm-symbolic")

        return header

    def _build_menu_popover(self) -> Gtk.Popover:
        """Builds the dropdown popover menu containing all options."""
        popover = Gtk.Popover()
        popover.add_css_class("menu-popover")
        popover.set_autohide(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # 1. Modo llamada (Voz a Voz) [Ctrl+L]
        self.menu_call_btn = Gtk.Button()
        self.menu_call_btn.add_css_class("flat")
        self.menu_call_btn.add_css_class("menu-item-row")
        call_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        call_box.set_halign(Gtk.Align.FILL)
        call_box.set_hexpand(True)
        self.menu_call_icon = Gtk.Image.new_from_icon_name("call-start-symbolic")
        self.menu_call_icon.set_pixel_size(14)
        self.menu_call_icon.add_css_class("menu-icon-call")
        self.menu_call_label = Gtk.Label(label="Modo llamada de voz", xalign=0)
        self.menu_call_label.set_halign(Gtk.Align.START)
        self.menu_call_label.set_hexpand(True)
        self.menu_call_label.add_css_class("menu-item-label")
        call_accel = Gtk.Label(label="Ctrl+L")
        call_accel.add_css_class("menu-accel-label")
        call_box.append(self.menu_call_icon)
        call_box.append(self.menu_call_label)
        call_box.append(call_accel)
        self.menu_call_btn.set_child(call_box)
        self.menu_call_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_toggle_call()))
        box.append(self.menu_call_btn)

        # 2. Terminal interactiva PTY [Ctrl+T]
        self.menu_terminal_btn = Gtk.Button()
        self.menu_terminal_btn.add_css_class("flat")
        self.menu_terminal_btn.add_css_class("menu-item-row")
        term_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        term_box.set_halign(Gtk.Align.FILL)
        term_box.set_hexpand(True)
        self.menu_terminal_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        self.menu_terminal_icon.set_pixel_size(14)
        self.menu_terminal_icon.add_css_class("menu-icon-terminal")
        self.menu_terminal_label = Gtk.Label(label="Terminal interactiva", xalign=0)
        self.menu_terminal_label.set_halign(Gtk.Align.START)
        self.menu_terminal_label.set_hexpand(True)
        self.menu_terminal_label.add_css_class("menu-item-label")
        term_accel = Gtk.Label(label="Ctrl+T")
        term_accel.add_css_class("menu-accel-label")
        term_box.append(self.menu_terminal_icon)
        term_box.append(self.menu_terminal_label)
        term_box.append(term_accel)
        self.menu_terminal_btn.set_child(term_box)
        self.menu_terminal_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_toggle_terminal()))
        box.append(self.menu_terminal_btn)

        # 3. Automatizaciones y Heartbeats [Ctrl+B]
        self.menu_heartbeat_btn = Gtk.Button()
        self.menu_heartbeat_btn.add_css_class("flat")
        self.menu_heartbeat_btn.add_css_class("menu-item-row")
        hb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hb_box.set_halign(Gtk.Align.FILL)
        hb_box.set_hexpand(True)
        self.menu_heartbeat_icon = Gtk.Image.new_from_icon_name("alarm-symbolic")
        self.menu_heartbeat_icon.set_pixel_size(14)
        self.menu_heartbeat_icon.add_css_class("menu-icon-heartbeat")
        self.menu_heartbeat_label = Gtk.Label(label="Automatizaciones y Heartbeats", xalign=0)
        self.menu_heartbeat_label.set_halign(Gtk.Align.START)
        self.menu_heartbeat_label.set_hexpand(True)
        self.menu_heartbeat_label.add_css_class("menu-item-label")
        self.menu_heartbeat_badge = Gtk.Label(label="")
        self.menu_heartbeat_badge.add_css_class("menu-badge-label")
        self.menu_heartbeat_badge.set_visible(False)
        hb_accel = Gtk.Label(label="Ctrl+B")
        hb_accel.add_css_class("menu-accel-label")
        hb_box.append(self.menu_heartbeat_icon)
        hb_box.append(self.menu_heartbeat_label)
        hb_box.append(self.menu_heartbeat_badge)
        hb_box.append(hb_accel)
        self.menu_heartbeat_btn.set_child(hb_box)
        self.menu_heartbeat_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_open_automations()))
        box.append(self.menu_heartbeat_btn)

        # Separator
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep1.add_css_class("menu-separator")
        box.append(sep1)

        # 4. Alternar Modo Claro / Oscuro [Ctrl+D]
        self.menu_theme_btn = Gtk.Button()
        self.menu_theme_btn.add_css_class("flat")
        self.menu_theme_btn.add_css_class("menu-item-row")
        theme_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        theme_box.set_halign(Gtk.Align.FILL)
        theme_box.set_hexpand(True)
        self.menu_theme_icon = Gtk.Image.new_from_icon_name("weather-clear-symbolic")
        self.menu_theme_icon.set_pixel_size(14)
        self.menu_theme_icon.add_css_class("menu-icon-theme")
        self.menu_theme_label = Gtk.Label(label="Cambiar a modo claro", xalign=0)
        self.menu_theme_label.set_halign(Gtk.Align.START)
        self.menu_theme_label.set_hexpand(True)
        self.menu_theme_label.add_css_class("menu-item-label")
        theme_accel = Gtk.Label(label="Ctrl+D")
        theme_accel.add_css_class("menu-accel-label")
        theme_box.append(self.menu_theme_icon)
        theme_box.append(self.menu_theme_label)
        theme_box.append(theme_accel)
        self.menu_theme_btn.set_child(theme_box)
        self.menu_theme_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_toggle_theme()))
        box.append(self.menu_theme_btn)

        # 5. Limpiar conversación actual
        self.menu_clear_btn = Gtk.Button()
        self.menu_clear_btn.add_css_class("flat")
        self.menu_clear_btn.add_css_class("menu-item-row")
        clear_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clear_box.set_halign(Gtk.Align.FILL)
        clear_box.set_hexpand(True)
        clear_icon = Gtk.Image.new_from_icon_name("edit-clear-symbolic")
        clear_icon.set_pixel_size(14)
        clear_icon.add_css_class("menu-icon-clear")
        clear_label = Gtk.Label(label="Limpiar conversación actual", xalign=0)
        clear_label.set_halign(Gtk.Align.START)
        clear_label.set_hexpand(True)
        clear_label.add_css_class("menu-item-label")
        clear_box.append(clear_icon)
        clear_box.append(clear_label)
        self.menu_clear_btn.set_child(clear_box)
        self.menu_clear_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_clear_chat(None)))
        box.append(self.menu_clear_btn)

        # 5. Configuración
        self.menu_settings_btn = Gtk.Button()
        self.menu_settings_btn.add_css_class("flat")
        self.menu_settings_btn.add_css_class("menu-item-row")
        settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        settings_box.set_halign(Gtk.Align.FILL)
        settings_box.set_hexpand(True)
        settings_icon = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
        settings_icon.set_pixel_size(14)
        settings_icon.add_css_class("menu-icon-settings")
        settings_label = Gtk.Label(label="Configuración", xalign=0)
        settings_label.set_halign(Gtk.Align.START)
        settings_label.set_hexpand(True)
        settings_label.add_css_class("menu-item-label")
        settings_box.append(settings_icon)
        settings_box.append(settings_label)
        self.menu_settings_btn.set_child(settings_box)
        self.menu_settings_btn.connect("clicked", lambda _b: (popover.popdown(), self._on_open_settings(None)))
        box.append(self.menu_settings_btn)

        popover.set_child(box)
        return popover

    def _build_input_bar(self) -> Gtk.Box:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrapper.add_css_class("input-container")

        # Inner capsule card (vertical so attachments preview bar sits cleanly above the input row)
        card_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card_vbox.add_css_class("input-card")

        # Pending attachments preview bar (hidden when empty)
        self.attachments_scroll = Gtk.ScrolledWindow()
        self.attachments_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.attachments_scroll.set_min_content_height(38)
        self.attachments_scroll.set_max_content_height(56)
        self.attachments_scroll.add_css_class("attachments-preview-scroll")
        self.attachments_scroll.set_visible(False)

        self.attachments_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.attachments_box.add_css_class("attachments-preview-bar")
        self.attachments_scroll.set_child(self.attachments_box)
        card_vbox.append(self.attachments_scroll)

        # Horizontal input row
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Attach file/image button
        self.attach_btn = Gtk.Button(icon_name="mail-attachment-symbolic")
        self.attach_btn.add_css_class("flat")
        self.attach_btn.add_css_class("attach-button")
        self.attach_btn.set_valign(Gtk.Align.END)
        self.attach_btn.set_margin_bottom(2)
        self.attach_btn.set_tooltip_text("Adjuntar documentos o imágenes (Ctrl+U)")
        self.attach_btn.connect("clicked", self._on_attach_clicked)
        bar.append(self.attach_btn)

        # Multiline text view inside scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(34)
        scrolled.set_max_content_height(96)
        scrolled.set_hexpand(True)
        scrolled.add_css_class("chat-input-scrolled")

        self.text_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.text_view.set_hexpand(True)
        self.text_view.add_css_class("chat-text-view")
        self.text_buffer = self.text_view.get_buffer()
        self.text_buffer.connect("changed", self._on_input_buffer_changed)
        self.text_buffer.connect("notify::cursor-position", lambda *_: self._on_input_buffer_changed(self.text_buffer))
        scrolled.set_child(self.text_view)
        bar.append(scrolled)

        # Autocomplete popover for /skills
        self.skills_popover = self._build_skills_popover(scrolled)

        # Enter & navigation key event handler on text view (capture phase to intercept before TextView)
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_input_key_pressed)
        self.text_view.add_controller(key_controller)

        # Action buttons (Mic / Stop / Send)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        btn_box.set_valign(Gtk.Align.END)
        btn_box.set_margin_bottom(2)

        # Microphone button (faster-whisper voice transcription)
        self.mic_btn = Gtk.Button()
        self.mic_btn.add_css_class("flat")
        self.mic_btn.add_css_class("mic-button")
        self.mic_btn.set_tooltip_text("Dictar voz (faster-whisper) (Ctrl+M)")
        self.mic_btn.connect("clicked", self._on_mic_clicked)

        self.mic_stack = Gtk.Stack()
        self.mic_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self.mic_stack.add_named(self.mic_icon, "icon")

        self.mic_spinner = Gtk.Spinner()
        self.mic_stack.add_named(self.mic_spinner, "spinner")
        self.mic_stack.set_visible_child_name("icon")

        self.mic_btn.set_child(self.mic_stack)
        btn_box.append(self.mic_btn)

        # Stop button (hidden by default)
        self.stop_btn = Gtk.Button(icon_name="process-stop-symbolic")
        self.stop_btn.add_css_class("stop-button")
        self.stop_btn.set_tooltip_text("Detener generación")
        self.stop_btn.set_visible(False)
        self.stop_btn.connect("clicked", self._on_stop_clicked)
        btn_box.append(self.stop_btn)

        # Send button
        self.send_btn = Gtk.Button(icon_name="mail-send-symbolic")
        self.send_btn.add_css_class("send-button")
        self.send_btn.set_tooltip_text("Enviar (Enter)")
        self.send_btn.connect("clicked", self._on_send_clicked)
        btn_box.append(self.send_btn)

        bar.append(btn_box)
        card_vbox.append(bar)

        # Drag-and-drop file controller on input card
        try:
            drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            drop_target.connect("drop", self._on_files_dropped)
            card_vbox.add_controller(drop_target)
        except Exception:
            pass

        # Center input card in a matching clamp (ChatGPT / Claude style)
        input_clamp = Adw.Clamp()
        input_clamp.set_maximum_size(720)
        input_clamp.set_tightening_threshold(560)
        input_clamp.set_child(card_vbox)
        wrapper.append(input_clamp)

        # Subtle bottom hint
        hint_label = Gtk.Label(
            label="<small>↵ Enviar  ·  ⇧↵ Salto  ·  Ctrl+U Adjuntar  ·  Ctrl+M Dictar voz  ·  Ctrl+N Nuevo</small>",
            use_markup=True
        )
        hint_label.add_css_class("input-hint")
        hint_label.set_halign(Gtk.Align.CENTER)
        wrapper.append(hint_label)

        return wrapper

    def _on_files_dropped(self, _target, value, _x, _y) -> bool:
        """Handles files dropped onto the input bar."""
        try:
            if isinstance(value, Gdk.FileList):
                for gfile in value.get_files():
                    path = gfile.get_path()
                    if path:
                        self.add_attachment(path)
                return True
        except Exception as e:
            logger.error(f"Error handling dropped files: {e}")
        return False

    def _on_attach_clicked(self, _btn):
        """Opens native file selector to attach images or documents."""
        try:
            filters = Gio.ListStore.new(Gtk.FileFilter)

            f_all_supported = Gtk.FileFilter()
            f_all_supported.set_name("Imágenes y Documentos")
            for pat in (
                "*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp", "*.svg", "*.tiff",
                "*.pdf", "*.docx", "*.odt", "*.xlsx", "*.pptx", "*.csv", "*.tsv", "*.epub",
                "*.md", "*.txt", "*.json", "*.yaml", "*.yml", "*.html", "*.rtf", "*.py", "*.js", "*.ts", "*.sh"
            ):
                f_all_supported.add_pattern(pat)
            filters.append(f_all_supported)

            f_images = Gtk.FileFilter()
            f_images.set_name("Imágenes (PNG, JPG, WEBP, GIF, SVG)")
            for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp", "*.svg", "*.tiff"):
                f_images.add_pattern(pat)
            filters.append(f_images)

            f_docs = Gtk.FileFilter()
            f_docs.set_name("Documentos (PDF, DOCX, XLSX, PPTX, TXT, MD, CSV)")
            for pat in ("*.pdf", "*.docx", "*.odt", "*.xlsx", "*.pptx", "*.csv", "*.tsv", "*.epub", "*.md", "*.txt", "*.json", "*.html", "*.rtf"):
                f_docs.add_pattern(pat)
            filters.append(f_docs)

            f_any = Gtk.FileFilter()
            f_any.set_name("Todos los archivos")
            f_any.add_pattern("*")
            filters.append(f_any)

            if hasattr(Gtk, "FileDialog"):
                dialog = Gtk.FileDialog()
                dialog.set_title("Adjuntar documentos o imágenes")
                dialog.set_filters(filters)
                dialog.set_default_filter(f_all_supported)
                dialog.open_multiple(self, None, self._on_file_dialog_ready)
            else:
                chooser = Gtk.FileChooserNative.new(
                    "Adjuntar documentos o imágenes",
                    self,
                    Gtk.FileChooserAction.OPEN,
                    "Adjuntar",
                    "Cancelar"
                )
                chooser.set_select_multiple(True)
                chooser.set_filter(f_all_supported)
                chooser.connect("response", self._on_file_chooser_response)
                chooser.show()
                self._active_file_chooser = chooser
        except Exception as e:
            logger.error(f"Error opening attachment dialog: {e}")

    def _on_file_dialog_ready(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            if files:
                for i in range(files.get_n_items()):
                    gfile = files.get_item(i)
                    if gfile and gfile.get_path():
                        self.add_attachment(gfile.get_path())
        except Exception:
            pass

    def _on_file_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = chooser.get_files()
            if files:
                for i in range(files.get_n_items()):
                    gfile = files.get_item(i)
                    if gfile and gfile.get_path():
                        self.add_attachment(gfile.get_path())
        self._active_file_chooser = None

    def add_attachment(self, file_path: str):
        """Adds a local file path to the pending attachments bar."""
        from pequeno_asistente.agent.media_processor import resolve_local_path
        resolved = str(resolve_local_path(file_path))
        if not resolved or not os.path.isfile(resolved):
            return
        if resolved not in self.pending_attachments:
            self.pending_attachments.append(resolved)
            self._refresh_attachments_preview()
        self.text_view.grab_focus()

    def remove_attachment(self, file_path: str):
        """Removes a file from pending attachments."""
        if file_path in self.pending_attachments:
            self.pending_attachments.remove(file_path)
            self._refresh_attachments_preview()

    def clear_attachments(self):
        """Clears all pending attachments."""
        self.pending_attachments.clear()
        self._refresh_attachments_preview()

    def _refresh_attachments_preview(self):
        """Updates the pending attachment chips inside the input bar."""
        from pequeno_asistente.agent.media_processor import is_image_file, format_file_size

        while child := self.attachments_box.get_first_child():
            self.attachments_box.remove(child)

        if not self.pending_attachments:
            self.attachments_scroll.set_visible(False)
            self.attach_btn.remove_css_class("attach-button-active")
            return

        self.attachments_scroll.set_visible(True)
        self.attach_btn.add_css_class("attach-button-active")

        for path in self.pending_attachments:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip.add_css_class("attachment-chip")
            name = os.path.basename(path)
            try:
                size_str = format_file_size(os.path.getsize(path))
            except Exception:
                size_str = ""
            chip.set_tooltip_text(f"{name} ({size_str})\n{path}")

            if is_image_file(path):
                thumb_added = False
                if GdkPixbuf is not None:
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 24, 24, True)
                        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                        img = Gtk.Image.new_from_paintable(texture)
                        img.set_pixel_size(22)
                        img.add_css_class("attachment-chip-thumb")
                        chip.append(img)
                        thumb_added = True
                    except Exception:
                        thumb_added = False
                if not thumb_added:
                    icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
                    icon.set_pixel_size(14)
                    chip.append(icon)
            else:
                icon = Gtk.Image.new_from_icon_name("x-office-document-symbolic")
                icon.set_pixel_size(14)
                chip.append(icon)

            lbl = Gtk.Label(label=name, xalign=0)
            lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl.set_max_width_chars(20)
            lbl.add_css_class("attachment-chip-label")
            chip.append(lbl)

            rm_btn = Gtk.Button(icon_name="window-close-symbolic")
            rm_btn.add_css_class("flat")
            rm_btn.add_css_class("attachment-chip-remove")
            rm_btn.set_tooltip_text("Quitar archivo adjunto")
            rm_btn.connect("clicked", lambda _b, p=path: self.remove_attachment(p))
            chip.append(rm_btn)

            self.attachments_box.append(chip)

    @property
    def chat_view(self) -> ChatView:
        """Returns the ChatView widget corresponding to the currently active session."""
        tid = self.agent.active_thread_id or "default"
        return self._get_or_create_chat_view(tid)

    def _get_or_create_chat_view(self, thread_id: str) -> ChatView:
        if thread_id in self.chat_views:
            return self.chat_views[thread_id]

        cv = ChatView(on_tts_requested=self._on_bubble_tts_requested)
        self.chat_views[thread_id] = cv
        self.chat_stack.add_named(cv, thread_id)

        # Load existing messages if thread has any
        if self.thread_manager:
            th = self.thread_manager.get_thread(thread_id)
            messages = th.get("messages", []) if th else []
            if messages:
                cv.load_history(messages)
            else:
                cv.show_welcome(on_suggestion_clicked=self._on_suggestion_clicked)
        else:
            cv.show_welcome(on_suggestion_clicked=self._on_suggestion_clicked)

        return cv

    def _show_welcome_message(self):
        self.chat_view.show_welcome(on_suggestion_clicked=self._on_suggestion_clicked)

    def _on_suggestion_clicked(self, prompt: str):
        self.text_buffer.set_text(prompt)
        self._on_send_clicked(None)

    def _init_thread(self):
        """Loads the most recent thread on startup, or creates a new one."""
        thread = self.thread_manager.get_or_create_active_thread()
        tid = thread["id"]
        self.agent.load_thread(tid)
        self._get_or_create_chat_view(tid)
        self.chat_stack.set_visible_child_name(tid)
        self._update_header_title(thread.get("title"))
        self._refresh_sidebar()

    def _update_header_title(self, title: Optional[str]):
        if not title or title in ("Nueva conversación", "Nuevo hilo"):
            self.thread_title_label.set_markup("<b>Pequeño Asistente</b>")
        else:
            escaped = html.escape(title)
            self.thread_title_label.set_markup(f"<b>{escaped}</b>")

    def _refresh_sidebar(self):
        threads = self.thread_manager.list_threads()
        running_ids = self.agent.get_running_thread_ids()
        self.sidebar.populate(threads, self.agent.active_thread_id, running_thread_ids=running_ids)

    def _on_toggle_sidebar(self):
        vis = not self.split_view.get_show_sidebar()
        if vis:
            self._refresh_sidebar()
        self.split_view.set_show_sidebar(vis)

    def _on_new_thread(self):
        """Starts a fresh new conversation thread without aborting background threads."""
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()

        # If current active thread is already empty and not running, reuse it
        cur_tid = self.agent.active_thread_id
        if cur_tid and not self.agent.is_thread_running(cur_tid):
            cur_th = self.thread_manager.get_thread(cur_tid) if self.thread_manager else None
            if cur_th and len(cur_th.get("messages", [])) == 0:
                self.chat_stack.set_visible_child_name(cur_tid)
                self._update_header_title("Pequeño Asistente")
                self.split_view.set_show_sidebar(False)
                self._refresh_sidebar()
                self.text_view.grab_focus()
                return

        new_th = self.agent.new_thread(title="Nueva conversación")
        new_tid = new_th["id"]
        self._get_or_create_chat_view(new_tid)
        self.chat_stack.set_visible_child_name(new_tid)
        self._update_header_title("Pequeño Asistente")

        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)

        self.split_view.set_show_sidebar(False)
        self._refresh_sidebar()
        self.update_status_indicators()
        self.text_view.grab_focus()

    def _on_switch_thread(self, thread_id: str):
        """Switches to an existing session without aborting running sessions."""
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()

        self.agent.load_thread(thread_id)
        self._get_or_create_chat_view(thread_id)
        self.chat_stack.set_visible_child_name(thread_id)

        th = self.thread_manager.get_thread(thread_id) if self.thread_manager else None
        if th:
            self._update_header_title(th.get("title"))

        is_running = self.agent.is_thread_running(thread_id)
        has_queue = self.agent.has_queued_turns(thread_id)
        is_speaking = bool(self.tts.is_speaking and thread_id == self.agent.active_thread_id)
        is_busy = is_running or has_queue or is_speaking

        self.send_btn.set_visible(True)
        if is_busy:
            self.send_btn.set_tooltip_text("Añadir a la cola (Enter)")
            self.stop_btn.set_visible(True)
        else:
            self.send_btn.set_tooltip_text("Enviar (Enter)")
            self.stop_btn.set_visible(False)

        self.split_view.set_show_sidebar(False)
        self._refresh_sidebar()
        self.update_status_indicators()
        self.text_view.grab_focus()

    def _on_delete_thread(self, thread_id: str):
        """Deletes a session and falls back to another or creates a new one."""
        is_active = (self.agent.active_thread_id == thread_id)
        if self.agent.is_thread_running(thread_id):
            self.agent.abort(thread_id)

        if thread_id in self.chat_views:
            widget = self.chat_views.pop(thread_id)
            self.chat_stack.remove(widget)

        self.agent.delete_session(thread_id)
        self.thread_manager.delete_thread(thread_id)

        if is_active:
            existing = self.thread_manager.list_threads()
            if existing:
                self._on_switch_thread(existing[0]["id"])
            else:
                self._on_new_thread()
        else:
            self._refresh_sidebar()
            self.update_status_indicators()

    def _navigate_session(self, delta: int):
        """Cycles to the previous or next conversation session."""
        threads = self.thread_manager.list_threads()
        if not threads:
            return
        cur_id = self.agent.active_thread_id
        tids = [t["id"] for t in threads]
        if cur_id in tids:
            cur_idx = tids.index(cur_id)
            next_idx = (cur_idx + delta) % len(tids)
        else:
            next_idx = 0
        self._on_switch_thread(tids[next_idx])

    def update_status_indicators(self):
        summary = self.mcp_manager.get_status_summary()
        conn = summary["connected"]
        tools = summary["total_tools"]
        self.status_pill.set_text(f"● {conn} MCPs")
        self.status_pill.set_tooltip_text(f"{conn} servidores MCP conectados ({tools} herramientas disponibles)")
        model_name = self.config_manager.get_active_model().split("/")[-1]
        self.model_pill.set_text(model_name)
        self.model_pill.set_tooltip_text(f"Modelo activo: {self.config_manager.get_active_model()}")

        # Update running sessions pill
        if hasattr(self, "running_sessions_btn"):
            running_ids = self.agent.get_running_thread_ids()
            count = len(running_ids)
            if count > 0:
                self.running_sessions_btn.set_visible(True)
                if count == 1:
                    self.running_sessions_btn.set_label("⚡ 1 activa")
                    self.running_sessions_btn.set_tooltip_text("1 sesión ejecutándose a la vez. Clic para ver sesiones (Ctrl+H).")
                else:
                    self.running_sessions_btn.set_label(f"⚡ {count} activas")
                    self.running_sessions_btn.set_tooltip_text(f"{count} sesiones ejecutándose concurrentemente. Clic para ver sesiones (Ctrl+H).")
            else:
                self.running_sessions_btn.set_visible(False)

        # Update Heartbeat indicator
        if hasattr(self, "heartbeat_manager") and self.heartbeat_manager:
            active_count = self.heartbeat_manager.get_active_count()
            if hasattr(self, "heartbeat_btn"):
                if active_count > 0:
                    self.heartbeat_btn.add_css_class("heartbeat-active")
                    self.heartbeat_btn.set_tooltip_text(f"Heartbeats y Automatizaciones ({active_count} activas) (Ctrl+B)")
                else:
                    self.heartbeat_btn.remove_css_class("heartbeat-active")
                    self.heartbeat_btn.set_tooltip_text("Automatizaciones y Heartbeats (Ctrl+B)")
            if hasattr(self, "menu_heartbeat_badge"):
                if active_count > 0:
                    self.menu_heartbeat_badge.set_text(f"{active_count} activas")
                    self.menu_heartbeat_badge.set_visible(True)
                else:
                    self.menu_heartbeat_badge.set_visible(False)
            if hasattr(self, "menu_btn"):
                if active_count > 0:
                    self.menu_btn.add_css_class("menu-btn-heartbeat-active")
                else:
                    self.menu_btn.remove_css_class("menu-btn-heartbeat-active")

    def _on_window_key_pressed(self, _controller, keyval, _keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)

        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.quit_app()
            return True
        elif ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._on_new_thread()
            return True
        elif ctrl and keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self._on_toggle_sidebar()
            return True
        elif ctrl and keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._on_mic_clicked(None)
            return True
        elif ctrl and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self._on_toggle_call()
            return True
        elif ctrl and keyval in (Gdk.KEY_t, Gdk.KEY_T):
            self._on_toggle_terminal()
            return True
        elif ctrl and keyval in (Gdk.KEY_b, Gdk.KEY_B):
            self._on_open_automations()
            return True
        elif ctrl and keyval in (Gdk.KEY_d, Gdk.KEY_D):
            self._on_toggle_theme()
            return True
        elif ctrl and keyval in (Gdk.KEY_u, Gdk.KEY_U):
            self._on_attach_clicked(None)
            return True
        elif (alt and keyval == Gdk.KEY_Left) or (ctrl and keyval == Gdk.KEY_Page_Up):
            self._navigate_session(delta=-1)
            return True
        elif (alt and keyval == Gdk.KEY_Right) or (ctrl and keyval == Gdk.KEY_Page_Down):
            self._navigate_session(delta=1)
            return True
        elif keyval == Gdk.KEY_Escape:
            if hasattr(self, "skills_popover") and self.skills_popover.get_visible():
                self.skills_popover.popdown()
                return True
            if hasattr(self, "call_engine") and self.call_engine.is_active:
                self._on_end_call()
                return True
            if self.view_stack.get_visible_child_name() == "terminal":
                self._on_toggle_terminal()
                return True
            if self.split_view.get_show_sidebar():
                self.split_view.set_show_sidebar(False)
                return True
            # Escape oculta la ventana manteniendo la sesión y MCPs vivos
            self.set_visible(False)
            return True
        return False

    def _build_skills_popover(self, anchor_widget: Gtk.Widget) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.set_parent(anchor_widget)
        popover.set_position(Gtk.PositionType.TOP)
        popover.set_autohide(False)
        popover.set_can_focus(False)
        popover.add_css_class("skills-autocomplete-popover")

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        container.set_size_request(380, -1)
        container.add_css_class("skills-autocomplete-container")

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.add_css_class("skills-autocomplete-header")
        icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        header_box.append(icon)
        lbl = Gtk.Label(label="<small><b>HABILIDADES / SKILLS (Tab o ↵ para insertar)</b></small>", use_markup=True)
        lbl.set_hexpand(True)
        lbl.set_xalign(0)
        header_box.append(lbl)
        container.append(header_box)

        # Scrolled ListBox
        self.skills_scrolled = Gtk.ScrolledWindow()
        self.skills_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.skills_scrolled.set_max_content_height(240)
        self.skills_scrolled.set_min_content_height(60)
        self.skills_scrolled.set_propagate_natural_height(True)

        self.skills_listbox = Gtk.ListBox()
        self.skills_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.skills_listbox.set_activate_on_single_click(True)
        self.skills_listbox.set_can_focus(False)
        self.skills_listbox.add_css_class("skills-autocomplete-list")
        self.skills_listbox.connect("row-activated", self._on_skill_row_activated)

        self.skills_scrolled.set_child(self.skills_listbox)
        container.append(self.skills_scrolled)

        popover.set_child(container)
        return popover

    def _on_input_buffer_changed(self, buffer: Gtk.TextBuffer):
        start, end = buffer.get_bounds()
        full_text = buffer.get_text(start, end, True)
        if not full_text:
            if hasattr(self, "skills_popover") and self.skills_popover.get_visible():
                self.skills_popover.popdown()
            return

        insert_mark = buffer.get_insert()
        cursor_iter = buffer.get_iter_at_mark(insert_mark)
        cursor_offset = cursor_iter.get_offset()
        text_before = full_text[:cursor_offset]

        # Match /prefix at the end of text_before (e.g. "/bit", "haz esto /ana")
        match = re.search(r'(?:^|\s)/([a-zA-Z0-9_-]*)$', text_before)
        if match:
            prefix = match.group(1).lower()
            self._update_skills_autocomplete(prefix)
        else:
            if hasattr(self, "skills_popover") and self.skills_popover.get_visible():
                self.skills_popover.popdown()

    def _update_skills_autocomplete(self, prefix: str):
        from pequeno_asistente.agent.skills_manager import SkillManager
        mgr = SkillManager.get_instance(self.agent.config_manager)
        skills = mgr.discover_skills()
        active = [s for s in skills.values() if s.enabled]

        matches = []
        for s in active:
            s_id = s.id.lower()
            s_name = s.name.lower()
            s_tags = [t.lower() for t in s.tags]
            if not prefix or prefix in s_id or prefix in s_name or any(prefix in t for t in s_tags):
                matches.append(s)

        def sort_key(s):
            is_start = 0 if (s.id.lower().startswith(prefix) or s.name.lower().startswith(prefix)) else 1
            return (is_start, -s.priority, s.name.lower())

        matches.sort(key=sort_key)

        # Clear listbox items
        while True:
            child = self.skills_listbox.get_first_child()
            if child is None:
                break
            self.skills_listbox.remove(child)

        if not matches:
            if self.skills_popover.get_visible():
                self.skills_popover.popdown()
            return

        for s in matches[:20]:
            row = self._create_skill_autocomplete_row(s)
            self.skills_listbox.append(row)

        first_row = self.skills_listbox.get_row_at_index(0)
        if first_row:
            self.skills_listbox.select_row(first_row)

        if not self.skills_popover.get_visible():
            self.skills_popover.popup()
        if not self.text_view.has_focus():
            self.text_view.grab_focus()

    def _create_skill_autocomplete_row(self, skill) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_can_focus(False)
        row.set_activatable(True)
        row.add_css_class("skill-autocomplete-row")
        row.skill_id = skill.id

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(8)
        box.set_margin_end(8)

        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        badge = Gtk.Label(label=f"/{skill.id}")
        badge.add_css_class("skill-id-badge")
        top_box.append(badge)

        safe_name = html.escape(skill.name or skill.id)
        name_lbl = Gtk.Label(label=f"<b>{safe_name}</b>", use_markup=True)
        name_lbl.set_hexpand(True)
        name_lbl.set_xalign(0)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        top_box.append(name_lbl)

        safe_src = html.escape(skill.source_label or "")
        src_lbl = Gtk.Label(label=f"<small>{safe_src}</small>", use_markup=True)
        src_lbl.add_css_class("skill-source-hint")
        top_box.append(src_lbl)

        box.append(top_box)

        desc = (skill.description or "").strip()
        if len(desc) > 85:
            desc = desc[:82] + "..."
        safe_desc = html.escape(desc)
        desc_lbl = Gtk.Label(label=f"<small>{safe_desc}</small>", use_markup=True)
        desc_lbl.set_xalign(0)
        desc_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        desc_lbl.add_css_class("skill-desc-hint")
        box.append(desc_lbl)

        row.set_child(box)
        return row

    def _on_skill_row_activated(self, _listbox, row):
        self._apply_selected_skill(row)

    def _apply_selected_skill(self, target_row=None):
        row = target_row or self.skills_listbox.get_selected_row()
        if not row:
            row = self.skills_listbox.get_row_at_index(0)
        if not row or not hasattr(row, "skill_id"):
            return

        skill_id = row.skill_id

        start, end = self.text_buffer.get_bounds()
        full_text = self.text_buffer.get_text(start, end, True)

        insert_mark = self.text_buffer.get_insert()
        cursor_iter = self.text_buffer.get_iter_at_mark(insert_mark)
        cursor_offset = cursor_iter.get_offset()

        text_before = full_text[:cursor_offset]
        text_after = full_text[cursor_offset:]

        slash_pos = text_before.rfind('/')
        if slash_pos != -1:
            new_before = text_before[:slash_pos] + f"/{skill_id} "
            new_full = new_before + text_after
            self.text_buffer.set_text(new_full)

            new_iter = self.text_buffer.get_iter_at_offset(len(new_before))
            self.text_buffer.place_cursor(new_iter)

        self.skills_popover.popdown()
        self.text_view.grab_focus()

    def _move_skills_selection(self, step: int):
        selected = self.skills_listbox.get_selected_row()
        current_idx = selected.get_index() if selected else 0
        next_idx = current_idx + step
        if next_idx < 0:
            next_idx = 0
        target = self.skills_listbox.get_row_at_index(next_idx)
        if target:
            self.skills_listbox.select_row(target)
            if hasattr(self, "skills_scrolled") and self.skills_scrolled:
                adj = self.skills_scrolled.get_vadjustment()
                ok, bounds = target.compute_bounds(self.skills_listbox)
                if ok and adj:
                    row_top = bounds.get_y()
                    row_bottom = row_top + bounds.get_height()
                    view_top = adj.get_value()
                    view_bottom = view_top + adj.get_page_size()
                    if row_top < view_top:
                        adj.set_value(row_top)
                    elif row_bottom > view_bottom:
                        adj.set_value(row_bottom - adj.get_page_size())

    def _on_input_key_pressed(self, _controller, keyval, _keycode, state):
        if hasattr(self, "skills_popover") and self.skills_popover.get_visible():
            if keyval == Gdk.KEY_Escape:
                self.skills_popover.popdown()
                return True
            elif keyval == Gdk.KEY_Down:
                self._move_skills_selection(1)
                return True
            elif keyval == Gdk.KEY_Up:
                self._move_skills_selection(-1)
                return True
            elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                if not (state & Gdk.ModifierType.SHIFT_MASK):
                    self._apply_selected_skill()
                    return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not (state & Gdk.ModifierType.SHIFT_MASK):
                self._on_send_clicked(None)
                return True
        return False

    def _on_send_clicked(self, _btn):
        start, end = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(start, end, True).strip()
        attachments = list(self.pending_attachments)
        if not text and not attachments:
            return

        if not text and attachments:
            text = "Analiza los archivos adjuntos."

        # Clear input box and pending attachments immediately
        self.text_buffer.set_text("")
        self.clear_attachments()

        # Identify target session
        tid = self.agent.active_thread_id
        if not tid:
            active_th = self.thread_manager.get_or_create_active_thread()
            tid = active_th["id"]
            self.agent.active_thread_id = tid

        # Auto-update thread title if default
        if self.thread_manager:
            th = self.thread_manager.get_thread(tid)
            if th and th.get("title") in ("Nueva conversación", "Nuevo hilo", ""):
                new_title = clean_thread_title(text)
                self.thread_manager.update_title(tid, new_title)
                self._update_header_title(new_title)

        target_cv = self._get_or_create_chat_view(tid)

        def run_ui(fn, *args):
            def _once():
                try:
                    fn(*args)
                except Exception as e:
                    logger.error(f"Error in UI callback: {e}")
                return False
            GLib.idle_add(_once)

        callbacks = AgentCallbacks(
            on_token=lambda tok, cv=target_cv: run_ui(cv.append_assistant_token, tok),
            on_thinking=lambda thought, cv=target_cv: run_ui(cv.append_assistant_thought, thought),
            on_tool_start=lambda name, args, cv=target_cv: run_ui(cv.start_tool_card, name, args),
            on_tool_end=lambda name, res, cv=target_cv: run_ui(cv.finish_tool_card, name, res),
            on_complete=lambda _final, t=tid, cv=target_cv: run_ui(self._on_turn_completed, t, cv),
            on_error=lambda err, t=tid, cv=target_cv: run_ui(self._on_turn_error, t, cv, err)
        )

        # Check if agent is currently speaking or running in this thread
        is_speaking = bool(self.tts.is_speaking and tid == self.agent.active_thread_id)
        is_running = self.agent.is_thread_running(tid)
        is_busy = is_running or is_speaking

        if is_busy:
            # Enqueue user message with visual queue indicator
            user_bubble = target_cv.add_user_message(text, is_queued=True, attachments=attachments)

            self.send_btn.set_visible(True)
            self.send_btn.set_tooltip_text("Añadir a la cola (Enter)")
            self.stop_btn.set_visible(True)

            def on_dequeued():
                def _start_turn():
                    user_bubble.set_queued(False)
                    target_cv.start_assistant_message()
                    self.stop_btn.set_visible(True)
                    self.update_status_indicators()
                    return False
                GLib.idle_add(_start_turn)

            # If agent has finished generating but TTS is currently speaking, register speech so queue waits for speech to end
            if is_speaking and not is_running:
                self.agent.register_speech_in_progress(tid)

            asyncio.run_coroutine_threadsafe(
                self.agent.run_turn(
                    text,
                    callbacks,
                    thread_id=tid,
                    on_dequeued=on_dequeued,
                    attachments=attachments
                ),
                self.async_loop
            )
            self._refresh_sidebar()
            self.update_status_indicators()
            return

        # Normal execution (session was idle)
        target_cv.add_user_message(text, is_queued=False, attachments=attachments)

        # Toggle buttons for current session
        self.send_btn.set_visible(True)
        self.send_btn.set_tooltip_text("Añadir a la cola (Enter)")
        self.stop_btn.set_visible(True)

        # Prepare assistant bubble in target session chat view
        target_cv.start_assistant_message()

        asyncio.run_coroutine_threadsafe(
            self.agent.run_turn(text, callbacks, thread_id=tid, attachments=attachments),
            self.async_loop
        )

        self._refresh_sidebar()
        self.update_status_indicators()

    def _on_turn_completed(self, thread_id: str, target_cv: ChatView):
        target_cv.finish_assistant_message()

        th = self.thread_manager.get_thread(thread_id) if self.thread_manager else None
        if thread_id == self.agent.active_thread_id:
            if th:
                self._update_header_title(th.get("title"))

            # Check if there are queued messages or session is still running
            has_queue = self.agent.has_queued_turns(thread_id)
            is_still_running = self.agent.is_thread_running(thread_id)
            if not has_queue and not is_still_running and not self.tts.is_speaking:
                self.send_btn.set_visible(True)
                self.send_btn.set_tooltip_text("Enviar (Enter)")
                self.stop_btn.set_visible(False)
            else:
                self.send_btn.set_visible(True)
                self.send_btn.set_tooltip_text("Añadir a la cola (Enter)")
                self.stop_btn.set_visible(True)

            self.text_view.grab_focus()

            # Voice reply if voice input
            was_voice = self._is_voice_turn
            self._is_voice_turn = False
            kokoro_cfg = self.config_manager.get_kokoro_config()
            if was_voice and kokoro_cfg.get("enabled", True) and kokoro_cfg.get("speak_on_voice_input", True):
                assistant_text = target_cv.last_assistant_text
                bubble = target_cv.last_assistant_bubble
                if assistant_text:
                    if has_queue:
                        self.agent.register_speech_in_progress(thread_id)
                    self._speak_bubble_text(assistant_text, bubble, thread_id=thread_id)
        else:
            # Completed in background while user was viewing another session
            title = th.get("title", "Sesión") if th else "Sesión"
            try:
                send_desktop_notification(
                    title=f"Sesión lista: {title}",
                    body="La respuesta de Pequeño Asistente ha finalizado.",
                    urgency="low"
                )
            except Exception:
                pass

        self._refresh_sidebar()
        self.update_status_indicators()
        return False

    def _on_turn_error(self, thread_id: str, target_cv: ChatView, error_message: str):
        target_cv.add_system_notice(f"⚠️ {error_message}", is_error=True)
        if thread_id == self.agent.active_thread_id:
            has_queue = self.agent.has_queued_turns(thread_id)
            is_still_running = self.agent.is_thread_running(thread_id)
            if not has_queue and not is_still_running and not self.tts.is_speaking:
                self.send_btn.set_visible(True)
                self.send_btn.set_tooltip_text("Enviar (Enter)")
                self.stop_btn.set_visible(False)
            else:
                self.send_btn.set_visible(True)
                self.send_btn.set_tooltip_text("Añadir a la cola (Enter)")
                self.stop_btn.set_visible(True)
            self.text_view.grab_focus()

        self._refresh_sidebar()
        self.update_status_indicators()
        return False

    def _on_stop_clicked(self, _btn):
        active_tid = self.agent.active_thread_id
        if active_tid:
            self.agent.abort(active_tid)
        self.tts.stop()
        if self.active_speaking_bubble:
            self.active_speaking_bubble.set_speaking_state(False)
            self.active_speaking_bubble = None
        self.send_btn.set_visible(True)
        self.send_btn.set_tooltip_text("Enviar (Enter)")
        self.stop_btn.set_visible(False)
        self._refresh_sidebar()
        self.update_status_indicators()
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        self._refresh_sidebar()
        self.update_status_indicators()

    def _on_clear_chat(self, _btn):
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()
        self.tts.stop()
        if self.active_speaking_bubble:
            self.active_speaking_bubble.set_speaking_state(False)
            self.active_speaking_bubble = None
        tid = self.agent.active_thread_id
        if tid:
            if self.agent.is_thread_running(tid):
                self.agent.abort(tid)
            self.agent.clear_history(tid)
            cv = self.chat_views.get(tid)
            if cv:
                cv.clear()
                cv.show_welcome(on_suggestion_clicked=self._on_suggestion_clicked)
        self._update_header_title("Pequeño Asistente")
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        self._refresh_sidebar()
        self.update_status_indicators()

    # -----------------------------------------------------------------
    # MODO LLAMADA (VOICE-TO-VOICE CALL MODE)
    # -----------------------------------------------------------------
    def _on_toggle_call(self):
        if self.call_engine.is_active:
            self._on_end_call()
        else:
            self._start_call()

    def _start_call(self):
        """Starts real-time voice call session."""
        if self.agent.is_running:
            self.agent.abort()
        self.tts.stop()

        # Hide threads sidebar if open
        self.split_view.set_show_sidebar(False)

        # Deactivate terminal button if active
        if hasattr(self, "terminal_btn"):
            self.terminal_btn.remove_css_class("terminal-btn-active")

        # Hide bottom input bar
        self.input_bar.set_visible(False)

        # Update header & menu status
        self.call_btn.set_icon_name("call-stop-symbolic")
        self.call_btn.set_tooltip_text("Finalizar llamada (Ctrl+L)")
        self.call_btn.remove_css_class("header-call-btn")
        self.call_btn.add_css_class("call-btn-active-mute")

        if hasattr(self, "menu_call_icon"):
            self.menu_call_icon.set_from_icon_name("call-stop-symbolic")
        if hasattr(self, "menu_call_label"):
            self.menu_call_label.set_text("Finalizar llamada")
        if hasattr(self, "menu_btn"):
            self.menu_btn.add_css_class("menu-btn-call-active")

        # Switch to Call View
        self.view_stack.set_visible_child_name("call")
        self.call_view.start_session()
        self.call_engine.start_call()

    def _on_end_call(self):
        """Hangs up and stops voice call engine."""
        self.call_engine.end_call()

    def _on_call_engine_ended(self):
        """Handles post-call UI restoration."""
        self.call_view.stop_session()
        self.call_btn.set_icon_name("call-start-symbolic")
        self.call_btn.set_tooltip_text("Iniciar Modo Llamada (Voz a Voz) (Ctrl+L)")
        self.call_btn.remove_css_class("call-btn-active-mute")
        self.call_btn.add_css_class("header-call-btn")

        if hasattr(self, "menu_call_icon"):
            self.menu_call_icon.set_from_icon_name("call-start-symbolic")
        if hasattr(self, "menu_call_label"):
            self.menu_call_label.set_text("Modo llamada de voz")
        if hasattr(self, "menu_btn"):
            self.menu_btn.remove_css_class("menu-btn-call-active")

        self.view_stack.set_visible_child_name("chat")
        self.input_bar.set_visible(True)
        self.text_view.grab_focus()

    # -----------------------------------------------------------------
    # MODO TERMINAL (INTERACTIVE PTY TERMINAL)
    # -----------------------------------------------------------------
    def _on_toggle_terminal(self):
        """Toggles between chat view and interactive terminal view."""
        cur = self.view_stack.get_visible_child_name()
        if cur == "terminal":
            self.view_stack.set_visible_child_name("chat")
            if hasattr(self, "terminal_btn"):
                self.terminal_btn.remove_css_class("terminal-btn-active")
            if hasattr(self, "menu_terminal_label"):
                self.menu_terminal_label.set_text("Terminal interactiva")
            if hasattr(self, "menu_btn"):
                self.menu_btn.remove_css_class("menu-btn-terminal-active")
            self.input_bar.set_visible(True)
            self.text_view.grab_focus()
        else:
            if hasattr(self, "call_engine") and self.call_engine.is_active:
                self._on_end_call()
            # Hide threads sidebar if open
            self.split_view.set_show_sidebar(False)
            self.view_stack.set_visible_child_name("terminal")
            if hasattr(self, "terminal_btn"):
                self.terminal_btn.add_css_class("terminal-btn-active")
            if hasattr(self, "menu_terminal_label"):
                self.menu_terminal_label.set_text("Cerrar terminal (Volver al chat)")
            if hasattr(self, "menu_btn"):
                self.menu_btn.add_css_class("menu-btn-terminal-active")
            self.input_bar.set_visible(False)
            self.terminal_view.grab_focus()

    def _on_call_mute_toggled(self) -> bool:
        return self.call_engine.toggle_mute()

    def _on_call_interrupt_requested(self):
        self.call_engine.interrupt_assistant()

    def _on_call_user_transcript(self, text: str):
        self.call_view.set_user_text(text)
        self.chat_view.add_user_message(text)

    def _on_call_turn_finished(self, user_text: str, assistant_text: str):
        if assistant_text:
            bubble = self.chat_view.start_assistant_message()
            bubble.set_text(assistant_text)
            self.chat_view.finish_assistant_message()
            self._refresh_sidebar()

    def _on_mic_clicked(self, _btn):
        """Toggles audio recording or completes transcription with faster-whisper."""
        # If assistant is speaking, stop playback immediately so user can speak
        if self.tts.is_speaking:
            self.tts.stop()
            if self.active_speaking_bubble:
                self.active_speaking_bubble.set_speaking_state(False)
                self.active_speaking_bubble = None

        if self.transcriber.is_transcribing:
            return

        if not self.transcriber.is_recording:
            # Start recording
            ok = self.transcriber.start_recording()
            if ok:
                self.mic_btn.add_css_class("mic-button-recording")
                self.mic_icon.set_from_icon_name("media-record-symbolic")
                self.mic_btn.set_tooltip_text("Grabando audio... Clic para detener y transcribir (Ctrl+M)")
            else:
                self.chat_view.add_system_notice(
                    "⚠️ No se pudo iniciar la grabación de audio. Verifica que tu micrófono y herramientas de sistema (pw-record o ffmpeg) estén disponibles.",
                    is_error=True
                )
        else:
            # Stop recording and start transcription
            self.mic_btn.remove_css_class("mic-button-recording")
            self.mic_btn.add_css_class("mic-button-transcribing")
            self.mic_btn.set_sensitive(False)
            self.mic_spinner.start()
            self.mic_stack.set_visible_child_name("spinner")
            self.mic_btn.set_tooltip_text("Transcribiendo con faster-whisper...")

            def on_transcribe_done(text: Optional[str], err: Optional[str]):
                GLib.idle_add(self._on_transcription_finished, text, err)

            self.transcriber.stop_and_transcribe_async(on_transcribe_done)

    def _on_transcription_finished(self, text: Optional[str], error: Optional[str]):
        """Callback executed on UI thread when transcription finishes."""
        self.mic_spinner.stop()
        self.mic_icon.set_from_icon_name("audio-input-microphone-symbolic")
        self.mic_stack.set_visible_child_name("icon")
        self.mic_btn.remove_css_class("mic-button-transcribing")
        self.mic_btn.set_sensitive(True)
        self.mic_btn.set_tooltip_text("Dictar voz (faster-whisper) (Ctrl+M)")

        if error:
            self.chat_view.add_system_notice(f"⚠️ Error al transcribir voz: {error}", is_error=True)
            return False

        if text and text.strip():
            transcribed = text.strip()
            self._is_voice_turn = True
            start, end = self.text_buffer.get_bounds()
            existing = self.text_buffer.get_text(start, end, True)
            if existing.strip():
                new_text = f"{existing.rstrip()} {transcribed}"
            else:
                new_text = transcribed

            self.text_buffer.set_text(new_text)
            end_iter = self.text_buffer.get_end_iter()
            self.text_buffer.place_cursor(end_iter)
            self.text_view.grab_focus()

            # Auto-send on voice if enabled
            audio_cfg = self.config_manager.get_audio_config()
            if audio_cfg.get("auto_send_on_voice", True):
                self._on_send_clicked(None)

        return False

    def _on_bubble_tts_requested(self, text: str, bubble: Any):
        """Triggered when user clicks the speaker button on an assistant bubble."""
        if self.tts.is_speaking and self.active_speaking_bubble == bubble:
            self.tts.stop()
            bubble.set_speaking_state(False)
            self.active_speaking_bubble = None
        else:
            self._speak_bubble_text(text, bubble)

    def _speak_bubble_text(self, text: str, bubble: Optional[Any] = None, thread_id: Optional[str] = None):
        """Speaks the text via Kokoro TTS and manages speaking state indicators."""
        if self.active_speaking_bubble and self.active_speaking_bubble != bubble:
            self.active_speaking_bubble.set_speaking_state(False)

        self.active_speaking_bubble = bubble
        target_tid = thread_id or self.agent.active_thread_id

        def on_start():
            def _ui_start():
                if bubble:
                    bubble.set_speaking_state(True)
                return False
            GLib.idle_add(_ui_start)

        def on_finish():
            if target_tid:
                self.agent.notify_speech_finished(target_tid)
            def _ui_finish():
                if bubble:
                    bubble.set_speaking_state(False)
                if self.active_speaking_bubble == bubble:
                    self.active_speaking_bubble = None
                if getattr(self.tts, "last_fallback_occurred", False):
                    reason = getattr(self.tts, "last_error_reason", "ElevenLabs no disponible; se usó Kokoro.")
                    self.chat_view.add_system_notice(f"ℹ️ {reason}")

                if target_tid == self.agent.active_thread_id:
                    has_queue = self.agent.has_queued_turns(target_tid)
                    is_running = self.agent.is_thread_running(target_tid)
                    if not has_queue and not is_running:
                        self.stop_btn.set_visible(False)
                        self.send_btn.set_visible(True)
                        self.send_btn.set_tooltip_text("Enviar (Enter)")
                    self.update_status_indicators()
                return False
            GLib.idle_add(_ui_finish)

        self.tts.speak_async(text, on_start=on_start, on_finish=on_finish)

    def _on_open_automations(self, _btn=None):
        dlg = AutomationManagerDialog(
            config_manager=self.config_manager,
            memory_manager=self.memory_manager,
            hb_manager=self.heartbeat_manager
        )
        dlg.set_transient_for(self)
        dlg.present()

    def _on_automation_output(self, data: Dict[str, Any]):
        """Dispatched when a background Heartbeat or Automation produces output."""
        def _ui():
            show_in_chat = data.get("show_in_chat", True)
            title = data.get("title", "Automatización")
            text = data.get("text", "")
            status = data.get("status", "ok")
            is_hb = data.get("id") == "global_heartbeat"

            if show_in_chat and text:
                self.chat_view.add_automation_card(
                    title=title,
                    text=text,
                    status=status,
                    is_heartbeat=is_hb
                )
                self._refresh_sidebar()

            if data.get("speak_tts", False) and text:
                self._speak_bubble_text(text)

            self.update_status_indicators()
            return False

        GLib.idle_add(_ui)

    def _on_open_settings(self, _btn):
        win = None

        def on_sync(force: bool = False):
            async def do_sync():
                await self.mcp_manager.sync_servers(self.config_manager.get_mcp_servers(), force=force)
                GLib.idle_add(self.update_status_indicators)
                if win:
                    GLib.idle_add(win.refresh_mcp_status)
            asyncio.run_coroutine_threadsafe(do_sync(), self.async_loop)
            GLib.idle_add(self.update_status_indicators)

        def on_model_changed(_new_model):
            GLib.idle_add(self.update_status_indicators)

        def on_audio_changed():
            # Update transcriber settings
            audio_cfg = self.config_manager.get_audio_config()
            self.transcriber.model_size = audio_cfg.get("whisper_model", "tiny")
            self.transcriber.language = audio_cfg.get("language")

            # Update Unified TTS settings (Kokoro & ElevenLabs)
            kokoro_cfg = self.config_manager.get_kokoro_config()
            el_cfg = self.config_manager.get_elevenlabs_config()

            self.tts.provider = audio_cfg.get("tts_provider", "kokoro")
            self.tts.fallback_to_kokoro = el_cfg.get("fallback_to_kokoro", True)
            self.tts.enabled = kokoro_cfg.get("enabled", True)

            # Kokoro
            self.tts.kokoro.url = kokoro_cfg.get("url", "http://localhost:8011").rstrip("/")
            self.tts.kokoro.voice = kokoro_cfg.get("voice", "es-MX-JorgeNeural")
            self.tts.kokoro.speed = float(kokoro_cfg.get("speed", 1.0))
            self.tts.kokoro.lang = kokoro_cfg.get("lang", "es")
            self.tts.kokoro.enabled = kokoro_cfg.get("enabled", True)

            # ElevenLabs
            self.tts.elevenlabs.api_key = el_cfg.get("api_key", "")
            self.tts.elevenlabs.voice_id = el_cfg.get("voice_id", "ErXwobaYiN019PkySvjV")
            self.tts.elevenlabs.model_id = el_cfg.get("model_id", "eleven_multilingual_v2")
            self.tts.elevenlabs.stability = float(el_cfg.get("stability", 0.5))
            self.tts.elevenlabs.similarity_boost = float(el_cfg.get("similarity_boost", 0.75))
            self.tts.elevenlabs.enabled = el_cfg.get("enabled", True)

        win = SettingsWindow(
            self.config_manager,
            self.memory_manager,
            mcp_manager=self.mcp_manager,
            on_sync_needed=on_sync,
            on_model_changed=on_model_changed,
            on_audio_changed=on_audio_changed,
            on_theme_changed=self._apply_theme
        )
        win.set_transient_for(self)
        win.present()

    def _on_toggle_theme(self):
        """Alterna rápidamente entre el modo oscuro y el modo claro."""
        is_currently_light = not Adw.StyleManager.get_default().get_dark()
        new_theme = "dark" if is_currently_light else "light"
        self.config_manager.set_theme(new_theme)
        self._apply_theme(new_theme)

    def _apply_theme(self, theme: Optional[str] = None):
        """Aplica el esquema de color en Libadwaita y sincroniza todos los componentes."""
        chosen = theme or self.config_manager.get_theme()
        style_mgr = Adw.StyleManager.get_default()
        if chosen == "light":
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif chosen == "dark":
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_mgr.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self._sync_effective_theme()

    def _sync_effective_theme(self):
        """Actualiza clases CSS, iconos y colores Pango/Cairo/Terminal según el modo activo."""
        is_light = not Adw.StyleManager.get_default().get_dark()
        set_chat_theme(is_light)

        targets = [self]
        if hasattr(self, "menu_popover") and self.menu_popover:
            targets.append(self.menu_popover)
        if hasattr(self, "skills_popover") and self.skills_popover:
            targets.append(self.skills_popover)

        for widget in targets:
            if is_light:
                widget.add_css_class("theme-light")
            else:
                widget.remove_css_class("theme-light")

        if hasattr(self, "theme_btn") and self.theme_btn:
            if is_light:
                self.theme_btn.set_icon_name("weather-clear-night-symbolic")
                self.theme_btn.set_tooltip_text("Cambiar a modo oscuro (Ctrl+D)")
            else:
                self.theme_btn.set_icon_name("weather-clear-symbolic")
                self.theme_btn.set_tooltip_text("Cambiar a modo claro (Ctrl+D)")

        if hasattr(self, "menu_theme_icon") and self.menu_theme_icon:
            self.menu_theme_icon.set_from_icon_name(
                "weather-clear-night-symbolic" if is_light else "weather-clear-symbolic"
            )
        if hasattr(self, "menu_theme_label") and self.menu_theme_label:
            self.menu_theme_label.set_text(
                "Cambiar a modo oscuro" if is_light else "Cambiar a modo claro"
            )

        if hasattr(self, "chat_views"):
            for cv in self.chat_views.values():
                cv.refresh_theme()

        if hasattr(self, "call_view") and self.call_view:
            self.call_view.set_theme(is_light)

        if hasattr(self, "terminal_view") and self.terminal_view:
            self.terminal_view.set_theme(is_light)

    def do_close_request(self):
        # Si se solicitó salir completamente, limpiamos recursos y cerramos
        if getattr(self, "really_quit", False):
            if hasattr(self, "call_engine") and self.call_engine.is_active:
                self._on_end_call()
            if hasattr(self, "terminal_view") and self.terminal_view:
                self.terminal_view.close()
            return super().do_close_request()

        # Al pulsar el botón de cerrar (X), ocultamos la ventana en vez de destruirla.
        # Esto mantiene los servidores MCP conectados y listos en segundo plano.
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()
        self.set_visible(False)
        return True  # Cancela la destrucción de la ventana

    def quit_app(self):
        """Cierra completamente la aplicación y desconecta todos los servidores MCP."""
        self.really_quit = True
        if hasattr(self, "heartbeat_manager") and self.heartbeat_manager:
            self.heartbeat_manager.stop()
        app = self.get_application()
        if app:
            app.quit()
        else:
            self.close()

    def toggle_window(self):
        """Toggle hide/show window."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self.set_visible(True)
            self.present()
            self.text_view.grab_focus()
