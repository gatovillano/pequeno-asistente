"""
Skill Dialogs for Pequeño Asistente.
Includes:
- SkillInstallDialog: Install skills from URL/Git, local path/folder, or direct manual authoring.
- SkillViewerDialog: View full markdown content, metadata, and origin of any discovered skill.
"""
import os
import html
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango

from pequeno_asistente.agent.skills_manager import SkillManager, SkillInfo


DEFAULT_SKILL_TEMPLATE = """---
name: {name}
description: Explicación concisa de qué hace esta habilidad y cuándo activarla.
tags: ["utilidad", "procedimiento"]
---

# Habilidad: {name}

## Cuándo Usar Esta Habilidad
- Describe aquí las situaciones exactas en las que el agente debe aplicar este procedimiento.

## Procedimiento Paso a Paso
1. Primer paso de la verificación o tarea.
2. Segundo paso o comando necesario.
3. Síntesis y confirmación al usuario.
"""


class SkillViewerDialog(Adw.Window):
    """Dialog that displays the full content and metadata of a skill."""
    def __init__(self, parent: Gtk.Window, skill: SkillInfo):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(680, 560)
        self.set_title(f"Habilidad: {skill.name}")
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # Header Bar
        header = Adw.HeaderBar()
        close_btn = Gtk.Button(label="Cerrar")
        close_btn.connect("clicked", lambda _b: self.close())
        header.pack_end(close_btn)
        main_box.append(header)

        # Content container
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_vexpand(True)
        main_box.append(content)

        # Info card
        info_grp = Adw.PreferencesGroup()
        meta_row = Adw.ActionRow()
        meta_row.set_title(f"<b>{html.escape(skill.name)}</b> <code>({html.escape(skill.id)})</code>")
        meta_row.set_subtitle(html.escape(skill.description))

        source_pill = Gtk.Label(label=skill.source_label)
        source_pill.add_css_class("status-pill")
        meta_row.add_suffix(source_pill)
        info_grp.add(meta_row)

        path_row = Adw.ActionRow(title="Ruta en disco", subtitle=str(skill.path))
        if skill.is_symlink and skill.symlink_target:
            path_row.set_subtitle(f"{skill.path} ➔ {skill.symlink_target}")
        info_grp.add(path_row)
        content.append(info_grp)

        # Markdown body
        label_code = Gtk.Label(label="<b>Contenido de la Habilidad (Markdown):</b>", use_markup=True, xalign=0)
        content.append(label_code)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_monospace(True)
        text_view.set_margin_start(8)
        text_view.set_margin_end(8)
        text_view.set_margin_top(8)
        text_view.set_margin_bottom(8)

        text_buffer = text_view.get_buffer()
        raw_text = skill.read_content()
        text_buffer.set_text(raw_text)

        scrolled.set_child(text_view)
        content.append(scrolled)


