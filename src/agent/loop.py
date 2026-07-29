from __future__ import annotations
from collections.abc import Iterator
from typing import TYPE_CHECKING
from src.types.events import StreamEvent
from src.ai.base import Provider

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


def agent_stream(
    provider: Provider,
    messages: list,
    tools: list,
    registry: ToolRegistry,
) -> Iterator[StreamEvent]:
    for event in provider.chat_stream(messages, tools):
        yield event
        if event.type == "tool-call":
            tc = event.data
            try:
                result_data = registry.execute(
                    tc["name"],
                    tc["input"] if isinstance(tc["input"], dict) else {},
                )
                result = result_data.get("output", "")
                attachments = result_data.get("attachments", [])
                yield StreamEvent(type="tool-result", data={
                    "id": tc["id"],
                    "name": tc["name"],
                    "result": result,
                    "attachments": attachments,
                })
            except Exception as e:
                yield StreamEvent(type="tool-error", data={
                    "id": tc["id"],
                    "name": tc["name"],
                    "error": str(e),
                })
