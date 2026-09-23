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

"""Export scene objects to per-object STEP files for EMERGE simulation bundles."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple
import re

import vtk

from .occ_export_builder import build_occ_shape_for_export, _rebuild_object_from_snapshot


_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_name(name: str) -> str:
    out = _NAME_SANITIZE_RE.sub("_", str(name).strip())
    out = out.strip("_")
    return out or "Object"


def _step_write_succeeded(step_path: Path, status: int) -> bool:
    if status == 1:
        return True
    try:
        return step_path.exists() and step_path.stat().st_size > 0
    except OSError:
        return False


def _transfer_step_shape(writer: Any, shape: Any, modes: list[Any]) -> bool:
    for mode in modes:
        try:
            writer.Transfer(shape, mode)
            return True
        except Exception:
            continue
    return False


def _count_solids_in_shape(shape: Any) -> int:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopAbs import TopAbs_SOLID

                exp = TopExp_Explorer(shape, TopAbs_SOLID)
                count = 0
                while exp.More():
                    count += 1
                    exp.Next()
                return count

            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_SOLID

            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            count = 0
            while exp.More():
                count += 1
                exp.Next()
            return count
        except ImportError:
            continue
        except Exception:
            return -1
    return -1


def _translate_shape(shape: Any, dx: float, dy: float, dz: float) -> Any:
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


def _count_open_or_nonmanifold_edges(poly: vtk.vtkPolyData) -> int:
    if poly is None or poly.GetNumberOfPoints() <= 0:
        return 0
    fe = vtk.vtkFeatureEdges()
    fe.SetInputData(poly)
    fe.BoundaryEdgesOn()
    fe.NonManifoldEdgesOn()
    fe.FeatureEdgesOff()
    fe.ManifoldEdgesOff()
    fe.Update()
    return int(fe.GetOutput().GetNumberOfCells())


def _repair_polydata_for_step(poly: vtk.vtkPolyData) -> Tuple[vtk.vtkPolyData, Dict[str, int]]:
    in_poly = vtk.vtkPolyData()
    in_poly.DeepCopy(poly)
    in_edges = _count_open_or_nonmanifold_edges(in_poly)

    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(in_poly)
    clean.Update()

    tri = vtk.vtkTriangleFilter()
    tri.SetInputData(clean.GetOutput())
    tri.Update()

    fill = vtk.vtkFillHolesFilter()
    fill.SetInputData(tri.GetOutput())
    fill.SetHoleSize(1e12)
    fill.Update()

    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(fill.GetOutput())
    normals.AutoOrientNormalsOn()
    normals.ConsistencyOn()
    normals.SplittingOff()
    normals.Update()

    out_poly = vtk.vtkPolyData()
    out_poly.DeepCopy(normals.GetOutput())
    out_edges = _count_open_or_nonmanifold_edges(out_poly)

    stats = {
        "in_points": int(in_poly.GetNumberOfPoints()),
        "in_polys": int(in_poly.GetNumberOfPolys()),
        "in_open_edges": int(in_edges),
        "out_points": int(out_poly.GetNumberOfPoints()),
        "out_polys": int(out_poly.GetNumberOfPolys()),
        "out_open_edges": int(out_edges),
    }
    return out_poly, stats


def _build_occ_faceted_shape_from_polydata(poly: vtk.vtkPolyData) -> Any | None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.gp import gp_Pnt
                from OCP.BRep import BRep_Builder
                from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
                from OCP.TopoDS import TopoDS_Compound

                builder = BRep_Builder()
                comp = TopoDS_Compound()
                builder.MakeCompound(comp)

                pts = poly.GetPoints()
                if pts is None:
                    return None

                added = 0
                for i in range(poly.GetNumberOfCells()):
                    cell = poly.GetCell(i)
                    if cell is None or cell.GetNumberOfPoints() != 3:
                        continue
                    ids = cell.GetPointIds()
                    p0 = pts.GetPoint(ids.GetId(0))
                    p1 = pts.GetPoint(ids.GetId(1))
                    p2 = pts.GetPoint(ids.GetId(2))
                    mk_poly = BRepBuilderAPI_MakePolygon()
                    mk_poly.Add(gp_Pnt(*p0))
                    mk_poly.Add(gp_Pnt(*p1))
                    mk_poly.Add(gp_Pnt(*p2))
                    mk_poly.Close()
                    if not mk_poly.IsDone():
                        continue
                    mk_face = BRepBuilderAPI_MakeFace(mk_poly.Wire())
                    if not mk_face.IsDone():
                        continue
                    builder.Add(comp, mk_face.Face())
                    added += 1

                return comp if added > 0 else None

            from OCC.Core.gp import gp_Pnt
            from OCC.Core.BRep import BRep_Builder
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
            from OCC.Core.TopoDS import TopoDS_Compound

            builder = BRep_Builder()
            comp = TopoDS_Compound()
            builder.MakeCompound(comp)

            pts = poly.GetPoints()
            if pts is None:
                return None

            added = 0
            for i in range(poly.GetNumberOfCells()):
                cell = poly.GetCell(i)
                if cell is None or cell.GetNumberOfPoints() != 3:
                    continue
                ids = cell.GetPointIds()
                p0 = pts.GetPoint(ids.GetId(0))
                p1 = pts.GetPoint(ids.GetId(1))
                p2 = pts.GetPoint(ids.GetId(2))
                mk_poly = BRepBuilderAPI_MakePolygon()
                mk_poly.Add(gp_Pnt(*p0))
                mk_poly.Add(gp_Pnt(*p1))
                mk_poly.Add(gp_Pnt(*p2))
                mk_poly.Close()
                if not mk_poly.IsDone():
                    continue
                mk_face = BRepBuilderAPI_MakeFace(mk_poly.Wire())
                if not mk_face.IsDone():
                    continue
                builder.Add(comp, mk_face.Face())
                added += 1

            return comp if added > 0 else None
        except ImportError:
            continue
        except Exception:
            return None
    return None


def _write_step_from_occ_shape_with_modes(shape: Any, step_path: Path) -> None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.STEPControl import (
                    STEPControl_Writer,
                    STEPControl_ManifoldSolidBrep,
                    STEPControl_ShellBasedSurfaceModel,
                    STEPControl_FacetedBrep,
                    STEPControl_AsIs,
                )
                modes = [STEPControl_ShellBasedSurfaceModel, STEPControl_FacetedBrep, STEPControl_AsIs, STEPControl_ManifoldSolidBrep]
                for mode in modes:
                    writer = STEPControl_Writer()
                    if _transfer_step_shape(writer, shape, [mode]):
                        status = int(writer.Write(str(step_path)))
                        if _step_write_succeeded(step_path, status):
                            return
                raise RuntimeError("STEP writer failed for all transfer modes")

            from OCC.Core.STEPControl import (
                STEPControl_Writer,
                STEPControl_ManifoldSolidBrep,
                STEPControl_ShellBasedSurfaceModel,
                STEPControl_FacetedBrep,
                STEPControl_AsIs,
            )
            modes = [STEPControl_ShellBasedSurfaceModel, STEPControl_FacetedBrep, STEPControl_AsIs, STEPControl_ManifoldSolidBrep]
            for mode in modes:
                writer = STEPControl_Writer()
                if _transfer_step_shape(writer, shape, [mode]):
                    status = int(writer.Write(str(step_path)))
                    if _step_write_succeeded(step_path, status):
                        return
            raise RuntimeError("STEP writer failed for all transfer modes")
        except ImportError:
            continue

    raise RuntimeError("STEP export requires OCP or pythonOCC-core")


def _world_polydata_from_actor(actor: vtk.vtkActor) -> vtk.vtkPolyData:
    mapper = actor.GetMapper()
    if mapper is None or mapper.GetInput() is None:
        return vtk.vtkPolyData()

    src = vtk.vtkPolyData()
    src.DeepCopy(mapper.GetInput())

    transform = vtk.vtkTransform()
    transform.SetMatrix(actor.GetMatrix())

    tf = vtk.vtkTransformPolyDataFilter()
    tf.SetTransform(transform)
    tf.SetInputData(src)
    tf.Update()

    tri = vtk.vtkTriangleFilter()
    tri.SetInputData(tf.GetOutput())
    tri.Update()

    out = vtk.vtkPolyData()
    out.DeepCopy(tri.GetOutput())
    return out


def _boolean_source_items(obj: Any) -> List[Any]:
    items = list(getattr(obj, "source_objects", []) or [])
    if items:
        return items
    raw_items = list(getattr(obj, "boolean_sources_data", []) or [])
    rebuilt: List[Any] = []
    for item in raw_items:
        src_obj = _rebuild_object_from_snapshot(item)
        if src_obj is not None:
            rebuilt.append(src_obj)
    return rebuilt


def _debug_boolean_source_shift(poly: vtk.vtkPolyData, source_index: int) -> tuple[float, float, float]:
    bounds = poly.GetBounds() if poly is not None else None
    if bounds is None:
        return float(source_index + 1) * 20.0, 0.0, 0.0
    span_x = max(float(bounds[1]) - float(bounds[0]), 1.0)
    span_y = max(float(bounds[3]) - float(bounds[2]), 1.0)
    span_z = max(float(bounds[5]) - float(bounds[4]), 1.0)
    step = max(span_x, span_y, span_z) * 2.5
    return float(source_index + 1) * step, 0.0, 0.0


def export_debug_scene_step(
    objects: Iterable[Any],
    step_path: Path,
    log_callback: Callable[[str, str], None] | None = None,
) -> Dict[str, Any]:
    """Export simulation objects as one colored, named STEP assembly for inspection."""
    candidates = [obj for obj in objects if bool(getattr(obj, "is_model", True))]
    if not candidates:
        raise RuntimeError("No simulation model objects are available for the debug STEP export")

    last_error: Exception | None = None
    for package in ("OCP", "OCC.Core"):
        document = None
        application = None
        try:
            if package == "OCP":
                from OCP.STEPControl import STEPControl_AsIs
                from OCP.STEPCAFControl import STEPCAFControl_Writer
                from OCP.TCollection import TCollection_ExtendedString
                from OCP.TDataStd import TDataStd_Name
                from OCP.TDocStd import TDocStd_Document
                from OCP.XCAFApp import XCAFApp_Application
                from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
                from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
            else:
                from OCC.Core.STEPControl import STEPControl_AsIs
                from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
                from OCC.Core.TCollection import TCollection_ExtendedString
                from OCC.Core.TDataStd import TDataStd_Name
                from OCC.Core.TDocStd import TDocStd_Document
                from OCC.Core.XCAFApp import XCAFApp_Application
                from OCC.Core.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
                from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB

            application = XCAFApp_Application.GetApplication_s()
            document = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
            application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), document)
            shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
            color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
            skipped: list[str] = []
            exported = 0
            occ_cache: Dict[str, Any] = {}

            for obj in candidates:
                object_name = str(getattr(obj, "name", "Object"))
                shape = build_occ_shape_for_export(obj, cache=occ_cache)
                if shape is None:
                    actor = getattr(obj, "actor", None)
                    poly = _world_polydata_from_actor(actor) if actor is not None else vtk.vtkPolyData()
                    shape = _build_occ_faceted_shape_from_polydata(poly)
                if shape is None:
                    skipped.append(object_name)
                    continue

                component = shape_tool.AddShape(shape, False)
                TDataStd_Name.Set_s(component, TCollection_ExtendedString(object_name))
                try:
                    rgb = tuple(float(value) for value in obj._base_color())
                except Exception:
                    custom_color = getattr(obj, "custom_color", None)
                    if custom_color is not None and len(custom_color) == 3:
                        rgb = tuple(float(value) for value in custom_color)
                    else:
                        rgb = tuple(float(value) for value in obj.actor.GetProperty().GetColor())
                color = Quantity_Color(*rgb, Quantity_TOC_RGB)
                color_tool.SetColor(component, color, XCAFDoc_ColorType.XCAFDoc_ColorGen)
                color_tool.SetColor(component, color, XCAFDoc_ColorType.XCAFDoc_ColorSurf)
                exported += 1

            if exported == 0:
                raise RuntimeError("No simulation object geometry could be converted to STEP")

            writer = STEPCAFControl_Writer()
            writer.SetColorMode(True)
            writer.SetNameMode(True)
            if not writer.Transfer(document, STEPControl_AsIs):
                raise RuntimeError("XCAF STEP transfer failed")
            step_path.parent.mkdir(parents=True, exist_ok=True)
            status = int(writer.Write(str(step_path)))
            if not _step_write_succeeded(step_path, status):
                raise RuntimeError(f"XCAF STEP writer failed with status={status}")

            result = {"step_file": step_path.name, "exported": exported, "skipped": skipped}
            if log_callback is not None:
                log_callback("INFO", f"debug_scene_step file={step_path.name} exported={exported} skipped={len(skipped)}")
            return result
        except ImportError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            if package == "OCC.Core":
                break
        finally:
            if document is not None and application is not None:
                try:
                    application.Close(document)
                except Exception:
                    pass

    raise RuntimeError(f"Colored debug STEP export failed: {last_error}") from last_error


def _write_step_from_polydata(
    poly: vtk.vtkPolyData,
    step_path: Path,
    log_callback: Callable[[str, str], None] | None = None,
) -> None:
    temp_stl = step_path.with_suffix(".tmp.stl")

    repaired_poly, repair_stats = _repair_polydata_for_step(poly)
    if log_callback is not None:
        log_callback(
            "DEBUG",
            "mesh_repair "
            f"in_points={repair_stats['in_points']} in_polys={repair_stats['in_polys']} "
            f"in_open_edges={repair_stats['in_open_edges']} out_points={repair_stats['out_points']} "
            f"out_polys={repair_stats['out_polys']} out_open_edges={repair_stats['out_open_edges']}",
        )

    # Prefer direct faceted OCC export from scene-world polydata.
    # This avoids STL roundtrip side effects and keeps app coordinates authoritative.
    faceted = _build_occ_faceted_shape_from_polydata(repaired_poly)
    if faceted is not None:
        try:
            _write_step_from_occ_shape_with_modes(faceted, step_path)
            if log_callback is not None:
                log_callback("INFO", "faceted_direct_export_ok")
            return
        except Exception as direct_exc:
            if log_callback is not None:
                log_callback("WARNING", f"faceted_direct_export_failed error={direct_exc}; trying stl roundtrip")

    stl_writer = vtk.vtkSTLWriter()
    stl_writer.SetFileName(str(temp_stl))
    stl_writer.SetInputData(repaired_poly)
    stl_writer.Write()

    if (not temp_stl.exists()) or temp_stl.stat().st_size <= 0:
        raise RuntimeError("STL staging write failed")

    stl_roundtrip_error: Exception | None = None

    try:
        try:
            from OCP.TopoDS import TopoDS_Shape
            from OCP.StlAPI import StlAPI_Reader
            from OCP.STEPControl import (
                STEPControl_Writer,
                STEPControl_ManifoldSolidBrep,
                STEPControl_ShellBasedSurfaceModel,
                STEPControl_FacetedBrep,
                STEPControl_AsIs,
            )

            shape = TopoDS_Shape()
            reader = StlAPI_Reader()
            reader.Read(shape, str(temp_stl))

            writer = STEPControl_Writer()
            if not _transfer_step_shape(
                writer,
                shape,
                [
                    STEPControl_ManifoldSolidBrep,
                    STEPControl_ShellBasedSurfaceModel,
                    STEPControl_FacetedBrep,
                    STEPControl_AsIs,
                ],
            ):
                raise RuntimeError("STEP transfer failed for polydata-derived shape")
            status = int(writer.Write(str(step_path)))
            if not _step_write_succeeded(step_path, status):
                raise RuntimeError(f"STEP writer failed with status={status}")
            return
        except ImportError:
            pass

        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.StlAPI import StlAPI_Reader
        from OCC.Core.STEPControl import (
            STEPControl_Writer,
            STEPControl_ManifoldSolidBrep,
            STEPControl_ShellBasedSurfaceModel,
            STEPControl_FacetedBrep,
            STEPControl_AsIs,
        )

        shape = TopoDS_Shape()
        reader = StlAPI_Reader()
        reader.Read(shape, str(temp_stl))

        writer = STEPControl_Writer()
        if not _transfer_step_shape(
            writer,
            shape,
            [
                STEPControl_ManifoldSolidBrep,
                STEPControl_ShellBasedSurfaceModel,
                STEPControl_FacetedBrep,
                STEPControl_AsIs,
            ],
        ):
            raise RuntimeError("STEP transfer failed for polydata-derived shape")
        status = int(writer.Write(str(step_path)))
        if not _step_write_succeeded(step_path, status):
            raise RuntimeError(f"STEP writer failed with status={status}")
    except ImportError as exc:
        raise RuntimeError(
            "STEP export requires OCP or pythonOCC-core. "
            "Install one of them to generate per-object STEP files."
        ) from exc
    except Exception as exc:
        stl_roundtrip_error = exc
    finally:
        if temp_stl.exists():
            temp_stl.unlink(missing_ok=True)

    if stl_roundtrip_error is None:
        return

    if log_callback is not None:
        log_callback("WARNING", f"stl_roundtrip_failed error={stl_roundtrip_error}; trying faceted direct builder")

    faceted = _build_occ_faceted_shape_from_polydata(repaired_poly)
    if faceted is None:
        raise RuntimeError(f"STEP mesh export failed (stl+faceted): {stl_roundtrip_error}")

    _write_step_from_occ_shape_with_modes(faceted, step_path)
    if log_callback is not None:
        log_callback("INFO", "faceted_direct_export_ok")


def _write_step_from_occ_shape(shape: Any, step_path: Path) -> None:
    for pkg in ("OCP", "OCC.Core"):
        try:
            if pkg == "OCP":
                from OCP.STEPControl import (
                    STEPControl_Writer,
                    STEPControl_ManifoldSolidBrep,
                    STEPControl_ShellBasedSurfaceModel,
                    STEPControl_FacetedBrep,
                    STEPControl_AsIs,
                )
                writer = STEPControl_Writer()
                if not _transfer_step_shape(
                    writer,
                    shape,
                    [
                        STEPControl_ManifoldSolidBrep,
                        STEPControl_ShellBasedSurfaceModel,
                        STEPControl_FacetedBrep,
                        STEPControl_AsIs,
                    ],
                ):
                    raise RuntimeError("STEP transfer failed for OCC shape")
                status = int(writer.Write(str(step_path)))
                if not _step_write_succeeded(step_path, status):
                    raise RuntimeError(f"STEP writer failed with status={status}")
                return

            from OCC.Core.STEPControl import (
                STEPControl_Writer,
                STEPControl_ManifoldSolidBrep,
                STEPControl_ShellBasedSurfaceModel,
                STEPControl_FacetedBrep,
                STEPControl_AsIs,
            )
            writer = STEPControl_Writer()
            if not _transfer_step_shape(
                writer,
                shape,
                [
                    STEPControl_ManifoldSolidBrep,
                    STEPControl_ShellBasedSurfaceModel,
                    STEPControl_FacetedBrep,
                    STEPControl_AsIs,
                ],
            ):
                raise RuntimeError("STEP transfer failed for OCC shape")
            status = int(writer.Write(str(step_path)))
            if not _step_write_succeeded(step_path, status):
                raise RuntimeError(f"STEP writer failed with status={status}")
            return
        except ImportError:
            continue

    raise RuntimeError(
        "STEP export requires OCP or pythonOCC-core. "
        "Install one of them to generate per-object STEP files."
    )


def export_objects_to_step_bundle(
    objects: Iterable[Any],
    bundle_dir: Path,
    progress_callback: Callable[[int, int, str], None] | None = None,
    log_callback: Callable[[str, str], None] | None = None,
    debug_boolean_sources_only: bool = False,
    material_priorities: Dict[str, int] | None = None,
    excluded_object_names: set[str] | None = None,
) -> Dict[str, Any]:
    """Export one STEP file per model object, excluding only named port plates.

    Returns a dict with:
      - entries: list of dicts with keys object_name, step_file, material, priority
      - skipped: list of object names skipped by design
    
    Args:
        objects: List of objects to export
        bundle_dir: Directory to export STEP files to
        progress_callback: Optional callback for progress updates
        log_callback: Optional callback for log messages
        debug_boolean_sources_only: If True, export only boolean source objects
        material_priorities: Optional dict mapping material names to priority values
    """
    if material_priorities is None:
        material_priorities = {}
    excluded_object_names = excluded_object_names or set()
    bundle_dir.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    used_filenames: set[str] = set()
    occ_cache: Dict[str, Any] = {}
    export_candidates = [
        obj for obj in objects
        if str(getattr(obj, "name", "")).strip() not in excluded_object_names
    ]

    total = 0
    for obj in export_candidates:
        boolean_op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
        if debug_boolean_sources_only and boolean_op in {"fuse", "cut", "common"}:
            source_items = _boolean_source_items(obj)
            total += max(len(source_items), 1)
        else:
            total += 1
    done = 0

    def _export_one(
        export_obj: Any,
        export_name: str,
        filename_stem: str,
        idx: int,
        shape_override: Any | None = None,
    ) -> None:
        nonlocal done

        obj_type = type(export_obj).__name__
        material = str(getattr(export_obj, "material", "PEC"))

        if log_callback is not None:
            log_callback("DEBUG", f"object_start name={export_name} type={obj_type} material={material}")

        actor = getattr(export_obj, "actor", None)
        if actor is None:
            skipped.append(f"{export_name} ({obj_type}: no actor)")
            if log_callback is not None:
                log_callback("WARNING", f"object_skip name={export_name} reason=no_actor")
            done += 1
            if progress_callback is not None:
                progress_callback(done, total, export_name)
            return

        poly = _world_polydata_from_actor(actor)
        if poly.GetNumberOfPoints() <= 0:
            skipped.append(f"{export_name} ({obj_type}: empty geometry)")
            if log_callback is not None:
                log_callback("WARNING", f"object_skip name={export_name} reason=empty_geometry")
            done += 1
            if progress_callback is not None:
                progress_callback(done, total, export_name)
            return

        stem = _sanitize_name(filename_stem)
        filename = f"{stem}.step"
        n = 2
        while filename.lower() in used_filenames:
            filename = f"{stem}_{n}.step"
            n += 1
        used_filenames.add(filename.lower())

        step_path = bundle_dir / filename
        export_mode = "mesh_roundtrip"
        solid_count = -1
        try:
            boolean_op = str(getattr(export_obj, "boolean_op", "") or "").strip().lower()
            has_step_source = bool(str(getattr(export_obj, "step_source_path", "") or "").strip())
            prefer_occ = (obj_type != "MeshObject") or bool(boolean_op) or has_step_source
            shape = shape_override if shape_override is not None else (
                build_occ_shape_for_export(export_obj, cache=occ_cache) if prefer_occ else None
            )

            if shape is not None:
                recovered = getattr(export_obj, "_step_export_offset_recovered", None)
                if recovered is not None and log_callback is not None:
                    try:
                        dx, dy, dz = float(recovered[0]), float(recovered[1]), float(recovered[2])
                        log_callback("INFO", f"legacy_offset_recovered name={export_name} d=({dx:.6g},{dy:.6g},{dz:.6g})")
                    except Exception:
                        pass
                    try:
                        delattr(export_obj, "_step_export_offset_recovered")
                    except Exception:
                        pass
                solid_count = _count_solids_in_shape(shape)
                if log_callback is not None:
                    log_callback("DEBUG", f"occ_candidate name={export_name} solids={solid_count}")
                try:
                    _write_step_from_occ_shape(shape, step_path)
                    export_mode = "brep_direct"
                    if log_callback is not None:
                        log_callback("INFO", f"occ_export_ok name={export_name} file={filename}")
                except Exception as occ_exc:
                    if log_callback is not None:
                        log_callback("WARNING", f"occ_export_failed name={export_name} error={occ_exc}; falling back to scene geometry")
                    _write_step_from_polydata(poly, step_path, log_callback=log_callback)
            else:
                if log_callback is not None:
                    log_callback("DEBUG", f"scene_geometry_export name={export_name} points={poly.GetNumberOfPoints()} polys={poly.GetNumberOfPolys()}")
                _write_step_from_polydata(poly, step_path, log_callback=log_callback)
        except Exception as exc:
            skipped.append(f"{export_name} ({obj_type}: export failed: {exc})")
            if log_callback is not None:
                log_callback("ERROR", f"object_failed name={export_name} mode={export_mode} file={filename} error={exc}")
            done += 1
            if progress_callback is not None:
                progress_callback(done, total, export_name)
            return

        done += 1
        if progress_callback is not None:
            progress_callback(done, total, export_name)

        # Calculate priority from material_priorities dict or use default (base + index)
        material_priority = material_priorities.get(material, 0) if material_priorities else 0
        priority_value = 5000 + idx + (material_priority * 100)

        entries.append(
            {
                "object_name": export_name,
                "object_type": obj_type,
                "step_file": filename,
                "material": material,
                "priority": priority_value,
                "export_mode": export_mode,
                "solid_count": solid_count,
                "poly_points": int(poly.GetNumberOfPoints()),
                "poly_polys": int(poly.GetNumberOfPolys()),
                "boolean_op": str(getattr(export_obj, "boolean_op", "") or "").strip().lower() or None,
                "source_count": len(list(getattr(export_obj, "source_objects", []) or [])),
            }
        )

        if log_callback is not None:
            log_callback(
                "INFO",
                f"object_done name={export_name} mode={export_mode} file={filename} "
                f"poly_points={poly.GetNumberOfPoints()} poly_polys={poly.GetNumberOfPolys()}",
            )

    for idx, obj in enumerate(export_candidates, start=1):
        obj_name = str(getattr(obj, "name", f"Object_{idx}"))
        obj_type = type(obj).__name__
        boolean_op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
        if debug_boolean_sources_only and boolean_op in {"fuse", "cut", "common"}:
            source_items = _boolean_source_items(obj)
            if not source_items:
                if log_callback is not None:
                    log_callback("WARNING", f"object_skip name={obj_name} reason=boolean_debug_no_sources")
                done += 1
                if progress_callback is not None:
                    progress_callback(done, total, obj_name)
                continue

            if log_callback is not None:
                log_callback("INFO", f"object_skip name={obj_name} reason=boolean_debug_sources_only")

            for source_index, source_obj in enumerate(source_items, start=1):
                source_name = str(getattr(source_obj, "name", f"{obj_name}_src{source_index}"))
                source_shape = build_occ_shape_for_export(source_obj, cache=occ_cache)
                if source_shape is None:
                    if log_callback is not None:
                        log_callback("WARNING", f"object_skip name={source_name} reason=debug_source_export_failed")
                    done += 1
                    if progress_callback is not None:
                        progress_callback(done, total, source_name)
                    continue
                source_poly = _world_polydata_from_actor(getattr(source_obj, "actor", None))
                if source_poly.GetNumberOfPoints() > 0:
                    shift = _debug_boolean_source_shift(source_poly, source_index)
                    source_shape = _translate_shape(source_shape, shift[0], shift[1], shift[2])
                    if log_callback is not None:
                        log_callback("DEBUG", f"boolean_debug_shift name={source_name} d=({shift[0]:.6g},{shift[1]:.6g},{shift[2]:.6g})")
                debug_name = f"{obj_name}__src{source_index}__{source_name}"
                _export_one(source_obj, debug_name, debug_name, idx * 1000 + source_index, shape_override=source_shape)
            continue

        _export_one(obj, obj_name, obj_name, idx)

    return {"entries": entries, "skipped": skipped}
