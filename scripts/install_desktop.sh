#!/usr/bin/env bash
# Installation & Desktop Integration script for Pequeño Asistente

set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DEST="$HOME/.local/bin"
DESKTOP_DEST="$HOME/.local/share/applications"

mkdir -p "$BIN_DEST"
mkdir -p "$DESKTOP_DEST"

WRAPPER_SCRIPT="$BIN_DEST/pequeno-asistente"
cat << 'EOF' > "$WRAPPER_SCRIPT"
#!/usr/bin/env bash
PROJECT_DIR="/home/gato/Proyectos/Pequeño asistente"
source "$PROJECT_DIR/.venv/bin/activate"
exec python3 "$PROJECT_DIR/pequeno_asistente/main.py" "$@"
EOF
chmod +x "$WRAPPER_SCRIPT"

DESKTOP_FILE="$DESKTOP_DEST/pequeno-asistente.desktop"
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=Pequeño Asistente
Comment=Asistente de escritorio ágil basado exclusivamente en MCP y LiteLLM
Exec=$WRAPPER_SCRIPT --toggle
Icon=$BASE_DIR/assets/icon.svg
Terminal=false
Type=Application
Categories=Utility;Development;AudioVideo;
StartupNotify=false
StartupWMClass=com.gatovillano.pequenoasistente
EOF
chmod +x "$DESKTOP_FILE"

echo "✅ Binario instalado en: $WRAPPER_SCRIPT"
echo "✅ Acceso directo instalado en: $DESKTOP_FILE"
echo ""
echo "--- CONFIGURACIÓN RECOMENDADA PARA HYPRLAND (~/.config/hypr/hyprland.conf) ---"
cat << 'EOF'
# Ventana flotante estilo dropdown
windowrulev2 = float, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = size 480 650, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = move 100%-500 42, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = pin, class:^(com.gatovillano.pequenoasistente)$

# Atajo global Super + A
bind = $mainMod, A, exec, ~/.local/bin/pequeno-asistente --toggle
EOF

echo ""
echo "--- CONFIGURACIÓN PARA WAYBAR (~/.config/waybar/config.jsonc) ---"
cat << 'EOF'
"custom/assistant": {
    "format": "🤖",
    "tooltip-format": "Pequeño Asistente (Super+A)",
    "on-click": "~/.local/bin/pequeno-asistente --toggle"
}
EOF
echo ""
echo "🎉 ¡Instalación completa!"
