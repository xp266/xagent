import json
import re


def unescape_json(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def json_field(raw: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        return ""
    return unescape_json(m.group(1))


def json_tail_field(raw: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"(.*)$', raw, re.S)
    if not m:
        return ""
    val = re.sub(r'"\s*[,}}]?\s*$', "", m.group(1))
    return unescape_json(val)


def json_prefix_field(raw: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"(.*?)(?="\s*[,}}]|$)', raw, re.S)
    if not m:
        return ""
    return unescape_json(m.group(1))


def stream_args(raw: str, name: str) -> dict:
    if not raw:
        return {}
    if name == "bash":
        return {"command": json_tail_field(raw, "command")}
    if name == "write":
        return {
            "path": json_field(raw, "path") or json_prefix_field(raw, "path"),
            "content": json_tail_field(raw, "content"),
        }
    if name == "edit":
        return {
            "filePath": json_field(raw, "filePath") or json_prefix_field(raw, "filePath"),
            "oldString": json_field(raw, "oldString") or json_prefix_field(raw, "oldString"),
            "newString": json_tail_field(raw, "newString"),
        }
    if name == "read":
        return {"path": json_field(raw, "path") or json_tail_field(raw, "path") or json_field(raw, "filePath")}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
