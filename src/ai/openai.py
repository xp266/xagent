import json
import re
from collections.abc import Iterator

from httpx import Timeout
from openai import OpenAI

from src.types.events import StreamEvent, TokenUsage
from src.ai.base import Provider
from src.ai.capabilities import detect_capabilities

_SURROGATE_RE = re.compile(r'[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]')


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


def _mime_to_modality(mime: str) -> str | None:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf":
        return "pdf"
    return None


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
                modality = _mime_to_modality(mime)
                if modality and not getattr(capabilities, modality, False):
                    filtered.append({
                        "type": "text",
                        "text": f"ERROR: Cannot read {part.get('filename', modality)} (this model does not support {modality} input).",
                    })
                elif can_image:
                    data = part.get("data", "")
                    filtered.append({
                        "type": "image_url",
                        "image_url": {"url": data if data.startswith("data:") else f"data:{mime};base64,{data}"},
                    })
                else:
                    filtered.append({
                        "type": "text",
                        "text": f"ERROR: Cannot read {part.get('filename', modality)} (this model does not support {modality} input).",
                    })
            else:
                filtered.append(part)
        msg["content"] = filtered
    return messages


def _clean_openai_messages(messages: list[dict], capabilities) -> list[dict]:
    cleaned = [_replace_surrogates_in_value(m) for m in messages]
    return _filter_unsupported_media(cleaned, capabilities)


def _stream_openai_events(stream, capabilities) -> Iterator[StreamEvent]:
    tool_calls: list[dict | None] = []
    finish_reason = ""
    usage = TokenUsage()
    step_started = False
    reasoning_active = False
    text_active = False

    for chunk in stream:
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
    ):
        self.model: str = model
        self.base_url: str = base_url
        self.capabilities = detect_capabilities(model)

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def stream(self, messages: list[dict], tools: list | None = None) -> Iterator[StreamEvent]:
        cleaned = _clean_openai_messages(messages, self.capabilities)

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                stream=True,
                messages=cleaned,
                tools=tools or None,
            )
        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": 0})
            return

        yield from _stream_openai_events(stream, self.capabilities)
