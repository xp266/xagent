from typing import Any
from pydantic import BaseModel


class StreamEvent(BaseModel):
    type: str
    data: Any = None


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str = ""
    reasoning: str = ""
    signature: str = ""
    tool_calls: list = []
    finish_reason: str = ""
    usage: TokenUsage = TokenUsage()
