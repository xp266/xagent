from __future__ import annotations

from rich.segment import Segment
from rich.style import Style

from textual.content import Content
from textual.strip import Strip
from textual.style import Style as TextualStyle
from textual.widgets import Static


class LazyText(Static):
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
        while self._lines and self._lines[-1].plain == "":
            self._lines.pop()
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
            line = self._lines[y]
            selection = self.text_selection
            if selection is not None:
                span = selection.get_span(y)
                if span is not None:
                    start, end = span
                    if end == -1:
                        end = len(line.plain)
                    if start < end:
                        selection_style = TextualStyle.from_styles(
                            self.screen.get_component_styles("screen--selection")
                        )
                        line = line.stylize(selection_style, start, end)
            self._strips[y] = _build_strip(line, y)
        return self._strips[y].apply_style(self.visual_style.rich_style)

    def selection_updated(self, selection) -> None:
        self._strips = [None] * len(self._lines)
        self.refresh()

    def update(self, content="", layout=True):
        self._lines = []
        self._strips = []
        self._strips_key = (-1, 0)
        super().update(content, layout=layout)


def _build_strip(line: Content, offset_y: int) -> Strip:
    plain = line.plain
    spans = list(line.spans)
    if not spans:
        return Strip([Segment(plain, Style(meta={"offset": (0, offset_y)}))])
    spans.sort(key=lambda s: s.start)
    segments: list[Segment] = []
    pos = 0
    x = 0
    for span in spans:
        if span.start > pos:
            text = plain[pos:span.start]
            segments.append(Segment(text, Style(meta={"offset": (x, offset_y)})))
            x += len(text)
        style_raw = span.style
        if isinstance(style_raw, str):
            seg_style = Style.parse(style_raw)
        elif style_raw is not None:
            seg_style = style_raw.rich_style
        else:
            seg_style = None
        text = plain[span.start:span.end]
        offset_style = Style(meta={"offset": (x, offset_y)})
        segments.append(
            Segment(
                text,
                seg_style + offset_style
                if seg_style is not None
                else offset_style,
            )
        )
        x += len(text)
        pos = span.end
    if pos < len(plain):
        text = plain[pos:]
        segments.append(Segment(text, Style(meta={"offset": (x, offset_y)})))
    return Strip(segments)
