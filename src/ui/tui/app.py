import json
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea, Collapsible
from textual.containers import Vertical, VerticalScroll


SESSION_PATH = os.path.expanduser("~/.local/share/xagent/sessions/8ecdddf0.json")


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
    .thinking-bubble > CollapsibleContent > Static,
    .tool-bubble > CollapsibleContent > Static {
        color: #808080;
    }
    .summary-bubble {
        height: 1;
        padding: 0 1 0 2;
    }
    """

    def __init__(self):
        super().__init__()
        with open(SESSION_PATH) as f:
            data = json.load(f)
        self._messages = data.get("messages", [])
        self._token_usage = data.get("token_usage", {})

    def _build_chat_widgets(self):
        tool_results = {}
        for msg in self._messages:
            if msg.get("role") == "tool":
                tool_results[msg["tool_call_id"]] = msg["content"]

        widgets = []
        for msg in self._messages:
            role = msg.get("role", "")
            if role == "system":
                continue

            if role == "user":
                widgets.append(
                    Vertical(Static(msg.get("content", "")), classes="bubble user-bubble")
                )
                continue

            if role == "assistant":
                reasoning = msg.get("reasoning_content", "") or ""
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", []) or []

                if reasoning:
                    widgets.append(
                        Collapsible(
                            Static(reasoning),
                            title="Thinking",
                            classes="bubble thinking-bubble",
                            collapsed=True,
                            collapsed_symbol="▸",
                            expanded_symbol="▾",
                        )
                    )

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    result = tool_results.get(tc.get("id", ""), "")
                    if args:
                        arg_str = " ".join(f"{k}={v}" for k, v in args.items())
                        title = f"{name}  {{{arg_str}}}"
                    else:
                        title = name
                    widgets.append(
                        Collapsible(
                            Static(result),
                            title=title,
                            classes="bubble tool-bubble",
                            collapsed=True,
                            collapsed_symbol="▸",
                            expanded_symbol="▾",
                        )
                    )

                if content:
                    widgets.append(
                        Vertical(Static(content), classes="bubble reply-bubble")
                    )
                    total_tokens = self._token_usage.get("total_tokens", 0)
                    summary = f"模型: gpt-4  Token: {total_tokens}  时间: 01h 23m 45s"
                    widgets.append(
                        Vertical(Static(summary), classes="summary-bubble")
                    )
                continue

        return widgets

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-box"):
            for w in self._build_chat_widgets():
                yield w

        with Vertical(id="input-box"):
            yield TextArea(soft_wrap=True)

        tu = self._token_usage
        total = tu.get("total_tokens", 0)
        model = "gpt-4"
        cwd = str(Path.cwd())
        project = Path.cwd().name
        status = f"模型: {model}  总token: {total}  上下文使用: 45%  |  xAgent - {cwd} - {project}"
        with Vertical(id="status-box"):
            yield Static(status)

    def on_mount(self) -> None:
        self.title = "XAgent"
        self.query_one("#chat-box").scroll_end(animate=False)


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
