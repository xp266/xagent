from __future__ import annotations

import re

from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound
from textual.content import Content, Span
from textual.highlight import guess_language

from src.ui.tui.colors import (
    _DIFF_ADD_BG, _DIFF_ADD_FG, _DIFF_DEL_BG, _DIFF_DEL_FG, _FENCE_BG, _LINE_NO_FG,
)

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
    Token.Name: "#9CDCFE",
    Token.Name.Builtin: "#9CDCFE",
    Token.Name.Builtin.Pseudo: "#9CDCFE",
    Token.Name.Class: "#4EC9B0",
    Token.Name.Constant: "#9CDCFE",
    Token.Name.Decorator: "#DCDCAA",
    Token.Name.Function: "#DCDCAA",
    Token.Name.Namespace: "#9CDCFE",
    Token.Name.Other: "#9CDCFE",
    Token.Name.Tag: "#569CD6",
    Token.Name.Attribute: "#9CDCFE",
    Token.Name.Variable: "#9CDCFE",
    Token.Number: "#B5CEA8",
    Token.Operator: "#D4D4D4",
    Token.Operator.Word: "#569CD6",
    Token.Punctuation: "#D4D4D4",
    Token.Generic.Prompt: "#CCCCCC",
    Token.Generic.Output: "#CCCCCC",
}
_NUMBERED_DIFF_RE = re.compile(r"^(\d{1,7}) ([ +\-]) (.*)$")
def _token_style(tok_type) -> str | None:
    if tok_type is Token.Error:
        return None
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


def _diff_highlight(code: str, lang: str | None, bg: bool = True) -> list[Content]:
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
        group: list[str] = []
        while i < n and lines[i] and lines[i][0] in "+-":
            group.append(lines[i])
            i += 1
        content = "\n".join(
            l[2:] if len(l) > 1 and l[1] == " " else l[1:] for l in group
        )
        hl = _highlight_lines(content, lang, bg)
        for j, hl_line in enumerate(hl):
            prefix = group[j][0]
            if prefix == "-":
                marker_style = _DIFF_DEL_FG
                row_bg = _DIFF_DEL_BG
            else:
                marker_style = _DIFF_ADD_FG
                row_bg = _DIFF_ADD_BG
            if bg:
                if hl_line.cell_length == 0:
                    hl_line = Content(" ", spans=[Span(0, 1, f"on {row_bg}")])
                else:
                    hl_line = hl_line.stylize(f"on {row_bg}")
            out.append(Content.assemble((f"{prefix} ", marker_style), hl_line))
    return out


def _numbered_diff_highlight(code: str, lang: str | None, bg: bool = True) -> list[Content]:
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
        hl = _highlight_lines(content, lang, bg)
        for g, hl_line in zip(group, hl):
            if kind == " ":
                no_style = f"{_LINE_NO_FG} on {_FENCE_BG}" if bg else _LINE_NO_FG
                parts = [(f"{g[0]:>{maxw}}   ", no_style)]
                row_bg = _FENCE_BG
            else:
                if kind == "-":
                    marker_style = _DIFF_DEL_FG
                    row_bg = _DIFF_DEL_BG
                else:
                    marker_style = _DIFF_ADD_FG
                    row_bg = _DIFF_ADD_BG
                no_style = f"{_LINE_NO_FG} on {row_bg}" if bg else _LINE_NO_FG
                marker_style = f"{marker_style} on {row_bg}" if bg else marker_style
                parts = [
                    (f"{g[0]:>{maxw}} ", no_style),
                    (f"{kind} ", marker_style),
                ]
            if bg:
                if hl_line.cell_length == 0:
                    hl_line = Content(" ", spans=[Span(0, 1, f"on {row_bg}")])
                else:
                    hl_line = hl_line.stylize(f"on {row_bg}")
            parts.append(hl_line)
            out.append(Content.assemble(*parts))
    return out


def _highlight_lines(code: str, lang: str | None, bg: bool = True) -> list[Content]:
    key = (lang, code, bg)
    hit = _HIGHLIGHT_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_HIGHLIGHT_CACHE) >= _HIGHLIGHT_CACHE_MAX:
        _HIGHLIGHT_CACHE.clear()
    if not code:
        out = [Content("")]
        _HIGHLIGHT_CACHE[key] = out
        return out
    if lang != "markdown" and _is_diff_code(code):
        out = _diff_highlight(code, lang, bg)
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


def _highlight_lines_fg(code: str, lang: str | None) -> list[Content]:
    key = ("fg", lang, code)
    hit = _HIGHLIGHT_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_HIGHLIGHT_CACHE) >= _HIGHLIGHT_CACHE_MAX:
        _HIGHLIGHT_CACHE.clear()
    if lang:
        try:
            lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except ClassNotFound:
            lexer = get_lexer_by_name("text", stripnl=False, ensurenl=False)
    else:
        lexer = get_lexer_by_name("text", stripnl=False, ensurenl=False)
    lines: list[str] = []
    spans_by_line: list[list[Span]] = []
    text_parts: list[str] = []
    spans: list[Span] = []
    pos = 0
    try:
        for tok_type, tok_text in lexer.get_tokens(code):
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
