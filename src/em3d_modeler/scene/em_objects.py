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

_OBJECT_COUNTER: Dict[str, int] = {}


def _auto_name(prefix: str) -> str:
    _OBJECT_COUNTER[prefix] = _OBJECT_COUNTER.get(prefix, 0) + 1
    return f"{prefix}_{_OBJECT_COUNTER[prefix]}"


class EMObject:
    """Base class for all EM scene objects."""

    def __init__(self, name: str, material: str = "PEC"):
        self.name = name
        self.material = material
        self.opacity: float = 0.85
        self._selected: bool = False
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

    def _base_color(self) -> tuple:
        return MATERIAL_COLORS.get(self.material, (0.75, 0.75, 0.85))

    def _apply_appearance(self, actor: vtk.vtkActor) -> None:
        prop = actor.GetProperty()
        prop.SetColor(*self._base_color())
        prop.SetOpacity(self.opacity)
        prop.SetRepresentationToSurface()
        prop.EdgeVisibilityOn()
        prop.SetEdgeColor(0.1, 0.1, 0.1)
        prop.SetLineWidth(1.2)

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        if self._actor:
            prop = self._actor.GetProperty()
            if sel:
                prop.SetColor(1.0, 0.78, 0.0)
                prop.SetLineWidth(2.5)
                prop.SetEdgeColor(0.8, 0.4, 0.0)
            else:
                self._apply_appearance(self._actor)

    def refresh_appearance(self) -> None:
        if self._actor:
            self._apply_appearance(self._actor)
            if self._selected:
                self.set_selected(True)


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
        return dict(X1=self.x1, Y1=self.y1, Z1=self.z1,
                    X2=self.x2, Y2=self.y2, Z2=self.z2,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.x1 = float(params.get("X1", self.x1))
        self.y1 = float(params.get("Y1", self.y1))
        self.z1 = float(params.get("Z1", self.z1))
        self.x2 = float(params.get("X2", self.x2))
        self.y2 = float(params.get("Y2", self.y2))
        self.z2 = float(params.get("Z2", self.z2))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        xmin, xmax = sorted([self.x1, self.x2])
        ymin, ymax = sorted([self.y1, self.y2])
        zmin, zmax = sorted([self.z1, self.z2])
        return (
            f'Block "{self.name}"\n'
            f'  Material = "{self.material}"\n'
            f"  XRange = [{xmin}, {xmax}]\n"
            f"  YRange = [{ymin}, {ymax}]\n"
            f"  ZRange = [{zmin}, {zmax}]\n"
            "EndBlock\n"
        )


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
        return (
            f'Cylinder "{self.name}"\n'
            f'  Material = "{self.material}"\n'
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]\n"
            f"  Radius = {abs(self.radius)}\n"
            f"  Height = {abs(self.height)}\n"
            f'  Axis = "{self.axis}"\n'
            "EndCylinder\n"
        )


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
        return (
            f'Cone "{self.name}"\n'
            f'  Material = "{self.material}"\n'
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]\n"
            f"  Radius = {abs(self.radius)}\n"
            f"  Height = {abs(self.height)}\n"
            f'  Axis = "{self.axis}"\n'
            "EndCone\n"
        )


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
        return dict(CenterX=self.cx, CenterY=self.cy, CenterZ=self.cz,
                    Radius=self.radius,
                    Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.cx = float(params.get("CenterX", self.cx))
        self.cy = float(params.get("CenterY", self.cy))
        self.cz = float(params.get("CenterZ", self.cz))
        self.radius = float(params.get("Radius", self.radius))
        self.material = params.get("Material", self.material)
        self.opacity = float(params.get("Opacity", self.opacity))
        self._refresh_source()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        return (
            f'Sphere "{self.name}"\n'
            f'  Material = "{self.material}"\n'
            f"  Center = [{self.cx}, {self.cy}, {self.cz}]\n"
            f"  Radius = {abs(self.radius)}\n"
            "EndSphere\n"
        )
