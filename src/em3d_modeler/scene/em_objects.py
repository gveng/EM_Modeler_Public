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
        return (
            f'# ExtrudedObject "{self.name}" — export via EMERGE extrude command\n'
            f'# Depth = {self._depth}, Material = "{self.material}"\n'
        )


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
                 material: str = "PEC"):
        self._profile_pts = list(profile_pts or [(2,0),(5,0),(5,10),(2,10)])
        self._angle       = angle
        self._axis_pt1    = tuple(axis_pt1)
        self._axis_pt2    = tuple(axis_pt2)
        super().__init__(name or _auto_name("Revolve"), material)

    def _build(self) -> None:
        poly = self._profile_to_polydata()
        self._revolve = vtk.vtkRotationalExtrusionFilter()
        self._revolve.SetInputData(poly)
        self._revolve.SetAngle(self._angle)
        self._revolve.SetResolution(60)
        self._revolve.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(self._revolve.GetOutputPort())
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def _profile_to_polydata(self) -> vtk.vtkPolyData:
        """Convert 2-D profile to polydata; vtkRotationalExtrusionFilter
        revolves around the *local* Y axis, so the profile sits in the XY plane."""
        vtk_pts = vtk.vtkPoints()
        lines   = vtk.vtkCellArray()
        n = len(self._profile_pts)
        for px, py in self._profile_pts:
            vtk_pts.InsertNextPoint(px, py, 0.0)
        for i in range(n - 1):
            line = vtk.vtkLine()
            line.GetPointIds().SetId(0, i)
            line.GetPointIds().SetId(1, i + 1)
            lines.InsertNextCell(line)
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
        self._revolve.SetAngle(self._angle)
        self._revolve.Update()
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        return (
            f'# RevolvedObject "{self.name}" — export via EMERGE revolve command\n'
            f'# Angle = {self._angle}, Material = "{self.material}"\n'
        )


# ═══════════════════════════════════════════════════════════════════════════════
class MeshObject(EMObject):
    """Generic triangulated mesh (e.g. imported from STEP via pythonOCC)."""

    def __init__(self, name: str = "",
                 polydata: "vtk.vtkPolyData | None" = None,
                 material: str = "PEC"):
        self._polydata = polydata
        super().__init__(name or _auto_name("Mesh"), material)

    def _build(self) -> None:
        if self._polydata is None:
            self._polydata = vtk.vtkPolyData()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(self._polydata)
        self._actor = vtk.vtkActor()
        self._actor.SetMapper(mapper)
        self._apply_appearance(self._actor)

    def get_parameters(self) -> Dict[str, Any]:
        return dict(Material=self.material, Opacity=self.opacity)

    def set_parameters(self, params: Dict[str, Any]) -> None:
        self.material = params.get("Material", self.material)
        self.opacity  = float(params.get("Opacity", self.opacity))
        self.refresh_appearance()

    def to_emerge_script(self) -> str:
        return f'# MeshObject "{self.name}" — Material = "{self.material}"\n'

