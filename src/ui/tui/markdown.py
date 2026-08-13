from __future__ import annotations

import re
import unicodedata

from textual.content import Content, Span

from src.ui.tui.colors import (
    _FENCE_BG, _HEADING_1_STYLE, _HEADING_3_FG, _HEADING_FG, _HR_FG, _INLINE_CODE_FG, _ITALIC_FG,
    _LINE_NO_FG, _LINK_FG, _OPEN_FENCE_FG, _QUOTE_FG, _TABLE_BORDER,
)
from src.ui.tui.highlight import _highlight_lines, _numbered_diff_highlight
from src.ui.tui.lazy import _TAB_WIDTH, _line_bg

_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+.\-@]*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")
_INLINE_RE = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|~~[^~\n]+~~|\[[^\]\n]+\]\([^)\n]+\))"
)
_ESCAPE_RE = re.compile(r"\\([\\`*_~\[\]])")

_TABLE_CELL_MAX = 40
_TABLE_WIDTH_MAX = 100


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return bool(s) and ("|" in s) and (s.startswith("|") or s.endswith("|"))


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if "|" not in s:
        return False
    return all(_TABLE_SEP_CELL_RE.match(cell) for cell in _split_row(s))


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "|" or nxt == "\\":
                cur.append(nxt)
                i += 2
                continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return cells


def _table_align(sep_cell: str) -> str:
    c = sep_cell.strip()
    left = c.startswith(":")
    right = c.endswith(":")
    if left and right:
        return "c"
    if right:
        return "r"
    return "l"


def _truncate_content(c: Content, w: int) -> Content:
    if c.cell_length <= w:
        return c
    keep = w - 1
    cells = 0
    cut = 0
    for ch in c.plain:
        wch = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cells + wch > keep:
            break
        cells += wch
        cut += 1
    spans = [Span(s, min(e, cut), st) for s, e, st in c.spans if s < cut]
    return Content(c.plain[:cut] + "…", spans=spans)


def _table_border(widths: list[int], kind: str) -> Content:
    l, m, r = {"top": ("┌", "┬", "┐"), "mid": ("├", "┼", "┤"), "bot": ("└", "┴", "┘")}[kind]
    chars = [l]
    for ci, w in enumerate(widths):
        chars.append("─" * (w + 2))
        chars.append(m if ci < len(widths) - 1 else r)
    return Content.assemble(("".join(chars), _TABLE_BORDER))


def _table_row(cells: list[Content], widths: list[int], aligns: list[str], header: bool = False) -> Content:
    parts: list = []
    for ci, cell in enumerate(cells):
        w = widths[ci]
        cell = _truncate_content(cell, w)
        if header:
            cell = cell.stylize(f"{_HEADING_FG}")
        pad = w - cell.cell_length
        align = aligns[ci]
        if align == "c":
            left, right = pad // 2, pad - pad // 2
        elif align == "r":
            left, right = pad, 0
        else:
            left, right = 0, pad
        parts.append(("│ ", _TABLE_BORDER))
        if left:
            parts.append(" " * left)
        parts.append(cell)
        if right:
            parts.append(" " * right)
        parts.append(" ")
    parts.append(("│", _TABLE_BORDER))
    return Content.assemble(*parts)


def _table_block(lines: list[str], start: int, n: int) -> tuple[Content, int]:
    header = _split_row(lines[start])
    sep = _split_row(lines[start + 1])
    aligns = [_table_align(c) for c in sep]
    rows = [header]
    i = start + 2
    while i < n and _is_table_row(lines[i]):
        rows.append(_split_row(lines[i]))
        i += 1
    ncols = max(len(r) for r in rows)
    aligns = (aligns + ["l"] * ncols)[:ncols]
    rendered = [
        [_inline(c) for c in r + [""] * (ncols - len(r))]
        for r in rows
    ]
    widths = [min(max(row[ci].cell_length for row in rendered), _TABLE_CELL_MAX) for ci in range(ncols)]
    total = sum(widths) + 3 * ncols + 1
    if total > _TABLE_WIDTH_MAX:
        scale = _TABLE_WIDTH_MAX / total
        widths = [max(3, int(w * scale)) for w in widths]
    parts: list = [_table_border(widths, "top")]
    parts.append(_table_row(rendered[0], widths, aligns, header=True))
    parts.append(_table_border(widths, "mid"))
    for row in rendered[1:]:
        parts.append(_table_row(row, widths, aligns))
    parts.append(_table_border(widths, "bot"))
    items: list = []
    for part in parts:
        items.append(part)
        items.append("\n")
    return Content.assemble(*items[:-1]), i


