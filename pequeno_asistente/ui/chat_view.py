"""
Chat View widget for Pequeño Asistente.
Renders message bubbles, streaming responses, tool execution cards,
code blocks, and modern interactive empty-state suggestions with GTK4 and Libadwaita.
"""
import re
import html
import json
from typing import Dict, Any, Optional, Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk


def validate_gtk_markup(markup: str) -> bool:
    """Validates if a markup string is safe for GTK/Pango display."""
    temp = re.sub(r'<a\s+href=[\'"][^\'"]*[\'"]>', '<span>', markup)
    temp = temp.replace('</a>', '</span>')
    try:
        ok, _, _, _ = Pango.parse_markup(temp, -1, '\0')
        return ok
    except Exception:
        return False


def markdown_to_pango(text: str) -> str:
    """Converts basic markdown inline elements and blocks to safe Pango/GTK markup."""
    escaped = html.escape(text, quote=False)

    # Headers
    escaped = re.sub(r"^###\s+(.+)$", r'<span size="medium" weight="bold">\1</span>', escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^##\s+(.+)$", r'<span size="large" weight="bold">\1</span>', escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^#\s+(.+)$", r'<span size="x-large" weight="bold">\1</span>', escaped, flags=re.MULTILINE)

    # Blockquotes
    escaped = re.sub(r"^&gt;\s+(.+)$", r"  │ <i>\1</i>", escaped, flags=re.MULTILINE)

    # Inline code: `code`
    escaped = re.sub(r"`([^`]+)`", r'<tt><span background="#212234" foreground="#89b4fa"> \1 </span></tt>', escaped)

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

    # Line breaks inside elements (e.g. <br> or <br/> in tables)
    escaped = re.sub(r"&lt;br\s*/?&gt;", "\n", escaped, flags=re.IGNORECASE)

    return escaped


def split_table_row(row_str: str) -> list[str]:
    """Splits a markdown table row into cells, respecting code spans and escaped pipes."""
    row_str = row_str.strip()
    if row_str.startswith('|'):
        row_str = row_str[1:]
    if row_str.endswith('|'):
        row_str = row_str[:-1]

    cells = []
    current = []
    in_code = False
    escaped = False

    for ch in row_str:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '`':
            in_code = not in_code
            current.append(ch)
        elif ch == '|' and not in_code:
            cells.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)

    cells.append(''.join(current).strip())
    return cells


def is_table_separator(row_str: str) -> bool:
    """Checks if a row is a valid Markdown table separator (e.g. |:---|:---:|---:|)."""
    cells = split_table_row(row_str)
    if not cells:
        return False
    sep_pattern = re.compile(r'^\s*:?-{1,}:?\s*$')
    return all(bool(sep_pattern.match(c)) for c in cells)


def parse_table_alignments(sep_row: str) -> list[str]:
    """Extracts column alignments ('left', 'center', 'right') from a table separator row."""
    cells = split_table_row(sep_row)
    alignments = []
    for c in cells:
        c = c.strip()
        left = c.startswith(':')
        right = c.endswith(':')
        if left and right:
            alignments.append('center')
        elif right:
            alignments.append('right')
        elif left:
            alignments.append('left')
        else:
            alignments.append('left')
    return alignments


def extract_tables_and_text(text: str):
    """Parses a text segment into alternating text and table blocks."""
    lines = text.split('\n')
    blocks = []
    current_text_lines = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # A Markdown table header and separator both must contain '|'
        if i + 1 < n and ('|' in line) and ('|' in lines[i + 1]):
            header_cells = split_table_row(line)
            if is_table_separator(lines[i + 1]) and len(header_cells) > 0:
                sep_cells = split_table_row(lines[i + 1])
                if len(header_cells) == len(sep_cells):
                    if current_text_lines:
                        txt = '\n'.join(current_text_lines).strip('\n')
                        if txt.strip():
                            blocks.append(('text', txt))
                        current_text_lines = []

                    alignments = parse_table_alignments(lines[i + 1])
                    num_cols = len(header_cells)

                    raw_lines = [line, lines[i + 1]]
                    table_rows = []
                    i += 2
                    while i < n:
                        row_line = lines[i]
                        if not row_line.strip() or '|' not in row_line:
                            break
                        raw_lines.append(row_line)
                        row_cells = split_table_row(row_line)
                        if len(row_cells) < num_cols:
                            row_cells.extend([''] * (num_cols - len(row_cells)))
                        elif len(row_cells) > num_cols:
                            row_cells = row_cells[:num_cols]
                        table_rows.append(row_cells)
                        i += 1

                    raw_table_text = '\n'.join(raw_lines)
                    blocks.append(('table', header_cells, table_rows, alignments, raw_table_text))
                    continue

        current_text_lines.append(line)
        i += 1

    if current_text_lines:
        txt = '\n'.join(current_text_lines).strip('\n')
        if txt.strip():
            blocks.append(('text', txt))

    return blocks


