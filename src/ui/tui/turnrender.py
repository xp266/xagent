from __future__ import annotations

import asyncio
import time

from src.agent import run_session_turn
from src.agent.turn import RETRY_LIMIT
from src.types.events import StreamEvent
from src.ui.tui.canvas import CanvasBlock
from src.ui.tui.colors import _TOOL_ERROR, _USER_BG
from src.ui.tui.markdown import StreamMarkdown
from src.ui.tui.render import (
    _cap_tool, block_tool, clean_result, code_tool, fmt_duration, is_error_result,
    read_line_start, render_tool_markdown, render_tool_markdown_lines, tool_block, tool_markdown, tool_num_width, tool_render,
)
from src.ui.tui.thinking import ThinkingMarkdown
from src.ui.tui.streaming import StreamArgs
from src.utils.config import get_config

_RENDER_COOLDOWN = 0.016
_TOOL_RENDER_COOLDOWN = 0.08
_THINKING_RENDER_INTERVAL = 0.1
_REPLY_RENDER_INTERVAL = 0.1
_COMPACT_RENDER_INTERVAL = 0.08


def new_turn_state() -> dict:
    return {
        "steps": 0,
        "reasoning_text": "",
        "reply_text": "",
        "thinking": None,
        "reply": None,
        "tools": {},
        "tool_buffers": {},
        "waiting": None,
        "retry": None,
        "last_stream_render": 0.0,
        "last_tool_render": 0.0,
        "_thinking_md_len": 0,
        "_thinking_render": 0.0,
        "_reply_md_len": 0,
    }


