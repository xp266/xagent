import os
import threading
import time

os.environ["XAGENT_DATA_DIR"] = "/tmp/xagent-test-data-cancel"

from src.agent.session import Session
from src.ai.base import Provider
from src.types.events import StreamEvent
from src.agent import cancel as cancelmod
from src.agent.turn import run_session_turn


class FakeRegistry:
    def schemas(self):
        return None


class FakeProvider(Provider):
    def __init__(self):
        self.model = "test"

    def stream(self, messages, tools=None):
        yield StreamEvent(type="tool-call", data={"id": "1", "name": "x", "input": {}})
        yield StreamEvent(type="tool-result", data={"id": "1", "name": "x", "result": "done"})
        yield StreamEvent(type="text-delta", data="partial reply")
        yield StreamEvent(type="step-finish", data={"finish_reason": "stop", "usage": {}})
        yield StreamEvent(type="finish", data={"finish_reason": "stop", "usage": {}})


class ErrorProvider(Provider):
    def __init__(self, code):
        self.model = "test"
        self.code = code

    def stream(self, messages, tools=None):
        yield StreamEvent(type="provider-error", data={"error": "boom", "code": self.code})


def _make_session(provider):
    s = Session()
    s.provider = provider
    s.registry = FakeRegistry()
    return s


def test_cancel_pending_commits_interrupted_partial():
    cancelmod.reset()
    cancelmod.cancel()
    try:
        s = _make_session(FakeProvider())
        types = [e.type for e in run_session_turn(s, "hello")]
        assert "turn-cancelled" in types
        tool_msgs = [m for m in s.messages if m.get("role") == "tool"]
        assert tool_msgs and "interrupted by user" in tool_msgs[0]["content"]
    finally:
        cancelmod.reset()


def test_cancelled_provider_error_is_not_retried():
    cancelmod.reset()
    cancelmod.cancel()
    try:
        s = _make_session(ErrorProvider(500))
        types = [e.type for e in run_session_turn(s, "hello")]
        assert "retry-schedule" not in types
        assert "turn-cancelled" in types
    finally:
        cancelmod.reset()


def test_abort_handlers_invoked_by_watchdog():
    cancelmod.reset()
    called = []

    def handler():
        called.append(True)

    cancelmod.register_abort(handler)
    try:
        cancelmod.cancel(grace=0.0)
        deadline = time.time() + 3
        while not called and time.time() < deadline:
            time.sleep(0.05)
        assert called, "watchdog abort handler never fired"
    finally:
        cancelmod.unregister_abort(handler)
        cancelmod.reset()


def test_unregistered_abort_is_not_called():
    cancelmod.reset()
    called = []

    def handler():
        called.append(True)

    cancelmod.register_abort(handler)
    cancelmod.unregister_abort(handler)
    cancelmod.abort()
    assert called == []


class BlockingProvider(Provider):
    def __init__(self):
        self.model = "test"
        self.aborted = threading.Event()

    def stream(self, messages, tools=None):
        yield StreamEvent(type="text-delta", data="a")
        while not self.aborted.wait(0.1):
            time.sleep(0.05)
        raise ConnectionError("connection closed by abort")

    def abort(self):
        self.aborted.set()


def test_stuck_stream_is_hard_aborted_and_cancelled():
    cancelmod.reset()
    p = BlockingProvider()
    s = _make_session(p)
    cancelmod.register_abort(p.abort)
    events = []
    t = threading.Thread(target=lambda: events.extend(e.type for e in run_session_turn(s, "hi")))
    try:
        t.start()
        time.sleep(0.6)
        cancelmod.cancel(grace=0.2)
        t.join(timeout=5)
        assert not t.is_alive(), "turn did not finish after hard abort"
        assert "turn-cancelled" in events
        assert p.aborted.is_set()
    finally:
        cancelmod.unregister_abort(p.abort)
        cancelmod.reset()