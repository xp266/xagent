from __future__ import annotations

import re
import unicodedata

from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from rich.style import Style as RichStyle
from textual.content import Content, Span
from textual.highlight import guess_language

_INLINE_CODE_FG = "#6A9955"
_HEADING_FG = "#FFA500"
_QUOTE_FG = "#9B9B9B"
_HR_FG = "#555555"
_LINK_FG = "#0178D4"
_LINE_NO_FG = "#858585"
_FENCE_BG = "#1A1A1A"
_OPEN_FENCE_FG = "#808080"

_DIFF_DEL_FG = "#FF9E9E"
_DIFF_DEL_BG = "#251F1F"
_DIFF_ADD_FG = "#9FD28A"
_DIFF_ADD_BG = "#1D271D"

_HIGHLIGHT_CACHE: dict[tuple, list[Content]] = {}
_HIGHLIGHT_CACHE_MAX = 100
_MAX_HIGHLIGHT_BYTES = 200_000

_TOKEN_STYLES = {
    Token.Comment: "italic #6A9955",
    Token.Error: "bold #F14C4C",
    Token.Generic.Emph: "italic",
    Token.Generic.Deleted: "#FF9E9E",
    Token.Generic.Inserted: "#9FD28A",
    Token.Generic.Heading: "bold #569CD6",
    Token.Generic.Strong: "bold",
    Token.Generic.Subheading: "bold #569CD6",
    Token.Keyword: "#569CD6",
    Token.Keyword.Constant: "#569CD6",
    Token.Keyword.Namespace: "#569CD6",
    Token.Keyword.Type: "#4EC9B0",
    Token.Literal.String: "#CE9178",
    Token.Literal.String.Doc: "italic #6A9955",
    Token.Literal.String.Double: "#CE9178",
    Token.Literal.String.Single: "#CE9178",
    Token.Name.Builtin: "#9CDCFE",
    Token.Name.Class: "#4EC9B0",
    Token.Name.Constant: "#9CDCFE",
    Token.Name.Decorator: "#DCDCAA",
    Token.Name.Function: "#DCDCAA",
    Token.Name.Tag: "#569CD6",
    Token.Name.Attribute: "#9CDCFE",
    Token.Name.Variable: "#9CDCFE",
    Token.Number: "#B5CEA8",
    Token.Operator: "#D4D4D4",
    Token.Operator.Word: "#569CD6",
    Token.Generic.Prompt: "#CCCCCC",
    Token.Generic.Output: "#CCCCCC",
}

_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+.\-@]*)\s*$")
_NUMBERED_DIFF_RE = re.compile(r"^(\d{1,7}) ([ +\-]) (.*)$")
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

_TABLE_BORDER = "#666666"
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
    row = Content.assemble(*parts)
    if header:
        row = row.stylize(f"{_HEADING_FG}")
    return row


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


def _token_style(tok_type) -> str | None:
    while tok_type is not None:
        style = _TOKEN_STYLES.get(tok_type)
        if style is not None:
            return style
        tok_type = tok_type.parent
    return None


def _get_lexer(lang: str | None, code: str):
    if lang:
        try:
            return get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except ClassNotFound:
            pass
    else:
        try:
            name = guess_language(code, None)
        except Exception:
            name = "text"
        if name and name != "default":
            try:
                return get_lexer_by_name(name, stripnl=False, ensurenl=False)
            except ClassNotFound:
                pass
    return get_lexer_by_name("text", stripnl=False, ensurenl=False)


def _is_diff_code(code: str) -> bool:
    lines = [l for l in code.split("\n") if l.strip()]
    return bool(lines) and all(l.startswith("@@") or l[0] in "+-" for l in lines)


def _diff_highlight(code: str, lang: str | None) -> list[Content]:
    lines = code.split("\n")
    out: list[Content] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line:
            out.append(Content(""))
            i += 1
            continue
        if line.startswith("@@"):
            out.append(Content.assemble((line, "bold #666666")))
            i += 1
            continue
        marker = line[0]
        group: list[str] = []
        while i < n and lines[i] and lines[i][0] in "+-":
            group.append(lines[i])
            i += 1
        content = "\n".join(
            l[2:] if len(l) > 1 and l[1] == " " else l[1:] for l in group
        )
        hl = _highlight_lines(content, lang)
        for j, hl_line in enumerate(hl):
            prefix = group[j][0]
            if prefix == "-":
                marker_style = _DIFF_DEL_FG
                bg = _DIFF_DEL_BG
            else:
                marker_style = _DIFF_ADD_FG
                bg = _DIFF_ADD_BG
            if hl_line.cell_length == 0:
                hl_line = Content(" ", spans=[Span(0, 1, f"on {bg}")])
            else:
                hl_line = hl_line.stylize(f"on {bg}")
            out.append(Content.assemble((f"{prefix} ", marker_style), hl_line))
    return out


