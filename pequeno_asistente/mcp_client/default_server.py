"""
Default Built-in MCP Server for Pequeño Asistente (`pequeno-core`).
Comes pre-configured and connected by default upon installation, providing all
core native tools via the MCP architecture:
- Episodic & Long-Term Memory Management (`memory__*`)
- Document & Image Reading / Inspection (`media__*`)
- Procedural Skills Management (`skills__*`)
- Proactive Automations & Heartbeats (`automation__*`)
- System Security & Custodied Credentials (`security__*`)

Can operate both in-process via `DefaultMCPServerInstance` (zero-latency, direct access
to GTK/singleton managers) and as a standalone MCP stdio server (`python -m pequeno_asistente.mcp_client.default_server`).
"""
import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Any, Optional

from pequeno_asistente.security.manager import CredentialManager
from pequeno_asistente.agent.media_processor import (
    process_document,
    process_image,
    process_audio,
    inspect_file,
    is_model_multimodal,
    is_model_audio_multimodal
)

logger = logging.getLogger("pequeno_asistente.mcp.default_server")

DEFAULT_MCP_SERVER_ID = "pequeno-core"

SECURITY_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "security__request_credential",
            "description": "[security] Abre un diálogo gráfico modal seguro en el escritorio solicitando una contraseña, token o credencial al usuario. Utilízala cuando necesites contraseñas para configurar servicios, bases de datos o cuentas de forma protegida sin pedirle al usuario que escriba contraseñas en el chat de texto plano. Retorna un objeto con credential_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Nombre o identificador del servicio/uso (ej: 'sudo', 'postgres', 'github_token', 'wifi', 'database')"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Explicación clara y detallada de por qué se necesita la contraseña o credencial"
                    },
                    "username": {
                        "type": "string",
                        "description": "Nombre de usuario si aplica (opcional)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Título del diálogo gráfico (opcional)"
                    }
                },
                "required": ["target", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "security__execute_sudo",
            "description": "[security] EXCLUSIVO para comandos administrativos que requieran OBLIGATORIAMENTE privilegios de superusuario ROOT (ej: 'apt install', 'apt update', 'systemctl restart', 'ufw', montaje de discos o modificación en /etc). ¡ESTÁ ESTRICTAMENTE PROHIBIDO usar esta herramienta para comandos ordinarios de usuario (como ls, pwd, cat, git, python, node, npm, curl, grep, ps, etc.)! Para comandos estándar que no son sudo, DEBES usar siempre las herramientas de terminal MCP regulares (como desktop-bash) o la terminal interactiva (Ctrl+T).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando administrativo estricto que requiere privilegios de superusuario root. NO usar para comandos ordinarios de usuario."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Justificación explícita de por qué este comando exige privilegios de superusuario root y no puede ser ejecutado como usuario estándar."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Tiempo límite de ejecución en segundos (por defecto 120)"
                    }
                },
                "required": ["command", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "security__apply_credential",
            "description": "[security] Aplica una credencial obtenida previamente mediante su credential_id a un archivo de configuración (creándolo con permisos protegidos 0600) reemplazando '{secret}' y '{username}', sin exponer la contraseña en texto plano en el chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "credential_id": {
                        "type": "string",
                        "description": "El ID de la credencial obtenido previamente con security__request_credential"
                    },
                    "destination_file": {
                        "type": "string",
                        "description": "Ruta absoluta del archivo de destino donde escribir la configuración protegida"
                    },
                    "file_template": {
                        "type": "string",
                        "description": "Contenido del archivo plantilla donde '{secret}' y '{username}' serán sustituidos de forma segura"
                    }
                },
                "required": ["credential_id", "destination_file", "file_template"]
            }
        }
    }
]

