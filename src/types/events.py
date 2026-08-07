from typing import Any, TypedDict
from pydantic import BaseModel


class ToolCallData(TypedDict):
    id: str
    name: str
    input: dict


class ToolResultData(TypedDict, total=False):
    id: str
    name: str
    result: str
    attachments: list
    is_error: bool


class ToolErrorData(TypedDict):
    id: str
    name: str
    error: str


class ProviderErrorData(TypedDict, total=False):
    error: str
    code: int


class RetryScheduleData(TypedDict, total=False):
    error: str
    delay: float
    attempt: int


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
