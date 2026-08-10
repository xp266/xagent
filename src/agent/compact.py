from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.agent.session import Session, get_session_manager
from src.utils.prompts import load as load_prompt
from src.utils.providers import get_store, is_anthropic_provider

MIN_USAGE_FLOOR = 10_000
USER_THRESHOLD = 0.80
WORK_THRESHOLD = 0.90

TAIL_MIN = 2000
TAIL_MAX = 8000

MAX_SUMMARY_CHARS = 16_384

_TRUNC_REASONING = 1000
_TRUNC_TOOL_ARGS = 1500
_TRUNC_TOOL_RESULT = 2000

_WRAP_OPEN = "<conversation_summary>"
_WRAP_CLOSE = "</conversation_summary>"


def _load_prompts() -> dict:
    text = load_prompt("compact")
    sections = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("=== ") and line.endswith(" ==="):
            current = line[4:-4]
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {k.lower(): "\n".join(v).strip() for k, v in sections.items()}


def _chars_estimate(messages: list) -> int:
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += len(text)
        total += len(m.get("reasoning_content") or "")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            total += len(fn.get("arguments") or "")
    return total // 4


def _msg_cost(msg: dict) -> int:
    return _chars_estimate([msg])


def estimate_context_usage(session: Session) -> int:
    msgs = session.messages
    last = None
    tail_start = None
    for i, m in enumerate(msgs):
        meta = m.get("_meta") or {}
        if meta.get("compacted"):
            last = i
            ts = meta.get("tail_start")
            if isinstance(ts, int) and 0 <= ts < i:
                tail_start = ts
    start = tail_start if tail_start is not None else (last + 1 if last is not None else 0)
    for msg in reversed(msgs[start:]):
        if msg.get("role") != "assistant":
            continue
        pt = (msg.get("_meta") or {}).get("prompt_tokens") or 0
        if pt > 0:
            return pt
        break
    if last is not None:
        pt = (msgs[last].get("_meta") or {}).get("prompt_tokens") or 0
        if pt > 0:
            return pt
    return _chars_estimate(msgs[start:])


def should_compact(usage: int, limit: int, threshold: float) -> bool:
    if usage < MIN_USAGE_FLOOR:
        return False
    return limit > 0 and usage >= limit * threshold


def _last_compacted_index(messages: list) -> int:
    last = None
    for i, m in enumerate(messages):
        if (m.get("_meta") or {}).get("compacted"):
            last = i
    return last if last is not None else -1


def split_tail(messages: list, budget: int) -> tuple[list, list]:
    start_idx = _last_compacted_index(messages) + 1
    boundary = len(messages)
    cost = 0
    for i in range(len(messages) - 1, start_idx - 1, -1):
        w = _msg_cost(messages[i])
        if boundary != len(messages) and cost + w > budget:
            break
        boundary = i
        cost += w
    while boundary > start_idx and messages[boundary].get("role") != "user":
        boundary -= 1
    return messages[start_idx:boundary], messages[boundary:]


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " [truncated]"


def _fmt_user(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return f"[User]: {content}"
    parts = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            parts.append(str(part))
            continue
        ptype = part.get("type")
        if ptype == "text":
            parts.append(part.get("text", ""))
        elif ptype in ("image_url", "media", "file"):
            filename = part.get("filename") or part.get("mime") or "media"
            parts.append(f"[Image: {filename}]")
        else:
            parts.append(str(part))
    return "[User]: " + "\n".join(parts) if parts else "[User]: "


def serialize_messages(messages: list) -> str:
    parts = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            parts.append(_fmt_user(m))
        elif role == "assistant":
            reasoning = m.get("reasoning_content") or ""
            if reasoning:
                parts.append(f"[Assistant reasoning]: {_truncate(reasoning, _TRUNC_REASONING)}")
            content = m.get("content") or ""
            if content:
                parts.append(f"[Assistant]: {content}")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                args = fn.get("arguments", "")
                parts.append(f"[Assistant tool call]: {fn.get('name', '')}({_truncate(args, _TRUNC_TOOL_ARGS)})")
        elif role == "tool":
            prefix = "[Tool error]" if m.get("is_error") else "[Tool result]"
            parts.append(f"{prefix}: {_truncate(m.get('content') or '', _TRUNC_TOOL_RESULT)}")
    return "\n\n".join(parts)


def _extract_summary(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS]
    start = text.find(_WRAP_OPEN)
    end = text.find(_WRAP_CLOSE)
    if start != -1 and end > start:
        return text[start + len(_WRAP_OPEN):end].strip()
    if start != -1:
        return text[start + len(_WRAP_OPEN):].strip()
    return "" if len(text) > MAX_SUMMARY_CHARS else text


def _build_summarizer(session: Session):
    store = get_store()
    model = (store.compact_model or "").strip()
    if not model:
        return session.provider
    active = store.get_active()
    resolved = store.resolve()
    model_meta = active.model_meta if active is not None else None
    if is_anthropic_provider(active):
        from src.ai.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=model,
            base_url=resolved["base_url"],
            api_key=resolved["api_key"],
            model_meta=model_meta,
            reasoning_effort="",
        )
    from src.ai.openai import OpenAIProvider

    return OpenAIProvider(
        model=model,
        base_url=resolved["base_url"],
        api_key=resolved["api_key"],
        model_meta=model_meta,
        reasoning_effort="",
    )


