"""
Standalone SUDO_ASKPASS and SSH_ASKPASS graphical helper.
Invoked by sudo (-A or without tty) to prompt for the password via GTK4 Libadwaita dialog.
Prints the password to standard output and exits with status 0 upon success.
"""
import sys
import os

from pequeno_asistente.security.dialog import prompt_password_sync


def main():
    prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else "Introduce la contraseña de sudo:"
    
    # Clean up standard sudo prompt format like "[sudo] password for gato:"
    title = "Autenticación de Superusuario (sudo)"
    if "password for" in prompt.lower():
        parts = prompt.split(":")
        title = parts[0].strip() if parts else title

    cred = prompt_password_sync(
        title=title,
        prompt=prompt,
        target="sudo"
    )

    if cred.is_valid:
        # sudo expects the password on standard output followed by a newline
        sys.stdout.write(cred.get_secret() + "\n")
        sys.stdout.flush()
        sys.exit(0)
    else:
        # Cancelled or closed
        sys.stderr.write("Operación cancelada por el usuario.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
