from rich.text import Text
from textual.widgets import Static

LOGO_ART = [
    "██▒   ██▒   ███▒     █████▒   ███████▒ ██▒   ██▒ ███████▒ ",
    "██▒   ██▒  ██▒ ██▒  ██▒   ██▒ ██▒      ███▒  ██▒   ██▒    ",
    " █████▒   ██▒   ██▒ ██▒       ██▒      ████▒ ██▒   ██▒    ",
    "  ███▒    ████████▒ ██▒  ███▒ █████▒   ██▒ ████▒   ██▒    ",
    " █████▒   ██▒   ██▒ ██▒   ██▒ ██▒      ██▒  ███▒   ██▒    ",
    "██▒   ██▒ ██▒   ██▒ ██▒   ██▒ ██▒      ██▒   ██▒   ██▒    ",
    "██▒   ██▒ ██▒   ██▒  █████▒   ███████▒ ██▒   ██▒   ██▒    ",
]

_LAYER_COLORS = {
    1: "#00ffff",
    2: "#00f0ff",
    3: "#00e0ff",
    4: "#00ccff",
    5: "#00bcff",
    6: "#00acff",
    7: "#0099ff",
}

_DEFAULT_COLOR = "#5B5B5B"

_WIDTH = max(len(row) for row in LOGO_ART)


def build_logo_text() -> Text:
    text = Text()
    for layer, row in enumerate(LOGO_ART, start=1):
        for ch in row.ljust(_WIDTH):
            if ch == " ":
                text.append(" ")
                continue
            base = _LAYER_COLORS[layer] if ch == "█" else _DEFAULT_COLOR
            text.append(ch, style=base)
        text.append("\n")
    text.rstrip()
    return text


class LogoWidget(Static):
    ALLOW_SELECT = False

    def on_mount(self) -> None:
        self.update(build_logo_text())
