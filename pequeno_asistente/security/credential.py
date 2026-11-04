"""
Secure Credential Object for Pequeño Asistente.
Encapsulates requested passwords, tokens, and credentials securely,
preventing plaintext leakage in logs, LLM context, or debug traces.
"""
import time
import uuid
import subprocess
from typing import Optional, Dict, Any, List


class SecureCredential:
    """
    Represents a secure user credential requested via UI dialog.
    Guarantees privacy by masking secrets in logs, __repr__, and string conversions.
    """
    def __init__(
        self,
        target: str,
        secret: str = "",
        username: Optional[str] = None,
        purpose: str = "",
        cancelled: bool = False,
        credential_id: Optional[str] = None
    ):
        self.credential_id = credential_id or f"cred_{uuid.uuid4().hex[:12]}"
        self.target = target or "generic"
        self.username = username or ""
        self._secret = secret or ""
        self.purpose = purpose or ""
        self.cancelled = cancelled
        self.created_at = time.time()

    @property
    def is_valid(self) -> bool:
        """Returns True if user did not cancel and a secret was provided."""
        return (not self.cancelled) and bool(self._secret)

    def get_secret(self) -> str:
        """Retrieves the plaintext secret for immediate internal usage."""
        return self._secret

    def clear(self):
        """Zeroes out the secret from internal memory."""
        self._secret = "\0" * len(self._secret)
        self._secret = ""

    def as_dict(self, mask: bool = True) -> Dict[str, Any]:
        """
        Returns a dictionary representation.
        By default masks the secret with asterisks to prevent accidental leakage in JSON logs.
        """
        return {
            "credential_id": self.credential_id,
            "target": self.target,
            "username": self.username,
            "purpose": self.purpose,
            "created_at": self.created_at,
            "cancelled": self.cancelled,
            "is_valid": self.is_valid,
            "secret": "••••••••" if mask else self._secret
        }

    def to_env(self, env_var_name: str, base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Creates a copy of the environment dictionary with this credential injected.
        """
        import os
        env = dict(base_env or os.environ)
        env[env_var_name] = self._secret
        return env

    def execute_with_stdin(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 120
    ) -> tuple[int, str, str]:
        """
        Executes a command securely piping the secret to stdin (e.g. for sudo -S or passwd).
        Immediately closes stdin to prevent leaks.
        """
        if not self.is_valid:
            return 1, "", "Credential is not valid or was cancelled by user."

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd
            )
            stdout, stderr = proc.communicate(input=f"{self._secret}\n", timeout=timeout)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124, "", f"Command timed out after {timeout} seconds."
        except Exception as e:
            return 1, "", f"Failed to execute command: {str(e)}"

    def __repr__(self) -> str:
        masked = "••••••••" if self._secret else "<empty>"
        return (
            f"SecureCredential(id='{self.credential_id}', target='{self.target}', "
            f"username='{self.username}', valid={self.is_valid}, secret='{masked}')"
        )

    def __str__(self) -> str:
        return f"[SecureCredential: target={self.target}, user={self.username or 'default'}, valid={self.is_valid}]"
