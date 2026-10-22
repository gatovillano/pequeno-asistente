"""
Provider specifications and presets matching KogniTerm and LiteLLM conventions.
"""
from typing import Dict, List, Any

# Map of provider identifier to default env var name
PROVIDER_ENV_VARS: Dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_API_BASE",
    "ollama_cloud": "OLLAMA_CLOUD_API_KEY",
    "antigravity": "ANTIGRAVITY_API_KEY",
    "kilocode": "KILOCODE_API_KEY",
    "inception": "INCEPTION_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "cohere": "COHERE_API_KEY",
    "zhipuai": "ZHIPUAI_API_KEY",
    "litellm": "LITELLM_API_KEY"
}

# Provider catalogue with display names and popular suggested models
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "name": "OpenRouter",
        "description": "Acceso a cientos de modelos (Claude, Llama, Gemini, DeepSeek, etc.) con una sola API key.",
        "env_var": "OPENROUTER_API_KEY",
        "default_model": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "suggested_models": [
            "openrouter/anthropic/claude-3.5-sonnet",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/meta-llama/llama-3.3-70b-instruct",
            "openrouter/deepseek/deepseek-chat",
            "openrouter/openai/gpt-4o-mini"
        ]
    },
    "google": {
        "name": "Google Gemini",
        "description": "Modelos Gemini directos de Google AI Studio.",
        "env_var": "GOOGLE_API_KEY",
        "default_model": "gemini/gemini-2.5-flash",
        "suggested_models": [
            "gemini/gemini-2.5-flash",
            "gemini/gemini-2.5-pro",
            "gemini/gemini-2.0-flash-exp"
        ]
    },
    "openai": {
        "name": "OpenAI",
        "description": "Modelos oficiales de OpenAI (GPT-4o, GPT-4o-mini, o1).",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "suggested_models": [
            "gpt-4o-mini",
            "gpt-4o",
            "o3-mini"
        ]
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "description": "Modelos Claude 3.5 Sonnet / Haiku directos.",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-sonnet-latest",
        "suggested_models": [
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest"
        ]
    },
    "ollama": {
        "name": "Ollama (Local)",
        "description": "Modelos locales ejecutados en tu máquina sin costo de API.",
        "env_var": "OLLAMA_API_BASE",
        "default_model": "ollama/llama3.2",
        "default_base_url": "http://localhost:11434",
        "suggested_models": [
            "ollama/llama3.2",
            "ollama/qwen2.5-coder",
            "ollama/mistral",
            "ollama/deepseek-coder"
        ]
    },
    "antigravity": {
        "name": "Antigravity Gateway",
        "description": "Acceso a modelos de Google DeepMind / Antigravity.",
        "env_var": "ANTIGRAVITY_API_KEY",
        "default_model": "antigravity/gemini-3.8-flash-tiered",
        "suggested_models": [
            "antigravity/gemini-3.8-flash-tiered",
            "antigravity/gemini-3.8-pro"
        ]
    },
    "kilocode": {
        "name": "KiloCode Gateway",
        "description": "Pasarela inteligente KiloCode (Kilo Auto, Claude Sonnet, GPT-4o).",
        "env_var": "KILOCODE_API_KEY",
        "default_model": "kilocode/kilo/auto",
        "suggested_models": [
            "kilocode/kilo/auto",
            "kilocode/anthropic/claude-sonnet-4",
            "kilocode/openai/gpt-4o"
        ]
    },
    "inception": {
        "name": "Inception Labs",
        "description": "Modelos de razonamiento ultra rápido Mercury de Inception Labs.",
        "env_var": "INCEPTION_API_KEY",
        "default_model": "inception/mercury-2.5",
        "suggested_models": [
            "inception/mercury-2",
            "inception/mercury-2.5"
        ]
    },
    "opencode": {
        "name": "OpenCode Zen",
        "description": "Pasarela multi-modelo OpenCode Zen para programación.",
        "env_var": "OPENCODE_API_KEY",
        "default_model": "opencode/claude-sonnet-4-5",
        "suggested_models": [
            "opencode/claude-sonnet-4-5",
            "opencode/gpt-5.5",
            "opencode/gemini-3-flash"
        ]
    }
}
