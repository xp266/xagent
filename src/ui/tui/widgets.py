from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static, TextArea, Input
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


def _center_widget(widget, *, w_frac: float = 0.45, h_frac: float = 0.45, h: int | None = None, use_region: bool = False) -> None:
    try:
        parent_w = widget.screen.size.width
        parent_h = widget.screen.size.height
        w = int(parent_w * w_frac)
        if h is None:
            h = int(parent_h * h_frac)
        if use_region:
            w = widget.region.width or w
            h = widget.region.height or h
        widget.styles.offset = (max(0, (parent_w - w) // 2), max(0, (parent_h - h) // 2))
    except Exception:
        pass


class _CenteredOverlay:
    _center_w_frac = 0.45
    _center_h_frac = 0.45

    def _store_center(self, *, w_frac: float = 0.45, h_frac: float = 0.45) -> None:
        self._center_w_frac = w_frac
        self._center_h_frac = h_frac

    def _recenter(self) -> None:
        _center_widget(
            self,
            w_frac=self._center_w_frac,
            h_frac=self._center_h_frac,
            use_region=True,
        )

    def on_resize(self, event) -> None:
        if getattr(self, "is_visible", False):
            self._recenter()


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


class _ListPicker(_CenteredOverlay, Vertical):
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
        self._rows: list = []
        self._pending_select: int | None = None

    def item_label(self, item) -> str:
        return str(item)

    def item_matches(self, item, query: str) -> bool:
        return query in self.item_label(item).lower()

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self.placeholder, id="picker-search")
        with VerticalScroll(id="picker-list"):
            pass
        yield Static(self.footer_text, id="picker-footer")

    def _make_row(self, item):
        return Static(self.item_label(item), classes="picker-row")

    def _sort_items(self, items: list) -> list:
        return items

    def _decorate_items(self, items: list) -> list:
        return items

    def _schedule_rebuild(self) -> None:
        self.run_worker(self._build_rows, exclusive=True, group="picker-rebuild")

    async def _build_rows(self) -> None:
        try:
            search = self.query_one("#picker-search", Input)
        except Exception:
            return
        query = search.value.strip().lower()
        if query:
            filtered = [i for i in self._items if self.item_matches(i, query)]
        else:
            filtered = list(self._items)
        filtered = self._sort_items(filtered)
        filtered = self._decorate_items(filtered)
        self._filtered = filtered
        target = self._pending_select if self._pending_select is not None else self._selected
        list_box = self.query_one("#picker-list", VerticalScroll)
        await list_box.remove_children(list_box.children)
        rows = []
        for i, item in enumerate(filtered):
            row = self._make_row(item)
            if i == 0:
                row.add_class("selected")
            rows.append(row)
        await list_box.mount_all(rows)
        self._rows = rows
        if filtered:
            self._selected = min(max(0, target if target is not None else 0), len(filtered) - 1)
        else:
            self._selected = 0
        self._update_selection()
        self._pending_select = None

    def show(self, items) -> None:
        self._items = items
        self._pending_select = None
        self._selected = 0
        search = self.query_one("#picker-search", Input)
        search.value = ""
        self._schedule_rebuild()
        self._store_center()
        self._recenter()
        self.add_class("visible")
        self.call_after_refresh(self._recenter)
        search.focus()

    def hide(self) -> None:
        self.remove_class("visible")

    @property
    def is_visible(self) -> bool:
        return "visible" in self.classes

    def _update_selection(self) -> None:
        for i, row in enumerate(self._rows):
            row.set_class(i == self._selected, "selected")

    def _select_item(self, item) -> None:
        self.post_message(self.Selected(item))

    def move(self, delta: int) -> None:
        if not self._filtered:
            return
        self._selected = (self._selected + delta) % len(self._filtered)
        self._update_selection()
        if 0 <= self._selected < len(self._rows):
            row = self._rows[self._selected]
            self.query_one("#picker-list", VerticalScroll).scroll_to_widget(row, animate=False)

    def update_items(self, items, select: int | None = None) -> None:
        self._items = items
        self._pending_select = select
        self._schedule_rebuild()

    def _on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._schedule_rebuild()

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

    def _sort_items(self, items: list) -> list:
        return sorted(items, key=lambda s: s.updated_at or s.created_at, reverse=True)


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

    def _decorate_items(self, items: list) -> list:
        if self._connected_only:
            return [i for i in items if i is not self.ADD_CUSTOM and getattr(i, "api_key", "")]
        return [self.ADD_CUSTOM] + items


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

    def _decorate_items(self, items: list) -> list:
        if self._add_enabled:
            return [self.ADD_MODEL] + items
        return items

    def _make_row(self, item):
        if isinstance(item, tuple) and len(item) >= 3 and item[2]:
            return Horizontal(
                Static(item[0], classes="model-name"),
                Static(item[2], classes="model-provider"),
                classes="picker-row",
            )
        return Static(self.item_label(item), classes="picker-row")


class StrengthPicker(_ListPicker):
    placeholder = "Search strengths..."
    footer_text = "Press ESC to exit"

    class Selected(Message):
        def __init__(self, effort: str) -> None:
            super().__init__()
            self.effort = effort

    class Dismissed(Message):
        pass

    def item_label(self, item) -> str:
        return str(item)

    def _select_item(self, item) -> None:
        self.post_message(self.Selected(item))


class McpPicker(_ListPicker):
    placeholder = "Search MCP servers..."
    footer_text = "Enter: toggle status  |  ESC: exit"

    class Toggled(Message):
        def __init__(self, server: str) -> None:
            super().__init__()
            self.server = server

    class Dismissed(Message):
        pass

    def item_label(self, item) -> str:
        return item[0]

    def item_matches(self, item, query: str) -> bool:
        return query in item[0].lower()

    def _make_row(self, item):
        name, enabled, conn = item
        if enabled:
            conn_color = {"connected": "green", "connecting": "yellow", "failed": "red"}.get(conn, "yellow")
            status = f"[{conn_color}]{conn}[/]  [green]enabled[/]"
        else:
            status = "[red]disabled[/]"
        return Horizontal(
            Static(name, classes="mcp-name"),
            Static(status, classes="mcp-status"),
            classes="picker-row",
        )

    def _select_item(self, item) -> None:
        self.post_message(self.Toggled(item[0]))

    async def _on_key(self, event: events.Key) -> None:
        if not self.is_visible:
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if self._filtered and 0 <= self._selected < len(self._filtered):
                self.post_message(self.Toggled(self._filtered[self._selected][0]))
            return
        await super()._on_key(event)


class ProviderKeyDialog(_CenteredOverlay, Vertical):
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
        self._store_center(h_frac=0.35)
        self._recenter()
        self.add_class("visible")
        self.call_after_refresh(self._recenter)
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
