"""
Configuration Manager for Pequeño Asistente.
Handles persistent JSON settings in ~/.config/pequeno-asistente/config.json
and imports existing environment/KogniTerm keys seamlessly.
"""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from pequeno_asistente.agent.providers import PROVIDER_ENV_VARS, PROVIDERS
from pequeno_asistente.mcp_client.catalog import MCP_CATALOG
from pequeno_asistente.mcp_client.registry import DEFAULT_REGISTRIES

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
    "mcp_servers": {
        "hypruse": {
            "name": "Hyprland Desktop",
            "command": "uvx",
            "args": ["hypruse"],
            "transport": "stdio",
            "env": {
                "HYPRUSE_SCREENSHOT_MODE": "image",
                "HYPRUSE_READONLY": "0"
            },
            "disabled": False
        },
        "time": {
            "name": "Reloj & Zonas Horarias",
            "command": "uvx",
            "args": ["mcp-server-time"],
            "transport": "stdio",
            "env": {},
            "disabled": False
        },
        "fetch": {
            "name": "Navegador Web / Fetch",
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "transport": "stdio",
            "env": {},
            "disabled": False
        }
    },
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
            for k in ("llm", "ui", "memory", "audio", "mcp_servers", "mcp_registries"):
                if k not in data:
                    data[k] = json.loads(json.dumps(DEFAULT_CONFIG.get(k, {})))

            # Ensure basic non-destructive defaults exist inside sections
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
                        for s_name, s_info in kdata["mcpServers"].items():
                            if s_name not in cfg["mcp_servers"]:
                                cfg["mcp_servers"][s_name] = {
                                    "name": s_name,
                                    "command": s_info.get("command", ""),
                                    "args": s_info.get("args", []),
                                    "env": s_info.get("env", {}),
                                    "transport": s_info.get("transport", "stdio"),
                                    "disabled": s_info.get("disabled", False)
                                }
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
        self.save()

    def get_api_key(self, provider: str) -> str:
        key = self.data["llm"]["api_keys"].get(provider, "")
        if not key:
            env_var = PROVIDER_ENV_VARS.get(provider)
            if env_var:
                key = os.getenv(env_var, "")
        return key

    def get_active_model(self) -> str:
        return self.data["llm"].get("model", "openrouter/meta-llama/llama-3.3-70b-instruct")

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
        self.data["mcp_servers"][server_id] = server_config
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
