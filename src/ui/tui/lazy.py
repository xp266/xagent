from __future__ import annotations

from rich.segment import Segment
from rich.text import Text as RichText

from textual.content import Content
from textual.strip import Strip
from textual.widgets import Static


class LazyText(Static):
    """Line-based lazy-rendering widget for large text content.

    Builds on Static but overrides get_content_height / render_line to
    avoid O(N) full-content re-wrap on every dirty refresh.
    """

    def __init__(self, content: str | RichText = "", **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._strips: list[Strip] = []
        self._strips_key: tuple[int, int] = (-1, 0)

    def _content_sig(self) -> int:
        return id(self.visual)

    def _rebuild(self, width: int) -> None:
        visual = self.visual
        if isinstance(visual, Content):
            lines = visual.split("\n", allow_blank=True)
            self._strips = _content_to_strips(lines)
        else:
            self._strips = []
        self._strips_key = (width, self._content_sig())

    def get_content_height(self, container, viewport, width):
        sig = self._content_sig()
        if self._strips_key != (width, sig):
            self._rebuild(width)
        visual = self.visual
        if isinstance(visual, Content):
            return visual.plain.count("\n") + 1
        return 0

    def render_line(self, y: int) -> Strip:
        w = self.size.width if self.size else 80
        sig = self._content_sig()
        if self._strips_key != (w, sig):
            self._rebuild(w)
        if y < len(self._strips):
            return self._strips[y]
        return Strip.blank(self.size.width)

    def update(self, content="", layout=True):
        self._strips = []
        self._strips_key = (-1, 0)
        super().update(content, layout=layout)


def _content_to_strips(lines: list[Content]) -> list[Strip]:
    result: list[Strip] = []
    for line in lines:
        plain = line.plain
        spans = list(line.spans)
        if not spans:
            result.append(Strip([Segment(plain, None)]))
            continue
        spans.sort(key=lambda s: s.start)
        segments: list[Segment] = []
        pos = 0
        for span in spans:
            if span.start > pos:
                segments.append(Segment(plain[pos:span.start], None))
            style_raw = span.style
            if isinstance(style_raw, str):
                seg_style = style_raw
            elif style_raw is not None:
                seg_style = style_raw.rich_style
            else:
                seg_style = None
            segments.append(Segment(plain[span.start:span.end], seg_style or None))
            pos = span.end
        if pos < len(plain):
            segments.append(Segment(plain[pos:], None))
        result.append(Strip(segments))
    return result
