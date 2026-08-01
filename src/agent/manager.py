from src.types.messages import (
    SystemMessage, UserMessage, AssistantMessage, ToolMessage, ToolCall, Message,
)
from src.types.events import LLMResponse

SYNTHETIC_ATTACHMENT_PROMPT = "The tool returned the following image attachment(s). Please use them to continue."


def _messages_to_api(messages: list) -> list[dict]:
    result = []
    for m in messages:
        api = m if isinstance(m, dict) else m.to_api()
        if api.get("role") == "assistant" and not api.get("content") and not api.get("tool_calls"):
            continue
        result.append(api)
    return result


class MessageManager:
    def __init__(self, system_prompt: str = "", session=None):
        self._session = session
        self._messages: list[Message | dict] = list(session.messages) if session else []

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
            self._messages.append(UserMessage(content=SYNTHETIC_ATTACHMENT_PROMPT))

    def get_messages(self) -> list:
        return self._messages

    def get_api_messages(self) -> list[dict]:
        return _messages_to_api(self._messages)

    def save(self) -> None:
        if self._session:
            self._session.messages = _messages_to_api(self._messages)
