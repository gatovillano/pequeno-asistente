"""
Socket IPC, Native StatusNotifierItem Tray Icon, and Toggle Server for Pequeño Asistente.
Allows waybar, KDE Plasma, GNOME (AppIndicator), XFCE, hyprland shortcuts, or CLI to show/hide/toggle the window.
"""
import os
import sys
import socket
import logging
import threading
from pathlib import Path
from typing import Callable, Optional, List, Tuple, Dict, Any

import gi
gi.require_version('GLib', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import GLib, Gio
try:
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
except Exception:
    GdkPixbuf = None

logger = logging.getLogger("pequeno_asistente.tray")

SOCKET_PATH = Path.home() / ".config" / "pequeno-asistente" / "asistente.sock"

def send_toggle_command() -> bool:
    """
    Sends a toggle command to a running assistant instance.
    Returns True if successfully sent, False if server is not running.
    """
    if not SOCKET_PATH.exists():
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(b"toggle\n")
        response = client.recv(1024)
        client.close()
        return True
    except Exception:
        # Stale socket
        try:
            SOCKET_PATH.unlink()
        except Exception:
            pass
        return False


def send_new_thread_command() -> bool:
    """
    Sends a new thread command to a running assistant instance.
    Returns True if successfully sent, False if server is not running.
    """
    if not SOCKET_PATH.exists():
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(b"new_thread\n")
        response = client.recv(1024)
        client.close()
        return True
    except Exception:
        return False


def send_quit_command() -> bool:
    """
    Sends a quit command to terminate the running assistant instance cleanly.
    Returns True if successfully sent, False if server is not running.
    """
    if not SOCKET_PATH.exists():
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(SOCKET_PATH))
        client.sendall(b"quit\n")
        response = client.recv(1024)
        client.close()
        return True
    except Exception:
        return False


class ToggleSocketServer:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_show: Callable[[], None],
        on_hide: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_new_thread: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None
    ):
        self.on_toggle = on_toggle
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_settings = on_settings
        self.on_clear = on_clear
        self.on_new_thread = on_new_thread
        self.on_quit = on_quit
        self.running = False
        self.server_thread: Optional[threading.Thread] = None

    def start(self):
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass


_SNI_XML = """
<node>
  <interface name='org.kde.StatusNotifierItem'>
    <property name='Category' type='s' access='read'/>
    <property name='Id' type='s' access='read'/>
    <property name='Title' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='WindowId' type='u' access='read'/>
    <property name='IconName' type='s' access='read'/>
    <property name='IconThemePath' type='s' access='read'/>
    <property name='IconPixmap' type='a(iiay)' access='read'/>
    <property name='OverlayIconName' type='s' access='read'/>
    <property name='OverlayIconPixmap' type='a(iiay)' access='read'/>
    <property name='AttentionIconName' type='s' access='read'/>
    <property name='AttentionIconPixmap' type='a(iiay)' access='read'/>
    <property name='AttentionMovieName' type='s' access='read'/>
    <property name='ToolTip' type='(sa(iiay)ss)' access='read'/>
    <property name='ItemIsMenu' type='b' access='read'/>
    <property name='Menu' type='o' access='read'/>
    <method name='ContextMenu'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='Activate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='SecondaryActivate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='Scroll'>
      <arg type='i' name='delta' direction='in'/>
      <arg type='s' name='orientation' direction='in'/>
    </method>
    <signal name='NewTitle'/>
    <signal name='NewIcon'/>
    <signal name='NewAttentionIcon'/>
    <signal name='NewOverlayIcon'/>
    <signal name='NewToolTip'/>
    <signal name='NewStatus'>
      <arg type='s' name='status'/>
    </signal>
  </interface>
</node>
"""