def _numbered_diff_highlight(code: str, lang: str | None) -> list[Content]:
    rows: list[tuple[int, str, str]] = []
    for l in code.split("\n"):
        m = _NUMBERED_DIFF_RE.match(l)
        if m is None:
            rows.append((0, " ", l))
        else:
            rows.append((int(m.group(1)), m.group(2), m.group(3)))
    maxw = max(2, max((len(str(r[0])) for r in rows), default=1))
    out: list[Content] = []
    i = 0
    n = len(rows)
    while i < n:
        kind = rows[i][1]
        group: list[tuple[int, str, str]] = []
        while i < n and rows[i][1] == kind:
            group.append(rows[i])
            i += 1
        content = "\n".join(g[2] for g in group)
        hl = _highlight_lines(content, lang)
        for g, hl_line in zip(group, hl):
            if kind == " ":
                parts = [(f"{g[0]:>{maxw}}   ", f"{_LINE_NO_FG} on {_FENCE_BG}")]
                bg = _FENCE_BG
            else:
                if kind == "-":
                    marker_style = _DIFF_DEL_FG
                    bg = _DIFF_DEL_BG
                else:
                    marker_style = _DIFF_ADD_FG
                    bg = _DIFF_ADD_BG
                parts = [
                    (f"{g[0]:>{maxw}} ", f"{_LINE_NO_FG} on {bg}"),
                    (f"{kind} ", f"{marker_style} on {bg}"),
                ]
            if hl_line.cell_length == 0:
                hl_line = Content(" ", spans=[Span(0, 1, f"on {bg}")])
            else:
                hl_line = hl_line.stylize(f"on {bg}")
            parts.append(hl_line)
            out.append(Content.assemble(*parts))
    return out


def _highlight_lines(code: str, lang: str | None) -> list[Content]:
    key = (lang, code)
    hit = _HIGHLIGHT_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_HIGHLIGHT_CACHE) >= _HIGHLIGHT_CACHE_MAX:
        _HIGHLIGHT_CACHE.clear()
    if not code:
        out = [Content("")]
        _HIGHLIGHT_CACHE[key] = out
        return out
    if _is_diff_code(code):
        out = _diff_highlight(code, lang)
        _HIGHLIGHT_CACHE[key] = out
        return out
    if len(code) > _MAX_HIGHLIGHT_BYTES:
        out = [Content(line) for line in code.split("\n")]
        _HIGHLIGHT_CACHE[key] = out
        return out
    lines: list[str] = []
    spans_by_line: list[list[Span]] = []
    text_parts: list[str] = []
    spans: list[Span] = []
    pos = 0
    try:
        for tok_type, tok_text in _get_lexer(lang, code).get_tokens(code):
            style = _token_style(tok_type)
            while True:
                nl = tok_text.find("\n")
                if nl < 0:
                    if style and tok_text:
                        spans.append(Span(pos, pos + len(tok_text), style))
                    text_parts.append(tok_text)
                    pos += len(tok_text)
                    break
                head, tok_text = tok_text[:nl], tok_text[nl + 1:]
                if style and head:
                    spans.append(Span(pos, pos + len(head), style))
                text_parts.append(head)
                lines.append("".join(text_parts))
                spans_by_line.append(spans)
                text_parts = []
                spans = []
                pos = 0
    except Exception:
        lines = code.split("\n")
        spans_by_line = [[] for _ in lines]
        text_parts = []
        spans = []
    lines.append("".join(text_parts))
    spans_by_line.append(spans)
    out = [Content(text, spans=sp) for text, sp in zip(lines, spans_by_line)]
    _HIGHLIGHT_CACHE[key] = out
    return out


def _line_bg(c: Content) -> str | None:
    for s in c._spans:
        style = s.style
        if style is None:
            continue
        if isinstance(style, str):
            style = RichStyle.parse(style)
        if style.bgcolor is not None:
            r, g, b = style.bgcolor.get_truecolor(None)
            return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _fence_body(code: str, lang: str | None, *, numbered: bool, diff_nums: bool = False, line_number_start: int = 1) -> Content:
    if diff_nums:
        lines = _numbered_diff_highlight(code, lang)
    else:
        lines = _highlight_lines(code, lang)
    max_cells = max((l.cell_length for l in lines), default=0)
    parts: list = []
    width = 4
    if numbered:
        width = max(2, len(str(max(1, line_number_start + len(lines) - 1))))
    last = len(lines) - 1
    for i, line in enumerate(lines):
        if numbered:
            parts.append((f"{i + line_number_start:>{width}}   ", f"{_LINE_NO_FG} on {_FENCE_BG}"))
        bg = _line_bg(line)
        if bg is None:
            line = line.stylize(f"on {_FENCE_BG}")
            bg = _FENCE_BG
        parts.append(line)
        pad = max_cells - line.cell_length
        if pad > 0:
            parts.append((" " * pad, f"on {bg}"))
        if i < last:
            parts.append("\n")
    return Content.assemble(*parts)


