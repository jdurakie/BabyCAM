"""Persist the most recently opened DXF drawings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .tool_library import library_path


MAX_RECENT_FILES = 30


def recent_files_path() -> Path:
    return library_path().with_name("recent_files.json")


def _normalized(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _unique_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not isinstance(path, str) or not path:
            continue
        normalized = _normalized(path)
        key = os.path.normcase(normalized)
        if key not in seen:
            result.append(normalized)
            seen.add(key)
        if len(result) == MAX_RECENT_FILES:
            break
    return result


def load_recent_files(path: Path | None = None) -> list[str]:
    source = path or recent_files_path()
    if not source.exists():
        return []
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Recent files must contain a list of paths.")
    return _unique_paths(data)


def save_recent_files(paths: list[str], path: Path | None = None) -> None:
    destination = path or recent_files_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(_unique_paths(paths), indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def add_recent_file(paths: list[str], path: str) -> list[str]:
    return _unique_paths([path, *paths])
