from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DATA = tempfile.mkdtemp(prefix="xagent-flow-data-")
os.environ["XAGENT_DATA_DIR"] = _DATA
os.environ["XAGENT_NO_CATALOG_REFRESH"] = "1"
os.environ.setdefault("XAGENT_RATE_MS", "0")
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from tests.stress.fake_provider import start as start_fake_provider

from src.agent.session import get_session_manager
from src.ui.tui.app import XAgentTUI
from src.ui.tui.inputbar import ChatInput


def _write_config(port: int) -> None:
    cfg = {
        "active_provider": "custom:flow",
        "active_model": "step-3.7-flash",
        "reasoning_effort": {},
        "model_contexts": {"step-3.7-flash": 150000},
        "providers": {
            "custom:flow": {
                "name": "Flow Mock",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "api_key": "sk-flow-test",
                "models": ["step-3.7-flash"],
                "selected_models": ["step-3.7-flash"],
            }
        },
        "mcp_servers": {},
    }
    os.makedirs(_DATA, exist_ok=True)
    with open(os.path.join(_DATA, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _seed_session() -> str:
    sm = get_session_manager()
    s = sm.create(name="Flow Seed", path=_ROOT, persist=True)
    s.messages = [
        {"role": "user", "content": "你好"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "思考一下怎么做",
            "tool_calls": [
                {
                    "id": "tc_flow_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path": "flow.txt"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_flow_1",
            "name": "read",
            "content": "(flow.txt, 3 lines)\n1: line one\n2: line two\n3: line three",
            "is_error": False,
        },
        {
            "role": "assistant",
            "content": "读完了",
            "_meta": {"model": "step-3.7-flash", "elapsed": 2.5, "prompt_tokens": 100},
        },
    ]
    sm.save(s)
    return s.id


def _submit(app, text: str) -> None:
    app._input().post_message(ChatInput.Submitted(text))


async def _pause(pilot, app, turns: int = 2) -> None:
    for _ in range(turns):
        await pilot.pause()
    exc = app._exception
    if exc is not None:
        raise exc


async def _wait_busy(app, timeout: float = 10.0) -> None:
    t0 = time.monotonic()
    while app._busy and time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.02)
    if app._busy:
        raise AssertionError("turn did not finish in time")


async def main() -> int:
    server = start_fake_provider()
    port = server.server_address[1]
    _write_config(port)
    seed_id = _seed_session()

    app = XAgentTUI()
    async with app.run_test(size=(96, 32)) as pilot:
        await _pause(pilot, app, 3)

        _submit(app, "/session")
        await _pause(pilot, app, 3)
        picker = app._picker()
        assert picker.is_visible, "/session should open the session picker"
        if not picker._filtered:
            await _pause(pilot, app, 3)
        assert picker._filtered, "session picker should list sessions"
        picker._select_item(picker._filtered[0])
        await _pause(pilot, app, 4)
        assert app._session.id == seed_id, "session switch should select the seeded session"

        _submit(app, "/new")
        await _pause(pilot, app, 3)
        assert app._session.id != seed_id, "/new should start a fresh session"

        _submit(app, f"/session {seed_id}")
        await _pause(pilot, app, 4)
        assert app._session.id == seed_id, "direct /session <id> should switch sessions"

        _submit(app, "/model")
        await _pause(pilot, app, 3)
        model_picker = app._model_picker()
        assert model_picker.is_visible, "/model should open the model picker"
        if not model_picker._filtered:
            await _pause(pilot, app, 3)
        assert model_picker._filtered, "model picker should list the mock model"
        model_picker._select_item(model_picker._filtered[0])
        await _pause(pilot, app, 4)

        _submit(app, "/effort")
        await _pause(pilot, app, 3)
        strength = app._strength_picker()
        if strength.is_visible:
            if strength._filtered:
                strength._select_item(strength._filtered[0])
                await _pause(pilot, app, 4)
            else:
                strength.hide()
                strength.post_message(strength.Dismissed())
                await _pause(pilot, app, 2)

        _submit(app, "/provider")
        await _pause(pilot, app, 3)
        provider_picker = app._provider_picker()
        if provider_picker.is_visible and provider_picker._filtered:
            provider_picker._select_item(provider_picker._filtered[0])
            await _pause(pilot, app, 3)
        key_dialog = app._key_dialog()
        if key_dialog.is_visible:
            key_dialog.hide()
            key_dialog.post_message(key_dialog.Canceled())
            await _pause(pilot, app, 2)

        _submit(app, "/mcp")
        await _pause(pilot, app, 3)
        mcp_picker = app._mcp_picker()
        if mcp_picker.is_visible:
            mcp_picker.hide()
            mcp_picker.post_message(mcp_picker.Dismissed())
            await _pause(pilot, app, 2)

        _submit(app, "/compact")
        await _wait_busy(app)
        await _pause(pilot, app, 3)

        _submit(app, "/exit")
        await asyncio.sleep(0.3)

    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))