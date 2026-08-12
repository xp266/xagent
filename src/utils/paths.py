import os

from platformdirs import user_data_dir

_DATA_DIR_ENV = "XAGENT_DATA_DIR"


def data_dir() -> str:
    d = os.environ.get(_DATA_DIR_ENV)
    if d:
        return d
    return user_data_dir("xagent", appauthor=False)


def truncation_dir() -> str:
    return os.path.join(data_dir(), "truncation")
