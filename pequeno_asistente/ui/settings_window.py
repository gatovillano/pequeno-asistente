"""
Settings Window for Pequeño Asistente.
Built with Libadwaita PreferencesWindow:
- Tab 1: Configuración de Modelos (Selección de proveedor y carga directa desde API)
- Tab 2: Gestión de API Keys (Validación en vivo y almacenamiento seguro)
- Tab 3: Catálogo MCP (Servidores pre-configurados y personalizados)
- Tab 4: Memoria en Capas (USER.md y MEMORY.md)
- Tab 5: Escritorio y Waybar
"""
import os
import json
import html
import asyncio
import threading
from pathlib import Path
from typing import Dict, Any, Callable, Optional, List

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.mcp_client.manager import (
    MCPManager,
    is_remote_server_config,
    extract_servers_from_json,
    normalize_mcp_server_config,
    infer_remote_transport,
)
from pequeno_asistente.agent.providers import PROVIDERS, PROVIDER_ENV_VARS
from pequeno_asistente.mcp_client.catalog import MCP_CATALOG, CATALOG_CATEGORIES
from pequeno_asistente.mcp_client.registry import RegistryClient, DEFAULT_REGISTRIES
from pequeno_asistente.agent.memory import MemoryManager, DEFAULT_HEARTBEAT_MD
from pequeno_asistente.agent.model_fetcher import fetch_provider_models, validate_api_key, get_cached_models
from pequeno_asistente.agent.heartbeat import HeartbeatManager, AUTOMATION_PRESETS, format_schedule_badge
from pequeno_asistente.agent.skills_manager import SkillManager, SkillInfo
from pequeno_asistente.ui.automation_dialog import AutomationEditorDialog
from pequeno_asistente.ui.skill_dialogs import SkillInstallDialog, SkillViewerDialog

