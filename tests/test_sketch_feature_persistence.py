import ast
from copy import deepcopy
import json
import os

import pytest
import vtk
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene.em_objects import BoxObject, ExtrudedObject, RevolvedObject
from em3d_modeler.scene.scene_manager import SceneManager
from em3d_modeler.ui.body_properties_widget import (
    BodyPropertiesWidget,
    _VariableCompleterLineEdit,
)


_QT_APP = None


def test_extruded_parameters_round_trip_and_rebuild():
    original = ExtrudedObject("Extrusion")
    original.set_parameters({
        "ProfilePts": "[(0, 0), (4, 0), (3, 2), (0, 2)]",
        "Depth": 7,
        "PlaneOriginX": 2,
        "PlaneOriginY": 3,
        "PlaneOriginZ": 4,
        "PlaneNormalX": 0,
        "PlaneNormalY": 1,
        "PlaneNormalZ": 0,
    })
    restored = ExtrudedObject("Restored")

    restored.set_parameters(original.get_parameters())

    params = restored.get_parameters()
    assert ast.literal_eval(params["ProfilePts"]) == [(0, 0), (4, 0), (3, 2), (0, 2)]
    assert (params["Depth"], params["PlaneOriginX"], params["PlaneOriginY"], params["PlaneOriginZ"]) == (7, 2, 3, 4)
    assert (params["PlaneNormalX"], params["PlaneNormalY"], params["PlaneNormalZ"]) == (0, 1, 0)
    assert restored._extrude.GetOutput().GetNumberOfCells() > 0


def test_revolved_parameters_round_trip_and_rebuild():
    original = RevolvedObject("Revolution")
    original.set_parameters({
        "ProfilePts": "[(1, 0), (3, 0), (3, 5)]",
        "Angle": 210,
        "AxisPt1X": 2,
        "AxisPt1Y": 3,
        "AxisPt1Z": 4,
        "AxisPt2X": 2,
        "AxisPt2Y": 4,
        "AxisPt2Z": 4,
        "PlaneOriginX": 5,
        "PlaneOriginY": 6,
        "PlaneOriginZ": 7,
        "PlaneNormalX": 1,
        "PlaneNormalY": 0,
        "PlaneNormalZ": 0,
    })
    restored = RevolvedObject("Restored")

    restored.set_parameters(original.get_parameters())

    params = restored.get_parameters()
    assert ast.literal_eval(params["ProfilePts"]) == [(1, 0), (3, 0), (3, 5)]
    assert params["Angle"] == 210
    assert (params["AxisPt1X"], params["AxisPt1Y"], params["AxisPt1Z"]) == (2, 3, 4)
    assert (params["AxisPt2X"], params["AxisPt2Y"], params["AxisPt2Z"]) == (2, 4, 4)
    assert (params["PlaneOriginX"], params["PlaneOriginY"], params["PlaneOriginZ"]) == (5, 6, 7)
    assert (params["PlaneNormalX"], params["PlaneNormalY"], params["PlaneNormalZ"]) == (1, 0, 0)
    assert restored._revolve.GetOutput().GetNumberOfCells() > 0


def test_feature_position_updates_translate_reference_geometry():
    extrusion = ExtrudedObject(plane_origin=(1, 2, 3))
    extrusion.set_parameters({"PositionX": 4, "PositionY": 6, "PositionZ": 8, "Depth": 12})
    assert extrusion._plane_origin == (4, 6, 8)
    assert extrusion._depth == 12

    revolution = RevolvedObject(
        axis_pt1=(1, 2, 3), axis_pt2=(1, 2, 8), plane_origin=(4, 5, 6)
    )
    revolution.set_parameters({"PositionX": 5, "PositionY": 0, "PositionZ": 7, "Angle": 180})
    assert revolution._axis_pt1 == (5, 0, 7)
    assert revolution._axis_pt2 == (5, 0, 12)
    assert revolution._plane_origin == (8, 3, 10)
    assert revolution._angle == 180


