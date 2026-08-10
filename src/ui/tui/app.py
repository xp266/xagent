import asyncio
import os
import sys
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from src.agent import get_session_manager
from src.mcp.manager import get_mcp_manager
from src.ui.tui.animations import SpinnerMixin
from src.ui.tui.canvas import CanvasBlock, ChatCanvas
from src.ui.tui.chat import MAX_CANVAS_BLOCKS, TRIM_SLACK, ChatMixin
from src.ui.tui.colors import _USER_BG
from src.ui.tui.commands import get_commands, match_commands
from src.ui.tui.css import CSS
from src.ui.tui.dialogs import PickerMixin
from src.ui.tui.logo import LogoWidget
from src.ui.tui.statusbar import StatusMixin
from src.ui.tui.turnrender import TurnRenderMixin, new_turn_state
from src.ui.tui.inputbar import ChatInput, CommandPalette
from src.ui.tui.pickers import (
    McpPicker,
    ModelPicker,
    ProviderKeyDialog,
    ProviderPicker,
    SessionPicker,
    StrengthPicker,
)
from src.utils.config import get_config
from src.utils.providers import get_store


class XAgentTUI(SpinnerMixin, StatusMixin, TurnRenderMixin, ChatMixin, PickerMixin, App):
    CSS = CSS

    def __init__(self):
        super().__init__()
        self._sm = get_session_manager()
        self._launch_dir = os.getcwd()
        self._project = self._launch_dir
        self._session = self._sm.create(path=self._project, persist=False)
        self._ctx_usage_tokens = 0
        self._busy = False
        self._current = None
        self._spinner_idx = 0
        self._last_spinner_time = 0.0
        self._spinners = {}
        self._waves = []
        self._idle_tick_time = 0.0
        self._add_model_provider_flow = False
        self._pending_model_provider = None
        self._deferred = None
        self._closing = False
        get_mcp_manager().connect_async(get_store().mcp_servers)

    def _chat(self):
        return self.query_one("#chat-box")

    def _canvas(self) -> ChatCanvas:
        return self.query_one("#chat-canvas", ChatCanvas)

    def _append_block(
        self,
        *,
        kind: str = "body",
        title: str = "",
        title_style: str = "",
        body_style: str = "",
        bg: str | None = None,
        content_bg: str | None = None,
        pad_top: int = 1,
        pad_bottom: int = 1,
        pad_left: int | None = 3,
        pad_right: int = 1,
        content_pad_left: int | None = None,
        expandable: bool = False,
        collapsed: bool = False,
        hide_arrow: bool = False,
    ) -> CanvasBlock:
        self._hide_logo()
        return self._canvas().append(
            CanvasBlock(
                kind=kind,
                title=title,
                title_style=title_style,
                body_style=body_style,
                bg=bg,
                content_bg=content_bg,
                pad_top=pad_top,
                pad_bottom=pad_bottom,
                pad_left=3 if pad_left is None else pad_left,
                pad_right=pad_right,
                content_pad_left=content_pad_left,
                expandable=expandable,
                collapsed=collapsed,
                hide_arrow=hide_arrow,
            )
        )

    def _show_logo(self) -> None:
        logo = self._logo()
        if logo is None:
            logo = Vertical(LogoWidget(), id="logo-overlay")
            self._chat().mount(logo)
        logo.display = True

    def _logo(self):
        try:
            return self._chat().get_widget_by_id("logo-overlay")
        except Exception:
            return None

    def _hide_logo(self) -> None:
        logo = self._logo()
        if logo is not None:
            logo.display = False

    def _clear_chat_messages(self) -> None:
        self._canvas().clear()
        logo = self._logo()
        if logo is not None:
            logo.display = False

    def _scroll_end(self, force: bool = False) -> None:
        chat = self._chat()
        if force or chat.max_scroll_y <= 0 or chat.scroll_offset.y >= chat.max_scroll_y - 3:
            chat.scroll_end(animate=False)

    def _append_user(self, text: str) -> None:
        block = self._append_block(kind="user", bg=_USER_BG)
        block.update(text)
        self._scroll_end()

    def _append_error(self, text: str) -> None:
        block = self._append_block(kind="error", body_style="bold #FF5555")
        block.update(text)
        self._scroll_end()

    def _run_manual_compact(self, focus: str = "") -> None:
        if self._busy:
            self._deferred = lambda: self._run_manual_compact(focus)
            self._append_error("Agent is busy, compaction queued after the turn")
            return
        from src.agent.cancel import reset, set_turn_task

        reset()
        self._input().busy = True
        self._busy = True
        self._waves.clear()
        self._current = new_turn_state()
        self._ensure_waiting()
        self._scroll_end()
        task = asyncio.create_task(self._compact_worker(focus), name="xagent-compact")
        set_turn_task(task)

    async def _compact_worker(self, focus: str) -> None:
        from src.agent.cancel import set_turn_task
        from src.agent.compact import compact_session_stream

        try:
            async for event in compact_session_stream(self._session, focus=focus):
                if self._exit or self._closing:
                    break
                self._handle_event(event)
            self._update_status()
        except asyncio.CancelledError:
            if self._exit or self._closing:
                raise
            block = self._append_block(kind="summary", pad_top=1, pad_left=3, pad_right=1)
            block.update("Compaction interrupted")
            self._scroll_end()
        except Exception as e:
            if self._exit or self._closing:
                return
            self._append_error(f"{type(e).__name__}: {e}")
        finally:
            if not (self._exit or self._closing):
                self._hide_waiting()
                self._stop_all_spinners()
                self._waves.clear()
                self._busy = False
                self._input().busy = False
                self._current = None
                self._update_status()
                self._scroll_end()
            set_turn_task(None)
        if self._exit or self._closing:
            return
        deferred = self._deferred
        self._deferred = None
        if deferred is not None:
            deferred()
            return
        self._input().focus()

    def _trim_canvas_blocks(self) -> None:
        canvas = self._canvas()
        if len(canvas._blocks) <= MAX_CANVAS_BLOCKS + TRIM_SLACK:
            return
        chat = self._chat()
        removed_lines = 0
        excess = len(canvas._blocks) - MAX_CANVAS_BLOCKS
        canvas._begin_bulk()
        try:
            while excess > 0 and canvas._blocks and canvas._blocks[0].kind != "divider":
                removed_lines += len(canvas._blocks[0]._lines)
                canvas.remove(canvas._blocks[0])
                excess -= 1
        finally:
            canvas._end_bulk()
        if removed_lines:
            chat.scroll_to(max(0, chat.scroll_offset.y - removed_lines), animate=False)

    def _tick_animations(self) -> None:
        try:
            self._canvas()._settle_resize()
        except Exception:
            pass
        try:
            self._trim_canvas_blocks()
        except Exception:
            pass
        if self._busy:
            self._tick_spinners()
            self._tick_status_wave()
        else:
            now = time.monotonic()
            if now - self._idle_tick_time >= 0.5:
                self._idle_tick_time = now
                self._update_status()
                self._refresh_mcp_picker()
        if self._current is not None:
            self._flush_streaming_content()
            self._tick_retry()

    def _palette(self) -> CommandPalette:
        return self.query_one("#command-palette", CommandPalette)

    def _refresh_palette(self) -> None:
        if getattr(self, "_suppress_palette", False):
            return
        text = self._input().text
        if not text.startswith("/"):
            self._palette().hide()
            self._set_palette_open(False)
            return
        query = text[1:].lstrip()
        commands = match_commands(query)
        self._palette().show(commands)
        self._set_palette_open(bool(commands))

    def _set_palette_open(self, open: bool) -> None:
        self._input().palette_open = open

    def on_chat_input_text_edited(self, message: ChatInput.TextEdited) -> None:
        self._refresh_palette()

    def on_chat_input_navigate(self, message: ChatInput.Navigate) -> None:
        self._palette().move(message.delta)

    def on_chat_input_accept_palette(self, message: ChatInput.AcceptPalette) -> None:
        cmd = self._palette().selected_command
        if cmd is None:
            return
        inp = self._input()
        inp.clear()
        self._palette().hide()
        self._set_palette_open(False)
        self._suppress_palette = True
        inp.insert(f"/{cmd.name} ")
        self._suppress_palette = False
        inp.focus()

    def _handle_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.startswith("/"):
            parts = text.split(None, 1)
            name = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            for cmd in get_commands():
                if cmd.name == name or name in cmd.aliases:
                    cmd.handler(self, args)
                    return
            self._append_error(f"Unknown command: /{name}")
            return
        if self._busy:
            self._append_error("Agent is busy, please wait.")
            return
        self._send(text)

    def _send(self, text: str) -> None:
        cfg = get_config()
        if not cfg.base_url:
            self._append_error("No provider connected. Type /provider to connect one.")
            return
        if not cfg.model:
            self._append_error("No model selected. Type /model to select one.")
            return
        from src.agent.cancel import reset, set_turn_task
        reset()
        self._input().busy = True
        self._busy = True
        self._waves.clear()
        self._current = new_turn_state()
        self._append_user(text)
        self._ensure_waiting()
        self._scroll_end()
        if self._session.name == "New Session":
            s = self._session
            asyncio.create_task(self._name_worker(s, text), name="xagent-naming")
        task = asyncio.create_task(self._turn_worker(text), name="xagent-turn")
        set_turn_task(task)

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        self._palette().hide()
        self._set_palette_open(False)
        self._handle_input(message.text)

    def on_chat_input_interrupt_confirmed(self, message: ChatInput.InterruptConfirmed) -> None:
        if not self._busy:
            return
        from src.agent.cancel import cancel
        cancel()
        self._update_input_status("Interrupting...")

    def on_resize(self, event) -> None:
        self.call_after_refresh(self._update_status)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-box"):
            yield ChatCanvas(id="chat-canvas")

        yield CommandPalette(id="command-palette")

        with Vertical(id="input-box"):
            yield ChatInput(soft_wrap=True, id="input")
            with Horizontal(id="input-status-row"):
                yield Static("", id="input-status")
                yield Static("", id="input-mcp")

        with Vertical(id="status-box"):
            yield Static("", id="status")

        yield SessionPicker(id="session-picker")
        yield ProviderPicker(id="provider-picker")
        yield ModelPicker(id="model-picker")
        yield StrengthPicker(id="strength-picker")
        yield McpPicker(id="mcp-picker")
        yield ProviderKeyDialog(id="provider-key-dialog")

    def on_mount(self) -> None:
        self.title = "XAgent"
        if sys.platform == "win32":
            self._sync_available = True
        from src.agent.truncate import TruncateService
        TruncateService().cleanup()
        self._update_status()
        self._show_logo()
        self._scroll_end()
        self.set_interval(1 / 60, self._tick_animations, pause=False)
        self._input().focus()
        asyncio.create_task(self._prewarm(), name="xagent-prewarm")

    async def _prewarm(self) -> None:
        try:
            await asyncio.sleep(0)
            self.screen._on_timer_update()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        try:
            await asyncio.to_thread(self._prewarm_providers)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _prewarm_providers(self) -> None:
        import src.ai.openai
        import src.ai.anthropic
        try:
            import openai.resources
        except Exception:
            pass
        try:
            self._session.provider
        except Exception:
            pass

    def on_unmount(self) -> None:
        self._closing = True
        from src.agent.cancel import cancel
        cancel()


def run_tui() -> None:
    app = XAgentTUI()
    app.run()
