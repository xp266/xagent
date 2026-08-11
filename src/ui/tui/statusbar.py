from __future__ import annotations

import time
import unicodedata

from rich.text import Text
from textual.widgets import Static

from src.mcp.manager import get_mcp_manager
from src.ui.tui.colors import _MCP_DOT
from src.utils.config import get_config
from src.utils.providers import get_store


def _truncate_cells(text: str, max_cells: int) -> str:
    if max_cells <= 0:
        return ""
    cells = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cells + w > max_cells:
            return text[:i]
        cells += w
    return text


class StatusMixin:
    def _context_pct(self, limit: int) -> float:
        if self._ctx_usage_tokens > 0 and limit > 0:
            return min(100.0, self._ctx_usage_tokens / limit * 100)
        total = self._session.token_usage.total_tokens
        if limit > 0 and total > 0:
            return min(100.0, total / limit * 100)
        return 0.0

    def _info_string(self) -> str:
        cfg = get_config()
        usage = self._session.token_usage
        key = (
            cfg.base_url,
            cfg.model,
            cfg.reasoning_effort,
            self._ctx_usage_tokens,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        if key != getattr(self, "_info_key", None):
            self._info_key = key
            self._info_cache = self._build_info_string()
        return self._info_cache

    def _build_info_string(self) -> str:
        cfg = get_config()
        if not cfg.base_url:
            model = "Type /provider to connect a provider"
        elif not cfg.model:
            model = "Type /model to select a model"
        else:
            model = cfg.model
            if cfg.reasoning_effort:
                model = f"{model}[{cfg.reasoning_effort}]"
        if not cfg.model:
            return model
        limit = get_store().get_effective_context_limit(cfg.model)
        pct = self._context_pct(limit)
        usage = self._session.token_usage
        status = (
            f"{model}[{usage.prompt_tokens:,}→{usage.completion_tokens:,}]"
            f"[{pct:.1f}% used]"
        )
        return status

    def _status_string(self) -> str:
        return f"xAgent - {self._project} - {self._session.name}"

    def _mcp_status_text(self) -> Text | None:
        counts = get_mcp_manager().status_counts()
        if sum(counts.values()) == 0:
            enabled = [
                name
                for name, cfg in get_store().mcp_servers.items()
                if isinstance(cfg, dict) and str(cfg.get("status", "enabled")).lower() != "disabled"
            ]
            if enabled:
                counts["connecting"] = len(enabled)
        inner = []
        for key, color in (("connected", "green"), ("connecting", "yellow"), ("failed", "red")):
            n = counts.get(key, 0)
            if n > 0:
                inner.append((color, n))
        if not inner:
            return None
        text = Text()
        text.append("MCP", style="#1066cb")
        text.append(" ")
        for i, (color, n) in enumerate(inner):
            text.append(f"{_MCP_DOT}{n}", style=color)
            if i < len(inner) - 1:
                text.append(" ")
        return text

    def _update_mcp(self, mcp: Text | None) -> None:
        key = mcp.plain if mcp is not None else ""
        if key == getattr(self, "_imcp_key", None):
            return
        self._imcp_key = key
        try:
            self.query_one("#input-mcp", Static).update(mcp if mcp is not None else "")
        except Exception:
            pass

    def _update_input_status(self, status: str | None = None) -> None:
        if status is None:
            status = self._info_string()
        width = self.size.width if self.size and self.size.width else 80
        mcp = self._mcp_status_text()
        mcp_len = mcp.cell_len if mcp is not None else 0
        avail = max(0, width - 4 - mcp_len)
        status = _truncate_cells(status, avail)
        text = Text(status)
        key = status
        self._update_mcp(mcp)
        if key is not None and key == getattr(self, "_istatus_key", None):
            return
        if key is not None:
            self._istatus_key = key
        try:
            self.query_one("#input-status", Static).update(text)
        except Exception:
            pass

    def _update_status(self) -> None:
        self._update_input_status()
        status = f" {self._status_string()}"
        width = self.size.width if self.size and self.size.width else 80
        status = _truncate_cells(status, max(0, width - 1))
        if status == getattr(self, "_status_key", None):
            return
        self._status_key = status
        try:
            self.query_one("#status", Static).update(Text(status, style="#666666"))
        except Exception:
            pass
