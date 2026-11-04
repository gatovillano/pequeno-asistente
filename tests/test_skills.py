"""
Unit tests for SkillManager, skills tools, and Claude Code / system skills discovery.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.skills_manager import SkillManager, SkillInfo
from pequeno_asistente.mcp_client.manager import MCPManager, SKILLS_TOOLS_DEFINITIONS


class TestSkillManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_pequeno_skills_")
        self.config_path = os.path.join(self.test_dir, "config.json")
        self.config_mgr = ConfigManager(config_path=self.config_path)

        # Create mock directories
        self.mock_user_skills = Path(self.test_dir) / "user_skills"
        self.mock_claude_skills = Path(self.test_dir) / "claude_skills"
        self.mock_agents_skills = Path(self.test_dir) / "agents_skills"
        self.mock_system_skills = Path(self.test_dir) / "system_skills"

        for p in (self.mock_user_skills, self.mock_claude_skills, self.mock_agents_skills, self.mock_system_skills):
            p.mkdir(parents=True, exist_ok=True)

        self.mgr = SkillManager(config_manager=self.config_mgr)

        # Override search directories for deterministic unit testing
        self.mgr.get_search_directories = lambda: [
            ("user", "Usuario", self.mock_user_skills, 100),
            ("claude", "Claude Code", self.mock_claude_skills, 70),
            ("agents_global", "Global Agentes", self.mock_agents_skills, 65),
            ("system", "Sistema", self.mock_system_skills, 40)
        ]

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_discover_claude_and_system_skills(self):
        # 1. Add skill to Claude Code (~/.claude/skills)
        claude_skill_dir = self.mock_claude_skills / "claude-analysis"
        claude_skill_dir.mkdir(parents=True)
        with open(claude_skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: claude-analysis\n"
                "description: Habilidad de análisis profundo originaria de Claude Code.\n"
                "tags: [claude, analysis]\n"
                "---\n"
                "# Análisis Claude\nInstrucciones de análisis."
            )

        # 2. Add skill to global system directory
        with open(self.mock_system_skills / "sys-diagnostics.md", "w", encoding="utf-8") as f:
            f.write(
                "# Diagnósticos del Sistema\n"
                "Verifica el estado del kernel y logs del sistema operativo.\n"
                "## Pasos\n1. Comprobar memoria y dmesg."
            )

        # 3. Add skill to ~/.agents/skills
        agents_skill_dir = self.mock_agents_skills / "agent-orchestrator"
        agents_skill_dir.mkdir(parents=True)
        with open(agents_skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: agent-orchestrator\n"
                "description: Orquestación multi-agente estándar.\n"
                "---\n"
                "# Orquestador\nProcedimiento."
            )

        discovered = self.mgr.discover_skills(force_refresh=True)

        self.assertIn("claude-analysis", discovered)
        self.assertEqual(discovered["claude-analysis"].source, "claude")
        self.assertIn("Claude Code", discovered["claude-analysis"].source_label)

        self.assertIn("sys-diagnostics", discovered)
        self.assertEqual(discovered["sys-diagnostics"].source, "system")

        self.assertIn("agent-orchestrator", discovered)
        self.assertEqual(discovered["agent-orchestrator"].source, "agents_global")

    def test_install_skill_from_content(self):
        content = (
            "---\n"
            "name: test-skill\n"
            "description: Habilidad de prueba instalada mediante contenido directo.\n"
            "---\n"
            "# Habilidad de Prueba\nContenido de prueba."
        )
        # Mock destination directory inside install_skill
        orig_ensure = self.mgr._ensure_user_skills_dir
        self.mgr._ensure_user_skills_dir = lambda: self.mock_user_skills

        # Patch Path.home in install_skill by installing directly to mock_user_skills
        res = self.mgr.install_skill(
            name="test-custom-skill",
            content=content,
            target_scope="user"
        )
        # Verify success or fallback path
        self.assertTrue(res.get("success") or "skill_id" in res)
        self.mgr.discover_skills(force_refresh=True)
        skill = self.mgr.get_skill("test-custom-skill")
        if skill:
            self.assertEqual(skill.id, "test-custom-skill")
            self.assertIn("Habilidad de prueba", skill.description)

        self.mgr._ensure_user_skills_dir = orig_ensure

    def test_toggle_and_uninstall_skill(self):
        # Create a user skill
        user_skill_dir = self.mock_user_skills / "toggle-me"
        user_skill_dir.mkdir(parents=True)
        with open(user_skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write("# Habilidad Toggle\nDescripción toggle.")

        self.mgr.discover_skills(force_refresh=True)
        self.assertIn("toggle-me", self.mgr.discover_skills())

        # Toggle off
        ok = self.mgr.toggle_skill("toggle-me", False)
        self.assertTrue(ok)
        self.assertIn("toggle-me", self.config_mgr.get_disabled_skills())

        # Toggle on
        ok = self.mgr.toggle_skill("toggle-me", True)
        self.assertTrue(ok)
        self.assertNotIn("toggle-me", self.config_mgr.get_disabled_skills())

        # Uninstall
        res = self.mgr.uninstall_skill("toggle-me")
        self.assertTrue(res.get("success"))
        self.assertNotIn("toggle-me", self.mgr.discover_skills(force_refresh=True))

    def test_format_skills_for_prompt(self):
        claude_dir = self.mock_claude_skills / "critical-thinking"
        claude_dir.mkdir(parents=True)
        with open(claude_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: critical-thinking\n"
                "description: Evaluar premisas y validar sesgos cognitivos.\n"
                "---\n"
                "# Pensamiento Crítico\nPasos detallados de validación."
            )

        self.mgr.discover_skills(force_refresh=True)
        prompt = self.mgr.format_skills_for_prompt()

        self.assertIn("<skills>", prompt)
        self.assertIn("critical-thinking", prompt)
        self.assertIn("Claude Code", prompt)
        self.assertIn("skills__get", prompt)
        self.assertIn("</skills>", prompt)

    def test_semantic_matching_and_slash_invocation(self):
        # Create audio / bitwig skill
        user_skill_dir = self.mock_user_skills / "bitwig-producer"
        user_skill_dir.mkdir(parents=True)
        with open(user_skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: bitwig-producer\n"
                "description: Controla la DAW Bitwig Studio, reproduce, crea pistas de audio y sintetizadores.\n"
                "tags: [bitwig, music, audio, daw]\n"
                "---\n"
                "# Bitwig Producer\nPasos para componer y reproducir."
            )

        self.mgr.discover_skills(force_refresh=True)

        # 1. Semantic match
        matches = self.mgr.match_relevant_skills("quiero reproducir una pista en bitwig")
        self.assertTrue(len(matches) > 0)
        top_skill, score = matches[0]
        self.assertEqual(top_skill.id, "bitwig-producer")
        self.assertGreater(score, 5.0)

        # 2. Slash invocation
        slash_matches = self.mgr.match_relevant_skills("/bitwig-producer crea un bajo")
        self.assertTrue(len(slash_matches) > 0)
        self.assertEqual(slash_matches[0][0].id, "bitwig-producer")
        self.assertGreaterEqual(slash_matches[0][1], 100.0)

        # 3. Dynamic prompt injection with semantic relevance
        prompt_with_query = self.mgr.format_skills_for_prompt(query="reproducir audio en bitwig")
        self.assertIn("HABILIDAD CARGADA DINÁMICAMENTE POR PERTINENCIA SEMÁNTICA", prompt_with_query)
        self.assertIn("Bitwig Producer", prompt_with_query)

        # 4. Prompt with intentional slash invocation
        prompt_slash = self.mgr.format_skills_for_prompt(query="/bitwig-producer dale play")
        self.assertIn("HABILIDAD EXPRESAMENTE SOLICITADA POR EL USUARIO: /bitwig-producer", prompt_slash)


class TestSkillsMCPToolsIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_manager_skills_tools_presence_and_call(self):
        mcp = MCPManager()
        tools = mcp.get_litellm_tools()
        tool_names = [t["function"]["name"] for t in tools]

        for expected in ("skills__list", "skills__get", "skills__install", "skills__uninstall", "skills__toggle"):
            self.assertIn(expected, tool_names)

        # Call skills__list
        list_res = await mcp.call_tool("skills__list", {"source": "all"})
        self.assertIn("skills", list_res)
        self.assertIn("count", list_res)

        # Call with aliases
        alias_res = await mcp.call_tool("list_skills", {})
        self.assertIn("skills", alias_res)


if __name__ == "__main__":
    unittest.main()
