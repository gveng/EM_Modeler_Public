from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List
import re
import vtk


_STEP_INDEX_RE = re.compile(r"(\d+)(?!.*\d)")


def _is_mesh_object(obj: Any) -> bool:
    return type(obj).__name__ == "MeshObject"


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
                return BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)).Shape()

            from OCC.Core.gp import gp_Pnt
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
            return BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)).Shape()
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _iter_solids(shape: Any) -> List[Any]:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopAbs import TopAbs_SOLID
                from OCP.TopoDS import TopoDS
                exp = TopExp_Explorer(shape, TopAbs_SOLID)
                out: List[Any] = []
                while exp.More():
                    out.append(TopoDS.Solid_s(exp.Current()))
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
                BRepBndLib.Add(shape, box)
                xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
                return (
                    0.5 * (float(xmin) + float(xmax)),
                    0.5 * (float(ymin) + float(ymax)),
                    0.5 * (float(zmin) + float(zmax)),
                )

            from OCC.Core.Bnd import Bnd_Box
            from OCC.Core.BRepBndLib import brepbndlib

            box = Bnd_Box()
            brepbndlib.Add(shape, box)
            xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
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
            return obj

        if obj_type == "BoxObject":
            obj = emo.BoxObject(
                name,
                params["X1"], params["Y1"], params["Z1"],
                params["X2"], params["Y2"], params["Z2"],
                params.get("Material", "PEC"),
            )
            obj.set_parameters(params)
            return obj
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

    result = shapes[0]
    for tool in shapes[1:]:
        result = _apply_boolean(result, tool, op)
        if result is None:
            return None
    return _apply_actor_transform(result, obj)


def _boolean_from_source_data(obj: Any, cache: Dict[str, Any], depth: int) -> Any | None:
    op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
    source_data = list(getattr(obj, "boolean_sources_data", []) or [])
    if op not in {"fuse", "cut", "common"} or len(source_data) < 2:
        return None

    shapes: List[Any] = []
    for item in source_data:
        src = _rebuild_object_from_snapshot(item)
        if src is None:
            return None
        shp = build_occ_shape_for_export(src, cache=cache, depth=depth + 1)
        if shp is None:
            return None
        shapes.append(shp)

    result = shapes[0]
    for tool in shapes[1:]:
        result = _apply_boolean(result, tool, op)
        if result is None:
            return None
    return _apply_actor_transform(result, obj)


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

    # Prefer reconstructing boolean outputs from their source objects.
    boolean_shape = _boolean_from_sources(obj, cache=cache, depth=depth)
    if boolean_shape is None:
        boolean_shape = _boolean_from_source_data(obj, cache=cache, depth=depth)
    if boolean_shape is not None:
        cache[oid] = boolean_shape
        return boolean_shape

    # Support direct OCC export for primitive boxes (e.g. AirBox) without mesh roundtrip.
    if type(obj).__name__ == "BoxObject":
        box_shape = _build_occ_box_shape(obj)
        if box_shape is not None:
            transformed_box = _apply_actor_transform(box_shape, obj)
            cache[oid] = transformed_box
            return transformed_box

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
    transformed, recovered = _recover_legacy_export_offset(transformed, obj)
    transformed = _apply_export_offset(transformed, obj)
    if recovered is not None:
        setattr(obj, "_step_export_offset_recovered", recovered)
    cache[oid] = transformed
    return transformed
