from rich.text import Text
from textual.widgets import Static

from src.ui.tui.colors import _lerp_hex, _LOGO_DEFAULT_COLOR, _LOGO_LAYER_COLORS

LOGO_ART = [
    "██▒   ██▒   ███▒     █████▒   ███████▒ ██▒   ██▒ ███████▒ ",
    "██▒   ██▒  ██▒ ██▒  ██▒   ██▒ ██▒      ███▒  ██▒   ██▒    ",
    " █████▒   ██▒   ██▒ ██▒       ██▒      ████▒ ██▒   ██▒    ",
    "  ███▒    ████████▒ ██▒  ███▒ █████▒   ██▒ ████▒   ██▒    ",
    " █████▒   ██▒   ██▒ ██▒   ██▒ ██▒      ██▒  ███▒   ██▒    ",
    "██▒   ██▒ ██▒   ██▒ ██▒   ██▒ ██▒      ██▒   ██▒   ██▒    ",
    "██▒   ██▒ ██▒   ██▒  █████▒   ███████▒ ██▒   ██▒   ██▒    ",
]

SLANT = 1.5
BAND_WIDTH = 3
MAX_WHITE = 0.6
FPS = 60
SWEEP_SECONDS = 3
PAUSE_SECONDS = 5

_WIDTH = max(len(row) for row in LOGO_ART)
_HEIGHT = len(LOGO_ART)
_MAX_D = int((_HEIGHT - 1) * SLANT) + (_WIDTH - 1)
_SWEEP_FRAMES = _MAX_D + BAND_WIDTH + 1
_TOTAL_FRAMES = _SWEEP_FRAMES + FPS * PAUSE_SECONDS


def _lerp_white(hex_color: str, w: float) -> str:
    return _lerp_hex(hex_color, "#ffffff", w)


def _sweep_weight(row: int, col: int, tick: int) -> float:
    d = row * SLANT + col
    dist = abs(d - tick)
    if dist >= BAND_WIDTH:
        return 0.0
    return MAX_WHITE * (1.0 - dist / BAND_WIDTH)


def build_logo_text(tick: int = -1) -> Text:
    text = Text()
    for layer, row in enumerate(LOGO_ART, start=1):
        for col, ch in enumerate(row.ljust(_WIDTH)):
            if ch == " ":
                text.append(" ")
                continue
            if tick >= 0:
                w = _sweep_weight(layer - 1, col, tick)
            else:
                w = 0.0
            base = _LOGO_LAYER_COLORS[layer] if ch == "█" else _LOGO_DEFAULT_COLOR
            style = _lerp_white(base, w) if w > 0 and ch == "█" else base
            text.append(ch, style=style)
        text.append("\n")
    text.rstrip()
    return text


class LogoWidget(Static):
    ALLOW_SELECT = False

    def on_mount(self) -> None:
        self._tick = 0
        self.update(build_logo_text())
        self.set_interval(1 / FPS, self._tick_cb)

    def _tick_cb(self) -> None:
        self._tick += 1
        if not self.display:
            return
        t = self._tick % _TOTAL_FRAMES
        if t < _SWEEP_FRAMES:
            self.update(build_logo_text(t))
