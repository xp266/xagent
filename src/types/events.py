from typing import Any
from pydantic import BaseModel


class StreamEvent(BaseModel):
    type: str
    data: Any = None


class LLMResponse(BaseModel):
    content: str = ""
    reasoning: str = ""
    tool_calls: list = []
    finish_reason: str = ""
