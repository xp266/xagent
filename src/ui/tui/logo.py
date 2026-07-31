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
    1: "#0771ae",
    2: "#0771ae",
    3: "#0e5c85",
    4: "#0e5c85",
    5: "#0e5c85",
    6: "#093c5a",
    7: "#093c5a",
}

_DEFAULT_COLOR = "#3E525E"


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
