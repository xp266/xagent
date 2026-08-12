from __future__ import annotations

import os

STRESS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(STRESS_DIR, "data")
LOG_DIR = os.path.join(STRESS_DIR, "logs")
SCRATCH_DIR = os.path.join(STRESS_DIR, "scratch")


def scratch_path(name: str) -> str:
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    return os.path.join(SCRATCH_DIR, name)


def write_fixture() -> str:
    lines = ["# stress fixture file", ""]
    for i in range(200):
        lines.append(f"def stress_fn_{i}(arg_{i}):")
        lines.append(f"    return {i}")
        lines.append("")
    content = "\n".join(lines)
    path = scratch_path("stress_file.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


THINKING_CHUNKS = [
    "Let me analyze the request step by step.\n",
    "First, I need to understand what the user wants. The request involves multiple parts that need to be decomposed into smaller sub-problems.\n",
    "The key insight is that the `render` pipeline operates on `Content` objects with `Span` metadata for styling.\n",
    "```python\nfor block in canvas._blocks:\n    block.height(width)\n    total += len(block._lines)\n```\n",
    "Each block keeps its own line cache, but any update invalidates the whole block's strip cache.\n",
    "```bash\nls -la src/ui/tui/\ngrep -n \"_build\" src/ui/tui/canvas.py | head -20\nwc -l src/ui/tui/*.py\n```\n",
    "```json\n{\n  \"provider\": \"mock\",\n  \"model\": \"step-3.7-flash\",\n  \"stream\": true,\n  \"usage\": {\"prompt_tokens\": 1000, \"completion_tokens\": 80},\n  \"flags\": [\"include_usage\", \"stream_options\"]\n}\n```\n",
    "When thinking about performance, the hottest path is streaming rendering: every tick rebuilds the whole markdown output object, including every line and span.\n",
    "The `_bump` method triggers `_rebuild_offsets` and a full layout refresh on every update.\n",
    "```typescript\nexport function flushBlock(block: CanvasBlock): void {\n    const width = block.owner?.size.width ?? 80;\n    block.rebuild(width);\n    block.owner?.refresh();\n}\n```\n",
    "```go\nfunc Flush(cur *TurnState) {\n\tif cur.Reply != nil {\n\t\tcur.Reply.Update(cur.Md.Render())\n\t}\n}\n```\n",
    "```python\ndef measure(blocks: list[CanvasBlock], width: int) -> tuple[int, float]:\n    total = 0\n    start = time.monotonic()\n    for b in blocks:\n        total += b.height(width)\n    elapsed = time.monotonic() - start\n    return total, elapsed\n```\n",
    "```\nStreamMarkdown.render()\n  -> Content.assemble(parts)\n  -> CanvasBlock.update()\n  -> _bump()\n  -> refresh(layout=True)\n```\n",
    "A possible optimization is to cache the rendered lines incrementally, appending only newly committed lines.\n",
    "Tables are re-rendered wholesale whenever a new row arrives, which can be quadratic in table size.\n",
    "waiting for the next token to arrive before continuing the analysis of the streaming pipeline.\n",
    "I will structure the answer into three sections: correctness, performance, and recommendations.\n",
]

REPLY_CHUNKS = [
    "# Streaming Pipeline Analysis\n",
    "## Refresh drivers\n",
    "The TUI refreshes from a single **30fps timer** plus event-driven updates:\n",
    "- `_tick_animations` runs at 30fps even when idle\n",
    "- spinners tick at 10Hz\n",
    "- streaming flush is gated at 16ms (reply) / 80ms (tools)\n",
    "\n",
    "## Key findings\n",
    "1. `StreamMarkdown.render()` rebuilds **all lines every tick**\n",
    "2. `tool_render` re-wraps the whole accumulated result every 80ms\n",
    "3. `_edit_hunk` re-reads the file from disk on every render\n",
    "\n",
    "| path | rate | cost |\n",
    "| --- | --- | --- |\n",
    "| reply | ~30fps | O(n) per tick |\n",
    "| tools | 12.5fps | O(output) per tick |\n",
    "| status | 30fps idle | small |\n",
    "\n",
    "```python\ndef flush(cur):\n    md = cur.get(\"_reply_md\")\n    if md is None:\n        md = StreamMarkdown(bg=False)\n    text = cur[\"reply_text\"]\n    prev = cur.get(\"_reply_md_len\", 0)\n    if len(text) > prev:\n        md.feed(text[prev:])\n        cur[\"_reply_md_len\"] = len(text)\n    cur[\"reply\"].update(md.render())\n```\n",
    "```bash\n$ seq 1 10\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n```\n",
    "```json\n{\"blocks\": 42, \"lines\": 7808, \"rss_mb\": 148.5, \"jank\": 43}\n```\n",
    "```sql\nSELECT block_id, SUM(line_count) AS total_lines\nFROM canvas_blocks\nWHERE owner = 'chat-canvas'\nGROUP BY block_id\nORDER BY total_lines DESC;\n```\n",
    "```python\ndef large_synthetic_function(blocks, width):\n    acc = 0.0\n    for idx, block in enumerate(blocks):\n        h = block.height(width)\n        acc += h * (idx + 1)\n        if h > 100:\n            acc -= h // 2\n    return acc\n\n\ndef another_helper(items):\n    return sorted(items, key=lambda x: len(x._lines), reverse=True)\n\n\nif __name__ == \"__main__\":\n    result = large_synthetic_function([], 80)\n    print(f\"result={result:.2f}\")\n```\n",
    "## Recommendations\n",
    "> Apply the same incremental caching used by `ThinkingMarkdown` to `StreamMarkdown`.\n",
    "> Skip the `update()` call entirely when no new data arrived.\n",
    "\n",
    "- gate reply rendering on new data\n",
    "- cap tool preview lines\n",
    "- move session persistence off the event loop\n",
    "\n",
    "See [the report](https://example.com/report) for details. Inline `code` and **bold** and *italic* are handled by `_INLINE_RE`.\n",
    "\n",
    "---\n",
    "\n",
    "## Streaming hot path\n",
    "Streaming rendering is the hottest path in the whole pipeline: a 30fps timer drives `_flush_streaming_content`, and every call rebuilds the full markdown `Content` object, including all line text and span metadata, causing heavy object allocation and GC pressure.\n",
]

def _cycle_blocks(chunks: list[str], target: int) -> str:
    parts = []
    total = 0
    while total < target:
        for chunk in chunks:
            parts.append(chunk)
            total += len(chunk)
            if total >= target:
                break
    return "".join(parts)


def mixed_think_block() -> str:
    return _cycle_blocks(THINKING_CHUNKS, int(os.environ.get("XAGENT_MIX_THINK_CHARS", "16384")))


def mixed_reply_block() -> str:
    return _cycle_blocks(REPLY_CHUNKS, int(os.environ.get("XAGENT_MIX_REPLY_CHARS", "32768")))


_BIG_PARA = (
    "This is a stress paragraph used to build a very large reply. "
    "It contains `inline code`, **bold text**, *italic text*, and a [link](https://example.com). "
    "The paragraph repeats many times so the total reply grows well beyond 100KB, "
    "which forces the streaming renderer to rebuild a huge Content object on every tick. "
)


def big_reply() -> str:
    parts = ["# Very Large Reply\n", "## Introduction\n"]
    for i in range(600):
        parts.append(f"### Section {i}\n")
        parts.append(_BIG_PARA + _BIG_PARA + "\n")
        parts.append("```python\ndef section_%d():\n    return %d\n```\n" % (i, i))
    parts.append("## Conclusion\n")
    parts.append("Done.\n")
    return "".join(parts)


def write_content() -> str:
    lines = ["# stress fixture file", ""]
    for i in range(200):
        lines.append(f"def stress_fn_{i}(arg_{i}):")
        lines.append(f"    return {i}")
        lines.append("")
    return "\n".join(lines)


def edit_args() -> dict:
    return {
        "filePath": scratch_path("stress_file.py"),
        "oldString": "def stress_fn_5(arg_5):\n    return 5",
        "newString": "def stress_fn_5(arg_5):\n    return 500\n\n# edited by stress",
    }


def read_args() -> dict:
    return {"path": scratch_path("stress_file.py"), "offset": 1, "limit": 400}


def grep_args() -> dict:
    return {"pattern": "def stress_fn", "path": SCRATCH_DIR}


def glob_args() -> dict:
    return {"pattern": "**/*", "path": SCRATCH_DIR}


def bash_args() -> dict:
    return {"command": "seq 1 800"}


def compact_summary() -> str:
    lines = [
        "<conversation_summary>",
        "## Objective",
        "- Analyze and optimize the TUI streaming render pipeline so long replies stay smooth.",
        "",
        "## Decisions & Constraints",
        "- Keep the canvas block model; avoid full re-renders.",
        "- No comments or docstrings in src/ (repo convention).",
        "",
        "## Progress",
        "### Completed",
        "- Incremental `StreamMarkdown` feed with open-fence re-render.",
        "- Throttled flush cooldowns (16ms reply / 80ms tools).",
        "- `ChatCanvas` strip cache keyed by render generation.",
        "",
        "### In Progress",
        "- Measuring per-tick cost of `Content.assemble` under 30fps.",
        "",
        "### Blocked",
        "- (none)",
        "",
        "## Files & Key Context",
        "- src/ui/tui/markdown.py: `StreamMarkdown.feed/render`",
        "- src/ui/tui/canvas.py: `CanvasBlock._build`, strip cache",
        "- src/ui/tui/turnrender.py: `_flush_streaming_content`",
        "- `wc -l src/ui/tui/*.py`",
        "",
        "## User Messages",
        "- \"Let me see how this snake game runs\"",
        "- \"Compact the context, keeping the rendering pipeline optimization plan\"",
        "",
        "## Next Move",
        "1. Run the stress harness and check `build_avg_ms` and `md_avg_ms`.",
        "2. If `md_avg_ms` stays high, cache the assembled `Content` per line count.",
        "</conversation_summary>",
    ]
    return "\n".join(lines)
