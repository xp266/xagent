from textual import events
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.message import Message
from textual.widgets import Static, TextArea, Input, Markdown, Collapsible


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


def _format_session_time(iso: str) -> str:
    """Format an ISO timestamp into a short readable string."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except Exception:
        return iso
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    delta = now - dt
    if delta < datetime.timedelta(minutes=1):
        return "just now"
    if delta < datetime.timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < datetime.timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    if delta < datetime.timedelta(days=7):
        return f"{delta.days}d ago"
    return dt.strftime("%Y-%m-%d")


class SessionPicker(Vertical):
    """Centered modal dialog for picking a session.

    Shows a search box at the top and a scrollable list of sessions
    (``id - name - time``). Filtering is applied live by id, name or time.
    """

    DEFAULT_CSS = """
    SessionPicker {
        display: none;
        layer: overlay;
        width: 45%;
        height: 45%;
        background: #1A1A1A;
        border: none;
        padding: 1;
    }
    SessionPicker.visible {
        display: block;
    }
    SessionPicker #picker-search {
        height: 1;
        border: none;
        background: #222222;
        margin-bottom: 1;
    }
    SessionPicker #picker-list {
        height: 1fr;
        border: none;
        padding: 0;
        scrollbar-size: 1 1;
    }
    SessionPicker #picker-footer {
        height: 1;
        padding: 0;
        content-align: left bottom;
        color: #888888;
    }
    SessionPicker > .picker-row {
        height: 1;
        padding: 0 1;
    }
    SessionPicker > .picker-row.selected {
        background: #334466;
    }
    """

    class Selected(Message):
        """A session was chosen from the picker."""

        def __init__(self, session) -> None:
            super().__init__()
            self.session = session

    class Dismissed(Message):
        """The picker was closed without selection."""

    def __init__(self, sessions=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sessions = sessions or []
        self._filtered: list = []
        self._selected = 0

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search sessions...", id="picker-search")
        with VerticalScroll(id="picker-list"):
            pass
        yield Static("Press ESC to exit", id="picker-footer")

    def _rebuild(self) -> None:
        """Rebuild the list from the current filter."""
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            self._filtered = [
                s for s in self._sessions
                if query in s.id.lower()
                or query in s.name.lower()
                or query in _format_session_time(s.updated_at or s.created_at).lower()
            ]
        else:
            self._filtered = list(self._sessions)
        self._filtered.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        self._selected = 0
        list_box = self.query_one("#picker-list", VerticalScroll)
        for child in list(list_box.children):
            child.remove()
        for i, s in enumerate(self._filtered):
            label = f"{s.id}  -  {s.name}  -  {_format_session_time(s.updated_at or s.created_at)}"
            row = Static(label, classes="picker-row")
            if i == 0:
                row.add_class("selected")
            list_box.mount(row)

    def show(self, sessions) -> None:
        self._sessions = sessions
        self.add_class("visible")
        self._rebuild()
        self.call_after_refresh(self._center)
        self.query_one("#picker-search", Input).focus()

    def _center(self) -> None:
        """Center the picker within the screen."""
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = self.size.width
            h = self.size.height
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def is_visible(self) -> bool:
        return "visible" in self.classes

    def _update_selection(self) -> None:
        rows = self.query(".picker-row")
        for i, row in enumerate(rows):
            row.set_class(i == self._selected, "selected")

    def move(self, delta: int) -> None:
        if not self._filtered:
            return
        self._selected = (self._selected + delta) % len(self._filtered)
        self._update_selection()
        rows = self.query(".picker-row")
        row = rows[self._selected]
        self.query_one("#picker-list", VerticalScroll).scroll_to_region(row.region, animate=False)

    def _on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._rebuild()

    async def _on_key(self, event: events.Key) -> None:
        if not self.is_visible:
            return
        key = event.key
        if key == "escape":
            event.stop()
            self.hide()
            self.post_message(self.Dismissed())
            return
        if key == "up":
            event.stop()
            event.prevent_default()
            self.move(-1)
            return
        if key == "down":
            event.stop()
            event.prevent_default()
            self.move(1)
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            if self._filtered and 0 <= self._selected < len(self._filtered):
                self.hide()
                self.post_message(self.Selected(self._filtered[self._selected]))
            else:
                self.post_message(self.Dismissed())
            return
        await super()._on_key(event)
