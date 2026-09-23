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

"""Boolean (CSG) operations between EMObjects via VTK."""
from __future__ import annotations

import math
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


def _bounds_have_volume_overlap(a: vtk.vtkPolyData, b: vtk.vtkPolyData, tol: float = 1e-6) -> bool:
    """Return whether AABBs share a non-zero volume, not just a touching face."""
    ab = a.GetBounds()
    bb = b.GetBounds()
    if ab is None or bb is None:
        return False
    return (
        min(ab[1], bb[1]) - max(ab[0], bb[0]) > tol
        and min(ab[3], bb[3]) - max(ab[2], bb[2]) > tol
        and min(ab[5], bb[5]) - max(ab[4], bb[4]) > tol
    )


def _bounds_share_coplanar_plane(a: vtk.vtkPolyData, b: vtk.vtkPolyData, tol: float = 1e-6) -> bool:
    """Return whether two AABBs have at least one coincident boundary plane."""
    ab = a.GetBounds()
    bb = b.GetBounds()
    if ab is None or bb is None:
        return False
    return any(
        abs(a_value - b_value) <= tol
        for axis in range(3)
        for a_value in (ab[axis * 2], ab[axis * 2 + 1])
        for b_value in (bb[axis * 2], bb[axis * 2 + 1])
    )


def _separate_coplanar_tool_planes(base: vtk.vtkPolyData, tool: vtk.vtkPolyData) -> vtk.vtkPolyData:
    """Nudge a tool off shared AABB planes so surface CSG has no coincident faces."""
    base_bounds = base.GetBounds()
    tool_bounds = tool.GetBounds()
    if base_bounds is None or tool_bounds is None:
        return tool

    scale = max(*(abs(value) for value in (*base_bounds, *tool_bounds)), 1.0)
    offset = scale * 1e-7
    translation = [0.0, 0.0, 0.0]
    for axis in range(3):
        base_min, base_max = base_bounds[axis * 2:axis * 2 + 2]
        tool_min, tool_max = tool_bounds[axis * 2:axis * 2 + 2]
        if abs(tool_min - base_min) <= offset:
            translation[axis] = offset
        elif abs(tool_max - base_max) <= offset:
            translation[axis] = -offset

    if not any(translation):
        return tool
    transform = vtk.vtkTransform()
    transform.Translate(*translation)
    transformed = vtk.vtkTransformPolyDataFilter()
    transformed.SetTransform(transform)
    transformed.SetInputData(tool)
    transformed.Update()
    return transformed.GetOutput()


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


def _axis_aligned_box_bounds(poly: vtk.vtkPolyData) -> tuple[float, float, float, float, float, float] | None:
    """Return bounds when ``poly`` is an axis-aligned rectangular solid."""
    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(poly)
    clean.SetToleranceIsAbsolute(1)
    clean.SetAbsoluteTolerance(1e-9)
    clean.Update()
    compact = clean.GetOutput()
    if compact is None or compact.GetNumberOfPoints() != 8:
        return None

    bounds = compact.GetBounds()
    if bounds is None:
        return None
    scale = max(max(abs(value) for value in bounds), 1.0)
    tolerance = scale * 1e-8
    for index in range(compact.GetNumberOfPoints()):
        x, y, z = compact.GetPoint(index)
        if not (
            min(abs(x - bounds[0]), abs(x - bounds[1])) <= tolerance
            and min(abs(y - bounds[2]), abs(y - bounds[3])) <= tolerance
            and min(abs(z - bounds[4]), abs(z - bounds[5])) <= tolerance
        ):
            return None
    return tuple(float(value) for value in bounds)


def _fuse_axis_aligned_boxes(polys: list[vtk.vtkPolyData]) -> vtk.vtkPolyData | None:
    """Create the exact exterior shell of overlapping axis-aligned boxes."""
    bounds_list = [_axis_aligned_box_bounds(poly) for poly in polys]
    if any(bounds is None for bounds in bounds_list):
        return None

    boxes = [bounds for bounds in bounds_list if bounds is not None]
    return _axis_aligned_box_shell(boxes)


