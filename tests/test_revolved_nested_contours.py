import ast
import math

import pytest
import vtk

from em3d_modeler.scene.em_objects import RevolvedObject


def _mesh_volume(polydata):
    triangles = vtk.vtkTriangleFilter()
    triangles.SetInputData(polydata)
    triangles.Update()
    properties = vtk.vtkMassProperties()
    properties.SetInputConnection(triangles.GetOutputPort())
    properties.Update()
    return properties.GetVolume()


def _is_inside(polydata, point):
    probe = vtk.vtkPolyData()
    points = vtk.vtkPoints()
    points.InsertNextPoint(*point)
    probe.SetPoints(points)
    selector = vtk.vtkSelectEnclosedPoints()
    selector.SetInputData(probe)
    selector.SetSurfaceData(polydata)
    selector.Update()
    return bool(selector.IsInside(0))


def _rectangular_annulus():
    outer = [(2, 0), (5, 0), (5, 10), (2, 10)]
    hole = [(3, 2), (3, 8), (4, 8), (4, 2)]
    return outer, hole


def test_rectangular_annulus_revolves_to_solid_with_expected_volume_and_void():
    outer, hole = _rectangular_annulus()
    revolution = RevolvedObject("Annular revolve", profile_pts=[outer, hole])

    mesh = revolution._revolve_normals.GetOutput()
    expected_volume = 168.0 * math.pi
    assert mesh.GetNumberOfCells() > 0
    assert _mesh_volume(mesh) == pytest.approx(expected_volume, rel=1e-3)
    angle = math.pi / 4.0
    assert _is_inside(mesh, (2.5 * math.cos(angle), 2.5 * math.sin(angle), 5))
    assert not _is_inside(mesh, (3.5 * math.cos(angle), 3.5 * math.sin(angle), 5))

    restored = RevolvedObject("Restored")
    restored.set_parameters(revolution.get_parameters())
    serialized = ast.literal_eval(restored.get_parameters()["ProfilePts"])
    assert len(serialized) == 2
    assert _mesh_volume(restored._revolve_normals.GetOutput()) == pytest.approx(
        expected_volume, rel=1e-3
    )


def test_partial_revolve_caps_the_profile_face():
    outer, hole = _rectangular_annulus()
    revolution = RevolvedObject(profile_pts=[outer, hole], angle=180)

    assert _mesh_volume(revolution._revolve_normals.GetOutput()) == pytest.approx(
        84.0 * math.pi, rel=1e-3
    )


def test_single_contour_serialization_stays_flat():
    profile = [(2, 0), (5, 0), (5, 10), (2, 10)]
    revolution = RevolvedObject(profile_pts=profile)

    assert ast.literal_eval(revolution.get_parameters()["ProfilePts"]) == profile


def test_invalid_nested_profile_update_is_atomic():
    outer, _hole = _rectangular_annulus()
    outside_hole = [(6, 3), (6, 5), (7, 5), (7, 3)]
    revolution = RevolvedObject(profile_pts=[outer])
    starting_parameters = revolution.get_parameters()
    starting_bounds = revolution._revolve_normals.GetOutput().GetBounds()
    starting_volume = _mesh_volume(revolution._revolve_normals.GetOutput())

    with pytest.raises(ValueError, match="enclosed holes"):
        revolution.set_parameters({
            "Angle": 180,
            "ProfilePts": [outer, outside_hole],
        })

    assert revolution.get_parameters() == starting_parameters
    assert revolution._revolve_normals.GetOutput().GetBounds() == starting_bounds
    assert _mesh_volume(revolution._revolve_normals.GetOutput()) == pytest.approx(starting_volume)