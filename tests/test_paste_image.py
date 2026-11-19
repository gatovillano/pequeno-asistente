import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from pequeno_asistente.ui.window import MainWindow


class TestClipboardImagePaste(unittest.TestCase):
    def _make_dummy_window(self):
        win = MainWindow.__new__(MainWindow)
        win.text_view = Gtk.TextView()
        win.pending_attachments = []
        win.add_attachment = MagicMock()
        return win

    def test_try_paste_ignores_plain_text(self):
        win = self._make_dummy_window()
        clipboard = MagicMock()
        clipboard.get_formats.return_value = Gdk.ContentFormats.new(["text/plain", "UTF8_STRING"])
        win.text_view.get_clipboard = MagicMock(return_value=clipboard)

        result = win._try_paste_image_from_clipboard()
        self.assertFalse(result)
        clipboard.read_texture_async.assert_not_called()
        clipboard.read_value_async.assert_not_called()

    def test_try_paste_detects_image_mime_and_reads_texture(self):
        win = self._make_dummy_window()
        clipboard = MagicMock()
        clipboard.get_formats.return_value = Gdk.ContentFormats.new(["image/png", "text/plain"])
        win.text_view.get_clipboard = MagicMock(return_value=clipboard)

        result = win._try_paste_image_from_clipboard()
        self.assertTrue(result)
        clipboard.read_texture_async.assert_called_once()

    def test_try_paste_detects_uri_list_and_reads_filelist(self):
        win = self._make_dummy_window()
        clipboard = MagicMock()
        clipboard.get_formats.return_value = Gdk.ContentFormats.new(["text/uri-list"])
        win.text_view.get_clipboard = MagicMock(return_value=clipboard)

        result = win._try_paste_image_from_clipboard()
        self.assertTrue(result)
        clipboard.read_value_async.assert_called_once()
        args = clipboard.read_value_async.call_args[0]
        self.assertEqual(args[0], Gdk.FileList)

    def test_on_text_view_paste_stops_emission_only_for_images(self):
        win = self._make_dummy_window()
        tv = MagicMock(spec=Gtk.TextView)

        win._try_paste_image_from_clipboard = MagicMock(return_value=True)
        win._on_text_view_paste_clipboard(tv)
        tv.stop_emission_by_name.assert_called_once_with("paste-clipboard")

        tv.reset_mock()
        win._try_paste_image_from_clipboard = MagicMock(return_value=False)
        win._on_text_view_paste_clipboard(tv)
        tv.stop_emission_by_name.assert_not_called()

    def test_save_pasted_texture_and_attach(self):
        win = self._make_dummy_window()
        data = bytes([10, 200, 30, 255] * 4)
        texture = Gdk.MemoryTexture.new(
            2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), 8
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            win._get_pasted_images_dir = MagicMock(return_value=Path(tmpdir))
            clipboard = MagicMock()
            clipboard.read_texture_finish.return_value = texture

            win._on_clipboard_texture_ready(clipboard, MagicMock(), ["image/png"])

            win.add_attachment.assert_called_once()
            saved_path = win.add_attachment.call_args[0][0]
            self.assertTrue(os.path.isfile(saved_path))
            self.assertTrue(saved_path.endswith(".png"))
            self.assertGreater(os.path.getsize(saved_path), 0)

    def test_on_clipboard_filelist_ready_attaches_existing_file(self):
        win = self._make_dummy_window()
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            gfile = Gio.File.new_for_path(tmp.name)
            file_list = Gdk.FileList.new_from_list([gfile])
            clipboard = MagicMock()
            clipboard.read_value_finish.return_value = file_list

            win._on_clipboard_filelist_ready(clipboard, MagicMock(), None)
            win.add_attachment.assert_called_once_with(tmp.name)


if __name__ == "__main__":
    unittest.main()
