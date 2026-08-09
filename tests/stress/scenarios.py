from __future__ import annotations

import itertools
import json
import time
from typing import Iterator

from tests.stress import content

_STATE: dict[str, dict] = {}
_MODEL = "step-3.7-flash"
_CHAT_ID = "chatcmpl-stress"
_SOFT_CAP_CHARS = 200_000_000


def _rate_sleep() -> float:
    return float(__import__("os").environ.get("XAGENT_RATE_MS", "8")) / 1000.0


def classify(messages: list) -> str:
    key = ""
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            key = c.strip()
    return key


def error_status(messages: list) -> int | None:
    key = classify(messages).lower()
    if key in ("retry", "rate", "429", "ratelimit"):
        return 429
    if key in ("error", "auth", "401"):
        return 401
    return None


def error_body(status: int) -> dict:
    if status == 429:
        return {
            "error": {
                "message": "Rate limit reached for stress model. Please try again later.",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }
        }
    return {
        "error": {
            "message": "Incorrect API key provided: sk-stress-test. You can find your API key at https://example.com.",
            "type": "authentication_error",
            "code": "invalid_api_key",
        }
    }


def _chunk(delta: dict, finish: str | None = None, created: int = 0) -> dict:
    return {
        "id": _CHAT_ID,
        "object": "chat.completion.chunk",
        "created": created,
        "model": _MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _usage_chunk(prompt: int, completion: int) -> dict:
    return {
        "id": _CHAT_ID,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": _MODEL,
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def _text_deltas(text: str, size: int, sleep: float):
    for i in range(0, len(text), size):
        yield text[i:i + size]
        if sleep:
            time.sleep(sleep)


def _finish(prompt: int, completion: int, finish: str = "stop") -> Iterator[dict]:
    yield _chunk({}, finish=finish)
    yield _usage_chunk(prompt, completion)


def _normal(key: str, model: str) -> Iterator[dict]:
    created = int(time.time())
    yield _chunk({"content": f"Normal reply for scenario: {key}\n"}, created=created)
    yield from _finish(120, 32)


def _loop_text(field: str, chunks: list[str], key: str, model: str) -> Iterator[dict]:
    created = int(time.time())
    sent = 0
    for cycle in itertools.count():
        for text in chunks:
            for delta in _text_deltas(text, 40, _rate_sleep()):
                sent += len(delta)
                if sent > _SOFT_CAP_CHARS:
                    yield from _finish(5000, sent)
                    return
                yield _chunk({field: delta}, created=created)
        if cycle % 5 == 0:
            marker = f"\n## cycle {cycle}\n"
            for delta in _text_deltas(marker, 20, _rate_sleep()):
                yield _chunk({field: delta}, created=created)


def _thinking_loop(key: str, model: str) -> Iterator[dict]:
    yield from _loop_text("reasoning_content", content.THINKING_CHUNKS, key, model)


def _reply_loop(key: str, model: str) -> Iterator[dict]:
    yield from _loop_text("content", content.REPLY_CHUNKS, key, model)


def _replong(key: str, model: str) -> Iterator[dict]:
    text = content.big_reply()
    created = int(time.time())
    for delta in _text_deltas(text, 4096, 0.005):
        yield _chunk({"content": delta}, created=created)
    yield from _finish(2000, len(text))


def _storm(key: str, model: str) -> Iterator[dict]:
    created = int(time.time())
    sent = 0
    chunk = "The quick brown fox jumps over the lazy dog. "
    for _ in itertools.count():
        for delta in _text_deltas(chunk * 4, 128, 0.0):
            sent += len(delta)
            if sent > _SOFT_CAP_CHARS:
                yield from _finish(1000, sent)
                return
            yield _chunk({"content": delta}, created=created)


def _tool_sequence(step: int) -> tuple[str, dict]:
    write_path = content.scratch_path("stress_file.py")
    sequence = [
        ("write", {"path": write_path, "content": content.write_content()}),
        ("edit", content.edit_args()),
        ("read", content.read_args()),
        ("bash", content.bash_args()),
        ("grep", content.grep_args()),
        ("glob", content.glob_args()),
    ]
    return sequence[step % len(sequence)]


def _tool_loop(key: str, model: str, messages: list) -> Iterator[dict]:
    state = _STATE.setdefault(key, {"step": 0, "prompt": 1000})
    step = state["step"]
    name, args = _tool_sequence(step)
    tc_id = f"call_{key.replace(' ', '_')}_{step}"
    raw = json.dumps(args)
    created = int(time.time())
    frag = 96
    first = True
    for i in range(0, len(raw), frag):
        piece = raw[i:i + frag]
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": tc_id if first else None,
                    "type": "function",
                    "function": {"name": name if first else None, "arguments": piece},
                }
            ]
        }
        yield _chunk(delta, created=created)
        first = False
        time.sleep(_rate_sleep())
    state["step"] += 1
    state["prompt"] += 900
    yield from _finish(state["prompt"], 80, finish="tool_calls")


def iter_chunks(request: dict) -> Iterator[dict]:
    messages = request.get("messages", []) or []
    key = classify(messages)
    model = request.get("model", _MODEL)
    if key in ("thinking", "reasoning"):
        return _thinking_loop(key, model)
    if key in ("reply", "answer"):
        return _reply_loop(key, model)
    if key == "replong":
        return _replong(key, model)
    if key == "storm":
        return _storm(key, model)
    if key in ("tools", "toolstorm"):
        return _tool_loop(key, model, messages)
    return _normal(key, model)
