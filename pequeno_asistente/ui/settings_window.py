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
from pequeno_asistente.agent.providers import PROVIDERS, PROVIDER_ENV_VARS
from pequeno_asistente.mcp_client.catalog import MCP_CATALOG, CATALOG_CATEGORIES
from pequeno_asistente.mcp_client.registry import RegistryClient, DEFAULT_REGISTRIES
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.model_fetcher import fetch_provider_models, validate_api_key, get_cached_models

class SettingsWindow(Adw.PreferencesWindow):
    def __init__(
        self,
        config_manager: ConfigManager,
        memory_manager: MemoryManager,
        on_sync_needed: Optional[Callable[[], None]] = None,
        on_model_changed: Optional[Callable[[str], None]] = None
    ):
        super().__init__()
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.on_sync_needed = on_sync_needed or (lambda: None)
        self.on_model_changed = on_model_changed or (lambda m: None)

        self.set_title("Configuración - Pequeño Asistente")
        self.set_default_size(780, 680)
        self.set_modal(True)

        # State for model explorer
        self.current_provider_models: List[Dict[str, Any]] = []
        self.search_filter_text = ""
        self.registry_client = RegistryClient()

        # Build pages
        self._build_models_page()
        self._build_api_keys_page()
        self._build_mcp_catalog_page()
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

        # 3. Local & Configured Servers Group
        self.local_grp = Adw.PreferencesGroup()
        self.local_grp.set_title("Servidores Instalados y Configurados")
        self.local_grp.set_description("Servidores activos en tu configuración local. Activa o desactiva con los interruptores o añade nuevos.")

        add_custom_btn = Gtk.Button(label="+ Añadir Servidor Local")
        add_custom_btn.add_css_class("suggested-action")
        add_custom_btn.connect("clicked", self._open_add_custom_mcp_dialog)
        
        local_header_row = Adw.ActionRow(title="Servidores Configurados", subtitle="Comandos locales o URLs remotas del asistente")
        local_header_row.add_suffix(add_custom_btn)
        self.local_grp.add(local_header_row)
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
            return

        for sid, scfg in servers.items():
            row = self._create_local_server_row(sid, scfg)
            self.local_grp.add(row)
            self._local_server_rows.append(row)

    def _create_local_server_row(self, server_id: str, scfg: Dict[str, Any]) -> Adw.ActionRow:
        row = Adw.ActionRow()
        name = scfg.get("name") or server_id
        cmd = scfg.get("command", "")
        args_str = " ".join(str(a) for a in scfg.get("args", []))
        transport = scfg.get("transport", "stdio")
        is_disabled = scfg.get("disabled", False)

        icon = "🧩"
        if server_id in MCP_CATALOG:
            icon = MCP_CATALOG[server_id].get("icon", "🧩")

        row.set_title(html.escape(f"{icon} {name} ({server_id})"))
        cmd_preview = f"{cmd} {args_str}".strip()
        row.set_subtitle(html.escape(f"Comando: {cmd_preview} | Transporte: {transport}"))

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
        cfg_btn.set_tooltip_text("Editar comando y variables")
        cfg_btn.connect("clicked", lambda _b: self._open_edit_mcp_dialog(server_id, scfg))

        # Switch toggle
        switch = Gtk.Switch()
        switch.set_active(not is_disabled)
        switch.set_valign(Gtk.Align.CENTER)

        def on_switch_toggled(sw, _param):
            active = sw.get_active()
            self.config_manager.toggle_mcp_server(server_id, active)
            self.on_sync_needed()

        switch.connect("notify::active", on_switch_toggled)

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
        cfg = {
            "name": server_info.get("name", sid),
            "command": server_info.get("command", "npx"),
            "args": server_info.get("args", []),
            "transport": server_info.get("transport", "stdio"),
            "env": server_info.get("env", {}),
            "disabled": False
        }
        self.config_manager.set_mcp_server(sid, cfg)
        self.on_sync_needed()
        self._refresh_mcp_servers_list()

    def _create_mcp_row(self, server_id: str, info: Dict[str, Any]) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(html.escape(f"{info.get('icon', '🔧')} {info['name']}"))
        row.set_subtitle(html.escape(info.get("description", "")))

        active_servers = self.config_manager.get_mcp_servers()
        is_active = (server_id in active_servers) and not active_servers[server_id].get("disabled", False)

        # Switch toggle
        switch = Gtk.Switch()
        switch.set_active(is_active)
        switch.set_valign(Gtk.Align.CENTER)

        def on_switch_toggled(sw, _param):
            active = sw.get_active()
            if active:
                cfg_entry = {
                    "name": info["name"],
                    "command": info["command"],
                    "args": info["args"],
                    "transport": info.get("transport", "stdio"),
                    "env": info.get("env", {}),
                    "disabled": False
                }
                self.config_manager.set_mcp_server(server_id, cfg_entry)
            else:
                self.config_manager.toggle_mcp_server(server_id, False)
            self.on_sync_needed()
            self._refresh_mcp_servers_list()

        switch.connect("notify::active", on_switch_toggled)

        # Config button
        cfg_btn = Gtk.Button(icon_name="document-properties-symbolic")
        cfg_btn.add_css_class("flat")
        cfg_btn.set_tooltip_text("Editar comando y variables")
        cfg_btn.connect("clicked", lambda _b: self._open_edit_mcp_dialog(server_id, info))

        row.add_suffix(cfg_btn)
        row.add_suffix(switch)
        return row

    def _open_edit_mcp_dialog(self, server_id: str, info: Dict[str, Any]):
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title(f"Configurar {info['name']}")
        dialog.set_default_size(500, 420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        active_cfg = self.config_manager.get_mcp_servers().get(server_id, info)

        cmd_entry = Adw.EntryRow(title="Comando")
        cmd_entry.set_text(active_cfg.get("command", ""))
        
        args_entry = Adw.EntryRow(title="Argumentos (separados por espacio)")
        args_entry.set_text(" ".join(active_cfg.get("args", [])))

        env_entry = Adw.EntryRow(title="Variables de Entorno (JSON)")
        env_entry.set_text(json.dumps(active_cfg.get("env", {})))

        pref_grp = Adw.PreferencesGroup()
        pref_grp.add(cmd_entry)
        pref_grp.add(args_entry)
        pref_grp.add(env_entry)
        box.append(pref_grp)

        save_btn = Gtk.Button(label="Guardar Configuración")
        save_btn.add_css_class("suggested-action")
        
        def save_and_close(_b):
            try:
                env_dict = json.loads(env_entry.get_text()) if env_entry.get_text() else {}
            except Exception:
                env_dict = {}

            args_list = [a for a in args_entry.get_text().split(" ") if a.strip()]
            new_cfg = {
                "name": info["name"],
                "command": cmd_entry.get_text().strip(),
                "args": args_list,
                "transport": active_cfg.get("transport", "stdio"),
                "env": env_dict,
                "disabled": active_cfg.get("disabled", False)
            }
            self.config_manager.set_mcp_server(server_id, new_cfg)
            self.on_sync_needed()
            self._refresh_mcp_servers_list()
            dialog.close()

        save_btn.connect("clicked", save_and_close)
        box.append(save_btn)
        dialog.set_content(box)
        dialog.present()

    def _open_add_custom_mcp_dialog(self, _btn):
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("Añadir Servidor MCP (Local / Personalizado)")
        dialog.set_default_size(580, 520)

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
            label="Pega la configuración completa en formato JSON (compatible con Claude Desktop <i>{\"mcpServers\": ...}</i> o definición de servidor directa):",
            use_markup=True,
            wrap=True,
            xalign=0
        )
        json_box.append(json_info)

        json_scrolled = Gtk.ScrolledWindow()
        json_scrolled.set_min_content_height(220)
        json_scrolled.set_max_content_height(300)
        json_scrolled.add_css_class("chat-input")

        json_textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.NONE)
        json_textview.set_monospace(True)
        sample_json = (
            '{\n'
            '  "mcpServers": {\n'
            '    "mi-servidor": {\n'
            '      "command": "uvx",\n'
            '      "args": ["weather-mcp"],\n'
            '      "env": {}\n'
            '    }\n'
            '  }\n'
            '}'
        )
        json_textview.get_buffer().set_text(sample_json)
        json_scrolled.set_child(json_textview)
        json_box.append(json_scrolled)

        json_status_label = Gtk.Label(xalign=0, wrap=True)
        json_status_label.add_css_class("system-notice")
        json_box.append(json_status_label)

        json_btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Helper to parse JSON
        def extract_servers_from_json(text: str):
            data = json.loads(text)
            extracted = []
            if "mcpServers" in data and isinstance(data["mcpServers"], dict):
                for sid, scfg in data["mcpServers"].items():
                    extracted.append((sid, scfg))
            elif isinstance(data, dict):
                if "command" in data:
                    sid = data.get("id") or data.get("name") or "custom-mcp"
                    extracted.append((sid, data))
                else:
                    for sid, scfg in data.items():
                        if isinstance(scfg, dict) and "command" in scfg:
                            extracted.append((sid, scfg))
            elif isinstance(data, list):
                for idx, item in enumerate(data):
                    if isinstance(item, dict) and "command" in item:
                        sid = item.get("id") or item.get("name") or f"custom-{idx}"
                        extracted.append((sid, item))
            return extracted

        import_json_btn = Gtk.Button(label="📥 Instalar desde JSON")
        import_json_btn.add_css_class("suggested-action")

        def import_from_json(_b):
            buf = json_textview.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            try:
                servers = extract_servers_from_json(text)
                if not servers:
                    json_status_label.set_markup("<span foreground='#f38ba8'>❌ No se encontró ningún servidor con 'command' en el JSON.</span>")
                    return

                installed_count = 0
                for sid, scfg in servers:
                    clean_id = "".join(c if c.isalnum() or c == "-" else "_" for c in sid.lower())
                    cfg = {
                        "name": scfg.get("name") or sid,
                        "command": scfg.get("command", ""),
                        "args": scfg.get("args", []),
                        "transport": scfg.get("transport") or ("sse" if str(scfg.get("command", "")).startswith("http") else "stdio"),
                        "env": scfg.get("env", {}),
                        "disabled": False
                    }
                    self.config_manager.set_mcp_server(clean_id, cfg)
                    installed_count += 1

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

        id_entry = Adw.EntryRow(title="ID único (ej: mi-herramienta)")
        name_entry = Adw.EntryRow(title="Nombre visible (ej: Mi Servidor MCP)")
        cmd_entry = Adw.EntryRow(title="Comando o URL (ej: uvx, npx, python, http://...)")
        args_entry = Adw.EntryRow(title="Argumentos (ej: -y @modelcontextprotocol/server-git)")
        env_entry = Adw.EntryRow(title="Variables de Entorno (JSON: {\"KEY\": \"VAL\"})")

        grp.add(id_entry)
        grp.add(name_entry)
        grp.add(cmd_entry)
        grp.add(args_entry)
        grp.add(env_entry)
        form_box.append(grp)

        form_save_btn = Gtk.Button(label="Añadir e Iniciar")
        form_save_btn.add_css_class("suggested-action")

        def add_from_form(_b):
            sid = id_entry.get_text().strip()
            if not sid:
                return
            sname = name_entry.get_text().strip() or sid
            cmd = cmd_entry.get_text().strip()
            args = [a for a in args_entry.get_text().split(" ") if a.strip()]
            try:
                env_dict = json.loads(env_entry.get_text()) if env_entry.get_text() else {}
            except Exception:
                env_dict = {}

            transport = "sse" if cmd.startswith("http://") or cmd.startswith("https://") else "stdio"

            self.config_manager.set_mcp_server(sid, {
                "name": sname,
                "command": cmd,
                "args": args,
                "transport": transport,
                "env": env_dict,
                "disabled": False
            })
            self.on_sync_needed()
            self._refresh_mcp_servers_list()
            dialog.close()

        form_save_btn.connect("clicked", add_from_form)
        form_box.append(form_save_btn)

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
                    cmd_entry.set_text(scfg.get("command", ""))
                    args_entry.set_text(" ".join(str(a) for a in scfg.get("args", [])))
                    env_entry.set_text(json.dumps(scfg.get("env", {})))
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

        self.add(page)

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

    # -------------------------------------------------------------
    # TAB 5: INTEGRACIÓN DE ESCRITORIO & WAYBAR
    # -------------------------------------------------------------
    def _build_desktop_integration_page(self):
        page = Adw.PreferencesPage()
        page.set_title("Escritorio y Waybar")
        page.set_icon_name("input-keyboard-symbolic")

        hypr_grp = Adw.PreferencesGroup()
        hypr_grp.set_title("Atajo en Hyprland")
        hypr_grp.set_description("Añade la siguiente línea en ~/.config/hypr/hyprland.conf para alternar el asistente con Super+A:")
        
        hypr_entry = Adw.EntryRow(title="Regla de atajo")
        hypr_entry.set_text("bind = $mainMod, A, exec, /home/gato/.local/bin/pequeno-asistente --toggle")
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

    def _on_temp_changed(self, spin_btn):
        self.config_manager.data["llm"]["temperature"] = round(spin_btn.get_value(), 2)
        self.config_manager.save()

    def _on_ollama_base_changed(self, new_url: str):
        self.config_manager.data["llm"]["ollama_base_url"] = new_url.strip()
        self.config_manager.save()