def test_feature_position_uses_first_sketch_point_on_oblique_plane():
    sketch = SketchEngine((1, 2, 3), (0, 1, 0))
    sketch.add_polyline([(2, 1), (5, 1), (5, 4), (2, 4), (2, 1)])
    feature = ExtrudedObject(
        profile_pts=sketch.build_profile(),
        plane_origin=sketch.plane_origin,
        plane_normal=sketch.plane_normal,
    )
    feature.sketch_definition = sketch.to_dict()

    feature.set_parameters({"PositionX": 10, "PositionY": 20, "PositionZ": 30})

    assert feature.sketch_reference_point() == pytest.approx((10, 20, 30))
    assert feature.sketch_definition["plane_origin"] == pytest.approx([9, 20, 32])


@pytest.mark.parametrize("factory", [ExtrudedObject, RevolvedObject])
def test_body_properties_expose_position_without_raw_feature_geometry(factory):
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    feature = factory()
    widget = BodyPropertiesWidget()
    widget.set_object(feature)

    labels = [widget._table.item(row, 0).text() for row in range(widget._table.rowCount())]
    params, _formulas = widget._collect_params()

    assert {"PositionX", "PositionY", "PositionZ"}.issubset(labels)
    assert not {"ProfilePts", "PlaneOriginX", "PlaneNormalX", "AxisPt1X", "AxisPt2X"}.intersection(labels)
    assert {"PositionX", "PositionY", "PositionZ"}.issubset(params)
    assert not {"ProfilePts", "PlaneOriginX", "PlaneNormalX", "AxisPt1X", "AxisPt2X"}.intersection(params)


def test_value_completion_filters_variables_and_replaces_only_current_token():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    editor = _VariableCompleterLineEdit(["body_length", "body_width", "wall"])
    editor.setText("2 * body_w + wall")
    editor.setCursorPosition(len("2 * body_w"))

    editor._update_completions(editor.text())

    assert editor._completer.completionCount() == 1
    assert editor._completer.currentCompletion() == "body_width"
    editor._insert_completion("body_width")

    assert editor.text() == "2 * body_width + wall"


def test_value_completion_can_be_accepted_from_keyboard():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    editor = _VariableCompleterLineEdit(["body_width", "body_length"])
    editor.show()
    editor.setFocus()

    QTest.keyClicks(editor, "body_w")
    assert editor._completer.popup().isVisible()
    assert editor._completer.currentCompletion() == "body_width"
    QTest.keyClick(editor, Qt.Key_Return)

    assert editor.text() == "body_width"
    editor.close()


def test_properties_widget_updates_variable_completion_candidates():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    widget = BodyPropertiesWidget()

    widget.set_project_variable_names(["length", "width", "length", "  "])

    assert widget._project_variable_names == ["length", "width"]


def test_invalid_or_executable_profile_is_rejected_atomically():
    extrusion = ExtrudedObject()
    starting = extrusion.get_parameters()

    with pytest.raises(ValueError, match="literal sequence"):
        extrusion.set_parameters({"ProfilePts": "__import__('os').system('echo unsafe')", "Depth": 20})

    assert extrusion.get_parameters() == starting


@pytest.mark.parametrize(
    "factory, params",
    [
        (ExtrudedObject, {"Depth": 0}),
        (ExtrudedObject, {"PlaneNormalX": 0, "PlaneNormalY": 0, "PlaneNormalZ": 0}),
        (RevolvedObject, {"AxisPt2X": 0, "AxisPt2Y": 0, "AxisPt2Z": 0}),
        (RevolvedObject, {"Angle": 361}),
    ],
)
def test_feature_setters_reject_non_meaningful_geometry(factory, params):
    feature = factory()

    with pytest.raises(ValueError):
        feature.set_parameters(params)


