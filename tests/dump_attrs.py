#!/usr/bin/env python3
"""被渲染对象的全部属性清单。

挂载真实 TUI, 加载真实会话, 对每个类型的渲染块(block)解剖其属性树:
  CanvasBlock -> Content(行) -> Span(段) -> rich Style
  CanvasBlock -> Strip -> Segment(段) -> rich Style
并输出 widget 级样式(CSS 解析后的 textual Style/visual_style)。
"""
import asyncio
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.mcp.manager as _mcpman
_mcpman.McpManager.connect_async = lambda self, servers: None

from src.ui.tui.app import XAgentTUI
from rich.segment import Segment
from textual.strip import Strip
from textual.content import Content, Span
from rich.style import Style as RichStyle

SID = "7d1adbfc"
BLOCK_KIND_CAPTURE = ("user", "thinking", "reply", "tool", "tool-block", "summary", "divider")


def gather_attrs(obj) -> dict:
    """返回该对象的属性清单: {(name, value, kind)}"""
    out = {}
    for name in dir(obj):
        if name.startswith("__"):
            continue
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if inspect.ismethod(val) or inspect.isfunction(val) or inspect.isclass(val) or isinstance(val, property):
            continue
        out[name] = val
    return out


def short(v, n=60) -> str:
    s = repr(v)
    if len(s) > n:
        s = s[:n] + "…"
    return s


def dump_content(content: Content, indent: str = "    ") -> None:
    print(f"{indent}Content(实例) 类型={type(content).__name__}")
    print(f"{indent}  ├─ 实例字段 (vars): { {k: short(v) for k, v in vars(content).items()} }")
    print(f"{indent}  ├─ 公开属性: plain=...({len(content.plain)}ch, cell_length={content.cell_length})")
    print(f"{indent}  ├─ .spans: {len(content.spans)} 个 (span.style 为 CSS 字符串)")
    extra = {}
    for name in ("style", "em_width", "half_em", "is_blank", "cell_length", "strip_control_codes"):
        if hasattr(content, name) and name != "plain":
            try:
                extra[name] = getattr(content, name)
            except Exception:
                pass
    if extra:
        print(f"{indent}  ├─ 其他属性: { {k: short(v) for k, v in extra.items()} }")
    for i, sp in enumerate(content.spans[:3]):
        dump_span(sp, f"{indent}  ├─ spans[{i}]")
    if content.spans:
        print(f"{indent}  └─ (span 共 {len(content.spans)} 个, 每行展示前 3 个)")


def dump_span(sp: Span, indent: str = "    ") -> None:
    print(f"{indent} Span: start={sp.start} end={sp.end} style={short(sp.style)} (@{id(sp.style)})")
    dump_style(sp.style, indent + "      └─")


def dump_style(st, indent: str = "    ") -> None:
    if st is None or isinstance(st, str):
        print(f"{indent} style={short(st)} (字符串/CSS)")
        return
    print(f"{indent} Style(rich) 类型={type(st).__name__}")
    slots = getattr(st, "__slots__", None)
    if slots:
        print(f"{indent}  ├─ __slots__: {list(slots)}")
    for name in ("color", "bgcolor", "bold", "dim", "italic", "underline", "underline2", "overstrike", "overline", "blink", "reverse", "strike", "meta", "link", "font", "font_size", "hint", "veil"):
        if hasattr(st, name):
            try:
                print(f"{indent}  ├─ .{name} = {short(getattr(st, name))}")
            except Exception as e:
                print(f"{indent}  ├─ .{name} (读取异常: {e})")
        else:
            print(f"{indent}  ├─ .{name} (无此属性)")
    try:
        print(f"{indent}  └─ str() = {st!r}")
    except Exception:
        pass


def dump_strip(strip: Strip, indent: str = "    ") -> None:
    print(f"{indent}Strip 类型={type(strip).__name__}")
    slots = getattr(strip, "__slots__", None)
    if slots:
        print(f"{indent}  ├─ __slots__: {list(slots)}")
        for s in slots:
            try:
                print(f"{indent}  ├─ .{s} = {short(getattr(strip, s))}")
            except Exception:
                pass
    for name in ("cell_length", "text", "align", "is_blank", "link_style"):
        if hasattr(strip, name) and name not in (slots or []):
            try:
                print(f"{indent}  ├─ .{name} = {short(getattr(strip, name))}")
            except Exception:
                pass
    print(f"{indent}  └─ segments: {len(strip._segments)} 个")
    for i, seg in enumerate(strip._segments[:3]):
        dump_segment(seg, f"{indent}      ├─ segments[{i}]")


def dump_segment(seg: Segment, indent: str = "    ") -> None:
    print(f"{indent}Segment: text={short(seg.text)} style_kind={type(seg.style).__name__} control={seg.control is not None}")
    dump_style(seg.style, indent + "      └─")
    if seg.control is not None:
        print(f"{indent}      └─ control(controlcode): {short(repr(seg.control))}")


def collect_block_line_samples(block, canvas):
    """取块内 2 条有代表性的行 (第一条非空白行 + 最长非空白行)"""
    if not block._lines:
        return []
    lines = [l for l in block._lines if l.plain.strip()]
    if not lines:
        lines = block._lines
    samples = [lines[0]]
    longest = max(lines, key=lambda l: l.cell_length)
    if longest is not lines[0]:
        samples.append(longest)
    return samples[:2]


