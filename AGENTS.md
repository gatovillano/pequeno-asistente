# Directivas de Herramientas y Servidores MCP

## Acceso y Uso Obligatorio de MCP (Model Context Protocol)

1. **Disponibilidad de herramientas MCP**:
   - Hay servidores MCP activos conectados al entorno (por ejemplo, `hypruse` para inspección de escritorio, capturas de pantalla, UI y atajos; y `bitwig` para interactuar con la DAW Bitwig Studio).
   - Estas herramientas se cargan bajo demanda (*lazy loading*), por lo que **no** aparecen directamente en la lista de declaraciones de funciones nativas principales.
   - **PROHIBIDO** responder al usuario afirmando que "no tienes herramientas disponibles", "no puedes interactuar con el escritorio/pantalla" o "no puedes interactuar con Bitwig" sin antes verificar e invocar los servidores MCP correspondientes.

2. **Cómo invocar herramientas MCP**:
   - Usa la función nativa `call_mcp_tool`:
     - `ServerName`: nombre del servidor (ej. `"hypruse"`, `"bitwig"`).
     - `ToolName`: nombre de la herramienta (ej. `"screenshot"`, `"desktop"`, `"click_ui"`, `"get_project_context"`, `"control_transport"`, etc.).
     - `Arguments`: diccionario con los parámetros correspondientes.
   - Si necesitas consultar los parámetros exactos de una herramienta MCP, lee su esquema JSON en:
     `/home/gato/.gemini/antigravity/mcp/<ServerName>/<ToolName>.json`

3. **Subagentes**:
   - Al definir o invocar subagentes que requieran interactuar con el sistema o con servidores MCP, asegúrate de activar `EnableMCPTools: true`.
