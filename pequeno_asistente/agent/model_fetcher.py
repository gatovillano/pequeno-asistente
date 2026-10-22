"""
Model Fetcher and API Key Validator for Pequeño Asistente.
Dynamically fetches models directly from provider APIs and tests API keys.
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import httpx

from pequeno_asistente.agent.providers import PROVIDERS

logger = logging.getLogger("pequeno_asistente.models")

CACHE_DIR = Path.home() / ".config" / "pequeno-asistente" / "cache"

def _get_cache_path(provider: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"models_{provider}.json"

def get_cached_models(provider: str) -> List[Dict[str, Any]]:
    p = _get_cache_path(provider)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass
    return []

def save_cached_models(provider: str, models: List[Dict[str, Any]]):
    p = _get_cache_path(provider)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(models, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not cache models for {provider}: {e}")


async def fetch_provider_models(
    provider: str,
    api_key: str = "",
    base_url: str = "",
    force_refresh: bool = False
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Fetches the real-time list of models directly from the provider's API.
    Returns (models_list, error_message_if_any).
    """
    if not force_refresh:
        cached = get_cached_models(provider)
        if cached:
            return cached, None

    models: List[Dict[str, Any]] = []
    error_msg: Optional[str] = None

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            # 1. OPENROUTER
            if provider == "openrouter":
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for m in data:
                        mid = m.get("id", "")
                        name = m.get("name", mid)
                        ctx = m.get("context_length", 0)
                        ctx_str = f"{int(ctx/1024)}k ctx" if ctx else ""
                        
                        pricing = m.get("pricing", {})
                        price_str = ""
                        if pricing:
                            p_in = float(pricing.get("prompt", 0)) * 1_000_000
                            p_out = float(pricing.get("completion", 0)) * 1_000_000
                            if p_in == 0 and p_out == 0:
                                price_str = "Gratis"
                            else:
                                price_str = f"${p_in:.2f}/M in, ${p_out:.2f}/M out"

                        desc_parts = [p for p in [ctx_str, price_str] if p]
                        desc = " • ".join(desc_parts)

                        models.append({
                            "id": f"openrouter/{mid}",
                            "raw_id": mid,
                            "name": name,
                            "provider": "openrouter",
                            "description": desc,
                            "context_length": ctx
                        })
                    models.sort(key=lambda x: x["name"].lower())
                else:
                    error_msg = f"HTTP {resp.status_code} desde OpenRouter: {resp.text[:120]}"

            # 2. GOOGLE GEMINI
            elif provider == "google":
                if not api_key:
                    return _fallback_models(provider), "Se requiere GOOGLE_API_KEY para consultar la API de Gemini."
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    raw_models = resp.json().get("models", [])
                    for m in raw_models:
                        # name looks like "models/gemini-2.5-flash"
                        full_name = m.get("name", "")
                        short_name = full_name.replace("models/", "")
                        # Filter to gemini generation models
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" in methods:
                            disp_name = m.get("displayName", short_name)
                            in_tok = m.get("inputTokenLimit", 0)
                            ctx_str = f"{int(in_tok/1024)}k ctx" if in_tok else ""
                            models.append({
                                "id": f"gemini/{short_name}",
                                "raw_id": short_name,
                                "name": disp_name,
                                "provider": "google",
                                "description": ctx_str,
                                "context_length": in_tok
                            })
                    models.sort(key=lambda x: x["name"].lower())
                else:
                    error_msg = f"HTTP {resp.status_code} desde Google: {resp.text[:120]}"

            # 3. OPENAI
            elif provider == "openai":
                if not api_key:
                    return _fallback_models(provider), "Se requiere OPENAI_API_KEY para consultar la API de OpenAI."
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = await client.get("https://api.openai.com/v1/models", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("data", [])
                    for m in raw_models:
                        mid = m.get("id", "")
                        # Filter to chat-capable models
                        if any(k in mid.lower() for k in ["gpt", "o1", "o3", "chat"]):
                            models.append({
                                "id": mid, # LiteLLM handles gpt-4o, o1 directly
                                "raw_id": mid,
                                "name": mid,
                                "provider": "openai",
                                "description": "OpenAI Oficial",
                                "context_length": 128000
                            })
                    models.sort(key=lambda x: x["name"].lower())
                else:
                    error_msg = f"HTTP {resp.status_code} desde OpenAI: {resp.text[:120]}"

            # 4. ANTHROPIC
            elif provider == "anthropic":
                if not api_key:
                    return _fallback_models(provider), "Se requiere ANTHROPIC_API_KEY para consultar la API de Anthropic."
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01"
                }
                resp = await client.get("https://api.anthropic.com/v1/models", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("data", [])
                    for m in raw_models:
                        mid = m.get("id", "")
                        disp = m.get("display_name", mid)
                        models.append({
                            "id": f"anthropic/{mid}",
                            "raw_id": mid,
                            "name": disp,
                            "provider": "anthropic",
                            "description": "200k ctx",
                            "context_length": 200000
                        })
                    models.sort(key=lambda x: x["name"].lower())
                else:
                    error_msg = f"HTTP {resp.status_code} desde Anthropic: {resp.text[:120]}"

            # 5. OLLAMA LOCAL
            elif provider == "ollama":
                target_base = base_url.rstrip("/") if base_url else "http://localhost:11434"
                url = f"{target_base}/api/tags"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        raw_models = resp.json().get("models", [])
                        for m in raw_models:
                            name = m.get("name", "")
                            size = m.get("size", 0)
                            size_str = f"{size / (1024**3):.1f} GB" if size else ""
                            models.append({
                                "id": f"ollama/{name}",
                                "raw_id": name,
                                "name": f"Local: {name}",
                                "provider": "ollama",
                                "description": size_str,
                                "context_length": 8192
                            })
                        models.sort(key=lambda x: x["name"].lower())
                    else:
                        error_msg = f"HTTP {resp.status_code} desde Ollama ({url})"
                except Exception as e:
                    error_msg = f"Ollama local no disponible en {target_base}: {str(e)}"

            # 6. OLLAMA CLOUD
            elif provider == "ollama_cloud":
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await client.get("https://ollama.com/api/tags", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("models", [])
                    for m in raw_models:
                        name = m.get("name", "")
                        models.append({
                            "id": f"ollama/{name}",
                            "raw_id": name,
                            "name": f"Ollama Cloud: {name}",
                            "provider": "ollama_cloud",
                            "description": "Cloud Model",
                            "context_length": 32768
                        })
                else:
                    error_msg = f"HTTP {resp.status_code} desde Ollama Cloud"

            # 7. KILOCODE GATEWAY
            elif provider == "kilocode":
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.get("https://api.kilo.ai/api/gateway/models", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("data", [])
                    for m in raw_models:
                        mid = m.get("id", "")
                        models.append({
                            "id": f"kilocode/{mid}",
                            "raw_id": mid,
                            "name": m.get("name", mid),
                            "provider": "kilocode",
                            "description": "KiloCode Gateway"
                        })
                else:
                    error_msg = f"HTTP {resp.status_code} desde KiloCode"

            # 8. INCEPTION LABS
            elif provider == "inception":
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.get("https://api.inceptionlabs.ai/v1/models", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("data", [])
                    for m in raw_models:
                        mid = m.get("id", "")
                        models.append({
                            "id": f"inception/{mid}",
                            "raw_id": mid,
                            "name": m.get("name", mid),
                            "provider": "inception",
                            "description": "Inception Labs"
                        })
                else:
                    error_msg = f"HTTP {resp.status_code} desde Inception Labs"

            # 9. OPENCODE ZEN
            elif provider == "opencode":
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.get("https://opencode.ai/zen/v1/models", headers=headers)
                if resp.status_code == 200:
                    raw_models = resp.json().get("data", [])
                    for m in raw_models:
                        mid = m.get("id", "")
                        models.append({
                            "id": f"opencode/{mid}",
                            "raw_id": mid,
                            "name": m.get("name", mid),
                            "provider": "opencode",
                            "description": "OpenCode Zen"
                        })
                else:
                    error_msg = f"HTTP {resp.status_code} desde OpenCode"

            # 10. ANTIGRAVITY
            elif provider == "antigravity":
                try:
                    from pequeno_asistente.agent.antigravity_client import AntigravityClient
                    if AntigravityClient.is_logged_in():
                        tuples = AntigravityClient.fetch_available_models()
                        for mid, name in tuples:
                            models.append({
                                "id": mid,
                                "raw_id": mid.split("/")[-1],
                                "name": name,
                                "provider": "antigravity",
                                "description": "Google Antigravity OAuth2"
                            })
                    else:
                        error_msg = "Sesión no iniciada. Inicia sesión con Google en la pestaña API Keys."
                except Exception as e:
                    error_msg = f"Error consultando Antigravity: {e}"

    except Exception as e:
        error_msg = f"Excepción de conexión ({provider}): {str(e)}"
        logger.warning(error_msg)

    if models:
        save_cached_models(provider, models)
        return models, None

    # Fallback to defaults
    return _fallback_models(provider), error_msg


def _fallback_models(provider: str) -> List[Dict[str, Any]]:
    prov_info = PROVIDERS.get(provider, {})
    suggested = prov_info.get("suggested_models", [])
    result = []
    for s in suggested:
        name = s.split("/")[-1]
        result.append({
            "id": s,
            "raw_id": name,
            "name": name,
            "provider": provider,
            "description": "Modelo sugerido (offline)"
        })
    return result


async def validate_api_key(provider: str, api_key: str, base_url: str = "") -> Tuple[bool, str]:
    """
    Validates credentials against the provider API.
    Returns (is_valid, status_message).
    """
    if provider == "ollama":
        target = base_url.rstrip("/") if base_url else "http://localhost:11434"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(f"{target}/api/tags")
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    return True, f"Servidor Ollama activo ({len(models)} modelos descargados)"
                return False, f"Servidor respondió con código HTTP {resp.status_code}"
        except Exception as e:
            return False, f"No se pudo conectar a Ollama en {target}: {str(e)}"

    if not api_key:
        return False, "La clave de API está vacía."

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if provider == "openrouter":
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = await client.get("https://openrouter.ai/api/v1/auth/key", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    label = data.get("label", "Key válida")
                    limit = data.get("limit")
                    usage = data.get("usage", 0)
                    limit_str = f"Límite: ${limit}" if limit else "Sin límite estricto"
                    return True, f"Clave válida ({label} • Uso: ${usage:.2f} • {limit_str})"
                return False, f"Error {resp.status_code}: Clave de OpenRouter rechazada"

            elif provider == "google":
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    count = len(resp.json().get("models", []))
                    return True, f"Clave válida de Google Gemini ({count} modelos disponibles)"
                return False, f"Error {resp.status_code}: Clave de Google rechazada"

            elif provider == "openai":
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = await client.get("https://api.openai.com/v1/models", headers=headers)
                if resp.status_code == 200:
                    return True, "Clave válida de OpenAI (Conexión exitosa)"
                return False, f"Error {resp.status_code}: Clave de OpenAI rechazada"

            elif provider == "anthropic":
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                resp = await client.get("https://api.anthropic.com/v1/models", headers=headers)
                if resp.status_code == 200:
                    return True, "Clave válida de Anthropic (Conexión exitosa)"
                return False, f"Error {resp.status_code}: Clave de Anthropic rechazada"

            elif provider == "antigravity":
                from pequeno_asistente.agent.antigravity_client import AntigravityClient
                if AntigravityClient.is_logged_in():
                    token = AntigravityClient.get_token()
                    if token:
                        return True, "Sesión de Google Antigravity activa (OAuth2 validado)"
                    return False, "Error al refrescar token de Google Antigravity"
                return False, "Sesión no iniciada. Haz clic en Iniciar Sesión con Google."

            elif provider in ("kilocode", "inception", "opencode"):
                url_map = {
                    "kilocode": "https://api.kilo.ai/api/gateway/models",
                    "inception": "https://api.inceptionlabs.ai/v1/models",
                    "opencode": "https://opencode.ai/zen/v1/models"
                }
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = await client.get(url_map[provider], headers=headers)
                if resp.status_code == 200:
                    return True, f"Clave válida de {provider.capitalize()} (Conexión exitosa)"
                return False, f"Error {resp.status_code}: Credenciales rechazadas"

            return True, "Clave guardada (Validación no disponible para este proveedor)"

    except Exception as e:
        return False, f"Error de conexión: {str(e)}"
