import json
import math

import pytest
import vtk

from em3d_modeler.scene.em_objects import SweptSurfaceObject
from em3d_modeler.scene.scene_manager import SceneManager


def _boundary_edge_count(polydata):
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(polydata)
    edges.BoundaryEdgesOn()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.NonManifoldEdgesOff()
    edges.Update()
    return edges.GetOutput().GetNumberOfCells()


def _assert_quads_only(polydata, expected_count):
    assert polydata.GetNumberOfVerts() == 0
    assert polydata.GetNumberOfLines() == 0
    assert polydata.GetNumberOfStrips() == 0
    assert polydata.GetNumberOfPolys() == expected_count
    assert polydata.GetNumberOfCells() == expected_count
    assert all(polydata.GetCell(index).GetNumberOfPoints() == 4
               for index in range(expected_count))


def test_closed_profile_creates_only_lateral_sheet_without_end_caps():
    profile = [
        (0, 0, 0),
        (4, 0, 0),
        (4, 2, 0),
        (0, 2, 0),
        (0, 0, 0),  # Explicit closure is normalized and preserved as Closed=True.
    ]
    sheet = SweptSurfaceObject(
        profile_pts=profile, displacement=(0, 0, 3)
    )
    mesh = sheet.surface

    _assert_quads_only(mesh, expected_count=4)
    assert mesh.GetNumberOfPoints() == 8
    # The two uncapped profile rims are the only boundary edges. A capped,
    # closed solid would have no boundary edges.
    assert _boundary_edge_count(mesh) == 8
    assert sheet.anchor == pytest.approx((2, 1, 0))
    assert sheet.endpoint == pytest.approx((2, 1, 3))
    assert sheet.profile_perimeter == pytest.approx(12)
    assert sheet.extrusion_length == pytest.approx(3)
    assert sheet.direction == pytest.approx((0, 0, 1))


def test_open_profile_creates_ruled_sheet_with_expected_swept_vertices():
    profile = [(1, 2, 3), (4, 2, 3), (4, 5, 3)]
    displacement = (1, -2, 6)
    sheet = SweptSurfaceObject(
        profile_pts=profile, displacement=displacement, closed=False
    )
    mesh = sheet.surface

    _assert_quads_only(mesh, expected_count=2)
    assert mesh.GetNumberOfPoints() == 2 * len(profile)
    expected_points = profile + [
        tuple(point[axis] + displacement[axis] for axis in range(3))
        for point in profile
    ]
    assert [mesh.GetPoint(index) for index in range(mesh.GetNumberOfPoints())] == (
        expected_points
    )
    # Two profile rims and the two open ends form the boundary of this strip.
    assert _boundary_edge_count(mesh) == 6
    assert sheet.anchor == profile[0]
    assert sheet.endpoint == pytest.approx((2, 0, 9))
    assert sheet.profile_perimeter == pytest.approx(6)
    assert sheet.extrusion_length == pytest.approx(math.sqrt(41))
    assert sheet.direction == pytest.approx(
        tuple(value / math.sqrt(41) for value in displacement)
    )


def test_swept_surface_parameters_are_json_reconstructible():
    original = SweptSurfaceObject(
        profile_pts=[(0, 0, 0), (3, 0, 0), (0, 2, 0)],
        displacement=(2, 3, 4),
        closed=True,
    )
    parameters = json.loads(json.dumps(original.get_parameters()))
    restored = SweptSurfaceObject()
    restored.set_parameters(parameters)

    assert restored.get_parameters() == parameters
    assert restored.surface.GetNumberOfPolys() == original.surface.GetNumberOfPolys()
    assert restored.surface.GetNumberOfPoints() == original.surface.GetNumberOfPoints()
    for index in range(original.surface.GetNumberOfPoints()):
        assert restored.surface.GetPoint(index) == pytest.approx(
            original.surface.GetPoint(index)
        )


def test_exact_circle_profile_is_json_reconstructible():
    profile_curve = {
        "kind": "circle",
        "center": [3.0, -4.0, 7.0],
        "radius": 5.0,
        "normal": [0.0, 0.0, 2.0],
        "x_axis": [2.0, 0.0, 0.0],
    }
    profile = SweptSurfaceObject.sample_profile_curve(profile_curve, 32)
    original = SweptSurfaceObject(
        "ExactCircle",
        profile_pts=profile,
        displacement=(0.0, 0.0, 3.0),
        closed=True,
        profile_curve=profile_curve,
    )
    parameters = json.loads(json.dumps(original.get_parameters()))
    restored = SweptSurfaceObject()
    restored.set_parameters(parameters)

    assert restored.get_parameters() == parameters
    assert restored.anchor == pytest.approx((3.0, -4.0, 7.0))
    assert restored.surface.GetNumberOfPolys() == 32
    assert restored.profile_perimeter == pytest.approx(original.profile_perimeter)
    assert restored.profile_perimeter == pytest.approx(2.0 * math.pi * 5.0)