class TurnRenderMixin:
    def _flush_streaming_content(self, force: bool = False) -> None:
        cur = self._current
        if cur is None:
            return
        now = time.monotonic()
        if not force and now - cur["last_stream_render"] < _RENDER_COOLDOWN:
            return
        cur["last_stream_render"] = now

        if cur["thinking"] is not None and cur.get("reasoning_text"):
            md = cur.get("_thinking_md")
            if md is None:
                md = ThinkingMarkdown()
                cur["_thinking_md"] = md
            prev_len = cur.get("_thinking_md_len", 0)
            text = cur["reasoning_text"]
            if len(text) > prev_len:
                md.feed(text[prev_len:])
                cur["_thinking_md_len"] = len(text)
            if force or now - cur.get("_thinking_render", 0.0) >= _THINKING_RENDER_INTERVAL:
                cur["_thinking_render"] = now
                cur["thinking"].update(md.render())

        if cur["reply"] is not None and cur.get("reply_text"):
            md = cur.get("_reply_md")
            if md is None:
                md = StreamMarkdown(bg=False)
                cur["_reply_md"] = md
            prev_len = cur.get("_reply_md_len", 0)
            text = cur["reply_text"]
            if len(text) > prev_len:
                md.feed(text[prev_len:])
                cur["_reply_md_len"] = len(text)
                if force or now - cur.get("_reply_render", 0.0) >= _REPLY_RENDER_INTERVAL:
                    cur["_reply_render"] = now
                    cur["reply"].update(md.render())
            elif force:
                cur["reply"].update(md.render())

        compact = cur.get("compact")
        if compact is not None and cur.get("compact_text"):
            md = cur.get("_compact_md")
            if md is None:
                md = StreamMarkdown(bg=False)
                cur["_compact_md"] = md
            prev_len = cur.get("_compact_md_len", 0)
            text = cur["compact_text"]
            if len(text) > prev_len:
                md.feed(text[prev_len:])
                cur["_compact_md_len"] = len(text)
            if force or now - cur.get("_compact_render", 0.0) >= _COMPACT_RENDER_INTERVAL:
                cur["_compact_render"] = now
                compact.update(md.render())

        pending_tool = False
        for tc_id, tool in cur["tools"].items():
            if tool.get("done"):
                continue
            info = cur["tool_buffers"].get(tc_id)
            if info is None:
                continue
            if force or len(info["raw"]) != info.get("_last_len", 0):
                pending_tool = True
                break

        if pending_tool and not force and now - cur.get("last_tool_render", 0.0) < _TOOL_RENDER_COOLDOWN:
            return

        if pending_tool:
            cur["last_tool_render"] = now

        for tc_id, tool in cur["tools"].items():
            if tool.get("done"):
                continue
            info = cur["tool_buffers"].get(tc_id)
            if info is None:
                continue
            raw_len = len(info["raw"])
            if not force and raw_len == info.get("_last_len", 0):
                continue
            info["_last_len"] = raw_len
            sa = info.get("sa")
            if sa is None:
                sa = StreamArgs(info["name"])
                info["sa"] = sa
            args = sa.feed(info["raw"])
            name = info["name"]
            if block_tool(name):
                title, body = tool_block(name, args, None, False, preview=True)
                self._set_tool_title(tool, title)
                tool["block"].update(render_tool_markdown(body, open=True))
            else:
                title, t = tool_render(name, args, None, False, preview=True)
                title = title.strip() or _cap_tool(name)
                self._set_tool_title(tool, title)
                tool["block"].update(t)

        self._scroll_end()

    def _reset_turn_render(self) -> None:
        cur = self._current
        if cur is None:
            return
        canvas = self._canvas()
        thinking = cur.get("thinking")
        if thinking is not None:
            try:
                canvas.remove(thinking)
            except Exception:
                pass
            cur["thinking"] = None
        cur["reasoning_text"] = ""
        cur.pop("_thinking_md", None)
        cur["_thinking_md_len"] = 0
        reply = cur.get("reply")
        if reply is not None:
            try:
                canvas.remove(reply)
            except Exception:
                pass
            cur["reply"] = None
            cur["reply_text"] = ""
        self._stop_all_spinners()
        self._hide_waiting()
        cur.pop("_reply_md", None)
        cur["_reply_md_len"] = 0
        cur.pop("_reply_render", None)

    def _show_retry(self, error: str, delay: float, attempt: int) -> None:
        cur = self._current
        if cur is None:
            return
        self._reset_turn_render()
        deadline = time.monotonic() + delay
        retry = cur.get("retry")
        if retry is None:
            block = self._append_block(kind="error")
            retry = {"block": block}
            cur["retry"] = retry
        retry["deadline"] = deadline
        retry["error"] = error
        retry["attempt"] = attempt
        retry.pop("last_text", None)
        self._render_retry(retry)
        self._scroll_end()

    def _tick_retry(self) -> None:
        cur = self._current
        if cur is None:
            return
        retry = cur.get("retry")
        if retry is not None:
            self._render_retry(retry)

    def _render_retry(self, retry) -> None:
        remaining = retry["deadline"] - time.monotonic()
        if remaining <= 0:
            text = f"Request failed: {retry['error']}\nRetrying... (attempt {retry['attempt']}/{RETRY_LIMIT})"
            self._ensure_waiting()
        else:
            text = f"Request failed: {retry['error']}\nRetrying in {int(remaining) + 1}s (attempt {retry['attempt']}/{RETRY_LIMIT})"
        if retry.get("last_text") != text:
            retry["last_text"] = text
            retry["block"].update(text)

    def _clear_retry(self) -> None:
        cur = self._current
        if cur is None:
            return
        retry = cur.get("retry")
        if retry is not None:
            cur["retry"] = None
            try:
                self._canvas().remove(retry["block"])
            except Exception:
                pass

    def _ensure_block(self, key: str, **kwargs) -> CanvasBlock:
        cur = self._current
        block = cur.get(key)
        if block is None:
            block = self._append_block(**kwargs)
            cur[key] = block
        return block

    def _ensure_thinking(self) -> CanvasBlock:
        block = self._ensure_block("thinking", kind="thinking")
        block.arrow_hidden = True
        self._start_spinner(block, "Thinking")
        return block

    def _ensure_waiting(self) -> None:
        if self._current is None or self._current.get("waiting") is not None:
            return
        block = self._ensure_block("waiting", kind="waiting")
        self._start_spinner(block, "Waiting for response...")

    def _hide_waiting(self) -> None:
        cur = self._current
        if cur is None:
            return
        waiting = cur.get("waiting")
        if waiting is None:
            return
        self._stop_spinner(waiting, "Waiting for response...", restore_arrow=False)
        cur["waiting"] = None
        try:
            self._canvas().remove(waiting)
        except Exception:
            pass

    def _remove_empty_thinking(self) -> None:
        cur = self._current
        if cur is None or cur.get("thinking") is None:
            return
        if cur.get("reasoning_text"):
            return
        self._stop_spinner(cur["thinking"], "Thinking")
        col = cur.get("thinking")
        if col is not None:
            self._canvas().remove(col)
        cur["thinking"] = None

    def _ensure_reply(self) -> CanvasBlock:
        return self._ensure_block("reply", kind="reply")

    def _add_tool_streaming(self, tc_id: str, name: str) -> None:
        tool = {
            "name": name,
            "title": _cap_tool(name),
            "spinning": False,
            "done": False,
            "input": {},
            "block": None,
        }
        if block_tool(name):
            block = self._append_block(kind="tool-block", title=name)
            tool["block"] = block
        else:
            title, t = tool_render(name, {}, None, False)
            block = self._append_block(
                kind="tool",
                title=title,
                collapsed=name != "bash",
                content_bg=_USER_BG if name == "bash" else None,
                content_pad_left=0 if name == "read" else None,
            )
            tool["title"] = title
            tool["block"] = block
            block.update(t)
        self._current["tools"][tc_id] = tool
        self._current["tool_buffers"][tc_id] = {"name": name, "raw": ""}
        if not block_tool(name):
            self._start_tool_spinner(tool)

    def _set_tool_title(self, tool, title: str) -> None:
        if tool["title"] != title:
            tool["title"] = title
            self._render_spinner(tool["block"], lambda: tool["title"])

    def _mark_tool_error(self, block) -> None:
        block.title_style = _TOOL_ERROR
        block._strips = []
        block._key = ()

    def _finalize_tool_stream(self, tc_id: str, name: str, args: dict) -> None:
        if tc_id not in self._current["tools"]:
            self._add_tool_streaming(tc_id, name)
        tool = self._current["tools"].get(tc_id)
        if tool is None:
            return
        info = self._current["tool_buffers"].setdefault(tc_id, {"name": name, "raw": ""})
        info["name"] = name
        tool["name"] = name
        tool["input"] = args
        if block_tool(name):
            title, body = tool_block(name, args, None, False)
            self._set_tool_title(tool, title)
            tool["block"].pad_left = tool_num_width(name, args) + 1
            tool["block"].update(render_tool_markdown(body, numbered=(name == "write")))
        else:
            title, t = tool_render(name, args, None, False)
            self._set_tool_title(tool, title)
            tool["block"].update(t)

    def _set_tool_result(self, tc_id: str, name: str, result: str, is_error: bool) -> None:
        tool = self._current["tools"].get(tc_id)
        if tool is None:
            return
        self._stop_tool_spinner(tool)
        tool["name"] = name
        if block_tool(name):
            title, body = tool_block(name, tool["input"], result, is_error)
            self._set_tool_title(tool, title)
            tool["block"].set_title(title)
            tool["block"].pad_left = tool_num_width(name, tool["input"], result, is_error) + 1
            tool["block"].update(render_tool_markdown(body, numbered=(name == "write")))
            if is_error:
                self._mark_tool_error(tool["block"])
            tool["done"] = True
            return
        if code_tool(name):
            md = tool_markdown(name, tool["input"], result, is_error)
            if md is not None:
                m_title, m = md
                self._set_tool_title(tool, m_title)
                tool["block"].pad_left = tool_num_width(name, tool["input"], result, is_error) + 1
                tool["block"].content_pad_left = 0
                tool["block"].update_lines(render_tool_markdown_lines(m, numbered=(name == "read"), line_number_start=read_line_start(result, tool["input"])))
                if is_error:
                    self._mark_tool_error(tool["block"])
                tool["done"] = True
                return
        title, t = tool_render(name, tool["input"], result, is_error)
        self._set_tool_title(tool, title)
        tool["block"].update(t)
        if is_error:
            self._mark_tool_error(tool["block"])
        tool["done"] = True

    def _add_summary(self, elapsed: float) -> None:
        cfg = get_config()
        model = cfg.model or "?"
        summary = f"{model} - {fmt_duration(elapsed)}"
        block = self._append_block(kind="summary")
        block.update(summary)
        self._scroll_end()

    def _handle_event(self, event: StreamEvent) -> None:
        cur = self._current
        if cur is None:
            return
        t = event.type
        if t in ("reasoning-start", "step-start", "text-start", "tool-input-start"):
            self._clear_retry()
        if t == "reasoning-start":
            cur["reasoning_text"] = ""
            cur.pop("_thinking_md", None)
            cur["_thinking_md_len"] = 0
            self._hide_waiting()
            self._ensure_thinking()
        elif t == "reasoning-delta":
            cur["reasoning_text"] += event.data
            if cur["thinking"] is None:
                self._ensure_thinking()
                self._hide_waiting()
            self._flush_streaming_content()
        elif t == "reasoning-end":
            self._flush_streaming_content(force=True)
            md = cur.get("_thinking_md")
            if md is not None:
                md.finish()
                block = cur.get("thinking")
                if block is not None:
                    block.update(md.render())
            self._stop_spinner(cur["thinking"], "Thinking")
            cur["thinking"] = None
            self._ensure_waiting()
        elif t == "text-start":
            self._flush_streaming_content(force=True)
            self._remove_empty_thinking()
            self._hide_waiting()
            cur["reply"] = None
            cur["reply_text"] = ""
            cur.pop("_reply_md", None)
            cur["_reply_md_len"] = 0
            cur.pop("_reply_render", None)
            self._ensure_reply()
        elif t == "text-delta":
            cur["reply_text"] += event.data
            if cur["reply"] is None:
                self._ensure_reply()
            self._flush_streaming_content()
        elif t == "tool-input-start":
            self._hide_waiting()
            self._remove_empty_thinking()
            data = event.data
            self._add_tool_streaming(data["id"], data.get("name", ""))
            self._scroll_end()
        elif t == "tool-input-delta":
            data = event.data
            info = self._current["tool_buffers"].get(data["id"])
            if info is not None:
                info["raw"] += data.get("delta", "")
                self._flush_streaming_content()
        elif t == "tool-input-end":
            self._flush_streaming_content(force=True)
        elif t == "tool-call":
            data = event.data
            self._finalize_tool_stream(data["id"], data["name"], data.get("input", {}))
            self._scroll_end()
        elif t == "tool-result":
            data = event.data
            result = clean_result(data["name"], data.get("result", ""))
            is_error = data.get("is_error", is_error_result(data["name"], result))
            self._set_tool_result(data["id"], data["name"], result, is_error)
            self._scroll_end()
            self._ensure_waiting()
        elif t == "tool-error":
            data = event.data
            self._set_tool_result(data["id"], data["name"], data.get("error", ""), True)
            self._scroll_end()
            self._ensure_waiting()
        elif t == "step-start":
            self._flush_streaming_content(force=True)
            self._stop_all_spinners()
            self._ensure_waiting()
        elif t == "step-finish":
            usage = event.data.get("usage", {}) or {}
            cur["steps"] += 1
            if usage:
                pt = usage.get("prompt_tokens", 0)
                if pt > 0:
                    self._ctx_usage_tokens = pt
            self._update_status()
        elif t == "provider-error":
            error = event.data.get("error", "Unknown error")
            retry = cur.get("retry")
            if retry is not None:
                cur["retry"] = None
                retry["block"].update(f"Request failed after {RETRY_LIMIT} retries: {error}")
            else:
                self._append_error(error)
        elif t == "retry-schedule":
            data = event.data or {}
            self._show_retry(
                data.get("error", "Unknown error"),
                float(data.get("delay", 5)),
                int(data.get("attempt", 1)),
            )
        elif t == "compacting":
            self._hide_waiting()
            self._remove_empty_thinking()
            block = self._ensure_block("compact", kind="compact")
            self._start_spinner(block, "Compressing context...")
            cur["compact_text"] = ""
            cur.pop("_compact_md", None)
            cur["_compact_md_len"] = 0
            cur["_compact_render"] = 0.0
            self._scroll_end()
        elif t == "compact-delta":
            cur["compact_text"] = cur.get("compact_text", "") + event.data
            self._flush_streaming_content()
        elif t == "compacted":
            self._flush_streaming_content(force=True)
            md = cur.get("_compact_md")
            block = cur.get("compact")
            if md is not None:
                md.finish()
                if block is not None:
                    block.update(md.render())
            if block is not None:
                self._stop_spinner(block, "Compression complete")
                block.set_title("Compression complete")
            self._scroll_end()
        elif t == "compact-error":
            block = cur.get("compact")
            if block is not None:
                self._stop_spinner(block, "Compression failed")
                block.set_title("Compression failed")
                block.update("Compression failed, context unchanged")
            self._scroll_end()

    async def _turn_worker(self, text: str) -> None:
        from src.agent.cancel import set_turn_task
        start = time.monotonic()
        try:
            async for event in run_session_turn(self._session, text):
                if self._exit or self._closing:
                    break
                self._handle_event(event)
        except asyncio.CancelledError:
            if self._exit or self._closing:
                raise
            cur = self._current
            if cur is not None:
                self._clear_retry()
                self._hide_waiting()
                cur["interrupted"] = True
                if self._deferred is None:
                    block = self._append_block(kind="summary")
                    block.update("Turn interrupted by user")
                    self._scroll_end()
        except Exception as e:
            if self._exit or self._closing:
                return
            self._log_error()
            self._append_error(f"{type(e).__name__}: {e}")
        finally:
            elapsed = time.monotonic() - start
            if not (self._exit or self._closing):
                self._finalize_turn(elapsed)
            set_turn_task(None)

    def _finalize_turn(self, elapsed: float) -> None:
        cur = self._current
        if cur is not None:
            self._flush_streaming_content(force=True)
            tmd = cur.get("_thinking_md")
            if tmd is not None:
                tmd.finish()
                tblock = cur.get("thinking")
                if tblock is not None:
                    tblock.update(tmd.render())
            md = cur.get("_reply_md")
            if md is not None:
                md.finish()
            cmd = cur.get("_compact_md")
            if cmd is not None:
                cmd.finish()
        self._flush_streaming_content(force=True)
        if cur and cur["steps"] > 0 and not cur.get("interrupted"):
            self._add_summary(elapsed)
        self._remove_empty_thinking()
        self._hide_waiting()
        self._stop_all_spinners()
        self._busy = False
        self._input().busy = False
        self._current = None
        self._update_status()
        self._scroll_end()
        deferred = self._deferred
        self._deferred = None
        if deferred is not None:
            deferred()
            return
        self._input().focus()
