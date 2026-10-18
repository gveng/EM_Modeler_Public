import os
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import vtk
from PySide6.QtWidgets import QApplication, QMainWindow

from em3d_modeler.emerge.ipc2581_board_json import (
    BoardJSONImportError,
    board_json_to_preview_groups,
    load_board_json,
    via_plating_requests,
)
from em3d_modeler.scene.em_objects import MeshObject
from em3d_modeler.scene.scene_manager import SceneManager
from em3d_modeler.ui.main_window import MainWindow


# Captured from ipc2581_pcb's CLI:
# parse mini_board.xml --layer Top --net SIG (Board.to_dict() JSON output).
FIXTURE = Path(__file__).parent / "fixtures" / "mini_board_sig_top.to_dict.json"
# Track records use the standalone parser's exact line/polyline JSON values
# from `parse mini_board.xml --layer Top --net GND`; its via is omitted here
# so these tests exercise only the supported track feature kinds.
TRACK_FIXTURE = Path(__file__).parent / "fixtures" / "mini_board_tracks.to_dict.json"


def _board():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _track_board():
    return json.loads(TRACK_FIXTURE.read_text(encoding="utf-8"))


def _board_with_arc_track(start, end, center):
    board = _track_board()
    board["units"] = "inch"
    stackup = board["stackups"][0]
    for layer in stackup["layers"]:
        layer["thickness"] /= 25.4
    stackup["overall_thickness"] /= 25.4
    feature = next(item for item in board["features"] if item["kind"] == "polyline")
    feature["geometry"]["points"] = [start, end]
    feature["geometry"]["segments"] = [
        {
            "kind": "polystepcurve",
            "to": end,
            "center": center,
            "clockwise": True,
        }
    ]
    board["features"] = [feature]
    return board


def _board_with_contour():
    board = _board()
    points = [
        {"x": 0.0, "y": 0.0},
        {"x": 4.0, "y": 0.0},
        {"x": 4.0, "y": 4.0},
        {"x": 0.0, "y": 4.0},
        {"x": 0.0, "y": 0.0},
    ]
    segments = [
        {"kind": "polystepsegment", "to": point}
        for point in points[1:]
    ]
    segments[1] = {
        "kind": "polystepcurve",
        "to": points[2],
        "center": {"x": 4.0, "y": 2.0},
        "clockwise": False,
    }
    board["features"][0]["kind"] = "contour"
    board["features"][0]["geometry"] = {
        "closed": True,
        "points": points,
        "segments": segments,
        "subpath_count": 1,
    }
    return board


def _padstack_circle_feature(kind, *, layer="Top", net="PWR", diameter=2.0):
    primitive = {
        "tag": "EntryStandard",
        "attributes": {"id": "LAND"},
        "text": None,
        "children": [{
            "tag": "Circle",
            "attributes": {"diameter": str(diameter)},
            "text": None,
            "children": [],
        }],
    }
    return {
        "kind": kind,
        "layer": layer,
        "net": net,
        "attributes": {},
        "geometry": {
            "padstack_ref": "P1",
            "center": {"x": 10.0, "y": 5.0},
            "pads": [{
                "layer_ref": layer,
                "pad_use": "REGULAR",
                "location": {"x": 0.0, "y": 0.0},
                "transform": None,
                "primitive_refs": ["LAND"],
                "primitives": {"LAND": primitive},
            }],
        },
    }


def _via_feature(*, include_lands=False):
    via = _padstack_circle_feature("via", diameter=2.0)
    via["geometry"].update({
        "hole_diameter": 0.3,
        "span": {"fromlayer": "Top", "tolayer": "Bottom"},
        "plating_status": "PLATED",
    })
    if include_lands:
        bottom = deepcopy(via["geometry"]["pads"][0])
        bottom["layer_ref"] = "Bottom"
        via["geometry"]["pads"].append(bottom)
    return via


def _board_with_outline():
    board = _board()
    points = [
        {"x": 0.0, "y": 0.0},
        {"x": 20.0, "y": 0.0},
        {"x": 20.0, "y": 10.0},
        {"x": 0.0, "y": 10.0},
        {"x": 0.0, "y": 0.0},
    ]
    board["outline"] = {
        "kind": "polygon",
        "geometry": {
            "closed": True,
            "points": points,
            "segments": [
                {"kind": "polystepsegment", "to": point}
                for point in points[1:]
            ],
        },
    }
    return board


def _contour(kind, points, *, curved_segment=None):
    segments = [
        {
            "kind": "polystepsegment",
            "to": point,
        }
        for point in points[1:]
    ]
    if curved_segment is not None:
        index, center, clockwise = curved_segment
        segments[index] = {
            "kind": "polystepcurve",
            "to": points[index + 1],
            "center": center,
            "clockwise": clockwise,
        }
    return {
        "kind": kind,
        "closed": True,
        "points": points,
        "segments": segments,
        "source_tag": "Polygon" if kind == "outline" else "Cutout",
    }


def _board_with_multipath_contour():
    board = _board_with_contour()
    outer = _contour("polygon", [
        {"x": 0.0, "y": 0.0},
        {"x": 10.0, "y": 0.0},
        {"x": 10.0, "y": 10.0},
        {"x": 0.0, "y": 10.0},
        {"x": 0.0, "y": 0.0},
    ])
    outer["role"] = "polygon"
    cutout = _contour("cutout", [
        {"x": 6.0, "y": 5.0},
        {"x": 4.0, "y": 5.0},
        {"x": 6.0, "y": 5.0},
    ])
    cutout["role"] = "cutout"
    cutout["segments"] = [
        {
            "kind": "polystepcurve",
            "to": cutout["points"][1],
            "center": {"x": 5.0, "y": 5.0},
            "clockwise": True,
        },
        {
            "kind": "polystepcurve",
            "to": cutout["points"][2],
            "center": {"x": 5.0, "y": 5.0},
            "clockwise": True,
        },
    ]
    board["features"][0]["geometry"] = {
        "closed": True,
        "points": outer["points"] + cutout["points"],
        "segments": outer["segments"] + cutout["segments"],
        "subpath_count": 2,
        "paths": [outer, cutout],
    }
    return board


def _board_with_multipath_polygon():
    board = _board()
    first = _contour("polygon", [
        {"x": 0.0, "y": 0.0},
        {"x": 4.0, "y": 0.0},
        {"x": 4.0, "y": 4.0},
        {"x": 0.0, "y": 4.0},
        {"x": 0.0, "y": 0.0},
    ])
    second = _contour("polygon", [
        {"x": 6.0, "y": 1.0},
        {"x": 9.0, "y": 1.0},
        {"x": 9.0, "y": 4.0},
        {"x": 6.0, "y": 4.0},
        {"x": 6.0, "y": 1.0},
    ])
    board["features"][0]["geometry"] = {
        "closed": True,
        "points": [],
        "segments": [],
        "subpath_count": 2,
        "paths": [first, second],
    }
    return board


def _board_with_multiple_exterior_contours():
    board = _board_with_contour()
    first = _contour("polygon", [
        {"x": 0.0, "y": 0.0},
        {"x": 10.0, "y": 0.0},
        {"x": 10.0, "y": 10.0},
        {"x": 0.0, "y": 10.0},
        {"x": 0.0, "y": 0.0},
    ])
    first["role"] = "polygon"
    second = _contour("polygon", [
        {"x": 12.0, "y": 0.0},
        {"x": 16.0, "y": 0.0},
        {"x": 16.0, "y": 4.0},
        {"x": 12.0, "y": 4.0},
        {"x": 12.0, "y": 0.0},
    ])
    second["role"] = "polygon"
    cutout = _contour("cutout", [
        {"x": 2.0, "y": 2.0},
        {"x": 4.0, "y": 2.0},
        {"x": 4.0, "y": 4.0},
        {"x": 2.0, "y": 4.0},
        {"x": 2.0, "y": 2.0},
    ])
    cutout["role"] = "cutout"
    board["features"][0]["geometry"] = {
        "closed": True,
        "points": [],
        "segments": [],
        "subpath_count": 3,
        "paths": [first, second, cutout],
    }
    return board