def test_exact_circle_profile_rejects_polyline_not_on_circle():
    with pytest.raises(ValueError, match="must lie on the exact ProfileCurve"):
        SweptSurfaceObject(
            profile_pts=[(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)],
            displacement=(0, 0, 1),
            closed=True,
            profile_curve={
                "kind": "circle",
                "center": [0, 0, 0],
                "radius": 2,
                "normal": [0, 0, 1],
                "x_axis": [1, 0, 0],
            },
        )


def test_swept_surface_survives_scene_json_roundtrip():
    scene = SceneManager(vtk.vtkRenderer())
    original = SweptSurfaceObject(
        "OpenSheet",
        profile_pts=[(0, 0, 0), (2, 0, 0), (1, 1, 0)],
        displacement=(0, 0, 4),
        closed=True,
    )
    scene.add_object(original)

    restored_scene = SceneManager(vtk.vtkRenderer())
    restored_scene.from_json(json.loads(json.dumps(scene.to_json())))

    assert len(restored_scene.objects) == 1
    restored = restored_scene.objects[0]
    assert isinstance(restored, SweptSurfaceObject)
    assert restored.get_parameters() == original.get_parameters()


def test_exact_circle_surface_survives_scene_json_roundtrip():
    profile_curve = {
        "kind": "circle",
        "center": [0.0, 0.0, 0.0],
        "radius": 2.0,
        "normal": [0.0, 0.0, 1.0],
        "x_axis": [1.0, 0.0, 0.0],
    }
    original = SweptSurfaceObject(
        "ExactSheet",
        profile_pts=SweptSurfaceObject.sample_profile_curve(profile_curve, 36),
        displacement=(0.0, 0.0, 5.0),
        closed=True,
        profile_curve=profile_curve,
    )
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(original)

    restored_scene = SceneManager(vtk.vtkRenderer())
    restored_scene.from_json(json.loads(json.dumps(scene.to_json())))
    restored = restored_scene.objects[0]

    assert isinstance(restored, SweptSurfaceObject)
    assert restored.get_parameters() == original.get_parameters()
    assert restored.profile_perimeter == pytest.approx(4.0 * math.pi)


def test_legacy_swept_surface_displacement_parameters_still_load():
    legacy_sheet = SweptSurfaceObject(
        "LegacySheet",
        profile_pts=[(0, 0, 0), (3, 0, 0), (0, 2, 0)],
        displacement=(0, 0, 4),
        closed=True,
    )
    data = SceneManager(vtk.vtkRenderer())
    data.add_object(legacy_sheet)
    project_data = data.to_json()
    legacy_params = project_data[0]["params"]
    legacy_params.pop("ProfilePerimeter")
    legacy_params.pop("ExtrusionLength")
    legacy_params.pop("Direction")
    legacy_params["Displacement"] = [0, 0, -4]

    restored_scene = SceneManager(vtk.vtkRenderer())
    restored_scene.from_json(json.loads(json.dumps(project_data)))
    restored = restored_scene.objects[0]

    assert restored.extrusion_length == pytest.approx(4)
    assert restored.direction == pytest.approx((0, 0, -1))
    assert restored.profile_perimeter == pytest.approx(3 + math.sqrt(13) + 2)
    assert restored.surface.GetPoint(3) == pytest.approx((0, 0, -4))


@pytest.mark.parametrize(
    ("profile", "closed", "message"),
    [
        ([], False, "at least two"),
        ([(0, 0, 0)], False, "at least two"),
        ([(0, 0, 0), (1, 0, 0), (2, 0, 0)], True, "non-zero area"),
        ([(0, 0, 0), (1, 0, 0), (1, 0, 0)], False, "zero-length"),
    ],
)
def test_invalid_profiles_fail_explicitly(profile, closed, message):
    with pytest.raises(ValueError, match=message):
        SweptSurfaceObject(profile_pts=profile, displacement=(0, 0, 1), closed=closed)


def test_zero_displacement_fails_explicitly():
    with pytest.raises(ValueError, match="Displacement must be non-zero"):
        SweptSurfaceObject(
            profile_pts=[(0, 0, 0), (1, 0, 0)],
            displacement=(0, 0, 0),
            closed=False,
        )
