from __future__ import annotations
from collections.abc import Iterator
from typing import TYPE_CHECKING
from src.types.events import StreamEvent
from src.ai.base import Provider

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


def agent_stream(
    provider: Provider,
    messages: list[dict],
    tools: list | None,
    registry: ToolRegistry,
) -> Iterator[StreamEvent]:
    for event in provider.stream(messages, tools):
        yield event
        if event.type == "tool-call":
            call = event.data
            try:
                result_data = registry.execute(
                    call["name"],
                    call["input"] if isinstance(call["input"], dict) else {},
                )
                result = result_data.get("output", "")
                attachments = result_data.get("attachments", [])
                yield StreamEvent(type="tool-result", data={
                    "id": call["id"],
                    "name": call["name"],
                    "result": result,
                    "attachments": attachments,
                })
            except Exception as e:
                yield StreamEvent(type="tool-error", data={
                    "id": call["id"],
                    "name": call["name"],
                    "error": str(e),
                })