_DBUSMENU_XML = """
<node>
  <interface name='com.canonical.dbusmenu'>
    <property name='Version' type='u' access='read'/>
    <property name='TextDirection' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconThemePath' type='as' access='read'/>
    <method name='GetLayout'>
      <arg type='i' name='parentId' direction='in'/>
      <arg type='i' name='recursionDepth' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='u' name='revision' direction='out'/>
      <arg type='(ia{sv}av)' name='layout' direction='out'/>
    </method>
    <method name='GetGroupProperties'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='a(ia{sv})' name='properties' direction='out'/>
    </method>
    <method name='GetProperty'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='name' direction='in'/>
      <arg type='v' name='value' direction='out'/>
    </method>
    <method name='Event'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='eventId' direction='in'/>
      <arg type='v' name='data' direction='in'/>
      <arg type='u' name='timestamp' direction='in'/>
    </method>
    <method name='EventGroup'>
      <arg type='a(isvu)' name='events' direction='in'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <method name='AboutToShow'>
      <arg type='i' name='id' direction='in'/>
      <arg type='b' name='needUpdate' direction='out'/>
    </method>
    <method name='AboutToShowGroup'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='ai' name='updatesNeeded' direction='out'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <signal name='ItemsPropertiesUpdated'>
      <arg type='a(ia{sv})' name='updatedProps'/>
      <arg type='a(ias)' name='removedProps'/>
    </signal>
    <signal name='LayoutUpdated'>
      <arg type='u' name='revision'/>
      <arg type='i' name='parent'/>
    </signal>
    <signal name='ItemActivationRequested'>
      <arg type='i' name='id'/>
      <arg type='u' name='timestamp'/>
    </signal>
  </interface>
</node>
"""


def _load_icon_pixmaps(icon_path: Optional[Path] = None) -> List[Tuple[int, int, bytes]]:
    """Loads the application icon as ARGB32 pixmaps for StatusNotifierItem IconPixmap."""
    if GdkPixbuf is None:
        return []

    if icon_path is None:
        icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "icon.svg"
    if not icon_path.is_file():
        return []

    pixmaps: List[Tuple[int, int, bytes]] = []
    for size in (16, 22, 32, 48):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), size, size, True)
            w = pb.get_width()
            h = pb.get_height()
            channels = pb.get_n_channels()
            rowstride = pb.get_rowstride()
            raw = pb.get_pixels()

            argb = bytearray(w * h * 4)
            for y in range(h):
                row = raw[y * rowstride : y * rowstride + w * channels]
                out_row = bytearray(w * 4)
                if channels == 4:
                    out_row[0::4] = row[3::4]
                    out_row[1::4] = row[0::4]
                    out_row[2::4] = row[1::4]
                    out_row[3::4] = row[2::4]
                elif channels == 3:
                    out_row[0::4] = b"\xff" * w
                    out_row[1::4] = row[0::4]
                    out_row[2::4] = row[1::4]
                    out_row[3::4] = row[2::4]
                argb[y * w * 4 : (y + 1) * w * 4] = out_row

            pixmaps.append((w, h, bytes(argb)))
        except Exception as e:
            logger.debug(f"Could not render icon pixmap at {size}px: {e}")
    return pixmaps


