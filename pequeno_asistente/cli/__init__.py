"""
CLI package for Pequeño Asistente.
Provides both a full-featured interactive terminal REPL/TUI and scriptable one-shot/subcommand modes.
"""
from pequeno_asistente.cli.app import main, PequenoAsistenteCLI

__all__ = ["main", "PequenoAsistenteCLI"]
