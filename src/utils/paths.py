import os
import tempfile

_DATA_DIR_ENV = "XAGENT_DATA_DIR"


def data_dir() -> str:
    d = os.environ.get(_DATA_DIR_ENV)
    if d:
        return d
    return os.path.join(os.path.expanduser("~"), ".local", "share", "xagent")


def truncation_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "xagent", "truncation")
