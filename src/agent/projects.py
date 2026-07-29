import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Optional

_PROJECTS_ROOT = os.path.join(os.getcwd(), ".lingcode", "projects")
_INDEX_PATH = os.path.join(os.getcwd(), ".lingcode", "projects_index.json")


def _ensure_dirs():
    os.makedirs(_PROJECTS_ROOT, exist_ok=True)


class Project:
    id: str
    name: str
    path: str
    created_at: str
    updated_at: str

    def __init__(self, id: str = "", name: str = "", path: str = ""):
        _ensure_dirs()
        self.id = id or str(uuid.uuid4())[:8]
        self.name = name or f"project-{self.id}"
        self.path = path or os.getcwd()
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    @property
    def project_dir(self) -> str:
        return os.path.join(_PROJECTS_ROOT, self.id)

    @property
    def sessions_dir(self) -> str:
        return os.path.join(self.project_dir, "sessions")

    def ensure_dirs(self):
        os.makedirs(self.sessions_dir, exist_ok=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        p = cls(id=d.get("id", ""), name=d.get("name", ""), path=d.get("path", ""))
        p.created_at = d.get("created_at", p.created_at)
        p.updated_at = d.get("updated_at", p.updated_at)
        return p


def _read_index() -> list[dict]:
    if not os.path.isfile(_INDEX_PATH):
        return []
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(projects: list[dict]):
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


class ProjectManager:
    def __init__(self):
        _ensure_dirs()
        self._projects: dict[str, Project] = {}
        self._current_id: str = ""
        self._load()

    def _load(self):
        self._projects = {}
        for entry in _read_index():
            p = Project.from_dict(entry)
            self._projects[p.id] = p
            p.ensure_dirs()

    def _save_index(self):
        _write_index([p.to_dict() for p in self._projects.values() if p])

    @property
    def current(self) -> Optional[Project]:
        if not self._current_id and self._projects:
            first_id = next(iter(self._projects))
            self._current_id = first_id
        return self._projects.get(self._current_id)

    @current.setter
    def current(self, project_id: str):
        if project_id in self._projects:
            self._current_id = project_id

    def list(self) -> list[Project]:
        return list(self._projects.values())

    def get(self, project_id: str) -> Optional[Project]:
        return self._projects.get(project_id)

    def create(self, name: str = "", path: str = "") -> Project:
        p = Project(name=name, path=path)
        p.ensure_dirs()
        self._projects[p.id] = p
        self._current_id = p.id
        self._save_index()
        return p

    def delete(self, project_id: str) -> bool:
        if project_id not in self._projects:
            return False
        p = self._projects[project_id]
        if os.path.isdir(p.project_dir):
            shutil.rmtree(p.project_dir)
        del self._projects[project_id]
        if self._current_id == project_id:
            self._current_id = next(iter(self._projects)) if self._projects else ""
        self._save_index()
        return True

    def rename(self, project_id: str, name: str) -> bool:
        p = self._projects.get(project_id)
        if not p:
            return False
        p.name = name
        p.updated_at = datetime.now().isoformat()
        self._save_index()
        return True
