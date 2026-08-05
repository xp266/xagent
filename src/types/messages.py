from __future__ import annotations
from typing import Union
from pydantic import BaseModel, Field


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


class UserMessage(BaseModel):
    content: str

    def to_api(self) -> dict:
        return {"role": "user", "content": self.content}


class AssistantMessage(BaseModel):
    content: str = ""
    reasoning: str = ""
    signature: str = ""
    tool_calls: list[ToolCall] = []
    finish_reason: str = ""
    meta: dict = Field(default_factory=dict)

    def to_api(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning:
            msg["reasoning_content"] = self.reasoning
        if self.signature:
            msg["signature"] = self.signature
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
        return msg


class ToolMessage(BaseModel):
    tool_call_id: str
    content: str
    is_error: bool = False
    attachments: list = []

    def to_api(self) -> dict:
        msg = {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.content}
        if self.is_error:
            msg["is_error"] = True
        return msg


Message = Union[SystemMessage, UserMessage, AssistantMessage, ToolMessage]
