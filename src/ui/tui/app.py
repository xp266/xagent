import json
import os
import threading
import time
import unicodedata

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from rich.text import Text
from rich.cells import cell_len

from src.agent import get_session_manager, run_session_turn, name_session_from_first_message
from src.agent.turn import RETRY_LIMIT
from src.utils.models import get_model_context_limit
from src.utils.config import get_config
from src.utils.providers import get_store
from src.mcp.manager import get_mcp_manager
from src.types.events import StreamEvent

from src.ui.tui.css import CSS
from src.ui.tui.commands import get_commands, match_commands
from src.ui.tui.dialogs import PickerMixin
from src.ui.tui.logo import LogoWidget
from src.ui.tui.canvas import CanvasBlock, ChatCanvas, _THINKING_BODY, _THINKING_TITLE, _TOOL_ERROR, _TOOL_HEADER, _TOOL_TITLE, _USER_BG
from src.ui.tui.lazy import LazyText
from src.ui.tui.markdown import StreamMarkdown, render_markdown
from src.ui.tui.render import (
    block_tool, clean_result, code_tool, fmt_duration, fmt_pct, is_error_result,
    read_line_start, tool_block, tool_markdown, tool_num_width, tool_render,
)
from src.ui.tui.streaming import stream_args
from src.ui.tui.widgets import (
    ChatInput,
    CommandPalette,
    McpPicker,
    ModelPicker,
    ProviderKeyDialog,
    ProviderPicker,
    SessionPicker,
    StrengthPicker,
)

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_MCP_DOT = "●"


def _truncate_cells(text: str, max_cells: int) -> str:
    if max_cells <= 0:
        return ""
    cells = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cells + w > max_cells:
            return text[:i]
        cells += w
    return text

_BLUE_WAVE = (
    "#1E3A8A",
    "#1D4ED8",
    "#2563EB",
    "#3B82F6",
    "#60A5FA",
    "#93C5FD",
    "#A5B8FB",
    "#C0CDFC",
    "#D6E0FD",
    "#E6EBFE",
    "#F1F4FF",
    "#F8FAFF",
)

_WAVE_SPEED = 10.0

_RENDER_COOLDOWN = 0.016


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r = int(c1[1:3], 16) + (int(c2[1:3], 16) - int(c1[1:3], 16)) * t
    g = int(c1[3:5], 16) + (int(c2[3:5], 16) - int(c1[3:5], 16)) * t
    b = int(c1[5:7], 16) + (int(c2[5:7], 16) - int(c1[5:7], 16)) * t
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


