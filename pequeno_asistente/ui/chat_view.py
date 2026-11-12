"""
Chat View widget for Pequeño Asistente.
Renders message bubbles, streaming responses, tool execution cards,
code blocks, and modern interactive empty-state suggestions with GTK4 and Libadwaita.
"""
import os
import re
import html
import json
import datetime
from typing import Dict, Any, Optional, Callable, List

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk, Gio
try:
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
except Exception:
    GdkPixbuf = None


_LIGHT_THEME_ACTIVE: bool = False


def set_chat_theme(is_light: bool):
    """Sets the active theme state for Pango markdown rendering."""
    global _LIGHT_THEME_ACTIVE
    _LIGHT_THEME_ACTIVE = bool(is_light)


def validate_gtk_markup(markup: str) -> bool:
    """Validates if a markup string is safe for GTK/Pango display."""
    temp = re.sub(r'<a\s+href=[\'"][^\'"]*[\'"]>', '<span>', markup)
    temp = temp.replace('</a>', '</span>')
    try:
        ok, _, _, _ = Pango.parse_markup(temp, -1, '\0')
        return ok
    except Exception:
        return False


def markdown_to_pango(text: str, is_light: Optional[bool] = None) -> str:
    """Converts basic markdown inline elements and blocks to safe Pango/GTK markup."""
    escaped = html.escape(text, quote=False)
    light = _LIGHT_THEME_ACTIVE if is_light is None else is_light

    fg_heading = "#0f172a" if light else "#f8fafc"
    fg_subheading = "#1e293b" if light else "#cdd6f4"
    fg_accent = "#2563eb" if light else "#89b4fa"
    fg_quote = "#475569" if light else "#a6adc8"
    bg_code = "#e2e8f0" if light else "#212234"
    fg_code = "#1d4ed8" if light else "#89b4fa"
    fg_hr = "#cbd5e1" if light else "#45475a"

    # 1. Protect inline code (`code`) with placeholders so inner *, _, #, etc. are not altered
    code_spans: list[str] = []

    def _save_code(match: re.Match) -> str:
        idx = len(code_spans)
        code_spans.append(match.group(1))
        return f"\x00CODE{idx}\x00"

    escaped = re.sub(r"`([^`\n]+)`", _save_code, escaped)

    # 2. Protect markdown links [text](url) URLs while allowing formatting in link text
    link_urls: list[str] = []

    def _save_link(match: re.Match) -> str:
        idx = len(link_urls)
        label = match.group(1)
        url = match.group(2).replace('"', "&quot;")
        link_urls.append(url)
        return f"\x00LINKOPEN{idx}\x00{label}\x00LINKCLOSE\x00"

    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", _save_link, escaped)

    # 3. Horizontal rules (must run before bold/italic/lists)
    escaped = re.sub(
        r"^[ \t]*(?:-{3,}|_{3,}|\*{3,})[ \t]*$",
        rf'<span foreground="{fg_hr}">────────────────────────</span>',
        escaped,
        flags=re.MULTILINE,
    )

    # 4. Headers (# to ######)
    def _replace_header(match: re.Match) -> str:
        level = len(match.group(1))
        content = match.group(2).strip()
        if level == 1:
            return f'<span size="x-large" weight="bold" foreground="{fg_heading}">{content}</span>'
        elif level == 2:
            return f'<span size="large" weight="bold" foreground="{fg_heading}">{content}</span>'
        elif level == 3:
            return f'<span size="112%" weight="bold" foreground="{fg_heading}">{content}</span>'
        elif level == 4:
            return f'<span size="105%" weight="bold" foreground="{fg_accent}">{content}</span>'
        elif level == 5:
            return f'<span size="medium" weight="bold" foreground="{fg_subheading}">{content}</span>'
        else:
            return f'<span size="medium" weight="bold" foreground="{fg_quote}">{content}</span>'

    escaped = re.sub(
        r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$",
        _replace_header,
        escaped,
        flags=re.MULTILINE,
    )

    # 5. Blockquotes
    escaped = re.sub(
        r"^[ \t]*&gt;[ \t]?(.*)$",
        rf'  <span foreground="{fg_accent}">│</span> <span foreground="{fg_quote}"><i>\1</i></span>',
        escaped,
        flags=re.MULTILINE,
    )

    # 6. Bullet lists: - item, * item, or + item (must run before *italic* regex)
    escaped = re.sub(
        r"^[ \t]*[-*+][ \t]+(.+)$",
        rf'  <span foreground="{fg_accent}">•</span> \1',
        escaped,
        flags=re.MULTILINE,
    )

    # 7. Numbered lists: 1. item
    escaped = re.sub(
        r"^[ \t]*(\d+)\.[ \t]+(.+)$",
        rf'  <span foreground="{fg_accent}" weight="bold">\1.</span> \2',
        escaped,
        flags=re.MULTILINE,
    )

    # 8. Bold & Italic
    escaped = re.sub(r"\*\*\*([^*\n]+?)\*\*\*", r"<b><i>\1</i></b>", escaped)
    escaped = re.sub(r"___([^_\n]+?)___", r"<b><i>\1</i></b>", escaped)

    # 9. Bold: **text** or __text__
    escaped = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__([^_\n]+?)__", r"<b>\1</b>", escaped)

    # 10. Italic: *text* or _text_ (single line, no surrounding spaces inside delimiters)
    escaped = re.sub(r"(?<![<a-zA-Z0-9\*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![>a-zA-Z0-9\*])", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<![<a-zA-Z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![>a-zA-Z0-9_])", r"<i>\1</i>", escaped)

    # 11. Strikethrough: ~~text~~
    escaped = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", escaped)

    # 12. Line breaks inside elements (e.g. <br> or <br/> in tables)
    escaped = re.sub(r"&lt;br\s*/?&gt;", "\n", escaped, flags=re.IGNORECASE)

    # 13. Restore links
    for idx, url in enumerate(link_urls):
        escaped = escaped.replace(
            f"\x00LINKOPEN{idx}\x00",
            f'<a href="{url}"><span foreground="{fg_accent}" underline="single">',
        )
    escaped = escaped.replace("\x00LINKCLOSE\x00", "</span></a>")

    # 14. Restore inline code spans
    for idx, code_str in enumerate(code_spans):
        escaped = escaped.replace(
            f"\x00CODE{idx}\x00",
            f'<tt><span background="{bg_code}" foreground="{fg_code}"> {code_str} </span></tt>',
        )

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
    """Splits markdown into text chunks, tables, and fenced code blocks (including unclosed blocks during streaming)."""
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
        # Check if there is an unclosed code block at the end (common during streaming)
        open_fence = re.search(r'```([a-zA-Z0-9_\-\+]*)\n?([\s\S]*)$', post_text)
        if open_fence:
            pre_code = post_text[:open_fence.start()]
            if pre_code.strip():
                blocks.extend(extract_tables_and_text(pre_code.strip('\n')))
            open_lang = open_fence.group(1).strip()
            open_code = open_fence.group(2) or ""
            blocks.append(('code', open_lang, open_code))
        else:
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
        avatar_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        avatar_icon.set_pixel_size(28)
        avatar_icon.add_css_class("welcome-avatar-icon")
        icon_box.append(avatar_icon)
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
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chip_lbl = Gtk.Label(label=label_text, xalign=0)
            chip_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            chip_lbl.set_hexpand(True)
            arrow_icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow_icon.set_pixel_size(12)
            arrow_icon.add_css_class("suggestion-arrow")
            btn_box.append(chip_lbl)
            btn_box.append(arrow_icon)
            btn.set_child(btn_box)
            btn.connect("clicked", lambda _b, p=prompt_text: on_suggestion_clicked(p))
            sug_box.append(btn)

        self.append(sug_box)


class CodeBlockWidget(Gtk.Box):
    """Compact card widget for code blocks with language badge and copy-to-clipboard button."""
    def __init__(self, code: str, lang: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.code = code
        self.lang = lang
        self.add_css_class("code-card")

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("code-header")

        display_lang = (lang if lang else "código").upper()
        self.lang_lbl = Gtk.Label(label=display_lang, xalign=0)
        self.lang_lbl.add_css_class("code-lang-label")
        self.lang_lbl.set_hexpand(True)
        header.append(self.lang_lbl)

        self.copy_btn = Gtk.Button(label="📋 Copiar")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("code-copy-btn")
        self.copy_btn.connect("clicked", self._on_copy)
        header.append(self.copy_btn)

        self.append(header)

        # Monospace Code content
        self.code_lbl = Gtk.Label(label=code, wrap=True, selectable=True, xalign=0)
        self.code_lbl.set_wrap_mode(Pango.WrapMode.CHAR)
        self.code_lbl.add_css_class("code-body")
        self.code_lbl.set_hexpand(True)
        self.append(self.code_lbl)

    def update_code(self, code: str, lang: str = ""):
        self.code = code
        if lang and lang != self.lang:
            self.lang = lang
            self.lang_lbl.set_text(lang.upper())
        self.code_lbl.set_text(code)

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
            cell_box.set_valign(Gtk.Align.START)
            if col_idx < num_cols - 1:
                cell_box.add_css_class("table-cell-divider")
            if total_rows == 0:
                cell_box.add_css_class("table-cell-last-row")
                if col_idx == 0:
                    cell_box.add_css_class("table-cell-bottom-left")
                if col_idx == num_cols - 1:
                    cell_box.add_css_class("table-cell-bottom-right")
            cell_box.set_hexpand(True)

            lbl = Gtk.Label(wrap=True, selectable=True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_valign(Gtk.Align.START)
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
                cell_box.set_valign(Gtk.Align.START)
                if col_idx < num_cols - 1:
                    cell_box.add_css_class("table-cell-divider")
                if is_last:
                    cell_box.add_css_class("table-cell-last-row")
                    if col_idx == 0:
                        cell_box.add_css_class("table-cell-bottom-left")
                    if col_idx == num_cols - 1:
                        cell_box.add_css_class("table-cell-bottom-right")
                cell_box.set_hexpand(True)

                lbl = Gtk.Label(wrap=True, selectable=True)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_valign(Gtk.Align.START)
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

        self.append(grid)

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


class ThinkingCard(Gtk.Box):
    """
    Compact collapsible widget displaying the LLM's thought / reasoning process.
    Folded (replegado) by default: 'en el chat se vea el pensamiento del llm replegado'.
    """
    def __init__(self, text: str = "", is_streaming: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.raw_text = text
        self.is_streaming = is_streaming
        self.add_css_class("thinking-card")

        # Header box
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header.add_css_class("thinking-header")

        # Clickable title box (icon + title + badges)
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_hexpand(True)

        self.icon_label = Gtk.Image.new_from_icon_name("system-run-symbolic")
        self.icon_label.set_pixel_size(14)
        self.icon_label.add_css_class("thinking-icon")
        title_box.append(self.icon_label)

        self.title_label = Gtk.Label(label="Pensamiento", xalign=0)
        self.title_label.add_css_class("thinking-title")
        title_box.append(self.title_label)

        self.badge = Gtk.Label(label="")
        self.badge.add_css_class("thinking-badge-active")
        self.badge.set_visible(False)
        title_box.append(self.badge)

        self.spinner = Gtk.Spinner()
        title_box.append(self.spinner)

        # Folded hint
        self.folded_hint = Gtk.Label(label="(replegado)")
        self.folded_hint.add_css_class("thinking-badge-done")
        self.folded_hint.set_visible(True)
        title_box.append(self.folded_hint)

        click_gesture = Gtk.GestureClick()
        click_gesture.connect("released", lambda *args: self._on_toggle(None))
        title_box.add_controller(click_gesture)

        self.header.append(title_box)

        # Expander toggle button
        self.toggle_btn = Gtk.Button(icon_name="pan-down-symbolic")
        self.toggle_btn.add_css_class("flat")
        self.toggle_btn.add_css_class("thinking-toggle-btn")
        self.toggle_btn.set_tooltip_text("Mostrar pensamiento")
        self.toggle_btn.connect("clicked", self._on_toggle)
        self.header.append(self.toggle_btn)

        self.append(self.header)

        # Details box (collapsible, hidden / folded by default!)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.details_box.set_visible(False)
        self.details_box.add_css_class("thinking-details")

        # Details toolbar (Copy button for thoughts)
        details_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        details_bar.set_halign(Gtk.Align.END)
        self.copy_btn = Gtk.Button(label="📋 Copiar")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("code-copy-btn")
        self.copy_btn.set_tooltip_text("Copiar razonamiento")
        self.copy_btn.connect("clicked", self._on_copy_thinking)
        details_bar.append(self.copy_btn)
        self.details_box.append(details_bar)

        # Text label
        self.text_label = Gtk.Label(
            label=text,
            wrap=True,
            selectable=True,
            xalign=0
        )
        self.text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text_label.set_hexpand(True)
        self.text_label.add_css_class("thinking-text")
        self.details_box.append(self.text_label)

        self.append(self.details_box)

        if is_streaming:
            self.start_streaming()
        elif text:
            self.finish()

    def start_streaming(self):
        self.is_streaming = True
        self.spinner.start()
        self.spinner.set_visible(True)
        self.badge.set_text("pensando...")
        self.badge.set_visible(True)
        self.folded_hint.set_visible(False)

    def append_chunk(self, chunk: str):
        if not self.is_streaming:
            self.start_streaming()
        self.raw_text += chunk
        self.text_label.set_text(self.raw_text)

    def finish(self):
        self.is_streaming = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.badge.set_visible(False)
        if not self.details_box.get_visible():
            self.folded_hint.set_visible(True)
        self.text_label.set_text(self.raw_text)

    def _on_toggle(self, _btn):
        vis = not self.details_box.get_visible()
        self.details_box.set_visible(vis)
        self.toggle_btn.set_icon_name("pan-up-symbolic" if vis else "pan-down-symbolic")
        self.toggle_btn.set_tooltip_text("Ocultar pensamiento" if vis else "Mostrar pensamiento")
        self.folded_hint.set_visible(not vis and not self.is_streaming)

    def _on_copy_thinking(self, _btn):
        display = Gdk.Display.get_default()
        if display and self.raw_text:
            clipboard = display.get_clipboard()
            clipboard.set(self.raw_text)
            self.copy_btn.set_label("✓ Copiado")
            GLib.timeout_add(1500, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self.copy_btn.set_label("📋 Copiar")
        return False


class AutomationCard(Gtk.Box):
    """
    Card rendered inside the chat view when a Heartbeat pulse or scheduled Automation
    reports findings, alerts, or summaries to the user.
    """
    def __init__(
        self,
        title: str,
        text: str,
        status: str = "ok",
        is_heartbeat: bool = False,
        on_tts_requested: Optional[Callable[[str, Any], None]] = None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.raw_text = text
        self.on_tts_requested = on_tts_requested
        self.add_css_class("automation-card")

        # Header box
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        icon = Gtk.Image.new_from_icon_name("alarm-symbolic" if is_heartbeat else "weather-clear-night-symbolic")
        icon.set_pixel_size(15)
        icon.add_css_class("automation-card-icon")
        header.append(icon)

        title_lbl = Gtk.Label(label=f"<b>{html.escape(title)}</b>", use_markup=True, xalign=0)
        title_lbl.set_hexpand(True)
        title_lbl.add_css_class("automation-title")
        header.append(title_lbl)

        now_str = datetime.datetime.now().strftime("%H:%M")
        time_lbl = Gtk.Label(label=now_str)
        time_lbl.add_css_class("dim-label")
        header.append(time_lbl)

        badge = Gtk.Label(label="PULSO" if is_heartbeat else "AUTOMATIZACIÓN")
        badge.add_css_class("automation-badge")
        header.append(badge)

        self.append(header)

        # Content body
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.content_box.add_css_class("automation-body")
        self.append(self.content_box)
        self._populate_content()

    def _populate_content(self):
        while child := self.content_box.get_first_child():
            self.content_box.remove(child)

        text = self.raw_text
        blocks = split_markdown_blocks(text)
        if not blocks:
            lbl = Gtk.Label(wrap=True, selectable=True, xalign=0)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.add_css_class("message-text")
            markup = markdown_to_pango(text)
            if validate_gtk_markup(markup):
                lbl.set_markup(markup)
            else:
                lbl.set_text(text)
            self.content_box.append(lbl)
        else:
            for item in blocks:
                if item[0] == 'text':
                    lbl = Gtk.Label(wrap=True, selectable=True, xalign=0)
                    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    lbl.set_hexpand(True)
                    lbl.add_css_class("message-text")
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

    def refresh_theme(self):
        """Re-renders Pango markdown blocks with current theme colors."""
        self._populate_content()


def _clean_user_display_text(text: Any) -> str:
    """Extracts clean user text from string or multimodal content list, stripping XML attachment envelopes."""
    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    elif not isinstance(text, str):
        text = str(text or "")

    text = re.sub(r"\n?<attached_(?:document|image|audio)\b[^>]*>[\s\S]*?</attached_(?:document|image|audio)>", "", text)
    text = re.sub(r"\n?<attached_(?:document|image|audio)\b[^>]*/?>", "", text)
    return text.strip()


class MessageBubble(Gtk.Box):
    """Message item for User or Assistant supporting rich Markdown formatting and modern AI-chat layout."""
    def __init__(
        self,
        role: str,
        text: Any = "",
        reasoning: str = "",
        on_tts_requested: Optional[Callable[[str, Any], None]] = None,
        is_queued: bool = False,
        attachments: Optional[List[Any]] = None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.role = role
        cleaned_initial = _clean_user_display_text(text) if role == "user" else (text if isinstance(text, str) else _clean_user_display_text(text))
        self.raw_text = cleaned_initial
        self.reasoning_text = reasoning
        self.thinking_card: Optional[ThinkingCard] = None
        self.on_tts_requested = on_tts_requested
        self._rendered_items: list[dict] = []
        self.is_queued = is_queued
        self.attachments: List[Any] = list(attachments) if attachments else []
        self.queue_badge: Optional[Gtk.Label] = None
        self.attachments_box: Optional[Gtk.Box] = None
        self.add_css_class("message-bubble")

        if role == "user":
            self.set_halign(Gtk.Align.END)
            self.add_css_class("user-bubble")
            if is_queued:
                self.add_css_class("user-bubble-queued")
            self.queue_badge = Gtk.Label(label="⏳ En cola", xalign=1)
            self.queue_badge.add_css_class("queue-badge")
            self.queue_badge.set_visible(is_queued)

            if self.attachments:
                self.attachments_box = self._build_attachments_gallery(self.attachments)
                self.append(self.attachments_box)
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.add_css_class("assistant-bubble")
            self.add_css_class("assistant-message")

            # Assistant header with clean bot badge
            self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.header_box.add_css_class("assistant-header")

            bot_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
            bot_icon.set_pixel_size(14)
            bot_icon.add_css_class("assistant-bot-icon")
            self.header_box.append(bot_icon)

            bot_lbl = Gtk.Label(label="Asistente", xalign=0)
            bot_lbl.add_css_class("assistant-title-label")
            bot_lbl.set_hexpand(True)
            self.header_box.append(bot_lbl)

            self.append(self.header_box)

            # Thinking card (if reasoning passed at initialization)
            if reasoning and reasoning.strip():
                self.thinking_card = ThinkingCard(text=reasoning.strip(), is_streaming=False)
                self.append(self.thinking_card)

        # Content container
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.content_box.set_hexpand(True)
        self.append(self.content_box)

        if role == "user" and self.queue_badge:
            self.append(self.queue_badge)

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

            self.actions_box.set_visible(bool(self.raw_text.strip()))
            self.append(self.actions_box)

        if self.raw_text:
            self.finalize()

    def _build_attachments_gallery(self, attachments: List[Any]) -> Gtk.Box:
        """Builds visual thumbnails for attached images and badges for attached audio and documents."""
        from pequeno_asistente.agent.media_processor import is_image_file, is_audio_file, format_file_size

        gallery = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gallery.add_css_class("message-attachments-gallery")

        for att in attachments:
            if isinstance(att, dict):
                path = att.get("path", "")
                name = att.get("name") or os.path.basename(path) or "Archivo"
                att_type = att.get("type") or (
                    "image" if is_image_file(path) else ("audio" if is_audio_file(path) else "document")
                )
                size_str = att.get("size_human", "")
                fmt_str = att.get("format", "")
                duration_str = att.get("duration_human", "")
            else:
                path = str(att)
                name = os.path.basename(path) or path
                att_type = "image" if is_image_file(path) else ("audio" if is_audio_file(path) else "document")
                try:
                    size_str = format_file_size(os.path.getsize(path)) if os.path.isfile(path) else ""
                except Exception:
                    size_str = ""
                fmt_str = os.path.splitext(name)[1].lstrip(".").upper()
                duration_str = ""

            if att_type == "image" and path and os.path.isfile(path):
                img_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                img_card.add_css_class("message-attachment-image")
                img_card.set_tooltip_text(f"{name}\n{path}")
                loaded_thumb = False
                if GdkPixbuf is not None:
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 220, 160, True)
                        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                        pic = Gtk.Picture.new_for_paintable(texture)
                        pic.set_can_shrink(True)
                        pic.set_size_request(min(220, pixbuf.get_width()), min(160, pixbuf.get_height()))
                        pic.add_css_class("attachment-image-preview")
                        img_card.append(pic)
                        loaded_thumb = True
                    except Exception:
                        loaded_thumb = False
                if not loaded_thumb:
                    try:
                        pic = Gtk.Picture.new_for_filename(path)
                        pic.set_size_request(180, 120)
                        img_card.append(pic)
                    except Exception:
                        pass
                cap_lbl = Gtk.Label(label=f"🖼 {name}" + (f" ({size_str})" if size_str else ""), xalign=0)
                cap_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                cap_lbl.set_max_width_chars(32)
                cap_lbl.add_css_class("attachment-caption-label")
                img_card.append(cap_lbl)
                gallery.append(img_card)
            elif att_type == "audio":
                aud_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                aud_chip.add_css_class("message-attachment-doc")
                aud_chip.set_tooltip_text(f"{name}\n{path}")
                icon = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
                icon.set_pixel_size(16)
                aud_chip.append(icon)

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                name_lbl = Gtk.Label(label=f"🎵 {name}", xalign=0)
                name_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                name_lbl.set_max_width_chars(30)
                name_lbl.add_css_class("attachment-doc-name")
                info_box.append(name_lbl)

                sub_parts = [p for p in ("Audio", fmt_str, duration_str, size_str) if p]
                if sub_parts:
                    sub_lbl = Gtk.Label(label=" • ".join(sub_parts), xalign=0)
                    sub_lbl.add_css_class("attachment-doc-meta")
                    info_box.append(sub_lbl)

                aud_chip.append(info_box)
                gallery.append(aud_chip)
            else:
                doc_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                doc_chip.add_css_class("message-attachment-doc")
                doc_chip.set_tooltip_text(f"{name}\n{path}")
                icon = Gtk.Image.new_from_icon_name("x-office-document-symbolic")
                icon.set_pixel_size(16)
                doc_chip.append(icon)

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                name_lbl = Gtk.Label(label=name, xalign=0)
                name_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                name_lbl.set_max_width_chars(30)
                name_lbl.add_css_class("attachment-doc-name")
                info_box.append(name_lbl)

                sub_parts = [p for p in (fmt_str, size_str) if p]
                if sub_parts:
                    sub_lbl = Gtk.Label(label=" • ".join(sub_parts), xalign=0)
                    sub_lbl.add_css_class("attachment-doc-meta")
                    info_box.append(sub_lbl)

                doc_chip.append(info_box)
                gallery.append(doc_chip)

        return gallery

    def set_queued(self, queued: bool):
        """Updates queued status badge and visual styling."""
        self.is_queued = queued
        if self.queue_badge:
            self.queue_badge.set_visible(queued)
        if queued:
            self.add_css_class("user-bubble-queued")
        else:
            self.remove_css_class("user-bubble-queued")

    @property
    def label(self) -> Optional[Gtk.Label]:
        for item in self._rendered_items:
            if item["type"] == "text":
                return item["widget"]
        return None

    def _create_text_label(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(wrap=True, selectable=True, xalign=0)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_hexpand(True)
        lbl.add_css_class("message-text")
        if self.role == "user":
            lbl.set_max_width_chars(52)
        markup = markdown_to_pango(text)
        if validate_gtk_markup(markup):
            lbl.set_markup(markup)
        else:
            lbl.set_text(text)
        return lbl

    def _update_text_label(self, lbl: Gtk.Label, text: str):
        markup = markdown_to_pango(text)
        if validate_gtk_markup(markup):
            lbl.set_markup(markup)
        else:
            lbl.set_text(text)

    def _render_markdown(self, is_streaming: bool = False):
        """Renders markdown blocks dynamically and incrementally during streaming or finalization."""
        if not self.raw_text.strip():
            while child := self.content_box.get_first_child():
                self.content_box.remove(child)
            self._rendered_items.clear()
            return

        blocks = split_markdown_blocks(self.raw_text)
        if not blocks:
            blocks = [('text', self.raw_text)]

        num_blocks = len(blocks)
        num_items = len(self._rendered_items)

        # Check existing items matching in type
        diverge_idx = 0
        while diverge_idx < min(num_blocks, num_items):
            b_type = blocks[diverge_idx][0]
            item = self._rendered_items[diverge_idx]
            if item["type"] != b_type:
                break
            diverge_idx += 1

        # Remove divergent trailing widgets
        while len(self._rendered_items) > diverge_idx:
            item = self._rendered_items.pop()
            self.content_box.remove(item["widget"])

        # Update matching items in-place
        for i in range(diverge_idx):
            block = blocks[i]
            item = self._rendered_items[i]
            b_type = block[0]

            if b_type == 'text':
                txt = block[1]
                if item.get("content") != txt:
                    item["content"] = txt
                    self._update_text_label(item["widget"], txt)
            elif b_type == 'code':
                _, lang, code = block
                if item.get("code") != code or item.get("lang") != lang:
                    item["code"] = code
                    item["lang"] = lang
                    item["widget"].update_code(code, lang)
            elif b_type == 'table':
                _, headers, rows, alignments, raw_md = block
                if item.get("raw_md") != raw_md:
                    item["raw_md"] = raw_md
                    old_widget = item["widget"]
                    new_widget = TableWidget(headers=headers, rows=rows, alignments=alignments, raw_markdown=raw_md)
                    self.content_box.insert_child_after(new_widget, old_widget)
                    self.content_box.remove(old_widget)
                    item["widget"] = new_widget

        # Append new blocks
        for i in range(diverge_idx, num_blocks):
            block = blocks[i]
            b_type = block[0]

            if b_type == 'text':
                txt = block[1]
                lbl = self._create_text_label(txt)
                self.content_box.append(lbl)
                self._rendered_items.append({"type": "text", "widget": lbl, "content": txt})
            elif b_type == 'code':
                _, lang, code = block
                widget = CodeBlockWidget(code=code, lang=lang)
                self.content_box.append(widget)
                self._rendered_items.append({"type": "code", "widget": widget, "code": code, "lang": lang})
            elif b_type == 'table':
                _, headers, rows, alignments, raw_md = block
                widget = TableWidget(headers=headers, rows=rows, alignments=alignments, raw_markdown=raw_md)
                self.content_box.append(widget)
                self._rendered_items.append({"type": "table", "widget": widget, "raw_md": raw_md})

    def append_thought(self, chunk: str):
        self.reasoning_text += chunk
        if not self.thinking_card:
            self.thinking_card = ThinkingCard(chunk, is_streaming=True)
            if hasattr(self, "header_box") and self.header_box:
                self.insert_child_after(self.thinking_card, self.header_box)
            else:
                self.prepend(self.thinking_card)
        else:
            self.thinking_card.append_chunk(chunk)

    def finish_thought(self):
        if self.thinking_card and self.thinking_card.is_streaming:
            self.thinking_card.finish()

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
        self.finish_thought()
        self.raw_text += chunk
        self._render_markdown(is_streaming=True)

    def finalize(self):
        """Applies rich Markdown formatting and code blocks safely."""
        self.finish_thought()
        if hasattr(self, "actions_box"):
            self.actions_box.set_visible(bool(self.raw_text.strip()))
        self._render_markdown(is_streaming=False)

    def refresh_theme(self):
        """Forces a full re-render of markdown blocks so Pango colors match the active theme."""
        for item in self._rendered_items:
            if item["type"] == "text":
                item["content"] = None
            elif item["type"] == "table":
                item["raw_md"] = None
        self._render_markdown(is_streaming=False)


class ChatView(Gtk.ScrolledWindow):
    def __init__(self, on_tts_requested: Optional[Callable[[str, Any], None]] = None):
        super().__init__()
        self.on_tts_requested = on_tts_requested
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self._scroll_pending = False
        self._auto_scroll = True
        self._programmatic_scroll = False
        self._scroll_threshold = 80
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

        vadj = self.get_vadjustment()
        vadj.connect("value-changed", self._on_scroll_value_changed)
        vadj.connect("notify::upper", self._on_scroll_metrics_changed)
        vadj.connect("notify::page-size", self._on_scroll_metrics_changed)

    def show_welcome(self, on_suggestion_clicked: Callable[[str], None]):
        """Displays modern welcome empty state."""
        self.clear()
        self.welcome_widget = WelcomeWidget(on_suggestion_clicked=on_suggestion_clicked)
        self.container.append(self.welcome_widget)

    def hide_welcome(self):
        if self.welcome_widget:
            self.container.remove(self.welcome_widget)
            self.welcome_widget = None

    def add_user_message(self, text: str, is_queued: bool = False, attachments: Optional[List[Any]] = None) -> MessageBubble:
        self.hide_welcome()
        bubble = MessageBubble("user", text, is_queued=is_queued, attachments=attachments)
        self.container.append(bubble)
        self.scroll_to_bottom(force=True)
        return bubble

    def start_assistant_message(self) -> MessageBubble:
        self.hide_welcome()
        self.current_assistant_bubble = MessageBubble("assistant", "", on_tts_requested=self.on_tts_requested)
        self.last_assistant_bubble = self.current_assistant_bubble
        self.container.append(self.current_assistant_bubble)
        self.scroll_to_bottom(force=True)
        return self.current_assistant_bubble

    def append_assistant_thought(self, chunk: str):
        if not self.current_assistant_bubble:
            self.start_assistant_message()
        self.current_assistant_bubble.append_thought(chunk)
        self.scroll_to_bottom()
        return False

    def append_assistant_token(self, token: str):
        if not self.current_assistant_bubble:
            self.start_assistant_message()
        self.current_assistant_bubble.append_chunk(token)
        self.scroll_to_bottom()
        return False

    def finish_assistant_message(self):
        if self.current_assistant_bubble:
            self.current_assistant_bubble.finish_thought()
            self.last_assistant_text = self.current_assistant_bubble.raw_text
            self.last_assistant_bubble = self.current_assistant_bubble
            has_content = bool(self.current_assistant_bubble.raw_text.strip())
            has_thinking = bool(self.current_assistant_bubble.reasoning_text.strip())
            if not has_content and not has_thinking:
                self.container.remove(self.current_assistant_bubble)
            else:
                self.current_assistant_bubble.finalize()
            self.current_assistant_bubble = None
        self.scroll_to_bottom()
        return False

    def start_tool_card(self, tool_name: str, arguments: Dict[str, Any]):
        self.hide_welcome()
        if self.current_assistant_bubble:
            self.finish_assistant_message()
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

    def add_automation_card(self, title: str, text: str, status: str = "ok", is_heartbeat: bool = False):
        self.hide_welcome()
        card = AutomationCard(
            title=title,
            text=text,
            status=status,
            is_heartbeat=is_heartbeat,
            on_tts_requested=self.on_tts_requested
        )
        self.container.append(card)
        self.scroll_to_bottom()
        return card

    def clear(self):
        while child := self.container.get_first_child():
            self.container.remove(child)
        self.current_assistant_bubble = None
        self.current_tool_card = None
        self.welcome_widget = None

    def refresh_theme(self):
        """Refreshes Pango markdown colors across all rendered bubbles and cards in this view."""
        child = self.container.get_first_child()
        while child:
            if hasattr(child, "refresh_theme"):
                child.refresh_theme()
            child = child.get_next_sibling()

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
                if not msg.get("_auto_injected_media"):
                    display_txt = msg.get("display_text") if msg.get("display_text") is not None else _clean_user_display_text(content)
                    atts = msg.get("attachments") or []
                    if display_txt or atts:
                        bubble = MessageBubble("user", display_txt or "", attachments=atts)
                        self.container.append(bubble)
                i += 1
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                raw_content = content or ""
                reasoning = msg.get("reasoning_content") or msg.get("thinking") or ""

                # Extract <think>...</think> from raw_content if present
                think_match = re.search(r'<think>([\s\S]*?)</think>', raw_content, re.IGNORECASE)
                if think_match:
                    if not reasoning:
                        reasoning = think_match.group(1).strip()
                    raw_content = re.sub(r'<think>[\s\S]*?</think>', '', raw_content, flags=re.IGNORECASE).strip()

                if tool_calls:
                    # If this assistant step had reasoning before calling tools, show it first
                    if raw_content or reasoning:
                        bubble = MessageBubble("assistant", raw_content, reasoning=reasoning, on_tts_requested=self.on_tts_requested)
                        self.container.append(bubble)
                        raw_content = ""
                        reasoning = ""

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

                if raw_content or reasoning:
                    bubble = MessageBubble("assistant", raw_content, reasoning=reasoning, on_tts_requested=self.on_tts_requested)
                    self.last_assistant_bubble = bubble
                    self.container.append(bubble)
            else:
                i += 1

        self.scroll_to_bottom()

    def _is_at_bottom(self) -> bool:
        adj = self.get_vadjustment()
        return adj.get_value() + adj.get_page_size() >= adj.get_upper() - self._scroll_threshold

    def _on_scroll_value_changed(self, _adj):
        if self._programmatic_scroll:
            return
        self._auto_scroll = self._is_at_bottom()

    def _on_scroll_metrics_changed(self, *_args):
        if self._auto_scroll and not self._is_at_bottom():
            self.scroll_to_bottom()

    def scroll_to_bottom(self, force: bool = False):
        if force:
            self._auto_scroll = True
        if not self._auto_scroll:
            return
        if not self._scroll_pending:
            self._scroll_pending = True
            GLib.idle_add(self._do_scroll)

    def _do_scroll(self):
        self._scroll_pending = False
        adj = self.get_vadjustment()
        self._programmatic_scroll = True
        try:
            adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        finally:
            self._programmatic_scroll = False
        return False
