from rich.text import Text

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


def build_logo_text() -> Text:
    width = max(len(row) for row in LOGO_ART)
    text = Text()
    for layer, row in enumerate(LOGO_ART, start=1):
        for ch in row.ljust(width):
            if ch == " ":
                text.append(" ")
            elif ch == "▒":
                text.append("▒", style=_DEFAULT_COLOR)
            else:
                text.append("█", style=_LAYER_COLORS[layer])
        text.append("\n")
    text.rstrip()
    return text