class XAgentTUI(PickerMixin, App):
    CSS = CSS

    def __init__(self):
        super().__init__()
        self._sm = get_session_manager()
        self._launch_dir = os.getcwd()
        self._project = self._launch_dir
        self._session = self._sm.create(path=self._project, persist=False)
        self._ctx_usage_tokens = 0
        self._busy = False
        self._current = None
        self._spinner_idx = 0
        self._last_spinner_time = 0.0
        self._spinners = {}
        self._waves = []
        self._add_model_provider_flow = False
        self._pending_model_provider = None
        self._deferred = None
        get_mcp_manager().connect_async(get_store().mcp_servers)

    def _post(self, fn, *args) -> None:
        try:
            self.call_from_thread(fn, *args)
        except Exception:
            pass

    def _chat(self):
        return self.query_one("#chat-box")

    def _canvas(self) -> ChatCanvas:
        return self.query_one("#chat-canvas", ChatCanvas)

    def _append_block(
        self,
        *,
        kind: str = "body",
        title: str = "",
        title_style: str = "",
        body_style: str = "",
        bg: str | None = None,
        content_bg: str | None = None,
        pad_top: int = 1,
        pad_bottom: int = 1,
        pad_left: int | None = 3,
        pad_right: int = 1,
        content_pad_left: int | None = None,
        expandable: bool = False,
        collapsed: bool = False,
        hide_arrow: bool = False,
    ) -> CanvasBlock:
        self._hide_logo()
        return self._canvas().append(
            CanvasBlock(
                kind=kind,
                title=title,
                title_style=title_style,
                body_style=body_style,
                bg=bg,
                content_bg=content_bg,
                pad_top=pad_top,
                pad_bottom=pad_bottom,
                pad_left=3 if pad_left is None else pad_left,
                pad_right=pad_right,
                content_pad_left=content_pad_left,
                expandable=expandable,
                collapsed=collapsed,
                hide_arrow=hide_arrow,
            )
        )

    def _show_logo(self) -> None:
        logo = self._logo()
        if logo is None:
            logo = Vertical(LogoWidget(), id="logo-overlay")
            self._chat().mount(logo)
        logo.display = True

    def _logo(self):
        try:
            return self._chat().get_widget_by_id("logo-overlay")
        except Exception:
            return None

    def _hide_logo(self) -> None:
        logo = self._logo()
        if logo is not None:
            logo.display = False

    def _clear_chat_messages(self) -> None:
        self._canvas().clear()
        logo = self._logo()
        if logo is not None:
            logo.display = False

    def _scroll_end(self):
        chat = self._chat()
        if chat.max_scroll_y <= 0 or chat.scroll_offset.y >= chat.max_scroll_y - 3:
            chat.scroll_end(animate=False)

    def _flush_streaming_content(self, force: bool = False) -> None:
        cur = self._current
        if cur is None:
            return
        now = time.monotonic()
        if not force and now - cur["last_stream_render"] < _RENDER_COOLDOWN:
            return
        cur["last_stream_render"] = now

        if cur["thinking"] is not None and cur.get("reasoning_text"):
            cur["thinking"].update(cur["reasoning_text"])

        if cur["reply"] is not None and cur.get("reply_text"):
            md = cur.get("_reply_md")
            if md is None:
                md = StreamMarkdown()
                cur["_reply_md"] = md
            prev_len = cur.get("_reply_md_len", 0)
            text = cur["reply_text"]
            if len(text) > prev_len:
                md.feed(text[prev_len:])
                cur["_reply_md_len"] = len(text)
            cur["reply"].update(md.render())

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
            args = stream_args(info["raw"], info["name"])
            name = info["name"]
            if block_tool(name):
                title, body = tool_block(name, args, None, False, preview=True)
                if tool["title"] != title:
                    tool["title"] = title
                    self._render_tool_spinner(tool)
                tool["st"].update(render_markdown(body))
            else:
                title, t = tool_render(name, args, None, False, preview=True)
                title = title.strip() or name
                if tool["block"] is not None and tool["block"].title != title:
                    tool["block"].set_title(title)
                    self._render_tool_spinner(tool)
                tool["st"].update(t)

        self._scroll_end()

    def _context_pct(self, limit: int) -> float:
        if self._ctx_usage_tokens > 0 and limit > 0:
            return min(100.0, self._ctx_usage_tokens / limit * 100)
        total = self._session.token_usage.total_tokens
        if limit > 0 and total > 0:
            return min(100.0, total / limit * 100)
        return 0.0

    def _status_string(self) -> str:
        cfg = get_config()
        if not cfg.base_url:
            model = "Type /provider to connect a provider"
        elif not cfg.model:
            model = "Type /model to select a model"
        else:
            model = cfg.model
            if cfg.reasoning_effort:
                model = f"{model} · {cfg.reasoning_effort}"
        total = self._session.token_usage.total_tokens
        limit = get_model_context_limit(cfg.model) if cfg.model else 0
        pct = self._context_pct(limit)
        return f"{model}  {total:,} tokens  {fmt_pct(pct)}  |  xAgent - {self._project} - {self._session.name}"

    def _mcp_status_text(self) -> Text | None:
        counts = get_mcp_manager().status_counts()
        if sum(counts.values()) == 0:
            enabled = [
                name
                for name, cfg in get_store().mcp_servers.items()
                if isinstance(cfg, dict) and str(cfg.get("status", "enabled")).lower() != "disabled"
            ]
            if enabled:
                counts["connecting"] = len(enabled)
        inner = []
        for key, color in (("connected", "green"), ("connecting", "yellow"), ("failed", "red")):
            n = counts.get(key, 0)
            if n > 0:
                inner.append((color, n))
        if not inner:
            return None
        text = Text()
        text.append("MCP ")
        for i, (color, n) in enumerate(inner):
            text.append(f"{_MCP_DOT}{n}", style=color)
            if i < len(inner) - 1:
                text.append(" ")
        return text

    def _wave_color_at(self, index: int, now: float):
        best = None
        for t0 in self._waves:
            distance = (now - t0) * _WAVE_SPEED - index
            if 0 <= distance < len(_BLUE_WAVE):
                if best is None or distance < best:
                    best = distance
        if best is None:
            return None
        i = int(best)
        frac = best - i
        if i >= len(_BLUE_WAVE) - 1:
            return _BLUE_WAVE[-1]
        return _lerp_hex(_BLUE_WAVE[i], _BLUE_WAVE[i + 1], frac)

    def _update_status(self, status: str | None = None) -> None:
        if status is None:
            status = self._status_string()
        width = self.size.width if self.size and self.size.width else 80
        mcp = self._mcp_status_text()
        mcp_len = mcp.cell_len if mcp is not None else 0
        avail = max(0, width - 1 - mcp_len)
        status = _truncate_cells(status, avail)
        text = Text()
        if self._busy and self._waves:
            now = time.monotonic()
            cell = 0
            for ch in status:
                text.append(ch, style=self._wave_color_at(cell, now))
                cell += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        else:
            text.append(status)
        pad = avail - cell_len(status)
        if pad > 0:
            text.append(" " * pad)
        if mcp is not None:
            text.append(mcp)
        self.query_one("#status", Static).update(text)

    def _append_user(self, text: str) -> None:
        block = self._append_block(kind="user", bg=_USER_BG)
        block.update(text)
        self._scroll_end()

    def _append_error(self, text: str) -> None:
        block = self._append_block(kind="error", body_style="bold #FF5555")
        block.update(text)
        self._scroll_end()

    def _show_retry(self, error: str, delay: float, attempt: int) -> None:
        cur = self._current
        if cur is None:
            return
        self._reset_turn_render()
        deadline = time.monotonic() + delay
        retry = cur.get("retry")
        if retry is None:
            block = self._append_block(kind="error", body_style="bold #FF5555")
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

    def _reset_turn_render(self) -> None:
        cur = self._current
        if cur is None:
            return
        canvas = self._canvas()
        for key in ("thinking", "thinking_title", "thinking_col"):
            col = cur.get(key)
            if col is not None:
                try:
                    canvas.remove(col)
                except Exception:
                    pass
                cur[key] = None
        cur["reasoning_text"] = ""
        reply = cur.get("reply")
        if reply is not None:
            try:
                canvas.remove(reply)
            except Exception:
                pass
            cur["reply"] = None
            cur["reply_text"] = ""
            cur["reply_appended"] = 0
        self._stop_all_spinners()
        self._hide_waiting()
        cur.pop("_reply_md", None)
        cur["_reply_md_len"] = 0

    def _start_spinner(self, title) -> None:
        if title is None:
            return
        self._spinners[id(title)] = title
        self._render_spinner(title)

    def _render_spinner(self, title) -> None:
        if id(title) not in self._spinners:
            return
        frame = _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]
        label = getattr(title, "label", title.title)
        title.arrow_hidden = True
        title.set_title(label)
        title.set_marker(frame)

    def _stop_spinner(self, title, *, restore_arrow: bool = True) -> None:
        if title is None:
            return
        if self._spinners.pop(id(title), None) is not None:
            label = getattr(title, "label", title.title)
            title.set_marker(None)
            if getattr(title, "hide_arrow", False):
                return
            if restore_arrow:
                title.arrow_hidden = False
            title.set_title(label)

    def _stop_all_spinners(self) -> None:
        for title in list(self._spinners.values()):
            self._stop_spinner(title)
        if self._current is not None:
            for tool in self._current["tools"].values():
                self._stop_tool_spinner(tool)

    def _tick_spinners(self) -> None:
        cur = self._current
        tool_spinning = cur is not None and any(t.get("spinning") for t in cur["tools"].values())
        if not self._spinners and not tool_spinning:
            return
        now = time.monotonic()
        if now - self._last_spinner_time < 0.1:
            return
        self._last_spinner_time = now
        self._spinner_idx += 1
        for title in list(self._spinners.values()):
            self._render_spinner(title)
        if cur is not None:
            for tool in cur["tools"].values():
                self._render_tool_spinner(tool)

    def _start_tool_spinner(self, tool) -> None:
        tool["spinning"] = True
        self._render_tool_spinner(tool)

    def _render_tool_spinner(self, tool) -> None:
        if not tool["spinning"]:
            return
        frame = _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]
        block = tool.get("block")
        if block is not None:
            block.arrow_hidden = True
            block.set_title(tool["title"])
            block.set_marker(frame)

    def _stop_tool_spinner(self, tool) -> None:
        if not tool["spinning"]:
            return
        tool["spinning"] = False
        block = tool.get("block")
        if block is not None:
            block.arrow_hidden = False
            block.set_marker(None)
            block.set_title(tool["title"])

    def _tick_animations(self) -> None:
        if self._busy:
            self._tick_spinners()
            self._tick_status_wave()
        else:
            self._refresh_mcp_status()
            self._refresh_mcp_picker()
        if self._current is not None:
            self._flush_streaming_content()
            self._tick_retry()

    def _refresh_mcp_status(self) -> None:
        self._update_status()

    def _refresh_mcp_picker(self) -> None:
        try:
            picker = self._mcp_picker()
        except Exception:
            return
        if not picker.is_visible:
            return
        if picker._pending_select is not None:
            return
        items = self._mcp_items()
        sig = tuple(items)
        if sig != getattr(self, "_mcp_picker_sig", None):
            self._mcp_picker_sig = sig
            picker.update_items(items, select=picker._selected)

    def _tick_status_wave(self) -> None:
        if not self._busy:
            if self._waves:
                self._waves.clear()
                self._update_status()
            return
        now = time.monotonic()
        status = self._status_string()
        n = cell_len(status)
        if self._waves:
            head = (now - self._waves[0]) * _WAVE_SPEED
            if head >= (n - 1) + (len(_BLUE_WAVE) - 1):
                self._waves = []
        if not self._waves:
            self._waves.append(now)
        self._waves = [t0 for t0 in self._waves if (now - t0) * _WAVE_SPEED < n + len(_BLUE_WAVE)]
        self._update_status(status=status)

    def _ensure_thinking(self):
        cur = self._current
        if cur["thinking"] is None:
            block = self._append_block(
                kind="thinking",
                title="Thinking",
                title_style=_THINKING_TITLE,
                body_style=_THINKING_BODY,
                expandable=True,
                collapsed=True,
                pad_bottom=0,
            )
            cur["thinking"] = block
            cur["thinking_title"] = block
            cur["thinking_col"] = block
            block.arrow_hidden = True
        self._start_spinner(cur["thinking_title"])
        return cur["thinking"]

    def _ensure_waiting(self) -> None:
        cur = self._current
        if cur is None or cur.get("waiting") is not None:
            return
        block = self._append_block(
            kind="waiting",
            title="Waiting for response...",
            title_style="bold white",
            expandable=True,
            collapsed=True,
            hide_arrow=True,
            pad_bottom=0,
        )
        cur["waiting"] = block
        self._start_spinner(block)

    def _hide_waiting(self) -> None:
        cur = self._current
        if cur is None:
            return
        waiting = cur.get("waiting")
        if waiting is None:
            return
        self._stop_spinner(waiting, restore_arrow=False)
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
        self._stop_spinner(cur.get("thinking_title"))
        col = cur.get("thinking_col")
        if col is not None:
            self._canvas().remove(col)
        cur["thinking"] = None
        cur["thinking_title"] = None
        cur["thinking_col"] = None

    def _ensure_reply(self):
        cur = self._current
        if cur["reply"] is None:
            block = self._append_block(
                kind="reply",
                pad_top=1,
                pad_bottom=0,
                pad_left=3,
                pad_right=1,
            )
            cur["reply"] = block
        return cur["reply"]

    def _add_tool_streaming(self, tc_id: str, name: str) -> None:
        tool = {
            "name": name,
            "title": name,
            "spinning": False,
            "done": False,
            "input": {},
            "header": None,
            "st": None,
            "col": None,
            "title_widget": None,
            "block": None,
        }
        if block_tool(name):
            block = self._append_block(
                kind="tool-block",
                title=name,
                title_style=_TOOL_HEADER,
                expandable=True,
                collapsed=False,
                pad_top=1,
                pad_bottom=0,
                pad_left=2,
                pad_right=1,
                content_pad_left=0,
            )
            tool["block"] = block
            tool["st"] = block
            tool["col"] = block
            tool["header"] = block
        else:
            title, t = tool_render(name, {}, None, False)
            block = self._append_block(
                kind="tool",
                title=title,
                title_style=_TOOL_TITLE,
                expandable=True,
                collapsed=False if name == "bash" else True,
                pad_bottom=0,
                content_bg=_USER_BG if name == "bash" else None,
                content_pad_left=0 if name == "read" else None,
            )
            tool["title"] = title
            tool["block"] = block
            tool["st"] = block
            tool["col"] = block
            tool["title_widget"] = block
            block.update(t)
        self._current["tools"][tc_id] = tool
        self._current["tool_inputs"][tc_id] = {}
        self._current["tool_buffers"][tc_id] = {"name": name, "raw": ""}
        if not block_tool(name):
            self._start_tool_spinner(tool)

    def _set_tool_content(self, col, widget) -> None:
        if isinstance(col, CanvasBlock):
            if isinstance(widget, LazyText):
                content = widget.visual
                col.update(content)
                col._strips = []
                col._key = ()
        else:
            contents = col.query_one("Contents")
            for child in list(contents.children):
                child.remove()
            contents.mount(widget)

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
        self._current["tool_inputs"][tc_id] = args
        if block_tool(name):
            title, body = tool_block(name, args, None, False)
            if tool["title"] != title:
                tool["title"] = title
                self._render_tool_spinner(tool)
            tool["block"].pad_left = tool_num_width(name, args) + 1
            tool["st"].update(render_markdown(body, numbered=(name == "write")))
        else:
            title, t = tool_render(name, args, None, False)
            tool["title"] = title
            block = tool["block"]
            if block is not None and block.title != title:
                block.set_title(title)
                self._render_tool_spinner(tool)
            tool["st"].update(t)

    def _set_tool_result(self, tc_id: str, name: str, result: str, is_error: bool) -> None:
        tool = self._current["tools"].get(tc_id)
        if tool is None:
            return
        self._stop_tool_spinner(tool)
        tool["name"] = name
        tool["input"] = self._current["tool_inputs"].get(tc_id, {})
        if block_tool(name):
            title, body = tool_block(name, tool["input"], result, is_error)
            tool["title"] = title
            tool["header"].set_title(title)
            tool["block"].pad_left = tool_num_width(name, tool["input"], result, is_error) + 1
            tool["st"].update(render_markdown(body, numbered=(name == "write")))
            if is_error:
                tool["col"].title_style = _TOOL_ERROR
                tool["col"]._strips = []
                tool["col"]._key = ()
            tool["done"] = True
            self._current["tool_done"].add(tc_id)
            return
        if code_tool(name):
            md = tool_markdown(name, tool["input"], result, is_error)
            if md is not None:
                m_title, m = md
                tool["title"] = m_title
                if tool["block"] is not None and tool["block"].title != m_title:
                    tool["block"].set_title(m_title)
                tool["col"].pad_left = tool_num_width(name, tool["input"], result, is_error) + 1
                tool["col"].content_pad_left = 0
                self._set_tool_content(tool["col"], LazyText(render_markdown(m, numbered=(name == "read"), line_number_start=read_line_start(result, tool["input"]))))
                if is_error:
                    tool["col"].title_style = _TOOL_ERROR
                    tool["col"]._strips = []
                    tool["col"]._key = ()
                tool["done"] = True
                self._current["tool_done"].add(tc_id)
                return
        title, t = tool_render(name, tool["input"], result, is_error)
        tool["title"] = title
        if tool["block"] is not None and tool["block"].title != title:
            tool["block"].set_title(title)
        tool["st"].update(t)
        if is_error:
            tool["col"].title_style = _TOOL_ERROR
            tool["col"]._strips = []
            tool["col"]._key = ()
        tool["done"] = True
        self._current["tool_done"].add(tc_id)

    def _add_summary(self, elapsed: float) -> None:
        cfg = get_config()
        model = cfg.model or "?"
        summary = f"{model} - {fmt_duration(elapsed)}"
        block = self._append_block(kind="summary", pad_top=1, pad_left=3, pad_right=1)
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
            self._stop_spinner(cur.get("thinking_title"))
            cur["thinking"] = None
            cur["thinking_title"] = None
            cur["thinking_col"] = None
            self._ensure_waiting()
        elif t == "text-start":
            self._flush_streaming_content(force=True)
            self._remove_empty_thinking()
            self._hide_waiting()
            cur["reply"] = None
            cur["reply_text"] = ""
            cur["reply_appended"] = 0
            cur.pop("_reply_md", None)
            cur["_reply_md_len"] = 0
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
            self._set_tool_result(
                data["id"],
                data["name"],
                result,
                is_error_result(data["name"], result),
            )
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
            self._ctx_usage_tokens = usage.get("prompt_tokens", 0)
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
        elif t == "turn-cancelled":
            self._clear_retry()
            self._hide_waiting()
            cur["interrupted"] = True
            block = self._append_block(kind="summary", pad_top=1, pad_left=3, pad_right=1)
            block.update("Turn interrupted by user")
            self._scroll_end()

    def _turn_worker(self, text: str) -> None:
        from src.agent.cancel import turn_done
        start = time.monotonic()
        try:
            for event in run_session_turn(self._session, text):
                self._post(self._handle_event, event)
        except Exception as e:
            self._post(self._append_error, f"{type(e).__name__}: {e}")
        elapsed = time.monotonic() - start
        self._post(self._finalize_turn, elapsed)
        turn_done()

    def _finalize_turn(self, elapsed: float) -> None:
        cur = self._current
        if cur is not None:
            md = cur.get("_reply_md")
            if md is not None:
                md.finish()
        self._flush_streaming_content(force=True)
        if cur and cur["steps"] > 0 and not cur.get("interrupted"):
            self._add_summary(elapsed)
        self._remove_empty_thinking()
        self._hide_waiting()
        self._stop_all_spinners()
        self._waves.clear()
        self._busy = False
        self._input().busy = False
        self._current = None
        self._update_status()
        self._scroll_end()
        deferred = self._deferred
        self._deferred = None
        if deferred is not None:
            cmd, args = deferred
            cmd.handler(self, args)
            return
        self._input().focus()

    def _apply_name(self, s: object, name: str) -> None:
        if not name or name == "New Session":
            return
        s.name = name
        self._sm.rename(s.id, name)
        if s is self._session:
            self._update_status()

    def _name_worker(self, s: object, first_message: str) -> None:
        try:
            name = name_session_from_first_message(s, first_message)
        except Exception:
            name = None
        if name:
            self._post(self._apply_name, s, name)

    def _new_chat(self) -> None:
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
        canvas = self._canvas()
        tool_results = {}
        for msg in self._session.messages:
            if msg.get("role") == "tool":
                tool_results[msg["tool_call_id"]] = msg["content"]

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
                        collapsed=True,
                        pad_bottom=0,
                    )
                    block.update(reasoning)

                if content:
                    block = self._append_block(
                        kind="reply",
                        pad_top=1,
                        pad_bottom=0,
                        pad_left=3,
                        pad_right=1,
                    )
                    block.update(render_markdown(content))

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    result = clean_result(name, tool_results.get(tc.get("id", ""), ""))
                    is_error = is_error_result(name, result)
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
                        block.update(render_markdown(body, numbered=(name == "write")))
                        continue
                    md = tool_markdown(name, args, result, is_error)
                    if md is not None:
                        title, m = md
                        content_widget = render_markdown(m, numbered=(name == "read"), line_number_start=read_line_start(result, args))
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

        self.call_after_refresh(self._scroll_end_force)

    def _scroll_end_force(self) -> None:
        self._chat().scroll_end(animate=False)

    @staticmethod
    def _is_turn_end(messages: list, idx: int) -> bool:
        for msg in messages[idx + 1:]:
            if msg.get("role") == "tool":
                continue
            return msg.get("role") != "assistant"
        return True

    def _palette(self) -> CommandPalette:
        return self.query_one("#command-palette", CommandPalette)

    def _refresh_palette(self) -> None:
        if getattr(self, "_suppress_palette", False):
            return
        text = self._input().text
        if not text.startswith("/"):
            self._palette().hide()
            self._set_palette_open(False)
            return
        query = text[1:].lstrip()
        commands = match_commands(query)
        self._palette().show(commands)
        self._set_palette_open(bool(commands))

    def _set_palette_open(self, open: bool) -> None:
        self._input().palette_open = open

    def on_chat_input_text_edited(self, message: ChatInput.TextEdited) -> None:
        self._refresh_palette()

    def on_chat_input_navigate(self, message: ChatInput.Navigate) -> None:
        self._palette().move(message.delta)

    def on_chat_input_accept_palette(self, message: ChatInput.AcceptPalette) -> None:
        cmd = self._palette().selected_command
        if cmd is None:
            return
        inp = self._input()
        inp.clear()
        self._palette().hide()
        self._set_palette_open(False)
        self._suppress_palette = True
        inp.insert(f"/{cmd.name} ")
        self._suppress_palette = False
        inp.focus()

    def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._busy:
            parts = text.split(None, 1)
            name = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            for cmd in get_commands():
                if cmd.name == name or name in cmd.aliases:
                    if cmd.name in ("new", "session", "exit"):
                        self._deferred = (cmd, args)
                        from src.agent.cancel import cancel
                        cancel()
                        self._update_status("Interrupting...")
                        return
            self._append_error("Agent is busy, please wait.")
            return
        if text.startswith("/"):
            parts = text.split(None, 1)
            name = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            for cmd in get_commands():
                if cmd.name == name or name in cmd.aliases:
                    cmd.handler(self, args)
                    return
            self._append_error(f"Unknown command: /{name}")
            return
        self._send(text)

    def _send(self, text: str) -> None:
        cfg = get_config()
        if not cfg.base_url:
            self._append_error("No provider connected. Type /provider to connect one.")
            return
        if not cfg.model:
            self._append_error("No model selected. Type /model to select one.")
            return
        from src.agent.cancel import reset
        reset()
        self._input().busy = True
        self._busy = True
        self._waves.clear()
        self._current = {
            "steps": 0,
            "reasoning_text": "",
            "reply_text": "",
            "reply_appended": 0,
            "thinking": None,
            "reply": None,
            "tools": {},
            "tool_inputs": {},
            "tool_buffers": {},
            "tool_done": set(),
            "waiting": None,
            "last_stream_render": 0.0,
            "_reply_md_len": 0,
        }
        self._append_user(text)
        self._ensure_waiting()
        self._scroll_end()
        if self._session.name == "New Session":
            s = self._session
            threading.Thread(target=self._name_worker, args=(s, text), name="xagent-naming", daemon=True).start()
        threading.Thread(target=self._turn_worker, args=(text,), name="xagent-turn", daemon=True).start()

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        self._handle_input(message.text)

    def on_chat_input_interrupt_confirmed(self, message: ChatInput.InterruptConfirmed) -> None:
        if not self._busy:
            return
        from src.agent.cancel import cancel
        cancel()
        self._update_status("Interrupting...")

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-box"):
            yield ChatCanvas(id="chat-canvas")

        yield CommandPalette(id="command-palette")

        with Vertical(id="input-box"):
            yield ChatInput(soft_wrap=True, id="input")

        with Vertical(id="status-box"):
            yield Static("", id="status")

        yield SessionPicker(id="session-picker")
        yield ProviderPicker(id="provider-picker")
        yield ModelPicker(id="model-picker")
        yield StrengthPicker(id="strength-picker")
        yield McpPicker(id="mcp-picker")
        yield ProviderKeyDialog(id="provider-key-dialog")

    def on_mount(self) -> None:
        self.title = "XAgent"
        from src.agent.truncate import TruncateService
        TruncateService().cleanup()
        self._update_status()
        self._show_logo()
        self._scroll_end()
        self.set_interval(0.033, self._tick_animations, pause=False)
        self._input().focus()

    def on_unmount(self) -> None:
        from src.agent.cancel import abort, turn_done
        turn_done()
        abort()


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
