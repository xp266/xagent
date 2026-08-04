import threading
import time


class TurnCancelled(Exception):
    pass


_event = threading.Event()
_finished = threading.Event()
_lock = threading.Lock()
_abort_handlers: set = set()
_aborted = False
_progress = 0.0


def reset() -> None:
    global _aborted, _progress
    _event.clear()
    _finished.clear()
    with _lock:
        _aborted = False
        _progress = time.monotonic()


def cancel(grace: float = 5.0) -> None:
    _event.set()
    threading.Thread(
        target=_watchdog,
        args=(grace,),
        name="xagent-cancel-watchdog",
        daemon=True,
    ).start()


def is_cancelled() -> bool:
    note_activity()
    return _event.is_set()


def note_activity() -> None:
    global _progress
    _progress = time.monotonic()


def turn_done() -> None:
    _finished.set()


def register_abort(handler) -> None:
    with _lock:
        _abort_handlers.add(handler)


def unregister_abort(handler) -> None:
    with _lock:
        _abort_handlers.discard(handler)


def abort() -> None:
    with _lock:
        handlers = list(_abort_handlers)
    for handler in handlers:
        try:
            handler()
        except Exception:
            pass


def _watchdog(grace: float) -> None:
    global _aborted
    while True:
        if _finished.wait(0.25):
            return
        with _lock:
            if _aborted:
                return
            cancelled = _event.is_set()
            idle = time.monotonic() - _progress
        if not cancelled:
            return
        if idle >= grace:
            with _lock:
                _aborted = True
            abort()
            return