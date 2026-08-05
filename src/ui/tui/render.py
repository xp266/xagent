import os
import re

from rich.text import Text
from textual.highlight import guess_language

_READ_HEADER_RE = re.compile(r"^\([^,]+(?:, \d+ lines|, lines [\d-]+/\d+)\)$")
_READ_LINE_RE = re.compile(r"^(\d+):(.*)$")

_CODE_TOOLS = {"write", "edit", "read"}
_BLOCK_TOOLS = {"write", "edit"}


def _lang_for(path: str) -> str:
    if not path:
        return "text"
    return guess_language(path, path)


def _code_block(lang: str, code: str) -> str:
    code = (code or "").rstrip("\n")
    return f"```{lang}\n{code}\n```\n"


def block_tool(name) -> bool:
    return name in _BLOCK_TOOLS


def is_error_result(name, result):
    if not result:
        return False
    if name == "bash":
        return not result.startswith("Command exited with code 0.")
    if name == "read":
        error_prefixes = (
            "failed to", "cannot read", "cannot list", "path does not exist",
            "start line", "image exceeds", "file is not valid", "is not a directory",
            "not a directory", "unable to", "permission denied",
        )
        low = result.lower()
        return any(low.startswith(p) for p in error_prefixes)
    _PREFIX_ONLY = {
        "web": ("search failed:", "failed to fetch url:", "error fetching url:", "error:"),
        "glob": ("path is not a directory:",),
        "grep": ("path does not exist:", "invalid regex pattern:"),
    }
    prefixes = _PREFIX_ONLY.get(name)
    if prefixes is not None:
        low = result.lower()
        return any(low.startswith(p) for p in prefixes)
    success_markers = {
        "write": ("Wrote file successfully", "Created file successfully", "Updated file successfully"),
        "edit": ("Edited file successfully",),
    }
    markers = success_markers.get(name, ())
    if any(result.startswith(m) for m in markers):
        return False
    error_keywords = (
        "failed", "error", "permission denied", "not found", "not a directory",
        "cannot", "unable to", "timed out", "exceeded timeout", "is not valid",
        "no changes to apply", "must not be empty", "does not exist", "binary",
    )
    low = result.lower()
    return any(k in low for k in error_keywords)


def clean_result(name, result):
    if name == "read" and result:
        lines = result.split("\n")
        if _READ_HEADER_RE.match(lines[0]):
            return "\n".join(lines[1:])
    return result


def read_result_to_lines(result: str) -> str:
    lines = []
    for line in (result or "").split("\n"):
        m = _READ_LINE_RE.match(line)
        lines.append(m.group(2) if m else line)
    return "\n".join(lines).rstrip("\n")


def read_line_start(result: str, args: dict) -> int:
    for line in (result or "").split("\n"):
        m = _READ_LINE_RE.match(line)
        if m:
            return int(m.group(1))
    offset = args.get("offset")
    try:
        return int(offset) if offset else 1
    except (TypeError, ValueError):
        return 1


def _params_suffix(args: dict, keys: tuple | None = None) -> str:
    parts = []
    for key, val in args.items():
        if keys is not None and key not in keys:
            continue
        if val is None or (isinstance(val, str) and not val):
            continue
        parts.append(f"{key}={val}")
    return f" [{','.join(parts)}]" if parts else ""


def _read_title(path: str, args: dict) -> str:
    return f"read {path}{_params_suffix(args, ('offset', 'limit'))}"


