import os
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QToolBar, QToolButton, QWidget

from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.drawing.interactor_style import EMInteractorStyle
from em3d_modeler.scene import boolean_ops, em_objects
from em3d_modeler.ui.body_properties_widget import BodyPropertiesWidget
from em3d_modeler.ui import main_window as main_window_module
from em3d_modeler.ui import viewport_widget as viewport_module
from em3d_modeler.ui.main_window import MainWindow, _add_toolbar_group, _icon
from em3d_modeler.ui.viewport_widget import Viewport3DWidget


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *values):
        self.values.append(values)


def _viewport_stub(engine):
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_axis_pick_mode=False,
        _sketch_axis_highlight=None,
        _sketch_dimension_kind=None,
        _sketch_dimension_picks=[],
        _sketch_dimension_resolver=lambda expression: float(expression),
        _grid_spacing=1.0,
        _units="mm",
        status_message=_Signal(),
        _refresh_sketch_overlay=Mock(),
        setCursor=Mock(),
    )
    viewport._sketch_set_tool = MethodType(Viewport3DWidget._sketch_set_tool, viewport)
    viewport._sketch_set_selection_mode = MethodType(
        Viewport3DWidget._sketch_set_selection_mode, viewport
    )
    viewport._center_arc_path = Viewport3DWidget._center_arc_path
    viewport._closest_point_2d = Viewport3DWidget._closest_point_2d
    viewport.recompute_sketch_dimensions = MethodType(
        Viewport3DWidget.recompute_sketch_dimensions, viewport
    )
    return viewport


def test_center_rectangle_and_construction_toggle_affect_profile_geometry():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    viewport = _viewport_stub(engine)

    Viewport3DWidget._sketch_set_rectangle_mode(viewport, "center-corner")
    engine.on_click((1, 2))
    engine.on_click((4, 6))
    rectangle = engine.entities[-1]
    assert rectangle.rectangle_mode == "center-corner"
    assert engine.build_profile() == [(-2, -2), (4, -2), (4, 6), (-2, 6), (-2, -2)]

    assert Viewport3DWidget._sketch_set_construction_mode(viewport, True) is True
    engine.add_line((10, 10), (12, 10))
    assert engine.entities[-1].construction is True
    assert len(engine.build_profile()) == 5


def test_right_click_cancels_active_sketch_tool():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_tool("line")
    engine.on_click((1, 2))
    viewport = _viewport_stub(engine)
    viewport._sketch_cancel_tool = MethodType(
        Viewport3DWidget._sketch_cancel_tool, viewport
    )

    consumed = Viewport3DWidget._on_sketch_right_press(viewport, 10, 20)

    assert consumed is True
    assert engine.tool is None
    assert engine.pending == []
    assert viewport.status_message.values[-1] == ("Sketch tool cancelled",)


def test_right_click_discards_in_progress_sketch_polyline():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_tool("polyline")
    engine.on_click((0, 0))
    engine.on_click((2, 0))
    viewport = _viewport_stub(engine)
    viewport._sketch_cancel_tool = MethodType(
        Viewport3DWidget._sketch_cancel_tool, viewport
    )

    assert Viewport3DWidget._on_sketch_right_press(viewport, 10, 20)

    assert engine.tool is None
    assert engine.entities == []
    assert engine.pending == []


def test_escape_discards_in_progress_sketch_polyline():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_tool("polyline")
    engine.on_click((0, 0))
    engine.on_click((2, 0))
    viewport = _viewport_stub(engine)
    viewport._sketch_cancel_tool = MethodType(
        Viewport3DWidget._sketch_cancel_tool, viewport
    )
    event = SimpleNamespace(key=lambda: Qt.Key_Escape, accept=Mock())

    Viewport3DWidget.keyPressEvent(viewport, event)

    assert engine.tool is None
    assert engine.entities == []
    event.accept.assert_called_once_with()


def test_right_click_callback_only_suppresses_pan_when_consumed():
    style = EMInteractorStyle()
    interactor = viewport_module.vtk.vtkRenderWindowInteractor()
    style.SetInteractor(interactor)
    interactor.SetEventPosition(10, 20)

    style.right_press_callback = lambda _x, _y: True
    style._on_right_press(None, None)
    assert not style._panning

    style.right_press_callback = lambda _x, _y: False
    style._on_right_press(None, None)
    assert style._panning


def test_sketch_click_selects_region_without_disabling_entity_delete():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_circle((0, 0), 5)
    inner_ref = engine.add_circle((0, 0), 2)
    viewport = _viewport_stub(engine)
    viewport._uv_from_screen = lambda _sx, _sy, **_kwargs: (3.0, 0.0)
    viewport._sketch_left_press = MethodType(Viewport3DWidget._sketch_left_press, viewport)
    viewport._sketch_delete_selected = MethodType(Viewport3DWidget._sketch_delete_selected, viewport)

    Viewport3DWidget._sketch_left_press(viewport, 0, 0)
    assert engine.operation_profile()[0][0][0] == pytest.approx(5.0)
    assert engine.operation_profile()[1][0][0] == pytest.approx(2.0)

    viewport._uv_from_screen = lambda _sx, _sy, **_kwargs: (2.0, 0.0)
    Viewport3DWidget._sketch_left_press(viewport, 0, 0)
    assert engine.selected_entity_id == inner_ref.entity_id
    Viewport3DWidget._sketch_delete_selected(viewport)
    assert all(entity.entity_id != inner_ref.entity_id for entity in engine.entities)


