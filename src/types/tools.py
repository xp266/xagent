from typing import Callable, Optional, TypedDict
from pydantic import BaseModel, ConfigDict


class Tool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    parameters: dict
    execute: Callable[..., dict]
    to_model_output: Optional[Callable[[dict], str]] = None


class ToolOutput(TypedDict, total=False):
    title: str
    output: str
    metadata: dict
    attachments: list
