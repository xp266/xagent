from __future__ import annotations

from rich.segment import Segment
from rich.style import Style

from textual.content import Content, Span
from textual.strip import Strip
from textual.style import Style as TextualStyle
from textual.widgets import Static

_LINE_NO_RGB = (133, 133, 133)


def _parse_rich_style(style):
    if isinstance(style, str):
        return Style.parse(style)
    return getattr(style, "rich_style", style)


def _pad_line(line: Content, width: int) -> tuple[Content, int | None]:
    spans = list(line.spans)
    if not spans:
        return line, None
    last = spans[-1]
    if last.end != len(line.plain):
        return line, None
    style = _parse_rich_style(last.style)
    if style is None or style.bgcolor is None:
        return line, None
    if line.plain[last.start:].strip(" "):
        fill_at = last.end
    else:
        fill_at = last.start
    pad = width - line.cell_length
    if pad > 0:
        r, g, b = style.bgcolor.get_truecolor(None)
        line = line.append_text(" " * pad, f"on #{r:02x}{g:02x}{b:02x}")
    return line, fill_at


def _line_no_end(line: Content, offset: int = 0) -> int:
    spans = list(line.spans)
    if not spans:
        return 0
    first = spans[0]
    if first.start < offset:
        for s in spans:
            if s.start >= offset:
                first = s
                break
        else:
            return 0
    if first.start != offset:
        return 0
    style = _parse_rich_style(first.style)
    if style is None or style.color is None:
        return 0
    try:
        rgb = style.color.get_truecolor(None)
    except Exception:
        return 0
    if rgb is None or tuple(rgb) != _LINE_NO_RGB:
        return 0
    return min(first.end - offset, len(line.plain) - offset)


def _diff_marker_end(line: Content, offset: int = 0) -> int:
    no_w = _line_no_end(line, offset)
    if no_w <= 0:
        return 0
    start = offset + no_w
    for s in line.spans:
        if s.start < start:
            continue
        if s.start > start:
            break
        style = _parse_rich_style(s.style)
        if style is None:
            continue
        if line.plain[s.start:s.end] in ("- ", "+ "):
            return s.end - s.start
    return 0


def _line_bg(line: Content) -> tuple[str | None, int]:
    color = None
    end = 0
    for s in line.spans:
        style = _parse_rich_style(s.style)
        if style is not None and style.bgcolor is not None:
            r, g, b = style.bgcolor.get_truecolor(None)
            color = f"#{r:02x}{g:02x}{b:02x}"
            if s.end > end:
                end = s.end
    return color, end


def _wrap_continuation(line: Content, width: int) -> list[Content]:
    if line.cell_length <= width:
        return [line]
    indent = _line_no_end(line) + _diff_marker_end(line)
    wrapped = line.wrap(width)
    if indent <= 0 or len(wrapped) <= 1:
        return wrapped
    avail = width - indent
    if avail < 1:
        return wrapped
    bg, _ = _line_bg(wrapped[0])
    head = (
        Content(" " * indent, spans=[Span(0, indent, f"on {bg}")])
        if bg
        else Content(" " * indent)
    )
    out = [wrapped[0]]
    for piece in wrapped[1:]:
        if piece.cell_length > avail:
            piece_lines = piece.wrap(avail)
        else:
            piece_lines = [piece]
        for sub in piece_lines:
            out.append(head + sub)
    return out


def selection_slice(line: Content, fill_at: int | None, x0: int, x1: int, pl: int) -> str:
    plain = line.plain
    s = x0
    e = x1 if x1 >= 0 else -1
    no_w = _line_no_end(line, pl) + _diff_marker_end(line, pl)
    if s < pl + no_w:
        s = pl + no_w
    if e < 0 or (fill_at is not None and e > fill_at):
        e = fill_at if fill_at is not None else len(plain)
    if fill_at is not None and s >= fill_at:
        s = pl + no_w
    return plain[s:e] if s < e else ""


def clip_selection_start(line: Content, start: int, pl: int = 0) -> int:
    no_w = _line_no_end(line, pl) + _diff_marker_end(line, pl)
    if start < pl + no_w:
        return pl + no_w
    return start


def _apply_highlight(line: Content, style, start: int, end: int) -> Content:
    spans: list = []
    for s in line.spans:
        if s.end <= start or s.start >= end:
            spans.append(s)
            continue
        if s.start < start:
            spans.append(Span(s.start, start, s.style))
        if s.end > end:
            spans.append(Span(end, s.end, s.style))
    spans.append(Span(start, end, style))
    return Content(line.plain, spans)


