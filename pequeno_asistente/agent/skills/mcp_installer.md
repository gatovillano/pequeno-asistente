---
name: mcp-installer-skill
description: Guía procedimental para analizar, instalar, compilar y configurar servidores MCP a partir de enlaces de GitHub, paquetes o documentación.
category: system
tags: ["mcp", "installation", "config", "tools", "skills"]
---

# 🛠️ Skill: Procedimiento para Instalar y Configurar Servidores MCP

Esta skill instruye al agente en el procedimiento metódico para analizar cualquier solicitud del usuario referente a instalar o agregar un nuevo servidor MCP (Model Context Protocol), ya sea que proporcione un enlace de GitHub, un paquete de npm/PyPI o un repositorio.

---

## 1. Análisis del Repositorio o Enlace
Cuando el usuario te entregue un enlace de GitHub (ej: `https://github.com/upstash/context7`) o el nombre de un servidor:

1. **Determinar el Stack del Servidor**:
   - **Node.js / TypeScript**: Si contiene `package.json`.
     - Si está publicado en npm: usar `npx -y <nombre-paquete>`
     - Si requiere clonar localmente: clonar en `~/.config/pequeno-asistente/custom_mcps/<nombre>`, instalar con `npm install` y compilar con `npm run build`. El comando de ejecución será `node dist/index.js` o `node build/index.js`.
   - **Python**: Si contiene `pyproject.toml`, `setup.py` o `requirements.txt`.
     - Si está publicado en PyPI: usar `uvx <nombre-paquete>`
     - Si requiere entorno local: crear un venv con `uv venv` o `python3 -m venv`, instalar con `uv pip install -e .`, y usar el binario en `.venv/bin/<ejecutable>`.
   - **Remoto / HTTP (Streamable HTTP o SSE)**: Si es una URL HTTP/HTTPS (ej: `https://server.smithery.ai/...`, `https://.../mcp` o `https://.../sse`).
     - Soporta `"transport": "auto"` (auto-detecta entre Streamable HTTP y SSE con respaldo automático), `"transport": "streamable_http"` o `"transport": "sse"`.
     - La dirección se especifica en `"url": "https://..."` y las credenciales en `"api_key"` (inyecta `Authorization: Bearer ...`) o en `"headers": {"Authorization": "Bearer ..."}`.
   - **Docker**: Si se distribuye como contenedor: `docker run -i --rm ...`.

---

## 2. Detección de Variables de Entorno y Credenciales
Revisar si el servidor requiere claves de API, tokens o parámetros de conexión:
- Por ejemplo: tokens de GitHub (`GITHUB_PERSONAL_ACCESS_TOKEN`), claves de búsqueda (`BRAVE_API_KEY`, `TAVILY_API_KEY`), o cadenas de conexión a bases de datos.
- Para servidores locales (`stdio`), estas se registran en `"env": { "VARIABLE": "VALOR" }`.
- Para servidores remotos (`streamable_http` / `sse` / `auto`), pueden registrarse en `"api_key": "..."` o en `"headers": { "Authorization": "Bearer ${MI_TOKEN}" }` (soportando interpolación `${VAR}` desde `env`).

---

## 3. Registro en la Configuración del Asistente
El cliente Pequeño Asistente gestiona sus servidores en el archivo:
`~/.config/pequeno-asistente/config.json`

Estructura para servidor **Local (`stdio`)** dentro de `"mcp_servers"`:
```json
{
  "mcp_servers": {
    "<id_unico>": {
      "name": "<Nombre Amigable>",
      "command": "<comando_ejecutable>",
      "args": ["<arg1>", "<arg2>"],
      "transport": "stdio",
      "env": {
        "MI_VARIABLE": "valor"
      },
      "disabled": false
    }
  }
}
```

Estructura para servidor **Remoto (`streamable_http` / `sse` / `auto`)** dentro de `"mcp_servers"`:
```json
{
  "mcp_servers": {
    "<id_remoto>": {
      "name": "<Nombre Amigable Remoto>",
      "url": "https://ejemplo.com/mcp",
      "transport": "auto",
      "api_key": "<token_bearer_opcional>",
      "headers": {
        "X-Custom-Header": "valor"
      },
      "env": {},
      "disabled": false
    }
  }
}
```

### Opciones de Instalación:
- **Si posees herramientas de sistema (ej: `filesystem` o `desktop-bash`)**:
  1. Puedes leer `~/.config/pequeno-asistente/config.json`.
  2. Añadir la nueva entrada en `"mcp_servers"`.
  3. Guardar el archivo.
  4. Si requiere clonación o instalación de dependencias, puedes ejecutar los comandos correspondientes (`git clone`, `npm install`, etc.).
- **Si el usuario prefiere hacerlo manualmente**:
  - Proporciónale el bloque JSON exacto listo para copiar y pegar en la interfaz de **Configuración ➔ Catálogo MCP ➔ + Añadir Servidor MCP ➔ Pestaña Pegar JSON Completo**.

---

## 4. Verificación y Recarga
Una vez añadida la configuración:
- La configuración se activa automáticamente o al alternar la ventana (`~/.local/bin/pequeno-asistente --toggle`).
- Confirma al usuario el comando utilizado, la ruta y las herramientas que ahora estarán disponibles.
