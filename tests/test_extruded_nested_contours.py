import ast
import math

import pytest
import vtk

from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene.em_objects import ExtrudedObject
from em3d_modeler.scene.boolean_ops import (
    _extend_extruded_tool_at_base_ends,
    _world_polydata,
    boolean_many,
)


def _triangle_area(polydata):
    area = 0.0
    for cell_index in range(polydata.GetNumberOfCells()):
        cell = polydata.GetCell(cell_index)
        p0, p1, p2 = (polydata.GetPoint(cell.GetPointId(index)) for index in range(3))
        a = tuple(p1[index] - p0[index] for index in range(3))
        b = tuple(p2[index] - p0[index] for index in range(3))
        cross = (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )
        area += math.sqrt(sum(component * component for component in cross)) * 0.5
    return area


def _mesh_volume(polydata):
    triangles = vtk.vtkTriangleFilter()
    triangles.SetInputData(polydata)
    triangles.Update()
    properties = vtk.vtkMassProperties()
    properties.SetInputConnection(triangles.GetOutputPort())
    properties.Update()
    return properties.GetVolume()


def test_nested_circle_contours_extrude_as_annulus_and_round_trip():
    segment_count = 128
    outer = [
        (2.0 * math.cos(2.0 * math.pi * index / segment_count),
         2.0 * math.sin(2.0 * math.pi * index / segment_count))
        for index in range(segment_count)
    ]
    inner = [
        (math.cos(2.0 * math.pi * index / segment_count),
         math.sin(2.0 * math.pi * index / segment_count))
        for index in range(segment_count)
    ]
    depth = 5.0
    expected_cap_area = math.pi * (2.0**2 - 1.0**2)

    extrusion = ExtrudedObject(
        "Annulus",
        profile_pts=[outer + [outer[0]], inner],
        depth=depth,
    )
    mesh = extrusion._extrude.GetOutput()
    assert mesh.GetNumberOfCells() > 0
    assert _triangle_area(extrusion._profile_to_polydata()) == pytest.approx(
        expected_cap_area, rel=5e-4
    )
    assert _mesh_volume(mesh) == pytest.approx(expected_cap_area * depth, rel=5e-4)

    serialized = ast.literal_eval(extrusion.get_parameters()["ProfilePts"])
    assert len(serialized) == 2
    assert len(serialized[0]) == len(serialized[1]) == segment_count
    restored = ExtrudedObject("Restored")
    restored.set_parameters(extrusion.get_parameters())
    assert len(ast.literal_eval(restored.get_parameters()["ProfilePts"])) == 2
    assert _mesh_volume(restored._extrude.GetOutput()) == pytest.approx(
        expected_cap_area * depth, rel=5e-4
    )


def test_single_contour_profile_serialization_stays_flat():
    profile = [(0, 0), (4, 0), (4, 3), (0, 3)]
    extrusion = ExtrudedObject(profile_pts=profile)
    extrusion.set_parameters({"ProfilePts": str(profile)})

    assert ast.literal_eval(extrusion.get_parameters()["ProfilePts"]) == profile


def test_disjoint_selected_regions_extrude_and_round_trip():
    first = [(0, 0), (2, 0), (2, 2), (0, 2)]
    second = [(4, 0), (5, 0), (5, 2), (4, 2)]
    depth = 3.0
    extrusion = ExtrudedObject(
        "Disjoint",
        profile_pts=[[first], [second]],
        depth=depth,
    )

    assert _triangle_area(extrusion._profile_to_polydata()) == pytest.approx(6.0)
    assert _mesh_volume(extrusion._extrude.GetOutput()) == pytest.approx(18.0)
    serialized = ast.literal_eval(extrusion.get_parameters()["ProfilePts"])
    assert len(serialized) == 2

    restored = ExtrudedObject("Restored")
    restored.set_parameters(extrusion.get_parameters())
    assert _mesh_volume(restored._extrude.GetOutput()) == pytest.approx(18.0)


