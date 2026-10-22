# 🤖 Pequeño Asistente de Escritorio

Un asistente de escritorio ágil, moderno y minimalista diseñado para Linux (Hyprland, Wayland, GNOME), cuyo funcionamiento se basa **exclusivamente en servidores MCP (Model Context Protocol)** y un **sistema de memoria en capas persistente** (estilo OpenClaw).

---

## 🌟 Características Principales

1. **Agente Puro MCP (Sin herramientas hardcodeadas)**:
   - El agente no tiene herramientas internas ni comandos fijos.
   - Todas sus capacidades (captura de pantalla, control de ventanas, lectura de archivos, búsqueda web, reloj, bases de datos) se descubren y ejecutan dinámicamente a través de los servidores MCP conectados.

2. **Acceso Universal a LLMs (vía LiteLLM) & Descubrimiento en Tiempo Real**:
   - **Carga de Modelos Directo de la API**: Selector de proveedor con botón **"🔄 Cargar desde API"** que consulta en tiempo real los catálogos oficiales (por ejemplo, los más de 400 modelos de OpenRouter con contexto y precios, modelos de Google Gemini, OpenAI, Claude, Ollama local/cloud, etc.).
   - **Buscador en Vivo**: Filtra al instante cientos de modelos escribiendo en la barra de búsqueda integrada.
   - **Gestión y Validación de API Keys**: Módulo dedicado para configurar claves con botón **"🧪 Validar Clave"** que prueba la conexión real contra la API correspondiente (HTTP 200, límites de uso, o reporta errores 401 si la clave es errónea).
   - Compatible con los mismos proveedores del proyecto **KogniTerm** (`gatovillano/KogniTerm`):
     - **OpenRouter** (Claude 3.5, Llama 3.3 70B, DeepSeek, etc.)
     - **Google Gemini** (Gemini 2.5 Flash / Pro)
     - **OpenAI** (GPT-4o, GPT-4o-mini, o1, o3-mini)
     - **Anthropic** (Claude 3.5 Sonnet / Haiku)
     - **Ollama Local & Cloud** (Llama 3.2, Qwen 2.5, DeepSeek)
     - **Antigravity Gateway, KiloCode, Inception Labs, OpenCode Zen**

3. **Sistema de Memoria en Capas (Estilo OpenClaw)**:
   Almacenada en texto plano Markdown en `~/.config/pequeno-asistente/memory/`:
   - **Capa 1 (`USER.md`)**: Perfil de usuario, rol, stack de desarrollo, entorno del sistema (Hyprland, Ubuntu, Zsh) y directrices.
   - **Capa 2 (`MEMORY.md`)**: Hechos duraderos aprendidos, notas del sistema, decisiones tomadas y preferencias recordadas.
   - **Capa 3 (`daily/YYYY-MM-DD.md`)**: Bitácora cronológica diaria de conversaciones.
   - **Editor Visual**: Pestaña dedicada en la Configuración para inspeccionar y editar tu memoria en cualquier momento.

4. **Extenso Catálogo & Auto-Instalación de Servidores MCP**:
   - **Auto-instalación por Chat**: Puedes pedirle directamente en el chat que se instale un servidor pasándole el enlace de GitHub o información del paquete (ej: *"Instálate el MCP de Context7: https://github.com/upstash/context7"* o *"Busca e instala el MCP de weather"*). El asistente buscará en el registro, configurará el comando e instalará el servidor en caliente.
   - **Importar JSON Completo de Configuración**: En la interfaz de configuración (pestaña Catálogo MCP ➔ *+ Añadir Servidor Local*), dispones de un modo para **pegar el JSON completo** de configuración, compatible directamente con el formato de Claude Desktop (`{"mcpServers": { ... }}`) o definiciones directas.
   - **Fuentes y Repositorios Conectados**: Conexión nativa al registro global **Smithery AI** (`https://api.smithery.ai/servers`, más de 10,000 servidores) y al catálogo oficial de Anthropic (`modelcontextprotocol/servers`).
   - **Añadir Repositorios Propios**: Botón **"➕ Añadir Repositorio"** para agregar cualquier URL de registro externo (APIs compatibles, repositorios Git o archivos JSON con manifiestos MCP).
   - **Búsqueda e Instalación en 1 Clic**: Busca servidores online por nombre o palabra clave (ej: `postgres`, `slack`, `notion`, `weather`, `linear`, `docker`) e instálalos directamente con un solo clic.
   - **Servidores Pre-configurados Listos**:
     - 🪟 **hypruse**: Control completo de ventanas Hyprland, capturas y clics UI.
     - 📁 **filesystem**: Explorador de archivos locales (`@modelcontextprotocol/server-filesystem`).
     - 🌐 **fetch**: Navegador web y conversor de páginas a Markdown (`mcp-server-fetch`).
     - 💻 **desktop-bash**: Terminal y diagnóstico Linux.
     - 🦆 **duckduckgo**: Búsqueda web abierta sin API Key.
     - 🦁 **brave-search** / 🔍 **tavily**: Búsqueda web con APIs dedicadas.
     - 🌿 **git** & 🐙 **github**: Historial de código, commits, ramas e issues.
     - 🗄️ **sqlite**: Consultas a bases de datos locales.
     - 🕸️ **memory-graph**: Grafo de conocimiento de entidades (@modelcontextprotocol/server-memory).
     - ⏰ **time** & ⛅ **weather**: Reloj de zonas horarias y meteorología.
     - 🎹 **bitwig**: Control de Bitwig Studio.
     - 🧠 **kognito-ai**: Integración con KognitoAI.
     - 🧩 **Servidores Personalizados**: Añade cualquier servidor stdio o SSE (`uvx`, `npx`, `python`, URLs).

