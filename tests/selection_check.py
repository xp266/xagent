from __future__ import annotations

import sys

_ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from rich.cells import get_character_cell_size
from rich.style import Style as RichStyle
from textual.content import Content
from textual.geometry import Offset
from textual.selection import Selection

from src.ui.tui.canvas import CanvasBlock, ChatCanvas
from src.ui.tui.lazy import _apply_selection


class _ProbeCanvas(ChatCanvas):

    @property
    def text_selection(self):
        return None


def _hit_test(strip, x):
    end = 0
    start = 0
    offset_y = None
    offset_x = 0
    offset_x2 = 0
    for segment in strip:
        end += segment.cell_length
        style = segment.style
        if style is not None and style._meta is not None and "offset" in style.meta:
            offset_x, offset_y = style.meta["offset"]
            offset_x2 = offset_x + len(segment.text)
            if x < end and x >= start:
                so = 0
                for ch in segment.text:
                    if x - start - so <= 0:
                        break
                    so += get_character_cell_size(ch)
                return Offset(offset_x + so, offset_y)
        start = end
    if offset_y is not None:
        return Offset(offset_x2, offset_y)
    return None


def _check(name, got, want):
    if got != want:
        print(f"FAIL {name}: got {got!r}, want {want!r}")
        sys.exit(1)
    print(f"ok {name}")


b = CanvasBlock(kind="user", pad_left=3, content_pad_left=3, bg="#101014", content_bg="#101014")
b.update("Hello world")
b._rebuild(60)
for x in (0, 1, 3, 5, 12, 30):
    o = _hit_test(b.render_line(0, 60), x)
    _check(f"anchor pl=3 col {x}", o, Offset(x, 0))

canvas = _ProbeCanvas()
u = CanvasBlock(kind="user", pad_left=3, content_pad_left=3)
u.update_lines([Content("alpha"), Content("beta")])
canvas.append(u)
t = CanvasBlock(kind="tool", pad_left=0, content_pad_left=0)
t.update_lines([Content("first"), Content("second")])
canvas.append(t)
canvas._rebuild_offsets(60)


def _drag(start_col, start_y, end_col, end_y):
    s_off = _hit_test(canvas.render_line(start_y), start_col)
    e_off = _hit_test(canvas.render_line(end_y), end_col)
    sel = Selection.from_offsets(s_off, e_off + (1, 0))
    txt, _ = canvas.get_selection(sel)
    return txt


_check("drag mid-text", _drag(4, 0, 2, 3), "lpha\nbeta\nfirst\nsec")
_check("drag from right blank (start line empty, terminal std)", _drag(30, 0, 1, 3), "\nbeta\nfirst\nse")
_check("drag from left pad (clamps to line start)", _drag(1, 0, 1, 3), "alpha\nbeta\nfirst\nse")
_check("upward drag", _drag(30, 3, 30, 0), "\nbeta\nfirst\nsecond")

strip = b.render_line(0, 60)
sel_strip = _apply_selection(strip, 3, 9, RichStyle.parse("on #334488"))
for x in (0, 3, 5, 9, 30):
    _check(f"anchor on selected strip col {x}", _hit_test(sel_strip, x), Offset(x, 0))

tb = CanvasBlock(kind="user", pad_left=3, content_pad_left=3, bg="#101014", content_bg="#101014")
tb.update("40charhash\trefs/tags/v2.5.0^{}\r\nansi\x1b[31mred\ttail")
tb._rebuild(60)
raw_lines = tb._lines
_check("tab expanded", raw_lines[0].plain, "40charhash  refs/tags/v2.5.0^{}")
_check("crlf+ansi stripped, tab expanded", raw_lines[1].plain, "ansired    tail")
_check("crlf split produced 2 lines", len(raw_lines), 2)
any_tab = any("\t" in seg.text for ln in tb._lines for seg in tb.render_line(0, 60))
_check("no literal tab in strips", any_tab, False)

canvas2 = _ProbeCanvas()
b3 = CanvasBlock(kind="user", pad_left=3, content_pad_left=3)
b3.update("h\trefs/tags/v2.5.0")
canvas2.append(b3)
canvas2._rebuild_offsets(60)
s_off2 = _hit_test(canvas2.render_line(0), 3)
e_off2 = _hit_test(canvas2.render_line(0), 30)
sel2 = Selection.from_offsets(s_off2, e_off2 + (1, 0))
txt2, _ = canvas2.get_selection(sel2)
_check("copy covers full expanded line", txt2, "h   refs/tags/v2.5.0")

print("selection_check: all ok")