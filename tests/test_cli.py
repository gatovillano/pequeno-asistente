import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pequeno_asistente.cli.app import (
    PequenoAsistenteCLI,
    build_parser,
    extract_inline_attachments,
    main as cli_main,
)
from pequeno_asistente.cli.renderer import CLIRenderer
from pequeno_asistente.cli.security_prompt import CLICredentialPrompt


class TestCLIParserAndHelpers(unittest.TestCase):
    def test_parser_defaults_and_flags(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.prompt)
        self.assertIsNone(args.subcommand)
        self.assertFalse(args.raw)
        self.assertFalse(args.json_mode)

        args2 = parser.parse_args([
            "-p", "Hola mundo",
            "-a", "doc.pdf",
            "-t", "thread-1",
            "-w", "/tmp/my-project",
            "-m", "openai/gpt-4o",
            "--no-mcp",
            "--no-workspace",
            "--show-thinking",
            "--json",
            "-q",
        ])
        self.assertEqual(args2.prompt, "Hola mundo")
        self.assertEqual(args2.attach, ["doc.pdf"])
        self.assertEqual(args2.thread, "thread-1")
        self.assertEqual(args2.workspace, "/tmp/my-project")
        self.assertEqual(args2.model, "openai/gpt-4o")
        self.assertTrue(args2.no_mcp)
        self.assertTrue(args2.no_workspace)
        self.assertTrue(args2.show_thinking)
        self.assertTrue(args2.json_mode)
        self.assertTrue(args2.quiet)

    def test_parser_subcommands(self):
        parser = build_parser()
        ask_args = parser.parse_args(["ask", "Resume", "este", "texto"])
        self.assertEqual(ask_args.subcommand, "ask")
        self.assertEqual(ask_args.query, ["Resume", "este", "texto"])

        th_args = parser.parse_args(["threads", "list"])
        self.assertEqual(th_args.subcommand, "threads")
        self.assertEqual(th_args.action, "list")

        mcp_args = parser.parse_args(["mcp", "tools", "pequeno-core"])
        self.assertEqual(mcp_args.subcommand, "mcp")
        self.assertEqual(mcp_args.action, "tools")
        self.assertEqual(mcp_args.target, "pequeno-core")

        mem_args = parser.parse_args(["memory", "search", "bitwig"])
        self.assertEqual(mem_args.subcommand, "memory")
        self.assertEqual(mem_args.action, "search")
        self.assertEqual(mem_args.query, ["bitwig"])

    def test_extract_inline_attachments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "notes.txt"
            f1.write_text("hola", encoding="utf-8")
            f2 = Path(tmpdir) / "my file.md"
            f2.write_text("# titulo", encoding="utf-8")

            text = f'Revisa @{f1} y @"{f2}" y @no_existe_12345.txt'
            cleaned, found = extract_inline_attachments(text)
            self.assertEqual(len(found), 2)
            self.assertIn(str(f1.resolve()), found)
            self.assertIn(str(f2.resolve()), found)
            self.assertIn("@no_existe_12345.txt", cleaned)
            self.assertNotIn(str(f1), cleaned)


class TestCLICommandsAndExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pequeno_asistente.agent.thread_manager import ThreadManager
        from pequeno_asistente.agent.memory import MemoryManager
        from pequeno_asistente.agent.heartbeat import HeartbeatManager

        MemoryManager._instance = None
        HeartbeatManager._instance = None
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.threads_dir = Path(self.tmpdir.name) / "threads"
        self.memory_dir = Path(self.tmpdir.name) / "memory"

        self._orig_tm_init = ThreadManager.__init__
        self._orig_mm_init = MemoryManager.__init__

        threads_dir = self.threads_dir
        memory_dir = self.memory_dir

        def tm_init_isolated(s, base_dir=None):
            self._orig_tm_init(s, base_dir=str(threads_dir))

        def mm_init_isolated(s, base_dir=None):
            self._orig_mm_init(s, base_dir=str(memory_dir))

        with patch.object(ThreadManager, "__init__", tm_init_isolated), \
             patch.object(MemoryManager, "__init__", mm_init_isolated):
            self.renderer = CLIRenderer(raw=False, json_mode=False, quiet=False)
            self.cli = PequenoAsistenteCLI(
                config_path=str(self.config_path),
                renderer=self.renderer,
                sync_mcp=False,
            )

    async def asyncTearDown(self):
        from pequeno_asistente.agent.memory import MemoryManager
        from pequeno_asistente.agent.heartbeat import HeartbeatManager
        await self.cli.shutdown()
        MemoryManager._instance = None
        HeartbeatManager._instance = None
        self.tmpdir.cleanup()

    async def test_slash_commands_lifecycle(self):
        cmd = self.cli.command_handler

        res_help = await cmd.handle("/help")
        self.assertTrue(res_help.handled)
        self.assertFalse(res_help.should_exit)

        old_tid = self.cli.active_thread_id
        res_new = await cmd.handle('/new "Hilo de Prueba"')
        self.assertTrue(res_new.handled)
        new_tid = self.cli.active_thread_id
        self.assertNotEqual(old_tid, new_tid)

        await cmd.handle("/rename Título Editado")
        th = self.cli.thread_manager.get_thread(new_tid)
        self.assertEqual(th["title"], "Título Editado")

        await cmd.handle(f"/thread {old_tid}")
        self.assertEqual(self.cli.active_thread_id, old_tid)

        await cmd.handle("/thinking on")
        self.assertTrue(self.cli.renderer.show_thinking)
        await cmd.handle("/thinking off")
        self.assertFalse(self.cli.renderer.show_thinking)

        await cmd.handle("/config set llm.temperature 0.25")
        self.assertEqual(self.cli.config_manager.data["llm"]["temperature"], 0.25)

        await cmd.handle("/model openai/gpt-4o-mini")
        self.assertEqual(self.cli.config_manager.get_active_model(), "openai/gpt-4o-mini")

        sample_file = Path(self.tmpdir.name) / "sample.txt"
        sample_file.write_text("contenido", encoding="utf-8")
        await cmd.handle(f"/attach {sample_file}")
        self.assertEqual(len(self.cli.pending_attachments), 1)
        await cmd.handle("/clear-attach")
        self.assertEqual(len(self.cli.pending_attachments), 0)

        export_path = Path(self.tmpdir.name) / "out.json"
        await cmd.handle(f"/export {export_path}")
        self.assertTrue(export_path.exists())

        await cmd.handle("/memory remember El usuario prefiere Python")
        self.assertIn("El usuario prefiere Python", self.cli.memory_manager.get_long_term_memory())

        res_exit = await cmd.handle("/exit")
        self.assertTrue(res_exit.should_exit)

    async def test_one_shot_json_output(self):
        self.cli.renderer = CLIRenderer(raw=False, json_mode=True, quiet=True, show_thinking=True)
        self.cli.command_handler.r = self.cli.renderer

        async def fake_run_turn(user_message, callbacks, is_call_mode=False, thread_id=None, on_dequeued=None, attachments=None):
            callbacks.on_thinking("Analizando solicitud...")
            callbacks.on_tool_start("pequeno-core__memory__stats", {"verbose": True})
            callbacks.on_tool_end("pequeno-core__memory__stats", '{"ok": true}')
            callbacks.on_token("Respuesta ")
            callbacks.on_token("final.")
            callbacks.on_complete("Respuesta final.")

        self.cli.agent.run_turn = AsyncMock(side_effect=fake_run_turn)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = await self.cli.run_one_shot("Hola")

        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["response"], "Respuesta final.")
        self.assertEqual(payload["thinking"], "Analizando solicitud...")
        self.assertEqual(len(payload["tools"]), 1)
        self.assertEqual(payload["tools"][0]["name"], "pequeno-core__memory__stats")
        self.assertTrue(payload["tools"][0]["done"])

    async def test_one_shot_raw_output(self):
        self.cli.renderer = CLIRenderer(raw=True, json_mode=False, quiet=True)
        self.cli.command_handler.r = self.cli.renderer

        async def fake_run_turn(user_message, callbacks, **kwargs):
            callbacks.on_token("Hola ")
            callbacks.on_token("CLI")
            callbacks.on_complete("Hola CLI")

        self.cli.agent.run_turn = AsyncMock(side_effect=fake_run_turn)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = await self.cli.run_one_shot("Di hola")

        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "Hola CLI")


