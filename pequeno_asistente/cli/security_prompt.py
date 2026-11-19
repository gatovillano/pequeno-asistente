"""
Terminal-native credential and sudo prompt handler for Pequeño Asistente CLI.
Integrates with CredentialManager._turn_credential_handler so `security__execute_sudo`
and `security__request_credential` work seamlessly in headless/TTY terminals.
"""
import os
import sys
import getpass
import asyncio
from typing import Optional, Any

from pequeno_asistente.security.credential import SecureCredential


class CLICredentialPrompt:
    """Async credential prompt handler for terminal sessions."""

    def __init__(self, renderer: Optional[Any] = None):
        self.renderer = renderer

    async def __call__(
        self,
        target: str = "generic",
        prompt: str = "Introduce la contraseña o credencial requerida:",
        username: Optional[str] = None,
        title: Optional[str] = None,
        show_username_field: bool = False,
    ) -> SecureCredential:
        if not sys.stdin.isatty():
            return SecureCredential(target=target, cancelled=True)

        loop = asyncio.get_running_loop()

        if self.renderer and hasattr(self.renderer, "pause_live"):
            self.renderer.pause_live()

        try:
            return await loop.run_in_executor(
                None,
                self._prompt_sync,
                target,
                prompt,
                username,
                title,
                show_username_field,
            )
        finally:
            if self.renderer and hasattr(self.renderer, "resume_live"):
                self.renderer.resume_live()

    def _prompt_sync(
        self,
        target: str,
        prompt: str,
        username: Optional[str],
        title: Optional[str],
        show_username_field: bool,
    ) -> SecureCredential:
        default_user = username or (getpass.getuser() if target == "sudo" else "")
        header = title or (
            "Autenticación de Superusuario (sudo)"
            if target == "sudo"
            else f"Credencial para {target}"
        )

        print(f"\n🔐 [{header}]", file=sys.stderr, flush=True)
        print(f"   {prompt}", file=sys.stderr, flush=True)

        try:
            entered_user = default_user
            if show_username_field:
                user_label = f"   Usuario [{default_user}]: " if default_user else "   Usuario: "
                sys.stderr.write(user_label)
                sys.stderr.flush()
                raw_user = input().strip()
                if raw_user:
                    entered_user = raw_user

            pw_label = (
                f"   Contraseña para {entered_user}: "
                if entered_user and target == "sudo"
                else "   Contraseña / Token: "
            )
            secret = getpass.getpass(pw_label, stream=sys.stderr)
            if not secret:
                return SecureCredential(target=target, username=entered_user, cancelled=True)
            return SecureCredential(
                target=target,
                username=entered_user or None,
                secret=secret,
                cancelled=False,
            )
        except (KeyboardInterrupt, EOFError):
            print("\n   [Cancelado]", file=sys.stderr, flush=True)
            return SecureCredential(target=target, cancelled=True)
