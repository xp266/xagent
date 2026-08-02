from __future__ import annotations

import re
import unicodedata

from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from textual.content import Content, Span
from textual.highlight import guess_language

_INLINE_CODE_FG = "#B3B3B3"
_INLINE_CODE_BG = "#2A2A2A"
_QUOTE_FG = "#9B9B9B"
_HR_FG = "#555555"
_LINK_FG = "#0178D4"
_LINE_NO_FG = "#555555"
_FENCE_BG = "#1A1A1A"
_OPEN_FENCE_FG = "#666666"

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
    Token.Name.Variable: "#9CDCFE",
    Token.Number: "#B5CEA8",
    Token.Operator.Word: "#569CD6",
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
        row = row.stylize("bold")
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
            marker_style = "#FF9E9E" if prefix == "-" else "#9FD28A"
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
    maxw = max(4, max(len(str(r[0])) for r in rows))
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
            parts: list = [(f"{g[0]:>{maxw}} ", _LINE_NO_FG)]
            if kind == " ":
                parts.append("  ")
            else:
                parts.append((f"{kind} ", "#FF9E9E" if kind == "-" else "#9FD28A"))
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


def _fence_body(code: str, lang: str | None, *, numbered: bool, diff_nums: bool = False) -> Content:
    if diff_nums:
        lines = _numbered_diff_highlight(code, lang)
    else:
        lines = _highlight_lines(code, lang)
    parts: list = []
    width = len(str(max(1, len(lines))))
    last = len(lines) - 1
    for i, line in enumerate(lines):
        if numbered:
            parts.append((f"{i + 1:>{width}} ", _LINE_NO_FG))
        parts.append(line)
        if i < last:
            parts.append("\n")
    content = Content.assemble(*parts)
    return content.stylize(f"on {_FENCE_BG}")


def _open_fence(code: str) -> Content:
    if not code:
        return Content("")
    return Content.assemble((code, f"{_OPEN_FENCE_FG} on {_FENCE_BG}"))


def _inline(text: str) -> Content:
    text = _ESCAPE_RE.sub(r"\1", text)
    parts: list = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
        token = m.group(0)
        if token[0] == "`":
            parts.append((token[1:-1], f"{_INLINE_CODE_FG} on {_INLINE_CODE_BG}"))
        elif token[0] == "*":
            if token.startswith("**"):
                parts.append((token[2:-2], "bold"))
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


def render_markdown(source: str, *, numbered: bool = False) -> Content:
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
            if closed:
                parts.append(_fence_body(code, lang or None, numbered=numbered, diff_nums=diff_nums))
            else:
                parts.append(_open_fence(code))
            parts.append("\n")
            continue
        if _is_table_row(line) and i + 1 < n and _is_table_sep(lines[i + 1]):
            table, i = _table_block(lines, i, n)
            parts.append(table)
            parts.append("\n")
            continue
        m = _HEADING_RE.match(line)
        if m is not None:
            parts.append(Content.assemble((m.group(2), "bold")))
            parts.append("\n")
            i += 1
            continue
        if _HR_RE.match(line):
            parts.append(Content.assemble(("─" * 30, _HR_FG)))
            parts.append("\n")
            i += 1
            continue
        m = _QUOTE_RE.match(line)
        if m is not None:
            parts.append(
                Content.assemble(
                    ("│ ", _QUOTE_FG), _inline(m.group(1)).stylize(_QUOTE_FG)
                )
            )
            parts.append("\n")
            i += 1
            continue
        m = _UL_RE.match(line)
        if m is not None:
            parts.append(
                Content.assemble((m.group(1) + "• ", _QUOTE_FG), _inline(m.group(2)))
            )
            parts.append("\n")
            i += 1
            continue
        m = _OL_RE.match(line)
        if m is not None:
            parts.append(
                Content.assemble(
                    (m.group(1) + m.group(2) + ". ", _QUOTE_FG), _inline(m.group(3))
                )
            )
            parts.append("\n")
            i += 1
            continue
        parts.append(_inline(line))
        parts.append("\n")
        i += 1
    return Content.assemble(*parts)
