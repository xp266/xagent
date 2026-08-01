import asyncio
import json
import os
import time
from functools import partial

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Static, Collapsible, Markdown
from rich.text import Text

from src.agent import get_session_manager, run_session_turn, name_session_from_first_message
from src.ai.capabilities import get_model_context_limit
from src.utils.config import get_config
from src.types.events import StreamEvent

from src.ui.tui.css import CSS
from src.ui.tui.commands import get_commands, match_commands
from src.ui.tui.dialogs import PickerMixin
from src.ui.tui.logo import build_logo_text
from src.ui.tui.lazy import LazyText
from src.ui.tui.render import clean_result, code_tool, fmt_duration, fmt_pct, is_error_result, tool_markdown, tool_render
from src.ui.tui.streaming import stream_args
from src.ui.tui.widgets import ChatInput, CommandPalette, LazyCollapsible, LazyMarkdown, ModelPicker, ProviderKeyDialog, ProviderPicker, SessionPicker

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

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

_WAVE_SPEED = 8.0

_RENDER_COOLDOWN = 0.04


class XAgentTUI(PickerMixin, App):
    CSS = CSS

    def __init__(self):
        super().__init__()
        self._sm = get_session_manager()
        self._project = os.getcwd()
        self._session = self._sm.create(path=self._project, persist=False)
        self._ctx_usage_tokens = 0
        self._busy = False
        self._current = None
        self._spinner_idx = 0
        self._spinners = {}
        self._waves = []
        self._add_model_provider_flow = False
        self._pending_model_provider = None

    def _chat(self):
        return self.query_one("#chat-box")

    def _show_logo(self) -> None:
        logo = self._logo()
        if logo is None:
            logo = Vertical(Static(build_logo_text()), id="logo-overlay")
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
        for child in list(self._chat().children):
            if getattr(child, "id", None) != "logo-overlay":
                child.remove()

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
            md = cur["reply"]
            reply_text = cur["reply_text"]
            prev_len = cur.get("reply_appended", 0)
            if len(reply_text) > prev_len:
                stream = cur.get("reply_stream")
                if stream is not None:
                    self.run_worker(stream.write(reply_text[prev_len:]))
                else:
                    md.append(reply_text[prev_len:])
                cur["reply_appended"] = len(reply_text)

        for tc_id, (st_widget, col) in cur["tools"].items():
            if tc_id in cur.get("tool_done", set()):
                continue
            info = cur["tool_buffers"].get(tc_id)
            if info is None:
                continue
            raw_len = len(info["raw"])
            if not force and raw_len == info.get("_last_len", 0):
                continue
            info["_last_len"] = raw_len
            args = stream_args(info["raw"], info["name"])
            title, t = tool_render(info["name"], args, None, False, preview=True)
            title = title.strip() or info["name"]
            if str(col._title.label) != title:
                col._title.label = title
                self._render_spinner(col._title)
            st_widget.update(t)

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
        total = self._session.token_usage.total_tokens
        limit = get_model_context_limit(model) if cfg.model else 0
        pct = self._context_pct(limit)
        return f"{model}  {total:,} tokens  {fmt_pct(pct)}  |  xAgent - {self._project} - {self._session.name}"

    def _wave_color_at(self, index: int, now: float):
        best = None
        for t0 in self._waves:
            distance = int((now - t0) * _WAVE_SPEED) - index
            if 0 <= distance < len(_BLUE_WAVE):
                if best is None or distance < best:
                    best = distance
        return _BLUE_WAVE[best] if best is not None else None

    def _update_status(self, status: str | None = None) -> None:
        if status is None:
            status = self._status_string()
        if self._busy and self._waves:
            now = time.monotonic()
            text = Text()
            for i, ch in enumerate(status):
                text.append(ch, style=self._wave_color_at(i, now))
        else:
            text = Text(status)
        self.query_one("#status", Static).update(text)

    def _append_user(self, text: str) -> None:
        self._hide_logo()
        self._chat().mount(Vertical(LazyText(text, markup=False), classes="bubble user-bubble"))
        self._scroll_end()

    def _append_error(self, text: str) -> None:
        self._hide_logo()
        self._chat().mount(
            Vertical(Static(Text(text, style="bold #FF5555")), classes="bubble reply-bubble")
        )
        self._scroll_end()

    def _start_spinner(self, title) -> None:
        if title is None:
            return
        self._spinners[title] = True
        self._render_spinner(title)

    def _render_spinner(self, title) -> None:
        if title not in self._spinners:
            return
        frame = _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]
        title.update(Content.assemble(frame, " ", title.label))

    def _stop_spinner(self, title) -> None:
        if title is None:
            return
        if self._spinners.pop(title, None) is not None:
            title._update_label()

    def _stop_all_spinners(self) -> None:
        for title in list(self._spinners):
            self._stop_spinner(title)

    def _tick_spinners(self) -> None:
        if not self._spinners:
            return
        self._spinner_idx += 1
        for title in list(self._spinners):
            self._render_spinner(title)

    def _tick_animations(self) -> None:
        if self._busy:
            self._tick_spinners()
            self._tick_status_wave()
        if self._current is not None:
            self._flush_streaming_content()

    def _tick_status_wave(self) -> None:
        if not self._busy:
            if self._waves:
                self._waves.clear()
                self._update_status()
            return
        now = time.monotonic()
        status = self._status_string()
        n = len(status)
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
            st = LazyText("", markup=False)
            col = Collapsible(
                st,
                title="Thinking",
                classes="bubble thinking-bubble",
                collapsed=True,
                collapsed_symbol="▸",
                expanded_symbol="▾",
            )
            self._chat().mount(col)
            cur["thinking"] = st
            cur["thinking_title"] = col._title
            cur["thinking_col"] = col
        self._start_spinner(cur["thinking_title"])
        return cur["thinking"]

    def _remove_empty_thinking(self) -> None:
        cur = self._current
        if cur is None or cur.get("thinking") is None:
            return
        if cur.get("reasoning_text"):
            return
        self._stop_spinner(cur.get("thinking_title"))
        col = cur.get("thinking_col")
        if col is not None:
            col.remove()
        cur["thinking"] = None
        cur["thinking_title"] = None
        cur["thinking_col"] = None

    def _ensure_reply(self):
        cur = self._current
        if cur["reply"] is None:
            st = Markdown("")
            self._chat().mount(Vertical(st, classes="bubble reply-bubble"))
            cur["reply"] = st
            cur["reply_stream"] = Markdown.get_stream(st)
            cur["reply_stream"].start()
        return cur["reply"]

    def _add_tool_streaming(self, tc_id: str, name: str) -> None:
        title, t = tool_render(name, {}, None, False)
        st = LazyText(t)
        col = Collapsible(
            st,
            title=title,
            classes="bubble tool-bubble",
            collapsed=True,
            collapsed_symbol="▸",
            expanded_symbol="▾",
        )
        self._chat().mount(col)
        self._current["tools"][tc_id] = (st, col)
        self._current["tool_inputs"][tc_id] = {}
        self._current["tool_buffers"][tc_id] = {"name": name, "raw": ""}
        self._start_spinner(col._title)

    def _set_tool_content(self, col, widget) -> None:
        contents = col.query_one("Contents")
        for child in list(contents.children):
            child.remove()
        contents.mount(widget)

    def _finalize_tool_stream(self, tc_id: str, name: str, args: dict) -> None:
        if tc_id not in self._current["tools"]:
            self._add_tool_streaming(tc_id, name)
        pair = self._current["tools"].get(tc_id)
        if pair is None:
            return
        st, col = pair
        info = self._current["tool_buffers"].setdefault(tc_id, {"name": name, "raw": ""})
        info["name"] = name
        self._current["tool_inputs"][tc_id] = args
        title, t = tool_render(name, args, None, False)
        if str(col._title.label) != title:
            col._title.label = title
            self._render_spinner(col._title)
        st.update(t)

    def _set_tool_result(self, tc_id: str, name: str, result: str, is_error: bool) -> None:
        pair = self._current["tools"].get(tc_id)
        if pair is None:
            return
        st, col = pair
        self._stop_spinner(col._title)
        args = self._current["tool_inputs"].get(tc_id, {})
        title, t = tool_render(name, args, result, is_error)
        if str(col._title.label) != title:
            col._title.label = title
        if code_tool(name):
            md = tool_markdown(name, args, result, is_error)
            if md is not None:
                m_title, m = md
                if str(col._title.label) != m_title:
                    col._title.label = m_title
                self._set_tool_content(col, Markdown(m))
                self._current["tool_done"].add(tc_id)
                if is_error:
                    col.add_class("tool-error")
                return
        st.update(t)
        if is_error:
            col.add_class("tool-error")
        self._current["tool_done"].add(tc_id)

    def _add_summary(self, tokens: int, elapsed: float) -> None:
        cfg = get_config()
        model = cfg.model or "?"
        summary = f"{model}  {tokens:,} tokens  {fmt_duration(elapsed)}"
        self._chat().mount(Vertical(Static(summary), classes="summary-bubble"))
        self._scroll_end()

    def _handle_event(self, event: StreamEvent) -> None:
        cur = self._current
        if cur is None:
            return
        t = event.type
        if t == "reasoning-start":
            cur["reasoning_text"] = ""
            if cur["thinking"] is None:
                self._ensure_thinking()
            else:
                self._start_spinner(cur["thinking_title"])
        elif t == "reasoning-delta":
            cur["reasoning_text"] += event.data
            if cur["thinking"] is None:
                self._ensure_thinking()
            self._flush_streaming_content()
        elif t == "reasoning-end":
            self._flush_streaming_content(force=True)
            self._stop_spinner(cur.get("thinking_title"))
            cur["thinking"] = None
            cur["thinking_title"] = None
            cur["thinking_col"] = None
        elif t == "text-start":
            self._flush_streaming_content(force=True)
            self._remove_empty_thinking()
            old_stream = cur.get("reply_stream")
            if old_stream is not None:
                self.run_worker(old_stream.stop())
            cur["reply"] = None
            cur["reply_stream"] = None
            cur["reply_text"] = ""
            cur["reply_appended"] = 0
            self._ensure_reply()
        elif t == "text-delta":
            cur["reply_text"] += event.data
            if cur["reply"] is None:
                self._ensure_reply()
            self._flush_streaming_content()
        elif t == "tool-input-start":
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
        elif t == "tool-error":
            data = event.data
            self._set_tool_result(data["id"], data["name"], data.get("error", ""), True)
            self._scroll_end()
        elif t == "step-start":
            self._flush_streaming_content(force=True)
            self._stop_all_spinners()
        elif t == "step-finish":
            usage = event.data.get("usage", {}) or {}
            cur["steps"] += 1
            cur["tokens"] += usage.get("total_tokens", 0)
            self._ctx_usage_tokens = usage.get("prompt_tokens", 0)
            self._update_status()
        elif t == "provider-error":
            self._append_error(event.data.get("error", "Unknown error"))

    def _turn_worker(self, text: str) -> None:
        start = time.monotonic()
        try:
            for event in run_session_turn(self._session, text):
                self.call_from_thread(self._handle_event, event)
        except Exception as e:
            self.call_from_thread(self._append_error, f"{type(e).__name__}: {e}")
        elapsed = time.monotonic() - start
        self.call_from_thread(self._finalize_turn, elapsed)

    def _finalize_turn(self, elapsed: float) -> None:
        self._flush_streaming_content(force=True)
        cur = self._current
        if cur:
            stream = cur.get("reply_stream")
            if stream is not None:
                self.run_worker(stream.stop())
        if cur and cur["steps"] > 0:
            self._add_summary(cur["tokens"], elapsed)
        self._remove_empty_thinking()
        self._stop_all_spinners()
        self._waves.clear()
        self._busy = False
        self._current = None
        self._update_status()
        self._scroll_end()
        self.query_one("#input", ChatInput).focus()

    def _apply_name(self, name: str) -> None:
        if not name or name == "New Session":
            return
        self._session.name = name
        self._sm.rename(self._session.id, name)
        self._update_status()

    def _name_worker(self, first_message: str) -> None:
        try:
            name = name_session_from_first_message(self._session, first_message)
        except Exception:
            name = None
        if name:
            self.call_from_thread(self._apply_name, name)

    def _new_chat(self) -> None:
        self._session = self._sm.create(path=self._project, persist=False)
        self._ctx_usage_tokens = 0
        self._clear_chat_messages()
        self._show_logo()
        self._update_status()
        self._scroll_end()
        self.query_one("#input", ChatInput).focus()

    def _switch_session(self, code: str) -> None:
        s = self._sm.get(code.strip())
        if s is None:
            self._append_error(f"Session not found: {code.strip()}")
            return
        self._sm.current = s.id
        self._session = s
        self._ctx_usage_tokens = 0
        self._render_messages()
        self._update_status()
        self.query_one("#input", ChatInput).focus()

    def _render_messages(self) -> None:
        self._clear_chat_messages()
        chat = self._chat()
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
                chat.mount(Vertical(LazyText(msg.get("content", ""), markup=False), classes="bubble user-bubble"))
                continue
            if role == "assistant":
                reasoning = msg.get("reasoning_content", "") or ""
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", []) or []

                if reasoning:
                    chat.mount(Collapsible(
                        LazyText(reasoning, markup=False),
                        title="Thinking",
                        classes="bubble thinking-bubble",
                        collapsed=True,
                        collapsed_symbol="▸",
                        expanded_symbol="▾",
                    ))

                if content:
                    chat.mount(Vertical(LazyMarkdown(content), classes="bubble reply-bubble"))

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    result = clean_result(name, tool_results.get(tc.get("id", ""), ""))
                    is_error = is_error_result(name, result)
                    classes = "bubble tool-bubble" + (" tool-error" if is_error else "")
                    md = tool_markdown(name, args, result, is_error)
                    if md is not None:
                        title, m = md
                        content_widget = LazyMarkdown(m, auto=False)
                    else:
                        title, t = tool_render(name, args, result, is_error)
                        content_widget = LazyText(t)
                    chat.mount(LazyCollapsible(
                        content_widget,
                        title=title,
                        classes=classes,
                        collapsed=True,
                        collapsed_symbol="▸",
                        expanded_symbol="▾",
                    ))

                if content:
                    if self._is_turn_end(messages, idx):
                        cfg = get_config()
                        model = cfg.model or "?"
                        total = self._session.token_usage.total_tokens
                        chat.mount(Vertical(
                            Static(f"{model}  {total:,} tokens"),
                            classes="summary-bubble",
                        ))

        if not any(m.get("role") != "system" for m in self._session.messages):
            self._show_logo()
        else:
            self._hide_logo()

        self.run_worker(self._activate_pending_markdown())

    async def _activate_pending_markdown(self) -> None:
        await asyncio.sleep(0)
        for md in self._chat().query(LazyMarkdown):
            if md._auto and md.is_pending:
                await md.activate()
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
        text = self.query_one("#input", ChatInput).text
        if not text.startswith("/"):
            self._palette().hide()
            self._set_palette_open(False)
            return
        query = text[1:].lstrip()
        commands = match_commands(query)
        self._palette().show(commands)
        self._set_palette_open(bool(commands))

    def _set_palette_open(self, open: bool) -> None:
        self.query_one("#input", ChatInput).palette_open = open

    def on_chat_input_text_edited(self, message: ChatInput.TextEdited) -> None:
        self._refresh_palette()

    def on_chat_input_navigate(self, message: ChatInput.Navigate) -> None:
        self._palette().move(message.delta)

    def on_chat_input_accept_palette(self, message: ChatInput.AcceptPalette) -> None:
        cmd = self._palette().selected_command
        if cmd is None:
            return
        inp = self.query_one("#input", ChatInput)
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
        self._busy = True
        self._waves.clear()
        self._current = {
            "steps": 0,
            "tokens": 0,
            "reasoning_text": "",
            "reply_text": "",
            "reply_appended": 0,
            "reply_stream": None,
            "thinking": None,
            "reply": None,
            "tools": {},
            "tool_inputs": {},
            "tool_buffers": {},
            "tool_done": set(),
            "last_stream_render": 0.0,
        }
        self._append_user(text)
        self._ensure_thinking()
        self._scroll_end()
        if self._session.name == "New Session":
            self.run_worker(partial(self._name_worker, text), name="naming", group="naming", thread=True)
        self.run_worker(partial(self._turn_worker, text), name="turn", group="turn", thread=True, exclusive=True)

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        self._handle_input(message.text)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-box"):
            pass

        yield CommandPalette(id="command-palette")

        with Vertical(id="input-box"):
            yield ChatInput(soft_wrap=True, id="input")

        with Vertical(id="status-box"):
            yield Static("", id="status")

        yield SessionPicker(id="session-picker")
        yield ProviderPicker(id="provider-picker")
        yield ModelPicker(id="model-picker")
        yield ProviderKeyDialog(id="provider-key-dialog")

    def on_mount(self) -> None:
        self.title = "XAgent"
        self._update_status()
        self._show_logo()
        self._scroll_end()
        self.set_interval(0.1, self._tick_animations, pause=False)
        self.query_one("#input", ChatInput).focus()


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
