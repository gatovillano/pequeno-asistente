"""
Secure Password Prompt Dialog for Pequeño Asistente.
Built with GTK4 and Libadwaita.
Provides a modern modal dialog requesting credentials, masked password input,
Caps-Lock alert, and non-blocking asynchronous or synchronous resolution.
"""
import os
import getpass
import asyncio
import threading
from typing import Optional, Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

from pequeno_asistente.security.credential import SecureCredential


class PasswordPromptDialog(Adw.Window):
    """
    Modern Libadwaita dialog window requesting a password or credential securely.
    """
    def __init__(
        self,
        title: str = "Autenticación requerida",
        prompt: str = "Introduce la contraseña para continuar:",
        target: str = "sudo",
        username: Optional[str] = None,
        show_username_field: bool = False,
        parent: Optional[Gtk.Window] = None,
        on_result: Optional[Callable[[SecureCredential], None]] = None
    ):
        super().__init__()
        self.target = target
        self.prompt_text = prompt
        self.on_result = on_result
        self._completed = False

        default_user = username or (getpass.getuser() if target == "sudo" else "")
        self.username = default_user

        # Configure window properties
        self.set_title(title)
        self.set_default_size(380, 260)
        self.set_resizable(False)
        self.set_modal(True)
        if parent:
            self.set_transient_for(parent)

        self.add_css_class("asistente-password-dialog")
        if not Adw.StyleManager.get_default().get_dark():
            self.add_css_class("theme-light")

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_top(24)
        main_box.set_margin_bottom(24)
        main_box.set_margin_start(28)
        main_box.set_margin_end(28)
        self.set_content(main_box)

        # Header area: Icon & Title
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header_box.set_halign(Gtk.Align.START)

        # Shield / Lock Icon
        icon_name = "dialog-password-symbolic" if target != "sudo" else "security-high-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(36)
        icon.add_css_class("password-dialog-icon")
        header_box.append(icon)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl_title = Gtk.Label(label=f"<b>{title}</b>", use_markup=True, xalign=0)
        lbl_title.add_css_class("title-3")
        title_col.append(lbl_title)

        if self.target == "sudo":
            tag_lbl = Gtk.Label(label="Permisos de Superusuario (root)", xalign=0)
            tag_lbl.add_css_class("dim-label")
            tag_lbl.add_css_class("caption")
            title_col.append(tag_lbl)
        header_box.append(title_col)
        main_box.append(header_box)

        # Descriptive prompt
        lbl_prompt = Gtk.Label(label=prompt, xalign=0)
        lbl_prompt.set_wrap(True)
        lbl_prompt.set_max_width_chars(42)
        lbl_prompt.add_css_class("body")
        main_box.append(lbl_prompt)

        # Form fields container (styled as an Adw-like card)
        form_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form_card.add_css_class("card")
        form_card.set_margin_top(4)
        form_card.set_margin_bottom(4)

        # Optional Username entry
        self.user_entry: Optional[Gtk.Entry] = None
        if show_username_field or (username and target != "sudo"):
            user_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            user_lbl = Gtk.Label(label="Usuario:", xalign=0)
            user_lbl.set_size_request(80, -1)
            self.user_entry = Gtk.Entry()
            self.user_entry.set_text(self.username)
            self.user_entry.set_hexpand(True)
            user_row.append(user_lbl)
            user_row.append(self.user_entry)
            form_card.append(user_row)

        # Password Entry
        pass_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.password_entry = Gtk.PasswordEntry()
        self.password_entry.set_show_peek_icon(True)
        self.password_entry.set_hexpand(True)
        self.password_entry.connect("activate", lambda _e: self._on_confirm())
        pass_row.append(self.password_entry)
        form_card.append(pass_row)

        main_box.append(form_card)

        # Buttons footer
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        self.btn_cancel = Gtk.Button(label="Cancelar")
        self.btn_cancel.connect("clicked", lambda _b: self._on_cancel())
        btn_box.append(self.btn_cancel)

        self.btn_confirm = Gtk.Button(label="Aceptar")
        self.btn_confirm.add_css_class("suggested-action")
        self.btn_confirm.connect("clicked", lambda _b: self._on_confirm())
        btn_box.append(self.btn_confirm)

        main_box.append(btn_box)

        # Keyboard controller for ESC key
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Connect close-request to ensure cancel is fired if closed via window manager
        self.connect("close-request", self._on_close_request)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self._on_cancel()
            return True
        return False

    def _on_close_request(self, _win):
        if not self._completed:
            self._on_cancel()
        return False

    def _on_confirm(self):
        if self._completed:
            return
        self._completed = True
        secret = self.password_entry.get_text()
        user = self.user_entry.get_text() if self.user_entry else self.username
        cred = SecureCredential(
            target=self.target,
            secret=secret,
            username=user,
            purpose=self.prompt_text,
            cancelled=False
        )
        self.close()
        if self.on_result:
            self.on_result(cred)

    def _on_cancel(self):
        if self._completed:
            return
        self._completed = True
        user = self.user_entry.get_text() if self.user_entry else self.username
        cred = SecureCredential(
            target=self.target,
            secret="",
            username=user,
            purpose=self.prompt_text,
            cancelled=True
        )
        self.close()
        if self.on_result:
            self.on_result(cred)


async def prompt_password_async(
    title: str = "Autenticación requerida",
    prompt: str = "Introduce la contraseña:",
    target: str = "sudo",
    username: Optional[str] = None,
    show_username_field: bool = False,
    parent: Optional[Gtk.Window] = None
) -> SecureCredential:
    """
    Asynchronously prompts the user for a password using the GTK4 dialog.
    Returns a SecureCredential object.
    Can be awaited from any asyncio coroutine.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[SecureCredential] = loop.create_future()

    def _deliver_result(cred: SecureCredential):
        if not future.done():
            loop.call_soon_threadsafe(future.set_result, cred)

    def _show_ui():
        dialog = PasswordPromptDialog(
            title=title,
            prompt=prompt,
            target=target,
            username=username,
            show_username_field=show_username_field,
            parent=parent,
            on_result=_deliver_result
        )
        dialog.present()

    GLib.idle_add(_show_ui)
    return await future


def prompt_password_sync(
    title: str = "Autenticación requerida",
    prompt: str = "Introduce la contraseña:",
    target: str = "sudo",
    username: Optional[str] = None,
    show_username_field: bool = False,
    parent: Optional[Gtk.Window] = None
) -> SecureCredential:
    """
    Synchronously prompts the user for a password (e.g. for standalone askpass script).
    Runs its own GTK iteration loop if needed.
    """
    from gi.repository import Gio
    app = Adw.Application(
        application_id=f"com.gatovillano.askpass.p{os.getpid()}",
        flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    result_cred: Optional[SecureCredential] = None

    def on_activate(application):
        nonlocal result_cred

        def on_done(cred: SecureCredential):
            nonlocal result_cred
            result_cred = cred
            application.quit()

        dialog = PasswordPromptDialog(
            title=title,
            prompt=prompt,
            target=target,
            username=username,
            show_username_field=show_username_field,
            parent=parent,
            on_result=on_done
        )
        dialog.set_application(application)
        dialog.present()

    app.connect("activate", on_activate)
    app.run(None)

    if result_cred is None:
        return SecureCredential(target=target, cancelled=True)
    return result_cred
