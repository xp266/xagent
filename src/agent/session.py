import os
import json
import uuid
from datetime import datetime

_DEFAULT_DATA_DIR = os.path.join(os.getcwd(), ".lingcode")
_DEFAULT_SESSIONS_DIR = os.path.join(_DEFAULT_DATA_DIR, "sessions")


def _ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)


class SessionStore:
    def __init__(self, session_id: str = "", sessions_dir: str = ""):
        self._sessions_dir = sessions_dir or _DEFAULT_SESSIONS_DIR
        _ensure_dir(self._sessions_dir)
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.messages: list = []
        self.agent_name: str = ""
        self.created_at: str = ""
        self.updated_at: str = ""
        self._load()

    def _session_path(self) -> str:
        safe = self.session_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self._sessions_dir, f"{safe}.json")

    def _load(self):
        path = self._session_path()
        if not os.path.isfile(path):
            self.created_at = datetime.now().isoformat()
            self.updated_at = self.created_at
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.messages = data.get("messages", [])
            self.agent_name = data.get("agent_name", "")
            self.created_at = data.get("created_at", "")
            self.updated_at = data.get("updated_at", "")
        except (json.JSONDecodeError, OSError):
            self.created_at = datetime.now().isoformat()
            self.updated_at = self.created_at

    def save(self):
        self.updated_at = datetime.now().isoformat()
        data = {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        with open(self._session_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear(self):
        self.messages = []
        self.agent_name = ""
        self.session_id = str(uuid.uuid4())[:8]
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    def list_sessions(self) -> list:
        result = []
        if not os.path.isdir(self._sessions_dir):
            return result
        for fname in os.listdir(self._sessions_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._sessions_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "session_id": data.get("session_id", fname[:-5]),
                    "agent_name": data.get("agent_name", ""),
                    "message_count": len(data.get("messages", [])),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result
