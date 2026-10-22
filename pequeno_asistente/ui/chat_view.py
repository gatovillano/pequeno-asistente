"""
Chat View widget for Pequeño Asistente.
Renders message bubbles, streaming responses, tool execution cards,
and code blocks with GTK4 and Libadwaita.
"""
import re
import html
from typing import Dict, Any, Optional

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk


def validate_gtk_markup(markup: str) -> bool:
    """Validates if a markup string is safe for GTK/Pango display."""
    # Temporarily replace GTK-specific <a href="..."> and </a> tags with <span> for Pango verification
    temp = re.sub(r'<a\s+href=[\'"][^\'"]*[\'"]>', '<span>', markup)
    temp = temp.replace('</a>', '</span>')
    try:
        ok, _, _, _ = Pango.parse_markup(temp, -1, '\0')
        return ok
    except Exception:
        return False


def markdown_to_pango(text: str) -> str:
    """Converts basic markdown inline elements and blocks to safe Pango/GTK markup."""
    # Escape &, <, > without escaping quotation marks
    escaped = html.escape(text, quote=False)

    # Headers
    escaped = re.sub(r"^###\s+(.+)$", r'<span size="medium" weight="bold">\1</span>', escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^##\s+(.+)$", r'<span size="large" weight="bold">\1</span>', escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^#\s+(.+)$", r'<span size="x-large" weight="bold">\1</span>', escaped, flags=re.MULTILINE)

    # Blockquotes (handle escaped &gt;)
    escaped = re.sub(r"^&gt;\s+(.+)$", r"  │ <i>\1</i>", escaped, flags=re.MULTILINE)

    # Inline code: `code`
    escaped = re.sub(r"`([^`]+)`", r'<tt><span background="#23272e" foreground="#a6e3a1"> \1 </span></tt>', escaped)

    # Bold & Italic
    escaped = re.sub(r"\*\*\*([^*]+?)\*\*\*", r"<b><i>\1</i></b>", escaped)
    escaped = re.sub(r"___([^_]+?)___", r"<b><i>\1</i></b>", escaped)

    # Bold: **text** or __text__
    escaped = re.sub(r"\*\*([^*]+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__([^_]+?)__", r"<b>\1</b>", escaped)

    # Italic: *text* or _text_
    escaped = re.sub(r"(?<![<a-zA-Z0-9])\*([^*]+?)\*(?![>a-zA-Z0-9])", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<![<a-zA-Z0-9])_([^_]+?)_(?![>a-zA-Z0-9])", r"<i>\1</i>", escaped)

    # Strikethrough: ~~text~~
    escaped = re.sub(r"~~([^~]+?)~~", r"<s>\1</s>", escaped)

    # Markdown links: [text](url) -> <a href="url">text</a>
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', escaped)

    # Bullet lists: - item or * item
    escaped = re.sub(r"^\s*[-*]\s+(.+)$", r"  • \1", escaped, flags=re.MULTILINE)

    # Numbered lists: 1. item
    escaped = re.sub(r"^\s*(\d+)\.\s+(.+)$", r"  \1. \2", escaped, flags=re.MULTILINE)

    # Horizontal rules
    escaped = re.sub(r"^(?:---|___|\*\*\*)$", r"────────────────────────", escaped, flags=re.MULTILINE)

    return escaped


def split_markdown_blocks(text: str):
    """Splits markdown into text chunks and fenced code blocks."""
    pattern = re.compile(r'```([a-zA-Z0-9_\-\+]*)\n?([\s\S]*?)```', re.MULTILINE)
    blocks = []
    last_idx = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_idx:
            pre_text = text[last_idx:start]
            if pre_text.strip():
                blocks.append(('text', pre_text.strip('\n')))
        lang = match.group(1).strip()
        code = match.group(2).rstrip('\n')
        blocks.append(('code', lang, code))
        last_idx = end
    if last_idx < len(text):
        post_text = text[last_idx:]
        if post_text.strip():
            blocks.append(('text', post_text.strip('\n')))
    return blocks


class CodeBlockWidget(Gtk.Box):
    """Card widget for code blocks with language badge and copy-to-clipboard button."""
    def __init__(self, code: str, lang: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.code = code
        self.add_css_class("code-card")

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("code-header")

        display_lang = lang if lang else "código"
        lang_lbl = Gtk.Label(label=display_lang, xalign=0)
        lang_lbl.add_css_class("code-lang-label")
        lang_lbl.set_hexpand(True)
        header.append(lang_lbl)

        self.copy_btn = Gtk.Button(label="📋 Copiar")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("code-copy-btn")
        self.copy_btn.connect("clicked", self._on_copy)
        header.append(self.copy_btn)

        self.append(header)

        # Monospace Code content
        code_lbl = Gtk.Label(label=code, wrap=True, selectable=True, xalign=0)
        code_lbl.set_wrap_mode(Pango.WrapMode.CHAR)
        code_lbl.add_css_class("code-body")
        code_lbl.set_hexpand(True)
        self.append(code_lbl)

    def _on_copy(self, _btn):
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(self.code)
            self.copy_btn.set_label("✓ Copiado")
            GLib.timeout_add(1500, self._reset_copy_label)

    def _reset_copy_label(self):
        self.copy_btn.set_label("📋 Copiar")
        return False


class ToolCard(Gtk.Box):
    """Collapsible card displaying an MCP tool call and its execution result."""
    def __init__(self, tool_name: str, arguments: Dict[str, Any]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.tool_name = tool_name
        self.arguments = arguments
        self.add_css_class("tool-card")

        # Header box
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.spinner = Gtk.Spinner()
        self.spinner.start()
        header.append(self.spinner)

        friendly_name = tool_name.replace("__", " › ")
        self.title_label = Gtk.Label(label=f"🔧 {friendly_name}", xalign=0)
        self.title_label.set_hexpand(True)
        self.title_label.add_css_class("tool-title")
        header.append(self.title_label)

        # Expander toggle button
        self.toggle_btn = Gtk.Button(icon_name="pan-down-symbolic")
        self.toggle_btn.add_css_class("flat")
        self.toggle_btn.connect("clicked", self._on_toggle)
        header.append(self.toggle_btn)

        self.append(header)

        # Details box (collapsible)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.details_box.set_visible(False)
        self.details_box.add_css_class("tool-details")

        # Arguments preview
        args_str = html.escape(str(arguments))
        args_label = Gtk.Label(
            label=f"<small><b>Parámetros:</b>\n<tt>{args_str}</tt></small>",
            use_markup=True,
            wrap=True,
            xalign=0
        )
        self.details_box.append(args_label)

        # Result preview
        self.result_label = Gtk.Label(
            label="<small><i>Ejecutando en servidor MCP...</i></small>",
            use_markup=True,
            wrap=True,
            xalign=0
        )
        self.details_box.append(self.result_label)
        self.append(self.details_box)

    def _on_toggle(self, _btn):
        vis = not self.details_box.get_visible()
        self.details_box.set_visible(vis)
        self.toggle_btn.set_icon_name("pan-up-symbolic" if vis else "pan-down-symbolic")

    def finish(self, result_text: str):
        self.spinner.stop()
        self.spinner.set_visible(False)
        
        is_error = "ERROR" in result_text or "Error" in result_text
        icon = "❌" if is_error else "✓"
        friendly_name = self.tool_name.replace("__", " › ")
        self.title_label.set_text(f"{icon} {friendly_name}")

        escaped_res = html.escape(result_text[:1200] + ("..." if len(result_text) > 1200 else ""))
        self.result_label.set_markup(f"<small><b>Resultado:</b>\n<tt>{escaped_res}</tt></small>")


class MessageBubble(Gtk.Box):
    """Message bubble for User or Assistant supporting rich Markdown formatting."""
    def __init__(self, role: str, text: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.role = role
        self.raw_text = text
        self.add_css_class("message-bubble")

        if role == "user":
            self.set_halign(Gtk.Align.END)
            self.add_css_class("user-bubble")
        else:
            self.set_halign(Gtk.Align.START)
            self.add_css_class("assistant-bubble")

        self.label = Gtk.Label(wrap=True, selectable=True, xalign=0)
        self.label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_max_width_chars(52)
        self.append(self.label)

        if text:
            self.raw_text = text
            self.finalize()

    def set_text(self, text: str):
        self.raw_text = text
        self.finalize()

    def append_chunk(self, chunk: str):
        self.raw_text += chunk
        if not self.label.get_parent():
            while child := self.get_first_child():
                self.remove(child)
            self.append(self.label)
        self.label.set_text(self.raw_text)

    def finalize(self):
        """Applies rich Markdown formatting and code blocks safely."""
        blocks = split_markdown_blocks(self.raw_text)
        if not blocks:
            self.label.set_text(self.raw_text)
            return

        # Replace streaming label with parsed blocks
        while child := self.get_first_child():
            self.remove(child)

        for item in blocks:
            if item[0] == 'text':
                lbl = Gtk.Label(wrap=True, selectable=True, xalign=0)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_max_width_chars(52)
                markup = markdown_to_pango(item[1])
                if validate_gtk_markup(markup):
                    lbl.set_markup(markup)
                else:
                    lbl.set_text(item[1])
                self.append(lbl)
            elif item[0] == 'code':
                _, lang, code = item
                widget = CodeBlockWidget(code=code, lang=lang)
                self.append(widget)


class ChatView(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self._scroll_pending = False
        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.container.set_margin_top(12)
        self.container.set_margin_bottom(12)
        self.container.set_margin_start(12)
        self.container.set_margin_end(12)
        self.set_child(self.container)

        self.current_assistant_bubble: Optional[MessageBubble] = None
        self.current_tool_card: Optional[ToolCard] = None

    def add_user_message(self, text: str):
        bubble = MessageBubble("user", text)
        self.container.append(bubble)
        self.scroll_to_bottom()

    def start_assistant_message(self) -> MessageBubble:
        self.current_assistant_bubble = MessageBubble("assistant", "")
        self.container.append(self.current_assistant_bubble)
        self.scroll_to_bottom()
        return self.current_assistant_bubble

    def append_assistant_token(self, token: str):
        if not self.current_assistant_bubble:
            self.start_assistant_message()
        self.current_assistant_bubble.append_chunk(token)
        self.scroll_to_bottom()
        return False

    def finish_assistant_message(self):
        if self.current_assistant_bubble:
            self.current_assistant_bubble.finalize()
            self.current_assistant_bubble = None
        self.scroll_to_bottom()
        return False

    def start_tool_card(self, tool_name: str, arguments: Dict[str, Any]):
        card = ToolCard(tool_name, arguments)
        self.current_tool_card = card
        self.container.append(card)
        self.scroll_to_bottom()
        return False

    def finish_tool_card(self, tool_name: str, result_text: str):
        if self.current_tool_card:
            self.current_tool_card.finish(result_text)
            self.current_tool_card = None
        self.scroll_to_bottom()
        return False

    def add_system_notice(self, text: str, is_error: bool = False):
        lbl = Gtk.Label(label=text, wrap=True, selectable=True)
        lbl.add_css_class("error-notice" if is_error else "system-notice")
        self.container.append(lbl)
        self.scroll_to_bottom()
        return False

    def clear(self):
        while child := self.container.get_first_child():
            self.container.remove(child)
        self.current_assistant_bubble = None
        self.current_tool_card = None

    def scroll_to_bottom(self):
        if not self._scroll_pending:
            self._scroll_pending = True
            GLib.idle_add(self._do_scroll)

    def _do_scroll(self):
        self._scroll_pending = False
        adj = self.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False