def _summary_payload(prompts: dict, previous: dict | None, head: list, focus: str) -> str:
    history = serialize_messages(head)
    if previous is not None:
        body = (
            f"{prompts['update']}\n<previous-summary>\n"
            f"{previous.get('content', '')}\n</previous-summary>\n\n"
            f"{prompts['template']}\n\nConversation history:\n{history}"
        )
    else:
        body = f"{prompts['new']}\n{prompts['template']}\n\nConversation history:\n{history}"
    if focus:
        body += f"\n\n{prompts['focus']} {focus}"
    return body


async def compact_session_stream(session: Session, focus: str = "") -> AsyncIterator[StreamEvent]:
    from src.types.events import StreamEvent, TokenUsage

    messages = session.messages
    resolved = get_store().resolve()
    if not resolved.get("base_url") or not resolved.get("model"):
        return
    try:
        limit = get_store().get_effective_context_limit(session.provider.model)
    except Exception:
        return
    budget = min(TAIL_MAX, max(TAIL_MIN, round(limit * 0.25)))
    head, tail = split_tail(messages, budget)
    if not head or not any(m.get("role") != "system" for m in head):
        return
    yield StreamEvent(type="compacting")

    previous_idx = _last_compacted_index(messages)
    previous = messages[previous_idx] if previous_idx >= 0 else None
    prompts = _load_prompts()
    provider = _build_summarizer(session)
    payload = _summary_payload(prompts, previous, head, focus)
    summary_messages = [
        {"role": "system", "content": prompts["system"]},
        {"role": "user", "content": payload},
    ]

    buf: list[str] = []
    try:
        async for event in provider.astream(summary_messages, None):
            if event.type == "provider-error":
                yield StreamEvent(type="compact-error")
                return
            if event.type == "step-finish":
                usage = event.data.get("usage", {})
                if usage:
                    session.token_usage = TokenUsage(
                        prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                        completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                        total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                        cached_tokens=session.token_usage.cached_tokens + usage.get("cached_tokens", 0),
                        cache_write_tokens=session.token_usage.cache_write_tokens + usage.get("cache_write_tokens", 0),
                        reasoning_tokens=session.token_usage.reasoning_tokens + usage.get("reasoning_tokens", 0),
                    )
                continue
            if event.type == "text-delta" and event.data:
                buf.append(event.data)
                yield StreamEvent(type="compact-delta", data=event.data)
    except asyncio.CancelledError:
        raise
    except Exception:
        yield StreamEvent(type="compact-error")
        return

    summary = _extract_summary("".join(buf))
    if not summary:
        yield StreamEvent(type="compact-error")
        return

    summary_msg = {
        "role": "user",
        "content": f"{_WRAP_OPEN}\n{summary}\n{_WRAP_CLOSE}",
        "_meta": {"compacted": True},
    }
    tail_msgs = messages[len(messages) - len(tail):]
    for m in tail_msgs:
        meta = m.get("_meta")
        if isinstance(meta, dict):
            meta.pop("prompt_tokens", None)
    tail_start = len(messages) - len(tail)
    summary_msg["_meta"]["tail_start"] = tail_start
    summary_msg["_meta"]["prompt_tokens"] = _chars_estimate([summary_msg] + tail_msgs)

    session.messages = messages + [summary_msg]
    session._msgs = None
    session.sync_messages()
    get_session_manager().save(session)
    yield StreamEvent(type="compacted", data={"removed": len(head)})


async def compact_session(session: Session, focus: str = "") -> dict | None:
    result = None
    async for event in compact_session_stream(session, focus=focus):
        if event.type == "compacted":
            result = event.data
    return result
