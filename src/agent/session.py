from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from src.utils.paths import data_dir
from src.utils.prompts import load as load_prompt
from src.utils.providers import get_store, is_anthropic_provider
from src.utils.instructions import build_system_prompt
from src.agent.manager import MessageManager
from src.agent.naming import generate_name
from src.tools.registry import ToolRegistry
from src.types.events import TokenUsage
from src.types.tools import Tool
from src.mcp.manager import get_mcp_manager


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _data_root() -> str:
    return os.path.join(data_dir(), "sessions")


def _index_path() -> str:
    return os.path.join(data_dir(), "sessions_index.json")


def _make_mcp_execute(manager, name: str):
    def execute(**kwargs) -> dict:
        return manager.execute(name, kwargs)

    return execute


def _ensure_dirs():
    os.makedirs(_data_root(), exist_ok=True)


def _file_path(session_id: str) -> str:
    safe = session_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_data_root(), f"{safe}.json")


def _read_index() -> list[dict]:
    if not os.path.isfile(_index_path()):
        return []
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(entries: list[dict]):
    os.makedirs(os.path.dirname(_index_path()), exist_ok=True)
    with open(_index_path(), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _read_session(session_id: str) -> dict | None:
    path = _file_path(session_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_session(data: dict):
    os.makedirs(_data_root(), exist_ok=True)
    path = _file_path(data["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Session:
    def __init__(
        self,
        *,
        id: str = "",
        name: str = "",
        path: str = "",
        messages: list | None = None,
        created_at: str = "",
        updated_at: str = "",
        token_usage: TokenUsage | None = None,
    ):
        self.id = id or str(uuid.uuid4())[:8]
        self.name = name or "New Session"
        self.path = path or os.getcwd()
        self.messages = messages or []
        now = datetime.now().isoformat()
        self.created_at = created_at or now
        self.updated_at = updated_at or now
        self.token_usage = token_usage or TokenUsage()

        self._provider = None
        self._registry = None
        self._msgs = None

    @property
    def provider(self):
        if self._provider is None:
            resolved = get_store().resolve()
            active = get_store().get_active()
            model_meta = active.model_meta if active is not None else None
            if is_anthropic_provider(active):
                from src.ai.anthropic import AnthropicProvider

                self._provider = AnthropicProvider(
                    model=resolved["model"],
                    base_url=resolved["base_url"],
                    api_key=resolved["api_key"],
                    model_meta=model_meta,
                    reasoning_effort=resolved.get("reasoning_effort", ""),
                )
            else:
                from src.ai.openai import OpenAIProvider

                self._provider = OpenAIProvider(
                    model=resolved["model"],
                    base_url=resolved["base_url"],
                    api_key=resolved["api_key"],
                    model_meta=model_meta,
                    reasoning_effort=resolved.get("reasoning_effort", ""),
                )
        return self._provider

    @provider.setter
    def provider(self, value):
        self._provider = value

    def reset_provider(self) -> None:
        self._provider = None

    @property
    def registry(self):
        if self._registry is None:
            self._registry = ToolRegistry()
            self._registry.load_local(os.path.join(_PROJECT_ROOT, "src", "tools"))
            self._registry.sync_hook = self._sync_mcp_tools
            self._sync_mcp_tools()
        return self._registry

    @registry.setter
    def registry(self, value):
        self._registry = value

    def _sync_mcp_tools(self) -> None:
        manager = get_mcp_manager()
        manager.configure(get_store().mcp_servers)
        for tool in manager.tools:
            name = tool.get("name", "")
            if not name or name in self._registry._tools:
                continue
            self._registry.register(Tool(
                name=name,
                description=tool.get("description", "") or "",
                parameters=tool.get("inputSchema") or {"type": "object", "properties": {}},
                execute=_make_mcp_execute(manager, name),
                to_model_output=lambda data: data.get("output", ""),
            ))

    @property
    def msgs(self) -> MessageManager:
        if self._msgs is None:
            system_prompt = build_system_prompt(load_prompt("default"), self.path)
            self._msgs = MessageManager(system_prompt, session=self)
        return self._msgs

    @msgs.setter
    def msgs(self, value: MessageManager):
        self._msgs = value

    def sync_messages(self):
        if self._msgs:
            self._msgs.save()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "token_usage": self.token_usage.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        kwargs = {k: data[k] for k in ("id", "name", "path", "messages", "created_at", "updated_at") if k in data}
        tu = data.get("token_usage")
        if tu:
            kwargs["token_usage"] = TokenUsage(**tu)
        return cls(**kwargs)


class SessionManager:
    def __init__(self):
        _ensure_dirs()
        self._index: dict[str, dict] = {}
        self._current_id: str = ""
        self._load_index()

    def _load_index(self):
        self._index = {}
        for entry in _read_index():
            self._index[entry["id"]] = entry
        stale = [sid for sid in self._index if not os.path.isfile(_file_path(sid))]
        for sid in stale:
            del self._index[sid]
        if stale:
            self._save_index()

    def _save_index(self):
        entries = []
        for s in self._index.values():
            entry = dict(s)
            entry.pop("messages", None)
            entries.append(entry)
        _write_index(entries)

    @property
    def current(self):
        if self._current_id:
            s = self.get(self._current_id)
            if s is not None:
                return s
        for sid in self._index:
            s = self.get(sid)
            if s is not None:
                self._current_id = sid
                return s
        return None

    @current.setter
    def current(self, session_id: str):
        if session_id in self._index:
            self._current_id = session_id

    def list(self) -> list[Session]:
        result = []
        for sid, entry in self._index.items():
            result.append(Session(
                id=sid,
                name=entry.get("name", ""),
                path=entry.get("path", ""),
                created_at=entry.get("created_at", ""),
                updated_at=entry.get("updated_at", ""),
            ))
        return result

    def get(self, session_id: str) -> Session | None:
        data = _read_session(session_id)
        if data is None:
            return None
        return Session.from_dict(data)

    def create(self, name: str = "", path: str = "", persist: bool = True) -> Session:
        s = Session(name=name, path=path)
        if persist:
            _write_session(s.to_dict())
            self._index[s.id] = {
                "id": s.id, "name": s.name, "path": s.path,
                "created_at": s.created_at, "updated_at": s.updated_at,
            }
            self._current_id = s.id
            self._save_index()
        return s

    def delete(self, session_id: str) -> bool:
        if session_id not in self._index:
            return False
        path = _file_path(session_id)
        if os.path.isfile(path):
            os.remove(path)
        del self._index[session_id]
        if self._current_id == session_id:
            self._current_id = next(iter(self._index)) if self._index else ""
        self._save_index()
        return True

    def rename(self, session_id: str, name: str) -> bool:
        entry = self._index.get(session_id)
        if not entry:
            return False
        entry["name"] = name
        entry["updated_at"] = datetime.now().isoformat()
        data = _read_session(session_id)
        if data:
            data["name"] = name
            data["updated_at"] = entry["updated_at"]
            _write_session(data)
        self._save_index()
        return True

    def save(self, session: Session):
        session.updated_at = datetime.now().isoformat()
        _write_session(session.to_dict())
        entry = self._index.get(session.id)
        if entry:
            entry["name"] = session.name
            entry["path"] = session.path
            entry["updated_at"] = session.updated_at
        else:
            self._index[session.id] = {
                "id": session.id, "name": session.name, "path": session.path,
                "created_at": session.created_at, "updated_at": session.updated_at,
            }
            self._current_id = session.id
        self._save_index()


_SESSION_MANAGER: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _SESSION_MANAGER
    if _SESSION_MANAGER is None:
        _SESSION_MANAGER = SessionManager()
    return _SESSION_MANAGER


async def name_session_from_first_message(session: Session, first_message: str) -> str | None:
    try:
        return await generate_name(session.provider, first_message)
    except Exception:
        return None
