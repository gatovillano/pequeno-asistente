"""
Unit tests for Pequeño Asistente Security and Credential Management.
"""
import os
import json
import asyncio
import tempfile
import unittest
from pathlib import Path

from pequeno_asistente.security.credential import SecureCredential
from pequeno_asistente.security.manager import CredentialManager
from pequeno_asistente.mcp_client.manager import MCPManager, SECURITY_TOOLS_DEFINITIONS


class TestSecurityCredentials(unittest.TestCase):
    def test_secure_credential_masking(self):
        cred = SecureCredential(
            target="postgres",
            secret="super_secret_password_123",
            username="pguser",
            purpose="Testing database connection"
        )

        self.assertTrue(cred.is_valid)
        self.assertEqual(cred.get_secret(), "super_secret_password_123")
        self.assertEqual(cred.username, "pguser")
        self.assertEqual(cred.target, "postgres")

        # Ensure representation masks password
        repr_str = repr(cred)
        self.assertNotIn("super_secret_password_123", repr_str)
        self.assertIn("••••••••", repr_str)

        str_str = str(cred)
        self.assertNotIn("super_secret_password_123", str_str)

        # Ensure masked dict
        masked_dict = cred.as_dict(mask=True)
        self.assertEqual(masked_dict["secret"], "••••••••")
        self.assertNotIn("super_secret_password_123", json.dumps(masked_dict))

        # Clear memory
        cred.clear()
        self.assertEqual(cred.get_secret(), "")
        self.assertFalse(cred.is_valid)

    def test_credential_manager_storage(self):
        mgr = CredentialManager()
        cred = SecureCredential(target="wifi", secret="wifi_pass_xyz")
        mgr._credentials[cred.credential_id] = cred

        retrieved = mgr.get_credential(cred.credential_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.get_secret(), "wifi_pass_xyz")

        mgr.clear_credential(cred.credential_id)
        self.assertIsNone(mgr.get_credential(cred.credential_id))

    def test_askpass_path_and_script(self):
        mgr = CredentialManager()
        askpass_path = mgr.get_askpass_path()
        self.assertTrue(os.path.exists(askpass_path))
        self.assertTrue(os.access(askpass_path, os.X_OK))

    def test_mcp_manager_security_tools_exposed(self):
        mcp = MCPManager()
        tools = mcp.get_litellm_tools()

        tool_names = [t["function"]["name"] for t in tools]
        self.assertIn("security__request_credential", tool_names)
        self.assertIn("security__execute_sudo", tool_names)
        self.assertIn("security__apply_credential", tool_names)

        # Check alias resolution
        self.assertEqual(mcp.alias_map.get("request_credential"), "security__request_credential")
        self.assertEqual(mcp.alias_map.get("execute_sudo"), "security__execute_sudo")
        self.assertEqual(mcp.alias_map.get("apply_credential"), "security__apply_credential")

    def test_security_apply_credential(self):
        async def run_async():
            mcp = MCPManager()
            sec_mgr = CredentialManager.get_instance()

            cred = SecureCredential(
                target="database",
                secret="db_secret_999",
                username="admin_user"
            )
            sec_mgr._credentials[cred.credential_id] = cred

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name

            try:
                res = await mcp.call_tool(
                    "security__apply_credential",
                    {
                        "credential_id": cred.credential_id,
                        "destination_file": tmp_path,
                        "file_template": "USER={username}\nPASS={secret}\n"
                    }
                )
                self.assertIn("exitosamente", res)

                # Verify file content
                with open(tmp_path, "r") as f:
                    content = f.read()
                self.assertEqual(content, "USER=admin_user\nPASS=db_secret_999\n")

                # Verify permissions 0600
                st_mode = oct(os.stat(tmp_path).st_mode & 0o777)
                self.assertEqual(st_mode, "0o600")

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        asyncio.run(run_async())

    def test_is_command_requiring_sudo(self):
        from pequeno_asistente.security.manager import is_command_requiring_sudo

        # User commands should NOT require sudo
        user_cmds = [
            "ls -la",
            "pwd",
            "cat README.md",
            "git status",
            "python3 -m unittest",
            "node index.js",
            "echo 'Hello world'",
            "curl -s https://example.com",
            "find . -name '*.py'",
            "grep -rn 'test' .",
            "df -h",
            "ps aux"
        ]
        for cmd in user_cmds:
            req, reason = is_command_requiring_sudo(cmd)
            self.assertFalse(req, f"Command '{cmd}' should NOT require sudo, but got {reason}")

        # Administrative / system commands SHOULD require sudo
        admin_cmds = [
            "apt update",
            "apt install -y jq",
            "systemctl restart nginx",
            "systemctl status docker",
            "ufw allow 22",
            "mount /dev/sdb1 /mnt",
            "useradd -m newuser",
            "cat /etc/shadow",
            "echo 'test' > /etc/custom.conf"
        ]
        for cmd in admin_cmds:
            req, reason = is_command_requiring_sudo(cmd)
            self.assertTrue(req, f"Command '{cmd}' SHOULD require sudo, but got {reason}")

    def test_execute_sudo_rejects_user_command(self):
        async def run_async():
            sec_mgr = CredentialManager.get_instance()
            # Attempt to execute an ordinary user command via execute_sudo
            res = await sec_mgr.execute_sudo_command("ls -la", reason="Ver archivos")
            self.assertFalse(res["success"])
            self.assertEqual(res["exit_code"], 1)
            self.assertIn("comando ordinario de usuario", res["stderr"])
            self.assertIn("desktop_bash", res["stderr"])

            # Also verify when prefixed with sudo
            res_prefixed = await sec_mgr.execute_sudo_command("sudo git status")
            self.assertFalse(res_prefixed["success"])
            self.assertIn("comando ordinario de usuario", res_prefixed["stderr"])

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
