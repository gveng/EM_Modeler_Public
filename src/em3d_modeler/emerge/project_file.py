# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

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
        reference_planes: list | None = None,
        active_plane_name: str | None = None,
        project_materials: list | None = None,
        global_material_db_path: str | None = None,
        camera: dict | None = None,
        display_settings: dict | None = None,
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
            "reference_planes":  reference_planes or [],
            "active_plane_name": active_plane_name,
            "project_materials": project_materials or [],
            "global_material_db_path": global_material_db_path,
            "camera": camera or {},
            "display_settings": display_settings or {},
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> Dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
