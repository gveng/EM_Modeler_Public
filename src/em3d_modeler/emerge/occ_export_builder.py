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

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import re
import vtk


_STEP_INDEX_RE = re.compile(r"(\d+)(?!.*\d)")


def _is_mesh_object(obj: Any) -> bool:
    return type(obj).__name__ == "MeshObject"


def _bounds_box_limits(box: Any) -> tuple[float, float, float, float, float, float]:
    try:
        return tuple(float(value) for value in box.Get())
    except TypeError:
        minimum = box.CornerMin()
        maximum = box.CornerMax()
        return (
            float(minimum.X()),
            float(minimum.Y()),
            float(minimum.Z()),
            float(maximum.X()),
            float(maximum.Y()),
            float(maximum.Z()),
        )


def _build_occ_box_shape(obj: Any) -> Any | None:
    try:
        x1 = float(getattr(obj, "x1"))
        y1 = float(getattr(obj, "y1"))
        z1 = float(getattr(obj, "z1"))
        x2 = float(getattr(obj, "x2"))
        y2 = float(getattr(obj, "y2"))
        z2 = float(getattr(obj, "z2"))
    except Exception:
        return None

    xmin, xmax = sorted((x1, x2))
    ymin, ymax = sorted((y1, y2))
    zmin, zmax = sorted((z1, z2))

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Pnt
                from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
                shape = BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)).Shape()
                return _apply_actor_transform(shape, obj)

            from OCC.Core.gp import gp_Pnt
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
            shape = BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)).Shape()
            return _apply_actor_transform(shape, obj)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _build_occ_ipc2581_shape(obj: Any) -> Any:
    geometry = getattr(obj, "ipc2581_step_geometry", None)
    if not isinstance(geometry, dict) or geometry.get("schema") != "ipc2581-profile-v1":
        raise ValueError("IPC-2581 object has invalid native STEP geometry metadata")
    solids = geometry.get("solids")
    if not isinstance(solids, list) or not solids:
        raise ValueError("IPC-2581 native STEP geometry contains no solids")

    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.BRep import BRep_Builder
                from OCP.BRepBuilderAPI import (
                    BRepBuilderAPI_MakeFace,
                    BRepBuilderAPI_MakePolygon,
                )
                from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
                from OCP.BRepPrimAPI import (
                    BRepPrimAPI_MakeCylinder,
                    BRepPrimAPI_MakePrism,
                )
                from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
                from OCP.TopoDS import TopoDS_Compound
            else:
                from OCC.Core.BRep import BRep_Builder
                from OCC.Core.BRepBuilderAPI import (
                    BRepBuilderAPI_MakeFace,
                    BRepBuilderAPI_MakePolygon,
                )
                from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
                from OCC.Core.BRepPrimAPI import (
                    BRepPrimAPI_MakeCylinder,
                    BRepPrimAPI_MakePrism,
                )
                from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
                from OCC.Core.TopoDS import TopoDS_Compound

            def make_wire(points: Any, z: float) -> Any:
                if not isinstance(points, list) or len(points) < 3:
                    raise ValueError("PCB profile rings need at least three points")
                wire = BRepBuilderAPI_MakePolygon()
                for point in points:
                    if not isinstance(point, (list, tuple)) or len(point) != 2:
                        raise ValueError("PCB profile points must be finite x/y pairs")
                    x, y = float(point[0]), float(point[1])
                    if not math.isfinite(x) or not math.isfinite(y):
                        raise ValueError("PCB profile coordinates must be finite")
                    wire.Add(gp_Pnt(x, y, z))
                wire.Close()
                if not wire.IsDone():
                    raise ValueError("OpenCascade could not build a closed PCB profile wire")
                return wire.Wire()

            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            added = 0
            for solid in solids:
                if not isinstance(solid, dict):
                    raise ValueError("PCB native STEP solid entry must be an object")
                kind = solid.get("kind")
                if kind == "extrusion":
                    z_bottom = float(solid["z_bottom"])
                    z_top = float(solid["z_top"])
                    if not math.isfinite(z_bottom) or not math.isfinite(z_top) or z_top <= z_bottom:
                        raise ValueError("PCB extrusion has invalid z bounds")
                    profiles = solid.get("profiles")
                    if not isinstance(profiles, list) or not profiles:
                        raise ValueError("PCB extrusion contains no profile rings")
                    for profile in profiles:
                        if not isinstance(profile, dict):
                            raise ValueError("PCB extrusion profile must be an object")
                        outer_wire = make_wire(profile.get("outer"), z_bottom)
                        face_maker = BRepBuilderAPI_MakeFace(outer_wire)
                        holes = profile.get("holes", [])
                        if not isinstance(holes, list):
                            raise ValueError("PCB profile holes must be a list")
                        for hole in holes:
                            face_maker.Add(make_wire(hole, z_bottom))
                        if not face_maker.IsDone():
                            raise ValueError("OpenCascade could not build a PCB profile face")
                        prism = BRepPrimAPI_MakePrism(
                            face_maker.Face(),
                            gp_Vec(0.0, 0.0, z_top - z_bottom),
                        )
                        if hasattr(prism, "IsDone") and not prism.IsDone():
                            raise ValueError("OpenCascade could not extrude a PCB profile")
                        builder.Add(compound, prism.Shape())
                        added += 1
                elif kind == "cylinder_tube":
                    center = solid.get("center")
                    if not isinstance(center, (list, tuple)) or len(center) != 2:
                        raise ValueError("PCB via barrel center must be an x/y pair")
                    x, y = float(center[0]), float(center[1])
                    z_bottom, z_top = float(solid["z_bottom"]), float(solid["z_top"])
                    inner_radius = float(solid["inner_radius"])
                    outer_radius = float(solid["outer_radius"])
                    if (
                        not all(math.isfinite(value) for value in (
                            x, y, z_bottom, z_top, inner_radius, outer_radius,
                        ))
                        or z_top <= z_bottom
                        or inner_radius <= 0
                        or outer_radius <= inner_radius
                    ):
                        raise ValueError("PCB via barrel has invalid dimensions")
                    axis = gp_Ax2(gp_Pnt(x, y, z_bottom), gp_Dir(0.0, 0.0, 1.0))
                    outer = BRepPrimAPI_MakeCylinder(
                        axis, outer_radius, z_top - z_bottom
                    ).Shape()
                    exported_displacement = extended_displacement
                    inner = BRepPrimAPI_MakeCylinder(
                        axis, inner_radius, z_top - z_bottom
                    ).Shape()
                    cut = BRepAlgoAPI_Cut(outer, inner)
                    cut.Build()
                    if hasattr(cut, "IsDone") and not cut.IsDone():
                        raise ValueError("OpenCascade could not hollow the PCB via barrel")
                    builder.Add(compound, cut.Shape())
                    added += 1
                else:
                    raise ValueError(f"Unsupported IPC-2581 STEP solid kind: {kind!r}")
            if added == 0:
                raise ValueError("IPC-2581 native STEP geometry produced no solids")
            return _apply_actor_transform(compound, obj)
        except ImportError:
            continue
        except Exception as exc:
            raise RuntimeError(f"Unable to build IPC-2581 native STEP geometry: {exc}") from exc

    raise RuntimeError("IPC-2581 native STEP export requires OCP or pythonOCC-core")