class TestCLICredentialPrompt(unittest.IsolatedAsyncioTestCase):
    async def test_credential_prompt_tty_success(self):
        prompt = CLICredentialPrompt()
        with patch("sys.stdin.isatty", return_value=True), \
             patch("getpass.getpass", return_value="secreto123"), \
             patch("builtins.input", return_value="mi_usuario"):
            cred = await prompt(
                target="sudo",
                prompt="Ingresa clave",
                username="gato",
                show_username_field=True,
            )
            self.assertFalse(cred.cancelled)
            self.assertEqual(cred.get_secret(), "secreto123")
            self.assertEqual(cred.username, "mi_usuario")

    async def test_credential_prompt_non_tty_cancels(self):
        prompt = CLICredentialPrompt()
        with patch("sys.stdin.isatty", return_value=False):
            cred = await prompt(target="sudo")
            self.assertTrue(cred.cancelled)


class TestMainCLIDispatch(unittest.TestCase):
    def test_main_cli_flag(self):
        with patch("pequeno_asistente.cli.app.main", return_value=0) as mock_cli_main:
            with patch.object(sys, "argv", ["main.py", "--cli", "skills", "list"]):
                from pequeno_asistente import main as entry_main
                with self.assertRaises(SystemExit) as ctx:
                    entry_main.main()
                self.assertEqual(ctx.exception.code, 0)
                mock_cli_main.assert_called_once_with(["skills", "list"])

    def test_cli_suppresses_litellm_stderr_logs(self):
        from pequeno_asistente.cli.app import configure_cli_logging

        configure_cli_logging(verbose=False)
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
                lgr = logging.getLogger(name)
                lgr.info("should not appear on stderr")
                lgr.warning("warning should not appear on stderr either")
        self.assertEqual(buf.getvalue(), "")


