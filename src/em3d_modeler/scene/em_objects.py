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
import ast
from copy import deepcopy
import json
import math
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


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _vector3(values: Any, label: str) -> tuple[float, float, float]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must contain three finite numbers")
    try:
        values = tuple(values)
    except TypeError as exc:
        raise ValueError(f"{label} must contain three finite numbers") from exc
    if len(values) != 3:
        raise ValueError(f"{label} must contain three finite numbers")
    return tuple(_finite_float(value, label) for value in values)


def _unit_vector3(values: Any, label: str) -> tuple[float, float, float]:
    vector = _vector3(values, label)
    length = math.hypot(*vector)
    if not math.isfinite(length) or length == 0.0:
        raise ValueError(f"{label} must have a finite, non-zero length")
    return tuple(value / length for value in vector)


def _json_safe_copy(value: Any) -> Any | None:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _profile_points(value: Any, minimum: int, require_area: bool = False) -> list[tuple[float, float]]:
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("ProfilePts must be a literal sequence of point pairs") from exc
    if not isinstance(value, (list, tuple)):
        raise ValueError("ProfilePts must be a sequence of point pairs")

    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("ProfilePts must contain only (x, y) point pairs")
        points.append((_finite_float(point[0], "ProfilePts"),
                       _finite_float(point[1], "ProfilePts")))
    if len(points) < minimum:
        raise ValueError(f"ProfilePts must contain at least {minimum} points")
    if len(set(points)) < 2:
        raise ValueError("ProfilePts must span a non-zero length")
    if require_area:
        twice_area = sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        if twice_area == 0.0:
            raise ValueError("ProfilePts must enclose a non-zero area")
    return points


def _color_from_parameters(params: Dict[str, Any], current: tuple | None) -> tuple | None:
    if "Color" not in params:
        return current
    color = str(params["Color"]).strip()
    if len(color) != 7 or not color.startswith("#"):
        return current
    try:
        return tuple(int(color[index:index + 2], 16) / 255.0 for index in (1, 3, 5))
    except ValueError:
        return current


