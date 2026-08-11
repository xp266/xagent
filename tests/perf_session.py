#!/usr/bin/env python3
"""真实场景性能基准: 加载 /session 中唯一的会话 json, 测静态渲染 + 滚动消耗。

模拟方式: headless 挂载真实 XAgentTUI, 走真实 _switch_session → _render_messages
→ ChatCanvas.render_line 渲染管线。报告:
  1) 会话加载(读盘/估算/全量渲染)
  2) 静态渲染 (全文档 / 单帧视口)
  3) 滚动消耗 (缓存热滚动 vs 每帧缓存失效=流式更新最坏情况)
  4) 60fps tick 各项开销
  5) 每步持久化 json.dump 全量写盘
  6) cProfile 归因
  7) 会话放大 10x/50x 的压力扩展曲线
"""
import asyncio
import cProfile
import io
import json
import os
import pstats
import stat
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SID = os.environ.get("PERF_SID", "7d1adbfc")
SIZE = (120, 40)
N_RUNS = 3

from textual.containers import VerticalScroll
from textual.widgets import Static

import src.mcp.manager as _mcpman
_mcpman.McpManager.connect_async = lambda self, servers: None  # 屏蔽 MCP 后台线程干扰

from src.ui.tui.app import XAgentTUI
from src.agent.session import get_session_manager, Session


def ms(t: float) -> str:
    return f"{t * 1000:.2f}ms"


def us(t: float) -> str:
    return f"{t * 1e6:8.1f}µs"


def bench(fn, n=N_RUNS):
    """返回 (median, all)"""
    times = []
    last = None
    for _ in range(n):
        t0 = time.perf_counter()
        last = fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), times, last


class FakeSession(Session):
    pass


def render_viewport(canvas, top, height):
    total = canvas._total_lines()
    lo = max(0, top)
    hi = min(total, top + height)
    for y in range(lo, hi):
        canvas.render_line(y)
    return hi - lo


