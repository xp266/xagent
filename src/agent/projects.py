import os
import json
import uuid
import shutil
from datetime import datetime
from pydantic import BaseModel, model_validator

from src.utils.paths import data_dir


_DATA_ROOT = os.path.join(data_dir(), "projects")
_INDEX_PATH = os.path.join(data_dir(), "projects_index.json")


def _ensure_dirs():
    os.makedirs(_DATA_ROOT, exist_ok=True)


class Project(BaseModel):
    id: str = ""
    name: str = ""
    path: str = ""
    messages: list = []
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="after")
    def _init_defaults(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.name:
            self.name = "New Session"
        if not self.path:
            self.path = os.getcwd()
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        return self

    @property
    def project_path(self) -> str:
        safe = self.id.replace("/", "_").replace("\\", "_")
        return os.path.join(_DATA_ROOT, f"{safe}.json")


def _read_index() -> list[dict]:
    if not os.path.isfile(_INDEX_PATH):
        return []
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(projects: list[dict]):
    os.makedirs(os.path.dirname(_INDEX_PATH), exist_ok=True)
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


def _read_project(project_id: str) -> dict | None:
    safe = project_id.replace("/", "_").replace("\\", "_")
    path = os.path.join(_DATA_ROOT, f"{safe}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_project(data: dict):
    safe = data["id"].replace("/", "_").replace("\\", "_")
    path = os.path.join(_DATA_ROOT, f"{safe}.json")
    os.makedirs(_DATA_ROOT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ProjectManager:
    def __init__(self):
        _ensure_dirs()
        self._index: dict[str, Project] = {}
        self._current_id: str = ""
        self._load_index()

    def _load_index(self):
        self._index = {}
        for entry in _read_index():
            p = Project.model_validate(entry)
            self._index[p.id] = p

    def _save_index(self):
        entries = []
        for p in self._index.values():
            d = p.model_dump()
            d.pop("messages", None)
            entries.append(d)
        _write_index(entries)

    @property
    def current(self) -> Project | None:
        if not self._current_id and self._index:
            self._current_id = next(iter(self._index))
        if self._current_id:
            return self.get(self._current_id)
        return None

    @current.setter
    def current(self, project_id: str):
        if project_id in self._index:
            self._current_id = project_id

    def list(self) -> list[Project]:
        return list(self._index.values())

    def get(self, project_id: str) -> Project | None:
        if project_id not in self._index:
            return None
        data = _read_project(project_id)
        if data is None:
            return self._index.get(project_id)
        return Project.model_validate(data)

    def create(self, name: str = "", path: str = "") -> Project:
        p = Project(name=name, path=path)
        _write_project(p.model_dump())
        self._index[p.id] = Project(
            id=p.id, name=p.name, path=p.path,
            created_at=p.created_at, updated_at=p.updated_at,
        )
        self._current_id = p.id
        self._save_index()
        return p

    def delete(self, project_id: str) -> bool:
        if project_id not in self._index:
            return False
        safe = project_id.replace("/", "_").replace("\\", "_")
        path = os.path.join(_DATA_ROOT, f"{safe}.json")
        if os.path.isfile(path):
            os.remove(path)
        del self._index[project_id]
        if self._current_id == project_id:
            self._current_id = next(iter(self._index)) if self._index else ""
        self._save_index()
        return True

    def rename(self, project_id: str, name: str) -> bool:
        p = self._index.get(project_id)
        if not p:
            return False
        p.name = name
        p.updated_at = datetime.now().isoformat()
        data = _read_project(project_id)
        if data:
            data["name"] = name
            data["updated_at"] = p.updated_at
            _write_project(data)
        self._save_index()
        return True

    def save(self, project: Project) -> None:
        project.updated_at = datetime.now().isoformat()
        _write_project(project.model_dump())
        idx = self._index.get(project.id)
        if idx:
            idx.name = project.name
            idx.path = project.path
            idx.updated_at = project.updated_at
        else:
            self._index[project.id] = Project(
                id=project.id, name=project.name, path=project.path,
                created_at=project.created_at, updated_at=project.updated_at,
            )
        self._save_index()
