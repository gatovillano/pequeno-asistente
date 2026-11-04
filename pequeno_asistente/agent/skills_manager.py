"""
Skills Manager for Pequeño Asistente.
Discovers, installs, and manages procedural agent skills from:
- User configuration directory (~/.config/pequeno-asistente/skills)
- Claude Code global and local skills (~/.claude/skills, .claude/skills)
- Open Agent skills standard (~/.agents/skills, .agents/skills)
- Operating system global skills (/etc/agent-skills, /usr/share/agent-skills, /etc/pequeno-asistente/skills)
- Antigravity / Gemini skills (~/.gemini/antigravity/builtin/skills, ~/.gemini/antigravity/skills)
- Built-in package skills (pequeno_asistente/agent/skills)
- Workspace-level skills
"""
import os
import re
import shutil
import logging
import urllib.parse
import urllib.request
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from pequeno_asistente.config import ConfigManager

logger = logging.getLogger("pequeno_asistente.skills")


class SkillInfo:
    """Represents a discovered procedural skill."""
    def __init__(
        self,
        skill_id: str,
        name: str,
        description: str,
        path: Path,
        source: str,
        source_label: str,
        tags: Optional[List[str]] = None,
        is_directory: bool = False,
        is_symlink: bool = False,
        symlink_target: Optional[str] = None,
        enabled: bool = True,
        priority: int = 10
    ):
        self.id = skill_id
        self.name = name or skill_id
        self.description = description or "Sin descripción disponible."
        self.path = Path(path)
        self.source = source
        self.source_label = source_label
        self.tags = tags or []
        self.is_directory = is_directory
        self.is_symlink = is_symlink
        self.symlink_target = symlink_target
        self.enabled = enabled
        self.priority = priority
        self._cached_content: Optional[str] = None

    def read_content(self, force_reload: bool = False) -> str:
        """Reads and returns the markdown content of the skill."""
        if self._cached_content is not None and not force_reload:
            return self._cached_content
        try:
            target_file = self.path
            if self.is_directory:
                candidate = target_file / "SKILL.md"
                if not candidate.exists():
                    candidate = target_file / "skill.md"
                if not candidate.exists():
                    candidate = target_file / "README.md"
                if candidate.exists():
                    target_file = candidate
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                self._cached_content = f.read().strip()
                return self._cached_content
        except Exception as e:
            logger.error(f"Error reading skill {self.id} at {self.path}: {e}")
            return f"# Error al cargar la habilidad '{self.id}'\n{e}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "source": self.source,
            "source_label": self.source_label,
            "tags": self.tags,
            "is_directory": self.is_directory,
            "is_symlink": self.is_symlink,
            "symlink_target": self.symlink_target,
            "enabled": self.enabled,
            "priority": self.priority
        }


