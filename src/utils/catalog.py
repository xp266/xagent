import io
import json
import os
import re
import tarfile
import time
import urllib.request
from datetime import datetime

from src.utils.paths import data_dir

_CATALOG_FILENAME = "models_catalog.json"

_PACKAGE = "@opencode-ai/models"

_REFRESH_INTERVAL = 24 * 3600

_SOURCES = (
    ("tarball", f"https://registry.npmjs.org/{_PACKAGE}/latest"),
    ("tarball", f"https://registry.npmmirror.com/{_PACKAGE}/latest"),
    ("file", f"https://cdn.jsdelivr.net/npm/{_PACKAGE}@latest/dist/snapshot.js"),
    ("file", f"https://unpkg.com/{_PACKAGE}@latest/dist/snapshot.js"),
)

_HTTP_TIMEOUT = 30.0

_SNAPSHOT_JSON_RE = re.compile(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)')

_NO_REFRESH_ENV = "XAGENT_NO_CATALOG_REFRESH"


def catalog_path() -> str:
    return os.path.join(data_dir(), _CATALOG_FILENAME)


def catalog_mtime() -> float | None:
    try:
        return os.path.getmtime(catalog_path())
    except OSError:
        return None


_catalog: dict = {}
_catalog_mtime: float | None = None


def load_catalog() -> dict:
    global _catalog, _catalog_mtime
    mtime = catalog_mtime()
    if mtime is not None and mtime == _catalog_mtime:
        return _catalog
    _catalog_mtime = mtime
    if mtime is None:
        _catalog = {}
        return _catalog
    try:
        with open(catalog_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        providers = data.get("providers") if isinstance(data, dict) else None
        _catalog = providers if isinstance(providers, dict) else {}
    except (OSError, json.JSONDecodeError):
        _catalog = {}
    return _catalog


def catalog_stale() -> bool:
    mtime = catalog_mtime()
    if mtime is None:
        return True
    return time.time() - mtime >= _REFRESH_INTERVAL


def ensure_catalog() -> None:
    if os.environ.get(_NO_REFRESH_ENV):
        return
    if not catalog_stale():
        return
    if not fetch_catalog_sync():
        _log_failure()


def _log_failure() -> None:
    try:
        path = os.path.join(data_dir(), "errors.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] catalog refresh failed\n")
    except OSError:
        pass


def fetch_catalog_sync() -> bool:
    for kind, url in _SOURCES:
        try:
            payload = _fetch(kind, url)
        except Exception:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
            continue
        _write_catalog(payload)
        return True
    return False


def _fetch(kind: str, url: str) -> dict:
    if kind == "file":
        return _parse_snapshot(_http_get(url))
    meta = json.loads(_http_get(url).decode("utf-8", "replace"))
    tarball = meta.get("dist", {}).get("tarball")
    if not tarball:
        raise ValueError("tarball url missing")
    raw = _http_get(tarball)
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    member = tf.extractfile("package/dist/snapshot.js")
    if member is None:
        raise ValueError("snapshot.js missing")
    return _parse_snapshot(member.read().decode("utf-8", "replace"))


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "xagent"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read()


def _parse_snapshot(text: str) -> dict:
    m = _SNAPSHOT_JSON_RE.search(text)
    if m is None:
        raise ValueError("snapshot payload not found")
    payload = json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))
    if not isinstance(payload, dict):
        raise ValueError("invalid snapshot payload")
    return payload


def _write_catalog(payload: dict) -> None:
    path = catalog_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {
        "_meta": {
            "generatedAt": payload.get("generatedAt", ""),
            "fetched_at": datetime.now().isoformat(),
        },
        "providers": payload.get("providers", {}),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)