import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    load_dotenv(_ROOT / ".env")
    load_dotenv(override=True)
    from src.ui.tui.app import run_tui

    run_tui()


if __name__ == "__main__":
    main()