def test_ctrl_click_toggles_multiple_sketch_regions():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_rectangle((0, 0), (2, 2))
    engine.add_rectangle((4, 0), (6, 2))
    viewport = _viewport_stub(engine)
    viewport._uv_from_screen = lambda sx, _sy, **_kwargs: (1.0 if sx == 1 else 5.0, 1.0)
    viewport._sketch_left_press = MethodType(Viewport3DWidget._sketch_left_press, viewport)

    Viewport3DWidget._sketch_left_press(viewport, 1, 0)
    Viewport3DWidget._sketch_left_press(viewport, 5, 0, ctrl=True)
    assert len(engine.selected_region_profiles()) == 2
    Viewport3DWidget._sketch_left_press(viewport, 1, 0, ctrl=True)
    assert len(engine.selected_region_profiles()) == 1
    assert engine.operation_regions()[0][0][0] == (4.0, 0.0)


def test_sketch_region_pick_uses_raw_uv_not_grid_snap():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_circle((0, 0), 50)
    engine.add_circle((0, 0), 20)
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_vertex_snap_enabled=True,
        _grid_spacing=10.0,
        _ray_plane_intersect=lambda *_args: (32.5, 0.0, 0.0),
    )

    uv = Viewport3DWidget._uv_from_screen(viewport, 10, 10, snap=False)

    assert uv == pytest.approx((0.0, 32.5))


def test_sketch_entity_pick_tolerance_stays_inside_narrow_ring_region():
    viewport = SimpleNamespace(
        _grid_spacing=10.0,
        _sketch_engine=SketchEngine((0, 0, 0), (0, 0, 1)),
        _ray_plane_intersect=lambda sx, *_args: (3.0 + sx / 12.0, 0.0, 0.0),
    )

    tolerance = Viewport3DWidget._sketch_entity_pick_tolerance(
        viewport, 0, 0, (3.0, 0.0)
    )

    assert tolerance == pytest.approx(0.5)


