from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from src.types.events import StreamEvent, ToolResultData
from src.ai.base import Provider

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


async def agent_stream(
    provider: Provider,
    messages: list[dict],
    tools: list | None,
    registry: ToolRegistry,
) -> AsyncIterator[StreamEvent]:
    async for event in provider.astream(messages, tools):
        yield event
        if event.type == "tool-call":
            call = event.data
            try:
                result_data = await asyncio.to_thread(
                    registry.execute,
                    call["name"],
                    call["input"] if isinstance(call["input"], dict) else {},
                )
                result = result_data.get("output", "")
                attachments = result_data.get("attachments", [])
                if not isinstance(attachments, list):
                    attachments = []
                meta = result_data.get("metadata")
                if not isinstance(meta, dict):
                    meta = {}
                tool_result_data: ToolResultData = {
                    "id": call["id"],
                    "name": call["name"],
                    "result": result,
                    "attachments": attachments,
                    "is_error": bool(meta.get("error")),
                }
                yield StreamEvent(type="tool-result", data=tool_result_data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                yield StreamEvent(type="tool-error", data={
                    "id": call["id"],
                    "name": call["name"],
                    "error": str(e),
                })
