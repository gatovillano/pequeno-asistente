import unittest
from unittest.mock import MagicMock, patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib

from pequeno_asistente.ui.tray import StatusNotifierTray, _load_icon_pixmaps
from pequeno_asistente.ui.window import MainWindow


class TestNativeTrayIcon(unittest.TestCase):
    def test_load_icon_pixmaps_returns_argb_sizes(self):
        pixmaps = _load_icon_pixmaps()
        self.assertEqual([p[0] for p in pixmaps], [16, 22, 32, 48])
        for w, h, data in pixmaps:
            self.assertEqual(w, h)
            self.assertEqual(len(data), w * h * 4)

    def test_sni_properties_and_activate(self):
        on_toggle = MagicMock()
        tray = StatusNotifierTray(on_toggle=on_toggle)

        self.assertEqual(
            tray._on_sni_get_property(None, None, None, None, "Id").unpack(),
            "pequeno-asistente",
        )
        self.assertEqual(
            tray._on_sni_get_property(None, None, None, None, "Category").unpack(),
            "ApplicationStatus",
        )
        self.assertFalse(
            tray._on_sni_get_property(None, None, None, None, "ItemIsMenu").unpack()
        )
        self.assertEqual(
            tray._on_sni_get_property(None, None, None, None, "Menu").unpack(),
            "/MenuBar",
        )

        invocation = MagicMock()
        with patch("pequeno_asistente.ui.tray.GLib.idle_add") as mock_idle:
            tray._on_sni_method_call(
                None, None, None, None, "Activate", GLib.Variant("(ii)", (0, 0)), invocation
            )
            mock_idle.assert_called_once_with(on_toggle)
        invocation.return_value.assert_called_once_with(None)

    def test_dbusmenu_layout_and_events(self):
        on_toggle = MagicMock()
        on_new = MagicMock()
        on_settings = MagicMock()
        on_quit = MagicMock()

        tray = StatusNotifierTray(
            on_toggle=on_toggle,
            on_new_thread=on_new,
            on_settings=on_settings,
            on_quit=on_quit,
        )

        inv = MagicMock()
        tray._on_menu_method_call(
            None, None, None, None, "GetLayout", GLib.Variant("(iias)", (0, -1, [])), inv
        )
        layout_variant = inv.return_value.call_args[0][0]
        rev, root = layout_variant.unpack()
        self.assertEqual(rev, 1)
        self.assertEqual(root[0], 0)
        self.assertGreaterEqual(len(root[2]), 4)

        with patch("pequeno_asistente.ui.tray.GLib.idle_add") as mock_idle:
            for item_id, expected_cb in [
                (1, on_toggle),
                (2, on_new),
                (4, on_settings),
                (6, on_quit),
            ]:
                ev_inv = MagicMock()
                tray._on_menu_method_call(
                    None,
                    None,
                    None,
                    None,
                    "Event",
                    GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("s", ""), 0)),
                    ev_inv,
                )
                self.assertEqual(mock_idle.call_args[0][0], expected_cb)

    def test_minimize_hides_window_when_tray_registered(self):
        win = MainWindow.__new__(MainWindow)
        win._was_minimized = False
        win.tray_icon = MagicMock(is_registered=True)
        win.get_visible = MagicMock(return_value=True)
        win.set_visible = MagicMock()

        surface = MagicMock()
        surface.get_state.return_value = Gdk.ToplevelState.MINIMIZED

        win._on_surface_state_changed(surface, None)
        win.set_visible.assert_called_once_with(False)
        self.assertTrue(win._was_minimized)

        # Does not hide if tray is not registered
        win2 = MainWindow.__new__(MainWindow)
        win2._was_minimized = False
        win2.tray_icon = MagicMock(is_registered=False)
        win2.get_visible = MagicMock(return_value=True)
        win2.set_visible = MagicMock()
        win2._on_surface_state_changed(surface, None)
        win2.set_visible.assert_not_called()


if __name__ == "__main__":
    unittest.main()
