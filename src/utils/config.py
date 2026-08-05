from src.types.config import Config
from src.utils.providers import get_store


def get_config() -> Config:
    resolved = get_store().resolve()
    return Config(
        base_url=resolved["base_url"],
        model=resolved["model"],
        api_key=resolved["api_key"],
    )


def get_exa_api_key() -> str:
    return get_store().exa_api_key