def _build_occ_plate_shape(obj: Any) -> Any | None:
    try:
        x1, x2 = sorted((float(obj.x1), float(obj.x2)))
        y1, y2 = sorted((float(obj.y1), float(obj.y2)))
        z1, z2 = sorted((float(obj.z1), float(obj.z2)))
    except Exception:
        return None

    spans = (x2 - x1, y2 - y1, z2 - z1)
    normal_axis = min(range(3), key=lambda index: abs(spans[index]))
    corners = {
        0: ((x1, y1, z1), (x1, y2, z1), (x1, y2, z2), (x1, y1, z2)),
        1: ((x1, y1, z1), (x2, y1, z1), (x2, y1, z2), (x1, y1, z2)),
        2: ((x1, y1, z1), (x2, y1, z1), (x2, y2, z1), (x1, y2, z1)),
    }[normal_axis]

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Pnt
                from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
            else:
                from OCC.Core.gp import gp_Pnt
                from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
            wire = BRepBuilderAPI_MakePolygon()
            for point in corners:
                wire.Add(gp_Pnt(*point))
            wire.Close()
            if not wire.IsDone():
                return None
            face = BRepBuilderAPI_MakeFace(wire.Wire())
            if not face.IsDone():
                return None
            return _apply_actor_transform(face.Face(), obj)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _translate_shape_local(shape: Any, dx: float, dy: float, dz: float) -> Any:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Trsf, gp_Vec
                from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
                tr = gp_Trsf()
                tr.SetTranslation(gp_Vec(dx, dy, dz))
                return BRepBuilderAPI_Transform(shape, tr, True).Shape()

            from OCC.Core.gp import gp_Trsf, gp_Vec
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
            tr = gp_Trsf()
            tr.SetTranslation(gp_Vec(dx, dy, dz))
            return BRepBuilderAPI_Transform(shape, tr, True).Shape()
        except ImportError:
            continue
        except Exception:
            return shape
    return shape


def _build_occ_cylinder_shape(obj: Any) -> Any | None:
    try:
        radius = abs(float(getattr(obj, "radius")))
        height = abs(float(getattr(obj, "height")))
    except Exception:
        return None
    if radius <= 0.0 or height <= 0.0:
        return None

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
                from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
                axis_frame = gp_Ax2(
                        gp_Pnt(0.0, -0.5 * height, 0.0),
                        gp_Dir(0.0, 1.0, 0.0),
                )
                shape = BRepPrimAPI_MakeCylinder(axis_frame, radius, height).Shape()
                return _apply_actor_transform(shape, obj)

            from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
            axis_frame = gp_Ax2(
                gp_Pnt(0.0, -0.5 * height, 0.0),
                gp_Dir(0.0, 1.0, 0.0),
            )
            shape = BRepPrimAPI_MakeCylinder(axis_frame, radius, height).Shape()
            return _apply_actor_transform(shape, obj)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _build_occ_cone_shape(obj: Any) -> Any | None:
    try:
        radius = abs(float(getattr(obj, "radius")))
        height = abs(float(getattr(obj, "height")))
        center = (
            float(getattr(obj, "cx")),
            float(getattr(obj, "cy")),
            float(getattr(obj, "cz")),
        )
        axis = str(getattr(obj, "axis", "Z")).upper()
    except Exception:
        return None
    if radius <= 0.0 or height <= 0.0:
        return None

    direction = {
        "X": (1.0, 0.0, 0.0),
        "Y": (0.0, 1.0, 0.0),
        "Z": (0.0, 0.0, 1.0),
    }.get(axis)
    if direction is None:
        return None
    base = tuple(center[index] - 0.5 * height * direction[index] for index in range(3))

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
                from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
                axis_frame = gp_Ax2(
                    gp_Pnt(*base),
                    gp_Dir(*direction),
                )
                shape = BRepPrimAPI_MakeCone(axis_frame, radius, 0.0, height).Shape()
                return _apply_actor_transform(shape, obj)

            from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCone
            axis_frame = gp_Ax2(
                gp_Pnt(*base),
                gp_Dir(*direction),
            )
            shape = BRepPrimAPI_MakeCone(axis_frame, radius, 0.0, height).Shape()
            return _apply_actor_transform(shape, obj)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _build_occ_sphere_shape(obj: Any) -> Any | None:
    try:
        radius = abs(float(getattr(obj, "radius")))
    except Exception:
        return None
    if radius <= 0.0:
        return None

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
                shape = BRepPrimAPI_MakeSphere(radius).Shape()
                return _apply_actor_transform(shape, obj)

            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
            shape = BRepPrimAPI_MakeSphere(radius).Shape()
            return _apply_actor_transform(shape, obj)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _topods_cast(topods: Any, shape_type: str) -> Any:
    cast = getattr(topods, f"{shape_type}_s", None)
    if cast is None:
        cast = getattr(topods, shape_type, None)
    if cast is None:
        raise AttributeError(f"TopoDS binding does not provide a {shape_type} cast")
    return cast


def _circle_arcs_cover_full_turn(
    arcs: list[tuple[float, float, float]], angle_tolerance: float
) -> bool:
    full_turn = 2.0 * math.pi
    intervals = []
    for start_angle, end_angle, span in arcs:
        if span >= full_turn - angle_tolerance:
            return True
        if span <= angle_tolerance or span > full_turn + angle_tolerance:
            continue

        forward_span = (end_angle - start_angle) % full_turn
        if abs(forward_span - span) <= angle_tolerance:
            coverage_start = start_angle
        elif abs((full_turn - forward_span) - span) <= angle_tolerance:
            coverage_start = end_angle
        else:
            continue

        coverage_start %= full_turn
        coverage_end = coverage_start + span
        if coverage_end <= full_turn:
            intervals.append((coverage_start, coverage_end))
        else:
            intervals.append((coverage_start, full_turn))
            intervals.append((0.0, coverage_end - full_turn))

    if not intervals:
        return False
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + angle_tolerance:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    gaps = [
        merged[index + 1][0] - merged[index][1]
        for index in range(len(merged) - 1)
    ]
    gaps.append(merged[0][0] + full_turn - merged[-1][1])
    return max(gaps) <= angle_tolerance


def _iter_solids(shape: Any) -> List[Any]:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopAbs import TopAbs_SOLID
                from OCP.TopoDS import TopoDS
                cast_solid = _topods_cast(TopoDS, "Solid")
                exp = TopExp_Explorer(shape, TopAbs_SOLID)
                out: List[Any] = []
                while exp.More():
                    out.append(cast_solid(exp.Current()))
                    exp.Next()
                return out

            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_SOLID
            from OCC.Core.TopoDS import topods
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            out = []
            while exp.More():
                out.append(topods.Solid(exp.Current()))
                exp.Next()
            return out
        except ImportError:
            continue
    return []


