from __future__ import annotations

import json
import os

from src.utils.paths import data_dir
from src.utils.models import load_models_catalog
from src.types.config import AppConfig, ProviderInfo


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

DEFAULT_CONTEXT = 200_000

_CONTEXT_FIELDS = (
    "context_length", "context_window", "max_context",
    "context_window_tokens", "max_input_tokens",
)


class ProviderStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else os.path.join(data_dir(), "config.json")
        self._config = self._load()

    @property
    def _builtin(self) -> dict:
        return load_models_catalog()


    def _load(self) -> AppConfig:
        if not os.path.isfile(self.path):
            return AppConfig()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return AppConfig()
        providers = raw.get("providers", {}) or {}
        raw_effort = raw.get("reasoning_effort", "")
        if isinstance(raw_effort, dict):
            efforts = {str(k): str(v) for k, v in raw_effort.items() if v}
        elif raw_effort:
            efforts = {str(raw.get("active_model", "")): str(raw_effort)} if raw.get("active_model") else {}
        else:
            efforts = {}
        raw_contexts = raw.get("model_contexts", {}) or {}
        contexts = {
            str(k): int(v) for k, v in raw_contexts.items()
            if isinstance(v, (int, float)) and int(v) > 0
        }
        return AppConfig(
            active_provider=str(raw.get("active_provider", "")),
            active_model=str(raw.get("active_model", "")),
            reasoning_effort=efforts,
            providers={k: v for k, v in providers.items() if isinstance(v, dict)},
            mcp_servers={k: v for k, v in raw.get("mcp_servers", {}).items() if isinstance(v, dict)},
            model_contexts=contexts,
            compact_model=str(raw.get("compact_model", "")),
        )

    def save(self) -> None:
        payload = {
            "active_provider": self._config.active_provider,
            "active_model": self._config.active_model,
            "reasoning_effort": self._config.reasoning_effort,
            "providers": self._config.providers,
            "mcp_servers": self._config.mcp_servers,
            "model_contexts": self._config.model_contexts,
            "compact_model": self._config.compact_model,
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


    def _builtin_base_url(self, pid: str) -> str:
        entry = self._builtin.get(pid, {})
        return entry.get("api") or FALLBACK_BASE_URL.get(pid, "")


    def _provider_info(self, pid: str, stored: dict, is_custom: bool) -> ProviderInfo:
        if is_custom:
            return ProviderInfo(
                id=pid,
                name=stored.get("name", pid[len(_CUSTOM_ID_PREFIX):]),
                base_url=stored.get("base_url", ""),
                api_key=stored.get("api_key", ""),
                is_custom=True,
                protocol=stored.get("protocol", ""),
                models=list(stored.get("models", [])),
                selected_models=[m for m in stored.get("selected_models", []) if m],
            )
        entry = self._builtin.get(pid, {})
        raw_models = entry.get("models", {})
        models = list(raw_models.keys()) if isinstance(raw_models, dict) else []
        meta = {mid: m for mid, m in raw_models.items() if isinstance(m, dict)}
        return ProviderInfo(
            id=pid,
            name=entry.get("name") or pid,
            base_url=self._builtin_base_url(pid),
            api_key=stored.get("api_key", ""),
            is_custom=False,
            protocol="anthropic" if pid == "anthropic" else "",
            models=models,
            model_meta=meta,
            selected_models=[m for m in stored.get("selected_models", []) if m],
        )

    def list_providers(self) -> list[ProviderInfo]:
        result = []
        for pid in self._builtin:
            result.append(self._provider_info(pid, self._config.providers.get(pid, {}), False))
        for pid, stored in self._config.providers.items():
            if pid.startswith(_CUSTOM_ID_PREFIX):
                result.append(self._provider_info(pid, stored, True))
        result.sort(key=lambda p: (not p.is_custom, p.name.lower()))
        return result

    def get_provider(self, pid: str) -> ProviderInfo | None:
        if pid in self._builtin:
            return self._provider_info(pid, self._config.providers.get(pid, {}), False)
        if pid.startswith(_CUSTOM_ID_PREFIX) and pid in self._config.providers:
            return self._provider_info(pid, self._config.providers[pid], True)
        return None

    def get_provider_models(self, pid: str) -> list[str]:
        p = self.get_provider(pid)
        if p is None:
            return []
        return p.models

    def add_selected_model(self, pid: str, model: str) -> None:
        stored = self._config.providers.setdefault(pid, {})
        if not isinstance(stored, dict) or not model:
            return
        sel = [m for m in stored.get("selected_models", []) if m]
        if model not in sel:
            sel.append(model)
        stored["selected_models"] = sel
        self._config.active_model = model
        self.save()


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

    def set_provider_api_key(self, pid: str, key: str) -> None:
        stored = self._config.providers.setdefault(pid, {})
        if not isinstance(stored, dict):
            self._config.providers[pid] = {}
            stored = self._config.providers[pid]
        stored["api_key"] = key.strip()
        self.save()

    @property
    def reasoning_effort(self) -> dict[str, str]:
        return dict(self._config.reasoning_effort)

    @property
    def mcp_servers(self) -> dict[str, dict]:
        return {k: v for k, v in self._config.mcp_servers.items() if isinstance(v, dict)}

    def mcp_server_status(self, name: str) -> bool:
        cfg = self._config.mcp_servers.get(name)
        if not isinstance(cfg, dict):
            return False
        return str(cfg.get("status", "enabled")).lower() != "disabled"

    def toggle_mcp_server(self, name: str) -> None:
        cfg = self._config.mcp_servers.get(name)
        if not isinstance(cfg, dict):
            cfg = {}
            self._config.mcp_servers[name] = cfg
        enabled = str(cfg.get("status", "enabled")).lower() != "disabled"
        cfg["status"] = "disabled" if enabled else "enabled"
        self.save()

    def get_reasoning_effort(self, model: str) -> str:
        return self._config.reasoning_effort.get(model, "")

    def set_reasoning_effort(self, model: str, value: str) -> None:
        value = (value or "").strip()
        if value:
            self._config.reasoning_effort[model] = value
        else:
            self._config.reasoning_effort.pop(model, None)
        self.save()

    def get_active(self) -> ProviderInfo | None:
        return self.get_provider(self._config.active_provider)

    def resolve(self) -> dict:
        model = self._config.active_model
        p = self.get_active()
        if p is None:
            return {"base_url": "", "api_key": "", "model": model or "", "reasoning_effort": self.get_reasoning_effort(model)}
        return {
            "base_url": p.base_url,
            "api_key": p.api_key,
            "model": model,
            "reasoning_effort": self.get_reasoning_effort(model),
        }


    def save_custom_provider(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: list[str] | None = None,
        pid: str = "",
        protocol: str = "",
    ) -> str:
        name = name.strip() or "Custom Provider"
        base_url = base_url.strip().rstrip("/")
        pid = (pid or f"{_CUSTOM_ID_PREFIX}{name.replace(' ', '-').lower() or 'custom'}")
        stored: dict = {
            "name": name,
            "base_url": base_url,
            "api_key": api_key.strip(),
            "models": [m for m in (models or []) if m],
        }
        protocol = (protocol or "").strip().lower()
        if protocol in ("anthropic", "openai"):
            stored["protocol"] = protocol
        self._config.providers[pid] = stored
        self.save()
        return pid

    def set_custom_models(self, pid: str, models: list[str]) -> None:
        stored = self._config.providers.get(pid)
        if stored is not None and pid.startswith(_CUSTOM_ID_PREFIX):
            stored["models"] = [m for m in models if m]
            self.save()

    @property
    def compact_model(self) -> str:
        return str(self._config.compact_model)

    def set_compact_model(self, model: str) -> None:
        self._config.compact_model = (model or "").strip()
        self.save()

    def get_model_context_override(self, model: str) -> int:
        return self._config.model_contexts.get(model, 0)

    def set_model_context_override(self, model: str, context: int) -> None:
        model = (model or "").strip()
        context = int(context) if context and int(context) > 0 else 0
        if not model:
            return
        if context > 0:
            self._config.model_contexts[model] = context
        else:
            self._config.model_contexts.pop(model, None)
        self.save()

    def seed_model_context(self, model: str) -> None:
        from src.utils.models import get_model_context_limit

        if not model or model in self._config.model_contexts:
            return
        if get_model_context_limit(model) > 0:
            return
        self._config.model_contexts[model] = DEFAULT_CONTEXT
        self.save()

    def get_effective_context_limit(self, model: str) -> int:
        override = self._config.model_contexts.get(model, 0)
        if override > 0:
            return override
        from src.utils.models import get_model_context_limit

        limit = get_model_context_limit(model)
        return limit if limit > 0 else DEFAULT_CONTEXT


def is_anthropic_provider(provider: ProviderInfo | None) -> bool:
    if provider is None:
        return False
    protocol = (provider.protocol or "").lower()
    if protocol:
        return protocol == "anthropic"
    return "anthropic" in (provider.base_url or "").lower()


def _item_context(item: dict) -> int:
    for key in _CONTEXT_FIELDS:
        val = item.get(key)
        if isinstance(val, (int, float)) and int(val) > 0:
            return int(val)
        if isinstance(val, dict):
            for v in val.values():
                if isinstance(v, (int, float)) and int(v) > 0:
                    return int(v)
    return 0


def fetch_models(base_url: str, api_key: str) -> list[str]:
    ids, _ = fetch_models_with_context(base_url, api_key)
    return ids


def fetch_models_with_context(base_url: str, api_key: str) -> tuple[list[str], dict[str, int]]:
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data", []) if isinstance(payload, dict) else payload
    ids = []
    contexts: dict[str, int] = {}
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            mid = str(item["id"])
            ids.append(mid)
            ctx = _item_context(item)
            if ctx:
                contexts[mid] = ctx
    return ids, contexts


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