def _board_with_cutout_touching_exterior():
    board = _board_with_contour()
    exterior = _contour("polygon", [
        {"x": 0.0, "y": 0.0},
        {"x": 1.4445355191256828, "y": 0.0},
        {"x": 1.4445355191256828, "y": 0.9707650273224043},
        {"x": 0.0, "y": 0.9707650273224043},
        {"x": 0.0, "y": 0.0},
    ])
    exterior["role"] = "polygon"
    cutout = _contour("cutout", [
        {"x": 0.0, "y": 0.0},
        {"x": 1.4445355191256828, "y": 0.0},
        {"x": 1.4445355191256828, "y": 0.33193432503541775},
        {"x": 1.369781336436619, "y": 0.33193432503541775},
        {"x": 1.3197813364366187, "y": 0.3819343250354178},
        {"x": 1.3197813364366187, "y": 0.4444343250354178},
        {"x": 1.344781336436619, "y": 0.4694343250354178},
        {"x": 1.432281336436619, "y": 0.4694343250354178},
        {"x": 1.4445355191256828, "y": 0.45718014234635396},
        {"x": 1.4445355191256828, "y": 0.9707650273224043},
        {"x": 0.0, "y": 0.9707650273224043},
        {"x": 0.0, "y": 0.0},
    ])
    cutout["role"] = "cutout"
    board["features"][0]["geometry"] = {
        "closed": False,
        "points": [],
        "segments": [],
        "subpath_count": 2,
        "paths": [exterior, cutout],
    }
    return board


def _board_with_parser_profile():
    board = _board_with_outline()
    board.pop("outline")
    board["board_outlines"] = [{
        "step_name": "Main",
        "contours": [
            _contour("outline", [
                {"x": 0.0, "y": 0.0},
                {"x": 20.0, "y": 0.0},
                {"x": 20.0, "y": 10.0},
                {"x": 0.0, "y": 10.0},
                {"x": 0.0, "y": 0.0},
            ]),
            _contour("cutout", [
                {"x": 5.0, "y": 2.0},
                {"x": 8.0, "y": 2.0},
                {"x": 8.0, "y": 5.0},
                {"x": 5.0, "y": 5.0},
                {"x": 5.0, "y": 2.0},
            ]),
        ],
    }]
    return board


def test_standalone_board_to_dict_fixture_imports_polygon_and_stackup():
    groups = board_json_to_preview_groups(_board())

    assert len(groups) == 1
    group = groups[0]
    assert (group.net, group.layer) == ("SIG", "Top")
    assert group.name == "SIG_Top"
    assert group.layer_type == "SIGNAL"
    assert group.feature_count == 1
    assert group.polydata.GetNumberOfPolys() > 1
    assert group.polydata.GetBounds() == pytest.approx(
        (10.0, 12.0, 1.0, 3.0, 0.0, 0.035),
        abs=1e-6,
    )
    assert group.metadata["layer_thickness_mm"] == pytest.approx(0.035)
    assert group.metadata["revision"] == "B"
    assert group.metadata["stackup"]["name"] == "Main"
    assert group.metadata["stackup_layer"]["material"] == "Copper"
    assert group.metadata["net_metadata"]["effective_class"] == "SIGNAL"
    assert group.metadata["feature_metadata"][0]["source_tag"] == "Polygon"
    assert group.metadata["feature_metadata"][0]["source_geometry"]["closed"] is True
    assert all(group.object_role != "dielectric" for group in groups)


def test_non_copper_layer_geometry_is_omitted_from_copper_preview():
    board = _board()
    non_conductor_layers = (
        ("DOCUMENT", "REF-route"),
        ("SILKSCREEN", "REF-top"),
        ("SOLDERMASK", "Soldermask"),
        ("PASTEMASK", "Pastemask"),
        ("ASSEMBLY", "Assembly"),
    )
    for function, name in non_conductor_layers:
        board["layers"].append({
            "function": function,
            "name": name,
            "polarity": "POSITIVE",
            "side": "TOP",
        })
        feature = deepcopy(board["features"][0])
        feature["layer"] = name
        board["features"].append(feature)

    for layer_name in ("REF-route", "REF-top", "Soldermask", "Pastemask", "Assembly"):
        pad = _padstack_circle_feature("pad")
        pad["layer"] = layer_name
        board["features"].append(pad)

    drill_layer = {
        "function": "DRILL",
        "name": "DRILL_1-2",
        "polarity": "POSITIVE",
        "side": "NONE",
    }
    board["layers"].append(drill_layer)
    drill_via = _via_feature(include_lands=True)
    drill_via["layer"] = "DRILL_1-2"
    board["features"].append(drill_via)

    groups = board_json_to_preview_groups(board)

    copper_groups = [group for group in groups if group.object_role == "copper"]
    via_groups = [group for group in groups if group.object_role == "via"]
    signal_group = next(group for group in copper_groups if group.net == "SIG")
    via_copper_layers = {
        group.layer
        for group in copper_groups
        if group.net == "PWR"
    }
    assert signal_group.layer == "Top"
    assert signal_group.feature_count == 1
    assert via_copper_layers == {"Top", "Bottom"}
    assert via_groups


def test_conductor_feature_missing_from_stackup_is_still_rejected():
    board = _board()
    board["layers"].append({
        "function": "SIGNAL",
        "name": "UnstackedCopper",
        "polarity": "POSITIVE",
        "side": "INTERNAL",
    })
    feature = deepcopy(board["features"][0])
    feature["layer"] = "UnstackedCopper"
    board["features"].append(feature)

    with pytest.raises(
        BoardJSONImportError,
        match="layer 'UnstackedCopper' has no position in the selected stackup",
    ):
        board_json_to_preview_groups(board)


def test_source_units_convert_xy_and_ordered_stackup_thickness_converts_z():
    board = _board()
    board["units"] = "mil"
    # Values in Board.to_dict() remain in its declared source units.
    board["features"][0]["geometry"]["points"] = [
        {"x": point["x"] / 0.0254, "y": point["y"] / 0.0254}
        for point in board["features"][0]["geometry"]["points"]
    ]
    board["features"][0]["geometry"]["segments"] = [
        {
            **segment,
            "to": {
                "x": segment["to"]["x"] / 0.0254,
                "y": segment["to"]["y"] / 0.0254,
            },
        }
        for segment in board["features"][0]["geometry"]["segments"]
    ]
    for layer in board["stackups"][0]["layers"]:
        layer["thickness"] /= 0.0254
    board["stackups"][0]["overall_thickness"] /= 0.0254
    board["stackups"][0]["layers"][0]["thickness"] = 1.0
    board["stackups"][0]["overall_thickness"] = sum(
        layer["thickness"] for layer in board["stackups"][0]["layers"]
    )

    group = board_json_to_preview_groups(board)[0]

    assert group.polydata.GetPoint(0) == pytest.approx((10.0, 1.0, 0.0))
    assert group.metadata["layer_z_bottom_mm"] == pytest.approx(0.0)
    assert group.metadata["layer_z_top_mm"] == pytest.approx(0.0254)


