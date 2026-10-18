import math

import pytest

from em3d_modeler.emerge.ipc2581_padstack_geometry import (
    PadstackGeometryError,
    decode_padstack_feature,
)


def _node(tag, attributes=None, children=None):
    return {
        "tag": tag,
        "attributes": attributes or {},
        "text": None,
        "children": children or [],
    }


def _circle(diameter, *, rotation=None):
    children = []
    if rotation is not None:
        children.append(_node("Xform", {"rotation": str(rotation)}))
    return _node("EntryStandard", {"id": "ROUND"}, [
        _node("Circle", {"diameter": str(diameter)}, children)
    ])


def _feature(kind="pad", primitive=None):
    primitive = primitive or _circle(2.0)
    return {
        "kind": kind,
        "geometry": {
            "padstack_ref": "P1",
            "center": {"x": 10.0, "y": 20.0},
            "pads": [{
                "layer_ref": "Top",
                "pad_use": "REGULAR",
                "location": {"x": 1.0, "y": 2.0},
                "transform": None,
                "primitive_refs": ["ROUND"],
                "primitives": {"ROUND": primitive},
            }],
        },
    }


def test_decodes_circle_with_pad_offset_and_feature_placement():
    feature = _feature()
    feature["geometry"]["placement_transform"] = {"rotation": "90"}

    decoded = decode_padstack_feature(feature)

    assert decoded.kind == "pad"
    assert decoded.via is None
    polygon = decoded.polygons[0]
    assert polygon.layer_ref == "Top"
    assert polygon.pad_use == "REGULAR"
    assert polygon.is_copper is True
    assert len(polygon.points) == 64
    assert min(point[0] for point in polygon.points) == pytest.approx(7.0)
    assert max(point[0] for point in polygon.points) == pytest.approx(9.0)
    assert min(point[1] for point in polygon.points) == pytest.approx(20.0)
    assert max(point[1] for point in polygon.points) == pytest.approx(22.0)


def test_decodes_rectcenter_with_primitive_mirror_rotation_and_layer_ref():
    primitive = _node("EntryStandard", children=[
        _node("RectCenter", {"width": "4", "height": "2"}, [
            _node("Xform", {"rotation": "90", "mirror": "true"})
        ])
    ])
    feature = _feature(primitive=primitive)
    feature["geometry"]["pads"][0]["location"] = {"x": 0, "y": 0}
    feature["geometry"]["center"] = {"x": 0, "y": 0}

    decoded = decode_padstack_feature(feature)

    assert decoded.polygons[0].layer_ref == "Top"
    assert decoded.polygons[0].points == (
        (pytest.approx(1.0), pytest.approx(2.0)),
        (pytest.approx(1.0), pytest.approx(-2.0)),
        (pytest.approx(-1.0), pytest.approx(-2.0)),
        (pytest.approx(-1.0), pytest.approx(2.0)),
    )


@pytest.mark.parametrize(
    ("width", "height", "expected_bounds"),
    [
        (4.0, 2.0, (-2.0, 2.0, -1.0, 1.0)),
        (2.0, 4.0, (-1.0, 1.0, -2.0, 2.0)),
        (2.0, 2.0, (-1.0, 1.0, -1.0, 1.0)),
    ],
)
def test_decodes_oval_standard_primitive(width, height, expected_bounds):
    primitive = _node("EntryStandard", children=[
        _node("Oval", {"width": str(width), "height": str(height)})
    ])
    feature = _feature(primitive=primitive)
    feature["geometry"]["pads"][0]["location"] = {"x": 0.0, "y": 0.0}
    feature["geometry"]["center"] = {"x": 0.0, "y": 0.0}

    points = decode_padstack_feature(feature).polygons[0].points

    assert len(points) == (64 if width == height else 74)
    assert (
        min(point[0] for point in points),
        max(point[0] for point in points),
        min(point[1] for point in points),
        max(point[1] for point in points),
    ) == pytest.approx(expected_bounds)


def test_resolves_primitive_from_top_level_parser_padstack_definitions():
    feature = _feature()
    feature["geometry"]["pads"][0]["primitives"] = {}
    definitions = {
        "P1": {
            "standard_primitives": {"ROUND": _circle(1.0)},
        }
    }

    decoded = decode_padstack_feature(feature, definitions)

    assert len(decoded.polygons[0].points) == 64
    assert max(point[0] for point in decoded.polygons[0].points) == pytest.approx(11.5)


