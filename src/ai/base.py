from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from src.types.events import StreamEvent, LLMResponse
from src.types.config import Capabilities


class Provider(ABC):
    model: str
    capabilities: Capabilities

    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        ...

    def abort(self) -> None:
        pass

    def respond(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        response = LLMResponse()
        for event in self.stream(messages, tools):
            if event.type == "reasoning-delta":
                response.reasoning += event.data
            elif event.type == "text-delta":
                response.content += event.data
            elif event.type == "tool-call":
                response.tool_calls.append(event.data)
            elif event.type == "finish":
                response.finish_reason = event.data.get("finish_reason", "")
        return response