async def main() -> None:
    async with XAgentTUI().run_test(size=(120, 40)) as pilot:
        app = pilot.app
        await pilot.pause()
        app._switch_session(SID)
        await pilot.pause()
        canvas = app._canvas()
        blocks = canvas._blocks

        print(f"=== 被渲染对象属性全清单  会话 {SID}  blocks={len(blocks)} 总行数={canvas._total_lines()} ===")

        # ---- block 层 ----
        print("\n【1】CanvasBlock(渲染块) 全部属性 (来自 __init__ + 运行时)")
        seen = set()
        for blk in blocks:
            if blk.kind in seen:
                continue
            seen.add(blk.kind)
        kinds_seen = sorted(seen, key=BLOCK_KIND_CAPTURE.index if all(k in BLOCK_KIND_CAPTURE for k in seen) else (lambda k: 0))
        sample_block = None
        for kind in BLOCK_KIND_CAPTURE:
            for blk in blocks:
                if blk.kind == kind:
                    sample_block = blk
                    break
            if sample_block:
                break
        if sample_block is not None:
            for name, val in gather_attrs(sample_block).items():
                print(f"    {name:24s} = {short(val, 90)}")
            print(f"    (以 kind={sample_block.kind} 块为例; 各 kind 差异仅在初始化参数: BLOCK_SPECS, 见 blocks.py)")

        # ---- 每种 kind 的行数/span 统计 ----
        print("\n【2】各类块的行/span 统计")
        stats = {}
        for blk in blocks:
            st = stats.setdefault(blk.kind, [0, 0, 0, 0])
            st[0] += 1
            st[1] += len(blk._lines)
            for ln in blk._lines:
                st[2] += len(ln.spans)
                st[3] = max(st[3], len(ln.spans))
        for k, v in stats.items():
            print(f"    {k:14s} 块数={v[0]:3d} 行数={v[1]:5d} span总数={v[2]:6d} 单行最多span={v[3]}")

        # ---- Content / Span / Style 深解剖(每种 kind 取样本) ----
        print("\n【3】行数据 Content → Span → rich Style 属性深解剖")
        done_kinds = set()
        for blk in blocks:
            kind = blk.kind
            if kind in done_kinds:
                continue
            done_kinds.add(kind)
            print(f"\n  == kind={kind}  (title={short(blk.title)}) ==")
            for i, line in enumerate(collect_block_line_samples(blk, canvas)):
                print(f"  -- 样例行[{i}] (cell_length={line.cell_length}, spans={len(line.spans)}) --")
                dump_content(line, "     ")

        # ---- Strip / Segment 深解剖(把首屏行真正渲染成 Strip) ----
        print("\n【4】渲染结果 Strip → Segment → rich Style 属性深解剖")
        done_kinds = set()
        total_lines = canvas._total_lines()
        for y in range(min(total_lines, 64)):
            strip = canvas.render_line(y)
            blk, by = canvas._block_at(y)
            if blk is None:
                continue
            kind = blk.kind
            if kind in done_kinds:
                continue
            done_kinds.add(kind)
            print(f"\n  == y={y} kind={kind} by={by} ==")
            dump_strip(strip, "     ")

        # ---- widget 级样式 ----
        print("\n【5】widget 级样式 (CSS 解析后的值)")
        chat = app._chat()
        for w, label in ((chat, "chat-box(VerticalScroll)"), (canvas, "chat-canvas(Static)"), (app._input(), "input(TextArea)"), (app.query_one("#status", object), "status(Static)")):
            print(f"\n  == {label} ==")
            try:
                vs = w.visual_style
                print(f"    visual_style: {vs}")
            except Exception as e:
                print(f"    visual_style: (无 {e})")
            try:
                sv = w.styles
                names = [n for n in dir(sv) if not n.startswith("_")]
                print(f"    styles 对象 {type(sv).__name__}, 可读属性 {len(names)} 个: {names[:40]}{'…' if len(names) > 40 else ''}")
                for n in ("background", "color", "padding", "border", "scrollbar_size", "text_style", "height", "width", "visibility", "display"):
                    if hasattr(sv, n):
                        try:
                            print(f"      styles.{n} = {getattr(sv, n)}")
                        except Exception:
                            pass
            except Exception as e:
                print(f"    styles: ({e})")

        # ---- 交互渲染附属物 ----
        print("\n【6】其他参与渲染/刷新的状态属性")
        print(f"    canvas._strip_cache: {len(canvas._strip_cache)} 条 (key=(gen,y,width)), gen={canvas._render_gen}")
        print(f"    canvas._offsets: {len(canvas._offsets)} 条")
        print(f"    canvas._bulk={canvas._bulk} _built_width={canvas._built_width} _pending_width={canvas._pending_width}")
        print(f"    app._win_msgs={app._win_msgs} _win_lines={app._win_lines} _hidden_msgs={app._hidden_msgs} _busy={app._busy}")
        print(f"    app.spinners: { {k: (v[0].kind, v[1]) for k, v in app._spinners.items()} }")


if __name__ == "__main__":
    asyncio.run(main())