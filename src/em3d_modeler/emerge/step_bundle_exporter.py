"""Export scene objects to per-object STEP files for EMERGE simulation bundles."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import re

import vtk


_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_name(name: str) -> str:
    out = _NAME_SANITIZE_RE.sub("_", str(name).strip())
    out = out.strip("_")
    return out or "Object"


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


def _write_step_from_polydata(poly: vtk.vtkPolyData, step_path: Path) -> None:
    temp_stl = step_path.with_suffix(".tmp.stl")

    stl_writer = vtk.vtkSTLWriter()
    stl_writer.SetFileName(str(temp_stl))
    stl_writer.SetInputData(poly)
    stl_writer.Write()

    try:
        try:
            from OCP.TopoDS import TopoDS_Shape
            from OCP.StlAPI import StlAPI_Reader
            from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs

            shape = TopoDS_Shape()
            reader = StlAPI_Reader()
            reader.Read(shape, str(temp_stl))

            writer = STEPControl_Writer()
            writer.Transfer(shape, STEPControl_AsIs)
            status = int(writer.Write(str(step_path)))
            if status != 1:
                raise RuntimeError(f"STEP writer failed with status={status}")
            return
        except ImportError:
            pass

        from OCC.Core.TopoDS import TopoDS_Shape
        from OCC.Core.StlAPI import StlAPI_Reader
        from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs

        shape = TopoDS_Shape()
        reader = StlAPI_Reader()
        reader.Read(shape, str(temp_stl))

        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        status = int(writer.Write(str(step_path)))
        if status != 1:
            raise RuntimeError(f"STEP writer failed with status={status}")
    except ImportError as exc:
        raise RuntimeError(
            "STEP export requires OCP or pythonOCC-core. "
            "Install one of them to generate per-object STEP files."
        ) from exc
    finally:
        if temp_stl.exists():
            temp_stl.unlink(missing_ok=True)


def export_objects_to_step_bundle(
    objects: Iterable[Any],
    bundle_dir: Path,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Dict[str, Any]:
    """Export one STEP file per object (excluding PlateObject).

    Returns a dict with:
      - entries: list of dicts with keys object_name, step_file, material, priority
      - skipped: list of object names skipped by design
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    used_filenames: set[str] = set()
    export_candidates = [
        obj for obj in objects
        if type(obj).__name__ != "PlateObject"
    ]
    total = len(export_candidates)
    done = 0

    for idx, obj in enumerate(objects, start=1):
        obj_name = str(getattr(obj, "name", f"Object_{idx}"))
        obj_type = type(obj).__name__
        material = str(getattr(obj, "material", "PEC"))

        if obj_type == "PlateObject":
            skipped.append(f"{obj_name} ({obj_type})")
            continue

        actor = getattr(obj, "actor", None)
        if actor is None:
            skipped.append(f"{obj_name} ({obj_type}: no actor)")
            done += 1
            if progress_callback is not None:
                progress_callback(done, total, obj_name)
            continue

        poly = _world_polydata_from_actor(actor)
        if poly.GetNumberOfPoints() <= 0:
            skipped.append(f"{obj_name} ({obj_type}: empty geometry)")
            done += 1
            if progress_callback is not None:
                progress_callback(done, total, obj_name)
            continue

        stem = _sanitize_name(obj_name)
        filename = f"{stem}.step"
        n = 2
        while filename.lower() in used_filenames:
            filename = f"{stem}_{n}.step"
            n += 1
        used_filenames.add(filename.lower())

        step_path = bundle_dir / filename
        _write_step_from_polydata(poly, step_path)

        done += 1
        if progress_callback is not None:
            progress_callback(done, total, obj_name)

        entries.append(
            {
                "object_name": obj_name,
                "step_file": filename,
                "material": material,
                "priority": 5000 + idx,
            }
        )

    return {"entries": entries, "skipped": skipped}