def _single_row_table(line: str) -> Content:
    cells = _split_row(line)
    if not cells:
        return Content("")
    rendered = [_inline(c) for c in cells]
    widths = [max(c.cell_length, 3) for c in rendered]
    aligns = ["l"] * len(cells)
    parts = [_table_border(widths, "top")]
    parts.append(_table_row(rendered, widths, aligns, header=True))
    parts.append(_table_border(widths, "bot"))
    items: list = []
    for part in parts:
        items.append(part)
        items.append("\n")
    return Content.assemble(*items[:-1])


def _fence_body(code: str, lang: str | None, *, numbered: bool, diff_nums: bool = False, line_number_start: int = 1, bg: bool = True) -> Content:
    code = code.expandtabs(_TAB_WIDTH)
    if diff_nums:
        lines = _numbered_diff_highlight(code, lang, bg)
    else:
        lines = _highlight_lines(code, lang, bg)
    if not bg:
        parts: list = []
        width = 4
        if numbered:
            width = max(2, len(str(max(1, line_number_start + len(lines) - 1))))
        last = len(lines) - 1
        for i, line in enumerate(lines):
            if numbered:
                parts.append((f"{i + line_number_start:>{width}}   ", _LINE_NO_FG))
            parts.append(line)
            if i < last:
                parts.append("\n")
        return Content.assemble(*parts)
    max_cells = max((l.cell_length for l in lines), default=0)
    parts: list = []
    width = 4
    if numbered:
        width = max(2, len(str(max(1, line_number_start + len(lines) - 1))))
    last = len(lines) - 1
    for i, line in enumerate(lines):
        if numbered:
            parts.append((f"{i + line_number_start:>{width}}   ", f"{_LINE_NO_FG} on {_FENCE_BG}"))
        bg_color = _line_bg(line)[0]
        if bg_color is None:
            line = line.stylize(f"on {_FENCE_BG}")
            bg_color = _FENCE_BG
        parts.append(line)
        pad = max_cells - line.cell_length
        if pad > 0:
            parts.append((" " * pad, f"on {bg_color}"))
        if i < last:
            parts.append("\n")
    return Content.assemble(*parts)


def _open_fence(code: str, bg: bool = True) -> Content:
    code = code.expandtabs(_TAB_WIDTH)
    if not code:
        return Content("")
    if bg:
        return Content.assemble((code, f"{_OPEN_FENCE_FG} on {_FENCE_BG}"))
    return Content.assemble((code, _OPEN_FENCE_FG))


def _plain_fence_body(code: str, bg: bool = True) -> list[Content]:
    lines = code.expandtabs(_TAB_WIDTH).split("\n")
    while lines and not lines[-1]:
        lines.pop()
    if bg:
        return [Content(line).stylize(f"on {_FENCE_BG}") for line in lines]
    return [Content(line) for line in lines]


def _inline(text: str) -> Content:
    text = _ESCAPE_RE.sub(r"\1", text)
    parts: list = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
        token = m.group(0)
        if token[0] == "`":
            parts.append((token[1:-1], _INLINE_CODE_FG))
        elif token[0] == "*":
            if token.startswith("**"):
                parts.append((token[2:-2], f"{_HEADING_FG}"))
            else:
                parts.append((token[1:-1], f"italic {_ITALIC_FG}"))
        elif token[0] == "~":
            parts.append((token[2:-2], "strike"))
        else:
            label, _, _ = token[1:-1].partition("](")
            parts.append((label, f"underline {_LINK_FG}"))
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return Content.assemble(*parts)


