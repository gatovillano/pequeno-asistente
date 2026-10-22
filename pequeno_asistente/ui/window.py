"""
Main Dropdown Window for Pequeño Asistente.
Built with GTK4 and Libadwaita.
"""
import os
import asyncio
import threading
from pathlib import Path
from typing import Optional

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.ui.chat_view import ChatView
from pequeno_asistente.ui.settings_window import SettingsWindow

class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        config_manager: ConfigManager,
        agent: AgentCore,
        mcp_manager: MCPManager,
        memory_manager: MemoryManager,
        async_loop: asyncio.AbstractEventLoop
    ):
        super().__init__(application=app)
        self.config_manager = config_manager
        self.agent = agent
        self.mcp_manager = mcp_manager
        self.memory_manager = memory_manager
        self.async_loop = async_loop

        self.set_title("Pequeño Asistente")
        self.set_default_size(
            self.config_manager.data["ui"].get("window_width", 480),
            self.config_manager.data["ui"].get("window_height", 650)
        )
        self.add_css_class("asistente-window")

        # Load CSS
        self._load_css()

        # Build UI layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # 1. Header Bar
        self.header = self._build_header()
        main_box.append(self.header)

        # 2. Chat View
        self.chat_view = ChatView()
        main_box.append(self.chat_view)

        # 3. Bottom Input Area
        input_bar = self._build_input_bar()
        main_box.append(input_bar)

        # Initial welcome message
        self._show_welcome_message()

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

        # Left: MCP status pill
        self.status_pill = Gtk.Label(label="MCP: ...")
        self.status_pill.add_css_class("status-pill")
        header.pack_start(self.status_pill)

        # Center Title & Model pill
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_valign(Gtk.Align.CENTER)
        
        main_title = Gtk.Label(label="<b>Pequeño Asistente</b>", use_markup=True)
        title_box.append(main_title)

        self.model_pill = Gtk.Label(label=self.config_manager.get_active_model())
        self.model_pill.add_css_class("model-pill")
        title_box.append(self.model_pill)

        header.set_title_widget(title_box)

        # Right buttons
        # 1. Clear chat
        clear_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_btn.set_tooltip_text("Limpiar chat")
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", self._on_clear_chat)
        header.pack_end(clear_btn)

        # 2. Settings
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_btn.set_tooltip_text("Configuración (Modelos, MCPs, Memoria)")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)

        return header

    def _build_input_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("input-box")

        # Multiline text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(42)
        scrolled.set_max_content_height(100)
        scrolled.set_hexpand(True)
        scrolled.add_css_class("chat-input")

        self.text_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.text_view.set_hexpand(True)
        self.text_buffer = self.text_view.get_buffer()
        scrolled.set_child(self.text_view)
        bar.append(scrolled)

        # Enter key event handler
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.text_view.add_controller(key_controller)

        # Stop button (hidden by default)
        self.stop_btn = Gtk.Button(icon_name="process-stop-symbolic")
        self.stop_btn.add_css_class("stop-button")
        self.stop_btn.set_tooltip_text("Detener generación")
        self.stop_btn.set_visible(False)
        self.stop_btn.connect("clicked", self._on_stop_clicked)
        bar.append(self.stop_btn)

        # Send button
        self.send_btn = Gtk.Button(icon_name="mail-send-symbolic")
        self.send_btn.add_css_class("send-button")
        self.send_btn.set_tooltip_text("Enviar (Enter)")
        self.send_btn.connect("clicked", self._on_send_clicked)
        bar.append(self.send_btn)

        return bar

    def _show_welcome_message(self):
        user_name = "Usuario"
        welcome_text = (
            f"👋 **¡Hola! Soy tu Pequeño Asistente de escritorio.**\n\n"
            f"Opero exclusivamente con servidores **MCP**. "
            f"Pregúntame cualquier cosa o pídeme realizar tareas en tu sistema.\n\n"
            f"💡 *Pulsa el icono de engranaje ⚙️ para explorar el catálogo de servidores MCP, cambiar de modelo o editar tu memoria.*"
        )
        self.chat_view.start_assistant_message()
        self.chat_view.append_assistant_token(welcome_text)

    def update_status_indicators(self):
        summary = self.mcp_manager.get_status_summary()
        conn = summary["connected"]
        tools = summary["total_tools"]
        self.status_pill.set_text(f"🟢 {conn} MCPs ({tools} herramientas)")
        self.model_pill.set_text(self.config_manager.get_active_model().split("/")[-1])

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            # Check if Shift is pressed
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
        return False

    def _on_turn_error(self, error_message: str):
        self.chat_view.add_system_notice(f"⚠️ {error_message}", is_error=True)
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        self.text_view.grab_focus()
        return False

    def _on_stop_clicked(self, _btn):
        self.agent.abort()
        self.send_btn.set_visible(True)
        self.stop_btn.set_visible(False)

    def _on_clear_chat(self, _btn):
        self.agent.clear_history()
        self.chat_view.clear()
        self._show_welcome_message()

    def _on_open_settings(self, _btn):
        def on_sync():
            # Trigger sync of MCP servers on background loop
            async def do_sync():
                await self.mcp_manager.sync_servers(self.config_manager.get_mcp_servers())
                GLib.idle_add(self.update_status_indicators)
            asyncio.run_coroutine_threadsafe(do_sync(), self.async_loop)
            GLib.idle_add(self.update_status_indicators)

        def on_model_changed(_new_model):
            GLib.idle_add(self.update_status_indicators)

        win = SettingsWindow(
            self.config_manager,
            self.memory_manager,
            on_sync_needed=on_sync,
            on_model_changed=on_model_changed
        )
        win.set_transient_for(self)
        win.present()

    def toggle_window(self):
        """Toggle hide/show window."""
        if self.get_visible():
            self.set_visible(False)
        else:
            self.set_visible(True)
            self.present()
            self.text_view.grab_focus()
