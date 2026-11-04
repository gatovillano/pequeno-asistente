"""
Vectorial & Cognitive Episodic Memory Index for Pequeño Asistente.
Combines:
1. Dense Neural Vector Embeddings (384-dim float32 via ONNX Runtime `all-MiniLM-L6-v2` + NumPy matrix cosine search)
2. Maximal Marginal Relevance (MMR) for semantic diversity and deduplication
3. SQLite FTS5 (diacritic-insensitive lexical BM25 search with column weights)
4. Reciprocal Rank Fusion (RRF) + Spanish/English Stemmed TF & Synonym Expansion
5. Temporal Intent Parsing ("ayer", "hoy", "la semana pasada", ISO dates)
6. Salience (importance) scoring and Spaced-Repetition Reinforcement (`access_count`)
"""
import os
import re
import json
import math
import uuid
import sqlite3
import datetime
import unicodedata
import logging
from collections import Counter
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

import numpy as np

from pequeno_asistente.agent.vector_engine import (
    DenseEmbeddingEngine,
    vector_to_blob,
    blob_to_vector,
    maximal_marginal_relevance
)

logger = logging.getLogger("pequeno_asistente.memory.episodic")

# Common conversational stop words in Spanish and English
STOP_WORDS: Set[str] = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al",
    "en", "para", "por", "con", "sin", "sobre", "entre", "hacia", "hasta", "desde",
    "que", "qué", "como", "cómo", "cual", "cuál", "cuando", "cuándo", "donde", "dónde",
    "quien", "quién", "cuyo", "cuya", "y", "o", "u", "ni", "pero", "mas", "sino",
    "te", "me", "se", "nos", "le", "les", "lo", "mi", "mis", "tu", "tus", "su", "sus",
    "es", "son", "fue", "era", "eran", "fueron", "ser", "estar", "está", "estan", "están",
    "estaba", "he", "ha", "han", "hemos", "habia", "había", "hay", "hubo",
    "hola", "asistente", "pequeño", "pequeno", "favor", "puedes", "podrias", "podrías",
    "dime", "dame", "quiero", "necesito", "hacer", "hice", "hicimos", "hizo", "decir",
    "dijiste", "dije", "hablamos", "hablado", "comentamos", "vimos", "paso", "pasó",
    "algo", "todo", "todos", "toda", "todas", "este", "esta", "estos", "estas",
    "ese", "esa", "esos", "esas", "aquel", "aquella", "esto", "eso", "muy", "mas", "más",
    "ya", "si", "sí", "no", "tambien", "también", "solo", "sólo", "the", "and", "for", "with"
}

TEMPORAL_NOISE_WORDS: Set[str] = {
    "ayer", "anoche", "hoy", "mañana", "manana", "tarde", "noche", "anteayer", "antier",
    "semana", "pasada", "pasado", "mes", "dias", "días", "hace", "recientemente", "poco",
    "ultimos", "últimos", "ultima", "última"
}

TRIVIAL_PATTERNS = re.compile(
    r"^(?:hola|buenos?\s+d[ií]as?|buenas?\s+tardes?|buenas?\s+noches?|buenas|"
    r"gracias|muchas\s+gracias|ok|okay|vale|listo|perfecto|entendido|genial|"
    r"adi[oó]s|hasta\s+luego|chao|nos\s+vemos|test|probando|ping|hey|hi|hello)[\s\!\?\.]*$",
    re.IGNORECASE
)

SYNONYM_GROUPS: List[Set[str]] = [
    {"captura", "screenshot", "pantalla", "imagen", "foto"},
    {"audio", "sonido", "pipewire", "wireplumber", "voz", "altavoz", "microfono", "tts"},
    {"error", "fallo", "bug", "excepcion", "problema", "crash", "falla"},
    {"instalar", "configurar", "setup", "instalacion", "configuracion", "ajustar"},
    {"borrar", "eliminar", "quitar", "delete", "remove", "desinstalar", "limpiar"},
    {"archivo", "fichero", "file", "documento", "ruta", "path", "carpeta", "directorio"},
    {"comando", "terminal", "bash", "consola", "shell", "script", "cli"},
    {"atajo", "keybind", "bind", "tecla", "hotkey", "shortcut"},
    {"memoria", "recuerdo", "recordar", "historial", "episodio", "bitacora"},
    {"bitwig", "daw", "musica", "pista", "track", "midi", "sintetizador", "acorde"},
    {"hyprland", "wayland", "ventana", "escritorio", "waybar", "monitor", "workspace"},
    {"automatizacion", "heartbeat", "pulso", "cron", "programar", "recordatorio", "alarma"},
    {"seguridad", "sudo", "contraseña", "password", "credencial", "permiso", "root"},
    {"red", "wifi", "internet", "puerto", "servidor", "api", "url", "http", "cloudflare"},
]

SYNONYM_LOOKUP: Dict[str, Set[str]] = {}
for _grp in SYNONYM_GROUPS:
    for _word in _grp:
        SYNONYM_LOOKUP[_word] = _grp - {_word}

DOMAIN_TAG_KEYWORDS: Dict[str, Set[str]] = {
    "hyprland": {"hyprland", "hypruse", "waybar", "wayland", "workspace", "ventana", "monitor", "bind"},
    "audio": {"audio", "sonido", "pipewire", "wireplumber", "tts", "voz", "kokoro", "whisper", "microfono"},
    "bitwig": {"bitwig", "daw", "midi", "pista", "track", "clip", "instrumento", "mezcla", "tempo"},
    "mcp": {"mcp", "servidor", "herramienta", "tool", "stdio", "sse", "registro"},
    "seguridad": {"sudo", "password", "contraseña", "credencial", "askpass", "root", "permisos", "ufw"},
    "automatizacion": {"heartbeat", "automatizacion", "pulso", "cron", "recordatorio", "intervalo"},
    "skills": {"skill", "skills", "habilidad", "habilidades", "procedimiento", "claude"},
    "codigo": {"python", "javascript", "typescript", "git", "commit", "funcion", "clase", "test", "bug", "codigo"},
    "terminal": {"bash", "zsh", "terminal", "comando", "consola", "apt", "flatpak", "docker", "systemctl"},
    "archivos": {"archivo", "carpeta", "directorio", "json", "markdown", "config", "backup"},
}


