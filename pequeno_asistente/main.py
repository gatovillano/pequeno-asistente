"""
Main Entrypoint for Pequeño Asistente.
A lightweight, MCP-only desktop AI assistant for Linux (Hyprland / Wayland / GNOME).
"""
import sys
import os
import signal
import asyncio
import threading
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.core import AgentCore
from pequeno_asistente.ui.tray import send_toggle_command, ToggleSocketServer

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pequeno_asistente")

def main():
    # Handle --toggle or --show command line arguments
    if "--toggle" in sys.argv or "-t" in sys.argv:
        if send_toggle_command():
            sys.exit(0)
        # Otherwise, not running yet, so continue to launch

    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw, GLib

    from pequeno_asistente.ui.window import MainWindow

    # 1. Start background asyncio event loop
    async_loop = asyncio.new_event_loop()
    def run_loop():
        asyncio.set_event_loop(async_loop)
        async_loop.run_forever()

    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()

    # 2. Initialize Managers
    config_manager = ConfigManager()
    memory_manager = MemoryManager()
    mcp_manager = MCPManager(config_manager=config_manager)
    agent = AgentCore(config_manager, mcp_manager, memory_manager)

    # 3. Synchronize MCP servers in background
    async def init_mcps():
        logger.info("Initializing configured MCP servers...")
        await mcp_manager.sync_servers(config_manager.get_mcp_servers())
        logger.info("MCP servers synchronized.")

    asyncio.run_coroutine_threadsafe(init_mcps(), async_loop)

    # 4. Create GTK Application
    app = Adw.Application(application_id="com.gatovillano.pequenoasistente")
    main_window = None

    def on_activate(application):
        nonlocal main_window
        if not main_window:
            main_window = MainWindow(
                app=application,
                config_manager=config_manager,
                agent=agent,
                mcp_manager=mcp_manager,
                memory_manager=memory_manager,
                async_loop=async_loop
            )
        main_window.present()

    app.connect("activate", on_activate)

    # 5. Socket IPC for instant toggling from Waybar or Hyprland shortcut
    def handle_toggle():
        if main_window:
            main_window.toggle_window()

    def handle_show():
        if main_window:
            main_window.set_visible(True)
            main_window.present()

    socket_server = ToggleSocketServer(handle_toggle, handle_show)
    socket_server.start()

    # 6. Cleanup on exit
    def cleanup():
        logger.info("Shutting down Pequeño Asistente...")
        socket_server.stop()
        # Shutdown MCPs
        try:
            future = asyncio.run_coroutine_threadsafe(mcp_manager.shutdown(), async_loop)
            future.result(timeout=3)
        except Exception:
            pass
        async_loop.call_soon_threadsafe(async_loop.stop)

    def sig_handler(sig, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        app.run(None)
    finally:
        cleanup()

if __name__ == "__main__":
    main()
