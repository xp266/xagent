from textual import events
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.message import Message
from textual.widgets import Static, TextArea, Input, Collapsible
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.palette_open = False
        self.busy = False
        self._armed_at = 0.0
        self._arm_timer = None

    def on_text_area_changed(self, event) -> None:
        self.post_message(self.TextEdited())

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
        await super()._on_key(event)


class CommandPalette(Vertical):
    DEFAULT_CSS = """
    CommandPalette {
        display: none;
        height: auto;
        max-height: 8;
        border: none;
        background: #1A1A1A;
        padding: 0;
        margin: 0 0 7 0;
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
        self.scroll_to_widget(row, animate=False)

    @property
    def selected_command(self):
        if self._commands and 0 <= self._selected < len(self._commands):
            return self._commands[self._selected]
        return None


def _format_session_time(iso: str) -> str:
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


class _ListPicker(Vertical):
    class Selected(Message):
        pass

    class Dismissed(Message):
        pass

    placeholder = "Search..."
    footer_text = "Press ESC to exit"

    def __init__(self, items=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: list = items or []
        self._filtered: list = []
        self._selected = 0

    def item_label(self, item) -> str:
        return str(item)

    def item_matches(self, item, query: str) -> bool:
        return query in self.item_label(item).lower()

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self.placeholder, id="picker-search")
        with VerticalScroll(id="picker-list"):
            pass
        yield Static(self.footer_text, id="picker-footer")

    def _rebuild(self) -> None:
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            self._filtered = [i for i in self._items if self.item_matches(i, query)]
        else:
            self._filtered = list(self._items)
        self._selected = 0
        list_box = self.query_one("#picker-list", VerticalScroll)
        for child in list(list_box.children):
            child.remove()
        for i, item in enumerate(self._filtered):
            row = Static(self.item_label(item), classes="picker-row")
            if i == 0:
                row.add_class("selected")
            list_box.mount(row)

    def show(self, items) -> None:
        self._items = items
        search = self.query_one("#picker-search", Input)
        search.value = ""
        self._rebuild()
        self._center_presets()
        self.add_class("visible")
        self.call_after_refresh(self._center)
        search.focus()

    def _center_presets(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = int(parent_w * 0.45)
            h = int(parent_h * 0.45)
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def _center(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = self.region.width or int(parent_w * 0.45)
            h = self.region.height or int(parent_h * 0.45)
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

    def _select_item(self, item) -> None:
        self.post_message(self.Selected(item))

    def move(self, delta: int) -> None:
        if not self._filtered:
            return
        self._selected = (self._selected + delta) % len(self._filtered)
        self._update_selection()
        rows = self.query(".picker-row")
        row = rows[self._selected]
        self.query_one("#picker-list", VerticalScroll).scroll_to_widget(row, animate=False)

    def update_items(self, items) -> None:
        self._items = items
        self._rebuild()

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
                item = self._filtered[self._selected]
                self.hide()
                self._select_item(item)
            else:
                self.post_message(self.Dismissed())
            return
        await super()._on_key(event)


class SessionPicker(_ListPicker):
    placeholder = "Search sessions..."
    footer_text = "Press ESC to exit   |   Ctrl+D to delete"

    _ARM_FOOTER = "Press Ctrl+D again to delete"
    _ARM_SECONDS = 3.0

    class Selected(Message):
        def __init__(self, session) -> None:
            super().__init__()
            self.session = session

    class Deleted(Message):
        def __init__(self, session) -> None:
            super().__init__()
            self.session = session

    class Dismissed(Message):
        pass

    def __init__(self, items=None, **kwargs) -> None:
        super().__init__(items=items, **kwargs)
        self._delete_armed_at = 0.0
        self._delete_target = None

    def item_label(self, item) -> str:
        return (
            f"{item.id}  -  {item.name}  -  "
            f"{_format_session_time(item.updated_at or item.created_at)}"
        )

    def item_matches(self, item, query: str) -> bool:
        return (
            query in item.id.lower()
            or query in item.name.lower()
            or query in _format_session_time(item.updated_at or item.created_at).lower()
        )

    def hide(self) -> None:
        self._disarm_delete()
        super().hide()

    def _rebuild(self) -> None:
        super()._rebuild()
        self._disarm_delete()

    def _arm_delete(self, item) -> None:
        self._delete_armed_at = time.monotonic()
        self._delete_target = item
        self.set_timer(self._ARM_SECONDS, self._check_delete_timeout)
        try:
            footer = self.query_one("#picker-footer", Static)
            footer.update(f"[bold #FF5555]{self._ARM_FOOTER}[/]")
        except Exception:
            pass

    def _check_delete_timeout(self) -> None:
        if self._delete_armed_at and time.monotonic() - self._delete_armed_at >= self._ARM_SECONDS:
            self._disarm_delete()

    def _disarm_delete(self) -> None:
        if not self._delete_armed_at:
            return
        self._delete_armed_at = 0.0
        self._delete_target = None
        try:
            footer = self.query_one("#picker-footer", Static)
            footer.update(self.footer_text)
        except Exception:
            pass

    async def _on_key(self, event: events.Key) -> None:
        if not self.is_visible:
            return
        if event.key == "ctrl+d":
            event.stop()
            event.prevent_default()
            if self._filtered and 0 <= self._selected < len(self._filtered):
                item = self._filtered[self._selected]
                if (
                    self._delete_target is item
                    and time.monotonic() - self._delete_armed_at < self._ARM_SECONDS
                ):
                    self._disarm_delete()
                    self.post_message(self.Deleted(item))
                else:
                    self._arm_delete(item)
            return
        if event.key in ("up", "down"):
            self._disarm_delete()
        await super()._on_key(event)

    def _rebuild(self) -> None:
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            self._filtered = [s for s in self._items if self.item_matches(s, query)]
        else:
            self._filtered = list(self._items)
        self._filtered.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        self._selected = 0
        list_box = self.query_one("#picker-list", VerticalScroll)
        for child in list(list_box.children):
            child.remove()
        for i, s in enumerate(self._filtered):
            row = Static(self.item_label(s), classes="picker-row")
            if i == 0:
                row.add_class("selected")
            list_box.mount(row)


class ProviderPicker(_ListPicker):
    placeholder = "Search providers..."

    ADD_CUSTOM = object()

    class Selected(Message):
        def __init__(self, provider) -> None:
            super().__init__()
            self.provider = provider

    class AddCustom(Message):
        pass

    class Dismissed(Message):
        pass

    def __init__(self, items=None, **kwargs) -> None:
        super().__init__(items=items, **kwargs)
        self._connected_only = False

    def item_label(self, item) -> str:
        if item is self.ADD_CUSTOM:
            return "+ Add custom provider"
        tag = "custom" if item.is_custom else "built-in"
        return f"{item.name}  [{tag}]"

    def item_matches(self, item, query: str) -> bool:
        if item is self.ADD_CUSTOM:
            return "add" in query or "custom" in query
        return query in item.name.lower() or query in item.id.lower()

    def _select_item(self, item) -> None:
        if item is self.ADD_CUSTOM:
            self.post_message(self.AddCustom())
        else:
            self.post_message(self.Selected(item))

    def _rebuild(self) -> None:
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            self._filtered = [i for i in self._items if self.item_matches(i, query)]
        else:
            self._filtered = list(self._items)
        if self._connected_only:
            self._filtered = [i for i in self._filtered if i is not self.ADD_CUSTOM and getattr(i, "api_key", "")]
        else:
            self._filtered = [self.ADD_CUSTOM] + self._filtered
        self._selected = 0
        list_box = self.query_one("#picker-list", VerticalScroll)
        for child in list(list_box.children):
            child.remove()
        for i, item in enumerate(self._filtered):
            row = Static(self.item_label(item), classes="picker-row")
            if i == 0:
                row.add_class("selected")
            list_box.mount(row)


class ModelPicker(_ListPicker):
    placeholder = "Search models..."

    ADD_MODEL = object()

    class Selected(Message):
        def __init__(self, model: str, provider_id: str | None = None) -> None:
            super().__init__()
            self.model = model
            self.provider_id = provider_id

    class AddModel(Message):
        pass

    class Dismissed(Message):
        pass

    def __init__(self, items=None, **kwargs) -> None:
        super().__init__(items=items, **kwargs)
        self._add_enabled = False

    def item_label(self, item) -> str:
        if item is self.ADD_MODEL:
            return "+ Add model"
        if isinstance(item, tuple):
            return f"{item[0]}  [{item[2]}]"
        return item

    def item_matches(self, item, query: str) -> bool:
        if item is self.ADD_MODEL:
            return "add" in query or "model" in query
        model = item[0] if isinstance(item, tuple) else item
        return query in model.lower()

    def _select_item(self, item) -> None:
        if item is self.ADD_MODEL:
            self.post_message(self.AddModel())
        elif isinstance(item, tuple):
            self.post_message(self.Selected(item[0], item[1]))
        else:
            self.post_message(self.Selected(item))

    def _rebuild(self) -> None:
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            self._filtered = [i for i in self._items if self.item_matches(i, query)]
        else:
            self._filtered = list(self._items)
        if self._add_enabled:
            self._filtered = [self.ADD_MODEL] + self._filtered
        self._selected = 0
        list_box = self.query_one("#picker-list", VerticalScroll)
        for child in list(list_box.children):
            child.remove()
        for i, item in enumerate(self._filtered):
            row = Static(self.item_label(item), classes="picker-row")
            if i == 0:
                row.add_class("selected")
            list_box.mount(row)


class ProviderKeyDialog(Vertical):
    class Saved(Message):
        def __init__(self, values: dict, provider=None) -> None:
            super().__init__()
            self.values = values
            self.provider = provider

    class Canceled(Message):
        pass

    def __init__(self, provider=None, is_custom: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._provider = provider
        self._is_custom = is_custom
        self._error = ""

    def compose(self) -> ComposeResult:
        yield Static("API Key for provider", id="dialog-title")
        yield Input(placeholder="Name", id="custom-name")
        yield Input(placeholder="Base URL (https://.../v1)", id="custom-url")
        yield Input(placeholder="API Key", id="api-key", password=True)
        yield Static(self._error, id="dialog-error")
        yield Static("Enter to save, ESC to cancel", id="dialog-footer")

    def show(self, provider=None, is_custom: bool = False, values: dict | None = None) -> None:
        self._provider = provider
        self._is_custom = is_custom
        self._error = ""
        self.query_one("#dialog-error", Static).update("")
        for field in ("custom-name", "custom-url", "api-key"):
            try:
                self.query_one(f"#{field}", Input).value = ""
            except Exception:
                pass
        if provider is not None and not is_custom:
            self.query_one("#dialog-title", Static).update(f"API Key for {provider.name}")
        else:
            self.query_one("#dialog-title", Static).update("Add custom provider")
        if is_custom:
            self.query_one("#dialog-footer", Static).update("Enter: next field  Up/Down: switch  Enter on API Key: save  ESC: cancel")
        else:
            self.query_one("#dialog-footer", Static).update("Enter to save, ESC to cancel")
        self.query_one("#custom-name").display = is_custom
        self.query_one("#custom-url").display = is_custom
        self._set_values(values or {})
        self._center_presets()
        self.add_class("visible")
        self.call_after_refresh(self._center)
        self.call_after_refresh(self._focus_first)

    def _set_values(self, values: dict) -> None:
        for field, val in values.items():
            try:
                self.query_one(f"#{field}", Input).value = val
            except Exception:
                pass

    def _focus_first(self) -> None:
        widget = None
        if self._is_custom:
            widget = self.query_one("#custom-name", Input)
        else:
            widget = self.query_one("#api-key", Input)
        widget.focus()

    def _center_presets(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = int(parent_w * 0.45)
            h = int(parent_h * 0.35)
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def _center(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = self.region.width or int(parent_w * 0.45)
            h = self.region.height or int(parent_h * 0.35)
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def is_visible(self) -> bool:
        return "visible" in self.classes

    def set_error(self, message: str) -> None:
        self._error = message
        try:
            self.query_one("#dialog-error", Static).update(message)
        except Exception:
            pass

    def _collect(self) -> dict:
        def val(field: str) -> str:
            try:
                return self.query_one(f"#{field}", Input).value.strip()
            except Exception:
                return ""

        if self._is_custom:
            return {
                "name": val("custom-name"),
                "base_url": val("custom-url"),
                "api_key": val("api-key"),
            }
        return {"api_key": val("api-key")}

    def _fields(self) -> list[str]:
        return ["custom-name", "custom-url", "api-key"] if self._is_custom else ["api-key"]

    def _current_field(self) -> str | None:
        focused = self.screen.focused
        if focused is not None and getattr(focused, "id", None) in self._fields():
            return focused.id
        return None

    def _on_last_field(self) -> bool:
        cur = self._current_field()
        if cur is None:
            return False
        return self._fields()[-1] == cur

    def _focus_field(self, delta: int) -> None:
        fields = self._fields()
        cur = self._current_field()
        idx = fields.index(cur) if cur in fields else 0
        self.query_one(f"#{fields[(idx + delta) % len(fields)]}", Input).focus()

    def _submit(self) -> None:
        values = self._collect()
        if self._is_custom:
            if not values["base_url"]:
                self.set_error("Base URL is required")
                return
            if not values["api_key"]:
                self.set_error("API Key is required")
                return
        elif not values["api_key"]:
            self.set_error("API Key is required")
            return
        self.hide()
        self.post_message(self.Saved(values, provider=self._provider))

    async def _on_key(self, event: events.Key) -> None:
        if not self.is_visible:
            return
        key = event.key
        if key == "escape":
            event.stop()
            self.hide()
            self.post_message(self.Canceled())
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            if self._is_custom and self._current_field() is not None and not self._on_last_field():
                self._focus_field(1)
            else:
                self._submit()
            return
        if key in ("up", "down") and self._is_custom:
            event.stop()
            event.prevent_default()
            self._focus_field(-1 if key == "up" else 1)
            return
        await super()._on_key(event)


class ExaKeyDialog(Vertical):
    class Saved(Message):
        def __init__(self, api_key: str) -> None:
            super().__init__()
            self.api_key = api_key

    class Canceled(Message):
        pass

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._error = ""

    def compose(self) -> ComposeResult:
        yield Static("Exa API Key", id="dialog-title")
        yield Input(placeholder="API Key", id="api-key", password=True)
        yield Static(self._error, id="dialog-error")
        yield Static("Enter to save, ESC to cancel", id="dialog-footer")

    def show(self) -> None:
        self._error = ""
        self.query_one("#dialog-error", Static).update("")
        self.query_one("#api-key", Input).value = ""
        self._center_presets()
        self.add_class("visible")
        self.call_after_refresh(self._center)
        self.call_after_refresh(lambda: self.query_one("#api-key", Input).focus())

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def is_visible(self) -> bool:
        return "visible" in self.classes

    def set_error(self, message: str) -> None:
        self._error = message
        try:
            self.query_one("#dialog-error", Static).update(message)
        except Exception:
            pass

    def _center_presets(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = int(parent_w * 0.45)
            h = 9
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def _center(self) -> None:
        try:
            parent_w = self.screen.size.width
            parent_h = self.screen.size.height
            w = self.region.width or int(parent_w * 0.45)
            h = self.region.height or int(parent_h * 0.35)
            self.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
        except Exception:
            pass

    def _submit(self) -> None:
        try:
            key = self.query_one("#api-key", Input).value.strip()
        except Exception:
            key = ""
        if not key:
            self.set_error("API Key is required")
            return
        self.hide()
        self.post_message(self.Saved(key))

    async def _on_key(self, event: events.Key) -> None:
        if not self.is_visible:
            return
        key = event.key
        if key == "escape":
            event.stop()
            self.hide()
            self.post_message(self.Canceled())
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            self._submit()
            return
        await super()._on_key(event)
