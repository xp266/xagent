import os
import base64

from src.utils.media import sniff_mime, is_supported_image, read_image_file, normalize_image, make_data_url
from src.types.tools import Tool

MAX_READ_LINES = 2_000
MAX_READ_BYTES = 50 * 1024
MAX_LINE_LENGTH = 2_000
MAX_LINE_SUFFIX = "... (line truncated to 2000 chars)"
MAX_MEDIA_INGEST_BYTES = 20 * 1024 * 1024

_BINARY_EXTENSIONS = frozenset({
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war",
    ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".bin", ".dat", ".obj", ".o", ".a", ".lib",
    ".wasm", ".pyc", ".pyo",
})


def _is_binary(path: str, raw: bytes) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BINARY_EXTENSIONS:
        return True
    if b"\x00" in raw:
        return True
    n = len(raw)
    if n == 0:
        return False
    printable = sum(1 for b in raw if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D))
    return printable < n * 0.7


def _list_directory(path: str, offset: int = 0, limit: int = 0) -> dict:
    entries = []
    try:
        names = os.listdir(path)
    except OSError as e:
        return {"title": path, "output": f"Failed to list directory: {e}", "metadata": {"error": True}}

    for name in names:
        full = os.path.join(path, name)
        try:
            t = "directory" if os.path.isdir(full) else "file"
        except OSError:
            t = "file"
        entries.append({"path": name + ("/" if t == "directory" else ""), "type": t})

    entries.sort(key=lambda e: (0 if e["type"] == "directory" else 1, e["path"]))

    total = len(entries)
    start = (offset - 1) if offset > 0 else 0
    end = total
    if limit > 0:
        end = min(start + limit, total)

    selected = entries[start:end]
    truncated = end < total

    lines = [f"{e['path']}  ({e['type']})" for e in selected]
    output = "\n".join(lines)
    if truncated:
        output += f"\n... ({total} total, showing first {len(selected)} entries)"

    return {
        "title": path,
        "output": output,
        "metadata": {"total": total, "truncated": truncated, "next": end + 1 if truncated else None},
    }


def _read_file(path: str, offset: int = 0, limit: int = 0) -> dict:
    file_size = os.path.getsize(path)

    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
    except OSError as e:
        return {"title": path, "output": f"Failed to read: {e}", "metadata": {"error": True}}

    img_mime = sniff_mime(sample)
    if img_mime and is_supported_image(img_mime):
        if file_size > MAX_MEDIA_INGEST_BYTES:
            return {
                "title": path,
                "output": f"Image exceeds {MAX_MEDIA_INGEST_BYTES} byte limit: {path}",
                "metadata": {"error": True},
            }
        with open(path, "rb") as f:
            full_data = f.read()
        full_data = normalize_image(full_data, img_mime)
        b64 = base64.b64encode(full_data).decode("ascii")
        data_url = f"data:{img_mime};base64,{b64}"
        return {
            "title": path,
            "output": f"Image read successfully: {os.path.basename(path)} ({img_mime}, {len(full_data)} bytes)",
            "metadata": {"mime": img_mime, "size": len(full_data)},
            "attachments": [
                {"type": "file", "mime": img_mime, "url": data_url, "filename": os.path.basename(path)},
            ],
        }

    if _is_binary(path, sample):
        return {"title": path, "output": f"Cannot read binary file: {path}", "metadata": {"error": True}}

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return {"title": path, "output": f"Failed to read: {e}", "metadata": {"error": True}}

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"title": path, "output": f"File is not valid UTF-8 text: {path}", "metadata": {"error": True}}

    total_lines = len(text.splitlines())
    paged = offset > 0 or limit > 0 or total_lines * 80 > MAX_READ_BYTES

    offset = offset or 1
    limit = limit or MAX_READ_LINES
    if limit > MAX_READ_LINES:
        limit = MAX_READ_LINES

    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = offset - 1
    end = min(start + limit, total)

    if start >= total:
        return {"title": path, "output": f"Start line {offset} exceeds total lines {total}", "metadata": {"error": True}}

    shown = lines[start:end]
    truncated_text = end < total

    content_lines = []
    for line in shown:
        content = line.rstrip("\n").rstrip("\r")
        if len(content) > MAX_LINE_LENGTH:
            content = content[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
        content_lines.append(content)

    content = "\n".join(content_lines)

    return {
        "title": path,
        "output": content,
        "metadata": {
            "name": os.path.basename(path),
            "total_lines": total,
            "offset": offset,
            "truncated": truncated_text,
            "paged": paged,
        },
    }


def execute(path: str, offset: int = 0, limit: int = 0, **kwargs) -> dict:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return {"title": path, "output": f"Path does not exist: {path}", "metadata": {"error": True}}
    if os.path.isdir(path):
        return _list_directory(path, offset, limit)
    return _read_file(path, offset, limit)


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]

    if meta.get("mime") and meta.get("mime", "").startswith("image/"):
        return data["output"]

    if "entries" in data:
        entries = data["entries"]
        lines = [f"{e['path']}  ({e['type']})" for e in entries]
        result = "\n".join(lines)
        if data.get("truncated"):
            result += f"\n... ({meta.get('total', '?')} total, showing first {len(entries)} entries)"
        return result

    content = data.get("output", "")
    total = meta.get("total_lines", "?")
    name = meta.get("name", os.path.basename(data.get("title", "")))
    offset = meta.get("offset", 0)
    paged = meta.get("paged", False)

    if paged:
        result_lines = []
        for i, line in enumerate(content.split("\n"), start=offset):
            result_lines.append(f"{i}:{line}")
        end = offset + len(result_lines) - 1
        header = f"({name}, lines {offset}-{end}/{total})"
        if not result_lines:
            return header
        return header + "\n" + "\n".join(result_lines)

    lines = content.split("\n")
    result_lines = []
    for i, line in enumerate(lines, start=1):
        result_lines.append(f"{i}:{line}")
    header = f"({name}, {total} lines)"
    if not result_lines:
        return header
    return header + "\n" + "\n".join(result_lines)


tool = Tool(
    name="read",
    description="""Read a file or directory from the local filesystem.

Usage:
- Use absolute paths when possible
- Default: returns up to 2000 lines from the start
- Use offset to start from a specific line; limit to control how many lines
- Output: each line prefixed as `<line>: <content>` (e.g., file with "foo\n" returns "1: foo\n")
- Directories: one entry per line, no line numbers, trailing `/` for subdirectories
- Lines over 2000 characters are truncated
- Use grep for content search in large files; use glob to find files by name
- Supports images and PDFs (returned as file attachments)
- Call in parallel for multiple files; avoid tiny repeated slices""",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file or directory to read. Relative paths resolve from the current working directory; absolute paths inside the working directory are accepted.",
            },
            "offset": {
                "type": "integer",
                "description": "Starting read position (1-based line number or entry index)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines or entries to read",
            },
        },
        "required": ["path"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