class StatusNotifierTray:
    """Native D-Bus StatusNotifierItem (SNI) + DBusMenu tray icon.

    Works across KDE Plasma, Hyprland/Sway (Waybar), GNOME (AppIndicator extension),
    XFCE, Cinnamon, MATE, LXQt, and Cosmic without GTK3/AppIndicator dependencies.
    """

    SNI_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_new_thread: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        icon_path: Optional[Path] = None,
    ):
        self.on_toggle = on_toggle
        self.on_new_thread = on_new_thread
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.icon_pixmaps = _load_icon_pixmaps(icon_path)

        self._bus: Optional[Gio.DBusConnection] = None
        self._sni_reg_id: int = 0
        self._menu_reg_id: int = 0
        self._name_owner_id: int = 0
        self._watcher_id: int = 0
        self._bus_name: str = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.is_registered: bool = False

        self._sni_node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
        self._menu_node = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_XML)
        self._menu_revision: int = 1

    def start(self):
        """Exports SNI & DBusMenu objects on the session bus and watches StatusNotifierWatcher."""
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.warning(f"Session D-Bus unavailable, native tray icon disabled: {e}")
            return

        self._sni_reg_id = self._bus.register_object(
            self.SNI_PATH,
            self._sni_node.interfaces[0],
            self._on_sni_method_call,
            self._on_sni_get_property,
            None,
        )
        self._menu_reg_id = self._bus.register_object(
            self.MENU_PATH,
            self._menu_node.interfaces[0],
            self._on_menu_method_call,
            self._on_menu_get_property,
            None,
        )

        self._name_owner_id = Gio.bus_own_name_on_connection(
            self._bus,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            None,
            None,
        )

        self._watcher_id = Gio.bus_watch_name_on_connection(
            self._bus,
            "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )

    def stop(self):
        """Unregisters D-Bus objects and name watchers cleanly."""
        self.is_registered = False
        if self._watcher_id:
            Gio.bus_unwatch_name(self._watcher_id)
            self._watcher_id = 0
        if self._name_owner_id:
            Gio.bus_unown_name(self._name_owner_id)
            self._name_owner_id = 0
        if self._bus:
            if self._sni_reg_id:
                self._bus.unregister_object(self._sni_reg_id)
                self._sni_reg_id = 0
            if self._menu_reg_id:
                self._bus.unregister_object(self._menu_reg_id)
                self._menu_reg_id = 0
            self._bus = None

    def _on_watcher_appeared(self, connection: Gio.DBusConnection, _name: str, _owner: str):
        """Registers this StatusNotifierItem whenever a tray host/watcher is active."""
        try:
            connection.call(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                self._on_register_finished,
                None,
            )
        except Exception as e:
            logger.debug(f"Failed to register StatusNotifierItem: {e}")

    def _on_register_finished(self, connection: Gio.DBusConnection, result, _user_data):
        try:
            connection.call_finish(result)
            self.is_registered = True
            logger.info("Native StatusNotifierItem tray icon registered.")
        except Exception as e:
            self.is_registered = False
            logger.debug(f"StatusNotifierWatcher registration failed: {e}")

    def _on_watcher_vanished(self, _connection: Gio.DBusConnection, _name: str):
        self.is_registered = False

    def _on_sni_method_call(
        self, _conn, _sender, _obj_path, _iface_name, method_name, _params, invocation
    ):
        if method_name in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self.on_toggle)
        invocation.return_value(None)

    def _on_sni_get_property(self, _conn, _sender, _obj_path, _iface_name, prop_name):
        if prop_name == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop_name == "Id":
            return GLib.Variant("s", "pequeno-asistente")
        if prop_name == "Title":
            return GLib.Variant("s", "Pequeño Asistente")
        if prop_name == "Status":
            return GLib.Variant("s", "Active")
        if prop_name == "WindowId":
            return GLib.Variant("u", 0)
        if prop_name == "IconName":
            return GLib.Variant("s", "" if self.icon_pixmaps else "pequeno-asistente")
        if prop_name == "IconThemePath":
            return GLib.Variant("s", "")
        if prop_name == "IconPixmap":
            return GLib.Variant("a(iiay)", self.icon_pixmaps)
        if prop_name in ("OverlayIconName", "AttentionIconName", "AttentionMovieName"):
            return GLib.Variant("s", "")
        if prop_name in ("OverlayIconPixmap", "AttentionIconPixmap"):
            return GLib.Variant("a(iiay)", [])
        if prop_name == "ToolTip":
            return GLib.Variant(
                "(sa(iiay)ss)",
                ("", [], "Pequeño Asistente", "Clic para mostrar u ocultar"),
            )
        if prop_name == "ItemIsMenu":
            return GLib.Variant("b", False)
        if prop_name == "Menu":
            return GLib.Variant("o", self.MENU_PATH)
        return None

    def _menu_items(self) -> List[Tuple[int, Dict[str, GLib.Variant]]]:
        items: List[Tuple[int, Dict[str, GLib.Variant]]] = [
            (1, {
                "label": GLib.Variant("s", "Mostrar / Ocultar"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
        ]
        if self.on_new_thread:
            items.append((2, {
                "label": GLib.Variant("s", "Nuevo chat"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        if self.on_settings:
            items.append((3, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            items.append((4, {
                "label": GLib.Variant("s", "Configuración"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        if self.on_quit:
            items.append((5, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            items.append((6, {
                "label": GLib.Variant("s", "Salir"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        return items

    def _dispatch_menu_click(self, item_id: int):
        if item_id == 1 and self.on_toggle:
            GLib.idle_add(self.on_toggle)
        elif item_id == 2 and self.on_new_thread:
            GLib.idle_add(self.on_new_thread)
        elif item_id == 4 and self.on_settings:
            GLib.idle_add(self.on_settings)
        elif item_id == 6 and self.on_quit:
            GLib.idle_add(self.on_quit)

    def _on_menu_get_property(self, _conn, _sender, _obj_path, _iface_name, prop_name):
        if prop_name == "Version":
            return GLib.Variant("u", 3)
        if prop_name == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop_name == "Status":
            return GLib.Variant("s", "normal")
        if prop_name == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _on_menu_method_call(
        self, _conn, _sender, _obj_path, _iface_name, method_name, params, invocation
    ):
        if method_name == "GetLayout":
            parent_id, _depth, _prop_names = params.unpack()
            if parent_id == 0:
                children = [
                    GLib.Variant("(ia{sv}av)", (item_id, props, []))
                    for item_id, props in self._menu_items()
                ]
                root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            else:
                props = dict(self._menu_items()).get(parent_id, {})
                root = (parent_id, props, [])
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._menu_revision, root)))
            return

        if method_name == "GetGroupProperties":
            ids, _prop_names = params.unpack()
            items_map = dict(self._menu_items())
            result = [(i, items_map[i]) for i in ids if i in items_map]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
            return

        if method_name == "GetProperty":
            item_id, name = params.unpack()
            props = dict(self._menu_items()).get(item_id, {})
            val = props.get(name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (val,)))
            return

        if method_name == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._dispatch_menu_click(item_id)
            invocation.return_value(None)
            return

        if method_name == "EventGroup":
            events = params.unpack()[0]
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self._dispatch_menu_click(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            return

        if method_name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        if method_name == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return

        invocation.return_value(None)

        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(str(SOCKET_PATH))
        self.server_sock.listen(5)
        self.running = True

        self.server_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.server_thread.start()

    def _listen_loop(self):
        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                data = conn.recv(1024).decode("utf-8").strip()
                if data == "toggle":
                    GLib.idle_add(self.on_toggle)
                    conn.sendall(b"OK: toggled\n")
                elif data == "show":
                    GLib.idle_add(self.on_show)
                    conn.sendall(b"OK: shown\n")
                elif data == "hide":
                    if self.on_hide:
                        GLib.idle_add(self.on_hide)
                    conn.sendall(b"OK: hidden\n")
                elif data == "settings":
                    if self.on_settings:
                        GLib.idle_add(self.on_settings)
                    conn.sendall(b"OK: settings\n")
                elif data == "clear":
                    if self.on_clear:
                        GLib.idle_add(self.on_clear)
                    conn.sendall(b"OK: cleared\n")
                elif data in ("new", "new_thread"):
                    if self.on_new_thread:
                        GLib.idle_add(self.on_new_thread)
                    conn.sendall(b"OK: new_thread\n")
                elif data in ("quit", "exit"):
                    if self.on_quit:
                        GLib.idle_add(self.on_quit)
                    conn.sendall(b"OK: quitting\n")
                elif data == "ping":
                    conn.sendall(b"PONG\n")
                conn.close()
            except Exception:
                if not self.running:
                    break

    def stop(self):
        self.running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass


_SNI_XML = """
<node>
  <interface name='org.kde.StatusNotifierItem'>
    <property name='Category' type='s' access='read'/>
    <property name='Id' type='s' access='read'/>
    <property name='Title' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='WindowId' type='u' access='read'/>
    <property name='IconName' type='s' access='read'/>
    <property name='IconThemePath' type='s' access='read'/>
    <property name='IconPixmap' type='a(iiay)' access='read'/>
    <property name='OverlayIconName' type='s' access='read'/>
    <property name='OverlayIconPixmap' type='a(iiay)' access='read'/>
    <property name='AttentionIconName' type='s' access='read'/>
    <property name='AttentionIconPixmap' type='a(iiay)' access='read'/>
    <property name='AttentionMovieName' type='s' access='read'/>
    <property name='ToolTip' type='(sa(iiay)ss)' access='read'/>
    <property name='ItemIsMenu' type='b' access='read'/>
    <property name='Menu' type='o' access='read'/>
    <method name='ContextMenu'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='Activate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='SecondaryActivate'>
      <arg type='i' name='x' direction='in'/>
      <arg type='i' name='y' direction='in'/>
    </method>
    <method name='Scroll'>
      <arg type='i' name='delta' direction='in'/>
      <arg type='s' name='orientation' direction='in'/>
    </method>
    <signal name='NewTitle'/>
    <signal name='NewIcon'/>
    <signal name='NewAttentionIcon'/>
    <signal name='NewOverlayIcon'/>
    <signal name='NewToolTip'/>
    <signal name='NewStatus'>
      <arg type='s' name='status'/>
    </signal>
  </interface>
</node>
"""

_DBUSMENU_XML = """
<node>
  <interface name='com.canonical.dbusmenu'>
    <property name='Version' type='u' access='read'/>
    <property name='TextDirection' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconThemePath' type='as' access='read'/>
    <method name='GetLayout'>
      <arg type='i' name='parentId' direction='in'/>
      <arg type='i' name='recursionDepth' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='u' name='revision' direction='out'/>
      <arg type='(ia{sv}av)' name='layout' direction='out'/>
    </method>
    <method name='GetGroupProperties'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='as' name='propertyNames' direction='in'/>
      <arg type='a(ia{sv})' name='properties' direction='out'/>
    </method>
    <method name='GetProperty'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='name' direction='in'/>
      <arg type='v' name='value' direction='out'/>
    </method>
    <method name='Event'>
      <arg type='i' name='id' direction='in'/>
      <arg type='s' name='eventId' direction='in'/>
      <arg type='v' name='data' direction='in'/>
      <arg type='u' name='timestamp' direction='in'/>
    </method>
    <method name='EventGroup'>
      <arg type='a(isvu)' name='events' direction='in'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <method name='AboutToShow'>
      <arg type='i' name='id' direction='in'/>
      <arg type='b' name='needUpdate' direction='out'/>
    </method>
    <method name='AboutToShowGroup'>
      <arg type='ai' name='ids' direction='in'/>
      <arg type='ai' name='updatesNeeded' direction='out'/>
      <arg type='ai' name='idErrors' direction='out'/>
    </method>
    <signal name='ItemsPropertiesUpdated'>
      <arg type='a(ia{sv})' name='updatedProps'/>
      <arg type='a(ias)' name='removedProps'/>
    </signal>
    <signal name='LayoutUpdated'>
      <arg type='u' name='revision'/>
      <arg type='i' name='parent'/>
    </signal>
    <signal name='ItemActivationRequested'>
      <arg type='i' name='id'/>
      <arg type='u' name='timestamp'/>
    </signal>
  </interface>
</node>
"""


def _load_icon_pixmaps(icon_path: Optional[Path] = None) -> List[Tuple[int, int, bytes]]:
    """Loads the application icon as ARGB32 pixmaps for StatusNotifierItem IconPixmap."""
    if GdkPixbuf is None:
        return []

    if icon_path is None:
        icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "icon.svg"
    if not icon_path.is_file():
        return []

    pixmaps: List[Tuple[int, int, bytes]] = []
    for size in (16, 22, 32, 48):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), size, size, True)
            w = pb.get_width()
            h = pb.get_height()
            channels = pb.get_n_channels()
            rowstride = pb.get_rowstride()
            raw = pb.get_pixels()

            argb = bytearray(w * h * 4)
            for y in range(h):
                row = raw[y * rowstride : y * rowstride + w * channels]
                out_row = bytearray(w * 4)
                if channels == 4:
                    out_row[0::4] = row[3::4]
                    out_row[1::4] = row[0::4]
                    out_row[2::4] = row[1::4]
                    out_row[3::4] = row[2::4]
                elif channels == 3:
                    out_row[0::4] = b"\xff" * w
                    out_row[1::4] = row[0::4]
                    out_row[2::4] = row[1::4]
                    out_row[3::4] = row[2::4]
                argb[y * w * 4 : (y + 1) * w * 4] = out_row

            pixmaps.append((w, h, bytes(argb)))
        except Exception as e:
            logger.debug(f"Could not render icon pixmap at {size}px: {e}")
    return pixmaps


class StatusNotifierTray:
    """Native D-Bus StatusNotifierItem (SNI) + DBusMenu tray icon.

    Works across KDE Plasma, Hyprland/Sway (Waybar), GNOME (AppIndicator extension),
    XFCE, Cinnamon, MATE, LXQt, and Cosmic without GTK3/AppIndicator dependencies.
    """

    SNI_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_new_thread: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        icon_path: Optional[Path] = None,
    ):
        self.on_toggle = on_toggle
        self.on_new_thread = on_new_thread
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.icon_pixmaps = _load_icon_pixmaps(icon_path)

        self._bus: Optional[Gio.DBusConnection] = None
        self._sni_reg_id: int = 0
        self._menu_reg_id: int = 0
        self._name_owner_id: int = 0
        self._watcher_id: int = 0
        self._bus_name: str = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.is_registered: bool = False

        self._sni_node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
        self._menu_node = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_XML)
        self._menu_revision: int = 1

    def start(self):
        """Exports SNI & DBusMenu objects on the session bus and watches StatusNotifierWatcher."""
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.warning(f"Session D-Bus unavailable, native tray icon disabled: {e}")
            return

        self._sni_reg_id = self._bus.register_object(
            self.SNI_PATH,
            self._sni_node.interfaces[0],
            self._on_sni_method_call,
            self._on_sni_get_property,
            None,
        )
        self._menu_reg_id = self._bus.register_object(
            self.MENU_PATH,
            self._menu_node.interfaces[0],
            self._on_menu_method_call,
            self._on_menu_get_property,
            None,
        )

        self._name_owner_id = Gio.bus_own_name_on_connection(
            self._bus,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            None,
            None,
        )

        self._watcher_id = Gio.bus_watch_name_on_connection(
            self._bus,
            "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )

    def stop(self):
        """Unregisters D-Bus objects and name watchers cleanly."""
        self.is_registered = False
        if self._watcher_id:
            Gio.bus_unwatch_name(self._watcher_id)
            self._watcher_id = 0
        if self._name_owner_id:
            Gio.bus_unown_name(self._name_owner_id)
            self._name_owner_id = 0
        if self._bus:
            if self._sni_reg_id:
                self._bus.unregister_object(self._sni_reg_id)
                self._sni_reg_id = 0
            if self._menu_reg_id:
                self._bus.unregister_object(self._menu_reg_id)
                self._menu_reg_id = 0
            self._bus = None

    def _on_watcher_appeared(self, connection: Gio.DBusConnection, _name: str, _owner: str):
        """Registers this StatusNotifierItem whenever a tray host/watcher is active."""
        try:
            connection.call(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._bus_name,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                self._on_register_finished,
                None,
            )
        except Exception as e:
            logger.debug(f"Failed to register StatusNotifierItem: {e}")

    def _on_register_finished(self, connection: Gio.DBusConnection, result, _user_data):
        try:
            connection.call_finish(result)
            self.is_registered = True
            logger.info("Native StatusNotifierItem tray icon registered.")
        except Exception as e:
            self.is_registered = False
            logger.debug(f"StatusNotifierWatcher registration failed: {e}")

    def _on_watcher_vanished(self, _connection: Gio.DBusConnection, _name: str):
        self.is_registered = False

    def _on_sni_method_call(
        self, _conn, _sender, _obj_path, _iface_name, method_name, _params, invocation
    ):
        if method_name in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self.on_toggle)
        invocation.return_value(None)

    def _on_sni_get_property(self, _conn, _sender, _obj_path, _iface_name, prop_name):
        if prop_name == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop_name == "Id":
            return GLib.Variant("s", "pequeno-asistente")
        if prop_name == "Title":
            return GLib.Variant("s", "Pequeño Asistente")
        if prop_name == "Status":
            return GLib.Variant("s", "Active")
        if prop_name == "WindowId":
            return GLib.Variant("u", 0)
        if prop_name == "IconName":
            return GLib.Variant("s", "" if self.icon_pixmaps else "pequeno-asistente")
        if prop_name == "IconThemePath":
            return GLib.Variant("s", "")
        if prop_name == "IconPixmap":
            return GLib.Variant("a(iiay)", self.icon_pixmaps)
        if prop_name in ("OverlayIconName", "AttentionIconName", "AttentionMovieName"):
            return GLib.Variant("s", "")
        if prop_name in ("OverlayIconPixmap", "AttentionIconPixmap"):
            return GLib.Variant("a(iiay)", [])
        if prop_name == "ToolTip":
            return GLib.Variant(
                "(sa(iiay)ss)",
                ("", [], "Pequeño Asistente", "Clic para mostrar u ocultar"),
            )
        if prop_name == "ItemIsMenu":
            return GLib.Variant("b", False)
        if prop_name == "Menu":
            return GLib.Variant("o", self.MENU_PATH)
        return None

    def _menu_items(self) -> List[Tuple[int, Dict[str, GLib.Variant]]]:
        items: List[Tuple[int, Dict[str, GLib.Variant]]] = [
            (1, {
                "label": GLib.Variant("s", "Mostrar / Ocultar"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
        ]
        if self.on_new_thread:
            items.append((2, {
                "label": GLib.Variant("s", "Nuevo chat"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        if self.on_settings:
            items.append((3, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            items.append((4, {
                "label": GLib.Variant("s", "Configuración"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        if self.on_quit:
            items.append((5, {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}))
            items.append((6, {
                "label": GLib.Variant("s", "Salir"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }))
        return items

    def _dispatch_menu_click(self, item_id: int):
        if item_id == 1 and self.on_toggle:
            GLib.idle_add(self.on_toggle)
        elif item_id == 2 and self.on_new_thread:
            GLib.idle_add(self.on_new_thread)
        elif item_id == 4 and self.on_settings:
            GLib.idle_add(self.on_settings)
        elif item_id == 6 and self.on_quit:
            GLib.idle_add(self.on_quit)

    def _on_menu_get_property(self, _conn, _sender, _obj_path, _iface_name, prop_name):
        if prop_name == "Version":
            return GLib.Variant("u", 3)
        if prop_name == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop_name == "Status":
            return GLib.Variant("s", "normal")
        if prop_name == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _on_menu_method_call(
        self, _conn, _sender, _obj_path, _iface_name, method_name, params, invocation
    ):
        if method_name == "GetLayout":
            parent_id, _depth, _prop_names = params.unpack()
            if parent_id == 0:
                children = [
                    GLib.Variant("(ia{sv}av)", (item_id, props, []))
                    for item_id, props in self._menu_items()
                ]
                root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            else:
                props = dict(self._menu_items()).get(parent_id, {})
                root = (parent_id, props, [])
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._menu_revision, root)))
            return

        if method_name == "GetGroupProperties":
            ids, _prop_names = params.unpack()
            items_map = dict(self._menu_items())
            result = [(i, items_map[i]) for i in ids if i in items_map]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
            return

        if method_name == "GetProperty":
            item_id, name = params.unpack()
            props = dict(self._menu_items()).get(item_id, {})
            val = props.get(name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (val,)))
            return

        if method_name == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._dispatch_menu_click(item_id)
            invocation.return_value(None)
            return

        if method_name == "EventGroup":
            events = params.unpack()[0]
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self._dispatch_menu_click(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            return

        if method_name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        if method_name == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return

        invocation.return_value(None)