def test_standalone_line_and_polyline_widths_create_visible_finite_geometry():
    group = board_json_to_preview_groups(_track_board())[0]

    assert (group.net, group.layer) == ("GND", "Top")
    assert group.feature_count == 2
    assert group.polydata.GetNumberOfPolys() >= 2
    assert group.polydata.GetBounds() == pytest.approx(
        (0.9, 5.1, 0.9, 4.075, 0.0, 0.035),
        abs=1e-5,
    )
    stroke_models = [
        feature["stroke_model"]
        for feature in group.metadata["feature_metadata"]
    ]
    assert all("explicit-width round-cap/round-join buffer" in model for model in stroke_models)


def test_aoi_split_polyline_renders_every_disjoint_path_with_the_feature_width():
    board = _track_board()
    track = next(feature for feature in board["features"] if feature["kind"] == "polyline")
    first = [{"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 1.0}]
    second = [{"x": 4.0, "y": 3.0}, {"x": 4.0, "y": 4.0}]
    paths = [
        {
            "role": "polyline",
            "points": points,
            "segments": [{"kind": "polystepsegment", "to": points[1]}],
            "closed": False,
        }
        for points in (first, second)
    ]
    track["geometry"].update(
        points=[],
        segments=[],
        closed=False,
        subpath_count=2,
        paths=paths,
    )
    board["features"] = [track]
    width = track["geometry"]["width"]

    group = board_json_to_preview_groups(board)[0]

    assert group.feature_count == 1
    assert group.polydata.GetBounds()[:4] == pytest.approx(
        (1.0 - width / 2, 4.0 + width / 2, 1.0 - width / 2, 4.0 + width / 2),
        abs=1e-5,
    )
    xs = [
        group.polydata.GetPoint(index)[0]
        for index in range(group.polydata.GetNumberOfPoints())
    ]
    # Nothing is drawn across the gap between the two clipped runs.
    assert not any(2.0 + width < x < 4.0 - width for x in xs)


def test_split_polyline_rejects_inconsistent_or_non_polyline_paths():
    board = _track_board()
    track = next(feature for feature in board["features"] if feature["kind"] == "polyline")
    points = [{"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 1.0}]
    path = {
        "role": "polyline",
        "points": points,
        "segments": [{"kind": "polystepsegment", "to": points[1]}],
        "closed": False,
    }
    track["geometry"].update(
        points=[], segments=[], closed=False, subpath_count=2, paths=[path]
    )
    board["features"] = [track]
    with pytest.raises(BoardJSONImportError, match="subpath_count"):
        board_json_to_preview_groups(board)

    track["geometry"].update(subpath_count=1, paths=[{**path, "role": "polygon"}])
    with pytest.raises(BoardJSONImportError, match="open polyline path"):
        board_json_to_preview_groups(board)


def test_concave_track_extrusion_caps_only_the_buffered_copper():
    from shapely.geometry import LineString

    board = _track_board()
    track = next(feature for feature in board["features"] if feature["kind"] == "polyline")
    width = 0.00525
    points = [
        {"x": 0.6644986338797814, "y": 0.4997704918032786},
        {"x": 0.6644986338797814, "y": 0.5687704918032785},
        {"x": 0.6694986338797813, "y": 0.5737704918032787},
        {"x": 0.6867486338797815, "y": 0.5737704918032787},
        {"x": 0.6917486338797814, "y": 0.5687704918032785},
        {"x": 0.6917486338797814, "y": 0.4787704918032787},
        {"x": 0.6967486338797813, "y": 0.47377049180327857},
        {"x": 0.7167486338797813, "y": 0.47377049180327857},
        {"x": 0.7217486338797812, "y": 0.4787704918032787},
        {"x": 0.7217486338797812, "y": 0.5637704918032787},
        {"x": 0.5917486338797813, "y": 0.6937704918032785},
        {"x": 0.29674863387978134, "y": 0.6937704918032785},
        {"x": 0.24624863387978135, "y": 0.6432704918032786},
        {"x": 0.23224863387978156, "y": 0.6432704918032786},
    ]
    track["geometry"].update(
        width=width,
        points=points,
        segments=[
            {"kind": "polystepsegment", "to": point}
            for point in points[1:]
        ],
    )
    board["features"] = [track]

    centerline = LineString([(point["x"], point["y"]) for point in points])
    expected_area = centerline.buffer(
        width / 2,
        8,
        cap_style=1,
        join_style=1,
    ).area
    group = board_json_to_preview_groups(board)[0]
    top_z = group.polydata.GetBounds()[5]
    top_area = 0.0
    top_faces = []
    for cell_index in range(group.polydata.GetNumberOfCells()):
        cell = group.polydata.GetCell(cell_index)
        coordinates = [
            cell.GetPoints().GetPoint(point_index)
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if not coordinates or not all(abs(point[2] - top_z) < 1e-9 for point in coordinates):
            continue
        top_faces.append(coordinates)
        top_area += abs(sum(
            coordinates[index][0] * coordinates[(index + 1) % len(coordinates)][1]
            - coordinates[(index + 1) % len(coordinates)][0] * coordinates[index][1]
            for index in range(len(coordinates))
        )) / 2.0

    assert top_faces
    assert all(len(face) == 3 for face in top_faces)
    assert top_area == pytest.approx(expected_area, rel=1e-5, abs=1e-9)


def test_extrusion_does_not_create_walls_on_shared_triangle_edges():
    from em3d_modeler.emerge.ipc2581_board_json import (
        _extrude_polydata,
        _polygons_to_polydata,
    )

    base = _polygons_to_polydata(
        [
            [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
            [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        ],
        z=0.0,
        scale=1.0,
    )
    solid = _extrude_polydata(base, height=1.0)

    cell_sizes = [
        solid.GetCell(index).GetNumberOfPoints()
        for index in range(solid.GetNumberOfCells())
    ]
    boundary_edges = vtk.vtkFeatureEdges()
    boundary_edges.SetInputData(solid)
    boundary_edges.BoundaryEdgesOn()
    boundary_edges.FeatureEdgesOff()
    boundary_edges.NonManifoldEdgesOff()
    boundary_edges.ManifoldEdgesOff()
    boundary_edges.Update()

    assert cell_sizes.count(3) == 4
    assert cell_sizes.count(4) == 4
    assert boundary_edges.GetOutput().GetNumberOfCells() == 0


def test_contour_feature_curves_are_tessellated_into_copper_polygons():
    group = board_json_to_preview_groups(_board_with_contour())[0]

    assert group.feature_count == 1
    assert group.polydata.GetNumberOfPolys() > 1
    assert group.polydata.GetBounds() == pytest.approx(
        (0.0, 6.0, 0.0, 4.0, 0.0, 0.035),
        abs=1e-3,
    )
    metadata = group.metadata["feature_metadata"][0]
    assert metadata["kind"] == "contour"
    assert metadata["source_geometry"]["subpath_count"] == 1
    assert metadata["stroke_model"] is None


def test_multipath_contour_cutout_remains_empty_in_copper_preview():
    group = board_json_to_preview_groups(_board_with_multipath_contour())[0]
    top_z = group.polydata.GetBounds()[5]
    top_area = 0.0
    for cell_index in range(group.polydata.GetNumberOfCells()):
        cell = group.polydata.GetCell(cell_index)
        coordinates = [
            cell.GetPoints().GetPoint(point_index)
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if not coordinates or not all(abs(point[2] - top_z) < 1e-9 for point in coordinates):
            continue
        top_area += abs(sum(
            coordinates[index][0] * coordinates[(index + 1) % len(coordinates)][1]
            - coordinates[(index + 1) % len(coordinates)][0] * coordinates[index][1]
            for index in range(len(coordinates))
        )) / 2.0

    assert group.feature_count == 1
    assert top_area == pytest.approx(100.0 - 3.141592653589793, abs=0.01)
    assert group.metadata["feature_metadata"][0]["source_geometry"]["subpath_count"] == 2


def test_polygon_feature_renders_all_paths_when_parent_points_and_segments_are_empty():
    group = board_json_to_preview_groups(_board_with_multipath_polygon())[0]
    top_z = group.polydata.GetBounds()[5]
    top_area = 0.0
    for cell_index in range(group.polydata.GetNumberOfCells()):
        cell = group.polydata.GetCell(cell_index)
        coordinates = [
            cell.GetPoints().GetPoint(point_index)
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if not coordinates or not all(abs(point[2] - top_z) < 1e-9 for point in coordinates):
            continue
        top_area += abs(sum(
            coordinates[index][0] * coordinates[(index + 1) % len(coordinates)][1]
            - coordinates[(index + 1) % len(coordinates)][0] * coordinates[index][1]
            for index in range(len(coordinates))
        )) / 2.0

    assert group.feature_count == 1
    assert group.polydata.GetBounds() == pytest.approx(
        (0.0, 9.0, 0.0, 4.0, 0.0, 0.035),
        abs=1e-6,
    )
    assert top_area == pytest.approx(25.0, abs=1e-6)


def test_contour_renders_multiple_disjoint_exteriors_and_only_explicit_cutouts_as_holes():
    group = board_json_to_preview_groups(_board_with_multiple_exterior_contours())[0]
    top_z = group.polydata.GetBounds()[5]
    top_area = 0.0
    for cell_index in range(group.polydata.GetNumberOfCells()):
        cell = group.polydata.GetCell(cell_index)
        coordinates = [
            cell.GetPoints().GetPoint(point_index)
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if not coordinates or not all(abs(point[2] - top_z) < 1e-9 for point in coordinates):
            continue
        top_area += abs(sum(
            coordinates[index][0] * coordinates[(index + 1) % len(coordinates)][1]
            - coordinates[(index + 1) % len(coordinates)][0] * coordinates[index][1]
            for index in range(len(coordinates))
        )) / 2.0

    assert group.feature_count == 1
    assert group.polydata.GetBounds() == pytest.approx(
        (0.0, 16.0, 0.0, 10.0, 0.0, 0.035),
        abs=1e-6,
    )
    assert top_area == pytest.approx(112.0, abs=1e-6)


def test_contour_cutout_clipped_to_exterior_boundary_is_subtracted_as_valid_geometry():
    group = board_json_to_preview_groups(_board_with_cutout_touching_exterior())[0]

    assert group.feature_count == 1
    assert group.polydata.GetBounds() == pytest.approx(
        (1.3197813364366187, 1.4445355191256828,
         0.33193432503541775, 0.4694343250354178, 0.0, 0.035),
        abs=1e-6,
    )
    assert group.polydata.GetNumberOfPolys() > 0


def test_plane_with_many_overlapping_antipad_cutouts_is_triangulated_completely():
    import math

    board = _board_with_contour()
    size = 30.0
    outer = _contour("polygon", [
        {"x": 0.0, "y": 0.0},
        {"x": size, "y": 0.0},
        {"x": size, "y": size},
        {"x": 0.0, "y": size},
        {"x": 0.0, "y": 0.0},
    ])
    outer["role"] = "polygon"
    paths = [outer]
    # Adjacent vias leave overlapping antipad circles in the same plane.
    for column in range(6):
        for row in range(6):
            center_x = 3.0 + column * 4.2
            center_y = 3.0 + row * 4.2
            ring = [
                {
                    "x": center_x + 2.4 * math.cos(2 * math.pi * step / 64),
                    "y": center_y + 2.4 * math.sin(2 * math.pi * step / 64),
                }
                for step in range(64)
            ]
            ring.append(dict(ring[0]))
            cutout = _contour("cutout", ring)
            cutout["role"] = "cutout"
            paths.append(cutout)
    board["features"][0]["geometry"] = {
        "closed": True,
        "points": [],
        "segments": [],
        "subpath_count": len(paths),
        "paths": paths,
    }

    group = board_json_to_preview_groups(board)[0]

    assert group.feature_count == 1
    assert group.polydata.GetNumberOfPolys() > 0
    top_z = group.polydata.GetBounds()[5]
    top_area = 0.0
    for cell_index in range(group.polydata.GetNumberOfCells()):
        cell = group.polydata.GetCell(cell_index)
        points = [
            cell.GetPoints().GetPoint(index)
            for index in range(cell.GetNumberOfPoints())
        ]
        if len(points) == 3 and all(abs(point[2] - top_z) < 1e-9 for point in points):
            top_area += abs(
                (points[1][0] - points[0][0]) * (points[2][1] - points[0][1])
                - (points[2][0] - points[0][0]) * (points[1][1] - points[0][1])
            ) / 2.0
    assert 0.0 < top_area < size * size


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda geometry: geometry.update(closed=False),
            "is not explicitly closed",
        ),
        (
            lambda geometry: geometry.update(subpath_count=2),
            "no explicit geometry.paths",
        ),
        (
            lambda geometry: geometry["segments"][0].update(
                to={"x": -1.0, "y": 0.0}
            ),
            "endpoint does not match geometry points",
        ),
    ],
)
def test_contour_feature_rejects_open_multipath_and_malformed_geometry(mutate, message):
    board = _board_with_contour()
    mutate(board["features"][0]["geometry"])

    with pytest.raises(BoardJSONImportError, match=message):
        board_json_to_preview_groups(board)


def test_padstack_circle_pad_is_rendered_on_its_declared_layer():
    board = _board()
    board["features"] = [_padstack_circle_feature("pad")]

    group = board_json_to_preview_groups(board)[0]

    assert (group.net, group.layer, group.feature_count) == ("PWR", "Top", 1)
    assert group.polydata.GetBounds() == pytest.approx(
        (9.0, 11.0, 4.0, 6.0, 0.0, 0.035),
        abs=1e-6,
    )
    assert group.metadata["feature_metadata"][0]["pad_uses"] == ["REGULAR"]


def test_plated_via_renders_land_rings_and_explicit_bore_wall():
    board = _board_with_outline()
    board["features"] = [_via_feature(include_lands=True)]

    groups = board_json_to_preview_groups(board)

    copper_groups = {
        group.layer: group
        for group in groups
        if group.net == "PWR" and group.object_role == "copper"
    }
    assert set(copper_groups) == {"Top", "Bottom"}
    assert all(group.feature_count == 1 for group in copper_groups.values())
    via = next(group for group in groups if group.object_role == "via")
    assert via.layer == "Top_to_Bottom"
    assert via.polydata.GetBounds() == pytest.approx(
        (9.85, 10.15, 4.85, 5.15, 0.0175, 1.5825),
        abs=1e-6,
    )
    assert via.metadata["feature_metadata"][0]["via"]["representation"].startswith(
        "open bore-wall surface"
    )
    dielectric = next(group for group in groups if group.object_role == "dielectric")
    assert dielectric.metadata["via_drill_count"] == 1
    assert dielectric.polydata.GetNumberOfPolys() > 0


def test_padless_plated_via_is_rendered_from_explicit_drill_and_span():
    board = _board_with_outline()
    via_feature = _via_feature()
    via_feature["geometry"]["pads"] = []
    board["features"] = [via_feature]

    groups = board_json_to_preview_groups(board)

    assert [group.object_role for group in groups] == ["via", "dielectric"]
    via = groups[0]
    assert via.polydata.GetBounds() == pytest.approx(
        (9.85, 10.15, 4.85, 5.15, 0.0175, 1.5825),
        abs=1e-6,
    )
    assert groups[1].metadata["via_drill_count"] == 1


def test_padstack_antipad_is_not_rendered_as_copper():
    board = _board()
    antipad = _padstack_circle_feature("pad")
    antipad["geometry"]["pads"][0]["pad_use"] = "ANTIPAD"
    board["features"] = [antipad]

    with pytest.raises(BoardJSONImportError, match="no copper pad geometry"):
        board_json_to_preview_groups(board)


def test_simulation_import_uses_native_profiles_and_requires_via_padstack_thickness():
    board = _board_with_outline()
    with pytest.raises(BoardJSONImportError, match="requires an explicit board outline"):
        board_json_to_preview_groups(_board(), simulation_ready=True)
    via = _via_feature(include_lands=True)
    board["features"].append(via)

    assert via_plating_requests(board) == [("padstack:P1", 1)]
    with pytest.raises(BoardJSONImportError, match="Missing explicit via plating thickness"):
        board_json_to_preview_groups(board, simulation_ready=True)

    groups = board_json_to_preview_groups(
        board,
        simulation_ready=True,
        via_plating_thicknesses_mm={"padstack:P1": 0.035},
    )
    copper = next(group for group in groups if group.object_role == "copper")
    barrel = next(group for group in groups if group.object_role == "via")

    assert copper.metadata["simulation_authoritative"] is True
    assert copper.step_geometry["schema"] == "ipc2581-profile-v1"
    assert copper.step_geometry["solids"][0]["kind"] == "extrusion"
    assert barrel.feature_count == 1
    assert barrel.step_geometry["solids"][0]["inner_radius"] == pytest.approx(0.15)
    assert barrel.step_geometry["solids"][0]["outer_radius"] == pytest.approx(0.185)
    assert barrel.metadata["feature_metadata"][0]["via"]["plating_thickness_mm"] == 0.035


def test_curve_segments_per_circle_reduce_native_circular_profile_vertices():
    board = _board_with_outline()
    board["features"] = [_padstack_circle_feature("pad")]

    reduced = board_json_to_preview_groups(
        board,
        simulation_ready=True,
        curve_segments_per_circle=6,
    )
    detailed = board_json_to_preview_groups(
        board,
        simulation_ready=True,
        curve_segments_per_circle=64,
    )
    reduced_profile = next(
        group for group in reduced if group.object_role == "copper"
    ).step_geometry["solids"][0]["profiles"][0]
    detailed_profile = next(
        group for group in detailed if group.object_role == "copper"
    ).step_geometry["solids"][0]["profiles"][0]

    assert len(reduced_profile["outer"]) == 6
    assert len(detailed_profile["outer"]) == 64
    assert len(reduced_profile["outer"]) < len(detailed_profile["outer"])
    assert reduced[0].metadata["curve_segments_per_circle"] == 6
    with pytest.raises(BoardJSONImportError, match="from 6 to 1000"):
        board_json_to_preview_groups(
            board,
            simulation_ready=True,
            curve_segments_per_circle=5,
        )


def test_simulation_import_subtracts_via_antipad_from_other_net_plane():
    board = _board_with_outline()
    plane = board["features"][0]
    plane["geometry"] = {
        "points": [
            {"x": 0.0, "y": 0.0},
            {"x": 20.0, "y": 0.0},
            {"x": 20.0, "y": 10.0},
            {"x": 0.0, "y": 10.0},
            {"x": 0.0, "y": 0.0},
        ],
        "segments": [
            {"kind": "polystepsegment", "to": point}
            for point in [
                {"x": 20.0, "y": 0.0},
                {"x": 20.0, "y": 10.0},
                {"x": 0.0, "y": 10.0},
                {"x": 0.0, "y": 0.0},
            ]
        ],
        "closed": True,
    }
    plane["net"] = "GND"
    via = _via_feature()
    via["net"] = "SIG"
    via["geometry"]["center"] = {"x": 5.0, "y": 5.0}
    via["geometry"]["pads"][0]["pad_use"] = "ANTIPAD"
    board["features"] = [plane, via]

    groups = board_json_to_preview_groups(
        board,
        simulation_ready=True,
        via_plating_thicknesses_mm={"padstack:P1": 0.035},
    )
    gnd = next(group for group in groups if group.net == "GND")
    profile = gnd.step_geometry["solids"][0]["profiles"][0]

    assert len(profile["holes"]) == 1


def test_native_ipc_profile_exports_as_brep_step(tmp_path):
    from importlib.util import find_spec

    if find_spec("OCP") is None:
        pytest.skip("OCP is not installed")

    from em3d_modeler.emerge.occ_export_builder import build_occ_shape_for_export
    from em3d_modeler.emerge.step_bundle_exporter import export_objects_to_step_bundle

    board = _board_with_outline()
    board["features"].append(_via_feature(include_lands=True))
    ground_plane = deepcopy(board["features"][0])
    ground_plane["net"] = "GND"
    ground_plane["geometry"] = {
        "closed": True,
        "points": [
            {"x": 0.0, "y": 0.0},
            {"x": 20.0, "y": 0.0},
            {"x": 20.0, "y": 10.0},
            {"x": 0.0, "y": 10.0},
            {"x": 0.0, "y": 0.0},
        ],
        "segments": [
            {"kind": "polystepsegment", "to": point}
            for point in [
                {"x": 20.0, "y": 0.0},
                {"x": 20.0, "y": 10.0},
                {"x": 0.0, "y": 10.0},
                {"x": 0.0, "y": 0.0},
            ]
        ],
    }
    antipad_via = _via_feature()
    antipad_via["net"] = "SIG"
    antipad_via["geometry"]["center"] = {"x": 15.0, "y": 5.0}
    antipad_via["geometry"]["pads"][0]["pad_use"] = "ANTIPAD"
    board["features"].extend((ground_plane, antipad_via))
    groups = board_json_to_preview_groups(
        board,
        simulation_ready=True,
        via_plating_thicknesses_mm={"padstack:P1": 0.035},
    )
    copper = next(group for group in groups if group.object_role == "copper")
    via = next(group for group in groups if group.object_role == "via")
    gnd = next(group for group in groups if group.net == "GND")
    assert len(gnd.step_geometry["solids"][0]["profiles"][0]["holes"]) == 1
    objects = [
        MeshObject(
            group.name,
            group.polydata,
            "PEC",
            ipc2581_step_geometry=group.step_geometry,
        )
        for group in (copper, via, gnd)
    ]
    assert all(build_occ_shape_for_export(obj) is not None for obj in objects)
    result = export_objects_to_step_bundle(objects, tmp_path / "bundle")
    assert len(result["entries"]) == 3
    for entry in result["entries"]:
        assert entry["export_mode"] == "brep_direct"
        output = tmp_path / "bundle" / entry["step_file"]
        step_text = output.read_text(encoding="ascii", errors="ignore")
        assert output.stat().st_size > 0
        assert "ADVANCED_FACE" in step_text
        assert "FACETED_BREP" not in step_text


def test_padstack_pad_and_via_reject_incomplete_geometry_explicitly():
    board = _board()
    board["features"] = [_padstack_circle_feature("pad")]
    board["features"][0]["geometry"]["pads"][0]["primitives"] = {}
    board["features"][0]["geometry"]["pads"][0]["primitive_refs"] = ["MISSING"]
    with pytest.raises(BoardJSONImportError, match="missing geometry for primitive"):
        board_json_to_preview_groups(board)

    board["features"] = [_via_feature()]
    board["features"][0]["geometry"].pop("span")
    with pytest.raises(BoardJSONImportError, match="span endpoints"):
        board_json_to_preview_groups(board)


@pytest.mark.parametrize("kind", ["line", "polyline"])
@pytest.mark.parametrize("width", [None, 0, -0.1])
def test_tracks_reject_missing_or_nonpositive_width(kind, width):
    board = _track_board()
    feature = next(item for item in board["features"] if item["kind"] == kind)
    if width is None:
        feature["geometry"].pop("width")
    else:
        feature["geometry"]["width"] = width

    with pytest.raises(BoardJSONImportError, match="width"):
        board_json_to_preview_groups(board)


def test_curved_track_segments_are_tessellated():
    board = _track_board()
    feature = board["features"][1]
    feature["geometry"]["points"] = [
        {"x": 1.625, "y": 2.175},
        {"x": 1.65, "y": 2.175},
        {"x": 1.675, "y": 2.2},
        {"x": 1.675, "y": 2.725},
    ]
    feature["geometry"]["segments"] = [
        {"kind": "polystepsegment", "to": {"x": 1.65, "y": 2.175}},
        {
            "kind": "polystepcurve",
            "to": {"x": 1.675, "y": 2.2},
            "center": {"x": 1.65, "y": 2.2},
            "clockwise": False,
        },
        {"kind": "polystepsegment", "to": {"x": 1.675, "y": 2.725}},
    ]
    board["features"] = [feature]

    group = board_json_to_preview_groups(board)[0]

    assert group.feature_count == 1
    assert group.polydata.GetNumberOfPolys() > 1
    assert group.polydata.GetBounds() == pytest.approx(
        (1.55, 1.75, 2.1, 2.8, 0.0, 0.035),
        abs=1e-5,
    )


def test_track_arc_center_correction_at_serialized_precision_boundary():
    board = _board_with_arc_track(
        {"x": 1.0, "y": 2.475},
        {"x": 1.075, "y": 2.475},
        {"x": 1.038, "y": 2.438},
    )

    group = board_json_to_preview_groups(board)[0]

    assert group.feature_count == 1
    assert group.polydata.GetNumberOfPolys() > 0


def test_track_arc_center_correction_outside_serialized_precision_is_rejected():
    board = _board_with_arc_track(
        {"x": 0.0, "y": 0.0},
        {"x": 0.1, "y": 0.0},
        {"x": 0.0506, "y": 0.0866},
    )

    with pytest.raises(BoardJSONImportError, match="inconsistent endpoint radii"):
        board_json_to_preview_groups(board)


def test_tracks_reject_segment_endpoint_mismatch_and_unimplemented_hole_kinds():
    board = _track_board()
    board["features"][1]["geometry"]["segments"][0]["to"] = {"x": 99.0, "y": 99.0}
    with pytest.raises(BoardJSONImportError, match="endpoint does not match geometry points"):
        board_json_to_preview_groups(board)

    board = _track_board()
    for kind in ("hole", "padstackfeature"):
        board["features"].append({"kind": kind})
    with pytest.raises(BoardJSONImportError) as error:
        board_json_to_preview_groups(board)
    assert "hole, padstackfeature" in str(error.value)


def test_unimplemented_hole_kinds_are_not_implicitly_dropped_or_approximated():
    board = _board()
    for kind in ("hole", "padstackfeature"):
        board["features"].append({"kind": kind})

    with pytest.raises(BoardJSONImportError) as error:
        board_json_to_preview_groups(board)

    message = str(error.value)
    assert "hole, padstackfeature" in message
    assert "polygon, line, polyline, contour, pad, and via" in message


def test_polygon_curve_segments_are_tessellated_and_unclosed_geometry_is_rejected():
    board = _board()
    board["features"][0]["geometry"] = _contour(
        "polygon",
        [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
            {"x": 0.0, "y": 0.0},
        ],
        curved_segment=(1, {"x": 4.0, "y": 2.0}, False),
    )
    group = board_json_to_preview_groups(board)[0]

    assert group.polydata.GetBounds() == pytest.approx(
        (0.0, 6.0, 0.0, 4.0, 0.0, 0.035),
        abs=1e-3,
    )
    assert group.polydata.GetNumberOfPolys() > 1

    board = _board()
    board["features"][0]["geometry"]["closed"] = False
    with pytest.raises(BoardJSONImportError, match="must explicitly be closed"):
        board_json_to_preview_groups(board)

    board = _board()
    board["features"][0]["geometry"]["segments"][0]["to"]["x"] = -1.0
    with pytest.raises(BoardJSONImportError, match="endpoint does not match"):
        board_json_to_preview_groups(board)


def test_polygons_with_small_curve_radius_rounding_import_together():
    board = _board()
    geometry = _contour(
        "polygon",
        [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
            {"x": 0.0, "y": 0.0},
        ],
        curved_segment=(1, {"x": 4.0, "y": 2.0}, False),
    )
    geometry["segments"][1]["center"]["y"] += 0.003
    first = deepcopy(board["features"][0])
    first["geometry"] = geometry
    second = deepcopy(first)
    second["geometry"] = deepcopy(geometry)
    second["geometry"]["segments"][1]["center"]["y"] += 0.001
    board["features"] = [first, second]

    group = board_json_to_preview_groups(board)[0]

    assert group.feature_count == 2
    assert group.polydata.GetBounds() == pytest.approx(
        (0.0, 6.0, 0.0, 4.0, 0.0, 0.035),
        abs=1e-3,
    )


def test_polygon_curve_rejects_mismatch_without_source_precision_metadata():
    board = _board()
    board["features"][0]["geometry"] = _contour(
        "polygon",
        [
            {"x": 0.006, "y": -0.003},
            {"x": 0.022, "y": 0.01},
            {"x": 0.02, "y": 0.029},
            {"x": 0.0, "y": 0.028},
            {"x": 0.0, "y": 0.0},
            {"x": 0.006, "y": -0.003},
        ],
        curved_segment=(4, {"x": 0.004, "y": 0.0}, False),
    )

    with pytest.raises(BoardJSONImportError, match="required center correction"):
        board_json_to_preview_groups(board)


def test_polygon_curve_rejects_large_radius_disagreement_with_diagnostics():
    board = _board()
    board["features"][0]["geometry"] = _contour(
        "polygon",
        [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
            {"x": 0.0, "y": 0.0},
        ],
        curved_segment=(1, {"x": 4.0, "y": 2.02}, False),
    )

    with pytest.raises(BoardJSONImportError, match="required center correction"):
        board_json_to_preview_groups(board)


def test_malformed_stackup_and_invalid_coordinates_are_rejected():
    board = _board()
    board["stackups"][0]["layers"][1]["thickness"] = None
    # An unknown later dielectric thickness does not make the first copper
    # layer's explicit z interval ambiguous.
    assert board_json_to_preview_groups(board)[0].polydata.GetBounds()[4:] == pytest.approx(
        (0.0, 0.035)
    )

    board = deepcopy(_board())
    board["features"][0]["geometry"]["points"][0]["x"] = float("nan")
    with pytest.raises(BoardJSONImportError, match="finite number"):
        board_json_to_preview_groups(board)

    board = _board()
    board["units"] = "unknown-unit"
    with pytest.raises(BoardJSONImportError, match="Unsupported or missing board unit"):
        board_json_to_preview_groups(board)


def test_missing_conductor_thickness_or_ambiguous_elevation_is_rejected():
    board = _board()
    board["stackups"][0]["layers"][0]["thickness"] = None
    with pytest.raises(BoardJSONImportError, match="no explicit positive thickness"):
        board_json_to_preview_groups(board)

    board = _board()
    board["features"][0]["layer"] = "Bottom"
    board["stackups"][0]["layers"][1]["thickness"] = None
    with pytest.raises(BoardJSONImportError, match="ambiguous z position"):
        board_json_to_preview_groups(board)


def test_inconsistent_overall_stackup_thickness_is_rejected():
    board = _board()
    board["stackups"][0]["overall_thickness"] = 1.7

    with pytest.raises(BoardJSONImportError, match="disagrees with the sum"):
        board_json_to_preview_groups(board)


def test_conductor_material_without_explicit_layer_function_is_required():
    board = _board()
    board["layers"][0]["function"] = None
    board["stackups"][0]["layers"][0]["material"] = "FR4"

    with pytest.raises(BoardJSONImportError, match="not identified as a conductor"):
        board_json_to_preview_groups(board)


def test_bottom_layer_extrudes_between_its_stackup_surfaces():
    board = _board()
    board["features"][0]["layer"] = "Bottom"

    group = board_json_to_preview_groups(board)[0]

    assert group.polydata.GetBounds()[4:] == pytest.approx((1.565, 1.6))


def test_explicit_outline_creates_one_extruded_dielectric_solid_per_dielectric_layer():
    groups = board_json_to_preview_groups(_board_with_outline())
    dielectric_groups = [group for group in groups if group.object_role == "dielectric"]

    assert len(dielectric_groups) == 1
    dielectric = dielectric_groups[0]
    assert dielectric.name == "IPC2581_Dielectric_Core"
    assert dielectric.layer == "Core"
    assert dielectric.metadata["stackup_layer"]["material"] == "FR4"
    assert dielectric.metadata["preview_only"] is True
    assert dielectric.metadata["simulation_authoritative"] is False
    assert dielectric.polydata.GetBounds() == pytest.approx(
        (0.0, 20.0, 0.0, 10.0, 0.035, 1.565),
        abs=1e-6,
    )


def test_single_dielectric_preview_combines_stackup_layers_into_one_object():
    board = _board_with_outline()
    stackup_layers = board["stackups"][0]["layers"]
    core = next(layer for layer in stackup_layers if layer["name"] == "Core")
    core["thickness"] -= 0.2
    core_index = stackup_layers.index(core)
    stackup_layers.insert(core_index, {
        "name": "Prepreg",
        "thickness": 0.2,
        "material": "FR4",
        "function": "DIELECTRIC",
    })
    board["layers"].append({"name": "Prepreg", "function": "DIELECTRIC"})
    board["modeler_options"] = {
        "dielectric_representation": "single_object",
    }

    groups = board_json_to_preview_groups(board)
    dielectric_groups = [
        group for group in groups if group.object_role == "dielectric"
    ]

    assert len(dielectric_groups) == 1
    dielectric = dielectric_groups[0]
    assert dielectric.name == "IPC2581_Dielectric"
    assert dielectric.layer == "Dielectric"
    assert [layer["name"] for layer in dielectric.metadata["stackup_layers"]] == [
        "Prepreg",
        "Core",
    ]
    assert dielectric.polydata.GetBounds() == pytest.approx(
        (0.0, 20.0, 0.0, 10.0, 0.035, 1.565),
        abs=1e-6,
    )
    assert dielectric.polydata.GetNumberOfPolys() > 0


def test_parser_board_outlines_support_curves_cutouts_and_zero_thickness_surface():
    board = _board_with_parser_profile()
    board["stackups"][0]["layers"].insert(0, {
        "name": "Surface",
        "thickness": 0.0,
        "material": "AIR",
        "function": "SURFACE",
    })
    board["stackups"][0]["layers"][2]["function"] = "DIELCORE"

    groups = board_json_to_preview_groups(board)
    dielectric_groups = [group for group in groups if group.object_role == "dielectric"]

    assert [group.layer for group in dielectric_groups] == ["Core"]
    dielectric = dielectric_groups[0]
    assert dielectric.metadata["board_cutout_count"] == 1
    assert dielectric.metadata["board_outlines"][0]["step_name"] == "Main"
    assert dielectric.polydata.GetBounds() == pytest.approx(
        (0.0, 20.0, 0.0, 10.0, 0.035, 1.565),
        abs=1e-6,
    )
    assert all(
        not (5.0 < dielectric.polydata.GetPoint(index)[0] < 8.0
             and 2.0 < dielectric.polydata.GetPoint(index)[1] < 5.0)
        for index in range(dielectric.polydata.GetNumberOfPoints())
    )


def test_parser_board_outline_curves_are_tessellated_within_profile():
    board = _board_with_parser_profile()
    board["board_outlines"][0]["contours"] = [
        _contour("outline", [
            {"x": 0.0, "y": 0.0},
            {"x": 4.0, "y": 0.0},
            {"x": 4.0, "y": 4.0},
            {"x": 0.0, "y": 4.0},
            {"x": 0.0, "y": 0.0},
        ], curved_segment=(1, {"x": 4.0, "y": 2.0}, False)),
    ]

    dielectric = next(
        group for group in board_json_to_preview_groups(board)
        if group.object_role == "dielectric"
    )

    assert dielectric.polydata.GetBounds() == pytest.approx(
        (0.0, 6.0, 0.0, 4.0, 0.035, 1.565),
        abs=1e-3,
    )


def test_outline_is_never_inferred_from_copper_bounds():
    groups = board_json_to_preview_groups(_board())
    assert [group.object_role for group in groups] == ["copper"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda board: board["outline"]["geometry"]["segments"][0].update(
                kind="polystepcurve"
            ),
            "no explicit center",
        ),
        (
            lambda board: board["outline"].update(holes=[[[1, 1], [2, 1], [1, 1]]]),
            "outline holes are not supported",
        ),
        (
            lambda board: board["outline"].update(
                contours=[
                    board["outline"],
                    board["outline"],
                ]
            ),
            "multiple outlines are not supported",
        ),
    ],
)
def test_curved_hole_or_multiple_board_outlines_are_rejected(mutate, message):
    board = _board_with_outline()
    mutate(board)

    with pytest.raises(BoardJSONImportError, match=message):
        board_json_to_preview_groups(board)


def test_dielectric_outline_requires_explicit_thickness_and_unambiguous_z():
    board = _board_with_outline()
    board["stackups"][0]["layers"][1]["thickness"] = None

    with pytest.raises(BoardJSONImportError, match="Dielectric layer 'Core'.*no explicit positive thickness"):
        board_json_to_preview_groups(board)

    board = _board_with_outline()
    board["features"][0]["layer"] = "Bottom"
    board["stackups"][0]["layers"][1]["thickness"] = None
    with pytest.raises(BoardJSONImportError, match="ambiguous z position"):
        board_json_to_preview_groups(board)


def test_preview_mesh_and_board_metadata_survive_project_json_roundtrip():
    group = board_json_to_preview_groups(_board())[0]
    scene = SceneManager(vtk.vtkRenderer())
    obj = MeshObject(group.name, group.polydata, "PEC")
    obj.is_model = False
    obj.creation_history = {"mode": "ipc2581_board_json", **group.metadata}
    scene.add_object(obj)

    restored = SceneManager(vtk.vtkRenderer())
    restored.from_json(scene.to_json())

    assert len(restored.objects) == 1
    assert restored.objects[0].creation_history["net"] == "SIG"
    assert restored.objects[0].creation_history["stackup"]["name"] == "Main"
    assert (
        restored.objects[0]
        .creation_history["feature_metadata"][0]["source_geometry"]["closed"]
    )
    assert not restored.objects[0].is_model
    assert restored.objects[0].actor.GetMapper().GetInput().GetBounds()[5] == pytest.approx(0.035)


def test_simulation_profile_and_native_step_data_survive_project_roundtrip():
    group = board_json_to_preview_groups(
        _board_with_outline(),
        simulation_ready=True,
    )[0]
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(MeshObject(
        group.name,
        group.polydata,
        "PEC",
        ipc2581_step_geometry=group.step_geometry,
    ))

    restored = SceneManager(vtk.vtkRenderer())
    restored.from_json(scene.to_json())

    restored_obj = restored.objects[0]
    assert restored_obj.is_model
    assert restored_obj.ipc2581_step_geometry == group.step_geometry


def test_board_json_loader_requires_object_root(tmp_path):
    path = tmp_path / "board.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_board_json(path)["revision"] == "B"

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(BoardJSONImportError, match="root must be an object"):
        load_board_json(path)


def test_main_window_board_json_entry_adds_identifiable_mesh(monkeypatch, tmp_path):
    path = tmp_path / "board-with-profile.json"
    path.write_text(json.dumps(_board_with_outline()), encoding="utf-8")
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QFileDialog.getOpenFileName",
        lambda *_args: (str(path), "Board JSON (*.json)"),
    )
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QInputDialog.getInt",
        lambda *_args, **_kwargs: (20, True),
    )
    scene = SceneManager(vtk.vtkRenderer())
    viewport = type("Viewport", (), {
        "scene": scene,
        "object_selected": Mock(),
        "selection_changed": Mock(),
        "scene_changed": Mock(),
        "_render": Mock(),
    })()
    info_bar = type("InfoBar", (), {"set_info": Mock()})()
    window = type("Window", (), {
        "_units": "mm",
        "_draw_material": "PEC",
        "_viewport": viewport,
        "_info_bar": info_bar,
        "_refresh_materials": Mock(),
    })()

    MainWindow._import_ipc2581_board_json_3d(window)

    copper = next(obj for obj in scene.objects if obj.name == "SIG_Top")
    assert copper.creation_history["mode"] == "ipc2581_board_json"
    assert copper.is_model
    assert copper.ipc2581_step_geometry["schema"] == "ipc2581-profile-v1"
    assert scene.objects[0].creation_history["net"] == "SIG"
    assert scene.objects[0].creation_history["stackup"]["name"] == "Main"
    assert MainWindow._simulation_model_objects(window) == scene.objects
    viewport.scene_changed.emit.assert_called_once()
    info_bar.set_info.assert_called_once()


def test_main_window_adds_simulation_dielectric_outline_solids(monkeypatch, tmp_path):
    path = tmp_path / "board-with-outline.json"
    path.write_text(json.dumps(_board_with_outline()), encoding="utf-8")
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QFileDialog.getOpenFileName",
        lambda *_args: (str(path), "Board JSON (*.json)"),
    )
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QInputDialog.getInt",
        lambda *_args, **_kwargs: (20, True),
    )
    scene = SceneManager(vtk.vtkRenderer())
    viewport = type("Viewport", (), {
        "scene": scene,
        "object_selected": Mock(),
        "selection_changed": Mock(),
        "scene_changed": Mock(),
        "_render": Mock(),
    })()
    window = type("Window", (), {
        "_units": "mm",
        "_draw_material": "PEC",
        "_viewport": viewport,
        "_info_bar": type("InfoBar", (), {"set_info": Mock()})(),
        "_refresh_materials": Mock(),
    })()

    MainWindow._import_ipc2581_board_json_3d(window)

    dielectric = next(obj for obj in scene.objects if "Dielectric" in obj.name)
    assert dielectric.material == "Dielectric"
    assert dielectric.is_model
    assert dielectric.ipc2581_step_geometry["solids"]
    assert dielectric.actor.GetMapper().GetInput().GetBounds() == pytest.approx(
        (0.0, 20.0, 0.0, 10.0, 0.035, 1.565),
        abs=1e-6,
    )


def test_main_window_applies_one_plating_thickness_to_all_padstacks(monkeypatch, tmp_path):
    board = _board_with_outline()
    first_via = _via_feature(include_lands=True)
    second_via = deepcopy(first_via)
    second_via["geometry"]["center"] = {"x": 12.0, "y": 5.0}
    second_via["geometry"]["padstack_ref"] = "P2"
    board["features"].extend((first_via, second_via))
    path = tmp_path / "board-with-vias.json"
    path.write_text(json.dumps(board), encoding="utf-8")
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QFileDialog.getOpenFileName",
        lambda *_args: (str(path), "Board JSON (*.json)"),
    )
    prompt = Mock(return_value=("0.035", True))
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QInputDialog.getText",
        prompt,
    )
    curve_prompt = Mock(return_value=(32, True))
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QInputDialog.getInt",
        curve_prompt,
    )
    scene = SceneManager(vtk.vtkRenderer())
    window = type("Window", (), {
        "_units": "mm",
        "_draw_material": "PEC",
        "_curved_boundary_resolution": 32,
        "_viewport": type("Viewport", (), {
            "scene": scene,
            "object_selected": Mock(),
            "selection_changed": Mock(),
            "scene_changed": Mock(),
            "_render": Mock(),
        })(),
        "_info_bar": type("InfoBar", (), {"set_info": Mock()})(),
        "_refresh_materials": Mock(),
    })()

    MainWindow._import_ipc2581_board_json_3d(window)

    prompt.assert_called_once()
    curve_prompt.assert_called_once()
    assert curve_prompt.call_args.kwargs["value"] == 32
    via_object = next(obj for obj in scene.objects if obj.name.startswith("PWR_Via"))
    assert via_object.is_model
    assert via_object.creation_history["feature_count"] == 2
    solids = via_object.ipc2581_step_geometry["solids"]
    assert len(solids) == 2
    assert [solid["outer_radius"] for solid in solids] == pytest.approx(
        [0.185, 0.185]
    )
    assert via_object.creation_history["curve_segments_per_circle"] == 32


