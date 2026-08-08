import asyncio
import threading


_event = threading.Event()
_turn_task: asyncio.Task | None = None
_lock = threading.Lock()


def reset() -> None:
    _event.clear()


def cancel() -> None:
    _event.set()
    with _lock:
        task = _turn_task
    if task is not None and not task.done():
        task.cancel()


def is_cancelled() -> bool:
    return _event.is_set()


def set_turn_task(task: asyncio.Task | None) -> None:
    global _turn_task
    with _lock:
        _turn_task = task
