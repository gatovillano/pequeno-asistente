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

# Si se detecta GNOME o está instalado gnome-shell, ofrecer/instalar la extensión
if command -v gnome-shell >/dev/null 2>&1; then
    echo "--- INSTALANDO EXTENSIÓN DE GNOME SHELL ---"
    "$BASE_DIR/scripts/install_gnome_extension.sh" || true
    echo ""
fi

echo "--- CONFIGURACIÓN RECOMENDADA PARA GNOME SHELL ---"
cat << 'EOF'
La extensión para la barra superior ha sido instalada y registrada en:
  ~/.local/share/gnome-shell/extensions/pequeno-asistente@gatovillano.com

• Clic izquierdo en el icono: Despliega / Oculta el asistente inmediatamente.
• Clic derecho en el icono: Abre el menú contextual (Configuración, Limpiar, Reiniciar, Salir).
• En sesiones Wayland, la primera vez puede requerir cerrar sesión e iniciarla nuevamente para que GNOME Shell monte la extensión.
EOF

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
