from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.types.events import StreamEvent, LLMResponse
from src.utils.models import Capabilities


class Provider(ABC):
    model: str
    capabilities: Capabilities

    @abstractmethod
    def astream(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[StreamEvent]:
        ...

    async def arespond(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        response = LLMResponse()
        async for event in self.astream(messages, tools):
            if event.type == "reasoning-delta":
                response.reasoning += event.data
            elif event.type == "text-delta":
                response.content += event.data
            elif event.type == "tool-call":
                response.tool_calls.append(event.data)
            elif event.type == "finish":
                response.finish_reason = event.data.get("finish_reason", "")
        return response
