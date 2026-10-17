"""Global endmill presets; jobs receive independent copies."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .model import Tool


def library_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "babycam" / "tools.json"


def load_tools(path: Path | None = None) -> list[Tool]:
    source = path or library_path()
    if not source.exists():
        return []
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Tool library must contain a list of endmills.")
    return [Tool(**item) for item in data]


def save_tools(tools: list[Tool], path: Path | None = None) -> None:
    destination = path or library_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(tool) for tool in tools], indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(destination)


def copy_tool(tool: Tool) -> Tool:
    return replace(tool)
