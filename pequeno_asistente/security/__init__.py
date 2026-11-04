"""
Security and Credential Management Module for Pequeño Asistente.
Provides secure password prompt dialogs, credential objects, and sudo askpass integration.
"""
from pequeno_asistente.security.credential import SecureCredential
from pequeno_asistente.security.dialog import PasswordPromptDialog, prompt_password_sync, prompt_password_async
from pequeno_asistente.security.manager import CredentialManager

__all__ = [
    "SecureCredential",
    "PasswordPromptDialog",
    "prompt_password_sync",
    "prompt_password_async",
    "CredentialManager"
]