def _position_delta(params: Dict[str, Any], keys: tuple[str, str, str], reference: tuple) -> tuple[float, float, float]:
    if not any(key in params for key in keys):
        return (0.0, 0.0, 0.0)
    target = tuple(_finite_float(params.get(key, reference[index]), key)
                   for index, key in enumerate(keys))
    return tuple(_finite_float(target[index] - reference[index], key)
                 for index, key in enumerate(keys))


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
        self.pattern_definition: Dict[str, Any] | None = None
        self.pattern_instance: Dict[str, Any] | None = None
        self.sketch_definition: Dict[str, Any] | None = None
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
            "pattern_definition": deepcopy(self.pattern_definition),
            "pattern_instance": deepcopy(self.pattern_instance),
            "sketch_definition": _json_safe_copy(self.sketch_definition),
        }

    def from_json_state(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        plane = str(data.get("creation_plane", self.creation_plane or "XY")).strip().upper() or "XY"
        origin = data.get("creation_plane_origin", self.creation_plane_origin)
        normal = data.get("creation_plane_normal", self.creation_plane_normal)
        try:
            origin_tuple = tuple(float(v) for v in origin)
            if len(origin_tuple) != 3:
                raise ValueError
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
        pattern = data.get("pattern_definition")
        self.pattern_definition = deepcopy(pattern) if isinstance(pattern, dict) else None
        instance = data.get("pattern_instance")
        self.pattern_instance = deepcopy(instance) if isinstance(instance, dict) else None
        sketch_definition = _json_safe_copy(data.get("sketch_definition"))
        self.sketch_definition = sketch_definition if isinstance(sketch_definition, dict) else None

    def sketch_reference_point(self) -> tuple[float, float, float]:
        """Return the world-space first profile point, or the feature origin."""
        definition = self.sketch_definition
        if isinstance(definition, dict):
            try:
                from ..drawing.sketch_engine import SketchEngine

                engine = SketchEngine.from_dict(definition)
                profile = engine.build_profile()
                if profile:
                    u, v = profile[0]
                    return tuple(
                        engine.plane_origin[axis]
                        + u * engine.u_axis[axis]
                        + v * engine.v_axis[axis]
                        for axis in range(3)
                    )
            except (KeyError, TypeError, ValueError, IndexError):
                pass
        fallback = getattr(self, "_axis_pt1", getattr(self, "_plane_origin", (0.0, 0.0, 0.0)))
        return tuple(fallback)


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
                 material: str = "PEC",
                 direction: str = "Normal",
                 symmetric: bool = False):
        initial_profile = profile_pts if profile_pts is not None else [(0, 0), (10, 0), (10, 10), (0, 10)]
        self._profile_pts, _ = self._parse_profile_groups(initial_profile)
        self._depth = _finite_float(depth, "Depth")
        if self._depth <= 0.0:
            raise ValueError("Depth must be greater than zero")
        self._plane_origin = _vector3(plane_origin, "PlaneOrigin")
        self._plane_normal = _vector3(plane_normal, "PlaneNormal")
        _unit_vector3(self._plane_normal, "PlaneNormal")
        self._direction = self._normalize_direction(direction)
        self._symmetric = bool(symmetric)
        super().__init__(name or _auto_name("Extrude"), material)

    @staticmethod
    def _normalize_direction(direction: Any) -> str:
        value = str(direction).strip().lower()
        if value in {"normal", "1", "+1", "along normal"}:
            return "Normal"
        if value in {"reverse", "-1", "opposite normal"}:
            return "Reverse"
        raise ValueError("Direction must be 'Normal' or 'Reverse'")

    @staticmethod
    def _extrusion_frame(origin, normal, depth, direction, symmetric):
        unit_normal = _unit_vector3(normal, "PlaneNormal")
        if symmetric:
            start = tuple(origin[index] - unit_normal[index] * depth * 0.5 for index in range(3))
            vector = unit_normal
        else:
            start = tuple(origin)
            sign = -1.0 if direction == "Reverse" else 1.0
            vector = tuple(component * sign for component in unit_normal)
        return start, vector

    # ── build ────────────────────────────────────────────────────────
    def _build(self) -> None:
        start, vector = self._extrusion_frame(
            self._plane_origin, self._plane_normal, self._depth,
            self._direction, self._symmetric,
        )
        poly = self._profile_to_polydata(plane_origin=start)
        self._extrude = self._make_extrusion(poly, self._depth, vector)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._extrude.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    @staticmethod
    def _parse_profile(value: Any) -> tuple[list, list[list[tuple[float, float]]]]:
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                raise ValueError("ProfilePts must be a literal sequence of point pairs or contours") from exc
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("ProfilePts must be a sequence of point pairs or contours")

        first = value[0]
        flat_profile = (
            isinstance(first, (list, tuple))
            and len(first) == 2
            and not isinstance(first[0], (list, tuple))
            and not isinstance(first[1], (list, tuple))
        )
        raw_contours = [value] if flat_profile else value
        contours = []
        for raw_contour in raw_contours:
            if not isinstance(raw_contour, (list, tuple)):
                raise ValueError("ProfilePts contours must be sequences of point pairs")
            contour = _profile_points(raw_contour, 3)
            if contour[0] == contour[-1]:
                contour.pop()
            if len(contour) < 3:
                raise ValueError("ProfilePts contours must contain at least three distinct vertices")
            if any(contour[index] == contour[(index + 1) % len(contour)]
                   for index in range(len(contour))):
                raise ValueError("ProfilePts contours must not contain zero-length edges")
            twice_area = sum(
                contour[index][0] * contour[(index + 1) % len(contour)][1]
                - contour[(index + 1) % len(contour)][0] * contour[index][1]
                for index in range(len(contour))
            )
            if not math.isfinite(twice_area) or twice_area == 0.0:
                raise ValueError("ProfilePts contours must enclose a finite, non-zero area")
            contours.append(contour)
        if not contours:
            raise ValueError("ProfilePts must contain at least one contour")

        profile = contours[0] if len(contours) == 1 else contours
        return profile, contours

    @classmethod
    def _parse_profile_groups(cls, value: Any) -> tuple[list, list[list[list[tuple[float, float]]]]]:
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                raise ValueError("ProfilePts must be a literal sequence of points, contours, or regions") from exc
        if (isinstance(value, (list, tuple)) and value
                and isinstance(value[0], (list, tuple)) and value[0]
                and isinstance(value[0][0], (list, tuple)) and value[0][0]
                and isinstance(value[0][0][0], (list, tuple))):
            profiles = []
            groups = []
            for raw_group in value:
                _, contours = cls._parse_profile(raw_group)
                profiles.append(contours)
                groups.append(contours)
            return profiles, groups
        profile, contours = cls._parse_profile(value)
        return profile, [contours]

    @staticmethod
    def _make_extrusion(poly: vtk.vtkPolyData, depth: float,
                        plane_normal: tuple[float, float, float]) -> vtk.vtkLinearExtrusionFilter:
        extrude = vtk.vtkLinearExtrusionFilter()
        extrude.SetInputData(poly)
        nx, ny, nz = _unit_vector3(plane_normal, "PlaneNormal")
        extrude.SetExtrusionTypeToVectorExtrusion()
        extrude.SetVector(nx * depth, ny * depth, nz * depth)
        extrude.CappingOn()
        extrude.Update()
        if extrude.GetOutput().GetNumberOfCells() == 0:
            raise ValueError("ProfilePts could not be triangulated for extrusion")
        return extrude

    def _profile_to_polydata(self, profile_pts=None, plane_origin=None,
                             plane_normal=None) -> vtk.vtkPolyData:
        """Triangulate profile contours and map the cap into the sketch plane."""
        profile = self._profile_pts if profile_pts is None else profile_pts
        origin = self._plane_origin if plane_origin is None else plane_origin
        normal = self._plane_normal if plane_normal is None else plane_normal
        _, profile_groups = self._parse_profile_groups(profile)
        caps = vtk.vtkAppendPolyData()
        for contours in profile_groups:
            contour_points = vtk.vtkPoints()
            contour_lines = vtk.vtkCellArray()
            expected_area = 0.0
            for contour_index, contour in enumerate(contours):
                signed_twice_area = sum(
                    contour[index][0] * contour[(index + 1) % len(contour)][1]
                    - contour[(index + 1) % len(contour)][0] * contour[index][1]
                    for index in range(len(contour))
                )
                if ((contour_index == 0 and signed_twice_area < 0.0)
                        or (contour_index > 0 and signed_twice_area > 0.0)):
                    contour = list(reversed(contour))
                contour_ids = vtk.vtkIdList()
                contour_ids.SetNumberOfIds(len(contour) + 1)
                start = contour_points.GetNumberOfPoints()
                for point_index, (x, y) in enumerate(contour):
                    contour_points.InsertNextPoint(x, y, 0.0)
                    contour_ids.SetId(point_index, start + point_index)
                contour_ids.SetId(len(contour), start)
                contour_lines.InsertNextCell(contour_ids)
                area = abs(signed_twice_area) * 0.5
                expected_area += area if contour_index == 0 else -area

            if expected_area <= 0.0:
                raise ValueError("ProfilePts contours must enclose a positive area")

            contour_data = vtk.vtkPolyData()
            contour_data.SetPoints(contour_points)
            contour_data.SetLines(contour_lines)
            triangulator = vtk.vtkContourTriangulator()
            triangulator.SetInputData(contour_data)
            triangulator.Update()
            cap = triangulator.GetOutput()
            cap_area = 0.0
            for cell_index in range(cap.GetNumberOfCells()):
                cell = cap.GetCell(cell_index)
                if cell.GetNumberOfPoints() != 3:
                    raise ValueError("ProfilePts contours could not be triangulated")
                p0, p1, p2 = (cap.GetPoint(cell.GetPointId(index)) for index in range(3))
                a = (p1[0] - p0[0], p1[1] - p0[1])
                b = (p2[0] - p0[0], p2[1] - p0[1])
                cap_area += abs(a[0] * b[1] - a[1] * b[0]) * 0.5
            if (cap.GetNumberOfCells() == 0
                or not math.isclose(cap_area, expected_area, rel_tol=1e-6,
                            abs_tol=max(1.0, expected_area) * 1e-10)):
                raise ValueError("ProfilePts contours must form one outer boundary with enclosed holes")
            caps.AddInputData(cap)

        caps.Update()
        cap = caps.GetOutput()

        # Build orthonormal basis in the plane
        n = list(_unit_vector3(normal, "PlaneNormal"))
        # Find a vector not parallel to n
        ref = [1, 0, 0] if abs(n[1]) > 0.1 or abs(n[2]) > 0.1 else [0, 1, 0]
        # u = n × ref  (cross product)
        u = [n[1]*ref[2] - n[2]*ref[1],
             n[2]*ref[0] - n[0]*ref[2],
             n[0]*ref[1] - n[1]*ref[0]]
        um = math.hypot(*u)
        u = [v/um for v in u]
        # v = u × n
        v = [u[1]*n[2] - u[2]*n[1],
             u[2]*n[0] - u[0]*n[2],
             u[0]*n[1] - u[1]*n[0]]

        ox, oy, oz = origin
        vtk_pts = vtk.vtkPoints()
        for i in range(cap.GetNumberOfPoints()):
            px, py, _ = cap.GetPoint(i)
            wx = ox + px*u[0] + py*v[0]
            wy = oy + px*u[1] + py*v[1]
            wz = oz + px*u[2] + py*v[2]
            vtk_pts.InsertNextPoint(wx, wy, wz)
        poly = vtk.vtkPolyData()
        poly.SetPoints(vtk_pts)
        poly.SetPolys(cap.GetPolys())
        return poly

    def _rebuild(self, extrusion: vtk.vtkLinearExtrusionFilter | None = None) -> None:
        if extrusion is None:
            poly = self._profile_to_polydata()
            extrusion = self._make_extrusion(poly, self._depth, self._plane_normal)
        self._extrude = extrusion
        self._actor.GetMapper().SetInputConnection(self._extrude.GetOutputPort())
        self.refresh_appearance()

    # ── parameters ───────────────────────────────────────────────────
    def get_parameters(self) -> Dict[str, Any]:
        params = dict(
            Depth      = self._depth,
            Direction  = self._direction,
            Symmetric  = self._symmetric,
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
        if self.custom_color is not None:
            params["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return params

    def set_parameters(self, params: Dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise ValueError("Parameters must be a dictionary")
        depth = _finite_float(params.get("Depth", self._depth), "Depth")
        if depth <= 0.0:
            raise ValueError("Depth must be greater than zero")
        profile, _ = self._parse_profile_groups(params.get("ProfilePts", self._profile_pts))
        direction = self._normalize_direction(params.get("Direction", self._direction))
        symmetric_value = params.get("Symmetric", self._symmetric)
        symmetric = (
            symmetric_value.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(symmetric_value, str) else bool(symmetric_value)
        )
        origin = tuple(_finite_float(params.get(key, self._plane_origin[index]), key)
                       for index, key in enumerate(("PlaneOriginX", "PlaneOriginY", "PlaneOriginZ")))
        normal = tuple(_finite_float(params.get(key, self._plane_normal[index]), key)
                       for index, key in enumerate(("PlaneNormalX", "PlaneNormalY", "PlaneNormalZ")))
        _unit_vector3(normal, "PlaneNormal")
        delta = _position_delta(params, ("PositionX", "PositionY", "PositionZ"), self.sketch_reference_point())
        origin = _vector3(tuple(origin[index] + delta[index] for index in range(3)), "PlaneOrigin")
        opacity = _finite_float(params.get("Opacity", self.opacity), "Opacity")
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Opacity must be between zero and one")
        start, vector = self._extrusion_frame(origin, normal, depth, direction, symmetric)
        poly = self._profile_to_polydata(profile, start, normal)
        extrusion = self._make_extrusion(poly, depth, vector)

        self._depth = depth
        self._profile_pts = profile
        self._plane_origin = origin
        self._plane_normal = normal
        self._direction = direction
        self._symmetric = symmetric
        if isinstance(self.sketch_definition, dict) and any(delta):
            self.sketch_definition = deepcopy(self.sketch_definition)
            self.sketch_definition["plane_origin"] = list(origin)
        self.material = str(params.get("Material", self.material))
        self.opacity = opacity
        self.custom_color = _color_from_parameters(params, self.custom_color)
        self._rebuild(extrusion)

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
    profile_pts : point pairs or contours defining one outer profile and holes
    angle       : sweep angle in degrees (0–360)
    axis_pt1    : first point of revolution axis (world coords)
    axis_pt2    : second point of revolution axis (world coords)
    material    : material name
    """

    def __init__(self, name: str = "",
                 profile_pts=None,
                 angle: float = 360.0,
                 axis_pt1=(0.0, 0.0, 0.0),
                 axis_pt2=(1.0, 0.0, 0.0),
                 plane_origin=(0.0, 0.0, 0.0),
                 plane_normal=(0.0, 0.0, 1.0),
                 material: str = "PEC"):
        initial_profile = profile_pts if profile_pts is not None else [(2, 0), (5, 0), (5, 10), (2, 10)]
        self._profile_pts, self._profile_contours = self._parse_profile(initial_profile)
        self._angle = _finite_float(angle, "Angle")
        if not 0.0 < self._angle <= 360.0:
            raise ValueError("Angle must be greater than zero and at most 360 degrees")
        self._axis_pt1 = _vector3(axis_pt1, "AxisPt1")
        self._axis_pt2 = _vector3(axis_pt2, "AxisPt2")
        initial_axis_length = math.hypot(*(self._axis_pt2[index] - self._axis_pt1[index] for index in range(3)))
        if not math.isfinite(initial_axis_length) or initial_axis_length == 0.0:
            raise ValueError("Revolve axis endpoints must be distinct")
        self._plane_origin = _vector3(plane_origin, "PlaneOrigin")
        self._plane_normal = _vector3(plane_normal, "PlaneNormal")
        _unit_vector3(self._plane_normal, "PlaneNormal")
        super().__init__(name or _auto_name("Revolve"), material)

    @staticmethod
    def _parse_profile(value: Any) -> tuple[list, list[list[tuple[float, float]]]]:
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                raise ValueError("ProfilePts must be a literal sequence of point pairs or contours") from exc
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("ProfilePts must be a sequence of point pairs or contours")

        first = value[0]
        flat_profile = (
            isinstance(first, (list, tuple))
            and len(first) == 2
            and not isinstance(first[0], (list, tuple))
            and not isinstance(first[1], (list, tuple))
        )
        raw_contours = [value] if flat_profile else value
        contours = []
        for raw_contour in raw_contours:
            if not isinstance(raw_contour, (list, tuple)):
                raise ValueError("ProfilePts contours must be sequences of point pairs")
            contour = _profile_points(raw_contour, 3)
            if contour[0] == contour[-1]:
                contour.pop()
            if len(contour) < 3:
                raise ValueError("ProfilePts contours must contain at least three distinct vertices")
            if any(contour[index] == contour[(index + 1) % len(contour)]
                   for index in range(len(contour))):
                raise ValueError("ProfilePts contours must not contain zero-length edges")
            twice_area = sum(
                contour[index][0] * contour[(index + 1) % len(contour)][1]
                - contour[(index + 1) % len(contour)][0] * contour[index][1]
                for index in range(len(contour))
            )
            if not math.isfinite(twice_area) or twice_area == 0.0:
                raise ValueError("ProfilePts contours must enclose a finite, non-zero area")
            contours.append(contour)
        if not contours:
            raise ValueError("ProfilePts must contain at least one contour")

        profile = contours[0] if len(contours) == 1 else contours
        return profile, contours

    @staticmethod
    def _plane_basis(normal):
        import math
        n = list(normal)
        nm = math.hypot(*n)
        if not math.isfinite(nm) or nm == 0.0:
            raise ValueError("PlaneNormal must have a finite, non-zero length")
        n = [v/nm for v in n]
        ref = [1, 0, 0] if abs(n[1]) > 0.1 or abs(n[2]) > 0.1 else [0, 1, 0]
        u = [n[1]*ref[2] - n[2]*ref[1],
             n[2]*ref[0] - n[0]*ref[2],
             n[0]*ref[1] - n[1]*ref[0]]
        um = math.hypot(*u) or 1.0
        u = [v/um for v in u]
        v = [u[1]*n[2] - u[2]*n[1],
             u[2]*n[0] - u[0]*n[2],
             u[0]*n[1] - u[1]*n[0]]
        return n, u, v

    def _profile_local_and_transform(self, contours=None, plane_origin=None,
                                     plane_normal=None, axis_pt1=None, axis_pt2=None):
        """Map sketch contours to an axis-aligned radial/axial frame."""
        import math
        contours = self._profile_contours if contours is None else contours
        plane_origin = self._plane_origin if plane_origin is None else plane_origin
        plane_normal = self._plane_normal if plane_normal is None else plane_normal
        axis_pt1 = self._axis_pt1 if axis_pt1 is None else axis_pt1
        axis_pt2 = self._axis_pt2 if axis_pt2 is None else axis_pt2
        _, ub, vb = self._plane_basis(plane_normal)
        ox, oy, oz = plane_origin

        a1 = axis_pt1
        a2 = axis_pt2
        ad = (a2[0]-a1[0], a2[1]-a1[1], a2[2]-a1[2])
        am = math.hypot(*ad)
        if not math.isfinite(am) or am == 0.0:
            raise ValueError("Revolve axis endpoints must have a finite, non-zero separation")
        ax = (ad[0]/am, ad[1]/am, ad[2]/am)

        local_contours = []
        for contour_index, contour in enumerate(contours):
            local_contour = []
            for pu, pv in contour:
                wx = ox + pu*ub[0] + pv*vb[0]
                wy = oy + pu*ub[1] + pv*vb[1]
                wz = oz + pu*ub[2] + pv*vb[2]
                dx, dy, dz = wx-a1[0], wy-a1[1], wz-a1[2]
                axial = dx*ax[0] + dy*ax[1] + dz*ax[2]
                px = dx - axial*ax[0]
                py = dy - axial*ax[1]
                pz = dz - axial*ax[2]
                radial = math.sqrt(px*px + py*py + pz*pz)
                local_contour.append((radial, axial))
            twice_area = sum(
                local_contour[index][0] * local_contour[(index + 1) % len(local_contour)][1]
                - local_contour[(index + 1) % len(local_contour)][0] * local_contour[index][1]
                for index in range(len(local_contour))
            )
            if not math.isfinite(twice_area):
                raise ValueError("ProfilePts must map to finite coordinates relative to the revolve axis")
            if ((contour_index == 0 and twice_area < 0.0)
                    or (contour_index > 0 and twice_area > 0.0)):
                local_contour.reverse()
            local_contours.append(local_contour)

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
        lm = math.hypot(*lx) or 1.0
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
        return local_contours, m

    def _build(self) -> None:
        local_contours, user_matrix = self._profile_local_and_transform()
        self._revolve, self._revolve_normals = self._make_revolved_mesh(local_contours, self._angle)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._revolve_normals.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._actor.SetUserMatrix(user_matrix)
        self._apply_appearance(self._actor)

    @staticmethod
    def _profile_to_polydata(local_contours) -> vtk.vtkPolyData:
        """Create closed contour lines in the local radial/axial plane."""
        vtk_pts = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        for contour in local_contours:
            start = vtk_pts.GetNumberOfPoints()
            for radial, axial in contour:
                vtk_pts.InsertNextPoint(radial, 0.0, axial)
            ids = vtk.vtkIdList()
            ids.SetNumberOfIds(len(contour) + 1)
            for index in range(len(contour)):
                ids.SetId(index, start + index)
            ids.SetId(len(contour), start)
            lines.InsertNextCell(ids)
        poly = vtk.vtkPolyData()
        poly.SetPoints(vtk_pts)
        poly.SetLines(lines)
        return poly

    @classmethod
    def _make_revolved_mesh(cls, local_contours, angle):
        """Sweep contour boundaries and cap partial revolutions with the profile face."""
        profile_points = vtk.vtkPoints()
        profile_lines = vtk.vtkCellArray()
        expected_area = 0.0
        for contour_index, contour in enumerate(local_contours):
            start = profile_points.GetNumberOfPoints()
            ids = vtk.vtkIdList()
            ids.SetNumberOfIds(len(contour) + 1)
            for index, (radial, axial) in enumerate(contour):
                profile_points.InsertNextPoint(radial, axial, 0.0)
                ids.SetId(index, start + index)
            ids.SetId(len(contour), start)
            profile_lines.InsertNextCell(ids)
            twice_area = sum(
                contour[index][0] * contour[(index + 1) % len(contour)][1]
                - contour[(index + 1) % len(contour)][0] * contour[index][1]
                for index in range(len(contour))
            )
            expected_area += abs(twice_area) * (0.5 if contour_index == 0 else -0.5)

        degenerate_section = expected_area == 0.0 and len(local_contours) == 1
        if expected_area < 0.0 or (expected_area == 0.0 and not degenerate_section):
            raise ValueError("ProfilePts contours must enclose a positive area")
        cap = None
        if not degenerate_section:
            profile = vtk.vtkPolyData()
            profile.SetPoints(profile_points)
            profile.SetLines(profile_lines)
            triangulator = vtk.vtkContourTriangulator()
            triangulator.SetInputData(profile)
            triangulator.Update()
            cap = triangulator.GetOutput()
            cap_area = 0.0
            for cell_index in range(cap.GetNumberOfCells()):
                cell = cap.GetCell(cell_index)
                if cell.GetNumberOfPoints() != 3:
                    raise ValueError("ProfilePts contours could not be triangulated")
                p0, p1, p2 = (cap.GetPoint(cell.GetPointId(index)) for index in range(3))
                cap_area += abs(
                    (p1[0] - p0[0]) * (p2[1] - p0[1])
                    - (p1[1] - p0[1]) * (p2[0] - p0[0])
                ) * 0.5
            if (cap.GetNumberOfCells() == 0
                    or not math.isclose(cap_area, expected_area, rel_tol=1e-6,
                                        abs_tol=max(1.0, expected_area) * 1e-10)):
                raise ValueError("ProfilePts contours must form one outer boundary with enclosed holes")

        revolve = vtk.vtkRotationalExtrusionFilter()
        revolve.SetInputData(cls._profile_to_polydata(local_contours))
        revolve.SetAngle(angle)
        revolve.SetResolution(120)
        revolve.Update()

        append = vtk.vtkAppendPolyData()
        append.AddInputConnection(revolve.GetOutputPort())
        if angle < 360.0 and cap is not None:
            cap_points = vtk.vtkPoints()
            cap_polys = vtk.vtkCellArray()
            for angle_index, sweep_angle in enumerate((0.0, angle)):
                radians = math.radians(sweep_angle)
                cosine, sine = math.cos(radians), math.sin(radians)
                reverse = angle_index == 1
                for cell_index in range(cap.GetNumberOfCells()):
                    cell = cap.GetCell(cell_index)
                    triangle = [cap.GetPoint(cell.GetPointId(index)) for index in range(3)]
                    twice_area = (
                        (triangle[1][0] - triangle[0][0]) * (triangle[2][1] - triangle[0][1])
                        - (triangle[1][1] - triangle[0][1]) * (triangle[2][0] - triangle[0][0])
                    )
                    if (twice_area < 0.0) != reverse:
                        triangle[1], triangle[2] = triangle[2], triangle[1]
                    ids = vtk.vtkIdList()
                    ids.SetNumberOfIds(3)
                    for index, (radial, axial, _unused) in enumerate(triangle):
                        ids.SetId(index, cap_points.InsertNextPoint(
                            radial * cosine, radial * sine, axial
                        ))
                    cap_polys.InsertNextCell(ids)
            cap_polydata = vtk.vtkPolyData()
            cap_polydata.SetPoints(cap_points)
            cap_polydata.SetPolys(cap_polys)
            append.AddInputData(cap_polydata)
        append.Update()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputConnection(append.GetOutputPort())
        normals.ConsistencyOn()
        normals.AutoOrientNormalsOff()
        normals.SplittingOff()
        normals.Update()
        triangles = vtk.vtkTriangleFilter()
        triangles.SetInputConnection(normals.GetOutputPort())
        triangles.Update()
        mass = vtk.vtkMassProperties()
        mass.SetInputConnection(triangles.GetOutputPort())
        mass.Update()
        volume = mass.GetVolume()
        if (normals.GetOutput().GetNumberOfCells() == 0
            or not math.isfinite(volume)
            or (not degenerate_section and volume <= 0.0)):
            raise ValueError("ProfilePts could not produce a closed revolved solid")
        return revolve, normals

    def get_parameters(self) -> Dict[str, Any]:
        params = dict(
            Angle      = self._angle,
            AxisPt1X   = self._axis_pt1[0],
            AxisPt1Y   = self._axis_pt1[1],
            AxisPt1Z   = self._axis_pt1[2],
            AxisPt2X   = self._axis_pt2[0],
            AxisPt2Y   = self._axis_pt2[1],
            AxisPt2Z   = self._axis_pt2[2],
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
        if self.custom_color is not None:
            params["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return params

    def set_parameters(self, params: Dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise ValueError("Parameters must be a dictionary")
        angle = _finite_float(params.get("Angle", self._angle), "Angle")
        if not 0.0 < angle <= 360.0:
            raise ValueError("Angle must be greater than zero and at most 360 degrees")
        profile, contours = self._parse_profile(params.get("ProfilePts", self._profile_pts))
        axis_pt1 = tuple(_finite_float(params.get(key, self._axis_pt1[index]), key)
                         for index, key in enumerate(("AxisPt1X", "AxisPt1Y", "AxisPt1Z")))
        axis_pt2 = tuple(_finite_float(params.get(key, self._axis_pt2[index]), key)
                         for index, key in enumerate(("AxisPt2X", "AxisPt2Y", "AxisPt2Z")))
        axis_length = math.hypot(*(axis_pt2[index] - axis_pt1[index] for index in range(3)))
        if not math.isfinite(axis_length) or axis_length == 0.0:
            raise ValueError("Revolve axis endpoints must have a finite, non-zero separation")
        origin = tuple(_finite_float(params.get(key, self._plane_origin[index]), key)
                       for index, key in enumerate(("PlaneOriginX", "PlaneOriginY", "PlaneOriginZ")))
        normal = tuple(_finite_float(params.get(key, self._plane_normal[index]), key)
                       for index, key in enumerate(("PlaneNormalX", "PlaneNormalY", "PlaneNormalZ")))
        _unit_vector3(normal, "PlaneNormal")
        delta = _position_delta(params, ("PositionX", "PositionY", "PositionZ"), self.sketch_reference_point())
        origin = _vector3(tuple(origin[index] + delta[index] for index in range(3)), "PlaneOrigin")
        axis_pt1 = _vector3(tuple(axis_pt1[index] + delta[index] for index in range(3)), "AxisPt1")
        axis_pt2 = _vector3(tuple(axis_pt2[index] + delta[index] for index in range(3)), "AxisPt2")
        opacity = _finite_float(params.get("Opacity", self.opacity), "Opacity")
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Opacity must be between zero and one")

        local_contours, user_matrix = self._profile_local_and_transform(
            contours, origin, normal, axis_pt1, axis_pt2
        )
        revolve, revolve_normals = self._make_revolved_mesh(local_contours, angle)
        material = str(params.get("Material", self.material))
        custom_color = _color_from_parameters(params, self.custom_color)
        sketch_definition = self.sketch_definition
        if isinstance(sketch_definition, dict) and any(delta):
            sketch_definition = deepcopy(sketch_definition)
            sketch_definition["plane_origin"] = list(origin)

        self._angle = angle
        self._profile_pts = profile
        self._profile_contours = contours
        self._axis_pt1 = axis_pt1
        self._axis_pt2 = axis_pt2
        self._plane_origin = origin
        self._plane_normal = normal
        self.sketch_definition = sketch_definition
        self.material = material
        self.opacity = opacity
        self.custom_color = custom_color
        self._revolve = revolve
        self._revolve_normals = revolve_normals
        self._actor.GetMapper().SetInputConnection(revolve_normals.GetOutputPort())
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
        self.boolean_mesh_reduction = 0.0
        self.boolean_source_names = [str(x) for x in (boolean_source_names or []) if str(x)]
        self.boolean_sources_data = [x for x in (boolean_sources_data or []) if isinstance(x, dict)]
        self.circular_plate = False
        self.circular_plate_center = (0.0, 0.0, 0.0)
        self.circular_plate_radius = 1.0
        self.circular_plate_thickness = 0.1
        self.circular_plate_normal = (0.0, 0.0, 1.0)
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

    @staticmethod
    def _make_circular_plate_polydata(center, radius, thickness, normal):
        unit_normal = _unit_vector3(normal, "CircularPlateNormal")
        center = _vector3(center, "CircularPlateCenter")
        radius = _finite_float(radius, "Radius")
        thickness = _finite_float(thickness, "Thickness")
        if radius <= 0.0:
            raise ValueError("Radius must be greater than zero")
        if thickness <= 0.0:
            raise ValueError("Thickness must be greater than zero")

        source = vtk.vtkRegularPolygonSource()
        source.SetNumberOfSides(96)
        source.SetRadius(radius)
        source.SetCenter(*(
            center[index] - unit_normal[index] * thickness * 0.5
            for index in range(3)
        ))
        source.SetNormal(*unit_normal)
        source.GeneratePolygonOn()
        source.Update()

        extrusion = vtk.vtkLinearExtrusionFilter()
        extrusion.SetInputConnection(source.GetOutputPort())
        extrusion.SetExtrusionTypeToVectorExtrusion()
        extrusion.SetVector(*(value * thickness for value in unit_normal))
        extrusion.CappingOn()
        extrusion.Update()
        polydata = vtk.vtkPolyData()
        polydata.DeepCopy(extrusion.GetOutput())
        return polydata

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
        if self.circular_plate:
            p.update({
                "CircularPlate": True,
                "CenterX": self.circular_plate_center[0],
                "CenterY": self.circular_plate_center[1],
                "CenterZ": self.circular_plate_center[2],
                "Radius": self.circular_plate_radius,
                "Thickness": self.circular_plate_thickness,
                "CircularPlateNormalX": self.circular_plate_normal[0],
                "CircularPlateNormalY": self.circular_plate_normal[1],
                "CircularPlateNormalZ": self.circular_plate_normal[2],
            })
        ox, oy, oz = self.step_export_offset
        if abs(float(ox)) > 1e-12 or abs(float(oy)) > 1e-12 or abs(float(oz)) > 1e-12:
            p["StepExportOffset"] = [float(ox), float(oy), float(oz)]
        if self.boolean_op:
            p["BooleanOperation"] = str(self.boolean_op)
        if self.boolean_mesh_reduction > 0.0:
            p["BooleanMeshReduction"] = float(self.boolean_mesh_reduction)
        if self.boolean_source_names:
            p["BooleanSourceNames"] = list(self.boolean_source_names)
        if self.boolean_sources_data:
            p["BooleanSourcesData"] = list(self.boolean_sources_data)
        if self.custom_color is not None:
            p["Color"] = f"#{int(self.custom_color[0]*255):02x}{int(self.custom_color[1]*255):02x}{int(self.custom_color[2]*255):02x}"
        return p

    def set_parameters(self, params: Dict[str, Any]) -> None:
        circular_plate_value = params.get("CircularPlate", self.circular_plate)
        if isinstance(circular_plate_value, str):
            circular_plate_value = circular_plate_value.strip().lower() in {
                "true", "1", "yes", "on",
            }
        circular_plate = bool(circular_plate_value)
        circular_plate_data = None
        if circular_plate:
            center = tuple(
                _finite_float(params.get(key, self.circular_plate_center[index]), key)
                for index, key in enumerate(("CenterX", "CenterY", "CenterZ"))
            )
            radius = _finite_float(params.get("Radius", self.circular_plate_radius), "Radius")
            thickness = _finite_float(
                params.get("Thickness", self.circular_plate_thickness), "Thickness"
            )
            normal = tuple(
                _finite_float(
                    params.get(key, self.circular_plate_normal[index]), key
                )
                for index, key in enumerate((
                    "CircularPlateNormalX", "CircularPlateNormalY", "CircularPlateNormalZ",
                ))
            )
            polydata = self._make_circular_plate_polydata(center, radius, thickness, normal)
            circular_plate_data = (center, radius, thickness, _unit_vector3(normal, "CircularPlateNormal"), polydata)

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
        self.circular_plate = circular_plate
        if circular_plate_data is not None:
            (
                self.circular_plate_center,
                self.circular_plate_radius,
                self.circular_plate_thickness,
                self.circular_plate_normal,
                self._polydata,
            ) = circular_plate_data
            self.plate_role = True
            self._actor.GetMapper().SetInputData(self._polydata)
        try:
            reduction = float(params.get("BooleanMeshReduction", self.boolean_mesh_reduction))
            self.boolean_mesh_reduction = reduction if 0.0 <= reduction <= 0.8 else 0.0
        except (TypeError, ValueError):
            self.boolean_mesh_reduction = 0.0
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

