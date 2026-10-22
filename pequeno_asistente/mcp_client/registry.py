"""
MCP Registry Client for Pequeño Asistente.
Fetches, searches, and installs servers from global registries (Smithery, Official MCP, and Custom repos).
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import httpx

logger = logging.getLogger("pequeno_asistente.registry")

CACHE_DIR = Path.home() / ".config" / "pequeno-asistente" / "cache"

DEFAULT_REGISTRIES = [
    {
        "id": "smithery",
        "name": "Smithery Registry (10,000+ servidores)",
        "url": "https://api.smithery.ai/servers",
        "type": "smithery",
        "enabled": True,
        "description": "Registro global abierto de servidores y habilidades MCP para IA."
    },
    {
        "id": "official",
        "name": "Anthropic Official MCP Registry",
        "url": "https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md",
        "type": "official",
        "enabled": True,
        "description": "Servidores de referencia oficiales mantenidos por el equipo de Anthropic / MCP."
    }
]

# Official Anthropic server catalog definition
OFFICIAL_SERVERS = [
    {
        "id": "official-filesystem",
        "name": "Filesystem (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-filesystem",
        "description": "Lectura, escritura y búsqueda segura de archivos locales en rutas permitidas.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/gato/Proyectos", "/home/gato/Documentos"],
        "transport": "stdio",
        "env": {},
        "category": "Escritorio & Sistema",
        "source": "Official MCP"
    },
    {
        "id": "official-memory",
        "name": "Memory Knowledge Graph (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-memory",
        "description": "Grafo de conocimiento persistente con entidades, relaciones y observaciones.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
        "env": {},
        "category": "Memoria & Notas",
        "source": "Official MCP"
    },
    {
        "id": "official-git",
        "name": "Git (Oficial)",
        "qualifiedName": "mcp-server-git",
        "description": "Herramientas para leer repositorios Git locales, inspeccionar diffs, commits y ramas.",
        "command": "uvx",
        "args": ["mcp-server-git"],
        "transport": "stdio",
        "env": {},
        "category": "Desarrollo & Código",
        "source": "Official MCP"
    },
    {
        "id": "official-github",
        "name": "GitHub (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-github",
        "description": "Operaciones en GitHub: consultar repositorios, issues, pull requests y archivos.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "transport": "stdio",
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
        "category": "Desarrollo & Código",
        "source": "Official MCP"
    },
    {
        "id": "official-sqlite",
        "name": "SQLite (Oficial)",
        "qualifiedName": "mcp-server-sqlite",
        "description": "Interacción y consultas SQL en bases de datos SQLite locales.",
        "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "/home/gato/.config/pequeno-asistente/asistente.db"],
        "transport": "stdio",
        "env": {},
        "category": "Desarrollo & Código",
        "source": "Official MCP"
    },
    {
        "id": "official-postgres",
        "name": "PostgreSQL (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-postgres",
        "description": "Inspección de esquemas y ejecución de consultas de sólo lectura en PostgreSQL.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
        "transport": "stdio",
        "env": {},
        "category": "Desarrollo & Código",
        "source": "Official MCP"
    },
    {
        "id": "official-brave-search",
        "name": "Brave Search (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-brave-search",
        "description": "Búsqueda web indexada y resultados locales usando la API de Brave Search.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "transport": "stdio",
        "env": {"BRAVE_API_KEY": ""},
        "category": "Búsqueda & Web",
        "source": "Official MCP"
    },
    {
        "id": "official-fetch",
        "name": "Fetch (Oficial)",
        "qualifiedName": "mcp-server-fetch",
        "description": "Descarga contenido web y lo convierte a formato Markdown para el modelo.",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "transport": "stdio",
        "env": {},
        "category": "Búsqueda & Web",
        "source": "Official MCP"
    },
    {
        "id": "official-puppeteer",
        "name": "Puppeteer (Oficial)",
        "qualifiedName": "@modelcontextprotocol/server-puppeteer",
        "description": "Automatización de navegador Chromium y capturas de páginas web dinámicas.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "transport": "stdio",
        "env": {},
        "category": "Búsqueda & Web",
        "source": "Official MCP"
    },
    {
        "id": "official-time",
        "name": "Time (Oficial)",
        "qualifiedName": "mcp-server-time",
        "description": "Hora local del sistema y conversión entre husos horarios mundiales.",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "transport": "stdio",
        "env": {},
        "category": "Productividad & Audio",
        "source": "Official MCP"
    }
]

class RegistryClient:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_file = CACHE_DIR / "mcp_registry_cache.json"

    async def search_smithery(self, query: str = "", page_size: int = 25) -> List[Dict[str, Any]]:
        """Searches the Smithery online registry API."""
        url = "https://api.smithery.ai/servers"
        params = {"pageSize": str(page_size)}
        if query:
            params["q"] = query

        servers: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json().get("servers", [])
                    for s in data:
                        qname = s.get("qualifiedName", "")
                        disp_name = s.get("displayName") or s.get("name") or qname
                        desc = s.get("description", "")
                        use_cnt = s.get("useCount", 0)
                        conn_url = s.get("connectionUrl")

                        # If remote SSE URL is provided, prefer sse transport, else stdio with smithery run
                        if conn_url:
                            transport = "sse"
                            command = conn_url
                            args = []
                        else:
                            transport = "stdio"
                            command = "npx"
                            args = ["-y", "@smithery/cli@latest", "run", qname, "--config", "{}"]

                        safe_id = "smithery-" + qname.replace("/", "-").replace("@", "").lower()
                        servers.append({
                            "id": safe_id,
                            "name": disp_name,
                            "qualifiedName": qname,
                            "description": desc,
                            "transport": transport,
                            "command": command,
                            "args": args,
                            "env": {},
                            "source": "Smithery Registry",
                            "useCount": use_cnt,
                            "category": "Comunidad & Smithery"
                        })
        except Exception as e:
            logger.warning(f"Error querying Smithery API: {e}")

        return servers

    async def fetch_custom_registry(self, registry_url: str) -> List[Dict[str, Any]]:
        """
        Fetches an external custom JSON registry from a given URL.
        Expects a JSON array or object with a 'servers' key.
        """
        servers = []
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(registry_url)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_list = data if isinstance(data, list) else data.get("servers", [])
                    for idx, s in enumerate(raw_list):
                        sid = s.get("id") or f"custom-{idx}"
                        servers.append({
                            "id": sid,
                            "name": s.get("name", sid),
                            "qualifiedName": s.get("qualifiedName", sid),
                            "description": s.get("description", ""),
                            "transport": s.get("transport", "stdio"),
                            "command": s.get("command", "npx"),
                            "args": s.get("args", []),
                            "env": s.get("env", {}),
                            "source": "Repositorio Personalizado",
                            "category": s.get("category", "Personalizado")
                        })
        except Exception as e:
            logger.warning(f"Error fetching custom registry {registry_url}: {e}")
        return servers

    async def search_all(
        self,
        query: str = "",
        custom_registries: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Aggregates search results across Official MCP, Smithery Registry, and any custom repositories.
        """
        q_lower = query.strip().lower()
        results: List[Dict[str, Any]] = []

        # 1. Official reference servers (offline/instant)
        for s in OFFICIAL_SERVERS:
            if not q_lower or (q_lower in s["name"].lower() or q_lower in s["description"].lower() or q_lower in s["qualifiedName"].lower()):
                results.append(dict(s))

        # 2. Custom registries if any
        if custom_registries:
            for cr in custom_registries:
                if cr.get("enabled", True) and cr.get("url"):
                    c_servers = await self.fetch_custom_registry(cr["url"])
                    for cs in c_servers:
                        if not q_lower or (q_lower in cs["name"].lower() or q_lower in cs["description"].lower()):
                            results.append(cs)

        # 3. Smithery online search
        smithery_results = await self.search_smithery(query=query, page_size=20)
        results.extend(smithery_results)

        return results
