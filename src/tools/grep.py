import os
import re

from src.types.tools import Tool


def _match_include(filename: str, include: str) -> bool:
    import fnmatch
    for part in include.split(","):
        part = part.strip()
        if fnmatch.fnmatch(filename, part):
            return True
    return False


def execute(pattern: str, path: str = "", include: str = "", **kwargs) -> dict:
    search_dir = path or os.getcwd()
    search_dir = os.path.abspath(os.path.expanduser(search_dir))

    if not os.path.exists(search_dir):
        return {
            "title": pattern,
            "output": f"Path does not exist: {search_dir}",
            "metadata": {"error": True},
        }

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {
            "title": pattern,
            "output": f"Invalid regex pattern: {e}",
            "metadata": {"error": True},
        }

    matches = []
    limit = 100
    _SKIP_DIRS = frozenset({".git", "__pycache__"})

    if os.path.isfile(search_dir):
        files = [search_dir]
    else:
        files = []
        for root, dirs, fnames in os.walk(search_dir):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            fnames.sort()
            for fname in fnames:
                if include and not _match_include(fname, include):
                    continue
                files.append(os.path.join(root, fname))
                if len(files) >= 5000:
                    break
            if len(files) >= 5000:
                break

    _BINARY_EXTS = frozenset({
        ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar",
        ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".bin", ".dat", ".obj", ".o", ".a", ".lib", ".wasm", ".pyc",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
    })

    for fpath in files:
        ext = os.path.splitext(fpath)[1].lower()
        if ext in _BINARY_EXTS:
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if regex.search(line.rstrip("\n").rstrip("\r")):
                        matches.append({
                            "path": os.path.abspath(fpath),
                            "line": i,
                            "text": line.rstrip("\n").rstrip("\r"),
                        })
                        if len(matches) >= limit:
                            break
        except (OSError, UnicodeDecodeError):
            continue
        if len(matches) >= limit:
            break

    if not matches:
        return {
            "title": pattern,
            "metadata": {"matches": 0, "truncated": False},
            "output": "No files found",
        }

    truncated = len(matches) >= limit
    output_lines = [f"Found {len(matches)} matches{'(more matches available)' if truncated else ''}"]

    current = ""
    for m in matches:
        if current != m["path"]:
            if current:
                output_lines.append("")
            current = m["path"]
            output_lines.append(f"{m['path']}:")
        output_lines.append(f"  Line {m['line']}: {m['text']}")

    if truncated:
        output_lines.append("")
        output_lines.append("(Results truncated. Consider using a more specific path or pattern.)")

    return {
        "title": pattern,
        "metadata": {"matches": len(matches), "truncated": truncated},
        "output": "\n".join(output_lines),
    }


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]
    return data.get("output", "")


tool = Tool(
    name="grep",
    description="""- Fast regex content search for any codebase size
- Supports full regex syntax (e.g. "log.*Error", "function\\s+\\w+")
- Returns file paths and line numbers with matching lines
- Use this tool to find files containing specific patterns
- To count matches, use Bash with `rg` (ripgrep) directly""",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. Defaults to the current working directory.",
            },
            "include": {
                "type": "string",
                "description": 'Comma-separated file patterns to include in the search (e.g. "*.py,*.md")',
            },
        },
        "required": ["pattern"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
