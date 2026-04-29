"""Scene manager: owns all EM objects and the VTK renderer."""
from __future__ import annotations
from typing import Dict, List, Optional

import vtk

from .em_objects import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject
)
from .grid_actor import build_grid_actor, build_axes_widget

_OBJECT_CLASSES = {
    "BoxObject":      BoxObject,
    "CylinderObject": CylinderObject,
    "ConeObject":     ConeObject,
    "SphereObject":   SphereObject,
}


class SceneManager:
    """Owns VTK renderer + all EM objects.  One instance per project."""

    def __init__(self, renderer: vtk.vtkRenderer):
        self.renderer = renderer
        self.objects: List[EMObject] = []
        self.selected: Optional[EMObject] = None

        # Grid state
        self._grid_actor: Optional[vtk.vtkActor] = None
        self._grid_size    = 200.0
        self._grid_spacing = 10.0
        self._grid_plane   = "XY"
        self._rebuild_grid()

        # Renderer background
        renderer.SetBackground(0.12, 0.12, 0.18)
        renderer.GradientBackgroundOn()
        renderer.SetBackground2(0.22, 0.22, 0.32)

    # ─────────────────────────────────────────────────── grid
    def _rebuild_grid(self) -> None:
        if self._grid_actor:
            self.renderer.RemoveActor(self._grid_actor)
        self._grid_actor = build_grid_actor(
            self._grid_size, self._grid_spacing, self._grid_plane
        )
        self.renderer.AddActor(self._grid_actor)

    def update_grid(self, size: float, spacing: float, plane: str) -> None:
        self._grid_size    = size
        self._grid_spacing = spacing
        self._grid_plane   = plane
        self._rebuild_grid()

    @property
    def grid_plane(self) -> str:
        return self._grid_plane

    @property
    def grid_spacing(self) -> float:
        return self._grid_spacing

    # ─────────────────────────────────────────────────── object management
    def add_object(self, obj: EMObject) -> None:
        self.objects.append(obj)
        for actor in obj.all_actors:
            self.renderer.AddActor(actor)

    def remove_object(self, obj: EMObject) -> None:
        if obj in self.objects:
            self.objects.remove(obj)
            for actor in obj.all_actors:
                self.renderer.RemoveActor(actor)
            if self.selected is obj:
                self.selected = None

    def select(self, obj: Optional[EMObject]) -> None:
        if self.selected and self.selected is not obj:
            self.selected.set_selected(False)
        self.selected = obj
        if obj:
            obj.set_selected(True)

    # ─────────────────────────────────────────────────── picking
    def pick_at(self, screen_x: int, screen_y: int) -> Optional[EMObject]:
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.005)
        picker.Pick(screen_x, screen_y, 0, self.renderer)
        picked = picker.GetActor()
        for obj in self.objects:
            if any(a is picked for a in obj.all_actors):
                return obj
        return None

    # ─────────────────────────────────────────────────── query
    def by_material(self) -> Dict[str, List[EMObject]]:
        result: Dict[str, List[EMObject]] = {}
        for obj in self.objects:
            result.setdefault(obj.material, []).append(obj)
        return result

    # ─────────────────────────────────────────────────── serialisation
    def to_json(self) -> list:
        out = []
        for obj in self.objects:
            out.append({
                "type": type(obj).__name__,
                "name": obj.name,
                "params": obj.get_parameters(),
            })
        return out

    def from_json(self, data: list) -> None:
        # Remove all existing objects
        for obj in list(self.objects):
            self.remove_object(obj)
        self.selected = None

        for item in data:
            cls = _OBJECT_CLASSES.get(item["type"])
            if cls is None:
                continue
            p = item["params"]
            t = item["type"]
            try:
                if t == "BoxObject":
                    obj = cls(item["name"],
                              p["X1"], p["Y1"], p["Z1"],
                              p["X2"], p["Y2"], p["Z2"],
                              p.get("Material", "PEC"))
                elif t in ("CylinderObject", "ConeObject"):
                    obj = cls(item["name"],
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["Radius"], p["Height"],
                              p.get("Axis", "Z"), p.get("Material", "PEC"))
                elif t == "SphereObject":
                    obj = cls(item["name"],
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["Radius"], p.get("Material", "PEC"))
                else:
                    continue
                obj.opacity = float(p.get("Opacity", 0.85))
                obj.refresh_appearance()
                self.add_object(obj)
            except (KeyError, TypeError):
                pass

    def clear(self) -> None:
        for obj in list(self.objects):
            self.remove_object(obj)
