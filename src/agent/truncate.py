import os
import hashlib
import tempfile
from datetime import datetime, timedelta
from pydantic import BaseModel


class TruncateResult(BaseModel):
    content: str
    truncated: bool
    output_path: str = ""


class TruncateService:
    def __init__(self, truncation_dir: str = ""):
        self._dir = truncation_dir or os.path.join(tempfile.gettempdir(), "lingcode", "truncation")
        os.makedirs(self._dir, exist_ok=True)

    def output(self, text: str, max_lines: int = 2000, max_bytes: int = 51200) -> TruncateResult:
        if not text:
            return TruncateResult(content=text, truncated=False)
        lines = text.split("\n")
        encoded = text.encode("utf-8")
        total_bytes = len(encoded)
        if len(lines) <= max_lines and total_bytes <= max_bytes:
            return TruncateResult(content=text, truncated=False)
        out_lines = []
        byte_count = 0
        for line in lines:
            line_encoded = line.encode("utf-8")
            sep = 1 if out_lines else 0
            if len(out_lines) >= max_lines or byte_count + len(line_encoded) + sep > max_bytes:
                break
            out_lines.append(line)
            byte_count += len(line_encoded) + sep
        preview = "\n".join(out_lines)
        file_path = self._write(encoded)
        removed_lines = len(lines) - len(out_lines)
        hint = (
            f"The tool call succeeded but the output was truncated. "
            f"Full output saved to: {file_path}\n"
            f"Use the explore agent to process this file with Grep and Read "
            f"(with offset/limit). Do NOT read the full file yourself - delegate to save context."
        )
        content = f"{preview}\n\n...{removed_lines} lines truncated...\n\n{hint}"
        return TruncateResult(content=content, truncated=True, output_path=file_path)

    def _write(self, data: bytes) -> str:
        h = hashlib.md5(data).hexdigest()[:8]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"tool_{ts}_{h}"
        fpath = os.path.join(self._dir, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        return fpath

    def cleanup(self, max_age_days: int = 7) -> None:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        for name in os.listdir(self._dir):
            fpath = os.path.join(self._dir, name)
            if not name.startswith("tool_"):
                continue
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
                    os.remove(fpath)
            except OSError:
                pass
