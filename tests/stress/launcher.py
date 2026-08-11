from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
os.environ["XAGENT_DATA_DIR"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.environ.setdefault("XAGENT_NO_CATALOG_REFRESH", "1")

from tests.stress import content, fake_provider, metrics as metrics_mod
from tests.stress.content import DATA_DIR, LOG_DIR, SCRATCH_DIR

MODEL = "step-3.7-flash"

_METRICS = [
    ("fps", "fps"),
    ("gap_ms", "tick gap avg ms"),
    ("gap_max_ms", "tick gap max ms"),
    ("tick_ms", "tick avg ms"),
    ("flush_avg_ms", "flush avg ms"),
    ("event_avg_ms", "event avg ms"),
    ("md_avg_ms", "StreamMarkdown.render avg ms"),
    ("think_avg_ms", "ThinkingMarkdown.render avg ms"),
    ("build_avg_ms", "CanvasBlock._build avg ms"),
    ("rebuild_avg_ms", "CanvasBlock._rebuild avg ms"),
    ("strip_avg_ms", "_build_strip avg ms"),
    ("pad_avg_ms", "_pad_line avg ms"),
    ("wrap_avg_ms", "_wrap_continuation avg ms"),
    ("line_avg_ms", "render_line avg ms"),
    ("status_avg_ms", "_update_status avg ms"),
    ("istatus_avg_ms", "_update_input_status avg ms"),
    ("hunk_avg_ms", "_edit_hunk avg ms"),
    ("compact_avg_ms", "compact avg ms"),
    ("rss_mb", "rss mb"),
]


def _write_config(port: int) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "active_provider": "custom:stress",
        "active_model": MODEL,
        "reasoning_effort": {},
        "model_contexts": {MODEL: 150000},
        "providers": {
            "custom:stress": {
                "name": "Stress Mock",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "api_key": "sk-stress-test",
                "models": [MODEL],
                "selected_models": [MODEL],
            }
        },
        "mcp_servers": {},
    }
    path = os.path.join(DATA_DIR, "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p / 100 * len(ordered)))
    return ordered[idx]


def summarize(log_path: str) -> None:
    lines = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
    except OSError as e:
        print(f"[stress] cannot read log {log_path}: {e}")
        return
    if not lines:
        print("[stress] empty log")
        return
    busy = [r for r in lines if r.get("busy")]
    idle = [r for r in lines if r.get("busy") == 0]
    duration = lines[-1]["sec"] - lines[0]["sec"]
    scenarios = {}
    for r in lines:
        s = r.get("scenario") or ""
        if s:
            scenarios[s] = scenarios.get(s, 0) + 1
    print(f"\n=== stress summary: {os.path.basename(log_path)} ===")
    print(f"duration {duration:.1f}s  lines {len(lines)}  busy {len(busy)}  idle {len(idle)}  scenarios {sorted(scenarios.items())}")

    def _row(name, values, digits=3):
        if not values:
            print(f"{name:<34} no data")
            return
        print(
            f"{name:<34} n={len(values):>6} avg={statistics.fmean(values):>{digits + 4}.{digits}f} "
            f"p50={_pct(values, 50):>{digits + 4}.{digits}f} p95={_pct(values, 95):>{digits + 4}.{digits}f} "
            f"max={max(values):>{digits + 4}.{digits}f}"
        )

    for key, label in _METRICS:
        _row(label + " (all)", [r.get(key, 0) for r in lines])
    for key, label in _METRICS:
        if key in ("fps", "gap_ms", "gap_max_ms", "tick_ms"):
            continue
        _row(label + " (busy)", [r.get(key, 0) for r in busy])
    for key, label in _METRICS:
        if key in ("fps", "gap_ms", "gap_max_ms", "tick_ms"):
            continue
        _row(label + " (idle)", [r.get(key, 0) for r in idle])
    totals = {
        "jank": sum(r.get("jank", 0) for r in lines),
        "event_jank": sum(r.get("event_jank", 0) for r in lines),
        "blocks_end": lines[-1].get("blocks", 0),
        "lines_end": lines[-1].get("lines", 0),
        "reply_chars_end": lines[-1].get("reply_chars", 0),
        "rss_max": max(r.get("rss_mb", 0) for r in lines),
        "rss_end": lines[-1].get("rss_mb", 0),
        "rss_start": lines[0].get("rss_mb", 0),
    }
    print(f"jank(>100ms tick gap)={totals['jank']}  event_jank={totals['event_jank']}")
    print(
        f"blocks_end={totals['blocks_end']} lines_end={totals['lines_end']} "
        f"reply_chars_end={totals['reply_chars_end']}"
    )
    print(f"rss start={totals['rss_start']:.1f}MB max={totals['rss_max']:.1f}MB end={totals['rss_end']:.1f}MB")


def main() -> int:
    ap = argparse.ArgumentParser(description="xagent TUI stress harness (mock provider, no tokens)")
    ap.add_argument("--summarize", metavar="LOG", help="aggregate a jsonl log and print stats")
    ap.add_argument("--rate-ms", type=float, default=8.0, help="mock token pacing ms per delta")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return 0
    os.environ["XAGENT_RATE_MS"] = str(args.rate_ms)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    content.write_fixture()
    server = fake_provider.start()
    port = server.server_address[1]
    _write_config(port)
    run_name = time.strftime("run_%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, run_name + ".jsonl")
    metrics = metrics_mod.install(log_path)
    print(f"[stress] mock provider  ->  http://127.0.0.1:{port}/v1")
    print(f"[stress] metrics log    ->  {log_path}")
    print(f"[stress] isolated data  ->  {DATA_DIR}")
    print("[stress] scenarios: thinking | reply | replong | storm | tools | mixed | retry | error | compact | <anything>")
    try:
        from src.ui.tui.app import run_tui
        run_tui()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        metrics.close()
    summarize(log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
