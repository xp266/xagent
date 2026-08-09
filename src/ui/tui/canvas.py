from __future__ import annotations

import bisect

from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.text import Text as RichText

from textual.content import Content, Span
from textual.strip import Strip
from textual.widgets import Static

from src.ui.tui.lazy import _apply_selection, _build_strip, _line_bg, _pad_line, _wrap_continuation, clip_selection_start, selection_slice


class CanvasBlock:

    def __init__(
        self,
        kind: str = "body",
        *,
        collapsed: bool = False,
        title: str = "",
        title_style: str = "",
        body_style: str = "",
        bg: str | None = None,
        content_bg: str | None = None,
        pad_top: int = 0,
        pad_bottom: int = 0,
        pad_left: int = 1,
        pad_right: int = 1,
        content_pad_left: int | None = None,
        expandable: bool = False,
        hide_arrow: bool = False,
    ) -> None:
        self.kind = kind
        self.collapsed = collapsed
        self.expandable = expandable
        self.hide_arrow = hide_arrow
        self.title = title
        self.label = title
        self.title_style = title_style
        self.body_style = body_style
        self.bg = bg
        self.content_bg = content_bg
        self.pad_top = pad_top
        self.pad_bottom = pad_bottom
        self.pad_left = pad_left
        self.pad_right = pad_right
        self.content_pad_left = pad_left if content_pad_left is None else content_pad_left
        self.arrow_hidden: bool = False
        self.marker: str | None = None
        self.content: Content | None = None
        self._lines: list[Content] = []
        self._strips: list[Strip | None] = []
        self._fill_at: list[int | None] = []
        self._key: tuple = ()
        self.offset = 0
        self.owner: ChatCanvas | None = None
        self._built_width: int = 0
        self._built_content_raw: list[Content] = []
        self._built_spans: list[int] = []
        self._built_content: list[Content] = []
        self._built_padded: list[Content] = []
        self._built_plain: str = ""
        self._built_content_start = 0
        self._built_reuse = 0

    @property
    def title_line(self) -> Content | None:
        if not self.title:
            return None
        return Content(self.title, spans=[Span(0, len(self.title), self.title_style)])

    def _content_sig(self) -> tuple:
        return (
            id(self.content),
            self.title,
            self.title_style,
            self.marker,
            self.collapsed,
            self.pad_top,
            self.pad_bottom,
            self.pad_left,
            self.content_pad_left,
            self.pad_right,
            self.bg,
            self.content_bg,
            self.body_style,
        )

    def set_title(self, title: str) -> None:
        if title != self.title:
            self.title = title
            self._strips = []
            self._fill_at = []
            self._key = ()
            owner = self.owner
            if owner is not None and owner.is_mounted:
                owner.refresh()

    def set_marker(self, marker: str | None) -> None:
        if marker != self.marker:
            self.marker = marker
            self._strips = []
            self._fill_at = []
            self._key = ()
            owner = self.owner
            if owner is not None and owner.is_mounted:
                owner.refresh()

    def update(self, content: Content | str | RichText) -> None:
        if isinstance(content, str):
            content = Content(content)
        elif not isinstance(content, Content):
            content = Content(
                content.plain,
                spans=[Span(s.start, s.end, s.style) for s in content.spans],
            )
        self.content = content
        self._strips = []
        self._key = ()
        self._bump()
    def _bump(self) -> None:
        owner = self.owner
        if owner is not None and owner.is_mounted:
            owner._rebuild_offsets()
            owner.refresh(layout=True)

    def _build(self, width: int) -> list[Content]:
        bg = f"on {self.bg}" if self.bg else ""
        cbg = f"on {self.content_bg}" if self.content_bg else bg
        inner_width = max(0, width - self.pad_left - self.pad_right)
        if self._built_width != width:
            self._built_width = width
            self._built_content_raw = []
            self._built_spans = []
            self._built_content = []
            self._built_padded = []
            self._built_plain = ""

        content_lines: list[Content] = []
        incremental = False
        keep = 0
        tail_start = 0
        if not self.collapsed and self.content is not None:
            plain = self.content.plain
            prev_plain = self._built_plain if self._built_width == width else ""
            cached = self._built_content_raw
            if cached:
                stable_end = len(prev_plain.rstrip("\n")) - len(cached[-1].plain)
            else:
                stable_end = -1
            if (
                len(plain) > len(prev_plain)
                and stable_end >= 0
                and plain[:stable_end] == prev_plain[:stable_end]
            ):
                incremental = True
                keep = len(cached) - 1
                content_lines = cached
                if keep < len(content_lines):
                    del content_lines[keep:]
                tail_start = stable_end
                tail_plain = plain[tail_start:]
                tail_spans = []
                spans = self.content.spans
                if spans:
                    start = bisect.bisect_right(spans, tail_start, key=lambda s: s.start)
                    for s in spans[max(0, start - 1):]:
                        if s.end <= tail_start:
                            continue
                        head = s.start if s.start >= tail_start else tail_start
                        tail_spans.append(Span(head - tail_start, s.end - tail_start, s.style))
                tail_lines = Content(tail_plain, spans=tail_spans).split("\n", allow_blank=True)
                while tail_lines and not tail_lines[-1].plain:
                    tail_lines.pop()
                content_lines.extend(tail_lines)
            else:
                content_lines = self.content.split("\n", allow_blank=True)
                while content_lines and not content_lines[-1].plain:
                    content_lines.pop()
                for a, b in zip(self._built_content_raw, content_lines):
                    if a.plain != b.plain or a.spans != b.spans:
                        break
                    keep += 1
            self._built_plain = plain

        new_content: list[Content] = []
        new_spans: list[int] = []
        for line in content_lines[keep:]:
            if inner_width > 0:
                if line.cell_length > inner_width:
                    wrapped = _wrap_continuation(line, inner_width)
                else:
                    wrapped = [line]
            else:
                wrapped = [line]
            new_spans.append(len(wrapped))
            for nline in wrapped:
                if cbg:
                    bg_end = _line_bg(nline)[1]
                    if bg_end < len(nline.plain):
                        nline = Content(
                            nline.plain,
                            [*nline.spans, Span(bg_end, len(nline.plain), cbg)],
                        )
                new_content.append(nline)
        if incremental:
            if keep < len(self._built_spans):
                dropped = sum(self._built_spans[keep:])
                del self._built_spans[keep:]
                del self._built_content[len(self._built_content) - dropped:]
                if len(self._built_padded) >= dropped:
                    del self._built_padded[len(self._built_padded) - dropped:]
            reuse = len(self._built_content)
            self._built_content.extend(new_content)
            self._built_spans.extend(new_spans)
        else:
            reuse = sum(self._built_spans[:keep])
            self._built_content = self._built_content[:reuse] + new_content
            self._built_spans = self._built_spans[:keep] + new_spans
        self._built_content_raw = content_lines
        rendered_content = self._built_content

        lines: list[Content] = []
        if self.pad_top > 0:
            lines.extend(
                Content(f"{' ' * width}", spans=[Span(0, width, bg)]) for _ in range(self.pad_top)
            )
        title_line = self.title_line
        if title_line is not None:
            if self.expandable:
                marker = self.marker
                if marker is None and not self.arrow_hidden and not self.hide_arrow:
                    marker = "↓" if not self.collapsed else "-"
                if marker is None:
                    title_line = Content.assemble("   ", title_line)
                else:
                    marker_c = Content(marker, spans=[Span(0, 1, self.title_style)])
                    title_line = Content.assemble(marker_c, "  ", title_line)
            else:
                title_line = Content.assemble(" " * self.pad_left, title_line)
            if self.bg:
                title_line = Content(
                    title_line.plain,
                    [*title_line.spans, Span(0, len(title_line.plain), bg)],
                )
            lines.append(title_line)
            if not self.collapsed and self.content is not None:
                lines.append(Content(f"{' ' * width}", spans=[Span(0, width, bg)]))
        self._built_content_start = len(lines)
        if inner_width > 0 and rendered_content:
            if cbg:
                left_pad = Content(" " * self.content_pad_left, spans=[Span(0, self.content_pad_left, cbg)])
            else:
                left_pad = Content(" " * self.content_pad_left)
            if reuse and len(self._built_padded) >= reuse:
                if len(self._built_padded) > reuse:
                    del self._built_padded[reuse:]
                self._built_padded.extend(left_pad + line for line in new_content)
            else:
                self._built_padded = [left_pad + line for line in rendered_content]
            lines.extend(self._built_padded)
        self._built_reuse = reuse
        if self.pad_bottom > 0:
            lines.extend(
                Content(f"{' ' * width}", spans=[Span(0, width, bg)]) for _ in range(self.pad_bottom)
            )
        return lines

    def _rebuild(self, width: int) -> None:
        key = (width, self._content_sig())
        if key == self._key:
            return
        raw = self._build(width)
        if width > 0:
            cs = self._built_content_start
            reuse = self._built_reuse
            if reuse and len(self._lines) >= cs + reuse and len(self._fill_at) >= cs + reuse:
                padded = []
                fills = []
                for line in raw[:cs]:
                    pl, fa = _pad_line(line, width)
                    padded.append(pl)
                    fills.append(fa)
                padded.extend(self._lines[cs:cs + reuse])
                fills.extend(self._fill_at[cs:cs + reuse])
                for line in raw[cs + reuse:]:
                    pl, fa = _pad_line(line, width)
                    padded.append(pl)
                    fills.append(fa)
                raw = padded
                self._fill_at = fills
            else:
                padded: list[Content] = []
                fills: list[int | None] = []
                for line in raw:
                    pl, fa = _pad_line(line, width)
                    padded.append(pl)
                    fills.append(fa)
                raw = padded
                self._fill_at = fills
        else:
            self._fill_at = [None] * len(raw)
        self._lines = raw
        self._strips = [None] * len(raw)
        self._key = key

    def height(self, width: int) -> int:
        self._rebuild(width)
        return len(self._lines)

    def render_line(self, y: int, width: int) -> Strip:
        self._rebuild(width)
        if y >= len(self._strips):
            return Strip.blank(width)
        if self._strips[y] is None:
            line = self._lines[y]
            self._strips[y] = _build_strip(line, self.offset + y)
        strip = self._strips[y]
        if self.body_style:
            style = RichStyle.parse(self.body_style)
            segments = []
            for seg in strip:
                if seg.style is None or seg.style.color is None:
                    new_style = seg.style + style if seg.style is not None else style
                    segments.append(Segment(seg.text, new_style, control=seg.control))
                else:
                    segments.append(seg)
            strip = Strip(segments, strip.cell_length)
        return strip


