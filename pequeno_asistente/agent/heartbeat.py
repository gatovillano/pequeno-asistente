"""
Heartbeat & Automation Engine for Pequeño Asistente.
Provides OpenClaw-style proactive Heartbeats (HEARTBEAT.md) and configurable
scheduled agent automations (interval, daily time, one-shot timer) with full MCP tool support.
"""
import os
import uuid
import json
import time
import asyncio
import datetime
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager

logger = logging.getLogger("pequeno_asistente.heartbeat")


AUTOMATION_PRESETS: List[Dict[str, Any]] = [
    {
        "name": "Monitor de Salud del Sistema (RAM y Disco)",
        "description": "Vigila cada 30 min que la RAM y el disco no superen el 85%. Silencioso si todo está normal.",
        "icon": "utilities-system-monitor-symbolic",
        "schedule_type": "interval",
        "interval_minutes": 30,
        "daily_time": "09:00",
        "silent_if_ok": True,
        "desktop_notification": True,
        "show_in_chat": True,
        "speak_tts": False,
        "prompt": (
            "Verifica el estado actual de recursos del sistema (uso de memoria RAM, carga de CPU y espacio en disco). "
            "Si algún recurso supera el 85% o detectas un proceso anómalo consumiendo demasiados recursos, "
            "repórtalo de forma concisa con recomendaciones. Si todo está en rangos normales, responde únicamente HEARTBEAT_OK."
        )
    },
    {
        "name": "Briefing Matutino Inteligente",
        "description": "Todos los días a las 09:00 genera un resumen del día, pendientes en memoria y estado del entorno.",
        "icon": "weather-clear-symbolic",
        "schedule_type": "daily",
        "interval_minutes": 60,
        "daily_time": "09:00",
        "silent_if_ok": False,
        "desktop_notification": True,
        "show_in_chat": True,
        "speak_tts": False,
        "prompt": (
            "¡Buenos días! Genera mi briefing matutino conciso: "
            "1) Fecha actual, 2) Revisa en mi memoria (MEMORY.md y bitácora reciente) qué proyectos o tareas tengo en curso, "
            "y 3) Sugiere las 3 prioridades más importantes para comenzar el día con foco."
        )
    },
    {
        "name": "Guardián de Contenedores y Servicios",
        "description": "Comprueba cada 60 min si hay servicios caídos o errores recientes. Solo notifica si detecta fallos.",
        "icon": "network-server-symbolic",
        "schedule_type": "interval",
        "interval_minutes": 60,
        "daily_time": "10:00",
        "silent_if_ok": True,
        "desktop_notification": True,
        "show_in_chat": True,
        "speak_tts": False,
        "prompt": (
            "Comprueba si los servicios locales o contenedores Docker activos presentan fallos o salidas inesperadas. "
            "Si encuentras algún servicio crítico caído, avísame indicando cuál es. "
            "Si todo funciona con normalidad, responde únicamente HEARTBEAT_OK."
        )
    },
    {
        "name": "Pausa Activa y Descanso Visual (20-20-20)",
        "description": "Cada 60 min envía un recordatorio breve de ergonomía, hidratación y descanso visual.",
        "icon": "face-smile-symbolic",
        "schedule_type": "interval",
        "interval_minutes": 60,
        "daily_time": "12:00",
        "silent_if_ok": False,
        "desktop_notification": True,
        "show_in_chat": False,
        "speak_tts": True,
        "prompt": (
            "Dame un recordatorio muy breve (máximo 2 frases), amigable y motivador para hacer una pausa activa: "
            "descansar la vista mirando a lo lejos 20 segundos, relajar los hombros y tomar un poco de agua."
        )
    }
]


def format_schedule_badge(auto: Dict[str, Any]) -> str:
    """Returns a compact human-readable label for an automation schedule."""
    stype = auto.get("schedule_type", "interval")
    if stype == "daily":
        t = auto.get("daily_time", "09:00")
        return f"🕒 Diario {t}"
    elif stype == "once":
        mins = int(auto.get("interval_minutes", 15))
        return f"🎯 Una vez ({mins}m)"
    else:
        mins = int(auto.get("interval_minutes", 30))
        if mins >= 60 and mins % 60 == 0:
            hrs = mins // 60
            return f"⏱ Cada {hrs}h" if hrs > 1 else "⏱ Cada 1h"
        return f"⏱ Cada {mins} min"


def send_desktop_notification(title: str, body: str, urgency: str = "normal"):
    """Sends a native desktop notification on Linux via notify-send."""
    clean_body = body.strip()
    if len(clean_body) > 260:
        clean_body = clean_body[:257] + "..."
    try:
        subprocess.Popen(
            [
                "notify-send",
                "-a", "Pequeño Asistente",
                "-i", "alarm-symbolic",
                "-u", urgency,
                title,
                clean_body
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.debug(f"Could not send desktop notification via notify-send: {e}")


class HeartbeatManager:
    """
    Central coordinator and background scheduler for:
    1. Global Heartbeat (HEARTBEAT.md periodic proactive check)
    2. Individual Scheduled Automations (interval, daily, once)
    """
    _instance: Optional["HeartbeatManager"] = None

    @classmethod
    def get_instance(cls) -> Optional["HeartbeatManager"]:
        return cls._instance

    def __init__(
        self,
        config_manager: ConfigManager,
        memory_manager: MemoryManager,
        agent: Any,
        async_loop: Optional[asyncio.AbstractEventLoop] = None,
        on_output_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.agent = agent
        self.async_loop = async_loop
        self.on_output_callback = on_output_callback

        self.logs_file = self.memory_manager.base_dir / "heartbeat_logs.json"
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._busy_ids: set[str] = set()
        self._listeners: List[Callable[[], None]] = []

        HeartbeatManager._instance = self

    def add_listener(self, callback: Callable[[], None]):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def notify_listeners(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as e:
                logger.debug(f"Listener callback error: {e}")

    def is_task_running(self, task_id: str) -> bool:
        return task_id in self._busy_ids

    def get_active_count(self) -> int:
        hb_enabled = 1 if self.config_manager.get_heartbeat_config().get("enabled", False) else 0
        auto_enabled = sum(1 for a in self.config_manager.get_automations() if a.get("enabled", False))
        return hb_enabled + auto_enabled

    # -----------------------------------------------------------------
    # LOGGING PERSISTENCE
    # -----------------------------------------------------------------
    def get_logs(self) -> List[Dict[str, Any]]:
        if not self.logs_file.exists():
            return []
        try:
            with open(self.logs_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def append_log(self, entry: Dict[str, Any]):
        logs = self.get_logs()
        logs.insert(0, entry)
        logs = logs[:60]  # Keep last 60 pulses
        try:
            with open(self.logs_file, "w", encoding="utf-8") as f:
                json.dump(logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving heartbeat_logs.json: {e}")

    def clear_logs(self):
        try:
            if self.logs_file.exists():
                self.logs_file.unlink()
        except Exception:
            pass
        self.notify_listeners()

    # -----------------------------------------------------------------
    # CRUD FOR AUTOMATIONS
    # -----------------------------------------------------------------
    def create_automation(
        self,
        name: str,
        prompt: str,
        schedule_type: str = "interval",
        interval_minutes: int = 30,
        daily_time: str = "09:00",
        silent_if_ok: bool = True,
        desktop_notification: bool = True,
        show_in_chat: bool = True,
        speak_tts: bool = False,
        enabled: bool = True
    ) -> Dict[str, Any]:
        auto_id = f"hb-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        new_auto = {
            "id": auto_id,
            "name": name.strip() or "Automatización",
            "prompt": prompt.strip(),
            "enabled": bool(enabled),
            "schedule_type": schedule_type if schedule_type in ("interval", "daily", "once") else "interval",
            "interval_minutes": max(1, int(interval_minutes)),
            "daily_time": daily_time.strip() or "09:00",
            "silent_if_ok": bool(silent_if_ok),
            "desktop_notification": bool(desktop_notification),
            "show_in_chat": bool(show_in_chat),
            "speak_tts": bool(speak_tts),
            "created_at": now_iso,
            "last_run": None,
            "last_status": "idle",
            "last_result": "",
            "run_count": 0
        }
        self.config_manager.add_automation(new_auto)
        self.notify_listeners()
        return new_auto

    def update_automation(self, auto_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if "enabled" in updates and updates["enabled"]:
            # Reset created_at reference when re-enabling a one-shot timer
            updates["activated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        updated = self.config_manager.update_automation(auto_id, updates)
        self.notify_listeners()
        return updated

    def delete_automation(self, auto_id: str) -> bool:
        ok = self.config_manager.delete_automation(auto_id)
        if ok:
            self.notify_listeners()
        return ok

    # -----------------------------------------------------------------
    # BACKGROUND SCHEDULER LOOP
    # -----------------------------------------------------------------
    def start(self, async_loop: Optional[asyncio.AbstractEventLoop] = None):
        if async_loop:
            self.async_loop = async_loop
        if not self.async_loop or self._running:
            return
        self._running = True
        asyncio.run_coroutine_threadsafe(self._start_async(), self.async_loop)

    async def _start_async(self):
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Heartbeat & Automation scheduler started.")

    def stop(self):
        self._running = False
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()

    async def _scheduler_loop(self):
        while self._running:
            try:
                await asyncio.sleep(15)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Heartbeat scheduler tick: {e}", exc_info=True)

    def _is_within_active_hours(self, hb_cfg: Dict[str, Any]) -> bool:
        if not hb_cfg.get("active_hours_only", True):
            return True
        now_time = datetime.datetime.now().time()
        try:
            sh, sm = [int(x) for x in hb_cfg.get("active_start", "08:00").split(":")]
            eh, em = [int(x) for x in hb_cfg.get("active_end", "23:00").split(":")]
            start_t = datetime.time(sh, sm)
            end_t = datetime.time(eh, em)
            if start_t <= end_t:
                return start_t <= now_time <= end_t
            else:
                return now_time >= start_t or now_time <= end_t
        except Exception:
            return True

    def _is_interval_due(self, last_run_iso: Optional[str], interval_minutes: int, created_iso: Optional[str] = None) -> bool:
        now = datetime.datetime.now()
        ref_iso = last_run_iso or created_iso
        if not ref_iso:
            return True
        try:
            ref_dt = datetime.datetime.fromisoformat(ref_iso)
            elapsed_min = (now - ref_dt).total_seconds() / 60.0
            return elapsed_min >= max(1, interval_minutes)
        except Exception:
            return True

    def _is_daily_due(self, last_run_iso: Optional[str], daily_time_str: str) -> bool:
        now = datetime.datetime.now()
        try:
            th, tm = [int(x) for x in daily_time_str.strip().split(":")]
            target_today = now.replace(hour=th, minute=tm, second=0, microsecond=0)
        except Exception:
            return False

        if now < target_today:
            return False

        # Only trigger if it hasn't run today at or after target_today, and within a 60-minute window
        if (now - target_today).total_seconds() > 3600:
            return False

        if last_run_iso:
            try:
                last_dt = datetime.datetime.fromisoformat(last_run_iso)
                if last_dt.date() == now.date() and last_dt >= target_today:
                    return False
            except Exception:
                pass
        return True

    async def _tick(self):
        # 1. Check Global Heartbeat (HEARTBEAT.md)
        hb_cfg = self.config_manager.get_heartbeat_config()
        if hb_cfg.get("enabled", False) and "global_heartbeat" not in self._busy_ids:
            if self._is_within_active_hours(hb_cfg):
                interval = int(hb_cfg.get("interval_minutes", 30))
                if self._is_interval_due(hb_cfg.get("last_run"), interval):
                    asyncio.create_task(self.run_global_heartbeat(manual=False))

        # 2. Check Individual Automations
        automations = self.config_manager.get_automations()
        for auto in list(automations):
            if not auto.get("enabled", False):
                continue
            aid = auto.get("id", "")
            if not aid or aid in self._busy_ids:
                continue

            stype = auto.get("schedule_type", "interval")
            due = False
            if stype == "interval":
                due = self._is_interval_due(
                    auto.get("last_run"),
                    int(auto.get("interval_minutes", 30)),
                    auto.get("activated_at") or auto.get("created_at")
                )
            elif stype == "daily":
                due = self._is_daily_due(auto.get("last_run"), auto.get("daily_time", "09:00"))
            elif stype == "once":
                due = self._is_interval_due(
                    None,
                    int(auto.get("interval_minutes", 15)),
                    auto.get("activated_at") or auto.get("created_at")
                )

            if due:
                asyncio.create_task(self.run_automation(aid, manual=False))

    # -----------------------------------------------------------------
    # EXECUTION ENGINE
    # -----------------------------------------------------------------
    def trigger_global_now(self):
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(self.run_global_heartbeat(manual=True), self.async_loop)

    def trigger_automation_now(self, auto_id: str):
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(self.run_automation(auto_id, manual=True), self.async_loop)

    @staticmethod
    def _is_heartbeat_ok(text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            return True
        upper = cleaned.upper()
        if upper == "HEARTBEAT_OK" or upper == "`HEARTBEAT_OK`":
            return True
        # If very short and contains HEARTBEAT_OK without warning/alert content
        if "HEARTBEAT_OK" in upper and len(cleaned) < 48:
            return True
        return False

    async def run_global_heartbeat(self, manual: bool = False):
        if "global_heartbeat" in self._busy_ids:
            return
        self._busy_ids.add("global_heartbeat")
        self.config_manager.set_heartbeat_config({"last_status": "running"})
        self.notify_listeners()

        t0 = time.monotonic()
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        hb_cfg = self.config_manager.get_heartbeat_config()
        hb_md = self.memory_manager.get_heartbeat_md().strip()
        silent_if_ok = hb_cfg.get("silent_if_ok", True)

        sys_extra = (
            "# MODO HEARTBEAT PROACTIVO ACTIVO\n"
            f"Fecha y hora actual del sistema: {now_iso}\n"
            "Estás ejecutando un pulso periódico de verificación (Heartbeat).\n"
            "Evalúa las instrucciones de HEARTBEAT.md usando las herramientas MCP disponibles si es necesario.\n"
        )
        if silent_if_ok:
            sys_extra += (
                "REGLA DE SILENCIO INTELIGENTE: Si tras verificar todo está en orden y no hay novedades ni alertas "
                "que requieran interrumpir al usuario, responde ÚNICAMENTE con la palabra exacta: HEARTBEAT_OK"
            )

        user_prompt = (
            "Ejecuta el pulso de verificación según mi archivo `HEARTBEAT.md`:\n\n"
            f"{hb_md}"
        )

        res = await self.agent.run_headless_turn(prompt=user_prompt, system_extra=sys_extra)
        duration = round(time.monotonic() - t0, 2)
        self._busy_ids.discard("global_heartbeat")

        raw_text = res.get("text", "").strip()
        is_err = res.get("status") == "error"
        is_ok_silent = (not is_err) and silent_if_ok and self._is_heartbeat_ok(raw_text)

        final_status = "error" if is_err else ("silent_ok" if is_ok_silent else "ok")
        self.config_manager.set_heartbeat_config({
            "last_run": now_iso,
            "last_status": final_status,
            "last_result": raw_text[:600]
        })

        self.append_log({
            "timestamp": now_iso,
            "source_id": "global_heartbeat",
            "source_name": "💓 Pulso Global (HEARTBEAT.md)",
            "status": final_status,
            "summary": raw_text[:800],
            "tools_used": res.get("tools_used", []),
            "duration_sec": duration,
            "manual": manual
        })

        self.notify_listeners()

        # Dispatch notifications / chat output if not suppressed by silent_ok (or if manually triggered)
        should_notify = (not is_ok_silent) or manual
        if should_notify:
            display_text = (
                "✅ Pulso completado (`HEARTBEAT_OK` — Todo en orden, sin alertas)."
                if is_ok_silent else raw_text
            )
            if hb_cfg.get("desktop_notification", True):
                send_desktop_notification("💓 Pulso Heartbeat", display_text)

            if self.on_output_callback:
                self.on_output_callback({
                    "id": "global_heartbeat",
                    "title": "Pulso Global (HEARTBEAT.md)",
                    "prompt": "Verificación periódica de HEARTBEAT.md",
                    "text": display_text,
                    "status": final_status,
                    "show_in_chat": hb_cfg.get("show_in_chat", True) or manual,
                    "speak_tts": hb_cfg.get("speak_tts", False) and not is_ok_silent
                })

    async def run_automation(self, auto_id: str, manual: bool = False):
        if auto_id in self._busy_ids:
            return

        automations = self.config_manager.get_automations()
        auto = next((a for a in automations if a.get("id") == auto_id), None)
        if not auto:
            return

        self._busy_ids.add(auto_id)
        self.config_manager.update_automation(auto_id, {"last_status": "running"})
        self.notify_listeners()

        t0 = time.monotonic()
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        name = auto.get("name", "Automatización")
        prompt = auto.get("prompt", "")
        silent_if_ok = auto.get("silent_if_ok", True)

        sys_extra = (
            f"# EJECUCIÓN DE AUTOMATIZACIÓN PROGRAMADA: {name}\n"
            f"Fecha y hora actual del sistema: {now_iso}\n"
            "Cumple la instrucción solicitada de forma directa, utilizando las herramientas MCP conectadas si es necesario.\n"
        )
        if silent_if_ok:
            sys_extra += (
                "REGLA DE SILENCIO INTELIGENTE: Si la tarea es de monitoreo/vigilancia y tras comprobar el estado "
                "todo está normal sin alertas para el usuario, responde ÚNICAMENTE con la palabra exacta: HEARTBEAT_OK"
            )

        res = await self.agent.run_headless_turn(prompt=prompt, system_extra=sys_extra)
        duration = round(time.monotonic() - t0, 2)
        self._busy_ids.discard(auto_id)

        raw_text = res.get("text", "").strip()
        is_err = res.get("status") == "error"
        is_ok_silent = (not is_err) and silent_if_ok and self._is_heartbeat_ok(raw_text)
        final_status = "error" if is_err else ("silent_ok" if is_ok_silent else "ok")

        updates: Dict[str, Any] = {
            "last_run": now_iso,
            "last_status": final_status,
            "last_result": raw_text[:600],
            "run_count": int(auto.get("run_count", 0)) + 1
        }
        # If one-shot timer and triggered by scheduler, disable after completion
        if auto.get("schedule_type") == "once" and not manual:
            updates["enabled"] = False

        self.config_manager.update_automation(auto_id, updates)

        self.append_log({
            "timestamp": now_iso,
            "source_id": auto_id,
            "source_name": f"⚡ {name}",
            "status": final_status,
            "summary": raw_text[:800],
            "tools_used": res.get("tools_used", []),
            "duration_sec": duration,
            "manual": manual
        })

        self.notify_listeners()

        should_notify = (not is_ok_silent) or manual
        if should_notify:
            display_text = (
                f"✅ Verificación '{name}' completada (`HEARTBEAT_OK` — Sin alertas)."
                if is_ok_silent else raw_text
            )
            if auto.get("desktop_notification", True):
                send_desktop_notification(f"⚡ {name}", display_text)

            if self.on_output_callback:
                self.on_output_callback({
                    "id": auto_id,
                    "title": name,
                    "prompt": prompt,
                    "text": display_text,
                    "status": final_status,
                    "show_in_chat": auto.get("show_in_chat", True) or manual,
                    "speak_tts": auto.get("speak_tts", False) and not is_ok_silent
                })
