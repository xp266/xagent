from __future__ import annotations

import bisect
import time

from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.text import Text as RichText

from textual.content import Content, Span
from textual.strip import Strip
from textual.widgets import Static

from src.ui.tui.lazy import _apply_selection, _build_strip, _line_fill, _wrap_continuation, clip_selection_start, selection_slice


def _qwidth(width: int) -> int:
    return (width // 4) * 4

_RESIZE_SETTLE = 0.25


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
        self._content_lines: list[Content] = []
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
        self._built_content_range = (0, 0)
        self._content_range = (0, 0)

    @property
    def title_line(self) -> Content | None:
        if not self.title:
            return None
        return Content(self.title, spans=[Span(0, len(self.title), self.title_style)])

    def _content_sig(self) -> tuple:
        content_id = id(self.content) if self.content is not None else id(self._content_lines)
        return (
            content_id,
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
                owner._invalidate_render()
                owner.refresh()

    def set_marker(self, marker: str | None) -> None:
        if marker != self.marker:
            self.marker = marker
            self._strips = []
            self._fill_at = []
            self._key = ()
            owner = self.owner
            if owner is not None and owner.is_mounted:
                owner._invalidate_render()
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
        lines = content.split("\n", allow_blank=True)
        while lines and not lines[-1].plain:
            lines.pop()
        self._content_lines = lines
        self._strips = []
        self._key = ()
        self._bump()

    def update_lines(self, lines: list[Content]) -> None:
        self.content = None
        while lines and not lines[-1].plain:
            lines.pop()
        self._content_lines = lines
        self._strips = []
        self._key = ()
        self._bump()
    def _bump(self) -> None:
        owner = self.owner
        if owner is None or not owner.is_mounted:
            return
        if owner._bulk:
            return
        owner._rebuild_offsets()
        owner.refresh(layout=True)

    def _build(self, width: int) -> list[Content]:
        bg = f"on {self.bg}" if self.bg else ""
        inner_width = max(0, width - self.pad_left - self.pad_right)
        if self._built_width != width:
            self._built_width = width
            self._built_content_raw = []
            self._built_spans = []
            self._built_content = []

        content_lines: list[Content] = []
        keep = 0
        if not self.collapsed and self._content_lines:
            content_lines = self._content_lines
            cached = self._built_content_raw
            for a, b in zip(cached, content_lines):
                if a.plain != b.plain or a.spans != b.spans:
                    break
                keep += 1

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
            new_content.extend(wrapped)
        if keep < len(self._built_spans):
            dropped = sum(self._built_spans[keep:])
            del self._built_spans[keep:]
            del self._built_content[len(self._built_content) - dropped:]
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
            if not self.collapsed and (self.content is not None or self._content_lines):
                lines.append(Content(f"{' ' * width}", spans=[Span(0, width, bg)]))
        if inner_width > 0 and rendered_content:
            content_start = len(lines)
            lines.extend(rendered_content)
            self._built_content_range = (content_start, content_start + len(rendered_content))
        else:
            self._built_content_range = (len(lines), len(lines))
        if self.pad_bottom > 0:
            lines.extend(
                Content(f"{' ' * width}", spans=[Span(0, width, bg)]) for _ in range(self.pad_bottom)
            )
        return lines

    def _rebuild(self, width: int) -> None:
        width = _qwidth(width)
        key = (width, self._content_sig())
        if key == self._key:
            return
        raw = self._build(width)
        self._content_range = self._built_content_range
        if width > 0:
            self._fill_at = [_line_fill(line)[0] for line in raw]
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
        strip = self._strips[y]
        if strip is None:
            line = self._lines[y]
            oy = self.offset + y
            strip = _build_strip(line, oy)
            cs, ce = self._content_range
            is_content = cs <= y < ce
            cbg = f"on {self.content_bg}" if self.content_bg else (f"on {self.bg}" if self.bg else "")
            cbg_style = RichStyle.parse(cbg) if (cbg and is_content) else None
            pl = self.content_pad_left if is_content else 0
            segments = []
            if pl > 0:
                segments.append(Segment(" " * pl, cbg_style + RichStyle(meta={"offset": (0, oy)}) if cbg_style is not None else RichStyle(meta={"offset": (0, oy)})))
            for seg in strip:
                if not seg.text:
                    continue
                seg_style = seg.style
                if seg_style is not None and seg_style.meta is not None:
                    meta = seg_style.meta
                    if "offset" in meta:
                        ox, _ = meta["offset"]
                        seg_style = seg_style + RichStyle(meta={"offset": (ox + pl, oy)})
                if cbg_style is not None and (seg_style is None or seg_style.bgcolor is None):
                    seg_style = seg_style + cbg_style if seg_style is not None else cbg_style
                segments.append(Segment(seg.text, seg_style, control=seg.control))
            strip = Strip(segments)
            fill_at, fill_bg = _line_fill(line)
            if fill_bg is None and cbg_style is not None:
                fill_bg = self.content_bg or self.bg
            if fill_bg is not None:
                pad = width - strip.cell_length
                if pad > 0:
                    fill_style = RichStyle.parse(f"on {fill_bg}") + RichStyle(meta={"offset": (pl + line.cell_length, oy)})
                    segments = list(strip)
                    segments.append(Segment(" " * pad, fill_style))
                    strip = Strip(segments)
            if self.body_style:
                style = RichStyle.parse(self.body_style)
                segments = []
                for seg in strip:
                    if seg.style is None or seg.style.color is None:
                        new_style = seg.style + style if seg.style is not None else style
                        segments.append(Segment(seg.text, new_style, control=seg.control))
                    else:
                        segments.append(seg)
                strip = Strip(segments)
            self._strips[y] = strip
        return strip


class ChatCanvas(Static):

    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._blocks: list[CanvasBlock] = []
        self._offsets: list[int] = []
        self._bulk = 0
        self._built_width = 0
        self._pending_width: int | None = None
        self._last_width_change = 0.0
        self._render_gen = 0
        self._strip_cache: dict = {}
        self._settle_width: int | None = None
        self._settle_blocks: list | None = None
        self._settle_idx = 0

    def _begin_bulk(self) -> None:
        self._bulk += 1

    def _end_bulk(self) -> None:
        self._bulk = max(0, self._bulk - 1)
        if self._bulk == 0:
            size = self.size
            width = _qwidth(size.width) if size is not None and size.width > 0 else 0
            if width > 0:
                self._rebuild_offsets(width)
            self.refresh(layout=True)

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

    def _invalidate_render(self) -> None:
        self._render_gen += 1
        self._strip_cache.clear()

    def _rebuild_offsets(self, width: int | None = None) -> None:
        if width is None:
            size = self.size
            width = _qwidth(size.width) if size is not None and size.width > 0 else 0
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
        self._built_width = width
        self._pending_width = None
        self._invalidate_render()

    def _total_lines(self) -> int:
        total = 0
        for block in self._blocks:
            total += len(block._lines)
        return total

    _SETTLE_BUDGET = 0.008

    def _settle_resize(self) -> None:
        now = time.monotonic()
        if self._pending_width is not None:
            if now - self._last_width_change < _RESIZE_SETTLE:
                return
            self._settle_width = self._pending_width
            self._settle_blocks = list(self._blocks)
            self._settle_idx = 0
            self._pending_width = None
        if self._settle_width is None:
            return
        t0 = time.perf_counter()
        blocks = self._settle_blocks
        idx = self._settle_idx
        width = self._settle_width
        while idx < len(blocks) and time.perf_counter() - t0 < self._SETTLE_BUDGET:
            blocks[idx]._build(width)
            idx += 1
        self._settle_idx = idx
        if idx >= len(blocks):
            for block in blocks:
                block._rebuild(width)
            self._rebuild_offsets(width)
            self.refresh(layout=True)
            self._settle_width = None
            self._settle_blocks = None
            self._settle_idx = 0

    def clear(self) -> None:
        self._blocks = []
        self._offsets = []
        self._invalidate_render()
        self.refresh(layout=True)

    def append(self, block: CanvasBlock) -> CanvasBlock:
        block.owner = self
        self._blocks.append(block)
        if not self._bulk:
            self._rebuild_offsets()
            self.refresh(layout=True)
        return block

    def remove(self, block: CanvasBlock) -> None:
        if block in self._blocks:
            block.owner = None
            self._blocks.remove(block)
            if not self._bulk:
                self._rebuild_offsets()
                self.refresh(layout=True)

    def _block_at(self, y: int) -> tuple[CanvasBlock | None, int]:
        idx = bisect.bisect_right(self._offsets, y) - 1
        if idx < 0 or idx >= len(self._blocks):
            return None, y
        block = self._blocks[idx]
        return block, y - self._offsets[idx]

    def get_content_height(self, container, viewport, width) -> int:
        width = _qwidth(width)
        now = time.monotonic()
        if width != self._built_width:
            self._pending_width = width
            self._last_width_change = now
        elif self._pending_width is not None:
            self._pending_width = None
        return self._total_lines()

    def render_line(self, y: int) -> Strip:
        width = self._built_width
        if width <= 0:
            size = self.size
            width = _qwidth(size.width) if size is not None and size.width > 0 else 80
        selection = self.text_selection
        cached = selection is None or selection.start is None
        if cached:
            key = (self._render_gen, y, width)
            strip = self._strip_cache.get(key)
            if strip is not None:
                return strip
        block, by = self._block_at(y)
        if block is None:
            strip = Strip.blank(width)
        else:
            strip = block.render_line(by, width)
        strip = strip.apply_style(self.visual_style.rich_style)
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
        if cached:
            if len(self._strip_cache) >= 1024:
                self._strip_cache.clear()
            self._strip_cache[key] = strip
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
