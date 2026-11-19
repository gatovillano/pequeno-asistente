"""Entrypoint for `python -m pequeno_asistente.cli`."""
import sys
from pequeno_asistente.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
