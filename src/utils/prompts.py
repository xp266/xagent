import os

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def load(name: str) -> str:
    with open(os.path.join(_DIR, f"{name}.md"), "r", encoding="utf-8") as f:
        return f.read().strip()
