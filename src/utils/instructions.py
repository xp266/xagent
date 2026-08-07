import os

from src.utils.paths import data_dir

_GLOBAL_FILE = "AGENTS.md"
_PROJECT_FILE = "AGENTS.md"


def global_instructions_path() -> str:
    return os.path.join(data_dir(), _GLOBAL_FILE)


def load_global_instructions() -> str:
    path = global_instructions_path()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def project_instructions_paths(root: str) -> list[str]:
    paths: list[str] = []
    cur = os.path.abspath(root)
    while True:
        candidate = os.path.join(cur, _PROJECT_FILE)
        if os.path.isfile(candidate):
            paths.append(candidate)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    paths.reverse()
    return paths


def load_project_instructions(root: str) -> str:
    parts: list[str] = []
    for path in project_instructions_paths(root):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except OSError:
            continue
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def build_system_prompt(base: str, project_root: str) -> str:
    parts = [base]
    global_text = load_global_instructions()
    if global_text:
        parts.append(
            f"<global_instructions {_GLOBAL_FILE}>\n{global_text}\n</global_instructions>"
        )
    project_text = load_project_instructions(project_root)
    if project_text:
        parts.append(
            f"<project_instructions {_PROJECT_FILE}>\n{project_text}\n</project_instructions>"
        )
    return "\n\n".join(parts)