def tool_render(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "bash":
        cmd = args.get("command", "")
        t = Text()
        t.append(f"$ {cmd}")
        if result:
            t.append("\n" + result, style="#9B9B9B")
        return "bash", t
    if name == "read":
        path = args.get("path", "") or args.get("filePath", "")
        if path:
            return _read_title(path, args), Text(result)
    if name == "web":
        return f"web{_params_suffix(args)}", Text(result)
    if name == "write":
        path = args.get("path", "")
        write_content = args.get("content", "")
        lines = write_content.rstrip("\n").split("\n")
        if preview:
            max_preview = 100
            if len(lines) > max_preview:
                t = Text(f"({len(lines)} lines, streaming)\n")
                t.append("\n".join(lines[-max_preview:]))
            else:
                t = Text("\n".join(lines))
        else:
            numbered = "\n".join(f"{i} {line}" for i, line in enumerate(lines, 1))
            t = Text(numbered)
        if is_error and result:
            t.append(f"\n\n{result}")
        return f"write {path}", t
    if name == "edit":
        file_path = args.get("filePath", "")
        old_str = args.get("oldString", "") or ""
        new_str = args.get("newString", "") or ""
        old_lines = old_str.rstrip("\n").split("\n")
        new_lines = new_str.rstrip("\n").split("\n")
        t = Text()
        for line in old_lines:
            t.append("- ", style="#FF9E9E")
            t.append(f"{line}\n")
        for line in new_lines:
            t.append("+ ", style="#9FD28A")
            t.append(f"{line}\n")
        t.rstrip()
        if is_error and result:
            t.append(f"\n\n{result}")
        return f"edit {file_path}", t
    if args:
        title = f"{name}{_params_suffix(args)}"
    else:
        title = name
    return title, Text(result)


def _edit_hunk(file_path: str, old_str: str, new_str: str, ctx: int = 3):
    if not file_path or not old_str:
        return None
    try:
        with open(os.path.abspath(os.path.expanduser(file_path)), "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    content = content.replace("\r\n", "\n")
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    old_lines = old_str.rstrip("\n").split("\n")
    new_lines = new_str.rstrip("\n").split("\n")
    delta = len(new_lines) - len(old_lines)

    if new_str and (idx := content.find(new_str)) >= 0:
        start = content[:idx].count("\n") + 1
        in_block = len(new_lines)
        after_offset = 0
    elif (idx := content.find(old_str)) >= 0:
        start = content[:idx].count("\n") + 1
        in_block = len(old_lines)
        after_offset = delta
    else:
        return None

    rows: list = []
    for i in range(max(0, start - 1 - ctx), start - 1):
        rows.append((i + 1, " ", lines[i]))
    for i, text in enumerate(old_lines):
        rows.append((start + i, "-", text))
    if new_str:
        for i, text in enumerate(new_lines):
            rows.append((start + i, "+", text))
    after_begin = start - 1 + in_block
    for i in range(after_begin, min(len(lines), after_begin + ctx)):
        rows.append((i + 1 + after_offset, " ", lines[i]))
    return rows


def tool_block(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "write":
        path = args.get("path", "")
        content = args.get("content", "")
        lines = content.rstrip("\n").split("\n")
        if preview:
            max_preview = 100
            if len(lines) > max_preview:
                body = f"( {len(lines)} lines, streaming )\n\n" + "\n".join(lines[-max_preview:])
            else:
                body = "\n".join(lines)
            body = f"```\n{body}"
        else:
            body = f"```{_lang_for(path)}\n" + "\n".join(lines) + "\n```"
        if is_error and result:
            body = body.rstrip("\n") + f"\n\n{result}"
        return f"write {path}", body
    if name == "edit":
        file_path = args.get("filePath", "") or args.get("path", "")
        old_str = args.get("oldString", "") or ""
        new_str = args.get("newString", "") or ""
        lang = _lang_for(file_path)
        rows = None
        if not is_error and not args.get("replaceAll"):
            rows = _edit_hunk(file_path, old_str, new_str)
        if rows is not None:
            body = f"```{lang}@n\n" + "\n".join(
                f"{num} {marker} {text}" for num, marker, text in rows
            )
        else:
            diff = []
            for line in old_str.rstrip("\n").split("\n"):
                diff.append(f"- {line}")
            if new_str:
                for line in new_str.rstrip("\n").split("\n"):
                    diff.append(f"+ {line}")
            body = f"```{lang}\n" + "\n".join(diff)
        if not preview:
            body += "\n```"
        if is_error and result:
            body = body.rstrip("\n") + f"\n\n{result}"
        return f"edit {file_path}", body
    return None


def tool_markdown(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "write":
        path = args.get("path", "")
        content = args.get("content", "")
        lines = content.rstrip("\n").split("\n")
        if preview:
            max_preview = 100
            if len(lines) > max_preview:
                body = f"( {len(lines)} lines, streaming )\n\n" + "\n".join(lines[-max_preview:])
            else:
                body = "\n".join(lines)
        else:
            body = content
        if is_error and result:
            body = body.rstrip("\n") + f"\n\n{result}"
        return f"write {path}", _code_block(_lang_for(path), body)
    if name == "read":
        path = args.get("path", "") or args.get("filePath", "")
        body = result
        if not is_error:
            body = read_result_to_lines(result)
        return _read_title(path, args), _code_block(_lang_for(path), body)
    if name == "edit":
        file_path = args.get("filePath", "") or args.get("path", "")
        old_str = args.get("oldString", "") or ""
        new_str = args.get("newString", "") or ""
        diff = []
        for line in old_str.rstrip("\n").split("\n"):
            diff.append(f"- {line}")
        for line in new_str.rstrip("\n").split("\n"):
            diff.append(f"+ {line}")
        body = "\n".join(diff)
        if is_error and result:
            body = body + f"\n\n{result}"
        return f"edit {file_path}", _code_block(_lang_for(file_path), body)
    return None


def code_tool(name) -> bool:
    return name in _CODE_TOOLS


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_pct(pct: float) -> str:
    return f"{pct:g}% context"
