from textual import events
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea, Markdown, Collapsible


class LazyMarkdown(Markdown):
    """Markdown that defers parsing until explicitly activated.

    With ``auto=True`` (default) the app calls ``activate_pending()`` after all
    widgets are mounted. With ``auto=False`` parsing is deferred until
    ``activate()`` is called (e.g. on Collapsible expand).
    """

    def __init__(self, markdown: str = "", *, auto: bool = True, **kwargs) -> None:
        self._lazy_source = markdown
        self._auto = auto
        super().__init__("", **kwargs)

    def set_markdown(self, markdown: str) -> None:
        """Set the source to parse on next activation."""
        self._lazy_source = markdown

    @property
    def is_pending(self) -> bool:
        return bool(self._lazy_source)

    async def activate(self) -> None:
        """Parse and display the pending markdown."""
        source = self._lazy_source
        self._lazy_source = ""
        if source:
            await self.update(source)


class LazyCollapsible(Collapsible):
    """Collapsible that activates lazy children when expanded."""

    def _watch_collapsed(self, collapsed: bool) -> None:
        super()._watch_collapsed(collapsed)
        if not collapsed:
            for child in self.query(LazyMarkdown):
                if child._lazy_source:
                    self.run_worker(self._activate_markdown(child))

    async def _activate_markdown(self, child: LazyMarkdown) -> None:
        await child.activate()


class ChatInput(TextArea):

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Navigate(Message):
        """Request the palette to move selection (up/down)."""

        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class AcceptPalette(Message):
        """Request the palette to accept the selected command."""

    class TextEdited(Message):
        """Input text changed (used to refresh the command palette)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.palette_open = False

    def on_text_area_changed(self, event) -> None:
        self.post_message(self.TextEdited())

    async def _on_key(self, event: events.Key) -> None:
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
                self.clear()
                self.post_message(self.Submitted(text))
            return
        if self.palette_open and event.key == "up":
            event.stop()
            event.prevent_default()
            self.post_message(self.Navigate(-1))
            return
        if self.palette_open and event.key == "down":
            event.stop()
            event.prevent_default()
            self.post_message(self.Navigate(1))
            return
        await super()._on_key(event)


class CommandPalette(Vertical):
    """A small popup listing commands matching the typed slash prefix.

    The palette is shown above the input when the user types ``/``.
    Navigation (up/down) and selection (enter) are handled by the parent
    via posted messages.
    """

    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        height: auto;
        max-height: 8;
        border: round #334466;
        background: #1A1A1A;
        padding: 0;
        margin: 0 2 0 2;
        overflow-y: auto;
        scrollbar-size: 0 0;
    }
    CommandPalette.visible {
        display: block;
    }
    CommandPalette > .cmd-row {
        height: 1;
        padding: 0 1;
    }
    CommandPalette > .cmd-row.selected {
        background: #334466;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands: list = []
        self._selected: int = 0

    def show(self, commands: list) -> None:
        """Show the palette with matching commands."""
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

    @property
    def visible_cmds(self) -> bool:
        return bool(self._commands and "visible" in self.classes)

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
        self.scroll_to_region(row.region, animate=False)

    @property
    def selected_command(self):
        if self._commands and 0 <= self._selected < len(self._commands):
            return self._commands[self._selected]
        return None
