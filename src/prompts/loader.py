import os

_DIR = os.path.dirname(__file__)


def load(name: str) -> str:
    with open(os.path.join(_DIR, f"{name}.md"), "r", encoding="utf-8") as f:
        return f.read().strip()