class SkillManager:
    """
    Central manager for discovering, loading, installing, and managing skills.
    Maintains a unified catalog across Claude Code, ~/.agents/skills, system,
    and user-installed skills.
    """
    _instance: Optional["SkillManager"] = None

    @classmethod
    def get_instance(cls, config_manager: Optional[ConfigManager] = None) -> "SkillManager":
        if cls._instance is None:
            cls._instance = cls(config_manager=config_manager)
        elif config_manager is not None and cls._instance.config_manager is None:
            cls._instance.config_manager = config_manager
        return cls._instance

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        self._skills_cache: Dict[str, SkillInfo] = {}
        self._listeners: List[Any] = []
        self._ensure_user_skills_dir()

    def _ensure_user_skills_dir(self) -> Path:
        user_dir = Path.home() / ".config" / "pequeno-asistente" / "skills"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def add_listener(self, callback: Any):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as e:
                logger.warning(f"Error in skills listener: {e}")

    def get_search_directories(self) -> List[Tuple[str, str, Path, int]]:
        """
        Returns list of (source_id, source_label, path, priority) to search.
        Higher priority takes precedence on duplicate skill IDs.
        """
        dirs: List[Tuple[str, str, Path, int]] = []
        home = Path.home()

        # 1. User configuration directory (highest priority)
        user_skills = home / ".config" / "pequeno-asistente" / "skills"
        dirs.append(("user", "Pequeño Asistente (Usuario)", user_skills, 100))

        # 2. Workspace local directories (if any in current working directory)
        cwd = Path.cwd()
        if cwd != home:
            ws_claude = cwd / ".claude" / "skills"
            ws_agents = cwd / ".agents" / "skills"
            ws_local = cwd / ".pequeno-asistente" / "skills"
            ws_skills = cwd / "skills"
            if ws_local.exists():
                dirs.append(("workspace", "Proyecto Local (.pequeno-asistente)", ws_local, 95))
            if ws_claude.exists():
                dirs.append(("workspace_claude", "Proyecto Local Claude (.claude)", ws_claude, 90))
            if ws_agents.exists():
                dirs.append(("workspace_agents", "Proyecto Local Agentes (.agents)", ws_agents, 85))
            if ws_skills.exists():
                dirs.append(("workspace", "Proyecto Local (skills/)", ws_skills, 80))

        # 3. Claude Code global directory (~/.claude/skills)
        claude_skills = home / ".claude" / "skills"
        dirs.append(("claude", "Claude Code (~/.claude/skills)", claude_skills, 70))

        # 4. Open Agent Skills standard (~/.agents/skills)
        agents_skills = home / ".agents" / "skills"
        dirs.append(("agents_global", "Estándar Agentes (~/.agents/skills)", agents_skills, 65))

        # 5. Antigravity / Gemini skills
        antigravity_builtin = home / ".gemini" / "antigravity" / "builtin" / "skills"
        antigravity_user = home / ".gemini" / "antigravity" / "skills"
        if antigravity_builtin.exists():
            dirs.append(("antigravity", "Antigravity Built-in", antigravity_builtin, 50))
        if antigravity_user.exists():
            dirs.append(("antigravity", "Antigravity Skills", antigravity_user, 55))

        # 6. System-wide global directories
        dirs.append(("system", "Sistema (/etc/agent-skills)", Path("/etc/agent-skills"), 40))
        dirs.append(("system", "Sistema (/usr/share/agent-skills)", Path("/usr/share/agent-skills"), 35))
        dirs.append(("system", "Sistema (/etc/pequeno-asistente/skills)", Path("/etc/pequeno-asistente/skills"), 30))

        # 7. Built-in package skills
        builtin_pkg = Path(os.path.dirname(__file__)) / "skills"
        dirs.append(("built-in", "Integrada (Built-in)", builtin_pkg, 20))

        # 8. Extra user-configured paths from config.json
        skills_cfg = self.config_manager.data.get("skills", {})
        extra_paths = skills_cfg.get("extra_paths", [])
        for ep in extra_paths:
            p = Path(os.path.expanduser(ep))
            dirs.append(("custom", f"Personalizada ({p.name})", p, 60))

        return dirs

    def _parse_frontmatter_or_fallback(self, content: str, default_name: str) -> Tuple[str, str, List[str]]:
        """Parses YAML frontmatter or falls back to Markdown titles."""
        name = ""
        description = ""
        tags: List[str] = []

        # 1. First, search for a human-readable H1 header in markdown
        h1_match = re.search(r'^\s*#\s+(.+)$', content, re.MULTILINE)
        if h1_match:
            h1 = h1_match.group(1).strip()
            # Clean emojis, prefixes and annotations
            h1 = re.sub(r'^[^\w\s]+', '', h1).strip()
            h1 = re.sub(r'^(Habilidad|Skill):\s*', '', h1, flags=re.IGNORECASE).strip()
            h1 = re.sub(r'\s*—.*$', '', h1).strip()
            h1 = re.sub(r'\s*-\s*a\s+Claude.*$', '', h1, flags=re.IGNORECASE).strip()
            if 2 <= len(h1) <= 60:
                name = h1

        # 2. Parse YAML Frontmatter
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if frontmatter_match:
            fm_text = frontmatter_match.group(1)
            parsed_yaml = None
            if HAS_YAML:
                try:
                    parsed_yaml = yaml.safe_load(fm_text)
                except Exception:
                    parsed_yaml = None
            
            if isinstance(parsed_yaml, dict):
                yaml_name = parsed_yaml.get("name")
                if yaml_name and not name:
                    name = str(yaml_name).strip()
                desc = parsed_yaml.get("description", "")
                if isinstance(desc, str):
                    description = desc.strip()
                elif isinstance(desc, (list, dict)):
                    description = str(desc)
                t = parsed_yaml.get("tags", [])
                if isinstance(t, list):
                    tags = [str(item) for item in t]
                elif isinstance(t, str):
                    tags = [t]
            else:
                # Regex fallback for frontmatter
                name_match = re.search(r"^name:\s*['\"]?(.*?)['\"]?\s*$", fm_text, re.MULTILINE)
                if name_match and not name:
                    name = name_match.group(1).strip()
                desc_match = re.search(r"^description:\s*(?:>|\|)?\s*\n?(.*?)(?=\n[a-zA-Z0-9_-]+:|\Z)", fm_text, re.DOTALL | re.MULTILINE)
                if desc_match:
                    description = desc_match.group(1).strip().replace("\n", " ")
                tags_match = re.search(r"^tags:\s*\[(.*?)\]", fm_text, re.MULTILINE)
                if tags_match:
                    raw_tags = tags_match.group(1).split(",")
                    tags = [t.strip().strip("'\"") for t in raw_tags if t.strip()]

        if not description:
            # Look for paragraph following H1
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if line.startswith("#"):
                    if i + 1 < len(lines) and not lines[i + 1].startswith("#") and not lines[i + 1].startswith("---"):
                        description = lines[i + 1]
                    break

        # If name is still missing or is a technical slug (e.g. 'analytical-thinking'),
        # convert it into title case (e.g. 'Analytical Thinking')
        if not name or name == default_name:
            target = default_name
            if '-' in target or '_' in target:
                words = [w.capitalize() for w in re.split(r'[-_]', target) if w]
                name = ' '.join(words)
            else:
                name = target.capitalize()
        elif '-' in name and name.islower():
            words = [w.capitalize() for w in name.split('-') if w]
            name = ' '.join(words)
        elif '_' in name and name.islower():
            words = [w.capitalize() for w in name.split('_') if w]
            name = ' '.join(words)

        if not description:
            description = f"Habilidad procedimental {name}."
        
        return name, description, tags

    def discover_skills(self, force_refresh: bool = False) -> Dict[str, SkillInfo]:
        """
        Discovers and returns all available skills mapped by skill_id.
        Handles duplicates and symlinks seamlessly.
        """
        if self._skills_cache and not force_refresh:
            return self._skills_cache

        skills_cfg = self.config_manager.data.get("skills", {})
        disabled_skills: Set[str] = set(skills_cfg.get("disabled_skills", []))
        
        discovered: Dict[str, SkillInfo] = {}
        search_dirs = self.get_search_directories()

        for source_id, source_label, sdir, priority in search_dirs:
            if not sdir.exists():
                continue

            try:
                entries = sorted(os.listdir(sdir))
            except Exception as e:
                logger.debug(f"Could not list directory {sdir}: {e}")
                continue

            for entry in entries:
                full_path = sdir / entry
                skill_id = ""
                target_md = None
                is_dir = False
                is_symlink = full_path.is_symlink()
                symlink_target = str(os.readlink(full_path)) if is_symlink else None

                if full_path.is_dir():
                    is_dir = True
                    skill_id = entry
                    # Look for SKILL.md, skill.md or README.md
                    for cand_name in ("SKILL.md", "skill.md", "README.md"):
                        cand_path = full_path / cand_name
                        if cand_path.exists():
                            target_md = cand_path
                            break
                    if not target_md:
                        # Find first .md inside
                        try:
                            md_files = [f for f in full_path.iterdir() if f.is_file() and f.suffix.lower() == ".md"]
                            if md_files:
                                target_md = md_files[0]
                        except Exception:
                            pass
                elif full_path.is_file() and full_path.suffix.lower() == ".md":
                    skill_id = entry[:-3]
                    target_md = full_path

                if not skill_id or not target_md or not target_md.exists():
                    continue

                # Normalise skill_id
                norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_id).strip("-").lower()

                # If already discovered with higher or equal priority, check if we should update or skip
                if norm_id in discovered:
                    existing = discovered[norm_id]
                    if existing.priority >= priority:
                        # Append source info if discovered in both Claude and Agents
                        if source_id == "claude" and "Claude" not in existing.source_label:
                            existing.source_label = f"{existing.source_label} / Claude"
                        elif source_id == "agents_global" and "Agentes" not in existing.source_label:
                            existing.source_label = f"{existing.source_label} / Agentes"
                        continue

                # Read frontmatter for metadata
                try:
                    with open(target_md, "r", encoding="utf-8", errors="replace") as f:
                        header_sample = f.read(4096)
                    name, desc, tags = self._parse_frontmatter_or_fallback(header_sample, default_name=skill_id)
                except Exception as e:
                    logger.debug(f"Error reading header of {target_md}: {e}")
                    name = skill_id
                    desc = f"Habilidad en {full_path.name}"
                    tags = []

                enabled = norm_id not in disabled_skills

                info = SkillInfo(
                    skill_id=norm_id,
                    name=name,
                    description=desc,
                    path=full_path,
                    source=source_id,
                    source_label=source_label,
                    tags=tags,
                    is_directory=is_dir,
                    is_symlink=is_symlink,
                    symlink_target=symlink_target,
                    enabled=enabled,
                    priority=priority
                )
                discovered[norm_id] = info

        self._skills_cache = discovered
        return self._skills_cache

    def get_skill(self, skill_id: str) -> Optional[SkillInfo]:
        """Returns SkillInfo by ID."""
        skills = self.discover_skills()
        norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_id).strip("-").lower()
        return skills.get(norm_id) or skills.get(skill_id)

    def match_relevant_skills(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.15
    ) -> List[Tuple[SkillInfo, float]]:
        """
        Calculates semantic & keyword relevance between the user query and available skills.
        Supports:
        - Explicit slash syntax: e.g. /bitwig-producer or /music gives maximum score (100.0).
        - Multi-field lexical and semantic matching on skill name, id, tags, and description.
        - Substring / morphological stem matching for Spanish/English technical terms.
        """
        if not query or not str(query).strip():
            return []

        skills = self.discover_skills()
        active = [s for s in skills.values() if s.enabled]
        if not active:
            return []

        # 1. Check for intentional slash commands: /<skill_id> or /<word>
        slash_invocations = set()
        for m in re.finditer(r'(?:^|\s)/([a-zA-Z0-9_-]+)', str(query)):
            slash_invocations.add(m.group(1).lower().strip("-"))

        # Stop words filter (Spanish and English common conversational tokens)
        stop_words = {
            "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al",
            "en", "para", "por", "con", "sin", "sobre", "que", "qué", "como", "cómo", "y",
            "o", "u", "te", "me", "se", "nos", "mi", "tu", "su", "es", "son", "fue", "era",
            "hola", "asistente", "favor", "puedes", "podrias", "podrías", "dime", "dame",
            "quiero", "haz", "hacer", "ejecutar", "the", "and", "or", "to", "in", "for", "with"
        }

        # Normalize query
        norm_query = str(query).lower()
        replacements = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ñ': 'n'}
        for a, b in replacements.items():
            norm_query = norm_query.replace(a, b)

        query_tokens = [w for w in re.split(r'[^a-zA-Z0-9_-]+', norm_query) if len(w) > 2 and w not in stop_words]

        scored: List[Tuple[SkillInfo, float]] = []

        for skill in active:
            # Case A: Explicit Slash Invocation (Instant 100.0)
            skill_id_clean = skill.id.lower().replace("_", "-")
            if any(inv == skill_id_clean or inv in skill_id_clean or skill_id_clean.startswith(inv) for inv in slash_invocations):
                scored.append((skill, 100.0 + skill.priority))
                continue

            # Case B: Semantic & Keyword relevance scoring
            score = 0.0

            # Normalized representations of skill metadata
            s_id = skill.id.lower().replace("_", "-")
            s_name = skill.name.lower()
            s_desc = skill.description.lower()
            for a, b in replacements.items():
                s_name = s_name.replace(a, b)
                s_desc = s_desc.replace(a, b)
            s_tags = [t.lower() for t in skill.tags]

            for tok in query_tokens:
                # 1. Exact token match in ID (+8.0)
                if tok == s_id or tok in s_id.split("-"):
                    score += 8.0
                elif tok in s_id:
                    score += 4.0

                # 2. Token match in Name (+5.0)
                if tok in s_name.split():
                    score += 5.0
                elif tok in s_name:
                    score += 2.5

                # 3. Token match in Tags (+4.0)
                if any(tok == t or tok in t for t in s_tags):
                    score += 4.0

                # 4. Token match in Description (+1.5)
                if tok in s_desc:
                    score += 1.5

                # 5. Morphological stem / prefix match for words >= 5 chars
                if len(tok) >= 5:
                    stem = tok[:5]
                    if stem in s_id:
                        score += 3.0
                    elif stem in s_name:
                        score += 2.0
                    elif stem in s_desc:
                        score += 1.0

            # Normalize by query length to avoid over-scoring long prompts
            if query_tokens:
                norm_score = score / (len(query_tokens) ** 0.6)
            else:
                norm_score = 0.0

            if norm_score >= threshold:
                scored.append((skill, norm_score))

        # Sort descending by score
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def toggle_skill(self, skill_id: str, enabled: bool) -> bool:
        """Enables or disables a skill in configuration."""
        skills = self.discover_skills()
        norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_id).strip("-").lower()
        if norm_id not in skills:
            return False

        skill = skills[norm_id]
        skill.enabled = enabled

        if "skills" not in self.config_manager.data:
            self.config_manager.data["skills"] = {}
        
        disabled = set(self.config_manager.data["skills"].get("disabled_skills", []))
        if enabled:
            disabled.discard(norm_id)
        else:
            disabled.add(norm_id)
            
        self.config_manager.data["skills"]["disabled_skills"] = sorted(list(disabled))
        self.config_manager.save()
        self._notify_listeners()
        return True

    def install_skill(
        self,
        source: Optional[str] = None,
        name: Optional[str] = None,
        content: Optional[str] = None,
        target_scope: str = "user",
        as_symlink: bool = False
    ) -> Dict[str, Any]:
        """
        Installs a skill from:
        - Local path or directory
        - URL (raw markdown or GitHub)
        - Git repository
        - Direct text/markdown content
        - Skills CLI package (skills.sh / npm package)
        
        target_scope:
          'user': ~/.config/pequeno-asistente/skills
          'claude': ~/.claude/skills
          'agents': ~/.agents/skills
        """
        home = Path.home()
        if target_scope == "claude":
            dest_dir = home / ".claude" / "skills"
        elif target_scope == "agents":
            dest_dir = home / ".agents" / "skills"
        else:
            dest_dir = home / ".config" / "pequeno-asistente" / "skills"

        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Case 1: Direct Content Installation
            if content and content.strip():
                skill_name = name or "custom-skill"
                norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-").lower()
                target_skill_dir = dest_dir / norm_id
                target_skill_dir.mkdir(parents=True, exist_ok=True)
                skill_file = target_skill_dir / "SKILL.md"

                # Ensure frontmatter has name
                if not content.startswith("---"):
                    formatted_content = f"---\nname: {norm_id}\ndescription: {skill_name}\n---\n\n{content.strip()}\n"
                else:
                    formatted_content = content.strip() + "\n"

                with open(skill_file, "w", encoding="utf-8") as f:
                    f.write(formatted_content)

                self.discover_skills(force_refresh=True)
                self._notify_listeners()
                return {
                    "success": True,
                    "skill_id": norm_id,
                    "path": str(target_skill_dir),
                    "message": f"Habilidad '{norm_id}' instalada exitosamente en {target_skill_dir}."
                }

            if not source:
                return {"success": False, "error": "Se requiere 'source' (URL, ruta local o repo) o 'content'."}

            source_clean = source.strip()

            # Case 2: Local File or Directory
            if os.path.exists(os.path.expanduser(source_clean)):
                src_path = Path(os.path.expanduser(source_clean)).resolve()
                skill_name = name or src_path.stem
                norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-").lower()
                dest_path = dest_dir / (norm_id if src_path.is_dir() else f"{norm_id}.md")

                if as_symlink:
                    if dest_path.exists() or dest_path.is_symlink():
                        if dest_path.is_dir() and not dest_path.is_symlink():
                            shutil.rmtree(dest_path)
                        else:
                            dest_path.unlink()
                    os.symlink(src_path, dest_path)
                    msg = f"Enlace simbólico creado a {src_path} en {dest_path}"
                else:
                    if src_path.is_dir():
                        if dest_path.exists():
                            shutil.rmtree(dest_path)
                        shutil.copytree(src_path, dest_path)
                    else:
                        target_dir = dest_dir / norm_id
                        target_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, target_dir / "SKILL.md")
                        dest_path = target_dir
                    msg = f"Habilidad copiada exitosamente a {dest_path}"

                self.discover_skills(force_refresh=True)
                self._notify_listeners()
                return {
                    "success": True,
                    "skill_id": norm_id,
                    "path": str(dest_path),
                    "message": msg
                }

            # Case 3: Git Repository or GitHub URL
            if source_clean.startswith(("http://", "https://", "git@")):
                parsed = urllib.parse.urlparse(source_clean)

                # Check if it's a direct raw file (e.g. raw.githubusercontent.com or ends in .md)
                if source_clean.endswith(".md") or "raw.githubusercontent.com" in source_clean:
                    req = urllib.request.Request(source_clean, headers={"User-Agent": "PequenoAsistente/1.0"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        downloaded = resp.read().decode("utf-8", errors="replace")

                    skill_name = name or Path(parsed.path).stem
                    norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-").lower()
                    target_dir = dest_dir / norm_id
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_file = target_dir / "SKILL.md"
                    with open(target_file, "w", encoding="utf-8") as f:
                        f.write(downloaded)

                    self.discover_skills(force_refresh=True)
                    self._notify_listeners()
                    return {
                        "success": True,
                        "skill_id": norm_id,
                        "path": str(target_dir),
                        "message": f"Habilidad descargada e instalada en {target_dir}."
                    }

                # Git Clone for repositories
                skill_name = name or Path(parsed.path).stem.replace(".git", "")
                norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-").lower()
                target_clone = dest_dir / norm_id

                if target_clone.exists():
                    shutil.rmtree(target_clone)

                res = subprocess.run(
                    ["git", "clone", "--depth", "1", source_clean, str(target_clone)],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if res.returncode != 0:
                    return {
                        "success": False,
                        "error": f"Fallo al clonar repositorio git: {res.stderr or res.stdout}"
                    }

                # Remove .git folder to keep directory clean
                git_internal = target_clone / ".git"
                if git_internal.exists():
                    shutil.rmtree(git_internal, ignore_errors=True)

                self.discover_skills(force_refresh=True)
                self._notify_listeners()
                return {
                    "success": True,
                    "skill_id": norm_id,
                    "path": str(target_clone),
                    "message": f"Repositorio de habilidad clonado e instalado en {target_clone}."
                }

            # Case 4: Package identifier via npx skills / skills.sh
            # e.g., 'anthropics/skills' or 'skills add <name>'
            if "/" in source_clean and not source_clean.startswith("/"):
                # Try cloning from github.com/owner/repo
                gh_url = f"https://github.com/{source_clean}.git"
                return self.install_skill(source=gh_url, name=name, target_scope=target_scope)

            return {
                "success": False,
                "error": f"Origen de habilidad no reconocido: {source_clean}"
            }

        except Exception as e:
            logger.error(f"Error installing skill: {e}")
            return {"success": False, "error": str(e)}

    def uninstall_skill(self, skill_id: str) -> Dict[str, Any]:
        """
        Uninstalls a skill from user or claude directories.
        Built-in and system skills cannot be deleted from disk; they get disabled instead.
        """
        skills = self.discover_skills()
        norm_id = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_id).strip("-").lower()
        if norm_id not in skills:
            return {"success": False, "error": f"Habilidad '{skill_id}' no encontrada."}

        skill = skills[norm_id]

        # Prevent deleting system or built-in skills from disk
        if skill.source in ("built-in", "system"):
            self.toggle_skill(norm_id, False)
            return {
                "success": True,
                "message": f"La habilidad '{skill.name}' es del sistema ({skill.source_label}) y no se puede borrar del disco. Ha sido deshabilitada en la configuración."
            }

        try:
            target_path = skill.path
            if target_path.is_symlink() or target_path.is_file():
                target_path.unlink()
            elif target_path.is_dir():
                shutil.rmtree(target_path)

            self.discover_skills(force_refresh=True)
            self._notify_listeners()
            return {
                "success": True,
                "message": f"Habilidad '{skill.name}' eliminada exitosamente del disco ({target_path})."
            }
        except Exception as e:
            logger.error(f"Error removing skill {skill_id}: {e}")
            return {"success": False, "error": str(e)}

    def format_skills_for_prompt(
        self,
        query: Optional[str] = None,
        max_full_skills: int = 5,
        semantic_top_k: int = 3
    ) -> str:
        """
        Builds a comprehensive <skills> block for the LLM system prompt:
        - Full catalog summary (Claude Code, System, Agents, User, Built-in)
        - Detailed procedural instructions dynamically tailored to the user query:
          * Automatically loads top semantically relevant skills
          * Prioritizes explicit slash-invoked skills (e.g. /skill-name)
          * Keeps critical security & installer skills always available
        """
        skills = self.discover_skills()
        if not skills:
            return ""

        active_skills = [s for s in skills.values() if s.enabled]
        if not active_skills:
            return ""

        # Key procedural skills that must always be injected in full
        always_full_ids = {"mcp-installer", "mcp-installer-skill", "security-credentials", "skills-installer", "skills-installer-skill"}

        catalog_lines = []
        full_sections = []
        injected_ids = set()

        # Sort alphabetically by human-readable Name for the catalog index
        sorted_skills = sorted(active_skills, key=lambda s: s.name.lower())
        for s in sorted_skills:
            catalog_lines.append(f"- **{s.name}** (`{s.id}`): {s.description} [Origen: {s.source_label}]")

        # 1. Semantic & Intent matching based on query
        explicit_slash_mention = False
        if query and str(query).strip():
            relevant_matches = self.match_relevant_skills(str(query), top_k=semantic_top_k, threshold=0.20)
            for skill, score in relevant_matches:
                if skill.id not in injected_ids:
                    injected_ids.add(skill.id)
                    content = skill.read_content()
                    if content:
                        is_slash = score >= 100.0
                        if is_slash:
                            explicit_slash_mention = True
                            header = f"### [HABILIDAD EXPRESAMENTE SOLICITADA POR EL USUARIO: /{skill.id}] {skill.name} [Origen: {skill.source_label}]\n"
                        else:
                            header = f"### [HABILIDAD CARGADA DINÁMICAMENTE POR PERTINENCIA SEMÁNTICA (Score: {score:.1f})] {skill.name} (`{skill.id}`) [Origen: {skill.source_label}]\n"
                        full_sections.append(f"{header}{content}")

        # 2. Key system procedural skills that must always be injected in full
        for s in sorted_skills:
            if s.id in always_full_ids or any(k in s.id for k in ("mcp_installer", "security", "skills_installer")):
                if s.id not in injected_ids:
                    injected_ids.add(s.id)
                    content = s.read_content()
                    if content:
                        full_sections.append(
                            f"### Habilidad del Sistema: {s.name} (`{s.id}`) [Origen: {s.source_label}]\n"
                            f"{content}"
                        )

        # 3. Fallback: if no query or few skills injected, fill up to max_full_skills with highest priority skills
        if len(injected_ids) < max_full_skills:
            priority_sorted = sorted(active_skills, key=lambda s: (-s.priority, s.name.lower()))
            for s in priority_sorted:
                if len(full_sections) >= max_full_skills:
                    break
                if s.id not in injected_ids:
                    injected_ids.add(s.id)
                    content = s.read_content()
                    if content:
                        full_sections.append(
                            f"### Habilidad: {s.name} (`{s.id}`) [Origen: {s.source_label}]\n"
                            f"{content}"
                        )

        index_text = "\n".join(catalog_lines)
        full_text = "\n\n---\n\n".join(full_sections)

        slash_discipline = ""
        if explicit_slash_mention:
            slash_discipline = "\n0. INVOCACIÓN EXPRESA: El usuario ha invocado explícitamente una habilidad mediante la sintaxis '/habilidad'. Aplica sus instrucciones de inmediato sin omitir ningún paso."

        prompt = (
            "<skills>\n"
            "# CATÁLOGO Y PROCEDIMIENTOS DE HABILIDADES (SKILLS)\n"
            "Dispones de habilidades procedimentales provenientes de tu entorno local, Claude Code (~/.claude/skills), "
            "el estándar global de agentes (~/.agents/skills) y el sistema operativo.\n\n"
            "## Catálogo de Habilidades Disponibles (ordenadas por Nombre):\n"
            f"{index_text}\n\n"
            "## Instrucciones Procedimentales Detalladas (Cargadas Dinámicamente por Pertinencia):\n"
            f"{full_text}\n\n"
            "DISCIPLINA ESTRICTA DE HABILIDADES:"
            f"{slash_discipline}\n"
            "1. PRECEDENCIA: Las habilidades registradas representan el estándar oficial y la forma comprobada de operar. Tienen precedencia absoluta sobre respuestas genéricas o improvisadas.\n"
            "2. CONSULTA Y VERIFICACIÓN OBLIGATORIA: Si la solicitud del usuario involucra una temática cubierta por el catálogo (ej. Bitwig/DAW, análisis, testing, MCP, etc.), DEBES aplicar su procedimiento rigurosamente.\n"
            "3. LECTURA BAJO DEMANDA: Si el procedimiento de una habilidad coincidente no está totalmente expandido arriba, invoca INMEDIATAMENTE `skills__get(skill_id=\"<id>\")` ANTES de realizar cualquier acción o responder.\n"
            "4. RESPUESTA AL USUARIO: Cuando el usuario te pregunte qué habilidades tienes o te pida listarlas, responde SIEMPRE listándolas por su NOMBRE amigable (ej: 1. Analytical Thinking, 2. Design Thinking, 3. Music Composer, etc.), acompañado de una frase breve con su utilidad.\n"
            "5. INSTALACIÓN: Si el usuario te pide instalar una nueva habilidad, puedes usar `skills__install` o seguir el procedimiento de `skills-installer`.\n"
            "</skills>"
        )
        return prompt
