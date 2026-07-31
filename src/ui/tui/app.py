import json
import os
import re
import time
from functools import partial

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Static, TextArea, Collapsible
from rich.text import Text

from src.agent import get_session_manager, run_session_turn, name_session_from_first_message
from src.ai.capabilities import get_model_context_limit
from src.utils.config import get_config
from src.types.events import StreamEvent

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_READ_HEADER_RE = re.compile(r"^\([^,]+(?:, \d+ lines|, lines [\d-]+/\d+)\)$")

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _unescape_json(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _json_field(raw: str, key: str) -> str:
    """Extract a completed string field value from streamed JSON args."""
    m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        return ""
    return _unescape_json(m.group(1))


def _json_tail_field(raw: str, key: str) -> str:
    """Extract a string field value that may still be streaming (last field)."""
    m = re.search(rf'"{key}"\s*:\s*"(.*)$', raw, re.S)
    if not m:
        return ""
    val = re.sub(r'"\s*[,}}]?\s*$', "", m.group(1))
    return _unescape_json(val)


def _json_prefix_field(raw: str, key: str) -> str:
    """Extract a non-last string field value that may still be streaming."""
    m = re.search(rf'"{key}"\s*:\s*"(.*?)(?="\s*[,}}]|$)', raw, re.S)
    if not m:
        return ""
    return _unescape_json(m.group(1))


def _stream_args(raw: str, name: str) -> dict:
    """Best-effort parse of partial streamed tool args JSON."""
    if not raw:
        return {}
    if name == "bash":
        return {"command": _json_tail_field(raw, "command")}
    if name == "write":
        return {
            "path": _json_field(raw, "path") or _json_prefix_field(raw, "path"),
            "content": _json_tail_field(raw, "content"),
        }
    if name == "edit":
        return {
            "filePath": _json_field(raw, "filePath") or _json_prefix_field(raw, "filePath"),
            "oldString": _json_field(raw, "oldString") or _json_prefix_field(raw, "oldString"),
            "newString": _json_tail_field(raw, "newString"),
        }
    if name == "read":
        return {"filePath": _json_field(raw, "filePath") or _json_tail_field(raw, "filePath")}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class ChatInput(TextArea):

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "ctrl+m"):
            event.stop()
            event.prevent_default()
            text = self.text
            if text:
                self.clear()
                self.post_message(self.Submitted(text))
            return
        await super()._on_key(event)


