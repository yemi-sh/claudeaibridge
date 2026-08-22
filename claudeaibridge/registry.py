"""
Project registry
-----------------
The allowlist of folders the running server is permitted to touch. This is
the security boundary for the whole tool: the connector (reachable from
claude.ai) can only ever pick from folders that already appear here — it has
no way to add a new one. Only a person with a terminal on this machine can
grow the list, via the CLI (`claudeaibridge add-project`).

Stored as a small JSON file so the CLI (writer) and the running server
(reader) never need to coordinate directly: the server just re-reads this
file on every `list_projects` call, so edits show up immediately with no
restart and no IPC between the two.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "claudeaibridge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return config_dir() / "projects.json"


def _load() -> dict:
    path = _registry_path()
    if not path.exists():
        return {"projects": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"projects": {}}
    data.setdefault("projects", {})
    return data


def _save(data: dict) -> None:
    path = _registry_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def list_projects() -> dict:
    """name -> {"path": str, "added_at": iso-str}, freshly read from disk."""
    return _load()["projects"]


def get_project_path(name: str) -> Optional[str]:
    return list_projects().get(name, {}).get("path")


def add_project(path: str, name: Optional[str] = None) -> str:
    """
    Register a folder as an allowed project root. Returns the name it was
    registered under (auto-derived from the folder name if not given, with a
    numeric suffix on collision).

    Resolves symlinks (`Path.resolve()` with strict=True) so the stored path
    is the real on-disk location, not a symlink that could later be
    repointed at something else.
    """
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"Not a directory: {resolved}")

    data = _load()
    projects = data["projects"]

    base_name = name or resolved.name
    candidate = base_name
    n = 2
    existing_paths = {v["path"] for v in projects.values()}
    if str(resolved) in existing_paths:
        for existing_name, v in projects.items():
            if v["path"] == str(resolved):
                return existing_name
    while candidate in projects:
        candidate = f"{base_name}-{n}"
        n += 1

    projects[candidate] = {
        "path": str(resolved),
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _save(data)
    return candidate


def remove_project(name: str) -> bool:
    data = _load()
    if name in data["projects"]:
        del data["projects"][name]
        _save(data)
        return True
    return False
