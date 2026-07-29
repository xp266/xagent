import re

_SURROGATE_RE = re.compile(r'[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]')


def replace_surrogates(text: str) -> str:
    if isinstance(text, str):
        return _SURROGATE_RE.sub('\uFFFD', text)
    return text


def replace_surrogates_in_value(v):
    if isinstance(v, str):
        return replace_surrogates(v)
    if isinstance(v, dict):
        return {kk: replace_surrogates_in_value(vv) for kk, vv in v.items()}
    if isinstance(v, list):
        return [replace_surrogates_in_value(item) for item in v]
    return v
