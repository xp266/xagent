from src.utils.providers import get_store, list_providers
from src.ui.tui.widgets import ChatInput, ModelPicker, ProviderKeyDialog, ProviderPicker, SessionPicker


class PickerMixin:
    def _picker(self) -> SessionPicker:
        return self.query_one("#session-picker", SessionPicker)

    def _open_session_picker(self) -> None:
        sessions = self._sm.list()
        self._palette().hide()
        self._set_palette_open(False)
        self._picker().show(sessions)

    def on_session_picker_selected(self, message: SessionPicker.Selected) -> None:
        self._switch_session(message.session.id)

    def on_session_picker_dismissed(self, message: SessionPicker.Dismissed) -> None:
        self.query_one("#input", ChatInput).focus()

    def _provider_picker(self) -> ProviderPicker:
        return self.query_one("#provider-picker", ProviderPicker)

    def _key_dialog(self) -> ProviderKeyDialog:
        return self.query_one("#provider-key-dialog", ProviderKeyDialog)

    def _model_picker(self) -> ModelPicker:
        return self.query_one("#model-picker", ModelPicker)

    def _open_provider_picker(self) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        self._provider_picker().show(list_providers())

    def _open_model_picker(self) -> None:
        store = get_store()
        provider = store.get_active()
        if provider is None:
            self._append_error("No active provider. Use /provider to select one first.")
            return
        self._palette().hide()
        self._set_palette_open(False)
        self._model_picker().show(store.get_provider_models(provider.id))

    def _close_key_dialog(self) -> None:
        self._key_dialog().hide()

    def on_provider_picker_selected(self, message: ProviderPicker.Selected) -> None:
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
        self.query_one("#input", ChatInput).focus()

    def on_provider_picker_add_custom(self, message: ProviderPicker.AddCustom) -> None:
        self._key_dialog().show(is_custom=True)

    def on_provider_key_dialog_saved(self, message: ProviderKeyDialog.Saved) -> None:
        store = get_store()
        provider = message.provider
        values = message.values

        if provider is not None and not provider.is_custom:
            store._config.providers.setdefault(provider.id, {})["api_key"] = values["api_key"]
            store.save()
            store.set_active_provider(provider.id)
            self._apply_active_provider(provider.id, store.get_provider(provider.id))
            self.query_one("#input", ChatInput).focus()
            return

        if values.get("base_url"):
            pid = store.save_custom_provider(
                name=values.get("name", ""),
                base_url=values["base_url"],
                api_key=values.get("api_key", ""),
                pid=provider.id if provider is not None and provider.is_custom else "",
            )
            store.set_active_provider(pid)
            self._apply_active_provider(pid, store.get_provider(pid))
            self.run_worker(self._refresh_custom_models(pid), thread=True)
            self.query_one("#input", ChatInput).focus()

    async def _refresh_custom_models(self, pid: str) -> None:
        store = get_store()
        provider = store.get_provider(pid)
        if provider is None or not provider.base_url:
            return
        try:
            from src.utils.providers import fetch_models
            models = fetch_models(provider.base_url, provider.api_key)
        except Exception as e:
            models = []
        store.set_custom_models(pid, models)
        self.call_from_thread(self._custom_models_refreshed, pid, models)

    def _custom_models_refreshed(self, pid: str, models: list[str]) -> None:
        self._update_status()

    def on_provider_key_dialog_canceled(self, message: ProviderKeyDialog.Canceled) -> None:
        self.query_one("#input", ChatInput).focus()

    def on_model_picker_selected(self, message: ModelPicker.Selected) -> None:
        store = get_store()
        store.set_active_model(message.model)
        self._session.reset_provider()
        self._ctx_usage_tokens = 0
        self._update_status()
        self.query_one("#input", ChatInput).focus()

    def on_model_picker_dismissed(self, message: ModelPicker.Dismissed) -> None:
        self.query_one("#input", ChatInput).focus()

    def _apply_active_provider(self, pid: str, provider) -> None:
        store = get_store()
        if provider is None:
            return
        if store.active_provider_id != pid:
            store.set_active_provider(pid)
        if not store.active_model or (provider.models and store.active_model not in provider.models):
            store.set_active_provider(pid, model=provider.models[0] if provider.models else "")
        self._session.reset_provider()
        self._ctx_usage_tokens = 0
        self._update_status()

    def on_key(self, event) -> None:
        modals = [
            ("#session-picker", self._picker),
            ("#provider-picker", self._provider_picker),
            ("#model-picker", self._model_picker),
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