AUTOMATION_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "automation__list",
            "description": "[automation] Lista el estado del Pulso Global (Heartbeat) y todas las automatizaciones programadas activas y configuradas en el sistema.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__create",
            "description": "[automation] Crea y programa una nueva automatización periódica para que el agente la ejecute en segundo plano con herramientas MCP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre descriptivo de la tarea o recordatorio"},
                    "prompt": {"type": "string", "description": "Instrucción clara que el agente evaluará en cada ciclo"},
                    "schedule_type": {"type": "string", "enum": ["interval", "daily", "once"], "description": "Tipo de programación: 'interval' (cada X min), 'daily' (hora fija HH:MM) o 'once' (temporizador único en X min)"},
                    "interval_minutes": {"type": "integer", "description": "Minutos de intervalo (para schedule_type='interval' o 'once'). Por defecto 30"},
                    "daily_time": {"type": "string", "description": "Hora del día en formato HH:MM (ej: '09:00', '18:30') si schedule_type='daily'"},
                    "silent_if_ok": {"type": "boolean", "description": "Si es True, el agente no molestará al usuario con notificaciones si no hay alertas o si todo está normal"},
                    "desktop_notification": {"type": "boolean", "description": "Enviar notificación gráfica al escritorio al detectar novedades (por defecto True)"},
                    "show_in_chat": {"type": "boolean", "description": "Mostrar reporte en la conversación de chat (por defecto True)"},
                    "speak_tts": {"type": "boolean", "description": "Pronunciar con voz TTS si hay alertas (por defecto False)"}
                },
                "required": ["name", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__update",
            "description": "[automation] Modifica una automatización existente por su ID (ej: cambiar intervalo, prompt, o activar/desactivar con enabled=true/false).",
            "parameters": {
                "type": "object",
                "properties": {
                    "auto_id": {"type": "string", "description": "ID de la automatización (ej: 'hb-a1b2c3d4')"},
                    "enabled": {"type": "boolean", "description": "Activar o desactivar la automatización"},
                    "name": {"type": "string", "description": "Nuevo nombre"},
                    "prompt": {"type": "string", "description": "Nueva instrucción"},
                    "schedule_type": {"type": "string", "enum": ["interval", "daily", "once"]},
                    "interval_minutes": {"type": "integer"},
                    "daily_time": {"type": "string"},
                    "silent_if_ok": {"type": "boolean"},
                    "desktop_notification": {"type": "boolean"},
                    "show_in_chat": {"type": "boolean"},
                    "speak_tts": {"type": "boolean"}
                },
                "required": ["auto_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__delete",
            "description": "[automation] Elimina permanentemente una automatización por su ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "auto_id": {"type": "string", "description": "ID de la automatización a eliminar"}
                },
                "required": ["auto_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__configure_heartbeat",
            "description": "[automation] Configura las opciones del Pulso Global de Heartbeat (activar/desactivar, intervalo, rango de horas activas, silencio).",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "description": "Habilitar o pausar el pulso global de Heartbeat"},
                    "interval_minutes": {"type": "integer", "description": "Intervalo entre pulsos en minutos (ej: 15, 30, 60)"},
                    "active_hours_only": {"type": "boolean", "description": "Limitar pulsos únicamente al rango de horas activas (ej: 08:00 a 23:00)"},
                    "active_start": {"type": "string", "description": "Hora de inicio activa (HH:MM)"},
                    "active_end": {"type": "string", "description": "Hora de fin activa (HH:MM)"},
                    "silent_if_ok": {"type": "boolean", "description": "Si todo está normal, no interrumpir"},
                    "desktop_notification": {"type": "boolean"},
                    "show_in_chat": {"type": "boolean"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__get_heartbeat_checklist",
            "description": "[automation] Lee la lista de verificación actual de HEARTBEAT.md que guía las comprobaciones periódicas del pulso proactivo.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__update_heartbeat_checklist",
            "description": "[automation] Actualiza el contenido de la lista de verificación HEARTBEAT.md con nuevos puntos o directivas periódicas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Contenido completo en Markdown para HEARTBEAT.md"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "automation__trigger_now",
            "description": "[automation] Dispara de inmediato la ejecución de un pulso de Heartbeat o de una automatización específica sin esperar su turno programado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'heartbeat' para el pulso global, o el ID de la automatización (ej: 'hb-a1b2c3d4')"}
                },
                "required": ["target"]
            }
        }
    }
]