@pytest.mark.parametrize(
    "feature",
    [
        ExtrudedObject("Extrusion", plane_origin=(2, 3, 4), plane_normal=(0, 1, 0)),
        RevolvedObject(
            "Revolution",
            axis_pt1=(2, 3, 4),
            axis_pt2=(2, 3, 9),
            plane_origin=(5, 6, 7),
            plane_normal=(1, 0, 0),
            angle=225,
        ),
    ],
)
def test_scene_json_round_trips_feature_state_and_appearance(feature):
    feature.set_parameters({"Opacity": 0.57, "Color": "#336699"})
    feature.set_visible(False)
    feature.is_model = False
    feature.creation_history = {"operation": "sketch"}
    feature.pattern_definition = {"count": 2}
    feature.param_formulas = {"Depth": "base_depth * 2"}
    feature.creation_reference_error = "reference warning"
    feature.set_creation_plane("XZ", (1, 2, 3), (0, 1, 0))
    if isinstance(feature, ExtrudedObject):
        feature.actor.SetPosition(3, 4, 5)
    expected_user_matrix = feature.actor.GetUserMatrix()

    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(feature)
    serialized = scene.to_json()
    restored_scene = SceneManager(vtk.vtkRenderer())
    restored_scene.from_json(serialized)

    restored = restored_scene.objects[0]
    assert type(restored) is type(feature)
    assert restored.get_parameters() == feature.get_parameters()
    assert restored.opacity == pytest.approx(0.57)
    assert restored.custom_color == pytest.approx((0x33 / 255, 0x66 / 255, 0x99 / 255))
    assert not restored.is_visible()
    assert restored.is_model is False
    assert restored.creation_history == {"operation": "sketch"}
    assert restored.pattern_definition == {"count": 2}
    assert restored.param_formulas == {"Depth": "base_depth * 2"}
    assert restored.creation_reference_error == "reference warning"
    assert restored.creation_plane == "XZ"
    if isinstance(feature, ExtrudedObject):
        assert restored.actor.GetPosition() == pytest.approx((3, 4, 5))
    else:
        restored_user_matrix = restored.actor.GetUserMatrix()
        assert restored_user_matrix is not None
        for row in range(4):
            for column in range(4):
                assert restored_user_matrix.GetElement(row, column) == pytest.approx(
                    expected_user_matrix.GetElement(row, column)
                )
    assert restored.actor.GetProperty().GetOpacity() == pytest.approx(0.57)


def test_scene_json_round_trips_sketch_definition_and_legacy_state():
    sketch = SketchEngine((1.0, 2.0, 3.0), (0.0, 1.0, 0.0))
    entity_ref = sketch.add_line((-2.0, 0.0), (2.0, 0.0), construction=True)
    sketch.add_dimension(
        "aligned",
        (sketch.point_ref(entity_ref, 0), sketch.point_ref(entity_ref, 1)),
        expression="project.diagonal",
    )
    definition = sketch.to_dict()
    feature = BoxObject("Sketch owner")
    feature.sketch_definition = definition
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(feature)

    serialized = scene.to_json()
    json_state = json.loads(json.dumps(serialized))
    copied_state = deepcopy(serialized)
    copied_state[0]["sketch_definition"]["entities"][0]["construction"] = False
    assert serialized[0]["sketch_definition"]["entities"][0]["construction"] is True
    serialized[0]["sketch_definition"]["entities"][0]["construction"] = False
    assert definition["entities"][0]["construction"] is True

    restored_scene = SceneManager(vtk.vtkRenderer())
    restored_scene.from_json(json_state)
    restored = restored_scene.objects[0]
    assert restored.sketch_definition == definition
    assert restored.sketch_definition["entities"][0]["id"] == entity_ref.entity_id
    assert restored.sketch_definition["entities"][0]["construction"] is True
    assert restored.sketch_definition["dimensions"][0]["expression"] == "project.diagonal"
    json_state[0]["sketch_definition"]["entities"][0]["construction"] = False
    assert restored.sketch_definition["entities"][0]["construction"] is True

    legacy = BoxObject("Legacy")
    legacy.from_json_state({"creation_plane": "XZ"})
    assert legacy.sketch_definition is None
    legacy.from_json_state({"sketch_definition": {"invalid": object()}})
    assert legacy.sketch_definition is None


def test_scene_state_ignores_malformed_creation_plane_origin():
    feature = BoxObject("Legacy")
    feature.from_json_state({
        "creation_plane": "XZ",
        "creation_plane_origin": [1, 2],
        "creation_plane_normal": [0, 1, 0],
    })

    assert feature.creation_plane_origin == (0.0, 0.0, 0.0)
    assert feature.creation_plane_normal == (0.0, 1.0, 0.0)