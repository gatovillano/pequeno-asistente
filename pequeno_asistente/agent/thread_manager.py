"""
Thread / Conversation Manager for Pequeño Asistente.
Handles persistent multi-thread conversation histories in ~/.config/pequeno-asistente/threads/
"""
import os
import re
import json
import uuid
import datetime
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger("pequeno_asistente.threads")


def format_relative_timestamp(iso_str: str) -> str:
    """Formats an ISO timestamp to a human-friendly relative label in Spanish."""
    if not iso_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        now = datetime.datetime.now()
        diff = now - dt
        if dt.date() == now.date():
            return f"Hoy {dt.strftime('%H:%M')}"
        elif dt.date() == (now - datetime.timedelta(days=1)).date():
            return f"Ayer {dt.strftime('%H:%M')}"
        elif diff.days < 7:
            days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
            return f"{days[dt.weekday()]} {dt.strftime('%H:%M')}"
        else:
            return dt.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str[:10]


def clean_thread_title(text: str) -> str:
    """Derives a concise, clean title for a conversation from the user's first prompt."""
    cleaned = re.sub(r"[\r\n\t]+", " ", text).strip()
    # Strip common markdown headers or prefixes
    cleaned = re.sub(r"^[#>*`\-\s]+", "", cleaned)
    if not cleaned:
        return "Nueva conversación"
    if len(cleaned) > 36:
        return cleaned[:36].rstrip() + "…"
    return cleaned


class ThreadManager:
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.threads_dir = Path(base_dir) / "threads"
        else:
            self.threads_dir = Path.home() / ".config" / "pequeno-asistente" / "threads"

        self.threads_dir.mkdir(parents=True, exist_ok=True)
        self.active_thread_id: Optional[str] = None

    def list_threads(self) -> List[Dict[str, Any]]:
        """Returns sorted list of all saved conversation threads (most recent first)."""
        threads = []
        try:
            for fpath in self.threads_dir.glob("*.json"):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    msg_count = len(data.get("messages", []))
                    threads.append({
                        "id": data.get("id", fpath.stem),
                        "title": data.get("title", "Conversación"),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "message_count": msg_count,
                        "file_path": str(fpath)
                    })
                except Exception as e:
                    logger.warning(f"Error reading thread file {fpath}: {e}")
        except Exception as e:
            logger.error(f"Error listing threads from {self.threads_dir}: {e}")

        # Sort by updated_at descending, fallback to created_at
        threads.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
        return threads

    def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Loads a thread by ID."""
        fpath = self.threads_dir / f"{thread_id}.json"
        if not fpath.exists():
            return None
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading thread {thread_id}: {e}")
            return None

    def create_thread(self, title: str = "Nueva conversación") -> Dict[str, Any]:
        """Creates a new empty thread and sets it as active."""
        now = datetime.datetime.now()
        thread_id = f"thread_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        data = {
            "id": thread_id,
            "title": title,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "messages": []
        }
        self.active_thread_id = thread_id
        self._write_thread(thread_id, data)
        return data

    def save_thread(
        self,
        thread_id: str,
        messages: List[Dict[str, Any]],
        title: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Saves messages to a thread, optionally auto-deriving title if default."""
        data = self.get_thread(thread_id)
        now = datetime.datetime.now().isoformat()
        if not data:
            data = {
                "id": thread_id,
                "title": title or "Nueva conversación",
                "created_at": now,
                "updated_at": now,
                "messages": messages
            }
        else:
            data["messages"] = messages
            data["updated_at"] = now
            if title:
                data["title"] = title
            elif data.get("title") in ("Nueva conversación", "Nuevo hilo", ""):
                # Find first user message with content
                for m in messages:
                    if m.get("role") == "user" and m.get("content"):
                        data["title"] = clean_thread_title(m["content"])
                        break

        self._write_thread(thread_id, data)
        return data

    def update_title(self, thread_id: str, new_title: str) -> bool:
        """Explicitly renames a thread."""
        data = self.get_thread(thread_id)
        if not data:
            return False
        data["title"] = clean_thread_title(new_title)
        data["updated_at"] = datetime.datetime.now().isoformat()
        self._write_thread(thread_id, data)
        return True

    def delete_thread(self, thread_id: str) -> bool:
        """Deletes a thread file."""
        fpath = self.threads_dir / f"{thread_id}.json"
        if fpath.exists():
            try:
                fpath.unlink()
                if self.active_thread_id == thread_id:
                    self.active_thread_id = None
                return True
            except Exception as e:
                logger.error(f"Error deleting thread {thread_id}: {e}")
        return False

    def get_or_create_active_thread(self) -> Dict[str, Any]:
        """Returns currently active thread, or most recent, or creates a new one."""
        if self.active_thread_id:
            th = self.get_thread(self.active_thread_id)
            if th:
                return th

        existing = self.list_threads()
        if existing:
            first_id = existing[0]["id"]
            th = self.get_thread(first_id)
            if th:
                self.active_thread_id = first_id
                return th

        return self.create_thread()

    def _write_thread(self, thread_id: str, data: Dict[str, Any]):
        fpath = self.threads_dir / f"{thread_id}.json"
        temp_path = self.threads_dir / f"{thread_id}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(fpath)
        except Exception as e:
            logger.error(f"Error writing thread {thread_id}: {e}")
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
