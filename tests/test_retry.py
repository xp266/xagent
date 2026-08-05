import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests away from the real config dir and from each other."""
    monkeypatch.setenv("XAGENT_DATA_DIR", str(tmp_path))


from src.agent.turn import _is_retryable, _ProviderError, run_session_turn
from src.agent.session import Session
from src.ai.base import Provider
from src.types.events import StreamEvent
import src.agent.turn as turnmod


class FakeRegistry:
    def schemas(self):
        return None


class FakeProvider(Provider):
    def __init__(self, fail_times=0, code=429):
        self.model = "test"
        self.fail_times = fail_times
        self.code = code
        self.calls = 0

    def stream(self, messages, tools=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            yield StreamEvent(type="provider-error", data={
                "error": f"Error code: {self.code} - rate limited",
                "code": self.code,
            })
            return
        yield StreamEvent(type="text-delta", data="ok")
        yield StreamEvent(type="step-finish", data={"finish_reason": "stop", "usage": {}})
        yield StreamEvent(type="finish", data={"finish_reason": "stop", "usage": {}})


def _run(provider):
    old = turnmod._retry_delay
    turnmod._retry_delay = lambda a: 0.001
    try:
        s = Session()
        s.provider = provider
        s.registry = FakeRegistry()
        return list(run_session_turn(s, "hello"))
    finally:
        turnmod._retry_delay = old


def test_retryable_status_codes():
    assert _is_retryable(_ProviderError("rate limited", 429))
    assert _is_retryable(_ProviderError("boom", 500))
    assert _is_retryable(_ProviderError("timeout", 408))
    assert not _is_retryable(_ProviderError("unauthorized", 401))
    assert not _is_retryable(_ProviderError("bad request", 400))
    assert not _is_retryable(_ProviderError("insufficient quota", 0))


def test_chinese_rate_limit_hint():
    assert _is_retryable(_ProviderError("您的账户已达到速率限制，请您控制请求频率", 0))


def test_provider_error_429_retries_then_succeeds():
    p = FakeProvider(fail_times=1, code=429)
    events = _run(p)
    assert [e.type for e in events].count("retry-schedule") == 1
    assert [e.type for e in events][-1] == "finish"
    assert p.calls == 2


def test_provider_error_429_gives_up_after_3():
    p = FakeProvider(fail_times=99, code=429)
    events = _run(p)
    types = [e.type for e in events]
    assert types.count("retry-schedule") == 3
    assert types[-1] == "provider-error"
    assert p.calls == 4


def test_provider_error_401_no_retry():
    p = FakeProvider(fail_times=99, code=401)
    events = _run(p)
    types = [e.type for e in events]
    assert "retry-schedule" not in types
    assert types[-1] == "provider-error"
    assert p.calls == 1
