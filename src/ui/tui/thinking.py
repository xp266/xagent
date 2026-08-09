from __future__ import annotations

import re

from textual.content import Content, Span

from src.ui.tui.colors import _INLINE_CODE_FG
from src.ui.tui.highlight import _highlight_lines_fg
from src.ui.tui.markdown import _FENCE_RE

_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _thinking_inline(text: str) -> Content:
    if not text:
        return Content("")
    m = _INLINE_CODE_RE.search(text)
    if m is None:
        return Content(text)
    parts: list = []
    pos = 0
    for m in _INLINE_CODE_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
        parts.append((m.group(0)[1:-1], _INLINE_CODE_FG))
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return Content.assemble(*parts)


def _thinking_line(line: str) -> Content:
    if _FENCE_RE.match(line) is not None:
        return Content(line)
    return _thinking_inline(line)


class ThinkingMarkdown:
    def __init__(self):
        self._lines: list[Content] = []
        self._tail = ""
        self._fence_open = False
        self._fence_lang: str | None = None
        self._fence_body: list[str] = []
        self._fence_start = 0
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
            self._close_fence()

    def render(self) -> Content:
        if self._rendered_n < len(self._lines):
            parts = []
            for line in self._lines[self._rendered_n:]:
                parts.append(line)
                parts.append("\n")
            block = Content.assemble(*parts)
            if self._rendered is None:
                self._rendered = block
                self._rendered_cell = block.cell_length
            else:
                base = self._rendered.plain
                self._rendered = Content(
                    base + block.plain,
                    spans=list(self._rendered.spans)
                    + [Span(s.start + len(base), s.end + len(base), s.style) for s in block.spans],
                    cell_length=self._rendered_cell + block.cell_length,
                    strip_control_codes=False,
                )
                self._rendered_cell += block.cell_length
            self._rendered_n = len(self._lines)
        result = self._rendered
        if self._tail:
            tail_c = _thinking_line(self._tail)
            if result is None:
                result = Content.assemble(tail_c, "\n")
            else:
                base = result.plain
                result = Content(
                    base + tail_c.plain + "\n",
                    spans=list(result.spans)
                    + [Span(s.start + len(base), s.end + len(base), s.style) for s in tail_c.spans],
                    cell_length=result.cell_length + tail_c.cell_length + 1,
                    strip_control_codes=False,
                )
        return result if result is not None else Content("")

    def _line(self, line: str) -> None:
        if self._fence_open:
            if _FENCE_RE.match(line) is not None:
                self._close_fence()
                self._lines.append(_thinking_line(line))
            else:
                self._fence_body.append(line)
                self._lines.append(_thinking_line(line))
            return
        m = _FENCE_RE.match(line)
        if m is not None:
            info = m.group(1)
            lang, _, _ = info.partition("@")
            self._fence_open = True
            self._fence_lang = lang or None
            self._fence_body = []
            self._fence_start = len(self._lines)
            self._lines.append(_thinking_line(line))
            return
        self._lines.append(_thinking_line(line))

    def _close_fence(self) -> None:
        code = "\n".join(self._fence_body).rstrip("\n")
        lines = _highlight_lines_fg(code, self._fence_lang)
        del self._lines[self._fence_start + 1:]
        self._lines.extend(lines)
        self._fence_open = False
        self._fence_body = []
        self._rendered = None
        self._rendered_n = 0


def render_thinking_markdown(source: str) -> Content:
    md = ThinkingMarkdown()
    md.feed(source)
    md.finish()
    return md.render()