def split_markdown_blocks(text: str):
    """Splits markdown into text chunks, tables, and fenced code blocks."""
    pattern = re.compile(r'```([a-zA-Z0-9_\-\+]*)\n?([\s\S]*?)```', re.MULTILINE)
    blocks = []
    last_idx = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_idx:
            pre_text = text[last_idx:start]
            if pre_text.strip():
                blocks.extend(extract_tables_and_text(pre_text.strip('\n')))
        lang = match.group(1).strip()
        code = match.group(2).rstrip('\n')
        blocks.append(('code', lang, code))
        last_idx = end
    if last_idx < len(text):
        post_text = text[last_idx:]
        if post_text.strip():
            blocks.extend(extract_tables_and_text(post_text.strip('\n')))
    return blocks


class WelcomeWidget(Gtk.Box):
    """Modern compact empty state with quick action suggestion chips."""
    def __init__(self, on_suggestion_clicked: Callable[[str], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("welcome-card")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_vexpand(True)
        self.set_hexpand(True)

        # Avatar / Icon badge
        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        icon_box.add_css_class("welcome-avatar-box")
        icon_box.set_halign(Gtk.Align.CENTER)
        avatar_lbl = Gtk.Label(label="🤖")
        avatar_lbl.add_css_class("welcome-avatar")
        icon_box.append(avatar_lbl)
        self.append(icon_box)

        # Title & Subtitle
        title = Gtk.Label(label="<b>Pequeño Asistente</b>", use_markup=True)
        title.add_css_class("welcome-title")
        self.append(title)

        subtitle = Gtk.Label(label="Asistente de escritorio ágil y moderno para Linux.")
        subtitle.add_css_class("welcome-subtitle")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        subtitle.set_max_width_chars(34)
        self.append(subtitle)

        # Suggestions box
        sug_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sug_box.add_css_class("welcome-suggestions")
        sug_box.set_halign(Gtk.Align.FILL)
        sug_box.set_margin_top(4)

        suggestions = [
            ("⚡ ¿Qué herramientas tienes disponibles?", "¿Qué herramientas MCP tienes conectadas?"),
            ("🕒 ¿Qué hora es en Tokio, Londres y NY?", "¿Qué hora es en Tokio, Londres y Nueva York?"),
            ("🌐 Buscar novedades de Linux e IA hoy", "Busca en la web las últimas noticias de Inteligencia Artificial"),
            ("💻 Resumen del sistema y aplicaciones", "¿Puedes decirme los detalles del sistema y qué aplicaciones tengo abiertas?")
        ]

        for label_text, prompt_text in suggestions:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("suggestion-chip")
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip_lbl = Gtk.Label(label=label_text, xalign=0)
            chip_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            chip_lbl.set_hexpand(True)
            arrow_lbl = Gtk.Label(label="›")
            arrow_lbl.add_css_class("suggestion-arrow")
            btn_box.append(chip_lbl)
            btn_box.append(arrow_lbl)
            btn.set_child(btn_box)
            btn.connect("clicked", lambda _b, p=prompt_text: on_suggestion_clicked(p))
            sug_box.append(btn)

        self.append(sug_box)


class CodeBlockWidget(Gtk.Box):
    """Compact card widget for code blocks with language badge and copy-to-clipboard button."""
    def __init__(self, code: str, lang: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.code = code
        self.add_css_class("code-card")

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("code-header")

        display_lang = (lang if lang else "código").upper()
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


class TableWidget(Gtk.Box):
    """Modern card widget for rendering Markdown tables with horizontal scrolling and copying."""
    def __init__(self, headers: list[str], rows: list[list[str]], alignments: list[str], raw_markdown: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.raw_markdown = raw_markdown
        self.add_css_class("table-card")
        self.set_hexpand(True)

        # Header bar
        header_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_bar.add_css_class("table-header-bar")

        badge_lbl = Gtk.Label(label="📊 TABLA", xalign=0)
        badge_lbl.add_css_class("table-badge-label")
        badge_lbl.set_hexpand(True)
        header_bar.append(badge_lbl)

        self.copy_btn = Gtk.Button(label="📋 Copiar")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("code-copy-btn")
        self.copy_btn.set_tooltip_text("Copiar tabla en formato Markdown")
        self.copy_btn.connect("clicked", self._on_copy)
        header_bar.append(self.copy_btn)

        self.append(header_bar)

        # Scrolled window for horizontal overflow
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_propagate_natural_height(True)
        scroll.set_hexpand(True)
        scroll.add_css_class("table-scroll")

        # Grid containing table cells
        grid = Gtk.Grid()
        grid.set_column_spacing(0)
        grid.set_row_spacing(0)
        grid.set_hexpand(True)
        grid.add_css_class("markdown-table-grid")

        num_cols = len(headers)
        total_rows = len(rows)

        # Header cells (Row 0)
        for col_idx, h_text in enumerate(headers):
            align = alignments[col_idx] if col_idx < len(alignments) else 'left'
            cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            cell_box.add_css_class("table-cell")
            cell_box.add_css_class("table-header-cell")
            if col_idx < num_cols - 1:
                cell_box.add_css_class("table-cell-divider")
            if total_rows == 0:
                cell_box.add_css_class("table-cell-last-row")
            cell_box.set_hexpand(True)

            lbl = Gtk.Label(wrap=True, selectable=True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_hexpand(True)

            if align == 'center':
                lbl.set_xalign(0.5)
                lbl.set_justify(Gtk.Justification.CENTER)
            elif align == 'right':
                lbl.set_xalign(1.0)
                lbl.set_justify(Gtk.Justification.RIGHT)
            else:
                lbl.set_xalign(0.0)
                lbl.set_justify(Gtk.Justification.LEFT)

            markup = markdown_to_pango(h_text)
            bold_markup = f"<b>{markup}</b>"
            if validate_gtk_markup(bold_markup):
                lbl.set_markup(bold_markup)
            elif validate_gtk_markup(markup):
                lbl.set_markup(markup)
            else:
                lbl.set_text(h_text)

            cell_box.append(lbl)
            grid.attach(cell_box, col_idx, 0, 1, 1)

        # Data rows (Row 1..N)
        for row_idx, row_cells in enumerate(rows, start=1):
            is_odd = (row_idx % 2 == 1)
            is_last = (row_idx == total_rows)
            for col_idx, cell_text in enumerate(row_cells):
                align = alignments[col_idx] if col_idx < len(alignments) else 'left'
                cell_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                cell_box.add_css_class("table-cell")
                cell_box.add_css_class("table-row-odd" if is_odd else "table-row-even")
                if col_idx < num_cols - 1:
                    cell_box.add_css_class("table-cell-divider")
                if is_last:
                    cell_box.add_css_class("table-cell-last-row")
                cell_box.set_hexpand(True)

                lbl = Gtk.Label(wrap=True, selectable=True)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_hexpand(True)

                if align == 'center':
                    lbl.set_xalign(0.5)
                    lbl.set_justify(Gtk.Justification.CENTER)
                elif align == 'right':
                    lbl.set_xalign(1.0)
                    lbl.set_justify(Gtk.Justification.RIGHT)
                else:
                    lbl.set_xalign(0.0)
                    lbl.set_justify(Gtk.Justification.LEFT)

                markup = markdown_to_pango(cell_text)
                if validate_gtk_markup(markup):
                    lbl.set_markup(markup)
                else:
                    lbl.set_text(cell_text)

                cell_box.append(lbl)
                grid.attach(cell_box, col_idx, row_idx, 1, 1)

        scroll.set_child(grid)
        self.append(scroll)

    def _on_copy(self, _btn):
        display = Gdk.Display.get_default()
        if display and self.raw_markdown:
            clipboard = display.get_clipboard()
            clipboard.set(self.raw_markdown)
            self.copy_btn.set_label("✓ Copiado")
            GLib.timeout_add(1500, self._reset_copy_label)

    def _reset_copy_label(self):
        self.copy_btn.set_label("📋 Copiar")
        return False


class ToolCard(Gtk.Box):
    """Compact collapsible card displaying an MCP tool execution and its result."""
    def __init__(self, tool_name: str, arguments: Dict[str, Any]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.tool_name = tool_name
        self.arguments = arguments
        self.add_css_class("tool-card")

        # Header box
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.spinner = Gtk.Spinner()
        self.spinner.start()
        header.append(self.spinner)

        self.status_icon = Gtk.Label(label="")
        self.status_icon.set_visible(False)
        header.append(self.status_icon)

        friendly_name = tool_name.replace("__", " › ")
        self.title_label = Gtk.Label(label=friendly_name, xalign=0)
        self.title_label.set_hexpand(True)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.add_css_class("tool-title")
        header.append(self.title_label)

        self.badge = Gtk.Label(label="ejecutando...")
        self.badge.add_css_class("tool-badge-running")
        header.append(self.badge)

        # Expander toggle button
        self.toggle_btn = Gtk.Button(icon_name="pan-down-symbolic")
        self.toggle_btn.add_css_class("flat")
        self.toggle_btn.add_css_class("tool-toggle-btn")
        self.toggle_btn.set_tooltip_text("Mostrar / ocultar detalles")
        self.toggle_btn.connect("clicked", self._on_toggle)
        header.append(self.toggle_btn)

        self.append(header)

        # Details box (collapsible)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.details_box.set_visible(False)
        self.details_box.add_css_class("tool-details")

        # Arguments preview
        if arguments:
            try:
                args_fmt = json.dumps(arguments, indent=2, ensure_ascii=False)
            except Exception:
                args_fmt = str(arguments)
            if len(args_fmt) > 300:
                args_fmt = args_fmt[:300] + "..."
            args_label = Gtk.Label(
                label=f"<small><b>Parámetros:</b></small>\n<tt>{html.escape(args_fmt)}</tt>",
                use_markup=True,
                wrap=True,
                selectable=True,
                xalign=0
            )
            args_label.set_wrap_mode(Pango.WrapMode.CHAR)
            self.details_box.append(args_label)

        # Result preview
        self.result_label = Gtk.Label(
            label="<small><i>Ejecutando en servidor MCP...</i></small>",
            use_markup=True,
            wrap=True,
            selectable=True,
            xalign=0
        )
        self.result_label.set_wrap_mode(Pango.WrapMode.CHAR)
        self.details_box.append(self.result_label)
        self.append(self.details_box)

    def _on_toggle(self, _btn):
        vis = not self.details_box.get_visible()
        self.details_box.set_visible(vis)
        self.toggle_btn.set_icon_name("pan-up-symbolic" if vis else "pan-down-symbolic")

    def finish(self, result_text: str):
        self.spinner.stop()
        self.spinner.set_visible(False)
        
        is_error = "ERROR" in result_text or "Error" in result_text or "exception" in result_text.lower()
        if is_error:
            self.status_icon.set_text("✕")
            self.status_icon.add_css_class("tool-icon-error")
            self.badge.set_text("error")
            self.badge.remove_css_class("tool-badge-running")
            self.badge.add_css_class("tool-badge-error")
        else:
            self.status_icon.set_text("✓")
            self.status_icon.add_css_class("tool-icon-success")
            self.badge.set_text("listo")
            self.badge.remove_css_class("tool-badge-running")
            self.badge.add_css_class("tool-badge-success")
        self.status_icon.set_visible(True)

        disp = result_text.strip()
        if len(disp) > 1000:
            disp = disp[:1000] + "\n...(salida truncada)"
        escaped_res = html.escape(disp)
        self.result_label.set_markup(f"<small><b>Resultado:</b></small>\n<tt>{escaped_res}</tt>")


class MessageBubble(Gtk.Box):
    """Message item for User or Assistant supporting rich Markdown formatting and modern AI-chat layout."""
    def __init__(self, role: str, text: str = "", on_tts_requested: Optional[Callable[[str, Any], None]] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.role = role
        self.raw_text = text
        self.on_tts_requested = on_tts_requested
        self.add_css_class("message-bubble")

        if role == "user":
            self.set_halign(Gtk.Align.END)
            self.add_css_class("user-bubble")
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.add_css_class("assistant-bubble")
            self.add_css_class("assistant-message")

            # Assistant header with clean bot badge
            self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.header_box.add_css_class("assistant-header")

            bot_icon = Gtk.Label(label="✨")
            bot_icon.add_css_class("assistant-bot-icon")
            self.header_box.append(bot_icon)

            bot_lbl = Gtk.Label(label="Asistente", xalign=0)
            bot_lbl.add_css_class("assistant-title-label")
            bot_lbl.set_hexpand(True)
            self.header_box.append(bot_lbl)

            self.append(self.header_box)

        # Content container
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.content_box.set_hexpand(True)
        self.append(self.content_box)

        self.label = Gtk.Label(wrap=True, selectable=True, xalign=0)
        self.label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_hexpand(True)
        self.label.add_css_class("message-text")
        if role == "user":
            self.label.set_max_width_chars(52)
        self.content_box.append(self.label)

        # Action toolbar for assistant responses (ChatGPT / Claude / Gemini style)
        if role != "user":
            self.actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            self.actions_box.add_css_class("assistant-actions-bar")
            self.actions_box.set_halign(Gtk.Align.START)

            self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
            self.copy_btn.add_css_class("flat")
            self.copy_btn.add_css_class("assistant-action-btn")
            self.copy_btn.set_tooltip_text("Copiar respuesta")
            self.copy_btn.connect("clicked", self._on_copy_message)
            self.actions_box.append(self.copy_btn)

            self.tts_btn = Gtk.Button(icon_name="audio-volume-high-symbolic")
            self.tts_btn.add_css_class("flat")
            self.tts_btn.add_css_class("assistant-action-btn")
            self.tts_btn.set_tooltip_text("Escuchar con voz (Kokoro TTS)")
            self.tts_btn.connect("clicked", self._on_tts_clicked)
            self.actions_box.append(self.tts_btn)

            self.actions_box.set_visible(bool(text.strip()))
            self.append(self.actions_box)

        if text:
            self.raw_text = text
            self.finalize()

    def _on_copy_message(self, _btn):
        display = Gdk.Display.get_default()
        if display and self.raw_text:
            clipboard = display.get_clipboard()
            clipboard.set(self.raw_text)
            self.copy_btn.set_icon_name("object-select-symbolic")
            self.copy_btn.set_tooltip_text("¡Copiado al portapapeles!")
            GLib.timeout_add(1500, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self.copy_btn.set_icon_name("edit-copy-symbolic")
        self.copy_btn.set_tooltip_text("Copiar respuesta")
        return False

    def _on_tts_clicked(self, _btn):
        if self.on_tts_requested and self.raw_text:
            self.on_tts_requested(self.raw_text, self)

    def set_speaking_state(self, is_speaking: bool):
        if hasattr(self, "tts_btn"):
            if is_speaking:
                self.tts_btn.set_icon_name("media-playback-stop-symbolic")
                self.tts_btn.set_tooltip_text("Detener audio (Kokoro TTS)")
            else:
                self.tts_btn.set_icon_name("audio-volume-high-symbolic")
                self.tts_btn.set_tooltip_text("Escuchar con voz (Kokoro TTS)")

    def set_text(self, text: str):
        self.raw_text = text
        self.finalize()

    def append_chunk(self, chunk: str):
        self.raw_text += chunk
        if not self.label.get_parent():
            while child := self.content_box.get_first_child():
                self.content_box.remove(child)
            self.content_box.append(self.label)
        self.label.set_text(self.raw_text)

    def finalize(self):
        """Applies rich Markdown formatting and code blocks safely."""
        if hasattr(self, "actions_box"):
            self.actions_box.set_visible(bool(self.raw_text.strip()))

        blocks = split_markdown_blocks(self.raw_text)
        if not blocks:
            self.label.set_text(self.raw_text)
            return

        while child := self.content_box.get_first_child():
            self.content_box.remove(child)

        for item in blocks:
            if item[0] == 'text':
                lbl = Gtk.Label(wrap=True, selectable=True, xalign=0)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_hexpand(True)
                lbl.add_css_class("message-text")
                if self.role == "user":
                    lbl.set_max_width_chars(52)
                markup = markdown_to_pango(item[1])
                if validate_gtk_markup(markup):
                    lbl.set_markup(markup)
                else:
                    lbl.set_text(item[1])
                self.content_box.append(lbl)
            elif item[0] == 'code':
                _, lang, code = item
                widget = CodeBlockWidget(code=code, lang=lang)
                self.content_box.append(widget)
            elif item[0] == 'table':
                _, headers, rows, alignments, raw_table_text = item
                widget = TableWidget(headers=headers, rows=rows, alignments=alignments, raw_markdown=raw_table_text)
                self.content_box.append(widget)


class ChatView(Gtk.ScrolledWindow):
    def __init__(self, on_tts_requested: Optional[Callable[[str, Any], None]] = None):
        super().__init__()
        self.on_tts_requested = on_tts_requested
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self._scroll_pending = False
        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.container.set_margin_top(12)
        self.container.set_margin_bottom(12)
        self.container.set_margin_start(16)
        self.container.set_margin_end(16)
        self.container.set_hexpand(True)

        # Center chat stream in a modern reading column (ChatGPT / Claude / Gemini style)
        self.clamp = Adw.Clamp()
        self.clamp.set_maximum_size(720)
        self.clamp.set_tightening_threshold(560)
        self.clamp.set_child(self.container)
        self.set_child(self.clamp)

        self.current_assistant_bubble: Optional[MessageBubble] = None
        self.last_assistant_bubble: Optional[MessageBubble] = None
        self.last_assistant_text: str = ""
        self.current_tool_card: Optional[ToolCard] = None
        self.welcome_widget: Optional[WelcomeWidget] = None

    def show_welcome(self, on_suggestion_clicked: Callable[[str], None]):
        """Displays modern welcome empty state."""
        self.clear()
        self.welcome_widget = WelcomeWidget(on_suggestion_clicked=on_suggestion_clicked)
        self.container.append(self.welcome_widget)

    def hide_welcome(self):
        if self.welcome_widget:
            self.container.remove(self.welcome_widget)
            self.welcome_widget = None

    def add_user_message(self, text: str):
        self.hide_welcome()
        bubble = MessageBubble("user", text)
        self.container.append(bubble)
        self.scroll_to_bottom()

    def start_assistant_message(self) -> MessageBubble:
        self.hide_welcome()
        self.current_assistant_bubble = MessageBubble("assistant", "", on_tts_requested=self.on_tts_requested)
        self.last_assistant_bubble = self.current_assistant_bubble
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
            self.last_assistant_text = self.current_assistant_bubble.raw_text
            self.last_assistant_bubble = self.current_assistant_bubble
            self.current_assistant_bubble.finalize()
            self.current_assistant_bubble = None
        self.scroll_to_bottom()
        return False

    def start_tool_card(self, tool_name: str, arguments: Dict[str, Any]):
        self.hide_welcome()
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
        self.hide_welcome()
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
        self.welcome_widget = None

    def load_history(self, messages: list):
        """Reconstructs the chat visual history from saved messages."""
        self.clear()
        if not messages:
            return

        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if content:
                    bubble = MessageBubble("user", content)
                    self.container.append(bubble)
                i += 1
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    tool_results = {}
                    j = i + 1
                    while j < n and messages[j].get("role") == "tool":
                        t_msg = messages[j]
                        cid = t_msg.get("tool_call_id")
                        if cid:
                            tool_results[cid] = t_msg.get("content", "")
                        j += 1

                    for tc in tool_calls:
                        cid = tc.get("id")
                        fn = tc.get("function", {})
                        tname = fn.get("name", "herramienta")
                        raw_args = fn.get("arguments", "{}")
                        if isinstance(raw_args, str):
                            try:
                                parsed_args = json.loads(raw_args)
                            except Exception:
                                parsed_args = {"raw_input": raw_args}
                        elif isinstance(raw_args, dict):
                            parsed_args = raw_args
                        else:
                            parsed_args = {"raw_input": str(raw_args)}

                        card = ToolCard(tname, parsed_args)
                        res_str = tool_results.get(cid, "Ejecutado con éxito")
                        card.finish(res_str)
                        self.container.append(card)

                    i = j
                else:
                    i += 1

                if content:
                    bubble = MessageBubble("assistant", content, on_tts_requested=self.on_tts_requested)
                    self.last_assistant_bubble = bubble
                    self.container.append(bubble)
            else:
                i += 1

        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        if not self._scroll_pending:
            self._scroll_pending = True
            GLib.idle_add(self._do_scroll)

    def _do_scroll(self):
        self._scroll_pending = False
        adj = self.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False