def _read_step_shape(step_path: str) -> Any | None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.STEPControl import STEPControl_Reader
                reader = STEPControl_Reader()
                status = int(reader.ReadFile(str(step_path)))
                if status != 1:
                    return None
                reader.TransferRoots()
                return reader.OneShape()

            from OCC.Core.STEPControl import STEPControl_Reader
            reader = STEPControl_Reader()
            status = int(reader.ReadFile(str(step_path)))
            if status != 1:
                return None
            reader.TransferRoots()
            return reader.OneShape()
        except ImportError:
            continue
    return None


def _apply_actor_transform(shape: Any, obj: Any) -> Any:
    actor = getattr(obj, "actor", None)
    if actor is None:
        return shape

    mat = actor.GetMatrix()
    if mat is None:
        return shape

    vals = [float(mat.GetElement(r, c)) for r in range(4) for c in range(4)]

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Trsf
                from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

                tr = gp_Trsf()
                tr.SetValues(
                    vals[0], vals[1], vals[2], vals[3],
                    vals[4], vals[5], vals[6], vals[7],
                    vals[8], vals[9], vals[10], vals[11],
                )
                return BRepBuilderAPI_Transform(shape, tr, True).Shape()

            from OCC.Core.gp import gp_Trsf
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

            tr = gp_Trsf()
            tr.SetValues(
                vals[0], vals[1], vals[2], vals[3],
                vals[4], vals[5], vals[6], vals[7],
                vals[8], vals[9], vals[10], vals[11],
            )
            return BRepBuilderAPI_Transform(shape, tr, True).Shape()
        except ImportError:
            continue
        except Exception:
            return shape

    return shape


def _apply_export_offset(shape: Any, obj: Any) -> Any:
    off = getattr(obj, "step_export_offset", (0.0, 0.0, 0.0))
    if not isinstance(off, (list, tuple)) or len(off) != 3:
        return shape
    try:
        dx, dy, dz = float(off[0]), float(off[1]), float(off[2])
    except Exception:
        return shape
    if abs(dx) < 1e-12 and abs(dy) < 1e-12 and abs(dz) < 1e-12:
        return shape

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Trsf, gp_Vec
                from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

                tr = gp_Trsf()
                tr.SetTranslation(gp_Vec(dx, dy, dz))
                return BRepBuilderAPI_Transform(shape, tr, True).Shape()

            from OCC.Core.gp import gp_Trsf, gp_Vec
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

            tr = gp_Trsf()
            tr.SetTranslation(gp_Vec(dx, dy, dz))
            return BRepBuilderAPI_Transform(shape, tr, True).Shape()
        except ImportError:
            continue
        except Exception:
            return shape
    return shape


def _shape_bounds_center(shape: Any) -> tuple[float, float, float] | None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.Bnd import Bnd_Box
                from OCP.BRepBndLib import BRepBndLib

                box = Bnd_Box()
                BRepBndLib.Add_s(shape, box)
                xmin, ymin, zmin, xmax, ymax, zmax = _bounds_box_limits(box)
                return (
                    0.5 * (float(xmin) + float(xmax)),
                    0.5 * (float(ymin) + float(ymax)),
                    0.5 * (float(zmin) + float(zmax)),
                )

            from OCC.Core.Bnd import Bnd_Box
            from OCC.Core.BRepBndLib import brepbndlib

            box = Bnd_Box()
            brepbndlib.Add(shape, box)
            xmin, ymin, zmin, xmax, ymax, zmax = _bounds_box_limits(box)
            return (
                0.5 * (float(xmin) + float(xmax)),
                0.5 * (float(ymin) + float(ymax)),
                0.5 * (float(zmin) + float(zmax)),
            )
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _shape_bounds(shape: Any) -> tuple[float, float, float, float, float, float] | None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.Bnd import Bnd_Box
                from OCP.BRepBndLib import BRepBndLib
                box = Bnd_Box()
                BRepBndLib.Add_s(shape, box)
            else:
                from OCC.Core.Bnd import Bnd_Box
                from OCC.Core.BRepBndLib import brepbndlib
                box = Bnd_Box()
                brepbndlib.Add(shape, box)
            xmin, ymin, zmin, xmax, ymax, zmax = _bounds_box_limits(box)
            return (xmin, xmax, ymin, ymax, zmin, zmax)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _world_poly_bounds_from_actor(obj: Any) -> tuple[float, float, float, float, float, float] | None:
    actor = getattr(obj, "actor", None)
    if actor is None or actor.GetMapper() is None or actor.GetMapper().GetInput() is None:
        return None
    source = vtk.vtkPolyData()
    source.DeepCopy(actor.GetMapper().GetInput())
    transform = vtk.vtkTransform()
    transform.SetMatrix(actor.GetMatrix())
    filter_ = vtk.vtkTransformPolyDataFilter()
    filter_.SetTransform(transform)
    filter_.SetInputData(source)
    filter_.Update()
    bounds = filter_.GetOutput().GetBounds()
    return tuple(float(value) for value in bounds) if bounds is not None else None


def _align_occ_shape_to_actor_bounds(shape: Any, obj: Any) -> Any:
    """Apply per-axis scale/translation from viewer bounds to the OCC shape."""
    source_bounds = _shape_bounds(shape)
    target_bounds = _world_poly_bounds_from_actor(obj)
    if source_bounds is None or target_bounds is None:
        return shape
    from .step_importer import STEP_MESH_LINEAR_DEFLECTION

    actor = getattr(obj, "actor", None)
    matrix = actor.GetMatrix() if actor is not None else None
    mesh_scale = 1.0
    if matrix is not None:
        mesh_scale = max(
            math.sqrt(sum(float(matrix.GetElement(row, column)) ** 2 for row in range(3)))
            for column in range(3)
        )
    mesh_tolerance = STEP_MESH_LINEAR_DEFLECTION * mesh_scale
    if max(
        abs(source_bounds[index] - target_bounds[index]) for index in range(6)
    ) <= mesh_tolerance:
        return shape
    source_spans = [
        source_bounds[axis * 2 + 1] - source_bounds[axis * 2]
        for axis in range(3)
    ]
    target_spans = [
        target_bounds[axis * 2 + 1] - target_bounds[axis * 2]
        for axis in range(3)
    ]
    source_center = [
        (source_bounds[axis * 2] + source_bounds[axis * 2 + 1]) * 0.5
        for axis in range(3)
    ]
    target_center = [
        (target_bounds[axis * 2] + target_bounds[axis * 2 + 1]) * 0.5
        for axis in range(3)
    ]
    scales = [
        target_spans[axis] / source_spans[axis]
        if abs(source_spans[axis]) >= 1e-12
        else 1.0
        for axis in range(3)
    ]
    translation = [
        target_center[axis] - scales[axis] * source_center[axis]
        for axis in range(3)
    ]
    if (
        max(abs(scale - 1.0) for scale in scales) < 1e-9
        and max(abs(value) for value in translation) < 1e-9
    ):
        return shape

    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_GTrsf
                from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
            else:
                from OCC.Core.gp import gp_GTrsf
                from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_GTransform
            transform = gp_GTrsf()
            for axis in range(3):
                transform.SetValue(axis + 1, axis + 1, scales[axis])
                transform.SetValue(axis + 1, 4, translation[axis])
            return BRepBuilderAPI_GTransform(shape, transform, True).Shape()
        except ImportError:
            continue
        except Exception:
            return shape
    return shape


