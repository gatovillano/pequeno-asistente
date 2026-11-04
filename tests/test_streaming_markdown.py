import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from pequeno_asistente.ui.chat_view import (
    split_markdown_blocks,
    markdown_to_pango,
    validate_gtk_markup,
    MessageBubble,
    CodeBlockWidget,
    TableWidget,
)

class TestStreamingMarkdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_split_markdown_blocks_unclosed_code(self):
        # When model streams code that is not closed yet
        text = "Explicación previa:\n```python\ndef foo():\n    return 42"
        blocks = split_markdown_blocks(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], "text")
        self.assertEqual(blocks[0][1], "Explicación previa:")
        self.assertEqual(blocks[1][0], "code")
        self.assertEqual(blocks[1][1], "python")
        self.assertEqual(blocks[1][2], "def foo():\n    return 42")

    def test_split_markdown_blocks_closed_then_unclosed(self):
        text = "Bloque 1:\n```bash\necho 1\n```\nTexto intermedio\n```python\nprint(2)"
        blocks = split_markdown_blocks(text)
        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0], ("text", "Bloque 1:"))
        self.assertEqual(blocks[1], ("code", "bash", "echo 1"))
        self.assertEqual(blocks[2], ("text", "Texto intermedio"))
        self.assertEqual(blocks[3], ("code", "python", "print(2)"))

    def test_message_bubble_streaming_rendering(self):
        bubble = MessageBubble(role="assistant", text="")
        self.assertEqual(len(bubble._rendered_items), 0)

        # 1. Stream initial text
        bubble.append_chunk("Hola ")
        bubble.append_chunk("**mundo** ")
        self.assertEqual(len(bubble._rendered_items), 1)
        self.assertEqual(bubble._rendered_items[0]["type"], "text")
        initial_label = bubble._rendered_items[0]["widget"]
        # Verify that pango markup was parsed: GTK's get_text() strips <b> tags and returns "Hola mundo "
        self.assertEqual(initial_label.get_text(), "Hola mundo ")
        self.assertIn("**mundo**", bubble.raw_text)

        # 2. Stream open code block
        bubble.append_chunk("\n```python\nx = 10")
        self.assertEqual(len(bubble._rendered_items), 2)
        self.assertEqual(bubble._rendered_items[1]["type"], "code")
        code_widget = bubble._rendered_items[1]["widget"]
        self.assertIsInstance(code_widget, CodeBlockWidget)
        self.assertEqual(code_widget.code, "x = 10")

        # 3. Stream next code chunk (should update in-place, NOT recreate widget)
        bubble.append_chunk("\ny = 20")
        self.assertEqual(len(bubble._rendered_items), 2)
        self.assertIs(bubble._rendered_items[1]["widget"], code_widget)
        self.assertEqual(code_widget.code, "x = 10\ny = 20")

        # 4. Stream code block close and trailing text
        bubble.append_chunk("\n```\nFin de la explicación.")
        self.assertEqual(len(bubble._rendered_items), 3)
        self.assertEqual(bubble._rendered_items[2]["type"], "text")
        self.assertIs(bubble._rendered_items[1]["widget"], code_widget)

        # 5. Finalize
        bubble.finalize()
        self.assertEqual(len(bubble._rendered_items), 3)
        self.assertIn("Fin de la explicación.", bubble.raw_text)

    def test_message_bubble_table_streaming(self):
        bubble = MessageBubble(role="assistant", text="")
        # Stream table header and separator
        bubble.append_chunk("| Col1 | Col2 |\n| --- | --- |\n| A | B |")
        self.assertEqual(len(bubble._rendered_items), 1)
        self.assertEqual(bubble._rendered_items[0]["type"], "table")
        self.assertIsInstance(bubble._rendered_items[0]["widget"], TableWidget)

        # Stream another row
        bubble.append_chunk("\n| C | D |")
        self.assertEqual(len(bubble._rendered_items), 1)
        self.assertEqual(bubble._rendered_items[0]["type"], "table")

    def test_markdown_headers_all_levels(self):
        md = (
            "# Título H1\n"
            "## Título H2\n"
            "### Título H3\n"
            "#### 6. Respaldo de seguridad adicional (Dumps lógicos)\n"
            "##### Subtítulo H5\n"
            "###### Nota H6 ###\n"
        )
        markup = markdown_to_pango(md)
        self.assertTrue(validate_gtk_markup(markup))
        self.assertNotIn("#", markup.replace("#f8fafc", "").replace("#89b4fa", "").replace("#cdd6f4", "").replace("#a6adc8", "").replace("#0f172a", "").replace("#2563eb", "").replace("#1e293b", "").replace("#475569", ""))

        bubble = MessageBubble(role="assistant", text=md)
        lbl = bubble.label
        self.assertIsNotNone(lbl)
        rendered_plain = lbl.get_text()
        self.assertIn("6. Respaldo de seguridad adicional (Dumps lógicos)", rendered_plain)
        self.assertNotIn("####", rendered_plain)

    def test_markdown_lists_and_inline_code_protection(self):
        md = (
            "* Primer ítem con `SELECT * FROM tabla_1`\n"
            "* Segundo ítem con `SELECT * FROM tabla_2`\n"
            "* Tercer ítem normal"
        )
        markup = markdown_to_pango(md)
        self.assertTrue(validate_gtk_markup(markup))
        self.assertEqual(markup.count("•"), 3)

if __name__ == '__main__':
    unittest.main()
