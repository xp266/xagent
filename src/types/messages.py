from pydantic import BaseModel


class ImageContent(BaseModel):
    url: str
    mime: str


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }

    @classmethod
    def from_api(cls, d: dict) -> "ToolCall":
        fn = d.get("function", {})
        return cls(
            id=d.get("id", ""),
            name=fn.get("name", ""),
            arguments=fn.get("arguments", ""),
        )


class SystemMessage(BaseModel):
    content: str

    def to_api(self) -> dict:
        return {"role": "system", "content": self.content}

    @classmethod
    def from_api(cls, d: dict) -> "SystemMessage":
        return cls(content=d.get("content", ""))


class UserMessage(BaseModel):
    content: str

    def to_api(self) -> dict:
        return {"role": "user", "content": self.content}

    @classmethod
    def from_api(cls, d: dict) -> "UserMessage":
        return cls(content=d.get("content", ""))


class AssistantMessage(BaseModel):
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = []
    finish_reason: str = ""

    def to_api(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning:
            msg["reasoning_content"] = self.reasoning
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
        return msg

    @classmethod
    def from_api(cls, d: dict, reasoning_fields: list[str] | None = None) -> "AssistantMessage":
        msg = cls(content=d.get("content", ""))
        fields = reasoning_fields or ["reasoning_content", "reasoning"]
        for f in fields:
            if d.get(f):
                msg.reasoning = d[f]
                break
        for tc in d.get("tool_calls", []):
            msg.tool_calls.append(ToolCall.from_api(tc))
        return msg


class ToolMessage(BaseModel):
    tool_call_id: str
    content: str
    is_error: bool = False
    attachments: list = []

    def to_api(self) -> dict:
        return {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.content}

    @classmethod
    def from_api(cls, d: dict) -> "ToolMessage":
        return cls(
            tool_call_id=d.get("tool_call_id", ""),
            content=d.get("content", ""),
        )