def _axis_aligned_box_shell(boxes: list[tuple[float, float, float, float, float, float]]) -> vtk.vtkPolyData:
    """Create an exterior shell from a set of non-rotated box bounds."""
    coordinates = [
        sorted({bound[index] for bound in boxes for index in (axis * 2, axis * 2 + 1)})
        for axis in range(3)
    ]
    points = vtk.vtkPoints()
    faces = vtk.vtkCellArray()
    emitted: set[tuple[int, float, float, float, float, float]] = set()

    def is_covered(point: tuple[float, float, float]) -> bool:
        return any(
            bounds[0] <= point[0] <= bounds[1]
            and bounds[2] <= point[1] <= bounds[3]
            and bounds[4] <= point[2] <= bounds[5]
            for bounds in boxes
        )

    for bounds in boxes:
        for axis in range(3):
            other_axes = [value for value in range(3) if value != axis]
            fixed_min, fixed_max = bounds[axis * 2], bounds[axis * 2 + 1]
            first_values = [
                value for value in coordinates[other_axes[0]]
                if bounds[other_axes[0] * 2] <= value <= bounds[other_axes[0] * 2 + 1]
            ]
            second_values = [
                value for value in coordinates[other_axes[1]]
                if bounds[other_axes[1] * 2] <= value <= bounds[other_axes[1] * 2 + 1]
            ]
            for fixed, direction in ((fixed_min, -1.0), (fixed_max, 1.0)):
                for first_index in range(len(first_values) - 1):
                    for second_index in range(len(second_values) - 1):
                        first_start, first_end = first_values[first_index:first_index + 2]
                        second_start, second_end = second_values[second_index:second_index + 2]
                        center = [0.0, 0.0, 0.0]
                        center[axis] = math.nextafter(
                            fixed,
                            math.inf if direction > 0.0 else -math.inf,
                        )
                        center[other_axes[0]] = (first_start + first_end) / 2.0
                        center[other_axes[1]] = (second_start + second_end) / 2.0
                        if is_covered(tuple(center)):
                            continue

                        key = (axis, fixed, first_start, first_end, second_start, second_end)
                        if key in emitted:
                            continue
                        emitted.add(key)
                        corners = []
                        for first, second in (
                            (first_start, second_start),
                            (first_end, second_start),
                            (first_end, second_end),
                            (first_start, second_end),
                        ):
                            corner = [0.0, 0.0, 0.0]
                            corner[axis] = fixed
                            corner[other_axes[0]] = first
                            corner[other_axes[1]] = second
                            corners.append(points.InsertNextPoint(corner))
                        faces.InsertNextCell(4, corners)

    result = vtk.vtkPolyData()
    result.SetPoints(points)
    result.SetPolys(faces)
    triangulate = vtk.vtkTriangleFilter()
    triangulate.SetInputData(result)
    triangulate.Update()
    return triangulate.GetOutput()


def _cut_axis_aligned_boxes(base: vtk.vtkPolyData, tool: vtk.vtkPolyData) -> vtk.vtkPolyData | None:
    """Return the exact difference of two axis-aligned rectangular solids."""
    base_bounds = _axis_aligned_box_bounds(base)
    tool_bounds = _axis_aligned_box_bounds(tool)
    if base_bounds is None or tool_bounds is None:
        return None

    intersection = (
        max(base_bounds[0], tool_bounds[0]), min(base_bounds[1], tool_bounds[1]),
        max(base_bounds[2], tool_bounds[2]), min(base_bounds[3], tool_bounds[3]),
        max(base_bounds[4], tool_bounds[4]), min(base_bounds[5], tool_bounds[5]),
    )
    if (
        intersection[0] >= intersection[1]
        or intersection[2] >= intersection[3]
        or intersection[4] >= intersection[5]
    ):
        return _axis_aligned_box_shell([base_bounds])

    x0, x1, y0, y1, z0, z1 = base_bounds
    ix0, ix1, iy0, iy1, iz0, iz1 = intersection
    pieces = [
        (x0, ix0, y0, y1, z0, z1),
        (ix1, x1, y0, y1, z0, z1),
        (ix0, ix1, y0, iy0, z0, z1),
        (ix0, ix1, iy1, y1, z0, z1),
        (ix0, ix1, iy0, iy1, z0, iz0),
        (ix0, ix1, iy0, iy1, iz1, z1),
    ]
    valid_pieces = [
        piece for piece in pieces
        if piece[0] < piece[1] and piece[2] < piece[3] and piece[4] < piece[5]
    ]
    return _axis_aligned_box_shell(valid_pieces) if valid_pieces else vtk.vtkPolyData()


