"""
Unit tests for Pequeño Asistente Heartbeats and Automations.
"""
import os
import json
import asyncio
import tempfile
import datetime
import unittest
from pathlib import Path

from pequeno_asistente.config import ConfigManager
from pequeno_asistente.agent.memory import MemoryManager, DEFAULT_HEARTBEAT_MD
from pequeno_asistente.agent.heartbeat import HeartbeatManager, AUTOMATION_PRESETS, format_schedule_badge
from pequeno_asistente.mcp_client.manager import MCPManager, AUTOMATION_TOOLS_DEFINITIONS


class MockAgent:
    def __init__(self, return_text: str = "HEARTBEAT_OK"):
        self.return_text = return_text
        self.calls = []

    async def run_headless_turn(self, prompt: str, system_extra: str = "", max_turns: int = 12):
        self.calls.append({"prompt": prompt, "system_extra": system_extra})
        return {
            "status": "ok",
            "text": self.return_text,
            "tools_used": ["desktop_bash__execute"]
        }


class TestHeartbeatsAndAutomations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "config.json")
        self.config_manager = ConfigManager(config_path=self.config_path)

        self.memory_dir = os.path.join(self.temp_dir.name, "memory")
        self.memory_manager = MemoryManager(base_dir=self.memory_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_config_manager_heartbeat_and_automations(self):
        hb_cfg = self.config_manager.get_heartbeat_config()
        self.assertFalse(hb_cfg.get("enabled", True))
        self.assertEqual(hb_cfg.get("interval_minutes", 0), 30)

        # Update heartbeat config
        self.config_manager.set_heartbeat_config({"enabled": True, "interval_minutes": 15})
        updated = self.config_manager.get_heartbeat_config()
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["interval_minutes"], 15)

        # Automations CRUD
        self.assertEqual(len(self.config_manager.get_automations()), 0)
        auto = self.config_manager.add_automation({
            "id": "hb-test1234",
            "name": "Test Task",
            "prompt": "Test prompt",
            "enabled": True,
            "interval_minutes": 20
        })
        self.assertEqual(auto["id"], "hb-test1234")
        self.assertEqual(len(self.config_manager.get_automations()), 1)

        # Update
        updated_auto = self.config_manager.update_automation("hb-test1234", {"name": "Updated Name"})
        self.assertIsNotNone(updated_auto)
        self.assertEqual(updated_auto["name"], "Updated Name")

        # Delete
        deleted = self.config_manager.delete_automation("hb-test1234")
        self.assertTrue(deleted)
        self.assertEqual(len(self.config_manager.get_automations()), 0)

    def test_memory_manager_heartbeat_md(self):
        content = self.memory_manager.get_heartbeat_md()
        self.assertIn("HEARTBEAT.md", content)
        self.assertIn("HEARTBEAT_OK", content)

        # Save custom
        custom_text = "# Mi Checklist Personalizado\n- Revisar disco > 80%"
        self.memory_manager.save_heartbeat_md(custom_text)
        self.assertEqual(self.memory_manager.get_heartbeat_md(), custom_text)

    def test_heartbeat_manager_crud_and_presets(self):
        mock_agent = MockAgent()
        hb_mgr = HeartbeatManager(
            config_manager=self.config_manager,
            memory_manager=self.memory_manager,
            agent=mock_agent
        )

        # Presets validity
        self.assertGreater(len(AUTOMATION_PRESETS), 2)
        for p in AUTOMATION_PRESETS:
            self.assertIn("name", p)
            self.assertIn("prompt", p)
            self.assertIn("schedule_type", p)

        # Create automation
        created = hb_mgr.create_automation(
            name="Vigilancia de RAM",
            prompt="Verifica el uso de RAM",
            schedule_type="interval",
            interval_minutes=45,
            silent_if_ok=True
        )
        self.assertTrue(created["id"].startswith("hb-"))
        self.assertEqual(created["name"], "Vigilancia de RAM")
        self.assertEqual(created["interval_minutes"], 45)

        # Format badge
        badge = format_schedule_badge(created)
        self.assertEqual(badge, "⏱ Cada 45 min")

        # Update
        hb_mgr.update_automation(created["id"], {"name": "Vigilancia Modificada"})
        auto_in_cfg = next(a for a in self.config_manager.get_automations() if a["id"] == created["id"])
        self.assertEqual(auto_in_cfg["name"], "Vigilancia Modificada")

        # Delete
        hb_mgr.delete_automation(created["id"])
        self.assertEqual(len(self.config_manager.get_automations()), 0)

    def test_heartbeat_ok_detection(self):
        self.assertTrue(HeartbeatManager._is_heartbeat_ok("HEARTBEAT_OK"))
        self.assertTrue(HeartbeatManager._is_heartbeat_ok("heartbeat_ok"))
        self.assertTrue(HeartbeatManager._is_heartbeat_ok("  `HEARTBEAT_OK`  "))
        self.assertTrue(HeartbeatManager._is_heartbeat_ok("HEARTBEAT_OK\n"))
        self.assertFalse(HeartbeatManager._is_heartbeat_ok("Alerta: la memoria RAM superó el 95%"))

    def test_heartbeat_manager_logs(self):
        mock_agent = MockAgent()
        hb_mgr = HeartbeatManager(
            config_manager=self.config_manager,
            memory_manager=self.memory_manager,
            agent=mock_agent
        )

        hb_mgr.clear_logs()
        self.assertEqual(len(hb_mgr.get_logs()), 0)

        hb_mgr.append_log({
            "timestamp": "2026-11-04T00:00:00",
            "source_id": "global_heartbeat",
            "source_name": "Pulso Global",
            "status": "silent_ok",
            "summary": "HEARTBEAT_OK",
            "duration_sec": 1.2
        })
        logs = hb_mgr.get_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "silent_ok")

        hb_mgr.clear_logs()
        self.assertEqual(len(hb_mgr.get_logs()), 0)

    def test_mcp_manager_automation_tools(self):
        mcp = MCPManager(config_manager=self.config_manager)
        tools = mcp.get_litellm_tools()
        tool_names = [t["function"]["name"] for t in tools]

        expected_tools = [
            "automation__list",
            "automation__create",
            "automation__update",
            "automation__delete",
            "automation__configure_heartbeat",
            "automation__get_heartbeat_checklist",
            "automation__update_heartbeat_checklist",
            "automation__trigger_now"
        ]
        for et in expected_tools:
            self.assertIn(et, tool_names)
            # Check alias
            bare_name = et.replace("automation__", "")
            self.assertEqual(mcp.alias_map.get(bare_name), et)

    def test_mcp_manager_automation_tool_execution(self):
        async def run_async():
            mock_agent = MockAgent()
            hb_mgr = HeartbeatManager(
                config_manager=self.config_manager,
                memory_manager=self.memory_manager,
                agent=mock_agent
            )
            mcp = MCPManager(config_manager=self.config_manager)

            # 1. automation__configure_heartbeat
            res = await mcp.call_tool("automation__configure_heartbeat", {
                "enabled": True,
                "interval_minutes": 25,
                "silent_if_ok": True
            })
            parsed = json.loads(res)
            self.assertEqual(parsed["status"], "updated")
            self.assertTrue(parsed["heartbeat"]["enabled"])
            self.assertEqual(parsed["heartbeat"]["interval_minutes"], 25)

            # 2. automation__create
            res_create = await mcp.call_tool("automation__create", {
                "name": "Monitoreo de Procesos",
                "prompt": "Revisa procesos con alto consumo de CPU",
                "schedule_type": "interval",
                "interval_minutes": 15
            })
            p_create = json.loads(res_create)
            self.assertEqual(p_create["status"], "created")
            auto_id = p_create["automation"]["id"]

            # 3. automation__list
            res_list = await mcp.call_tool("automation__list", {})
            p_list = json.loads(res_list)
            self.assertTrue(p_list["heartbeat"]["enabled"])
            self.assertEqual(len(p_list["automations"]), 1)
            self.assertEqual(p_list["automations"][0]["id"], auto_id)

            # 4. automation__get_heartbeat_checklist & update
            res_checklist = await mcp.call_tool("automation__get_heartbeat_checklist", {})
            self.assertIn("HEARTBEAT.md", res_checklist)

            res_upd_md = await mcp.call_tool("automation__update_heartbeat_checklist", {
                "content": "# Nuevo Checklist\n- Tarea de prueba"
            })
            self.assertIn("guardada", res_upd_md.lower())
            self.assertEqual(self.memory_manager.get_heartbeat_md(), "# Nuevo Checklist\n- Tarea de prueba")

            # 5. automation__update
            res_upd = await mcp.call_tool("automation__update", {
                "auto_id": auto_id,
                "name": "Procesos Actualizados"
            })
            p_upd = json.loads(res_upd)
            self.assertEqual(p_upd["automation"]["name"], "Procesos Actualizados")

            # 6. automation__delete
            res_del = await mcp.call_tool("automation__delete", {
                "auto_id": auto_id
            })
            self.assertIn("eliminada", res_del.lower())
            self.assertEqual(len(self.config_manager.get_automations()), 0)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
