#!/usr/bin/env python3
"""Real-session performance benchmark: loads the only session json from /session,
measures static render + scrolling cost.

How it works: headless mount of a real XAgentTUI, driving the real
_switch_session -> _render_messages -> ChatCanvas.render_line pipeline. Reports:
  1) session load (disk read / estimate / full render)
  2) static render (full document / single viewport frame)
  3) scroll cost (warm cache scrolling vs per-frame cache invalidation = worst case for streaming)
  4) per-item cost of the 60fps tick
  5) per-step persistence (full json.dump to disk)
  6) cProfile attribution
  7) stress scaling curve (session x10 / x50)
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
_mcpman.McpManager.connect_async = lambda self, servers: None  # silence MCP background threads

from src.ui.tui.app import XAgentTUI
from src.agent.session import get_session_manager, Session


def ms(t: float) -> str:
    return f"{t * 1000:.2f}ms"


def us(t: float) -> str:
    return f"{t * 1e6:8.1f}µs"


def bench(fn, n=N_RUNS):
    """Return (median, all)"""
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
    print(f"=== XAgent real-session performance benchmark  session: {SID}  terminal: {SIZE[0]}x{SIZE[1]} ===")
    t0 = time.perf_counter()
    async with XAgentTUI().run_test(size=SIZE) as pilot:
        app = pilot.app
        print(f"mount cold start: {ms(time.perf_counter() - t0)}")

        # ---------- Phase 0: pure load cost ----------
        path = os.path.join(os.path.expanduser("~/.local/share/xagent/sessions"), f"{SID}.json")
        b, _, d = bench(lambda: json.load(open(path, encoding="utf-8")))
        print(f"\n[0] session file disk read + json parse: {ms(b)} (file {os.path.getsize(path)}B)")
        session_file = d
        b, _, _ = bench(lambda: get_session_manager().get(SID))
        print(f"[0] SessionManager.get() (disk read + object construction): {ms(b)}")

        # ---------- Phase 1: real session switch (per-part attribution) ----------
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
        print(f"[1] _switch_session total: {ms(sum(parts))}")
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
        print(f"[1] after deferred refresh: blocks: {len(blocks)}, total_lines: {total_lines}, viewport height {vp_h}, max_scroll_y {max_scroll}")
        hidden_msgs = app._hidden_msgs
        print(f"    (message window: msgs={app._win_msgs}, lines={app._win_lines}, hidden={hidden_msgs})")

        # cost of a single layout refresh (differ/layout/composite)
        def full_frame_flush():
            t0 = time.perf_counter()
            app._canvas().refresh(layout=True)
            return time.perf_counter() - t0
        b, _, _ = bench(full_frame_flush, n=5)
        print(f"    canvas.refresh(layout=True) single call: {us(b)} (actual composite happens on next idle frame)")

        # per-block breakdown: lines/count per kind
        kinds = {}
        for blk in blocks:
            k = kinds.setdefault(blk.kind, [0, 0])
            k[0] += 1
            k[1] += len(blk._lines)
        print(f"    blocks by kind (count/lines): { {k: v for k, v in kinds.items()} }")

        # ---------- Phase 2: static full-document render ----------
        b, _, _ = bench(lambda: render_viewport(canvas, 0, total_lines))
        print(f"\n[2] static render - full doc (all lines, cold strip cache): {ms(b)} / {total_lines} lines = {b / total_lines * 1e6:.1f}µs/line (median n={N_RUNS})")
        b, _, _ = bench(lambda: render_viewport(canvas, 0, total_lines))
        print(f"[2] static render - full doc (hot canvas cache, should be ≈0): {ms(b)}")

        def cold_viewport(top):
            """Simulate a no-cache frame: clear strip cache then render the viewport"""
            canvas._invalidate_render()
            return render_viewport(canvas, top, vp_h)

        b, _, lines_rendered = bench(lambda: cold_viewport(0), n=N_RUNS * 2)
        # actual cell count of the first viewport screen (accumulate block order up to vp_h lines)
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
        print(f"[2] single viewport redraw (full cache miss, streaming-equivalent): {ms(b)} / {lines_rendered} lines = {b / lines_rendered * 1e6:.1f}µs/line")
        print(f"    └─ viewport frame output: ~{cells} cells ≈ {cells * 4 // 1024}KB ANSI (estimated 4B/cell) - Windows per-cell console API amplification lives here")

        # ---------- Phase 3: scroll simulation ----------
        print("\n[3] scroll (trackbar/paging) frame cost - from top to bottom:")
        # a) real scroll: canvas cache stays, only newly entered lines render
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
        print(f"    warm-cache full scroll: {ms(b)} ({sp_n} lines rendered)")
        print(f"    per-frame mean: {statistics.median(f[1] for f in frames) * 1e6:8.1f}µs   worst frame (top={frame_worst[0]}): {frame_worst[1] * 1e6:8.1f}µs")

        # b) worst case: cache invalidated every frame (streaming delta / spinner animation)
        frames_wc = []
        for _ in range(N_RUNS):
            top = 0
            while top < total_lines:
                t0 = time.perf_counter()
                canvas._invalidate_render()
                n = render_viewport(canvas, top, vp_h)
                frames_wc.append(time.perf_counter() - t0)
                top += vp_h
        print(f"    per-frame cache miss (streaming-equivalent): mean {statistics.median(frames_wc) * 1e6:8.1f}µs/frame  worst {max(frames_wc) * 1e6:8.1f}µs/frame")
        fma = max(frames_wc)
        if fma > 0.016:
            print(f"    ⚠ exceeds the 60fps budget of 16.7ms by {fma / 0.016:.1f}x")

        # ---------- Phase 4: tick and status refresh cost ----------
        print("\n[4] 60fps tick per-item cost (per call):")
        b, _, _ = bench(lambda: app._trim_message_window(), n=5)
        print(f"    _trim_message_window (per frame): {us(b)}, share of 60fps budget {b / 0.016 * 100:.1f}%")
        b, _, _ = bench(lambda: app._canvas()._settle_resize(), n=5)
        print(f"    _settle_resize (no pending): {us(b)}")
        b, _, _ = bench(lambda: app._trim_canvas_blocks(), n=5)
        print(f"    _trim_canvas_blocks: {us(b)}")
        b, _, _ = bench(lambda: app._info_string(), n=5)
        print(f"    _info_string (status bar, incl. context limit lookup): {us(b)}")
        b, _, _ = bench(lambda: app._mcp_status_text(), n=5)
        print(f"    _mcp_status_text: {us(b)}")
        b, _, _ = bench(lambda: app._update_status(), n=5)
        print(f"    _update_status (full status bar group): {us(b)}")
        b, _, _ = bench(lambda: app._refresh_mcp_picker(), n=5)
        print(f"    _refresh_mcp_picker (idle 0.5s): {us(b)}")
        b, _, _ = bench(lambda: app._flush_streaming_content(), n=5)
        print(f"    _flush_streaming_content (no stream): {us(b)}")

        # busy state: spinner + full-frame tick combination
        cur = app._current = {"steps": 0, "reasoning_text": "", "reply_text": "", "thinking": None, "reply": None, "tools": {}, "tool_buffers": {}, "waiting": None, "retry": None, "last_stream_render": 0.0, "last_tool_render": 0.0, "_thinking_md_len": 0, "_thinking_render": 0.0, "_reply_md_len": 0}
        blk = app._append_block(kind="thinking")
        app._start_spinner(blk, "Thinking")
        app._busy = True
        from src.ui.tui.turnrender import new_turn_state
        app._current = new_turn_state()
        app._ensure_thinking()

        def busy_tick():
            app._spinner_idx += 1
            app._render_spinner(cur["thinking"], "Thinking")

        b, _, _ = bench(busy_tick, n=5)
        print(f"    busy frame (spinner redraw, per 60fps frame): {us(b)}")
        b, _, _ = bench(lambda: app._tick_animations(), n=5)
        print(f"    _tick_animations full frame (busy, runs every frame): {us(b)}")
        app._stop_all_spinners()
        app._busy = False
        app._current = None

        # ---------- Phase 5: persistence ----------
        tmp = "/tmp/opencode/persist_bench.json"
        os.makedirs("/tmp/opencode", exist_ok=True)
        def persist():
            tmp2 = tmp + ".tmp"
            with open(tmp2, "w", encoding="utf-8") as f:
                json.dump(app._session.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp2, tmp)
        b, _, _ = bench(persist, n=10)
        print(f"\n[5] per-step persistence (full json.dump indent=2 + atomic replace, real session size): {ms(b)}")
        print(f"    (runs synchronously on the event loop, once per assistant step / tool step)")

        # ---------- Phase 6: message-level render attribution ----------
        print("\n[6] cProfile attribution (load + full-doc render + cache-miss scroll + persistence):")

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

        # ---------- Phase 7: stress scaling (session x10 / x50, message window lifted) ----------
        print("\n[7] stress scaling - repeat session messages n times (long-session simulation, 100-msg window lifted):")
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
                print(f"    x{factor}: {factor * len(renderable)} messages -> {len(canvas2._blocks)} blocks/{tl2} lines after render")
                print(f"      full _render_messages: {ms(rt)}   full-doc render: {ms(b2)}")
                print(f"      streaming worst frame (cache-miss scroll): median {wf_st * 1e6:8.1f}µs  worst {wf_worst * 1e6:8.1f}µs {'⚠over budget' if wf_worst > 0.016 else ''}")
        finally:
            app._session = prev_app_session
            app._win_msgs, app._win_lines = prev_win
            app._render_messages()


if __name__ == "__main__":
    asyncio.run(main())
