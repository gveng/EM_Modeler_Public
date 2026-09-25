import json

import pytest

from em3d_modeler.drawing.sketch_engine import SketchEngine


def _engine():
    return SketchEngine((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def test_legacy_entities_keep_tuple_shape_and_profile_order():
    engine = _engine()
    engine.entities.extend([
        ("line", (0.0, 0.0), (2.0, 0.0)),
        ("rect", (4.0, 3.0), (1.0, 1.0)),
    ])

    assert engine.build_profile() == [
        (0.0, 0.0), (2.0, 0.0),
        (4.0, 3.0), (1.0, 3.0), (1.0, 1.0), (4.0, 1.0), (4.0, 3.0),
    ]

    entity_ref = engine.entity_ref(0)
    record = engine.entities[0]
    assert record == ("line", (0.0, 0.0), (2.0, 0.0))
    assert record[0] == "line" and len(record) == 3
    assert engine.point_ref(entity_ref, 1).point_index == 1


def test_operation_profile_selects_nested_regions_with_even_odd_boundaries():
    engine = _engine()
    engine.add_circle((0.0, 0.0), 5.0)
    engine.add_circle((0.0, 0.0), 2.0)

    with pytest.raises(ValueError, match="Select a sketch region"):
        engine.operation_profile()

    assert engine.select_region_at_uv((3.0, 0.0))
    annulus = engine.operation_profile()
    assert len(annulus) == 2
    assert len(annulus[0]) == 65
    assert len(annulus[1]) == 65
    assert max(point[0] for point in annulus[0]) == pytest.approx(5.0)
    assert max(point[0] for point in annulus[1]) == pytest.approx(2.0)

    assert engine.select_region_at_uv((0.0, 0.0))
    inner_disk = engine.operation_profile()
    assert len(inner_disk) == 65
    assert max(point[0] for point in inner_disk) == pytest.approx(2.0)


def test_operation_profile_requires_selection_for_disjoint_closed_regions():
    engine = _engine()
    engine.add_rectangle((0.0, 0.0), (2.0, 2.0))
    engine.add_rectangle((4.0, 0.0), (6.0, 2.0))

    with pytest.raises(ValueError, match="Select a sketch region"):
        engine.operation_profile()
    assert engine.select_region_at_uv((5.0, 1.0))
    assert engine.operation_profile() == [
        (4.0, 0.0), (6.0, 0.0), (6.0, 2.0), (4.0, 2.0), (4.0, 0.0),
    ]


def test_bounded_faces_split_intersecting_profiles_and_support_ctrl_toggle():
    engine = _engine()
    engine.add_rectangle((0.0, 0.0), (2.0, 2.0))
    engine.add_rectangle((1.0, 0.0), (3.0, 2.0))

    assert len(engine.bounded_region_profiles()) == 3
    assert engine.select_region_at_uv((0.5, 1.0))
    assert engine.select_region_at_uv((1.5, 1.0), additive=True, toggle=True)
    selected = engine.operation_regions()
    assert len(selected) == 1
    assert len(selected[0]) == 1
    assert engine.select_region_at_uv((1.5, 1.0), additive=True, toggle=True)
    assert len(engine.selected_region_profiles()) == 1
    assert engine.select_region_at_uv((0.5, 1.0), additive=True, toggle=True)
    assert engine.selected_region_profiles() == []


def test_selecting_all_intersection_faces_merges_their_shared_boundaries():
    engine = _engine()
    engine.add_rectangle((0.0, 0.0), (2.0, 2.0))
    engine.add_rectangle((1.0, 0.0), (3.0, 2.0))
    for point in ((0.5, 1.0), (1.5, 1.0), (2.5, 1.0)):
        assert engine.select_region_at_uv(
            point, additive=bool(engine.selected_region_profiles()), toggle=True
        )

    regions = engine.operation_regions()
    assert len(regions) == 1
    assert len(regions[0]) == 1
    contour = regions[0][0]
    area = abs(sum(
        contour[index][0] * contour[index + 1][1]
        - contour[index + 1][0] * contour[index][1]
        for index in range(len(contour) - 1)
    )) * 0.5
    assert area == pytest.approx(6.0)


def test_self_intersecting_closed_profile_produces_selectable_faces():
    engine = _engine()
    engine.add_polyline([
        (0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0), (0.0, 0.0),
    ])

    assert len(engine.bounded_region_profiles()) == 2
    assert engine.select_region_at_uv((0.5, 0.25))
    assert len(engine.operation_regions()) == 1


def test_selected_region_anchors_round_trip_with_sketch_definition():
    engine = _engine()
    engine.add_rectangle((0.0, 0.0), (2.0, 2.0))
    engine.add_rectangle((4.0, 0.0), (6.0, 2.0))
    engine.select_region_at_uv((1.0, 1.0))
    engine.select_region_at_uv((5.0, 1.0), additive=True, toggle=True)

    restored = SketchEngine.from_dict(engine.to_dict())

    assert restored.operation_regions() == engine.operation_regions()


def test_construction_lines_are_pickable_but_excluded_from_profiles():
    engine = _engine()
    line_ref = engine.add_line((-2.0, 0.0), (2.0, 0.0), construction=True)
    engine.add_rectangle((0.0, 0.0), (1.0, 1.0))

    assert engine.find_construction_line_at_uv((0.0, 0.05), 0.1) == (
        (-2.0, 0.0), (2.0, 0.0)
    )
    assert engine.find_line_at_uv((0.0, 0.05), 0.1) == (
        (-2.0, 0.0), (2.0, 0.0)
    )
    assert engine.build_profile() == [
        (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0),
    ]
    assert engine.entities[0].entity_id == line_ref.entity_id


def test_construction_state_applies_to_lines_and_arcs_drawn_by_clicks():
    engine = _engine()
    engine.set_construction_mode(True)
    engine.set_tool("line")
    engine.on_click((0.0, 0.0))
    engine.on_click((1.0, 0.0))
    engine.set_tool("arc")
    engine.on_click((1.0, 0.0))
    engine.on_click((0.0, 1.0))
    engine.on_click((-1.0, 0.0))

    assert all(entity.construction for entity in engine.entities)
    assert engine.build_profile() == []

    engine.set_construction_mode(False)
    engine.set_tool("arc")
    engine.on_click((2.0, 0.0))
    engine.on_click((0.0, 2.0))
    engine.on_click((-2.0, 0.0))
    assert not engine.entities[-1].construction
    assert len(engine.build_profile()) == 33


def test_rectangle_center_corner_mode_and_default_corner_corner_mode():
    engine = _engine()
    engine.set_rectangle_mode("center-corner")
    engine.set_tool("rect")
    engine.on_click((2.0, 3.0))
    engine.on_click((4.0, 5.0))
    assert engine.entities[-1].rectangle_mode == "center-corner"
    assert engine.build_profile() == [
        (0.0, 1.0), (4.0, 1.0), (4.0, 5.0), (0.0, 5.0), (0.0, 1.0),
    ]

    engine.set_rectangle_mode("corner-corner")
    engine.add_rectangle((3.0, 4.0), (5.0, 6.0))
    assert engine.entities[-1].rectangle_mode == "corner-corner"
    assert engine.build_profile()[-5:] == [
        (3.0, 4.0), (5.0, 4.0), (5.0, 6.0), (3.0, 6.0), (3.0, 4.0),
    ]

    with pytest.raises(ValueError, match="rectangle mode"):
        engine.set_rectangle_mode("three-point")


def test_dimension_refs_survive_point_edits_and_json_round_trip():
    engine = _engine()
    line_ref = engine.add_line((0.0, 0.0), (3.0, 4.0))
    dimension = engine.add_dimension(
        "aligned",
        (engine.point_ref(line_ref, 0), engine.point_ref(line_ref, 1)),
        expression="project.diagonal",
    )

    resolved = engine.recompute_dimensions(lambda expression: 10.0)
    assert resolved == {dimension.dimension_id: 10.0}
    assert engine.apply_dimension(dimension)
    assert engine.entities[0][2] == pytest.approx((6.0, 8.0))

    serialized = json.loads(json.dumps(engine.to_dict()))
    restored = SketchEngine.from_dict(serialized)
    restored_dimension = restored.dimensions[0]
    assert restored_dimension.expression == "project.diagonal"
    assert restored_dimension.resolved_value == pytest.approx(10.0)
    assert restored_dimension.refs == dimension.refs
    assert restored.measure_dimension(restored_dimension) == pytest.approx(10.0)

    restored.edit_point(restored_dimension.refs[0], (-1.0, 0.0))
    assert restored.dimensions[0].dimension_id == dimension.dimension_id
    assert restored.measure_dimension(restored_dimension) != pytest.approx(10.0)


def test_linear_dimension_moves_line_or_polyline_endpoint_deterministically():
    engine = _engine()
    line_ref = engine.add_line((0.0, 0.0), (3.0, 4.0))
    horizontal = engine.add_dimension(
        "linear",
        (engine.point_ref(line_ref, 0), engine.point_ref(line_ref, 1)),
        value=8.0,
        axis="u",
    )
    assert engine.apply_dimension(horizontal)
    assert engine.entities[0][2] == pytest.approx((8.0, 4.0))

    polyline_ref = engine.add_polyline([(0.0, 0.0), (2.0, 1.0), (2.0, 3.0)])
    vertical = engine.add_dimension(
        "linear",
        (engine.point_ref(polyline_ref, 0), engine.point_ref(polyline_ref, 2)),
        value=6.0,
        axis="v",
    )
    assert engine.apply_dimension(vertical)
    assert engine.entities[1][1][-1] == pytest.approx((2.0, 6.0))


def test_angular_radius_and_diameter_dimensions_measure_and_apply_supported_changes():
    engine = _engine()
    horizontal = engine.add_line((0.0, 0.0), (1.0, 0.0))
    vertical = engine.add_line((0.0, 0.0), (0.0, 1.0))
    angle = engine.add_dimension("angular", (horizontal, vertical))
    assert engine.measure_dimension(angle) == pytest.approx(90.0)
    assert not engine.apply_dimension(angle)

    circle = engine.add_circle((2.0, 3.0), 2.0)
    radius = engine.add_dimension("radius", (circle,), value=4.0)
    assert engine.apply_dimension(radius)
    assert engine.measure_dimension(radius) == pytest.approx(4.0)

    diameter = engine.add_dimension("diameter", (circle,), value=10.0)
    assert engine.apply_dimension(diameter)
    assert engine.measure_dimension(diameter) == pytest.approx(10.0)

    arc = engine.add_arc((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0))
    arc_radius = engine.add_dimension("radius", (arc,), value=2.5)
    assert engine.apply_dimension(arc_radius)
    assert engine.measure_dimension(arc_radius) == pytest.approx(2.5)


@pytest.mark.parametrize(("tool", "points", "expected_tag"), [
    ("line", ((0, 0), (2, 0)), "line"),
    ("rect", ((0, 0), (2, 1)), "rect"),
    ("circle", ((0, 0), (1, 0)), "circle"),
    ("arc", ((1, 0), (0, 1), (-1, 0)), "arc"),
])
def test_committing_single_shape_returns_to_select_mode(tool, points, expected_tag):
    engine = _engine()
    engine.set_tool(tool)

    for point in points:
        engine.on_click(point)

    assert engine.entities[-1][0] == expected_tag
    assert engine.tool is None
    assert engine.pending == []


def test_polyline_finish_preserves_completed_geometry_and_selection_id():
    engine = _engine()
    engine.set_tool("polyline")
    engine.on_click((0, 0))
    engine.on_click((2, 0))
    engine.on_click((2, 2))
    entity_id = engine.entity_ref(0).entity_id

    assert engine.tool == "polyline"
    engine.finish_current()

    assert engine.tool is None
    assert engine.entities[0] == ("polyline", [(0, 0), (2, 0), (2, 2)])
    assert engine.entity_ref(0).entity_id == entity_id
    assert engine.select_entity_at_uv((1, 0.01), 0.1).entity_id == entity_id


def test_selected_entity_hit_testing_clears_on_empty_and_delete_prunes_dimensions():
    engine = _engine()
    line_ref = engine.add_line((0, 0), (4, 0))
    line_dimension = engine.add_dimension(
        "aligned", (engine.point_ref(line_ref, 0), engine.point_ref(line_ref, 1))
    )
    circle_ref = engine.add_circle((10, 0), 2, construction=True)
    circle_dimension = engine.add_dimension("radius", (circle_ref,))

    selected = engine.select_entity_at_uv((2, 0.02), 0.1)
    assert selected.entity_id == line_ref.entity_id
    _, highlighted = engine.to_lines_polydata()
    assert highlighted.GetNumberOfCells() == 1

    assert engine.select_entity_at_uv((20, 20), 0.1) is None
    assert engine.selected_entity_id is None
    engine.select_entity_at_uv((2, 0.02), 0.1)
    assert engine.delete_selected()

    assert line_ref.entity_id not in [entity.entity_id for entity in engine.entities]
    assert [dimension.dimension_id for dimension in engine.dimensions] == [
        circle_dimension.dimension_id,
    ]
    assert line_dimension not in engine.dimensions
    assert engine.entities[0].entity_id == circle_ref.entity_id
    assert engine.entities[0].construction is True