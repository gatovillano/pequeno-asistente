"""
Curated MCP Server Catalog for Pequeño Asistente.
Contains pre-configured, tested MCP servers across multiple categories.
"""
from typing import Dict, Any, List

MCP_CATALOG: Dict[str, Dict[str, Any]] = {
    # --- ESCRITORIO & SISTEMA ---
    "hypruse": {
        "name": "Hyprland Desktop (hypruse)",
        "category": "Escritorio & Sistema",
        "icon": "🪟",
        "description": "Control integral del gestor Hyprland: inspeccionar ventanas, capturas de pantalla, interacción con UI y atajos.",
        "command": "uvx",
        "args": ["hypruse"],
        "transport": "stdio",
        "env": {
            "HYPRUSE_SCREENSHOT_MODE": "image",
            "HYPRUSE_READONLY": "0"
        },
        "requires_api_key": False
    },
    "filesystem": {
        "name": "Explorador de Archivos (Filesystem)",
        "category": "Escritorio & Sistema",
        "icon": "📁",
        "description": "Permite al asistente leer, escribir, buscar y listar archivos en carpetas permitidas de tu equipo.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/gato/Proyectos", "/home/gato/Documentos", "/home/gato/Descargas"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "fetch": {
        "name": "Navegador Web / Fetch",
        "category": "Escritorio & Sistema",
        "icon": "🌐",
        "description": "Descarga y procesa páginas web convirtiéndolas a Markdown limpio para responder dudas sobre documentación o artículos.",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "desktop-bash": {
        "name": "Terminal Bash / Sistema",
        "category": "Escritorio & Sistema",
        "icon": "💻",
        "description": "Ejecución de comandos bash en el sistema para diagnóstico, scripts y utilidades de terminal.",
        "command": "npx",
        "args": ["-y", "@wonderwhy-er/desktop-commander"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },

    # --- BÚSQUEDA & WEB ---
    "duckduckgo": {
        "name": "DuckDuckGo Search",
        "category": "Búsqueda & Web",
        "icon": "🦆",
        "description": "Búsqueda web directa en Internet sin necesidad de API Key ni registros externos.",
        "command": "uvx",
        "args": ["duckduckgo-mcp"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "brave-search": {
        "name": "Brave Search",
        "category": "Búsqueda & Web",
        "icon": "🦁",
        "description": "Búsqueda web e índice de sitios mediante la API de Brave Search.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "transport": "stdio",
        "env": {
            "BRAVE_API_KEY": ""
        },
        "requires_api_key": True,
        "api_key_env": "BRAVE_API_KEY"
    },
    "tavily": {
        "name": "Tavily Search Engine",
        "category": "Búsqueda & Web",
        "icon": "🔍",
        "description": "Motor de búsqueda optimizado para agentes IA con extracción de citas.",
        "command": "uvx",
        "args": ["tavily-mcp"],
        "transport": "stdio",
        "env": {
            "TAVILY_API_KEY": ""
        },
        "requires_api_key": True,
        "api_key_env": "TAVILY_API_KEY"
    },
    "puppeteer": {
        "name": "Puppeteer Automator",
        "category": "Búsqueda & Web",
        "icon": "🎭",
        "description": "Navegador Chromium headless para hacer capturas completas, rellenar formularios y hacer scraping avanzado.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },

    # --- DESARROLLO & CÓDIGO ---
    "git": {
        "name": "Git Repository Manager",
        "category": "Desarrollo & Código",
        "icon": "🌿",
        "description": "Lee el historial de commits, diffs, ramas y estado de tus repositorios Git locales.",
        "command": "uvx",
        "args": ["mcp-server-git"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "github": {
        "name": "GitHub Integration",
        "category": "Desarrollo & Código",
        "icon": "🐙",
        "description": "Acceso a repositorios remotos, issues, pull requests y archivos en GitHub.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "transport": "stdio",
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": ""
        },
        "requires_api_key": True,
        "api_key_env": "GITHUB_PERSONAL_ACCESS_TOKEN"
    },
    "sqlite": {
        "name": "SQLite Database",
        "category": "Desarrollo & Código",
        "icon": "🗄️",
        "description": "Consulta esquemas y ejecuta consultas SQL en bases de datos SQLite locales.",
        "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "/home/gato/.config/pequeno-asistente/asistente.db"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },

    # --- MEMORIA & NOTAS ---
    "memory-graph": {
        "name": "Knowledge Graph Memory",
        "category": "Memoria & Notas",
        "icon": "🕸️",
        "description": "Servidor MCP oficial de Anthropic que crea y navega un grafo de entidades, relaciones y observaciones.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "obsidian": {
        "name": "Obsidian Vault Notes",
        "category": "Memoria & Notas",
        "icon": "💎",
        "description": "Lee y busca notas dentro de tu bóveda de Markdown de Obsidian.",
        "command": "uvx",
        "args": ["obsidian-mcp", "--vault-path", "/home/gato/Documentos/Obsidian"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },

    # --- PRODUCTIVIDAD & AUDIO ---
    "time": {
        "name": "Reloj & Zonas Horarias",
        "category": "Productividad & Audio",
        "icon": "⏰",
        "description": "Proporciona la hora y fecha exacta del sistema y conversiones de zonas horarias mundiales.",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "weather": {
        "name": "El Tiempo & Clima",
        "category": "Productividad & Audio",
        "icon": "⛅",
        "description": "Reporte meteorológico actual y pronósticos por ciudad o coordenadas.",
        "command": "uvx",
        "args": ["weather-mcp"],
        "transport": "stdio",
        "env": {},
        "requires_api_key": False
    },
    "bitwig": {
        "name": "Bitwig Studio Controller",
        "category": "Productividad & Audio",
        "icon": "🎹",
        "description": "Control de transporte, pistas, clips, dispositivos e instrumentos en Bitwig Studio.",
        "command": "/home/gato/Proyectos/bitwig_agent/python/.venv/bin/bitwig-agent-mcp",
        "args": [],
        "transport": "stdio",
        "env": {
            "BITWIG_BRIDGE_URL": "http://localhost:8989"
        },
        "requires_api_key": False
    },
    "kognito-ai": {
        "name": "Kognito AI Server",
        "category": "Productividad & Audio",
        "icon": "🧠",
        "description": "Conexión directa con la pasarela de KognitoAI.",
        "command": "/home/gato/Proyectos/KognitoAI/mcp_server/run.sh",
        "args": [],
        "transport": "stdio",
        "env": {
            "KOGNITO_ACCOUNT_ID": "5b8d59b0-69b7-4aa8-9bb0-bf07511222a6"
        },
        "requires_api_key": False
    }
}

CATALOG_CATEGORIES = [
    "Todos",
    "Escritorio & Sistema",
    "Búsqueda & Web",
    "Desarrollo & Código",
    "Memoria & Notas",
    "Productividad & Audio"
]
