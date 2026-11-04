# Habilidad: Solicitud Segura de Contraseñas y Distinción Sudo vs Terminal MCP

Esta habilidad define el protocolo estricto para solicitar credenciales de forma segura y **distinguir con precisión cuándo ejecutar comandos administrativos con sudo frente a comandos ordinarios de consola mediante herramientas MCP de terminal**.

---

## 🔒 Regla Fundamental de Privacidad
**BAJO NINGUNA CIRCUNSTANCIA solicites al usuario que escriba una contraseña, token sensible o clave privada en el chat de texto plano.**
El historial de mensajes se guarda y puede ser enviado a proveedores de LLM. Para proteger la privacidad del usuario, debes utilizar siempre los mecanismos seguros dedicados.

---

## ⚖️ Distinción Obligatoria: ¿Cuándo usar `security__execute_sudo` vs Terminal MCP?

| Tipo de Comando | Ejemplos | Herramienta a Usar |
| :--- | :--- | :--- |
| **Comandos Ordinarios de Usuario (NO SUDO)** | `ls`, `pwd`, `cat`, `head`, `tail`, `grep`, `find`, `git status`, `git commit`, `python script.py`, `node app.js`, `npm install`, `curl -I`, `df -h`, `free -m`, `ps aux`, scripts de usuario | **Herramientas de Terminal MCP** (ej: `desktop-bash` / `execute_command`) o indicar al usuario que use la Terminal Integrada (`Ctrl+T`). **¡NUNCA `security__execute_sudo`!** |
| **Comandos Administrativos ROOT (SUDO)** | `apt install`, `apt update`, `pacman -S`, `dnf install`, `systemctl restart/start/stop/enable`, `ufw allow`, modificar `/etc/*`, montar discos (`mount`, `umount`), crear usuarios | **`security__execute_sudo`** (abrirá el diálogo seguro para la contraseña de sudo si es necesario). |

> [!WARNING]
> Si intentas llamar a `security__execute_sudo` con comandos ordinarios como `ls`, `cat`, `git` o diagnósticos de usuario, el sistema **rechazará la llamada** para proteger al usuario de elevaciones de privilegios innecesarias y prompts molestos.

---

## ⚡ Caso 1: Ejecución de Comandos Administrativos con Sudo
Cuando una tarea requiera OBLIGATORIAMENTE permisos de administrador del sistema:

1. Invoca la herramienta `security__execute_sudo`:
   ```json
   {
     "command": "apt update && apt install -y curl",
     "reason": "Instalar la herramienta de red curl a nivel de sistema para diagnósticos."
   }
   ```
2. Si el sistema requiere la contraseña de superusuario, se abrirá automáticamente el diálogo gráfico seguro en pantalla. Si la contraseña ya fue introducida recientemente (dentro de los últimos 5 minutos), el sistema utilizará la sesión autorizada en caché.
3. La herramienta retornará `stdout`, `stderr` y el código de salida (`exit_code`). Informa el resultado al usuario de manera concisa.

---

## 💻 Caso 2: Comandos Ordinarios de Consola (Terminal MCP)
Para cualquier tarea que no altere el sistema operativo a nivel root:
- Inspeccionar archivos o directorios del usuario (`ls -la`, `cat archivo.py`).
- Operaciones de Git (`git status`, `git diff`, `git log`).
- Ejecutar pruebas, compilaciones o scripts locales (`pytest`, `npm run dev`, `cargo build`).
- Diagnósticos de usuario (`whoami`, `uname -a`, `df -h`).

👉 **Utiliza SIEMPRE las herramientas regulares del servidor MCP de terminal (`desktop-bash` / `execute_command`)**.
Si no hay un servidor MCP de terminal activo, dile al usuario:
*"Puedes ejecutar el comando en la Terminal Interactiva pulsando `Ctrl+T` o el botón `[>_]`:"*
```bash
ls -la ~/Proyectos
```

---

## 🔑 Caso 3: Solicitar Contraseñas o Credenciales para Configuración
Cuando requieras configurar una base de datos (PostgreSQL, MySQL, Redis), un token de servicio, clave SSH o contraseña de aplicación:

1. Invoca la herramienta `security__request_credential`:
   ```json
   {
     "target": "postgres",
     "prompt": "Por favor ingresa la contraseña para el usuario 'postgres' para configurar la base de datos local.",
     "username": "postgres"
   }
   ```
2. El sistema abrirá un diálogo gráfico nativo con campo protegido (`Gtk.PasswordEntry`), botón de ver/ocultar y advertencia de mayúsculas activadas.
3. Al confirmar el usuario, la herramienta te devolverá un estado `"granted"` con un `credential_id` único (ej. `"cred_a1b2c3d4"`). El secreto real permanece custodiado en memoria y nunca es impreso en texto plano.
4. Si necesitas escribir esta credencial en un archivo de configuración, usa `security__apply_credential`:
   ```json
   {
     "credential_id": "cred_a1b2c3d4",
     "destination_file": "~/.pgpass",
     "file_template": "localhost:5432:*:postgres:{secret}\n"
   }
   ```
   El sistema escribirá el archivo con permisos restrictivos `0600` (solo legible por el usuario).
