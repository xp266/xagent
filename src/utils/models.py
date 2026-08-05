import os
import json

from pydantic import BaseModel


class Capabilities(BaseModel):
    image: bool = False
    pdf: bool = False
    reasoning_field: str = "reasoning_content"


_MODELS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "models.json")
_CATALOG: dict | None = None
_MODELS_DB: dict | None = None


def load_models_catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        if os.path.isfile(_MODELS_PATH):
            try:
                with open(_MODELS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _CATALOG = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                _CATALOG = {}
        else:
            _CATALOG = {}
    return _CATALOG


def _load_models_db() -> dict:
    index = {}
    for provider in load_models_catalog().values():
        for mname, m in provider.get("models", {}).items():
            if not isinstance(m, dict):
                continue
            mods = m.get("modalities", {}).get("input", [])
            if mname not in index:
                index[mname] = {"image": False, "pdf": False, "context": 0, "output": 0, "reasoning_field": "reasoning_content"}
            index[mname]["image"] = index[mname]["image"] or ("image" in mods)
            index[mname]["pdf"] = index[mname]["pdf"] or ("pdf" in mods)
            limit = m.get("limit", {})
            if limit.get("context", 0) > index[mname]["context"]:
                index[mname]["context"] = limit.get("context", 0)
            if limit.get("output", 0) > index[mname]["output"]:
                index[mname]["output"] = limit.get("output", 0)
            inter = m.get("interleaved")
            if isinstance(inter, dict) and inter.get("field"):
                index[mname]["reasoning_field"] = inter["field"]
    return index


def _raw_entry(model_name: str, provider_meta: dict | None) -> dict | None:
    if isinstance(provider_meta, dict):
        entry = provider_meta.get(model_name)
        if isinstance(entry, dict):
            return entry
    return None


def _caps_from_raw(entry: dict) -> Capabilities:
    mods = entry.get("modalities", {}).get("input", [])
    inter = entry.get("interleaved")
    field = inter.get("field", "reasoning_content") if isinstance(inter, dict) else "reasoning_content"
    return Capabilities(image="image" in mods, pdf="pdf" in mods, reasoning_field=field)


def detect_capabilities(model_name: str, provider_meta: dict | None = None) -> Capabilities:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    raw = _raw_entry(model_name, provider_meta)
    if raw is not None:
        return _caps_from_raw(raw)
    entry = _MODELS_DB.get(model_name)
    if entry is not None:
        return Capabilities(image=entry["image"], pdf=entry["pdf"], reasoning_field=entry["reasoning_field"])
    for short, meta in _MODELS_DB.items():
        if model_name in short or short in model_name:
            return Capabilities(image=meta["image"], pdf=meta["pdf"], reasoning_field=meta["reasoning_field"])
    return Capabilities()


def get_model_context_limit(model_name: str, provider_meta: dict | None = None) -> int:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    raw = _raw_entry(model_name, provider_meta)
    if raw is not None:
        ctx = raw.get("limit", {}).get("context", 0)
        if ctx:
            return ctx
    entry = _MODELS_DB.get(model_name)
    if entry and entry.get("context"):
        return entry["context"]
    best = 0
    for mname, m in _MODELS_DB.items():
        if model_name in mname and m.get("context", 0) > best:
            best = m["context"]
    return best


def get_model_output_limit(model_name: str, provider_meta: dict | None = None) -> int:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    raw = _raw_entry(model_name, provider_meta)
    if raw is not None:
        out = raw.get("limit", {}).get("output", 0)
        if out:
            return out
    entry = _MODELS_DB.get(model_name)
    if entry and entry.get("output"):
        return entry["output"]
    best = 0
    for mname, m in _MODELS_DB.items():
        if model_name in mname and m.get("output", 0) > best:
            best = m["output"]
    return best
