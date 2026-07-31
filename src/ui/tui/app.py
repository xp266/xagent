import json
import os
import time
from functools import partial

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Static, Collapsible
from rich.text import Text

from src.agent import get_session_manager, run_session_turn, name_session_from_first_message
from src.ai.capabilities import get_model_context_limit
from src.utils.config import get_config
from src.types.events import StreamEvent

from src.ui.tui.css import CSS
from src.ui.tui.render import clean_result, fmt_duration, fmt_pct, is_error_result, tool_render
from src.ui.tui.streaming import stream_args
from src.ui.tui.widgets import ChatInput

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

_BLUE_WAVE = (
    "#1E3A8A",  # head (deepest)
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
    "#F8FAFF",  # tail (lightest)
)

_WAVE_SPEED = 15.0  # characters per second
_WAVE_INTERVAL = 3.0  # seconds between wave launches


class XAgentTUI(App):
    CSS = CSS

    def __init__(self):
        super().__init__()
        self._sm = get_session_manager()
        self._session = self._sm.create(path=_PROJECT_ROOT, persist=False)
        self._ctx_usage_tokens = 0
        self._busy = False
        self._current = None
        self._spinner_idx = 0
        self._spinners = {}
        self._waves = []

    def _chat(self):
        return self.query_one("#chat-box")

    def _scroll_end(self):
        self._chat().scroll_end(animate=False)

    def _context_pct(self, limit: int) -> float:
        if self._ctx_usage_tokens > 0 and limit > 0:
            return min(100.0, self._ctx_usage_tokens / limit * 100)
        total = self._session.token_usage.total_tokens
        if limit > 0 and total > 0:
            return min(100.0, total / limit * 100)
        return 0.0

    def _status_string(self) -> str:
        cfg = get_config()
        model = cfg.model or "?"
        total = self._session.token_usage.total_tokens
        limit = get_model_context_limit(model)
        pct = self._context_pct(limit)
        return f"{model}  {total:,} tokens  {fmt_pct(pct)}  |  xAgent - {self._session.name}"

    def _wave_color_at(self, index: int, now: float):
        best = None
        for t0 in self._waves:
            distance = int((now - t0) * _WAVE_SPEED) - index
            if 0 <= distance < len(_BLUE_WAVE):
                if best is None or distance < best:
                    best = distance
        return _BLUE_WAVE[best] if best is not None else None

    def _update_status(self) -> None:
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
        self._chat().mount(Vertical(Static(text), classes="bubble user-bubble"))
        self._scroll_end()

    def _append_error(self, text: str) -> None:
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
        self._tick_spinners()
        self._tick_status_wave()

    def _tick_status_wave(self) -> None:
        if not self._busy:
            if self._waves:
                self._waves.clear()
                self._update_status()
            return
        now = time.monotonic()
        if not self._waves or now - self._waves[-1] >= _WAVE_INTERVAL:
            self._waves.append(now)
        limit = len(self._status_string()) + len(_BLUE_WAVE)
        self._waves = [t0 for t0 in self._waves if (now - t0) * _WAVE_SPEED < limit]
        self._update_status()

    def _ensure_thinking(self):
        cur = self._current
        if cur["thinking"] is None:
            st = Static("")
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
        self._start_spinner(cur["thinking_title"])
        return cur["thinking"]

    def _ensure_reply(self):
        cur = self._current
        if cur["reply"] is None:
            st = Static("")
            self._chat().mount(Vertical(st, classes="bubble reply-bubble"))
            cur["reply"] = st
        return cur["reply"]

    def _add_tool_streaming(self, tc_id: str, name: str) -> None:
        title, t = tool_render(name, {}, None, False)
        st = Static(t)
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

    def _update_tool_stream(self, tc_id: str) -> None:
        now = time.monotonic()
        if now - self._current["last_stream_render"] < 0.03:
            return
        self._current["last_stream_render"] = now
        pair = self._current["tools"].get(tc_id)
        info = self._current["tool_buffers"].get(tc_id)
        if pair is None or info is None:
            return
        st, col = pair
        args = stream_args(info["raw"], info["name"])
        title, t = tool_render(info["name"], args, None, False)
        title = title.strip() or info["name"]
        if str(col._title.label) != title:
            col._title.label = title
            self._render_spinner(col._title)
        st.update(t)
        self._scroll_end()

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
        st.update(t)
        if is_error:
            col.add_class("tool-error")

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
            cur["thinking"] = None
            cur["reasoning_text"] = ""
            self._ensure_thinking()
        elif t == "reasoning-delta":
            cur["reasoning_text"] += event.data
            self._ensure_thinking().update(cur["reasoning_text"])
            self._scroll_end()
        elif t == "reasoning-end":
            self._stop_spinner(cur.get("thinking_title"))
        elif t == "text-start":
            cur["reply"] = None
            cur["reply_text"] = ""
            self._ensure_reply()
        elif t == "text-delta":
            cur["reply_text"] += event.data
            self._ensure_reply().update(cur["reply_text"])
            self._scroll_end()
        elif t == "tool-input-start":
            data = event.data
            self._add_tool_streaming(data["id"], data.get("name", ""))
            self._scroll_end()
        elif t == "tool-input-delta":
            data = event.data
            info = self._current["tool_buffers"].get(data["id"])
            if info is not None:
                info["raw"] += data.get("delta", "")
                self._update_tool_stream(data["id"])
        elif t == "tool-input-end":
            data = event.data
            self._current["last_stream_render"] = 0.0
            self._update_tool_stream(data["id"])
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
        cur = self._current
        if cur and cur["steps"] > 0:
            self._add_summary(cur["tokens"], elapsed)
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
        self._session = self._sm.create(path=_PROJECT_ROOT, persist=False)
        self._ctx_usage_tokens = 0
        self._chat().remove_children()
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
        self._scroll_end()
        self.query_one("#input", ChatInput).focus()

    def _render_messages(self) -> None:
        chat = self._chat()
        chat.remove_children()
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
                chat.mount(Vertical(Static(msg.get("content", "")), classes="bubble user-bubble"))
                continue
            if role == "assistant":
                reasoning = msg.get("reasoning_content", "") or ""
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", []) or []

                if reasoning:
                    chat.mount(Collapsible(
                        Static(reasoning),
                        title="Thinking",
                        classes="bubble thinking-bubble",
                        collapsed=True,
                        collapsed_symbol="▸",
                        expanded_symbol="▾",
                    ))

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    result = clean_result(name, tool_results.get(tc.get("id", ""), ""))
                    is_error = is_error_result(name, result)
                    title, t = tool_render(name, args, result, is_error)
                    classes = "bubble tool-bubble" + (" tool-error" if is_error else "")
                    chat.mount(Collapsible(
                        Static(t),
                        title=title,
                        classes=classes,
                        collapsed=True,
                        collapsed_symbol="▸",
                        expanded_symbol="▾",
                    ))

                if content:
                    chat.mount(Vertical(Static(content), classes="bubble reply-bubble"))
                    if self._is_turn_end(messages, idx):
                        cfg = get_config()
                        model = cfg.model or "?"
                        total = self._session.token_usage.total_tokens
                        chat.mount(Vertical(
                            Static(f"{model}  {total:,} tokens"),
                            classes="summary-bubble",
                        ))

    @staticmethod
    def _is_turn_end(messages: list, idx: int) -> bool:
        for msg in messages[idx + 1:]:
            if msg.get("role") == "tool":
                continue
            return msg.get("role") != "assistant"
        return True

    def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._busy:
            self._append_error("Agent is busy, please wait.")
            return
        if text == "/new":
            self._new_chat()
            return
        if text.startswith("/session"):
            parts = text.split(None, 1)
            if len(parts) == 2:
                self._switch_session(parts[1])
            else:
                self._append_error("Usage: /session <session-id>")
            return
        self._send(text)

    def _send(self, text: str) -> None:
        self._busy = True
        self._waves.clear()
        self._current = {
            "steps": 0,
            "tokens": 0,
            "reasoning_text": "",
            "reply_text": "",
            "thinking": None,
            "reply": None,
            "tools": {},
            "tool_inputs": {},
            "tool_buffers": {},
            "last_stream_render": 0.0,
        }
        self._append_user(text)
        if self._session.name == "New Session":
            self.run_worker(partial(self._name_worker, text), name="naming", group="naming", thread=True)
        self.run_worker(partial(self._turn_worker, text), name="turn", group="turn", thread=True, exclusive=True)

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        self._handle_input(message.text)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-box"):
            pass

        with Vertical(id="input-box"):
            yield ChatInput(soft_wrap=True, id="input")

        with Vertical(id="status-box"):
            yield Static("", id="status")

    def on_mount(self) -> None:
        self.title = "XAgent"
        self._update_status()
        self._scroll_end()
        self.set_interval(0.1, self._tick_animations, pause=False)
        self.query_one("#input", ChatInput).focus()


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
