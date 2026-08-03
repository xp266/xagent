import os
import json

from src.types.config import Capabilities

_MODELS_DB: dict | None = None


def _load_models_db() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "models.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    index = {}
    for provider in data.values():
        for mname, m in provider.get("models", {}).items():
            mods = m.get("modalities", {}).get("input", [])
            if mname not in index:
                index[mname] = {"image": False, "audio": False, "video": False, "pdf": False, "context": 0, "output": 0}
            index[mname]["image"] = index[mname]["image"] or ("image" in mods)
            index[mname]["audio"] = index[mname]["audio"] or ("audio" in mods)
            index[mname]["video"] = index[mname]["video"] or ("video" in mods)
            index[mname]["pdf"] = index[mname]["pdf"] or ("pdf" in mods)
            limit = m.get("limit", {}).get("context", 0)
            if limit > index[mname]["context"]:
                index[mname]["context"] = limit
            output = m.get("limit", {}).get("output", 0)
            if output > index[mname]["output"]:
                index[mname]["output"] = output
    return index


def detect_capabilities(model_name: str) -> Capabilities:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()

    def field(meta: dict) -> str:
        return meta.get("interleaved", {}).get("field", "reasoning_content") if isinstance(meta.get("interleaved"), dict) else "reasoning_content"

    entry = _MODELS_DB.get(model_name)
    if entry is not None:
        return Capabilities(
            image=entry["image"], audio=entry["audio"],
            video=entry["video"], pdf=entry["pdf"],
            reasoning_field=field(entry),
        )
    for short, meta in _MODELS_DB.items():
        if model_name in short or short in model_name:
            return Capabilities(
                image=meta["image"], audio=meta["audio"],
                video=meta["video"], pdf=meta["pdf"],
                reasoning_field=field(meta),
            )
    return Capabilities()


def get_model_context_limit(model_name: str) -> int:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    entry = _MODELS_DB.get(model_name)
    if entry and entry.get("context"):
        return entry["context"]
    best = 0
    for mname, m in _MODELS_DB.items():
        if model_name in mname:
            if m.get("context", 0) > best:
                best = m["context"]
    return best


def get_model_output_limit(model_name: str) -> int:
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    entry = _MODELS_DB.get(model_name)
    if entry and entry.get("output"):
        return entry["output"]
    best = 0
    for mname, m in _MODELS_DB.items():
        if model_name in mname:
            if m.get("output", 0) > best:
                best = m["output"]
    return best