def test_decodes_contour_with_straight_segment_and_explicit_arc():
    contour = _node("EntryStandard", children=[
        _node("Contour", children=[
            _node("Polygon", children=[
                _node("PolyBegin", {"x": "1", "y": "0"}),
                _node("PolyStepCurve", {
                    "x": "0", "y": "1", "centerX": "0", "centerY": "0",
                    "clockwise": "false",
                }),
                _node("PolyStepSegment", {"x": "1", "y": "0"}),
            ])
        ])
    ])
    feature = _feature(primitive=contour)
    feature["geometry"]["pads"][0]["location"] = {"x": 0, "y": 0}
    feature["geometry"]["center"] = {"x": 0, "y": 0}

    decoded = decode_padstack_feature(feature)

    points = decoded.polygons[0].points
    assert len(points) > 10
    assert points[0] == pytest.approx((1.0, 0.0))
    assert points[1] == pytest.approx(
        (math.cos(math.radians(5)), math.sin(math.radians(5)))
    )
    # The duplicate closing PolyStepSegment endpoint is normalized away.
    assert points[-1] == pytest.approx((0.0, 1.0))


def test_via_contains_explicit_center_drill_span_and_plating():
    feature = _feature("via")
    feature["geometry"].update({
        "hole_diameter": 0.3,
        "span": {"fromlayer": "Top", "tolayer": "Bottom"},
        "plating_status": "PLATED",
    })

    via = decode_padstack_feature(feature).via

    assert via is not None
    assert via.center == (10.0, 20.0)
    assert via.drill_diameter == pytest.approx(0.3)
    assert (via.span_start, via.span_end) == ("Top", "Bottom")
    assert via.plating_status == "PLATED"


@pytest.mark.parametrize("pads_value", [None, []])
def test_via_without_land_pads_returns_explicit_via_descriptor(pads_value):
    feature = _feature("via")
    feature["geometry"].update({
        "pads": pads_value,
        "hole_diameter": 0.4,
        "span": {"fromlayer": "Top", "tolayer": "Bottom"},
        "plating_status": "PLATED",
    })

    decoded = decode_padstack_feature(feature)

    assert decoded.polygons == ()
    assert decoded.via is not None
    assert decoded.via.center == (10.0, 20.0)
    assert decoded.via.drill_diameter == pytest.approx(0.4)
    assert (decoded.via.span_start, decoded.via.span_end) == ("Top", "Bottom")
    assert decoded.via.plating_status == "PLATED"


def test_padless_via_requires_explicit_plating_status():
    feature = _feature("via")
    feature["geometry"].update({
        "pads": [],
        "hole_diameter": 0.4,
        "span": {"fromlayer": "Top", "tolayer": "Bottom"},
    })

    with pytest.raises(PadstackGeometryError, match="explicit plating_status"):
        decode_padstack_feature(feature)


@pytest.mark.parametrize(
    "pad_use, expected_copper",
    [("REGULAR", True), ("THERMAL", True), ("ANTIPAD", False)],
)
def test_preserves_and_classifies_pad_use(pad_use, expected_copper):
    feature = _feature()
    feature["geometry"]["pads"][0]["pad_use"] = pad_use

    polygon = decode_padstack_feature(feature).polygons[0]

    assert polygon.pad_use == pad_use
    assert polygon.is_copper is expected_copper


@pytest.mark.parametrize("pad_use", [None, "", "UNKNOWN"])
def test_rejects_missing_or_unsupported_pad_use(pad_use):
    feature = _feature()
    feature["geometry"]["pads"][0]["pad_use"] = pad_use

    with pytest.raises(PadstackGeometryError, match="pad_use"):
        decode_padstack_feature(feature)


@pytest.mark.parametrize("geometry, message", [
    ({}, "missing pad geometry"),
    ({"center": {"x": 0, "y": 0}}, "missing pad geometry"),
])
def test_rejects_missing_geometry(geometry, message):
    feature = {"kind": "pad", "geometry": geometry}
    with pytest.raises(PadstackGeometryError, match=message):
        decode_padstack_feature(feature)


def test_pad_without_land_polygons_is_rejected():
    feature = {
        "kind": "pad",
        "geometry": {"center": {"x": 1, "y": 2}, "pads": []},
    }

    with pytest.raises(PadstackGeometryError, match="missing pad geometry"):
        decode_padstack_feature(feature)


def test_rejects_unknown_primitive_and_unknown_transform():
    feature = _feature(primitive=_node("EntryStandard", children=[
        _node("UnsupportedShape", {"width": "1"})
    ]))
    with pytest.raises(PadstackGeometryError, match="unsupported standard primitive"):
        decode_padstack_feature(feature)

    feature = _feature()
    feature["geometry"]["placement_transform"] = {"scale": "2"}
    with pytest.raises(PadstackGeometryError, match="unsupported transform fields"):
        decode_padstack_feature(feature)


def test_rejects_missing_via_span_or_invalid_primitive_dimensions():
    feature = _feature("via")
    feature["geometry"]["hole_diameter"] = 0.2
    with pytest.raises(PadstackGeometryError, match="span endpoints"):
        decode_padstack_feature(feature)

    feature = _feature(primitive=_circle(0))
    with pytest.raises(PadstackGeometryError, match="diameter must be positive"):
        decode_padstack_feature(feature)