def _world_poly_center_from_actor(obj: Any) -> tuple[float, float, float] | None:
    actor = getattr(obj, "actor", None)
    if actor is None:
        return None
    mapper = actor.GetMapper() if hasattr(actor, "GetMapper") else None
    if mapper is None or mapper.GetInput() is None:
        return None

    src = vtk.vtkPolyData()
    src.DeepCopy(mapper.GetInput())

    tfm = vtk.vtkTransform()
    tfm.SetMatrix(actor.GetMatrix())

    tf = vtk.vtkTransformPolyDataFilter()
    tf.SetTransform(tfm)
    tf.SetInputData(src)
    tf.Update()

    b = tf.GetOutput().GetBounds()
    if b is None:
        return None
    return (
        0.5 * (float(b[0]) + float(b[1])),
        0.5 * (float(b[2]) + float(b[3])),
        0.5 * (float(b[4]) + float(b[5])),
    )


def _polydata_from_json(data: Any) -> vtk.vtkPolyData:
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


def _restore_actor_transform_from_snapshot(obj: Any, item: Dict[str, Any]) -> Any:
    transform = item.get("actor_transform", {})
    actor = getattr(obj, "actor", None)
    if not isinstance(transform, dict) or actor is None:
        return obj

    setters = (
        ("origin", actor.SetOrigin),
        ("position", actor.SetPosition),
        ("orientation", actor.SetOrientation),
        ("scale", actor.SetScale),
    )
    for key, setter in setters:
        values = transform.get(key)
        if isinstance(values, (list, tuple)) and len(values) == 3:
            setter(*[float(value) for value in values])
    return obj


def _rebuild_object_from_snapshot(item: Any) -> Any | None:
    if not isinstance(item, dict):
        return None

    from ..scene import em_objects as emo

    obj_type = str(item.get("type", "") or "").strip()
    params = item.get("params", {})
    if not isinstance(params, dict):
        params = {}

    name = str(item.get("name", "") or "")

    try:
        if obj_type == "MeshObject":
            mesh_poly = _polydata_from_json(item.get("mesh"))
            obj = emo.MeshObject(
                name,
                mesh_poly,
                params.get("Material", "PEC"),
                step_source_path=params.get("StepSourcePath"),
                step_solid_name=params.get("StepSolidName"),
                plate_role=bool(params.get("PlateRole", False)),
                boolean_op=params.get("BooleanOperation"),
                boolean_source_names=params.get("BooleanSourceNames"),
                boolean_sources_data=params.get("BooleanSourcesData"),
            )
            obj.set_parameters(params)
            if (
                "StepGeometryModified" not in params
                and item.get("mesh") is not None
                and str(params.get("StepSourcePath", "") or "").strip()
            ):
                obj.step_geometry_modified = True
            return _restore_actor_transform_from_snapshot(obj, item)

        if obj_type == "BoxObject":
            obj = emo.BoxObject(
                name,
                params["X1"], params["Y1"], params["Z1"],
                params["X2"], params["Y2"], params["Z2"],
                params.get("Material", "PEC"),
            )
            obj.set_parameters(params)
            return _restore_actor_transform_from_snapshot(obj, item)
    except Exception:
        return None

    return None


def _recover_legacy_export_offset(shape: Any, obj: Any) -> tuple[Any, tuple[float, float, float] | None]:
    off = getattr(obj, "step_export_offset", (0.0, 0.0, 0.0))
    try:
        ox, oy, oz = float(off[0]), float(off[1]), float(off[2])
    except Exception:
        ox, oy, oz = 0.0, 0.0, 0.0

    # Only migrate legacy objects that were edited but have no persisted offset yet.
    if not bool(getattr(obj, "step_geometry_modified", False)):
        return shape, None
    if abs(ox) > 1e-12 or abs(oy) > 1e-12 or abs(oz) > 1e-12:
        return shape, None

    shape_center = _shape_bounds_center(shape)
    poly_center = _world_poly_center_from_actor(obj)
    if shape_center is None or poly_center is None:
        return shape, None

    dx = poly_center[0] - shape_center[0]
    dy = poly_center[1] - shape_center[1]
    dz = poly_center[2] - shape_center[2]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(dz) < 1e-9:
        return shape, None

    obj.step_export_offset = (dx, dy, dz)
    return shape, (dx, dy, dz)


def _select_shape_for_mesh_object(obj: Any, root_shape: Any) -> Any:
    solids = _iter_solids(root_shape)
    if not solids:
        return root_shape

    solid_name = str(getattr(obj, "step_solid_name", "") or "")
    match = _STEP_INDEX_RE.search(solid_name)
    if match:
        idx = int(match.group(1))
        if 1 <= idx <= len(solids):
            return solids[idx - 1]

    return solids[0]


def _boolean_from_sources(obj: Any, cache: Dict[str, Any], depth: int) -> Any | None:
    op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
    sources = list(getattr(obj, "source_objects", []) or [])
    if op not in {"fuse", "cut", "common"} or len(sources) < 2:
        return None

    shapes: List[Any] = []
    for src in sources:
        shp = build_occ_shape_for_export(src, cache=cache, depth=depth + 1)
        if shp is None:
            return None
        shapes.append(shp)

    result = _combine_boolean_shapes(shapes, op)
    if result is None:
        return None
    return _apply_actor_transform(result, obj)


def _boolean_from_source_data(obj: Any, cache: Dict[str, Any], depth: int) -> Any | None:
    op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
    source_data = list(getattr(obj, "boolean_sources_data", []) or [])
    if op not in {"fuse", "cut", "common"} or len(source_data) < 2:
        return None

    sources = [_rebuild_object_from_snapshot(item) for item in source_data]
    if any(src is None for src in sources):
        return None

    source_cache = {
        key: value
        for key, value in cache.items()
        if isinstance(key, str) and key.startswith("root::")
    }
    shapes: List[Any] = []
    for src in sources:
        shp = build_occ_shape_for_export(src, cache=source_cache, depth=depth + 1)
        if shp is None:
            return None
        shapes.append(shp)

    cache.update(
        (key, value)
        for key, value in source_cache.items()
        if isinstance(key, str) and key.startswith("root::")
    )

    result = _combine_boolean_shapes(shapes, op)
    if result is None:
        return None
    return _apply_actor_transform(result, obj)


