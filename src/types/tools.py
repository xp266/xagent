from typing import Any, Callable, Optional
from pydantic import BaseModel


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict
    execute: Callable[..., dict]
    label: str = ""
    to_model_output: Optional[Callable[[dict], str]] = None
    execution_mode: str = "sequential"

    class Config:
        arbitrary_types_allowed = True


class ToolResult(BaseModel):
    title: str
    output: str
    metadata: dict = {}
    attachments: list = []
    is_error: bool = False
