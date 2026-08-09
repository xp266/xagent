from __future__ import annotations

import time

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class SpinnerMixin:
    def _spinner_frame(self) -> str:
        return _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]

    def _start_spinner(self, block, label) -> None:
        if block is None:
            return
        self._spinners[id(block)] = (block, label)
        self._render_spinner(block, label)

    def _render_spinner(self, block, label) -> None:
        if id(block) not in self._spinners:
            return
        label = label() if callable(label) else label
        block.arrow_hidden = True
        block.set_title(label)
        block.set_marker(self._spinner_frame())

    def _stop_spinner(self, block, label, *, restore_arrow: bool = True) -> None:
        if block is None:
            return
        if self._spinners.pop(id(block), None) is not None:
            label = label() if callable(label) else label
            block.set_marker(None)
            if getattr(block, "hide_arrow", False):
                return
            if restore_arrow:
                block.arrow_hidden = False
            block.set_title(label)

    def _start_tool_spinner(self, tool) -> None:
        tool["spinning"] = True
        self._start_spinner(tool["block"], lambda: tool["title"])

    def _stop_tool_spinner(self, tool) -> None:
        if not tool["spinning"]:
            return
        tool["spinning"] = False
        self._stop_spinner(tool["block"], lambda: tool["title"])

    def _stop_all_spinners(self) -> None:
        for block, label in list(self._spinners.values()):
            self._stop_spinner(block, label)
        cur = self._current
        if cur is not None:
            for tool in cur["tools"].values():
                tool["spinning"] = False

    def _tick_spinners(self) -> None:
        if not self._spinners:
            return
        now = time.monotonic()
        if now - self._last_spinner_time < 0.1:
            return
        self._last_spinner_time = now
        self._spinner_idx += 1
        for block, label in list(self._spinners.values()):
            self._render_spinner(block, label)