async def main() -> None:
    print(f"=== XAgent 真实会话性能基准  会话: {SID}  终端: {SIZE[0]}x{SIZE[1]} ===")
    t0 = time.perf_counter()
    async with XAgentTUI().run_test(size=SIZE) as pilot:
        app = pilot.app
        print(f"mount 冷启动耗时: {ms(time.perf_counter() - t0)}")

        # ---------- Phase 0: 纯加载开销 ----------
        path = os.path.join(os.path.expanduser("~/.local/share/xagent/sessions"), f"{SID}.json")
        b, _, d = bench(lambda: json.load(open(path, encoding="utf-8")))
        print(f"\n[0] 会话文件读盘+json解析: {ms(b)} (文件 {os.path.getsize(path)}B)")
        session_file = d
        b, _, _ = bench(lambda: get_session_manager().get(SID))
        print(f"[0] SessionManager.get() (读盘+对象构造): {ms(b)}")

        # ---------- Phase 1: 真实切换会话(分段归因) ----------
        from src.agent.compact import estimate_context_usage

        def phase_switch():
            t0 = time.perf_counter()
            s = app._sm.get(SID)
            t_get = time.perf_counter() - t0
            t0 = time.perf_counter()
            app._sm.current = s.id
            app._session = s
            t0 = time.perf_counter()
            app._ctx_usage_tokens = estimate_context_usage(s)
            t_est = time.perf_counter() - t0
            t0 = time.perf_counter()
            app._render_messages()
            t_render = time.perf_counter() - t0
            t0 = time.perf_counter()
            app._update_status()
            t_status = time.perf_counter() - t0
            t0 = time.perf_counter()
            app._input().focus()
            t_focus = time.perf_counter() - t0
            return (t_get, t_est, t_render, t_status, t_focus)

        b, _, parts = bench(phase_switch, n=2)
        print(f"[1] _switch_session 总耗时: {ms(sum(parts))}")
        print(f"    ├─ sm.get+estimate: {ms(parts[0] + parts[1])} (estimate≈{est if (est := estimate_context_usage(app._session)) is not None else 0} tokens)")
        print(f"    ├─ _render_messages: {ms(parts[2])}")
        print(f"    ├─ _update_status:   {ms(parts[3])}")
        print(f"    └─ input.focus:      {ms(parts[4])}")

        await pilot.pause()
        canvas = app._canvas()
        chat = app._chat()
        blocks = canvas._blocks
        total_lines = canvas._total_lines()
        vp_h = max(1, chat.size.height or 24)
        max_scroll = chat.max_scroll_y
        print(f"[1] 冲刷延迟刷新后: blocks: {len(blocks)}, 总行数: {total_lines}, 可视视口高 {vp_h}, max_scroll_y {max_scroll}")
        hidden_msgs = app._hidden_msgs
        print(f"    (隐藏消息窗口: msgs={app._win_msgs}, lines={app._win_lines}, hidden={hidden_msgs})")

        # 布局刷新本身(differ/layout/合成)的一次代价
        def full_frame_flush():
            t0 = time.perf_counter()
            app._canvas().refresh(layout=True)
            return time.perf_counter() - t0
        b, _, _ = bench(full_frame_flush, n=5)
        print(f"    canvas.refresh(layout=True) 单次调用开销: {us(b)} (实际合成在下一 idle 帧执行)")

        # per-block 明细: 每类 block 行数/条数
        kinds = {}
        for blk in blocks:
            k = kinds.setdefault(blk.kind, [0, 0])
            k[0] += 1
            k[1] += len(blk._lines)
        print(f"    blocks 分类(条数/行数): { {k: v for k, v in kinds.items()} }")

        # ---------- Phase 2: 静态全文档渲染 ----------
        b, _, _ = bench(lambda: render_viewport(canvas, 0, total_lines))
        print(f"\n[2] 静态渲染-全文档(全部行, 冷strip缓存): {ms(b)} / {total_lines} 行 = {b / total_lines * 1e6:.1f}µs/行 (中位数 n={N_RUNS})")
        b, _, _ = bench(lambda: render_viewport(canvas, 0, total_lines))
        print(f"[2] 静态渲染-全文档(热canvas缓存, 应≈0): {ms(b)}")

        def cold_viewport(top):
            """模拟无缓存的一帧: 清 strip 缓存后渲染视口"""
            canvas._invalidate_render()
            return render_viewport(canvas, top, vp_h)

        b, _, lines_rendered = bench(lambda: cold_viewport(0), n=N_RUNS * 2)
        # 视口第一屏实际 cell 数(按块顺序累加到 vp_h 行)
        cells = 0
        n_cells_lines = 0
        for blk in blocks:
            for l in blk._lines:
                if n_cells_lines >= vp_h:
                    break
                cells += len(l.plain)
                n_cells_lines += 1
            if n_cells_lines >= vp_h:
                break
        print(f"[2] 单帧视口重绘(缓存全失效, 流式更新等价): {ms(b)} / {lines_rendered} 行 = {b / lines_rendered * 1e6:.1f}µs/行")
        print(f"    └─ 视口帧输出量: ~{cells} cells ≈ {cells * 4 // 1024}KB ANSI(按每cell 4B估) — Windows 逐cell控制台API的额外放大系数在此")

        # ---------- Phase 3: 滚动模拟 ----------
        print("\n[3] 滚动(滑块/翻页)帧消耗 — 从顶滚到底:")
        # a) 真实滚动: canvas 缓存保持, 只渲染新进入视口的行
        def scroll_pass_cached():
            rendered = 0
            t0 = time.perf_counter()
            top = 0
            while top < total_lines:
                rendered += render_viewport(canvas, top, vp_h)
                top += vp_h
            return time.perf_counter() - t0, rendered

        frames = []
        for _ in range(N_RUNS):
            top = 0
            while top < total_lines:
                t0 = time.perf_counter()
                n = render_viewport(canvas, top, vp_h)
                frames.append((top, time.perf_counter() - t0, n))
                top += vp_h
        b, _, (sp_t, sp_n) = bench(scroll_pass_cached)
        frame_worst = max(frames, key=lambda f: f[1])
        print(f"    缓存热滚动整趟: {ms(b)} ({sp_n} 行触发渲染)")
        print(f"    每帧均值: {statistics.median(f[1] for f in frames) * 1e6:8.1f}µs   最差帧(top={frame_worst[0]}): {frame_worst[1] * 1e6:8.1f}µs")

        # b) 最坏情况: 每帧都失效缓存(模拟流式 delta/转圈动画每帧刷新)
        frames_wc = []
        for _ in range(N_RUNS):
            top = 0
            while top < total_lines:
                t0 = time.perf_counter()
                canvas._invalidate_render()
                n = render_viewport(canvas, top, vp_h)
                frames_wc.append(time.perf_counter() - t0)
                top += vp_h
        print(f"    每帧缓存失效(流式更新等价): 均值 {statistics.median(frames_wc) * 1e6:8.1f}µs/帧  最差 {max(frames_wc) * 1e6:8.1f}µs/帧")
        fma = max(frames_wc)
        if fma > 0.016:
            print(f"    ⚠ 已超过 60fps 帧预算 16.7ms 的 {fma / 0.016:.1f}x")

        # ---------- Phase 4: tick 与状态刷新开销 ----------
        print("\n[4] 60fps tick 各子项开销(每次调用):")
        b, _, _ = bench(lambda: app._trim_message_window(), n=5)
        print(f"    _trim_message_window (每帧): {us(b)}, x60fps/帧预算占比 {b / 0.016 * 100:.1f}%")
        b, _, _ = bench(lambda: app._canvas()._settle_resize(), n=5)
        print(f"    _settle_resize (无pending时): {us(b)}")
        b, _, _ = bench(lambda: app._trim_canvas_blocks(), n=5)
        print(f"    _trim_canvas_blocks: {us(b)}")
        b, _, _ = bench(lambda: app._info_string(), n=5)
        print(f"    _info_string (状态栏, 含context limit查询): {us(b)}")
        b, _, _ = bench(lambda: app._mcp_status_text(), n=5)
        print(f"    _mcp_status_text: {us(b)}")
        b, _, _ = bench(lambda: app._update_status(), n=5)
        print(f"    _update_status (整组状态栏): {us(b)}")
        b, _, _ = bench(lambda: app._refresh_mcp_picker(), n=5)
        print(f"    _refresh_mcp_picker (idle 0.5s): {us(b)}")
        b, _, _ = bench(lambda: app._flush_streaming_content(), n=5)
        print(f"    _flush_streaming_content (无流时): {us(b)}")

        # busy 态: 挂 spinner + wave, 测整帧 tick 组合
        cur = app._current = {"steps": 0, "reasoning_text": "", "reply_text": "", "thinking": None, "reply": None, "tools": {}, "tool_buffers": {}, "waiting": None, "retry": None, "last_stream_render": 0.0, "last_tool_render": 0.0, "_thinking_md_len": 0, "_thinking_render": 0.0, "_reply_md_len": 0}
        blk = app._append_block(kind="thinking")
        app._start_spinner(blk, "Thinking")
        app._busy = True
        from src.ui.tui.turnrender import new_turn_state
        app._current = new_turn_state()
        app._ensure_thinking()
        app._waves.append(time.monotonic())

        def busy_tick():
            app._spinner_idx += 1
            app._render_spinner(cur["thinking"], "Thinking")
            app._tick_status_wave()

        b, _, _ = bench(busy_tick, n=5)
        print(f"    busy 帧组合 (spinner重绘+wave动画, 60fps 每帧): {us(b)}")
        b, _, _ = bench(lambda: app._tick_animations(), n=5)
        print(f"    _tick_animations 整帧 (busy, 每帧必跑): {us(b)}")
        app._stop_all_spinners()
        app._waves.clear()
        app._busy = False
        app._current = None

        # ---------- Phase 5: 持久化 ----------
        tmp = "/tmp/opencode/persist_bench.json"
        os.makedirs("/tmp/opencode", exist_ok=True)
        def persist():
            tmp2 = tmp + ".tmp"
            with open(tmp2, "w", encoding="utf-8") as f:
                json.dump(app._session.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp2, tmp)
        b, _, _ = bench(persist, n=10)
        print(f"\n[5] 每步持久化(全量 json.dump indent=2 + 原子替换, 真实会话大小): {ms(b)}")
        print(f"    (此操作在事件循环上同步执行, 每 assistant 步/每 tool 步各一次)")

        # ---------- Phase 6: 消息级渲染归因 ----------
        print("\n[6] cProfile 归因 (载入+全文档渲染+缓存失效滚动一趟+持久化):")

        def profiled_run():
            app._switch_session(SID)
            render_viewport(canvas, 0, total_lines)
            for top in range(0, total_lines, vp_h):
                canvas._invalidate_render()
                render_viewport(canvas, top, vp_h)
            persist()
            app._render_messages()

        pr = cProfile.Profile()
        pr.enable()
        profiled_run()
        pr.disable()
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
        ps.print_stats(18)
        for line in s.getvalue().splitlines():
            if line.strip():
                print("    " + line)

        # ---------- Phase 7: 压力扩展 (会话 ×10 / ×50, 解除消息窗口上限) ----------
        print("\n[7] 压力扩展 — 把该会话消息重复 n 次(模拟长会话, 解除100条窗口上限):")
        renderable = [m for m in session_file["messages"] if m.get("role") != "system"]
        prev_win = (app._win_msgs, app._win_lines)
        app._win_msgs = 10 ** 9
        app._win_lines = 10 ** 9
        prev_app_session = app._session
        try:
            for factor in (10, 50):
                dup = []
                for i in range(factor):
                    for m in renderable:
                        d2 = dict(m)
                        d2["_meta"] = dict(m.get("_meta") or {})
                        dup.append(d2)
                app._session = FakeSession(id="x", messages=dup)
                t0 = time.perf_counter()
                app._render_messages(10 ** 9, 10 ** 9)
                rt = time.perf_counter() - t0
                canvas2 = app._canvas()
                tl2 = canvas2._total_lines()
                b2, _, _ = bench(lambda: render_viewport(canvas2, 0, tl2), n=2)
                top = 0
                wc_frames = []
                while top < tl2:
                    t0 = time.perf_counter()
                    canvas2._invalidate_render()
                    render_viewport(canvas2, top, min(vp_h, tl2 - top))
                    wc_frames.append(time.perf_counter() - t0)
                    top += min(vp_h, tl2 - top)
                wf_st, wf_worst = statistics.median(wc_frames), max(wc_frames)
                print(f"    ×{factor}: 消息{factor * len(renderable)}条 → 渲染后 {len(canvas2._blocks)} blocks/{tl2} 行")
                print(f"      全量重渲染 _render_messages: {ms(rt)}   全文档渲染: {ms(b2)}")
                print(f"      流式最坏帧(失效缓存逐帧滚): 中位 {wf_st * 1e6:8.1f}µs  最差 {wf_worst * 1e6:8.1f}µs {'⚠超帧预算' if wf_worst > 0.016 else ''}")
        finally:
            app._session = prev_app_session
            app._win_msgs, app._win_lines = prev_win
            app._render_messages()


if __name__ == "__main__":
    asyncio.run(main())