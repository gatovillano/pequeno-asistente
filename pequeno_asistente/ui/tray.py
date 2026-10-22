"""
Socket IPC and Toggle Server for Pequeño Asistente.
Allows waybar, hyprland shortcuts, or CLI to show/hide/toggle the window.
"""
import os
import sys
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

SOCKET_PATH = Path.home() / ".config" / "pequeno-asistente" / "asistente.sock"

def send_toggle_command() -> bool:
    """
    Sends a toggle command to a running assistant instance.
    Returns True if successfully sent, False if server is not running.
    """
    if not SOCKET_PATH.exists():
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(b"toggle\n")
        response = client.recv(1024)
        client.close()
        return True
    except Exception:
        # Stale socket
        try:
            SOCKET_PATH.unlink()
        except Exception:
            pass
        return False


class ToggleSocketServer:
    def __init__(self, on_toggle: Callable[[], None], on_show: Callable[[], None]):
        self.on_toggle = on_toggle
        self.on_show = on_show
        self.running = False
        self.server_thread: Optional[threading.Thread] = None

    def start(self):
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass

        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(str(SOCKET_PATH))
        self.server_sock.listen(5)
        self.running = True

        self.server_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.server_thread.start()

    def _listen_loop(self):
        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                data = conn.recv(1024).decode("utf-8").strip()
                if data == "toggle":
                    GLib.idle_add(self.on_toggle)
                    conn.sendall(b"OK: toggled\n")
                elif data == "show":
                    GLib.idle_add(self.on_show)
                    conn.sendall(b"OK: shown\n")
                elif data == "ping":
                    conn.sendall(b"PONG\n")
                conn.close()
            except Exception:
                if not self.running:
                    break

    def stop(self):
        self.running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass
