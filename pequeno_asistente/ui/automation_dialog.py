"""
Automation & Heartbeat Management Dialogs for Pequeño Asistente.
Built with GTK4 and Libadwaita.
Allows configuring OpenClaw-style Heartbeats, editing HEARTBEAT.md,
and managing scheduled automations (intervals, daily times, one-shot timers).
"""
import html
import datetime
from typing import Dict, Any, Optional, Callable, List

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager, DEFAULT_HEARTBEAT_MD
from pequeno_asistente.agent.heartbeat import HeartbeatManager, AUTOMATION_PRESETS, format_schedule_badge


class AutomationEditorDialog(Adw.Window):
    """
    Modal dialog to create a new automation or edit an existing one.
    Includes presets (RAM monitor, daily briefing, service guardian, active break).
    """
    def __init__(
        self,
        parent: Gtk.Window,
        hb_manager: HeartbeatManager,
        automation_data: Optional[Dict[str, Any]] = None,
        on_saved: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(520, 560)
        self.hb_manager = hb_manager
        self.automation_data = automation_data or {}
        self.on_saved = on_saved
        self.is_editing = bool(automation_data and "id" in automation_data)

        self.set_title("Editar Automatización" if self.is_editing else "Nueva Automatización")
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # Header Bar
        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancelar")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Guardar" if self.is_editing else "Crear")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(save_btn)
        main_box.append(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(14)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        scrolled.set_child(content)

        # 1. Preset Selector (if creating)
        if not self.is_editing:
            preset_grp = Adw.PreferencesGroup(title="Plantillas Rápidas (Presets)")
            preset_grp.set_description("Selecciona una plantilla predefinida o crea una personalizada.")

            preset_labels = ["Personalizada (Desde cero)"] + [p["name"] for p in AUTOMATION_PRESETS]
            self.preset_combo = Adw.ComboRow(title="Plantilla", model=Gtk.StringList.new(preset_labels))
            self.preset_combo.connect("notify::selected", self._on_preset_selected)
            preset_grp.add(self.preset_combo)
            content.append(preset_grp)

        # 2. General info group
        info_grp = Adw.PreferencesGroup(title="Información y Tarea")

        self.name_entry = Adw.EntryRow(title="Nombre de la Automatización")
        self.name_entry.set_text(self.automation_data.get("name", "Mi Automatización"))
        info_grp.add(self.name_entry)

        # Prompt text view
        prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        prompt_box.set_margin_top(8)
        prompt_box.set_margin_bottom(8)
        prompt_lbl = Gtk.Label(label="<b>Instrucción para el Agente (Prompt)</b>", use_markup=True, xalign=0)
        prompt_box.append(prompt_lbl)

        prompt_desc = Gtk.Label(
            label="Describe con claridad la tarea. El agente usará sus herramientas MCP disponibles para cumplirla.",
            xalign=0,
            wrap=True
        )
        prompt_desc.add_css_class("dim-label")
        prompt_box.append(prompt_desc)

        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_min_content_height(100)
        prompt_scroll.set_max_content_height(180)
        prompt_scroll.add_css_class("chat-input")

        self.prompt_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.prompt_textview.get_buffer().set_text(self.automation_data.get("prompt", ""))
        prompt_scroll.set_child(self.prompt_textview)
        prompt_box.append(prompt_scroll)

        prompt_row = Adw.ActionRow()
        prompt_row.set_child(prompt_box)
        info_grp.add(prompt_row)
        content.append(info_grp)

        # 3. Schedule Group
        sched_grp = Adw.PreferencesGroup(title="Frecuencia y Programación")

        schedule_types = [
            ("interval", "Intervalo recurrente (cada X minutos)"),
            ("daily", "Hora fija diaria (todos los días a las HH:MM)"),
            ("once", "Temporizador único (ejecutar en X minutos)")
        ]
        self.sched_type_keys = [s[0] for s in schedule_types]
        sched_labels = [s[1] for s in schedule_types]

        curr_type = self.automation_data.get("schedule_type", "interval")
        sel_idx = self.sched_type_keys.index(curr_type) if curr_type in self.sched_type_keys else 0

        self.sched_type_combo = Adw.ComboRow(
            title="Tipo de Programación",
            model=Gtk.StringList.new(sched_labels)
        )
        self.sched_type_combo.set_selected(sel_idx)
        self.sched_type_combo.connect("notify::selected", self._on_sched_type_changed)
        sched_grp.add(self.sched_type_combo)

        # Interval Spin Row
        self.interval_row = Adw.ActionRow(
            title="Minutos de Intervalo",
            subtitle="Frecuencia con la que se repite la tarea (ej: 15, 30, 60, 120)"
        )
        self.interval_spin = Gtk.SpinButton.new_with_range(1, 1440, 5)
        self.interval_spin.set_value(float(self.automation_data.get("interval_minutes", 30)))
        self.interval_row.add_suffix(self.interval_spin)
        sched_grp.add(self.interval_row)

        # Daily Time Entry Row
        self.daily_time_entry = Adw.EntryRow(title="Hora de Ejecución (HH:MM)")
        self.daily_time_entry.set_text(self.automation_data.get("daily_time", "09:00"))
        sched_grp.add(self.daily_time_entry)

        content.append(sched_grp)

        # 4. Behavior & Notifications Group
        behavior_grp = Adw.PreferencesGroup(title="Comportamiento y Notificaciones")

        self.silent_row = Adw.SwitchRow(
            title="Silencio Inteligente",
            subtitle="Si la tarea no detecta alertas ni novedades (HEARTBEAT_OK), no interrumpe al usuario"
        )
        self.silent_row.set_active(self.automation_data.get("silent_if_ok", True))
        behavior_grp.add(self.silent_row)

        self.notif_row = Adw.SwitchRow(
            title="Notificación Gráfica de Escritorio",
            subtitle="Envía una notificación nativa al escritorio cuando se completan o alertan tareas"
        )
        self.notif_row.set_active(self.automation_data.get("desktop_notification", True))
        behavior_grp.add(self.notif_row)

        self.chat_row = Adw.SwitchRow(
            title="Mostrar Reporte en el Chat",
            subtitle="Publica una tarjeta informativa en el hilo de chat del asistente"
        )
        self.chat_row.set_active(self.automation_data.get("show_in_chat", True))
        behavior_grp.add(self.chat_row)

        self.tts_row = Adw.SwitchRow(
            title="Pronunciar con Voz (TTS)",
            subtitle="Sintetiza la alerta o reporte por los altavoces con Kokoro / ElevenLabs"
        )
        self.tts_row.set_active(self.automation_data.get("speak_tts", False))
        behavior_grp.add(self.tts_row)

        self.enabled_row = Adw.SwitchRow(
            title="Automatización Habilitada",
            subtitle="Mantiene activa la programación en segundo plano"
        )
        self.enabled_row.set_active(self.automation_data.get("enabled", True))
        behavior_grp.add(self.enabled_row)

        content.append(behavior_grp)

        # Update initial visibility of interval vs daily rows
        self._on_sched_type_changed(self.sched_type_combo, None)

    def _on_sched_type_changed(self, combo, _param):
        idx = combo.get_selected()
        if 0 <= idx < len(self.sched_type_keys):
            stype = self.sched_type_keys[idx]
            if stype == "daily":
                self.interval_row.set_visible(False)
                self.daily_time_entry.set_visible(True)
            elif stype == "once":
                self.interval_row.set_visible(True)
                self.interval_row.set_title("Minutos hasta la ejecución")
                self.interval_row.set_subtitle("Se ejecutará una única vez tras transcurrir este tiempo")
                self.daily_time_entry.set_visible(False)
            else:
                self.interval_row.set_visible(True)
                self.interval_row.set_title("Minutos de Intervalo")
                self.interval_row.set_subtitle("Frecuencia con la que se repite la tarea")
                self.daily_time_entry.set_visible(False)

    def _on_preset_selected(self, combo, _param):
        idx = combo.get_selected()
        if idx == 0:
            return
        preset_idx = idx - 1
        if 0 <= preset_idx < len(AUTOMATION_PRESETS):
            p = AUTOMATION_PRESETS[preset_idx]
            self.name_entry.set_text(p["name"])
            self.prompt_textview.get_buffer().set_text(p["prompt"])
            stype = p.get("schedule_type", "interval")
            if stype in self.sched_type_keys:
                self.sched_type_combo.set_selected(self.sched_type_keys.index(stype))
            self.interval_spin.set_value(float(p.get("interval_minutes", 30)))
            self.daily_time_entry.set_text(p.get("daily_time", "09:00"))
            self.silent_row.set_active(p.get("silent_if_ok", True))
            self.notif_row.set_active(p.get("desktop_notification", True))
            self.chat_row.set_active(p.get("show_in_chat", True))
            self.tts_row.set_active(p.get("speak_tts", False))

    def _on_save_clicked(self, _btn):
        name = self.name_entry.get_text().strip()
        buf = self.prompt_textview.get_buffer()
        start, end = buf.get_bounds()
        prompt = buf.get_text(start, end, True).strip()

        if not name:
            self.name_entry.grab_focus()
            return
        if not prompt:
            self.prompt_textview.grab_focus()
            return

        idx = self.sched_type_combo.get_selected()
        stype = self.sched_type_keys[idx] if 0 <= idx < len(self.sched_type_keys) else "interval"
        interval = int(self.interval_spin.get_value())
        daily_time = self.daily_time_entry.get_text().strip() or "09:00"

        payload = {
            "name": name,
            "prompt": prompt,
            "schedule_type": stype,
            "interval_minutes": interval,
            "daily_time": daily_time,
            "silent_if_ok": self.silent_row.get_active(),
            "desktop_notification": self.notif_row.get_active(),
            "show_in_chat": self.chat_row.get_active(),
            "speak_tts": self.tts_row.get_active(),
            "enabled": self.enabled_row.get_active()
        }

        if self.is_editing:
            aid = self.automation_data["id"]
            updated = self.hb_manager.update_automation(aid, payload)
            if self.on_saved and updated:
                self.on_saved(updated)
        else:
            created = self.hb_manager.create_automation(**payload)
            if self.on_saved:
                self.on_saved(created)

        self.close()


class AutomationManagerDialog(Adw.PreferencesWindow):
    """
    Main visual dashboard to configure Heartbeats, edit HEARTBEAT.md,
    manage automations, view run logs, and manually trigger pulses.
    """
    def __init__(
        self,
        config_manager: ConfigManager,
        memory_manager: MemoryManager,
        hb_manager: HeartbeatManager
    ):
        super().__init__()
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.hb_manager = hb_manager

        self.set_title("Heartbeats y Automatizaciones")
        self.set_default_size(720, 640)
        self.set_modal(True)
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")

        self._auto_rows: List[Any] = []
        self._logs_rows: List[Any] = []

        self._build_heartbeat_page()
        self._build_automations_page()
        self._build_logs_page()

        # Listen to heartbeat background events to refresh UI live
        self._listener_cb = lambda: GLib.idle_add(self._refresh_all_ui)
        self.hb_manager.add_listener(self._listener_cb)
        self.connect("close-request", self._on_close)

    def _on_close(self, _w):
        self.hb_manager.remove_listener(self._listener_cb)
        return False

    def _refresh_all_ui(self):
        self._refresh_hb_status_ui()
        self._refresh_automations_list()
        self._refresh_logs_ui()
        return False

    # -------------------------------------------------------------
    # TAB 1: PULSO PROACTIVO GLOBAL (HEARTBEAT)
    # -------------------------------------------------------------
    def _build_heartbeat_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Pulso Heartbeat")
        page.set_icon_name("alarm-symbolic")

        hb_cfg = self.config_manager.get_heartbeat_config()

        # Group 1: Master Controls & Triggers
        ctrl_grp = Adw.PreferencesGroup()
        ctrl_grp.set_title("Pulso Proactivo del Agente")
        ctrl_grp.set_description(
            "El agente despierta periódicamente en segundo plano y evalúa la lista de verificación "
            "de HEARTBEAT.md utilizando sus herramientas MCP para mantener tu sistema y tareas al día."
        )

        self.hb_enable_row = Adw.SwitchRow(
            title="Habilitar Pulso Periódico (Heartbeat)",
            subtitle="Despierta automáticamente al agente según el intervalo configurado"
        )
        self.hb_enable_row.set_active(hb_cfg.get("enabled", False))
        def _on_enable_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"enabled": val})
            self.hb_manager.notify_listeners()
        self.hb_enable_row.connect("notify::active", _on_enable_toggled)
        ctrl_grp.add(self.hb_enable_row)

        # Interval Options
        intervals = [
            (10, "Cada 10 minutos"),
            (15, "Cada 15 minutos"),
            (30, "Cada 30 minutos (Recomendado)"),
            (60, "Cada 1 hora"),
            (120, "Cada 2 horas")
        ]
        self.interval_values = [i[0] for i in intervals]
        interval_labels = [i[1] for i in intervals]
        curr_interval = int(hb_cfg.get("interval_minutes", 30))
        sel_idx = self.interval_values.index(curr_interval) if curr_interval in self.interval_values else 2

        self.interval_combo = Adw.ComboRow(
            title="Intervalo entre Pulsos",
            model=Gtk.StringList.new(interval_labels)
        )
        self.interval_combo.set_selected(sel_idx)
        def _on_interval_changed(combo, _param):
            idx = combo.get_selected()
            if 0 <= idx < len(self.interval_values):
                val = self.interval_values[idx]
                self.config_manager.set_heartbeat_config({"interval_minutes": val})
                self.hb_manager.notify_listeners()
        self.interval_combo.connect("notify::selected", _on_interval_changed)
        ctrl_grp.add(self.interval_combo)

        # Active Hours Filter
        self.active_hours_row = Adw.SwitchRow(
            title="Solo en Horario de Actividad",
            subtitle="Pausa los pulsos durante la noche para evitar interrupciones o consumo innecesario"
        )
        self.active_hours_row.set_active(hb_cfg.get("active_hours_only", True))
        def _on_active_hours_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"active_hours_only": val})
        self.active_hours_row.connect("notify::active", _on_active_hours_toggled)
        ctrl_grp.add(self.active_hours_row)

        # Hours Range Entries Box
        range_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        range_box.set_margin_top(4)
        range_box.set_margin_bottom(4)

        start_lbl = Gtk.Label(label="Inicio:")
        range_box.append(start_lbl)
        self.start_hour_entry = Gtk.Entry()
        self.start_hour_entry.set_text(hb_cfg.get("active_start", "08:00"))
        self.start_hour_entry.set_max_width_chars(6)
        range_box.append(self.start_hour_entry)

        end_lbl = Gtk.Label(label="Fin:")
        range_box.append(end_lbl)
        self.end_hour_entry = Gtk.Entry()
        self.end_hour_entry.set_text(hb_cfg.get("active_end", "23:00"))
        self.end_hour_entry.set_max_width_chars(6)
        range_box.append(self.end_hour_entry)

        save_hours_btn = Gtk.Button(label="Aplicar Horas")
        save_hours_btn.add_css_class("flat")
        def _on_save_hours(_b):
            sh = self.start_hour_entry.get_text().strip()
            eh = self.end_hour_entry.get_text().strip()
            self.config_manager.set_heartbeat_config({"active_start": sh, "active_end": eh})
            save_hours_btn.set_label("✓ Aplicado")
            GLib.timeout_add(1500, lambda: (save_hours_btn.set_label("Aplicar Horas"), False)[1])
        save_hours_btn.connect("clicked", _on_save_hours)
        range_box.append(save_hours_btn)

        hours_row = Adw.ActionRow(title="Rango Horario Activo (HH:MM)")
        hours_row.set_child(range_box)
        ctrl_grp.add(hours_row)

        # Intelligent Silence & Notifications
        self.hb_silent_row = Adw.SwitchRow(
            title="Silencio Inteligente",
            subtitle="Si no hay tareas pendientes ni alertas (HEARTBEAT_OK), no envía notificaciones"
        )
        self.hb_silent_row.set_active(hb_cfg.get("silent_if_ok", True))
        def _on_silent_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"silent_if_ok": val})
        self.hb_silent_row.connect("notify::active", _on_silent_toggled)
        ctrl_grp.add(self.hb_silent_row)

        self.hb_notif_row = Adw.SwitchRow(
            title="Notificaciones Gráficas de Escritorio",
            subtitle="Muestra aviso emergente con notify-send cuando hay hallazgos o alertas"
        )
        self.hb_notif_row.set_active(hb_cfg.get("desktop_notification", True))
        def _on_notif_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"desktop_notification": val})
        self.hb_notif_row.connect("notify::active", _on_notif_toggled)
        ctrl_grp.add(self.hb_notif_row)

        self.hb_chat_row = Adw.SwitchRow(
            title="Mostrar en el Chat",
            subtitle="Publica el resumen en una tarjeta dentro del chat interactivo"
        )
        self.hb_chat_row.set_active(hb_cfg.get("show_in_chat", True))
        def _on_chat_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"show_in_chat": val})
        self.hb_chat_row.connect("notify::active", _on_chat_toggled)
        ctrl_grp.add(self.hb_chat_row)

        self.hb_tts_row = Adw.SwitchRow(
            title="Pronunciar con Voz TTS",
            subtitle="Lee el aviso por voz si hay alertas importantes"
        )
        self.hb_tts_row.set_active(hb_cfg.get("speak_tts", False))
        def _on_tts_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"speak_tts": val})
        self.hb_tts_row.connect("notify::active", _on_tts_toggled)
        ctrl_grp.add(self.hb_tts_row)

        # Trigger Now Row with status pill
        self.trigger_row = Adw.ActionRow(
            title="Estado del Pulso y Ejecución Inmediata",
            subtitle="Último pulso: Nunca"
        )
        self.hb_status_pill = Gtk.Label(label="IDLE")
        self.hb_status_pill.add_css_class("status-pill")
        self.trigger_row.add_suffix(self.hb_status_pill)

        self.hb_spinner = Gtk.Spinner()
        self.trigger_row.add_suffix(self.hb_spinner)

        self.trigger_hb_btn = Gtk.Button(label="💓 Probar Pulso Ahora")
        self.trigger_hb_btn.add_css_class("suggested-action")
        self.trigger_hb_btn.connect("clicked", lambda _b: self.hb_manager.trigger_global_now())
        self.trigger_row.add_suffix(self.trigger_hb_btn)
        ctrl_grp.add(self.trigger_row)

        page.add(ctrl_grp)

        # Group 2: HEARTBEAT.md Checklist Editor
        md_grp = Adw.PreferencesGroup()
        md_grp.set_title("Lista de Verificación del Pulso (HEARTBEAT.md)")
        md_grp.set_description(
            "Archivo Markdown donde se definen los puntos exactos que el agente comprueba en cada ciclo. "
            "Puedes editarlo libremente o pedirle al agente en el chat que lo ajuste por ti."
        )

        md_scroll = Gtk.ScrolledWindow()
        md_scroll.set_min_content_height(200)
        md_scroll.set_max_content_height(320)
        md_scroll.add_css_class("chat-input")

        self.hb_md_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.hb_md_textview.get_buffer().set_text(self.memory_manager.get_heartbeat_md())
        md_scroll.set_child(self.hb_md_textview)

        md_row = Adw.ActionRow()
        md_row.set_child(md_scroll)
        md_grp.add(md_row)

        # Buttons Bar for HEARTBEAT.md
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_halign(Gtk.Align.END)
        btn_bar.set_margin_top(6)
        btn_bar.set_margin_bottom(6)

        reset_btn = Gtk.Button(label="Restablecer por Defecto")
        reset_btn.add_css_class("flat")
        def _on_reset(_b):
            self.hb_md_textview.get_buffer().set_text(DEFAULT_HEARTBEAT_MD)
            self.memory_manager.save_heartbeat_md(DEFAULT_HEARTBEAT_MD)
            self.hb_manager.notify_listeners()
        reset_btn.connect("clicked", _on_reset)
        btn_bar.append(reset_btn)

        save_md_btn = Gtk.Button(label="Guardar HEARTBEAT.md")
        save_md_btn.add_css_class("suggested-action")
        def _on_save_md(_b):
            buf = self.hb_md_textview.get_buffer()
            start, end = buf.get_bounds()
            text = buf.get_text(start, end, True)
            self.memory_manager.save_heartbeat_md(text)
            self.hb_manager.notify_listeners()
            save_md_btn.set_label("✓ Guardado")
            GLib.timeout_add(1500, lambda: (save_md_btn.set_label("Guardar HEARTBEAT.md"), False)[1])
        save_md_btn.connect("clicked", _on_save_md)
        btn_bar.append(save_md_btn)

        btn_row = Adw.ActionRow()
        btn_row.set_child(btn_bar)
        md_grp.add(btn_row)

        page.add(md_grp)
        self.add(page)
        self._refresh_hb_status_ui()

    def _refresh_hb_status_ui(self):
        if not hasattr(self, "hb_status_pill"):
            return
        hb_cfg = self.config_manager.get_heartbeat_config()
        status = hb_cfg.get("last_status", "idle")
        last_run = hb_cfg.get("last_run")
        is_running = self.hb_manager.is_task_running("global_heartbeat") or status == "running"

        if is_running:
            self.hb_spinner.start()
            self.trigger_hb_btn.set_sensitive(False)
            self.hb_status_pill.set_text("⏳ EJECUTANDO")
            self.hb_status_pill.set_css_classes(["status-pill-connecting"])
            self.trigger_row.set_subtitle("Ejecutando verificación proactiva en segundo plano...")
        else:
            self.hb_spinner.stop()
            self.trigger_hb_btn.set_sensitive(True)

            if status == "silent_ok":
                self.hb_status_pill.set_text("✓ OK (SILENCIOSO)")
                self.hb_status_pill.set_css_classes(["status-pill-connected"])
            elif status == "ok":
                self.hb_status_pill.set_text("● ALERTA/REPORTE")
                self.hb_status_pill.set_css_classes(["status-pill-connected"])
            elif status == "error":
                self.hb_status_pill.set_text("❌ ERROR")
                self.hb_status_pill.set_css_classes(["status-pill-error"])
            else:
                self.hb_status_pill.set_text("○ EN ESPERA")
                self.hb_status_pill.set_css_classes(["status-pill-disconnected"])

            if last_run:
                try:
                    dt = datetime.datetime.fromisoformat(last_run)
                    sub = f"Último pulso: {dt.strftime('%H:%M:%S (%d/%m)')}"
                    res_snippet = hb_cfg.get("last_result", "").strip().replace("\n", " ")
                    if res_snippet:
                        sub += f" · {res_snippet[:60]}..."
                    self.trigger_row.set_subtitle(sub)
                except Exception:
                    self.trigger_row.set_subtitle(f"Último pulso: {last_run}")
            else:
                self.trigger_row.set_subtitle("Aún no se ha ejecutado ningún pulso")

    # -------------------------------------------------------------
    # TAB 2: AUTOMATIZACIONES PROGRAMADAS
    # -------------------------------------------------------------
    def _build_automations_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Automatizaciones")
        page.set_icon_name("weather-clear-night-symbolic")

        header_grp = Adw.PreferencesGroup()
        header_grp.set_title("Tareas y Recordatorios Automáticos")
        header_grp.set_description(
            "Crea tareas que el agente evaluará a intervalos regulares, a una hora fija del día o con un temporizador."
        )

        # Action bar to create new
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_margin_top(4)
        action_box.set_margin_bottom(6)

        new_btn = Gtk.Button(label="➕ Nueva Automatización")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda _b: self._open_automation_editor(None))
        action_box.append(new_btn)

        action_row = Adw.ActionRow()
        action_row.set_child(action_box)
        header_grp.add(action_row)
        page.add(header_grp)

        # Group for dynamic list
        self.auto_list_grp = Adw.PreferencesGroup()
        self.auto_list_grp.set_title("Automatizaciones Configuradas")
        page.add(self.auto_list_grp)

        self.add(page)
        self._refresh_automations_list()

    def _open_automation_editor(self, auto_data: Optional[Dict[str, Any]]):
        dlg = AutomationEditorDialog(
            parent=self,
            hb_manager=self.hb_manager,
            automation_data=auto_data,
            on_saved=lambda _a: self._refresh_automations_list()
        )
        dlg.present()

    def _refresh_automations_list(self):
        if not hasattr(self, "auto_list_grp"):
            return

        # Clear existing rows
        for r in list(self._auto_rows):
            try:
                self.auto_list_grp.remove(r)
            except Exception:
                pass
        self._auto_rows.clear()

        automations = self.config_manager.get_automations()

        if not automations:
            empty_row = Adw.ActionRow(
                title="No hay automatizaciones programadas",
                subtitle="Haz clic en '➕ Nueva Automatización' para crear una o elegir una plantilla rápida."
            )
            self.auto_list_grp.add(empty_row)
            self._auto_rows.append(empty_row)
            return

        for auto in automations:
            aid = auto.get("id", "")
            name = auto.get("name", "Automatización")
            is_enabled = auto.get("enabled", False)
            badge_str = format_schedule_badge(auto)
            last_run = auto.get("last_run", "")
            last_status = auto.get("last_status", "idle")
            is_running = self.hb_manager.is_task_running(aid) or last_status == "running"

            row = Adw.ActionRow()
            row.set_title(f"<b>{html.escape(name)}</b>")

            sub_parts = [badge_str]
            if last_run:
                try:
                    dt = datetime.datetime.fromisoformat(last_run)
                    sub_parts.append(f"Última: {dt.strftime('%H:%M (%d/%m)')}")
                except Exception:
                    pass
            prompt_snip = auto.get("prompt", "").replace("\n", " ").strip()
            if prompt_snip:
                sub_parts.append(f'"{prompt_snip[:45]}..."')
            row.set_subtitle(" · ".join(sub_parts))

            # Status pill
            status_pill = Gtk.Label()
            if is_running:
                status_pill.set_text("⏳")
                status_pill.add_css_class("status-pill-connecting")
            elif last_status in ("ok", "silent_ok"):
                status_pill.set_text("✓")
                status_pill.add_css_class("status-pill-connected")
            elif last_status == "error":
                status_pill.set_text("❌")
                status_pill.add_css_class("status-pill-error")
            else:
                status_pill.set_text("○")
                status_pill.add_css_class("status-pill-disconnected")
            row.add_prefix(status_pill)

            # Controls:
            # 1. Switch
            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(is_enabled)
            def _on_sw_toggled(_sw, _param, target_id=aid):
                self.hb_manager.update_automation(target_id, {"enabled": _sw.get_active()})
            sw.connect("notify::active", _on_sw_toggled)
            row.add_suffix(sw)

            # 2. Run now button
            run_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
            run_btn.add_css_class("flat")
            run_btn.set_tooltip_text("Ejecutar ahora")
            run_btn.set_sensitive(not is_running)
            run_btn.connect("clicked", lambda _b, target_id=aid: self.hb_manager.trigger_automation_now(target_id))
            row.add_suffix(run_btn)

            # 3. Edit button
            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.set_tooltip_text("Editar automatización")
            edit_btn.connect("clicked", lambda _b, a_data=auto: self._open_automation_editor(a_data))
            row.add_suffix(edit_btn)

            # 4. Delete button
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Eliminar automatización")
            def _on_del(_b, target_id=aid):
                self.hb_manager.delete_automation(target_id)
                self._refresh_automations_list()
            del_btn.connect("clicked", _on_del)
            row.add_suffix(del_btn)

            self.auto_list_grp.add(row)
            self._auto_rows.append(row)

    # -------------------------------------------------------------
    # TAB 3: REGISTRO DE PULSOS Y EJECUCIONES (LOGS)
    # -------------------------------------------------------------
    def _build_logs_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Historial de Pulsos")
        page.set_icon_name("view-list-bullet-symbolic")

        header_grp = Adw.PreferencesGroup()
        header_grp.set_title("Registro de Ejecuciones Recientes")
        header_grp.set_description("Historial de pulsos proactivos y tareas automáticas evaluadas en segundo plano.")

        clear_btn = Gtk.Button(label="🗑 Limpiar Registro")
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", lambda _b: (self.hb_manager.clear_logs(), self._refresh_logs_ui()))

        action_row = Adw.ActionRow()
        action_row.add_suffix(clear_btn)
        header_grp.add(action_row)
        page.add(header_grp)

        self.logs_list_grp = Adw.PreferencesGroup()
        self.logs_list_grp.set_title("Últimos Pulsos")
        page.add(self.logs_list_grp)

        self.add(page)
        self._refresh_logs_ui()

    def _refresh_logs_ui(self):
        if not hasattr(self, "logs_list_grp"):
            return

        for r in list(self._logs_rows):
            try:
                self.logs_list_grp.remove(r)
            except Exception:
                pass
        self._logs_rows.clear()

        logs = self.hb_manager.get_logs()
        if not logs:
            empty_row = Adw.ActionRow(
                title="No hay registros de ejecución",
                subtitle="Los pulsos evaluados se registrarán aquí con su duración y herramientas usadas."
            )
            self.logs_list_grp.add(empty_row)
            self._logs_rows.append(empty_row)
            return

        for item in logs[:25]:
            ts = item.get("timestamp", "")
            sname = item.get("source_name", "Pulso")
            status = item.get("status", "ok")
            summary = item.get("summary", "").strip().replace("\n", " ")
            dur = item.get("duration_sec", 0.0)
            tools = item.get("tools_used", [])

            time_str = ts
            try:
                dt = datetime.datetime.fromisoformat(ts)
                time_str = dt.strftime("%H:%M:%S · %d/%m")
            except Exception:
                pass

            row = Adw.ActionRow()
            row.set_title(f"<b>{html.escape(sname)}</b>")

            sub_items = [time_str, f"{dur}s"]
            if tools:
                sub_items.append(f"{len(tools)} herramientas ({', '.join(tools[:3])})")
            if summary:
                sub_items.append(f'"{summary[:50]}..."')
            row.set_subtitle(" · ".join(sub_items))

            status_pill = Gtk.Label()
            if status == "silent_ok":
                status_pill.set_text("HEARTBEAT_OK")
                status_pill.add_css_class("status-pill-connected")
            elif status == "ok":
                status_pill.set_text("REPORTE")
                status_pill.add_css_class("status-pill-connected")
            else:
                status_pill.set_text("ERROR")
                status_pill.add_css_class("status-pill-error")

            row.add_suffix(status_pill)
            self.logs_list_grp.add(row)
            self._logs_rows.append(row)
