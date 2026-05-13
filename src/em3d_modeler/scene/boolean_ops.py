"""Boolean (CSG) operations between EMObjects via VTK."""
from __future__ import annotations

from typing import TYPE_CHECKING

import vtk

if TYPE_CHECKING:
    from .em_objects import EMObject


_OPS = {
    "cut": vtk.vtkBooleanOperationPolyDataFilter.VTK_DIFFERENCE,
    "fuse": vtk.vtkBooleanOperationPolyDataFilter.VTK_UNION,
    "common": vtk.vtkBooleanOperationPolyDataFilter.VTK_INTERSECTION,
}


def _bounds_overlap(a: vtk.vtkPolyData, b: vtk.vtkPolyData, tol: float = 1e-6) -> bool:
    """Fast AABB overlap test; avoids boolean on clearly disjoint meshes."""
    ab = a.GetBounds()
    bb = b.GetBounds()
    if ab is None or bb is None:
        return False

    return not (
        ab[1] < bb[0] - tol
        or bb[1] < ab[0] - tol
        or ab[3] < bb[2] - tol
        or bb[3] < ab[2] - tol
        or ab[5] < bb[4] - tol
        or bb[5] < ab[4] - tol
    )


def _is_closed_manifold(poly: vtk.vtkPolyData) -> bool:
    """Heuristic safety gate for VTK boolean filter stability."""
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(poly)
    edges.BoundaryEdgesOn()
    edges.NonManifoldEdgesOn()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.Update()
    out = edges.GetOutput()
    return out is not None and out.GetNumberOfCells() == 0


def _is_step_solid_name(name: str) -> bool:
    u = name.upper()
    return u.startswith("STEP_") or "STEP_SOLID" in u


def _world_polydata(obj: "EMObject") -> vtk.vtkPolyData:
    """Extract the actor's polydata in world coordinates, triangulated."""
    actor = obj.actor
    if actor is None:
        raise ValueError(f"Object {obj.name} has no actor")

    mapper = actor.GetMapper()
    if mapper is None:
        raise ValueError(f"Object {obj.name} has no mapper")

    # Try modern method first: GetInputDataObject works for all mapper types
    poly = None
    try:
        poly = mapper.GetInputDataObject(0, 0)
    except (AttributeError, RuntimeError):
        pass

    # Fallback: try GetInputAlgorithm() + Update() + GetOutput()
    if poly is None:
        src = mapper.GetInputAlgorithm()
        if src is not None:
            try:
                src.Update()
                if hasattr(src, 'GetOutput'):
                    poly = src.GetOutput()
            except (AttributeError, RuntimeError):
                pass

    # Final fallback: direct GetInput()
    if poly is None:
        poly = mapper.GetInput()

    if poly is None:
        raise ValueError(f"Object {obj.name} has no polydata input")

    transform = vtk.vtkTransform()
    transform.SetMatrix(actor.GetMatrix())

    tf = vtk.vtkTransformPolyDataFilter()
    tf.SetTransform(transform)
    tf.SetInputData(poly)
    tf.Update()

    tri = vtk.vtkTriangleFilter()
    tri.SetInputConnection(tf.GetOutputPort())
    tri.PassLinesOff()
    tri.PassVertsOff()
    tri.Update()
    return tri.GetOutput()


def _postprocess(poly: vtk.vtkPolyData) -> vtk.vtkPolyData:
    if poly is None or poly.GetNumberOfPoints() == 0:
        raise RuntimeError("Boolean produced an empty result")

    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(poly)
    clean.Update()

    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(clean.GetOutput())
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    normals.SplittingOff()
    normals.Update()
    return normals.GetOutput()


def _boolean_polydata(op: str, pa: vtk.vtkPolyData, pb: vtk.vtkPolyData) -> vtk.vtkPolyData:
    if op not in _OPS:
        raise ValueError(f"Unknown boolean op: {op}")

    bf = vtk.vtkBooleanOperationPolyDataFilter()
    bf.SetOperation(_OPS[op])
    bf.SetInputData(0, pa)
    bf.SetInputData(1, pb)
    bf.Update()
    return _postprocess(bf.GetOutput())


def boolean(op: str, a: "EMObject", b: "EMObject") -> vtk.vtkPolyData:
    """Run a boolean operation on two objects; returns triangulated polydata."""
    pa = _world_polydata(a)
    pb = _world_polydata(b)
    try:
        return _boolean_polydata(op, pa, pb)
    except RuntimeError:
        raise RuntimeError(
            f"Boolean '{op}' produced an empty result. "
            "Inputs may be non-manifold, non-overlapping, or coincident."
        )


def fuse_many(objects: list["EMObject"]) -> vtk.vtkPolyData:
    """Fuse 2..N objects into one polydata.

    Uses sequential boolean union first; if a step fails, falls back to
    append+clean so union of disjoint bodies still returns a valid merged mesh.
    """
    if len(objects) < 2:
        raise ValueError("Select at least 2 objects for fuse")

    # Extract and validate all polydata upfront
    world_polys = []
    object_names = []
    for obj in objects:
        try:
            poly = _world_polydata(obj)
            if poly is None or poly.GetNumberOfPoints() == 0:
                raise RuntimeError(f"Object {obj.name} has invalid polydata (no points)")
            world_polys.append(poly)
            object_names.append(obj.name)
        except Exception as e:
            raise RuntimeError(
                f"Failed to extract polydata from {obj.name}: {str(e)}"
            )

    # Robust path for complex imported STEP assemblies (prevents VTK native crashes).
    if len(objects) > 2 and all(_is_step_solid_name(n) for n in object_names):
        append = vtk.vtkAppendPolyData()
        for p in world_polys:
            append.AddInputData(p)
        append.Update()
        return _postprocess(append.GetOutput())

    current = world_polys[0]
    for idx, nxt in enumerate(world_polys[1:], start=1):
        try:
            # Validate inputs before operation
            if current.GetNumberOfPoints() == 0 or nxt.GetNumberOfPoints() == 0:
                raise RuntimeError("One of the input polydata has no points")

            # Avoid known VTK crash paths on bad STEP topology.
            if not _bounds_overlap(current, nxt):
                raise RuntimeError("Inputs are disjoint; using append fallback")
            if not _is_closed_manifold(current) or not _is_closed_manifold(nxt):
                raise RuntimeError("Non-manifold/open mesh detected; using append fallback")

            current = _boolean_polydata("fuse", current, nxt)
        except Exception as e:
            # Boolean union failed; use append for remaining objects
            append = vtk.vtkAppendPolyData()
            append.AddInputData(current)
            # Add all remaining polydata starting from index idx
            for p in world_polys[idx:]:
                append.AddInputData(p)
            append.Update()
            result = append.GetOutput()
            if result is None or result.GetNumberOfPoints() == 0:
                raise RuntimeError(
                    f"Boolean fuse failed at step {idx} and append fallback also produced empty result. "
                    f"Original error: {str(e)}"
                )
            return _postprocess(result)

    return _postprocess(current)
