from __future__ import annotations

import functools
import json
import time

from src.ui.tui import lazy
from src.ui.tui.app import XAgentTUI
from src.ui.tui.canvas import CanvasBlock, ChatCanvas
from src.ui.tui.markdown import StreamMarkdown
from src.ui.tui import render as render_mod
from src.ui.tui.statusbar import StatusMixin
from src.ui.tui.thinking import ThinkingMarkdown
from src.ui.tui.turnrender import TurnRenderMixin

_LOG_INTERVAL = 0.1


def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


class Metrics:
    def __init__(self, path: str) -> None:
        self._f = open(path, "w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._last_line = self._t0
        self._last_tick: float | None = None
        self._ticks = 0
        self._gap_sum = 0.0
        self._gap_max = 0.0
        self._jank = 0
        self._last_event: float | None = None
        self._event_gap_max = 0.0
        self._event_jank = 0
        self._window: dict[str, list] = {}
        self._totals: dict[str, list] = {}
        self._app = None
        self._seq = 0
        self._closed = False

    def _add(self, key: str, ms: float) -> None:
        e = self._window.setdefault(key, [0, 0.0])
        e[0] += 1
        e[1] += ms
        t = self._totals.setdefault(key, [0, 0.0])
        t[0] += 1
        t[1] += ms

    def tick(self, app, ms: float) -> None:
        now = time.monotonic()
        if self._app is None:
            self._app = app
        self._ticks += 1
        if self._last_tick is not None:
            gap = (now - self._last_tick) * 1000.0
            self._gap_sum += gap
            self._gap_max = max(self._gap_max, gap)
            if gap > 100.0:
                self._jank += 1
        self._last_tick = now
        self._add("tick", ms)
        if now - self._last_line >= _LOG_INTERVAL:
            self._write_line(now)

    def event(self, ms: float) -> None:
        now = time.monotonic()
        if self._last_event is not None:
            gap = (now - self._last_event) * 1000.0
            self._event_gap_max = max(self._event_gap_max, gap)
            if gap > 100.0:
                self._event_jank += 1
        self._last_event = now
        self._add("event", ms)

    def _app_state(self) -> tuple[int, int, int, int, str]:
        app = self._app
        cur = getattr(app, "_current", None)
        busy = 1 if cur is not None else 0
        reply_chars = len(cur.get("reply_text", "")) if cur is not None else 0
        blocks = 0
        lines = 0
        try:
            canvas = app._canvas()
            blocks = len(canvas._blocks)
            lines = sum(len(b._lines) for b in canvas._blocks)
        except Exception:
            pass
        scenario = ""
        try:
            session = getattr(app, "_session", None)
            msgs = getattr(session, "msgs", None)
            if msgs is not None:
                for msg in reversed(getattr(msgs, "_messages", []) or []):
                    try:
                        role = msg.to_api().get("role")
                    except Exception:
                        role = None
                    if role == "user":
                        scenario = str(getattr(msg, "content", ""))[:40]
                        break
        except Exception:
            pass
        return busy, reply_chars, blocks, lines, scenario

    def _write_line(self, now: float) -> None:
        busy, reply_chars, blocks, lines, scenario = self._app_state()
        tick_calls = max(self._window.get("tick", [0, 0.0])[0], 1)
        tick_ms = self._window.get("tick", [0, 0.0])[1]
        fps = tick_calls / max(now - self._last_line, 1e-6)
        rec = {
            "seq": self._seq,
            "sec": round(now - self._t0, 3),
            "dt": round(now - self._last_line, 3),
            "busy": busy,
            "scenario": scenario,
            "fps": round(fps, 2),
            "gap_ms": round(self._gap_sum / tick_calls, 2),
            "gap_max_ms": round(self._gap_max, 2),
            "tick_ms": round(tick_ms / tick_calls, 3),
            "jank": self._jank,
            "event_gap_max_ms": round(self._event_gap_max, 2),
            "event_jank": self._event_jank,
            "rss_mb": round(_rss_mb(), 2),
            "blocks": blocks,
            "lines": lines,
            "reply_chars": reply_chars,
        }
        for key in ("event", "flush", "md", "think", "build", "rebuild", "strip", "pad", "wrap", "line", "status", "istatus", "hunk"):
            calls, total = self._window.get(key, [0, 0.0])
            rec[f"{key}_calls"] = calls
            rec[f"{key}_total_ms"] = round(total, 3)
            rec[f"{key}_avg_ms"] = round(total / calls, 4) if calls else 0.0
        self._seq += 1
        self._window = {}
        self._gap_sum = 0.0
        self._gap_max = 0.0
        self._jank = 0
        self._event_gap_max = 0.0
        self._event_jank = 0
        self._last_line = now
        self._f.write(json.dumps(rec) + "\n")
        self._f.flush()

    def totals(self) -> dict[str, list]:
        return dict(self._totals)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._f.close()
        except OSError:
            pass


def install(log_path: str) -> Metrics:
    m = Metrics(log_path)

    def _wrap_method(owner, name: str, key: str) -> None:
        orig = getattr(owner, name)

        @functools.wraps(orig)
        def wrapper(self, *a, **kw):
            t0 = time.perf_counter()
            try:
                return orig(self, *a, **kw)
            finally:
                m._add(key, (time.perf_counter() - t0) * 1000.0)

        setattr(owner, name, wrapper)

    def _wrap_func(mod, name: str, key: str) -> None:
        orig = getattr(mod, name)

        @functools.wraps(orig)
        def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return orig(*a, **kw)
            finally:
                m._add(key, (time.perf_counter() - t0) * 1000.0)

        setattr(mod, name, wrapper)

    orig_tick = XAgentTUI._tick_animations

    @functools.wraps(orig_tick)
    def tick_wrapper(self, *a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_tick(self, *a, **kw)
        finally:
            m.tick(self, (time.perf_counter() - t0) * 1000.0)

    XAgentTUI._tick_animations = tick_wrapper

    orig_event = TurnRenderMixin._handle_event

    @functools.wraps(orig_event)
    def event_wrapper(self, event, *a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_event(self, event, *a, **kw)
        finally:
            m.event((time.perf_counter() - t0) * 1000.0)

    TurnRenderMixin._handle_event = event_wrapper

    _wrap_method(TurnRenderMixin, "_flush_streaming_content", "flush")
    _wrap_method(StatusMixin, "_update_status", "status")
    _wrap_method(StatusMixin, "_update_input_status", "istatus")
    _wrap_method(StreamMarkdown, "render", "md")
    _wrap_method(ThinkingMarkdown, "render", "think")
    _wrap_method(CanvasBlock, "_build", "build")
    _wrap_method(CanvasBlock, "_rebuild", "rebuild")
    _wrap_method(ChatCanvas, "render_line", "line")

    from src.ui.tui import canvas as canvas_mod

    for mod in (lazy, canvas_mod):
        _wrap_func(mod, "_build_strip", "strip")
        _wrap_func(mod, "_wrap_continuation", "wrap")
    _wrap_func(lazy, "_pad_line", "pad")
    _wrap_func(lazy, "_line_fill", "pad")
    _wrap_func(render_mod, "_edit_hunk", "hunk")
    return m
