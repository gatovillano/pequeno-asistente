"""
Unit tests for Automatic Thread Title Generation in Pequeño Asistente.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager, clean_llm_title, clean_thread_title
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks
from pequeno_asistente.mcp_client.manager import MCPManager


class TestAutoTitleGeneration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name)
        self.thread_manager = ThreadManager(base_dir=str(self.config_dir))

        self.config_manager = MagicMock(spec=ConfigManager)
        self.config_manager.data = {
            "llm": {"provider": "mock", "temperature": 0.7, "max_tokens": 100},
            "memory": {"auto_log": False}
        }
        self.config_manager.get_active_model.return_value = "mock/model-v1"
        self.config_manager.apply_env_keys = MagicMock()

        self.memory_manager = MagicMock(spec=MemoryManager)
        self.memory_manager.get_memory_context_prompt.return_value = ""
        self.memory_manager.detect_and_store_lesson.return_value = None

        self.mcp_manager = MagicMock(spec=MCPManager)
        self.mcp_manager.get_litellm_tools.return_value = []

        self.agent = AgentCore(
            config_manager=self.config_manager,
            mcp_manager=self.mcp_manager,
            memory_manager=self.memory_manager,
            thread_manager=self.thread_manager
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_llm_title_sanitization(self):
        """Verify LLM titles are properly cleaned and stripped of common prefixes/quotes/markdown."""
        # Strips prefix and wrapping quotes
        self.assertEqual(clean_llm_title('Título: "Configuración de Git"'), "Configuración de Git")
        self.assertEqual(clean_llm_title('Title: How to write unit tests'), "How to write unit tests")
        self.assertEqual(clean_llm_title('Tema: Script en Python para audios.'), "Script en Python para audios")
        self.assertEqual(clean_llm_title('Resumen: Creación de API con FastAPI'), "Creación de API con FastAPI")
        self.assertEqual(clean_llm_title('**Asunto: Base de datos SQLite**'), "Base de datos SQLite")

        # Strips markdown backticks and quotes
        self.assertEqual(clean_llm_title('`Optimizaciones de rendimiento`'), "Optimizaciones de rendimiento")
        self.assertEqual(clean_llm_title('«Plan de viaje a Chile»'), "Plan de viaje a Chile")
        self.assertEqual(clean_llm_title('“Análisis de datos médicos”'), "Análisis de datos médicos")

        # Truncation for very long titles
        long_title = "Esta es una explicación sumamente larga y detallada sobre cómo configurar servidores web en la nube"
        cleaned_long = clean_llm_title(long_title)
        self.assertTrue(len(cleaned_long) <= 50)
        self.assertTrue(cleaned_long.endswith("…"))

    def test_thread_manager_title_flags(self):
        """Verify thread manager tracks title_auto_generated and title_custom."""
        th = self.thread_manager.create_thread(title="Nueva conversación")
        tid = th["id"]

        self.assertFalse(self.thread_manager.is_title_custom(tid))
        self.assertFalse(self.thread_manager.is_title_auto_generated(tid))

        # Auto generated update
        self.thread_manager.update_title(tid, "Título: Depuración de Red", is_auto=True)
        self.assertTrue(self.thread_manager.is_title_auto_generated(tid))
        self.assertFalse(self.thread_manager.is_title_custom(tid))
        updated_th = self.thread_manager.get_thread(tid)
        self.assertEqual(updated_th["title"], "Depuración de Red")

        # Custom manual update overrides auto
        self.thread_manager.update_title(tid, "Mi Título Personalizado", is_custom=True)
        self.assertTrue(self.thread_manager.is_title_custom(tid))
        self.assertFalse(self.thread_manager.is_title_auto_generated(tid))
        self.assertEqual(self.thread_manager.get_thread(tid)["title"], "Mi Título Personalizado")

    def test_generate_thread_title_flow(self):
        """Verify AgentCore.generate_thread_title calls LLM and updates thread."""
        async def run_test():
            th = self.thread_manager.create_thread(title="Nueva conversación")
            tid = th["id"]

            # Add sample messages
            self.agent.load_thread(tid)
            self.agent.history.append({"role": "user", "content": "¿Cómo configuro un servidor Nginx con SSL en Ubuntu?"})
            self.agent.history.append({"role": "assistant", "content": "Para configurar Nginx con SSL puedes usar Certbot..."})
            self.thread_manager.save_thread(tid, self.agent.history)

            class MockDelta:
                def __init__(self, text):
                    self.content = text
                    self.tool_calls = None
                    self.reasoning_content = None

            class MockChoice:
                def __init__(self, text):
                    self.delta = MockDelta(text)
                    self.message = MockDelta(text)

            class MockChunk:
                def __init__(self, text):
                    self.choices = [MockChoice(text)]

            async def mock_stream():
                for piece in ["Título: ", "Configuración ", "de Nginx ", "con SSL"]:
                    yield MockChunk(piece)

            title_listener_calls = []
            self.agent.add_title_listener(lambda t_id, title: title_listener_calls.append((t_id, title)))

            cb_title = []
            callbacks = AgentCallbacks(
                on_thread_title_updated=lambda t_id, title: cb_title.append((t_id, title))
            )

            with patch("litellm.acompletion", return_value=mock_stream()):
                generated = await self.agent.generate_thread_title(tid, force=False, callbacks=callbacks)

            self.assertEqual(generated, "Configuración de Nginx con SSL")
            saved = self.thread_manager.get_thread(tid)
            self.assertEqual(saved["title"], "Configuración de Nginx con SSL")
            self.assertTrue(saved.get("title_auto_generated"))

            # Listeners notified
            self.assertEqual(len(title_listener_calls), 1)
            self.assertEqual(title_listener_calls[0], (tid, "Configuración de Nginx con SSL"))
            self.assertEqual(len(cb_title), 1)
            self.assertEqual(cb_title[0], (tid, "Configuración de Nginx con SSL"))

            # Without force, subsequent calls should not re-query LLM
            with patch("litellm.acompletion") as mock_second_call:
                second = await self.agent.generate_thread_title(tid, force=False)
                mock_second_call.assert_not_called()
                self.assertEqual(second, "Configuración de Nginx con SSL")

        asyncio.run(run_test())

    def test_custom_title_preserved_unless_forced(self):
        """User-defined custom titles should never be overwritten unless force=True."""
        async def run_test():
            th = self.thread_manager.create_thread(title="Mi Proyecto Secreto", custom=True)
            tid = th["id"]
            self.thread_manager.save_thread(tid, [{"role": "user", "content": "Hola mundo"}])

            with patch("litellm.acompletion") as mock_call:
                title = await self.agent.generate_thread_title(tid, force=False)
                mock_call.assert_not_called()
                self.assertEqual(title, "Mi Proyecto Secreto")

        asyncio.run(run_test())

    def test_auto_title_triggered_after_turn(self):
        """After completing a turn in a default thread, auto-title is generated in background."""
        async def run_test():
            th = self.thread_manager.create_thread(title="Nueva conversación")
            tid = th["id"]

            class MockDelta:
                def __init__(self, text):
                    self.content = text
                    self.tool_calls = None
                    self.reasoning_content = None

            class MockChoice:
                def __init__(self, text):
                    self.delta = MockDelta(text)
                    self.message = MockDelta(text)

            class MockChunk:
                def __init__(self, text):
                    self.choices = [MockChoice(text)]

            call_count = 0

            async def mock_acompletion(**kwargs):
                nonlocal call_count
                call_count += 1
                messages = kwargs.get("messages", [])
                is_title_call = any("título" in m.get("content", "").lower() for m in messages if m.get("role") == "system")

                async def gen_turn():
                    yield MockChunk("Aquí tienes la receta de galletas de avena con canela.")

                async def gen_title():
                    yield MockChunk("Título: Receta de Galletas de Avena")

                if is_title_call:
                    return gen_title()
                return gen_turn()

            title_updates = []
            callbacks = AgentCallbacks(
                on_thread_title_updated=lambda t_id, title: title_updates.append((t_id, title))
            )

            with patch("litellm.acompletion", side_effect=mock_acompletion):
                await self.agent.run_turn(
                    user_message="¿Cómo hago galletas de avena?",
                    callbacks=callbacks,
                    thread_id=tid
                )

                # Wait for background task if pending
                sess = self.agent.get_session(tid)
                if sess and getattr(sess, "title_task", None):
                    await sess.title_task

            self.assertGreaterEqual(len(title_updates), 1)
            self.assertEqual(title_updates[-1], (tid, "Receta de Galletas de Avena"))

            final_th = self.thread_manager.get_thread(tid)
            self.assertEqual(final_th["title"], "Receta de Galletas de Avena")
            self.assertTrue(final_th["title_auto_generated"])

        asyncio.run(run_test())

    def test_api_endpoints_title_and_auto_title(self):
        """Test REST API endpoints for updating title and triggering auto-title."""
        from fastapi.testclient import TestClient
        from pequeno_asistente.server import app, get_agent_stack, verify_auth

        th = self.thread_manager.create_thread(title="Nueva conversación")
        tid = th["id"]
        self.thread_manager.save_thread(tid, [
            {"role": "user", "content": "¿Cómo instalar Docker en Arch Linux?"},
            {"role": "assistant", "content": "Para instalar Docker en Arch usa pacman -S docker"}
        ])

        app.dependency_overrides[verify_auth] = lambda: True
        try:
            with patch("pequeno_asistente.server.get_agent_stack", return_value=(
                self.config_manager, self.memory_manager, self.thread_manager, self.mcp_manager, self.agent
            )):
                client = TestClient(app)

                # 1. Test PUT /api/threads/{thread_id}/title
                put_res = client.put(f"/api/threads/{tid}/title", json={"title": "Guía de Docker en Arch"})
                self.assertEqual(put_res.status_code, 200)
                data = put_res.json()
                self.assertEqual(data["title"], "Guía de Docker en Arch")
                self.assertTrue(data["success"])
                self.assertTrue(self.thread_manager.is_title_custom(tid))

                # 2. Test POST /api/threads/{thread_id}/auto-title
                class MockDelta:
                    def __init__(self, text):
                        self.content = text
                        self.tool_calls = None
                        self.reasoning_content = None

                class MockChoice:
                    def __init__(self, text):
                        self.delta = MockDelta(text)
                        self.message = MockDelta(text)

                class MockChunk:
                    def __init__(self, text):
                        self.choices = [MockChoice(text)]

                async def mock_auto_title_stream(**kwargs):
                    async def gen():
                        yield MockChunk("Título: Docker en Arch Linux")
                    return gen()

                with patch("litellm.acompletion", side_effect=mock_auto_title_stream):
                    post_res = client.post(f"/api/threads/{tid}/auto-title")
                    self.assertEqual(post_res.status_code, 200)
                    auto_data = post_res.json()
                    self.assertEqual(auto_data["title"], "Docker en Arch Linux")
                    self.assertTrue(auto_data["generated"])
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