def _combine_boolean_shapes(shapes: List[Any], op: str) -> Any | None:
    if not shapes:
        return None
    if op in {"fuse", "cut"} and len(shapes) > 2:
        for package in ("OCP", "OCC.Core"):
            try:
                if package == "OCP":
                    from OCP.BOPAlgo import BOPAlgo_BOP, BOPAlgo_CUT, BOPAlgo_FUSE
                    from OCP.TopTools import TopTools_ListOfShape
                else:
                    from OCC.Core.BOPAlgo import BOPAlgo_BOP, BOPAlgo_CUT, BOPAlgo_FUSE
                    from OCC.Core.TopTools import TopTools_ListOfShape

                arguments = TopTools_ListOfShape()
                arguments.Append(shapes[0])
                tools = TopTools_ListOfShape()
                for shape in shapes[1:]:
                    tools.Append(shape)
                operation = BOPAlgo_BOP()
                operation.SetArguments(arguments)
                operation.SetTools(tools)
                operation.SetOperation(BOPAlgo_FUSE if op == "fuse" else BOPAlgo_CUT)
                try:
                    operation.SetRunParallel(True)
                except AttributeError:
                    pass
                operation.Perform()
                if operation.HasErrors():
                    break
                result = operation.Shape()
                if result.IsNull():
                    break
                return result
            except ImportError:
                continue
            except Exception:
                break

    result = shapes[0]
    for tool in shapes[1:]:
        result = _apply_boolean(result, tool, op)
        if result is None:
            return None
    return result


def _apply_boolean(a: Any, b: Any, op: str) -> Any | None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
                if op == "fuse":
                    algo = BRepAlgoAPI_Fuse(a, b)
                elif op == "cut":
                    algo = BRepAlgoAPI_Cut(a, b)
                else:
                    algo = BRepAlgoAPI_Common(a, b)
                algo.Build()
                if hasattr(algo, "IsDone") and not algo.IsDone():
                    return None
                return algo.Shape()

            from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common
            if op == "fuse":
                algo = BRepAlgoAPI_Fuse(a, b)
            elif op == "cut":
                algo = BRepAlgoAPI_Cut(a, b)
            else:
                algo = BRepAlgoAPI_Common(a, b)
            algo.Build()
            if hasattr(algo, "IsDone") and not algo.IsDone():
                return None
            return algo.Shape()
        except ImportError:
            continue
        except Exception:
            return None
    return None


def build_occ_shape_for_export(obj: Any, cache: Dict[str, Any] | None = None, depth: int = 0) -> Any | None:
    if depth > 16:
        return None
    if cache is None:
        cache = {}

    oid = id(obj)
    if oid in cache:
        return cache[oid]

    # Reduced Boolean results must export their stored mesh, not rebuild the
    # exact source BREP and silently discard the requested simplification.
    reduced_boolean_mesh = float(getattr(obj, "boolean_mesh_reduction", 0.0) or 0.0) > 0.0
    if not reduced_boolean_mesh:
        boolean_shape = _boolean_from_sources(obj, cache=cache, depth=depth)
        if boolean_shape is None:
            boolean_shape = _boolean_from_source_data(obj, cache=cache, depth=depth)
        if boolean_shape is not None:
            cache[oid] = boolean_shape
            return boolean_shape

    if getattr(obj, "ipc2581_step_geometry", None) is not None:
        native_shape = _build_occ_ipc2581_shape(obj)
        cache[oid] = native_shape
        return native_shape

    # Support direct OCC export for primitive solids without mesh roundtrip.
    obj_type = type(obj).__name__
    if obj_type == "BoxObject":
        box_shape = _build_occ_box_shape(obj)
        if box_shape is not None:
            cache[oid] = box_shape
            return box_shape
    elif obj_type == "PlateObject":
        plate_shape = _build_occ_plate_shape(obj)
        if plate_shape is not None:
            cache[oid] = plate_shape
            return plate_shape
    elif obj_type == "CylinderObject":
        cyl_shape = _build_occ_cylinder_shape(obj)
        if cyl_shape is not None:
            cache[oid] = cyl_shape
            return cyl_shape
    elif obj_type == "ConeObject":
        cone_shape = _build_occ_cone_shape(obj)
        if cone_shape is not None:
            cache[oid] = cone_shape
            return cone_shape
    elif obj_type == "SphereObject":
        sphere_shape = _build_occ_sphere_shape(obj)
        if sphere_shape is not None:
            cache[oid] = sphere_shape
            return sphere_shape

    if not _is_mesh_object(obj):
        return None

    step_path = str(getattr(obj, "step_source_path", "") or "").strip()
    if not step_path:
        return None

    root_key = f"root::{Path(step_path).resolve()}"
    root_shape = cache.get(root_key)
    if root_shape is None:
        root_shape = _read_step_shape(step_path)
        if root_shape is None:
            return None
        cache[root_key] = root_shape

    selected = _select_shape_for_mesh_object(obj, root_shape)
    transformed = _apply_actor_transform(selected, obj)
    target_bounds = _world_poly_bounds_from_actor(obj)
    if target_bounds is not None:
        if bool(getattr(obj, "step_geometry_modified", False)):
            transformed = _align_occ_shape_to_actor_bounds(transformed, obj)
        # Bounds alignment already includes the current scale and translation;
        # do not apply stale offsets persisted by the legacy exporter.
        recovered = None
    else:
        transformed, recovered = _recover_legacy_export_offset(transformed, obj)
        transformed = _apply_export_offset(transformed, obj)
    if recovered is not None:
        setattr(obj, "_step_export_offset_recovered", recovered)
    cache[oid] = transformed
    return transformed


