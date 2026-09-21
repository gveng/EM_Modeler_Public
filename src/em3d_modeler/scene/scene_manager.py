"""Scene manager: owns all EM objects and the VTK renderer."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import vtk

from .em_objects import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject, MeshObject,
    PlateObject, PyramidObject, WedgeObject, TorusObject, EllipsoidObject
)
from .grid_actor import build_grid_actor, build_axes_widget

_OBJECT_CLASSES = {
    "BoxObject":      BoxObject,
    "CylinderObject": CylinderObject,
    "ConeObject":     ConeObject,
    "SphereObject":   SphereObject,
    "MeshObject":     MeshObject,
    "PlateObject":    PlateObject,
    "PyramidObject":  PyramidObject,
    "WedgeObject":    WedgeObject,
    "TorusObject":    TorusObject,
    "EllipsoidObject": EllipsoidObject,
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

    def set_visibility(self, objects: List[EMObject], visible: bool) -> None:
        for o in objects:
            o.set_visible(visible)

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

    # ─────────────────────────────────────────────────── reference plane serialization
    _DEFAULT_PLANE_NAMES = ("XY (Z=0)", "XZ (Y=0)", "YZ (X=0)")

    def reference_planes_to_json(self) -> list:
        """Serialize only user-defined planes (built-in axis presets are recreated on load)."""
        return [
            {"name": p.name, "origin": list(p.origin), "normal": list(p.normal)}
            for p in self.reference_planes
            if p.name not in self._DEFAULT_PLANE_NAMES
        ]

    def reference_planes_from_json(self, data: list,
                                   active_name: Optional[str] = None) -> None:
        """Restore user-defined planes; keeps built-in axis presets at the top."""
        # Reset to built-ins
        self.reference_planes = [
            ReferencePlane("XY (Z=0)", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            ReferencePlane("XZ (Y=0)", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ReferencePlane("YZ (X=0)", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ]
        for item in data or []:
            try:
                self.reference_planes.append(ReferencePlane(
                    str(item["name"]),
                    tuple(item["origin"]),
                    tuple(item["normal"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        # Restore active plane
        if active_name:
            for p in self.reference_planes:
                if p.name == active_name:
                    self.active_plane = p
                    break
            else:
                self.active_plane = self.reference_planes[0]
        else:
            self.active_plane = self.reference_planes[0]
        self._rebuild_grid()

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
    @staticmethod
    def _polydata_to_json(polydata: Optional[vtk.vtkPolyData]) -> Dict[str, Any]:
        """Serialize vtkPolyData into JSON-safe points + polygon indices."""
        if polydata is None:
            return {"points": [], "polys": []}

        points: List[List[float]] = []
        vtk_points = polydata.GetPoints()
        if vtk_points is not None:
            for i in range(vtk_points.GetNumberOfPoints()):
                x, y, z = vtk_points.GetPoint(i)
                points.append([float(x), float(y), float(z)])

        polys: List[List[int]] = []
        vtk_polys = polydata.GetPolys()
        if vtk_polys is not None:
            vtk_polys.InitTraversal()
            ids = vtk.vtkIdList()
            while vtk_polys.GetNextCell(ids):
                if ids.GetNumberOfIds() >= 3:
                    polys.append([int(ids.GetId(j)) for j in range(ids.GetNumberOfIds())])

        return {"points": points, "polys": polys}

    @staticmethod
    def _polydata_from_json(data: Any) -> vtk.vtkPolyData:
        """Deserialize JSON-safe points + polygon indices into vtkPolyData."""
        poly = vtk.vtkPolyData()
        if not isinstance(data, dict):
            return poly

        raw_points = data.get("points", [])
        raw_polys = data.get("polys", [])
        if not isinstance(raw_points, list) or not isinstance(raw_polys, list):
            return poly

        points = vtk.vtkPoints()
        for p in raw_points:
            if isinstance(p, (list, tuple)) and len(p) == 3:
                try:
                    points.InsertNextPoint(float(p[0]), float(p[1]), float(p[2]))
                except (TypeError, ValueError):
                    continue

        polys = vtk.vtkCellArray()
        n_points = points.GetNumberOfPoints()
        for cell in raw_polys:
            if not isinstance(cell, list) or len(cell) < 3:
                continue
            valid_ids: List[int] = []
            bad_cell = False
            for idx in cell:
                try:
                    ii = int(idx)
                except (TypeError, ValueError):
                    bad_cell = True
                    break
                if ii < 0 or ii >= n_points:
                    bad_cell = True
                    break
                valid_ids.append(ii)
            if bad_cell or len(valid_ids) < 3:
                continue
            polys.InsertNextCell(len(valid_ids))
            for ii in valid_ids:
                polys.InsertCellPoint(ii)

        poly.SetPoints(points)
        poly.SetPolys(polys)
        return poly

    def to_json(self) -> list:
        out = []
        for obj in self.objects:
            item = {
                "type": type(obj).__name__,
                "name": obj.name,
                "params": obj.get_parameters(),
                "visible": obj.is_visible(),
                "is_model": bool(getattr(obj, "is_model", True)),
                "param_formulas": dict(getattr(obj, "param_formulas", {}) or {}),
                "creation_history": dict(getattr(obj, "creation_history", {}) or {}),
                "creation_reference_error": str(getattr(obj, "creation_reference_error", "")),
                **obj.to_json_state(),
            }
            if obj.actor is not None:
                item["actor_transform"] = {
                    "origin": list(obj.actor.GetOrigin()),
                    "position": list(obj.actor.GetPosition()),
                    "orientation": list(obj.actor.GetOrientation()),
                    "scale": list(obj.actor.GetScale()),
                }
            if isinstance(obj, MeshObject):
                mesh_poly = None
                if obj.actor is not None and obj.actor.GetMapper() is not None:
                    mesh_poly = obj.actor.GetMapper().GetInput()
                item["mesh"] = self._polydata_to_json(mesh_poly)
            out.append(item)
        return out

    def from_json(self, data: list) -> None:
        # Remove all existing objects
        for obj in list(self.objects):
            self.remove_object(obj)
        self.selected = None

        pending_boolean_sources: List[Tuple[MeshObject, List[str]]] = []

        for item in data:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            cls = _OBJECT_CLASSES.get(t)
            if cls is None:
                continue
            p = item.get("params", {})
            if not isinstance(p, dict):
                p = {}
            try:
                if t == "BoxObject":
                    obj = cls(item.get("name", ""),
                              p["X1"], p["Y1"], p["Z1"],
                              p["X2"], p["Y2"], p["Z2"],
                              p.get("Material", "PEC"))
                elif t in ("CylinderObject", "ConeObject"):
                    obj = cls(item.get("name", ""),
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["Radius"], p["Height"],
                              p.get("Axis", "Z"), p.get("Material", "PEC"))
                elif t == "SphereObject":
                    obj = cls(item.get("name", ""),
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["Radius"], p.get("Material", "PEC"))
                elif t == "MeshObject":
                    mesh_blob = item.get("mesh")
                    mesh_poly = self._polydata_from_json(mesh_blob)
                    obj = cls(item.get("name", ""),
                              mesh_poly,
                              p.get("Material", "PEC"),
                              step_source_path=p.get("StepSourcePath"),
                              step_solid_name=p.get("StepSolidName"),
                              boolean_op=p.get("BooleanOperation"),
                              boolean_source_names=p.get("BooleanSourceNames"),
                              boolean_sources_data=p.get("BooleanSourcesData"))
                    if hasattr(obj, "set_parameters"):
                        obj.set_parameters(p)
                    if (
                        "StepGeometryModified" not in p
                        and mesh_blob is not None
                        and str(p.get("StepSourcePath", "") or "").strip()
                    ):
                        obj.step_geometry_modified = True
                elif t == "PlateObject":
                    obj = cls(item.get("name", ""),
                              p["X1"], p["Y1"], p["Z1"],
                              p["X2"], p["Y2"], p["Z2"],
                              p.get("Material", "PEC"))
                elif t == "PyramidObject":
                    obj = cls(item.get("name", ""),
                              p["BaseX1"], p["BaseY1"], p["BaseZ"],
                              p["BaseX2"], p["BaseY2"],
                              p["ApexZ"], p.get("Material", "PEC"))
                elif t == "WedgeObject":
                    obj = cls(item.get("name", ""),
                              p["X1"], p["Y1"], p["Z1"],
                              p["X2"], p["Y2"], p["Z2"],
                              p["X3"], p["Y3"], p["ZHeight"],
                              p.get("Material", "PEC"))
                elif t == "TorusObject":
                    obj = cls(item.get("name", ""),
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["MajorRadius"], p["MinorRadius"],
                              p.get("Material", "PEC"))
                elif t == "EllipsoidObject":
                    obj = cls(item.get("name", ""),
                              p["CenterX"], p["CenterY"], p["CenterZ"],
                              p["RadiusX"], p["RadiusY"], p["RadiusZ"],
                              p.get("Material", "PEC"))
                else:
                    continue
                obj.opacity = float(p.get("Opacity", 0.85))
                obj.from_json_state(item)
                # Restore custom color if present
                if "Color" in p:
                    col_str = str(p["Color"]).strip()
                    if col_str.startswith("#") and len(col_str) == 7:
                        try:
                            r = int(col_str[1:3], 16) / 255.0
                            g = int(col_str[3:5], 16) / 255.0
                            b = int(col_str[5:7], 16) / 255.0
                            obj.custom_color = (r, g, b)
                        except ValueError:
                            pass
                obj.refresh_appearance()
                obj.set_visible(bool(item.get("visible", True)))
                obj.is_model = bool(item.get("is_model", True))
                formulas = item.get("param_formulas", {})
                obj.param_formulas = dict(formulas) if isinstance(formulas, dict) else {}
                history = item.get("creation_history", {})
                obj.creation_history = dict(history) if isinstance(history, dict) else {}
                obj.creation_reference_error = str(item.get("creation_reference_error", ""))
                self.add_object(obj)
                transform = item.get("actor_transform", {})
                if isinstance(transform, dict) and obj.actor is not None:
                    origin = transform.get("origin")
                    position = transform.get("position")
                    orientation = transform.get("orientation")
                    scale = transform.get("scale")
                    if isinstance(origin, (list, tuple)) and len(origin) == 3:
                        obj.actor.SetOrigin(*[float(value) for value in origin])
                    if isinstance(position, (list, tuple)) and len(position) == 3:
                        obj.actor.SetPosition(*[float(value) for value in position])
                    if isinstance(orientation, (list, tuple)) and len(orientation) == 3:
                        obj.actor.SetOrientation(*[float(value) for value in orientation])
                    if isinstance(scale, (list, tuple)) and len(scale) == 3:
                        obj.actor.SetScale(*[float(value) for value in scale])

                if isinstance(obj, MeshObject) and obj.boolean_source_names:
                    pending_boolean_sources.append((obj, list(obj.boolean_source_names)))
            except (KeyError, TypeError, ValueError):
                pass

        if pending_boolean_sources:
            by_name = {o.name: o for o in self.objects}
            for mesh_obj, names in pending_boolean_sources:
                mesh_obj.source_objects = [by_name[n] for n in names if n in by_name]

    def clear(self) -> None:
        for obj in list(self.objects):
            self.remove_object(obj)
