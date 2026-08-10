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
    "考虑性能时，最关键的路径是流式渲染：每个 tick 都会重建整个 markdown 输出对象，包含所有行与 span。\n",
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
    "## 中文测试段落\n",
    "流式渲染是全链路最热的路径：30fps 定时器驱动 `_flush_streaming_content`，每次调用都会重建完整的 markdown `Content` 对象，其中包含所有行文本与 span 元数据，产生大量对象分配与 GC 压力。\n",
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
