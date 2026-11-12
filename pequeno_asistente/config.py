"""
Configuration Manager for Pequeño Asistente.
Handles persistent JSON settings in ~/.config/pequeno-asistente/config.json
and imports existing environment/KogniTerm keys seamlessly.
"""
import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from pequeno_asistente.agent.providers import PROVIDER_ENV_VARS, PROVIDERS
from pequeno_asistente.mcp_client.catalog import MCP_CATALOG
from pequeno_asistente.mcp_client.registry import DEFAULT_REGISTRIES

DEFAULT_MCP_SERVERS: Dict[str, Any] = {
    "pequeno-core": {
        "name": "Pequeño Asistente Core (Por Defecto)",
        "command": sys.executable,
        "args": ["-m", "pequeno_asistente.mcp_client.default_server"],
        "env": {},
        "transport": "stdio",
        "disabled": False,
        "builtin": True
    }
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "provider": "openrouter",
        "model": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "api_keys": {},
        "ollama_base_url": "http://localhost:11434",
        "temperature": 0.7,
        "max_tokens": 4096
    },
    "mcp_registries": [
        {
            "id": "smithery",
            "name": "Smithery AI Registry (10k+ servidores)",
            "url": "https://api.smithery.ai/servers",
            "enabled": True
        },
        {
            "id": "official",
            "name": "Official Anthropic MCP Registry",
            "url": "https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md",
            "enabled": True
        }
    ],
    "mcp_servers": dict(DEFAULT_MCP_SERVERS),
    "ui": {
        "window_width": 440,
        "window_height": 590,
        "always_on_top": True,
        "theme": "dark"
    },
    "memory": {
        "auto_log": True,
        "inject_memory": True
    },
    "audio": {
        "whisper_model": "tiny",
        "language": None,
        "device": "cpu",
        "compute_type": "int8",
        "auto_send_on_voice": True,
        "tts_provider": "kokoro",
        "kokoro": {
            "enabled": True,
            "url": "http://localhost:8011",
            "voice": "es-MX-JorgeNeural",
            "speed": 1.0,
            "lang": "es",
            "speak_on_voice_input": True
        },
        "elevenlabs": {
            "enabled": True,
            "api_key": "",
            "voice_id": "ErXwobaYiN019PkySvjV",
            "model_id": "eleven_multilingual_v2",
            "stability": 0.5,
            "similarity_boost": 0.75,
            "fallback_to_kokoro": True
        }
    },
    "heartbeat": {
        "enabled": False,
        "interval_minutes": 30,
        "active_hours_only": True,
        "active_start": "08:00",
        "active_end": "23:00",
        "silent_if_ok": True,
        "desktop_notification": True,
        "show_in_chat": True,
        "speak_tts": False,
        "last_run": None,
        "last_status": "idle",
        "last_result": ""
    },
    "automations": [],
    "skills": {
        "disabled_skills": [],
        "extra_paths": []
    },
    "agents": {},
    "workspaces": {},
    "server": {
        "host": "0.0.0.0",
        "port": 8770,
        "api_key": "",
        "cors_origins": ["*"]
    }
}

