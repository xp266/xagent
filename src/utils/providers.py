"""Provider registry and persistent configuration.

Providers are OpenAI-compatible endpoints. Built-in providers are loaded from
``data/models.json`` (id, display name, base URL from the ``api`` field and
model list). Custom providers (name + base_url + api_key) are stored in
``<data_dir>/config.json`` together with the active provider/model selection.

Config file layout::

    {
      "active_provider": "deepseek",
      "active_model": "deepseek-chat",
      "exa_api_key": "...",
      "providers": {
        "deepseek": { "api_key": "..." },
        "my-custom": {
          "name": "My Custom",
          "base_url": "https://...",
          "api_key": "...",
          "models": ["m1", "m2"]
        }
      }
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from src.utils.paths import data_dir

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODELS_PATH = os.path.join(_PROJECT_ROOT, "data", "models.json")
_CONFIG_PATH = os.path.join(data_dir(), "config.json")

# Well-known providers that lack an ``api`` field in models.json but expose a
# standard /v1 endpoint.
FALLBACK_BASE_URL: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "togetherai": "https://api.together.xyz/v1",
    "perplexity": "https://api.perplexity.ai",
    "cohere": "https://api.cohere.com/v2",
    "github-models": "https://models.github.ai/inference",
    "cloudflare-ai-gateway": "https://api.cloudflare.com/client/v4/ai",
    "anthropic": "https://api.anthropic.com/v1",
}

_CUSTOM_ID_PREFIX = "custom:"


@dataclass
class ProviderInfo:
    id: str
    name: str
    base_url: str
    api_key: str = ""
    is_custom: bool = False
    models: list[str] = field(default_factory=list)
    model_meta: dict[str, dict] = field(default_factory=dict)


@dataclass
class AppConfig:
    active_provider: str = ""
    active_model: str = ""
    exa_api_key: str = ""
    providers: dict[str, dict] = field(default_factory=dict)


class ProviderStore:
    """Loads/saves the JSON config and exposes provider lookups."""

    def __init__(self, path: str = _CONFIG_PATH) -> None:
        self.path = path
        self._config = self._load()
        self._builtin = self._load_builtin()

    # ------------------------------------------------------------------ load
    def _load(self) -> AppConfig:
        if not os.path.isfile(self.path):
            return AppConfig()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return AppConfig()
        providers = raw.get("providers", {}) or {}
        return AppConfig(
            active_provider=str(raw.get("active_provider", "")),
            active_model=str(raw.get("active_model", "")),
            exa_api_key=str(raw.get("exa_api_key", "")),
            providers={k: v for k, v in providers.items() if isinstance(v, dict)},
        )

    def _load_builtin(self) -> dict[str, dict]:
        if not os.path.isfile(_MODELS_PATH):
            return {}
        try:
            with open(_MODELS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    # ----------------------------------------------------------------- save
    def save(self) -> None:
        payload = {
            "active_provider": self._config.active_provider,
            "active_model": self._config.active_model,
            "exa_api_key": self._config.exa_api_key,
            "providers": self._config.providers,
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    # ------------------------------------------------------------- built-in
    def _builtin_base_url(self, pid: str) -> str:
        entry = self._builtin.get(pid, {})
        return entry.get("api") or FALLBACK_BASE_URL.get(pid, "")

    def _builtin_models(self, pid: str) -> tuple[list[str], dict[str, dict]]:
        entry = self._builtin.get(pid, {})
        models = entry.get("models", {})
        if not isinstance(models, dict):
            return [], {}
        ids = list(models.keys())
        meta = {}
        for mid, m in models.items():
            if isinstance(m, dict):
                meta[mid] = m
        return ids, meta

    # --------------------------------------------------------------- public
    def list_providers(self) -> list[ProviderInfo]:
        result = []
        for pid in self._builtin:
            entry = self._builtin[pid]
            name = entry.get("name") or pid
            base_url = self._builtin_base_url(pid)
            models, meta = self._builtin_models(pid)
            stored = self._config.providers.get(pid, {})
            result.append(ProviderInfo(
                id=pid,
                name=name,
                base_url=base_url,
                api_key=stored.get("api_key", ""),
                is_custom=False,
                models=models,
                model_meta=meta,
            ))
        for pid, stored in self._config.providers.items():
            if pid.startswith(_CUSTOM_ID_PREFIX):
                result.append(ProviderInfo(
                    id=pid,
                    name=stored.get("name", pid[len(_CUSTOM_ID_PREFIX):]),
                    base_url=stored.get("base_url", ""),
                    api_key=stored.get("api_key", ""),
                    is_custom=True,
                    models=list(stored.get("models", [])),
                ))
        result.sort(key=lambda p: (not p.is_custom, p.name.lower()))
        return result

    def get_provider(self, pid: str) -> ProviderInfo | None:
        for p in self.list_providers():
            if p.id == pid:
                return p
        return None

    def get_provider_models(self, pid: str) -> list[str]:
        p = self.get_provider(pid)
        if p is None:
            return []
        return p.models

    # --------------------------------------------------------------- active
    @property
    def active_provider_id(self) -> str:
        return self._config.active_provider

    @property
    def active_model(self) -> str:
        return self._config.active_model

    def set_active_provider(self, pid: str, model: str = "") -> None:
        self._config.active_provider = pid
        if model:
            self._config.active_model = model
        self.save()

    def set_active_model(self, mid: str) -> None:
        self._config.active_model = mid
        self.save()

    @property
    def exa_api_key(self) -> str:
        return self._config.exa_api_key

    def set_exa_api_key(self, key: str) -> None:
        self._config.exa_api_key = key.strip()
        self.save()

    def get_active(self) -> ProviderInfo | None:
        return self.get_provider(self._config.active_provider)

    def resolve(self) -> dict:
        """Return ``{base_url, api_key, model}`` for the active provider."""
        model = self._config.active_model
        p = self.get_active()
        if p is None:
            return {"base_url": "", "api_key": "", "model": model or ""}
        return {
            "base_url": p.base_url,
            "api_key": p.api_key,
            "model": model or (p.models[0] if p.models else ""),
        }

    # -------------------------------------------------------------- custom
    def save_custom_provider(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: list[str] | None = None,
        pid: str = "",
    ) -> str:
        name = name.strip() or "Custom Provider"
        base_url = base_url.strip().rstrip("/")
        pid = (pid or f"{_CUSTOM_ID_PREFIX}{name.replace(' ', '-').lower() or 'custom'}")
        self._config.providers[pid] = {
            "name": name,
            "base_url": base_url,
            "api_key": api_key.strip(),
            "models": [m for m in (models or []) if m],
        }
        self.save()
        return pid

    def set_custom_models(self, pid: str, models: list[str]) -> None:
        stored = self._config.providers.get(pid)
        if stored is not None and pid.startswith(_CUSTOM_ID_PREFIX):
            stored["models"] = [m for m in models if m]
            self.save()

    def remove_custom_provider(self, pid: str) -> None:
        if pid.startswith(_CUSTOM_ID_PREFIX) and pid in self._config.providers:
            del self._config.providers[pid]
            if self._config.active_provider == pid:
                self._config.active_provider = ""
                self._config.active_model = ""
            self.save()


def is_anthropic_provider(provider: ProviderInfo | None) -> bool:
    """Whether a provider speaks the Anthropic Messages API."""
    if provider is None:
        return False
    return "anthropic" in (provider.base_url or "").lower()


def fetch_models(base_url: str, api_key: str) -> list[str]:
    """Fetch an OpenAI-compatible model list via ``GET {base_url}/models``."""
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data", []) if isinstance(payload, dict) else payload
    ids = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


_store: ProviderStore | None = None


def get_store() -> ProviderStore:
    global _store
    if _store is None:
        _store = ProviderStore()
    return _store


def list_providers() -> list[ProviderInfo]:
    return get_store().list_providers()


def get_active() -> ProviderInfo | None:
    return get_store().get_active()