def _render_line(line: str) -> Content | None:
    if not line.strip():
        return None
    m = _HEADING_RE.match(line)
    if m is not None:
        level = len(m.group(1))
        if level == 1:
            style = _HEADING_1_STYLE
        elif level == 2:
            style = _HEADING_FG
        else:
            style = _HEADING_3_FG
        return Content.assemble((m.group(2), style))
    if _HR_RE.match(line):
        return Content.assemble(("─" * 30, _HR_FG))
    m = _QUOTE_RE.match(line)
    if m is not None:
        return Content.assemble(
            ("│ ", _QUOTE_FG), _inline(m.group(1)).stylize(_QUOTE_FG)
        )
    m = _UL_RE.match(line)
    if m is not None:
        return Content.assemble((m.group(1) + "• ", _QUOTE_FG), _inline(m.group(2)))
    m = _OL_RE.match(line)
    if m is not None:
        return Content.assemble(
            (m.group(1) + m.group(2) + ". ", _QUOTE_FG), _inline(m.group(3))
        )
    return _inline(line)


def _split_content_lines(content: Content) -> list[Content]:
    result = content.split("\n", allow_blank=True)
    while result and not result[-1].plain:
        result.pop()
    return result


def render_markdown_lines(source: str, *, numbered: bool = False, line_number_start: int = 1, bg: bool = True) -> list[Content]:
    out: list[Content] = []
    lines = source.split("\n")
    i = 0
    n = len(lines)
    pending_blank = False
    while i < n:
        line = lines[i].expandtabs(_TAB_WIDTH)
        if not line.strip():
            pending_blank = True
            i += 1
            continue
        if pending_blank and out:
            out.append(Content(""))
        pending_blank = False
        m = _FENCE_RE.match(line)
        if m is not None:
            info = m.group(1)
            lang, _, flags = info.partition("@")
            diff_nums = flags == "n"
            if not bg:
                out.append(Content(m.group(0), spans=[Span(0, len(m.group(0)), _OPEN_FENCE_FG)]))
            body: list[str] = []
            i += 1
            closed = False
            close_line = ""
            while i < n:
                if _FENCE_RE.match(lines[i]):
                    closed = True
                    close_line = lines[i]
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            code = "\n".join(body).rstrip("\n")
            out.extend(_split_content_lines(_fence_body(code, lang or None, numbered=numbered, diff_nums=diff_nums, line_number_start=line_number_start, bg=bg)))
            if closed and not bg:
                out.append(Content(close_line, spans=[Span(0, len(close_line), _OPEN_FENCE_FG)]))
            continue
        if _is_table_row(line) and i + 1 < n and _is_table_sep(lines[i + 1]):
            table, i = _table_block(lines, i, n)
            out.extend(_split_content_lines(table))
            continue
        rendered = _render_line(line)
        if rendered is not None:
            out.append(rendered)
        i += 1
    while out and not out[-1].plain:
        out.pop()
    return out


def render_markdown(source: str, *, numbered: bool = False, line_number_start: int = 1, bg: bool = True) -> Content:
    lines = render_markdown_lines(source, numbered=numbered, line_number_start=line_number_start, bg=bg)
    if not lines:
        return Content("")
    parts: list = []
    for line in lines:
        parts.append(line)
        parts.append("\n")
    return Content.assemble(*parts)

