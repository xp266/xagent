_NAMING_PROMPT = (
    "You are a naming assistant. Given the user's first message, "
    "generate a short session name (2-5 words, no quotes, no punctuation). "
    "Reply with ONLY the name, nothing else."
)

_NAME_MAX_LEN = 50


def _truncate_name(name: str) -> str:
    name = name.strip().strip('"').strip("'").strip(".")
    if len(name) > _NAME_MAX_LEN:
        name = name[:_NAME_MAX_LEN].rsplit(" ", 1)[0]
    return name


def generate_name(provider, first_message: str) -> str:
    messages = [
        {"role": "system", "content": _NAMING_PROMPT},
        {"role": "user", "content": first_message[:500]},
    ]
    try:
        response = provider.chat(messages)
        raw = response.content.strip()
        return _truncate_name(raw) if raw else "New Session"
    except Exception:
        return "New Session"
