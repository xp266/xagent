import json
import re
from collections.abc import Iterator
from openai import OpenAI
from src.types.events import LLMResponse, StreamEvent
from src.utils.media import mime_to_modality

_SURROGATE_RE = re.compile(r'[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]')


def _sanitize(text: str) -> str:
    if isinstance(text, str):
        return _SURROGATE_RE.sub('\uFFFD', text)
    return text


def _sanitize_value(v):
    if isinstance(v, str):
        return _sanitize(v)
    if isinstance(v, dict):
        return {kk: _sanitize_value(vv) for kk, vv in v.items()}
    if isinstance(v, list):
        return [_sanitize_value(item) for item in v]
    return v


def _clean_messages(messages: list) -> list:
    return [_sanitize_value(msg) for msg in messages]


def _filter_unsupported_media(messages: list, capabilities: dict) -> list:
    can_image = capabilities.get("image", False)
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
                modality = mime_to_modality(mime)
                if modality and not capabilities.get(modality, False):
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


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        reasoning_fields: list = None,
        timeout: int = 60,
        max_retries: int = 3,
        capabilities: dict = None,
    ):
        self.model = model
        self.reasoning_fields = reasoning_fields or ["reasoning_content", "reasoning"]
        self.capabilities = capabilities or {"image": False}
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def chat(self, messages: list, tools: list = None) -> LLMResponse:
        response = LLMResponse()
        for event in self.chat_stream(messages, tools):
            if event.type == "reasoning-delta":
                response.reasoning += event.data
            elif event.type == "text-delta":
                response.content += event.data
            elif event.type == "tool-call":
                response.tool_calls.append(event.data)
            elif event.type == "finish":
                response.finish_reason = event.data.get("finish_reason", "")
        return response

    def chat_stream(self, messages: list, tools: list = None) -> Iterator[StreamEvent]:
        cleaned = _clean_messages(messages)
        cleaned = _filter_unsupported_media(cleaned, self.capabilities)
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                stream=True,
                messages=cleaned,
                tools=tools or None,
                stream_options={"include_usage": True},
            )
        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": 0})
            return

        tool_calls = []
        tool_call_names = {}
        finish_reason = ""
        usage = {}
        step_started = False
        reasoning_active = False
        text_active = False

        for chunk in stream:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    usage = {
                        "prompt_tokens": getattr(u, "prompt_tokens", 0),
                        "completion_tokens": getattr(u, "completion_tokens", 0),
                        "total_tokens": getattr(u, "total_tokens", 0),
                    }
                continue

            if not step_started:
                yield StreamEvent(type="step-start")
                step_started = True

            delta = chunk.choices[0].delta

            tc = delta.tool_calls or []
            if tc:
                if reasoning_active:
                    yield StreamEvent(type="reasoning-end")
                    reasoning_active = False
                for call in tc:
                    idx = call.index
                    while len(tool_calls) <= idx:
                        tool_calls.append(None)
                        tool_call_names[idx] = ""

                    if call.id:
                        tool_call_names[idx] = call.function.name or ""
                        yield StreamEvent(
                            type="tool-input-start",
                            data={"id": call.id, "name": tool_call_names[idx]},
                        )

                    if tool_calls[idx] is None:
                        tool_calls[idx] = {
                            "id": call.id or "",
                            "type": call.type or "function",
                            "function": {
                                "name": call.function.name or "",
                                "arguments": call.function.arguments or "",
                            },
                        }
                    else:
                        existing = tool_calls[idx]
                        if call.id:
                            existing["id"] = call.id
                        if call.function.arguments:
                            existing["function"]["arguments"] += (
                                call.function.arguments
                            )

                    if call.function and call.function.arguments:
                        yield StreamEvent(
                            type="tool-input-delta",
                            data={"id": tool_calls[idx]["id"], "delta": call.function.arguments},
                        )

            rc = ""
            for field in self.reasoning_fields:
                rc = getattr(delta, field, None)
                if rc:
                    break
            if rc:
                rc = _sanitize(rc)
                if text_active:
                    yield StreamEvent(type="text-end")
                    text_active = False
                if not reasoning_active:
                    yield StreamEvent(type="reasoning-start")
                    reasoning_active = True
                yield StreamEvent(type="reasoning-delta", data=rc)

            ct = delta.content or ""
            if ct:
                ct = _sanitize(ct)
                if reasoning_active:
                    yield StreamEvent(type="reasoning-end")
                    reasoning_active = False
                if not text_active:
                    yield StreamEvent(type="text-start")
                    text_active = True
                yield StreamEvent(type="text-delta", data=ct)

            fr = chunk.choices[0].finish_reason or ""
            if fr:
                finish_reason = fr

        if reasoning_active:
            yield StreamEvent(type="reasoning-end")
        if text_active:
            yield StreamEvent(type="text-end")

        tool_calls = [tc for tc in tool_calls if tc is not None]
        for tc in tool_calls:
            tc_id = tc["id"]
            tc_name = tc["function"]["name"]
            yield StreamEvent(type="tool-input-end", data={"id": tc_id})
            try:
                parsed = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                parsed = tc["function"]["arguments"]
            yield StreamEvent(type="tool-call", data={
                "id": tc_id,
                "name": tc_name,
                "input": parsed,
            })

        if step_started:
            yield StreamEvent(type="step-finish", data={"finish_reason": finish_reason, "usage": usage})
        yield StreamEvent(type="finish", data={"finish_reason": finish_reason, "usage": usage})
