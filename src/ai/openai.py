import json
import re
from collections.abc import AsyncIterator

from httpx import Timeout
from openai import AsyncOpenAI

from src.types.events import StreamEvent, TokenUsage
from src.ai.base import Provider
from src.utils.models import detect_capabilities, get_model_output_limit, reasoning_effort_options

_SURROGATE_RE = re.compile(r'[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]')
_REASONING_MODEL = re.compile(r"(?i)^(gpt-5|o[1-9])")


def _is_reasoning_model(model: str) -> bool:
    short = model.rsplit("/", 1)[-1]
    return bool(_REASONING_MODEL.match(short))


def _replace_surrogates(text: str) -> str:
    if isinstance(text, str):
        return _SURROGATE_RE.sub('\uFFFD', text)
    return text


def _replace_surrogates_in_value(v):
    if isinstance(v, str):
        return _replace_surrogates(v)
    if isinstance(v, dict):
        return {kk: _replace_surrogates_in_value(vv) for kk, vv in v.items()}
    if isinstance(v, list):
        return [_replace_surrogates_in_value(item) for item in v]
    return v


def _filter_unsupported_media(messages: list, capabilities) -> list:
    can_image = capabilities.image
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        filtered = []
        for part in content:
            if part.get("type") == "image_url":
                if can_image:
                    filtered.append(part)
                else:
                    filename = part.get("image_url", {}).get("url", "")[:40]
                    filtered.append({
                        "type": "text",
                        "text": f"ERROR: Cannot read image (this model does not support image input). URL: {filename}...",
                    })
            elif part.get("type") == "media":
                mime = part.get("mediaType", "")
                if mime.startswith("image/") and can_image:
                    data = part.get("data", "")
                    filtered.append({
                        "type": "image_url",
                        "image_url": {"url": data if data.startswith("data:") else f"data:{mime};base64,{data}"},
                    })
                else:
                    filtered.append({
                        "type": "text",
                        "text": f"ERROR: Cannot read {part.get('filename', mime or 'media')} (this model does not support this media type).",
                    })
            else:
                filtered.append(part)
        msg["content"] = filtered
    return messages


def _clean_openai_messages(messages: list[dict], capabilities) -> list[dict]:
    cleaned = [_replace_surrogates_in_value(m) for m in messages]
    return _filter_unsupported_media(cleaned, capabilities)


async def _stream_openai_events(stream, capabilities) -> AsyncIterator[StreamEvent]:
    tool_calls: list[dict | None] = []
    finish_reason = ""
    usage = TokenUsage()
    step_started = False
    reasoning_active = False
    text_active = False

    async for chunk in stream:
        if hasattr(chunk, "usage") and chunk.usage:
            raw = chunk.usage
            usage = TokenUsage(
                prompt_tokens=getattr(raw, "prompt_tokens", 0),
                completion_tokens=getattr(raw, "completion_tokens", 0),
                total_tokens=getattr(raw, "total_tokens", 0),
            )

        if not chunk.choices:
            continue

        if not step_started:
            yield StreamEvent(type="step-start")
            step_started = True

        choice = chunk.choices[0]
        delta = choice.delta
        finish = choice.finish_reason or ""

        tool_deltas = delta.tool_calls or []
        if tool_deltas:
            if reasoning_active:
                yield StreamEvent(type="reasoning-end")
                reasoning_active = False
            for tool in tool_deltas:
                idx = tool.index
                while len(tool_calls) <= idx:
                    tool_calls.append(None)

                if tool.id:
                    yield StreamEvent(
                        type="tool-input-start",
                        data={"id": tool.id, "name": tool.function.name or ""},
                    )

                if tool_calls[idx] is None:
                    tool_calls[idx] = {
                        "id": tool.id or "",
                        "type": "function",
                        "function": {
                            "name": tool.function.name or "",
                            "arguments": tool.function.arguments or "",
                        },
                    }
                else:
                    existing = tool_calls[idx]
                    if tool.id:
                        existing["id"] = tool.id
                    if tool.function.arguments:
                        existing["function"]["arguments"] += tool.function.arguments

                if tool.function and tool.function.arguments:
                    yield StreamEvent(
                        type="tool-input-delta",
                        data={"id": tool_calls[idx]["id"], "delta": tool.function.arguments},
                    )

        reasoning = getattr(delta, capabilities.reasoning_field, None)
        if reasoning:
            reasoning = _replace_surrogates(reasoning)
            if text_active:
                yield StreamEvent(type="text-end")
                text_active = False
            if not reasoning_active:
                yield StreamEvent(type="reasoning-start")
                reasoning_active = True
            yield StreamEvent(type="reasoning-delta", data=reasoning)

        content = delta.content or ""
        if content:
            content = _replace_surrogates(content)
            if reasoning_active:
                yield StreamEvent(type="reasoning-end")
                reasoning_active = False
            if not text_active:
                yield StreamEvent(type="text-start")
                text_active = True
            yield StreamEvent(type="text-delta", data=content)

        if finish:
            finish_reason = finish

    if reasoning_active:
        yield StreamEvent(type="reasoning-end")
    if text_active:
        yield StreamEvent(type="text-end")

    for pending in tool_calls:
        if pending is None:
            continue
        tc_id = pending["id"]
        tc_name = pending["function"]["name"]
        yield StreamEvent(type="tool-input-end", data={"id": tc_id})
        try:
            parsed = json.loads(pending["function"]["arguments"])
        except json.JSONDecodeError:
            parsed = pending["function"]["arguments"]
        yield StreamEvent(type="tool-call", data={
            "id": tc_id,
            "name": tc_name,
            "input": parsed,
        })

    if step_started:
        yield StreamEvent(type="step-finish", data={"finish_reason": finish_reason, "usage": usage.model_dump()})
    yield StreamEvent(type="finish", data={"finish_reason": finish_reason, "usage": usage.model_dump()})


class OpenAIProvider(Provider):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: Timeout = Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0),
        max_retries: int = 3,
        model_meta: dict | None = None,
        reasoning_effort: str = "",
    ):
        self.model: str = model
        self.base_url: str = base_url
        self.model_meta: dict | None = model_meta
        self.reasoning_effort: str = (reasoning_effort or "").strip()
        self.capabilities = detect_capabilities(model, model_meta)
        self.max_tokens = get_model_output_limit(model, model_meta)

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def astream(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[StreamEvent]:
        cleaned = _clean_openai_messages(messages, self.capabilities)

        kwargs: dict = {
            "model": self.model,
            "stream": True,
            "messages": cleaned,
            "tools": tools or None,
            "stream_options": {"include_usage": True},
        }
        if self.max_tokens:
            if _is_reasoning_model(self.model):
                kwargs["max_completion_tokens"] = self.max_tokens
            else:
                kwargs["max_tokens"] = self.max_tokens

        effort = self.reasoning_effort
        if effort in ("none", "off"):
            effort = ""
        elif effort == "max":
            effort = "high"
        if effort and effort in reasoning_effort_options(self.model, self.model_meta):
            kwargs["reasoning_effort"] = effort

        try:
            stream = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": getattr(e, "status_code", None) or 0})
            return

        async for event in _stream_openai_events(stream, self.capabilities):
            yield event
