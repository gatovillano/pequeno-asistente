"""
Main Dropdown Window for Pequeño Asistente.
Built with GTK4 and Libadwaita.
Supports multi-thread persistent conversations with sliding sidebar and instant shortcuts.
"""
import os
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
from gi.repository import Gtk, Adw, GLib, Gdk, Pango

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager, format_relative_timestamp, clean_thread_title
from pequeno_asistente.audio.transcriber import AudioTranscriber
from pequeno_asistente.audio.tts import KokoroTTS, UnifiedTTS
from pequeno_asistente.audio.call_engine import CallEngine
from pequeno_asistente.ui.chat_view import ChatView
from pequeno_asistente.ui.call_view import CallView
from pequeno_asistente.ui.settings_window import SettingsWindow

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

        title = Gtk.Label(label="<b>Historial</b>", use_markup=True, xalign=0)
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

        # "➕ Nuevo chat" button
        new_btn = Gtk.Button()
        new_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_btn_box.set_halign(Gtk.Align.CENTER)
        plus_lbl = Gtk.Label(label="➕")
        text_lbl = Gtk.Label(label="Nuevo chat")
        text_lbl.add_css_class("new-thread-btn-text")
        new_btn_box.append(plus_lbl)
        new_btn_box.append(text_lbl)
        new_btn.set_child(new_btn_box)
        new_btn.add_css_class("new-thread-btn")
        new_btn.set_tooltip_text("Nuevo chat (Ctrl+N)")
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

    def populate(self, threads: List[Dict[str, Any]], active_thread_id: Optional[str]):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)

        if not threads:
            empty_lbl = Gtk.Label(label="No hay conversaciones previas")
            empty_lbl.add_css_class("dim-label")
            empty_lbl.set_margin_top(20)
            self.list_box.append(empty_lbl)
            return

        for th in threads:
            tid = th["id"]
            is_active = (tid == active_thread_id)

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row_box.add_css_class("thread-row")
            if is_active:
                row_box.add_css_class("thread-row-active")

            # Selection button (click row body to open thread)
            select_btn = Gtk.Button()
            select_btn.add_css_class("flat")
            select_btn.add_css_class("thread-select-btn")
            select_btn.set_hexpand(True)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            info_box.set_halign(Gtk.Align.START)

            title_txt = th.get("title") or "Conversación"
            title_lbl = Gtk.Label(label=title_txt, xalign=0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_max_width_chars(16)
            title_lbl.add_css_class("thread-item-title")
            info_box.append(title_lbl)

            date_str = format_relative_timestamp(th.get("updated_at") or th.get("created_at") or "")
            msg_count = th.get("message_count", 0)
            count_str = f"{msg_count} m" if msg_count != 1 else "1 m"
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
            del_btn.set_tooltip_text("Eliminar conversación")
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

        # 2. Main Stack (Chat View & Call View)
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_transition_duration(200)
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)

        self.chat_view = ChatView(on_tts_requested=self._on_bubble_tts_requested)
        self.view_stack.add_named(self.chat_view, "chat")

        self.call_view = CallView(
            on_end_call_requested=self._on_end_call,
            on_mute_toggled=self._on_call_mute_toggled,
            on_interrupt_requested=self._on_call_interrupt_requested
        )
        self.view_stack.add_named(self.call_view, "call")
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

        # Global key shortcuts on window
        win_key_controller = Gtk.EventControllerKey()
        win_key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(win_key_controller)

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

        # Right buttons:
        # 1. Call button (Toggle Voice-to-Voice Call Mode)
        self.call_btn = Gtk.Button(icon_name="call-start-symbolic")
        self.call_btn.set_tooltip_text("Iniciar Modo Llamada (Voz a Voz) (Ctrl+L)")
        self.call_btn.add_css_class("flat")
        self.call_btn.add_css_class("header-call-btn")
        self.call_btn.connect("clicked", lambda _b: self._on_toggle_call())
        header.pack_end(self.call_btn)

        # 2. Settings
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_btn.set_tooltip_text("Configuración (Modelos, MCPs, Memoria)")
        settings_btn.add_css_class("flat")
        settings_btn.add_css_class("header-icon-btn")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)

        # Clear chat in current thread
        clear_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_btn.set_tooltip_text("Limpiar conversación actual")
        clear_btn.add_css_class("flat")
        clear_btn.add_css_class("header-icon-btn")
        clear_btn.connect("clicked", self._on_clear_chat)
        header.pack_end(clear_btn)

        # Compact MCP status pill
        self.status_pill = Gtk.Label(label="● MCPs")
        self.status_pill.add_css_class("status-pill")
        header.pack_end(self.status_pill)

        return header

    def _build_input_bar(self) -> Gtk.Box:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrapper.add_css_class("input-container")

        # Inner capsule card
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("input-card")

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
        scrolled.set_child(self.text_view)
        bar.append(scrolled)

        # Enter key event handler on text view
        key_controller = Gtk.EventControllerKey()
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

        # Center input card in a matching clamp (ChatGPT / Claude style)
        input_clamp = Adw.Clamp()
        input_clamp.set_maximum_size(720)
        input_clamp.set_tightening_threshold(560)
        input_clamp.set_child(bar)
        wrapper.append(input_clamp)

        # Subtle bottom hint
        hint_label = Gtk.Label(label="<small>↵ Enviar  ·  ⇧↵ Salto  ·  Ctrl+M Dictar voz  ·  Ctrl+N Nuevo</small>", use_markup=True)
        hint_label.add_css_class("input-hint")
        hint_label.set_halign(Gtk.Align.CENTER)
        wrapper.append(hint_label)

        return wrapper

    def _show_welcome_message(self):
        self.chat_view.show_welcome(on_suggestion_clicked=self._on_suggestion_clicked)

    def _on_suggestion_clicked(self, prompt: str):
        self.text_buffer.set_text(prompt)
        self._on_send_clicked(None)

    def _init_thread(self):
        """Loads the most recent thread on startup, or creates a new one."""
        thread = self.thread_manager.get_or_create_active_thread()
        messages = thread.get("messages", [])
        self.agent.active_thread_id = thread["id"]
        self.agent.history = list(messages)
        self._update_header_title(thread.get("title"))

        if messages:
            self.chat_view.load_history(messages)
        else:
            self._show_welcome_message()

        self._refresh_sidebar()

    def _update_header_title(self, title: Optional[str]):
        if not title or title in ("Nueva conversación", "Nuevo hilo"):
            self.thread_title_label.set_markup("<b>Pequeño Asistente</b>")
        else:
            escaped = html.escape(title)
            self.thread_title_label.set_markup(f"<b>{escaped}</b>")

    def _refresh_sidebar(self):
        threads = self.thread_manager.list_threads()
        self.sidebar.populate(threads, self.thread_manager.active_thread_id)

    def _on_toggle_sidebar(self):
        vis = not self.split_view.get_show_sidebar()
        if vis:
            self._refresh_sidebar()
        self.split_view.set_show_sidebar(vis)

    def _on_new_thread(self):
        """Starts a fresh new conversation thread."""
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()

        if self.agent.is_running:
            self.agent.abort()
            self.send_btn.set_visible(True)
            self.stop_btn.set_visible(False)

        self.agent.new_thread(title="Nueva conversación")
        self.chat_view.clear()
        self._show_welcome_message()
        self._update_header_title("Pequeño Asistente")
        self.split_view.set_show_sidebar(False)
        self._refresh_sidebar()
        self.text_view.grab_focus()

    def _on_switch_thread(self, thread_id: str):
        """Switches to an existing thread."""
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()

        if self.agent.is_running:
            self.agent.abort()
            self.send_btn.set_visible(True)
            self.stop_btn.set_visible(False)

        th = self.thread_manager.get_thread(thread_id)
        if not th:
            return

        self.agent.load_thread(thread_id)
        messages = th.get("messages", [])
        self._update_header_title(th.get("title"))

        if messages:
            self.chat_view.load_history(messages)
        else:
            self.chat_view.clear()
            self._show_welcome_message()

        self.split_view.set_show_sidebar(False)
        self._refresh_sidebar()
        self.text_view.grab_focus()

    def _on_delete_thread(self, thread_id: str):
        """Deletes a thread and falls back to another or creates a new one."""
        is_active = (self.thread_manager.active_thread_id == thread_id)
        self.thread_manager.delete_thread(thread_id)

        if is_active:
            existing = self.thread_manager.list_threads()
            if existing:
                self._on_switch_thread(existing[0]["id"])
            else:
                self._on_new_thread()
        else:
            self._refresh_sidebar()

    def update_status_indicators(self):
        summary = self.mcp_manager.get_status_summary()
        conn = summary["connected"]
        tools = summary["total_tools"]
        self.status_pill.set_text(f"● {conn} MCPs")
        self.status_pill.set_tooltip_text(f"{conn} servidores MCP conectados ({tools} herramientas disponibles)")
        model_name = self.config_manager.get_active_model().split("/")[-1]
        self.model_pill.set_text(model_name)
        self.model_pill.set_tooltip_text(f"Modelo activo: {self.config_manager.get_active_model()}")

    def _on_window_key_pressed(self, _controller, keyval, _keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
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
        elif keyval == Gdk.KEY_Escape:
            if hasattr(self, "call_engine") and self.call_engine.is_active:
                self._on_end_call()
                return True
            if self.split_view.get_show_sidebar():
                self.split_view.set_show_sidebar(False)
                return True
        return False

    def _on_input_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not (state & Gdk.ModifierType.SHIFT_MASK):
                self._on_send_clicked(None)
                return True
        return False

    def _on_send_clicked(self, _btn):
        start, end = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(start, end, True).strip()
        if not text:
            return

        # Clear input
        self.text_buffer.set_text("")

        # Auto-update thread title if default
        active_id = self.agent.active_thread_id
        if active_id:
            th = self.thread_manager.get_thread(active_id)
            if th and th.get("title") in ("Nueva conversación", "Nuevo hilo", ""):
                new_title = clean_thread_title(text)
                self.thread_manager.update_title(active_id, new_title)
                self._update_header_title(new_title)

        # Add user message to chat view
        self.chat_view.add_user_message(text)

        # Set UI to running state
        self.send_btn.set_visible(False)
        self.stop_btn.set_visible(True)

        # Prepare assistant bubble
        self.chat_view.start_assistant_message()

        # Helper to ensure any idle_add function runs strictly once
        def run_ui(fn, *args):
            def _once():
                try:
                    fn(*args)
                except Exception as e:
                    logger.error(f"Error in UI callback: {e}")
                return False
            GLib.idle_add(_once)

        # Submit agent turn to background asyncio loop
        callbacks = AgentCallbacks(
            on_token=lambda tok: run_ui(self.chat_view.append_assistant_token, tok),
            on_tool_start=lambda name, args: run_ui(self.chat_view.start_tool_card, name, args),
            on_tool_end=lambda name, res: run_ui(self.chat_view.finish_tool_card, name, res),
            on_complete=lambda _final: run_ui(self._on_turn_completed),
            on_error=lambda err: run_ui(self._on_turn_error, err)
        )

        asyncio.run_coroutine_threadsafe(
            self.agent.run_turn(text, callbacks),
            self.async_loop
        )

    def _on_turn_completed(self):
        self.chat_view.finish_assistant_message()
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        self.text_view.grab_focus()
        self._refresh_sidebar()

        # Check if we should respond with Kokoro TTS speech
        was_voice = self._is_voice_turn
        self._is_voice_turn = False

        kokoro_cfg = self.config_manager.get_kokoro_config()
        if was_voice and kokoro_cfg.get("enabled", True) and kokoro_cfg.get("speak_on_voice_input", True):
            assistant_text = self.chat_view.last_assistant_text
            bubble = self.chat_view.last_assistant_bubble
            if assistant_text:
                self._speak_bubble_text(assistant_text, bubble)

        return False

    def _on_turn_error(self, error_message: str):
        self.chat_view.add_system_notice(f"⚠️ {error_message}", is_error=True)
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        self.text_view.grab_focus()
        self._refresh_sidebar()
        return False

    def _on_stop_clicked(self, _btn):
        self.agent.abort()
        self.tts.stop()
        if self.active_speaking_bubble:
            self.active_speaking_bubble.set_speaking_state(False)
            self.active_speaking_bubble = None
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)

    def _on_clear_chat(self, _btn):
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()
        self.tts.stop()
        if self.active_speaking_bubble:
            self.active_speaking_bubble.set_speaking_state(False)
            self.active_speaking_bubble = None
        self.agent.clear_history()
        self.chat_view.clear()
        self._show_welcome_message()
        self._update_header_title("Pequeño Asistente")
        self._refresh_sidebar()

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

        # Hide bottom input bar
        self.input_bar.set_visible(False)

        # Update header button
        self.call_btn.set_icon_name("call-stop-symbolic")
        self.call_btn.set_tooltip_text("Finalizar llamada (Ctrl+L)")
        self.call_btn.remove_css_class("header-call-btn")
        self.call_btn.add_css_class("call-btn-active-mute")

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

        self.view_stack.set_visible_child_name("chat")
        self.input_bar.set_visible(True)
        self.text_view.grab_focus()

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

    def _speak_bubble_text(self, text: str, bubble: Optional[Any] = None):
        """Speaks the text via Kokoro TTS and manages speaking state indicators."""
        if self.active_speaking_bubble and self.active_speaking_bubble != bubble:
            self.active_speaking_bubble.set_speaking_state(False)

        self.active_speaking_bubble = bubble

        def on_start():
            def _ui_start():
                if bubble:
                    bubble.set_speaking_state(True)
                return False
            GLib.idle_add(_ui_start)

        def on_finish():
            def _ui_finish():
                if bubble:
                    bubble.set_speaking_state(False)
                if self.active_speaking_bubble == bubble:
                    self.active_speaking_bubble = None
                if getattr(self.tts, "last_fallback_occurred", False):
                    reason = getattr(self.tts, "last_error_reason", "ElevenLabs no disponible; se usó Kokoro.")
                    self.chat_view.add_system_notice(f"ℹ️ {reason}")
                return False
            GLib.idle_add(_ui_finish)

        self.tts.speak_async(text, on_start=on_start, on_finish=on_finish)

    def _on_open_settings(self, _btn):
        def on_sync():
            async def do_sync():
                await self.mcp_manager.sync_servers(self.config_manager.get_mcp_servers())
                GLib.idle_add(self.update_status_indicators)
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
            on_sync_needed=on_sync,
            on_model_changed=on_model_changed,
            on_audio_changed=on_audio_changed
        )
        win.set_transient_for(self)
        win.present()

    def do_close_request(self):
        if hasattr(self, "call_engine") and self.call_engine.is_active:
            self._on_end_call()
        return super().do_close_request()

    def toggle_window(self):
        """Toggle hide/show window."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self.set_visible(True)
            self.present()
            self.text_view.grab_focus()
