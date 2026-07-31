from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from src.utils.paths import data_dir
from src.utils.config import get_config
from src.utils.prompts import load as load_prompt
from src.agent.manager import MessageManager
from src.agent.loop import agent_stream
from src.agent.naming import generate_name
from src.tools.registry import ToolRegistry
from src.types.events import LLMResponse, StreamEvent, TokenUsage

if TYPE_CHECKING:
    from src.ai.base import Provider


_DATA_ROOT = os.path.join(data_dir(), "sessions")
_INDEX_PATH = os.path.join(data_dir(), "sessions_index.json")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_dirs():
    os.makedirs(_DATA_ROOT, exist_ok=True)


def _file_path(session_id: str) -> str:
    safe = session_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_DATA_ROOT, f"{safe}.json")


def _read_index() -> list[dict]:
    if not os.path.isfile(_INDEX_PATH):
        return []
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(entries: list[dict]):
    os.makedirs(os.path.dirname(_INDEX_PATH), exist_ok=True)
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
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
    os.makedirs(_DATA_ROOT, exist_ok=True)
    with open(_file_path(data["id"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
            from src.ai.openai import OpenAIProvider

            cfg = get_config()
            self._provider = OpenAIProvider(
                model=cfg.model,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
            )
        return self._provider

    @provider.setter
    def provider(self, value):
        self._provider = value

    @property
    def registry(self):
        if self._registry is None:
            self._registry = ToolRegistry()
            self._registry.load_local(os.path.join(_PROJECT_ROOT, "src", "tools"))
        return self._registry

    @registry.setter
    def registry(self, value):
        self._registry = value

    @property
    def msgs(self) -> MessageManager:
        if self._msgs is None:
            self._msgs = MessageManager(load_prompt("default"), session=self)
        return self._msgs

    @msgs.setter
    def msgs(self, value: MessageManager):
        self._msgs = value

    def sync_messages(self):
        if self._msgs:
            self._msgs.save()

    def release(self):
        if self._registry:
            self._registry.cleanup()
        self._provider = None
        self._registry = None
        self._msgs = None

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

    def _save_index(self):
        entries = []
        for s in self._index.values():
            entry = dict(s)
            entry.pop("messages", None)
            entries.append(entry)
        _write_index(entries)

    @property
    def current(self):
        if not self._current_id and self._index:
            self._current_id = next(iter(self._index))
        if self._current_id:
            return self.get(self._current_id)
        return None

    @current.setter
    def current(self, session_id: str):
        if session_id in self._index:
            self._current_id = session_id

    def list(self) -> list[Session]:
        result = []
        for sid in self._index:
            s = self.get(sid)
            if s:
                result.append(s)
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
        session.sync_messages()
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

    def get_or_create_current(self) -> Session:
        s = self.current
        if not s:
            s = self.create(path=_PROJECT_ROOT)
        return s


_SESSION_MANAGER: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _SESSION_MANAGER
    if _SESSION_MANAGER is None:
        _SESSION_MANAGER = SessionManager()
    return _SESSION_MANAGER


def run_session_turn(
    session: Session,
    user_input: str,
) -> Iterator[StreamEvent]:
    session.msgs.add_user(user_input)

    while True:
        response = LLMResponse()
        tool_calls_pending = []
        tool_results = []

        try:
            stream = agent_stream(
                session.provider,
                session.msgs.get_api_messages(),
                session.registry.schemas() or None,
                session.registry,
            )

            for event in stream:
                if event.type == "step-start":
                    response = LLMResponse()
                    tool_calls_pending = []
                    tool_results = []
                elif event.type == "reasoning-delta":
                    response.reasoning += event.data
                elif event.type == "text-delta":
                    response.content += event.data
                elif event.type == "tool-call":
                    tool_calls_pending.append(event.data)
                elif event.type in ("tool-result", "tool-error"):
                    tool_results.append(event.data)
                elif event.type == "step-finish":
                    response.finish_reason = event.data.get("finish_reason", "")
                    usage = event.data.get("usage", {})
                    if usage:
                        session.token_usage = TokenUsage(
                            prompt_tokens=session.token_usage.prompt_tokens + usage.get("prompt_tokens", 0),
                            completion_tokens=session.token_usage.completion_tokens + usage.get("completion_tokens", 0),
                            total_tokens=session.token_usage.total_tokens + usage.get("total_tokens", 0),
                        )

                yield event

        except Exception as e:
            yield StreamEvent(type="provider-error", data={"error": str(e), "code": 0})
            session.sync_messages()
            get_session_manager().save(session)
            return

        for tc in tool_calls_pending:
            response.tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["input"]) if isinstance(tc["input"], dict) else str(tc["input"]),
                },
            })

        session.msgs.add_assistant(response)

        for tr in tool_results:
            session.msgs.add_tool(
                tr["id"],
                tr.get("result", tr.get("error", "")),
                tr.get("attachments"),
            )

        if response.finish_reason != "tool_calls":
            break

    session.sync_messages()
    get_session_manager().save(session)


def name_session_from_first_message(session: Session, first_message: str) -> str | None:
    try:
        name = generate_name(session.provider, first_message)
        return name
    except Exception:
        return None
