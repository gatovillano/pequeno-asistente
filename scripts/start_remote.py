#!/usr/bin/env python3
"""
Remote Launcher for Pequeño Asistente.
Runs the FastAPI backend and establishes a Cloudflare Tunnel,
printing the public URL and configuration for the Android App.
"""
import os
import sys
import re
import time
import shutil
import signal
import subprocess
import threading
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pequeno_asistente.config import ConfigManager

def check_cloudflared() -> str:
    path = shutil.which("cloudflared")
    if not path:
        print("\n❌ Error: 'cloudflared' no está instalado en el sistema.")
        print("💡 Para instalarlo en Ubuntu/Debian:")
        print("   curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null")
        print("   echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main' | sudo tee /etc/apt/sources.list.d/cloudflared.list")
        print("   sudo apt update && sudo apt install cloudflared\n")
        sys.exit(1)
    return path

def main():
    cfg = ConfigManager()
    server_cfg = cfg.get_server_config()
    port = server_cfg.get("port", 8765)
    host = "127.0.0.1"
    api_key = server_cfg.get("api_key") or os.environ.get("PEQUENO_API_KEY") or ""

    cloudflared_bin = check_cloudflared()

    print("=" * 65)
    print("🤖 PEQUEÑO ASISTENTE - MODO ACCESO REMOTO (CLOUDFLARE TUNNEL)")
    print("=" * 65)
    print(f"📡 Puerto local: {port}")
    if api_key:
        print(f"🔑 API Key activa: {api_key}")
    else:
        print("⚠️  Sin API Key configurada. Puedes definir una en ~/.config/pequeno-asistente/config.json")
    print("=" * 65)

    # 1. Start FastAPI server
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    py_exec = str(venv_python) if venv_python.exists() else sys.executable

    server_cmd = [
        py_exec,
        "-m", "pequeno_asistente.server",
        "--host", host,
        "--port", str(port)
    ]

    print("\n⏳ 1/2 Iniciando servidor FastAPI local...")
    server_proc = subprocess.Popen(
        server_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # 2. Start Cloudflare Tunnel
    tunnel_cmd = [
        cloudflared_bin,
        "tunnel",
        "--url", f"http://{host}:{port}",
        "--no-autoupdate"
    ]

    print("⏳ 2/2 Conectando túnel Cloudflare...")
    tunnel_proc = subprocess.Popen(
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    public_url = None
    url_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def monitor_tunnel():
        nonlocal public_url
        for line in tunnel_proc.stdout:
            match = url_regex.search(line)
            if match and not public_url:
                public_url = match.group(0)
                print("\n" + "🎉" * 25)
                print(f"🌐 ¡TÚNEL ACTIVO EXITOSAMENTE!")
                print(f"👉 URL PÚBLICA PARA ANDROID:")
                print(f"   \033[1;32m{public_url}\033[0m")
                print("🎉" * 25)
                print("\n📱 CÓMO CONECTAR DESDE TU APP ANDROID:")
                print(f"   1. Abre la app Pequeño Asistente en tu móvil.")
                print(f"   2. Ve a Ajustes / Conexión.")
                print(f"   3. Pega la URL: {public_url}")
                if api_key:
                    print(f"   4. Ingresa tu API Key: {api_key}")
                print(f"   5. Toca 'Guardar y Probar Conexión'. ¡Listo!\n")
                print("Presiona Ctrl+C para detener el servidor y cerrar el túnel.\n")
                print("-" * 65)

    threading.Thread(target=monitor_tunnel, daemon=True).start()

    def signal_handler(sig, frame):
        print("\n\n🛑 Deteniendo servidor y túnel Cloudflare...")
        try:
            tunnel_proc.terminate()
            tunnel_proc.wait(timeout=3)
        except Exception:
            tunnel_proc.kill()
        try:
            server_proc.terminate()
            server_proc.wait(timeout=3)
        except Exception:
            server_proc.kill()
        print("✅ Todo cerrado limpiamente.")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Stream server logs or wait
        while True:
            line = server_proc.stdout.readline()
            if not line and server_proc.poll() is not None:
                break
            if line:
                # Optionally filter or print server logs
                pass
            time.sleep(0.1)
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == "__main__":
    main()