SKILLS_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "skills__list",
            "description": "[skills] Lista todas las habilidades procedimentales (skills) descubiertas en el sistema (incluyendo Claude Code ~/.claude/skills, estándar global ~/.agents/skills, sistema operativo, Antigravity y usuario).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["all", "claude", "agents_global", "system", "user", "built-in", "antigravity", "workspace"],
                        "description": "Filtrar por origen (ej: 'claude' para Claude Code, 'system' para el sistema, 'all' para todas)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda opcional para filtrar por nombre o descripción"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skills__get",
            "description": "[skills] Lee el contenido completo en Markdown y metadatos de una habilidad específica por su ID (ej: 'analytical-thinking', 'mcp-installer', 'find-skills').",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "ID o nombre de la habilidad a consultar"
                    }
                },
                "required": ["skill_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skills__install",
            "description": "[skills] Instala una nueva habilidad en el sistema desde una URL (raw markdown / GitHub), un repositorio Git, una ruta local, o contenido directo en Markdown. Puede instalarse para Pequeño Asistente, para Claude Code (~/.claude/skills) o en el estándar global (~/.agents/skills).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "URL de GitHub / archivo raw, URL de repositorio Git, o ruta a archivo/directorio local."
                    },
                    "name": {
                        "type": "string",
                        "description": "Nombre o identificador único deseado para la habilidad (ej: 'react-patterns')"
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenido completo en Markdown si se está redactando o creando una habilidad directamente."
                    },
                    "target": {
                        "type": "string",
                        "enum": ["user", "claude", "agents"],
                        "description": "Destino de instalación: 'user' (~/.config/pequeno-asistente/skills), 'claude' (~/.claude/skills) o 'agents' (~/.agents/skills). Por defecto 'user'."
                    },
                    "as_symlink": {
                        "type": "boolean",
                        "description": "Si source es una ruta local, crear un enlace simbólico en vez de copiar."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skills__uninstall",
            "description": "[skills] Desinstala y elimina una habilidad del usuario o de Claude Code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "ID de la habilidad a desinstalar"
                    }
                },
                "required": ["skill_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skills__toggle",
            "description": "[skills] Habilita o deshabilita una habilidad en la configuración sin eliminar sus archivos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "ID de la habilidad"
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "True para habilitar, False para deshabilitar"
                    }
                },
                "required": ["skill_id", "enabled"]
            }
        }
    }
]

MEMORY_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory__search_episodes",
            "description": "[memory] Busca en la Memoria Episódica Cognitiva (SQLite FTS5 + Ranking Híbrido Semántico/Temporal/Importancia) interacciones pasadas, soluciones de errores, herramientas usadas, decisiones o conversaciones por texto, fechas ('ayer', 'la semana pasada', 'YYYY-MM-DD'), etiquetas o herramientas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Consulta de búsqueda en lenguaje natural o palabras clave (ej: 'qué hicimos ayer', 'configuración de pipewire', 'error en bitwig')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Número máximo de recuerdos a retornar (por defecto 5)"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Fecha inicial opcional en formato YYYY-MM-DD"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Fecha final opcional en formato YYYY-MM-DD"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["interaction", "tool_execution", "error_fix", "milestone", "decision", "lesson"],
                        "description": "Filtrar por categoría de episodio (opcional)"
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filtrar por etiqueta temática (ej: 'hyprland', 'audio', 'bitwig', 'mcp', 'codigo')"
                    },
                    "tool_filter": {
                        "type": "string",
                        "description": "Filtrar episodios donde se haya utilizado una herramienta específica (ej: 'hypruse', 'execute_command')"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory__save_episode",
            "description": "[memory] Guarda explícitamente un hito importante, una solución técnica, una decisión de arquitectura o un recuerdo episódico relevante con alta prioridad para el futuro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título descriptivo y conciso del episodio o logro"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Resumen detallado de lo realizado, comandos clave, decisiones o solución aplicada"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["milestone", "decision", "error_fix", "lesson", "interaction"],
                        "description": "Categoría del recuerdo (por defecto 'milestone')"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de etiquetas temáticas (ej: ['hyprland', 'waybar', 'config'])"
                    },
                    "importance": {
                        "type": "number",
                        "description": "Nivel de importancia entre 0.0 y 1.0 (por defecto 0.88)"
                    }
                },
                "required": ["title", "summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory__get_timeline",
            "description": "[memory] Obtiene la línea de tiempo cronológica de episodios registrados en una fecha específica (YYYY-MM-DD) o los episodios más recientes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Fecha en formato YYYY-MM-DD (opcional; si se omite devuelve los más recientes)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima de episodios a listar (por defecto 15)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Filtrar por categoría opcional"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory__remember_fact",
            "description": "[memory] Registra un hecho persistente a largo plazo en MEMORY.md o una regla/preferencia en LESSONS.md, indexándolo además en la memoria episódica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "El dato, preferencia del usuario o regla que debe recordarse permanentemente"
                    },
                    "target": {
                        "type": "string",
                        "enum": ["memory", "lessons"],
                        "description": "'memory' para hechos/notas en MEMORY.md (por defecto), o 'lessons' para reglas/correcciones en LESSONS.md"
                    },
                    "section": {
                        "type": "string",
                        "description": "Sección Markdown donde insertar el dato (ej: '## Notas Recientes' o '## Reglas Críticas')"
                    }
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory__find_related",
            "description": "[memory] Encuentra los episodios semánticamente más similares a un episodio dado (`source_id`) utilizando similitud coseno sobre vectores neuronales densos (384-dim float32).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "El source_id del episodio de referencia (ej: 'episode:2026-11-07#...')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima de episodios relacionados a devolver (por defecto 4)"
                    }
                },
                "required": ["source_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory__stats",
            "description": "[memory] Muestra estadísticas detalladas del índice de Memoria Episódica (total de episodios, categorías, etiquetas principales, herramientas más utilizadas, rango de fechas y sincronización).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sync": {
                        "type": "boolean",
                        "description": "Si es True, sincroniza bitácoras diarias e hilos de conversación antes de calcular estadísticas"
                    }
                }
            }
        }
    }
]