class ChatCanvas(Static):

    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._blocks: list[CanvasBlock] = []
        self._offsets: list[int] = []

    def get_selection(self, selection) -> tuple[str, str] | None:
        if not self._blocks:
            return None
        start = selection.start
        end = selection.end
        if start is None and end is None:
            return None
        if start is not None:
            y0, x0 = start.y, start.x
        else:
            y0, x0 = 0, 0
        if end is not None:
            y1, x1 = end.y, end.x
        else:
            y1 = self.get_content_height(None, None, self.size.width) - 1
            x1 = -1
        if (y0, x0) > (y1, x1):
            y0, x0, y1, x1 = y1, x1, y0, x0
        max_y = sum(len(b._lines) for b in self._blocks) - 1
        y0, y1 = max(0, y0), min(max(0, y1), max_y)
        parts: list[str] = []
        for y in range(y0, y1 + 1):
            block, by = self._block_at(y)
            if block is None or by >= len(block._lines):
                parts.append("")
                continue
            line = block._lines[by]
            pl = 0 if block.title_line is not None and by == block.pad_top else block.content_pad_left
            s = x0 if y == y0 else 0
            e = x1 if y == y1 else -1
            fill_at = block._fill_at[by] if by < len(block._fill_at) else None
            parts.append(selection_slice(line, fill_at, s, e, pl))
        return "\n".join(parts), "\n"

    def selection_updated(self, selection) -> None:
        self.refresh()

    def _rebuild_offsets(self, width: int | None = None) -> None:
        if width is None:
            width = self.size.width if self.size else 0
        offsets: list[int] = []
        total = 0
        for block in self._blocks:
            block.height(width)
            if block.offset != total:
                block.offset = total
                block._strips = [None] * len(block._lines)
            offsets.append(total)
            total += len(block._lines)
        self._offsets = offsets

    def clear(self) -> None:
        self._blocks = []
        self._offsets = []
        self.refresh(layout=True)

    def append(self, block: CanvasBlock) -> CanvasBlock:
        block.owner = self
        self._blocks.append(block)
        self._rebuild_offsets()
        self.refresh(layout=True)
        return block

    def remove(self, block: CanvasBlock) -> None:
        if block in self._blocks:
            block.owner = None
            self._blocks.remove(block)
            self._rebuild_offsets()
            self.refresh(layout=True)

    def _block_at(self, y: int) -> tuple[CanvasBlock | None, int]:
        idx = bisect.bisect_right(self._offsets, y) - 1
        if idx < 0 or idx >= len(self._blocks):
            return None, y
        block = self._blocks[idx]
        return block, y - self._offsets[idx]

    def get_content_height(self, container, viewport, width) -> int:
        self._rebuild_offsets(width)
        total = sum(len(b._lines) for b in self._blocks)
        return total

    def render_line(self, y: int) -> Strip:
        width = self.size.width if self.size else 80
        block, by = self._block_at(y)
        if block is None:
            strip = Strip.blank(width)
        else:
            strip = block.render_line(by, width)
        strip = strip.apply_style(self.visual_style.rich_style)
        selection = self.text_selection
        if selection is not None and selection.start is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = strip.cell_length
                if start < end:
                    if block is not None and by < len(block._lines):
                        line = block._lines[by]
                        pl = 0 if block.title_line is not None and by == block.pad_top else block.content_pad_left
                        start = clip_selection_start(line, start, pl)
                    if start < end:
                        style = self.screen.get_component_styles("screen--selection")
                        strip = _apply_selection(strip, start, end, style)
        return strip

    def on_click(self, event) -> None:
        if not self._blocks:
            return
        if self.text_selection is not None:
            return
        try:
            widget, offset = self.screen.get_widget_and_offset_at(
                event.screen_x, event.screen_y
            )
        except Exception:
            return
        if widget is not self or offset is None:
            return
        block, by = self._block_at(offset.y)
        if block is None or not block.expandable:
            return
        if by != block.pad_top:
            return
        block.collapsed = not block.collapsed
        block._strips = []
        block._key = ()
        self._rebuild_offsets()
        self.refresh(layout=True)