def strip_accents(text: str) -> str:
    """Removes diacritical marks from text while preserving base letters."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def stem_word(word: str) -> str:
    """Lightweight Spanish & English suffix stemmer for lexical/semantic normalization."""
    w = strip_accents(word.lower().strip())
    if len(w) <= 4:
        return w

    suffixes = (
        "amientos", "imientos", "amiento", "imiento",
        "aciones", "iciones", "acion", "icion",
        "adoras", "adores", "adora", "ador",
        "izando", "iendo", "ando", "yendo",
        "mente", "idades", "idad",
        "ieron", "aron", "aban", "ian",
        "ando", "iendo", "ar", "er", "ir",
        "ciones", "siones", "cion", "sion",
        "ing", "ed", "es", "s"
    )
    for suf in suffixes:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)]
    return w


def tokenize_and_stem(text: str, remove_temporal: bool = False) -> List[str]:
    """Tokenizes text into normalized, stemmed significant tokens."""
    norm = strip_accents(text.lower())
    words = re.findall(r"[a-z0-9_]{3,}", norm)
    tokens = []
    for w in words:
        if w in STOP_WORDS:
            continue
        if remove_temporal and w in TEMPORAL_NOISE_WORDS:
            continue
        tokens.append(stem_word(w))
    return tokens


def compute_token_vector(text: str, extra_boost_text: str = "") -> Dict[str, float]:
    """Computes an L2-normalized sparse TF vector over stemmed tokens (plus expanded synonyms)."""
    tokens = tokenize_and_stem(text)
    boost_tokens = tokenize_and_stem(extra_boost_text)
    counts: Counter = Counter(tokens)
    for bt in boost_tokens:
        counts[bt] += 2.0

    raw_words = set(re.findall(r"[a-z0-9]{3,}", strip_accents((text + " " + extra_boost_text).lower())))
    for rw in raw_words:
        if rw in SYNONYM_LOOKUP:
            for syn in SYNONYM_LOOKUP[rw]:
                counts[stem_word(syn)] += 0.5

    if not counts:
        return {}

    vec: Dict[str, float] = {}
    norm_sq = 0.0
    for term, freq in counts.items():
        val = 1.0 + math.log(freq) if freq > 0 else 0.0
        vec[term] = round(val, 4)
        norm_sq += val * val

    norm = math.sqrt(norm_sq)
    if norm > 0:
        vec = {k: round(v / norm, 4) for k, v in vec.items()}
    return vec


def cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """Computes cosine similarity between two sparse L2-normalized token vectors."""
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    dot = sum(val * vec_b.get(term, 0.0) for term, val in vec_a.items())
    return max(0.0, min(1.0, dot))


def parse_temporal_intent(query: str, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """
    Detects temporal expressions in Spanish (e.g., 'ayer', 'hoy', 'anteayer',
    'hace 3 días', 'la semana pasada', 'este mes', '2026-10-24') and returns
    a structured date range filter plus a cleaned topical query.
    """
    if today is None:
        today = datetime.date.today()

    q_norm = strip_accents(query.lower())
    date_from: Optional[datetime.date] = None
    date_to: Optional[datetime.date] = None
    label: Optional[str] = None

    # 1. Explicit ISO date YYYY-MM-DD
    m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q_norm)
    if m_iso:
        try:
            dt = datetime.date.fromisoformat(m_iso.group(1))
            date_from = date_to = dt
            label = m_iso.group(1)
        except ValueError:
            pass

    # 2. Explicit DD/MM/YYYY
    if not date_from:
        m_dmy = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", q_norm)
        if m_dmy:
            try:
                d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
                dt = datetime.date(y, m, d)
                date_from = date_to = dt
                label = dt.isoformat()
            except ValueError:
                pass

    # 3. Relative Spanish expressions
    if not date_from:
        if re.search(r"\b(anteayer|antier|antes\s+de\s+ayer)\b", q_norm):
            date_from = date_to = today - datetime.timedelta(days=2)
            label = "anteayer"
        elif re.search(r"\b(ayer|anoche)\b", q_norm):
            date_from = date_to = today - datetime.timedelta(days=1)
            label = "ayer"
        elif re.search(r"\b(hoy|esta\s+manana|esta\s+tarde|esta\s+noche)\b", q_norm):
            date_from = date_to = today
            label = "hoy"
        elif m_days := re.search(r"\bhace\s+(\d+)\s+dias?\b", q_norm):
            n_days = min(int(m_days.group(1)), 365)
            target = today - datetime.timedelta(days=n_days)
            date_from = target - datetime.timedelta(days=1)
            date_to = target + datetime.timedelta(days=1)
            label = f"hace {n_days} días"
        elif re.search(r"\b(semana\s+pasada|ultima\s+semana)\b", q_norm):
            date_from = today - datetime.timedelta(days=14)
            date_to = today - datetime.timedelta(days=1)
            label = "semana pasada"
        elif re.search(r"\b(esta\s+semana|ultimos\s+dias|recientemente|hace\s+poco)\b", q_norm):
            date_from = today - datetime.timedelta(days=7)
            date_to = today
            label = "últimos 7 días"
        elif re.search(r"\b(mes\s+pasado|ultimo\s+mes)\b", q_norm):
            date_from = today - datetime.timedelta(days=60)
            date_to = today - datetime.timedelta(days=15)
            label = "mes pasado"
        elif re.search(r"\b(este\s+mes)\b", q_norm):
            date_from = today.replace(day=1)
            date_to = today
            label = "este mes"

    cleaned_topical = query
    for pat in (
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b(?:antes\s+de\s+ayer|anteayer|antier|ayer|anoche|hoy|esta\s+ma[ñn]ana|esta\s+tarde|esta\s+noche)\b",
        r"\bhace\s+\d+\s+d[ií]as?\b",
        r"\b(?:la\s+)?(?:semana\s+pasada|[uú]ltima\s+semana|esta\s+semana|[uú]ltimos\s+d[ií]as|recientemente|hace\s+poco)\b",
        r"\b(?:el\s+)?(?:mes\s+pasado|[uú]ltimo\s+mes|este\s+mes)\b",
    ):
        cleaned_topical = re.sub(pat, " ", cleaned_topical, flags=re.IGNORECASE)

    return {
        "is_temporal_query": date_from is not None,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "label": label,
        "cleaned_query": cleaned_topical.strip()
    }


def is_trivial_interaction(user_msg: str, assistant_msg: str = "", tools_used: Optional[List[str]] = None) -> bool:
    """Returns True if the interaction is a trivial greeting or one-word ack with no tool usage."""
    if tools_used:
        return False
    u_clean = user_msg.strip()
    if not u_clean:
        return True
    if len(u_clean) < 28 and TRIVIAL_PATTERNS.match(u_clean):
        return True
    return False


def extract_episode_metadata(
    user_msg: str,
    assistant_msg: str,
    tools_used: Optional[List[str]] = None,
    explicit_tags: Optional[List[str]] = None,
    explicit_category: Optional[str] = None,
    explicit_importance: Optional[float] = None
) -> Dict[str, Any]:
    """
    Analyzes an interaction turn to extract cognitive metadata:
    - entities (file paths, commands, URLs, ecosystem components)
    - tags (domain categories & hashtags)
    - category (interaction, tool_execution, error_fix, decision, milestone, lesson)
    - importance (0.0 to 1.0 salience score)
    - title (concise semantic summary of the episode)
    """
    tools_list = list(tools_used) if tools_used else []
    combined = f"{user_msg}\n{assistant_msg}"
    combined_lower = strip_accents(combined.lower())

    # 1. Extract Entities (paths, files, URLs, backtick identifiers)
    entities: List[str] = []
    for m in re.finditer(r"(?:~/[^\s\"'`\)\]]+|/home/[^\s\"'`\)\]]+|/etc/[^\s\"'`\)\]]+|\b[\w\-]+\.(?:py|json|jsonc|md|sh|yml|yaml|toml|js|ts|css|db|conf|service|ini|sql)\b)", combined):
        val = m.group(0).rstrip(".,;:)")
        if val not in entities and len(val) > 3:
            entities.append(val)
    for m in re.finditer(r"https?://[^\s\"'`\)\]]+", combined):
        val = m.group(0).rstrip(".,;:)")
        if val not in entities:
            entities.append(val)
    for m in re.finditer(r"`([^`\n]{3,45})`", combined):
        val = m.group(1).strip()
        if val not in entities and not val.startswith("{"):
            entities.append(val)
    entities = entities[:12]

    # 2. Extract Domain Tags
    tags: Set[str] = set(t.lower().lstrip("#") for t in (explicit_tags or []) if t.strip())
    for m in re.finditer(r"(?:^|\s)#([a-zA-Z0-9_áéíóúñ]{3,24})\b", combined):
        tags.add(strip_accents(m.group(1).lower()))

    for t_name in tools_list:
        srv = t_name.split("__")[0].lower() if "__" in t_name else t_name.lower()
        if srv and srv not in ("default", "mcp"):
            tags.add(srv)

    words_set = set(re.findall(r"[a-z0-9_]{3,}", combined_lower))
    for domain_tag, kw_set in DOMAIN_TAG_KEYWORDS.items():
        if words_set & kw_set:
            tags.add(domain_tag)

    # 3. Determine Category
    if explicit_category:
        category = explicit_category
    elif any(w in combined_lower for w in ("solucionado", "corregido", "arreglado", "fixed", "resuelto")) and any(w in combined_lower for w in ("error", "bug", "fallo", "problema")):
        category = "error_fix"
    elif any(w in combined_lower for w in ("recuerda que", "de ahora en adelante", "regla:", "no vuelvas a")):
        category = "lesson"
    elif any(w in combined_lower for w in ("decidimos", "decision", "arquitectura", "implementado", "completado")):
        category = "milestone"
    elif tools_list:
        category = "tool_execution"
    else:
        category = "interaction"

    # 4. Calculate Salience / Importance (0.0 - 1.0)
    if explicit_importance is not None:
        importance = max(0.0, min(1.0, float(explicit_importance)))
    elif is_trivial_interaction(user_msg, assistant_msg, tools_list):
        importance = 0.08
    else:
        score = 0.40
        if tools_list:
            score += min(0.22, 0.12 + 0.04 * len(tools_list))
        if entities:
            score += min(0.15, 0.05 * len(entities))
        if category in ("error_fix", "milestone", "lesson", "decision"):
            score += 0.15
        if any(w in combined_lower for w in ("importante", "recuerda", "guardar", "nota", "clave", "configur")):
            score += 0.08
        if len(combined) > 350:
            score += 0.07
        importance = round(min(0.98, score), 2)

    # 5. Generate Concise Semantic Title
    clean_u = re.sub(r"[\r\n\t]+", " ", user_msg).strip()
    clean_u = re.sub(r"^[#>*`\-\s¿¡]+", "", clean_u).rstrip("?!")
    if len(clean_u) > 68:
        title_base = clean_u[:65].rstrip() + "…"
    elif clean_u:
        title_base = clean_u
    else:
        title_base = "Registro episódico"

    if tools_list:
        short_tools = [t.split("__")[-1] for t in tools_list[:2]]
        suffix = f" [{', '.join(short_tools)}]"
        if len(title_base) + len(suffix) <= 85:
            title_base += suffix

    return {
        "title": title_base,
        "category": category,
        "tags": sorted(tags)[:10],
        "entities": entities,
        "tools_used": tools_list,
        "importance": importance
    }


def summarize_assistant_response(assistant_msg: str, max_chars: int = 700) -> str:
    """
    Creates a structured, high-signal summary of the assistant's response.
    Avoids cutting mid-word or filling the snippet with raw code blocks.
    """
    text = assistant_msg.strip()
    if not text:
        return ""

    def _compact_code(match):
        lang = (match.group(1) or "código").strip()
        code_lines = match.group(2).strip().splitlines()
        first_line = code_lines[0].strip()[:60] if code_lines else ""
        return f"[Bloque {lang}: `{first_line}` ({len(code_lines)} líneas)]"

    compacted = re.sub(r"```([a-zA-Z0-9_\-]*)\n(.*?)```", _compact_code, text, flags=re.DOTALL)
    compacted = re.sub(r"\n{3,}", "\n\n", compacted).strip()

    if len(compacted) <= max_chars:
        return compacted

    head_len = int(max_chars * 0.72)
    tail_len = max_chars - head_len - 15
    head = compacted[:head_len].rsplit(" ", 1)[0]
    tail = compacted[-tail_len:].split(" ", 1)[-1]
    return f"{head} [...] {tail}"


def clean_query_for_fts(query: str) -> str:
    """
    Prepares a user query for SQLite FTS5 matching with diacritic-insensitive tokens,
    stemmed prefixes, and synonym expansion.
    """
    temporal = parse_temporal_intent(query)
    cleaned_raw = temporal["cleaned_query"] or query

    norm = strip_accents(cleaned_raw.lower())
    norm = re.sub(r"[¿\?¡\!\"\'\(\)\[\]\{\}\:\;\,\.]+", " ", norm).strip()
    words = norm.split()

    keywords = [w for w in words if len(w) > 2 and w not in STOP_WORDS and w not in TEMPORAL_NOISE_WORDS]
    if not keywords:
        keywords = [w for w in words if len(w) > 2 and w not in STOP_WORDS]

    if not keywords:
        return ""

    fts_terms: List[str] = []
    seen_terms: Set[str] = set()

    for kw in keywords[:8]:
        clean_kw = re.sub(r"[^a-z0-9_]", "", kw)
        if not clean_kw or len(clean_kw) < 2:
            continue

        if clean_kw not in seen_terms:
            fts_terms.append(f'"{clean_kw}"*')
            seen_terms.add(clean_kw)

        stem = stem_word(clean_kw)
        if len(stem) >= 4 and stem not in seen_terms:
            fts_terms.append(f'"{stem}"*')
            seen_terms.add(stem)

        if clean_kw in SYNONYM_LOOKUP:
            for syn in list(SYNONYM_LOOKUP[clean_kw])[:2]:
                clean_syn = re.sub(r"[^a-z0-9_]", "", strip_accents(syn))
                if len(clean_syn) >= 3 and clean_syn not in seen_terms:
                    fts_terms.append(f'"{clean_syn}"*')
                    seen_terms.add(clean_syn)

    if not fts_terms:
        return ""

    return " OR ".join(fts_terms)


class EpisodicMemoryIndex:
    SCHEMA_VERSION = 3

    def __init__(self, base_dir: Path, vector_engine: Optional[DenseEmbeddingEngine] = None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "episodes.db"
        self.vector_engine = vector_engine or DenseEmbeddingEngine.get_instance()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes `episodes_meta` (with binary float32 `embedding` BLOB) and `episodes_fts` tables."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS meta_sync (
                        file_name TEXT PRIMARY KEY,
                        last_mtime REAL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_info (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

                cur = conn.execute("SELECT value FROM schema_info WHERE key = 'version'")
                row = cur.fetchone()
                current_ver = int(row["value"]) if row else 1

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS episodes_meta (
                        source_id TEXT PRIMARY KEY,
                        category TEXT DEFAULT 'interaction',
                        date_str TEXT,
                        timestamp TEXT,
                        thread_id TEXT DEFAULT '',
                        title TEXT,
                        user_msg TEXT DEFAULT '',
                        assistant_msg TEXT DEFAULT '',
                        content TEXT,
                        tools_used TEXT DEFAULT '[]',
                        tags TEXT DEFAULT '[]',
                        entities TEXT DEFAULT '[]',
                        importance REAL DEFAULT 0.5,
                        access_count INTEGER DEFAULT 0,
                        last_accessed TEXT DEFAULT '',
                        token_vector TEXT DEFAULT '{}',
                        embedding BLOB DEFAULT NULL,
                        embedding_model TEXT DEFAULT ''
                    )
                """)

                # Ensure v2 -> v3 columns exist if upgrading from v2
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(episodes_meta)").fetchall()}
                if "embedding" not in cols:
                    conn.execute("ALTER TABLE episodes_meta ADD COLUMN embedding BLOB DEFAULT NULL")
                if "embedding_model" not in cols:
                    conn.execute("ALTER TABLE episodes_meta ADD COLUMN embedding_model TEXT DEFAULT ''")

                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_date ON episodes_meta(date_str)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_category ON episodes_meta(category)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes_meta(importance)")

                if current_ver < 2:
                    existing_v1 = []
                    try:
                        cur_v1 = conn.execute("SELECT source_id, category, date_str, title, content FROM episodes_fts")
                        existing_v1 = cur_v1.fetchall()
                    except Exception:
                        pass

                    conn.execute("DROP TABLE IF EXISTS episodes_fts")
                    conn.execute("DELETE FROM meta_sync")

                    conn.execute("""
                        CREATE VIRTUAL TABLE episodes_fts USING fts5(
                            source_id UNINDEXED,
                            title,
                            content,
                            tags_text,
                            entities_text,
                            tools_text,
                            tokenize = 'unicode61 remove_diacritics 2'
                        )
                    """)

                    for r in existing_v1:
                        sid = r["source_id"]
                        if not sid.startswith("daily:"):
                            self._upsert_episode_conn(
                                conn=conn,
                                source_id=sid,
                                category=r["category"] or "interaction",
                                date_str=r["date_str"] or datetime.date.today().isoformat(),
                                timestamp=f"{r['date_str']}T00:00:00",
                                title=r["title"] or "Episodio",
                                user_msg="",
                                assistant_msg="",
                                content=r["content"] or "",
                                tools_used=[],
                                tags=[],
                                entities=[],
                                importance=0.5
                            )
                else:
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                            source_id UNINDEXED,
                            title,
                            content,
                            tags_text,
                            entities_text,
                            tools_text,
                            tokenize = 'unicode61 remove_diacritics 2'
                        )
                    """)

                if current_ver < self.SCHEMA_VERSION:
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)",
                        (str(self.SCHEMA_VERSION),)
                    )

                conn.commit()

                # Backfill any missing dense embeddings in batch
                self._backfill_missing_embeddings()
        except Exception as e:
            logger.error(f"Error initializing EpisodicMemoryIndex database: {e}")

    def _build_embedding_text(
        self,
        title: str,
        user_msg: str,
        assistant_msg: str,
        content: str,
        tags: List[str],
        entities: List[str],
        tools_used: List[str]
    ) -> str:
        """Constructs the canonical semantic passage for dense neural embedding."""
        parts = [title]
        if user_msg or assistant_msg:
            if user_msg:
                parts.append(f"Pregunta: {user_msg[:350]}")
            if assistant_msg:
                parts.append(f"Respuesta: {assistant_msg[:450]}")
        else:
            parts.append(content[:600])
        if tags:
            parts.append(f"Temas: {', '.join(tags)}")
        if entities:
            parts.append(f"Entidades: {', '.join(entities[:6])}")
        if tools_used:
            parts.append(f"Herramientas: {', '.join(tools_used[:4])}")
        return "\n".join(p for p in parts if p)

    def _backfill_missing_embeddings(self):
        """Computes dense float32 embeddings in batch for any episode missing a vector."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT source_id, title, user_msg, assistant_msg, content, tags, entities, tools_used "
                    "FROM episodes_meta WHERE embedding IS NULL"
                ).fetchall()
                if not rows:
                    return

                texts = []
                sids = []
                for r in rows:
                    sids.append(r["source_id"])
                    texts.append(self._build_embedding_text(
                        title=r["title"] or "",
                        user_msg=r["user_msg"] or "",
                        assistant_msg=r["assistant_msg"] or "",
                        content=r["content"] or "",
                        tags=json.loads(r["tags"] or "[]"),
                        entities=json.loads(r["entities"] or "[]"),
                        tools_used=json.loads(r["tools_used"] or "[]")
                    ))

                vecs = self.vector_engine.embed_batch(texts)
                model_name = self.vector_engine.backend_name
                for sid, vec in zip(sids, vecs):
                    conn.execute(
                        "UPDATE episodes_meta SET embedding = ?, embedding_model = ? WHERE source_id = ?",
                        (vector_to_blob(vec), model_name, sid)
                    )
                conn.commit()
                logger.info(f"Backfilled {len(sids)} episodic dense vector embeddings ({model_name}).")
        except Exception as e:
            logger.debug(f"Error backfilling embeddings: {e}")

    def _upsert_episode_conn(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        category: str,
        date_str: str,
        timestamp: str,
        title: str,
        user_msg: str,
        assistant_msg: str,
        content: str,
        tools_used: List[str],
        tags: List[str],
        entities: List[str],
        importance: float,
        thread_id: str = "",
        precomputed_vec: Optional[np.ndarray] = None
    ):
        """Internal helper to upsert an episode into both `episodes_meta` (with dense BLOB) and `episodes_fts`."""
        tags_text = " ".join(tags)
        entities_text = " ".join(entities)
        tools_text = " ".join(tools_used)
        extra_boost = f"{title} {tags_text} {entities_text} {tools_text}"
        token_vec = compute_token_vector(content, extra_boost_text=extra_boost)

        if precomputed_vec is not None:
            dense_vec = precomputed_vec
        else:
            emb_text = self._build_embedding_text(
                title=title,
                user_msg=user_msg,
                assistant_msg=assistant_msg,
                content=content,
                tags=tags,
                entities=entities,
                tools_used=tools_used
            )
            dense_vec = self.vector_engine.embed(emb_text)

        emb_blob = vector_to_blob(dense_vec)
        emb_model = self.vector_engine.backend_name

        cur = conn.execute("SELECT access_count, last_accessed FROM episodes_meta WHERE source_id = ?", (source_id,))
        prev = cur.fetchone()
        access_count = prev["access_count"] if prev else 0
        last_accessed = prev["last_accessed"] if prev else ""

        conn.execute("""
            INSERT OR REPLACE INTO episodes_meta (
                source_id, category, date_str, timestamp, thread_id,
                title, user_msg, assistant_msg, content,
                tools_used, tags, entities, importance,
                access_count, last_accessed, token_vector,
                embedding, embedding_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_id,
            category,
            date_str,
            timestamp,
            thread_id or "",
            title,
            user_msg,
            assistant_msg,
            content,
            json.dumps(tools_used, ensure_ascii=False),
            json.dumps(tags, ensure_ascii=False),
            json.dumps(entities, ensure_ascii=False),
            importance,
            access_count,
            last_accessed,
            json.dumps(token_vec, ensure_ascii=False),
            emb_blob,
            emb_model
        ))

        conn.execute("DELETE FROM episodes_fts WHERE source_id = ?", (source_id,))
        conn.execute("""
            INSERT INTO episodes_fts (source_id, title, content, tags_text, entities_text, tools_text)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            source_id,
            title,
            content,
            tags_text,
            entities_text,
            tools_text
        ))

    def index_interaction(
        self,
        date_str: str,
        user_msg: str,
        assistant_summary: str,
        source_id: Optional[str] = None,
        tools_used: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        importance: Optional[float] = None,
        timestamp: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Indexes a single interaction turn into the vectorial episodic index with automatic
        entity, tag, tool, salience, and dense neural embedding computation.
        """
        u_clean = user_msg.strip()
        a_clean = assistant_summary.strip()
        if not u_clean and not a_clean:
            return None

        if importance is None and is_trivial_interaction(u_clean, a_clean, tools_used):
            return None

        now_dt = datetime.datetime.now()
        ts = timestamp or now_dt.isoformat(timespec="seconds")
        sid = source_id or f"daily:{date_str}#{now_dt.strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"

        compact_assistant = summarize_assistant_response(a_clean, max_chars=750)
        meta = extract_episode_metadata(
            user_msg=u_clean,
            assistant_msg=compact_assistant,
            tools_used=tools_used,
            explicit_tags=tags,
            explicit_category=category,
            explicit_importance=importance
        )

        content_parts = [f"Usuario: {u_clean}", f"Asistente: {compact_assistant}"]
        if meta["tools_used"]:
            content_parts.append(f"Herramientas: {', '.join(meta['tools_used'])}")
        if meta["entities"]:
            content_parts.append(f"Entidades: {', '.join(meta['entities'])}")
        content = "\n".join(content_parts)

        try:
            with self._get_connection() as conn:
                self._upsert_episode_conn(
                    conn=conn,
                    source_id=sid,
                    category=meta["category"],
                    date_str=date_str,
                    timestamp=ts,
                    title=meta["title"],
                    user_msg=u_clean,
                    assistant_msg=compact_assistant,
                    content=content,
                    tools_used=meta["tools_used"],
                    tags=meta["tags"],
                    entities=meta["entities"],
                    importance=meta["importance"],
                    thread_id=thread_id or ""
                )
                conn.commit()
            return {
                "source_id": sid,
                "date_str": date_str,
                "timestamp": ts,
                "embedding_model": self.vector_engine.backend_name,
                **meta
            }
        except Exception as e:
            logger.warning(f"Error indexing episodic interaction: {e}")
            return None

    def save_custom_episode(
        self,
        title: str,
        summary: str,
        category: str = "milestone",
        tags: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        importance: float = 0.85,
        date_str: Optional[str] = None,
        thread_id: str = ""
    ) -> Dict[str, Any]:
        """Explicitly records a high-salience episodic memory with dense vector embedding."""
        now_dt = datetime.datetime.now()
        d_str = date_str or now_dt.date().isoformat()
        ts = now_dt.isoformat(timespec="seconds")
        sid = f"episode:{d_str}#{now_dt.strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"

        meta = extract_episode_metadata(
            user_msg=title,
            assistant_msg=summary,
            explicit_tags=tags,
            explicit_category=category,
            explicit_importance=importance
        )
        merged_entities = list(dict.fromkeys((entities or []) + meta["entities"]))[:12]
        content = f"{title.strip()}\n{summary.strip()}"

        with self._get_connection() as conn:
            self._upsert_episode_conn(
                conn=conn,
                source_id=sid,
                category=category,
                date_str=d_str,
                timestamp=ts,
                title=title.strip() or meta["title"],
                user_msg=title.strip(),
                assistant_msg=summary.strip(),
                content=content,
                tools_used=meta["tools_used"],
                tags=meta["tags"],
                entities=merged_entities,
                importance=meta["importance"],
                thread_id=thread_id
            )
            conn.commit()

        return {
            "source_id": sid,
            "title": title.strip() or meta["title"],
            "category": category,
            "date_str": d_str,
            "timestamp": ts,
            "tags": meta["tags"],
            "entities": merged_entities,
            "importance": meta["importance"],
            "embedding_model": self.vector_engine.backend_name
        }

    def sync_daily_logs(self, daily_dir: Path):
        """
        Scans all markdown files in `daily/` and indexes new or modified files
        using batched dense neural vector embeddings.
        """
        if not daily_dir.exists():
            return

        try:
            with self._get_connection() as conn:
                for file_path in sorted(daily_dir.glob("*.md")):
                    date_str = file_path.stem
                    mtime = file_path.stat().st_mtime

                    cur = conn.execute("SELECT last_mtime FROM meta_sync WHERE file_name = ?", (file_path.name,))
                    row = cur.fetchone()
                    if row and row["last_mtime"] >= mtime:
                        continue

                    content = file_path.read_text(encoding="utf-8", errors="replace")

                    conn.execute("DELETE FROM episodes_fts WHERE source_id LIKE ?", (f"daily:{date_str}#sync_%",))
                    conn.execute("DELETE FROM episodes_meta WHERE source_id LIKE ?", (f"daily:{date_str}#sync_%",))

                    sections = re.split(r"(?=###\s*\[\d{2}:\d{2}:\d{2}\])", content)
                    pending_records = []
                    emb_texts = []
                    idx = 0

                    for sec in sections:
                        sec_strip = sec.strip()
                        if not sec_strip or sec_strip.startswith("# 📅 Bitácora"):
                            continue

                        idx += 1
                        sid = f"daily:{date_str}#sync_{idx}"

                        m_time = re.match(r"###\s*\[(\d{2}:\d{2}:\d{2})\]", sec_strip)
                        time_str = m_time.group(1) if m_time else "00:00:00"
                        timestamp = f"{date_str}T{time_str}"

                        m_user = re.search(r"\*\*Usuario\*\*:\s*(.+?)(?=\n\*\*(?:Asistente|Herramientas|Etiquetas|Hilo)\*\*:|\Z)", sec_strip, re.DOTALL)
                        m_asst = re.search(r"\*\*Asistente\*\*:\s*(.+?)(?=\n\*\*(?:Herramientas|Etiquetas|Hilo)\*\*:|\Z)", sec_strip, re.DOTALL)
                        m_tools = re.search(r"\*\*Herramientas\*\*:\s*(.+)", sec_strip)
                        m_tags = re.search(r"\*\*Etiquetas\*\*:\s*(.+)", sec_strip)
                        m_thread = re.search(r"\*\*Hilo\*\*:\s*(.+)", sec_strip)

                        user_msg = m_user.group(1).strip() if m_user else ""
                        asst_msg = m_asst.group(1).strip() if m_asst else sec_strip
                        tools_used = [t.strip() for t in m_tools.group(1).split(",") if t.strip()] if m_tools else []
                        explicit_tags = [t.strip().lstrip("#") for t in m_tags.group(1).split(",") if t.strip()] if m_tags else []
                        thread_id = m_thread.group(1).strip() if m_thread else ""

                        if is_trivial_interaction(user_msg, asst_msg, tools_used):
                            continue

                        meta = extract_episode_metadata(
                            user_msg=user_msg or sec_strip[:120],
                            assistant_msg=asst_msg,
                            tools_used=tools_used,
                            explicit_tags=explicit_tags
                        )

                        emb_txt = self._build_embedding_text(
                            title=meta["title"],
                            user_msg=user_msg,
                            assistant_msg=asst_msg,
                            content=sec_strip,
                            tags=meta["tags"],
                            entities=meta["entities"],
                            tools_used=meta["tools_used"]
                        )
                        emb_texts.append(emb_txt)
                        pending_records.append((sid, timestamp, user_msg, asst_msg, sec_strip, thread_id, meta))

                    if pending_records:
                        batch_vecs = self.vector_engine.embed_batch(emb_texts)
                        for (sid, timestamp, user_msg, asst_msg, sec_strip, thread_id, meta), vec in zip(pending_records, batch_vecs):
                            self._upsert_episode_conn(
                                conn=conn,
                                source_id=sid,
                                category=meta["category"],
                                date_str=date_str,
                                timestamp=timestamp,
                                title=meta["title"],
                                user_msg=user_msg,
                                assistant_msg=asst_msg,
                                content=sec_strip,
                                tools_used=meta["tools_used"],
                                tags=meta["tags"],
                                entities=meta["entities"],
                                importance=meta["importance"],
                                thread_id=thread_id,
                                precomputed_vec=vec
                            )

                    conn.execute(
                        "INSERT OR REPLACE INTO meta_sync (file_name, last_mtime) VALUES (?, ?)",
                        (file_path.name, mtime)
                    )

                conn.commit()
        except Exception as e:
            logger.error(f"Error syncing daily logs to EpisodicMemoryIndex: {e}")

    def sync_threads(self, threads_dir: Path):
        """
        Scans saved conversation thread JSON files (`threads/*.json`) and indexes
        substantive conversational turns using batched dense vector embeddings.
        """
        if not threads_dir or not threads_dir.exists():
            return

        try:
            with self._get_connection() as conn:
                for fpath in sorted(threads_dir.glob("*.json")):
                    mtime = fpath.stat().st_mtime
                    sync_key = f"thread:{fpath.name}"

                    cur = conn.execute("SELECT last_mtime FROM meta_sync WHERE file_name = ?", (sync_key,))
                    row = cur.fetchone()
                    if row and row["last_mtime"] >= mtime:
                        continue

                    try:
                        data = json.loads(fpath.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        continue

                    thread_id = data.get("id", fpath.stem)
                    thread_title = data.get("title", "Conversación")
                    created_at = data.get("created_at", "") or data.get("updated_at", "")
                    date_str = created_at[:10] if len(created_at) >= 10 else datetime.date.today().isoformat()
                    timestamp = created_at[:19] if len(created_at) >= 19 else f"{date_str}T00:00:00"

                    messages = data.get("messages", [])
                    if not messages:
                        continue

                    conn.execute("DELETE FROM episodes_fts WHERE source_id LIKE ?", (f"thread:{thread_id}#%",))
                    conn.execute("DELETE FROM episodes_meta WHERE source_id LIKE ?", (f"thread:{thread_id}#%",))

                    pending_records = []
                    emb_texts = []
                    turn_idx = 0
                    i = 0
                    while i < len(messages):
                        msg = messages[i]
                        if msg.get("role") != "user" or msg.get("_auto_injected_media"):
                            i += 1
                            continue

                        raw_u = msg.get("display_text") if msg.get("display_text") is not None else msg.get("content")
                        if isinstance(raw_u, list):
                            raw_u = " ".join(
                                str(p.get("text") or "")
                                for p in raw_u
                                if isinstance(p, dict) and p.get("type") == "text"
                            )
                        user_text = str(raw_u or "").strip()
                        tools_in_turn: List[str] = []
                        asst_texts: List[str] = []
                        j = i + 1
                        while j < len(messages) and (messages[j].get("role") != "user" or messages[j].get("_auto_injected_media")):
                            sub = messages[j]
                            role = sub.get("role")
                            if role == "assistant":
                                sub_c = sub.get("content")
                                if isinstance(sub_c, str) and sub_c.strip():
                                    asst_texts.append(sub_c.strip())
                                for tc in sub.get("tool_calls") or []:
                                    fn = tc.get("function", {}).get("name")
                                    if fn and fn not in tools_in_turn:
                                        tools_in_turn.append(fn)
                            elif role == "tool":
                                tname = sub.get("name")
                                if tname and tname not in tools_in_turn:
                                    tools_in_turn.append(tname)
                            j += 1

                        i = j
                        asst_combined = "\n".join(asst_texts).strip()
                        if is_trivial_interaction(user_text, asst_combined, tools_in_turn):
                            continue

                        turn_idx += 1
                        sid = f"thread:{thread_id}#{turn_idx}"
                        compact_asst = summarize_assistant_response(asst_combined, max_chars=700)
                        meta = extract_episode_metadata(
                            user_msg=user_text,
                            assistant_msg=compact_asst,
                            tools_used=tools_in_turn
                        )

                        title = meta["title"]
                        if thread_title and thread_title not in ("Nueva conversación", "Nuevo hilo"):
                            title = f"{thread_title}: {title}"[:90]

                        content = f"Hilo: {thread_title}\nUsuario: {user_text}\nAsistente: {compact_asst}"
                        if tools_in_turn:
                            content += f"\nHerramientas: {', '.join(tools_in_turn)}"

                        emb_txt = self._build_embedding_text(
                            title=title,
                            user_msg=user_text,
                            assistant_msg=compact_asst,
                            content=content,
                            tags=meta["tags"],
                            entities=meta["entities"],
                            tools_used=tools_in_turn
                        )
                        emb_texts.append(emb_txt)
                        pending_records.append((sid, title, user_text, compact_asst, content, tools_in_turn, meta))

                    if pending_records:
                        batch_vecs = self.vector_engine.embed_batch(emb_texts)
                        for (sid, title, user_text, compact_asst, content, tools_in_turn, meta), vec in zip(pending_records, batch_vecs):
                            self._upsert_episode_conn(
                                conn=conn,
                                source_id=sid,
                                category=meta["category"],
                                date_str=date_str,
                                timestamp=timestamp,
                                title=title,
                                user_msg=user_text,
                                assistant_msg=compact_asst,
                                content=content,
                                tools_used=tools_in_turn,
                                tags=meta["tags"],
                                entities=meta["entities"],
                                importance=meta["importance"],
                                thread_id=thread_id,
                                precomputed_vec=vec
                            )

                    conn.execute(
                        "INSERT OR REPLACE INTO meta_sync (file_name, last_mtime) VALUES (?, ?)",
                        (sync_key, mtime)
                    )

                conn.commit()
        except Exception as e:
            logger.error(f"Error syncing threads to EpisodicMemoryIndex: {e}")

    def search(
        self,
        query: str,
        limit: int = 4,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        tool_filter: Optional[str] = None,
        thread_id: Optional[str] = None,
        min_importance: float = 0.15,
        reinforce: bool = True,
        use_mmr: bool = True,
        today: Optional[datetime.date] = None
    ) -> List[Dict[str, Any]]:
        """
        True Vectorial + Hybrid Cognitive Search across episodic memory:
        1. Parses natural language temporal expressions ("ayer", "la semana pasada", etc.).
        2. Encodes the query into a 384-dim dense float32 vector via `DenseEmbeddingEngine`
           and computes exact vectorized cosine similarity (`matrix @ query_vec`) across all episodes.
        3. Retrieves FTS5 BM25 lexical candidates and stemmed sparse TF similarity.
        4. Combines Dense Vector Similarity + Lexical BM25 + Reciprocal Rank Fusion (RRF) +
           Exponential Recency Decay + Salience (`importance`) + Spaced Repetition (`access_count`).
        5. Applies Maximal Marginal Relevance (MMR) over the dense embedding vectors to guarantee
           semantic diversity and eliminate redundant results.
        """
        if today is None:
            today = datetime.date.today()

        temporal = parse_temporal_intent(query, today=today)
        eff_date_from = date_from or temporal["date_from"]
        eff_date_to = date_to or temporal["date_to"]
        topical_query = temporal["cleaned_query"]
        has_topical_text = bool(topical_query and len( tokenize_and_stem(topical_query, remove_temporal=True) ) > 0)

        effective_q_text = topical_query if has_topical_text else query
        fts_query = clean_query_for_fts(effective_q_text)
        sparse_query_vec = compute_token_vector(effective_q_text)

        # Compute 384-dim dense neural embedding for query
        dense_query_vec: Optional[np.ndarray] = None
        if has_topical_text or not temporal["is_temporal_query"]:
            dense_query_vec = self.vector_engine.embed(effective_q_text)

        fts_candidates: Dict[str, Dict[str, Any]] = {}

        try:
            with self._get_connection() as conn:
                # 1. FTS5 Lexical Candidates
                if fts_query:
                    try:
                        sql_fts = """
                            SELECT
                                f.source_id,
                                snippet(episodes_fts, 1, '«', '»', '...', 32) AS snippet,
                                bm25(episodes_fts, 0.0, 5.0, 1.0, 4.0, 4.0, 3.0) AS raw_bm25
                            FROM episodes_fts f
                            WHERE episodes_fts MATCH ?
                            ORDER BY raw_bm25 ASC
                            LIMIT 50
                        """
                        for rank_idx, r in enumerate(conn.execute(sql_fts, (fts_query,)).fetchall()):
                            fts_candidates[r["source_id"]] = {
                                "snippet": r["snippet"],
                                "raw_bm25": float(r["raw_bm25"]),
                                "fts_rank": rank_idx + 1
                            }
                    except Exception as e:
                        logger.debug(f"FTS5 query fallback for '{fts_query}': {e}")

                # 2. Fetch candidate rows from `episodes_meta` matching metadata filters
                where_clauses = ["importance >= ?"]
                params: List[Any] = [min_importance]

                if eff_date_from:
                    where_clauses.append("date_str >= ?")
                    params.append(eff_date_from)
                if eff_date_to:
                    where_clauses.append("date_str <= ?")
                    params.append(eff_date_to)
                if category:
                    where_clauses.append("category = ?")
                    params.append(category)
                if thread_id:
                    where_clauses.append("thread_id = ?")
                    params.append(thread_id)

                where_sql = " AND ".join(where_clauses)
                meta_rows = conn.execute(
                    f"SELECT * FROM episodes_meta WHERE {where_sql} ORDER BY date_str DESC, importance DESC LIMIT 500",
                    params
                ).fetchall()

                if not meta_rows:
                    return []

                tag_norm = strip_accents(tag.lower().lstrip("#")) if tag else None
                tool_norm = tool_filter.lower() if tool_filter else None

                filtered_rows = []
                dense_vecs_list: List[np.ndarray] = []
                dim = self.vector_engine.dim

                for row in meta_rows:
                    tags_list = json.loads(row["tags"] or "[]")
                    tools_list = json.loads(row["tools_used"] or "[]")
                    if tag_norm and tag_norm not in [strip_accents(t.lower()) for t in tags_list]:
                        continue
                    if tool_norm and not any(tool_norm in t.lower() for t in tools_list):
                        continue

                    vec = blob_to_vector(row["embedding"], dim=dim)
                    if vec is None:
                        emb_txt = self._build_embedding_text(
                            title=row["title"] or "",
                            user_msg=row["user_msg"] or "",
                            assistant_msg=row["assistant_msg"] or "",
                            content=row["content"] or "",
                            tags=tags_list,
                            entities=json.loads(row["entities"] or "[]"),
                            tools_used=tools_list
                        )
                        vec = self.vector_engine.embed(emb_txt)

                    filtered_rows.append((row, tags_list, tools_list))
                    dense_vecs_list.append(vec)

                if not filtered_rows:
                    return []

                # 3. Vectorized NumPy Matrix Cosine Similarity: (N, D) @ (D,) -> (N,)
                candidate_matrix = np.vstack(dense_vecs_list)  # shape (N, 384)
                if dense_query_vec is not None:
                    raw_dense_sims = np.clip(candidate_matrix @ dense_query_vec, -1.0, 1.0)
                    # Rank order by dense similarity (1-based)
                    dense_order = np.argsort(-raw_dense_sims)
                    dense_ranks = {int(idx): rank + 1 for rank, idx in enumerate(dense_order)}
                else:
                    raw_dense_sims = np.zeros(len(filtered_rows), dtype=np.float32)
                    dense_ranks = {i: i + 1 for i in range(len(filtered_rows))}

                bm25_vals = [abs(c["raw_bm25"]) for c in fts_candidates.values()]
                max_bm25 = max(bm25_vals) if bm25_vals else 1.0

                scored_candidates: List[Dict[str, Any]] = []
                scored_vecs: List[np.ndarray] = []

                has_explicit_filter = bool(tag_norm or tool_norm or category or temporal["is_temporal_query"])

                for idx, (row, tags_list, tools_list) in enumerate(filtered_rows):
                    sid = row["source_id"]
                    entities_list = json.loads(row["entities"] or "[]")

                    # (a) Dense Neural Vector Cosine Similarity
                    dense_sim = float(raw_dense_sims[idx])
                    dense_score = max(0.0, dense_sim)

                    # (b) FTS5 BM25 Lexical Score
                    fts_info = fts_candidates.get(sid)
                    if fts_info and max_bm25 > 0:
                        lexical_score = min(1.0, abs(fts_info["raw_bm25"]) / max_bm25)
                        fts_rank = fts_info["fts_rank"]
                    else:
                        lexical_score = 0.0
                        fts_rank = 200

                    # (c) Sparse Stemmed + Synonym Cosine Similarity
                    try:
                        doc_sparse = json.loads(row["token_vector"] or "{}")
                    except Exception:
                        doc_sparse = {}
                    sparse_score = cosine_similarity(sparse_query_vec, doc_sparse) if sparse_query_vec else 0.0

                    # Relevance gate: skip completely unrelated candidates unless browsing a temporal/tag filter
                    if has_topical_text and not has_explicit_filter:
                        if dense_score < 0.22 and lexical_score < 0.05 and sparse_score < 0.12:
                            continue

                    # (d) Reciprocal Rank Fusion (RRF) bonus when strong in both vector and lexical
                    d_rank = dense_ranks.get(idx, 200)
                    rrf_score = (1.0 / (15.0 + d_rank)) + (1.0 / (15.0 + fts_rank))
                    rrf_norm = min(1.0, rrf_score * 8.0)

                    # (e) Temporal Recency Score (exponential decay, half-life ~15 days)
                    try:
                        ep_date = datetime.date.fromisoformat(row["date_str"])
                        days_ago = max(0, (today - ep_date).days)
                    except Exception:
                        days_ago = 30
                    recency_score = math.exp(-0.045 * days_ago)

                    # (f) Salience (`importance`) & Spaced Repetition (`access_count`)
                    imp_score = float(row["importance"] or 0.5)
                    acc_count = int(row["access_count"] or 0)
                    reinforcement_score = min(1.0, math.log1p(acc_count) / math.log1p(10))

                    if has_topical_text:
                        final_score = (
                            0.38 * dense_score
                            + 0.24 * lexical_score
                            + 0.14 * sparse_score
                            + 0.06 * rrf_norm
                            + 0.09 * imp_score
                            + 0.06 * recency_score
                            + 0.03 * reinforcement_score
                        )
                    else:
                        # Pure temporal / browse query (e.g. "¿Qué hicimos ayer?")
                        final_score = (
                            0.50 * imp_score
                            + 0.35 * recency_score
                            + 0.15 * reinforcement_score
                        )

                    snippet = fts_info["snippet"] if (fts_info and fts_info.get("snippet")) else ""
                    if not snippet:
                        u_part = (row["user_msg"] or "").strip()
                        a_part = (row["assistant_msg"] or "").strip()
                        if u_part and a_part:
                            snippet = f"Usuario: {u_part[:110]} | Asistente: {a_part[:160]}"
                        else:
                            snippet = (row["content"] or "")[:240]

                    scored_candidates.append({
                        "source_id": sid,
                        "category": row["category"],
                        "date_str": row["date_str"],
                        "timestamp": row["timestamp"] or row["date_str"],
                        "thread_id": row["thread_id"] or "",
                        "title": row["title"],
                        "user_msg": row["user_msg"] or "",
                        "assistant_msg": row["assistant_msg"] or "",
                        "snippet": snippet,
                        "content": row["content"],
                        "tools_used": tools_list,
                        "tags": tags_list,
                        "entities": entities_list,
                        "importance": round(imp_score, 2),
                        "access_count": acc_count,
                        "vector_similarity": round(dense_score, 4),
                        "lexical_score": round(lexical_score, 4),
                        "score": round(final_score, 4),
                        "rank": -round(final_score, 4),
                        "temporal_label": temporal["label"]
                    })
                    scored_vecs.append(candidate_matrix[idx])

                if not scored_candidates:
                    return []

                # Sort candidates by score descending first, deduplicating exact text signatures
                order = sorted(range(len(scored_candidates)), key=lambda i: scored_candidates[i]["score"], reverse=True)
                dedup_cands: List[Dict[str, Any]] = []
                dedup_vecs: List[np.ndarray] = []
                seen_sigs: Set[str] = set()

                for i in order:
                    item = scored_candidates[i]
                    sig_base = strip_accents((item["user_msg"] or item["title"] or item["content"][:80]).lower())
                    sig_base = re.sub(r"\s+", " ", sig_base).strip()[:65]
                    sig = f"{item['date_str']}:{sig_base}"
                    if sig in seen_sigs:
                        continue
                    seen_sigs.add(sig)
                    dedup_cands.append(item)
                    dedup_vecs.append(scored_vecs[i])
                    if len(dedup_cands) >= max(limit * 4, 20):
                        break

                # 4. Maximal Marginal Relevance (MMR) over dense embeddings for semantic diversity
                if use_mmr and dense_query_vec is not None and len(dedup_cands) > 1:
                    c_mat = np.vstack(dedup_vecs)
                    base_scores = [c["score"] for c in dedup_cands]
                    selected_indices = maximal_marginal_relevance(
                        query_vec=dense_query_vec,
                        candidate_vecs=c_mat,
                        base_scores=base_scores,
                        top_k=limit,
                        lambda_mult=0.80
                    )
                    final_results = [dedup_cands[i] for i in selected_indices]
                else:
                    final_results = dedup_cands[:limit]

                # 5. Spaced-Repetition Reinforcement
                if reinforce and final_results:
                    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
                    for item in final_results:
                        conn.execute(
                            "UPDATE episodes_meta SET access_count = access_count + 1, last_accessed = ? WHERE source_id = ?",
                            (now_iso, item["source_id"])
                        )
                        item["access_count"] += 1
                    conn.commit()

                return final_results

        except Exception as e:
            logger.warning(f"Error performing vectorial episodic search for '{query}': {e}", exc_info=True)
            return []

    def find_related_episodes(self, source_id: str, limit: int = 4) -> List[Dict[str, Any]]:
        """
        Finds the top-K most semantically related episodes to a given `source_id`
        using dense neural vector cosine similarity (`matrix @ target_vec`).
        """
        try:
            dim = self.vector_engine.dim
            with self._get_connection() as conn:
                target_row = conn.execute(
                    "SELECT * FROM episodes_meta WHERE source_id = ?",
                    (source_id,)
                ).fetchone()
                if not target_row:
                    return []

                target_vec = blob_to_vector(target_row["embedding"], dim=dim)
                if target_vec is None:
                    target_vec = self.vector_engine.embed(target_row["content"] or target_row["title"] or "")

                other_rows = conn.execute(
                    "SELECT * FROM episodes_meta WHERE source_id != ? ORDER BY date_str DESC LIMIT 400",
                    (source_id,)
                ).fetchall()
                if not other_rows:
                    return []

                vecs = []
                valid_rows = []
                for r in other_rows:
                    v = blob_to_vector(r["embedding"], dim=dim)
                    if v is not None:
                        vecs.append(v)
                        valid_rows.append(r)

                if not valid_rows:
                    return []

                mat = np.vstack(vecs)
                sims = mat @ target_vec
                top_indices = np.argsort(-sims)[:limit]

                related = []
                for idx in top_indices:
                    sim_val = float(sims[int(idx)])
                    if sim_val < 0.20:
                        continue
                    r = valid_rows[int(idx)]
                    related.append({
                        "source_id": r["source_id"],
                        "title": r["title"],
                        "category": r["category"],
                        "date_str": r["date_str"],
                        "timestamp": r["timestamp"],
                        "vector_similarity": round(sim_val, 4),
                        "tags": json.loads(r["tags"] or "[]"),
                        "tools_used": json.loads(r["tools_used"] or "[]"),
                        "user_msg": r["user_msg"],
                        "assistant_msg": r["assistant_msg"]
                    })
                return related
        except Exception as e:
            logger.warning(f"Error finding related episodes for '{source_id}': {e}")
            return []

    def get_timeline(
        self,
        date_str: Optional[str] = None,
        limit: int = 20,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Returns chronological episodes for a specific date or the most recent episodes."""
        results = []
        try:
            with self._get_connection() as conn:
                clauses = []
                params: List[Any] = []
                if date_str:
                    clauses.append("date_str = ?")
                    params.append(date_str)
                if category:
                    clauses.append("category = ?")
                    params.append(category)

                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                sql = f"""
                    SELECT * FROM episodes_meta
                    {where_sql}
                    ORDER BY timestamp DESC, date_str DESC
                    LIMIT ?
                """
                params.append(max(limit * 3, 50))
                seen_sigs: Set[str] = set()
                for row in conn.execute(sql, params).fetchall():
                    sig_base = strip_accents((row["user_msg"] or row["title"] or (row["content"] or "")[:80]).lower())
                    sig_base = re.sub(r"\s+", " ", sig_base).strip()[:65]
                    sig = f"{row['date_str']}:{sig_base}"
                    if sig in seen_sigs:
                        continue
                    seen_sigs.add(sig)
                    results.append({
                        "source_id": row["source_id"],
                        "category": row["category"],
                        "date_str": row["date_str"],
                        "timestamp": row["timestamp"],
                        "thread_id": row["thread_id"],
                        "title": row["title"],
                        "user_msg": row["user_msg"],
                        "assistant_msg": row["assistant_msg"],
                        "content": row["content"],
                        "tools_used": json.loads(row["tools_used"] or "[]"),
                        "tags": json.loads(row["tags"] or "[]"),
                        "entities": json.loads(row["entities"] or "[]"),
                        "importance": row["importance"],
                        "access_count": row["access_count"],
                        "has_dense_vector": row["embedding"] is not None
                    })
                    if len(results) >= limit:
                        break
        except Exception as e:
            logger.warning(f"Error fetching episodic timeline: {e}")
        return results

    def delete_episode(self, source_id: str) -> bool:
        """Deletes a specific episode by its `source_id` (and any synced duplicate on the same date)."""
        try:
            with self._get_connection() as conn:
                target = conn.execute(
                    "SELECT date_str, title, user_msg FROM episodes_meta WHERE source_id = ?",
                    (source_id,)
                ).fetchone()
                cur = conn.execute("DELETE FROM episodes_meta WHERE source_id = ?", (source_id,))
                conn.execute("DELETE FROM episodes_fts WHERE source_id = ?", (source_id,))
                if target:
                    dup_rows = conn.execute(
                        "SELECT source_id FROM episodes_meta WHERE date_str = ? AND title = ? AND user_msg = ?",
                        (target["date_str"], target["title"], target["user_msg"])
                    ).fetchall()
                    for dr in dup_rows:
                        conn.execute("DELETE FROM episodes_meta WHERE source_id = ?", (dr["source_id"],))
                        conn.execute("DELETE FROM episodes_fts WHERE source_id = ?", (dr["source_id"],))
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting episode '{source_id}': {e}")
            return False

    def consolidate_old_episodes(self, days_threshold: int = 30, min_importance_keep: float = 0.35) -> Dict[str, Any]:
        """
        Prunes old, low-importance, never-accessed noise episodes older than `days_threshold` days,
        preserving all milestones, lessons, error fixes, or recalled episodes.
        """
        cutoff_date = (datetime.date.today() - datetime.timedelta(days=days_threshold)).isoformat()
        removed = 0
        try:
            with self._get_connection() as conn:
                cur = conn.execute("""
                    SELECT source_id FROM episodes_meta
                    WHERE date_str < ?
                      AND importance < ?
                      AND access_count = 0
                      AND category NOT IN ('milestone', 'lesson', 'decision', 'error_fix')
                """, (cutoff_date, min_importance_keep))
                to_delete = [r["source_id"] for r in cur.fetchall()]
                for sid in to_delete:
                    conn.execute("DELETE FROM episodes_meta WHERE source_id = ?", (sid,))
                    conn.execute("DELETE FROM episodes_fts WHERE source_id = ?", (sid,))
                removed = len(to_delete)
                conn.commit()
        except Exception as e:
            logger.warning(f"Error consolidating old episodes: {e}")
        return {"removed_count": removed, "cutoff_date": cutoff_date}

    def get_stats(self) -> Dict[str, Any]:
        """Returns comprehensive statistics about the vectorial episodic memory index."""
        vec_info = self.vector_engine.get_info()
        stats: Dict[str, Any] = {
            "total_episodes": 0,
            "vectorized_episodes": 0,
            "vector_engine": vec_info,
            "categories": {},
            "top_tags": [],
            "top_tools": [],
            "date_range": {"oldest": None, "newest": None},
            "avg_importance": 0.0,
            "total_recalls": 0,
            "db_size_kb": 0.0
        }
        try:
            if self.db_path.exists():
                stats["db_size_kb"] = round(self.db_path.stat().st_size / 1024.0, 1)

            with self._get_connection() as conn:
                cur = conn.execute("""
                    SELECT
                        COUNT(*) AS cnt,
                        SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS vec_cnt,
                        MIN(date_str) AS oldest,
                        MAX(date_str) AS newest,
                        AVG(importance) AS avg_imp,
                        SUM(access_count) AS sum_acc
                    FROM episodes_meta
                """)
                row = cur.fetchone()
                if row and row["cnt"] > 0:
                    stats["total_episodes"] = row["cnt"]
                    stats["vectorized_episodes"] = int(row["vec_cnt"] or 0)
                    stats["date_range"] = {"oldest": row["oldest"], "newest": row["newest"]}
                    stats["avg_importance"] = round(float(row["avg_imp"] or 0.0), 2)
                    stats["total_recalls"] = int(row["sum_acc"] or 0)

                for r in conn.execute("SELECT category, COUNT(*) AS c FROM episodes_meta GROUP BY category ORDER BY c DESC"):
                    stats["categories"][r["category"] or "interaction"] = r["c"]

                tag_counter: Counter = Counter()
                tool_counter: Counter = Counter()
                for r in conn.execute("SELECT tags, tools_used FROM episodes_meta"):
                    for tg in json.loads(r["tags"] or "[]"):
                        tag_counter[tg] += 1
                    for tl in json.loads(r["tools_used"] or "[]"):
                        tool_counter[tl] += 1

                stats["top_tags"] = [{"tag": k, "count": v} for k, v in tag_counter.most_common(10)]
                stats["top_tools"] = [{"tool": k, "count": v} for k, v in tool_counter.most_common(10)]
        except Exception as e:
            logger.warning(f"Error computing episodic memory stats: {e}")

        return stats
