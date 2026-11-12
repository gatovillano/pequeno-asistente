"""
Real-time Log Viewer Window for Pequeño Asistente.
Displays live internal logs (AgentCore, MediaProcessor, Antigravity, LiteLLM, MCP tools)
with search filtering, level filtering, auto-scroll, and one-click copy.
"""
import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Pango
from typing import Dict, Any, List

from pequeno_asistente.log_stream import LiveLogBroadcaster, LOG_FILE_PATH


class LogViewerWindow(Adw.Window):
    """Floating non-modal window showing real-time application logs."""

    def __init__(self, parent_window: Gtk.Window):
        super().__init__(
            title="Registros en Tiempo Real (Logs)",
            default_width=760,
            default_height=480,
            transient_for=parent_window,
            modal=False,
        )
        self.add_css_class("main-window")
        self.broadcaster = LiveLogBroadcaster.get_instance()
        self.auto_scroll = True
        self.level_filter = "ALL"
        self.search_query = ""
        self._all_lines: List[Dict[str, Any]] = []

        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(root_box)

        # Header Bar
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        title_box.append(icon)
        title_lbl = Gtk.Label(label="<b>Registros en Tiempo Real</b>", use_markup=True)
        title_box.append(title_lbl)
        header.set_title_widget(title_box)

        # Clear button
        clear_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_btn.add_css_class("flat")
        clear_btn.set_tooltip_text("Limpiar visor de registros")
        clear_btn.connect("clicked", self._on_clear_clicked)
        header.pack_end(clear_btn)

        # Copy button
        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.set_tooltip_text("Copiar todos los registros visibles")
        self.copy_btn.connect("clicked", self._on_copy_clicked)
        header.pack_end(self.copy_btn)

        root_box.append(header)

        # Toolbar (Search + Level Filter + Auto-scroll)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filtrar por texto (ej. audio, antigravity, mcp, error)...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_filter_changed)
        toolbar.append(self.search_entry)

        level_model = Gtk.StringList.new(["Todos", "INFO+", "WARNING+", "ERROR"])
        self.level_dropdown = Gtk.DropDown(model=level_model)
        self.level_dropdown.set_selected(0)
        self.level_dropdown.connect("notify::selected", self._on_level_changed)
        toolbar.append(self.level_dropdown)

        self.autoscroll_toggle = Gtk.ToggleButton(label="Auto-scroll")
        self.autoscroll_toggle.set_active(True)
        self.autoscroll_toggle.connect("toggled", self._on_autoscroll_toggled)
        toolbar.append(self.autoscroll_toggle)

        root_box.append(toolbar)

        # Scrolled TextView for Monospace Logs
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_margin_start(12)
        self.scrolled.set_margin_end(12)
        self.scrolled.set_margin_bottom(6)

        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_top_margin(8)
        self.text_view.set_bottom_margin(8)
        self.text_view.set_left_margin(10)
        self.text_view.set_right_margin(10)
        self.text_view.add_css_class("code-block-card")

        self.buffer = self.text_view.get_buffer()
        self.tag_info = self.buffer.create_tag("INFO", foreground="#89b4fa")
        self.tag_warn = self.buffer.create_tag("WARNING", foreground="#f9e2af")
        self.tag_error = self.buffer.create_tag("ERROR", foreground="#f38ba8", weight=Pango.Weight.BOLD)
        self.tag_time = self.buffer.create_tag("TIME", foreground="#6c7086")

        self.scrolled.set_child(self.text_view)
        root_box.append(self.scrolled)

        # Footer showing log file path & terminal command
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_start(14)
        footer.set_margin_end(14)
        footer.set_margin_bottom(8)

        cmd_str = f"tail -f {LOG_FILE_PATH}"
        footer_lbl = Gtk.Label(
            label=f"<small>Archivo en vivo: <tt>{LOG_FILE_PATH}</tt>  ·  Terminal: <tt>{cmd_str}</tt></small>",
            use_markup=True,
            xalign=0,
            selectable=True,
        )
        footer_lbl.add_css_class("dim-label")
        footer_lbl.set_hexpand(True)
        footer.append(footer_lbl)
        root_box.append(footer)

        # Load initial records from disk/broadcaster
        self._load_initial_logs()
        self.broadcaster.add_listener(self._on_new_log_record_threadsafe)
        self.connect("close-request", self._on_close_request)

    def _load_initial_logs(self):
        records = self.broadcaster.get_recent_records(limit=400)
        if not records and LOG_FILE_PATH.exists():
            try:
                lines = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-250:]
                for ln in lines:
                    lvl = "ERROR" if "[ERROR]" in ln else ("WARNING" if "[WARNING]" in ln else "INFO")
                    records.append({
                        "timestamp": ln[:8] if len(ln) > 8 else "",
                        "level": lvl,
                        "name": "",
                        "message": ln,
                        "formatted": ln,
                    })
            except Exception:
                pass
        self._all_lines = list(records)
        self._rebuild_buffer()

    def _matches_filter(self, entry: Dict[str, Any]) -> bool:
        lvl = entry.get("level", "INFO")
        if self.level_filter == "WARNING" and lvl not in ("WARNING", "ERROR", "CRITICAL"):
            return False
        if self.level_filter == "ERROR" and lvl not in ("ERROR", "CRITICAL"):
            return False
        if self.search_query:
            text = entry.get("formatted", "").lower()
            if self.search_query not in text:
                return False
        return True

    def _append_entry_to_buffer(self, entry: Dict[str, Any]):
        end_iter = self.buffer.get_end_iter()
        line_text = entry.get("formatted", "") + "\n"
        lvl = entry.get("level", "INFO")
        if lvl in ("ERROR", "CRITICAL"):
            self.buffer.insert_with_tags(end_iter, line_text, self.tag_error)
        elif lvl == "WARNING":
            self.buffer.insert_with_tags(end_iter, line_text, self.tag_warn)
        else:
            self.buffer.insert(end_iter, line_text)

    def _rebuild_buffer(self):
        self.buffer.set_text("")
        for entry in self._all_lines:
            if self._matches_filter(entry):
                self._append_entry_to_buffer(entry)
        if self.auto_scroll:
            GLib.idle_add(self._scroll_to_bottom)

    def _on_new_log_record_threadsafe(self, entry: Dict[str, Any]):
        GLib.idle_add(self._on_new_log_record_ui, entry)

    def _on_new_log_record_ui(self, entry: Dict[str, Any]):
        self._all_lines.append(entry)
        if len(self._all_lines) > 600:
            self._all_lines = self._all_lines[-600:]
        if self._matches_filter(entry):
            self._append_entry_to_buffer(entry)
            if self.auto_scroll:
                self._scroll_to_bottom()
        return False

    def _scroll_to_bottom(self):
        adj = self.scrolled.get_vadjustment()
        if adj:
            adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _on_filter_changed(self, entry: Gtk.SearchEntry):
        self.search_query = entry.get_text().strip().lower()
        self._rebuild_buffer()

    def _on_level_changed(self, dropdown: Gtk.DropDown, _pspec):
        idx = dropdown.get_selected()
        if idx == 2:
            self.level_filter = "WARNING"
        elif idx == 3:
            self.level_filter = "ERROR"
        else:
            self.level_filter = "ALL"
        self._rebuild_buffer()

    def _on_autoscroll_toggled(self, btn: Gtk.ToggleButton):
        self.auto_scroll = btn.get_active()
        if self.auto_scroll:
            self._scroll_to_bottom()

    def _on_clear_clicked(self, _btn):
        self._all_lines.clear()
        self.broadcaster.clear()
        self.buffer.set_text("")

    def _on_copy_clicked(self, _btn):
        start, end = self.buffer.get_bounds()
        txt = self.buffer.get_text(start, end, True)
        display = Gdk.Display.get_default()
        if display and txt:
            display.get_clipboard().set(txt)
            self.copy_btn.set_icon_name("object-select-symbolic")
            GLib.timeout_add(1400, lambda: (self.copy_btn.set_icon_name("edit-copy-symbolic"), False)[1])

    def _on_close_request(self, *_args):
        self.broadcaster.remove_listener(self._on_new_log_record_threadsafe)
        return False