def test_extrude_and_cut_requests_emit_selected_nested_profile(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_circle((0, 0), 5)
    engine.add_circle((0, 0), 2)
    assert engine.select_region_at_uv((3, 0))
    expected = engine.operation_profile()
    extrude_viewport = _viewport_stub(engine)
    extrude_viewport.sketch_extrude_requested = _Signal()
    extrude_viewport._request_extrude_options = lambda: (4.0, "Reverse", True)
    extrude_viewport.exit_sketch = Mock()
    Viewport3DWidget._sketch_request_extrude(extrude_viewport)

    monkeypatch.setattr(
        viewport_module.QInputDialog, "getDouble",
        lambda *_args, **_kwargs: (4.0, True),
    )
    cut_viewport = _viewport_stub(engine)
    cut_viewport.scene = SimpleNamespace(selection=[object()])
    cut_viewport.sketch_cut_requested = _Signal()
    cut_viewport.exit_sketch = Mock()
    Viewport3DWidget._sketch_request_extruded_cut(cut_viewport)

    assert extrude_viewport.sketch_extrude_requested.values[0][0] == expected
    assert extrude_viewport.sketch_extrude_requested.values[0][4:] == ("Reverse", True)
    assert cut_viewport.sketch_cut_requested.values[0][0] == expected


def test_extrude_requests_all_ctrl_selected_disjoint_regions(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_rectangle((0, 0), (2, 2))
    engine.add_rectangle((4, 0), (6, 2))
    engine.select_region_at_uv((1, 1))
    engine.select_region_at_uv((5, 1), additive=True, toggle=True)
    expected = engine.operation_regions()
    viewport = _viewport_stub(engine)
    viewport.sketch_extrude_requested = _Signal()
    viewport._request_extrude_options = lambda: (4.0, "Normal", False)
    viewport.exit_sketch = Mock()
    Viewport3DWidget._sketch_request_extrude(viewport)

    assert viewport.sketch_extrude_requested.values[0][0] == expected


def test_extrude_options_dialog_returns_direction_and_symmetric_choice():
    application = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent._units = "mm"

    def choose_options():
        dialog = next(
            widget for widget in application.topLevelWidgets()
            if widget.windowTitle() == "Extrude"
        )
        dialog.findChild(viewport_module.QDoubleSpinBox).setValue(12.5)
        direction = dialog.findChild(viewport_module.QComboBox)
        direction.setCurrentIndex(direction.findData("Reverse"))
        dialog.findChild(viewport_module.QCheckBox).setChecked(True)
        assert not direction.isEnabled()
        dialog.accept()

    QTimer.singleShot(0, choose_options)
    assert Viewport3DWidget._request_extrude_options(parent) == (12.5, "Reverse", True)


def _revolve_viewport(engine):
    viewport = _viewport_stub(engine)
    viewport._sketch_revolve_axis_segments = Viewport3DWidget._sketch_revolve_axis_segments
    viewport._sketch_revolve_segments_match = Viewport3DWidget._sketch_revolve_segments_match
    viewport._sketch_begin_revolve = MethodType(
        Viewport3DWidget._sketch_begin_revolve, viewport
    )
    viewport._sketch_left_press = MethodType(
        Viewport3DWidget._sketch_left_press, viewport
    )
    viewport._uv_from_screen = lambda _sx, _sy, **_kwargs: viewport.pick_uv
    viewport._world_from_uv = lambda u, v: (u + 10.0, v + 20.0, 30.0)
    viewport.sketch_revolve_requested = _Signal()
    viewport.exit_sketch = Mock()
    return viewport


@pytest.mark.parametrize("shape", ["open", "single-closed", "ambiguous"])
def test_revolve_requires_explicit_closed_region_selection(shape):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    if shape == "open":
        engine.add_polyline([(0, 0), (4, 0), (4, 3)])
    elif shape == "single-closed":
        engine.add_circle((0, 0), 5)
    else:
        engine.add_circle((0, 0), 5)
        engine.add_circle((10, 0), 3)
    viewport = _revolve_viewport(engine)

    Viewport3DWidget._sketch_begin_revolve(viewport)

    assert not viewport._sketch_axis_pick_mode
    assert "Select a closed sketch region" in viewport.status_message.values[-1][0]
    assert not viewport._refresh_sketch_overlay.called


def test_revolve_emits_selected_nested_profile_and_construction_axis(monkeypatch):
    engine = SketchEngine((1, 2, 3), (0, 0, 1))
    engine.add_circle((0, 0), 5)
    engine.add_circle((0, 0), 2)
    engine.add_line((0, -6), (0, 6), construction=True)
    assert engine.select_region_at_uv((3, 0))
    expected_profile = engine.operation_profile()
    viewport = _revolve_viewport(engine)
    viewport.pick_uv = (0, 6)
    highlighted_axes = []
    viewport._refresh_sketch_overlay.side_effect = lambda: highlighted_axes.append(
        viewport._sketch_axis_highlight
    )
    monkeypatch.setattr(
        viewport_module.QInputDialog, "getDouble",
        lambda *_args, **_kwargs: (270.0, True),
    )

    Viewport3DWidget._sketch_begin_revolve(viewport)
    Viewport3DWidget._sketch_left_press(viewport, 0, 0)

    profile, angle, axis_start, axis_end, origin, normal = (
        viewport.sketch_revolve_requested.values[0]
    )
    assert profile == expected_profile
    assert isinstance(profile[0][0], (list, tuple))
    assert angle == 270.0
    assert axis_start == pytest.approx((10.0, 14.0, 30.0))
    assert axis_end == pytest.approx((10.0, 26.0, 30.0))
    assert origin == (1.0, 2.0, 3.0)
    assert normal == (0.0, 0.0, 1.0)
    assert ((0, -6), (0, 6)) in highlighted_axes
    viewport.exit_sketch.assert_called_once_with(commit=True)


def test_revolve_rejects_region_boundary_axis_when_separate_axis_exists(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_polyline([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
    engine.add_line((10, 0), (10, 4), construction=True)
    assert engine.select_region_at_uv((2, 2))
    viewport = _revolve_viewport(engine)
    viewport.pick_uv = (2, 0)
    monkeypatch.setattr(
        viewport_module.QInputDialog, "getDouble",
        lambda *_args, **_kwargs: pytest.fail("invalid boundary axis opened angle dialog"),
    )

    Viewport3DWidget._sketch_begin_revolve(viewport)
    Viewport3DWidget._sketch_left_press(viewport, 0, 0)

    assert viewport._sketch_axis_pick_mode
    assert not viewport.sketch_revolve_requested.values
    assert "bounds the selected region" in viewport.status_message.values[-1][0]


def test_revolve_allows_boundary_axis_when_no_separate_line_exists(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_polyline([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)])
    assert engine.select_region_at_uv((2, 2))
    viewport = _revolve_viewport(engine)
    viewport.pick_uv = (2, 0)
    monkeypatch.setattr(
        viewport_module.QInputDialog, "getDouble",
        lambda *_args, **_kwargs: (180.0, True),
    )

    Viewport3DWidget._sketch_begin_revolve(viewport)
    Viewport3DWidget._sketch_left_press(viewport, 0, 0)

    assert len(viewport.sketch_revolve_requested.values) == 1
    assert viewport.sketch_revolve_requested.values[0][0] == engine.operation_profile()


def test_sketch_overlay_fills_selected_region_with_configured_selection_color(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_circle((0, 0), 5)
    engine.add_circle((0, 0), 2)
    engine.add_line((0, -6), (0, 6), construction=True)
    assert engine.select_region_at_uv((3, 0))
    actors = []
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_axis_highlight=((0, -6), (0, 6)),
        _sketch_lines_actor=None,
        _sketch_construction_actor=None,
        _sketch_hi_actor=None,
        _sketch_region_actor=None,
        _sketch_preview_actor=None,
        _sketch_dimension_actors=[],
        _renderer=SimpleNamespace(AddActor=Mock(), RemoveActor=Mock()),
        _make_line_actor=lambda poly, color, width, dashed=False: (
            actors.append((poly, color, width)) or object()
        ),
        _world_from_uv=lambda u, v: (u, v, 0),
        _refresh_sketch_dimensions=Mock(),
        _refresh_sketch_preview=Mock(),
        _render=Mock(),
    )
    viewport._sketch_uv_path_polydata = MethodType(
        Viewport3DWidget._sketch_uv_path_polydata, viewport
    )

    Viewport3DWidget._refresh_sketch_overlay(viewport)

    axis_highlights = [
        poly for poly, color, _width in actors if color == (1.0, 0.85, 0.0)
    ]
    assert axis_highlights
    assert any(poly.GetNumberOfCells() > 0 for poly in axis_highlights)
    monkeypatch.setattr(viewport_module.scene_objects, "SELECTION_COLOR", (0.12, 0.34, 0.56))
    Viewport3DWidget._refresh_sketch_overlay(viewport)
    region_actor = viewport._sketch_region_actor
    assert region_actor.GetMapper().GetInput().GetNumberOfCells() > 0
    assert region_actor.GetProperty().GetColor() == pytest.approx((0.12, 0.34, 0.56))
    assert region_actor.GetProperty().GetOpacity() == pytest.approx(0.48)
    assert not region_actor.GetPickable()


def test_center_start_end_arc_uses_center_and_start_radius():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_tool("center_arc")
    viewport = _viewport_stub(engine)

    for point in ((0, 0), (2, 0), (0, 5)):
        Viewport3DWidget._sketch_center_arc_click(viewport, point)

    arc = engine.entities[-1]
    assert arc[0] == "arc"
    assert arc[1] == pytest.approx((2, 0))
    assert arc[2] == pytest.approx((0, 2))
    assert arc[3] == pytest.approx((2**0.5, 2**0.5))


def test_expression_dimension_applies_resolved_value_and_serializes_refs(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    entity_ref = engine.add_line((0, 0), (10, 0))
    refs = [engine.point_ref(entity_ref, 0), engine.point_ref(entity_ref, 1)]
    viewport = _viewport_stub(engine)
    viewport._sketch_dimension_resolver = lambda expression: {"width": 4.0}[expression]
    monkeypatch.setattr(viewport_module.QInputDialog, "getText", lambda *_args, **_kwargs: ("width", True))

    Viewport3DWidget._sketch_create_dimension(viewport, "linear", refs)

    dimension = engine.dimensions[0]
    assert dimension.expression == "width"
    assert dimension.value == pytest.approx(10.0)
    assert dimension.resolved_value == pytest.approx(4.0)
    assert engine.entities[0][2] == pytest.approx((4.0, 0.0))
    serialized = engine.to_dict()["dimensions"][0]
    assert serialized["expression"] == "width"
    assert serialized["resolved_value"] == pytest.approx(4.0)
    assert "width = 4.000 mm" in Viewport3DWidget._sketch_dimension_label(viewport, dimension)[0]


def test_angular_dimension_keeps_requested_target_without_moving_geometry(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    first = engine.add_line((0, 0), (2, 0))
    second = engine.add_line((0, 0), (2, 2))
    original = tuple(engine.entities[1][2])
    viewport = _viewport_stub(engine)
    monkeypatch.setattr(viewport_module.QInputDialog, "getText", lambda *_args, **_kwargs: ("90", True))

    Viewport3DWidget._sketch_create_dimension(
        viewport, "angular", [first, second]
    )

    dimension = engine.dimensions[0]
    assert dimension.value == pytest.approx(45.0)
    assert dimension.resolved_value == pytest.approx(90.0)
    assert engine.entities[1][2] == original
    assert "not driven" in Viewport3DWidget._sketch_dimension_label(viewport, dimension)[0]


def test_dimension_pick_uses_external_edge_alignment_then_stable_sketch_ref():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    entity_ref = engine.add_line((0, 0), (10, 0))
    viewport = _viewport_stub(engine)
    viewport._sketch_dimension_kind = "aligned"
    viewport._ray_plane_intersect = lambda *_args: (0, 5, 0)
    viewport._snap_to_visible_geometry = lambda _sx, _sy, _fallback, snap_mode: (
        ((0, 5, 3), "edge") if snap_mode == "edge" else None
    )
    viewport._project_point_to_draw_plane = lambda point: (point[0], point[1], 0)
    viewport._sketch_dimension_ref_at_uv = MethodType(
        Viewport3DWidget._sketch_dimension_ref_at_uv, viewport
    )

    Viewport3DWidget._sketch_dimension_left_press(viewport, 200, 150)

    picked = viewport._sketch_dimension_picks[0]
    assert picked.entity_id == entity_ref.entity_id
    assert any("aligned to external edge" in value[0] for value in viewport.status_message.values)


def test_extruded_cut_uses_selected_base_and_records_sketch_definition(monkeypatch):
    class _ExtrudedObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.name = kwargs["name"]
            self.sketch_definition = None

    class _MeshObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def refresh_appearance(self):
            pass

    monkeypatch.setattr(em_objects, "ExtrudedObject", _ExtrudedObject)
    monkeypatch.setattr(em_objects, "MeshObject", _MeshObject)
    boolean_calls = []
    monkeypatch.setattr(boolean_ops, "boolean_many", lambda operation, objects: (
        boolean_calls.append((operation, objects)) or "cut-mesh"
    ))

    base = SimpleNamespace(name="Base", material="PEC")

    class _Scene:
        objects = [base]
        selection = [base]

        def add_object(self, obj):
            self.objects.append(obj)

        def remove_object(self, obj):
            self.objects.remove(obj)

        def select(self, obj):
            self.selection = [obj]

    scene = _Scene()
    viewport = SimpleNamespace(
        scene=scene,
        sketch_definition=lambda: {"dimensions": [{"expression": "cut_depth", "resolved_value": 5}]},
        object_selected=_Signal(),
        selection_changed=_Signal(),
        scene_changed=_Signal(),
        _render=Mock(),
    )
    window = SimpleNamespace(
        _viewport=viewport,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _serialize_object_snapshot=lambda obj: {"name": obj.name},
        _is_plate_role_object=lambda _obj: False,
        _refresh_materials=Mock(),
    )

    MainWindow._on_extruded_cut_requested(
        window, [(0, 0), (1, 0), (0, 1)], 5.0, (0, 0, 0), (0, 0, 1)
    )

    assert boolean_calls[0][0] == "cut"
    assert boolean_calls[0][1][0] is base
    result = scene.selection[0]
    assert result.polydata == "cut-mesh"
    assert result.sketch_definition["dimensions"][0]["expression"] == "cut_depth"
    assert scene.objects == [result]


def test_sketch_toolbar_uses_available_freecad_icon_assets():
    names = (
        "Part_Extrude", "Part_Revolve", "Part_Box", "Part_Cylinder", "Part_Cut", "Std_Tool1", "Std_Tool2",
        "Std_Tool3", "Std_Tool4", "Std_Tool5", "Std_Tool6", "Std_Tool7",
        "Std_Tool8", "Std_Axis", "Std_Point", "Tree_Dimension",
        "view-measurement", "umf-measurement", "LinkArray", "Std_Plane",
        "edit_Cancel",
    )

    assert all(not _icon(name).isNull() for name in names)


def test_toolbar_groups_use_icon_only_buttons_and_size_for_all_columns():
    application = QApplication.instance() or QApplication([])
    toolbar = QToolBar()

    two_d_names = (
        "Line", "Polyline", "Corner Rectangle", "Center Rectangle", "Construction",
        "Vertex Snap", "Circle", "3-Point Arc", "Center Arc", "Tangent Arc",
    )
    two_d_actions = [QAction(name, toolbar) for name in two_d_names]
    for action in two_d_actions:
        action.setToolTip(f"Help for {action.text()}")
    two_d_actions[4].setCheckable(True)
    two_d_actions[4].setChecked(True)
    _add_toolbar_group(toolbar, "2D", two_d_actions, columns=5)

    zoom_names = ("Fit All", "Fit Selection", "Isometric View", "Projection")
    zoom_actions = [QAction(name, toolbar) for name in zoom_names]
    _add_toolbar_group(toolbar, "View", zoom_actions, columns=4)

    default_actions = [QAction(f"Main {index}", toolbar) for index in range(3)]
    _add_toolbar_group(toolbar, "Main", default_actions, columns=3)

    groups = {}
    for toolbar_action in toolbar.actions():
        widget = toolbar.widgetForAction(toolbar_action)
        if widget is None or widget.layout() is None or widget.layout().count() == 0:
            continue
        caption = widget.layout().itemAt(0).widget()
        if isinstance(caption, QLabel):
            groups[caption.text()] = widget
    two_d_group = groups["2D"]
    two_d_grid = two_d_group.layout().itemAt(1).layout()
    two_d_buttons = two_d_group.findChildren(QToolButton)
    assert two_d_grid.rowCount() == 2
    assert two_d_grid.columnCount() == 5
    assert [button.defaultAction().text() for button in two_d_buttons] == list(two_d_names)
    assert all(button.toolButtonStyle() == Qt.ToolButtonIconOnly for button in two_d_buttons)
    assert all(button.toolTip() == f"Help for {button.text()}" for button in two_d_buttons)
    assert two_d_actions[4].isChecked()
    assert two_d_group.width() >= two_d_grid.sizeHint().width() + 20

    zoom_group = groups["View"]
    zoom_grid = zoom_group.layout().itemAt(1).layout()
    assert zoom_grid.rowCount() == 1
    assert zoom_grid.columnCount() == 4
    assert [button.defaultAction().text() for button in zoom_group.findChildren(QToolButton)] == list(zoom_names)

    main_group = groups["Main"]
    assert main_group.width() == 122
    assert all(
        button.toolButtonStyle() == Qt.ToolButtonIconOnly
        and button.size().width() == 30
        and button.size().height() == 27
        for button in main_group.findChildren(QToolButton)
    )
    assert application is not None


def test_main_window_builds_icon_only_sketch_tools_and_reuses_main_zoom_actions():
    application = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.resize(640, 700)
    window._viewport = Mock()
    window._viewport.is_parallel_projection.return_value = False
    for name in (
        "_start_draw", "_open_region_pml_wizard", "_open_sketch",
        "_create_plate_from_face", "_bool_cut", "_bool_fuse", "_bool_common",
        "_scale_selected_objects", "_move_selection_to_plane_origin",
        "_create_object_pattern", "_copy_selected_object_by_vertices",
        "_bool_dissolve", "_import_step",
        "_open_simulation_window", "_on_check_simulation", "_on_selection_mode_changed",
        "_sync_projection_action", "_open_measure_tool", "_open_project_parameters",
        "_recompute_model",
    ):
        setattr(window, name, Mock())

    MainWindow._build_toolbar(window)
    window.show()
    window._main_toolbar.hide()
    window._workspace_toolbar.hide()
    window._sketch_context_toolbar.show()
    application.processEvents()

    def groups_by_title(toolbar):
        groups = {}
        for toolbar_action in toolbar.actions():
            widget = toolbar.widgetForAction(toolbar_action)
            if widget is None:
                continue
            title = widget.property("toolbarGroupTitle")
            if title:
                groups[title] = widget
        return groups

    main_groups = groups_by_title(window._main_toolbar)
    main_groups.update(groups_by_title(window._workspace_toolbar))
    sketch_groups = groups_by_title(window._sketch_context_toolbar)
    assert max(
        window._main_toolbar.sizeHint().width(),
        window._workspace_toolbar.sizeHint().width(),
    ) < 900
    main_3d_buttons = main_groups["3D"].findChildren(QToolButton)
    assert [button.defaultAction().text() for button in main_3d_buttons] == [
        "Box", "Cylinder", "Cone", "Sphere", "Open Region / PML", "Import STEP",
    ]
    simulation_buttons = main_groups["Simulation"].findChildren(QToolButton)
    assert [button.defaultAction().text() for button in simulation_buttons] == [
        "Check Simulation", "Play",
    ]
    tools_buttons = main_groups["Tools"].findChildren(QToolButton)
    assert [button.defaultAction().text() for button in tools_buttons] == ["Measure", "Parameters"]
    assert all(not button.defaultAction().icon().isNull() for button in tools_buttons)
    main_2d_buttons = main_groups["2D"].findChildren(QToolButton)
    assert [button.defaultAction().text() for button in main_2d_buttons] == [
        "Sketch", "Planar", "Plate from Face/Edge", "Circular Plate",
    ]
    circular_plate_action = main_2d_buttons[-1].defaultAction()
    assert circular_plate_action.icon().pixmap(QSize(18, 18)).toImage() == (
        _icon("Sketcher_CreateCircle").pixmap(QSize(18, 18)).toImage()
    )
    circular_plate_action.trigger()
    window._start_draw.assert_called_with("circular_plate")
    assert set(sketch_groups) == {"3D", "2D", "Dimensions", "View", "Edit"}
    two_d_grid = sketch_groups["2D"].layout()
    assert two_d_grid.rowCount() == 2
    assert two_d_grid.columnCount() == 5
    assert len(sketch_groups["2D"].findChildren(QToolButton)) == 9
    edit_buttons = sketch_groups["Edit"].findChildren(QToolButton)
    assert [button.defaultAction().text() for button in edit_buttons] == [
        "Select", "Delete", "Exit Sketch",
    ]
    expected_actions = {
        "3D": ["Extrude", "Revolve", "Extruded Cut"],
        "2D": [
            "Line", "Polyline", "Corner Rectangle", "Center Rectangle", "Construction",
            "Vertex Snap", "Circle", "3-Point Arc", "Center Arc",
        ],
        "Dimensions": ["Linear", "Angular", "Radius", "Diameter", "Aligned"],
        "View": ["Fit All", "Fit Selection", "Isometric View", "Projection", ""],
        "Edit": ["Select", "Delete", "Exit Sketch"],
    }
    for title, group in sketch_groups.items():
        buttons = group.findChildren(QToolButton)
        assert [button.defaultAction().text() for button in buttons] == expected_actions[title]
        assert group.isVisible()
        assert group.accessibleName() == f"{title} tools"
        assert not group.findChildren(QLabel)
        assert all(button.isVisible() for button in buttons)
        assert all(button.toolButtonStyle() == Qt.ToolButtonIconOnly for button in buttons)
        assert all(button.accessibleName() == button.defaultAction().text() for button in buttons)
        assert all(button.toolTip() for button in buttons)
        assert all(not button.defaultAction().icon().isNull() for button in buttons)
    assert all(
        button.toolButtonStyle() == Qt.ToolButtonIconOnly
        for group in (*main_groups.values(), *sketch_groups.values())
        for button in group.findChildren(QToolButton)
    )
    assert all(
        button.iconSize() == window._sketch_context_toolbar.iconSize()
        for group in sketch_groups.values()
        for button in group.findChildren(QToolButton)
    )
    toolbar_rect = window._sketch_context_toolbar.contentsRect()
    assert all(
        group.geometry().left() >= toolbar_rect.left()
        and group.geometry().right() <= toolbar_rect.right()
        for group in sketch_groups.values()
    )

    dimensions_grid = sketch_groups["Dimensions"].layout()
    assert dimensions_grid.rowCount() == 2
    assert dimensions_grid.columnCount() == 3

    expected_zoom = ("Fit All", "Fit Selection", "Isometric View", "Projection", "")
    main_zoom = [button.defaultAction() for button in main_groups["View"].findChildren(QToolButton)]
    sketch_zoom = [button.defaultAction() for button in sketch_groups["View"].findChildren(QToolButton)]
    assert tuple(action.text() for action in sketch_zoom) == expected_zoom
    assert sketch_zoom == main_zoom
    assert all(action.toolTip() for action in sketch_zoom)
    projection_action = sketch_zoom[3]
    assert projection_action.isCheckable()
    assert not projection_action.isChecked()
    assert not projection_action.icon().isNull()
    projection_action.setChecked(True)
    window._viewport.set_parallel_projection.assert_called_with(True)
    assert not window._sketch_context_actions["Extrude"].icon().isNull()
    assert not window._sketch_context_actions["Revolve"].icon().isNull()
    for action_name, asset_name in (("Extrude", "Part_Extrude"), ("Revolve", "Part_Revolve")):
        assert (
            window._sketch_context_actions[action_name].icon().pixmap(QSize(18, 18)).toImage()
            == _icon(asset_name).pixmap(QSize(18, 18)).toImage()
        )
    for icon_name in (
        "Sketcher_CreateLine", "Sketcher_CreatePolyline", "Sketcher_CreateRectangle",
        "Sketcher_CreateRectangle_Center", "Sketcher_CreateCircle", "Sketcher_CreateArc",
        "Sketcher_Create3PointArc", "Sketcher_ToggleConstruction",
        "Sketcher_ToggleConstruction_Constr", "Constraint_Dimension", "Constraint_Length",
        "Constraint_InternalAngle", "Constraint_Radius", "Constraint_Radiam",
        "Sketcher_LeaveSketch",
    ):
        assert not _icon(icon_name).isNull(), icon_name
    assert window._sketch_context_actions["Construction"].isCheckable()
    assert window._sketch_context_actions["Vertex Snap"].isChecked()
    assert not window._sketch_context_actions["Exit Sketch"].icon().isNull()
    construction_action = window._sketch_context_actions["Construction"]
    normal_icon = construction_action.icon().cacheKey()
    construction_action.setChecked(True)
    assert construction_action.icon().cacheKey() != normal_icon
    sketch_zoom[1].trigger()
    window._viewport.fit_selection.assert_called_once()
    assert application is not None


def test_materials_panel_cannot_collapse_at_narrow_window_width(monkeypatch):
    application = QApplication.instance() or QApplication([])

    class ViewportStub(QWidget):
        def set_plane_triad_size(self, _size):
            pass

        def set_adaptive_grid(self, _enabled, _margin):
            pass

    def make_body_properties():
        widget = QWidget()
        widget.has_content = False
        widget.content_changed = SimpleNamespace(connect=Mock())
        return widget

    monkeypatch.setattr(main_window_module, "ProjectTreeWidget", QWidget)
    monkeypatch.setattr(main_window_module, "BodyPropertiesWidget", make_body_properties)
    monkeypatch.setattr(main_window_module, "Viewport3DWidget", ViewportStub)
    monkeypatch.setattr(main_window_module, "MaterialsWidget", QWidget)
    monkeypatch.setattr(main_window_module, "InfoBarWidget", QWidget)

    window = QMainWindow()
    window._workspace_windows = {}
    window._plot_subwindows = {}
    window._plane_triad_size = 25.0
    window._adaptive_grid_enabled = False
    window._adaptive_grid_margin = 20.0
    window._on_workspace_window_activated = Mock()
    window._on_workspace_subwindow_closed = Mock()
    window._update_left_splitter_layout = Mock()
    window._schedule_left_splitter_layout = Mock()
    MainWindow._build_ui(window)
    window.resize(1280, 720)
    window.show()
    application.processEvents()

    assert window._main_splitter.isCollapsible(2) is False
    window._main_splitter.setSizes([360, 900, 0])
    application.processEvents()
    assert window._main_splitter.sizes()[2] >= 190
    window.close()


def test_circular_plate_uses_grid_snapped_radius_and_active_plane():
    viewport = SimpleNamespace(
        _draw_mode="circular_plate",
        _draw_state=0,
        _draw_pts=[],
        _draw_material="COPPER",
        _grid_spacing=0.5,
        _units="mm",
        _selection_mode="object",
        _last_drawing_snap_kind="grid",
        _snap_to_visible_geometry=Mock(return_value=None),
        _ray_plane_intersect=Mock(side_effect=[(1.24, 2.0, 3.24), (1.49, 2.0, 3.49)]),
        _active_draw_origin_normal=lambda: ((1.0, 2.0, 3.0), (0.0, 1.0, 0.0)),
        _project_point_to_draw_plane=lambda point: (point[0], 2.0, point[2]),
        _finish_object=Mock(),
        status_message=_Signal(),
    )
    viewport._snap = MethodType(Viewport3DWidget._snap, viewport)
    viewport._drawing_snap_point = MethodType(
        Viewport3DWidget._drawing_snap_point, viewport
    )
    viewport._circular_plate_radius = MethodType(
        Viewport3DWidget._circular_plate_radius, viewport
    )
    viewport._fsm_circular_plate_click = MethodType(
        Viewport3DWidget._fsm_circular_plate_click, viewport
    )

    Viewport3DWidget._drawing_click(viewport, 10, 20)
    Viewport3DWidget._drawing_click(viewport, 30, 40)

    viewport._finish_object.assert_called_once()
    obj = viewport._finish_object.call_args.args[0]
    assert obj.plate_role is True
    assert obj.material == "COPPER"
    assert obj.circular_plate is True
    assert obj.actor.GetMapper().GetInput().GetNumberOfPolys() > 1
    assert obj.actor.GetBounds() == pytest.approx((0.5, 1.5, 1.9975, 2.0025, 2.5, 3.5))
    assert viewport._draw_pts[0] == pytest.approx((1.0, 2.0, 3.0))
    assert viewport._draw_pts[-1] == pytest.approx((1.3535533906, 2.0, 3.3535533906))
    assert obj.get_parameters()["Thickness"] == pytest.approx(0.005)


def test_circular_plate_dimensions_edit_geometry_and_round_trip():
    plate = em_objects.MeshObject("CircularPlate", plate_role=True)
    plate.set_parameters({
        "CircularPlate": True,
        "CenterX": 1.0,
        "CenterY": 2.0,
        "CenterZ": 3.0,
        "Radius": 2.0,
        "Thickness": 0.2,
        "CircularPlateNormalX": 0.0,
        "CircularPlateNormalY": 0.0,
        "CircularPlateNormalZ": 1.0,
    })

    plate.set_parameters({**plate.get_parameters(), "CenterX": 4.0, "Radius": 3.0, "Thickness": 0.4})
    parameters = plate.get_parameters()
    restored = em_objects.MeshObject(
        "Restored", plate.actor.GetMapper().GetInput(), plate_role=True
    )
    restored.set_parameters(parameters)

    assert parameters["CenterX"] == pytest.approx(4.0)
    assert parameters["CenterY"] == pytest.approx(2.0)
    assert parameters["CenterZ"] == pytest.approx(3.0)
    assert parameters["Radius"] == pytest.approx(3.0)
    assert parameters["Thickness"] == pytest.approx(0.4)
    assert restored.circular_plate is True
    assert restored.actor.GetBounds() == pytest.approx((1.0, 7.0, -1.0, 5.0, 2.8, 3.2))


def test_circular_plate_properties_expose_only_center_radius_and_thickness():
    application = QApplication.instance() or QApplication([])
    plate = em_objects.MeshObject("CircularPlate", plate_role=True)
    plate.set_parameters({
        "CircularPlate": True,
        "CenterX": 1.0,
        "CenterY": 2.0,
        "CenterZ": 3.0,
        "Radius": 2.0,
        "Thickness": 0.2,
    })
    widget = BodyPropertiesWidget()
    widget.set_object(plate)

    labels = [widget._table.item(row, 0).text() for row in range(widget._table.rowCount())]
    parameters, _formulas = widget._collect_params()

    assert {"CenterX", "CenterY", "CenterZ", "Radius", "Thickness"}.issubset(labels)
    assert not {"CircularPlate", "CircularPlateNormalX", "CircularPlateNormalY", "CircularPlateNormalZ"}.intersection(labels)
    assert {"CenterX", "CenterY", "CenterZ", "Radius", "Thickness"}.issubset(parameters)
    assert application is not None


def test_editing_cut_reuses_original_base_and_updates_same_result(monkeypatch):
    class _ExtrudedObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _MeshObject:
        pass

    monkeypatch.setattr(em_objects, "ExtrudedObject", _ExtrudedObject)
    monkeypatch.setattr(em_objects, "MeshObject", _MeshObject)
    base = SimpleNamespace(name="Base", material="PEC")
    previous_tool = SimpleNamespace(name="CutTool")
    target = _MeshObject()
    target.name = "CutResult"
    target.boolean_op = "cut"
    target.source_objects = [base, previous_tool]
    target.boolean_sources_data = []
    updated_definition = {"entities": [{"id": "edited-cut"}]}
    boolean_calls = []
    monkeypatch.setattr(
        boolean_ops, "boolean_many",
        lambda operation, sources: boolean_calls.append((operation, sources)) or "new mesh",
    )
    window = SimpleNamespace(
        _sketch_edit_target=target,
        _viewport=SimpleNamespace(
            sketch_definition=lambda: updated_definition,
            scene=SimpleNamespace(selection=[]),
        ),
        _replace_boolean_result_object=Mock(),
        _finish_sketch_feature_edit=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )

    MainWindow._on_extruded_cut_requested(
        window, [(0, 0), (3, 0), (0, 2)], 6, (1, 2, 3), (0, 0, 1)
    )

    operation, sources = boolean_calls[0]
    assert operation == "cut"
    assert sources[0] is base
    assert sources[1].name == "CutTool"
    assert sources[1].sketch_definition == updated_definition
    window._replace_boolean_result_object.assert_called_once_with(
        target, "new mesh", sources
    )
    assert target.sketch_definition == updated_definition
    window._finish_sketch_feature_edit.assert_called_once_with(target)


def test_invalid_extrusion_update_is_reported_without_mutating_feature():
    feature = em_objects.ExtrudedObject("Editable")
    previous_depth = feature._depth
    previous_definition = {"entities": [{"id": "unchanged"}]}
    feature.sketch_definition = previous_definition
    window = SimpleNamespace(
        _sketch_edit_target=feature,
        _viewport=SimpleNamespace(sketch_definition=lambda: {"entities": []}),
        _finish_sketch_feature_edit=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )

    MainWindow._on_extrude_requested(
        window, [(0, 0), (1, 0), (0, 1)], 0, (0, 0, 0), (0, 0, 1)
    )

    assert feature._depth == previous_depth
    assert feature.sketch_definition == previous_definition
    window._info_bar.set_info.assert_called_once()
    window._finish_sketch_feature_edit.assert_not_called()


def test_viewport_sketch_selection_dispatch_and_delete_key_remove_selected_entity():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    selected_ref = engine.add_line((0, 0), (4, 0))
    dimension = engine.add_dimension(
        "aligned", (engine.point_ref(selected_ref, 0), engine.point_ref(selected_ref, 1))
    )
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_axis_pick_mode=False,
        _sketch_axis_highlight=None,
        _sketch_dimension_kind=None,
        _sketch_dimension_picks=[],
        _grid_spacing=1.0,
            _uv_from_screen=lambda sx, sy, **_kwargs: (float(sx), float(sy)),
        _handle_pick_request=lambda *_args: False,
        _refresh_sketch_overlay=Mock(),
        setCursor=Mock(),
        status_message=_Signal(),
    )
    viewport._sketch_left_press = MethodType(Viewport3DWidget._sketch_left_press, viewport)
    viewport._sketch_set_selection_mode = MethodType(
        Viewport3DWidget._sketch_set_selection_mode, viewport
    )
    viewport._sketch_delete_selected = MethodType(
        Viewport3DWidget._sketch_delete_selected, viewport
    )

    engine.set_tool("line")
    engine.on_click((8, 8))
    Viewport3DWidget._sketch_set_selection_mode(viewport)
    assert engine.tool is None
    assert engine.pending == []

    Viewport3DWidget._on_left_press(viewport, 2, 0)
    assert engine.selected_entity_id == selected_ref.entity_id
    Viewport3DWidget._on_left_press(viewport, 20, 20)
    assert engine.selected_entity_id is None
    Viewport3DWidget._on_left_press(viewport, 2, 0)

    event = SimpleNamespace(key=lambda: Qt.Key_Delete, accept=Mock())
    Viewport3DWidget.keyPressEvent(viewport, event)

    event.accept.assert_called_once_with()
    assert engine.entities == []
    assert engine.dimensions == []
    assert dimension not in engine.dimensions


@pytest.mark.parametrize("key", (Qt.Key_Return, Qt.Key_Escape))
def test_viewport_enter_finishes_but_escape_cancels_polyline(key):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_tool("polyline")
    engine.on_click((0, 0))
    engine.on_click((2, 0))
    engine.on_click((2, 2))
    entity_id = engine.entity_ref(0).entity_id
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_axis_pick_mode=False,
        _sketch_axis_highlight=None,
        _sketch_dimension_kind=None,
        _sketch_dimension_picks=[],
        _refresh_sketch_overlay=Mock(),
        setCursor=Mock(),
        status_message=_Signal(),
    )
    viewport._sketch_set_selection_mode = MethodType(
        Viewport3DWidget._sketch_set_selection_mode, viewport
    )
    viewport._sketch_cancel_tool = MethodType(
        Viewport3DWidget._sketch_cancel_tool, viewport
    )
    event = SimpleNamespace(key=lambda: key, accept=Mock())

    Viewport3DWidget.keyPressEvent(viewport, event)

    event.accept.assert_called_once_with()
    assert engine.tool is None
    if key == Qt.Key_Return:
        assert engine.entities[0] == ("polyline", [(0, 0), (2, 0), (2, 2)])
        assert engine.entity_ref(0).entity_id == entity_id
    else:
        assert engine.entities == []