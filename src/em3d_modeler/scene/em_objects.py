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

"""EM scene objects: Box, Cylinder, Cone, Sphere."""
from __future__ import annotations
from typing import Any, Dict, List

import vtk

# Default colour palette per material type
MATERIAL_COLORS: Dict[str, tuple] = {
    "PEC":       (0.80, 0.80, 0.90),
    "PML":       (0.90, 0.90, 0.60),
    "Dielectric":(0.60, 0.80, 0.60),
    "Air":       (0.70, 0.85, 1.00),
    "Custom":    (0.80, 0.70, 0.60),
}

SELECTION_COLOR: tuple = (0.62, 0.34, 0.85)

_OBJECT_COUNTER: Dict[str, int] = {}


def _auto_name(prefix: str) -> str:
    _OBJECT_COUNTER[prefix] = _OBJECT_COUNTER.get(prefix, 0) + 1
    return f"{prefix}_{_OBJECT_COUNTER[prefix]}"


def set_selection_color(color: tuple) -> None:
    global SELECTION_COLOR
    if isinstance(color, tuple) and len(color) == 3:
        SELECTION_COLOR = tuple(float(v) for v in color)


class EMObject:
    """Base class for all EM scene objects."""

    def __init__(self, name: str, material: str = "PEC"):
        self.name = name
        self.material = material
        self.is_model: bool = True
        self.opacity: float = 0.85
        self.custom_color: tuple | None = None  # Optional (R, G, B) override; if set, overrides material color
        self._selected: bool = False
        # Optional role-highlight color (e.g. boolean base/tool); overrides
        # both base material color and the regular yellow selection color.
        self._role_color: tuple | None = None
        self.creation_plane: str = "XY"
        self.creation_plane_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.creation_plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
        self.param_formulas: Dict[str, str] = {}
        self.creation_history: Dict[str, Any] = {}
        self.creation_reference_error: str = ""
        self._actor: vtk.vtkActor | None = None
        self._build()

    # ─────────────────────────────────────────────────────── override in subclasses
    def _build(self) -> None:
        raise NotImplementedError

    def get_parameters(self) -> Dict[str, Any]:
        raise NotImplementedError

    def set_parameters(self, params: Dict[str, Any]) -> None:
        raise NotImplementedError

    def to_emerge_script(self) -> str:
        raise NotImplementedError

    # ─────────────────────────────────────────────────────── common helpers
    @property
    def actor(self) -> vtk.vtkActor:
        return self._actor

    @property
    def all_actors(self) -> List[vtk.vtkActor]:
        return [a for a in [self._actor] if a is not None]

    def set_visible(self, visible: bool) -> None:
        if self._actor is not None:
            self._actor.SetVisibility(1 if visible else 0)

    def is_visible(self) -> bool:
        if self._actor is None:
            return True
        return bool(self._actor.GetVisibility())

    def _base_color(self) -> tuple:
        if self.custom_color is not None:
            return self.custom_color
        return MATERIAL_COLORS.get(self.material, (0.75, 0.75, 0.85))

    def _apply_appearance(self, actor: vtk.vtkActor) -> None:
        prop = actor.GetProperty()
        prop.SetColor(*self._base_color())
        prop.SetOpacity(self.opacity)
        prop.SetRepresentationToSurface()
        prop.EdgeVisibilityOff()
        prop.SetEdgeColor(0.1, 0.1, 0.1)
        prop.SetLineWidth(1.2)

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        if self._actor:
            prop = self._actor.GetProperty()
            if self._role_color is not None:
                # Role color always wins (e.g. boolean base/tool highlight)
                r, g, b = self._role_color
                prop.SetColor(r, g, b)
                prop.SetLineWidth(2.5)
                prop.EdgeVisibilityOn()
                prop.SetEdgeColor(r * 0.6, g * 0.6, b * 0.6)
            elif sel:
                prop.SetColor(*SELECTION_COLOR)
                prop.SetLineWidth(2.5)
                prop.SetEdgeColor(
                    max(SELECTION_COLOR[0] * 0.65, 0.0),
                    max(SELECTION_COLOR[1] * 0.65, 0.0),
                    max(SELECTION_COLOR[2] * 0.65, 0.0),
                )
            else:
                self._apply_appearance(self._actor)

    def set_role_color(self, color: tuple | None) -> None:
        """Apply a persistent role highlight color (e.g. boolean base/tool).

        Pass ``None`` to clear the role color and revert to normal appearance
        / selection color.
        """
        self._role_color = tuple(color) if color is not None else None
        # Re-apply visual state to reflect the change immediately
        self.set_selected(self._selected)

    def refresh_appearance(self) -> None:
        if self._actor:
            self._apply_appearance(self._actor)
            if self._selected or self._role_color is not None:
                self.set_selected(self._selected)

    def set_creation_plane(self, plane: str, origin: tuple | None = None, normal: tuple | None = None) -> None:
        self.creation_plane = str(plane or "XY").upper()
        self.creation_plane_origin = tuple(origin if origin is not None else (0.0, 0.0, 0.0))
        self.creation_plane_normal = tuple(normal if normal is not None else (0.0, 0.0, 1.0))

    def creation_plane_reference_block(self) -> str:
        if self.creation_plane == "XY" and self.creation_plane_normal == (0.0, 0.0, 1.0):
            return ""
        ox, oy, oz = self.creation_plane_origin
        nx, ny, nz = self.creation_plane_normal
        name = f"Ref_{self.creation_plane}_{self.name}"
        return (
            f'ReferencePlane "{name}"\n'
            f"  Origin = [{ox}, {oy}, {oz}]\n"
            f"  Normal = [{nx}, {ny}, {nz}]\n"
            "EndReferencePlane\n"
        )

    def creation_plane_reference_tag(self) -> str:
        if self.creation_plane == "XY" and self.creation_plane_normal == (0.0, 0.0, 1.0):
            return ""
        return f'  ReferencePlane = "Ref_{self.creation_plane}_{self.name}"\n'

    def to_json_state(self) -> Dict[str, Any]:
        return {
            "creation_plane": self.creation_plane,
            "creation_plane_origin": list(self.creation_plane_origin),
            "creation_plane_normal": list(self.creation_plane_normal),
            "creation_history": dict(self.creation_history),
        }

    def from_json_state(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        plane = str(data.get("creation_plane", self.creation_plane or "XY")).strip().upper() or "XY"
        origin = data.get("creation_plane_origin", self.creation_plane_origin)
        normal = data.get("creation_plane_normal", self.creation_plane_normal)
        try:
            origin_tuple = tuple(float(v) for v in origin)
        except Exception:
            origin_tuple = self.creation_plane_origin
        try:
            normal_tuple = tuple(float(v) for v in normal)
            if len(normal_tuple) != 3:
                raise ValueError
        except Exception:
            normal_tuple = self.creation_plane_normal
        self.set_creation_plane(plane, origin_tuple, normal_tuple)
        history = data.get("creation_history", {})
        self.creation_history = dict(history) if isinstance(history, dict) else {}


# ═══════════════════════════════════════════════════════════════════════════════
class BoxObject(EMObject):
    """Rectangular parallelepiped defined by two opposite corners."""

    def __init__(self, name: str = "",
                 x1: float = 0, y1: float = 0, z1: float = 0,
                 x2: float = 10, y2: float = 10, z2: float = 10,
                 material: str = "PEC"):
        self.x1, self.y1, self.z1 = x1, y1, z1
        self.x2, self.y2, self.z2 = x2, y2, z2
        super().__init__(name or _auto_name("Box"), material)

    def _build(self) -> None:
        self._source = vtk.vtkCubeSource()
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _refresh_source(self) -> None:
        self._source.SetBounds(
            min(self.x1, self.x2), max(self.x1, self.x2),
            min(self.y1, self.y2), max(self.y1, self.y2),
            min(self.z1, self.z2), max(self.z1, self.z2),
        )
        self._source.Update()

    def get_parameters(self) -> Dict[str, Any]:
        p = dict(X1=self.x1, Y1=self.y1, Z1=self.z1,
                    X2=self.x2, Y2=self.y2, Z2=self.z2,
                    Material=self.material, Opacity=self.opacity)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.x1 = float(params.get("X1", self.x1))
        self.y1 = float(params.get("Y1", self.y1))
        self.z1 = float(params.get("Z1", self.z1))
        self.x2 = float(params.get("X2", self.x2))
        self.y2 = float(params.get("Y2", self.y2))
        self.z2 = float(params.get("Z2", self.z2))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        if "Color" in params:
            col = str(params["Color"]).strip()
            if col.startswith("#") and len(col) == 7:
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    self.custom_color = (r, g, b)
                except ValueError:
                    pass
        self._refresh_source()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        xmin, xmax = sorted([self.x1, self.x2])
        ymin, ymax = sorted([self.y1, self.y2])
        zmin, zmax = sorted([self.z1, self.z2])
        lines = [
            f'Block "{self.name}"',
            f'  Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"  XRange = [{xmin}, {xmax}]",
            f"  YRange = [{ymin}, {ymax}]",
            f"  ZRange = [{zmin}, {zmax}]",
            "EndBlock",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class CylinderObject(EMObject):
    """Cylinder with axis along X, Y, or Z."""

    AXIS_ROTATION = {"X": (0, 0, 90), "Y": (0, 0, 0), "Z": (90, 0, 0)}

    def __init__(self, name: str = "",
                 cx: float = 0, cy: float = 0, cz: float = 0,
                 radius: float = 5, height: float = 10,
                 axis: str = "Z", material: str = "PEC"):
        self.cx, self.cy, self.cz = cx, cy, cz
        self.radius = radius
        self.height = height
        self.axis = axis
        super().__init__(name or _auto_name("Cylinder"), material)

    def _build(self) -> None:
        self._source = vtk.vtkCylinderSource()
        self._source.SetResolution(36)
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)
        self._apply_orientation()

    def _refresh_source(self) -> None:
        self._source.SetCenter(0, 0, 0)   # position via actor transform
        self._source.SetRadius(abs(self.radius))
        self._source.SetHeight(abs(self.height))
        self._source.Update()

    def _apply_orientation(self) -> None:
        rx, ry, rz = self.AXIS_ROTATION.get(self.axis, (90, 0, 0))
        self._actor.SetPosition(self.cx, self.cy, self.cz)
        self._actor.SetOrientation(rx, ry, rz)

    def get_parameters(self) -> Dict[str, Any]:
        return dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    Radius=self.radius, Height=self.height, Axis=self.axis,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.radius = float(params.get("Radius", self.radius))
        self.height = float(params.get("Height", self.height))
        self.axis = params.get("Axis", self.axis)
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self._apply_orientation()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'Cylinder "{self.name}"',
            f'  Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]",
            f"  Radius = {abs(self.radius)}",
            f"  Height = {abs(self.height)}",
            f'  Axis = "{self.axis}"',
            "EndCylinder",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class ConeObject(EMObject):
    """Cone with apex along X, Y, or Z."""

    AXIS_ROTATION = {"X": (0, 0, 90), "Y": (0, 0, 0), "Z": (0, 0, -90)}

    def __init__(self, name: str = "",
                 cx: float = 0, cy: float = 0, cz: float = 0,
                 radius: float = 5, height: float = 10,
                 axis: str = "Z", material: str = "PEC"):
        self.cx, self.cy, self.cz = cx, cy, cz
        self.radius = radius
        self.height = height
        self.axis = axis
        super().__init__(name or _auto_name("Cone"), material)

    def _build(self) -> None:
        self._source = vtk.vtkConeSource()
        self._source.SetResolution(36)
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)
        self._apply_orientation()

    def _refresh_source(self) -> None:
        self._source.SetCenter(0, 0, 0)
        self._source.SetRadius(abs(self.radius))
        self._source.SetHeight(abs(self.height))
        self._source.Update()

    def _apply_orientation(self) -> None:
        rx, ry, rz = self.AXIS_ROTATION.get(self.axis, (0, 0, -90))
        self._actor.SetPosition(self.cx, self.cy, self.cz)
        self._actor.SetOrientation(rx, ry, rz)

    def get_parameters(self) -> Dict[str, Any]:
        p = dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    Radius=self.radius, Height=self.height, Axis=self.axis,
                    Material=self.material, Opacity=self.opacity)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.radius = float(params.get("Radius", self.radius))
        self.height = float(params.get("Height", self.height))
        self.axis = params.get("Axis", self.axis)
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        if "Color" in params:
            col = str(params["Color"]).strip()
            if col.startswith("#") and len(col) == 7:
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    self.custom_color = (r, g, b)
                except ValueError:
                    pass
        self._refresh_source()
        self._apply_orientation()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'Cone "{self.name}"',
            f'  Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]",
            f"  Radius = {abs(self.radius)}",
            f"  Height = {abs(self.height)}",
            f'  Axis = "{self.axis}"',
            "EndCone",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class SphereObject(EMObject):
    """Sphere defined by center and radius."""

    def __init__(self, name: str = "",
                 cx: float = 0, cy: float = 0, cz: float = 0,
                 radius: float = 5, material: str = "PEC"):
        self.cx, self.cy, self.cz = cx, cy, cz
        self.radius = radius
        super().__init__(name or _auto_name("Sphere"), material)

    def _build(self) -> None:
        self._source = vtk.vtkSphereSource()
        self._source.SetPhiResolution(32)
        self._source.SetThetaResolution(32)
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _refresh_source(self) -> None:
        self._source.SetCenter(self.cx, self.cy, self.cz)
        self._source.SetRadius(abs(self.radius))
        self._source.Update()

    def get_parameters(self) -> Dict[str, Any]:
        p = dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    Radius=self.radius,
                    Material=self.material, Opacity=self.opacity)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.radius = float(params.get("Radius", self.radius))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        if "Color" in params:
            col = str(params["Color"]).strip()
            if col.startswith("#") and len(col) == 7:
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    self.custom_color = (r, g, b)
                except ValueError:
                    pass
        self._refresh_source()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'Sphere "{self.name}"',
            f'  Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]",
            f"  Radius = {abs(self.radius)}",
            "EndSphere",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class PlateObject(EMObject):
    """Rectangular plate (thin box specialized for 2D structures)."""

    def __init__(self, name: str = "",
                 x1: float = -5, y1: float = -5, z1: float = 0,
                 x2: float = 5, y2: float = 5, z2: float = 0.1,
                 material: str = "PEC"):
        self.x1, self.y1, self.z1 = x1, y1, z1
        self.x2, self.y2, self.z2 = x2, y2, z2
        super().__init__(name or _auto_name("Plate"), material)

    def _build(self) -> None:
        self._source = vtk.vtkCubeSource()
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _refresh_source(self) -> None:
        self._source.SetBounds(
            min(self.x1, self.x2), max(self.x1, self.x2),
            min(self.y1, self.y2), max(self.y1, self.y2),
            min(self.z1, self.z2), max(self.z1, self.z2),
        )
        self._source.Update()

    def get_parameters(self) -> Dict[str, Any]:
        p = dict(X1=self.x1, Y1=self.y1, Z1=self.z1,
                    X2=self.x2, Y2=self.y2, Z2=self.z2,
                    Material=self.material, Opacity=self.opacity)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.x1 = float(params.get("X1", self.x1))
        self.y1 = float(params.get("Y1", self.y1))
        self.z1 = float(params.get("Z1", self.z1))
        self.x2 = float(params.get("X2", self.x2))
        self.y2 = float(params.get("Y2", self.y2))
        self.z2 = float(params.get("Z2", self.z2))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        if "Color" in params:
            col = str(params["Color"]).strip()
            if col.startswith("#") and len(col) == 7:
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    self.custom_color = (r, g, b)
                except ValueError:
                    pass
        self._refresh_source()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        xmin, xmax = sorted([self.x1, self.x2])
        ymin, ymax = sorted([self.y1, self.y2])
        zmin, zmax = sorted([self.z1, self.z2])
        lines = [
            f'Block "{self.name}"',
            f'  Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"  XRange = [{xmin}, {xmax}]",
            f"  YRange = [{ymin}, {ymax}]",
            f"  ZRange = [{zmin}, {zmax}]",
            "EndBlock",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class PyramidObject(EMObject):
    """Pyramid with rectangular base and apex."""

    def __init__(self, name: str = "",
                 bx1: float = -5, by1: float = -5, bz: float = 0,
                 bx2: float = 5, by2: float = 5,
                 apex_z: float = 10, material: str = "PEC"):
        self.bx1, self.by1 = bx1, by1
        self.bx2, self.by2 = bx2, by2
        self.bz = bz
        self.apex_z = apex_z
        super().__init__(name or _auto_name("Pyramid"), material)

    def _build(self) -> None:
        self._source = vtk.vtkConeSource()
        self._source.SetResolution(4)
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)
        self._apply_position()

    def _refresh_source(self) -> None:
        base_w = abs(self.bx2 - self.bx1)
        base_h = abs(self.by2 - self.by1)
        height = abs(self.apex_z - self.bz)
        radius = max(base_w, base_h) / 2.0
        self._source.SetRadius(radius)
        self._source.SetHeight(height)
        self._source.SetDirection(0, 0, 1)
        self._source.Update()

    def _apply_position(self) -> None:
        cx = (self.bx1 + self.bx2) / 2.0
        cy = (self.by1 + self.by2) / 2.0
        cz = (self.bz + self.apex_z) / 2.0
        self._actor.SetPosition(cx, cy, cz)

    def get_parameters(self) -> Dict[str, Any]:
        return dict(BaseX1=self.bx1, BaseY1=self.by1, BaseZ=self.bz,
                    BaseX2=self.bx2, BaseY2=self.by2, ApexZ=self.apex_z,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.bx1 = float(params.get("BaseX1", self.bx1))
        self.by1 = float(params.get("BaseY1", self.by1))
        self.bz = float(params.get("BaseZ", self.bz))
        self.bx2 = float(params.get("BaseX2", self.bx2))
        self.by2 = float(params.get("BaseY2", self.by2))
        self.apex_z = float(params.get("ApexZ", self.apex_z))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self._apply_position()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        xmin, xmax = sorted([self.bx1, self.bx2])
        ymin, ymax = sorted([self.by1, self.by2])
        lines = [
            f'# Pyramid "{self.name}" — rectangular base + apex',
            f'# Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"# Base: X=[{xmin}, {xmax}], Y=[{ymin}, {ymax}], Z={self.bz}",
            f"# Apex Z: {self.apex_z}",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class WedgeObject(EMObject):
    """Wedge/prism: triangular cross-section extruded along Z."""

    def __init__(self, name: str = "",
                 x1: float = -5, y1: float = 0, z1: float = 0,
                 x2: float = 5, y2: float = 0, z2: float = 0,
                 x3: float = 0, y3: float = 5,
                 z_height: float = 10, material: str = "PEC"):
        self.x1, self.y1, self.z1 = x1, y1, z1
        self.x2, self.y2, self.z2 = x2, y2, z2
        self.x3, self.y3 = x3, y3
        self.z_height = z_height
        super().__init__(name or _auto_name("Wedge"), material)

    def _build(self) -> None:
        self._source = self._make_wedge_polydata()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(self._source)
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _make_wedge_polydata(self) -> "vtk.vtkPolyData":
        points = vtk.vtkPoints()
        points.InsertNextPoint(self.x1, self.y1, self.z1)
        points.InsertNextPoint(self.x2, self.y2, self.z2)
        points.InsertNextPoint(self.x3, self.y3, self.z1)
        points.InsertNextPoint(self.x1, self.y1, self.z1 + self.z_height)
        points.InsertNextPoint(self.x2, self.y2, self.z2 + self.z_height)
        points.InsertNextPoint(self.x3, self.y3, self.z1 + self.z_height)
        cells = vtk.vtkCellArray()
        triangles = [(0, 1, 2), (3, 5, 4), (0, 3, 4), (0, 4, 1), (1, 4, 5), (1, 5, 2), (2, 5, 3), (2, 3, 0)]
        for tri in triangles:
            triangle = vtk.vtkTriangle()
            for i, pt_id in enumerate(tri):
                triangle.GetPointIds().SetId(i, pt_id)
            cells.InsertNextCell(triangle)
        poly = vtk.vtkPolyData()
        poly.SetPoints(points)
        poly.SetPolys(cells)
        return poly

    def get_parameters(self) -> Dict[str, Any]:
        return dict(X1=self.x1, Y1=self.y1, Z1=self.z1,
                    X2=self.x2, Y2=self.y2, Z2=self.z2,
                    X3=self.x3, Y3=self.y3, ZHeight=self.z_height,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.x1 = float(params.get("X1", self.x1))
        self.y1 = float(params.get("Y1", self.y1))
        self.z1 = float(params.get("Z1", self.z1))
        self.x2 = float(params.get("X2", self.x2))
        self.y2 = float(params.get("Y2", self.y2))
        self.z2 = float(params.get("Z2", self.z2))
        self.x3 = float(params.get("X3", self.x3))
        self.y3 = float(params.get("Y3", self.y3))
        self.z_height = float(params.get("ZHeight", self.z_height))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._source = self._make_wedge_polydata()
        self._actor.GetMapper().SetInputData(self._source)
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'# Wedge "{self.name}" — triangular prism',
            f'# Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"# Base triangle: ({self.x1},{self.y1}) ({self.x2},{self.y2}) ({self.x3},{self.y3})",
            f"# Height: {self.z_height}",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class TorusObject(EMObject):
    """Torus: donut shape with major and minor radii."""

    def __init__(self, name: str = "",
                 cx: float = 0, cy: float = 0, cz: float = 0,
                 major_radius: float = 10, minor_radius: float = 3,
                 material: str = "PEC"):
        self.cx, self.cy, self.cz = cx, cy, cz
        self.major_radius = major_radius
        self.minor_radius = minor_radius
        super().__init__(name or _auto_name("Torus"), material)

    def _build(self) -> None:
        self._source = vtk.vtkTorusSource()
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)
        self._actor.SetPosition(self.cx, self.cy, self.cz)

    def _refresh_source(self) -> None:
        self._source.SetInnerRadius(abs(self.minor_radius))
        self._source.SetOuterRadius(abs(self.major_radius) + abs(self.minor_radius))
        self._source.Update()

    def get_parameters(self) -> Dict[str, Any]:
        return dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    MajorRadius=self.major_radius, MinorRadius=self.minor_radius,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.major_radius = float(params.get("MajorRadius", self.major_radius))
        self.minor_radius = float(params.get("MinorRadius", self.minor_radius))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self._actor.SetPosition(self.cx, self.cy, self.cz)
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'# Torus "{self.name}" — donut shape',
            f'# Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"# Center: [{self.cx}, {self.cy}, {self.cz}]",
            f"# Major Radius: {self.major_radius}, Minor Radius: {self.minor_radius}",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class EllipsoidObject(EMObject):
    """Ellipsoid with three independent radii (X, Y, Z)."""

    def __init__(self, name: str = "",
                 cx: float = 0, cy: float = 0, cz: float = 0,
                 rx: float = 5, ry: float = 5, rz: float = 10,
                 material: str = "PEC"):
        self.cx, self.cy, self.cz = cx, cy, cz
        self.rx, self.ry, self.rz = rx, ry, rz
        super().__init__(name or _auto_name("Ellipsoid"), material)

    def _build(self) -> None:
        self._source = vtk.vtkSphereSource()
        self._refresh_source()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._source.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)
        self._apply_scale()

    def _refresh_source(self) -> None:
        self._source.SetRadius(1.0)
        self._source.SetThetaResolution(32)
        self._source.SetPhiResolution(16)
        self._source.Update()

    def _apply_scale(self) -> None:
        self._actor.SetPosition(self.cx, self.cy, self.cz)
        self._actor.SetScale(abs(self.rx), abs(self.ry), abs(self.rz))

    def get_parameters(self) -> Dict[str, Any]:
        return dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    RadiusX=self.rx, RadiusY=self.ry, RadiusZ=self.rz,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.rx = float(params.get("RadiusX", self.rx))
        self.ry = float(params.get("RadiusY", self.ry))
        self.rz = float(params.get("RadiusZ", self.rz))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self._apply_scale()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'# Ellipsoid "{self.name}" — scaled sphere',
            f'# Material = "{self.material}"',
            self.creation_plane_reference_tag(),
            f"# Center: [{self.cx}, {self.cy}, {self.cz}]",
            f"# Radii: RX={self.rx}, RY={self.ry}, RZ={self.rz}",
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class ExtrudedObject(EMObject):
    """Solid created by linearly extruding a 2-D sketch profile.

    Parameters
    ----------
    profile_pts : list of (x, y) tuples  – 2-D polyline in the sketch plane
    depth       : extrusion depth along the plane normal
    plane_origin: 3-D origin of the sketch plane
    plane_normal: unit normal of the sketch plane
    material    : material name
    """

    def __init__(self, name: str = "",
                 profile_pts=None,
                 depth: float = 10.0,
                 plane_origin=(0.0, 0.0, 0.0),
                 plane_normal=(0.0, 0.0, 1.0),
                 material: str = "PEC"):
        self._profile_pts  = list(profile_pts or [(0,0),(10,0),(10,10),(0,10)])
        self._depth        = depth
        self._plane_origin = tuple(plane_origin)
        self._plane_normal = tuple(plane_normal)
        super().__init__(name or _auto_name("Extrude"), material)

    # ── build ────────────────────────────────────────────────────────
    def _build(self) -> None:
        poly = self._profile_to_polydata()
        self._extrude = vtk.vtkLinearExtrusionFilter()
        self._extrude.SetInputData(poly)
        nx, ny, nz = self._plane_normal
        self._extrude.SetExtrusionTypeToVectorExtrusion()
        self._extrude.SetVector(nx * self._depth,
                                ny * self._depth,
                                nz * self._depth)
        self._extrude.CappingOn()
        self._extrude.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._extrude.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _profile_to_polydata(self) -> vtk.vtkPolyData:
        """Convert 2-D profile points to vtkPolyData in the sketch plane."""
        import math
        pts = self._profile_pts
        # Build orthonormal basis in the plane
        n = list(self._plane_normal)
        # Find a vector not parallel to n
        ref = [1, 0, 0] if abs(n[1]) > 0.1 or abs(n[2]) > 0.1 else [0, 1, 0]
        # u = n × ref  (cross product)
        u = [n[1]*ref[2] - n[2]*ref[1],
             n[2]*ref[0] - n[0]*ref[2],
             n[0]*ref[1] - n[1]*ref[0]]
        um = math.sqrt(sum(v*v for v in u))
        u = [v/um for v in u]
        # v = u × n
        v = [u[1]*n[2] - u[2]*n[1],
             u[2]*n[0] - u[0]*n[2],
             u[0]*n[1] - u[1]*n[0]]

        ox, oy, oz = self._plane_origin
        vtk_pts = vtk.vtkPoints()
        polygon  = vtk.vtkPolygon()
        polygon.GetPointIds().SetNumberOfIds(len(pts))
        for i, (px, py) in enumerate(pts):
            wx = ox + px*u[0] + py*v[0]
            wy = oy + px*u[1] + py*v[1]
            wz = oz + px*u[2] + py*v[2]
            vtk_pts.InsertNextPoint(wx, wy, wz)
            polygon.GetPointIds().SetId(i, i)

        cells = vtk.vtkCellArray()
        cells.InsertNextCell(polygon)
        poly = vtk.vtkPolyData()
        poly.SetPoints(vtk_pts)
        poly.SetPolys(cells)
        return poly

    def _rebuild(self) -> None:
        poly = self._profile_to_polydata()
        self._extrude.SetInputData(poly)
        nx, ny, nz = self._plane_normal
        self._extrude.SetVector(nx * self._depth,
                                ny * self._depth,
                                nz * self._depth)
        self._extrude.Update()
        self.refresh_appearance()

    # ── parameters ───────────────────────────────────────────────────
    def get_parameters(self) -> Dict[str, Any]:
        return dict(
            Depth      = self._depth,
            PlaneOriginX = self._plane_origin[0],
            PlaneOriginY = self._plane_origin[1],
            PlaneOriginZ = self._plane_origin[2],
            PlaneNormalX = self._plane_normal[0],
            PlaneNormalY = self._plane_normal[1],
            PlaneNormalZ = self._plane_normal[2],
            ProfilePts = str(self._profile_pts),
            Material   = self.material,
            Opacity    = self.opacity,
        )

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self._depth = float(params.get("Depth", self._depth))
        self.material = params.get("Material", self.material)
        self.opacity  = float(params.get("Opacity", self.opacity))
        self._rebuild()

    def to_emerge_script(self) -> str:
        lines = [
            f'# ExtrudedObject "{self.name}" — export via EMERGE extrude command',
            f'# Depth = {self._depth}, Material = "{self.material}"',
            self.creation_plane_reference_tag(),
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class RevolvedObject(EMObject):
    """Solid created by revolving a 2-D sketch profile around an axis.

    Parameters
    ----------
    profile_pts : list of (x, y) tuples – 2-D profile (x=radial, y=axial)
    angle       : sweep angle in degrees (0–360)
    axis_pt1    : first point of revolution axis (world coords)
    axis_pt2    : second point of revolution axis (world coords)
    material    : material name
    """

    def __init__(self, name: str = "",
                 profile_pts=None,
                 angle: float = 360.0,
                 axis_pt1=(0.0, 0.0, 0.0),
                 axis_pt2=(0.0, 0.0, 1.0),
                 plane_origin=(0.0, 0.0, 0.0),
                 plane_normal=(0.0, 0.0, 1.0),
                 material: str = "PEC"):
        self._profile_pts  = list(profile_pts or [(2,0),(5,0),(5,10),(2,10)])
        self._angle        = angle
        self._axis_pt1     = tuple(axis_pt1)
        self._axis_pt2     = tuple(axis_pt2)
        self._plane_origin = tuple(plane_origin)
        self._plane_normal = tuple(plane_normal)
        super().__init__(name or _auto_name("Revolve"), material)

    @staticmethod
    def _plane_basis(normal):
        import math
        n = list(normal)
        nm = math.sqrt(sum(v*v for v in n)) or 1.0
        n = [v/nm for v in n]
        ref = [1, 0, 0] if abs(n[1]) > 0.1 or abs(n[2]) > 0.1 else [0, 1, 0]
        u = [n[1]*ref[2] - n[2]*ref[1],
             n[2]*ref[0] - n[0]*ref[2],
             n[0]*ref[1] - n[1]*ref[0]]
        um = math.sqrt(sum(v*v for v in u)) or 1.0
        u = [v/um for v in u]
        v = [u[1]*n[2] - u[2]*n[1],
             u[2]*n[0] - u[0]*n[2],
             u[0]*n[1] - u[1]*n[0]]
        return n, u, v

    def _profile_local_and_transform(self):
        """Convert (u,v) sketch profile points to (radial, axial) local coords,
        and produce a vtkTransform that maps local Y-axis to the world axis."""
        import math
        _, ub, vb = self._plane_basis(self._plane_normal)
        ox, oy, oz = self._plane_origin

        # Axis in world coords
        a1 = self._axis_pt1
        a2 = self._axis_pt2
        ad = (a2[0]-a1[0], a2[1]-a1[1], a2[2]-a1[2])
        am = math.sqrt(sum(v*v for v in ad)) or 1.0
        ax = (ad[0]/am, ad[1]/am, ad[2]/am)

        local_pts = []
        for (pu, pv) in self._profile_pts:
            # World coords of profile point
            wx = ox + pu*ub[0] + pv*vb[0]
            wy = oy + pu*ub[1] + pv*vb[1]
            wz = oz + pu*ub[2] + pv*vb[2]
            # Vector from axis_pt1
            dx, dy, dz = wx-a1[0], wy-a1[1], wz-a1[2]
            # Axial component (signed projection)
            axial = dx*ax[0] + dy*ax[1] + dz*ax[2]
            # Perpendicular component (radial vector)
            px = dx - axial*ax[0]
            py = dy - axial*ax[1]
            pz = dz - axial*ax[2]
            radial = math.sqrt(px*px + py*py + pz*pz)
            local_pts.append((radial, axial))

        # Build a transform: local frame has Y == world axis direction, origin at axis_pt1.
        # vtkRotationalExtrusionFilter rotates around local Y, sweeping in the +X direction.
        # We need a rotation matrix whose 2nd column == ax (world axis). Pick any orthonormal basis.
        # Choose temp vector not parallel to ax
        if abs(ax[0]) < 0.9:
            tmp = (1.0, 0.0, 0.0)
        else:
            tmp = (0.0, 1.0, 0.0)
        # local X = normalize(tmp - (tmp·ax)*ax)
        d = tmp[0]*ax[0] + tmp[1]*ax[1] + tmp[2]*ax[2]
        lx = (tmp[0]-d*ax[0], tmp[1]-d*ax[1], tmp[2]-d*ax[2])
        lm = math.sqrt(sum(v*v for v in lx)) or 1.0
        lx = (lx[0]/lm, lx[1]/lm, lx[2]/lm)
        # local Z = ax × lx
        lz = (ax[1]*lx[2]-ax[2]*lx[1],
              ax[2]*lx[0]-ax[0]*lx[2],
              ax[0]*lx[1]-ax[1]*lx[0])

        m = vtk.vtkMatrix4x4()
        m.Identity()
        # vtkRotationalExtrusionFilter rotates around local Z. Map local Z -> world axis.
        # Columns: localX, localY, localZ(==ax), origin
        m.SetElement(0, 0, lx[0]); m.SetElement(1, 0, lx[1]); m.SetElement(2, 0, lx[2])
        m.SetElement(0, 1, lz[0]); m.SetElement(1, 1, lz[1]); m.SetElement(2, 1, lz[2])
        m.SetElement(0, 2, ax[0]); m.SetElement(1, 2, ax[1]); m.SetElement(2, 2, ax[2])
        m.SetElement(0, 3, a1[0]); m.SetElement(1, 3, a1[1]); m.SetElement(2, 3, a1[2])
        return local_pts, m

    def _build(self) -> None:
        local_pts, user_matrix = self._profile_local_and_transform()
        poly = self._profile_to_polydata(local_pts)
        self._revolve = vtk.vtkRotationalExtrusionFilter()
        self._revolve.SetInputData(poly)
        self._revolve.SetAngle(self._angle)
        self._revolve.SetResolution(60)
        self._revolve.Update()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputConnection(self._revolve.GetOutputPort())
        normals.ConsistencyOn()
        normals.AutoOrientNormalsOn()
        normals.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._actor.SetUserMatrix(user_matrix)
        self._apply_appearance(self._actor)

    def _profile_to_polydata(self, local_pts=None) -> vtk.vtkPolyData:
        """Profile in local frame: x=radial, y=0, z=axial.
        vtkRotationalExtrusionFilter sweeps the polyline around local Z axis."""
        if local_pts is None:
            local_pts = self._profile_pts
        vtk_pts = vtk.vtkPoints()
        for pr, pa in local_pts:
            vtk_pts.InsertNextPoint(pr, 0.0, pa)
        n = vtk_pts.GetNumberOfPoints()
        lines = vtk.vtkCellArray()
        if n >= 2:
            polyline = vtk.vtkPolyLine()
            polyline.GetPointIds().SetNumberOfIds(n)
            for i in range(n):
                polyline.GetPointIds().SetId(i, i)
            lines.InsertNextCell(polyline)
        poly = vtk.vtkPolyData()
        poly.SetPoints(vtk_pts)
        poly.SetLines(lines)
        return poly

    def get_parameters(self) -> Dict[str, Any]:
        return dict(
            Angle      = self._angle,
            AxisPt1X   = self._axis_pt1[0],
            AxisPt1Y   = self._axis_pt1[1],
            AxisPt1Z   = self._axis_pt1[2],
            AxisPt2X   = self._axis_pt2[0],
            AxisPt2Y   = self._axis_pt2[1],
            AxisPt2Z   = self._axis_pt2[2],
            ProfilePts = str(self._profile_pts),
            Material   = self.material,
            Opacity    = self.opacity,
        )

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self._angle = float(params.get("Angle", self._angle))
        self.material = params.get("Material", self.material)
        self.opacity  = float(params.get("Opacity", self.opacity))
        local_pts, user_matrix = self._profile_local_and_transform()
        self._revolve.SetInputData(self._profile_to_polydata(local_pts))
        self._revolve.SetAngle(self._angle)
        self._revolve.Update()
        if self._actor is not None:
            self._actor.SetUserMatrix(user_matrix)
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'# RevolvedObject "{self.name}" — export via EMERGE revolve command',
            f'# Angle = {self._angle}, Material = "{self.material}"',
            self.creation_plane_reference_tag(),
        ]
        return "\n".join(part for part in lines if part)


# ═══════════════════════════════════════════════════════════════════════════════
class MeshObject(EMObject):
    """Generic triangulated mesh (e.g. imported from STEP via pythonOCC)."""

    def __init__(self, name: str = "",
                 polydata: "vtk.vtkPolyData | None" = None,
                 material: str = "PEC",
                 color: tuple | None = None,
                 step_source_path: str | None = None,
                 step_solid_name: str | None = None,
                 plate_role: bool = False,
                 boolean_op: str | None = None,
                 boolean_source_names: List[str] | None = None,
                 boolean_sources_data: List[Dict[str, Any]] | None = None):
        self._polydata = polydata
        self.step_source_path = step_source_path
        self.step_solid_name = step_solid_name or (name or "")
        self.plate_role = bool(plate_role)
        self.step_geometry_modified = False
        self.step_export_offset = (0.0, 0.0, 0.0)
        self.source_objects: List[EMObject] = []
        self.boolean_op = str(boolean_op or "").strip().lower() or None
        self.boolean_source_names = [str(x) for x in (boolean_source_names or []) if str(x)]
        self.boolean_sources_data = [x for x in (boolean_sources_data or []) if isinstance(x, dict)]
        super().__init__(name or _auto_name("Mesh"), material)
        if color is not None:
            self.custom_color = color
            # Actor was already built in EMObject.__init__; re-apply appearance
            # so imported STEP colors are visible immediately.
            self.refresh_appearance()

    def _build(self) -> None:
        if self._polydata is None:
            self._polydata = vtk.vtkPolyData()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(self._polydata)
        # Disable scalar visibility to ensure uniform material color rendering,
        # not gradient/heatmap coloring. This is especially important for boolean results.
        mapper.ScalarVisibilityOff()
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def get_parameters(self) -> Dict[str, Any]:
        p = dict(Material=self.material, Opacity=self.opacity)
        if self.step_source_path:
            p["StepSourcePath"] = str(self.step_source_path)
        if self.step_solid_name:
            p["StepSolidName"] = str(self.step_solid_name)
        if self.step_geometry_modified:
            p["StepGeometryModified"] = True
        if self.plate_role:
            p["PlateRole"] = True
        ox, oy, oz = self.step_export_offset
        if abs(float(ox)) > 1e-12 or abs(float(oy)) > 1e-12 or abs(float(oz)) > 1e-12:
            p["StepExportOffset"] = [float(ox), float(oy), float(oz)]
        if self.boolean_op:
            p["BooleanOperation"] = str(self.boolean_op)
        if self.boolean_source_names:
            p["BooleanSourceNames"] = list(self.boolean_source_names)
        if self.boolean_sources_data:
            p["BooleanSourcesData"] = list(self.boolean_sources_data)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.material = params.get("Material", self.material)
        self.opacity  = float(params.get("Opacity", self.opacity))
        self.step_source_path = params.get("StepSourcePath", self.step_source_path)
        self.step_solid_name = params.get("StepSolidName", self.step_solid_name)
        self.step_geometry_modified = bool(params.get("StepGeometryModified", self.step_geometry_modified))
        if "PlateRole" in params:
            self.plate_role = bool(params.get("PlateRole"))
        off = params.get("StepExportOffset", self.step_export_offset)
        if isinstance(off, (list, tuple)) and len(off) == 3:
            try:
                self.step_export_offset = (float(off[0]), float(off[1]), float(off[2]))
            except Exception:
                pass
        self.boolean_op = str(params.get("BooleanOperation", self.boolean_op or "")).strip().lower() or None
        names = params.get("BooleanSourceNames", self.boolean_source_names)
        if isinstance(names, list):
            self.boolean_source_names = [str(x) for x in names if str(x)]
        src_data = params.get("BooleanSourcesData", self.boolean_sources_data)
        if isinstance(src_data, list):
            self.boolean_sources_data = [x for x in src_data if isinstance(x, dict)]
        if (
            "PlateRole" not in params
            and self.boolean_op == "cut"
            and self.boolean_sources_data
            and str(self.boolean_sources_data[0].get("type", "")) == "PlateObject"
        ):
            self.plate_role = True
        if "Color" in params:
            col = str(params["Color"]).strip()
            if col.startswith("#") and len(col) == 7:
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    self.custom_color = (r, g, b)
                except ValueError:
                    pass
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        lines = [
            f'# MeshObject "{self.name}" — Material = "{self.material}"',
            self.creation_plane_reference_tag(),
        ]
        return "\n".join(part for part in lines if part)