def _open_fence(code: str) -> Content:
    if not code:
        return Content("")
    return Content.assemble((code, f"{_OPEN_FENCE_FG} on {_FENCE_BG}"))


def _plain_fence_body(code: str) -> list[Content]:
    lines = code.split("\n")
    while lines and not lines[-1]:
        lines.pop()
    return [Content(line).stylize(f"on {_FENCE_BG}") for line in lines]


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
                parts.append((token[1:-1], "italic"))
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
        return Content.assemble((m.group(2), f"{_HEADING_FG}"))
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


def render_markdown(source: str, *, numbered: bool = False, line_number_start: int = 1) -> Content:
    parts: list = []
    lines = source.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE_RE.match(line)
        if m is not None:
            info = m.group(1)
            lang, _, flags = info.partition("@")
            diff_nums = flags == "n"
            body: list[str] = []
            i += 1
            closed = False
            while i < n:
                if _FENCE_RE.match(lines[i]):
                    closed = True
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            code = "\n".join(body).rstrip("\n")
            parts.append(_fence_body(code, lang or None, numbered=numbered, diff_nums=diff_nums, line_number_start=line_number_start))
            parts.append("\n")
            continue
        if _is_table_row(line) and i + 1 < n and _is_table_sep(lines[i + 1]):
            table, i = _table_block(lines, i, n)
            parts.append(table)
            parts.append("\n")
            continue
        rendered = _render_line(line)
        if rendered is not None:
            parts.append(rendered)
            parts.append("\n")
        i += 1
    return Content.assemble(*parts)


class StreamMarkdown:
    def __init__(self, *, numbered: bool = False, line_number_start: int = 1):
        self._numbered = numbered
        self._line_number_start = line_number_start
        self._lines: list[Content] = []
        self._tail = ""
        self._prev: Content | None = None
        self._prev_text = ""
        self._fence_open = False
        self._fence_lang: str | None = None
        self._fence_diff = False
        self._fence_body: list[str] = []
        self._fence_start = 0
        self._table_rows: list[str] | None = None

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
            code = "\n".join(self._fence_body).rstrip("\n")
            block = _fence_body(code, self._fence_lang, numbered=self._numbered, diff_nums=self._fence_diff, line_number_start=self._line_number_start)
            del self._lines[self._fence_start:]
            self._lines.extend(block.split("\n", allow_blank=True))
            self._fence_open = False
            self._fence_body = []
        if self._table_rows is not None:
            rows = self._table_rows
            self._table_rows = None
            block, _ = _table_block(rows, 0, len(rows))
            self._lines.append(block)
        self._commit_prev()

    def render(self) -> Content:
        parts: list = []
        for line in self._lines:
            parts.append(line)
            parts.append("\n")
        if self._prev is not None:
            parts.append(self._prev)
            parts.append("\n")
        if self._tail:
            if self._fence_open:
                parts.append(_open_fence(self._tail))
                parts.append("\n")
            else:
                tail_line = _render_line(self._tail)
                if tail_line is not None:
                    parts.append(tail_line)
                    parts.append("\n")
        return Content.assemble(*parts)

    def _commit_prev(self) -> None:
        if self._prev is not None:
            self._lines.append(self._prev)
            self._prev = None
            self._prev_text = ""

    def _line(self, line: str) -> None:
        if self._fence_open:
            if _FENCE_RE.match(line) is not None:
                code = "\n".join(self._fence_body).rstrip("\n")
                self._close_fence(code)
            else:
                self._fence_body.append(line)
                self._rerender_open_fence()
            return
        if self._table_rows is not None:
            if _is_table_row(line):
                self._table_rows.append(line)
                return
            rows = self._table_rows
            self._table_rows = None
            block, _ = _table_block(rows, 0, len(rows))
            self._lines.append(block)
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
            return
        if self._prev is not None:
            if _is_table_row(self._prev_text) and _is_table_sep(line):
                self._table_rows = [self._prev_text, line]
                self._prev = None
                self._prev_text = ""
                return
            self._lines.append(self._prev)
        self._prev = _render_line(line)
        self._prev_text = line

    def _close_fence(self, code: str) -> None:
        lang = self._fence_lang
        block = _fence_body(code, lang, numbered=self._numbered, diff_nums=self._fence_diff, line_number_start=self._line_number_start)
        del self._lines[self._fence_start:]
        self._lines.extend(block.split("\n", allow_blank=True))
        self._fence_open = False
        self._fence_body = []
        self._fence_start = 0

    def _rerender_open_fence(self) -> None:
        code = "\n".join(self._fence_body)
        del self._lines[self._fence_start:]
        self._lines.extend(_plain_fence_body(code))
