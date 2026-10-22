"""
Layered Memory System for Pequeño Asistente.
Inspired by OpenClaw and modular markdown memory architectures:
- Layer 1: User Profile & Identity (USER.md)
- Layer 2: Long-Term Knowledge & Facts (MEMORY.md)
- Layer 3: Daily Ephemera & Interaction Logs (daily/YYYY-MM-DD.md)
"""
import os
import datetime
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_USER_MD = """# 🧑 Perfil de Usuario (USER.md)

## Identidad
- **Nombre**: Usuario
- **Idioma preferido**: Español (respuestas naturales, concisas y claras)
- **Rol**: Desarrollador / Creador

## Entorno del Sistema
- **Sistema Operativo**: Linux Ubuntu 26.04.1 LTS
- **Gestor de Ventanas**: Hyprland (Wayland)
- **Barra de Estado**: Waybar
- **Shell**: Zsh (con Oh My Zsh, autosuggestions y syntax-highlighting)
- **Herramientas de Paquetes**: Flatpak (user mode), apt, uv, npx, docker

## Proyectos y Ecosistema
- **KogniTerm**: Terminal AI interactiva
- **KognitoAI**: Asistente inteligente y agente multimodal
- **Bitwig Studio**: Entorno de producción musical y control por agente MCP
- **Pequeño Asistente**: Asistente de escritorio ágil basado en MCP

## Preferencias de Interacción
- Prefiere respuestas al grano, bien estructuradas en Markdown.
- Código limpio, explicaciones directas sin relleno innecesario.
"""

DEFAULT_MEMORY_MD = """# 🧠 Memoria a Largo Plazo (MEMORY.md)

> Este archivo almacena hechos persistentes, decisiones tomadas y preferencias
> aprendidas a lo largo del tiempo. Se inyecta automáticamente en el contexto del agente.

## Hechos del Sistema y Hardware
- El usuario utiliza el gestor de ventanas Hyprland en Wayland.
- El servidor MCP `hypruse` está configurado para controlar Hyprland e interactuar con el escritorio.
- El entorno de audio usa PipeWire y WirePlumber.
- Soporta integración con Flatpak y OBS Studio para streaming/grabación.

## Preferencias y Estilo
- Respuestas directas, sintéticas y en español.
- Cuando se requiera ejecutar acciones en el sistema o buscar información, usar los servidores MCP disponibles.

## Proyectos y Contexto
- Integración continua con el ecosistema local (KogniTerm, KognitoAI, Bitwig).
"""

class MemoryManager:
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".config" / "pequeno-asistente" / "memory"
        
        self.daily_dir = self.base_dir / "daily"
        self._ensure_directories()
        self._ensure_default_files()

    def _ensure_directories(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_default_files(self):
        user_file = self.base_dir / "USER.md"
        memory_file = self.base_dir / "MEMORY.md"

        # Check if kogniterm's user_context.md has custom info to seed USER.md
        kogni_context = Path.home() / ".kogniterm" / "user_context.md"
        if not user_file.exists():
            if kogni_context.exists():
                try:
                    content = kogni_context.read_text(encoding="utf-8")
                    if len(content.strip()) > 50:
                        user_file.write_text(content, encoding="utf-8")
                    else:
                        user_file.write_text(DEFAULT_USER_MD, encoding="utf-8")
                except Exception:
                    user_file.write_text(DEFAULT_USER_MD, encoding="utf-8")
            else:
                user_file.write_text(DEFAULT_USER_MD, encoding="utf-8")

        if not memory_file.exists():
            memory_file.write_text(DEFAULT_MEMORY_MD, encoding="utf-8")

    def get_user_profile(self) -> str:
        user_file = self.base_dir / "USER.md"
        if user_file.exists():
            return user_file.read_text(encoding="utf-8")
        return DEFAULT_USER_MD

    def get_long_term_memory(self) -> str:
        memory_file = self.base_dir / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return DEFAULT_MEMORY_MD

    def get_today_log(self) -> str:
        today_str = datetime.date.today().isoformat()
        today_file = self.daily_dir / f"{today_str}.md"
        if today_file.exists():
            return today_file.read_text(encoding="utf-8")
        return ""

    def get_memory_context_prompt(self) -> str:
        """
        Combines active memory layers into a structured system prompt section.
        """
        user_text = self.get_user_profile().strip()
        memory_text = self.get_long_term_memory().strip()
        today_text = self.get_today_log().strip()

        context_parts = [
            "<user_memory_system>",
            "Información y contexto persistente del usuario y entorno (manténlo siempre en mente):",
            "",
            "--- PERFIL DE USUARIO (USER.md) ---",
            user_text,
            "",
            "--- MEMORIA A LARGO PLAZO (MEMORY.md) ---",
            memory_text
        ]

        if today_text:
            context_parts.extend([
                "",
                f"--- BITÁCORA DEL DÍA ({datetime.date.today().isoformat()}) ---",
                today_text
            ])

        context_parts.extend([
            "",
            "Instrucciones de memoria:",
            "- Utiliza esta información para personalizar respuestas y entender referencias del usuario.",
            "- Si el usuario comparte datos importantes sobre sí mismo o solicita recordar algo, indícale amablemente que lo has guardado en la memoria.",
            "</user_memory_system>"
        ])

        return "\n".join(context_parts)

    def save_user_profile(self, content: str):
        (self.base_dir / "USER.md").write_text(content, encoding="utf-8")

    def save_long_term_memory(self, content: str):
        (self.base_dir / "MEMORY.md").write_text(content, encoding="utf-8")

    def append_fact(self, fact: str, section: str = "## Notas Recientes"):
        memory_file = self.base_dir / "MEMORY.md"
        content = memory_file.read_text(encoding="utf-8") if memory_file.exists() else DEFAULT_MEMORY_MD
        
        date_stamp = datetime.date.today().isoformat()
        fact_entry = f"- [{date_stamp}] {fact.strip()}\n"

        if section in content:
            parts = content.split(section)
            content = parts[0] + section + "\n" + fact_entry + parts[1].lstrip("\n")
        else:
            content += f"\n\n{section}\n{fact_entry}"

        memory_file.write_text(content, encoding="utf-8")

    def log_daily_interaction(self, user_msg: str, assistant_summary: str):
        today_str = datetime.date.today().isoformat()
        today_file = self.daily_dir / f"{today_str}.md"
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        entry = f"### [{timestamp}] Interacción\n**Usuario**: {user_msg.strip()}\n**Asistente**: {assistant_summary.strip()}\n\n"
        
        if not today_file.exists():
            today_file.write_text(f"# 📅 Bitácora {today_str}\n\n" + entry, encoding="utf-8")
        else:
            with open(today_file, "a", encoding="utf-8") as f:
                f.write(entry)

    def list_daily_logs(self) -> List[str]:
        logs = sorted(self.daily_dir.glob("*.md"), reverse=True)
        return [log.stem for log in logs]
