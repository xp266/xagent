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


_SIMPLE_KEYS = ("path", "filePath", "oldString")
_TAIL_KEYS = {"bash": "command", "write": "content", "edit": "newString"}
_WINDOW = 21


def _find_str_value(raw: str, key: str, from_pos: int) -> tuple[int, str | None, str]:
    i = raw.find(f'"{key}"', from_pos)
    if i < 0:
        return i, None, ""
    j = i + len(key) + 2
    while j < len(raw) and raw[j] in " \t":
        j += 1
    if j >= len(raw) or raw[j] != ":":
        return i, None, ""
    j += 1
    while j < len(raw) and raw[j] in " \t":
        j += 1
    if j >= len(raw) or raw[j] != '"':
        return i, None, ""
    start = j + 1
    end = start
    while end < len(raw):
        c = raw[end]
        if c == "\\":
            end += 2
            continue
        if c == '"':
            return start, raw[start:end], ""
        end += 1
    return i, None, raw[start:]


class StreamArgs:
    def __init__(self, name: str):
        self.name = name
        self.raw = ""
        self.sk: dict = {}
        self.tail_key = _TAIL_KEYS.get(name)
        self.tail_at: int | None = None
        self.value_done = False
        self.pending = ""
        self.consume = 0
        self.window: list[str] = []
        self.partial = ""
        self.lines = 0

    def feed(self, raw: str) -> dict:
        self.raw = raw
        if self.tail_at is None:
            for key in _SIMPLE_KEYS:
                st = self.sk.get(key)
                if st is None:
                    st = [0, None, ""]
                    self.sk[key] = st
                if st[1] is not None:
                    continue
                pos, val, partial = _find_str_value(raw, key, st[0])
                if val is not None:
                    st[1] = unescape_json(val)
                    st[0] = pos + 1
                elif partial:
                    st[2] = unescape_json(partial)
                else:
                    st[0] = pos if pos >= 0 else st[0]
        if self.tail_key is not None:
            self._tail_decode()
        return self._args()

    def _tail_decode(self) -> None:
        raw = self.raw
        if self.value_done:
            return
        if self.tail_at is None:
            i = raw.find(f'"{self.tail_key}"')
            if i < 0:
                return
            j = i + len(self.tail_key) + 2
            while j < len(raw) and raw[j] in " \t":
                j += 1
            if j >= len(raw) or raw[j] != ":":
                return
            j += 1
            while j < len(raw) and raw[j] in " \t":
                j += 1
            if j >= len(raw) or raw[j] != '"':
                return
            self.tail_at = j
            self.consume = j + 1
        seg = self.pending + raw[self.consume:]
        i = 0
        while i < len(seg):
            c = seg[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                chunk = seg[:i]
                if chunk:
                    self._add(chunk)
                self.value_done = True
                return
            i += 1
        i = len(seg)
        while i > 0 and seg[i - 1] == "\\":
            i -= 1
        run = len(seg) - i
        if run % 2 == 1:
            chunk, keep = seg[:-1], seg[-1:]
        else:
            chunk, keep = seg, ""
        self.pending = keep
        if chunk:
            self._add(chunk)
        self.consume = len(raw)

    def _add(self, text: str) -> None:
        dec = unescape_json(text)
        self.partial += dec
        parts = self.partial.split("\n")
        if len(parts) > 1:
            self.window.extend(parts[:-1])
            self.lines += len(parts) - 1
            if len(self.window) > _WINDOW:
                del self.window[:len(self.window) - _WINDOW]
            self.partial = parts[-1]

    def _tail_value(self) -> str:
        out = list(self.window)
        if self.partial:
            out.append(self.partial)
        return "\n".join(out) if out else ""

    def _tail_lines(self) -> int:
        return self.lines + (1 if self.partial else 0)

    def _args(self) -> dict:
        name = self.name
        if name == "bash":
            return {"command": self._tail_value()}
        if name == "write":
            path = self.sk.get("path")
            return {
                "path": path[1] or path[2] if path else "",
                "content": self._tail_value(),
                "_stream_lines": self._tail_lines(),
            }
        if name == "edit":
            fp = self.sk.get("filePath")
            os = self.sk.get("oldString")
            return {
                "filePath": fp[1] or fp[2] if fp else "",
                "oldString": os[1] or os[2] if os else "",
                "newString": self._tail_value(),
                "_stream_lines": self._tail_lines(),
            }
        if name == "read":
            p = self.sk.get("path")
            fp = self.sk.get("filePath")
            path = ""
            if p:
                path = p[1] or p[2] or ""
            if not path and fp:
                path = fp[1] or ""
            return {"path": path}
        if not self.raw:
            return {}
        try:
            parsed = json.loads(self.raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}