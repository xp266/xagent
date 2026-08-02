import threading


class TurnCancelled(Exception):
    pass


_event = threading.Event()


def reset() -> None:
    _event.clear()


def cancel() -> None:
    _event.set()


def is_cancelled() -> bool:
    return _event.is_set()