class StreamMarkdown:
    def __init__(self, *, numbered: bool = False, line_number_start: int = 1, bg: bool = True):
        self._numbered = numbered
        self._line_number_start = line_number_start
        self._bg = bg
        self._lines: list[Content] = []
        self._tail = ""
        self._prev: Content | None = None
        self._prev_text = ""
        self._fence_open = False
        self._fence_lang: str | None = None
        self._fence_diff = False
        self._fence_body: list[str] = []
        self._fence_start = 0
        self._fence_marker: Content | None = None
        self._table_rows: list[str] | None = None
        self._table_start = 0
        self._table_n: int | None = None
        self._table_raw_max: list[int] = []
        self._table_widths: list[int] | None = None
        self._table_aligns: list[str] | None = None
        self._table_bot: Content | None = None
        self._rendered: Content | None = None
        self._rendered_n = 0
        self._rendered_cell = 0

    def feed(self, text: str) -> None:
        text = self._tail + text
        raw = text.split("\n")
        self._tail = raw.pop()
        for line in raw:
            self._line(line)

    def finish(self) -> None:
        if self._tail:
            self._line(self._tail)
            self._tail = ""
        if self._fence_open:
            self._close_fence(None)
        if self._table_rows is not None:
            self._table_render_full()
            self._table_rows = None
            self._table_start = 0
        self._commit_prev()
        while self._lines and not self._lines[-1].plain:
            self._lines.pop()
        self._reset_incremental()

    def render(self) -> Content:
        parts: list = []
        if self._rendered_n < len(self._lines):
            block = Content.assemble(
                *[x for line in self._lines[self._rendered_n:] for x in (line, "\n")]
            )
            if self._rendered is None:
                self._rendered = block
                self._rendered_cell = block.cell_length
            else:
                base = self._rendered.plain
                spans = self._rendered.spans
                spans.extend(
                    Span(s.start + len(base), s.end + len(base), s.style) for s in block.spans
                )
                self._rendered = Content(
                    base + block.plain,
                    spans=spans,
                    cell_length=self._rendered_cell + block.cell_length,
                    strip_control_codes=False,
                )
                self._rendered_cell += block.cell_length
            self._rendered_n = len(self._lines)
        result = self._rendered
        if self._prev is not None:
            if _is_table_row(self._prev_text):
                parts.append(_single_row_table(self._prev_text))
            else:
                parts.append(self._prev)
            parts.append("\n")
        if self._tail:
            if self._fence_open:
                parts.append(_open_fence(self._tail.expandtabs(_TAB_WIDTH), self._bg))
                parts.append("\n")
            elif self._table_rows is not None:
                pass
            elif _is_table_row(self._tail.expandtabs(_TAB_WIDTH)):
                parts.append(_single_row_table(self._tail.expandtabs(_TAB_WIDTH)))
                parts.append("\n")
            else:
                tail_line = _render_line(self._tail.expandtabs(_TAB_WIDTH))
                if tail_line is not None:
                    parts.append(tail_line)
                    parts.append("\n")
        if result is None:
            return Content.assemble(*parts) if parts else Content("")
        if not parts:
            return result
        parts.insert(0, result)
        parts.insert(1, "\n")
        return Content.assemble(*parts)

    def _reset_table_geometry(self) -> None:
        self._table_n = None
        self._table_raw_max = []
        self._table_widths = None
        self._table_aligns = None
        self._table_bot = None

    def _reset_incremental(self) -> None:
        self._rendered = None
        self._rendered_n = 0
        self._rendered_cell = 0

    def _commit_prev(self) -> None:
        if self._prev is not None:
            self._lines.append(self._prev)
            self._prev = None
            self._prev_text = ""

    def _line(self, line: str) -> None:
        line = line.expandtabs(_TAB_WIDTH)
        if self._fence_open:
            if _FENCE_RE.match(line) is not None:
                self._close_fence(line)
            else:
                self._fence_body.append(line)
                body = _plain_fence_body(line, self._bg)
                self._lines.append(body[0] if body else Content(""))
            return
        if self._table_rows is not None:
            if _is_table_row(line):
                self._table_rows.append(line)
                self._rerender_open_table()
                return
            self._table_rows = None
            self._table_start = 0
            self._reset_table_geometry()
        m = _FENCE_RE.match(line)
        if m is not None:
            info = m.group(1)
            lang, _, flags = info.partition("@")
            self._fence_open = True
            self._fence_lang = lang or None
            self._fence_diff = flags == "n"
            self._fence_body = []
            self._commit_prev()
            self._fence_start = len(self._lines)
            if not self._bg:
                self._fence_marker = Content(line, spans=[Span(0, len(line), _OPEN_FENCE_FG)])
                self._lines.append(self._fence_marker)
            else:
                self._fence_marker = None
            return
        if not line.strip():
            self._commit_prev()
            if self._lines and self._lines[-1].plain != "":
                self._lines.append(Content(""))
            return
        if self._prev is not None:
            if _is_table_row(self._prev_text) and _is_table_sep(line):
                self._table_rows = [self._prev_text, line]
                self._table_start = len(self._lines)
                self._reset_table_geometry()
                self._prev = None
                self._prev_text = ""
                self._rerender_open_table()
                return
            self._lines.append(self._prev)
        self._prev = _render_line(line)
        self._prev_text = line

    def _table_row_raw_widths(self, row: str) -> list[int]:
        cells = [_inline(c) for c in _split_row(row)]
        return [min(max(c.cell_length, 3), _TABLE_CELL_MAX) for c in cells]

    def _table_scale(self, widths: list[int]) -> list[int]:
        total = sum(widths) + 3 * len(widths) + 1
        if total > _TABLE_WIDTH_MAX:
            scale = _TABLE_WIDTH_MAX / total
            widths = [max(3, int(w * scale)) for w in widths]
        return widths

    def _rerender_open_table(self) -> None:
        rows = self._table_rows
        if not rows:
            return
        if self._table_n is None:
            self._table_render_full()
            return
        self._reset_incremental()
        if len(rows) == self._table_n:
            return
        new_widths = self._table_row_raw_widths(rows[-1])
        if len(new_widths) > len(self._table_raw_max) or any(
            w > m for w, m in zip(new_widths, self._table_raw_max)
        ):
            self._table_render_full()
            return
        row_cells = [_inline(c) for c in _split_row(rows[-1])]
        row_cells += [Content("")] * (len(self._table_widths) - len(row_cells))
        row = _table_row(row_cells, self._table_widths, self._table_aligns)
        bot_idx = self._table_start + self._table_n + 1
        del self._lines[bot_idx]
        self._lines.insert(bot_idx, row)
        self._lines.insert(bot_idx + 1, self._table_bot)
        self._table_n += 1

    def _table_render_full(self) -> None:
        rows = self._table_rows
        if not rows:
            return
        self._reset_incremental()
        block, _ = _table_block(rows, 0, len(rows))
        del self._lines[self._table_start:]
        lines = block.split("\n", allow_blank=True)
        self._lines.extend(lines)
        self._table_n = len(rows)
        raw_max: list[int] = []
        for row in rows:
            widths = self._table_row_raw_widths(row)
            if len(widths) > len(raw_max):
                raw_max.extend([0] * (len(widths) - len(raw_max)))
            for i, w in enumerate(widths):
                if w > raw_max[i]:
                    raw_max[i] = w
        self._table_raw_max = raw_max
        self._table_widths = self._table_scale(list(raw_max))
        aligns = [_table_align(c) for c in _split_row(rows[1])] if len(rows) > 1 else ["l"]
        self._table_aligns = (aligns + ["l"] * len(self._table_widths))[:len(self._table_widths)]
        self._table_bot = lines[-1]

    def _close_fence(self, close_line: str | None) -> None:
        code = "\n".join(self._fence_body).rstrip("\n")
        block = _fence_body(code, self._fence_lang, numbered=self._numbered, diff_nums=self._fence_diff, line_number_start=self._line_number_start, bg=self._bg)
        offset = 1 if self._fence_marker is not None else 0
        del self._lines[self._fence_start + offset:]
        self._lines.extend(block.split("\n", allow_blank=True))
        if self._fence_marker is not None and close_line is not None:
            self._lines.append(Content(close_line, spans=[Span(0, len(close_line), _OPEN_FENCE_FG)]))
        self._fence_open = False
        self._fence_body = []
        self._fence_start = 0
        self._fence_marker = None
        self._reset_incremental()



