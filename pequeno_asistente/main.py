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
from pequeno_asistente.log_stream import setup_realtime_logging, LOG_FILE_PATH
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager
from pequeno_asistente.mcp_client.manager import MCPManager
from pequeno_asistente.agent.core import AgentCore
from pequeno_asistente.ui.tray import (
    send_toggle_command,
    send_new_thread_command,
    send_quit_command,
    ToggleSocketServer,
    StatusNotifierTray,
)

# Setup real-time file + broadcaster + stderr logging
setup_realtime_logging(level=logging.INFO)
logger = logging.getLogger("pequeno_asistente")

def main():
    # Handle --logs / -l command line arguments to tail real-time logs in terminal
    if "--logs" in sys.argv:
        import subprocess
        print(f"📡 Siguiendo registros en tiempo real desde: {LOG_FILE_PATH} (Ctrl+C para salir)\n")
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE_PATH.touch(exist_ok=True)
        try:
            subprocess.run(["tail", "-n", "80", "-f", str(LOG_FILE_PATH)])
        except KeyboardInterrupt:
            pass
        sys.exit(0)

    # Handle --quit / -q command line arguments
    if "--quit" in sys.argv or "-q" in sys.argv:
        if send_quit_command():
            logger.info("Sent quit command to running assistant instance.")
            sys.exit(0)
        else:
            logger.info("No running assistant instance found.")
            sys.exit(0)

    # Handle --new-thread / -n command line arguments
    if "--new-thread" in sys.argv or "-n" in sys.argv or "--new" in sys.argv:
        if send_new_thread_command():
            sys.exit(0)

    # Handle --cli / -c command line arguments to run full CLI version
    if "--cli" in sys.argv or "-c" in sys.argv:
        from pequeno_asistente.cli.app import main as cli_main
        cli_args = [a for a in sys.argv[1:] if a not in ("--cli", "-c")]
        sys.exit(cli_main(cli_args))

    # Handle --server / -s command line arguments to run headless FastAPI server
    if "--server" in sys.argv or "-s" in sys.argv:
        from pequeno_asistente.server import run_server
        port = None
        host = None
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
            elif arg == "--host" and i + 1 < len(sys.argv):
                host = sys.argv[i + 1]
        run_server(host=host, port=port)
        sys.exit(0)

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
    thread_manager = ThreadManager()
    mcp_manager = MCPManager(config_manager=config_manager)
    agent = AgentCore(config_manager, mcp_manager, memory_manager, thread_manager=thread_manager)

    # 3. Synchronize MCP servers in background
    async def init_mcps():
        logger.info("Initializing configured MCP servers...")
        await mcp_manager.sync_servers(config_manager.get_mcp_servers())
        logger.info("MCP servers synchronized.")

    asyncio.run_coroutine_threadsafe(init_mcps(), async_loop)

    # 4. Create GTK Application
    app = Adw.Application(application_id="com.gatovillano.pequenoasistente")
    main_window = None
    tray_icon = None

    def on_activate(application):
        nonlocal main_window
        if not main_window:
            application.hold()  # Keeps application alive when window is hidden
            main_window = MainWindow(
                app=application,
                config_manager=config_manager,
                agent=agent,
                mcp_manager=mcp_manager,
                memory_manager=memory_manager,
                thread_manager=thread_manager,
                async_loop=async_loop
            )
            main_window.tray_icon = tray_icon
        main_window.unminimize()
        main_window.set_visible(True)
        main_window.present()

    app.connect("activate", on_activate)

    # 5. Socket IPC & Native StatusNotifierItem Tray Icon
    def handle_toggle():
        if main_window:
            main_window.toggle_window()

    def handle_show():
        if main_window:
            main_window.unminimize()
            main_window.set_visible(True)
            main_window.present()

    def handle_hide():
        if main_window:
            main_window.set_visible(False)

    def handle_settings():
        if main_window:
            main_window.unminimize()
            main_window.set_visible(True)
            main_window.present()
            main_window._on_open_settings(None)

    def handle_clear():
        if main_window:
            main_window._on_clear_chat(None)

    def handle_new_thread():
        if main_window:
            main_window.unminimize()
            main_window.set_visible(True)
            main_window.present()
            main_window._on_new_thread()

    def handle_quit():
        if main_window:
            main_window.quit_app()
        else:
            app.quit()

    tray_icon = StatusNotifierTray(
        on_toggle=handle_toggle,
        on_new_thread=handle_new_thread,
        on_settings=handle_settings,
        on_quit=handle_quit,
    )
    tray_icon.start()

    socket_server = ToggleSocketServer(
        on_toggle=handle_toggle,
        on_show=handle_show,
        on_hide=handle_hide,
        on_settings=handle_settings,
        on_clear=handle_clear,
        on_new_thread=handle_new_thread,
        on_quit=handle_quit
    )
    socket_server.start()

    # 6. Cleanup on exit
    def cleanup():
        logger.info("Shutting down Pequeño Asistente...")
        tray_icon.stop()
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
