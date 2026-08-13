from __future__ import annotations

from rich.segment import Segment
from rich.style import Style

from textual.content import Content, Span
from textual.strip import Strip

_LINE_NO_RGB = (133, 133, 133)


def _parse_rich_style(style):
    if isinstance(style, str):
        return Style.parse(style)
    return getattr(style, "rich_style", style)


def _line_fill(line: Content) -> tuple[int | None, str | None]:
    spans = line.spans
    if not spans:
        return None, None
    last = spans[-1]
    if last.end != len(line.plain):
        return None, None
    style = _parse_rich_style(last.style)
    if style is None or style.bgcolor is None:
        return None, None
    fill_at = last.end if line.plain[last.start:].strip(" ") else last.start
    r, g, b = style.bgcolor.get_truecolor(None)
    return fill_at, f"#{r:02x}{g:02x}{b:02x}"


def _pad_line(line: Content, width: int) -> tuple[Content, int | None]:
    fill_at, bg = _line_fill(line)
    if bg is None:
        return line, None
    pad = width - line.cell_length
    if pad > 0:
        line = line.append_text(" " * pad, f"on {bg}")
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
    no_w = _line_no_end(line, pl) + _diff_marker_end(line, pl)
    s = x0 - pl
    if s < no_w:
        s = no_w
    e = x1 - pl if x1 >= 0 else -1
    if x1 >= 0 and x1 - pl <= no_w:
        return ""
    if e < 0 or (fill_at is not None and e > fill_at):
        e = fill_at if fill_at is not None else len(plain)
    if e > len(plain):
        e = len(plain)
    if fill_at is not None and s >= fill_at:
        return ""
    return plain[s:e] if s < e else ""


def clip_selection_start(line: Content, start: int, pl: int = 0) -> int:
    no_w = _line_no_end(line, pl) + _diff_marker_end(line, pl)
    if start < pl + no_w:
        return pl + no_w
    return start


def _build_segments(line: Content) -> list[Segment]:
    plain = line.plain
    spans = list(line.spans)
    if not spans:
        return [Segment(plain)]
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
    prev_key = (-1, 2)
    sorted_ok = True
    for e in events:
        key = (e[0], -e[1])
        if key < prev_key:
            sorted_ok = False
            break
        prev_key = key
    if not sorted_ok:
        events.sort(key=lambda e: (e[0], -e[1]))
    segments: list[Segment] = []
    active: list[Style] = []
    pos = 0
    for boundary, delta, seg_style in events:
        if boundary > pos:
            if active:
                seg = Style.combine(active)
            else:
                seg = None
            segments.append(Segment(plain[pos:boundary], seg))
            pos = boundary
        if delta > 0:
            active.append(seg_style)
        elif seg_style in active:
            active.remove(seg_style)
    if pos < len(plain):
        if active:
            seg = Style.combine(active)
        else:
            seg = None
        segments.append(Segment(plain[pos:], seg))
    return segments


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
