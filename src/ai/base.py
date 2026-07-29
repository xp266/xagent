from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from src.types.events import StreamEvent, LLMResponse


class Provider(ABC):
    model: str
    reasoning_fields: list[str]
    capabilities: dict[str, bool]

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        ...
