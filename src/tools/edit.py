import os
import re

from src.types.tools import Tool


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n")


def _detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _convert_line_ending(text: str, ending: str) -> str:
    if ending == "\n":
        return text.replace("\r\n", "\n")
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _levenshtein(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    matrix = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        matrix[i][0] = i
    for j in range(len(b) + 1):
        matrix[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
    return matrix[len(a)][len(b)]


def _simple_replacer(content: str, find: str):
    yield find


def _line_trimmed_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    for i in range(len(original_lines) - len(search_lines) + 1):
        matches = True
        for j in range(len(search_lines)):
            if original_lines[i + j].strip() != search_lines[j].strip():
                matches = False
                break
        if matches:
            start = sum(len(original_lines[k]) + 1 for k in range(i))
            end = start
            for k in range(len(search_lines)):
                end += len(original_lines[i + k])
                if k < len(search_lines) - 1:
                    end += 1
            yield content[start:end]


def _block_anchor_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    first_line = search_lines[0].strip()
    last_line = search_lines[-1].strip()
    search_block_size = len(search_lines)
    max_line_delta = max(1, search_block_size // 4)

    candidates = []
    for i in range(len(original_lines)):
        if original_lines[i].strip() != first_line:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_line:
                actual_size = j - i + 1
                if abs(actual_size - search_block_size) <= max_line_delta:
                    candidates.append((i, j))
                break

    if not candidates:
        return

    SINGLE_THRESHOLD = 0.65
    MULTI_THRESHOLD = 0.65

    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        actual_size = end_line - start_line + 1
        lines_to_check = min(search_block_size - 2, actual_size - 2)
        similarity = 0.0
        if lines_to_check > 0:
            for j in range(1, min(search_block_size - 1, actual_size)):
                orig = original_lines[start_line + j].strip()
                search = search_lines[j].strip()
                max_len = max(len(orig), len(search))
                if max_len == 0:
                    continue
                dist = _levenshtein(orig, search)
                similarity += (1 - dist / max_len) / lines_to_check
                if similarity >= SINGLE_THRESHOLD:
                    break
        else:
            similarity = 1.0
        if similarity >= SINGLE_THRESHOLD:
            start = sum(len(original_lines[k]) + 1 for k in range(start_line))
            end = start
            for k in range(start_line, end_line + 1):
                end += len(original_lines[k])
                if k < end_line:
                    end += 1
            yield content[start:end]
        return

    best_match = None
    max_similarity = -1
    for start_line, end_line in candidates:
        actual_size = end_line - start_line + 1
        lines_to_check = min(search_block_size - 2, actual_size - 2)
        similarity = 0.0
        if lines_to_check > 0:
            for j in range(1, min(search_block_size - 1, actual_size)):
                orig = original_lines[start_line + j].strip()
                search = search_lines[j].strip()
                max_len = max(len(orig), len(search))
                if max_len == 0:
                    continue
                dist = _levenshtein(orig, search)
                similarity += 1 - dist / max_len
            similarity /= lines_to_check
        else:
            similarity = 1.0
        if similarity > max_similarity:
            max_similarity = similarity
            best_match = (start_line, end_line)

    if max_similarity >= MULTI_THRESHOLD and best_match:
        start_line, end_line = best_match
        start = sum(len(original_lines[k]) + 1 for k in range(start_line))
        end = start
        for k in range(start_line, end_line + 1):
            end += len(original_lines[k])
            if k < end_line:
                end += 1
        yield content[start:end]


def _whitespace_normalized_replacer(content: str, find: str):
    def norm(s):
        return re.sub(r"\s+", " ", s).strip()

    normalized_find = norm(find)
    lines = content.split("\n")

    for i, line in enumerate(lines):
        if norm(line) == normalized_find:
            yield line
        else:
            normalized_line = norm(line)
            if normalized_find in normalized_line:
                words = find.strip().split()
                if words:
                    escaped = [re.escape(w) for w in words]
                    pat = r"\s+".join(escaped)
                    match = re.search(pat, line)
                    if match:
                        yield match.group(0)

    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if norm(block) == normalized_find:
                yield block


def _indentation_flexible_replacer(content: str, find: str):
    def remove_indent(text: str):
        lines = text.split("\n")
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return text
        min_indent = min(len(re.match(r"^(\s*)", l).group(1)) for l in non_empty)
        return "\n".join(
            line if not line.strip() else line[min_indent:]
            for line in lines
        )

    normalized = remove_indent(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")

    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if remove_indent(block) == normalized:
            yield block


def _escape_normalized_replacer(content: str, find: str):
    def unescape(s):
        def _replace(m):
            mapping = {
                "n": "\n", "t": "\t", "r": "\r",
                "'": "'", '"': '"', "`": "`",
                "\\": "\\", "\n": "\n", "$": "$",
            }
            return mapping.get(m.group(1), m.group(0))
        return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", _replace, s)

    unescaped = unescape(find)
    if unescaped in content:
        yield unescaped

    lines = content.split("\n")
    find_lines = unescaped.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if unescape(block) == unescaped:
            yield block


def _trimmed_boundary_replacer(content: str, find: str):
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed in content:
        yield trimmed
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if block.strip() == trimmed:
            yield block


def _context_aware_replacer(content: str, find: str):
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines and find_lines[-1] == "":
        find_lines.pop()

    content_lines = content.split("\n")
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()

    for i in range(len(content_lines)):
        if content_lines[i].strip() != first_line:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() == last_line:
                block_lines = content_lines[i:j + 1]
                block = "\n".join(block_lines)
                if len(block_lines) == len(find_lines):
                    matching = 0
                    total = 0
                    for k in range(1, len(block_lines) - 1):
                        bl = block_lines[k].strip()
                        fl = find_lines[k].strip()
                        if bl or fl:
                            total += 1
                            if bl == fl:
                                matching += 1
                    if total == 0 or matching / total >= 0.5:
                        yield block
                break


def _multi_occurrence_replacer(content: str, find: str):
    start = 0
    while True:
        idx = content.find(find, start)
        if idx == -1:
            break
        yield find
        start = idx + len(find)


def _is_disproportionate(search: str, old_string: str) -> bool:
    old_lines = old_string.count("\n") + 1
    search_lines = search.count("\n") + 1
    if search_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(search.strip()) > max(len(old_string.strip()) + 500, len(old_string.strip()) * 4)


def _replace(content: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    if old_string == new_string:
        raise ValueError("No changes to apply: oldString and newString are identical.")
    if not old_string:
        raise ValueError("oldString must not be empty. Use write to create or overwrite a file.")

    not_found = True
    replacers = [
        _simple_replacer,
        _line_trimmed_replacer,
        _block_anchor_replacer,
        _whitespace_normalized_replacer,
        _indentation_flexible_replacer,
        _escape_normalized_replacer,
        _trimmed_boundary_replacer,
        _context_aware_replacer,
        _multi_occurrence_replacer,
    ]

    for replacer in replacers:
        for search in replacer(content, old_string):
            idx = content.find(search)
            if idx == -1:
                continue
            not_found = False
            if _is_disproportionate(search, old_string):
                raise ValueError(
                    "Refusing replacement because the matched span is much larger than oldString. "
                    "Re-read the file and provide the full exact oldString for the intended replacement."
                )
            if replace_all:
                return content.replace(search, new_string)
            last_idx = content.rfind(search)
            if idx != last_idx:
                continue
            return content[:idx] + new_string + content[idx + len(search):]

    if not_found:
        raise ValueError(
            "Could not find oldString in the file. It must match exactly, including whitespace, indentation, and line endings."
        )
    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding context to make the match unique."
    )


def _preview_lines(value: str, prefix: str) -> list:
    lines = value.replace("\r\n", "\n").split("\n")
    shown = []
    for line in lines[:6]:
        if len(line) > 240:
            line = line[:240] + "..."
        shown.append(f"{prefix}{line}")
    if len(lines) > 6:
        shown.append(f"{prefix}...")
    return shown


def execute(filePath: str, oldString: str, newString: str = "", replaceAll: bool = False, **kwargs) -> dict:
    path = filePath
    if not path:
        return {
            "title": "",
            "output": "filePath is required",
            "metadata": {"error": True},
        }
    path = os.path.abspath(os.path.expanduser(path))

    if oldString == newString:
        return {
            "title": path,
            "output": "No changes to apply: oldString and newString are identical.",
            "metadata": {"error": True},
        }

    if not oldString:
        if os.path.exists(path):
            return {
                "title": path,
                "output": "oldString cannot be empty when editing an existing file. Provide the exact text to replace, or use write for an intentional full-file replacement.",
                "metadata": {"error": True},
            }

    if not os.path.exists(path):
        return {"title": path, "output": f"File not found: {path}", "metadata": {"error": True}}

    if os.path.isdir(path):
        return {"title": path, "output": f"Path is a directory, not a file: {path}", "metadata": {"error": True}}

    try:
        with open(path, "rb") as f:
            original_bytes = f.read()
    except OSError as e:
        return {"title": path, "output": f"Failed to read {path}: {e}", "metadata": {"error": True}}

    try:
        text = original_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {"title": path, "output": f"File is not valid UTF-8: {path}", "metadata": {"error": True}}

    _BOM_UTF8 = b"\xef\xbb\xbf"
    bom = original_bytes[:3] if original_bytes[:3] == _BOM_UTF8 else b""
    if bom and text.startswith("\ufeff"):
        text = text[1:]

    ending = _detect_line_ending(text)
    search_old = _convert_line_ending(_normalize_line_endings(oldString), ending)
    search_new = _convert_line_ending(_normalize_line_endings(newString), ending)

    try:
        replaced_text = _replace(text, search_old, search_new, replaceAll)
    except ValueError as e:
        return {
            "title": path,
            "output": str(e),
            "metadata": {"error": True},
        }

    import difflib
    diff = list(difflib.unified_diff(
        text.splitlines(keepends=True),
        replaced_text.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
    ))
    additions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    new_bytes = bom + replaced_text.encode("utf-8") if bom else replaced_text.encode("utf-8")

    try:
        with open(path, "rb") as f:
            current_bytes = f.read()
    except OSError as e:
        return {"title": path, "output": f"Verification failed: {e}", "metadata": {"error": True}}

    if current_bytes != original_bytes:
        return {
            "title": path,
            "output": "File changed after initial read. Read it again before editing.",
            "metadata": {"error": True},
        }

    try:
        with open(path, "wb") as f:
            f.write(new_bytes)
    except OSError as e:
        return {"title": path, "output": f"Write failed: {e}", "metadata": {"error": True}}

    patch_str = "".join(diff[:20])
    if len(diff) > 20:
        patch_str += "... (diff truncated)"

    output_lines = [
        f"Edited file successfully: {path}",
        "```diff",
    ]
    output_lines.extend(_preview_lines(oldString, "-"))
    output_lines.extend(_preview_lines(newString, "+"))
    output_lines.append("```")

    return {
        "title": path,
        "output": "\n".join(output_lines),
        "metadata": {
            "files": [
                {
                    "file": path,
                    "patch": patch_str,
                    "status": "modified",
                    "additions": additions,
                    "deletions": deletions,
                }
            ],
        },
    }


def to_model_output(data: dict) -> str:
    meta = data.get("metadata", {})
    if meta.get("error"):
        return data["output"]
    return data.get("output", "")


tool = Tool(
    name="edit",
    description="""Performs exact string replacements in existing files.

- MUST read the file with Read tool before editing
- When matching from Read output, use content AFTER the line prefix
  (e.g., "1: content" — match "content", not "1: content")
- Edit FAILS if oldString is not found or has multiple matches
  To fix: add more surrounding context, or use replaceAll for bulk rename
- Prefer editing existing files; do NOT create new files unless asked
- No emojis unless the user explicitly asks""",
    parameters={
        "type": "object",
        "properties": {
            "filePath": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "oldString": {
                "type": "string",
                "description": "The text to replace",
            },
            "newString": {
                "type": "string",
                "description": "The text to replace it with (must be different from oldString)",
            },
            "replaceAll": {
                "type": "boolean",
                "description": "Replace all occurrences of oldString (default false)",
            },
        },
        "required": ["filePath", "oldString", "newString"],
    },
    execute=execute,
    to_model_output=to_model_output,
)
