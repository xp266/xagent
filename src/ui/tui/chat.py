from __future__ import annotations

import json
import os

from src.agent import name_session_from_first_message
from src.ui.tui.colors import _TOOL_ERROR, _TOOL_HEADER, _TOOL_TITLE, _USER_BG
from src.ui.tui.markdown import render_markdown_lines
from src.ui.tui.render import (
    block_tool, clean_result, fmt_duration, is_error_result, read_line_start,
    render_tool_markdown_lines, tool_block, tool_markdown, tool_num_width, tool_render,
)
from src.ui.tui.thinking import render_thinking_markdown_lines
from src.utils.config import get_config

MAX_VISIBLE_MESSAGES = 100
MAX_RENDER_LINES = 6000
MAX_CANVAS_BLOCKS = 200
TRIM_SLACK = 20


def _msg_render_lines(msg: dict) -> int:
    total = 0
    if msg.get("role") == "assistant":
        for key in ("content", "reasoning_content"):
            text = msg.get(key) or ""
            total += len(text) // 80 + text.count("\n") + 1
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            args = fn.get("arguments") or ""
            total += len(args) // 80 + 1
    else:
        text = msg.get("content")
        if isinstance(text, str):
            total += len(text) // 80 + text.count("\n") + 1
    return total


class ChatMixin:
    def _apply_name(self, s: object, name: str) -> None:
        if not name or name == "New Session":
            return
        s.name = name
        self._sm.rename(s.id, name)
        if s is self._session:
            self._update_status()

    async def _name_worker(self, s: object, first_message: str) -> None:
        try:
            name = await name_session_from_first_message(s, first_message)
        except Exception:
            name = None
        if name and not self._closing:
            self._apply_name(s, name)

    def _defer_switch(self, fn) -> None:
        self._deferred = fn
        from src.agent.cancel import cancel
        cancel()

    def _new_chat(self) -> None:
        if self._busy:
            self._defer_switch(self._new_chat)
            return
        if os.path.isdir(self._launch_dir):
            os.chdir(self._launch_dir)
        self._project = self._launch_dir
        self._session = self._sm.create(path=self._project, persist=False)
        self._ctx_usage_tokens = 0
        self._last_usage = None
        self._win_msgs = MAX_VISIBLE_MESSAGES
        self._win_lines = MAX_RENDER_LINES
        self._hidden_msgs = 0
        self._clear_chat_messages()
        self._show_logo()
        self._update_status()
        self._scroll_end()
        self._input().focus()

    def _switch_session(self, code: str) -> None:
        s = self._sm.get(code.strip())
        if s is None:
            self._append_error(f"Session not found: {code.strip()}")
            return
        if self._busy:
            self._defer_switch(lambda: self._switch_session(code))
            return
        self._sm.current = s.id
        self._session = s
        target = (s.path or "").strip()
        if os.path.isdir(target):
            os.chdir(target)
            self._project = os.getcwd()
        from src.agent.compact import estimate_context_usage

        self._ctx_usage_tokens = estimate_context_usage(s)
        self._last_usage = None
        self._win_msgs = MAX_VISIBLE_MESSAGES
        self._win_lines = MAX_RENDER_LINES
        self._hidden_msgs = 0
        self._render_messages()
        self._update_status()
        self._input().focus()

    def _render_messages(self, max_messages: int = 0, max_lines: int = 0, scroll_to_end: bool = True) -> None:
        self._clear_chat_messages()
        all_messages = self._session.messages
        tool_results = {}
        tool_errors = {}
        for msg in all_messages:
            if msg.get("role") == "tool":
                tool_results[msg["tool_call_id"]] = msg["content"]
                tool_errors[msg["tool_call_id"]] = bool(msg.get("is_error"))

        renderable = [m for m in all_messages if m.get("role") != "system"]
        max_messages = min(max_messages or MAX_VISIBLE_MESSAGES, len(renderable))
        max_lines = max_lines or MAX_RENDER_LINES

        window = []
        cost = 0
        for msg in reversed(renderable):
            if len(window) >= max_messages:
                break
            window.append(msg)
            cost += _msg_render_lines(msg)
            if cost > max_lines:
                break
        messages = window[::-1]
        hidden = len(renderable) - len(messages)

        canvas = self._canvas()
        canvas._begin_bulk()
        try:
            if hidden > 0:
                div_block = self._append_block(
                    kind="divider",
                    title=f"↕ {hidden} earlier messages hidden (click to expand)",
                )
                div_block.action = self._expand_window

            for idx, msg in enumerate(messages):
                role = msg.get("role", "")
                if role == "system":
                    continue
                if role == "user":
                    meta = msg.get("_meta") or {}
                    if meta.get("compacted"):
                        content = msg.get("content", "") or ""
                        content = content.replace("<conversation_summary>", "").replace("</conversation_summary>", "").strip()
                        block = self._append_block(kind="compact-summary")
                        block.update_lines(render_markdown_lines(content, bg=False))
                        continue
                    block = self._append_block(kind="user")
                    block.update(msg.get("content", ""))
                    continue
                if role == "assistant":
                    reasoning = msg.get("reasoning_content", "") or ""
                    content = msg.get("content", "") or ""
                    tool_calls = msg.get("tool_calls", []) or []

                    if reasoning:
                        block = self._append_block(kind="thinking")
                        block.update_lines(render_thinking_markdown_lines(reasoning))

                    if content:
                        block = self._append_block(kind="reply")
                        block.update_lines(render_markdown_lines(content, bg=False))

                    for tc in tool_calls:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        if not isinstance(fn, dict):
                            fn = {}
                        name = fn.get("name", "")
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        result = clean_result(name, tool_results.get(tc.get("id", ""), ""))
                        tc_id = tc.get("id", "")
                        is_error = (
                            tool_errors[tc_id]
                            if tc_id in tool_errors
                            else is_error_result(name, result)
                        )
                        if block_tool(name):
                            title, body = tool_block(name, args, result, is_error)
                            block = self._append_block(
                                kind="tool-block",
                                title=title,
                                title_style=_TOOL_ERROR if is_error else _TOOL_HEADER,
                                pad_left=tool_num_width(name, args, result, is_error) + 1,
                            )
                            block.update_lines(render_tool_markdown_lines(body, numbered=(name == "write")))
                            continue
                        md = tool_markdown(name, args, result, is_error)
                        if md is not None:
                            title, m = md
                            content_widget = render_tool_markdown_lines(m, numbered=(name == "read"), line_number_start=read_line_start(result, args))
                        else:
                            title, t = tool_render(name, args, result, is_error)
                            content_widget = t
                        block = self._append_block(
                            kind="tool",
                            title=title,
                            title_style=_TOOL_ERROR if is_error else _TOOL_TITLE,
                            collapsed=name != "bash",
                            content_bg=_USER_BG if name == "bash" else None,
                            pad_left=tool_num_width(name, args, result, is_error) + 1 if name == "read" else None,
                            content_pad_left=0 if name == "read" else None,
                        )
                        if isinstance(content_widget, list):
                            block.update_lines(content_widget)
                        else:
                            block.update(content_widget)

                    if content:
                        if self._is_turn_end(messages, idx):
                            cfg = get_config()
                            meta = msg.get("_meta") or {}
                            model = meta.get("model") or (cfg.model or "?")
                            summary = model
                            if meta.get("elapsed") is not None:
                                summary += f" - {fmt_duration(meta['elapsed'])}"
                            block = self._append_block(kind="summary")
                            block.update(summary)
        finally:
            canvas._end_bulk()

        if not renderable:
            self._show_logo()
        else:
            self._hide_logo()

        if scroll_to_end:
            self.call_after_refresh(lambda: self._scroll_end(force=True))

    def _expand_window(self) -> None:
        if self._busy:
            self._append_error("Agent is busy, please wait.")
            return
        self._win_msgs *= 2
        self._win_lines *= 2
        self._hidden_msgs = 0
        self._render_messages(self._win_msgs, self._win_lines, scroll_to_end=False)

    @staticmethod
    def _is_turn_end(messages: list, idx: int) -> bool:
        for msg in messages[idx + 1:]:
            if msg.get("role") == "tool":
                continue
            return msg.get("role") != "assistant"
        return True
