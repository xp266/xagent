from textual import events
from textual.message import Message
from textual.widgets import TextArea, Markdown, Collapsible


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

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "ctrl+m"):
            event.stop()
            event.prevent_default()
            text = self.text
            if text:
                self.clear()
                self.post_message(self.Submitted(text))
            return
        await super()._on_key(event)
