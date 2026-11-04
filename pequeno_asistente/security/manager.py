"""
Credential and Security Manager for Pequeño Asistente.
Coordinates secure password dialogs, in-memory credential storage,
sudo command execution, and askpass helper integration.
"""
import os
import sys
import time
import shutil
import logging
import asyncio
import subprocess
import contextvars
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Callable, Awaitable

from pequeno_asistente.security.credential import SecureCredential
from pequeno_asistente.security.dialog import prompt_password_async

logger = logging.getLogger("pequeno_asistente.security")

# Context variable for remote turn-based credential requests (e.g. mobile app SSE)
_turn_credential_handler: contextvars.ContextVar[Optional[Callable[..., Awaitable[SecureCredential]]]] = (
    contextvars.ContextVar("_turn_credential_handler", default=None)
)


ADMIN_COMMANDS = {
    "apt", "apt-get", "dpkg", "pacman", "dnf", "yum", "zypper", "apk",
    "systemctl", "journalctl", "service",
    "ufw", "iptables", "nftables", "firewall-cmd",
    "mount", "umount", "fdisk", "gdisk", "parted", "mkfs", "fsck", "losetup",
    "chown", "chmod", "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "passwd", "visudo", "modprobe", "insmod", "rmmod", "dmesg", "sysctl",
    "reboot", "shutdown", "poweroff", "init", "telinit",
    "dpkg-reconfigure", "add-apt-repository", "ip", "ifconfig", "ethtool",
    "dmidecode", "lshw", "partx", "swapoff", "swapon", "blkdiscard"
}

NON_SUDO_USER_COMMANDS = {
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "grep", "egrep", "fgrep",
    "rg", "find", "wc", "sort", "uniq", "diff", "git", "python", "python3",
    "node", "npm", "npx", "pnpm", "yarn", "pip", "pip3", "cargo", "rustc", "go",
    "ruby", "perl", "curl", "wget", "which", "whereis", "type", "file", "uname",
    "whoami", "id", "uptime", "date", "cal", "df", "free", "ps", "top", "htop",
    "tree", "jq", "sed", "awk", "tar", "zip", "unzip", "gzip", "gunzip",
    "env", "printenv", "neofetch", "fastfetch", "lsblk", "lscpu", "lsusb", "lspci"
}

RESTRICTED_READ_FILES = (
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/root"
)

PROTECTED_PATHS = (
    "/etc", "/boot", "/sys", "/proc/sys", "/usr", "/opt", "/var/log"
)


def is_command_requiring_sudo(command: str) -> tuple[bool, str]:
    """
    Evaluates whether a command legitimately requires superuser (sudo) privileges.
    Returns (True, reason) if sudo is justified.
    Returns (False, reason) if it is purely a standard non-privileged user command.
    """
    import re
    clean = command.strip()
    while clean.startswith("sudo "):
        clean = clean[5:].strip()

    if not clean:
        return False, "Comando vacío"

    # Split into subcommands across pipes, conditionals and sequential operators
    subcommands = re.split(r'\s*(?:&&|\|\||;|\|)\s*', clean)

    has_admin_cmd = False
    all_user_cmds = True

    for sub in subcommands:
        sub = sub.strip()
        if not sub:
            continue

        parts = sub.split()
        if not parts:
            continue

        # Check binary name (stripping path like /usr/bin/apt -> apt)
        base_cmd = os.path.basename(parts[0]).lower()

        # Check if accessing strictly restricted files
        for r_file in RESTRICTED_READ_FILES:
            if r_file in sub:
                return True, f"Acceso a archivo restringido ({r_file})"

        # Check if writing/modifying protected system paths
        has_write_op = any(op in sub for op in (">", ">>", "tee", "mv", "cp", "rm", "touch"))
        if has_write_op:
            for p_path in PROTECTED_PATHS:
                if p_path in sub:
                    return True, f"Modificación de ruta protegida del sistema ({p_path})"

        if base_cmd in ADMIN_COMMANDS:
            has_admin_cmd = True
            all_user_cmds = False
        elif base_cmd in NON_SUDO_USER_COMMANDS:
            pass
        else:
            # Custom script or unrecognized command
            all_user_cmds = False

    if has_admin_cmd:
        return True, "Comando administrativo del sistema detectado"

    if all_user_cmds:
        return False, "Operación ordinaria de usuario sin requerimiento de superusuario"

    return True, "Comando no determinado"


