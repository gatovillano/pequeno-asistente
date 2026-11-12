"""
Real-time Logging Hub for Pequeño Asistente.
Captures all application, agent, media processor, MCP, and LLM transport logs into:
1. A live rotating file at `~/.config/pequeno-asistente/app.log` (for `tail -f`).
2. A thread-safe ring buffer with real-time listener callbacks (for the GTK4 Live Log Viewer and API).
"""
import os
import sys
import time
import logging
import threading
from collections import deque
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Callable, Deque, Dict, List, Any, Optional

LOG_DIR = Path.home() / ".config" / "pequeno-asistente"
LOG_FILE_PATH = LOG_DIR / "app.log"


class LiveLogBroadcaster(logging.Handler):
    """
    Custom logging handler that stores recent log records in a thread-safe ring buffer
    and notifies registered UI/API listeners in real time.
    """
    _instance: Optional["LiveLogBroadcaster"] = None
    _lock = threading.Lock()

    def __init__(self, max_records: int = 600):
        super().__init__(level=logging.INFO)
        self.records: Deque[Dict[str, Any]] = deque(maxlen=max_records)
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._listeners_lock = threading.Lock()
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )

    @classmethod
    def get_instance(cls) -> "LiveLogBroadcaster":
        with cls._lock:
            if cls._instance is None:
                cls._instance = LiveLogBroadcaster()
            return cls._instance

    def emit(self, record: logging.LogRecord):
        try:
            formatted = self.format(record)
            entry = {
                "timestamp": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
                "formatted": formatted,
            }
            self.records.append(entry)
            with self._listeners_lock:
                listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb(entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._listeners_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._listeners_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def get_recent_records(self, limit: int = 300) -> List[Dict[str, Any]]:
        items = list(self.records)
        return items[-limit:] if limit and len(items) > limit else items

    def clear(self):
        self.records.clear()


class FlushingRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that flushes to disk on every emit for instant `tail -f` visibility."""
    def emit(self, record: logging.LogRecord):
        super().emit(record)
        self.flush()


_logging_initialized = False


def setup_realtime_logging(level: int = logging.INFO) -> Path:
    """
    Configures root and application loggers to output simultaneously to:
    - stderr (console / journald)
    - `~/.config/pequeno-asistente/app.log` (flushed immediately)
    - `LiveLogBroadcaster` (in-memory ring buffer for real-time UI inspection)
    """
    global _logging_initialized
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not _logging_initialized:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        # Ensure console handler exists
        has_stream = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            for h in root_logger.handlers
        )
        if not has_stream:
            sh = logging.StreamHandler(sys.stderr)
            sh.setLevel(level)
            sh.setFormatter(fmt)
            root_logger.addHandler(sh)

        # File handler with immediate flush
        try:
            fh = FlushingRotatingFileHandler(
                LOG_FILE_PATH,
                maxBytes=5 * 1024 * 1024,
                backupCount=2,
                encoding="utf-8"
            )
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root_logger.addHandler(fh)
        except Exception as e:
            sys.stderr.write(f"Warning: could not attach file logger {LOG_FILE_PATH}: {e}\n")

        # Live broadcaster handler for UI
        broadcaster = LiveLogBroadcaster.get_instance()
        if broadcaster not in root_logger.handlers:
            root_logger.addHandler(broadcaster)

        _logging_initialized = True

    return LOG_FILE_PATH
