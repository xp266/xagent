import os
from pydantic import BaseModel


class Config(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""


_config = Config(
    base_url=os.getenv("BASE_URL", ""),
    model=os.getenv("MODEL_NAME", ""),
    api_key=os.getenv("API_KEY", ""),
)


def get_config() -> Config:
    return _config


def update_config(**kwargs) -> Config:
    for k, v in kwargs.items():
        if v is not None and hasattr(_config, k):
            setattr(_config, k, v)
    return _config
