import json
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea, Collapsible
from textual.containers import Vertical, VerticalScroll


SESSION_PATH = os.path.expanduser("~/.local/share/xagent/sessions/8ecdddf0.json")


class XAgentTUI(App):
    CSS = """
    #title-box {
        height: 3;
        border: solid lightblue;
    }
    #chat-box {
        height: 1fr;
        border: solid lightblue;
    }
    TextArea {
        height: 6;
        border: solid lightblue;
    }
    #status-box {
        height: 3;
        border: solid lightblue;
    }
    .bubble {
        border: solid lightyellow;
        margin: 1 2;
        height: auto;
    }
    Collapsible.bubble > CollapsibleTitle {
        padding: 0 1;
    }
    Collapsible.bubble > CollapsibleContent {
        padding: 0 1 1 1;
    }
    Vertical.bubble {
        padding: 0 1;
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
                    Vertical(Static(msg.get("content", "")), classes="bubble")
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
                            classes="bubble",
                            collapsed=True,
                        )
                    )

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    arg_str = (
                        " ".join(f"{k}={v}" for k, v in args.items())
                        if args
                        else fn.get("arguments", "")
                    )
                    title = f"→ {name}  {arg_str}" if arg_str else f"→ {name}"
                    result = tool_results.get(tc.get("id", ""), "")
                    widgets.append(
                        Collapsible(
                            Static(result),
                            title=title,
                            classes="bubble",
                            collapsed=True,
                        )
                    )

                if content:
                    widgets.append(
                        Vertical(Static(content), classes="bubble")
                    )
                continue

        return widgets

    def compose(self) -> ComposeResult:
        cwd = str(Path.cwd())
        project = Path.cwd().name

        with Vertical(id="title-box"):
            yield Static(f"xAgent - {cwd} - {project}")

        with VerticalScroll(id="chat-box"):
            for w in self._build_chat_widgets():
                yield w

        yield TextArea(soft_wrap=True)

        tu = self._token_usage
        total = tu.get("total_tokens", 0)
        model = "gpt-4"
        status = f"模型: {model}  总token: {total}  上下文使用: 45%"
        with Vertical(id="status-box"):
            yield Static(status)

    def on_mount(self) -> None:
        self.title = "XAgent"


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
