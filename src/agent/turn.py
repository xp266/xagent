import json
import time
from collections.abc import Iterator

import httpx

from src.agent.cancel import TurnCancelled, is_cancelled, register_abort, unregister_abort
from src.agent.loop import agent_stream
from src.agent.session import Session, get_session_manager
from src.types.events import (
    LLMResponse, StreamEvent, TokenUsage,
    ToolCallData, ToolResultData, ToolErrorData, ProviderErrorData, RetryScheduleData,
)
from src.types.messages import AssistantMessage

INTERRUPTED_TOOL_RESULT = "Tool call interrupted by user."

RETRY_LIMIT = 3
RETRY_BASE_DELAY = 5.0

_NON_RETRYABLE_HINTS = (
    "invalid api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "insufficient_quota",
    "insufficient quota",
    "billing",
    "out of credits",
    "permission",
    "access denied",
    "balance",
    "wrong api key",
)

_RETRYABLE_HINTS = (
    "rate limit",
    "too many requests",
    "request queue is full",
    "temporarily unavailable",
    "server overload",
    "overloaded",
    "try again",
    "temporary failure",
    "connection",
    "timed out",
    "timeout",
    "busy",
    "retry",
    "速率限制",
    "频率",
    "请稍后",
    "服务器繁忙",
)


