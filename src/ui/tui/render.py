import os
import re

from rich.text import Text
from textual.highlight import guess_language

_READ_HEADER_RE = re.compile(r"^\([^,]+(?:, \d+ lines|, lines [\d-]+/\d+)\)$")

_CODE_TOOLS = {"write", "edit", "read"}


def _lang_for(path: str) -> str:
    """Infer a pygments language name from a file path."""
    if not path:
        return "text"
    return guess_language(path, path)


def _code_block(lang: str, code: str) -> str:
    """Wrap code in a fenced markdown code block."""
    code = (code or "").rstrip("\n")
    return f"```{lang}\n{code}\n```\n"


def is_error_result(name, result):
    if not result:
        return False
    if name == "bash":
        return not result.startswith("Command exited with code 0.")
    success_markers = {
        "write": ("Wrote file successfully", "Created file successfully", "Updated file successfully"),
        "edit": ("Edited file successfully",),
        "read": ("read successfully",),
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


def tool_render(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "bash":
        cmd = args.get("command", "")
        t = Text()
        t.append(f"$ {cmd}", style="bold #70AD47")
        if result:
            t.append("\n" + result, style="bold #FF5555" if is_error else "#9B9B9B")
        return "bash", t
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
            t.append(f"\n\n{result}", style="bold #FF5555")
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
            t.append(f"\n\n{result}", style="bold #FF5555")
        return f"edit {file_path}", t
    if args:
        arg_str = " ".join(f"{k}={v}" for k, v in args.items())
        title = f"{name}  {{{arg_str}}}"
    else:
        title = name
    return title, Text(result, style="bold #FF5555" if is_error else None)


def tool_markdown(name, args, result, is_error, preview=False):
    """Render write/edit/read tool output as markdown with language highlighting.

    Returns (title, markdown_str) for code tools, or None to keep plain rendering.
    """
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
        if is_error and not result:
            body = result
        return f"read {path}", _code_block(_lang_for(path), body)
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
