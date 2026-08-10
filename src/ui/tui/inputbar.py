from textual import events
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea
import time


class ChatInput(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Navigate(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class AcceptPalette(Message):
        pass

    class TextEdited(Message):
        pass

    class InterruptConfirmed(Message):
        pass

    _ARM_PLACEHOLDER = "Press ctrl+c again to interrupt"
    _ARM_SECONDS = 3.0
    _HISTORY_MAX = 100

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("tab_behavior", "indent")
        super().__init__(*args, **kwargs)
        self.palette_open = False
        self.busy = False
        self._armed_at = 0.0
        self._arm_timer = None
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._hist_draft: str = ""
        self._restoring_history = False

    def on_text_area_changed(self, event) -> None:
        if self._restoring_history:
            self._restoring_history = False
            return
        self.post_message(self.TextEdited())

    def _push_history(self, text: str) -> None:
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            if len(self._history) > self._HISTORY_MAX:
                self._history.pop(0)
        self._hist_idx = None
        self._hist_draft = ""

    def _set_text_quiet(self, text: str) -> None:
        self._restoring_history = True
        try:
            self.text = text
            doc = self.document
            if doc.line_count:
                last = doc.lines[-1]
                self.move_cursor((doc.line_count - 1, len(last)))
        except Exception:
            self._restoring_history = False
            raise

    def _recall_history(self, delta: int) -> bool:
        if not self._history:
            return False
        if delta < 0:
            if self._hist_idx is None:
                self._hist_draft = self.text
                self._hist_idx = len(self._history) - 1
            else:
                if self._hist_idx <= 0:
                    return False
                self._hist_idx -= 1
            self._set_text_quiet(self._history[self._hist_idx])
            return True
        if self._hist_idx is None:
            return False
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._set_text_quiet(self._history[self._hist_idx])
        else:
            self._hist_idx = None
            self._set_text_quiet(self._hist_draft)
        return True

    def _arm(self) -> None:
        self._armed_at = time.monotonic()
        if not self.text:
            self.placeholder = self._ARM_PLACEHOLDER
        if self._arm_timer is not None:
            self._arm_timer.stop()
        self._arm_timer = self.set_timer(self._ARM_SECONDS, self._disarm)

    def _disarm(self) -> None:
        self._armed_at = 0.0
        if self._arm_timer is not None:
            self._arm_timer.stop()
            self._arm_timer = None
        if self.placeholder == self._ARM_PLACEHOLDER:
            self.placeholder = ""

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ("ctrl+j", "newline"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key in ("enter", "ctrl+m"):
            if self.palette_open:
                event.stop()
                event.prevent_default()
                self.post_message(self.AcceptPalette())
                return
            event.stop()
            event.prevent_default()
            text = self.text
            if text:
                self._push_history(text)
                self.clear()
                self.post_message(self.Submitted(text))
            return
        if event.key in ("up", "down"):
            if self.palette_open:
                event.stop()
                event.prevent_default()
                self.post_message(self.Navigate(-1 if event.key == "up" else 1))
                return
            if self._recall_history(-1 if event.key == "up" else 1):
                event.stop()
                event.prevent_default()
            return
        if event.key == "ctrl+c":
            if self.busy:
                event.stop()
                event.prevent_default()
                now = time.monotonic()
                if self._armed_at and now - self._armed_at <= self._ARM_SECONDS:
                    self._disarm()
                    self.post_message(self.InterruptConfirmed())
                else:
                    self._arm()
                return
            self._disarm()
        if event.key in ("ctrl+u", "ctrl+d"):
            event.stop()
            event.prevent_default()
            chat = self.app._chat()
            if event.key == "ctrl+u":
                chat.scroll_page_up(animate=False)
            else:
                chat.scroll_page_down(animate=False)
            return
        await super()._on_key(event)

class CommandPalette(Vertical):

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands: list = []
        self._selected: int = 0

    def show(self, commands: list) -> None:
        self._commands = commands
        self._selected = 0
        for child in list(self.children):
            child.remove()
        for i, cmd in enumerate(commands):
            row = Static(f"/{cmd.name}  {cmd.description}", classes="cmd-row")
            if i == 0:
                row.add_class("selected")
            self.mount(row)
        if commands:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    def hide(self) -> None:
        self.remove_class("visible")
        self._commands = []

    def _update_selection(self) -> None:
        rows = self.query(".cmd-row")
        for i, row in enumerate(rows):
            row.set_class(i == self._selected, "selected")

    def move(self, delta: int) -> None:
        if not self._commands:
            return
        self._selected = (self._selected + delta) % len(self._commands)
        self._update_selection()
        row = self.query(".cmd-row")[self._selected]
        self.scroll_to_widget(row, animate=False)

    @property
    def selected_command(self):
        if self._commands and 0 <= self._selected < len(self._commands):
            return self._commands[self._selected]
        return None