def _postprocess(poly: vtk.vtkPolyData) -> vtk.vtkPolyData:
    if poly is None or poly.GetNumberOfPoints() == 0:
        raise RuntimeError("Boolean produced an empty result")

    # First pass: aggressive cleaning to remove degenerate triangles and duplicate points
    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(poly)
    clean.ToleranceIsAbsoluteOff()
    clean.SetTolerance(1e-6)
    clean.ConvertLinesToPointsOff()
    clean.ConvertPolysToLinesOff()
    clean.Update()

    # Second pass: remove degenerate cells
    cleaned_data = clean.GetOutput()
    
    # Preserve hard CSG edges while smoothing only continuous surfaces.
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(cleaned_data)
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    normals.SetFeatureAngle(60.0)
    normals.SplittingOn()
    normals.Update()
    
    result = normals.GetOutput()
    
    return result


def _boolean_polydata(op: str, pa: vtk.vtkPolyData, pb: vtk.vtkPolyData) -> vtk.vtkPolyData:
    if op not in _OPS:
        raise ValueError(f"Unknown boolean op: {op}")

    bf = vtk.vtkBooleanOperationPolyDataFilter()
    bf.SetOperation(_OPS[op])
    bf.SetInputData(0, pa)
    bf.SetInputData(1, pb)
    bf.Update()
    return _postprocess(bf.GetOutput())


def _implicit_cut_polydata(base: vtk.vtkPolyData, tool: vtk.vtkPolyData) -> vtk.vtkPolyData:
    """Re-mesh a cut from signed distances when surface CSG is degenerate."""
    bounds = base.GetBounds()
    if bounds is None:
        raise RuntimeError("Boolean cut base has no bounds")

    lengths = [bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]]
    maximum_length = max(lengths)
    if maximum_length <= 0.0:
        raise RuntimeError("Boolean cut base has degenerate bounds")

    spacing = maximum_length / 128.0
    padding = spacing * 2.0
    dimensions = [
        max(24, min(160, int(length / spacing) + 5))
        for length in lengths
    ]
    base_distance = vtk.vtkImplicitPolyDataDistance()
    base_distance.SetInput(base)
    tool_distance = vtk.vtkImplicitPolyDataDistance()
    tool_distance.SetInput(tool)
    difference = vtk.vtkImplicitBoolean()
    difference.SetOperationTypeToDifference()
    difference.AddFunction(base_distance)
    difference.AddFunction(tool_distance)

    sample = vtk.vtkSampleFunction()
    sample.SetImplicitFunction(difference)
    sample.SetModelBounds(
        bounds[0] - padding, bounds[1] + padding,
        bounds[2] - padding, bounds[3] + padding,
        bounds[4] - padding, bounds[5] + padding,
    )
    sample.SetSampleDimensions(*dimensions)
    sample.ComputeNormalsOff()
    sample.Update()

    contour = vtk.vtkFlyingEdges3D()
    contour.SetInputConnection(sample.GetOutputPort())
    contour.SetValue(0, 0.0)
    contour.Update()
    return _postprocess(contour.GetOutput())


def _occ_boolean(op: str, objects: list["EMObject"]) -> vtk.vtkPolyData | None:
    """Run an exact OpenCascade boolean for supported native primitives."""
    try:
        from ..emerge.occ_export_builder import _apply_boolean, build_occ_shape_for_export
    except ImportError:
        return None

    try:
        shapes = [build_occ_shape_for_export(obj) for obj in objects]
        if any(shape is None for shape in shapes):
            return None
        result = shapes[0]
        for tool in shapes[1:]:
            result = _apply_boolean(result, tool, op)
            if result is None:
                return None

        bounds = result.BoundingBox() if hasattr(result, "BoundingBox") else None
        if bounds is not None:
            extent = max(abs(float(bounds.GetXmax()) - float(bounds.GetXmin())),
                         abs(float(bounds.GetYmax()) - float(bounds.GetYmin())),
                         abs(float(bounds.GetZmax()) - float(bounds.GetZmin())))
        else:
            extent = 1.0
        # Boolean edges on curved primitives need a finer angular mesh than the
        # export default; otherwise the cylinder intersection visibly facets.
        deflection = max(extent / 1000.0, 1e-4)
        angular_deflection = 0.03

        try:
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from ..emerge.step_importer import _ocp_shape_to_vtk
            BRepMesh_IncrementalMesh(result, deflection, False, angular_deflection, True)
            poly = _ocp_shape_to_vtk(result)
        except ImportError:
            from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
            from ..emerge.step_importer import _occ_shape_to_vtk
            BRepMesh_IncrementalMesh(result, deflection, False, angular_deflection)
            poly = _occ_shape_to_vtk(result)
        return poly if poly is not None and poly.GetNumberOfPoints() > 0 else None
    except Exception:
        return None