def _is_retryable(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if not isinstance(status, int):
        response = getattr(e, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    if isinstance(status, int) and status > 0:
        if status in (400, 401, 402, 403, 404, 422):
            return False
        return status in (408, 409, 429) or status >= 500
    if isinstance(e, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    msg = str(e).lower()
    if any(k in msg for k in _NON_RETRYABLE_HINTS):
        return False
    if any(k in msg for k in ("[429]", "[500]", "[502]", "[503]", "[504]", "429 ", " 500 ", " 502 ", " 503 ", " 504 ")):
        return True
    return any(k in msg for k in _RETRYABLE_HINTS)


def _retry_delay(attempt: int) -> float:
    return RETRY_BASE_DELAY * (2 ** (attempt - 1))


def _sleep_interruptible(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if is_cancelled():
            raise TurnCancelled
        time.sleep(0.25)


class _ProviderError(Exception):
    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.status_code = code


def _attach_turn_meta(session: Session, model: str, usage: TokenUsage, prompt_tokens: int, elapsed: float) -> None:
    meta = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "total_tokens": usage.total_tokens,
        "elapsed": round(elapsed, 2),
    }
    for msg in reversed(session.msgs.get_messages()):
        if isinstance(msg, AssistantMessage):
            msg.meta = dict(meta)
            return


def _fill_tool_calls(response: LLMResponse, tool_calls_pending: list[ToolCallData]) -> None:
    for tc in tool_calls_pending:
        response.tool_calls.append({
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc["input"]) if isinstance(tc["input"], dict) else str(tc["input"]),
            },
        })


def _commit_response(session: Session, response: LLMResponse, tool_results: list, cancelled: bool) -> None:
    session.msgs.add_assistant(response)
    if cancelled:
        executed = {tr["id"] for tr in tool_results}
        for tc in response.tool_calls:
            if tc["id"] not in executed:
                session.msgs.add_tool(tc["id"], INTERRUPTED_TOOL_RESULT)


def _persist(session: Session) -> None:
    session.sync_messages()
    get_session_manager().save(session)


def run_session_turn(session: Session, user_input: str) -> Iterator[StreamEvent]:
    session.msgs.add_user(user_input)
    start = time.monotonic()

    cancelled = False
    retry_count = 0

    provider = session.provider
    register_abort(provider.abort)
    try:
        while not cancelled:
            response = LLMResponse()
            tool_calls_pending: list[ToolCallData] = []
            tool_results: list[ToolResultData | ToolErrorData] = []
            turn_usage = TokenUsage()
            last_prompt_tokens = 0
            committed = False

            try:
                stream = agent_stream(
                    provider,
                    session.msgs.get_api_messages(),
                    session.registry.schemas() or None,
                    session.registry,
                )

                for event in stream:
                    if event.type == "step-start":
                        response = LLMResponse()
                        tool_calls_pending = []
                        tool_results = []
                    elif event.type == "reasoning-delta":
                        response.reasoning += event.data
                    elif event.type == "signature":
                        response.signature = event.data
                    elif event.type == "text-delta":
                        response.content += event.data
                    elif event.type == "tool-call":
                        tool_calls_pending.append(event.data)
                    elif event.type in ("tool-result", "tool-error"):
                        tool_results.append(event.data)
                    elif event.type == "step-finish":
                        response.finish_reason = event.data.get("finish_reason", "")
                        usage = event.data.get("usage", {})
                        if usage:
                            last_prompt_tokens = usage.get("prompt_tokens", 0)
                            turn_usage = TokenUsage(
                                prompt_tokens=turn_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                                completion_tokens=turn_usage.completion_tokens + usage.get("completion_tokens", 0),
                                total_tokens=turn_usage.total_tokens + usage.get("total_tokens", 0),
                            )
                            session.token_usage = TokenUsage(
                                prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                                completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                                total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                            )
                    elif event.type == "provider-error":
                        if is_cancelled():
                            raise TurnCancelled
                        raise _ProviderError(
                            event.data.get("error", "provider error"),
                            int(event.data.get("code") or 0),
                        )

                    yield event

            except TurnCancelled:
                cancelled = True
            except Exception as e:
                if is_cancelled():
                    cancelled = True
                elif retry_count < RETRY_LIMIT and _is_retryable(e):
                    retry_count += 1
                    if tool_calls_pending or tool_results:
                        _fill_tool_calls(response, tool_calls_pending)
                        _commit_response(session, response, tool_results, cancelled=False)
                        for tr in tool_results:
                            session.msgs.add_tool(
                                tr["id"],
                                tr.get("result", tr.get("error", "")),
                                tr.get("attachments"),
                                is_error=bool(tr.get("is_error") or tr.get("error")),
                            )
                        _persist(session)
                    delay = _retry_delay(retry_count)
                    retry_data: RetryScheduleData = {
                        "error": str(e),
                        "delay": delay,
                        "attempt": retry_count,
                    }
                    yield StreamEvent(type="retry-schedule", data=retry_data)
                    try:
                        _sleep_interruptible(delay)
                    except TurnCancelled:
                        cancelled = True
                    continue
                else:
                    err_data: ProviderErrorData = {"error": str(e), "code": 0}
                    yield StreamEvent(type="provider-error", data=err_data)
                    _persist(session)
                    return

            if not cancelled:
                _fill_tool_calls(response, tool_calls_pending)
                _commit_response(session, response, tool_results, cancelled=False)
                committed = True
                retry_count = 0
                for tr in tool_results:
                    session.msgs.add_tool(
                        tr["id"],
                        tr.get("result", tr.get("error", "")),
                        tr.get("attachments"),
                        is_error=bool(tr.get("is_error") or tr.get("error")),
                    )
                _persist(session)

                if response.finish_reason != "tool_calls":
                    break

        if cancelled and not committed:
            _fill_tool_calls(response, tool_calls_pending)
            _commit_response(session, response, tool_results, cancelled=True)
            for tr in tool_results:
                session.msgs.add_tool(
                    tr["id"],
                    tr.get("result", tr.get("error", "")),
                    tr.get("attachments"),
                    is_error=bool(tr.get("is_error") or tr.get("error")),
                )
            _persist(session)

        if cancelled:
            yield StreamEvent(type="turn-cancelled", data={})
            session.reset_provider()

        _attach_turn_meta(session, provider.model, turn_usage, last_prompt_tokens, time.monotonic() - start)
        _persist(session)
    finally:
        unregister_abort(provider.abort)
