import os
import json

from src.types.config import Capabilities

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODELS_DB: dict | None = None


def _load_models_db() -> dict:
    path = os.path.join(_PROJECT_ROOT, "data", "models.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    index = {}
    for provider in data.values():
        for mname, m in provider.get("models", {}).items():
            mods = m.get("modalities", {}).get("input", [])
            if mname not in index:
                index[mname] = {"image": False, "audio": False, "video": False, "pdf": False}
            index[mname]["image"] = index[mname]["image"] or ("image" in mods)
            index[mname]["audio"] = index[mname]["audio"] or ("audio" in mods)
            index[mname]["video"] = index[mname]["video"] or ("video" in mods)
            index[mname]["pdf"] = index[mname]["pdf"] or ("pdf" in mods)
    return index


def detect_capabilities(model_name: str) -> Capabilities:
    image = False
    audio = False
    reasoning_field = "reasoning_content"
    env_image = os.environ.get("MODEL_CAP_IMAGE")
    env_audio = os.environ.get("MODEL_CAP_AUDIO")
    if env_image is not None:
        image = env_image == "1"
    if env_audio is not None:
        audio = env_audio == "1"
    if env_image is not None and env_audio is not None:
        return Capabilities(image=image, audio=audio, reasoning_field=reasoning_field)
    global _MODELS_DB
    if _MODELS_DB is None:
        _MODELS_DB = _load_models_db()
    entry = _MODELS_DB.get(model_name)
    if entry is not None:
        if env_image is None:
            image = entry["image"]
        if env_audio is None:
            audio = entry["audio"]
        reasoning_field = entry.get("interleaved", {}).get("field", "reasoning_content") if isinstance(entry.get("interleaved"), dict) else "reasoning_content"
        return Capabilities(image=image, audio=audio, reasoning_field=reasoning_field)
    for short, meta in _MODELS_DB.items():
        if model_name in short or short in model_name:
            if env_image is None:
                image = meta["image"]
            if env_audio is None:
                audio = meta["audio"]
            reasoning_field = meta.get("interleaved", {}).get("field", "reasoning_content") if isinstance(meta.get("interleaved"), dict) else "reasoning_content"
    return Capabilities(image=image, audio=audio, reasoning_field=reasoning_field)


def get_model_context_limit(model_name: str) -> int:
    path = os.path.join(_PROJECT_ROOT, "data", "models.json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    best = 0
    for provider in data.values():
        for mname, m in provider.get("models", {}).items():
            if mname == model_name or model_name in mname:
                limit = m.get("limit", {}).get("context", 0)
                if limit > best:
                    best = limit
    return best
