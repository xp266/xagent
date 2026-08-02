import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")


def main() -> None:
    from src.ui.tui.app import run_tui

    run_tui()


if __name__ == "__main__":
    main()
