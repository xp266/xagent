import os
import re

from rich.text import Text
from textual.content import Content
from textual.highlight import guess_language

from src.mcp import is_mcp_tool
from src.ui.tui.markdown import _fence_body, render_markdown

_READ_HEADER_RE = re.compile(r"^\([^,]+(?:, \d+ lines|, lines [\d-]+/\d+)\)$")
_READ_LINE_RE = re.compile(r"^(\d+):(.*)$")

_CODE_TOOLS = {"write", "edit", "read"}
_BLOCK_TOOLS = {"write", "edit"}
_STREAM_PREVIEW_MAX = 20


def _lang_for(path: str) -> str:
    if not path:
        return "text"
    return guess_language(path, path)


def _code_block(lang: str, code: str) -> str:
    code = (code or "").rstrip("\n")
    return f"```{lang}\n{code}\n```\n"


def block_tool(name) -> bool:
    return name in _BLOCK_TOOLS


def _cap_tool(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def is_error_result(name, result):
    if not result:
        return False
    if is_mcp_tool(name):
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
    return f"Read {path}{_params_suffix(args, ('offset', 'limit'))}"


def tool_render(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "bash":
        cmd = args.get("command", "")
        t = Text()
        t.append(f"$ {cmd}")
        if result:
            t.append("\n" + result, style="#9B9B9B")
        return _cap_tool(name), t
    if name == "read":
        path = args.get("path", "") or args.get("filePath", "")
        if path:
            return _read_title(path, args), Text(result)
    if name == "web":
        return f"{_cap_tool(name)}{_params_suffix(args)}", Text(result)
    if is_mcp_tool(name):
        return f"MCP({name})", Text(result)
    if args:
        title = f"{_cap_tool(name)}{_params_suffix(args)}"
    else:
        title = _cap_tool(name)
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
            if len(lines) > _STREAM_PREVIEW_MAX:
                body = f"( {len(lines)} lines, streaming )\n\n" + "\n".join(lines[-_STREAM_PREVIEW_MAX:])
            else:
                body = "\n".join(lines)
            body = f"```text\n{body}"
        else:
            body = f"```{_lang_for(path)}\n" + "\n".join(lines) + "\n```"
        if is_error and result:
            body = body.rstrip("\n") + f"\n\n{result}"
        return f"Write {path}", body
    if name == "edit":
        file_path = args.get("filePath", "") or args.get("path", "")
        old_str = args.get("oldString", "") or ""
        new_str = args.get("newString", "") or ""
        lang = _lang_for(file_path)
        rows = None
        if not is_error and not args.get("replaceAll"):
            rows = _edit_hunk(file_path, old_str, new_str)
        if rows is not None:
            diff = [f"{num} {marker} {text}" for num, marker, text in rows]
        else:
            diff = []
            num = 0
            for line in old_str.rstrip("\n").split("\n"):
                num += 1
                diff.append(f"{num} - {line}")
            if new_str:
                for line in new_str.rstrip("\n").split("\n"):
                    num += 1
                    diff.append(f"{num} + {line}")
        if preview:
            if len(diff) > _STREAM_PREVIEW_MAX:
                body = f"( {len(diff)} lines, streaming )\n\n" + "```text\n" + "\n".join(diff[-_STREAM_PREVIEW_MAX:])
            else:
                body = "```text\n" + "\n".join(diff)
        else:
            body = f"```{lang}@n\n" + "\n".join(diff) + "\n```"
        if is_error and result:
            body = body.rstrip("\n") + f"\n\n{result}"
        return f"Edit {file_path}", body
    return None


def tool_num_width(name: str, args: dict, result: str = "", is_error: bool = False) -> int:
    if name == "write":
        lines = (args.get("content", "") or "").rstrip("\n").split("\n")
        return len(str(max(1, len(lines))))
    if name == "read":
        body = read_result_to_lines(result)
        n = len(body.split("\n"))
        start = read_line_start(result, args)
        return len(str(max(1, start + n - 1)))
    if name == "edit":
        if not is_error and not args.get("replaceAll"):
            rows = _edit_hunk(
                args.get("filePath", "") or args.get("path", ""),
                args.get("oldString", "") or "",
                args.get("newString", "") or "",
            )
            if rows is not None:
                return len(str(max(r[0] for r in rows)))
        old_lines = (args.get("oldString", "") or "").rstrip("\n").split("\n")
        new_lines = (args.get("newString", "") or "").rstrip("\n").split("\n")
        return len(str(max(1, len(old_lines) + len(new_lines))))
    return 0


def tool_markdown(name, args, result, is_error, preview=False):
    result = result or ""
    if name == "read":
        path = args.get("path", "") or args.get("filePath", "")
        body = result
        if not is_error:
            body = read_result_to_lines(result)
        return _read_title(path, args), _code_block(_lang_for(path), body)
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
    return f"{round(pct)}% context"

_FENCE_LINE_RE = re.compile(r"^```([A-Za-z0-9_+.\-@]*)\s*$")


def render_tool_markdown(body: str, *, numbered: bool = False, line_number_start: int = 1, open: bool = False) -> Content:
    lines = body.split("\n")
    i = 0
    header: list[str] = []
    while i < len(lines) and not lines[i].startswith("```"):
        header.append(lines[i])
        i += 1
    if i >= len(lines):
        return render_markdown(body)
    info = lines[i][3:].strip()
    lang, _, flags = info.partition("@")
    diff_nums = flags == "n"
    lines = lines[i + 1:]
    rest: list[str] = []
    if open:
        code_lines = lines
    else:
        close_idx = None
        for k in range(len(lines) - 1, -1, -1):
            if _FENCE_LINE_RE.match(lines[k]):
                close_idx = k
                break
        if close_idx is None:
            code_lines = lines
        else:
            code_lines = lines[:close_idx]
            rest = lines[close_idx + 1:]
    parts: list = []
    if header:
        parts.append(render_markdown("\n".join(header)))
    code = "\n".join(code_lines).rstrip("\n")
    parts.append(_fence_body(code, lang or None, numbered=numbered, diff_nums=diff_nums, line_number_start=line_number_start))
    parts.append("\n")
    if rest:
        parts.append(render_markdown("\n".join(rest)))
    return Content.assemble(*parts)


