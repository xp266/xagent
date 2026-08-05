from __future__ import annotations
from collections.abc import Iterator
from typing import TYPE_CHECKING
from src.types.events import StreamEvent, ToolResultData
from src.ai.base import Provider
from src.agent.cancel import TurnCancelled, is_cancelled

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
        if is_cancelled():
            raise TurnCancelled
        if event.type == "tool-call":
            call = event.data
            try:
                result_data = registry.execute(
                    call["name"],
                    call["input"] if isinstance(call["input"], dict) else {},
                )
                result = result_data.get("output", "")
                attachments = result_data.get("attachments", [])
                tool_result_data: ToolResultData = {
                    "id": call["id"],
                    "name": call["name"],
                    "result": result,
                    "attachments": attachments,
                }
                yield StreamEvent(type="tool-result", data=tool_result_data)
            except Exception as e:
                yield StreamEvent(type="tool-error", data={
                    "id": call["id"],
                    "name": call["name"],
                    "error": str(e),
                })
