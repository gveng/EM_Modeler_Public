"""Project file save / load (JSON format)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict


class ProjectFile:
    """Serialises / deserialises the full project state."""

    VERSION = "0.1"

    @staticmethod
    def save(
        path: str | Path,
        project_name: str,
        settings: Dict[str, Any],
        objects_json: list,
        units: str = "mm",
        grid_size: float = 200.0,
        grid_spacing: float = 10.0,
        grid_plane: str = "XY",
    ) -> None:
        data = {
            "version":      ProjectFile.VERSION,
            "project_name": project_name,
            "units":        units,
            "grid": {
                "size":    grid_size,
                "spacing": grid_spacing,
                "plane":   grid_plane,
            },
            "emerge_settings": settings,
            "objects":     objects_json,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> Dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
