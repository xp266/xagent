from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
from httpx import Timeout

from src.ai.base import Provider
from src.utils.models import Capabilities, detect_capabilities, get_model_output_limit
from src.types.events import StreamEvent, TokenUsage
from src.agent.cancel import TurnCancelled, is_cancelled

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8192


_ANTHROPIC_STOP_REASONS = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


def _split_data_url(url: str) -> tuple[str, str] | None:
    if url.startswith("data:") and ";base64," in url:
        head, b64 = url[5:].split(";base64,", 1)
        return head, b64
    return None


def _user_content_to_anthropic(content, capabilities: Capabilities) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]

    blocks = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype in ("image_url", "media"):
            mime, data = part.get("mediaType", ""), part.get("data", "")
            if ptype == "image_url":
                parsed = _split_data_url(part.get("image_url", {}).get("url", ""))
                if parsed:
                    mime, data = parsed
            elif data.startswith("data:"):
                parsed = _split_data_url(data)
                if parsed:
                    mime, data = parsed
            if not data:
                continue
            if mime.startswith("image/"):
                if capabilities.image:
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
                else:
                    blocks.append({"type": "text", "text": "[Image omitted: this model does not support image input]"})
            elif mime == "application/pdf" and capabilities.pdf:
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": mime, "data": data}})
            else:
                filename = part.get("filename", mime)
                blocks.append({"type": "text", "text": f"[Unsupported media: {filename}]"})
        else:
            blocks.append({"type": "text", "text": json.dumps(part, ensure_ascii=False)})
    return blocks


def _assistant_content_to_anthropic(msg: dict) -> list[dict]:
    blocks = []
    reasoning = msg.get("reasoning_content") or ""
    if reasoning:
        thinking_block: dict = {"type": "thinking", "thinking": reasoning}
        if msg.get("signature"):
            thinking_block["signature"] = msg["signature"]
        blocks.append(thinking_block)
    content = msg.get("content")
    if content:
        blocks.append({"type": "text", "text": content})
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        try:
            input_json = json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {}
        except (json.JSONDecodeError, TypeError):
            input_json = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "input": input_json,
        })
    return blocks


def _merge_anthropic_messages(messages: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = merged[-1]["content"] + msg["content"]
        else:
            merged.append({"role": msg["role"], "content": list(msg["content"])})
    return merged


def _messages_to_anthropic(messages: list[dict], capabilities: Capabilities) -> tuple[str, list[dict]]:
    system = ""
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            text = msg.get("content", "") or ""
            system = f"{system}\n{text}".strip() if system else text
        elif role == "user":
            result.append({"role": "user", "content": _user_content_to_anthropic(msg.get("content"), capabilities)})
        elif role == "assistant":
            blocks = _assistant_content_to_anthropic(msg)
            if blocks:
                result.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            result_block: dict = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", "") or "",
            }
            if msg.get("is_error"):
                result_block["is_error"] = True
            result.append({
                "role": "user",
                "content": [result_block],
            })
    return system, _merge_anthropic_messages(result)


def _close_schema(schema) -> dict:
    if not isinstance(schema, dict):
        return schema
    closed = {k: _close_schema(v) for k, v in schema.items() if k not in ("$schema", "$defs", "$ref")}
    if closed.get("type") == "object" or "properties" in closed:
        closed.setdefault("additionalProperties", False)
    return closed


def _tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    result = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function", t) if t.get("type") == "function" else t
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", "") or "",
            "input_schema": _close_schema(fn.get("parameters")) or {"type": "object", "properties": {}},
        })
    return result or None


def _extract_api_error(body: str) -> str:
    try:
        data = json.loads(body)
        return data.get("error", {}).get("message", body[:300])
    except Exception:
        return body[:300]