class TestCLIWorkspaceBehavior(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pequeno_asistente.agent.thread_manager import ThreadManager
        from pequeno_asistente.agent.memory import MemoryManager
        from pequeno_asistente.agent.heartbeat import HeartbeatManager

        MemoryManager._instance = None
        HeartbeatManager._instance = None
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.config_path = self.root / "config.json"
        self.threads_dir = self.root / "threads"
        self.memory_dir = self.root / "memory"
        self.proj_a = self.root / "project_alpha"
        self.proj_b = self.root / "project_beta"
        self.proj_a.mkdir()
        self.proj_b.mkdir()
        (self.proj_a / "CONTEXT.md").write_text("# Alpha Notes\nUses FastAPI.", encoding="utf-8")

        self._orig_tm_init = ThreadManager.__init__
        self._orig_mm_init = MemoryManager.__init__

    async def asyncTearDown(self):
        from pequeno_asistente.agent.memory import MemoryManager
        from pequeno_asistente.agent.heartbeat import HeartbeatManager
        MemoryManager._instance = None
        HeartbeatManager._instance = None
        self.tmpdir.cleanup()

    def _make_cli(self, **kwargs):
        from pequeno_asistente.agent.thread_manager import ThreadManager
        from pequeno_asistente.agent.memory import MemoryManager

        threads_dir = self.threads_dir
        memory_dir = self.memory_dir
        orig_tm = self._orig_tm_init
        orig_mm = self._orig_mm_init

        def tm_init_isolated(s, base_dir=None):
            orig_tm(s, base_dir=str(threads_dir))

        def mm_init_isolated(s, base_dir=None):
            orig_mm(s, base_dir=str(memory_dir))

        with patch.object(ThreadManager, "__init__", tm_init_isolated), \
             patch.object(MemoryManager, "__init__", mm_init_isolated):
            return PequenoAsistenteCLI(
                config_path=str(self.config_path),
                renderer=CLIRenderer(quiet=True),
                sync_mcp=False,
                **kwargs,
            )

    async def test_cwd_folder_becomes_workspace_and_injects_context(self):
        cli = self._make_cli(cwd=self.proj_a)
        try:
            self.assertIsNotNone(cli.workspace_profile)
            self.assertEqual(cli.workspace_profile["name"], "project_alpha")
            self.assertEqual(cli.workspace_profile["folders"], [str(self.proj_a.resolve())])

            ws = cli.thread_manager.get_workspace_profile(cli.active_thread_id)
            self.assertIsNotNone(ws)
            self.assertEqual(ws["folders"], [str(self.proj_a.resolve())])

            sys_prompt = cli.agent._build_system_prompt(thread_id=cli.active_thread_id)
            self.assertIn("<workspace_context>", sys_prompt)
            self.assertIn(str(self.proj_a.resolve()), sys_prompt)
            self.assertIn("Uses FastAPI.", sys_prompt)
        finally:
            await cli.shutdown()

    async def test_reopening_in_same_and_different_folders_isolates_threads(self):
        cli_a1 = self._make_cli(cwd=self.proj_a)
        tid_a1 = cli_a1.active_thread_id
        await cli_a1.shutdown()

        # Re-opening in proj_a resumes the same workspace thread
        cli_a2 = self._make_cli(cwd=self.proj_a)
        self.assertEqual(cli_a2.active_thread_id, tid_a1)
        await cli_a2.shutdown()

        # Opening in proj_b creates a distinct thread bound to proj_b
        cli_b = self._make_cli(cwd=self.proj_b)
        self.assertNotEqual(cli_b.active_thread_id, tid_a1)
        ws_b = cli_b.thread_manager.get_workspace_profile(cli_b.active_thread_id)
        self.assertEqual(ws_b["folders"], [str(self.proj_b.resolve())])
        await cli_b.shutdown()

    async def test_subfolder_matches_configured_workspace(self):
        nested = self.proj_a / "src" / "pkg"
        nested.mkdir(parents=True)

        cli_setup = self._make_cli(cwd=self.proj_a)
        cli_setup.config_manager.save_workspace(
            "alpha-ws",
            {
                "id": "alpha-ws",
                "name": "Alpha Configured",
                "folders": [str(self.proj_a.resolve())],
                "memory_file": str((self.proj_a / "CONTEXT.md").resolve()),
            },
        )
        await cli_setup.shutdown()

        cli_nested = self._make_cli(cwd=nested)
        try:
            self.assertEqual(cli_nested.workspace_id, "alpha-ws")
            self.assertEqual(cli_nested.workspace_profile["name"], "Alpha Configured")
        finally:
            await cli_nested.shutdown()

    async def test_no_workspace_flag_and_slash_workspace_commands(self):
        cli = self._make_cli(cwd=self.proj_a, use_cwd_workspace=False)
        try:
            self.assertIsNone(cli.workspace_profile)
            self.assertIsNone(cli.thread_manager.get_workspace_profile(cli.active_thread_id))

            cmd = cli.command_handler
            await cmd.handle(f"/workspace use {self.proj_b}")
            self.assertEqual(cli.workspace_profile["folders"], [str(self.proj_b.resolve())])

            await cmd.handle("/workspace save Beta Saved")
            saved = cli.config_manager.get_workspace("beta-saved")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["name"], "Beta Saved")

            await cmd.handle("/workspace clear")
            self.assertIsNone(cli.workspace_profile)
            self.assertIsNone(cli.thread_manager.get_workspace_profile(cli.active_thread_id))
        finally:
            await cli.shutdown()

    async def test_runtime_mcp_filesystem_adds_workspace_folder(self):
        cli = self._make_cli(cwd=self.proj_a)
        try:
            cli.config_manager.data.setdefault("mcp_servers", {})["filesystem"] = {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/some/other/root"],
                "transport": "stdio",
                "disabled": False,
            }
            runtime = cli._get_runtime_mcp_servers()
            self.assertIn(str(self.proj_a.resolve()), runtime["filesystem"]["args"])
            # Original config must not be mutated
            self.assertNotIn(
                str(self.proj_a.resolve()),
                cli.config_manager.get_mcp_servers()["filesystem"]["args"],
            )
        finally:
            await cli.shutdown()


if __name__ == "__main__":
    unittest.main()
