from __future__ import annotations

import time
import unicodedata

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from src.mcp.manager import get_mcp_manager
from src.ui.tui.colors import _BLUE_WAVE, _lerp_hex, _MCP_DOT
from src.ui.tui.render import fmt_pct
from src.utils.config import get_config
from src.utils.models import get_model_context_limit
from src.utils.providers import get_store

_WAVE_SPEED = 10.0


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
        if not cfg.base_url:
            model = "Type /provider to connect a provider"
        elif not cfg.model:
            model = "Type /model to select a model"
        else:
            model = cfg.model
            if cfg.reasoning_effort:
                model = f"{model} · {cfg.reasoning_effort}"
        total = self._session.token_usage.total_tokens
        limit = get_model_context_limit(cfg.model) if cfg.model else 0
        pct = self._context_pct(limit)
        return f"{model}  {total:,} tokens  {fmt_pct(pct)}"

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

    def _wave_color_at(self, index: int, now: float):
        best = None
        for t0 in self._waves:
            distance = (now - t0) * _WAVE_SPEED - index
            if 0 <= distance < len(_BLUE_WAVE):
                if best is None or distance < best:
                    best = distance
        if best is None:
            return None
        i = int(best)
        frac = best - i
        if i >= len(_BLUE_WAVE) - 1:
            return _BLUE_WAVE[-1]
        return _lerp_hex(_BLUE_WAVE[i], _BLUE_WAVE[i + 1], frac)

    def _update_input_status(self, status: str | None = None) -> None:
        if status is None:
            status = self._info_string()
        width = self.size.width if self.size and self.size.width else 80
        mcp = self._mcp_status_text()
        mcp_len = mcp.cell_len if mcp is not None else 0
        avail = max(0, width - 4 - mcp_len)
        status = _truncate_cells(status, avail)
        text = Text()
        if self._busy and self._waves:
            now = time.monotonic()
            cell = 0
            for ch in status:
                text.append(ch, style=self._wave_color_at(cell, now))
                cell += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        else:
            text.append(status)
        pad = avail - cell_len(status)
        if pad > 0:
            text.append(" " * pad)
        if mcp is not None:
            text.append(mcp)
        try:
            self.query_one("#input-status", Static).update(text)
        except Exception:
            pass

    def _update_status(self) -> None:
        self._update_input_status()
        status = f" {self._status_string()}"
        width = self.size.width if self.size and self.size.width else 80
        status = _truncate_cells(status, max(0, width - 1))
        try:
            self.query_one("#status", Static).update(Text(status, style="#666666"))
        except Exception:
            pass

    def _tick_status_wave(self) -> None:
        if not self._busy:
            if self._waves:
                self._waves.clear()
                self._update_status()
            return
        now = time.monotonic()
        status = self._info_string()
        n = cell_len(status)
        if self._waves:
            head = (now - self._waves[0]) * _WAVE_SPEED
            if head >= (n - 1) + (len(_BLUE_WAVE) - 1):
                self._waves = []
        if not self._waves:
            self._waves.append(now)
        self._waves = [t0 for t0 in self._waves if (now - t0) * _WAVE_SPEED < n + len(_BLUE_WAVE)]
        self._update_input_status(status=status)
