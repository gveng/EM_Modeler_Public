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
) -> vtk.vtkActor:
    """Return a VTK actor that draws a uniform grid on *plane* centred at origin."""
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
