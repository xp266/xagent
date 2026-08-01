import json
from collections.abc import Iterator

from src.agent.loop import agent_stream
from src.agent.session import Session, get_session_manager
from src.types.events import LLMResponse, StreamEvent, TokenUsage


def run_session_turn(session: Session, user_input: str) -> Iterator[StreamEvent]:
    session.msgs.add_user(user_input)

    while True:
        response = LLMResponse()
        tool_calls_pending = []
        tool_results = []

        try:
            stream = agent_stream(
                session.provider,
                session.msgs.get_api_messages(),
                session.registry.schemas() or None,
                session.registry,
            )

            for event in stream:
                if event.type == "step-start":
                    response = LLMResponse()
                    tool_calls_pending = []
                    tool_results = []
                elif event.type == "reasoning-delta":
                    response.reasoning += event.data
                elif event.type == "signature":
                    response.signature = event.data
                elif event.type == "text-delta":
                    response.content += event.data
                elif event.type == "tool-call":
                    tool_calls_pending.append(event.data)
                elif event.type in ("tool-result", "tool-error"):
                    tool_results.append(event.data)
                elif event.type == "step-finish":
                    response.finish_reason = event.data.get("finish_reason", "")
                    usage = event.data.get("usage", {})
                    if usage:
                        session.token_usage = TokenUsage(
                            prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                            completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                            total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                        )

                yield event

        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": 0})
            session.sync_messages()
            get_session_manager().save(session)
            return

        for tc in tool_calls_pending:
            response.tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["input"]) if isinstance(tc["input"], dict) else str(tc["input"]),
                },
            })

        session.msgs.add_assistant(response)

        for tr in tool_results:
            session.msgs.add_tool(
                tr["id"],
                tr.get("result", tr.get("error", "")),
                tr.get("attachments"),
                is_error=bool(tr.get("error")),
            )

        if response.finish_reason != "tool_calls":
            break

    session.sync_messages()
    get_session_manager().save(session)