class CredentialManager:
    """
    Central manager for secure user credentials and privileged command execution.
    """
    _instance: Optional["CredentialManager"] = None

    def __init__(self, parent_window_getter: Optional[callable] = None):
        self._credentials: Dict[str, SecureCredential] = {}
        self._sudo_cached_cred: Optional[SecureCredential] = None
        self._sudo_cache_time: float = 0.0
        self._sudo_cache_duration: float = 300.0  # 5 minutes, like standard sudo
        self.parent_window_getter = parent_window_getter

    @classmethod
    def get_instance(cls) -> "CredentialManager":
        if cls._instance is None:
            cls._instance = CredentialManager()
        return cls._instance

    @classmethod
    def set_instance(cls, manager: "CredentialManager"):
        cls._instance = manager

    @staticmethod
    def set_turn_handler(handler: Optional[Callable[..., Awaitable[SecureCredential]]]):
        """Sets a context-local credential prompt handler for the current async task/turn."""
        return _turn_credential_handler.set(handler)

    @staticmethod
    def reset_turn_handler(token):
        """Resets the context-local credential prompt handler."""
        if token is not None:
            _turn_credential_handler.reset(token)

    def get_parent_window(self):
        if self.parent_window_getter:
            try:
                return self.parent_window_getter()
            except Exception:
                return None
        return None

    def get_askpass_path(self) -> str:
        """
        Returns the absolute path to the executable askpass helper script.
        Ensures the helper script exists and is executable.
        """
        script_dir = Path(__file__).parent.parent.parent / "scripts"
        script_path = script_dir / "pequeno-asistente-askpass"

        if not script_path.exists():
            script_dir.mkdir(parents=True, exist_ok=True)
            self._generate_askpass_script(script_path)
        else:
            # Ensure executable
            os.chmod(script_path, 0o755)

        # Also place in ~/.local/bin if directory exists in PATH
        user_local_bin = Path.home() / ".local" / "bin"
        if user_local_bin.exists():
            local_bin_target = user_local_bin / "pequeno-asistente-askpass"
            try:
                if not local_bin_target.exists():
                    local_bin_target.symlink_to(script_path)
            except Exception as e:
                logger.debug(f"Could not symlink askpass to ~/.local/bin: {e}")

        return str(script_path)

    def _generate_askpass_script(self, target_path: Path):
        """Generates the standalone askpass helper executable script."""
        repo_root = Path(__file__).parent.parent.parent.resolve()
        python_bin = sys.executable

        content = f"""#!/usr/bin/env bash
# Pequeño Asistente SUDO_ASKPASS Helper
# Invoked by sudo, ssh, or Git when a graphical password prompt is required.

REPO_DIR="{repo_root}"
PYTHON_BIN="{python_bin}"

if [ -f "$REPO_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$REPO_DIR/.venv/bin/python3"
fi

export PYTHONPATH="$REPO_DIR:$PYTHONPATH"
exec "$PYTHON_BIN" -m pequeno_asistente.security.askpass "$@"
"""
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(target_path, 0o755)

    async def request_credential(
        self,
        target: str = "generic",
        prompt: str = "Introduce la contraseña o credencial requerida:",
        username: Optional[str] = None,
        title: Optional[str] = None,
        show_username_field: bool = False
    ) -> SecureCredential:
        """
        Presents the secure password dialog to the user and returns the resulting SecureCredential.
        """
        default_title = "Autenticación de Superusuario (sudo)" if target == "sudo" else f"Credencial para {target.capitalize()}"
        dialog_title = title or default_title

        logger.info(f"Prompting user for secure credential: target={target}, user={username}")

        turn_handler = _turn_credential_handler.get()
        if turn_handler is not None:
            logger.info("Using context-local turn credential handler (e.g. mobile client SSE)")
            try:
                cred = await turn_handler(
                    target=target,
                    prompt=prompt,
                    username=username,
                    title=dialog_title,
                    show_username_field=show_username_field
                )
            except Exception as e:
                logger.error(f"Error in remote turn credential handler: {e}", exc_info=True)
                cred = SecureCredential(target=target, cancelled=True)
        else:
            has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            if has_display:
                parent = self.get_parent_window()
                cred = await prompt_password_async(
                    title=dialog_title,
                    prompt=prompt,
                    target=target,
                    username=username,
                    show_username_field=show_username_field,
                    parent=parent
                )
            else:
                logger.warning("No display or remote turn handler available for credential prompt; marking cancelled.")
                cred = SecureCredential(target=target, cancelled=True)

        if cred.is_valid:
            self._credentials[cred.credential_id] = cred
            if target == "sudo":
                self._sudo_cached_cred = cred
                self._sudo_cache_time = time.time()
            logger.info(f"Credential acquired successfully: id={cred.credential_id}, target={target}")
        else:
            logger.warning(f"Credential prompt cancelled or empty for target={target}")

        return cred

    def get_credential(self, credential_id: str) -> Optional[SecureCredential]:
        """Retrieves a previously stored credential by its unique ID."""
        return self._credentials.get(credential_id)

    def clear_credential(self, credential_id: str):
        """Clears and removes a stored credential from memory."""
        if credential_id in self._credentials:
            cred = self._credentials.pop(credential_id)
            cred.clear()

    async def execute_sudo_command(
        self,
        command: str,
        reason: Optional[str] = None,
        timeout: int = 120
    ) -> Dict[str, Any]:
        """
        Executes a command using sudo.
        If sudo requires password, presents the secure dialog to the user.
        Uses cached sudo credential if within 5-minute cache window.
        """
        clean_command = command.strip()
        while clean_command.startswith("sudo "):
            clean_command = clean_command[5:].strip()

        # Guardrail: Check if command legitimately requires sudo
        is_admin, justification = is_command_requiring_sudo(clean_command)
        if not is_admin:
            logger.warning(f"execute_sudo rejected non-sudo command: '{command}' ({justification})")
            return {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": (
                    f"Aviso de Seguridad: El comando '{command}' es un comando ordinario de usuario y NO requiere privilegios de superusuario root ({justification}).\n"
                    "REGLA ESTRICTA DE HERRAMIENTAS: 'security__execute_sudo' está EXCLUSIVAMENTE reservada para operaciones administrativas (ej. apt install, systemctl, montaje de discos o configuración en /etc).\n"
                    "Por favor ejecuta comandos ordinarios de usuario mediante tus herramientas estándar de terminal MCP (como desktop_bash o execute_command) o indícale al usuario que puede utilizar la Terminal Interactiva integrada (Ctrl+T)."
                ),
                "command": command
            }
        # First check if sudo runs without password (e.g. system configured NOPASSWD or cached)
        check_proc = await asyncio.create_subprocess_shell(
            "sudo -n true 2>/dev/null",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await check_proc.wait()

        # If sudo -n true succeeds, sudo is already authenticated
        if check_proc.returncode == 0:
            logger.info("sudo already authorized without password prompt.")
            return await self._run_shell_command(f"sudo {clean_command}", timeout=timeout)

        # Check if we have a valid cached credential
        now = time.time()
        if (
            self._sudo_cached_cred
            and self._sudo_cached_cred.is_valid
            and (now - self._sudo_cache_time < self._sudo_cache_duration)
        ):
            logger.info("Using cached sudo credential.")
            retcode, stdout, stderr = await self._run_with_stdin_async(
                ["sudo", "-S", "sh", "-c", clean_command],
                secret=self._sudo_cached_cred.get_secret(),
                timeout=timeout
            )
            if retcode == 0:
                self._sudo_cache_time = time.time() # refresh cache timestamp
                return {
                    "success": True,
                    "exit_code": 0,
                    "stdout": stdout,
                    "stderr": stderr,
                    "command": clean_command
                }
            else:
                # Password might have been wrong or expired, invalidate cache
                self._sudo_cached_cred = None

        # Request password through secure dialog
        prompt_text = reason or f"El agente necesita ejecutar el comando administrativo:\n`{clean_command}`"
        cred = await self.request_credential(
            target="sudo",
            prompt=prompt_text,
            title="Autenticación de Superusuario (sudo)"
        )

        if not cred.is_valid:
            return {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Operación cancelada por el usuario (no se proporcionó contraseña de sudo).",
                "command": clean_command
            }

        # Run command with sudo -S providing secret via stdin
        retcode, stdout, stderr = await self._run_with_stdin_async(
            ["sudo", "-S", "sh", "-c", clean_command],
            secret=cred.get_secret(),
            timeout=timeout
        )

        success = (retcode == 0)
        if not success:
            # If failed, invalidate cache in case password was incorrect
            self._sudo_cached_cred = None

        return {
            "success": success,
            "exit_code": retcode,
            "stdout": stdout,
            "stderr": stderr,
            "command": clean_command
        }

    async def _run_shell_command(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode("utf-8", errors="replace"),
                "stderr": stderr_b.decode("utf-8", errors="replace"),
                "command": cmd
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "success": False,
                "exit_code": 124,
                "stdout": "",
                "stderr": f"Comando excedió el tiempo límite de {timeout} segundos.",
                "command": cmd
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": str(e),
                "command": cmd
            }

    async def _run_with_stdin_async(
        self,
        cmd: List[str],
        secret: str,
        timeout: int = 120
    ) -> Tuple[int, str, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._run_with_stdin_sync(cmd, secret, timeout)
        )

    def _run_with_stdin_sync(
        self,
        cmd: List[str],
        secret: str,
        timeout: int = 120
    ) -> Tuple[int, str, str]:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = proc.communicate(input=f"{secret}\n", timeout=timeout)
            # Filter standard "[sudo] password for..." prompt out of stderr
            clean_stderr = []
            for line in stderr.splitlines():
                if "password for" in line.lower() and "[sudo]" in line.lower():
                    continue
                clean_stderr.append(line)
            return proc.returncode, stdout, "\n".join(clean_stderr)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124, "", f"Timed out after {timeout} seconds"
        except Exception as e:
            return 1, "", str(e)
