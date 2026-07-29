import os
import json
import uuid
import shutil
from datetime import datetime
from pydantic import BaseModel, model_validator

_PROJECTS_ROOT = os.path.join(os.getcwd(), ".lingcode", "projects")
_INDEX_PATH = os.path.join(os.getcwd(), ".lingcode", "projects_index.json")


def _ensure_dirs():
    os.makedirs(_PROJECTS_ROOT, exist_ok=True)


class Project(BaseModel):
    id: str = ""
    name: str = ""
    path: str = ""
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="after")
    def _init_defaults(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.name:
            self.name = f"project-{self.id}"
        if not self.path:
            self.path = os.getcwd()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        return self

    @property
    def project_dir(self) -> str:
        return os.path.join(_PROJECTS_ROOT, self.id)

    @property
    def sessions_dir(self) -> str:
        return os.path.join(self.project_dir, "sessions")

    def ensure_dirs(self):
        os.makedirs(self.sessions_dir, exist_ok=True)


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
            p = Project.model_validate(entry)
            self._projects[p.id] = p
            p.ensure_dirs()

    def _save_index(self):
        _write_index([p.model_dump() for p in self._projects.values()])

    @property
    def current(self) -> Project | None:
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

    def get(self, project_id: str) -> Project | None:
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