MEDIA_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "media__read_document",
            "description": "[media] Lee y extrae el contenido estructurado (texto, tablas en Markdown, diapositivas y metadatos) de cualquier documento local por su ruta (PDF, DOCX, ODT, XLSX, CSV, TSV, PPTX, EPUB, HTML, RTF, Markdown, JSON, YAML, TXT o código fuente). Permite consultar rangos de páginas en PDF (start_page, end_page), limitar caracteres (max_chars) o buscar términos dentro del documento (search_query).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta local al documento a leer (ej: '/home/usuario/Documentos/informe.pdf' o '~/notas.docx')"
                    },
                    "start_page": {
                        "type": "integer",
                        "description": "Página inicial a extraer en documentos paginados como PDF (por defecto 1)"
                    },
                    "end_page": {
                        "type": "integer",
                        "description": "Página final a extraer en documentos PDF (opcional; si se omite lee todas las páginas hasta max_chars)"
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Límite máximo de caracteres a devolver (por defecto 30000)"
                    },
                    "search_query": {
                        "type": "string",
                        "description": "Texto o palabra clave opcional para filtrar y extraer únicamente las secciones o líneas que coincidan dentro del documento"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media__read_image",
            "description": "[media] Solicita la lectura y análisis de una imagen local por su ruta (PNG, JPG, JPEG, WEBP, GIF, BMP, SVG, TIFF). Extrae sus metadatos (dimensiones, formato, tamaño, texto OCR si está disponible) y, si el modelo activo es multimodal, inyecta automáticamente la imagen visual en el turno actual para que el modelo la procese directamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta local del archivo de imagen (ej: '/home/usuario/Imágenes/captura.png')"
                    },
                    "extract_ocr": {
                        "type": "boolean",
                        "description": "Si es True, intenta extraer texto incrustado mediante OCR (tesseract) o nodos SVG (por defecto True)"
                    },
                    "max_dimension": {
                        "type": "integer",
                        "description": "Dimensión máxima en píxeles (ancho o alto) para optimizar la imagen antes de enviarla al modelo multimodal (por defecto 1568)"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media__read_audio",
            "description": "[media] Solicita la lectura y escucha multimodal directa de un archivo de audio local por su ruta (WAV, MP3, OGG, OPUS, FLAC, M4A, AAC, WEBM, AIFF). Extrae sus metadatos (duración, frecuencia de muestreo, canales, codec, tamaño) y, si el modelo activo soporta entrada multimodal de audio, inyecta el audio en el turno actual para que el modelo lo escuche directamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta local del archivo de audio (ej: '/home/usuario/Música/nota_voz.ogg' o '~/grabacion.mp3')"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media__inspect_file",
            "description": "[media] Inspecciona un archivo o directorio local por su ruta devolviendo sus metadatos detallados (existencia, categoría imagen/audio/documento/otro, tipo MIME, tamaño en bytes, dimensiones si es imagen, duración si es audio o número de páginas si es PDF).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta local del archivo o carpeta a inspeccionar"
                    }
                },
                "required": ["path"]
            }
        }
    }
]

ALL_DEFAULT_TOOLS_DEFINITIONS: List[Dict[str, Any]] = (
    SECURITY_TOOLS_DEFINITIONS
    + AUTOMATION_TOOLS_DEFINITIONS
    + SKILLS_TOOLS_DEFINITIONS
    + MEMORY_TOOLS_DEFINITIONS
    + MEDIA_TOOLS_DEFINITIONS
)