def test_legacy_3d_import_keeps_progress_dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QFileDialog.getOpenFileName",
        lambda *_args: (str(FIXTURE), "Board JSON (*.json)"),
    )
    monkeypatch.setattr(
        "em3d_modeler.emerge.ipc2581_board_json.board_json_to_preview_groups",
        lambda *_args, **_kwargs: [],
    )

    class ProgressDialog:
        instances = []

        def __init__(self, *args):
            self.labels = [args[0]]
            self.closed = False
            self.instances.append(self)

        def setWindowTitle(self, _title):
            pass

        def setWindowModality(self, _modality):
            pass

        def setCancelButton(self, _button):
            pass

        def setMinimumDuration(self, _duration):
            pass

        def setAutoClose(self, _auto_close):
            pass

        def setAutoReset(self, _auto_reset):
            pass

        def show(self):
            pass

        def setLabelText(self, label):
            self.labels.append(label)

        def setRange(self, _minimum, _maximum):
            pass

        def setValue(self, _value):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QProgressDialog",
        ProgressDialog,
    )
    monkeypatch.setattr(
        "em3d_modeler.ui.main_window.QInputDialog.getInt",
        lambda *_args, **_kwargs: (20, True),
    )
    window = QMainWindow()
    window._units = "mm"
    window._draw_material = "PEC"
    window._viewport = type("Viewport", (), {
        "scene": SceneManager(vtk.vtkRenderer()),
    })()
    window._info_bar = type("InfoBar", (), {"set_info": Mock()})()
    window._refresh_materials = Mock()

    MainWindow._import_ipc2581_board_json_3d(window)

    progress = ProgressDialog.instances[-1]
    assert any("Reading IPC-2581B board JSON" in label for label in progress.labels)
    assert any("Validating and building simulation geometry" in label for label in progress.labels)
    assert any("Adding PCB model objects" in label for label in progress.labels)
    assert progress.closed
    assert app is not None
