import json
import time
from collections.abc import Iterator

from src.agent.cancel import TurnCancelled
from src.agent.loop import agent_stream
from src.agent.session import Session, get_session_manager
from src.types.events import LLMResponse, StreamEvent, TokenUsage
from src.types.messages import AssistantMessage

INTERRUPTED_TOOL_RESULT = "Tool call interrupted by user."


def _attach_turn_meta(session: Session, model: str, usage: TokenUsage, prompt_tokens: int, elapsed: float) -> None:
    meta = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "total_tokens": usage.total_tokens,
        "elapsed": round(elapsed, 2),
    }
    for msg in reversed(session.msgs.get_messages()):
        if isinstance(msg, AssistantMessage):
            msg.meta = dict(meta)
            return


def _fill_tool_calls(response: LLMResponse, tool_calls_pending: list) -> None:
    for tc in tool_calls_pending:
        response.tool_calls.append({
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc["input"]) if isinstance(tc["input"], dict) else str(tc["input"]),
            },
        })


def _commit_response(session: Session, response: LLMResponse, tool_results: list, cancelled: bool) -> None:
    if response.reasoning and not response.signature:
        response.reasoning = ""
    session.msgs.add_assistant(response)
    if cancelled:
        executed = {tr["id"] for tr in tool_results}
        for tc in response.tool_calls:
            if tc["id"] not in executed:
                session.msgs.add_tool(tc["id"], INTERRUPTED_TOOL_RESULT)


def run_session_turn(session: Session, user_input: str) -> Iterator[StreamEvent]:
    session.msgs.add_user(user_input)
    start = time.monotonic()

    cancelled = False

    while not cancelled:
        response = LLMResponse()
        tool_calls_pending = []
        tool_results = []
        turn_usage = TokenUsage()
        last_prompt_tokens = 0
        committed = False

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
                        last_prompt_tokens = usage.get("prompt_tokens", 0)
                        turn_usage = TokenUsage(
                            prompt_tokens=turn_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                            completion_tokens=turn_usage.completion_tokens + usage.get("completion_tokens", 0),
                            total_tokens=turn_usage.total_tokens + usage.get("total_tokens", 0),
                        )
                        session.token_usage = TokenUsage(
                            prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                            completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                            total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                        )

                yield event

        except TurnCancelled:
            cancelled = True
        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": 0})
            session.sync_messages()
            get_session_manager().save(session)
            return

        if not cancelled:
            _fill_tool_calls(response, tool_calls_pending)
            _commit_response(session, response, tool_results, cancelled=False)
            committed = True
            for tr in tool_results:
                session.msgs.add_tool(
                    tr["id"],
                    tr.get("result", tr.get("error", "")),
                    tr.get("attachments"),
                    is_error=bool(tr.get("error")),
                )

            if response.finish_reason != "tool_calls":
                break

    if cancelled and not committed:
        _fill_tool_calls(response, tool_calls_pending)
        _commit_response(session, response, tool_results, cancelled=True)
        for tr in tool_results:
            session.msgs.add_tool(
                tr["id"],
                tr.get("result", tr.get("error", "")),
                tr.get("attachments"),
                is_error=bool(tr.get("error")),
            )

    if cancelled:
        yield StreamEvent(type="turn-cancelled", data={})

    _attach_turn_meta(session, session.provider.model, turn_usage, last_prompt_tokens, time.monotonic() - start)
    session.sync_messages()
    get_session_manager().save(session)