class SettingsWindow(Adw.PreferencesWindow):
    def __init__(
        self,
        config_manager: ConfigManager,
        memory_manager: MemoryManager,
        mcp_manager: Optional[MCPManager] = None,
        on_sync_needed: Optional[Callable[..., None]] = None,
        on_model_changed: Optional[Callable[[str], None]] = None,
        on_audio_changed: Optional[Callable[[], None]] = None,
        on_theme_changed: Optional[Callable[[str], None]] = None
    ):
        super().__init__()
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.mcp_manager = mcp_manager
        self.on_sync_needed = on_sync_needed or (lambda *args, **kwargs: None)
        self.on_model_changed = on_model_changed or (lambda m: None)
        self.on_audio_changed = on_audio_changed or (lambda: None)
        self.on_theme_changed = on_theme_changed or (lambda t: None)

        self.set_title("Configuración - Pequeño Asistente")
        self.set_default_size(780, 680)
        self.set_modal(True)
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")

        # State for model explorer
        self.current_provider_models: List[Dict[str, Any]] = []
        self.search_filter_text = ""
        self.registry_client = RegistryClient()
        self._catalog_rows: Dict[str, Dict[str, Any]] = {}
        self._syncing_switches = False
        self._shb_auto_rows: List[Any] = []
        self._shb_logs_rows: List[Any] = []

        # Heartbeat manager reference & live updates
        self.hb_manager = HeartbeatManager.get_instance()
        if self.hb_manager:
            self._hb_listener = lambda: GLib.idle_add(self._refresh_settings_hb_ui)
            self.hb_manager.add_listener(self._hb_listener)

        # Skill manager reference & live updates
        self.skill_manager = SkillManager.get_instance(self.config_manager)
        self._skills_listener = lambda: GLib.idle_add(self._refresh_skills_list)
        self.skill_manager.add_listener(self._skills_listener)
        self._skills_rows_widgets: List[Any] = []

        self.connect("close-request", self._on_settings_close)

        # Build pages
        self._build_models_page()
        self._build_api_keys_page()
        self._build_mcp_catalog_page()
        self._build_skills_page()
        self._build_workspaces_page()
        self._build_agents_page()
        self._build_heartbeats_page()
        self._build_audio_page()
        self._build_memory_page()
        self._build_desktop_integration_page()

    # -------------------------------------------------------------
    # TAB 1: CONFIGURACIÓN DE MODELOS (CARGA DIRECTO DE APIS)
    # -------------------------------------------------------------
    def _build_models_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Modelos")
        page.set_icon_name("network-server-symbolic")

        # 1. Header & Active Model Card
        active_grp = Adw.PreferencesGroup()
        active_grp.set_title("Modelo Activo Actualmente")
        
        self.active_model_row = Adw.ActionRow()
        self.active_model_row.set_title("<b>" + html.escape(self.config_manager.get_active_model()) + "</b>")
        self.active_model_row.set_subtitle(f"Proveedor: {self.config_manager.data['llm'].get('provider', 'openrouter')}")

        active_badge = Gtk.Label(label="⭐ ACTIVO")
        active_badge.add_css_class("status-pill")
        self.active_model_row.add_suffix(active_badge)
        active_grp.add(self.active_model_row)

        # Manual model input row
        self.manual_model_entry = Adw.EntryRow(title="Editar ID de Modelo Manualmente")
        self.manual_model_entry.set_text(self.config_manager.get_active_model())
        
        save_manual_btn = Gtk.Button(label="Aplicar")
        save_manual_btn.add_css_class("suggested-action")
        save_manual_btn.connect("clicked", self._on_apply_manual_model)
        self.manual_model_entry.add_suffix(save_manual_btn)
        active_grp.add(self.manual_model_entry)

        # Temperature & Tokens
        temp_row = Adw.ActionRow(title="Temperatura", subtitle="0.0 (determinista) a 1.0 (creativo)")
        self.temp_spin = Gtk.SpinButton.new_with_range(0.0, 1.5, 0.1)
        self.temp_spin.set_value(self.config_manager.data["llm"].get("temperature", 0.7))
        self.temp_spin.connect("value-changed", self._on_temp_changed)
        temp_row.add_suffix(self.temp_spin)
        active_grp.add(temp_row)
        page.add(active_grp)

        # 2. Explorer and Direct API Loader
        explorer_grp = Adw.PreferencesGroup()
        explorer_grp.set_title("Catálogo de Modelos por Proveedor (Carga desde API)")
        explorer_grp.set_description("Selecciona un proveedor para cargar los modelos directamente desde su API oficial.")

        # Provider Selector row
        prov_list = list(PROVIDERS.keys())
        current_prov = self.config_manager.data["llm"].get("provider", "openrouter")
        prov_names = [f"{PROVIDERS[p]['name']}" for p in prov_list]
        
        prov_string_list = Gtk.StringList.new(prov_names)
        self.models_prov_combo = Adw.ComboRow(title="Seleccionar Proveedor", model=prov_string_list)
        if current_prov in prov_list:
            self.models_prov_combo.set_selected(prov_list.index(current_prov))
        self.models_prov_combo.connect("notify::selected", self._on_explorer_provider_changed)
        explorer_grp.add(self.models_prov_combo)

        # Action bar: Fetch button + Search entry
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_margin_top(8)
        action_box.set_margin_bottom(8)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar modelo por nombre o ID (ej: claude, llama, gpt, flash)...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        action_box.append(self.search_entry)

        # Refresh / Fetch button
        self.fetch_btn = Gtk.Button(label="🔄 Cargar desde API")
        self.fetch_btn.add_css_class("suggested-action")
        self.fetch_btn.connect("clicked", lambda _b: self._fetch_models_from_api(force=True))
        action_box.append(self.fetch_btn)

        self.fetch_spinner = Gtk.Spinner()
        action_box.append(self.fetch_spinner)

        action_row = Adw.ActionRow()
        action_row.set_child(action_box)
        explorer_grp.add(action_row)

        # Status label for API fetch
        self.fetch_status_label = Gtk.Label(xalign=0, wrap=True)
        self.fetch_status_label.set_margin_start(12)
        self.fetch_status_label.set_margin_bottom(6)
        self.fetch_status_label.add_css_class("system-notice")
        explorer_grp.add(self.fetch_status_label)

        # Scrolled List for Models
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(260)
        scrolled.set_max_content_height(400)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.model_list_box = Gtk.ListBox()
        self.model_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.model_list_box.add_css_class("boxed-list")
        self.model_list_box.set_filter_func(self._model_filter_func)
        scrolled.set_child(self.model_list_box)

        list_row = Adw.ActionRow()
        list_row.set_child(scrolled)
        explorer_grp.add(list_row)

        page.add(explorer_grp)
        self.add(page)

        # Initial load of cached or default models for active provider
        GLib.idle_add(lambda: self._fetch_models_from_api(force=False))

    def _on_apply_manual_model(self, _btn):
        new_model = self.manual_model_entry.get_text().strip()
        if new_model:
            self._set_active_model(new_model)

    def _set_active_model(self, model_id: str, provider: Optional[str] = None):
        if not provider:
            # Infer provider from prefix
            if "/" in model_id:
                candidate = model_id.split("/")[0]
                if candidate in PROVIDERS:
                    provider = candidate
                elif candidate in ("anthropic", "meta-llama", "deepseek", "google"):
                    # OpenRouter prefixed without openrouter/
                    provider = "openrouter"
                    model_id = f"openrouter/{model_id}"
            else:
                provider = self.config_manager.data["llm"].get("provider", "openai")

        self.config_manager.data["llm"]["model"] = model_id
        if provider:
            self.config_manager.data["llm"]["provider"] = provider
        self.config_manager.data["llm"]["model_user_override"] = True
        self.config_manager.save()

        # Update UI labels
        self.active_model_row.set_title("<b>" + html.escape(model_id) + "</b>")
        self.active_model_row.set_subtitle(f"Proveedor: {provider or 'Detectado'}")
        self.manual_model_entry.set_text(model_id)
        
        self.on_model_changed(model_id)
        self._refresh_model_list_ui()

    def _on_explorer_provider_changed(self, combo, _param):
        idx = combo.get_selected()
        prov_list = list(PROVIDERS.keys())
        if 0 <= idx < len(prov_list):
            selected_prov = prov_list[idx]
            self._fetch_models_from_api(force=False, target_provider=selected_prov)

    def _fetch_models_from_api(self, force: bool = False, target_provider: Optional[str] = None):
        if not target_provider:
            idx = self.models_prov_combo.get_selected()
            prov_list = list(PROVIDERS.keys())
            target_provider = prov_list[idx] if 0 <= idx < len(prov_list) else "openrouter"

        api_key = self.config_manager.get_api_key(target_provider)
        base_url = self.config_manager.data["llm"].get("ollama_base_url", "http://localhost:11434")

        self.fetch_spinner.start()
        self.fetch_btn.set_sensitive(False)
        self.fetch_status_label.set_text(f"⏳ Conectando con la API de {PROVIDERS[target_provider]['name']}...")

        def run_fetch():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                models, err = loop.run_until_complete(
                    fetch_provider_models(target_provider, api_key=api_key, base_url=base_url, force_refresh=force)
                )
            except Exception as e:
                models, err = [], str(e)
            finally:
                loop.close()

            GLib.idle_add(self._on_models_fetched, target_provider, models, err)

        threading.Thread(target=run_fetch, daemon=True).start()

    def _on_models_fetched(self, provider: str, models: List[Dict[str, Any]], err: Optional[str]):
        self.fetch_spinner.stop()
        self.fetch_btn.set_sensitive(True)
        self.current_provider_models = models

        if err:
            self.fetch_status_label.set_markup(f"<span foreground='#f38ba8'>⚠️ {html.escape(err)}</span> (Mostrando modelos sugeridos)")
        else:
            self.fetch_status_label.set_markup(f"<span foreground='#a6e3a1'>✅ {len(models)} modelos cargados exitosamente desde la API de {PROVIDERS[provider]['name']}.</span>")

        self._refresh_model_list_ui()

    def _refresh_model_list_ui(self):
        # Clear list box
        while child := self.model_list_box.get_first_child():
            self.model_list_box.remove(child)

        current_active = self.config_manager.get_active_model()

        for m in self.current_provider_models:
            mid = m["id"]
            name = m.get("name", mid)
            desc = m.get("description", "")
            is_active = (mid == current_active)

            row = Adw.ActionRow()
            row.set_title(html.escape(name))
            sub = f"<tt>{html.escape(mid)}</tt>"
            if desc:
                sub += f" • {html.escape(desc)}"
            row.set_subtitle(sub)

            # Store search text in row for fast filtering
            row._search_key = f"{name} {mid} {desc}".lower()

            if is_active:
                active_lbl = Gtk.Label(label="✓ Activo")
                active_lbl.add_css_class("status-pill")
                row.add_suffix(active_lbl)
            else:
                sel_btn = Gtk.Button(label="Seleccionar")
                sel_btn.add_css_class("flat")
                
                def make_sel(model_id, prov):
                    return lambda _b: self._set_active_model(model_id, prov)

                sel_btn.connect("clicked", make_sel(mid, m.get("provider")))
                row.add_suffix(sel_btn)

            self.model_list_box.append(row)

        self.model_list_box.invalidate_filter()

    def _on_search_changed(self, entry):
        self.search_filter_text = entry.get_text().strip().lower()
        self.model_list_box.invalidate_filter()

    def _model_filter_func(self, row):
        if not self.search_filter_text:
            return True
        key = getattr(row, "_search_key", "")
        return self.search_filter_text in key

    # -------------------------------------------------------------
    # TAB 2: GESTIÓN DE API KEYS (VALIDACIÓN EN VIVO)
    # -------------------------------------------------------------
    def _build_api_keys_page(self):
        page = Adw.PreferencesPage()
        page.set_title("API Keys")
        page.set_icon_name("dialog-password-symbolic")

        info_grp = Adw.PreferencesGroup()
        info_grp.set_title("Gestión de Claves de Proveedores")
        info_grp.set_description("Administra y valida en tiempo real las credenciales de acceso a las APIs de IA.")
        page.add(info_grp)

        for prov_id, prov_info in PROVIDERS.items():
            grp = Adw.PreferencesGroup()
            grp.set_title(f"{prov_info['name']}")

            current_key = self.config_manager.get_api_key(prov_id)
            has_key = bool(current_key.strip()) if prov_id != "ollama" else True

            # Status row
            status_row = Adw.ActionRow()
            status_badge = Gtk.Label(label="🟢 CONFIGURADA" if has_key and prov_id != "ollama" else ("🟢 ACTIVO" if prov_id == "ollama" else "⚪ SIN CONFIGURAR"))
            status_badge.add_css_class("status-pill" if has_key else "system-notice")
            status_row.set_title(prov_info.get("description", ""))
            status_row.add_suffix(status_badge)
            grp.add(status_row)

            if prov_id == "antigravity":
                from pequeno_asistente.agent.antigravity_client import AntigravityClient
                is_logged = AntigravityClient.is_logged_in()
                status_badge.set_text("🟢 SESIÓN ACTIVA" if is_logged else "⚪ NO AUTENTICADO")
                status_badge.set_css_classes(["status-pill"] if is_logged else ["system-notice"])

                auth_row = Adw.ActionRow()
                auth_row.set_title("Autenticación OAuth2 de Google")
                auth_row.set_subtitle("Antigravity no utiliza API keys: autentica tu sesión directamente con Google.")

                login_btn = Gtk.Button(label="🔄 Re-autenticar" if is_logged else "🔑 Iniciar Sesión con Google")
                login_btn.add_css_class("suggested-action")
                login_btn.connect("clicked", lambda _b, b=status_badge, btn=login_btn: self._start_antigravity_login(b, btn))
                auth_row.add_suffix(login_btn)
                grp.add(auth_row)

            elif prov_id == "ollama":
                # Entry for Ollama Base URL
                ollama_entry = Adw.EntryRow(title="Dirección Base (OLLAMA_API_BASE)")
                ollama_entry.set_text(self.config_manager.data["llm"].get("ollama_base_url", "http://localhost:11434"))
                ollama_entry.connect("changed", lambda r: self._on_ollama_base_changed(r.get_text()))
                grp.add(ollama_entry)

                # Test button
                test_btn = Gtk.Button(label="🧪 Probar Servidor Ollama")
                test_btn.connect("clicked", lambda _b, pid=prov_id, r=ollama_entry, b=status_badge: self._test_api_key(pid, "", r.get_text(), b))
                ollama_entry.add_suffix(test_btn)
            else:
                key_entry = Adw.PasswordEntryRow(title=f"Clave API ({prov_info['env_var']})")
                if current_key:
                    key_entry.set_text(current_key)

                # Save on change
                def make_key_change(pid, badge):
                    def handler(r):
                        val = r.get_text().strip()
                        self.config_manager.set_api_key(pid, val)
                        badge.set_text("🟢 CONFIGURADA" if val else "⚪ SIN CONFIGURAR")
                        badge.set_css_classes(["status-pill"] if val else ["system-notice"])
                    return handler

                key_entry.connect("changed", make_key_change(prov_id, status_badge))

                # Test / Validate button
                test_btn = Gtk.Button(label="🧪 Validar Clave")
                test_btn.connect("clicked", lambda _b, pid=prov_id, r=key_entry, b=status_badge: self._test_api_key(pid, r.get_text(), "", b))
                key_entry.add_suffix(test_btn)
                grp.add(key_entry)

            page.add(grp)

        self.add(page)

    def _start_antigravity_login(self, badge_label: Gtk.Label, login_btn: Gtk.Button):
        badge_label.set_text("⏳ Abriendo navegador...")
        badge_label.set_css_classes(["system-notice"])
        login_btn.set_sensitive(False)

        def worker():
            try:
                from pequeno_asistente.agent.antigravity_client import run_login_flow
                success = run_login_flow()
            except Exception:
                success = False
            GLib.idle_add(self._on_antigravity_login_done, badge_label, login_btn, success)

        threading.Thread(target=worker, daemon=True).start()

    def _on_antigravity_login_done(self, badge_label: Gtk.Label, login_btn: Gtk.Button, success: bool):
        login_btn.set_sensitive(True)
        if success:
            badge_label.set_markup("<span foreground='#a6e3a1'>🟢 SESIÓN ACTIVA</span>")
            badge_label.set_css_classes(["status-pill"])
            login_btn.set_label("🔄 Re-autenticar")
        else:
            badge_label.set_markup("<span foreground='#f38ba8'>❌ ERROR EN LOGIN</span>")
            badge_label.set_css_classes(["error-notice"])

    def _test_api_key(self, provider: str, key: str, base_url: str, badge_label: Gtk.Label):
        badge_label.set_text("⏳ Probando...")
        badge_label.set_css_classes(["system-notice"])

        def run_test():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                valid, msg = loop.run_until_complete(
                    validate_api_key(provider, key, base_url)
                )
            except Exception as e:
                valid, msg = False, str(e)
            finally:
                loop.close()

            GLib.idle_add(self._on_test_result, badge_label, valid, msg)

        threading.Thread(target=run_test, daemon=True).start()

    def _on_test_result(self, badge_label: Gtk.Label, is_valid: bool, msg: str):
        if is_valid:
            badge_label.set_markup(f"<span foreground='#a6e3a1'>✅ {html.escape(msg)}</span>")
            badge_label.set_css_classes(["status-pill"])
        else:
            badge_label.set_markup(f"<span foreground='#f38ba8'>❌ {html.escape(msg)}</span>")
            badge_label.set_css_classes(["error-notice"])

    # -------------------------------------------------------------
    # TAB 3: CATÁLOGO Y REGISTROS MCP
    # -------------------------------------------------------------
    def _build_mcp_catalog_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Catálogo MCP")
        page.set_icon_name("application-x-addon-symbolic")

        # 1. Connected Registries and Repositories Group
        reg_grp = Adw.PreferencesGroup()
        reg_grp.set_title("Repositorios de Registro MCP (Fuentes)")
        reg_grp.set_description("Repositorios conectados para descubrir e instalar servidores automáticamente.")

        add_reg_btn = Gtk.Button(label="+ Añadir Repositorio")
        add_reg_btn.add_css_class("suggested-action")
        add_reg_btn.connect("clicked", self._open_add_registry_dialog)

        reg_header_row = Adw.ActionRow(title="Registros Conectados", subtitle="Smithery, Anthropic Official y repositorios personalizados")
        reg_header_row.add_suffix(add_reg_btn)
        reg_grp.add(reg_header_row)

        for reg in self.config_manager.get_mcp_registries():
            r_row = Adw.ActionRow()
            r_row.set_title(html.escape(reg.get("name", "Registro")))
            r_row.set_subtitle(html.escape(reg.get("url", "")))
            
            # Badge
            badge = Gtk.Label(label="🟢 Activo")
            badge.add_css_class("status-pill")
            r_row.add_suffix(badge)
            reg_grp.add(r_row)

        page.add(reg_grp)

        # 2. Online Registry Search Group
        search_grp = Adw.PreferencesGroup()
        search_grp.set_title("Explorador de Registros MCP Online")
        search_grp.set_description("Busca entre más de 10,000 servidores en Smithery y registros oficiales para instalarlos.")

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)

        self.reg_search_entry = Gtk.SearchEntry()
        self.reg_search_entry.set_placeholder_text("Buscar servidores en el registro (ej: git, slack, weather, notion, postgres)...")
        self.reg_search_entry.set_hexpand(True)
        self.reg_search_entry.connect("activate", lambda _e: self._search_online_registry())
        search_box.append(self.reg_search_entry)

        search_btn = Gtk.Button(label="🔍 Buscar")
        search_btn.add_css_class("suggested-action")
        search_btn.connect("clicked", lambda _b: self._search_online_registry())
        search_box.append(search_btn)

        self.reg_spinner = Gtk.Spinner()
        search_box.append(self.reg_spinner)

        search_action_row = Adw.ActionRow()
        search_action_row.set_child(search_box)
        search_grp.add(search_action_row)

        self.reg_status_label = Gtk.Label(xalign=0, wrap=True)
        self.reg_status_label.set_margin_start(12)
        self.reg_status_label.set_margin_bottom(6)
        self.reg_status_label.add_css_class("system-notice")
        search_grp.add(self.reg_status_label)

        # Scrolled list of online registry search results
        reg_scrolled = Gtk.ScrolledWindow()
        reg_scrolled.set_min_content_height(200)
        reg_scrolled.set_max_content_height(320)
        reg_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.reg_results_list = Gtk.ListBox()
        self.reg_results_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.reg_results_list.add_css_class("boxed-list")
        reg_scrolled.set_child(self.reg_results_list)

        reg_list_row = Adw.ActionRow()
        reg_list_row.set_child(reg_scrolled)
        search_grp.add(reg_list_row)
        page.add(search_grp)

        # 3. Local & Remote Configured Servers Group
        self.local_grp = Adw.PreferencesGroup()
        self.local_grp.set_title("Servidores Instalados y Configurados (Locales y Remotos)")
        self.local_grp.set_description("Servidores MCP locales (stdio) y remotos (Streamable HTTP / SSE) en tu configuración.")

        header_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Comprobar conexión y reconectar servidores MCP")
        refresh_btn.add_css_class("flat")
        refresh_btn.connect("clicked", lambda _b: self._on_reconnect_clicked(refresh_btn))

        add_custom_btn = Gtk.Button(label="+ Añadir Servidor MCP")
        add_custom_btn.add_css_class("suggested-action")
        add_custom_btn.connect("clicked", self._open_add_custom_mcp_dialog)

        header_actions_box.append(refresh_btn)
        header_actions_box.append(add_custom_btn)

        self.local_header_row = Adw.ActionRow(
            title="Servidores Configurados",
            subtitle="Comandos locales (stdio) o servidores remotos (Streamable HTTP / SSE)"
        )
        self.local_header_row.add_suffix(header_actions_box)
        self.local_grp.add(self.local_header_row)
        page.add(self.local_grp)

        self._local_server_rows = []
        self._refresh_mcp_servers_list()

        # 4. Preset Catalog grouped by category
        categories = ["Escritorio & Sistema", "Búsqueda & Web", "Desarrollo & Código", "Memoria & Notas", "Productividad & Audio"]
        
        for cat in categories:
            items_in_cat = {sid: item for sid, item in MCP_CATALOG.items() if item.get("category") == cat}
            if not items_in_cat:
                continue

            cat_grp = Adw.PreferencesGroup()
            cat_grp.set_title(html.escape(f"Catálogo: {cat}"))

            for s_id, info in items_in_cat.items():
                row = self._create_mcp_row(s_id, info)
                cat_grp.add(row)

            page.add(cat_grp)

        self.add(page)

    def _update_mcp_status_pill(self, label: Gtk.Label, server_id: str, is_active: bool) -> Tuple[str, Optional[str], int]:
        """
        Updates a Gtk.Label with the appropriate status text, CSS class, and tooltip.
        Returns (status_text, error_detail, tools_count).
        """
        for c in ["status-pill", "status-pill-connected", "status-pill-connecting", "status-pill-error", "status-pill-disconnected"]:
            label.remove_css_class(c)

        if not is_active:
            status_text = "⚪ Desactivado"
            css_class = "status-pill-disconnected"
            tooltip_text = f"El servidor '{server_id}' está desactivado."
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, 0

        if not self.mcp_manager:
            status_text = "⚪ Desconectado"
            css_class = "status-pill-disconnected"
            tooltip_text = "Gestor MCP no disponible."
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, 0

        inst = self.mcp_manager.instances.get(server_id)
        if not inst:
            status_text = "⚪ Desconectado"
            css_class = "status-pill-disconnected"
            tooltip_text = f"El servidor '{server_id}' no está iniciado."
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, 0

        if inst.status == "connected":
            tools_count = len(inst.tools)
            status_text = f"🟢 Conectado ({tools_count})" if tools_count > 0 else "🟢 Conectado"
            css_class = "status-pill-connected"
            tool_names = [t.name for t in inst.tools[:8]]
            names_preview = ", ".join(tool_names)
            if len(inst.tools) > 8:
                names_preview += f"... (+{len(inst.tools) - 8} más)"
            tooltip_text = f"Conectado exitosamente.\nHerramientas ({tools_count}): {names_preview or 'ninguna'}"
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, tools_count

        elif inst.status == "connecting":
            status_text = "🟡 Conectando..."
            css_class = "status-pill-connecting"
            tooltip_text = f"Intentando conectar al servidor '{server_id}'..."
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, 0

        elif inst.status == "error":
            status_text = "🔴 Error"
            css_class = "status-pill-error"
            error_detail = inst.error_message or "Error desconocido al conectar"
            tooltip_text = f"Error de conexión en '{server_id}':\n{error_detail}"
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, error_detail, 0

        else:
            status_text = "⚪ Desconectado"
            css_class = "status-pill-disconnected"
            tooltip_text = f"Estado: {inst.status}"
            label.set_text(status_text)
            label.add_css_class(css_class)
            label.set_tooltip_text(tooltip_text)
            return status_text, None, 0

    def _update_local_header_summary(self):
        if not hasattr(self, "local_header_row"):
            return
        servers = self.config_manager.get_mcp_servers()
        total = len(servers)
        if total == 0:
            self.local_header_row.set_subtitle("No hay servidores configurados")
            return

        connected_count = 0
        error_count = 0
        disabled_count = 0
        total_tools = 0

        if self.mcp_manager:
            for sid, scfg in servers.items():
                if scfg.get("disabled", False):
                    disabled_count += 1
                    continue
                inst = self.mcp_manager.instances.get(sid)
                if inst:
                    if inst.status == "connected":
                        connected_count += 1
                        total_tools += len(inst.tools)
                    elif inst.status == "error":
                        error_count += 1
        else:
            for sid, scfg in servers.items():
                if scfg.get("disabled", False):
                    disabled_count += 1

        parts = []
        if connected_count > 0:
            parts.append(f"🟢 {connected_count} conectados")
        if error_count > 0:
            parts.append(f"🔴 {error_count} con error")
        if disabled_count > 0:
            parts.append(f"⚪ {disabled_count} desactivados")
        if total_tools > 0:
            parts.append(f"{total_tools} herramientas")

        if parts:
            self.local_header_row.set_subtitle(" · ".join(parts))
        else:
            self.local_header_row.set_subtitle(f"{total - disabled_count} de {total} servidores activos")

    def _on_reconnect_clicked(self, btn: Gtk.Button):
        btn.set_sensitive(False)
        if hasattr(self, "local_header_row"):
            self.local_header_row.set_subtitle("Comprobando conexiones y reconectando...")
        self.on_sync_needed(force=True)
        GLib.timeout_add(1500, lambda: btn.set_sensitive(True) or False)

    def refresh_mcp_status(self):
        """Refreshes all MCP connection status indicators and summaries."""
        self._refresh_mcp_servers_list()

    def _refresh_catalog_status(self):
        if not hasattr(self, "_catalog_rows"):
            return
        active_servers = self.config_manager.get_mcp_servers()
        self._syncing_switches = True
        try:
            for server_id, item in self._catalog_rows.items():
                badge = item.get("badge")
                switch = item.get("switch")
                if not badge or not switch:
                    continue
                is_configured = server_id in active_servers
                is_active = is_configured and not active_servers[server_id].get("disabled", False)
                if switch.get_active() != is_active:
                    switch.set_active(is_active)
                if not is_configured:
                    badge.set_visible(False)
                else:
                    badge.set_visible(True)
                    self._update_mcp_status_pill(badge, server_id, is_active)
        finally:
            self._syncing_switches = False

    def _refresh_mcp_servers_list(self):
        """Re-renders the list of all currently configured local servers."""
        for row in self._local_server_rows:
            self.local_grp.remove(row)
        self._local_server_rows.clear()

        servers = self.config_manager.get_mcp_servers()
        if not servers:
            empty_row = Adw.ActionRow(
                title="No hay servidores configurados",
                subtitle="Haz clic en '+ Añadir Servidor Local' o activa uno del catálogo inferior."
            )
            self.local_grp.add(empty_row)
            self._local_server_rows.append(empty_row)
            self._update_local_header_summary()
            self._refresh_catalog_status()
            return

        for sid, scfg in servers.items():
            row = self._create_local_server_row(sid, scfg)
            self.local_grp.add(row)
            self._local_server_rows.append(row)

        self._update_local_header_summary()
        self._refresh_catalog_status()

    def _create_local_server_row(self, server_id: str, scfg: Dict[str, Any]) -> Adw.ActionRow:
        row = Adw.ActionRow()
        name = scfg.get("name") or server_id
        cmd = scfg.get("command", "")
        url = scfg.get("url") or (cmd if str(cmd).startswith(("http://", "https://")) else "")
        args_str = " ".join(str(a) for a in scfg.get("args", []))
        transport = scfg.get("transport", "stdio")
        is_disabled = scfg.get("disabled", False)
        is_remote = is_remote_server_config(scfg)

        icon = "🌐" if is_remote else "🧩"
        if server_id in MCP_CATALOG:
            icon = MCP_CATALOG[server_id].get("icon", icon)

        row.set_title(html.escape(f"{icon} {name} ({server_id})"))
        cmd_preview = url if is_remote else f"{cmd} {args_str}".strip()

        # Check negotiated transport if connected
        effective_transport = transport
        if self.mcp_manager and server_id in self.mcp_manager.instances:
            inst = self.mcp_manager.instances[server_id]
            if getattr(inst, "active_transport", None):
                effective_transport = inst.active_transport

        has_auth = bool(scfg.get("api_key") or scfg.get("headers"))
        auth_tag = " · 🔒 Auth" if (is_remote and has_auth) else ""
        endpoint_prefix = "URL" if is_remote else "Comando"

        # Status badge
        status_label = Gtk.Label()
        status_label.set_valign(Gtk.Align.CENTER)
        status_text, error_detail, tools_count = self._update_mcp_status_pill(
            status_label, server_id, not is_disabled
        )

        if error_detail:
            err_line = error_detail.splitlines()[0] if error_detail else "Error"
            if len(err_line) > 65:
                err_line = err_line[:62] + "..."
            row.set_subtitle(html.escape(f"⚠️ Error: {err_line} | {cmd_preview}"))
        elif tools_count > 0:
            row.set_subtitle(html.escape(f"{endpoint_prefix}: {cmd_preview} | Transporte: {effective_transport}{auth_tag} | {tools_count} herramientas"))
        else:
            row.set_subtitle(html.escape(f"{endpoint_prefix}: {cmd_preview} | Transporte: {effective_transport}{auth_tag}"))

        # Delete button
        del_btn = Gtk.Button(icon_name="user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.set_tooltip_text(f"Eliminar '{server_id}' de la configuración")

        def on_delete(_b):
            self.config_manager.remove_mcp_server(server_id)
            self.on_sync_needed()
            self._refresh_mcp_servers_list()

        del_btn.connect("clicked", on_delete)

        # Config / Edit button
        cfg_btn = Gtk.Button(icon_name="document-properties-symbolic")
        cfg_btn.add_css_class("flat")
        cfg_btn.set_tooltip_text("Editar comando, URL, cabeceras y variables")
        cfg_btn.connect("clicked", lambda _b: self._open_edit_mcp_dialog(server_id, scfg))

        # Switch toggle
        switch = Gtk.Switch()
        switch.set_active(not is_disabled)
        switch.set_valign(Gtk.Align.CENTER)

        def on_switch_toggled(sw, _param):
            if getattr(self, "_syncing_switches", False):
                return
            active = sw.get_active()
            self.config_manager.toggle_mcp_server(server_id, active)
            self._update_mcp_status_pill(status_label, server_id, active)
            self.on_sync_needed()
            self._update_local_header_summary()

        switch.connect("notify::active", on_switch_toggled)

        row.add_suffix(status_label)
        row.add_suffix(cfg_btn)
        row.add_suffix(del_btn)
        row.add_suffix(switch)
        return row

    def _open_add_registry_dialog(self, _btn):
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("Añadir Repositorio de Registro MCP")
        dialog.set_default_size(520, 320)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        grp = Adw.PreferencesGroup(title="Datos del Repositorio")
        grp.set_description("Introduce la URL del registro (API compatible con Smithery o archivo JSON de catálogo).")

        name_entry = Adw.EntryRow(title="Nombre del Registro (ej: Mi Repositorio MCP)")
        url_entry = Adw.EntryRow(title="URL del Registro (ej: https://.../registry.json)")

        grp.add(name_entry)
        grp.add(url_entry)
        box.append(grp)

        save_btn = Gtk.Button(label="Añadir Repositorio")
        save_btn.add_css_class("suggested-action")

        def add_and_close(_b):
            name = name_entry.get_text().strip()
            url = url_entry.get_text().strip()
            if name and url:
                self.config_manager.add_mcp_registry(name, url)
                dialog.close()

        save_btn.connect("clicked", add_and_close)
        box.append(save_btn)
        dialog.set_content(box)
        dialog.present()

    def _search_online_registry(self):
        query = self.reg_search_entry.get_text().strip()
        self.reg_spinner.start()
        self.reg_status_label.set_text(f"⏳ Buscando '{query}' en Smithery y registros oficiales...")

        custom_regs = self.config_manager.get_mcp_registries()

        def do_search():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                results = loop.run_until_complete(
                    self.registry_client.search_all(query, custom_registries=custom_regs)
                )
            except Exception as e:
                results = []
                logger.warning(f"Error in registry search: {e}")
            finally:
                loop.close()

            GLib.idle_add(self._on_registry_search_completed, results, query)

        threading.Thread(target=do_search, daemon=True).start()

    def _on_registry_search_completed(self, results: List[Dict[str, Any]], query: str):
        self.reg_spinner.stop()
        
        while child := self.reg_results_list.get_first_child():
            self.reg_results_list.remove(child)

        if not results:
            self.reg_status_label.set_markup(f"<span foreground='#f38ba8'>No se encontraron servidores para '{html.escape(query)}'.</span>")
            return

        self.reg_status_label.set_markup(f"<span foreground='#a6e3a1'>✅ {len(results)} servidores encontrados en los registros.</span>")

        active_servers = self.config_manager.get_mcp_servers()

        for s in results:
            sid = s["id"]
            name = s.get("name") or s.get("qualifiedName") or sid
            desc = s.get("description", "")
            source = s.get("source", "Registro")

            row = Adw.ActionRow()
            row.set_title(html.escape(name))
            row.set_subtitle(f"<small><b>[{html.escape(source)}]</b> {html.escape(desc[:140])}...</small>")

            if sid in active_servers and not active_servers[sid].get("disabled", False):
                lbl = Gtk.Label(label="✓ Instalado")
                lbl.add_css_class("status-pill")
                row.add_suffix(lbl)
            else:
                install_btn = Gtk.Button(label="📥 Instalar")
                install_btn.add_css_class("suggested-action")

                def make_install(server_info, btn_ref):
                    def handler(_b):
                        self._install_registry_server(server_info)
                        btn_ref.set_label("✓ Listo")
                        btn_ref.set_sensitive(False)
                    return handler

                install_btn.connect("clicked", make_install(s, install_btn))
                row.add_suffix(install_btn)

            self.reg_results_list.append(row)

    def _install_registry_server(self, server_info: Dict[str, Any]):
        sid = server_info["id"]
        cfg = normalize_mcp_server_config({
            "name": server_info.get("name", sid),
            "command": server_info.get("command", ""),
            "url": server_info.get("url", ""),
            "args": server_info.get("args", []),
            "transport": server_info.get("transport", "stdio"),
            "headers": server_info.get("headers", {}),
            "api_key": server_info.get("api_key", ""),
            "env": server_info.get("env", {}),
            "disabled": False
        }, fallback_name=sid)
        self.config_manager.set_mcp_server(sid, cfg)
        self.on_sync_needed()
        self._refresh_mcp_servers_list()

    def _create_mcp_row(self, server_id: str, info: Dict[str, Any]) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(html.escape(f"{info.get('icon', '🔧')} {info['name']}"))
        row.set_subtitle(html.escape(info.get("description", "")))

        active_servers = self.config_manager.get_mcp_servers()
        is_configured = server_id in active_servers
        is_active = is_configured and not active_servers[server_id].get("disabled", False)

        # Status badge
        badge = Gtk.Label()
        badge.set_valign(Gtk.Align.CENTER)
        self._update_mcp_status_pill(badge, server_id, is_active)
        if not is_configured:
            badge.set_visible(False)

        # Switch toggle
        switch = Gtk.Switch()
        switch.set_active(is_active)
        switch.set_valign(Gtk.Align.CENTER)

        # Track for live status sync
        self._catalog_rows[server_id] = {
            "row": row,
            "badge": badge,
            "switch": switch,
            "info": info
        }

        def on_switch_toggled(sw, _param):
            if getattr(self, "_syncing_switches", False):
                return
            active = sw.get_active()
            if active:
                cfg_entry = normalize_mcp_server_config({
                    "name": info["name"],
                    "command": info.get("command", ""),
                    "url": info.get("url", ""),
                    "args": info.get("args", []),
                    "transport": info.get("transport", "stdio"),
                    "headers": info.get("headers", {}),
                    "api_key": info.get("api_key", ""),
                    "env": info.get("env", {}),
                    "disabled": False
                }, fallback_name=server_id)
                self.config_manager.set_mcp_server(server_id, cfg_entry)
                badge.set_visible(True)
                self._update_mcp_status_pill(badge, server_id, True)
            else:
                self.config_manager.toggle_mcp_server(server_id, False)
                self._update_mcp_status_pill(badge, server_id, False)

            self.on_sync_needed()
            self._refresh_mcp_servers_list()

        switch.connect("notify::active", on_switch_toggled)

        # Config button
        cfg_btn = Gtk.Button(icon_name="document-properties-symbolic")
        cfg_btn.add_css_class("flat")
        cfg_btn.set_tooltip_text("Editar comando, URL, cabeceras y variables")
        cfg_btn.connect("clicked", lambda _b: self._open_edit_mcp_dialog(server_id, info))

        row.add_suffix(badge)
        row.add_suffix(cfg_btn)
        row.add_suffix(switch)
        return row

    def _open_edit_mcp_dialog(self, server_id: str, info: Dict[str, Any]):
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title(f"Configurar {info.get('name', server_id)}")
        dialog.set_default_size(560, 560)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        active_cfg = self.config_manager.get_mcp_servers().get(server_id, info)
        is_disabled = active_cfg.get("disabled", False)
        is_remote_init = is_remote_server_config(active_cfg)

        # Status Banner
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_margin_bottom(4)
        status_badge = Gtk.Label()
        status_badge.set_valign(Gtk.Align.CENTER)
        status_text, error_detail, tools_count = self._update_mcp_status_pill(
            status_badge, server_id, not is_disabled
        )
        status_box.append(status_badge)

        status_info_label = Gtk.Label(xalign=0, wrap=True)
        status_info_label.set_hexpand(True)
        if error_detail:
            status_info_label.set_markup(f"<span foreground='#f38ba8'><b>Error:</b> {html.escape(error_detail)}</span>")
        elif tools_count > 0:
            status_info_label.set_markup(f"<span foreground='#a6adc8'>{tools_count} herramientas disponibles para el asistente.</span>")
        elif not is_disabled:
            status_info_label.set_markup("<span foreground='#a6adc8'>Servidor configurado.</span>")
        else:
            status_info_label.set_markup("<span foreground='#6c7086'>Servidor desactivado.</span>")
        status_box.append(status_info_label)
        box.append(status_box)

        transport_options = [
            ("stdio", "Local — Comando (stdio)"),
            ("auto", "Remoto — Auto-detectar (Streamable HTTP / SSE)"),
            ("streamable_http", "Remoto — Streamable HTTP (POST/SSE moderno)"),
            ("sse", "Remoto — SSE (Server-Sent Events)"),
        ]
        transport_keys = [t[0] for t in transport_options]
        transport_labels = [t[1] for t in transport_options]

        curr_transport = str(active_cfg.get("transport", "stdio")).strip().lower()
        if is_remote_init and curr_transport == "stdio":
            curr_transport = infer_remote_transport(active_cfg.get("url") or active_cfg.get("command", ""))
        elif curr_transport in ("streamable-http", "http"):
            curr_transport = "streamable_http"

        t_idx = transport_keys.index(curr_transport) if curr_transport in transport_keys else 0
        transport_row = Adw.ComboRow(
            title="Tipo de Transporte",
            model=Gtk.StringList.new(transport_labels)
        )
        transport_row.set_selected(t_idx)

        cmd_entry = Adw.EntryRow(title="Comando (stdio)")
        cmd_entry.set_text(active_cfg.get("command", "") if not is_remote_init else "")

        args_entry = Adw.EntryRow(title="Argumentos (separados por espacio)")
        args_entry.set_text(" ".join(str(a) for a in active_cfg.get("args", [])))

        url_entry = Adw.EntryRow(title="URL del Servidor Remoto (https://...)")
        initial_url = active_cfg.get("url") or (active_cfg.get("command", "") if is_remote_init else "")
        url_entry.set_text(initial_url)

        api_key_entry = Adw.PasswordEntryRow(title="Token Bearer / API Key (Opcional)")
        api_key_entry.set_text(active_cfg.get("api_key", ""))

        headers_entry = Adw.EntryRow(title="Cabeceras HTTP Adicionales (JSON)")
        headers_entry.set_text(json.dumps(active_cfg.get("headers", {})))

        env_entry = Adw.EntryRow(title="Variables de Entorno (JSON)")
        env_entry.set_text(json.dumps(active_cfg.get("env", {})))

        def _sync_edit_transport_fields(*_):
            idx = transport_row.get_selected()
            sel_key = transport_keys[idx] if 0 <= idx < len(transport_keys) else "stdio"
            remote_mode = sel_key != "stdio"
            cmd_entry.set_visible(not remote_mode)
            args_entry.set_visible(not remote_mode)
            url_entry.set_visible(remote_mode)
            api_key_entry.set_visible(remote_mode)
            headers_entry.set_visible(remote_mode)

        transport_row.connect("notify::selected", _sync_edit_transport_fields)
        _sync_edit_transport_fields()

        pref_grp = Adw.PreferencesGroup()
        pref_grp.add(transport_row)
        pref_grp.add(cmd_entry)
        pref_grp.add(args_entry)
        pref_grp.add(url_entry)
        pref_grp.add(api_key_entry)
        pref_grp.add(headers_entry)
        pref_grp.add(env_entry)
        box.append(pref_grp)

        test_status_lbl = Gtk.Label(xalign=0, wrap=True)
        test_status_lbl.add_css_class("system-notice")
        box.append(test_status_lbl)

        btn_row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        def _build_draft_cfg() -> Dict[str, Any]:
            idx = transport_row.get_selected()
            sel_transport = transport_keys[idx] if 0 <= idx < len(transport_keys) else "stdio"
            try:
                env_dict = json.loads(env_entry.get_text()) if env_entry.get_text().strip() else {}
            except Exception:
                env_dict = {}
            try:
                hdr_dict = json.loads(headers_entry.get_text()) if headers_entry.get_text().strip() else {}
            except Exception:
                hdr_dict = {}

            args_list = [a for a in args_entry.get_text().split(" ") if a.strip()]
            remote_mode = sel_transport != "stdio"
            url_val = url_entry.get_text().strip() if remote_mode else ""
            cmd_val = url_val if remote_mode else cmd_entry.get_text().strip()

            return normalize_mcp_server_config({
                "name": info.get("name", server_id),
                "command": cmd_val,
                "url": url_val,
                "args": [] if remote_mode else args_list,
                "transport": sel_transport,
                "headers": hdr_dict if remote_mode else {},
                "api_key": api_key_entry.get_text().strip() if remote_mode else "",
                "env": env_dict,
                "disabled": active_cfg.get("disabled", False)
            }, fallback_name=server_id)

        test_btn = Gtk.Button(label="🧪 Probar Conexión")
        def on_test_conn(_b):
            draft = _build_draft_cfg()
            test_btn.set_sensitive(False)
            test_status_lbl.set_text("⏳ Probando conexión con el servidor MCP...")

            def _worker():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    res = loop.run_until_complete(MCPManager.test_server_connection(draft, server_id=server_id))
                except Exception as ex:
                    res = {"ok": False, "error": str(ex), "tools_count": 0}
                finally:
                    loop.close()

                def _done():
                    test_btn.set_sensitive(True)
                    if res.get("ok"):
                        t_cnt = res.get("tools_count", 0)
                        t_mode = res.get("transport", draft.get("transport"))
                        preview = ", ".join(res.get("tools", [])[:5])
                        extra = f" ({preview})" if preview else ""
                        test_status_lbl.set_markup(
                            f"<span foreground='#a6e3a1'>✅ Conectado ({html.escape(str(t_mode))}): {t_cnt} herramientas descubiertas{html.escape(extra)}</span>"
                        )
                    else:
                        err_txt = res.get("error") or "Error al conectar"
                        test_status_lbl.set_markup(
                            f"<span foreground='#f38ba8'>❌ Error: {html.escape(str(err_txt))}</span>"
                        )
                    return False

                GLib.idle_add(_done)

            threading.Thread(target=_worker, daemon=True).start()

        test_btn.connect("clicked", on_test_conn)
        btn_row_box.append(test_btn)

        save_btn = Gtk.Button(label="Guardar Configuración")
        save_btn.add_css_class("suggested-action")
        save_btn.set_hexpand(True)

        def save_and_close(_b):
            new_cfg = _build_draft_cfg()
            self.config_manager.set_mcp_server(server_id, new_cfg)
            self.on_sync_needed(force=True)
            self._refresh_mcp_servers_list()
            dialog.close()

        save_btn.connect("clicked", save_and_close)
        btn_row_box.append(save_btn)
        box.append(btn_row_box)
        dialog.set_content(box)
        dialog.present()

    def _open_add_custom_mcp_dialog(self, _btn):
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("Añadir Servidor MCP (Local o Remoto)")
        dialog.set_default_size(620, 580)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)

        # ViewStack for JSON mode vs Form mode
        stack = Adw.ViewStack()

        # --- TAB 1: PEGAR JSON COMPLETO ---
        json_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        json_info = Gtk.Label(
            label="Pega la configuración en formato JSON (compatible con Claude Desktop, Cursor y VS Code: soporta servidores locales con <i>\"command\"</i> y remotos con <i>\"url\"</i> y <i>\"headers\"</i>):",
            use_markup=True,
            wrap=True,
            xalign=0
        )
        json_box.append(json_info)

        sample_local_json = (
            '{\n'
            '  "mcpServers": {\n'
            '    "mi-servidor-local": {\n'
            '      "command": "uvx",\n'
            '      "args": ["weather-mcp"],\n'
            '      "env": {}\n'
            '    }\n'
            '  }\n'
            '}'
        )
        sample_remote_json = (
            '{\n'
            '  "mcpServers": {\n'
            '    "mi-servidor-remoto": {\n'
            '      "url": "https://ejemplo.com/mcp",\n'
            '      "transport": "streamable_http",\n'
            '      "headers": {\n'
            '        "Authorization": "Bearer TU_API_KEY"\n'
            '      }\n'
            '    }\n'
            '  }\n'
            '}'
        )

        tpl_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tpl_local_btn = Gtk.Button(label="💻 Plantilla Local (stdio)")
        tpl_local_btn.add_css_class("flat")
        tpl_remote_btn = Gtk.Button(label="🌐 Plantilla Remota (HTTP / SSE)")
        tpl_remote_btn.add_css_class("flat")
        tpl_bar.append(tpl_local_btn)
        tpl_bar.append(tpl_remote_btn)
        json_box.append(tpl_bar)

        json_scrolled = Gtk.ScrolledWindow()
        json_scrolled.set_min_content_height(210)
        json_scrolled.set_max_content_height(290)
        json_scrolled.add_css_class("chat-input")

        json_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.NONE)
        json_textview.set_monospace(True)
        json_textview.get_buffer().set_text(sample_remote_json)
        json_scrolled.set_child(json_textview)
        json_box.append(json_scrolled)

        tpl_local_btn.connect("clicked", lambda _b: json_textview.get_buffer().set_text(sample_local_json))
        tpl_remote_btn.connect("clicked", lambda _b: json_textview.get_buffer().set_text(sample_remote_json))

        json_status_label = Gtk.Label(xalign=0, wrap=True)
        json_status_label.add_css_class("system-notice")
        json_box.append(json_status_label)

        json_btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        import_json_btn = Gtk.Button(label="📥 Instalar desde JSON")
        import_json_btn.add_css_class("suggested-action")

        def import_from_json(_b):
            buf = json_textview.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            try:
                servers = extract_servers_from_json(text)
                if not servers:
                    json_status_label.set_markup(
                        "<span foreground='#f38ba8'>❌ No se encontró ningún servidor con 'command' o 'url' en el JSON.</span>"
                    )
                    return

                for sid, scfg in servers:
                    clean_id = "".join(c if c.isalnum() or c == "-" else "_" for c in sid.lower())
                    self.config_manager.set_mcp_server(clean_id, scfg)

                self.on_sync_needed()
                self._refresh_mcp_servers_list()
                dialog.close()
            except Exception as e:
                json_status_label.set_markup(f"<span foreground='#f38ba8'>❌ Error de sintaxis JSON: {html.escape(str(e))}</span>")

        import_json_btn.connect("clicked", import_from_json)
        json_btn_bar.append(import_json_btn)
        json_box.append(json_btn_bar)

        # --- TAB 2: FORMULARIO VISUAL ---
        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        grp = Adw.PreferencesGroup()
        grp.set_title("Campos de Configuración")

        transport_options = [
            ("stdio", "Local — Comando (stdio)"),
            ("auto", "Remoto — Auto-detectar (Streamable HTTP / SSE)"),
            ("streamable_http", "Remoto — Streamable HTTP (POST/SSE moderno)"),
            ("sse", "Remoto — SSE (Server-Sent Events)"),
        ]
        transport_keys = [t[0] for t in transport_options]
        transport_labels = [t[1] for t in transport_options]

        transport_row = Adw.ComboRow(
            title="Tipo de Servidor / Transporte",
            model=Gtk.StringList.new(transport_labels)
        )
        transport_row.set_selected(1)  # Default to Remote Auto-detect or user can pick Local

        id_entry = Adw.EntryRow(title="ID único (ej: mi-servidor-mcp)")
        name_entry = Adw.EntryRow(title="Nombre visible (ej: Servidor MCP Remoto)")

        # Local fields
        cmd_entry = Adw.EntryRow(title="Comando Local (ej: uvx, npx, python)")
        args_entry = Adw.EntryRow(title="Argumentos (ej: -y @modelcontextprotocol/server-git)")

        # Remote fields
        url_entry = Adw.EntryRow(title="URL del Servidor Remoto (ej: https://.../mcp o https://.../sse)")
        api_key_entry = Adw.PasswordEntryRow(title="Token Bearer / API Key (Opcional)")
        headers_entry = Adw.EntryRow(title="Cabeceras HTTP Adicionales (JSON: {\"X-API-Key\": \"...\"})")
        headers_entry.set_text("{}")

        env_entry = Adw.EntryRow(title="Variables de Entorno (JSON: {\"KEY\": \"VAL\"})")
        env_entry.set_text("{}")

        def _sync_form_transport_fields(*_):
            idx = transport_row.get_selected()
            sel_key = transport_keys[idx] if 0 <= idx < len(transport_keys) else "stdio"
            remote_mode = sel_key != "stdio"
            cmd_entry.set_visible(not remote_mode)
            args_entry.set_visible(not remote_mode)
            url_entry.set_visible(remote_mode)
            api_key_entry.set_visible(remote_mode)
            headers_entry.set_visible(remote_mode)

        transport_row.connect("notify::selected", _sync_form_transport_fields)
        _sync_form_transport_fields()

        grp.add(transport_row)
        grp.add(id_entry)
        grp.add(name_entry)
        grp.add(cmd_entry)
        grp.add(args_entry)
        grp.add(url_entry)
        grp.add(api_key_entry)
        grp.add(headers_entry)
        grp.add(env_entry)
        form_box.append(grp)

        form_status_label = Gtk.Label(xalign=0, wrap=True)
        form_status_label.add_css_class("system-notice")
        form_box.append(form_status_label)

        form_actions_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        def _build_form_cfg() -> Tuple[str, Dict[str, Any]]:
            sid = id_entry.get_text().strip()
            sname = name_entry.get_text().strip() or sid or "custom-mcp"
            idx = transport_row.get_selected()
            sel_transport = transport_keys[idx] if 0 <= idx < len(transport_keys) else "stdio"

            try:
                env_dict = json.loads(env_entry.get_text()) if env_entry.get_text().strip() else {}
            except Exception:
                env_dict = {}
            try:
                hdr_dict = json.loads(headers_entry.get_text()) if headers_entry.get_text().strip() else {}
            except Exception:
                hdr_dict = {}

            remote_mode = sel_transport != "stdio"
            cmd_val = cmd_entry.get_text().strip()
            url_val = url_entry.get_text().strip()

            # If user pasted an http(s) URL into command entry while in stdio mode, treat as remote
            if not remote_mode and cmd_val.startswith(("http://", "https://")):
                remote_mode = True
                url_val = cmd_val
                sel_transport = infer_remote_transport(url_val, "auto")

            args_list = [a for a in args_entry.get_text().split(" ") if a.strip()]
            cfg = normalize_mcp_server_config({
                "name": sname,
                "command": url_val if remote_mode else cmd_val,
                "url": url_val if remote_mode else "",
                "args": [] if remote_mode else args_list,
                "transport": sel_transport,
                "headers": hdr_dict if remote_mode else {},
                "api_key": api_key_entry.get_text().strip() if remote_mode else "",
                "env": env_dict,
                "disabled": False
            }, fallback_name=sid or "custom-mcp")
            return sid, cfg

        form_test_btn = Gtk.Button(label="🧪 Probar Conexión")
        def test_from_form(_b):
            sid, draft_cfg = _build_form_cfg()
            if not draft_cfg.get("command") and not draft_cfg.get("url"):
                form_status_label.set_markup("<span foreground='#f38ba8'>⚠️ Ingresa un comando o URL remota para probar.</span>")
                return
            form_test_btn.set_sensitive(False)
            form_status_label.set_text("⏳ Probando conexión con el servidor MCP...")

            def _worker():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    res = loop.run_until_complete(MCPManager.test_server_connection(draft_cfg, server_id=sid or "test-mcp"))
                except Exception as ex:
                    res = {"ok": False, "error": str(ex), "tools_count": 0}
                finally:
                    loop.close()

                def _done():
                    form_test_btn.set_sensitive(True)
                    if res.get("ok"):
                        t_cnt = res.get("tools_count", 0)
                        t_mode = res.get("transport", draft_cfg.get("transport"))
                        preview = ", ".join(res.get("tools", [])[:5])
                        extra = f" ({preview})" if preview else ""
                        form_status_label.set_markup(
                            f"<span foreground='#a6e3a1'>✅ Conectado ({html.escape(str(t_mode))}): {t_cnt} herramientas descubiertas{html.escape(extra)}</span>"
                        )
                    else:
                        err_txt = res.get("error") or "Error al conectar"
                        form_status_label.set_markup(
                            f"<span foreground='#f38ba8'>❌ Error: {html.escape(str(err_txt))}</span>"
                        )
                    return False

                GLib.idle_add(_done)

            threading.Thread(target=_worker, daemon=True).start()

        form_test_btn.connect("clicked", test_from_form)
        form_actions_bar.append(form_test_btn)

        form_save_btn = Gtk.Button(label="Añadir e Iniciar")
        form_save_btn.add_css_class("suggested-action")
        form_save_btn.set_hexpand(True)

        def add_from_form(_b):
            sid, cfg = _build_form_cfg()
            if not sid:
                form_status_label.set_markup("<span foreground='#f38ba8'>⚠️ Introduce un ID único para el servidor.</span>")
                return
            if not cfg.get("command") and not cfg.get("url"):
                form_status_label.set_markup("<span foreground='#f38ba8'>⚠️ Introduce un comando local o una URL remota.</span>")
                return

            clean_id = "".join(c if c.isalnum() or c == "-" else "_" for c in sid.lower())
            self.config_manager.set_mcp_server(clean_id, cfg)
            self.on_sync_needed()
            self._refresh_mcp_servers_list()
            dialog.close()

        form_save_btn.connect("clicked", add_from_form)
        form_actions_bar.append(form_save_btn)
        form_box.append(form_actions_bar)

        # Populate form button from JSON tab
        load_to_form_btn = Gtk.Button(label="➡️ Cargar en Formulario")
        def load_json_to_form(_b):
            buf = json_textview.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            try:
                servers = extract_servers_from_json(text)
                if servers:
                    sid, scfg = servers[0]
                    id_entry.set_text(sid)
                    name_entry.set_text(scfg.get("name") or sid)
                    t_val = scfg.get("transport", "stdio")
                    if t_val in transport_keys:
                        transport_row.set_selected(transport_keys.index(t_val))
                    elif is_remote_server_config(scfg):
                        transport_row.set_selected(transport_keys.index("auto"))
                    else:
                        transport_row.set_selected(0)

                    if is_remote_server_config(scfg):
                        url_entry.set_text(scfg.get("url") or scfg.get("command", ""))
                        api_key_entry.set_text(scfg.get("api_key", ""))
                        headers_entry.set_text(json.dumps(scfg.get("headers", {})))
                    else:
                        cmd_entry.set_text(scfg.get("command", ""))
                        args_entry.set_text(" ".join(str(a) for a in scfg.get("args", [])))

                    env_entry.set_text(json.dumps(scfg.get("env", {})))
                    _sync_form_transport_fields()
                    stack.set_visible_child_name("form")
                else:
                    json_status_label.set_text("No se detectó un servidor válido en el JSON.")
            except Exception as e:
                json_status_label.set_text(f"Error parseando JSON: {e}")

        load_to_form_btn.connect("clicked", load_json_to_form)
        json_btn_bar.append(load_to_form_btn)

        # Add pages to ViewStack
        page_json = stack.add_titled(json_box, "json", "Pegar JSON Completo")
        page_json.set_icon_name("edit-copy-symbolic")

        page_form = stack.add_titled(form_box, "form", "Formulario Visual")
        page_form.set_icon_name("document-edit-symbolic")

        switcher = Adw.ViewSwitcher(stack=stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        main_box.append(switcher)
        main_box.append(stack)

        dialog.set_content(main_box)
        dialog.present()

    # -------------------------------------------------------------
    # TAB 4: MEMORIA EN CAPAS
    # -------------------------------------------------------------
    def _build_memory_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Memoria en Capas")
        page.set_icon_name("emblem-documents-symbolic")

        header_grp = Adw.PreferencesGroup()
        header_grp.set_title("Arquitectura de Memoria (Estilo OpenClaw)")
        header_grp.set_description("Archivos Markdown persistentes que el agente consulta e inyecta en cada interacción.")
        page.add(header_grp)

        # Layer 1: USER.md
        user_grp = Adw.PreferencesGroup(title="Capa 1: Perfil de Usuario (USER.md)")
        user_grp.set_description("Preferencias personales, rol, stack técnico y lenguaje.")

        user_scrolled = Gtk.ScrolledWindow()
        user_scrolled.set_min_content_height(160)
        self.user_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.user_textview.get_buffer().set_text(self.memory_manager.get_user_profile())
        user_scrolled.set_child(self.user_textview)
        user_grp.add(user_scrolled)

        save_user_btn = Gtk.Button(label="Guardar USER.md")
        save_user_btn.connect("clicked", self._save_user_profile)
        user_action = Adw.ActionRow()
        user_action.add_suffix(save_user_btn)
        user_grp.add(user_action)
        page.add(user_grp)

        # Layer 2: MEMORY.md
        mem_grp = Adw.PreferencesGroup(title="Capa 2: Memoria a Largo Plazo (MEMORY.md)")
        mem_grp.set_description("Hechos consolidados, notas duraderas y conocimiento persistente.")

        mem_scrolled = Gtk.ScrolledWindow()
        mem_scrolled.set_min_content_height(160)
        self.mem_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.mem_textview.get_buffer().set_text(self.memory_manager.get_long_term_memory())
        mem_scrolled.set_child(self.mem_textview)
        mem_grp.add(mem_scrolled)

        save_mem_btn = Gtk.Button(label="Guardar MEMORY.md")
        save_mem_btn.connect("clicked", self._save_long_term_memory)
        mem_action = Adw.ActionRow()
        mem_action.add_suffix(save_mem_btn)
        mem_grp.add(mem_action)
        page.add(mem_grp)

        # Layer 3: LESSONS.md
        lessons_grp = Adw.PreferencesGroup(title="Capa 3: Reglas y Lecciones Aprendidas (LESSONS.md)")
        lessons_grp.set_description("Correcciones del usuario, límites y directivas que evitan repetir errores.")

        lessons_scrolled = Gtk.ScrolledWindow()
        lessons_scrolled.set_min_content_height(160)
        self.lessons_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.lessons_textview.get_buffer().set_text(self.memory_manager.get_lessons())
        lessons_scrolled.set_child(self.lessons_textview)
        lessons_grp.add(lessons_scrolled)

        save_lessons_btn = Gtk.Button(label="Guardar LESSONS.md")
        save_lessons_btn.connect("clicked", self._save_lessons)
        lessons_action = Adw.ActionRow()
        lessons_action.add_suffix(save_lessons_btn)
        lessons_grp.add(lessons_action)
        page.add(lessons_grp)

        # Layer 4 & 5: Cognitive Episodic Memory Index (SQLite FTS5 + Hybrid Semantic/Temporal Ranking)
        ep_grp = Adw.PreferencesGroup(title="Capas 4 y 5: Memoria Episódica Cognitiva (SQLite FTS5 + Ranking Híbrido)")
        ep_grp.set_description(
            "Índice híbrido léxico, semántico (vectores TF con stemming y sinónimos), temporal ('ayer', 'la semana pasada') "
            "y por importancia cognitiva con refuerzo por evocación. Haz clic en cualquier recuerdo para desplegar su título y contenido completo."
        )

        self.ep_stats_row = Adw.ActionRow(title="Estado del Índice Episódico")
        sync_ep_btn = Gtk.Button(label="Sincronizar Hilos y Bitácoras")
        sync_ep_btn.set_valign(Gtk.Align.CENTER)
        sync_ep_btn.connect("clicked", self._on_sync_episodic_memory)

        prune_ep_btn = Gtk.Button(label="Consolidar Ruido")
        prune_ep_btn.set_valign(Gtk.Align.CENTER)
        prune_ep_btn.set_tooltip_text("Elimina episodios antiguos de muy baja importancia que nunca fueron evocados")
        prune_ep_btn.connect("clicked", self._on_consolidate_episodic_memory)

        self.ep_stats_row.add_suffix(sync_ep_btn)
        self.ep_stats_row.add_suffix(prune_ep_btn)
        ep_grp.add(self.ep_stats_row)

        search_row = Adw.ActionRow(title="Explorar / Buscar Recuerdos Episódicos")
        self.ep_search_entry = Gtk.SearchEntry(placeholder_text="Ej: qué hicimos ayer, hyprland, audio, error...")
        self.ep_search_entry.set_hexpand(True)
        self.ep_search_entry.set_valign(Gtk.Align.CENTER)
        self.ep_search_entry.connect("activate", self._on_search_episodic_memory)
        self.ep_search_entry.connect("search-changed", self._on_search_episodic_memory)
        search_row.add_suffix(self.ep_search_entry)
        ep_grp.add(search_row)

        self.ep_results_box = Gtk.ListBox()
        self.ep_results_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.ep_results_box.add_css_class("boxed-list")
        ep_grp.add(self.ep_results_box)

        page.add(ep_grp)
        self._refresh_episodic_stats_and_list()

        self.add(page)

    def _build_full_episode_title(self, ep: Dict[str, Any]) -> str:
        """Reconstructs the complete untruncated title for an episode."""
        raw_title = (ep.get("title") or "Episodio").strip()
        u_msg = (ep.get("user_msg") or "").strip()
        a_msg = (ep.get("assistant_msg") or "").strip()
        raw_content = (ep.get("content") or "").strip()
        tools_list = ep.get("tools_used") or []
        short_tools = [t.split("__")[-1] for t in tools_list[:2]]

        if raw_title.startswith("Lección aprendida:") and a_msg:
            first_line = next((ln.strip() for ln in a_msg.splitlines() if ln.strip()), a_msg)
            return f"Lección aprendida: {first_line}"
        if raw_title.startswith("Hecho recordado:") and a_msg:
            first_line = next((ln.strip() for ln in a_msg.splitlines() if ln.strip()), a_msg)
            return f"Hecho recordado: {first_line}"

        if u_msg:
            first_u_para = next((ln.strip() for ln in u_msg.splitlines() if ln.strip()), "")
            first_u_para = first_u_para.lstrip("#>*`- ")
            if raw_content.startswith("Hilo: "):
                thread_prefix = raw_content.splitlines()[0][6:].strip()
                if thread_prefix and thread_prefix not in ("Nueva conversación", "Nuevo hilo") and not first_u_para.startswith(thread_prefix):
                    candidate = f"{thread_prefix}: {first_u_para}"
                else:
                    candidate = first_u_para
            else:
                candidate = first_u_para

            if short_tools:
                tools_suffix = f" [{', '.join(short_tools)}]"
                if tools_suffix not in candidate:
                    candidate = f"{candidate}{tools_suffix}"

            if candidate and ("…" in raw_title or len(candidate) > len(raw_title)):
                return candidate

        return raw_title

    def _create_episodic_expander_row(self, ep: Dict[str, Any]) -> Adw.ExpanderRow:
        """Creates a clickable Adw.ExpanderRow that unfolds the full title and complete memory content."""
        short_title = (ep.get("title") or "Episodio").strip()
        full_title = self._build_full_episode_title(ep)

        date_str = ep.get("timestamp") or ep.get("date_str") or ""
        if "T" in date_str:
            date_str = date_str.replace("T", " ")[:16]
        cat = ep.get("category", "interaction")
        imp = ep.get("importance", 0.5)
        score = ep.get("score")
        all_tags = ep.get("tags") or []
        all_tools = ep.get("tools_used") or []
        all_entities = ep.get("entities") or []
        access_count = ep.get("access_count", 0)
        sid = ep.get("source_id", "")
        thread_id = ep.get("thread_id", "")

        tags_short = " ".join(f"#{t}" for t in all_tags[:4])
        tools_short = ", ".join(t.split("__")[-1] for t in all_tools[:3])

        meta_parts = [date_str, f"cat:{cat}", f"imp:{imp}"]
        if score is not None:
            meta_parts.append(f"score:{score:.2f}")
        if tags_short:
            meta_parts.append(tags_short)
        if tools_short:
            meta_parts.append(f"tools:[{tools_short}]")
        meta_line = " • ".join(p for p in meta_parts if p)

        u_msg = (ep.get("user_msg") or "").strip()
        a_msg = (ep.get("assistant_msg") or "").strip()
        raw_content = (ep.get("content") or "").strip()

        preview_source = (a_msg or raw_content).replace("\n", " ").strip()
        preview_snippet = preview_source[:160] + ("…" if len(preview_source) > 160 else "")
        collapsed_subtitle = f"{meta_line}\n{preview_snippet}" if preview_snippet else meta_line

        row = Adw.ExpanderRow()
        row.set_use_markup(True)
        row.set_title(html.escape(short_title))
        row.set_subtitle(html.escape(collapsed_subtitle))
        row.set_title_lines(1)
        row.set_subtitle_lines(2)
        row.set_tooltip_text("Haz clic para desplegar el título completo y ver la memoria detallada")

        def _on_expanded_changed(exp_row, _pspec):
            if exp_row.get_expanded():
                exp_row.set_title(html.escape(full_title))
                exp_row.set_title_lines(0)
                exp_row.set_subtitle(html.escape(meta_line))
                exp_row.set_subtitle_lines(0)
            else:
                exp_row.set_title(html.escape(short_title))
                exp_row.set_title_lines(1)
                exp_row.set_subtitle(html.escape(collapsed_subtitle))
                exp_row.set_subtitle_lines(2)

        row.connect("notify::expanded", _on_expanded_changed)

        # Build expanded detail card inside the ExpanderRow
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail_box.set_margin_top(10)
        detail_box.set_margin_bottom(12)
        detail_box.set_margin_start(14)
        detail_box.set_margin_end(14)

        # 1. Full Title / User Request section
        prompt_text = u_msg or full_title
        if prompt_text:
            u_hdr = Gtk.Label(
                label="<b>📌 Título / Entrada Completa:</b>",
                use_markup=True,
                xalign=0
            )
            detail_box.append(u_hdr)

            u_lbl = Gtk.Label(
                label=prompt_text,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                xalign=0,
                selectable=True
            )
            if len(prompt_text) > 600 or prompt_text.count("\n") > 8:
                u_scroll = Gtk.ScrolledWindow()
                u_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                u_scroll.set_min_content_height(100)
                u_scroll.set_max_content_height(200)
                u_scroll.set_propagate_natural_height(True)
                u_scroll.set_child(u_lbl)
                detail_box.append(u_scroll)
            else:
                detail_box.append(u_lbl)

        # 2. Full Assistant / Memory Body section
        memory_body = a_msg or raw_content
        if memory_body:
            a_hdr = Gtk.Label(
                label="<b>🧠 Contenido de la Memoria:</b>",
                use_markup=True,
                xalign=0
            )
            a_hdr.set_margin_top(4)
            detail_box.append(a_hdr)

            a_lbl = Gtk.Label(
                label=memory_body,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                xalign=0,
                selectable=True
            )
            if len(memory_body) > 900 or memory_body.count("\n") > 12:
                a_scroll = Gtk.ScrolledWindow()
                a_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                a_scroll.set_min_content_height(120)
                a_scroll.set_max_content_height(240)
                a_scroll.set_propagate_natural_height(True)
                a_scroll.set_child(a_lbl)
                detail_box.append(a_scroll)
            else:
                detail_box.append(a_lbl)

        # 3. Full Metadata footer
        extra_meta_lines = []
        if all_tags:
            extra_meta_lines.append(f"<b>Etiquetas:</b> {html.escape(' '.join('#' + t for t in all_tags))}")
        if all_tools:
            extra_meta_lines.append(f"<b>Herramientas:</b> <tt>{html.escape(', '.join(all_tools))}</tt>")
        if all_entities:
            extra_meta_lines.append(f"<b>Entidades:</b> <tt>{html.escape(', '.join(all_entities))}</tt>")
        meta_tail = [f"<b>Fecha:</b> {html.escape(date_str)}", f"<b>Categoría:</b> {html.escape(cat)}", f"<b>Importancia:</b> {imp}", f"<b>Evocaciones:</b> {access_count}"]
        if thread_id:
            meta_tail.append(f"<b>Hilo:</b> <tt>{html.escape(thread_id)}</tt>")
        if sid:
            meta_tail.append(f"<b>ID:</b> <tt>{html.escape(sid)}</tt>")
        extra_meta_lines.append(" • ".join(meta_tail))

        meta_footer_lbl = Gtk.Label(
            label="<small>" + "\n".join(extra_meta_lines) + "</small>",
            use_markup=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            xalign=0,
            selectable=True
        )
        meta_footer_lbl.set_margin_top(4)
        meta_footer_lbl.set_opacity(0.8)
        detail_box.append(meta_footer_lbl)

        detail_row = Gtk.ListBoxRow()
        detail_row.set_activatable(False)
        detail_row.set_selectable(False)
        detail_row.set_child(detail_box)
        row.add_row(detail_row)

        # Suffix buttons: Copy & Delete
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copiar contenido completo de la memoria")

        def _on_copy_memory(_btn):
            full_copy_text = f"Título: {full_title}\nFecha: {date_str} ({cat})\n"
            if u_msg and u_msg != full_title:
                full_copy_text += f"\nUsuario:\n{u_msg}\n"
            if memory_body:
                full_copy_text += f"\nMemoria:\n{memory_body}\n"
            clipboard = self.get_clipboard()
            clipboard.set(full_copy_text.strip())
            copy_btn.set_icon_name("object-select-symbolic")
            GLib.timeout_add(1400, lambda: (copy_btn.set_icon_name("edit-copy-symbolic"), False)[1])

        copy_btn.connect("clicked", _on_copy_memory)
        row.add_suffix(copy_btn)

        if sid:
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Eliminar este recuerdo episódico")
            del_btn.connect("clicked", lambda _b, s=sid: self._on_delete_episode(s))
            row.add_suffix(del_btn)

        return row

    def _refresh_episodic_stats_and_list(self, query: str = ""):
        try:
            stats = self.memory_manager.get_episodic_stats()
            total = stats.get("total_episodes", 0)
            vec_cnt = stats.get("vectorized_episodes", 0)
            vec_eng = stats.get("vector_engine", {})
            eng_label = vec_eng.get("backend", "vector-384")
            avg_imp = stats.get("avg_importance", 0.0)
            recalls = stats.get("total_recalls", 0)
            db_kb = stats.get("db_size_kb", 0.0)
            top_tags = ", ".join(f"#{t['tag']} ({t['count']})" for t in stats.get("top_tags", [])[:5])
            sub = f"{total} episodios ({vec_cnt} vectorizados • {eng_label}) • Imp. media: {avg_imp} • Evocaciones: {recalls} • {db_kb} KB"
            if top_tags:
                sub += f"\nEtiquetas principales: {top_tags}"
            self.ep_stats_row.set_subtitle(sub)

            # Clear existing rows
            while True:
                child = self.ep_results_box.get_first_child()
                if not child:
                    break
                self.ep_results_box.remove(child)

            q = query.strip()
            if q:
                items = self.memory_manager.episodic_index.search(q, limit=15, reinforce=False)
            else:
                items = self.memory_manager.get_timeline(limit=15)

            if not items:
                empty_row = Adw.ActionRow(
                    title="No se encontraron episodios",
                    subtitle="Interactúa con el asistente o pulsa 'Sincronizar Hilos y Bitácoras'."
                )
                self.ep_results_box.append(empty_row)
                return

            for ep in items:
                row = self._create_episodic_expander_row(ep)
                self.ep_results_box.append(row)
        except Exception as e:
            logger.warning(f"Error refreshing episodic UI: {e}")

    def _on_sync_episodic_memory(self, _btn):
        self.memory_manager.sync_all()
        q = self.ep_search_entry.get_text() if hasattr(self, "ep_search_entry") else ""
        self._refresh_episodic_stats_and_list(q)

    def _on_consolidate_episodic_memory(self, _btn):
        self.memory_manager.episodic_index.consolidate_old_episodes()
        q = self.ep_search_entry.get_text() if hasattr(self, "ep_search_entry") else ""
        self._refresh_episodic_stats_and_list(q)

    def _on_search_episodic_memory(self, entry):
        self._refresh_episodic_stats_and_list(entry.get_text())

    def _on_delete_episode(self, source_id: str):
        self.memory_manager.episodic_index.delete_episode(source_id)
        q = self.ep_search_entry.get_text() if hasattr(self, "ep_search_entry") else ""
        self._refresh_episodic_stats_and_list(q)

    def _save_user_profile(self, _btn):
        buf = self.user_textview.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True)
        self.memory_manager.save_user_profile(text)

    def _save_long_term_memory(self, _btn):
        buf = self.mem_textview.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True)
        self.memory_manager.save_long_term_memory(text)

    def _save_lessons(self, _btn):
        buf = self.lessons_textview.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True)
        self.memory_manager.save_lessons(text)

    # -------------------------------------------------------------
    # TAB 5: APARIENCIA, INTEGRACIÓN DE ESCRITORIO & WAYBAR
    # -------------------------------------------------------------
    def _build_desktop_integration_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Escritorio y UI")
        page.set_icon_name("preferences-desktop-appearance-symbolic")

        # 1. Apariencia de la Interfaz (Modo Claro / Oscuro / Sistema)
        theme_grp = Adw.PreferencesGroup()
        theme_grp.set_title("Apariencia de la Interfaz (Tema Visual)")
        theme_grp.set_description("Alterna entre el modo oscuro, el modo claro o sincroniza con el tema de tu entorno Linux.")

        theme_options = [
            ("dark", "🌙 Modo Oscuro"),
            ("light", "☀️ Modo Claro"),
            ("system", "🖥️ Automático (Seguir al sistema)")
        ]
        theme_keys = [t[0] for t in theme_options]
        theme_labels = [t[1] for t in theme_options]

        curr_theme = self.config_manager.get_theme()
        curr_idx = theme_keys.index(curr_theme) if curr_theme in theme_keys else 0

        self.theme_combo_row = Adw.ComboRow(
            title="Tema de color",
            subtitle="También puedes alternar rápidamente desde el menú principal o con Ctrl+D",
            model=Gtk.StringList.new(theme_labels)
        )
        self.theme_combo_row.set_selected(curr_idx)

        def _on_theme_row_changed(combo, _pspec):
            idx = combo.get_selected()
            if 0 <= idx < len(theme_keys):
                chosen_theme = theme_keys[idx]
                self.config_manager.set_theme(chosen_theme)
                self.on_theme_changed(chosen_theme)
                if not Adw.StyleManager.get_default().get_dark():
                    self.add_css_class("theme-light")
                else:
                    self.remove_css_class("theme-light")

        self.theme_combo_row.connect("notify::selected", _on_theme_row_changed)
        theme_grp.add(self.theme_combo_row)
        page.add(theme_grp)

        hypr_grp = Adw.PreferencesGroup()
        hypr_grp.set_title("Atajo en Hyprland")
        hypr_grp.set_description("Añade la siguiente línea en ~/.config/hypr/hyprland.conf para alternar el asistente con Super+A:")
        
        hypr_entry = Adw.EntryRow(title="Regla de atajo")
        hypr_entry.set_text(f"bind = $mainMod, A, exec, {Path.home() / '.local' / 'bin' / 'pequeno-asistente'} --toggle")
        hypr_entry.set_editable(False)
        hypr_grp.add(hypr_entry)
        page.add(hypr_grp)

        waybar_grp = Adw.PreferencesGroup()
        waybar_grp.set_title("Icono en Waybar (Barra Superior)")
        waybar_grp.set_description("Agrega este bloque en modules-right de tu ~/.config/waybar/config.jsonc:")

        waybar_code = (
            '"custom/assistant": {\n'
            '    "format": "🤖",\n'
            '    "tooltip-format": "Pequeño Asistente (Super+A)",\n'
            '    "on-click": "~/.local/bin/pequeno-asistente --toggle"\n'
            '}'
        )
        
        waybar_label = Gtk.Label(label=f"<tt>{waybar_code}</tt>", use_markup=True, wrap=True, selectable=True)
        waybar_label.set_margin_top(8)
        waybar_label.set_margin_bottom(8)
        waybar_grp.add(waybar_label)
        page.add(waybar_grp)

        self.add(page)

    def _on_settings_close(self, _w):
        if hasattr(self, "hb_manager") and self.hb_manager and hasattr(self, "_hb_listener"):
            self.hb_manager.remove_listener(self._hb_listener)
        if hasattr(self, "skill_manager") and self.skill_manager and hasattr(self, "_skills_listener"):
            self.skill_manager.remove_listener(self._skills_listener)
        return False

    def _refresh_settings_hb_ui(self):
        self._refresh_shb_status_ui()
        self._refresh_shb_automations_list()
        self._refresh_shb_logs_ui()
        return False

    # -------------------------------------------------------------
    # TAB: HABILIDADES PROCEDIMENTALES (SKILLS)
    # -------------------------------------------------------------
    def _build_skills_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Habilidades")
        page.set_icon_name("system-run-symbolic")

        # 1. Header & Actions Group
        actions_grp = Adw.PreferencesGroup()
        actions_grp.set_title("Ecosistema de Habilidades (Skills)")
        actions_grp.set_description(
            "Habilidades modulares para el agente. Reconoce e integra automáticamente las habilidades de "
            "Claude Code (~/.claude/skills), el estándar abierto ~/.agents/skills y las globales del sistema."
        )

        header_row = Adw.ActionRow(
            title="Gestión de Habilidades",
            subtitle="Instala desde GitHub/URL, importa carpetas locales o crea nuevas habilidades."
        )

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        open_folder_btn = Gtk.Button(label="📂 Abrir Carpeta")
        open_folder_btn.set_tooltip_text("Abrir carpeta de habilidades del usuario en el gestor de archivos")
        open_folder_btn.connect("clicked", self._on_open_skills_folder)
        btn_box.append(open_folder_btn)

        reload_btn = Gtk.Button(label="🔄 Recargar")
        reload_btn.set_tooltip_text("Volver a escanear habilidades en disco")
        reload_btn.connect("clicked", self._on_reload_skills)
        btn_box.append(reload_btn)

        install_btn = Gtk.Button(label="+ Instalar Habilidad")
        install_btn.add_css_class("suggested-action")
        install_btn.connect("clicked", self._on_open_install_skill_dialog)
        btn_box.append(install_btn)

        header_row.add_suffix(btn_box)
        actions_grp.add(header_row)
        page.add(actions_grp)

        # 2. Filter & Search Group
        search_grp = Adw.PreferencesGroup()
        search_grp.set_title("Explorador y Filtros")

        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_box.set_margin_top(4)
        filter_box.set_margin_bottom(6)

        self.skills_search_entry = Gtk.SearchEntry()
        self.skills_search_entry.set_placeholder_text("Buscar por nombre, ID o descripción (ej: claude, git, thinking)...")
        self.skills_search_entry.set_hexpand(True)
        self.skills_search_entry.connect("search-changed", lambda _e: self._refresh_skills_list())
        filter_box.append(self.skills_search_entry)

        sources_labels = [
            "Todos los orígenes",
            "Claude Code",
            "Pequeño Asistente",
            "Global Agentes (~/.agents)",
            "Sistema (/etc, /usr)",
            "Antigravity"
        ]
        self.skills_source_combo = Adw.ComboRow(title="Filtrar por origen", model=Gtk.StringList.new(sources_labels))
        self.skills_source_combo.connect("notify::selected", lambda _c, _p: self._refresh_skills_list())

        filter_action_row = Adw.ActionRow()
        filter_action_row.set_child(filter_box)
        search_grp.add(filter_action_row)
        search_grp.add(self.skills_source_combo)
        page.add(search_grp)

        # 3. Discovered Skills List Group
        self.skills_list_grp = Adw.PreferencesGroup()
        self.skills_list_grp.set_title("Habilidades Disponibles")
        self.skills_list_grp.set_description("Lista unificada de habilidades detectadas en tu entorno.")
        page.add(self.skills_list_grp)

        self.add(page)
        self._refresh_skills_list()

    def _on_open_install_skill_dialog(self, _btn):
        dlg = SkillInstallDialog(self, self.skill_manager, on_installed=self._refresh_skills_list)
        dlg.present()

    def _on_open_skills_folder(self, _btn):
        import subprocess
        user_skills_dir = os.path.expanduser("~/.config/pequeno-asistente/skills")
        os.makedirs(user_skills_dir, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", user_skills_dir])
        except Exception as e:
            logger.warning(f"Error opening skills folder: {e}")

    def _on_reload_skills(self, _btn):
        self.skill_manager.discover_skills(force_refresh=True)
        self._refresh_skills_list()

    def _refresh_skills_list(self):
        if not hasattr(self, "skills_list_grp") or self.skills_list_grp is None:
            return

        for w in list(self._skills_rows_widgets):
            try:
                self.skills_list_grp.remove(w)
            except Exception:
                pass
        self._skills_rows_widgets.clear()

        query = self.skills_search_entry.get_text().strip().lower() if hasattr(self, "skills_search_entry") else ""
        source_idx = self.skills_source_combo.get_selected() if hasattr(self, "skills_source_combo") else 0

        source_map = {
            1: "claude",
            2: "user",
            3: "agents_global",
            4: "system",
            5: "antigravity"
        }
        filter_source = source_map.get(source_idx)

        skills = self.skill_manager.discover_skills()
        # Sort alphabetically by human-readable Name
        sorted_skills = sorted(skills.values(), key=lambda s: s.name.lower())

        matched_count = 0
        for skill in sorted_skills:
            if filter_source and skill.source != filter_source and filter_source not in skill.source:
                continue

            if query:
                match = (query in skill.name.lower() or query in skill.description.lower() or query in skill.id.lower())
                if not match and not any(query in t.lower() for t in skill.tags):
                    continue

            matched_count += 1
            row = Adw.ActionRow()
            escaped_name = html.escape(skill.name)
            escaped_id = html.escape(skill.id)
            desc_text = html.escape(skill.description)

            # Prominently show the skill Name as the primary title
            row.set_title(escaped_name)
            row.set_subtitle(f"ID: {escaped_id} • {desc_text}")
            row.set_title_lines(1)
            row.set_subtitle_lines(2)
            row.set_tooltip_text(f"{skill.name} ({skill.id})\n\n{skill.description}\n\nRuta: {skill.path}")

            # Source Badge
            badge = Gtk.Label(label=skill.source_label)
            badge.add_css_class("status-pill")
            row.add_suffix(badge)

            # View button
            view_btn = Gtk.Button(label="👁️ Ver")
            view_btn.set_tooltip_text("Ver contenido e instrucciones de esta habilidad")
            view_btn.connect("clicked", lambda _b, s=skill: self._on_skill_view_clicked(s))
            row.add_suffix(view_btn)

            # Toggle Switch
            switch = Gtk.Switch()
            switch.set_active(skill.enabled)
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect("state-set", lambda sw, state, sid=skill.id: self._on_skill_toggle(sw, state, sid))
            row.add_suffix(switch)

            # Uninstall button (for user or claude skills)
            if skill.source in ("user", "claude", "agents_global") or not skill.source in ("built-in", "system"):
                del_btn = Gtk.Button()
                del_btn.set_icon_name("user-trash-symbolic")
                del_btn.add_css_class("flat")
                del_btn.set_tooltip_text("Eliminar habilidad")
                del_btn.connect("clicked", lambda _b, s=skill: self._on_skill_delete(s))
                row.add_suffix(del_btn)

            self.skills_list_grp.add(row)
            self._skills_rows_widgets.append(row)

        if matched_count == 0:
            empty_row = Adw.ActionRow()
            empty_row.set_title("No se encontraron habilidades")
            empty_row.set_subtitle("Usa el botón '+ Instalar Habilidad' para agregar una desde GitHub, URL o crearla.")
            self.skills_list_grp.add(empty_row)
            self._skills_rows_widgets.append(empty_row)

    def _on_skill_view_clicked(self, skill: SkillInfo):
        dlg = SkillViewerDialog(self, skill)
        dlg.present()

    def _on_skill_toggle(self, switch: Gtk.Switch, state: bool, skill_id: str) -> bool:
        self.skill_manager.toggle_skill(skill_id, state)
        return False

    def _on_skill_delete(self, skill: SkillInfo):
        msg_dialog = Adw.MessageDialog(
            transient_for=self,
            heading="¿Eliminar habilidad?",
            body=f"¿Estás seguro de que deseas eliminar permanentemente la habilidad '{skill.name}' de tu disco ({skill.path})?"
        )
        msg_dialog.add_response("cancel", "Cancelar")
        msg_dialog.add_response("delete", "Eliminar")
        msg_dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _on_response(_d, response_id):
            if response_id == "delete":
                res = self.skill_manager.uninstall_skill(skill.id)
                self._refresh_skills_list()

        msg_dialog.connect("response", _on_response)
        msg_dialog.present()


    # -------------------------------------------------------------
    # TAB: WORKSPACES (proyecto/carpetas + memoria contextual)
    # -------------------------------------------------------------
    def _build_workspaces_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Workspaces")
        page.set_icon_name("folder-symbolic")

        info_grp = Adw.PreferencesGroup()
        info_grp.set_title("Workspaces de Proyecto")
        info_grp.set_description(
            "Crea workspaces para organizar hilos de chat independientes asociados a una o varias carpetas (proyecto). "
            "Cada workspace puede tener un archivo de memoria contextual para el agente."
        )

        header_row = Adw.ActionRow(
            title="Gestión de Workspaces",
            subtitle="Asocia carpetas del proyecto (explorador de archivos) y genera automáticamente la memoria contextual (CONTEXT.md)."
        )
        add_btn = Gtk.Button(label="+ Nuevo Workspace")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda _b: self._on_edit_workspace(None))
        header_row.add_suffix(add_btn)
        info_grp.add(header_row)
        page.add(info_grp)

        self.workspaces_list_grp = Adw.PreferencesGroup()
        self.workspaces_list_grp.set_title("Workspaces Configurados")
        self.workspaces_list_grp.set_description("Workspaces disponibles para iniciar conversaciones en contexto de proyecto.")
        page.add(self.workspaces_list_grp)

        self._workspaces_rows_widgets = []
        self.add(page)
        self._refresh_workspaces_list()

    def _refresh_workspaces_list(self):
        if not hasattr(self, "workspaces_list_grp") or self.workspaces_list_grp is None:
            return
        for w in list(getattr(self, "_workspaces_rows_widgets", [])):
            try:
                self.workspaces_list_grp.remove(w)
            except Exception:
                pass
        self._workspaces_rows_widgets = []

        workspaces = self.config_manager.get_workspaces() if hasattr(self.config_manager, "get_workspaces") else {}
        items = list(workspaces.values()) if isinstance(workspaces, dict) else []
        items = [w for w in items if isinstance(w, dict)]
        items.sort(key=lambda x: (x.get("name") or x.get("id") or "").lower())

        if not items:
            empty_row = Adw.ActionRow(
                title="No hay workspaces",
                subtitle="Pulsa '+ Nuevo Workspace' para elegir carpetas del proyecto; la memoria contextual se crea automáticamente."
            )
            self.workspaces_list_grp.add(empty_row)
            self._workspaces_rows_widgets.append(empty_row)
            return

        for ws in items:
            wid = ws.get("id", "")
            name = ws.get("name") or wid or "Workspace"
            desc = ws.get("description") or ""
            folders = ws.get("folders") or []
            if not folders and ws.get("workspace_path"):
                folders = [ws.get("workspace_path")]
            mem_file = ws.get("memory_file") or ""

            row = Adw.ActionRow()
            row.set_title(f"<b>{html.escape(name)}</b>")
            subtitle_parts = []
            if desc:
                subtitle_parts.append(html.escape(desc))
            if folders:
                if len(folders) == 1:
                    subtitle_parts.append(f"📁 <tt>{html.escape(folders[0])}</tt>")
                else:
                    subtitle_parts.append(f"📁 {len(folders)} carpetas: <tt>{html.escape(folders[0])}</tt> …")
            if mem_file:
                subtitle_parts.append(f"🧠 Memoria: <tt>{html.escape(mem_file)}</tt>")
            row.set_subtitle("\n".join(subtitle_parts) if subtitle_parts else "Sin carpetas ni memoria configuradas")
            row.set_title_lines(1)
            row.set_subtitle_lines(3)

            new_chat_btn = Gtk.Button(label="Nuevo chat")
            new_chat_btn.add_css_class("flat")
            new_chat_btn.set_tooltip_text("Crear una conversación nueva en este workspace")
            new_chat_btn.connect("clicked", lambda _b, w=wid: self._on_create_workspace_chat(w))
            row.add_suffix(new_chat_btn)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.set_tooltip_text("Editar workspace")
            edit_btn.connect("clicked", lambda _b, w=dict(ws): self._on_edit_workspace(w))
            row.add_suffix(edit_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Eliminar workspace")
            del_btn.connect("clicked", lambda _b, w=wid, n=name: self._on_delete_workspace(w, n))
            row.add_suffix(del_btn)

            self.workspaces_list_grp.add(row)
            self._workspaces_rows_widgets.append(row)

    def _on_create_workspace_chat(self, workspace_id: str):
        try:
            root = self.get_transient_for()
            if root and hasattr(root, "_on_new_thread"):
                root._on_new_thread(workspace_id=workspace_id)
                root.present()
                return
        except Exception:
            pass
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="Workspace listo",
            body="El workspace está configurado. Usa el botón 'Nuevo chat' de la ventana principal y selecciona este workspace."
        )
        dlg.add_response("ok", "Entendido")
        dlg.present()

    def _on_delete_workspace(self, workspace_id: str, name: str):
        msg = Adw.MessageDialog(
            transient_for=self,
            heading="¿Eliminar workspace?",
            body=f"Se eliminará el workspace '{name}' de la configuración. Las carpetas y archivos de memoria en disco no se borran. Las conversaciones existentes conservarán su copia del perfil."
        )
        msg.add_response("cancel", "Cancelar")
        msg.add_response("delete", "Eliminar")
        msg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _resp(_d, resp):
            if resp == "delete":
                self.config_manager.delete_workspace(workspace_id)
                self._refresh_workspaces_list()
        msg.connect("response", _resp)
        msg.present()

    def _on_edit_workspace(self, workspace_data):
        is_new = not (workspace_data and workspace_data.get("id"))
        win = Adw.Window()
        win.set_transient_for(self)
        win.set_modal(True)
        win.set_default_size(660, 560)
        win.set_title("Nuevo Workspace" if is_new else "Editar Workspace")
        if not Adw.StyleManager.get_default().get_dark():
            win.add_css_class("theme-light")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.set_content(main_box)

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancelar")
        cancel_btn.connect("clicked", lambda *_: win.close())
        header.pack_start(cancel_btn)
        save_btn = Gtk.Button(label="Guardar")
        save_btn.add_css_class("suggested-action")
        header.pack_end(save_btn)
        main_box.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12); body.set_margin_bottom(16)
        body.set_margin_start(16); body.set_margin_end(16)
        scrolled.set_child(body)

        grp = Adw.PreferencesGroup(title="Perfil del Workspace")
        name_row = Adw.EntryRow(title="Nombre del workspace/proyecto")
        name_row.set_text((workspace_data or {}).get("name", ""))
        grp.add(name_row)

        desc_row = Adw.EntryRow(title="Descripción (opcional)")
        desc_row.set_text((workspace_data or {}).get("description", ""))
        grp.add(desc_row)
        body.append(grp)

        # Folders picker (file browser) - supports one or more folders
        folders_grp = Adw.PreferencesGroup(
            title="Carpetas asociadas",
            description="Asocia una o varias carpetas del proyecto. Se abrirá el navegador de archivos para seleccionarlas."
        )
        selected_folders = []
        existing_folders = (workspace_data or {}).get("folders") or []
        if not existing_folders and (workspace_data or {}).get("workspace_path"):
            existing_folders = [(workspace_data or {}).get("workspace_path")]
        for f in existing_folders:
            if f and str(f).strip():
                selected_folders.append(str(f))

        folders_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        folders_grp.add(Adw.ActionRow(child=folders_list_box))

        add_folder_row = Adw.ActionRow(
            title="Añadir carpeta…",
            subtitle="Abre el explorador de archivos para seleccionar una carpeta del proyecto"
        )
        add_folder_btn = Gtk.Button(label="Examinar…")
        add_folder_btn.add_css_class("suggested-action")
        add_folder_row.add_suffix(add_folder_btn)
        add_folder_row.set_activatable_widget(add_folder_btn)
        folders_grp.add(add_folder_row)
        body.append(folders_grp)

        def _refresh_folders_ui():
            while child := folders_list_box.get_first_child():
                folders_list_box.remove(child)
            if not selected_folders:
                empty = Gtk.Label(label="No hay carpetas seleccionadas.", xalign=0)
                empty.add_css_class("dim-label")
                empty.set_margin_top(6); empty.set_margin_bottom(6)
                empty.set_margin_start(8)
                folders_list_box.append(empty)
                return
            for idx, folder_path in enumerate(list(selected_folders)):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.set_margin_top(2); row.set_margin_bottom(2)
                icon = Gtk.Image.new_from_icon_name("folder-symbolic")
                row.append(icon)
                lbl = Gtk.Label(label=folder_path, xalign=0)
                lbl.set_hexpand(True)
                lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                lbl.set_selectable(True)
                lbl.set_tooltip_text(folder_path)
                row.append(lbl)
                rm = Gtk.Button(icon_name="user-trash-symbolic")
                rm.add_css_class("flat")
                rm.set_tooltip_text("Quitar carpeta")
                def _rm(_b, i=idx):
                    if 0 <= i < len(selected_folders):
                        selected_folders.pop(i)
                        _refresh_folders_ui()
                rm.connect("clicked", _rm)
                row.append(rm)
                folders_list_box.append(row)

        def _on_folder_selected(dialog, result, multiple=False):
            try:
                if multiple and hasattr(dialog, "select_multiple_folders_finish"):
                    files = dialog.select_multiple_folders_finish(result)
                    if files:
                        n = files.get_n_items() if hasattr(files, "get_n_items") else 0
                        for i in range(n):
                            f = files.get_item(i)
                            if f and hasattr(f, "get_path"):
                                p = f.get_path()
                                if p and p not in selected_folders:
                                    selected_folders.append(p)
                else:
                    f = dialog.select_folder_finish(result)
                    if f and hasattr(f, "get_path"):
                        p = f.get_path()
                        if p and p not in selected_folders:
                            selected_folders.append(p)
            except Exception:
                # User cancelled or error
                pass
            _refresh_folders_ui()

        def _browse_folder(_btn=None):
            try:
                if hasattr(Gtk, "FileDialog"):
                    dialog = Gtk.FileDialog()
                    dialog.set_title("Seleccionar carpeta del workspace")
                    # Prefer multiple selection when available (GTK 4.10+)
                    if hasattr(dialog, "select_multiple_folders"):
                        dialog.select_multiple_folders(win, None, lambda d, r: _on_folder_selected(d, r, multiple=True))
                        return
                    dialog.select_folder(win, None, lambda d, r: _on_folder_selected(d, r, multiple=False))
                    return
            except Exception:
                pass
            # Fallback: Gtk.FileChooserNative
            try:
                chooser = Gtk.FileChooserNative.new(
                    "Seleccionar carpeta del workspace",
                    win,
                    Gtk.FileChooserAction.SELECT_FOLDER,
                    "Seleccionar",
                    "Cancelar"
                )
                chooser.set_select_multiple(True)
                def _on_native(resp_chooser, resp_id):
                    try:
                        if resp_id == Gtk.ResponseType.ACCEPT:
                            files = resp_chooser.get_files()
                            if files:
                                n = files.get_n_items() if hasattr(files, "get_n_items") else 0
                                for i in range(n):
                                    f = files.get_item(i)
                                    if f and hasattr(f, "get_path"):
                                        p = f.get_path()
                                        if p and p not in selected_folders:
                                            selected_folders.append(p)
                    except Exception:
                        pass
                    _refresh_folders_ui()
                    resp_chooser.destroy()
                chooser.connect("response", _on_native)
                chooser.show()
            except Exception as e:
                status_lbl.set_markup(f"<span foreground='#f38ba8'>No se pudo abrir el explorador de archivos: {html.escape(str(e))}</span>")

        add_folder_btn.connect("clicked", _browse_folder)
        _refresh_folders_ui()

        # Contextual memory file: created automatically in the first folder
        mem_grp = Adw.PreferencesGroup(
            title="Memoria contextual del workspace",
            description="Archivo Markdown con contexto persistente del proyecto (decisiones, convenciones, notas). Se inyectará al agente en los chats de este workspace."
        )
        mem_info = Adw.ActionRow(
            title="Archivo de memoria contextual (automático)",
            subtitle="Se creará y usará automáticamente CONTEXT.md en la primera carpeta del workspace."
        )
        mem_badge = Gtk.Label(label="CONTEXT.md")
        mem_badge.add_css_class("status-pill")
        mem_info.add_suffix(mem_badge)
        mem_grp.add(mem_info)
        mem_hint = Gtk.Label(
            label="<small>La memoria contextual se genera automáticamente al guardar (o se reutiliza si ya existe) dentro de la carpeta principal del proyecto.</small>",
            use_markup=True, xalign=0, wrap=True
        )
        mem_hint.add_css_class("dim-label")
        hint_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hint_box.set_margin_start(12); hint_box.set_margin_end(12); hint_box.set_margin_bottom(6)
        hint_box.append(mem_hint)
        mem_grp.add(hint_box)
        body.append(mem_grp)

        status_lbl = Gtk.Label(label="", xalign=0, wrap=True)
        status_lbl.add_css_class("dim-label")
        body.append(status_lbl)

        def _save(_btn):
            name = name_row.get_text().strip()
            desc = desc_row.get_text().strip()
            folders = [f.strip() for f in selected_folders if f and str(f).strip()]
            if not name:
                status_lbl.set_markup("<span foreground='#f38ba8'>El nombre del workspace es obligatorio.</span>")
                return
            if not folders:
                status_lbl.set_markup("<span foreground='#f38ba8'>Debes seleccionar al menos una carpeta para el workspace.</span>")
                return

            existing_id = (workspace_data or {}).get("id")
            if existing_id:
                workspace_id = existing_id
            else:
                import re, uuid
                workspace_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")[:40] or f"workspace-{uuid.uuid4().hex[:6]}"

            # normalize folders; contextual memory file is automatic (CONTEXT.md in first folder)
            norm_folders = []
            for f in folders:
                try:
                    p = Path(f).expanduser().resolve()
                    p.mkdir(parents=True, exist_ok=True)
                    norm_folders.append(str(p))
                except Exception as ex:
                    status_lbl.set_markup(f"<span foreground='#f38ba8'>No se pudo preparar la carpeta {html.escape(f)}: {html.escape(str(ex))}</span>")
                    return

            mem_file = str(Path(norm_folders[0]) / "CONTEXT.md")
            try:
                mp = Path(mem_file)
                mp.parent.mkdir(parents=True, exist_ok=True)
                if not mp.exists():
                    mp.write_text(
                        f"# Memoria contextual del workspace: {name}\n\n"
                        "Notas, decisiones, convenciones y contexto persistente del proyecto.\n",
                        encoding="utf-8"
                    )
            except Exception as ex:
                status_lbl.set_markup(f"<span foreground='#f38ba8'>No se pudo crear el archivo de memoria contextual: {html.escape(str(ex))}</span>")
                return

            data = {
                "id": workspace_id,
                "name": name,
                "description": desc,
                "folders": norm_folders,
                "memory_file": mem_file,
            }
            self.config_manager.save_workspace(workspace_id, data)
            self._refresh_workspaces_list()
            win.close()

        save_btn.connect("clicked", _save)
        win.present()

    # -------------------------------------------------------------
    # TAB: AGENTES ESPECIALIZADOS (prompt reutilizable en todos los workspaces)
    # -------------------------------------------------------------
    def _build_agents_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Agentes")
        page.set_icon_name("system-users-symbolic")

        info_grp = Adw.PreferencesGroup()
        info_grp.set_title("Agentes Especializados")
        info_grp.set_description(
            "Crea agentes con prompts/instrucciones especializados. "
            "Los agentes son reutilizables y pueden utilizarse en cualquier workspace."
        )

        header_row = Adw.ActionRow(
            title="Gestión de Agentes",
            subtitle="Cada agente define su rol y prompt; el workspace/proyecto se elige al crear el chat."
        )
        add_btn = Gtk.Button(label="+ Nuevo Agente")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda _b: self._on_edit_agent(None))
        header_row.add_suffix(add_btn)
        info_grp.add(header_row)
        page.add(info_grp)

        self.agents_list_grp = Adw.PreferencesGroup()
        self.agents_list_grp.set_title("Agentes Configurados")
        self.agents_list_grp.set_description("Agentes con prompt especializado disponibles en todos los workspaces.")
        page.add(self.agents_list_grp)

        self._agents_rows_widgets = []
        self.add(page)
        self._refresh_agents_list()

    def _refresh_agents_list(self):
        if not hasattr(self, "agents_list_grp") or self.agents_list_grp is None:
            return
        for w in list(getattr(self, "_agents_rows_widgets", [])):
            try:
                self.agents_list_grp.remove(w)
            except Exception:
                pass
        self._agents_rows_widgets = []

        agents = self.config_manager.get_agents() if hasattr(self.config_manager, "get_agents") else {}
        items = list(agents.values()) if isinstance(agents, dict) else []
        items = [a for a in items if isinstance(a, dict)]
        items.sort(key=lambda a: (a.get("name") or a.get("id") or "").lower())

        if not items:
            empty_row = Adw.ActionRow(
                title="No hay agentes especializados",
                subtitle="Pulsa '+ Nuevo Agente' para definir un prompt especializado reutilizable."
            )
            self.agents_list_grp.add(empty_row)
            self._agents_rows_widgets.append(empty_row)
            return

        for agent in items:
            aid = agent.get("id", "")
            name = agent.get("name") or aid or "Agente"
            desc = agent.get("description") or ""
            prompt = (agent.get("system_prompt") or "").strip()

            row = Adw.ActionRow()
            row.set_title(f"<b>{html.escape(name)}</b>")
            subtitle_parts = []
            if desc:
                subtitle_parts.append(html.escape(desc))
            if prompt:
                preview = prompt.replace("\n", " ")
                if len(preview) > 100:
                    preview = preview[:100].rstrip() + "…"
                subtitle_parts.append(f"Prompt: {html.escape(preview)}")
            row.set_subtitle("\n".join(subtitle_parts) if subtitle_parts else "Sin descripción ni prompt configurado")
            row.set_title_lines(1)
            row.set_subtitle_lines(3)

            new_chat_btn = Gtk.Button(label="Nuevo chat")
            new_chat_btn.add_css_class("flat")
            new_chat_btn.set_tooltip_text("Crear una conversación nueva con este agente (en el workspace activo o general)")
            new_chat_btn.connect("clicked", lambda _b, a=aid: self._on_create_agent_chat(a))
            row.add_suffix(new_chat_btn)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.set_tooltip_text("Editar agente")
            edit_btn.connect("clicked", lambda _b, a=dict(agent): self._on_edit_agent(a))
            row.add_suffix(edit_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Eliminar agente")
            del_btn.connect("clicked", lambda _b, a=aid, n=name: self._on_delete_agent(a, n))
            row.add_suffix(del_btn)

            self.agents_list_grp.add(row)
            self._agents_rows_widgets.append(row)

    def _on_create_agent_chat(self, agent_id: str):
        """Creates a new thread with the selected agent (reusable in any workspace)."""
        try:
            root = self.get_transient_for()
            if root and hasattr(root, "_on_new_thread"):
                root._on_new_thread(agent_id=agent_id)
                root.present()
                return
        except Exception:
            pass
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="Agente listo",
            body="El agente está configurado. Usa el botón 'Nuevo chat' y selecciona este agente; puedes usarlo en cualquier workspace."
        )
        dlg.add_response("ok", "Entendido")
        dlg.present()

    def _on_delete_agent(self, agent_id: str, name: str):
        msg = Adw.MessageDialog(
            transient_for=self,
            heading="¿Eliminar agente?",
            body=f"Se eliminará el agente '{name}'. Las conversaciones existentes conservarán su copia del prompt, pero el agente dejará de estar disponible para nuevos chats."
        )
        msg.add_response("cancel", "Cancelar")
        msg.add_response("delete", "Eliminar")
        msg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _resp(_d, resp):
            if resp == "delete":
                self.config_manager.delete_agent(agent_id)
                self._refresh_agents_list()
        msg.connect("response", _resp)
        msg.present()

    def _on_edit_agent(self, agent_data):
        is_new = not (agent_data and agent_data.get("id"))
        win = Adw.Window()
        win.set_transient_for(self)
        win.set_modal(True)
        win.set_default_size(620, 520)
        win.set_title("Nuevo Agente Especializado" if is_new else "Editar Agente")
        if not Adw.StyleManager.get_default().get_dark():
            win.add_css_class("theme-light")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.set_content(main_box)

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancelar")
        cancel_btn.connect("clicked", lambda *_: win.close())
        header.pack_start(cancel_btn)
        save_btn = Gtk.Button(label="Guardar")
        save_btn.add_css_class("suggested-action")
        header.pack_end(save_btn)
        main_box.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12); body.set_margin_bottom(16)
        body.set_margin_start(16); body.set_margin_end(16)
        scrolled.set_child(body)

        grp = Adw.PreferencesGroup(title="Perfil del Agente (prompt especializado)")
        name_row = Adw.EntryRow(title="Nombre del agente")
        name_row.set_text((agent_data or {}).get("name", ""))
        grp.add(name_row)

        desc_row = Adw.EntryRow(title="Descripción / rol (opcional)")
        desc_row.set_text((agent_data or {}).get("description", ""))
        grp.add(desc_row)

        hint = Gtk.Label(
            label="<small>Los agentes son solo prompts especializados y pueden usarse en cualquier workspace. Las carpetas y memoria de proyecto se configuran en Workspaces.</small>",
            use_markup=True, xalign=0, wrap=True
        )
        hint.add_css_class("dim-label")
        hint_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hint_box.set_margin_start(12); hint_box.set_margin_end(12); hint_box.set_margin_bottom(6)
        hint_box.append(hint)
        grp.add(hint_box)
        body.append(grp)

        prompt_grp = Adw.PreferencesGroup(title="Prompt / Instrucciones específicas")
        prompt_grp.set_description("Define el comportamiento, objetivos y reglas de este agente. Se aplicará como prompt especializado en las conversaciones donde lo uses.")
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_min_content_height(200)
        prompt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        prompt_view = Gtk.TextView()
        prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        prompt_view.set_monospace(True)
        prompt_view.set_top_margin(8); prompt_view.set_bottom_margin(8)
        prompt_view.set_left_margin(8); prompt_view.set_right_margin(8)
        prompt_buf = prompt_view.get_buffer()
        prompt_buf.set_text((agent_data or {}).get("system_prompt", ""))
        prompt_scroll.set_child(prompt_view)
        prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        prompt_box.append(prompt_scroll)
        prompt_row = Adw.ActionRow()
        prompt_row.set_child(prompt_box)
        prompt_grp.add(prompt_row)
        body.append(prompt_grp)

        status_lbl = Gtk.Label(label="", xalign=0, wrap=True)
        status_lbl.add_css_class("dim-label")
        body.append(status_lbl)

        def _save(_btn):
            name = name_row.get_text().strip()
            desc = desc_row.get_text().strip()
            start, end = prompt_buf.get_bounds()
            prompt = prompt_buf.get_text(start, end, True).strip()
            if not name:
                status_lbl.set_markup("<span foreground='#f38ba8'>El nombre del agente es obligatorio.</span>")
                return
            if not prompt:
                status_lbl.set_markup("<span foreground='#f38ba8'>Debes proporcionar un prompt/instrucciones para el agente.</span>")
                return
            existing_id = (agent_data or {}).get("id")
            if existing_id:
                agent_id = existing_id
            else:
                import re, uuid
                agent_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")[:40] or f"agent-{uuid.uuid4().hex[:6]}"
            data = {
                "id": agent_id,
                "name": name,
                "description": desc,
                "system_prompt": prompt,
            }
            self.config_manager.save_agent(agent_id, data)
            self._refresh_agents_list()
            win.close()

        save_btn.connect("clicked", _save)
        win.present()


    # -------------------------------------------------------------
    # TAB: HEARTBEATS Y AUTOMATIZACIONES
    # -------------------------------------------------------------
    def _build_heartbeats_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Heartbeats")
        page.set_icon_name("alarm-symbolic")

        hb_cfg = self.config_manager.get_heartbeat_config()

        # 1. Master Control
        ctrl_grp = Adw.PreferencesGroup()
        ctrl_grp.set_title("Pulso Proactivo del Agente (Heartbeat)")
        ctrl_grp.set_description(
            "El agente despierta de forma periódica en segundo plano para verificar el estado del sistema, "
            "evaluar tareas pendientes y consultar HEARTBEAT.md con herramientas MCP."
        )

        self.shb_enable_row = Adw.SwitchRow(
            title="Activar Pulso Periódico (Heartbeat)",
            subtitle="Despierta automáticamente al agente según el intervalo seleccionado"
        )
        self.shb_enable_row.set_active(hb_cfg.get("enabled", False))
        def _on_enable_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"enabled": val})
            if self.hb_manager:
                self.hb_manager.notify_listeners()
        self.shb_enable_row.connect("notify::active", _on_enable_toggled)
        ctrl_grp.add(self.shb_enable_row)

        # Interval Options
        intervals = [
            (10, "Cada 10 minutos"),
            (15, "Cada 15 minutos"),
            (30, "Cada 30 minutos (Recomendado)"),
            (60, "Cada 1 hora"),
            (120, "Cada 2 horas")
        ]
        self.shb_interval_values = [i[0] for i in intervals]
        interval_labels = [i[1] for i in intervals]
        curr_interval = int(hb_cfg.get("interval_minutes", 30))
        sel_idx = self.shb_interval_values.index(curr_interval) if curr_interval in self.shb_interval_values else 2

        self.shb_interval_combo = Adw.ComboRow(
            title="Intervalo entre Pulsos",
            model=Gtk.StringList.new(interval_labels)
        )
        self.shb_interval_combo.set_selected(sel_idx)
        def _on_interval_changed(combo, _param):
            idx = combo.get_selected()
            if 0 <= idx < len(self.shb_interval_values):
                val = self.shb_interval_values[idx]
                self.config_manager.set_heartbeat_config({"interval_minutes": val})
                if self.hb_manager:
                    self.hb_manager.notify_listeners()
        self.shb_interval_combo.connect("notify::selected", _on_interval_changed)
        ctrl_grp.add(self.shb_interval_combo)

        # Active Hours Filter
        self.shb_active_hours_row = Adw.SwitchRow(
            title="Solo en Horario de Actividad",
            subtitle="Pausa los pulsos durante la noche para evitar interrupciones o consumo innecesario"
        )
        self.shb_active_hours_row.set_active(hb_cfg.get("active_hours_only", True))
        def _on_active_hours_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"active_hours_only": val})
        self.shb_active_hours_row.connect("notify::active", _on_active_hours_toggled)
        ctrl_grp.add(self.shb_active_hours_row)

        # Hours Range
        range_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        range_box.set_margin_top(4)
        range_box.set_margin_bottom(4)

        start_lbl = Gtk.Label(label="Inicio:")
        range_box.append(start_lbl)
        self.shb_start_entry = Gtk.Entry()
        self.shb_start_entry.set_text(hb_cfg.get("active_start", "08:00"))
        self.shb_start_entry.set_max_width_chars(6)
        range_box.append(self.shb_start_entry)

        end_lbl = Gtk.Label(label="Fin:")
        range_box.append(end_lbl)
        self.shb_end_entry = Gtk.Entry()
        self.shb_end_entry.set_text(hb_cfg.get("active_end", "23:00"))
        self.shb_end_entry.set_max_width_chars(6)
        range_box.append(self.shb_end_entry)

        save_hours_btn = Gtk.Button(label="Aplicar Horas")
        save_hours_btn.add_css_class("flat")
        def _on_save_hours(_b):
            sh = self.shb_start_entry.get_text().strip()
            eh = self.shb_end_entry.get_text().strip()
            self.config_manager.set_heartbeat_config({"active_start": sh, "active_end": eh})
            save_hours_btn.set_label("✓ Aplicado")
            GLib.timeout_add(1500, lambda: (save_hours_btn.set_label("Aplicar Horas"), False)[1])
        save_hours_btn.connect("clicked", _on_save_hours)
        range_box.append(save_hours_btn)

        hours_row = Adw.ActionRow(title="Rango Horario Activo (HH:MM)")
        hours_row.set_child(range_box)
        ctrl_grp.add(hours_row)

        # Silent if OK & Alerts
        self.shb_silent_row = Adw.SwitchRow(
            title="Silencio Inteligente",
            subtitle="Si no hay alertas ni novedades (HEARTBEAT_OK), no molesta al usuario"
        )
        self.shb_silent_row.set_active(hb_cfg.get("silent_if_ok", True))
        def _on_silent_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"silent_if_ok": val})
        self.shb_silent_row.connect("notify::active", _on_silent_toggled)
        ctrl_grp.add(self.shb_silent_row)

        self.shb_notif_row = Adw.SwitchRow(
            title="Notificaciones Gráficas de Escritorio",
            subtitle="Muestra aviso emergente con notify-send cuando hay hallazgos o alertas"
        )
        self.shb_notif_row.set_active(hb_cfg.get("desktop_notification", True))
        def _on_notif_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"desktop_notification": val})
        self.shb_notif_row.connect("notify::active", _on_notif_toggled)
        ctrl_grp.add(self.shb_notif_row)

        self.shb_chat_row = Adw.SwitchRow(
            title="Mostrar en el Chat",
            subtitle="Publica el resumen en una tarjeta en el hilo de conversación"
        )
        self.shb_chat_row.set_active(hb_cfg.get("show_in_chat", True))
        def _on_chat_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"show_in_chat": val})
        self.shb_chat_row.connect("notify::active", _on_chat_toggled)
        ctrl_grp.add(self.shb_chat_row)

        self.shb_tts_row = Adw.SwitchRow(
            title="Pronunciar con Voz TTS",
            subtitle="Lee el aviso por voz si hay alertas importantes"
        )
        self.shb_tts_row.set_active(hb_cfg.get("speak_tts", False))
        def _on_tts_toggled(row, _param):
            val = row.get_active()
            self.config_manager.set_heartbeat_config({"speak_tts": val})
        self.shb_tts_row.connect("notify::active", _on_tts_toggled)
        ctrl_grp.add(self.shb_tts_row)

        # Trigger now row
        self.shb_trigger_row = Adw.ActionRow(
            title="Estado del Pulso y Ejecución Inmediata",
            subtitle="Último pulso: Nunca"
        )
        self.shb_status_pill = Gtk.Label(label="IDLE")
        self.shb_status_pill.add_css_class("status-pill")
        self.shb_trigger_row.add_suffix(self.shb_status_pill)

        self.shb_spinner = Gtk.Spinner()
        self.shb_trigger_row.add_suffix(self.shb_spinner)

        self.shb_trigger_btn = Gtk.Button(label="💓 Probar Pulso Ahora")
        self.shb_trigger_btn.add_css_class("suggested-action")
        self.shb_trigger_btn.connect("clicked", lambda _b: self.hb_manager and self.hb_manager.trigger_global_now())
        self.shb_trigger_row.add_suffix(self.shb_trigger_btn)
        ctrl_grp.add(self.shb_trigger_row)

        page.add(ctrl_grp)

        # 2. HEARTBEAT.md Checklist Editor
        md_grp = Adw.PreferencesGroup()
        md_grp.set_title("Lista de Verificación (HEARTBEAT.md)")
        md_grp.set_description(
            "Archivo Markdown con las instrucciones que el agente verifica en cada pulso periódico."
        )

        md_scroll = Gtk.ScrolledWindow()
        md_scroll.set_min_content_height(180)
        md_scroll.set_max_content_height(280)
        md_scroll.add_css_class("chat-input")

        self.shb_md_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.shb_md_textview.get_buffer().set_text(self.memory_manager.get_heartbeat_md())
        md_scroll.set_child(self.shb_md_textview)

        md_row = Adw.ActionRow()
        md_row.set_child(md_scroll)
        md_grp.add(md_row)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_halign(Gtk.Align.END)
        btn_bar.set_margin_top(6)
        btn_bar.set_margin_bottom(6)

        reset_btn = Gtk.Button(label="Restablecer por Defecto")
        reset_btn.add_css_class("flat")
        def _on_reset(_b):
            self.shb_md_textview.get_buffer().set_text(DEFAULT_HEARTBEAT_MD)
            self.memory_manager.save_heartbeat_md(DEFAULT_HEARTBEAT_MD)
            if self.hb_manager:
                self.hb_manager.notify_listeners()
        reset_btn.connect("clicked", _on_reset)
        btn_bar.append(reset_btn)

        save_md_btn = Gtk.Button(label="Guardar HEARTBEAT.md")
        save_md_btn.add_css_class("suggested-action")
        def _on_save_md(_b):
            buf = self.shb_md_textview.get_buffer()
            start, end = buf.get_bounds()
            text = buf.get_text(start, end, True)
            self.memory_manager.save_heartbeat_md(text)
            if self.hb_manager:
                self.hb_manager.notify_listeners()
            save_md_btn.set_label("✓ Guardado")
            GLib.timeout_add(1500, lambda: (save_md_btn.set_label("Guardar HEARTBEAT.md"), False)[1])
        save_md_btn.connect("clicked", _on_save_md)
        btn_bar.append(save_md_btn)

        btn_row = Adw.ActionRow()
        btn_row.set_child(btn_bar)
        md_grp.add(btn_row)
        page.add(md_grp)

        # 3. Individual Automations List
        auto_header_grp = Adw.PreferencesGroup()
        auto_header_grp.set_title("Automatizaciones Programadas")
        auto_header_grp.set_description(
            "Tareas específicas (monitores de sistema, briefings diarios, recordatorios) que el agente ejecuta de forma autónoma."
        )

        auto_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        auto_action_box.set_margin_top(4)
        auto_action_box.set_margin_bottom(4)

        new_auto_btn = Gtk.Button(label="➕ Nueva Automatización")
        new_auto_btn.add_css_class("suggested-action")
        new_auto_btn.connect("clicked", lambda _b: self._open_shb_automation_editor(None))
        auto_action_box.append(new_auto_btn)

        auto_act_row = Adw.ActionRow()
        auto_act_row.set_child(auto_action_box)
        auto_header_grp.add(auto_act_row)
        page.add(auto_header_grp)

        self.shb_auto_list_grp = Adw.PreferencesGroup()
        self.shb_auto_list_grp.set_title("Tareas Activas")
        page.add(self.shb_auto_list_grp)

        # 4. Logs Group
        logs_header_grp = Adw.PreferencesGroup()
        logs_header_grp.set_title("Registro de Ejecuciones")
        logs_header_grp.set_description("Historial de pulsos recientes evaluados en segundo plano.")

        clear_logs_btn = Gtk.Button(label="🗑 Limpiar Registro")
        clear_logs_btn.add_css_class("flat")
        clear_logs_btn.connect("clicked", lambda _b: (self.hb_manager and self.hb_manager.clear_logs(), self._refresh_shb_logs_ui()))
        logs_act_row = Adw.ActionRow()
        logs_act_row.add_suffix(clear_logs_btn)
        logs_header_grp.add(logs_act_row)
        page.add(logs_header_grp)

        self.shb_logs_grp = Adw.PreferencesGroup()
        self.shb_logs_grp.set_title("Últimos Pulsos")
        page.add(self.shb_logs_grp)

        self.add(page)
        self._refresh_settings_hb_ui()

    def _open_shb_automation_editor(self, auto_data: Optional[Dict[str, Any]]):
        if not self.hb_manager:
            return
        dlg = AutomationEditorDialog(
            parent=self,
            hb_manager=self.hb_manager,
            automation_data=auto_data,
            on_saved=lambda _a: self._refresh_shb_automations_list()
        )
        dlg.present()

    def _refresh_shb_status_ui(self):
        if not hasattr(self, "shb_status_pill"):
            return
        hb_cfg = self.config_manager.get_heartbeat_config()
        status = hb_cfg.get("last_status", "idle")
        last_run = hb_cfg.get("last_run")
        is_running = (self.hb_manager and self.hb_manager.is_task_running("global_heartbeat")) or status == "running"

        if is_running:
            self.shb_spinner.start()
            self.shb_trigger_btn.set_sensitive(False)
            self.shb_status_pill.set_text("⏳ EJECUTANDO")
            self.shb_status_pill.set_css_classes(["status-pill-connecting"])
            self.shb_trigger_row.set_subtitle("Ejecutando verificación proactiva en segundo plano...")
        else:
            self.shb_spinner.stop()
            self.shb_trigger_btn.set_sensitive(True)

            if status == "silent_ok":
                self.shb_status_pill.set_text("✓ OK (SILENCIOSO)")
                self.shb_status_pill.set_css_classes(["status-pill-connected"])
            elif status == "ok":
                self.shb_status_pill.set_text("● REPORTE")
                self.shb_status_pill.set_css_classes(["status-pill-connected"])
            elif status == "error":
                self.shb_status_pill.set_text("❌ ERROR")
                self.shb_status_pill.set_css_classes(["status-pill-error"])
            else:
                self.shb_status_pill.set_text("○ EN ESPERA")
                self.shb_status_pill.set_css_classes(["status-pill-disconnected"])

            if last_run:
                try:
                    dt = datetime.datetime.fromisoformat(last_run)
                    sub = f"Último pulso: {dt.strftime('%H:%M:%S (%d/%m)')}"
                    res_snippet = hb_cfg.get("last_result", "").strip().replace("\n", " ")
                    if res_snippet:
                        sub += f" · {res_snippet[:60]}..."
                    self.shb_trigger_row.set_subtitle(sub)
                except Exception:
                    self.shb_trigger_row.set_subtitle(f"Último pulso: {last_run}")
            else:
                self.shb_trigger_row.set_subtitle("Aún no se ha ejecutado ningún pulso")

    def _refresh_shb_automations_list(self):
        if not hasattr(self, "shb_auto_list_grp"):
            return

        for r in list(self._shb_auto_rows):
            try:
                self.shb_auto_list_grp.remove(r)
            except Exception:
                pass
        self._shb_auto_rows.clear()

        automations = self.config_manager.get_automations()

        if not automations:
            empty_row = Adw.ActionRow(
                title="No hay automatizaciones configuradas",
                subtitle="Haz clic en '➕ Nueva Automatización' para crear una tarea periódica."
            )
            self.shb_auto_list_grp.add(empty_row)
            self._shb_auto_rows.append(empty_row)
            return

        for auto in automations:
            aid = auto.get("id", "")
            name = auto.get("name", "Automatización")
            is_enabled = auto.get("enabled", False)
            badge_str = format_schedule_badge(auto)
            last_run = auto.get("last_run", "")
            last_status = auto.get("last_status", "idle")
            is_running = (self.hb_manager and self.hb_manager.is_task_running(aid)) or last_status == "running"

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
            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(is_enabled)
            def _on_sw_toggled(_sw, _param, target_id=aid):
                if self.hb_manager:
                    self.hb_manager.update_automation(target_id, {"enabled": _sw.get_active()})
            sw.connect("notify::active", _on_sw_toggled)
            row.add_suffix(sw)

            run_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
            run_btn.add_css_class("flat")
            run_btn.set_tooltip_text("Ejecutar ahora")
            run_btn.set_sensitive(not is_running)
            run_btn.connect("clicked", lambda _b, target_id=aid: self.hb_manager and self.hb_manager.trigger_automation_now(target_id))
            row.add_suffix(run_btn)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.set_tooltip_text("Editar automatización")
            edit_btn.connect("clicked", lambda _b, a_data=auto: self._open_shb_automation_editor(a_data))
            row.add_suffix(edit_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text("Eliminar automatización")
            def _on_del(_b, target_id=aid):
                if self.hb_manager:
                    self.hb_manager.delete_automation(target_id)
                self._refresh_shb_automations_list()
            del_btn.connect("clicked", _on_del)
            row.add_suffix(del_btn)

            self.shb_auto_list_grp.add(row)
            self._shb_auto_rows.append(row)

    def _refresh_shb_logs_ui(self):
        if not hasattr(self, "shb_logs_grp"):
            return

        for r in list(self._shb_logs_rows):
            try:
                self.shb_logs_grp.remove(r)
            except Exception:
                pass
        self._shb_logs_rows.clear()

        logs = self.hb_manager.get_logs() if self.hb_manager else []
        if not logs:
            empty_row = Adw.ActionRow(
                title="No hay registros de ejecución",
                subtitle="Los pulsos evaluados se registrarán aquí con su duración y herramientas usadas."
            )
            self.shb_logs_grp.add(empty_row)
            self._shb_logs_rows.append(empty_row)
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
            self.shb_logs_grp.add(row)
            self._shb_logs_rows.append(row)

    def _build_audio_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Voz y Audio")
        page.set_icon_name("audio-input-microphone-symbolic")

        whisper_grp = Adw.PreferencesGroup()
        whisper_grp.set_title("Transcripción de Voz (faster-whisper)")
        whisper_grp.set_description("Configura el modelo y motor de reconocimiento de voz para el botón de micrófono del chat.")

        # Model size dropdown/combo
        audio_cfg = self.config_manager.get_audio_config()
        current_model = audio_cfg.get("whisper_model", "tiny")

        models = [
            ("tiny", "tiny — Ultraligero (~75 MB, alta velocidad en CPU)"),
            ("base", "base — Ligero y balanceado (~140 MB, óptimo para español)"),
            ("small", "small — Precisión avanzada (~460 MB)")
        ]
        model_names = [m[1] for m in models]
        model_keys = [m[0] for m in models]

        active_idx = 0
        if current_model in model_keys:
            active_idx = model_keys.index(current_model)

        model_row = Adw.ComboRow(
            title="Tamaño del Modelo faster-whisper",
            subtitle="Se descargará automáticamente al transcribir por primera vez"
        )
        model_row.set_model(Gtk.StringList.new(model_names))
        model_row.set_selected(active_idx)

        def _on_model_selected(_row, _param):
            idx = model_row.get_selected()
            if 0 <= idx < len(model_keys):
                selected = model_keys[idx]
                self.config_manager.set_whisper_model(selected)
                self.on_audio_changed()

        model_row.connect("notify::selected", _on_model_selected)
        whisper_grp.add(model_row)

        # Language row
        current_lang = audio_cfg.get("language")
        langs = [
            (None, "Auto-detectar idioma (Español, Inglés, etc.)"),
            ("es", "Español (es)"),
            ("en", "Inglés (en)")
        ]
        lang_names = [l[1] for l in langs]
        lang_keys = [l[0] for l in langs]
        active_lang_idx = 0
        if current_lang in lang_keys:
            active_lang_idx = lang_keys.index(current_lang)

        lang_row = Adw.ComboRow(title="Idioma de Transcripción", subtitle="Idioma preferido para el reconocimiento")
        lang_row.set_model(Gtk.StringList.new(lang_names))
        lang_row.set_selected(active_lang_idx)

        def _on_lang_selected(_row, _param):
            idx = lang_row.get_selected()
            if 0 <= idx < len(lang_keys):
                selected_l = lang_keys[idx]
                self.config_manager.data["audio"]["language"] = selected_l
                self.config_manager.save()
                self.on_audio_changed()

        lang_row.connect("notify::selected", _on_lang_selected)
        whisper_grp.add(lang_row)

        # Backend status row
        backend_row = Adw.ActionRow(title="Backend de Captura de Audio")
        import shutil
        if shutil.which("pw-record"):
            status_text = "pw-record (PipeWire nativo)"
            status_cls = "status-pill"
        elif shutil.which("ffmpeg"):
            status_text = "ffmpeg (PulseAudio / PipeWire)"
            status_cls = "status-pill"
        elif shutil.which("arecord"):
            status_text = "arecord (ALSA)"
            status_cls = "status-pill"
        else:
            status_text = "No detectado"
            status_cls = "tool-badge-error"

        backend_badge = Gtk.Label(label=status_text)
        backend_badge.add_css_class(status_cls)
        backend_row.add_suffix(backend_badge)
        whisper_grp.add(backend_row)

        page.add(whisper_grp)

        # -------------------------------------------------------------
        # SECCIÓN 1.5: ACTIVACIÓN POR VOZ (WAKE WORD - VOSK)
        # -------------------------------------------------------------
        wake_grp = Adw.PreferencesGroup()
        wake_grp.set_title("Activación por Voz (Wake Word)")
        wake_grp.set_description(
            "Escucha en segundo plano con Vosk (bajo consumo, 100% local) para abrir la ventana "
            "automáticamente al decir la frase de activación."
        )

        wake_enable_row = Adw.SwitchRow(
            title="Activar con palabra clave",
            subtitle="Abre y enfoca la ventana cuando digas la frase configurada"
        )
        wake_enable_row.set_active(audio_cfg.get("wake_word_enabled", False))

        def _on_wake_toggled(row, _param):
            self.config_manager.get_audio_config()["wake_word_enabled"] = row.get_active()
            self.config_manager.save()
            self.on_audio_changed()

        wake_enable_row.connect("notify::active", _on_wake_toggled)
        wake_grp.add(wake_enable_row)

        wake_phrase_row = Adw.EntryRow(title="Frase de activación")
        wake_phrase_row.set_text(audio_cfg.get("wake_word_phrase", "okay asistente"))
        save_wake_btn = Gtk.Button(label="Guardar")
        save_wake_btn.add_css_class("suggested-action")
        save_wake_btn.set_valign(Gtk.Align.CENTER)

        def _on_save_wake_phrase(_btn):
            phrase = wake_phrase_row.get_text().strip() or "okay asistente"
            wake_phrase_row.set_text(phrase)
            self.config_manager.get_audio_config()["wake_word_phrase"] = phrase
            self.config_manager.save()
            self.on_audio_changed()
            save_wake_btn.set_label("✓ Guardado")
            GLib.timeout_add(1500, lambda: (save_wake_btn.set_label("Guardar"), False)[1])

        save_wake_btn.connect("clicked", _on_save_wake_phrase)
        wake_phrase_row.add_suffix(save_wake_btn)
        wake_grp.add(wake_phrase_row)

        # Acoustic neural-embedding enrollment (no transcription)
        from pequeno_asistente.audio.acoustic_wake import AcousticWakeMatcher, record_pcm_clip
        acoustic_matcher = AcousticWakeMatcher()

        def _acoustic_subtitle() -> str:
            counts = acoustic_matcher.template_counts()
            return (
                f"Muestras — OK Asistente: {counts.get('wake', 0)} | "
                f"Ocultar: {counts.get('ocultar ventana', 0)} | "
                f"Nuevo chat: {counts.get('nuevo chat', 0)}"
            )

        acoustic_row = Adw.ActionRow(
            title="Entrenamiento acústico directo (sin transcripción)",
            subtitle=_acoustic_subtitle(),
        )

        def _enroll_label(label: str, btn: Gtk.Button, default_text: str):
            btn.set_sensitive(False)
            btn.set_label("Grabando (2.5s)...")

            def _worker():
                try:
                    pcm = record_pcm_clip(duration_seconds=2.5)
                    count = acoustic_matcher.enroll_pcm(label, pcm)
                    def _done():
                        acoustic_row.set_subtitle(_acoustic_subtitle())
                        btn.set_label(f"✓ ({count})")
                        btn.set_sensitive(True)
                        self.on_audio_changed()
                        GLib.timeout_add(1500, lambda: (btn.set_label(default_text), False)[1])
                        return False
                    GLib.idle_add(_done)
                except Exception as e:
                    def _err():
                        acoustic_row.set_subtitle(f"Error: {e}")
                        btn.set_label(default_text)
                        btn.set_sensitive(True)
                        return False
                    GLib.idle_add(_err)

            threading.Thread(target=_worker, daemon=True).start()

        btn_enroll_wake = Gtk.Button(label="Grabar OK Asistente")
        btn_enroll_wake.set_valign(Gtk.Align.CENTER)
        btn_enroll_wake.add_css_class("suggested-action")
        btn_enroll_wake.connect("clicked", lambda b: _enroll_label("wake", b, "Grabar OK Asistente"))

        btn_enroll_hide = Gtk.Button(label="Grabar Ocultar")
        btn_enroll_hide.set_valign(Gtk.Align.CENTER)
        btn_enroll_hide.connect("clicked", lambda b: _enroll_label("ocultar ventana", b, "Grabar Ocultar"))

        btn_enroll_new_chat = Gtk.Button(label="Grabar Nuevo chat")
        btn_enroll_new_chat.set_valign(Gtk.Align.CENTER)
        btn_enroll_new_chat.connect("clicked", lambda b: _enroll_label("nuevo chat", b, "Grabar Nuevo chat"))

        btn_clear_acoustic = Gtk.Button(label="Borrar")
        btn_clear_acoustic.set_valign(Gtk.Align.CENTER)
        btn_clear_acoustic.add_css_class("destructive-action")

        def _on_clear_acoustic(_b):
            acoustic_matcher.clear_templates()
            acoustic_row.set_subtitle(_acoustic_subtitle())
            self.on_audio_changed()

        btn_clear_acoustic.connect("clicked", _on_clear_acoustic)
        acoustic_row.add_suffix(btn_enroll_wake)
        acoustic_row.add_suffix(btn_enroll_hide)
        acoustic_row.add_suffix(btn_enroll_new_chat)
        acoustic_row.add_suffix(btn_clear_acoustic)
        wake_grp.add(acoustic_row)

        page.add(wake_grp)

        # -------------------------------------------------------------
        # SECCIÓN 1B: COMANDOS DE VOZ PERSONALIZADOS (SIN TRANSCRIPCIÓN)
        # -------------------------------------------------------------
        custom_cmd_grp = Adw.PreferencesGroup()
        custom_cmd_grp.set_title("Comandos de voz personalizados (sin transcripción)")
        custom_cmd_grp.set_description(
            "Crea comandos grabados con tu voz para ejecutar apps, scripts o mensajes fijos directamente al decirlos."
        )

        cmd_name_row = Adw.EntryRow(title="Nombre del comando (ej. abre firefox)")
        cmd_type_row = Adw.ComboRow(title="Tipo de acción")
        cmd_type_model = Gtk.StringList.new(["Ejecutar comando / app", "Enviar mensaje al agente"])
        cmd_type_row.set_model(cmd_type_model)
        cmd_val_row = Adw.EntryRow(title="Comando o mensaje a ejecutar")

        add_cmd_btn = Gtk.Button(label="Añadir comando")
        add_cmd_btn.add_css_class("suggested-action")
        add_cmd_btn.set_valign(Gtk.Align.CENTER)
        cmd_val_row.add_suffix(add_cmd_btn)

        custom_cmd_grp.add(cmd_name_row)
        custom_cmd_grp.add(cmd_type_row)
        custom_cmd_grp.add(cmd_val_row)

        custom_cmd_rows: list = []

        def _refresh_custom_cmd_rows():
            for r in custom_cmd_rows:
                custom_cmd_grp.remove(r)
            custom_cmd_rows.clear()
            counts = acoustic_matcher.load_templates()
            entries = list(self.config_manager.get_audio_config().get("custom_voice_commands", []))
            for idx, entry in enumerate(entries):
                cname = (entry.get("name") or "").lower().strip()
                atype = entry.get("action_type", "shell")
                aval = entry.get("action_value", "")
                if not cname:
                    continue
                tipo_lbl = "App/Shell" if atype == "shell" else "Mensaje"
                sample_cnt = counts.get(cname, 0)
                row = Adw.ActionRow(
                    title=cname,
                    subtitle=f"{tipo_lbl}: {aval} • Muestras grabadas: {sample_cnt}",
                )

                rec_btn = Gtk.Button(label="Grabar (2.5s)")
                rec_btn.set_valign(Gtk.Align.CENTER)
                rec_btn.add_css_class("suggested-action")

                def _on_rec_custom(b, label=cname, r=row, t_lbl=tipo_lbl, val=aval):
                    b.set_sensitive(False)
                    b.set_label("Grabando...")

                    def _worker():
                        try:
                            pcm = record_pcm_clip(duration_seconds=2.5)
                            cnt = acoustic_matcher.enroll_pcm(label, pcm)
                            def _done():
                                r.set_subtitle(f"{t_lbl}: {val} • Muestras grabadas: {cnt}")
                                b.set_label(f"✓ ({cnt})")
                                b.set_sensitive(True)
                                self.on_audio_changed()
                                GLib.timeout_add(1500, lambda: (b.set_label("Grabar (2.5s)"), False)[1])
                                return False
                            GLib.idle_add(_done)
                        except Exception as e:
                            def _err():
                                r.set_subtitle(f"Error: {e}")
                                b.set_label("Grabar (2.5s)")
                                b.set_sensitive(True)
                                return False
                            GLib.idle_add(_err)

                    threading.Thread(target=_worker, daemon=True).start()

                rec_btn.connect("clicked", _on_rec_custom)

                del_btn = Gtk.Button(label="Eliminar")
                del_btn.set_valign(Gtk.Align.CENTER)
                del_btn.add_css_class("destructive-action")

                def _on_del_custom(_b, remove_idx=idx, label=cname):
                    cur = list(self.config_manager.get_audio_config().get("custom_voice_commands", []))
                    if 0 <= remove_idx < len(cur):
                        cur.pop(remove_idx)
                        self.config_manager.get_audio_config()["custom_voice_commands"] = cur
                        self.config_manager.save()
                    acoustic_matcher.clear_templates(label)
                    self.on_audio_changed()
                    _refresh_custom_cmd_rows()

                del_btn.connect("clicked", _on_del_custom)
                row.add_suffix(rec_btn)
                row.add_suffix(del_btn)
                custom_cmd_grp.add(row)
                custom_cmd_rows.append(row)

        def _on_add_custom_cmd(_btn):
            name = " ".join(cmd_name_row.get_text().lower().strip().split())
            val = cmd_val_row.get_text().strip()
            if not name or not val:
                return
            atype = "prompt" if cmd_type_row.get_selected() == 1 else "shell"
            cur = list(self.config_manager.get_audio_config().get("custom_voice_commands", []))
            cur = [c for c in cur if (c.get("name") or "").lower().strip() != name]
            cur.append({"name": name, "action_type": atype, "action_value": val})
            self.config_manager.get_audio_config()["custom_voice_commands"] = cur
            self.config_manager.save()
            cmd_name_row.set_text("")
            cmd_val_row.set_text("")
            self.on_audio_changed()
            _refresh_custom_cmd_rows()

        add_cmd_btn.connect("clicked", _on_add_custom_cmd)
        _refresh_custom_cmd_rows()
        page.add(custom_cmd_grp)

        # -------------------------------------------------------------
        # SECCIÓN 2: PROVEEDOR PRINCIPAL DE VOZ (TTS)
        # -------------------------------------------------------------
        tts_provider_grp = Adw.PreferencesGroup()
        tts_provider_grp.set_title("Motor de Síntesis de Voz (TTS)")
        tts_provider_grp.set_description(
            "Escoge si deseas usar ElevenLabs (hiperrealista) o Kokoro TTS (local gratuito). "
            "Si se agota tu cuota de ElevenLabs, el asistente pasará automáticamente a Kokoro."
        )

        kokoro_cfg = self.config_manager.get_kokoro_config()
        el_cfg = self.config_manager.get_elevenlabs_config()

        providers = [
            ("kokoro", "Kokoro TTS / Local (Gratis, Docker localhost:8011)"),
            ("elevenlabs", "ElevenLabs (Máxima Calidad con Respaldo Automático)"),
            ("system", "Sistema Linux / spd-say (Nativo, Sin Docker ni Internet)")
        ]
        provider_keys = [p[0] for p in providers]
        provider_labels = [p[1] for p in providers]

        active_provider = self.config_manager.get_tts_provider()
        prov_idx = 0
        if active_provider in provider_keys:
            prov_idx = provider_keys.index(active_provider)

        provider_row = Adw.ComboRow(
            title="Motor de Voz Activo",
            subtitle="Proveedor que genera la voz del asistente"
        )
        provider_row.set_model(Gtk.StringList.new(provider_labels))
        provider_row.set_selected(prov_idx)

        def _on_provider_changed(row, _param):
            idx = row.get_selected()
            if 0 <= idx < len(provider_keys):
                self.config_manager.set_tts_provider(provider_keys[idx])
                self.on_audio_changed()

        provider_row.connect("notify::selected", _on_provider_changed)
        tts_provider_grp.add(provider_row)

        # Switches globales
        speak_on_voice_row = Adw.SwitchRow(
            title="Responder directo con voz al recibir audio",
            subtitle="Cuando dictas con el micrófono, el asistente responde hablando directamente"
        )
        speak_on_voice_row.set_active(kokoro_cfg.get("speak_on_voice_input", True))
        def _on_speak_voice_toggled(row, _param):
            self.config_manager.set_kokoro_config({"speak_on_voice_input": row.get_active()})
            self.on_audio_changed()
        speak_on_voice_row.connect("notify::active", _on_speak_voice_toggled)
        tts_provider_grp.add(speak_on_voice_row)

        auto_send_row = Adw.SwitchRow(
            title="Auto-enviar al finalizar la grabación",
            subtitle="Envía automáticamente el mensaje al detener la grabación con el micrófono"
        )
        auto_send_row.set_active(audio_cfg.get("auto_send_on_voice", True))
        def _on_auto_send_toggled(row, _param):
            self.config_manager.data["audio"]["auto_send_on_voice"] = row.get_active()
            self.config_manager.save()
            self.on_audio_changed()
        auto_send_row.connect("notify::active", _on_auto_send_toggled)
        tts_provider_grp.add(auto_send_row)

        page.add(tts_provider_grp)

        # -------------------------------------------------------------
        # SECCIÓN 3: CONFIGURACIÓN DE ELEVENLABS
        # -------------------------------------------------------------
        el_grp = Adw.PreferencesGroup()
        el_grp.set_title("Configuración de ElevenLabs")
        el_grp.set_description("Audio hiperrealista con respiración y entonación humana natural (https://elevenlabs.io).")

        # Switch: Respaldo automático a Kokoro
        fallback_row = Adw.SwitchRow(
            title="Respaldo automático a Kokoro (Jorge)",
            subtitle="Al agotarse los créditos del plan gratuito o haber error de cuota, usa Kokoro sin interrumpir la voz"
        )
        fallback_row.set_active(el_cfg.get("fallback_to_kokoro", True))
        def _on_fallback_toggled(row, _param):
            self.config_manager.set_elevenlabs_config({"fallback_to_kokoro": row.get_active()})
            self.on_audio_changed()
        fallback_row.connect("notify::active", _on_fallback_toggled)
        el_grp.add(fallback_row)

        # API Key de ElevenLabs
        key_row = Adw.PasswordEntryRow(title="API Key de ElevenLabs")
        key_row.set_text(el_cfg.get("api_key", ""))
        save_key_btn = Gtk.Button(label="Guardar")
        save_key_btn.add_css_class("suggested-action")
        def _on_save_key(_btn):
            k = key_row.get_text().strip()
            self.config_manager.set_elevenlabs_config({"api_key": k})
            self.on_audio_changed()
            save_key_btn.set_label("✓ Guardado")
            GLib.timeout_add(1500, lambda: (save_key_btn.set_label("Guardar"), False)[1])
        save_key_btn.connect("clicked", _on_save_key)
        key_row.add_suffix(save_key_btn)
        el_grp.add(key_row)

        # Selector de Voces de ElevenLabs
        el_voices = [
            ("ErXwobaYiN019PkySvjV", "Antoni (Masculino - Excelente en Español - Recomendada)"),
            ("JBFqnCBsd6RMkjVDRZzb", "George / Jorge (Masculino - Cálido y Natural)"),
            ("21m00Tcm4TlvDq8ikWAM", "Rachel (Femenino - Calma y Clara)"),
            ("pNInz6obpgDQGcFmaJgB", "Adam (Masculino - Profundo y Cálido)"),
            ("TxGEqnHWrfWFTfGW9XjX", "Josh (Masculino - Joven y Natural)"),
            ("VR6AewLTigWG4xSOukaG", "Arnold (Masculino - Claro y Preciso)"),
            ("EXAVITQu4vr4xnSDxMaL", "Bella / Sarah (Femenino - Suave y Dulce)"),
            ("AZnzlk1XvdvUeBnXmlld", "Domi (Femenino - Firme y Expresiva)"),
            ("MF3mGyEYCl7XYWbV9V6O", "Elli (Femenino - Amable y Calurosa)"),
            ("custom", "Otra (Personalizada por Voice ID)")
        ]
        el_voice_keys = [v[0] for v in el_voices]
        el_voice_labels = [v[1] for v in el_voices]

        curr_el_voice = el_cfg.get("voice_id", "ErXwobaYiN019PkySvjV")
        el_voice_idx = 0
        if curr_el_voice in el_voice_keys:
            el_voice_idx = el_voice_keys.index(curr_el_voice)
        else:
            el_voice_idx = el_voice_keys.index("custom")

        el_voice_row = Adw.ComboRow(
            title="Voz de ElevenLabs",
            subtitle="Voz utilizada para la síntesis de ElevenLabs"
        )
        el_voice_row.set_model(Gtk.StringList.new(el_voice_labels))
        el_voice_row.set_selected(el_voice_idx)

        # Entrada para Voice ID personalizado
        custom_voice_row = Adw.EntryRow(title="Voice ID Personalizado")
        custom_voice_row.set_text(curr_el_voice)
        save_custom_voice_btn = Gtk.Button(label="Guardar")
        save_custom_voice_btn.add_css_class("suggested-action")
        def _on_save_custom_voice(_btn):
            vid = custom_voice_row.get_text().strip()
            if vid:
                self.config_manager.set_elevenlabs_config({"voice_id": vid})
                self.on_audio_changed()
                save_custom_voice_btn.set_label("✓ Guardado")
                GLib.timeout_add(1500, lambda: (save_custom_voice_btn.set_label("Guardar"), False)[1])
        save_custom_voice_btn.connect("clicked", _on_save_custom_voice)
        custom_voice_row.add_suffix(save_custom_voice_btn)
        custom_voice_row.set_visible(el_voice_idx == el_voice_keys.index("custom"))

        def _on_el_voice_changed(row, _param):
            idx = row.get_selected()
            if 0 <= idx < len(el_voice_keys):
                selected_key = el_voice_keys[idx]
                is_custom = selected_key == "custom"
                custom_voice_row.set_visible(is_custom)
                if not is_custom:
                    custom_voice_row.set_text(selected_key)
                    self.config_manager.set_elevenlabs_config({"voice_id": selected_key})
                    self.on_audio_changed()

        el_voice_row.connect("notify::selected", _on_el_voice_changed)
        el_grp.add(el_voice_row)
        el_grp.add(custom_voice_row)

        # Selector de Modelo de ElevenLabs
        el_models = [
            ("eleven_multilingual_v2", "eleven_multilingual_v2 (Máxima calidad y entonación en español)"),
            ("eleven_flash_v2_5", "eleven_flash_v2_5 (Ultra baja latencia ~75ms y menor consumo de créditos)"),
            ("eleven_turbo_v2_5", "eleven_turbo_v2_5 (Baja latencia y gran calidad)")
        ]
        el_model_keys = [m[0] for m in el_models]
        el_model_labels = [m[1] for m in el_models]

        curr_model = el_cfg.get("model_id", "eleven_multilingual_v2")
        el_model_idx = 0
        if curr_model in el_model_keys:
            el_model_idx = el_model_keys.index(curr_model)

        el_model_row = Adw.ComboRow(
            title="Modelo de ElevenLabs",
            subtitle="Modelo de red neuronal para la síntesis"
        )
        el_model_row.set_model(Gtk.StringList.new(el_model_labels))
        el_model_row.set_selected(el_model_idx)

        def _on_el_model_changed(row, _param):
            idx = row.get_selected()
            if 0 <= idx < len(el_model_keys):
                self.config_manager.set_elevenlabs_config({"model_id": el_model_keys[idx]})
                self.on_audio_changed()

        el_model_row.connect("notify::selected", _on_el_model_changed)
        el_grp.add(el_model_row)

        page.add(el_grp)

        # -------------------------------------------------------------
        # SECCIÓN 4: CONFIGURACIÓN DE KOKORO TTS (LOCAL)
        # -------------------------------------------------------------
        kokoro_grp = Adw.PreferencesGroup()
        kokoro_grp.set_title("Configuración de Kokoro TTS (Local)")
        kokoro_grp.set_description("Servidor local en Docker (puerto 8011). También actúa como respaldo automático de ElevenLabs.")

        # URL del contenedor Docker
        url_entry = Adw.EntryRow(title="URL del Servidor Kokoro TTS")
        url_entry.set_text(kokoro_cfg.get("url", "http://localhost:8011"))
        save_url_btn = Gtk.Button(label="Guardar")
        save_url_btn.add_css_class("suggested-action")
        def _on_save_url(_btn):
            new_u = url_entry.get_text().strip().rstrip("/")
            if new_u:
                self.config_manager.set_kokoro_config({"url": new_u})
                self.on_audio_changed()
                save_url_btn.set_label("✓ Guardado")
                GLib.timeout_add(1500, lambda: (save_url_btn.set_label("Guardar"), False)[1])
        save_url_btn.connect("clicked", _on_save_url)
        url_entry.add_suffix(save_url_btn)
        kokoro_grp.add(url_entry)

        # Selector de Voz de Kokoro
        known_voices = [
            ("es-MX-JorgeNeural", "es-MX-JorgeNeural (México Masculino - Recomendada)"),
            ("ef_dora", "ef_dora (Español Femenino)"),
            ("em_alex", "em_alex (Español Masculino)"),
            ("es-MX-DaliaNeural", "es-MX-DaliaNeural (México Femenino)"),
            ("es-ES-AlvaroNeural", "es-ES-AlvaroNeural (España Masculino)"),
            ("af_heart", "af_heart (Inglés Femenino)"),
            ("am_adam", "am_adam (Inglés Masculino)")
        ]
        voice_keys = [v[0] for v in known_voices]
        voice_labels = [v[1] for v in known_voices]

        curr_voice = kokoro_cfg.get("voice", "es-MX-JorgeNeural")
        voice_idx = 0
        if curr_voice in voice_keys:
            voice_idx = voice_keys.index(curr_voice)

        voice_row = Adw.ComboRow(
            title="Voz de Kokoro",
            subtitle="Voz utilizada para la síntesis local en español"
        )
        voice_row.set_model(Gtk.StringList.new(voice_labels))
        voice_row.set_selected(voice_idx)

        def _on_voice_changed(row, _param):
            idx = row.get_selected()
            if 0 <= idx < len(voice_keys):
                chosen_voice = voice_keys[idx]
                self.config_manager.set_kokoro_config({"voice": chosen_voice})
                self.on_audio_changed()

        voice_row.connect("notify::selected", _on_voice_changed)
        kokoro_grp.add(voice_row)
        page.add(kokoro_grp)

        # -------------------------------------------------------------
        # SECCIÓN 5: PRUEBA DE AUDIO Y CONEXIÓN
        # -------------------------------------------------------------
        test_grp = Adw.PreferencesGroup()
        test_grp.set_title("Prueba de Audio y Conexión")
        test_grp.set_description("Comprueba la voz activa. Si ElevenLabs no tiene cuota, se escuchará Kokoro como respaldo.")

        test_row = Adw.ActionRow(
            title="Probar Síntesis de Voz",
            subtitle="Genera una frase de prueba con el motor activo configurado"
        )

        test_spinner = Gtk.Spinner()
        test_row.add_suffix(test_spinner)

        test_btn = Gtk.Button(label="🔊 Probar Voz")
        test_btn.add_css_class("flat")
        
        def _on_test_clicked(_btn):
            test_spinner.start()
            test_btn.set_sensitive(False)

            from pequeno_asistente.audio.tts import UnifiedTTS
            audio_c = self.config_manager.get_audio_config()
            k_c = self.config_manager.get_kokoro_config()
            e_c = self.config_manager.get_elevenlabs_config()

            tts_client = UnifiedTTS(
                provider=audio_c.get("tts_provider", "kokoro"),
                kokoro_url=k_c.get("url", "http://localhost:8011"),
                kokoro_voice=k_c.get("voice", "es-MX-JorgeNeural"),
                kokoro_speed=float(k_c.get("speed", 1.0)),
                kokoro_lang=k_c.get("lang", "es"),
                elevenlabs_api_key=e_c.get("api_key", ""),
                elevenlabs_voice_id=e_c.get("voice_id", "ErXwobaYiN019PkySvjV"),
                elevenlabs_model_id=e_c.get("model_id", "eleven_multilingual_v2"),
                fallback_to_kokoro=e_c.get("fallback_to_kokoro", True),
                enabled=True
            )

            def finish_test():
                def _ui():
                    test_spinner.stop()
                    test_btn.set_sensitive(True)
                    if tts_client.last_fallback_occurred:
                        test_row.set_subtitle("✓ Se utilizó Kokoro como respaldo (ElevenLabs sin cuota o clave).")
                    else:
                        test_row.set_subtitle(f"✓ Audio reproducido correctamente con {tts_client.last_used_provider.capitalize()}.")
                    return False
                GLib.idle_add(_ui)

            test_text = (
                "¡Hola! Soy tu asistente de voz. La síntesis de audio está configurada y lista para hablar."
            )
            tts_client.speak_async(test_text, on_finish=finish_test)

        test_btn.connect("clicked", _on_test_clicked)
        test_row.add_suffix(test_btn)
        test_grp.add(test_row)
        page.add(test_grp)
        self.add(page)

    def _on_temp_changed(self, spin_btn):
        self.config_manager.data["llm"]["temperature"] = round(spin_btn.get_value(), 2)
        self.config_manager.save()

    def _on_ollama_base_changed(self, new_url: str):
        self.config_manager.data["llm"]["ollama_base_url"] = new_url.strip()
        self.config_manager.save()
