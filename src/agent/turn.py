import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from src.agent.compact import compact_session_stream, estimate_context_usage, should_compact, USER_THRESHOLD, WORK_THRESHOLD
from src.agent.loop import agent_stream
from src.agent.session import Session, get_session_manager
from src.types.events import (
    LLMResponse, StreamEvent, TokenUsage,
    ToolCallData, ToolResultData, ToolErrorData, ProviderErrorData, RetryScheduleData,
)
from src.types.messages import AssistantMessage
from src.utils.providers import get_store

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
                session.msgs.add_tool(tc["id"], INTERRUPTED_TOOL_RESULT, is_error=True)


_PERSIST_INTERVAL = 0.5
_persist_pending: Session | None = None
_persist_lock_until = 0.0


def _write_session_files(data: dict) -> None:
    from src.agent.session import _write_session, get_session_manager

    _write_session(data)
    mgr = get_session_manager()
    entry = mgr._index.get(data["id"])
    if entry:
        entry["name"] = data["name"]
        entry["path"] = data["path"]
        entry["updated_at"] = data["updated_at"]
    else:
        mgr._index[data["id"]] = {
            "id": data["id"],
            "name": data["name"],
            "path": data["path"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }
    mgr._save_index()


async def _flush_persist(session: Session) -> None:
    session.updated_at = datetime.now().isoformat()
    data = session.to_dict()
    await asyncio.to_thread(_write_session_files, data)


async def _persist(session: Session, *, force: bool = False) -> None:
    session.sync_messages()
    global _persist_pending, _persist_lock_until
    _persist_pending = session
    now = time.monotonic()
    if not force and now < _persist_lock_until:
        return
    _persist_lock_until = now + _PERSIST_INTERVAL
    target, _persist_pending = _persist_pending, None
    if target is None:
        return
    await _flush_persist(target)


async def _flush_persist_pending() -> None:
    global _persist_pending
    target, _persist_pending = _persist_pending, None
    if target is not None:
        await _flush_persist(target)


async def _compact_if_needed(session: Session, threshold: float, usage_override: int = 0):
    usage = usage_override if usage_override > 0 else estimate_context_usage(session)
    try:
        limit = get_store().get_effective_context_limit(session.provider.model)
    except Exception:
        return
    if not should_compact(usage, limit, threshold):
        return
    try:
        async for ev in compact_session_stream(session):
            yield ev
    except asyncio.CancelledError:
        raise
    except Exception:
        yield StreamEvent(type="compact-error")


async def close_turn_stream(gen) -> None:
    try:
        await gen.athrow(asyncio.CancelledError())
    except (asyncio.CancelledError, GeneratorExit, StopAsyncIteration, RuntimeError):
        pass


async def run_session_turn(session: Session, user_input: str) -> AsyncIterator[StreamEvent]:
    async for ev in _compact_if_needed(session, USER_THRESHOLD):
        yield ev
    session.msgs.add_user(user_input)
    start = time.monotonic()

    cancelled = False
    retry_count = 0
    done = False

    provider = session.provider
    response = LLMResponse()
    tool_calls_pending: list[ToolCallData] = []
    tool_results: list[ToolResultData | ToolErrorData] = []
    turn_usage = TokenUsage()
    last_prompt_tokens = 0
    committed = False

    try:
        while True:
            response = LLMResponse()
            tool_calls_pending = []
            tool_results = []
            last_prompt_tokens = 0
            committed = False

            try:
                stream = agent_stream(
                    provider,
                    session.msgs.get_api_messages(),
                    session.registry.schemas() or None,
                    session.registry,
                )

                async for event in stream:
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
                                reasoning_tokens=turn_usage.reasoning_tokens + usage.get("reasoning_tokens", 0),
                            )
                            session.token_usage = TokenUsage(
                                prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                                completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                                total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                                reasoning_tokens=session.token_usage.reasoning_tokens + usage.get("reasoning_tokens", 0),
                            )
                    elif event.type == "provider-error":
                        raise _ProviderError(
                            event.data.get("error", "provider error"),
                            int(event.data.get("code") or 0),
                        )

                    yield event

            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as e:
                if retry_count < RETRY_LIMIT and _is_retryable(e):
                    retry_count += 1
                    if tool_calls_pending or tool_results:
                        _fill_tool_calls(response, tool_calls_pending)
                        _commit_response(session, response, tool_results, cancelled=False)
                        committed = True
                        for tr in tool_results:
                            session.msgs.add_tool(
                                tr["id"],
                                tr.get("result", tr.get("error", "")),
                                tr.get("attachments"),
                                is_error=bool(tr.get("is_error") or tr.get("error")),
                            )
                        session.msgs.finalize_tool_results()
                        await _persist(session)
                    delay = _retry_delay(retry_count)
                    retry_data: RetryScheduleData = {
                        "error": str(e),
                        "delay": delay,
                        "attempt": retry_count,
                    }
                    yield StreamEvent(type="retry-schedule", data=retry_data)
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    continue
                else:
                    err_data: ProviderErrorData = {
                        "error": str(e),
                        "code": getattr(e, "status_code", None) or 0,
                    }
                    yield StreamEvent(type="provider-error", data=err_data)
                    done = True
                    break

            if cancelled:
                break

            _fill_tool_calls(response, tool_calls_pending)
            _commit_response(session, response, tool_results, cancelled=False)
            committed = True
            retry_count = 0
            _attach_turn_meta(session, provider.model, turn_usage, last_prompt_tokens, time.monotonic() - start)
            for tr in tool_results:
                session.msgs.add_tool(
                    tr["id"],
                    tr.get("result", tr.get("error", "")),
                    tr.get("attachments"),
                    is_error=bool(tr.get("is_error") or tr.get("error")),
                )
            session.msgs.finalize_tool_results()
            await _persist(session)

            if response.finish_reason == "tool_calls":
                async for ev in _compact_if_needed(session, WORK_THRESHOLD, last_prompt_tokens):
                    yield ev

            if response.finish_reason != "tool_calls":
                done = True
                break
    finally:
        try:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                cancelled = True
        except Exception:
            pass

        if not done and not committed:
            _fill_tool_calls(response, tool_calls_pending)
            _commit_response(session, response, tool_results, cancelled=True)
            for tr in tool_results:
                session.msgs.add_tool(
                    tr["id"],
                    tr.get("result", tr.get("error", "")),
                    tr.get("attachments"),
                    is_error=bool(tr.get("is_error") or tr.get("error")),
                )
            session.msgs.finalize_tool_results()

        _attach_turn_meta(session, provider.model, turn_usage, last_prompt_tokens, time.monotonic() - start)
        await _flush_persist_pending()
        await _persist(session, force=True)

        if cancelled or not done:
            session.reset_provider()
