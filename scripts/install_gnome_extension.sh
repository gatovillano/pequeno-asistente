#!/usr/bin/env bash
# Installation script for Pequeño Asistente GNOME Shell Extension

set -e

UUID="pequeno-asistente@gatovillano.com"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_SRC="$BASE_DIR/gnome-extension/$UUID"
EXT_DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

echo "📦 Instalando extensión de GNOME Shell: $UUID..."

if [ ! -d "$EXT_SRC" ]; then
    echo "❌ Error: Directorio de extensión no encontrado en $EXT_SRC"
    exit 1
fi

mkdir -p "$EXT_DEST"
cp -r "$EXT_SRC/"* "$EXT_DEST/"

echo "✅ Archivos copiados a: $EXT_DEST"

# Check if gnome-extensions CLI is available
if command -v gnome-extensions >/dev/null 2>&1; then
    echo "🔄 Habilitando extensión..."
    # Disable first if was enabled to force refresh
    gnome-extensions disable "$UUID" 2>/dev/null || true
    sleep 0.5
    gnome-extensions enable "$UUID" 2>/dev/null || true
    echo "🎉 ¡Extensión instalada y habilitada con éxito!"
    echo "💡 Nota: Si estás en sesión Wayland de GNOME y no aparece de inmediato,"
    echo "   cierra e inicia sesión nuevamente o ejecuta: gnome-extensions enable $UUID"
else
    echo "⚠️ 'gnome-extensions' CLI no encontrado en PATH."
    echo "   Por favor habilita la extensión usando la app 'Extensiones' de GNOME."
fi