class LazyText(Static):
    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._lines: list[Content] = []
        self._strips: list[Strip | None] = []
        self._strips_key: tuple[int, int] = (-1, 0)
        self._fill_at: list[int | None] = []

    def _content_sig(self) -> int:
        return id(self.visual)

    def _rebuild(self, width: int) -> None:
        visual = self.visual
        if isinstance(visual, Content):
            lines = visual.split("\n", allow_blank=True)
            self._lines = []
            for line in lines:
                if width > 0 and line.cell_length > width:
                    self._lines.extend(_wrap_continuation(line, width))
                else:
                    self._lines.append(line)
        else:
            self._lines = []
        while self._lines and self._lines[-1].plain == "":
            self._lines.pop()
        if width > 0:
            padded: list[Content] = []
            fills: list[int | None] = []
            for line in self._lines:
                pl, fa = _pad_line(line, width)
                padded.append(pl)
                fills.append(fa)
            self._lines = padded
            self._fill_at = fills
        else:
            self._fill_at = [None] * len(self._lines)
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
                    start = clip_selection_start(line, start)
                    if start < end:
                        selection_style = TextualStyle.from_styles(
                            self.screen.get_component_styles("screen--selection")
                        )
                        line = _apply_highlight(line, selection_style, start, end)
            self._strips[y] = _build_strip(line, y)
        return self._strips[y].apply_style(self.visual_style.rich_style)

    def selection_updated(self, selection) -> None:
        self._strips = [None] * len(self._lines)
        self.refresh()

    def get_selection(self, selection):
        if not self._lines:
            return super().get_selection(selection)
        start = selection.start
        end = selection.end
        if start is None and end is None:
            y0, x0, y1, x1 = 0, 0, len(self._lines) - 1, -1
        elif start is None or end is None:
            return "", "\n"
        else:
            if (start.y, start.x) > (end.y, end.x):
                start, end = end, start
            y0, x0 = start.y, start.x
            y1, x1 = end.y, end.x
        out: list[str] = []
        for y in range(y0, y1 + 1):
            line = self._lines[y]
            s = x0 if y == y0 else 0
            e = x1 if y == y1 and x1 >= 0 else -1
            fill_at = self._fill_at[y] if y < len(self._fill_at) else None
            out.append(selection_slice(line, fill_at, s, e, 0))
        return "\n".join(out), "\n"

    def update(self, content="", layout=True):
        self._lines = []
        self._strips = []
        self._strips_key = (-1, 0)
        self._fill_at = []
        super().update(content, layout=layout)


def _build_strip(line: Content, offset_y: int) -> Strip:
    plain = line.plain
    spans = list(line.spans)
    if not spans:
        return Strip([Segment(plain, Style(meta={"offset": (0, offset_y)}))])
    events: list[tuple[int, int, Style]] = []
    for span in spans:
        start, end = span.start, span.end
        if start < 0 or end > len(plain) or start >= end:
            continue
        style_raw = span.style
        if isinstance(style_raw, str):
            seg_style = Style.parse(style_raw)
        elif style_raw is not None:
            seg_style = style_raw.rich_style
        else:
            continue
        events.append((start, 1, seg_style))
        events.append((end, -1, seg_style))
    events.sort(key=lambda e: (e[0], -e[1]))
    segments: list[Segment] = []
    active: list[Style] = []
    pos = 0
    x = 0
    for boundary, delta, seg_style in events:
        if boundary > pos:
            if active:
                seg = Style.combine(active)
            else:
                seg = None
            if seg is not None:
                segments.append(Segment(plain[pos:boundary], seg + Style(meta={"offset": (x, offset_y)})))
            else:
                segments.append(Segment(plain[pos:boundary], Style(meta={"offset": (x, offset_y)})))
            x += boundary - pos
            pos = boundary
        if delta > 0:
            active.append(seg_style)
        elif seg_style in active:
            active.remove(seg_style)
    if pos < len(plain):
        if active:
            seg = Style.combine(active) + Style(meta={"offset": (x, offset_y)})
        else:
            seg = Style(meta={"offset": (x, offset_y)})
        segments.append(Segment(plain[pos:], seg))
    return Strip(segments)


def _apply_selection(strip: Strip, start: int, end: int, style) -> Strip:
    from textual.style import Style as TextualStyle

    if isinstance(style, TextualStyle):
        rich_style = style.rich_style
    elif hasattr(style, "text_style"):
        rich_style = TextualStyle.from_styles(style).rich_style
    else:
        rich_style = style

    def _meta_x(seg_style, base_x: int) -> Style | None:
        if seg_style is None or seg_style.meta is None:
            return None
        meta = seg_style.meta
        if "offset" not in meta:
            return None
        _ox, oy = meta["offset"]
        return Style(meta={"offset": (base_x, oy)})

    segments: list[Segment] = []
    x = 0
    for seg in strip:
        cell_length = seg.cell_length
        seg_end = x + cell_length
        text, seg_style, _ = seg
        if seg_end <= start or x >= end or cell_length == 0:
            segments.append(seg)
            x = seg_end
            continue
        base = seg_style if seg_style is not None else Style()
        cut_a = max(start - x, 0)
        cut_b = min(end - x, cell_length)
        before = text[:cut_a]
        mid = text[cut_a:cut_b]
        after = text[cut_b:]
        if before:
            mx = _meta_x(seg_style, x)
            segments.append(
                Segment(before, seg_style + mx if mx is not None else seg_style)
            )
        if mid:
            mx = _meta_x(seg_style, x + cut_a)
            segments.append(Segment(mid, base + rich_style + (mx if mx is not None else Style())))
        if after:
            mx = _meta_x(seg_style, x + cut_b)
            segments.append(
                Segment(after, seg_style + mx if mx is not None else seg_style)
            )
        x = seg_end
    return Strip(segments)
