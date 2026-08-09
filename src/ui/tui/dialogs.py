from src.utils.models import reasoning_effort_options
from src.utils.providers import get_store, list_providers
from src.mcp.manager import get_mcp_manager
from src.ui.tui.inputbar import ChatInput
from src.ui.tui.pickers import (
    McpPicker,
    ModelPicker,
    ProviderKeyDialog,
    ProviderPicker,
    SessionPicker,
    StrengthPicker,
)


class PickerMixin:
    def _input(self) -> ChatInput:
        return self.query_one("#input", ChatInput)

    def _picker(self) -> SessionPicker:
        return self.query_one("#session-picker", SessionPicker)

    def _open_session_picker(self) -> None:
        sessions = self._sm.list()
        self._palette().hide()
        self._set_palette_open(False)
        self._picker().show(sessions)

    def on_session_picker_selected(self, message: SessionPicker.Selected) -> None:
        self._switch_session(message.session.id)

    def on_session_picker_deleted(self, message: SessionPicker.Deleted) -> None:
        target = message.session
        current = self._sm.current
        was_current = current is not None and current.id == target.id
        self._sm.delete(target.id)
        remaining = self._sm.list()
        if not remaining:
            self._picker().hide()
            self._new_chat()
            return
        if was_current:
            self._new_chat()
            try:
                self.query_one("#picker-search").focus()
            except Exception:
                pass
        self._picker().update_items(remaining)

    def on_session_picker_dismissed(self, message: SessionPicker.Dismissed) -> None:
        self._input().focus()

    def _provider_picker(self) -> ProviderPicker:
        return self.query_one("#provider-picker", ProviderPicker)

    def _key_dialog(self) -> ProviderKeyDialog:
        return self.query_one("#provider-key-dialog", ProviderKeyDialog)

    def _model_picker(self) -> ModelPicker:
        return self.query_one("#model-picker", ModelPicker)

    def _strength_picker(self) -> StrengthPicker:
        return self.query_one("#strength-picker", StrengthPicker)

    def _open_provider_picker(self) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        picker = self._provider_picker()
        picker._connected_only = False
        picker.show(list_providers())

    def _open_model_picker(self) -> None:
        store = get_store()
        provider = store.get_active()
        if provider is None:
            self._append_error("No provider connected. Use /provider to connect one first.")
            self._input().focus()
            return
        self._palette().hide()
        self._set_palette_open(False)
        entries = []
        seen = set()
        for p in store.list_providers():
            if not p.api_key:
                continue
            for m in p.selected_models:
                if m not in seen:
                    seen.add(m)
                    entries.append((m, p.id, p.name))
        picker = self._model_picker()
        picker._add_enabled = True
        picker.show(entries)

    def _open_strength_picker(self) -> None:
        store = get_store()
        active = store.get_active()
        if active is None or not store.active_model:
            self._append_error("No model selected. Use /model to pick one first.")
            self._input().focus()
            return
        options = reasoning_effort_options(store.active_model, active.model_meta)
        if not options:
            self._append_error(f"{store.active_model} does not support reasoning strength.")
            self._input().focus()
            return
        self._palette().hide()
        self._set_palette_open(False)
        picker = self._strength_picker()
        picker.show(options)

    def _mcp_picker(self) -> McpPicker:
        return self.query_one("#mcp-picker", McpPicker)

    def _mcp_items(self) -> list:
        store = get_store()
        mgr = get_mcp_manager()
        return [(name, store.mcp_server_status(name), mgr.server_status(name)) for name in store.mcp_servers]

    def _refresh_mcp_picker(self) -> None:
        try:
            picker = self._mcp_picker()
        except Exception:
            return
        if not picker.is_visible:
            return
        if picker._pending_select is not None:
            return
        items = self._mcp_items()
        sig = tuple(items)
        if sig != getattr(self, "_mcp_picker_sig", None):
            self._mcp_picker_sig = sig
            picker.update_items(items, select=picker._selected)

    def _open_mcp_picker(self) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        self._mcp_picker().show(self._mcp_items())

    def on_mcp_picker_toggled(self, message: McpPicker.Toggled) -> None:
        store = get_store()
        store.toggle_mcp_server(message.server)
        get_mcp_manager().connect_async(store.mcp_servers)
        self._session.registry = None
        items = self._mcp_items()
        select = None
        for i, item in enumerate(items):
            if item[0] == message.server:
                select = i
                break
        self._mcp_picker().update_items(items, select=select)
        self._update_status()

    def on_mcp_picker_dismissed(self, message: McpPicker.Dismissed) -> None:
        self._input().focus()

    def on_strength_picker_selected(self, message: StrengthPicker.Selected) -> None:
        store = get_store()
        store.set_reasoning_effort(store.active_model, message.effort)
        self._session.reset_provider()
        self._update_status()
        self._input().focus()

    def on_strength_picker_dismissed(self, message: StrengthPicker.Dismissed) -> None:
        self._input().focus()

    def _open_model_picker_all(self, provider) -> None:
        store = get_store()
        models = store.get_provider_models(provider.id)
        if not models:
            self._append_error(f"No models available for {provider.name}. The model list could not be loaded.")
            self._input().focus()
            return
        self._pending_model_provider = provider
        self._palette().hide()
        self._set_palette_open(False)
        picker = self._model_picker()
        picker._add_enabled = False
        picker.show([(m, provider.id, provider.name) for m in models])

    def _open_add_model_flow(self) -> None:
        store = get_store()
        connected = [p for p in store.list_providers() if p.api_key]
        if not connected:
            self._append_error("Connect a provider first (/provider), then add its models.")
            self._input().focus()
            return
        self._palette().hide()
        self._set_palette_open(False)
        picker = self._provider_picker()
        picker._connected_only = True
        picker.show(connected)
        self._add_model_provider_flow = True

    def _close_key_dialog(self) -> None:
        self._key_dialog().hide()

    def on_provider_picker_selected(self, message: ProviderPicker.Selected) -> None:
        if getattr(self, "_add_model_provider_flow", False):
            self._add_model_provider_flow = False
            self._provider_picker()._connected_only = False
            self._open_model_picker_all(message.provider)
            return
        self._close_key_dialog()
        self._key_dialog().show(
            provider=message.provider,
            is_custom=message.provider.is_custom,
            values={} if not message.provider.is_custom else {
                "custom-name": message.provider.name,
                "custom-url": message.provider.base_url,
                "api-key": message.provider.api_key,
            },
        )

    def on_provider_picker_dismissed(self, message: ProviderPicker.Dismissed) -> None:
        self._add_model_provider_flow = False
        self._provider_picker()._connected_only = False
        self._input().focus()

    def on_provider_picker_add_custom(self, message: ProviderPicker.AddCustom) -> None:
        self._add_model_provider_flow = False
        self._key_dialog().show(is_custom=True)

    def on_provider_key_dialog_saved(self, message: ProviderKeyDialog.Saved) -> None:
        store = get_store()
        provider = message.provider
        values = message.values

        if provider is not None and not provider.is_custom:
            store.set_provider_api_key(provider.id, values["api_key"])
            store.set_active_provider(provider.id)
            store.set_active_model("")
            self._apply_active_provider(provider.id, store.get_provider(provider.id))
            self._open_model_picker_all(store.get_provider(provider.id))
            return

        if values.get("base_url"):
            pid = store.save_custom_provider(
                name=values.get("name", ""),
                base_url=values["base_url"],
                api_key=values.get("api_key", ""),
                pid=provider.id if provider is not None and provider.is_custom else "",
            )
            store.set_active_provider(pid)
            store.set_active_model("")
            self._apply_active_provider(pid, store.get_provider(pid))
            self.run_worker(self._refresh_custom_models(pid, open_picker=True), thread=True)

    async def _refresh_custom_models(self, pid: str, open_picker: bool = False) -> None:
        store = get_store()
        provider = store.get_provider(pid)
        if provider is None or not provider.base_url:
            return
        try:
            from src.utils.providers import fetch_models
            models = fetch_models(provider.base_url, provider.api_key)
        except Exception:
            models = []
        store.set_custom_models(pid, models)
        self.call_from_thread(self._custom_models_refreshed, pid, models, open_picker)

    def _custom_models_refreshed(self, pid: str, models: list[str], open_picker: bool = False) -> None:
        if open_picker:
            store = get_store()
            provider = store.get_provider(pid)
            if provider is not None and models:
                self._open_model_picker_all(provider)
            else:
                self._append_error(f"No models available for {pid}. The model list could not be fetched.")
                self._update_status()
                self._input().focus()
        else:
            self._update_status()

    def on_provider_key_dialog_canceled(self, message: ProviderKeyDialog.Canceled) -> None:
        self._input().focus()

    def on_model_picker_selected(self, message: ModelPicker.Selected) -> None:
        store = get_store()
        provider = getattr(self, "_pending_model_provider", None)
        if provider is None and message.provider_id:
            provider = store.get_provider(message.provider_id)
        if provider is None:
            provider = store.get_active()
        self._pending_model_provider = None
        if provider is not None:
            store.set_active_provider(provider.id)
            store.add_selected_model(provider.id, message.model)
        self._session.reset_provider()
        self._ctx_usage_tokens = 0
        self._update_status()
        self._input().focus()

    def on_model_picker_add_model(self, message: ModelPicker.AddModel) -> None:
        self._open_add_model_flow()

    def on_model_picker_dismissed(self, message: ModelPicker.Dismissed) -> None:
        self._pending_model_provider = None
        self._input().focus()

    def _apply_active_provider(self, pid: str, provider) -> None:
        store = get_store()
        if provider is None:
            return
        if store.active_provider_id != pid:
            store.set_active_provider(pid)
        self._session.reset_provider()
        self._ctx_usage_tokens = 0
        self._update_status()

    def on_key(self, event) -> None:
        modals = [
            ("#session-picker", self._picker),
            ("#provider-picker", self._provider_picker),
            ("#model-picker", self._model_picker),
            ("#strength-picker", self._strength_picker),
            ("#mcp-picker", self._mcp_picker),
            ("#provider-key-dialog", self._key_dialog),
        ]
        active = None
        for selector, getter in modals:
            widget = getter()
            if widget.is_visible:
                active = widget
                break
        if active is None:
            return
        key = event.key
        if key == "escape":
            event.stop()
            active.hide()
            if active is self._picker():
                active.post_message(SessionPicker.Dismissed())
            elif active is self._provider_picker():
                active.post_message(ProviderPicker.Dismissed())
            elif active is self._model_picker():
                active.post_message(ModelPicker.Dismissed())
            elif active is self._strength_picker():
                active.post_message(StrengthPicker.Dismissed())
            elif active is self._key_dialog():
                active.post_message(ProviderKeyDialog.Canceled())
            return
        if active is self._key_dialog():
            return
        if key == "up":
            event.stop()
            active.move(-1)
            return
        if key == "down":
            event.stop()
            active.move(1)
            return
        if key == "enter":
            event.stop()
            if active._filtered and 0 <= active._selected < len(active._filtered):
                item = active._filtered[active._selected]
                active.hide()
                active._select_item(item)
            else:
                active.post_message(active.Dismissed())
            return