class XAgentTUI(App):
    CSS = """
    #chat-box {
        height: 1fr;
        border: none;
        padding: 1;
        scrollbar-size: 2 1;
        scrollbar-color: #808080;
    }
    TextArea {
        height: 1fr;
        border: none;
        scrollbar-size: 0 0;
        background: transparent;
    }
    #input-box {
        height: 6;
        border: solid #334466;
        padding: 0;
    }
    TextArea .text-area--cursor-line {
        background: transparent;
    }
    #status-box {
        height: 1;
        border: none;
        padding: 0 0 0 1;
    }

    .bubble {
        border: none;
        margin: 0;
        height: auto;
        padding: 1;
    }
    Collapsible.bubble > CollapsibleTitle {
        padding: 0 1;
    }
    .bubble *:focus,
    .bubble *:hover {
        background-tint: transparent;
    }
    CollapsibleTitle:focus {
        background: transparent;
    }

    .user-bubble, .reply-bubble {
        padding: 1 1 1 2;
    }
    .user-bubble {
        background: #1A1A1A;
    }
    .thinking-bubble, .tool-bubble {
        background: transparent;
        padding: 1 1 0 1;
    }
    .thinking-bubble > CollapsibleTitle {
        color: #5B9BD5;
    }
    .tool-bubble > CollapsibleTitle {
        color: #70AD47;
    }
    .thinking-bubble > Contents > Static {
        color: #9B9B9B;
    }
    .tool-error > CollapsibleTitle {
        color: #FF5555;
    }
    .summary-bubble {
        height: 1;
        margin: 0 0 1 0;
        padding: 0 1 0 2;
    }
    """

    def __init__(self):
        super().__init__()
        self._sm = get_session_manager()
        self._session = self._sm.create(path=_PROJECT_ROOT)
        self._ctx_usage_tokens = 0
        self._busy = False
        self._current = None
        self._spinner_idx = 0
        self._spinners = {}

    @staticmethod
    def _is_error_result(name, result):
        if not result:
            return False
        if name == "bash":
            return not result.startswith("Command exited with code 0.")
        success_markers = {
            "write": ("Wrote file successfully", "Created file successfully", "Updated file successfully"),
            "edit": ("Edited file successfully",),
            "read": ("read successfully",),
        }
        markers = success_markers.get(name, ())
        if any(result.startswith(m) for m in markers):
            return False
        error_keywords = (
            "failed", "error", "permission denied", "not found", "not a directory",
            "cannot", "unable to", "timed out", "exceeded timeout", "is not valid",
            "no changes to apply", "must not be empty", "does not exist", "binary",
        )
        low = result.lower()
        return any(k in low for k in error_keywords)

    @staticmethod
    def _clean_result(name, result):
        if name == "read" and result:
            lines = result.split("\n")
            if _READ_HEADER_RE.match(lines[0]):
                return "\n".join(lines[1:])
        return result

    @staticmethod
    def _tool_render(name, args, result, is_error):
        result = result or ""
        if name == "bash":
            cmd = args.get("command", "")
            t = Text()
            t.append(f"$ {cmd}", style="bold #70AD47")
            if result:
                t.append("\n" + result, style="bold #FF5555" if is_error else "#9B9B9B")
            return "bash", t
        if name == "write":
            path = args.get("path", "")
            write_content = args.get("content", "")
            lines = write_content.rstrip("\n").split("\n")
            numbered = "\n".join(f"{i} {line}" for i, line in enumerate(lines, 1))
            t = Text(numbered)
            if is_error and result:
                t.append(f"\n\n{result}", style="bold #FF5555")
            return f"write {path}", t
        if name == "edit":
            file_path = args.get("filePath", "")
            old_str = args.get("oldString", "") or ""
            new_str = args.get("newString", "") or ""
            old_lines = old_str.rstrip("\n").split("\n")
            new_lines = new_str.rstrip("\n").split("\n")
            t = Text()
            for line in old_lines:
                t.append("- ", style="#FF9E9E")
                t.append(f"{line}\n")
            for line in new_lines:
                t.append("+ ", style="#9FD28A")
                t.append(f"{line}\n")
            t.rstrip()
            if is_error and result:
                t.append(f"\n\n{result}", style="bold #FF5555")
            return f"edit {file_path}", t
        if args:
            arg_str = " ".join(f"{k}={v}" for k, v in args.items())
            title = f"{name}  {{{arg_str}}}"
        else:
            title = name
        return title, Text(result, style="bold #FF5555" if is_error else None)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    @staticmethod
    def _fmt_pct(pct: float) -> str:
        return f"{pct:g}% context"

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

    def _update_status(self) -> None:
        cfg = get_config()
        model = cfg.model or "?"
        total = self._session.token_usage.total_tokens
        limit = get_model_context_limit(model)
        pct = self._context_pct(limit)
        status = f"{model}  {total:,} tokens  {self._fmt_pct(pct)}  |  xAgent - {self._session.name}"
        self.query_one("#status", Static).update(status)

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
        title, t = self._tool_render(name, {}, None, False)
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
        args = _stream_args(info["raw"], info["name"])
        title, t = self._tool_render(info["name"], args, None, False)
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
        title, t = self._tool_render(name, args, None, False)
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
        title, t = self._tool_render(name, args, result, is_error)
        st.update(t)
        if is_error:
            col.add_class("tool-error")

    def _add_summary(self, tokens: int, elapsed: float) -> None:
        cfg = get_config()
        model = cfg.model or "?"
        summary = f"{model}  {tokens:,} tokens  {self._fmt_duration(elapsed)}"
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
            result = self._clean_result(data["name"], data.get("result", ""))
            self._set_tool_result(
                data["id"],
                data["name"],
                result,
                self._is_error_result(data["name"], result),
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
        self._session = self._sm.create(path=_PROJECT_ROOT)
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

        for msg in self._session.messages:
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
                    result = self._clean_result(name, tool_results.get(tc.get("id", ""), ""))
                    is_error = self._is_error_result(name, result)
                    title, t = self._tool_render(name, args, result, is_error)
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
                    cfg = get_config()
                    model = cfg.model or "?"
                    total = self._session.token_usage.total_tokens
                    chat.mount(Vertical(
                        Static(f"{model}  {total:,} tokens"),
                        classes="summary-bubble",
                    ))

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
        self.set_interval(0.1, self._tick_spinners, pause=False)
        self.query_one("#input", ChatInput).focus()


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