5. **Ventana Desplegable (Dropdown) desde la Barra Superior**:
   - Se despliega como un widget flotante en la esquina superior derecha con bordes redondeados y tema oscuro Libadwaita.
   - Se oculta/muestra al instante (socket IPC <5ms) con un atajo o clic en Waybar.

---

## 🚀 Instalación y Puesta en Marcha

### 1. Activar el entorno e instalar dependencias
```bash
cd "/home/gato/Proyectos/Pequeño asistente"
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Instalar el lanzador de escritorio
Ejecuta el script de instalación para registrar el comando `pequeno-asistente` y su acceso directo:
```bash
./scripts/install_desktop.sh
```

### 3. Integración con Waybar (Barra Superior)
Edita `~/.config/waybar/config.jsonc` y añade en `"modules-right"` el módulo:
```jsonc
"custom/assistant": {
    "format": "🤖",
    "tooltip-format": "Pequeño Asistente (Super+A)",
    "on-click": "~/.local/bin/pequeno-asistente --toggle"
}
```

### 4. Atajo de Teclado en Hyprland
Añade a tu `~/.config/hypr/hyprland.conf`:
```ini
# Ventana flotante estilo dropdown
windowrulev2 = float, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = size 480 650, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = move 100%-500 42, class:^(com.gatovillano.pequenoasistente)$
windowrulev2 = pin, class:^(com.gatovillano.pequenoasistente)$

# Atajo global Super + A
bind = $mainMod, A, exec, ~/.local/bin/pequeno-asistente --toggle
```

---

## 🛠️ Estructura del Código

```
Pequeño asistente/
├── assets/
│   ├── icon.svg             # Icono SVG del asistente
│   └── style.css            # Estilos CSS modernos GTK4 / Libadwaita
├── pequeno_asistente/
│   ├── agent/
│   │   ├── core.py          # Bucle del agente (LiteLLM + herramientas MCP dinámicas)
│   │   ├── memory.py        # Gestor de memoria en capas (USER.md, MEMORY.md, daily)
│   │   └── providers.py     # Catálogo de proveedores LLM (estilo KogniTerm)
│   ├── mcp_client/
│   │   ├── catalog.py       # Catálogo extenso de servidores MCP
│   │   └── manager.py       # Gestor multisesión de MCPs (stdio / sse)
│   ├── ui/
│   │   ├── chat_view.py     # Componente visual del chat con burbujas y tarjetas MCP
│   │   ├── settings_window.py # Ventana de preferencias (Modelos, Catálogo MCP, Memoria)
│   │   ├── tray.py          # Servidor de Socket IPC para toggling instantáneo
│   │   └── window.py        # Ventana principal tipo dropdown
│   ├── config.py            # Persistencia de configuración en ~/.config/pequeno-asistente
│   └── main.py              # Punto de entrada de la aplicación
├── scripts/
│   ├── toggle.sh            # Script para alternar ventana
│   └── install_desktop.sh   # Instalador del lanzador y atajos
└── requirements.txt
```

---

## 💡 Uso Rápido
- **Abrir / Cerrar**: Presiona `Super + A` o haz clic en el icono 🤖 de Waybar.
- **Configuración**: Haz clic en el botón de engranaje ⚙️ en la cabecera.
- **Limpiar Chat**: Haz clic en el botón 🧹.
- **Enviar Mensaje**: Escribe y pulsa `Enter` (usa `Shift + Enter` para salto de línea).
