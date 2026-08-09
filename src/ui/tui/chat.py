from __future__ import annotations

import json
import os

from src.agent import name_session_from_first_message
from src.ui.tui.colors import _THINKING_BODY, _THINKING_TITLE, _TOOL_ERROR, _TOOL_HEADER, _TOOL_TITLE, _USER_BG
from src.ui.tui.markdown import render_markdown
from src.ui.tui.render import (
    block_tool, clean_result, fmt_duration, is_error_result, read_line_start,
    render_tool_markdown, tool_block, tool_markdown, tool_num_width, tool_render,
)
from src.ui.tui.thinking import render_thinking_markdown
from src.utils.config import get_config


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
        self._ctx_usage_tokens = 0
        for msg in reversed(s.messages):
            meta = msg.get("_meta")
            if meta and meta.get("prompt_tokens"):
                self._ctx_usage_tokens = meta["prompt_tokens"]
                break
        self._render_messages()
        self._update_status()
        self._input().focus()

    def _render_messages(self) -> None:
        self._clear_chat_messages()
        tool_results = {}
        tool_errors = {}
        for msg in self._session.messages:
            if msg.get("role") == "tool":
                tool_results[msg["tool_call_id"]] = msg["content"]
                tool_errors[msg["tool_call_id"]] = bool(msg.get("is_error"))

        messages = self._session.messages
        for idx, msg in enumerate(messages):
            role = msg.get("role", "")
            if role == "system":
                continue
            if role == "user":
                block = self._append_block(kind="user", bg=_USER_BG)
                block.update(msg.get("content", ""))
                continue
            if role == "assistant":
                reasoning = msg.get("reasoning_content", "") or ""
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", []) or []

                if reasoning:
                    block = self._append_block(
                        kind="thinking",
                        title="Thinking",
                        title_style=_THINKING_TITLE,
                        body_style=_THINKING_BODY,
                        expandable=True,
                        collapsed=False,
                        pad_bottom=0,
                    )
                    block.update(render_thinking_markdown(reasoning))

                if content:
                    block = self._append_block(
                        kind="reply",
                        pad_top=1,
                        pad_bottom=0,
                        pad_left=3,
                        pad_right=1,
                    )
                    block.update(render_markdown(content, bg=False))

                for tc in tool_calls:
                    fn = tc.get("function", {})
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
                            expandable=True,
                            collapsed=False,
                            pad_top=1,
                            pad_bottom=0,
                            pad_left=tool_num_width(name, args, result, is_error) + 1,
                            pad_right=1,
                            content_pad_left=0,
                        )
                        block.update(render_tool_markdown(body, numbered=(name == "write")))
                        continue
                    md = tool_markdown(name, args, result, is_error)
                    if md is not None:
                        title, m = md
                        content_widget = render_tool_markdown(m, numbered=(name == "read"), line_number_start=read_line_start(result, args))
                    else:
                        title, t = tool_render(name, args, result, is_error)
                        content_widget = t
                    block = self._append_block(
                        kind="tool",
                        title=title,
                        title_style=_TOOL_ERROR if is_error else _TOOL_TITLE,
                        expandable=True,
                        collapsed=False if name == "bash" else True,
                        pad_bottom=0,
                        content_bg=_USER_BG if name == "bash" else None,
                        pad_left=tool_num_width(name, args, result, is_error) + 1 if name == "read" else None,
                        content_pad_left=0 if name == "read" else None,
                    )
                    block.update(content_widget)

                if content:
                    if self._is_turn_end(messages, idx):
                        cfg = get_config()
                        meta = msg.get("_meta") or {}
                        model = meta.get("model") or (cfg.model or "?")
                        summary = model
                        if meta.get("elapsed") is not None:
                            summary += f" - {fmt_duration(meta['elapsed'])}"
                        block = self._append_block(
                            kind="summary",
                            pad_top=1,
                            pad_left=3,
                            pad_right=1,
                        )
                        block.update(summary)

        if not any(m.get("role") != "system" for m in self._session.messages):
            self._show_logo()
        else:
            self._hide_logo()

        for block in self._canvas()._blocks:
            block.settle()

        self.call_after_refresh(lambda: self._scroll_end(force=True))

    @staticmethod
    def _is_turn_end(messages: list, idx: int) -> bool:
        for msg in messages[idx + 1:]:
            if msg.get("role") == "tool":
                continue
            return msg.get("role") != "assistant"
        return True