def intersect_step_mesh_face_line(
    obj: Any,
    occ_face_id: int,
    origin: Iterable[float],
    direction: Iterable[float],
) -> List[tuple[float, tuple[float, float, float]]]:
    """Intersect a world-space line with one exact face of a STEP MeshObject.

    ``occ_face_id`` uses the same 1-based face order as the ``OCCFaceId``
    cell-data array created by the STEP importer. Returned parameters satisfy
    ``world_point = origin + parameter * direction``; therefore they remain
    signed and are scaled appropriately when *direction* is not unit length.
    The STEP shape is built through :func:`build_occ_shape_for_export`, so its
    selected solid and actor/export transforms match the established export
    geometry path.
    """
    if not _is_mesh_object(obj) or not str(
        getattr(obj, "step_source_path", "") or ""
    ).strip():
        raise ValueError("Exact face intersection requires a STEP-backed MeshObject")
    if isinstance(occ_face_id, bool) or not isinstance(occ_face_id, int):
        raise ValueError("OCCFaceId must be a positive 1-based integer")
    if occ_face_id < 1:
        raise ValueError("OCCFaceId must be a positive 1-based integer")

    def finite_vector3(value: Iterable[float], name: str) -> tuple[float, float, float]:
        if isinstance(value, (str, bytes)):
            raise ValueError(f"{name} must be a finite 3-D vector")
        try:
            vector = tuple(float(component) for component in value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite 3-D vector") from exc
        if len(vector) != 3 or not all(math.isfinite(component) for component in vector):
            raise ValueError(f"{name} must be a finite 3-D vector")
        return vector

    line_origin = finite_vector3(origin, "Line origin")
    line_direction = finite_vector3(direction, "Line direction")
    direction_length = math.hypot(*line_direction)
    if not math.isfinite(direction_length) or direction_length <= 0.0:
        raise ValueError("Line direction must be non-zero and finite")

    shape = build_occ_shape_for_export(obj)
    if shape is None:
        raise ValueError("The exact STEP BREP could not be built for this MeshObject")

    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
                from OCP.Precision import Precision
                from OCP.TopAbs import TopAbs_FACE
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS
                from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

                cast_face = _topods_cast(TopoDS, "Face")
                confusion = float(Precision.Confusion_s())
                infinite = float(Precision.Infinite_s())
            else:
                from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
                from OCC.Core.Precision import Precision
                from OCC.Core.TopAbs import TopAbs_FACE
                from OCC.Core.TopExp import TopExp_Explorer
                from OCC.Core.TopoDS import topods
                from OCC.Core.gp import gp_Dir, gp_Lin, gp_Pnt

                cast_face = topods.Face
                confusion = float(Precision.Confusion())
                infinite = float(Precision.Infinite())
        except ImportError:
            continue

        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        selected_face = None
        face_index = 0
        while explorer.More():
            face_index += 1
            if face_index == occ_face_id:
                selected_face = cast_face(explorer.Current())
                break
            explorer.Next()
        if selected_face is None:
            raise ValueError(
                f"OCCFaceId {occ_face_id} is outside the STEP BREP face range "
                f"(found {face_index} faces)"
            )

        unit_direction = tuple(component / direction_length for component in line_direction)
        line = gp_Lin(gp_Pnt(*line_origin), gp_Dir(*unit_direction))
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(selected_face, confusion)
        intersector.Perform(line, -infinite, infinite)
        if hasattr(intersector, "IsDone") and not intersector.IsDone():
            raise RuntimeError("OpenCascade failed to intersect the selected STEP face")

        hits: List[tuple[float, tuple[float, float, float]]] = []
        for hit_index in range(1, int(intersector.NbPnt()) + 1):
            parameter = float(intersector.WParameter(hit_index)) / direction_length
            point = intersector.Pnt(hit_index)
            world_point = (float(point.X()), float(point.Y()), float(point.Z()))
            if math.isfinite(parameter) and all(
                math.isfinite(component) for component in world_point
            ):
                hits.append((parameter, world_point))
        hits.sort(key=lambda hit: hit[0])
        return hits

    raise ImportError("Exact STEP face intersection requires OCP or pythonOCC")


def resolve_exact_step_circle_profile(
    obj: Any, profile_points: Iterable[Iterable[float]]
) -> Dict[str, Any]:
    """Resolve a picked mesh contour to one unambiguous full STEP circle."""
    step_path = str(getattr(obj, "step_source_path", "") or "").strip()
    if not step_path:
        raise ValueError("The selected object has no STEP source geometry")
    source_shape = build_occ_shape_for_export(obj)
    if source_shape is None:
        raise ValueError("The exact STEP source geometry could not be loaded")

    points = [tuple(float(value) for value in point) for point in profile_points]
    if len(points) < 6 or any(
        len(point) != 3 or not all(math.isfinite(value) for value in point)
        for point in points
    ):
        raise ValueError("The selected mesh contour is not a valid circular chain")

    actor = getattr(obj, "actor", None)
    matrix = actor.GetMatrix() if actor is not None else None
    mesh_scale = 1.0
    if matrix is not None:
        mesh_scale = max(
            math.sqrt(sum(float(matrix.GetElement(row, column)) ** 2 for row in range(3)))
            for column in range(3)
        )
    if not math.isfinite(mesh_scale) or mesh_scale <= 0.0:
        raise ValueError("The selected STEP object has an invalid actor scale")

    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.BRepAdaptor import BRepAdaptor_Curve
                from OCP.Precision import Precision
                from OCP.TopAbs import TopAbs_EDGE
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS
                from OCP.GeomAbs import GeomAbs_Circle

                cast_edge = _topods_cast(TopoDS, "Edge")
                confusion = float(Precision.Confusion_s())
            else:
                from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
                from OCC.Core.Precision import Precision
                from OCC.Core.TopAbs import TopAbs_EDGE
                from OCC.Core.TopExp import TopExp_Explorer
                from OCC.Core.TopoDS import topods
                from OCC.Core.GeomAbs import GeomAbs_Circle

                cast_edge = topods.Edge
                confusion = float(Precision.Confusion())
        except ImportError:
            continue

        coordinate_scale = max(
            (abs(value) for point in points for value in point),
            default=1.0,
        )
        # Tessellation deflection affects surface bounds, not the exact edge nodes.
        match_tolerance = max(
            confusion * 10.0 * mesh_scale,
            coordinate_scale * 1e-7,
            1e-7,
        )
        circle_groups: list[dict[str, Any]] = []
        explorer = TopExp_Explorer(source_shape, TopAbs_EDGE)
        while explorer.More():
            edge = cast_edge(explorer.Current())
            explorer.Next()
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() != GeomAbs_Circle:
                continue
            first_parameter = float(curve.FirstParameter())
            last_parameter = float(curve.LastParameter())
            circle = curve.Circle()
            location = circle.Location()
            axis = circle.Axis().Direction()
            x_direction = circle.Position().XDirection()
            center = (float(location.X()), float(location.Y()), float(location.Z()))
            normal = (float(axis.X()), float(axis.Y()), float(axis.Z()))
            x_axis = (
                float(x_direction.X()),
                float(x_direction.Y()),
                float(x_direction.Z()),
            )
            radius = float(circle.Radius())
            if (
                not math.isfinite(radius)
                or radius <= 0.0
                or not all(math.isfinite(value) for value in (*center, *normal, *x_axis))
            ):
                continue

            group = next(
                (
                    item
                    for item in circle_groups
                    if math.dist(center, item["center"]) <= confusion * 10.0
                    and abs(radius - item["radius"]) <= confusion * 10.0
                    and abs(sum(
                        normal[index] * item["normal"][index]
                        for index in range(3)
                    )) >= 1.0 - 1e-8
                ),
                None,
            )
            if group is None:
                group = {
                    "center": center,
                    "radius": radius,
                    "normal": normal,
                    "x_axis": x_axis,
                    "arcs": [],
                }
                circle_groups.append(group)

            y_axis = (
                group["normal"][1] * group["x_axis"][2]
                - group["normal"][2] * group["x_axis"][1],
                group["normal"][2] * group["x_axis"][0]
                - group["normal"][0] * group["x_axis"][2],
                group["normal"][0] * group["x_axis"][1]
                - group["normal"][1] * group["x_axis"][0],
            )
            start_point = curve.Value(first_parameter)
            end_point = curve.Value(last_parameter)

            def circle_angle(point) -> float:
                relative = tuple(
                    float(point.Coord(axis + 1)) - group["center"][axis]
                    for axis in range(3)
                )
                return math.atan2(
                    sum(relative[axis] * y_axis[axis] for axis in range(3)),
                    sum(relative[axis] * group["x_axis"][axis] for axis in range(3)),
                ) % (2.0 * math.pi)

            group["arcs"].append((
                circle_angle(start_point),
                circle_angle(end_point),
                abs(last_parameter - first_parameter),
            ))

        candidates: list[dict[str, Any]] = []
        for group in circle_groups:
            angle_tolerance = max(1e-7, confusion * 10.0 / group["radius"])
            if not _circle_arcs_cover_full_turn(group["arcs"], angle_tolerance):
                continue
            center = group["center"]
            normal = group["normal"]
            x_axis = group["x_axis"]
            radius = group["radius"]
            max_deviation = 0.0
            for point in points:
                relative = tuple(point[index] - center[index] for index in range(3))
                plane_distance = sum(
                    relative[index] * normal[index] for index in range(3)
                )
                projected = tuple(
                    relative[index] - plane_distance * normal[index]
                    for index in range(3)
                )
                radial_distance = math.hypot(*projected)
                deviation = math.hypot(
                    plane_distance, radial_distance - radius
                )
                max_deviation = max(max_deviation, deviation)
            if max_deviation <= match_tolerance:
                candidate = {
                    "kind": "circle",
                    "center": list(group["center"]),
                    "radius": radius,
                    "normal": list(group["normal"]),
                    "x_axis": list(group["x_axis"]),
                    "max_mesh_deviation": max_deviation,
                }
                equivalent = any(
                    math.dist(candidate["center"], item["center"]) <= confusion * 10.0
                    and abs(candidate["radius"] - item["radius"]) <= confusion * 10.0
                    and abs(sum(
                        candidate["normal"][index] * item["normal"][index]
                        for index in range(3)
                    )) >= 1.0 - 1e-8
                    for item in candidates
                )
                if not equivalent:
                    candidates.append(candidate)

        if not candidates:
            raise ValueError(
                "The picked mesh contour does not match a complete circular "
                "STEP edge or co-circular edge chain "
                f"in the STEP BREP within {match_tolerance:.6g} model units"
            )
        if len(candidates) != 1:
            raise ValueError(
                "The picked mesh contour matches multiple distinct STEP circles; "
                "select an unambiguous edge"
            )
        candidates[0].pop("max_mesh_deviation")
        return candidates[0]
    raise RuntimeError("Exact STEP edge resolution requires OCP or pythonOCC-core")


def build_occ_swept_surface_shape(
    obj: Any,
    source_shapes: Iterable[tuple[str, Any]],
    log_callback: Callable[[str, str], None] | None = None,
    mm_per_unit: float = 1.0,
    connection_metadata: dict[str, Any] | None = None,
) -> Any:
    """Build an uncapped BREP sheet from the swept profile and vector."""
    closed = bool(getattr(obj, "_closed", False))
    profile = getattr(obj, "_profile_pts", None)
    if not isinstance(profile, (list, tuple)) or len(profile) < (3 if closed else 2):
        raise ValueError("Swept surface profile is invalid")
    local_points = [tuple(float(value) for value in point) for point in profile]
    if any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in local_points):
        raise ValueError("Swept surface profile must contain finite 3-D points")
    if not math.isfinite(mm_per_unit) or mm_per_unit <= 0.0:
        raise ValueError("STEP export scale factor must be finite and positive")

    actor = getattr(obj, "actor", None)
    matrix = actor.GetMatrix() if actor is not None else None
    if matrix is None:
        transform = tuple(
            tuple(1.0 if row == column else 0.0 for column in range(4))
            for row in range(4)
        )
    else:
        transform = tuple(
            tuple(float(matrix.GetElement(row, column)) for column in range(4))
            for row in range(4)
        )
        if any(not math.isfinite(value) for row in transform for value in row):
            raise ValueError("Swept surface actor transform must be finite")

    def transform_point(point: tuple[float, ...]) -> tuple[float, float, float]:
        return tuple(
            sum(transform[row][column] * point[column] for column in range(3))
            + transform[row][3]
            for row in range(3)
        )

    def transform_vector(vector: tuple[float, ...]) -> tuple[float, float, float]:
        return tuple(
            sum(transform[row][column] * vector[column] for column in range(3))
            for row in range(3)
        )

    points = [transform_point(point) for point in local_points]
    if any(not all(math.isfinite(value) for value in point) for point in points):
        raise ValueError("Transformed swept surface profile must be finite")
    try:
        local_vector = tuple(
            float(obj.direction[axis]) * float(obj.extrusion_length)
            for axis in range(3)
        )
    except (AttributeError, TypeError, ValueError, IndexError) as exc:
        raise ValueError("Swept surface has an invalid extrusion vector") from exc
    displacement = transform_vector(local_vector)
    length = math.hypot(*displacement)
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("Swept surface extrusion vector must be finite and non-zero")

    edge_count = len(points) if closed else len(points) - 1
    perimeter = sum(
        math.dist(points[index], points[(index + 1) % len(points)])
        for index in range(edge_count)
    )
    profile_scale = max(
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    )
    direction = tuple(value / length for value in displacement)
    overlap = 0.0
    profile_curve = getattr(obj, "_profile_curve", None)

    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.Bnd import Bnd_Box
                from OCP.BRepBndLib import BRepBndLib
                from OCP.BRepBuilderAPI import (
                    BRepBuilderAPI_MakeEdge,
                    BRepBuilderAPI_MakePolygon,
                    BRepBuilderAPI_MakeWire,
                )
                from OCP.BRepCheck import BRepCheck_Analyzer
                from OCP.BRepExtrema import BRepExtrema_DistShapeShape
                from OCP.BRepGProp import BRepGProp
                from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
                from OCP.GProp import GProp_GProps
                from OCP.Precision import Precision
                from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_WIRE
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS
                from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec

                add_bounds = BRepBndLib.Add_s
                linear_properties = BRepGProp.LinearProperties_s
                cast_wire = _topods_cast(TopoDS, "Wire")
                confusion = float(Precision.Confusion_s())
            else:
                from OCC.Core.Bnd import Bnd_Box
                from OCC.Core.BRepBndLib import brepbndlib
                from OCC.Core.BRepBuilderAPI import (
                    BRepBuilderAPI_MakeEdge,
                    BRepBuilderAPI_MakePolygon,
                    BRepBuilderAPI_MakeWire,
                )
                from OCC.Core.BRepCheck import BRepCheck_Analyzer
                from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
                from OCC.Core.BRepGProp import brepgprop
                from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
                from OCC.Core.GProp import GProp_GProps
                from OCC.Core.Precision import Precision
                from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_WIRE
                from OCC.Core.TopExp import TopExp_Explorer
                from OCC.Core.TopoDS import topods
                from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec

                add_bounds = brepbndlib.Add
                linear_properties = brepgprop.LinearProperties
                cast_wire = topods.Wire
                confusion = float(Precision.Confusion())
        except ImportError:
            continue

        if profile_curve is not None:
            if not closed:
                raise ValueError("An exact circular swept profile must be closed")
            center = transform_point(tuple(profile_curve["center"]))
            normal = tuple(float(value) for value in profile_curve["normal"])
            x_axis = tuple(float(value) for value in profile_curve["x_axis"])
            y_axis = (
                normal[1] * x_axis[2] - normal[2] * x_axis[1],
                normal[2] * x_axis[0] - normal[0] * x_axis[2],
                normal[0] * x_axis[1] - normal[1] * x_axis[0],
            )
            transformed_x = transform_vector(x_axis)
            transformed_y = transform_vector(y_axis)
            x_scale = math.hypot(*transformed_x)
            y_scale = math.hypot(*transformed_y)
            scale_tolerance = max(max(x_scale, y_scale) * 1e-8, 1e-12)
            if (
                x_scale <= 1e-12
                or y_scale <= 1e-12
                or abs(x_scale - y_scale) > scale_tolerance
                or abs(sum(
                    transformed_x[axis] * transformed_y[axis]
                    for axis in range(3)
                )) > x_scale * y_scale * 1e-8
            ):
                raise ValueError(
                    "The exact circular profile cannot be exported with a "
                    "non-uniform or sheared actor transform"
                )
            transformed_normal = (
                transformed_x[1] * transformed_y[2]
                - transformed_x[2] * transformed_y[1],
                transformed_x[2] * transformed_y[0]
                - transformed_x[0] * transformed_y[2],
                transformed_x[0] * transformed_y[1]
                - transformed_x[1] * transformed_y[0],
            )
            normal_length = math.hypot(*transformed_normal)
            transformed_normal = tuple(
                value / normal_length for value in transformed_normal
            )
            transformed_x = tuple(value / x_scale for value in transformed_x)
            radius = float(profile_curve["radius"]) * x_scale
            circle = gp_Circ(
                gp_Ax2(
                    gp_Pnt(*center),
                    gp_Dir(*transformed_normal),
                    gp_Dir(*transformed_x),
                ),
                radius,
            )
            edge_builder = BRepBuilderAPI_MakeEdge(circle)
            if not edge_builder.IsDone():
                raise ValueError("OpenCascade could not construct the exact circle edge")
            wire_builder = BRepBuilderAPI_MakeWire(edge_builder.Edge())
            if not wire_builder.IsDone():
                raise ValueError("OpenCascade could not construct the exact circle wire")
            wire = wire_builder.Wire()
            perimeter = 2.0 * math.pi * radius
        else:
            polygon = BRepBuilderAPI_MakePolygon()
            for point in points:
                polygon.Add(gp_Pnt(*point))
            if closed:
                polygon.Close()
            if not polygon.IsDone():
                raise ValueError("OpenCascade could not construct the swept profile wire")
            wire = polygon.Wire()

        if closed and source_shapes and profile_curve is None:
            target_properties = GProp_GProps()
            linear_properties(wire, target_properties)
            target_perimeter = float(target_properties.Mass())
            target_box = Bnd_Box()
            add_bounds(wire, target_box)
            target_bounds = _bounds_box_limits(target_box)
            exact_hosts = []
            exact_tolerance = max(confusion * 10.0, 1e-7)
            for _source_name, source_shape in source_shapes:
                solids = TopExp_Explorer(source_shape, TopAbs_SOLID)
                if not solids.More():
                    continue
                wires = TopExp_Explorer(source_shape, TopAbs_WIRE)
                while wires.More():
                    candidate_wire = cast_wire(wires.Current())
                    wires.Next()
                    candidate_properties = GProp_GProps()
                    linear_properties(candidate_wire, candidate_properties)
                    if abs(float(candidate_properties.Mass()) - target_perimeter) > exact_tolerance:
                        continue
                    candidate_box = Bnd_Box()
                    add_bounds(candidate_wire, candidate_box)
                    candidate_bounds = _bounds_box_limits(candidate_box)
                    if max(
                        abs(target_bounds[index] - candidate_bounds[index])
                        for index in range(6)
                    ) > exact_tolerance:
                        continue
                    distance = BRepExtrema_DistShapeShape(wire, candidate_wire)
                    distance.Perform()
                    if distance.IsDone() and float(distance.Value()) <= exact_tolerance:
                        if not any(source_shape is host for host in exact_hosts):
                            exact_hosts.append(source_shape)
                        break
            if len(exact_hosts) == 1:
                host_solids = TopExp_Explorer(exact_hosts[0], TopAbs_SOLID)
                if host_solids.More():
                    overlap = max(profile_scale * 0.005, 1e-6)

        extended_displacement = tuple(
            displacement[axis] + direction[axis] * overlap
            for axis in range(3)
        )
        if profile_curve is None:
            shifted_points = [
                tuple(point[axis] - direction[axis] * overlap for axis in range(3))
                for point in points
            ]
            shifted_polygon = BRepBuilderAPI_MakePolygon()
            for point in shifted_points:
                shifted_polygon.Add(gp_Pnt(*point))
            if closed:
                shifted_polygon.Close()
            if not shifted_polygon.IsDone():
                raise ValueError("OpenCascade could not construct the swept profile wire")
            wire = shifted_polygon.Wire()
        prism = BRepPrimAPI_MakePrism(wire, gp_Vec(*extended_displacement), True, True)
        if not prism.IsDone():
            raise RuntimeError("OpenCascade could not construct the swept surface BREP")
        shape = prism.Shape()
        if not BRepCheck_Analyzer(shape).IsValid():
            raise RuntimeError("OpenCascade produced an invalid swept surface BREP")
        solids = TopExp_Explorer(shape, TopAbs_SOLID)
        if solids.More():
            raise RuntimeError("Swept surface export unexpectedly produced a solid")
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        if not faces.More():
            raise RuntimeError("Swept surface export contains no lateral faces")

        if connection_metadata is not None:
            connection_metadata.update({
                "profile_perimeter_mm": perimeter * mm_per_unit,
                "extrusion_length_mm": (length + overlap) * mm_per_unit,
                "direction": direction,
                "repair_method": (
                    "exact_step_circle"
                    if profile_curve is not None
                    else "direct_open_profile" if not closed else "direct_closed_profile"
                ),
            })
        if log_callback is not None:
            log_callback(
                "INFO",
                f"swept_brep_direct name={getattr(obj, 'name', 'SweptSurface')} "
                f"perimeter={perimeter * mm_per_unit:.6g} "
                f"length={(length + overlap) * mm_per_unit:.6g}",
            )
        return shape
    raise RuntimeError("Swept-surface BREP export requires OCP or pythonOCC-core")