def test_concentric_sketch_inner_disk_extrudes_after_region_selection():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    sketch.add_circle((0, 0), 5)
    sketch.add_circle((0, 0), 2)
    assert sketch.select_region_at_uv((0.5, 0.5))

    extrusion = ExtrudedObject(
        "InnerDisk",
        profile_pts=sketch.operation_profile(),
        depth=4,
    )

    assert extrusion._extrude.GetOutput().GetNumberOfCells() > 0
    assert _mesh_volume(extrusion._extrude.GetOutput()) == pytest.approx(
        4 * 2 * 2 * math.sin(math.pi / 32) * 64 / 2,
        rel=1e-6,
    )


@pytest.mark.parametrize(
    ("direction", "symmetric", "expected_z"),
    [
        ("Normal", False, (0.0, 4.0)),
        ("Reverse", False, (-4.0, 0.0)),
        ("Normal", True, (-2.0, 2.0)),
        ("Reverse", True, (-2.0, 2.0)),
    ],
)
def test_extrusion_direction_and_symmetric_bounds_round_trip(direction, symmetric, expected_z):
    profile = [(0, 0), (2, 0), (2, 2), (0, 2)]
    extrusion = ExtrudedObject(
        profile_pts=profile,
        depth=4,
        direction=direction,
        symmetric=symmetric,
    )

    assert extrusion._extrude.GetOutput().GetBounds()[4:] == pytest.approx(expected_z)
    params = extrusion.get_parameters()
    restored = ExtrudedObject()
    restored.set_parameters(params)
    assert restored.get_parameters()["Direction"] == direction
    assert restored.get_parameters()["Symmetric"] is symmetric
    assert restored._extrude.GetOutput().GetBounds()[4:] == pytest.approx(expected_z)


def test_coax_cut_preserves_dielectric_in_gap_between_conductors():
    segment_count = 64

    def circle(radius):
        return [
            (radius * math.cos(2.0 * math.pi * index / segment_count),
             radius * math.sin(2.0 * math.pi * index / segment_count))
            for index in range(segment_count)
        ]

    depth = 200.0
    dielectric = ExtrudedObject(
        "Dielectric", profile_pts=circle(2.1540659), depth=depth
    )
    coax = ExtrudedObject(
        "Coax",
        profile_pts=[[circle(2.3600847), circle(2.1400935)], [circle(0.4242641)]],
        depth=depth,
    )

    result = boolean_many("cut", [dielectric, coax])

    assert result.GetNumberOfCells() > 0
    assert _mesh_volume(result) > 2600.0
    assert result.GetBounds()[0] >= -2.2
    assert result.GetBounds()[1] <= 2.2
    assert result.GetBounds()[4:] == pytest.approx((0.0, depth))

    distance = vtk.vtkImplicitPolyDataDistance()
    distance.SetInput(result)
    assert distance.EvaluateFunction((1.0, 0.0, depth * 0.5)) < 0.0
    assert distance.EvaluateFunction((0.2, 0.0, depth * 0.5)) > 0.0
    assert distance.EvaluateFunction((0.2, 0.0, 0.01)) > 0.0
    assert distance.EvaluateFunction((0.2, 0.0, depth - 0.01)) > 0.0
    assert _mesh_volume(result) > 2700.0


def test_finite_depth_cut_only_extends_the_coincident_base_end():
    profile = [(0, 0), (2, 0), (2, 2), (0, 2)]
    base = ExtrudedObject("Base", profile_pts=profile, depth=10.0)
    tool = ExtrudedObject("BlindTool", profile_pts=profile, depth=5.0)

    extended = _extend_extruded_tool_at_base_ends(
        tool, _world_polydata(base), _world_polydata(tool)
    )

    assert extended is not None
    assert extended._extrude.GetOutput().GetBounds()[4:] == pytest.approx(
        (-0.025, 5.0)
    )
    assert tool._depth == 5.0


def test_invalid_nested_contour_update_is_atomic():
    extrusion = ExtrudedObject("Atomic")
    starting_parameters = extrusion.get_parameters()
    starting_bounds = extrusion._extrude.GetOutput().GetBounds()

    with pytest.raises(ValueError, match="area"):
        extrusion.set_parameters({
            "Depth": 20.0,
            "ProfilePts": [
                [(0, 0), (4, 0), (4, 4), (0, 4)],
                [(1, 1), (2, 2), (3, 3)],
            ],
        })

    assert extrusion.get_parameters() == starting_parameters
    assert extrusion._extrude.GetOutput().GetBounds() == starting_bounds