def _iter_sse_events(resp: httpx.Response) -> Iterator[tuple[str, str]]:
    event_name = ""
    data_lines: list[str] = []
    for line in resp.iter_lines():
        if is_cancelled():
            raise TurnCancelled
        if line == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = ""
            data_lines = []
        elif line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _stream_anthropic_events(resp: httpx.Response) -> Iterator[StreamEvent]:
    step_started = False
    reasoning_active = False
    text_active = False
    current_type: str | None = None
    thinking_signature = ""
    tool_id = ""
    tool_name = ""
    tool_input_raw = ""
    stop_reason = ""
    input_tokens = 0
    output_tokens = 0

    for name, data in _iter_sse_events(resp):
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue

        if name == "error":
            err = payload.get("error", {})
            yield StreamEvent(type="provider-error", data={"error": err.get("message", str(payload)), "code": 0})
            return

        if name == "message_start":
            step_started = True
            input_tokens = payload.get("message", {}).get("usage", {}).get("input_tokens", 0)
            yield StreamEvent(type="step-start")
            continue

        if name == "content_block_start":
            block = payload.get("content_block", {})
            current_type = block.get("type")
            if current_type == "tool_use":
                tool_id = block.get("id", "")
                tool_name = block.get("name", "")
                tool_input_raw = ""
                yield StreamEvent(type="tool-input-start", data={"id": tool_id, "name": tool_name})
            elif current_type == "thinking":
                thinking_signature = block.get("signature", "")
            continue

        if name == "content_block_delta":
            delta = payload.get("delta", {})
            dtype = delta.get("type")
            if dtype == "text_delta":
                if reasoning_active:
                    yield StreamEvent(type="reasoning-end")
                    reasoning_active = False
                if not text_active:
                    yield StreamEvent(type="text-start")
                    text_active = True
                yield StreamEvent(type="text-delta", data=delta.get("text", ""))
            elif dtype == "thinking_delta":
                if text_active:
                    yield StreamEvent(type="text-end")
                    text_active = False
                if not reasoning_active:
                    yield StreamEvent(type="reasoning-start")
                    reasoning_active = True
                yield StreamEvent(type="reasoning-delta", data=delta.get("thinking", ""))
            elif dtype == "signature_delta":
                thinking_signature = delta.get("signature", "")
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                tool_input_raw += partial
                yield StreamEvent(type="tool-input-delta", data={"id": tool_id, "delta": partial})
            continue

        if name == "content_block_stop":
            if current_type == "tool_use":
                yield StreamEvent(type="tool-input-end", data={"id": tool_id})
                try:
                    parsed = json.loads(tool_input_raw) if tool_input_raw.strip() else {}
                except json.JSONDecodeError:
                    parsed = tool_input_raw
                yield StreamEvent(type="tool-call", data={"id": tool_id, "name": tool_name, "input": parsed})
            elif current_type == "thinking":
                if reasoning_active:
                    yield StreamEvent(type="reasoning-end")
                    reasoning_active = False
                if thinking_signature:
                    yield StreamEvent(type="signature", data=thinking_signature)
                    thinking_signature = ""
            elif current_type == "text":
                if text_active:
                    yield StreamEvent(type="text-end")
                    text_active = False
            current_type = None
            continue

        if name == "message_delta":
            stop_reason = _ANTHROPIC_STOP_REASONS.get(
                payload.get("delta", {}).get("stop_reason", ""),
                payload.get("delta", {}).get("stop_reason", stop_reason),
            )
            output_tokens = payload.get("usage", {}).get("output_tokens", 0)
            continue

        if name == "message_stop":
            usage = TokenUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
            usage_data = usage.model_dump()
            if step_started:
                yield StreamEvent(type="step-finish", data={"finish_reason": stop_reason, "usage": usage_data})
            yield StreamEvent(type="finish", data={"finish_reason": stop_reason, "usage": usage_data})
            return


class AnthropicProvider(Provider):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: Timeout = Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0),
    ):
        self.model: str = model
        self.base_url: str = base_url.rstrip("/")
        self.api_key: str = api_key
        self.timeout: Timeout = timeout
        self.capabilities: Capabilities = detect_capabilities(model)
        self.max_tokens: int = get_model_output_limit(model) or _DEFAULT_MAX_TOKENS
        self._client: httpx.Client = httpx.Client(timeout=self.timeout)

    def stream(self, messages: list[dict], tools: list | None = None) -> Iterator[StreamEvent]:
        system, anthropic_messages = _messages_to_anthropic(messages, self.capabilities)
        anthropic_tools = _tools_to_anthropic(tools)

        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            with self._client.stream("POST", f"{self.base_url}/messages", json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    yield StreamEvent(type="provider-error", data={"error": _extract_api_error(body), "code": resp.status_code})
                    return
                yield from _stream_anthropic_events(resp)
        except TurnCancelled:
            raise
        except Exception as e:
            if is_cancelled():
                raise TurnCancelled
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": getattr(e, "status_code", None) or 0})

    def abort(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