class SkillInstallDialog(Adw.Window):
    """Modal dialog to install a new skill via URL/Git, Local Path, or Direct Authoring."""
    def __init__(
        self,
        parent: Gtk.Window,
        skill_manager: SkillManager,
        on_installed: Optional[Callable[[], None]] = None
    ):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(600, 580)
        self.set_title("Instalar Nueva Habilidad (Skill)")
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")
        self.skill_manager = skill_manager
        self.on_installed = on_installed

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # Header Bar
        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancelar")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self.install_btn = Gtk.Button(label="Instalar Habilidad")
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", self._on_install_clicked)
        header.pack_end(self.install_btn)
        main_box.append(header)

        # Scrolled body
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(14)
        body.set_margin_bottom(16)
        body.set_margin_start(16)
        body.set_margin_end(16)
        scrolled.set_child(body)

        # Target scope selector
        dest_grp = Adw.PreferencesGroup(title="Destino de Instalación")
        dest_grp.set_description("Elige dónde se almacenará la habilidad para que esté disponible.")

        scopes = [
            "Pequeño Asistente (~/.config/pequeno-asistente/skills)",
            "Claude Code (~/.claude/skills)",
            "Estándar Global de Agentes (~/.agents/skills)"
        ]
        self.scope_combo = Adw.ComboRow(title="Entorno Destino", model=Gtk.StringList.new(scopes))
        self.scope_combo.set_selected(0)
        dest_grp.add(self.scope_combo)
        body.append(dest_grp)

        # Method tabs / ViewStack
        method_grp = Adw.PreferencesGroup(title="Método de Instalación")
        methods = [
            "Desde URL o Repositorio Git (GitHub / skills.sh)",
            "Desde Archivo o Carpeta Local",
            "Crear Manualmente (Editor Markdown)"
        ]
        self.method_combo = Adw.ComboRow(title="Origen", model=Gtk.StringList.new(methods))
        self.method_combo.set_selected(0)
        self.method_combo.connect("notify::selected", self._on_method_changed)
        method_grp.add(self.method_combo)
        body.append(method_grp)

        # Stack for method specific inputs
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        body.append(self.stack)

        # 1. URL / Git page
        url_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        url_grp = Adw.PreferencesGroup()
        self.url_entry = Adw.EntryRow(title="URL de Repositorio o Archivo Raw (.md)")
        self.url_entry.set_text("")
        url_grp.add(self.url_entry)

        self.url_name_entry = Adw.EntryRow(title="Nombre de la Habilidad (Opcional)")
        url_grp.add(self.url_name_entry)
        url_box.append(url_grp)

        url_hint = Gtk.Label(
            label="<small>Ejemplos:\n• <code>https://github.com/anthropics/skills</code>\n• <code>https://raw.githubusercontent.com/.../SKILL.md</code>\n• <code>owner/repo</code> (paquete comunitario)</small>",
            use_markup=True,
            xalign=0
        )
        url_box.append(url_hint)
        self.stack.add_named(url_box, "url")

        # 2. Local File / Folder page
        local_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        local_grp = Adw.PreferencesGroup()
        self.local_path_entry = Adw.EntryRow(title="Ruta al Archivo (.md) o Carpeta con SKILL.md")
        local_grp.add(self.local_path_entry)

        self.symlink_switch = Adw.SwitchRow(title="Crear enlace simbólico (symlink)", subtitle="Mantiene la habilidad sincronizada con su ruta original de desarrollo.")
        self.symlink_switch.set_active(False)
        local_grp.add(self.symlink_switch)

        self.local_name_entry = Adw.EntryRow(title="Nombre o ID deseado (Opcional)")
        local_grp.add(self.local_name_entry)
        local_box.append(local_grp)
        self.stack.add_named(local_box, "local")

        # 3. Manual authoring page
        manual_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        manual_grp = Adw.PreferencesGroup()
        self.manual_id_entry = Adw.EntryRow(title="Identificador Único (ID)")
        self.manual_id_entry.set_text("mi-nueva-habilidad")
        manual_grp.add(self.manual_id_entry)

        self.manual_name_entry = Adw.EntryRow(title="Nombre Descriptivo")
        self.manual_name_entry.set_text("Mi Nueva Habilidad")
        manual_grp.add(self.manual_name_entry)
        manual_box.append(manual_grp)

        manual_label = Gtk.Label(label="<b>Contenido Markdown de la Habilidad:</b>", use_markup=True, xalign=0)
        manual_box.append(manual_label)

        editor_scrolled = Gtk.ScrolledWindow()
        editor_scrolled.set_min_content_height(180)
        editor_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.manual_editor = Gtk.TextView()
        self.manual_editor.set_monospace(True)
        self.manual_editor.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.manual_editor.set_margin_start(8)
        self.manual_editor.set_margin_end(8)
        self.manual_editor.set_margin_top(8)
        self.manual_editor.set_margin_bottom(8)
        init_text = DEFAULT_SKILL_TEMPLATE.format(name="Mi Nueva Habilidad")
        self.manual_editor.get_buffer().set_text(init_text)
        editor_scrolled.set_child(self.manual_editor)
        manual_box.append(editor_scrolled)
        self.stack.add_named(manual_box, "manual")

        # Status label & Spinner
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner = Gtk.Spinner()
        status_box.append(self.spinner)
        self.status_label = Gtk.Label(xalign=0, wrap=True)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)
        body.append(status_box)

    def _on_method_changed(self, combo, _pspec):
        sel = combo.get_selected()
        if sel == 0:
            self.stack.set_visible_child_name("url")
            self.install_btn.set_label("Descargar e Instalar")
        elif sel == 1:
            self.stack.set_visible_child_name("local")
            self.install_btn.set_label("Instalar Local")
        else:
            self.stack.set_visible_child_name("manual")
            self.install_btn.set_label("Crear y Guardar")

    def _get_target_scope(self) -> str:
        sel = self.scope_combo.get_selected()
        if sel == 1:
            return "claude"
        elif sel == 2:
            return "agents"
        return "user"

    def _on_install_clicked(self, _btn):
        method_idx = self.method_combo.get_selected()
        target_scope = self._get_target_scope()

        self.install_btn.set_sensitive(False)
        self.spinner.start()
        self.status_label.set_text("Instalando habilidad...")

        def _worker():
            try:
                if method_idx == 0:
                    url = self.url_entry.get_text().strip()
                    name = self.url_name_entry.get_text().strip() or None
                    if not url:
                        return {"success": False, "error": "Por favor ingresa una URL válida."}
                    return self.skill_manager.install_skill(
                        source=url,
                        name=name,
                        target_scope=target_scope
                    )

                elif method_idx == 1:
                    path_str = self.local_path_entry.get_text().strip()
                    name = self.local_name_entry.get_text().strip() or None
                    as_symlink = self.symlink_switch.get_active()
                    if not path_str:
                        return {"success": False, "error": "Por favor ingresa una ruta local válida."}
                    return self.skill_manager.install_skill(
                        source=path_str,
                        name=name,
                        target_scope=target_scope,
                        as_symlink=as_symlink
                    )

                else:
                    skill_id = self.manual_id_entry.get_text().strip() or "nueva-habilidad"
                    buf = self.manual_editor.get_buffer()
                    content = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
                    if not content:
                        return {"success": False, "error": "El contenido Markdown no puede estar vacío."}
                    return self.skill_manager.install_skill(
                        name=skill_id,
                        content=content,
                        target_scope=target_scope
                    )

            except Exception as e:
                return {"success": False, "error": str(e)}

        def _done(res):
            self.spinner.stop()
            self.install_btn.set_sensitive(True)
            if res.get("success"):
                msg = res.get("message", "Habilidad instalada con éxito.")
                self.status_label.set_markup(f"<span color='green'>✔ {html.escape(msg)}</span>")
                if self.on_installed:
                    self.on_installed()
                GLib.timeout_add(1200, self.close)
            else:
                err = res.get("error", "Error desconocido al instalar.")
                self.status_label.set_markup(f"<span color='red'>✖ {html.escape(err)}</span>")

        def _thread_target():
            res = _worker()
            GLib.idle_add(_done, res)

        threading.Thread(target=_thread_target, daemon=True).start()
