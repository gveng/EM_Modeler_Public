"""Scene manager: owns all EM objects and the VTK renderer."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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


@dataclass
class ReferencePlane:
    """User-defined reference plane / coordinate system."""
    name:   str
    origin: Tuple[float, float, float]
    normal: Tuple[float, float, float]


class SceneManager:
    """Owns VTK renderer + all EM objects.  One instance per project."""

    def __init__(self, renderer: vtk.vtkRenderer):
        self.renderer = renderer
        self.objects: List[EMObject] = []
        self.selected: Optional[EMObject] = None
        self.selection: List[EMObject] = []   # multi-selection list

        # Reference-plane registry (built-in axis presets + user-defined)
        self.reference_planes: List[ReferencePlane] = [
            ReferencePlane("XY (Z=0)", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            ReferencePlane("XZ (Y=0)", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ReferencePlane("YZ (X=0)", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ]
        self.active_plane: Optional[ReferencePlane] = self.reference_planes[0]

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
        # Determina origine e normale dal piano attivo
        origin = (0.0, 0.0, 0.0)
        normal = None
        plane = self._grid_plane
        if self.active_plane:
            origin = self.active_plane.origin
            normal = self.active_plane.normal
            # Se il piano è XY/XZ/YZ con origine (0,0,0), usa la modalità classica
            if plane == "XY" and normal == (0.0, 0.0, 1.0) and origin == (0.0, 0.0, 0.0):
                normal = None
            elif plane == "XZ" and normal == (0.0, 1.0, 0.0) and origin == (0.0, 0.0, 0.0):
                normal = None
            elif plane == "YZ" and normal == (1.0, 0.0, 0.0) and origin == (0.0, 0.0, 0.0):
                normal = None
        self._grid_actor = build_grid_actor(
            self._grid_size, self._grid_spacing, plane,
            origin=origin, normal=normal
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
            if obj in self.selection:
                self.selection.remove(obj)

    def select(self, obj: Optional[EMObject]) -> None:
        """Single-select: deselects all, selects one."""
        for o in self.selection:
            o.set_selected(False)
        self.selection.clear()
        self.selected = obj
        if obj:
            obj.set_selected(True)
            self.selection = [obj]

    def select_add(self, obj: EMObject) -> None:
        """Add obj to the current multi-selection (toggle if already in it)."""
        if obj in self.selection:
            obj.set_selected(False)
            self.selection.remove(obj)
            self.selected = self.selection[-1] if self.selection else None
        else:
            obj.set_selected(True)
            self.selection.append(obj)
            self.selected = obj

    def select_all(self) -> None:
        """Select every object in the scene."""
        for o in self.objects:
            if o not in self.selection:
                o.set_selected(True)
                self.selection.append(o)
        self.selected = self.selection[-1] if self.selection else None

    def deselect_all(self) -> None:
        for o in self.selection:
            o.set_selected(False)
        self.selection.clear()
        self.selected = None

    def assign_material_to_selection(self, material: str) -> None:
        """Bulk-assign material to all selected objects."""
        for o in self.selection:
            o.material = material
            o.refresh_appearance()

    # ─────────────────────────────────────────────────── reference planes
    def add_reference_plane(self, name: str, origin, normal,
                            make_active: bool = True) -> ReferencePlane:
        """Register a new reference plane.  Auto-numbers duplicate names."""
        used = {p.name for p in self.reference_planes}
        unique = name
        n = 2
        while unique in used:
            unique = f"{name} ({n})"
            n += 1
        plane = ReferencePlane(unique, tuple(origin), tuple(normal))
        self.reference_planes.append(plane)
        if make_active:
            self.active_plane = plane
        return plane

    def remove_reference_plane(self, plane: ReferencePlane) -> None:
        if plane in self.reference_planes:
            self.reference_planes.remove(plane)
            if self.active_plane is plane:
                self.active_plane = self.reference_planes[0] if self.reference_planes else None

    def set_active_plane(self, plane: ReferencePlane) -> None:
        if plane in self.reference_planes:
            self.active_plane = plane

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
