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
    1: "#0c9cf0",
    2: "#0b90dc",
    3: "#09699d",
    4: "#075681",
    5: "#104c6c",
    6: "#0e3d58",
    7: "#0b3750",
}

_DEFAULT_COLOR = "#30373C"


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
