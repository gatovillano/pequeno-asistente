"""
Tests for Vercel AI Gateway integration in Pequeño Asistente.
Verifies provider definitions, API key management, model fetching from API,
LiteLLM call preparation, and multimodal heuristics.
"""
import os
import unittest
import asyncio
from unittest.mock import patch, MagicMock
import httpx

from pequeno_asistente.agent.providers import PROVIDERS, PROVIDER_ENV_VARS
from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.core import prepare_provider_call
from pequeno_asistente.agent.model_fetcher import (
    fetch_provider_models,
    validate_api_key,
    _fallback_models
)
from pequeno_asistente.agent.media_processor import is_model_multimodal, is_model_audio_multimodal


class TestVercelProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_config_path = "/tmp/test_pequeno_config_vercel.json"
        if os.path.exists(self.tmp_config_path):
            os.remove(self.tmp_config_path)
        self.cfg = ConfigManager(self.tmp_config_path)

    def tearDown(self):
        if os.path.exists(self.tmp_config_path):
            os.remove(self.tmp_config_path)
        os.environ.pop("VERCEL_AI_GATEWAY_API_KEY", None)
        os.environ.pop("AI_GATEWAY_API_KEY", None)

    def test_vercel_provider_registry(self):
        """Verify Vercel AI Gateway is present in catalog and env var mapping."""
        self.assertIn("vercel", PROVIDERS)
        v_info = PROVIDERS["vercel"]
        self.assertEqual(v_info["name"], "Vercel AI Gateway")
        self.assertEqual(v_info["env_var"], "VERCEL_AI_GATEWAY_API_KEY")
        self.assertTrue(v_info["default_model"].startswith("vercel/"))
        self.assertTrue(len(v_info["suggested_models"]) >= 3)
        self.assertEqual(PROVIDER_ENV_VARS.get("vercel"), "VERCEL_AI_GATEWAY_API_KEY")

    def test_vercel_api_key_resolution(self):
        """Verify API key resolution from config and environment variables."""
        # 1. Initially empty
        self.assertEqual(self.cfg.get_api_key("vercel"), "")

        # 2. From AI_GATEWAY_API_KEY fallback
        os.environ["AI_GATEWAY_API_KEY"] = "key-from-gateway-env"
        self.assertEqual(self.cfg.get_api_key("vercel"), "key-from-gateway-env")

        # 3. From VERCEL_AI_GATEWAY_API_KEY precedence over fallback
        os.environ["VERCEL_AI_GATEWAY_API_KEY"] = "key-from-vercel-env"
        self.assertEqual(self.cfg.get_api_key("vercel"), "key-from-vercel-env")

        # 4. From config precedence over env vars
        self.cfg.set_api_key("vercel", "key-from-config")
        self.assertEqual(self.cfg.get_api_key("vercel"), "key-from-config")
        self.assertEqual(os.environ.get("AI_GATEWAY_API_KEY"), "key-from-config")

    def test_prepare_provider_call_vercel(self):
        """Verify prepare_provider_call configures LiteLLM for Vercel AI Gateway."""
        self.cfg.set_api_key("vercel", "vcl_secret_123")
        self.cfg.set_active_provider("vercel")

        engine, kwargs = prepare_provider_call("vercel/anthropic/claude-sonnet-4.5", self.cfg)
        self.assertEqual(engine, "litellm")
        self.assertEqual(kwargs["model"], "openai/anthropic/claude-sonnet-4.5")
        self.assertEqual(kwargs["api_base"], "https://ai-gateway.vercel.sh/v1")
        self.assertEqual(kwargs["custom_llm_provider"], "openai")
        self.assertEqual(kwargs["api_key"], "vcl_secret_123")
        self.assertTrue(kwargs["stream"])

        # Without vercel/ prefix when provider is active
        engine2, kwargs2 = prepare_provider_call("openai/gpt-4o", self.cfg)
        self.assertEqual(engine2, "litellm")
        self.assertEqual(kwargs2["model"], "openai/openai/gpt-4o")
        self.assertEqual(kwargs2["api_base"], "https://ai-gateway.vercel.sh/v1")
        self.assertEqual(kwargs2["custom_llm_provider"], "openai")

    def test_fetch_provider_models_mocked(self):
        """Verify fetch_provider_models parses Vercel models, extracts metadata and filters non-language types."""
        mock_payload = {
            "object": "list",
            "data": [
                {
                    "id": "anthropic/claude-sonnet-4.5",
                    "name": "Claude 3.5 Sonnet",
                    "type": "language",
                    "context_window": 200000,
                    "pricing": {"input": "0.000003", "output": "0.000015"}
                },
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "type": "language",
                    "context_window": 128000,
                    "pricing": {"input": "0.0000025", "output": "0.00001"}
                },
                {
                    "id": "alibaba/qwen3-embedding-8b",
                    "name": "Qwen3 Embedding",
                    "type": "embedding",
                    "context_window": 8192
                }
            ]
        }

        async def run_test():
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_payload

            with patch("httpx.AsyncClient.get", return_value=mock_resp):
                models, err = await fetch_provider_models("vercel", api_key="test-key", force_refresh=True)
                self.assertIsNone(err)
                self.assertEqual(len(models), 2)  # embedding model filtered out

                ids = [m["id"] for m in models]
                self.assertIn("vercel/anthropic/claude-sonnet-4.5", ids)
                self.assertIn("vercel/openai/gpt-4o", ids)

                sonnet = next(m for m in models if m["raw_id"] == "anthropic/claude-sonnet-4.5")
                self.assertEqual(sonnet["provider"], "vercel")
                self.assertEqual(sonnet["context_length"], 200000)
                self.assertIn("195k ctx", sonnet["description"])
                self.assertIn("$3.00/M in, $15.00/M out", sonnet["description"])

        asyncio.run(run_test())

    def test_validate_api_key_mocked(self):
        """Verify API key validation against Vercel endpoint."""
        async def run_test():
            # 1. Success (200)
            mock_200 = MagicMock()
            mock_200.status_code = 200
            mock_200.json.return_value = {"data": [{"id": "model1"}, {"id": "model2"}]}

            with patch("httpx.AsyncClient.get", return_value=mock_200):
                ok, msg = await validate_api_key("vercel", "valid_key")
                self.assertTrue(ok)
                self.assertIn("Clave válida de Vercel AI Gateway", msg)

            # 2. Unauthorized (401)
            mock_401 = MagicMock()
            mock_401.status_code = 401
            mock_401.text = "Unauthorized"

            with patch("httpx.AsyncClient.get", return_value=mock_401):
                ok, msg = await validate_api_key("vercel", "bad_key")
                self.assertFalse(ok)
                self.assertIn("rechazada", msg.lower())

        asyncio.run(run_test())

    def test_multimodal_heuristics(self):
        """Verify vision and audio multimodal detection for Vercel model IDs."""
        # Vision
        self.assertTrue(is_model_multimodal("vercel/openai/gpt-4o", "vercel"))
        self.assertTrue(is_model_multimodal("vercel/google/gemini-2.5-flash", "vercel"))
        self.assertTrue(is_model_multimodal("vercel/anthropic/claude-sonnet-4.5", "vercel"))
        self.assertFalse(is_model_multimodal("vercel/meta/llama-3.3-70b", "vercel"))
        self.assertFalse(is_model_multimodal("vercel/deepseek/deepseek-r1", "vercel"))

        # Audio
        self.assertTrue(is_model_audio_multimodal("vercel/google/gemini-2.5-flash", "vercel"))
        self.assertFalse(is_model_audio_multimodal("vercel/anthropic/claude-sonnet-4.5", "vercel"))
        self.assertFalse(is_model_audio_multimodal("vercel/deepseek/deepseek-r1", "vercel"))
        self.assertFalse(is_model_audio_multimodal("vercel/meta/llama-3.3-70b", "vercel"))


if __name__ == "__main__":
    unittest.main()