class ConfigManager:
    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.path = Path(config_path)
        else:
            self.path = Path.home() / ".config" / "pequeno-asistente" / "config.json"
        
        self.config_dir = self.path.parent
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            self._import_kogniterm_and_env(cfg)
            self._save_raw(cfg)
            return cfg

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Ensure top-level sections exist without overwriting user values
            for k in ("llm", "ui", "memory", "audio", "mcp_servers", "mcp_registries", "heartbeat", "automations", "skills", "agents", "workspaces", "server"):
                if k not in data:
                    data[k] = json.loads(json.dumps(DEFAULT_CONFIG.get(k, {} if k not in ("automations",) else [])))

            # Ensure built-in default MCP server ('pequeno-core') is present in mcp_servers
            if not isinstance(data.get("mcp_servers"), dict):
                data["mcp_servers"] = json.loads(json.dumps(DEFAULT_MCP_SERVERS))
            elif "pequeno-core" not in data["mcp_servers"]:
                data["mcp_servers"]["pequeno-core"] = json.loads(json.dumps(DEFAULT_MCP_SERVERS["pequeno-core"]))

            if "server" not in data:
                data["server"] = json.loads(json.dumps(DEFAULT_CONFIG["server"]))
            else:
                for subk, subv in DEFAULT_CONFIG["server"].items():
                    if subk not in data["server"]:
                        data["server"][subk] = subv

            if "skills" not in data:
                data["skills"] = json.loads(json.dumps(DEFAULT_CONFIG["skills"]))
            else:
                for subk, subv in DEFAULT_CONFIG["skills"].items():
                    if subk not in data["skills"]:
                        data["skills"][subk] = subv

            if "agents" not in data or not isinstance(data.get("agents"), dict):
                data["agents"] = {}
            if "workspaces" not in data or not isinstance(data.get("workspaces"), dict):
                data["workspaces"] = {}

            # Ensure basic non-destructive defaults exist inside sections
            for subk, subv in DEFAULT_CONFIG["heartbeat"].items():
                if subk not in data["heartbeat"]:
                    data["heartbeat"][subk] = subv
            for subk, subv in DEFAULT_CONFIG["ui"].items():
                if subk not in data["ui"]:
                    data["ui"][subk] = subv

            for subk, subv in DEFAULT_CONFIG["memory"].items():
                if subk not in data["memory"]:
                    data["memory"][subk] = subv

            if "audio" not in data:
                data["audio"] = json.loads(json.dumps(DEFAULT_CONFIG.get("audio", {})))
            else:
                for subk, subv in DEFAULT_CONFIG["audio"].items():
                    if subk not in data["audio"]:
                        data["audio"][subk] = subv
                if "kokoro" not in data["audio"]:
                    data["audio"]["kokoro"] = json.loads(json.dumps(DEFAULT_CONFIG["audio"]["kokoro"]))
                else:
                    for ksub, kval in DEFAULT_CONFIG["audio"]["kokoro"].items():
                        if ksub not in data["audio"]["kokoro"]:
                            data["audio"]["kokoro"][ksub] = kval

            if "api_keys" not in data["llm"]:
                data["llm"]["api_keys"] = {}

            # Import missing API keys from environment only if empty
            for prov, env_name in PROVIDER_ENV_VARS.items():
                val = os.getenv(env_name)
                if val and not data["llm"]["api_keys"].get(prov):
                    data["llm"]["api_keys"][prov] = val

            return data
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")
            return json.loads(json.dumps(DEFAULT_CONFIG))

    def _import_kogniterm_and_env(self, cfg: Dict[str, Any]):
        """Imports existing keys and models from environment or KogniTerm."""
        # 1. Environment variables
        for prov, env_name in PROVIDER_ENV_VARS.items():
            val = os.getenv(env_name)
            if val and not cfg["llm"]["api_keys"].get(prov):
                cfg["llm"]["api_keys"][prov] = val

        # 2. Check ~/.kogniterm/config.json
        kogni_cfg_path = Path.home() / ".kogniterm" / "config.json"
        if kogni_cfg_path.exists():
            try:
                with open(kogni_cfg_path, "r", encoding="utf-8") as f:
                    kdata = json.load(f)
                    
                    # Model
                    if "default_model" in kdata and not cfg["llm"].get("model_user_override"):
                        kmodel = kdata["default_model"]
                        cfg["llm"]["model"] = kmodel
                        if "/" in kmodel:
                            prov = kmodel.split("/")[0]
                            if prov in PROVIDERS:
                                cfg["llm"]["provider"] = prov
                                
                    # MCP Servers from KogniTerm
                    if "mcpServers" in kdata and isinstance(kdata["mcpServers"], dict):
                        from pequeno_asistente.mcp_client.manager import normalize_mcp_server_config
                        for s_name, s_info in kdata["mcpServers"].items():
                            if s_name not in cfg["mcp_servers"] and isinstance(s_info, dict):
                                cfg["mcp_servers"][s_name] = normalize_mcp_server_config(s_info, fallback_name=s_name)
            except Exception:
                pass

        # 3. Check ~/.kogniterm/api_token
        kogni_token_path = Path.home() / ".kogniterm" / "api_token"
        if kogni_token_path.exists():
            try:
                token = kogni_token_path.read_text(encoding="utf-8").strip()
                if token and not cfg["llm"]["api_keys"].get("antigravity"):
                    cfg["llm"]["api_keys"]["antigravity"] = token
            except Exception:
                pass

    def _save_raw(self, data: Dict[str, Any]):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def save(self):
        self._save_raw(self.data)

    # Accessors
    def get_ui_config(self) -> Dict[str, Any]:
        if "ui" not in self.data or not isinstance(self.data["ui"], dict):
            self.data["ui"] = json.loads(json.dumps(DEFAULT_CONFIG["ui"]))
        return self.data["ui"]

    def get_theme(self) -> str:
        theme = self.get_ui_config().get("theme", "dark")
        if theme not in ("dark", "light", "system"):
            return "dark"
        return theme

    def set_theme(self, theme: str):
        if theme not in ("dark", "light", "system"):
            theme = "dark"
        self.get_ui_config()["theme"] = theme
        self.save()

    def get_llm_config(self) -> Dict[str, Any]:
        return self.data.get("llm", {})

    def set_llm_provider(self, provider: str, model: Optional[str] = None):
        self.data["llm"]["provider"] = provider
        if model:
            self.data["llm"]["model"] = model
            self.data["llm"]["model_user_override"] = True
        elif provider in PROVIDERS:
            self.data["llm"]["model"] = PROVIDERS[provider]["default_model"]
        self.save()

    def set_api_key(self, provider: str, key: str):
        self.data["llm"]["api_keys"][provider] = key
        # Also set in active environment for litellm
        env_var = PROVIDER_ENV_VARS.get(provider)
        if env_var:
            os.environ[env_var] = key
        if provider == "vercel":
            os.environ["AI_GATEWAY_API_KEY"] = key
        self.save()

    def get_api_key(self, provider: str) -> str:
        key = self.data["llm"]["api_keys"].get(provider, "")
        if not key:
            env_var = PROVIDER_ENV_VARS.get(provider)
            if env_var:
                key = os.getenv(env_var, "")
            if not key and provider in ("vercel", "vercel_ai_gateway"):
                key = os.getenv("AI_GATEWAY_API_KEY", "") or os.getenv("VERCEL_API_KEY", "")
        return key

    def get_active_model(self) -> str:
        return self.data["llm"].get("model", "openrouter/meta-llama/llama-3.3-70b-instruct")

    def get_active_provider(self) -> str:
        return self.data["llm"].get("provider", "openrouter")

    def set_active_model(self, model: str):
        self.data["llm"]["model"] = model
        self.data["llm"]["model_user_override"] = True
        self.save()

    def set_active_provider(self, provider: str):
        self.data["llm"]["provider"] = provider
        self.save()

    def get_mcp_servers(self) -> Dict[str, Any]:
        return self.data.get("mcp_servers", {})

    def get_mcp_registries(self) -> List[Dict[str, Any]]:
        if "mcp_registries" not in self.data:
            self.data["mcp_registries"] = list(DEFAULT_REGISTRIES)
            self.save()
        return self.data.get("mcp_registries", [])

    def add_mcp_registry(self, name: str, url: str) -> str:
        registries = self.get_mcp_registries()
        reg_id = "reg-" + "".join(c for c in name.lower() if c.isalnum() or c == "-")[:16]
        registries.append({
            "id": reg_id,
            "name": name,
            "url": url,
            "enabled": True
        })
        self.data["mcp_registries"] = registries
        self.save()
        return reg_id

    def remove_mcp_registry(self, registry_id: str):
        registries = self.get_mcp_registries()
        self.data["mcp_registries"] = [r for r in registries if r.get("id") != registry_id]
        self.save()

    def set_mcp_server(self, server_id: str, server_config: Dict[str, Any]):
        from pequeno_asistente.mcp_client.manager import normalize_mcp_server_config
        self.data["mcp_servers"][server_id] = normalize_mcp_server_config(server_config, fallback_name=server_id)
        self.save()

    def toggle_mcp_server(self, server_id: str, enabled: bool):
        if server_id in self.data["mcp_servers"]:
            self.data["mcp_servers"][server_id]["disabled"] = not enabled
            self.save()

    def remove_mcp_server(self, server_id: str):
        if server_id in self.data["mcp_servers"]:
            del self.data["mcp_servers"][server_id]
            self.save()

    def apply_env_keys(self):
        """Inject configured API keys into os.environ for LiteLLM."""
        for prov, key in self.data["llm"].get("api_keys", {}).items():
            if key:
                env_var = PROVIDER_ENV_VARS.get(prov)
                if env_var:
                    os.environ[env_var] = key
        
        # Ollama base URL if set
        base_url = self.data["llm"].get("ollama_base_url")
        if base_url:
            os.environ["OLLAMA_API_BASE"] = base_url

    def get_audio_config(self) -> Dict[str, Any]:
        if "audio" not in self.data:
            self.data["audio"] = json.loads(json.dumps(DEFAULT_CONFIG.get("audio", {})))
        return self.data["audio"]

    def set_whisper_model(self, model: str):
        if "audio" not in self.data:
            self.data["audio"] = json.loads(json.dumps(DEFAULT_CONFIG.get("audio", {})))
        self.data["audio"]["whisper_model"] = model
        self.save()

    def get_tts_provider(self) -> str:
        return self.get_audio_config().get("tts_provider", "kokoro")

    def set_tts_provider(self, provider: str):
        self.get_audio_config()["tts_provider"] = provider
        self.save()

    def get_kokoro_config(self) -> Dict[str, Any]:
        audio_cfg = self.get_audio_config()
        if "kokoro" not in audio_cfg:
            audio_cfg["kokoro"] = json.loads(json.dumps(DEFAULT_CONFIG["audio"]["kokoro"]))
        return audio_cfg["kokoro"]

    def set_kokoro_config(self, updates: Dict[str, Any]):
        cfg = self.get_kokoro_config()
        cfg.update(updates)
        self.save()

    def get_elevenlabs_config(self) -> Dict[str, Any]:
        audio_cfg = self.get_audio_config()
        if "elevenlabs" not in audio_cfg:
            audio_cfg["elevenlabs"] = json.loads(json.dumps(DEFAULT_CONFIG["audio"]["elevenlabs"]))
        return audio_cfg["elevenlabs"]

    def set_elevenlabs_config(self, updates: Dict[str, Any]):
        cfg = self.get_elevenlabs_config()
        cfg.update(updates)
        self.save()

    # Heartbeat & Automations Accessors
    def get_heartbeat_config(self) -> Dict[str, Any]:
        if "heartbeat" not in self.data:
            self.data["heartbeat"] = json.loads(json.dumps(DEFAULT_CONFIG["heartbeat"]))
        return self.data["heartbeat"]

    def set_heartbeat_config(self, updates: Dict[str, Any]):
        cfg = self.get_heartbeat_config()
        cfg.update(updates)
        self.save()

    def get_automations(self) -> List[Dict[str, Any]]:
        if "automations" not in self.data or not isinstance(self.data["automations"], list):
            self.data["automations"] = []
        return self.data["automations"]

    def save_automations(self, automations: List[Dict[str, Any]]):
        self.data["automations"] = automations
        self.save()

    def add_automation(self, auto_dict: Dict[str, Any]) -> Dict[str, Any]:
        automations = self.get_automations()
        automations.append(auto_dict)
        self.save()
        return auto_dict

    def update_automation(self, auto_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        automations = self.get_automations()
        for item in automations:
            if item.get("id") == auto_id:
                item.update(updates)
                self.save()
                return item
        return None

    def delete_automation(self, auto_id: str) -> bool:
        automations = self.get_automations()
        before = len(automations)
        self.data["automations"] = [a for a in automations if a.get("id") != auto_id]
        if len(self.data["automations"]) != before:
            self.save()
            return True
        return False

    def get_skills_config(self) -> Dict[str, Any]:
        if "skills" not in self.data or not isinstance(self.data["skills"], dict):
            self.data["skills"] = {"disabled_skills": [], "extra_paths": []}
        return self.data["skills"]

    def get_disabled_skills(self) -> List[str]:
        return list(self.get_skills_config().get("disabled_skills", []))

    def set_skill_disabled(self, skill_id: str, disabled: bool):
        cfg = self.get_skills_config()
        disabled_set = set(cfg.get("disabled_skills", []))
        if disabled:
            disabled_set.add(skill_id)
        else:
            disabled_set.discard(skill_id)
        cfg["disabled_skills"] = sorted(list(disabled_set))
        self.save()

    # Specialized Agents Accessors (prompt-only profiles, reusable across workspaces)
    def get_agents(self) -> Dict[str, Any]:
        if "agents" not in self.data or not isinstance(self.data["agents"], dict):
            self.data["agents"] = {}
        return self.data["agents"]

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self.get_agents().get(agent_id)

    def save_agent(self, agent_id: str, agent_data: Dict[str, Any]):
        agents = self.get_agents()
        agent_data = dict(agent_data or {})
        agent_data["id"] = agent_id
        # Agents are prompt-specialized and reusable across workspaces (no workspace binding)
        agent_data.pop("workspace_path", None)
        agents[agent_id] = agent_data
        self.save()

    def delete_agent(self, agent_id: str) -> bool:
        agents = self.get_agents()
        if agent_id in agents:
            del agents[agent_id]
            self.save()
            return True
        return False

    # Workspaces Accessors (project/folders + contextual memory per workspace)
    def get_workspaces(self) -> Dict[str, Any]:
        if "workspaces" not in self.data or not isinstance(self.data["workspaces"], dict):
            self.data["workspaces"] = {}
        return self.data["workspaces"]

    def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return self.get_workspaces().get(workspace_id)

    def save_workspace(self, workspace_id: str, workspace_data: Dict[str, Any]):
        workspaces = self.get_workspaces()
        workspace_data = dict(workspace_data or {})
        workspace_data["id"] = workspace_id
        workspaces[workspace_id] = workspace_data
        self.save()

    def delete_workspace(self, workspace_id: str) -> bool:
        workspaces = self.get_workspaces()
        if workspace_id in workspaces:
            del workspaces[workspace_id]
            self.save()
            return True
        return False

    def get_server_config(self) -> Dict[str, Any]:
        server_cfg = self.data.get("server")
        if not isinstance(server_cfg, dict):
            server_cfg = dict(DEFAULT_CONFIG["server"])
            self.data["server"] = server_cfg
        return server_cfg

    def set_server_config(self, host: Optional[str] = None, port: Optional[int] = None, api_key: Optional[str] = None):
        cfg = self.get_server_config()
        if host is not None:
            cfg["host"] = host
        if port is not None:
            cfg["port"] = int(port)
        if api_key is not None:
            cfg["api_key"] = api_key
        self.save()

