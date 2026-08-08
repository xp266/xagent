from src.ai.base import Provider
from src.utils import load_prompt

_NAME_MAX_LEN = 50
_MAX_FIRST_MSG = 500


def _truncate(name: str) -> str:
    name = name.strip().strip('"\'.')
    if len(name) > _NAME_MAX_LEN:
        name = name[:_NAME_MAX_LEN].rsplit(" ", 1)[0]
    return name


async def generate_name(provider: Provider, first_message: str) -> str:
    messages = [
        {"role": "system", "content": load_prompt("naming")},
        {"role": "user", "content": first_message[:_MAX_FIRST_MSG]},
    ]
    try:
        response = await provider.arespond(messages)
        raw = response.content.strip()
        return _truncate(raw) if raw else "New Session"
    except Exception:
        return "New Session"
