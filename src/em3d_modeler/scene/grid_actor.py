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
import vtk

PLANES = ("XY", "XZ", "YZ")


def build_grid_actor(

    size: float = 200.0,
    spacing: float = 10.0,
    plane: str = "XY",
    color: tuple = (0.45, 0.45, 0.45),
    opacity: float = 0.40,
    origin: tuple = (0.0, 0.0, 0.0),
    normal: tuple = None,
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

    if normal is None:
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
        nrm = np.array(normal)
        nrm = nrm / np.linalg.norm(nrm)
        # Find a vector not parallel to nrm
        if abs(nrm[2]) < 0.99:
            v = np.array([0,0,1])
        else:
            v = np.array([0,1,0])
        x_axis = np.cross(v, nrm)
        x_axis = x_axis / np.linalg.norm(x_axis)
        y_axis = np.cross(nrm, x_axis)
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
