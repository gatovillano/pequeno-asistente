"""
Layered Memory System for Pequeño Asistente.
Inspired by OpenClaw and cognitive agent memory architectures:
- Layer 1: User Profile & Identity (USER.md)
- Layer 2: Long-Term Knowledge & Facts (MEMORY.md)
- Layer 3: Learned Rules & Anti-Patterns (LESSONS.md)
- Layer 4: Daily Ephemera & Interaction Logs (daily/YYYY-MM-DD.md)
- Layer 5: Cognitive Episodic Memory Index (SQLite FTS5 + Hybrid Semantic/Temporal/Salience Ranking)
"""
import os
import re
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from pequeno_asistente.agent.episodic_index import (
    EpisodicMemoryIndex,
    summarize_assistant_response,
    is_trivial_interaction,
    extract_episode_metadata
)

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

DEFAULT_LESSONS_MD = """# 🛡️ Reglas y Lecciones Aprendidas (LESSONS.md)

> Este archivo almacena correcciones expresadas por el usuario, advertencias de fallos
> y directivas de comportamiento para evitar repetir errores.
> TIENEN PRIORIDAD MÁXIMA sobre cualquier sugerencia genérica del modelo.

## Reglas Críticas
- [2026-10-24] Respetar las preferencias de herramientas del usuario y evitar repeticiones innecesarias de comandos ya ejecutados.
- [2026-10-24] Para tareas con comandos interactivos o permisos elevados (sudo), guiar al usuario a la Terminal PTY (Ctrl+T) en lugar de intentar ejecutarlos en segundo plano.
- [2026-10-25] Ante correcciones del usuario ("no hagas X, haz Y"), acatar de inmediato la corrección y mantener la consistencia en los siguientes turnos.
"""

DEFAULT_HEARTBEAT_MD = """# 💓 Pulso Proactivo del Agente (HEARTBEAT.md)

> Este archivo define la lista de chequeo periódico que Pequeño Asistente evalúa
> automáticamente en cada pulso (Heartbeat) cuando está activo.
> Si tras revisar los puntos todo está normal y no hay alertas, responde exactamente `HEARTBEAT_OK`.

## Lista de Verificación en cada Pulso
- Revisa si hay tareas pendientes urgentes o recordatorios anotados en `MEMORY.md` o en la bitácora de hoy.
- Si dispones de herramientas de sistema, verifica que no haya alertas críticas (uso excesivo de RAM/disco > 90%).
- Si todo se encuentra en orden y no requiere la atención del usuario, responde únicamente: `HEARTBEAT_OK`.
"""


