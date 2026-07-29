import os

from src.types.tools import Tool


def execute(path: str, content: str, **kwargs) -> dict:
    path = os.path.abspath(os.path.expanduser(path))
    _BOM_UTF8 = b"\xef\xbb\xbf"

    existed = os.path.exists(path)
    has_bom = False
    if existed:
        try:
            with open(path, "rb") as f:
                existing = f.read(3)
            has_bom = existing == _BOM_UTF8
        except OSError:
            pass

    if has_bom:
        raw = content.encode("utf-8-sig")
    else:
        raw = content.encode("utf-8")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except OSError as e:
        return {"title": path, "output": f"Write failed with error: {e}", "metadata": {"error": True}}

    op = "Wrote" if existed else "Created"
    return {
        "title": path,
        "output": f"{op} file successfully: {path}",
        "metadata": {"operation": "write", "target": path, "existed": existed},
    }


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]
    return data.get("output", "")


tool = Tool(
    name="write",
    description="""Writes a file to the local filesystem (overwrites if exists).
    
- Prefer Edit tool for existing files; only use Write for new files
- Do NOT create documentation files (*.md, README) unless explicitly requested
- No emojis unless the user explicitly asks""",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write. Relative paths resolve from the current working directory.",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["path", "content"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
