from __future__ import annotations

from rich.segment import Segment
from rich.style import Style

from textual.content import Content
from textual.strip import Strip
from textual.widgets import Static


class LazyText(Static):
    """Line-based lazy-rendering widget for large text content.

    Pre-splits content into per-line Content objects once on content/width
    change, then builds Strips lazily on first render_line access.  This
    avoids O(N) full-content strip building on every dirty refresh.
    """

    def __init__(self, content: str | RichText = "", **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._lines: list[Content] = []
        self._strips: list[Strip | None] = []
        self._strips_key: tuple[int, int] = (-1, 0)

    def _content_sig(self) -> int:
        return id(self.visual)

    def _rebuild(self, width: int) -> None:
        visual = self.visual
        if isinstance(visual, Content):
            self._lines = visual.split("\n", allow_blank=True)
        else:
            self._lines = []
        self._strips = [None] * len(self._lines)
        self._strips_key = (width, self._content_sig())

    def get_content_height(self, container, viewport, width):
        sig = self._content_sig()
        if self._strips_key != (width, sig):
            self._rebuild(width)
        return len(self._lines)

    def render_line(self, y: int) -> Strip:
        w = self.size.width if self.size else 80
        sig = self._content_sig()
        if self._strips_key != (w, sig):
            self._rebuild(w)
        if y >= len(self._strips):
            return Strip.blank(self.size.width)
        if self._strips[y] is None:
            self._strips[y] = _build_strip(self._lines[y])
        return self._strips[y]  # type: ignore

    def selection_updated(self, selection) -> None:
        pass

    def update(self, content="", layout=True):
        self._lines = []
        self._strips = []
        self._strips_key = (-1, 0)
        super().update(content, layout=layout)


def _build_strip(line: Content) -> Strip:
    plain = line.plain
    spans = list(line.spans)
    if not spans:
        return Strip([Segment(plain, None)])
    spans.sort(key=lambda s: s.start)
    segments: list[Segment] = []
    pos = 0
    for span in spans:
        if span.start > pos:
            segments.append(Segment(plain[pos:span.start], None))
        style_raw = span.style
        if isinstance(style_raw, str):
            seg_style = Style.parse(style_raw)
        elif style_raw is not None:
            seg_style = style_raw.rich_style
        else:
            seg_style = None
        segments.append(Segment(plain[span.start:span.end], seg_style or None))
        pos = span.end
    if pos < len(plain):
        segments.append(Segment(plain[pos:], None))
    return Strip(segments)
