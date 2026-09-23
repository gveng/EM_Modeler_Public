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

"""Grid actor builder for the 3D viewport."""
from __future__ import annotations
import math
import vtk

PLANES = ("XY", "XZ", "YZ")


def plane_basis_from_normal(normal: tuple) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return stable orthonormal local X/Y axes for a plane normal."""
    import math

    nx, ny, nz = (float(value) for value in normal)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        raise ValueError("Plane normal must be non-zero")
    nx, ny, nz = nx / length, ny / length, nz / length
    reference = (0.0, 0.0, 1.0) if abs(nz) < 0.99 else (0.0, 1.0, 0.0)
    xx = reference[1] * nz - reference[2] * ny
    xy = reference[2] * nx - reference[0] * nz
    xz = reference[0] * ny - reference[1] * nx
    x_length = math.sqrt(xx * xx + xy * xy + xz * xz)
    x_axis = (xx / x_length, xy / x_length, xz / x_length)
    y_axis = (
        ny * x_axis[2] - nz * x_axis[1],
        nz * x_axis[0] - nx * x_axis[2],
        nx * x_axis[1] - ny * x_axis[0],
    )
    return x_axis, y_axis


def project_bounds_to_plane(bounds: tuple, origin: tuple, normal: tuple,
                            margin: float = 0.0) -> tuple[float, float, float, float] | None:
    """Project world-axis-aligned bounds onto a plane and expand by *margin*."""
    import math

    if bounds is None or len(bounds) != 6:
        return None
    values = tuple(float(value) for value in bounds)
    if not all(math.isfinite(value) for value in values):
        return None
    if values[0] > values[1] or values[2] > values[3] or values[4] > values[5]:
        return None

    x_axis, y_axis = plane_basis_from_normal(normal)
    margin = max(0.0, float(margin))
    origin = tuple(float(value) for value in origin)
    projected_x = []
    projected_y = []
    for x in (values[0], values[1]):
        for y in (values[2], values[3]):
            for z in (values[4], values[5]):
                point = (x - origin[0], y - origin[1], z - origin[2])
                projected_x.append(sum(point[i] * x_axis[i] for i in range(3)))
                projected_y.append(sum(point[i] * y_axis[i] for i in range(3)))

    return (
        min(projected_x) - margin,
        max(projected_x) + margin,
        min(projected_y) - margin,
        max(projected_y) + margin,
    )


def build_grid_actor(

    size: float = 200.0,
    spacing: float = 10.0,
    plane: str = "XY",
    color: tuple = (0.45, 0.45, 0.45),
    opacity: float = 0.40,
    origin: tuple = (0.0, 0.0, 0.0),
    normal: tuple = None,
    extents: tuple | None = None,
) -> vtk.vtkActor:
    """
    Return a VTK actor that draws a uniform grid on *plane* centred at origin.
    If normal is provided, grid is built on the arbitrary plane defined by (origin, normal).
    """
    import numpy as np
    points = vtk.vtkPoints()
    lines  = vtk.vtkCellArray()
    half   = size / 2.0
    n      = int(half / spacing)

    def add_line(p1, p2):
        i1 = points.InsertNextPoint(*p1)
        i2 = points.InsertNextPoint(*p2)
        cell = vtk.vtkLine()
        cell.GetPointIds().SetId(0, i1)
        cell.GetPointIds().SetId(1, i2)
        lines.InsertNextCell(cell)

    if extents is not None:
        x_min, x_max, y_min, y_max = (float(value) for value in extents)
        x_min = math.floor(x_min / spacing) * spacing
        x_max = math.ceil(x_max / spacing) * spacing
        y_min = math.floor(y_min / spacing) * spacing
        y_max = math.ceil(y_max / spacing) * spacing
        x_axis, y_axis = plane_basis_from_normal(
            normal if normal is not None else {
                "XY": (0.0, 0.0, 1.0),
                "XZ": (0.0, 1.0, 0.0),
                "YZ": (1.0, 0.0, 0.0),
            }.get(plane, (0.0, 0.0, 1.0))
        )
        o = np.asarray(origin, dtype=float)

        def world_point(u, v):
            point = o + u * np.asarray(x_axis) + v * np.asarray(y_axis)
            return tuple(point)

        first_x = math.ceil(x_min / spacing)
        last_x = math.floor(x_max / spacing)
        first_y = math.ceil(y_min / spacing)
        last_y = math.floor(y_max / spacing)
        for index in range(first_y, last_y + 1):
            v = index * spacing
            add_line(world_point(x_min, v), world_point(x_max, v))
        for index in range(first_x, last_x + 1):
            u = index * spacing
            add_line(world_point(u, y_min), world_point(u, y_max))
    elif normal is None:
        # Standard planes
        for i in range(-n, n + 1):
            c = i * spacing
            if plane == "XY":
                add_line((-half, c,     0),  (half, c,     0))
                add_line((c,    -half,  0),  (c,    half,  0))
            elif plane == "XZ":
                add_line((-half, 0, c),      (half, 0, c))
                add_line((c,     0, -half),  (c,    0,  half))
            elif plane == "YZ":
                add_line((0, -half, c),      (0, half, c))
                add_line((0, c,    -half),   (0, c,     half))
        # Apply translation if origin is not (0,0,0)
        if origin != (0.0, 0.0, 0.0):
            for i in range(points.GetNumberOfPoints()):
                p = np.array(points.GetPoint(i)) + np.array(origin)
                points.SetPoint(i, *p)
    else:
        # Arbitrary plane
        # Build local axes
        o = np.array(origin)
        x_axis, y_axis = (np.asarray(axis) for axis in plane_basis_from_normal(normal))
        # Build grid in local 2D, then map to 3D
        for i in range(-n, n + 1):
            c = i * spacing
            # Lines parallel to x_axis
            p1 = o + (-half)*x_axis + c*y_axis
            p2 = o + (half)*x_axis + c*y_axis
            add_line(tuple(p1), tuple(p2))
            # Lines parallel to y_axis
            p3 = o + c*x_axis + (-half)*y_axis
            p4 = o + c*x_axis + (half)*y_axis
            add_line(tuple(p3), tuple(p4))

    poly = vtk.vtkPolyData()
    poly.SetPoints(points)
    poly.SetLines(lines)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(poly)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color)
    actor.GetProperty().SetOpacity(opacity)
    actor.GetProperty().SetLineWidth(1.0)
    actor.PickableOff()
    return actor


def build_axes_widget(interactor: vtk.vtkRenderWindowInteractor) -> vtk.vtkOrientationMarkerWidget:
    """Return an orientation-marker widget (XYZ triad) shown in viewport corner."""
    axes = vtk.vtkAxesActor()
    widget = vtk.vtkOrientationMarkerWidget()
    widget.SetOrientationMarker(axes)
    widget.SetInteractor(interactor)
    widget.SetViewport(0.0, 0.0, 0.18, 0.18)
    widget.EnabledOn()
    widget.InteractiveOff()
    return widget
