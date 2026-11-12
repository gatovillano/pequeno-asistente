"""
Unit tests for Pequeño Asistente Multi-session & Concurrent Session Execution.
"""
import os
import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager
from pequeno_asistente.agent.thread_manager import ThreadManager
from pequeno_asistente.agent.core import AgentCore, AgentCallbacks, SessionState
from pequeno_asistente.mcp_client.manager import MCPManager


class TestMultiSession(unittest.TestCase):
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
        self.mcp_manager.call_tool = AsyncMock(return_value="tool_ok")

        self.agent = AgentCore(
            config_manager=self.config_manager,
            mcp_manager=self.mcp_manager,
            memory_manager=self.memory_manager,
            thread_manager=self.thread_manager
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_state_isolation(self):
        """Verify each session has its own isolated history and state flags."""
        sess_a = self.agent.get_or_create_session("thread_a")
        sess_b = self.agent.get_or_create_session("thread_b")

        self.assertNotEqual(sess_a, sess_b)
        self.assertEqual(sess_a.thread_id, "thread_a")
        self.assertEqual(sess_b.thread_id, "thread_b")

        sess_a.history.append({"role": "user", "content": "Hola A"})
        self.assertEqual(len(sess_a.history), 1)
        self.assertEqual(len(sess_b.history), 0)

        sess_a.is_running = True
        self.assertTrue(self.agent.is_thread_running("thread_a"))
        self.assertFalse(self.agent.is_thread_running("thread_b"))
        self.assertEqual(self.agent.get_running_thread_ids(), ["thread_a"])

    def test_targeted_abort(self):
        """Aborting session A should never cancel session B."""
        sess_a = self.agent.get_or_create_session("thread_a")
        sess_b = self.agent.get_or_create_session("thread_b")

        sess_a.is_running = True
        sess_b.is_running = True

        self.agent.abort(thread_id="thread_a")
        self.assertTrue(sess_a.abort_requested)
        self.assertFalse(sess_b.abort_requested)

    def test_session_switching_without_state_loss(self):
        """Switching active thread preserves messages and does not interrupt running sessions."""
        th_1 = self.agent.new_thread(title="Chat 1")
        tid_1 = th_1["id"]
        self.agent.history.append({"role": "user", "content": "Mensaje 1"})

        th_2 = self.agent.new_thread(title="Chat 2")
        tid_2 = th_2["id"]
        self.assertEqual(self.agent.active_thread_id, tid_2)
        self.assertEqual(len(self.agent.history), 0)

        # Switch back to thread 1
        loaded_1 = self.agent.load_thread(tid_1)
        self.assertEqual(self.agent.active_thread_id, tid_1)
        self.assertEqual(len(loaded_1), 1)
        self.assertEqual(loaded_1[0]["content"], "Mensaje 1")

    def test_concurrent_sessions_execution(self):
        """Test two sessions running concurrently at the exact same time."""
        async def run_test():
            th_a = self.agent.new_thread(title="Sesión Alfa")
            tid_a = th_a["id"]
            th_b = self.agent.new_thread(title="Sesión Beta")
            tid_b = th_b["id"]

            tokens_a = []
            tokens_b = []
            completed_a = []
            completed_b = []

            cb_a = AgentCallbacks(
                on_token=lambda tok: tokens_a.append(tok),
                on_complete=lambda final: completed_a.append(final)
            )
            cb_b = AgentCallbacks(
                on_token=lambda tok: tokens_b.append(tok),
                on_complete=lambda final: completed_b.append(final)
            )

            # Mock litellm.acompletion to stream chunks with simulated delays
            async def mock_acompletion(**kwargs):
                messages = kwargs.get("messages", [])
                user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")

                class MockDelta:
                    def __init__(self, content):
                        self.content = content
                        self.reasoning_content = None
                        self.tool_calls = None

                class MockChoice:
                    def __init__(self, content):
                        self.delta = MockDelta(content)

                class MockChunk:
                    def __init__(self, content):
                        self.choices = [MockChoice(content)]

                async def gen():
                    for chunk in [f"Resp-{user_msg}-1 ", f"Resp-{user_msg}-2"]:
                        await asyncio.sleep(0.02)
                        yield MockChunk(chunk)

                return gen()

            with patch("litellm.acompletion", side_effect=mock_acompletion):
                # Launch both sessions concurrently
                task_a = asyncio.create_task(self.agent.run_turn("Hola Alfa", cb_a, thread_id=tid_a))
                task_b = asyncio.create_task(self.agent.run_turn("Hola Beta", cb_b, thread_id=tid_b))

                # Allow both to start
                await asyncio.sleep(0.01)

                # Verify both are running concurrently
                running = self.agent.get_running_thread_ids()
                self.assertIn(tid_a, running)
                self.assertIn(tid_b, running)
                self.assertEqual(len(running), 2)
                self.assertTrue(self.agent.is_any_running())

                # Wait for both to finish
                await asyncio.gather(task_a, task_b, return_exceptions=True)

            # Verify no sessions running
            self.assertEqual(self.agent.get_running_thread_ids(), [])
            self.assertFalse(self.agent.is_thread_running(tid_a))
            self.assertFalse(self.agent.is_thread_running(tid_b))

            # Verify token separation
            self.assertEqual("".join(tokens_a), "Resp-Hola Alfa-1 Resp-Hola Alfa-2")
            self.assertEqual("".join(tokens_b), "Resp-Hola Beta-1 Resp-Hola Beta-2")

            # Verify histories are isolated
            sess_a = self.agent.get_session(tid_a)
            sess_b = self.agent.get_session(tid_b)
            self.assertEqual(len(sess_a.history), 2)  # user + assistant
            self.assertEqual(sess_a.history[0]["content"], "Hola Alfa")
            self.assertEqual(sess_a.history[1]["content"], "Resp-Hola Alfa-1 Resp-Hola Alfa-2")

            self.assertEqual(len(sess_b.history), 2)  # user + assistant
            self.assertEqual(sess_b.history[0]["content"], "Hola Beta")
            self.assertEqual(sess_b.history[1]["content"], "Resp-Hola Beta-1 Resp-Hola Beta-2")

        asyncio.run(run_test())

    def test_concurrent_abort_one_keeps_other_running(self):
        """Canceling session A while session B is running lets session B finish cleanly."""
        async def run_test():
            th_a = self.agent.new_thread(title="Largo A")
            tid_a = th_a["id"]
            th_b = self.agent.new_thread(title="Largo B")
            tid_b = th_b["id"]

            errors_a = []
            completed_b = []

            cb_a = AgentCallbacks(on_error=lambda err: errors_a.append(err))
            cb_b = AgentCallbacks(on_complete=lambda final: completed_b.append(final))

            async def mock_acompletion(**kwargs):
                class MockDelta:
                    def __init__(self, content):
                        self.content = content
                        self.reasoning_content = None
                        self.tool_calls = None

                class MockChoice:
                    def __init__(self, content):
                        self.delta = MockDelta(content)

                class MockChunk:
                    def __init__(self, content):
                        self.choices = [MockChoice(content)]

                async def gen():
                    for i in range(10):
                        await asyncio.sleep(0.02)
                        yield MockChunk(f"chunk_{i} ")

                return gen()

            with patch("litellm.acompletion", side_effect=mock_acompletion):
                task_a = asyncio.create_task(self.agent.run_turn("Prompt A", cb_a, thread_id=tid_a))
                task_b = asyncio.create_task(self.agent.run_turn("Prompt B", cb_b, thread_id=tid_b))

                # Allow both to start
                await asyncio.sleep(0.03)

                # Abort session A ONLY
                self.agent.abort(thread_id=tid_a)

                await asyncio.gather(task_a, task_b, return_exceptions=True)

            # Session A was canceled with an error notice
            self.assertTrue(len(errors_a) > 0)
            self.assertIn("cancelada", errors_a[0].lower())

            # Session B finished successfully
            self.assertTrue(len(completed_b) > 0)
            self.assertIn("chunk_9", completed_b[0])

        asyncio.run(run_test())


    def test_three_or_more_concurrent_sessions(self):
        """Test 3 or more sessions running concurrently without interference."""
        async def run_test():
            threads = [self.agent.new_thread(title=f"Sess {i}") for i in range(3)]
            tids = [th["id"] for th in threads]
            results = {tid: [] for tid in tids}

            def make_callbacks(tid):
                return AgentCallbacks(
                    on_token=lambda tok, t=tid: results[t].append(tok),
                    on_complete=lambda fin, t=tid: results[t].append(f"DONE_{fin}")
                )

            async def mock_acompletion(**kwargs):
                messages = kwargs.get("messages", [])
                user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")

                class MockDelta:
                    def __init__(self, content):
                        self.content = content
                        self.reasoning_content = None
                        self.tool_calls = None

                class MockChoice:
                    def __init__(self, content):
                        self.delta = MockDelta(content)

                class MockChunk:
                    def __init__(self, content):
                        self.choices = [MockChoice(content)]

                async def gen():
                    for step in ["part1", "part2"]:
                        await asyncio.sleep(0.02)
                        yield MockChunk(f"{user_msg}_{step} ")

                return gen()

            with patch("litellm.acompletion", side_effect=mock_acompletion):
                tasks = [
                    asyncio.create_task(
                        self.agent.run_turn(f"Query-{i}", make_callbacks(tid), thread_id=tid)
                    )
                    for i, tid in enumerate(tids)
                ]

                await asyncio.sleep(0.01)

                running = self.agent.get_running_thread_ids()
                self.assertEqual(len(running), 3)
                for tid in tids:
                    self.assertIn(tid, running)

                await asyncio.gather(*tasks)

            self.assertEqual(self.agent.get_running_thread_ids(), [])
            for i, tid in enumerate(tids):
                full_text = "".join(results[tid])
                self.assertIn(f"Query-{i}_part1", full_text)
                self.assertIn(f"Query-{i}_part2", full_text)
                self.assertIn("DONE_", full_text)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