async def execute_default_mcp_tool(
    namespaced_name: str,
    arguments: Dict[str, Any],
    config_manager: Any = None
) -> str:
    """
    Executes any tool belonging to the default Pequeño Asistente Core MCP server
    (`security__*`, `automation__*`, `skills__*`, `memory__*`, `media__*`).
    """
    if "__" in namespaced_name:
        category, original_name = namespaced_name.split("__", 1)
    else:
        category, original_name = "media", namespaced_name

    # 1. Security tools
    if category == "security":
        sec_mgr = CredentialManager.get_instance()
        try:
            if original_name == "request_credential":
                target = arguments.get("target", "generic")
                prompt = arguments.get("prompt", "Introduce la contraseña:")
                username = arguments.get("username")
                title = arguments.get("title")
                cred = await sec_mgr.request_credential(
                    target=target,
                    prompt=prompt,
                    username=username,
                    title=title
                )
                if cred.is_valid:
                    return json.dumps({
                        "status": "granted",
                        "credential_id": cred.credential_id,
                        "target": cred.target,
                        "username": cred.username,
                        "note": "Contraseña/credencial adquirida con éxito mediante el diálogo seguro y retenida en memoria protegida. Para aplicarla a una configuración usa security__apply_credential o para sudo usa security__execute_sudo sin revelar el secreto en texto plano."
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "status": "cancelled",
                        "message": "El usuario canceló la solicitud o cerró el diálogo sin introducir la contraseña."
                    }, ensure_ascii=False)

            elif original_name == "execute_sudo":
                cmd = arguments.get("command", "").strip()
                reason = arguments.get("reason")
                timeout = int(arguments.get("timeout", 120))
                res = await sec_mgr.execute_sudo_command(
                    command=cmd,
                    reason=reason,
                    timeout=timeout
                )
                return json.dumps(res, indent=2, ensure_ascii=False)

            elif original_name == "apply_credential":
                cred_id = arguments.get("credential_id")
                dest_file = arguments.get("destination_file")
                template = arguments.get("file_template", "{secret}")

                cred = sec_mgr.get_credential(cred_id)
                if not cred or not cred.is_valid:
                    return f"Error: Credencial con ID '{cred_id}' no encontrada, expirada o inválida."

                dest_path = Path(dest_file).expanduser().resolve()
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                content = template.replace("{secret}", cred.get_secret())
                content = content.replace("{username}", cred.username or "")

                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(content)
                os.chmod(dest_path, 0o600)

                return f"Credencial aplicada exitosamente en '{dest_path}' con permisos protegidos (0600)."

            else:
                return f"Error: Acción de seguridad desconocida '{original_name}'."
        except Exception as e:
            logger.error(f"Error executing security tool '{original_name}': {e}", exc_info=True)
            return f"Error al ejecutar herramienta de seguridad '{original_name}': {str(e)}"

    # 2. Automation & Heartbeat tools
    if category == "automation":
        try:
            from pequeno_asistente.agent.heartbeat import HeartbeatManager
            from pequeno_asistente.agent.memory import MemoryManager
            hb_mgr = HeartbeatManager.get_instance()
            mem_mgr = hb_mgr.memory_manager if (hb_mgr and hasattr(hb_mgr, "memory_manager")) else MemoryManager.get_instance()

            if original_name == "list":
                hb_cfg = config_manager.get_heartbeat_config() if config_manager else {}
                autos = config_manager.get_automations() if config_manager else []
                logs = hb_mgr.get_logs()[:5] if hb_mgr else []
                return json.dumps({
                    "heartbeat": hb_cfg,
                    "automations": autos,
                    "recent_logs": logs
                }, indent=2, ensure_ascii=False)

            elif original_name == "create":
                if not hb_mgr:
                    return "Error: HeartbeatManager no está inicializado."
                created = hb_mgr.create_automation(
                    name=arguments.get("name", "Automatización"),
                    prompt=arguments.get("prompt", ""),
                    schedule_type=arguments.get("schedule_type", "interval"),
                    interval_minutes=int(arguments.get("interval_minutes", 30)),
                    daily_time=arguments.get("daily_time", "09:00"),
                    silent_if_ok=arguments.get("silent_if_ok", True),
                    desktop_notification=arguments.get("desktop_notification", True),
                    show_in_chat=arguments.get("show_in_chat", True),
                    speak_tts=arguments.get("speak_tts", False),
                    enabled=arguments.get("enabled", True)
                )
                return json.dumps({
                    "status": "created",
                    "automation": created,
                    "message": f"Automatización '{created['name']}' programada exitosamente."
                }, indent=2, ensure_ascii=False)

            elif original_name == "update":
                if not hb_mgr:
                    return "Error: HeartbeatManager no está inicializado."
                auto_id = arguments.get("auto_id")
                if not auto_id:
                    return "Error: Falta 'auto_id'."
                updates = {k: v for k, v in arguments.items() if k != "auto_id"}
                updated = hb_mgr.update_automation(auto_id, updates)
                if updated:
                    return json.dumps({"status": "updated", "automation": updated}, indent=2, ensure_ascii=False)
                return f"Error: No se encontró la automatización con ID '{auto_id}'."

            elif original_name == "delete":
                if not hb_mgr:
                    return "Error: HeartbeatManager no está inicializado."
                auto_id = arguments.get("auto_id")
                ok = hb_mgr.delete_automation(auto_id)
                if ok:
                    return f"Automatización '{auto_id}' eliminada con éxito."
                return f"Error: No se encontró la automatización '{auto_id}'."

            elif original_name == "configure_heartbeat":
                if not config_manager:
                    return "Error: ConfigManager no disponible."
                config_manager.set_heartbeat_config(arguments)
                if hb_mgr:
                    hb_mgr.notify_listeners()
                return json.dumps({
                    "status": "updated",
                    "heartbeat": config_manager.get_heartbeat_config(),
                    "message": "Configuración de Heartbeat actualizada."
                }, indent=2, ensure_ascii=False)

            elif original_name == "get_heartbeat_checklist":
                return mem_mgr.get_heartbeat_md()

            elif original_name == "update_heartbeat_checklist":
                new_content = arguments.get("content", "").strip()
                if not new_content:
                    return "Error: El contenido para HEARTBEAT.md no puede estar vacío."
                mem_mgr.save_heartbeat_md(new_content)
                if hb_mgr:
                    hb_mgr.notify_listeners()
                return "Lista de verificación HEARTBEAT.md guardada correctamente."

            elif original_name == "trigger_now":
                if not hb_mgr:
                    return "Error: HeartbeatManager no está inicializado."
                target = arguments.get("target", "heartbeat")
                if target in ("heartbeat", "global", "global_heartbeat"):
                    hb_mgr.trigger_global_now()
                    return "Pulso Global de Heartbeat iniciado de inmediato en segundo plano."
                else:
                    hb_mgr.trigger_automation_now(target)
                    return f"Automatización '{target}' iniciada de inmediato en segundo plano."

            else:
                return f"Error: Herramienta de automatización desconocida '{original_name}'."
        except Exception as e:
            logger.error(f"Error executing automation tool '{original_name}': {e}", exc_info=True)
            return f"Error al ejecutar herramienta de automatización '{original_name}': {str(e)}"

    # 3. Skills tools
    if category == "skills":
        from pequeno_asistente.agent.skills_manager import SkillManager
        skills_mgr = SkillManager.get_instance(config_manager)
        try:
            if original_name == "list":
                src_filter = arguments.get("source", "all")
                query = arguments.get("query", "").strip().lower()
                discovered = skills_mgr.discover_skills()
                res_list = []
                for sid, s in discovered.items():
                    if src_filter != "all" and s.source != src_filter:
                        continue
                    if query and query not in s.name.lower() and query not in s.description.lower() and query not in sid:
                        continue
                    res_list.append(s.to_dict())
                return json.dumps({
                    "count": len(res_list),
                    "skills": res_list
                }, indent=2, ensure_ascii=False)

            elif original_name == "get":
                sid = arguments.get("skill_id", "")
                s = skills_mgr.get_skill(sid)
                if not s:
                    return f"Error: No se encontró la habilidad '{sid}'."
                return json.dumps({
                    "id": s.id,
                    "name": s.name,
                    "source": s.source_label,
                    "description": s.description,
                    "path": str(s.path),
                    "tags": s.tags,
                    "content": s.read_content()
                }, indent=2, ensure_ascii=False)

            elif original_name == "install":
                result = skills_mgr.install_skill(
                    source=arguments.get("source"),
                    name=arguments.get("name"),
                    content=arguments.get("content"),
                    target_scope=arguments.get("target", "user"),
                    as_symlink=arguments.get("as_symlink", False)
                )
                return json.dumps(result, indent=2, ensure_ascii=False)

            elif original_name == "uninstall":
                sid = arguments.get("skill_id", "")
                res = skills_mgr.uninstall_skill(sid)
                return json.dumps(res, indent=2, ensure_ascii=False)

            elif original_name == "toggle":
                sid = arguments.get("skill_id", "")
                enabled = bool(arguments.get("enabled", True))
                ok = skills_mgr.toggle_skill(sid, enabled)
                if ok:
                    state_str = "habilitada" if enabled else "deshabilitada"
                    return json.dumps({
                        "status": "success",
                        "skill_id": sid,
                        "enabled": enabled,
                        "message": f"Habilidad '{sid}' {state_str} con éxito."
                    }, indent=2, ensure_ascii=False)
                return f"Error: No se encontró la habilidad '{sid}'."

            return f"Error: Herramienta de skills '{original_name}' no reconocida."
        except Exception as e:
            logger.error(f"Error executing skill tool '{original_name}': {e}", exc_info=True)
            return f"Error al ejecutar habilidad '{original_name}': {str(e)}"

    # 4. Episodic & Long-Term Memory tools
    if category == "memory":
        try:
            from pequeno_asistente.agent.heartbeat import HeartbeatManager
            from pequeno_asistente.agent.memory import MemoryManager
            hb_mgr = HeartbeatManager.get_instance()
            mem_mgr = (
                hb_mgr.memory_manager
                if (hb_mgr and getattr(hb_mgr, "memory_manager", None))
                else MemoryManager.get_instance()
            )

            if original_name == "search_episodes":
                query = arguments.get("query", "")
                limit = int(arguments.get("limit", 5))
                results = mem_mgr.search_episodes(
                    query=query,
                    limit=limit,
                    date_from=arguments.get("date_from"),
                    date_to=arguments.get("date_to"),
                    category=arguments.get("category"),
                    tag=arguments.get("tag"),
                    tool_filter=arguments.get("tool_filter")
                )
                return json.dumps({
                    "query": query,
                    "count": len(results),
                    "episodes": results
                }, indent=2, ensure_ascii=False)

            elif original_name == "save_episode":
                title = arguments.get("title", "").strip()
                summary = arguments.get("summary", "").strip()
                if not title or not summary:
                    return "Error: Se requieren 'title' y 'summary' para guardar un episodio."
                saved = mem_mgr.save_episode(
                    title=title,
                    summary=summary,
                    category=arguments.get("category", "milestone"),
                    tags=arguments.get("tags"),
                    importance=float(arguments.get("importance", 0.88))
                )
                return json.dumps({
                    "status": "saved",
                    "episode": saved
                }, indent=2, ensure_ascii=False)

            elif original_name == "get_timeline":
                timeline = mem_mgr.get_timeline(
                    date_str=arguments.get("date_str"),
                    limit=int(arguments.get("limit", 15)),
                    category=arguments.get("category")
                )
                return json.dumps({
                    "count": len(timeline),
                    "timeline": timeline
                }, indent=2, ensure_ascii=False)

            elif original_name == "remember_fact":
                fact = arguments.get("fact", "").strip()
                if not fact:
                    return "Error: El parámetro 'fact' no puede estar vacío."
                target = arguments.get("target", "memory")
                if target == "lessons":
                    sec = arguments.get("section", "## Reglas Críticas")
                    mem_mgr.append_lesson(fact, section=sec)
                    return json.dumps({
                        "status": "saved",
                        "target": "LESSONS.md",
                        "section": sec,
                        "fact": fact
                    }, indent=2, ensure_ascii=False)
                else:
                    sec = arguments.get("section", "## Notas Recientes")
                    mem_mgr.append_fact(fact, section=sec)
                    return json.dumps({
                        "status": "saved",
                        "target": "MEMORY.md",
                        "section": sec,
                        "fact": fact
                    }, indent=2, ensure_ascii=False)

            elif original_name == "find_related":
                sid = arguments.get("source_id", "").strip()
                if not sid:
                    return "Error: Se requiere 'source_id'."
                limit = int(arguments.get("limit", 4))
                rel = mem_mgr.find_related_episodes(source_id=sid, limit=limit)
                return json.dumps({
                    "source_id": sid,
                    "count": len(rel),
                    "related_episodes": rel
                }, indent=2, ensure_ascii=False)

            elif original_name == "stats":
                if arguments.get("sync", False):
                    mem_mgr.sync_all()
                stats = mem_mgr.get_episodic_stats()
                return json.dumps(stats, indent=2, ensure_ascii=False)

            return f"Error: Herramienta de memoria '{original_name}' no reconocida."
        except Exception as e:
            logger.error(f"Error executing memory tool '{original_name}': {e}", exc_info=True)
            return f"Error al ejecutar herramienta de memoria '{original_name}': {str(e)}"

    # 5. Media (Documents & Images) tools
    if category == "media":
        try:
            if original_name == "read_document":
                doc_path = arguments.get("path", "").strip()
                if not doc_path:
                    return "Error: El parámetro 'path' es obligatorio para leer un documento."
                start_page = int(arguments.get("start_page", 1) or 1)
                end_page_raw = arguments.get("end_page")
                end_page = int(end_page_raw) if end_page_raw is not None else None
                max_chars = int(arguments.get("max_chars", 30000) or 30000)
                search_query = arguments.get("search_query")

                res = process_document(
                    path=doc_path,
                    start_page=start_page,
                    end_page=end_page,
                    max_chars=max_chars,
                    search_query=search_query
                )
                return json.dumps(res, indent=2, ensure_ascii=False)

            elif original_name == "read_image":
                img_path = arguments.get("path", "").strip()
                if not img_path:
                    return "Error: El parámetro 'path' es obligatorio para leer una imagen."
                extract_ocr = bool(arguments.get("extract_ocr", True))
                max_dim = int(arguments.get("max_dimension", 1568) or 1568)

                res = process_image(
                    path=img_path,
                    max_dimension=max_dim,
                    include_base64=False,
                    extract_ocr=extract_ocr
                )
                if res.get("status") == "ok":
                    active_model = config_manager.get_active_model() if config_manager else ""
                    provider = config_manager.data["llm"].get("provider", "") if config_manager else ""
                    multimodal = is_model_multimodal(active_model, provider) if active_model else True
                    res["model_is_multimodal"] = multimodal
                    res["multimodal_ready"] = True
                    if multimodal:
                        res["vision_note"] = (
                            "La imagen ha sido cargada e inyectada visualmente en tu contexto multimodal actual. "
                            "Analízala directamente en tu siguiente respuesta."
                        )
                    else:
                        res["vision_note"] = (
                            f"El modelo activo ({active_model}) no es multimodal; se proporcionan los metadatos "
                            "y el texto OCR extraído de la imagen."
                        )
                return json.dumps(res, indent=2, ensure_ascii=False)

            elif original_name == "read_audio":
                aud_path = arguments.get("path", "").strip()
                if not aud_path:
                    return "Error: El parámetro 'path' es obligatorio para leer un archivo de audio."

                active_model = config_manager.get_active_model() if config_manager else ""
                provider = config_manager.data["llm"].get("provider", "") if config_manager else ""
                audio_multimodal = is_model_audio_multimodal(active_model, provider) if active_model else True

                res = process_audio(
                    path=aud_path,
                    include_base64=False,
                    target_provider=provider,
                    target_model=active_model
                )
                if res.get("status") == "ok":
                    res["model_is_audio_multimodal"] = audio_multimodal
                    res["multimodal_ready"] = audio_multimodal
                    if audio_multimodal:
                        res["audio_note"] = (
                            "El archivo de audio ha sido cargado e inyectado multimodalmente en tu contexto actual. "
                            "Escúchalo y analízalo directamente en tu siguiente respuesta."
                        )
                    else:
                        res["audio_note"] = (
                            f"El modelo activo ({active_model}) no soporta entrada de audio multimodal directa; "
                            "se proporcionan únicamente los metadatos técnicos del archivo."
                        )
                return json.dumps(res, indent=2, ensure_ascii=False)

            elif original_name == "inspect_file":
                target_path = arguments.get("path", "").strip()
                if not target_path:
                    return "Error: El parámetro 'path' es obligatorio."
                res = inspect_file(target_path)
                return json.dumps(res, indent=2, ensure_ascii=False)

            return f"Error: Herramienta multimedia '{original_name}' no reconocida."
        except Exception as e:
            logger.error(f"Error executing media tool '{original_name}': {e}", exc_info=True)
            return f"Error al ejecutar herramienta multimedia '{original_name}': {str(e)}"

    return f"Error: Categoría o herramienta '{namespaced_name}' no reconocida en {DEFAULT_MCP_SERVER_ID}."


def build_default_mcp_tool_objects() -> List[SimpleNamespace]:
    """Builds MCP Tool-compatible objects for the default server instance."""
    tools = []
    for t_def in ALL_DEFAULT_TOOLS_DEFINITIONS:
        fn = t_def.get("function", {})
        tools.append(
            SimpleNamespace(
                name=fn.get("name", ""),
                description=fn.get("description", ""),
                inputSchema=fn.get("parameters", {"type": "object", "properties": {}})
            )
        )
    return tools


async def run_stdio_mcp_server():
    """
    Runs the default Pequeño Asistente Core MCP server over standard input/output (stdio)
    using the official `mcp` SDK protocol.
    """
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
    from pequeno_asistente.config import ConfigManager

    cfg_mgr = ConfigManager()
    server = Server(DEFAULT_MCP_SERVER_ID)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        res = []
        for t_def in ALL_DEFAULT_TOOLS_DEFINITIONS:
            fn = t_def["function"]
            res.append(
                types.Tool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    inputSchema=fn.get("parameters", {"type": "object", "properties": {}})
                )
            )
        return res

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        out = await execute_default_mcp_tool(name, arguments or {}, config_manager=cfg_mgr)
        return [types.TextContent(type="text", text=out)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(run_stdio_mcp_server())