class MemoryManager:
    _instance: Optional["MemoryManager"] = None

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".config" / "pequeno-asistente" / "memory"

        self.daily_dir = self.base_dir / "daily"
        self.threads_dir = self.base_dir.parent / "threads"
        self._ensure_directories()
        self._ensure_default_files()

        # Cognitive Episodic Memory Index (SQLite FTS5 + Hybrid Ranking)
        self.episodic_index = EpisodicMemoryIndex(self.base_dir)
        self.sync_all()

        if base_dir is None:
            MemoryManager._instance = self

    @classmethod
    def get_instance(cls) -> "MemoryManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def sync_all(self):
        """Synchronizes both daily markdown logs and saved conversation threads into the episodic index."""
        try:
            self.episodic_index.sync_daily_logs(self.daily_dir)
            if self.threads_dir.exists():
                self.episodic_index.sync_threads(self.threads_dir)
        except Exception:
            pass

    def _ensure_directories(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_default_files(self):
        user_file = self.base_dir / "USER.md"
        memory_file = self.base_dir / "MEMORY.md"
        lessons_file = self.base_dir / "LESSONS.md"

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

        if not lessons_file.exists():
            lessons_file.write_text(DEFAULT_LESSONS_MD, encoding="utf-8")

        heartbeat_file = self.base_dir / "HEARTBEAT.md"
        if not heartbeat_file.exists():
            heartbeat_file.write_text(DEFAULT_HEARTBEAT_MD, encoding="utf-8")

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

    def get_lessons(self) -> str:
        lessons_file = self.base_dir / "LESSONS.md"
        if lessons_file.exists():
            return lessons_file.read_text(encoding="utf-8")
        return DEFAULT_LESSONS_MD

    def get_heartbeat_md(self) -> str:
        heartbeat_file = self.base_dir / "HEARTBEAT.md"
        if heartbeat_file.exists():
            return heartbeat_file.read_text(encoding="utf-8")
        return DEFAULT_HEARTBEAT_MD

    def save_heartbeat_md(self, content: str):
        (self.base_dir / "HEARTBEAT.md").write_text(content, encoding="utf-8")

    def get_today_log(self, max_chars: int = 2400) -> str:
        today_str = datetime.date.today().isoformat()
        today_file = self.daily_dir / f"{today_str}.md"
        if today_file.exists():
            text = today_file.read_text(encoding="utf-8").strip()
            if len(text) > max_chars:
                return "...(bitácora previa truncada)...\n" + text[-max_chars:]
            return text
        return ""

    def get_relevant_episodes_prompt(self, query: str, limit: int = 4) -> str:
        """
        Retrieves matching historical interactions from the cognitive episodic index
        (combining FTS5 BM25, stemmed TF cosine similarity, temporal intent parsing,
        salience, and spaced-repetition reinforcement).
        """
        if not query or len(query.strip()) < 3:
            return ""

        if is_trivial_interaction(query):
            return ""

        matches = self.episodic_index.search(query, limit=limit)
        if not matches:
            return ""

        episodes_text = []
        for m in matches:
            ts_label = m.get("timestamp") or m.get("date_str", "Pasado")
            if "T" in ts_label:
                ts_label = ts_label.replace("T", " ")[:16]
            cat = m.get("category", "interaction")
            imp = m.get("importance", 0.5)
            title = m.get("title", "Episodio")
            tags = m.get("tags", [])
            tools = m.get("tools_used", [])

            vec_sim = m.get("vector_similarity")
            meta_badges = [f"{ts_label}", f"cat:{cat}", f"score:{m.get('score', 0):.2f}"]
            if vec_sim is not None and vec_sim > 0:
                meta_badges.append(f"cos_vec:{vec_sim:.2f}")
            if tags:
                meta_badges.append(" ".join(f"#{t}" for t in tags[:4]))
            if tools:
                short_tools = [t.split("__")[-1] for t in tools[:3]]
                meta_badges.append(f"tools:[{', '.join(short_tools)}]")

            header = f"- **[{ ' | '.join(meta_badges) }] {title}**"

            # Prefer structured user + assistant summary if available
            u_msg = (m.get("user_msg") or "").strip().replace("\n", " ")
            a_msg = (m.get("assistant_msg") or "").strip().replace("\n", " ")
            if u_msg and a_msg:
                body = f"  • Usuario: {u_msg[:180]}\n  • Asistente: {a_msg[:260]}"
            else:
                snippet = (m.get("snippet") or m.get("content") or "").replace("\n", " ").strip()
                body = f"  • Detalle: {snippet[:300]}"

            episodes_text.append(f"{header}\n{body}")

        if not episodes_text:
            return ""

        temporal_note = ""
        if matches[0].get("temporal_label"):
            temporal_note = f" (Filtro temporal detectado: {matches[0]['temporal_label']})"

        return (
            f"--- RECUERDOS EPISÓDICOS RELEVANTES{temporal_note} ---\n"
            + "\n".join(episodes_text)
        )

    def get_memory_context_prompt(self, current_query: Optional[str] = None) -> str:
        """
        Combines active memory layers into a structured system prompt section:
        1. USER.md
        2. LESSONS.md (Priority behavioral rules & corrections)
        3. MEMORY.md
        4. Daily Log (Today's recent turns)
        5. Cognitive Episodic Retrieval (Hybrid FTS5 + Semantic + Temporal + Salience)
        """
        user_text = self.get_user_profile().strip()
        lessons_text = self.get_lessons().strip()
        memory_text = self.get_long_term_memory().strip()
        today_text = self.get_today_log().strip()

        context_parts = [
            "<user_memory_system>",
            "Información, contexto persistente y directivas aprendidas (manténlas siempre presentes):",
            "",
            "--- PERFIL DE USUARIO (USER.md) ---",
            user_text,
            "",
            "--- REGLAS Y LECCIONES APRENDIDAS (LESSONS.md) ---",
            lessons_text,
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

        # Dynamic episodic memory retrieval if query provided
        if current_query:
            episodic_block = self.get_relevant_episodes_prompt(current_query)
            if episodic_block:
                context_parts.extend([
                    "",
                    episodic_block
                ])

        context_parts.extend([
            "",
            "Instrucciones de consistencia y memoria:",
            "- Cumple ESTRICTAMENTE las directivas de LESSONS.md; evitan repetir errores ya corregidos por el usuario.",
            "- Utiliza los hechos de USER.md, MEMORY.md y los Recuerdos Episódicos para responder con continuidad histórica sin pedir datos que ya conoces.",
            "- Cuentas con las herramientas `memory__search_episodes`, `memory__save_episode`, `memory__get_timeline`, `memory__remember_fact` y `memory__stats` cuando necesites buscar activamente en el pasado, consultar qué se hizo en una fecha o guardar un hito importante.",
            "- Si el usuario corrige tu comportamiento ('no hagas X, haz Y' o 'recuerda que...'), asume esa regla y mantenla constante.",
            "</user_memory_system>"
        ])

        return "\n".join(context_parts)

    def save_user_profile(self, content: str):
        (self.base_dir / "USER.md").write_text(content, encoding="utf-8")

    def save_long_term_memory(self, content: str):
        (self.base_dir / "MEMORY.md").write_text(content, encoding="utf-8")

    def save_lessons(self, content: str):
        (self.base_dir / "LESSONS.md").write_text(content, encoding="utf-8")

    def append_lesson(self, lesson: str, section: str = "## Reglas Críticas"):
        lessons_file = self.base_dir / "LESSONS.md"
        content = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else DEFAULT_LESSONS_MD

        date_stamp = datetime.date.today().isoformat()
        entry = f"- [{date_stamp}] {lesson.strip()}\n"

        if section in content:
            parts = content.split(section)
            content = parts[0] + section + "\n" + entry + parts[1].lstrip("\n")
        else:
            content += f"\n\n{section}\n{entry}"

        lessons_file.write_text(content, encoding="utf-8")

        # Also index as a high-importance lesson episode
        try:
            self.episodic_index.save_custom_episode(
                title=f"Lección aprendida: {lesson.strip()}",
                summary=lesson.strip(),
                category="lesson",
                tags=["regla", "leccion"],
                importance=0.92,
                date_str=date_stamp
            )
        except Exception:
            pass

    def detect_and_store_lesson(self, user_msg: str) -> Optional[str]:
        """
        Detects explicit user corrections or rule instructions and saves them to LESSONS.md.
        e.g.:
        'de ahora en adelante responde siempre en inglés'
        'recuerda que siempre debes usar uv para python'
        'no vuelvas a borrar archivos sin preguntar'
        """
        patterns = [
            r"^(?:de ahora en adelante|a partir de ahora|en adelante)[,\s]+(.+)$",
            r"^(?:recuerda que|acuérdate de|acuerdate de|no olvides que)[,\s]+(.+)$",
            r"^(?:no vuelvas a|nunca hagas|nunca más|nunca mas|no vuelvas nunca a)[,\s]+(.+)$",
            r"^(?:regla:|directiva:)\s*(.+)$"
        ]
        msg_clean = user_msg.strip()
        for pat in patterns:
            m = re.search(pat, msg_clean, re.IGNORECASE)
            if m:
                rule_candidate = m.group(1).strip()
                if len(rule_candidate) > 6:
                    self.append_lesson(rule_candidate)
                    return rule_candidate
        return None

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

        # Also record in episodic memory
        try:
            self.episodic_index.save_custom_episode(
                title=f"Hecho recordado: {fact.strip()}",
                summary=fact.strip(),
                category="decision",
                tags=["memoria", "hecho"],
                importance=0.88,
                date_str=date_stamp
            )
        except Exception:
            pass

    def log_daily_interaction(
        self,
        user_msg: str,
        assistant_summary: str,
        tools_used: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Logs an interaction turn to `daily/YYYY-MM-DD.md` and indexes it into the
        cognitive episodic memory index with extracted tools, entities, tags, and salience.
        """
        if is_trivial_interaction(user_msg, assistant_summary, tools_used):
            return None

        today_str = datetime.date.today().isoformat()
        today_file = self.daily_dir / f"{today_str}.md"
        now_dt = datetime.datetime.now()
        timestamp = now_dt.strftime("%H:%M:%S")

        compact_summary = summarize_assistant_response(assistant_summary, max_chars=700)
        meta = extract_episode_metadata(
            user_msg=user_msg.strip(),
            assistant_msg=compact_summary,
            tools_used=tools_used,
            explicit_tags=tags
        )

        lines = [
            f"### [{timestamp}] {meta['title']}",
            f"**Usuario**: {user_msg.strip()}",
            f"**Asistente**: {compact_summary}"
        ]
        if meta["tools_used"]:
            lines.append(f"**Herramientas**: {', '.join(meta['tools_used'])}")
        if meta["tags"]:
            lines.append(f"**Etiquetas**: {', '.join('#' + t for t in meta['tags'])}")
        if thread_id:
            lines.append(f"**Hilo**: {thread_id}")
        entry = "\n".join(lines) + "\n\n"

        if not today_file.exists():
            today_file.write_text(f"# 📅 Bitácora {today_str}\n\n" + entry, encoding="utf-8")
        else:
            with open(today_file, "a", encoding="utf-8") as f:
                f.write(entry)

        # Index into cognitive episodic memory index
        try:
            return self.episodic_index.index_interaction(
                date_str=today_str,
                user_msg=user_msg,
                assistant_summary=compact_summary,
                tools_used=meta["tools_used"],
                thread_id=thread_id,
                tags=meta["tags"],
                category=meta["category"],
                importance=meta["importance"],
                timestamp=now_dt.isoformat(timespec="seconds")
            )
        except Exception:
            return None

    def search_episodes(
        self,
        query: str,
        limit: int = 5,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        tool_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Searches episodic memory with hybrid lexical, semantic, temporal, and metadata filters."""
        return self.episodic_index.search(
            query=query,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            category=category,
            tag=tag,
            tool_filter=tool_filter
        )

    def save_episode(
        self,
        title: str,
        summary: str,
        category: str = "milestone",
        tags: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        importance: float = 0.85
    ) -> Dict[str, Any]:
        """Saves an explicit high-importance episode into the episodic index."""
        return self.episodic_index.save_custom_episode(
            title=title,
            summary=summary,
            category=category,
            tags=tags,
            entities=entities,
            importance=importance
        )

    def get_timeline(
        self,
        date_str: Optional[str] = None,
        limit: int = 20,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Returns chronological timeline of episodes."""
        return self.episodic_index.get_timeline(date_str=date_str, limit=limit, category=category)

    def get_episodic_stats(self) -> Dict[str, Any]:
        """Returns statistics about the episodic memory index."""
        return self.episodic_index.get_stats()

    def find_related_episodes(self, source_id: str, limit: int = 4) -> List[Dict[str, Any]]:
        """Finds semantically related episodes via dense vector cosine similarity."""
        return self.episodic_index.find_related_episodes(source_id=source_id, limit=limit)

    def list_daily_logs(self) -> List[str]:
        logs = sorted(self.daily_dir.glob("*.md"), reverse=True)
        return [log.stem for log in logs]
