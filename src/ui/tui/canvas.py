from __future__ import annotations

import bisect

from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.text import Text as RichText

from textual.content import Content, Span
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Static

from src.ui.tui.lazy import _apply_selection, _build_strip, _line_no_end, _pad_line

_USER_BG = "#1A1A1A"
_TOOL_BG = "#1A1A1A"
_THINKING_TITLE = "#5B9BD5"
_TOOL_TITLE = "#808080"
_TOOL_ERROR = "#A75252"
_TOOL_BASH_TITLE = "#888888"
_TOOL_HEADER = "#808080"
_THINKING_BODY = "#9B9B9B"


class CanvasBlock:
    """A single message block rendered into the chat canvas.

    Renders a title line (optional, used when collapsed or for tool headers)
    plus a body. Line content is cached per width; only the block that
    changes is rebuilt.
    """

    def __init__(
        self,
        kind: str = "body",
        *,
        collapsed: bool = False,
        title: str = "",
        title_style: str = "",
        body_style: str = "",
        bg: str | None = None,
        pad_top: int = 0,
        pad_bottom: int = 0,
        pad_left: int = 1,
        pad_right: int = 1,
        expandable: bool = False,
    ) -> None:
        self.kind = kind
        self.collapsed = collapsed
        self.expandable = expandable
        self.title = title
        self.label = title
        self.title_style = title_style
        self.body_style = body_style
        self.bg = bg
        self.pad_top = pad_top
        self.pad_bottom = pad_bottom
        self.pad_left = pad_left
        self.pad_right = pad_right
        self.content: Content | None = None
        self._lines: list[Content] = []
        self._strips: list[Strip | None] = []
        self._fill_at: list[int | None] = []
        self._key: tuple = ()
        self.offset = 0
        self.owner: ChatCanvas | None = None

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
            self.collapsed,
            self.pad_top,
            self.pad_bottom,
            self.pad_left,
            self.pad_right,
            self.bg,
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

    def set_label(self, label: str) -> None:
        self.label = label

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
        self._fill_at = []
        self._key = ()
        self._bump()

    def _bump(self) -> None:
        owner = self.owner
        if owner is not None and owner.is_mounted:
            owner._rebuild_offsets()
            owner.refresh(layout=True)

    def _build(self, width: int) -> list[Content]:
        lines: list[Content] = []
        bg = f"on {self.bg}" if self.bg else ""
        if self.pad_top > 0:
            lines.extend(
                Content(f"{' ' * width}", spans=[Span(0, width, bg)]) for _ in range(self.pad_top)
            )
        title_line = self.title_line
        if title_line is not None:
            if self.expandable:
                arrow = "▾" if not self.collapsed else "▸"
                title_line = Content.assemble(arrow, " ", title_line)
            if self.bg:
                title_line = Content(
                    title_line.plain,
                    [*title_line.spans, Span(0, len(title_line.plain), bg)],
                )
            lines.append(title_line)
        if not self.collapsed and self.content is not None:
            inner_width = max(0, width - self.pad_left - self.pad_right)
            if inner_width > 0:
                if bg:
                    left_pad = Content(" " * self.pad_left, spans=[Span(0, self.pad_left, bg)])
                else:
                    left_pad = Content(" " * self.pad_left)
                for line in self.content.split("\n", allow_blank=True):
                    if line.cell_length > inner_width:
                        new_lines = line.wrap(inner_width)
                    else:
                        new_lines = [line]
                    for nline in new_lines:
                        if self.bg:
                            nline = Content(
                                nline.plain,
                                [*nline.spans, Span(0, len(nline.plain), bg)],
                            )
                        lines.append(left_pad + nline)
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
            padded: list[Content] = []
            fills: list[int | None] = []
            for line in raw:
                if self.bg:
                    pl, fa = _pad_line(line, width)
                    line = pl
                    fills.append(fa)
                else:
                    fills.append(None)
                padded.append(line)
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
            strip = strip.apply_style(RichStyle.parse(self.body_style))
        return strip


class ChatCanvas(Static):
    """Single-widget chat area.

    All messages are drawn into one big canvas; scrolling the parent
    scroll container only shifts cached lines (translate), new lines
    entering the viewport are rendered on demand.
    """

    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._blocks: list[CanvasBlock] = []
        self._offsets: list[int] = []

    def text_select_all(self) -> None:
        """Double-click select-all is disabled; drag-select still works."""

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
            plain = line.plain
            pl = 0 if block.title_line is not None and by == block.pad_top else block.pad_left
            s = x0 if y == y0 else 0
            e = x1 if y == y1 else -1
            no_w = _line_no_end(line, pl)
            if s < pl + no_w:
                s = pl + no_w
            fill_at = block._fill_at[by] if by < len(block._fill_at) else None
            if e < 0 or (fill_at is not None and e > fill_at):
                e = fill_at if fill_at is not None else len(plain)
            if fill_at is not None and s >= fill_at:
                s = pl + no_w
            parts.append(plain[s:e] if s < e else "")
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
        self.refresh()

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
            return Strip.blank(width)
        strip = block.render_line(by, width)
        selection = self.text_selection
        if selection is not None and selection.start is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = strip.cell_length
                if start < end:
                    style = self.screen.get_component_styles("screen--selection")
                    strip = _apply_selection(strip, start, end, style)
        return strip

    def on_click(self, event) -> None:
        if not self._blocks:
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