def boolean(op: str, a: "EMObject", b: "EMObject") -> vtk.vtkPolyData:
    """Run a boolean operation on two objects; returns triangulated polydata."""
    pa = _world_polydata(a)
    pb = _world_polydata(b)
    if op == "cut":
        if not _bounds_have_volume_overlap(pa, pb):
            return _postprocess(pa)
        exact_cut = _cut_axis_aligned_boxes(pa, pb)
        if exact_cut is not None:
            if exact_cut.GetNumberOfPoints() == 0:
                raise RuntimeError(
                    "Boolean cut removes the entire base object. "
                    "Select the solid to keep first, then the solid to subtract."
                )
            return _postprocess(exact_cut)
    occ_result = _occ_boolean(op, [a, b])
    if occ_result is not None:
        return _postprocess(occ_result)
    if op == "cut" and _bounds_share_coplanar_plane(pa, pb):
        pb = _separate_coplanar_tool_planes(pa, pb)
    try:
        return _boolean_polydata(op, pa, pb)
    except RuntimeError as error:
        if op == "cut":
            try:
                return _implicit_cut_polydata(pa, pb)
            except RuntimeError:
                pass
        raise RuntimeError(
            f"Boolean '{op}' produced an empty result. "
            "Inputs may be non-manifold, non-overlapping, or coincident."
        ) from error


def boolean_many(op: str, objects: list["EMObject"]) -> vtk.vtkPolyData:
    """Apply one boolean operation to original base and tool objects together."""
    if len(objects) < 2:
        raise ValueError("Select at least two objects for a boolean operation")

    occ_result = _occ_boolean(op, objects)
    if occ_result is not None:
        return _postprocess(occ_result)

    current = objects[0]
    result = None
    from .em_objects import MeshObject
    for index, tool in enumerate(objects[1:]):
        result = boolean(op, current, tool)
        if index < len(objects) - 2:
            current = MeshObject(
                name=f"_tmp_{op}_{index}",
                polydata=result,
                material=objects[0].material,
            )
    if result is None:
        raise RuntimeError(f"Boolean '{op}' produced no result")
    return result


def fuse_many(objects: list["EMObject"]) -> vtk.vtkPolyData:
    """Fuse 2..N objects into one polydata.

    Uses sequential boolean union first; if a step fails, falls back to
    append+clean so union of disjoint bodies still returns a valid merged mesh.
    """
    if len(objects) < 2:
        raise ValueError("Select at least 2 objects for fuse")

    if not all(type(obj).__name__ in {"BoxObject", "PlateObject"} for obj in objects):
        occ_result = _occ_boolean("fuse", objects)
        if occ_result is not None:
            return _postprocess(occ_result)

    # Extract and validate all polydata upfront
    world_polys = []
    for obj in objects:
        try:
            poly = _world_polydata(obj)
            if poly is None or poly.GetNumberOfPoints() == 0:
                raise RuntimeError(f"Object {obj.name} has invalid polydata (no points)")
            world_polys.append(poly)
        except Exception as e:
            raise RuntimeError(
                f"Failed to extract polydata from {obj.name}: {str(e)}"
            )

    box_fuse = _fuse_axis_aligned_boxes(world_polys)
    if box_fuse is not None:
        return _postprocess(box_fuse)

    current = world_polys[0]
    for idx, nxt in enumerate(world_polys[1:], start=1):
        try:
            # Validate inputs before operation
            if current.GetNumberOfPoints() == 0 or nxt.GetNumberOfPoints() == 0:
                raise RuntimeError("One of the input polydata has no points")

            # Disjoint solids cannot have their common volume removed.
            if not _bounds_overlap(current, nxt):
                raise RuntimeError("Inputs are disjoint; using append fallback")

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
