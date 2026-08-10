from src.types.messages import (
    SystemMessage, UserMessage, AssistantMessage, ToolMessage, ToolCall, Message,
)
from src.types.events import LLMResponse

SYNTHETIC_ATTACHMENT_PROMPT = "The tool returned the following image attachment(s). Please use them to continue."


def _sanitize_for_storage(api: dict) -> dict:
    if api.get("role") != "user":
        return api
    content = api.get("content")
    if not isinstance(content, list):
        return api
    out = dict(api)
    cleaned = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            filename = part.get("filename") or ""
            cleaned.append({"type": "text", "text": f"[image: {filename}]" if filename else "[image]"})
        else:
            cleaned.append(part)
    if all(isinstance(p, dict) and p.get("type") == "text" for p in cleaned):
        out["content"] = "\n".join(p.get("text", "") for p in cleaned)
    else:
        out["content"] = cleaned
    return out


def _messages_to_api(messages: list, *, include_meta: bool = False) -> list[dict]:
    result = []
    for m in messages:
        api = m if isinstance(m, dict) else m.to_api()
        if include_meta and isinstance(m, AssistantMessage) and m.meta:
            api["_meta"] = dict(m.meta)
        elif isinstance(api, dict) and "_meta" in api and not include_meta:
            api = {k: v for k, v in api.items() if k != "_meta"}
        if include_meta:
            api = _sanitize_for_storage(api)
        if api.get("role") == "assistant" and not api.get("content") and not api.get("tool_calls"):
            continue
        result.append(api)
    return result


class MessageManager:
    def __init__(self, system_prompt: str = "", session=None):
        self._session = session
        self._messages: list[Message | dict] = list(session.messages) if session else []
        self._pending_attachments: list = []

        if system_prompt:
            if not self._messages or not isinstance(self._messages[0], dict) or self._messages[0].get("role") != "system":
                self._messages.insert(0, SystemMessage(content=system_prompt))
            elif isinstance(self._messages[0], dict) and self._messages[0].get("content") != system_prompt:
                self._messages[0]["content"] = system_prompt

    def add_user(self, content: str) -> None:
        self._messages.append(UserMessage(content=content))

    def add_assistant(self, response: LLMResponse) -> None:
        if not response.content and not response.tool_calls:
            return
        tool_calls = []
        for tool_call in response.tool_calls:
            if isinstance(tool_call, dict):
                tool_calls.append(ToolCall.from_api(tool_call))
            elif hasattr(tool_call, "id"):
                tool_calls.append(ToolCall(id=tool_call.id, name=tool_call.name, arguments=tool_call.arguments))
            else:
                tool_calls.append(ToolCall(id=tool_call["id"], name=tool_call["name"], arguments=tool_call.get("arguments", "")))

        self._messages.append(AssistantMessage(
            content=response.content,
            reasoning=response.reasoning,
            signature=response.signature,
            tool_calls=tool_calls,
            finish_reason=response.finish_reason,
        ))

    def add_tool(self, tool_call_id: str, content: str, attachments: list | None = None, is_error: bool = False) -> None:
        self._messages.append(ToolMessage(
            tool_call_id=tool_call_id,
            content=content,
            attachments=attachments or [],
            is_error=is_error,
        ))
        if attachments:
            self._pending_attachments.extend(attachments)

    def finalize_tool_results(self) -> None:
        if not self._pending_attachments:
            return
        self._messages.append(UserMessage(
            content=SYNTHETIC_ATTACHMENT_PROMPT,
            attachments=self._pending_attachments,
        ))
        self._pending_attachments = []

    def get_messages(self) -> list:
        return self._messages

    def _context_messages(self) -> list:
        msgs = self._messages
        last = None
        tail_start = None
        for i, m in enumerate(msgs):
            if isinstance(m, dict) and (m.get("_meta") or {}).get("compacted"):
                last = i
                ts = (m.get("_meta") or {}).get("tail_start")
                if isinstance(ts, int) and 0 <= ts < i:
                    tail_start = ts
        if last is None:
            return list(msgs)
        out = [
            m for m in msgs
            if isinstance(m, SystemMessage) or (isinstance(m, dict) and m.get("role") == "system")
        ]
        out.append(msgs[last])
        if tail_start is not None:
            out.extend(msgs[tail_start:last])
        out.extend(msgs[last + 1:])
        return out

    def get_api_messages(self) -> list[dict]:
        return _messages_to_api(self._context_messages())

    def save(self) -> None:
        if self._session:
            self._session.messages = _messages_to_api(self._messages, include_meta=True)
