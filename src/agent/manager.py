from src.types.messages import (
    SystemMessage, UserMessage, AssistantMessage, ToolMessage, ToolCall,
)
from src.types.events import LLMResponse
from src.agent.session import SessionStore


SYNTHETIC_ATTACHMENT_PROMPT = "The tool returned the following image attachment(s). Please use them to continue."


_ROLE_MAP = {
    "system": SystemMessage,
    "user": UserMessage,
    "assistant": AssistantMessage,
    "tool": ToolMessage,
}


def _dict_to_message(d: dict, reasoning_fields: list[str] | None = None) -> SystemMessage | UserMessage | AssistantMessage | ToolMessage:
    role = d.get("role", "")
    cls = _ROLE_MAP.get(role)
    if cls is AssistantMessage:
        return AssistantMessage.from_api(d, reasoning_fields)
    if cls:
        return cls.from_api(d)
    return UserMessage(content=str(d))


def _messages_to_api(messages: list) -> list[dict]:
    result = []
    for m in messages:
        if isinstance(m, dict):
            result.append(m)
        else:
            result.append(m.to_api())
    return result


class MessageManager:

    def __init__(self, system_prompt: str = "", session: SessionStore = None):
        self._session = session or SessionStore()
        self._reasoning_fields: list[str] = []
        self._messages: list = list(self._session.messages)

        if system_prompt:
            if not self._messages or not hasattr(self._messages[0], "content") or self._messages[0].get("role") != "system":
                self._messages.insert(0, SystemMessage(content=system_prompt))
            elif isinstance(self._messages[0], dict) and self._messages[0]["content"] != system_prompt:
                self._messages[0]["content"] = system_prompt

    def set_reasoning_fields(self, fields: list[str]):
        self._reasoning_fields = fields

    def add_user(self, content: str):
        self._messages.append(UserMessage(content=content))

    def add_assistant(self, response: LLMResponse, reasoning_field: str = ""):
        if not isinstance(response, LLMResponse):
            text = response.get("content", "") if isinstance(response, dict) else str(response)
            tool_calls_raw = response.get("tool_calls", []) if isinstance(response, dict) else []
            reasoning = response.get("reasoning", "") if isinstance(response, dict) else ""
            self._messages.append(AssistantMessage(
                content=text,
                reasoning=reasoning,
                tool_calls=[ToolCall.from_api(tc) for tc in tool_calls_raw] if tool_calls_raw else [],
            ))
            return

        fields = [reasoning_field] + self._reasoning_fields if reasoning_field else self._reasoning_fields
        tc_list = []
        for tc in response.tool_calls:
            if isinstance(tc, dict):
                tc_list.append(ToolCall.from_api(tc))
            elif hasattr(tc, "id"):
                tc_list.append(ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments))
            else:
                tc_list.append(ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", "")))

        self._messages.append(AssistantMessage(
            content=response.content,
            reasoning=response.reasoning,
            tool_calls=tc_list,
            finish_reason=response.finish_reason,
        ))

    def add_tool(self, tool_call_id: str, content: str, attachments: list = None):
        self._messages.append(ToolMessage(
            tool_call_id=tool_call_id,
            content=content,
            attachments=attachments or [],
        ))
        if attachments:
            self._add_user_multipart(SYNTHETIC_ATTACHMENT_PROMPT, attachments)

    def _add_user_multipart(self, text: str, attachments: list):
        self._messages.append(UserMessage(content=text))

    def get_messages(self) -> list:
        return self._messages

    def get_api_messages(self) -> list[dict]:
        return _messages_to_api(self._messages)

    def save(self):
        self._session.messages = _messages_to_api(self._messages)
        self._session.save()

    @property
    def session(self) -> SessionStore:
        return self._session
