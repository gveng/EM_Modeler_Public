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

"""Scene manager: owns all EM objects and the VTK renderer."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import vtk

from .em_objects import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject, MeshObject,
    PlateObject, PyramidObject, WedgeObject, TorusObject, EllipsoidObject,
    ExtrudedObject, RevolvedObject, SweptSurfaceObject,
)
from .grid_actor import build_grid_actor, build_axes_widget, project_bounds_to_plane

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
    "ExtrudedObject": ExtrudedObject,
    "RevolvedObject": RevolvedObject,
    "SweptSurfaceObject": SweptSurfaceObject,
}


@dataclass
class ReferencePlane:
    """User-defined reference plane / coordinate system."""
    name:   str
    origin: Tuple[float, float, float]
    normal: Tuple[float, float, float]


@dataclass(eq=False)
class SceneObjectGroup:
    """Organizational group; it does not combine or transform its objects."""

    name: str
    objects: List[EMObject] = field(default_factory=list)
    subgroups: List[SceneObjectGroup] = field(default_factory=list)
    group_id: str = field(default_factory=lambda: uuid4().hex)
    priority: int | None = None


class SceneManager:
    """Owns VTK renderer + all EM objects.  One instance per project."""

    def __init__(self, renderer: vtk.vtkRenderer):
        self.renderer = renderer
        self.objects: List[EMObject] = []
        self.object_groups: List[SceneObjectGroup] = []
        self._object_added_callback: Callable[[EMObject], None] | None = None
        self.selected: Optional[EMObject] = None
        self.selection: List[EMObject] = []   # multi-selection list
        self._mesh_json_cache: Dict[int, Tuple[vtk.vtkPolyData, int, Dict[str, Any]]] = {}

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
        self._adaptive_grid = False
        self._adaptive_grid_margin = 20.0
        self._rebuild_grid()

        # Renderer background
        renderer.SetBackground(0.12, 0.12, 0.18)
        renderer.GradientBackgroundOn()
        renderer.SetBackground2(0.22, 0.22, 0.32)

    # ─────────────────────────────────────────────────── grid
    def _rebuild_grid(self) -> None:
        grid_visible = bool(self._grid_actor.GetVisibility()) if self._grid_actor else True
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
        extents = None
        if self._adaptive_grid and self.active_plane is not None:
            projected = [
                project_bounds_to_plane(
                    obj.actor.GetBounds(),
                    self.active_plane.origin,
                    self.active_plane.normal,
                    self._adaptive_grid_margin,
                )
                for obj in self.objects
                if obj.is_visible() and getattr(obj, "actor", None) is not None
            ]
            projected = [bounds for bounds in projected if bounds is not None]
            if projected:
                extents = (
                    min(bounds[0] for bounds in projected),
                    max(bounds[1] for bounds in projected),
                    min(bounds[2] for bounds in projected),
                    max(bounds[3] for bounds in projected),
                )
        self._grid_actor = build_grid_actor(
            self._grid_size, self._grid_spacing, plane,
            origin=origin, normal=normal, extents=extents,
        )
        self._grid_actor.SetVisibility(grid_visible)
        self.renderer.AddActor(self._grid_actor)

    def set_adaptive_grid(self, enabled: bool, margin: float | None = None) -> None:
        self._adaptive_grid = bool(enabled)
        if margin is not None:
            self._adaptive_grid_margin = max(0.0, float(margin))
        self._rebuild_grid()

    def refresh_adaptive_grid(self) -> None:
        if self._adaptive_grid:
            self._rebuild_grid()

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
    def set_object_added_callback(
        self, callback: Callable[[EMObject], None] | None
    ) -> None:
        """Set a temporary callback invoked before a new object's actors are added."""
        self._object_added_callback = callback

    def add_object(self, obj: EMObject, *, preserve_name: bool = False) -> None:
        used_names = {str(existing.name).strip() for existing in self.objects}
        requested_name = str(getattr(obj, "name", "")).strip() or type(obj).__name__
        match = re.fullmatch(r"(.+?)_(\d+)", requested_name)
        if requested_name in used_names or (match and not preserve_name):
            prefix = match.group(1) if match else requested_name
            index = 1
            while f"{prefix}_{index}" in used_names:
                index += 1
            obj.name = f"{prefix}_{index}"
        self.objects.append(obj)
        if self._object_added_callback is not None:
            self._object_added_callback(obj)
        for actor in obj.all_actors:
            self.renderer.AddActor(actor)
        self.refresh_adaptive_grid()

    def remove_object(self, obj: EMObject) -> None:
        if obj in self.objects:
            for group in self.object_groups:
                if obj in group.objects:
                    group.objects.remove(obj)
                for subgroup in group.subgroups:
                    if obj in subgroup.objects:
                        subgroup.objects.remove(obj)
            self.objects.remove(obj)
            for actor in obj.all_actors:
                self.renderer.RemoveActor(actor)
            if self.selected is obj:
                self.selected = None
            if obj in self.selection:
                self.selection.remove(obj)
            self.refresh_adaptive_grid()

    @staticmethod
    def _normalized_group_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("Group name must be text.")
        normalized = name.strip()
        if not normalized:
            raise ValueError("Group name cannot be empty.")
        return normalized

    @staticmethod
    def _unique_objects(objects: List[EMObject]) -> List[EMObject]:
        return list(dict.fromkeys(objects))

    def _validate_group_objects(self, objects: List[EMObject]) -> List[EMObject]:
        unique = self._unique_objects(list(objects))
        if not unique:
            raise ValueError("Select at least one scene object.")
        if any(obj not in self.objects for obj in unique):
            raise ValueError("Groups can only contain objects currently in the scene.")
        return unique

    @staticmethod
    def _group_members(group: SceneObjectGroup) -> list[EMObject]:
        return [
            *group.objects,
            *(obj for subgroup in group.subgroups for obj in subgroup.objects),
        ]

    @staticmethod
    def _validate_single_group_material(objects: list[EMObject]) -> None:
        materials = {
            str(getattr(obj, "material", "")).strip()
            for obj in objects
            if str(getattr(obj, "material", "")).strip()
        }
        if len(materials) > 1:
            raise ValueError(
                "All objects in a group, including its subgroups, must use the same material."
            )

    def _unassign_group_objects(self, objects: List[EMObject]) -> None:
        moving = set(objects)
        for group in self.object_groups:
            group.objects[:] = [obj for obj in group.objects if obj not in moving]
            for subgroup in group.subgroups:
                subgroup.objects[:] = [
                    obj for obj in subgroup.objects if obj not in moving
                ]

    def create_object_group(
        self,
        name: str,
        objects: List[EMObject],
    ) -> SceneObjectGroup:
        name = self._normalized_group_name(name)
        if any(group.name.casefold() == name.casefold() for group in self.object_groups):
            raise ValueError(f"A group named '{name}' already exists.")
        members = self._validate_group_objects(objects)
        self._validate_single_group_material(members)
        self._unassign_group_objects(members)
        group = SceneObjectGroup(name=name, objects=members)
        self.object_groups.append(group)
        return group

    def create_object_subgroup(
        self,
        parent: SceneObjectGroup,
        name: str,
        objects: List[EMObject] | None = None,
    ) -> SceneObjectGroup:
        if parent not in self.object_groups:
            raise ValueError("The parent group is no longer in the scene.")
        name = self._normalized_group_name(name)
        if any(
            subgroup.name.casefold() == name.casefold()
            for subgroup in parent.subgroups
        ):
            raise ValueError(
                f"A subgroup named '{name}' already exists in group '{parent.name}'."
            )
        members = self._validate_group_objects(list(objects or [])) if objects else []
        self._validate_single_group_material([*self._group_members(parent), *members])
        self._unassign_group_objects(members)
        subgroup = SceneObjectGroup(name=name, objects=members)
        parent.subgroups.append(subgroup)
        return subgroup

    def assign_objects_to_group(
        self,
        objects: List[EMObject],
        group: SceneObjectGroup,
        subgroup: SceneObjectGroup | None = None,
    ) -> None:
        if group not in self.object_groups:
            raise ValueError("The target group is no longer in the scene.")
        if subgroup is not None and subgroup not in group.subgroups:
            raise ValueError("The target subgroup is not a child of that group.")
        members = self._validate_group_objects(objects)
        moving = set(members)
        remaining = [
            obj for obj in self._group_members(group)
            if obj not in moving
        ]
        self._validate_single_group_material([*remaining, *members])
        self._unassign_group_objects(members)
        (subgroup.objects if subgroup is not None else group.objects).extend(members)

    def delete_object_subgroup(
        self,
        parent: SceneObjectGroup,
        subgroup: SceneObjectGroup,
    ) -> None:
        if parent not in self.object_groups or subgroup not in parent.subgroups:
            raise ValueError("The subgroup is no longer in the scene.")
        parent.objects.extend(subgroup.objects)
        parent.subgroups.remove(subgroup)

    def delete_object_group(self, group: SceneObjectGroup) -> None:
        if group not in self.object_groups:
            raise ValueError("The group is no longer in the scene.")
        self.object_groups.remove(group)

    def group_for_object(
        self,
        obj: EMObject,
    ) -> tuple[SceneObjectGroup, SceneObjectGroup | None] | None:
        for group in self.object_groups:
            if obj in group.objects:
                return group, None
            for subgroup in group.subgroups:
                if obj in subgroup.objects:
                    return group, subgroup
        return None

    def groups_with_mixed_materials(self) -> list[str]:
        return [
            group.name
            for group in self.object_groups
            if len({
                str(getattr(obj, "material", "")).strip()
                for obj in self._group_members(group)
                if str(getattr(obj, "material", "")).strip()
            }) > 1
        ]

    def material_assignment_error(
        self,
        objects: List[EMObject],
        material: str,
    ) -> str | None:
        target_material = str(material).strip()
        if not target_material:
            return "Select a material before applying it."
        selected = set(objects)
        affected_groups = {
            membership[0]
            for obj in objects
            if (membership := self.group_for_object(obj)) is not None
        }
        for group in affected_groups:
            projected_materials = set()
            for obj in self._group_members(group):
                obj_material = (
                    target_material if obj in selected
                    else str(obj.material).strip()
                )
                if obj_material:
                    projected_materials.add(obj_material)
            if len(projected_materials) > 1:
                return (
                    f"Group '{group.name}' must contain objects with one material. "
                    "Apply the same material to all group members, or ungroup the object first."
                )
        return None

    def set_object_group_priority(
        self,
        group: SceneObjectGroup,
        priority: int,
    ) -> SceneObjectGroup:
        root = group if group in self.object_groups else next(
            (parent for parent in self.object_groups if group in parent.subgroups),
            None,
        )
        if root is None:
            raise ValueError("The target group is no longer in the scene.")
        self._validate_single_group_material(self._group_members(root))
        root.priority = int(priority)
        return root

    def group_object_priorities(self) -> dict[str, int]:
        return {
            obj.name: int(group.priority)
            for group in self.object_groups
            if group.priority is not None
            for obj in self._group_members(group)
        }

    def object_groups_to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "id": group.group_id,
                "name": group.name,
                "priority": group.priority,
                "objects": [obj.name for obj in group.objects],
                "subgroups": [
                    {
                        "id": subgroup.group_id,
                        "name": subgroup.name,
                        "objects": [obj.name for obj in subgroup.objects],
                    }
                    for subgroup in group.subgroups
                ],
            }
            for group in self.object_groups
        ]

    def object_groups_from_json(self, data: Any) -> None:
        if not isinstance(data, list):
            raise ValueError("Project object_groups must be a list.")
        object_by_name = {obj.name: obj for obj in self.objects}
        restored: list[SceneObjectGroup] = []
        used_group_ids: set[str] = set()
        used_object_ids: set[int] = set()

        def read_members(raw_names: Any, label: str) -> list[EMObject]:
            if not isinstance(raw_names, list):
                raise ValueError(f"{label} objects must be a list.")
            members = []
            for raw_name in raw_names:
                name = str(raw_name)
                obj = object_by_name.get(name)
                if obj is None:
                    raise ValueError(
                        f"{label} references missing scene object '{name}'."
                    )
                if id(obj) in used_object_ids:
                    raise ValueError(
                        f"Scene object '{name}' belongs to more than one group."
                    )
                used_object_ids.add(id(obj))
                members.append(obj)
            return members

        def read_id(item: dict[str, Any], label: str) -> str:
            group_id = str(item.get("id") or uuid4().hex)
            if group_id in used_group_ids:
                raise ValueError(f"Duplicate group identifier in {label}.")
            used_group_ids.add(group_id)
            return group_id

        root_names: set[str] = set()
        for index, raw_group in enumerate(data):
            label = f"Group {index + 1}"
            if not isinstance(raw_group, dict):
                raise ValueError(f"{label} must be an object.")
            name = self._normalized_group_name(raw_group.get("name", ""))
            normalized = name.casefold()
            if normalized in root_names:
                raise ValueError(f"Duplicate group name '{name}'.")
            root_names.add(normalized)
            group = SceneObjectGroup(
                name=name,
                objects=read_members(raw_group.get("objects", []), label),
                group_id=read_id(raw_group, label),
                priority=(
                    int(raw_group["priority"])
                    if raw_group.get("priority") is not None
                    else None
                ),
            )
            raw_subgroups = raw_group.get("subgroups", [])
            if not isinstance(raw_subgroups, list):
                raise ValueError(f"Subgroups in '{name}' must be a list.")
            subgroup_names: set[str] = set()
            for subgroup_index, raw_subgroup in enumerate(raw_subgroups):
                subgroup_label = f"Subgroup {subgroup_index + 1} in '{name}'"
                if not isinstance(raw_subgroup, dict):
                    raise ValueError(f"{subgroup_label} must be an object.")
                subgroup_name = self._normalized_group_name(
                    raw_subgroup.get("name", "")
                )
                subgroup_key = subgroup_name.casefold()
                if subgroup_key in subgroup_names:
                    raise ValueError(
                        f"Duplicate subgroup name '{subgroup_name}' in '{name}'."
                    )
                subgroup_names.add(subgroup_key)
                group.subgroups.append(SceneObjectGroup(
                    name=subgroup_name,
                    objects=read_members(
                        raw_subgroup.get("objects", []),
                        subgroup_label,
                    ),
                    group_id=read_id(raw_subgroup, subgroup_label),
                ))
            restored.append(group)
        self.object_groups = restored

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
        error = self.material_assignment_error(self.selection, material)
        if error:
            raise ValueError(error)
        for o in self.selection:
            o.material = material
            o.refresh_appearance()

    def set_visibility(self, objects: List[EMObject], visible: bool) -> None:
        for o in objects:
            o.set_visible(visible)
        self.refresh_adaptive_grid()

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

    def to_json(self, *, cache_meshes: bool = False) -> list:
        out = []
        active_mesh_ids = set()
        if cache_meshes:
            for obj in self.objects:
                actor = getattr(obj, "actor", None)
                mapper = actor.GetMapper() if actor is not None else None
                mesh_poly = mapper.GetInput() if mapper is not None else None
                if isinstance(mesh_poly, vtk.vtkPolyData):
                    active_mesh_ids.add(id(mesh_poly))
            self._mesh_json_cache = {
                key: entry for key, entry in self._mesh_json_cache.items()
                if key in active_mesh_ids
            }
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
                if cache_meshes and isinstance(mesh_poly, vtk.vtkPolyData):
                    cache_key = id(mesh_poly)
                    mesh_mtime = int(mesh_poly.GetMTime())
                    cached = self._mesh_json_cache.get(cache_key)
                    if cached is not None and cached[0] is mesh_poly and cached[1] == mesh_mtime:
                        mesh_data = cached[2]
                    else:
                        mesh_data = self._polydata_to_json(mesh_poly)
                        self._mesh_json_cache[cache_key] = (mesh_poly, mesh_mtime, mesh_data)
                else:
                    mesh_data = self._polydata_to_json(mesh_poly)
                item["mesh"] = mesh_data
            if cache_meshes:
                mesh_data = item.pop("mesh", None)
                boolean_sources_data = item.get("params", {}).get("BooleanSourcesData")
                item = deepcopy(item)
                if isinstance(boolean_sources_data, list):
                    item["params"]["BooleanSourcesData"] = boolean_sources_data
                if mesh_data is not None:
                    item["mesh"] = mesh_data
            out.append(item)
        return out

    def from_json(self, data: list) -> None:
        # Remove all existing objects
        self.object_groups.clear()
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
                              plate_role=bool(p.get("PlateRole", False)),
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
                elif t in ("ExtrudedObject", "RevolvedObject", "SweptSurfaceObject"):
                    obj = cls(item.get("name", ""))
                    obj.set_parameters(p)
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
                self.add_object(obj, preserve_name=True)
                transform = item.get("actor_transform", {})
                if isinstance(transform, dict) and obj.actor is not None:
                    origin = transform.get("origin")
                    position = transform.get("position")
                    orientation = transform.get("orientation")
                    scale = transform.get("scale")
                    feature_matrix = isinstance(obj, RevolvedObject)
                    if (isinstance(origin, (list, tuple)) and len(origin) == 3
                            and (not feature_matrix or tuple(origin) != (0.0, 0.0, 0.0))):
                        obj.actor.SetOrigin(*[float(value) for value in origin])
                    if (isinstance(position, (list, tuple)) and len(position) == 3
                            and (not feature_matrix or tuple(position) != (0.0, 0.0, 0.0))):
                        obj.actor.SetPosition(*[float(value) for value in position])
                    if (isinstance(orientation, (list, tuple)) and len(orientation) == 3
                            and (not feature_matrix or tuple(orientation) != (0.0, 0.0, 0.0))):
                        obj.actor.SetOrientation(*[float(value) for value in orientation])
                    if (isinstance(scale, (list, tuple)) and len(scale) == 3
                            and (not feature_matrix or tuple(scale) != (1.0, 1.0, 1.0))):
                        obj.actor.SetScale(*[float(value) for value in scale])

                if isinstance(obj, MeshObject) and obj.boolean_source_names:
                    pending_boolean_sources.append((obj, list(obj.boolean_source_names)))
            except (KeyError, TypeError, ValueError):
                pass

        if pending_boolean_sources:
            by_name = {o.name: o for o in self.objects}
            for mesh_obj, names in pending_boolean_sources:
                if len(names) != len(set(names)):
                    continue
                mesh_obj.source_objects = [by_name[n] for n in names if n in by_name]

    def clear(self) -> None:
        for obj in list(self.objects):
            self.remove_object(obj)
        self.object_groups.clear()
        self.reference_planes_from_json([])
