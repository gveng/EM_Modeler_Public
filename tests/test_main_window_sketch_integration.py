import os
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy

import pytest
import vtk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMainWindow, QToolButton

import em3d_modeler.ui.main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene.em_objects import BoxObject, ExtrudedObject, MeshObject, RevolvedObject


def _main_window_stub(viewport):
    return SimpleNamespace(
        _viewport=viewport,
        _planar_face_sketch_plane=MainWindow._planar_face_sketch_plane,
        _start_embedded_sketch=Mock(),
        _open_reference_plane_dialog=Mock(),
    )


def test_planar_face_pick_produces_centroid_and_unit_normal():
    plane = MainWindow._planar_face_sketch_plane({
        "points": [(2, 4, 1), (6, 4, 1), (6, 8, 1), (2, 8, 1)],
    })

    assert plane == ((4.0, 6.0, 1.0), (0.0, 0.0, 1.0))


def test_boolean_provenance_sync_only_serializes_changed_source():
    changed = SimpleNamespace(name="Changed")
    unchanged = SimpleNamespace(name="Unchanged")
    first_result = SimpleNamespace(
        boolean_op="cut",
        source_objects=[changed],
        boolean_sources_data=[{"name": "Changed", "version": 1}],
    )
    second_result = SimpleNamespace(
        boolean_op="cut",
        source_objects=[unchanged],
        boolean_sources_data=[{"name": "Unchanged", "version": 1}],
    )
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[first_result, second_result])),
        _serialize_object_snapshot=Mock(side_effect=lambda source, **_kwargs: {
            "name": source.name, "version": 2,
        }),
    )

    MainWindow._sync_boolean_provenance(window, changed)

    window._serialize_object_snapshot.assert_called_once_with(
        changed, include_mesh=True
    )
    assert first_result.boolean_sources_data == [{"name": "Changed", "version": 2}]
    assert second_result.boolean_sources_data == [{"name": "Unchanged", "version": 1}]


def test_boolean_provenance_reuses_mesh_when_only_appearance_changes():
    source = MeshObject("Source", vtk.vtkPolyData())
    window = SimpleNamespace(
        _viewport=SimpleNamespace(
            scene=SimpleNamespace(
                _polydata_to_json=lambda _polydata: {"points": [], "polys": []},
            ),
        ),
    )
    snapshot = MainWindow._serialize_object_snapshot(window, source)
    result = SimpleNamespace(
        boolean_op="cut",
        source_objects=[source],
        boolean_sources_data=[snapshot],
    )
    window._viewport.scene.objects = [result]
    serialize = Mock(
        side_effect=lambda obj, **kwargs: MainWindow._serialize_object_snapshot(
            window, obj, **kwargs
        ),
    )
    window._serialize_object_snapshot = serialize
    source.set_parameters({"Material": "Copper", "Opacity": 0.5})

    MainWindow._sync_boolean_provenance(window, source)

    serialize.assert_called_once_with(source, include_mesh=False)
    assert result.boolean_sources_data[0]["mesh"] is snapshot["mesh"]


def test_non_planar_face_pick_is_not_used_for_sketch():
    assert MainWindow._planar_face_sketch_plane({
        "points": [(0, 0, 0), (2, 0, 0), (2, 2, 0.01), (0, 2, 0)],
    }) is None


def test_sketch_uses_selected_planar_face_before_active_plane():
    face_pick = {"points": [(0, 0, 3), (4, 0, 3), (4, 2, 3), (0, 2, 3)]}
    active_plane = SimpleNamespace(origin=(0, 0, 0), normal=(0, 0, 1))
    viewport = SimpleNamespace(
        _selection_mode="face",
        _last_face_pick=face_pick,
        _sub_pick_actor=object(),
        scene=SimpleNamespace(active_plane=active_plane),
    )
    window = _main_window_stub(viewport)

    MainWindow._open_sketch(window)

    window._start_embedded_sketch.assert_called_once_with((2.0, 1.0, 3.0), (0.0, 0.0, 1.0))
    window._open_reference_plane_dialog.assert_not_called()


def test_sketch_uses_active_reference_plane_when_no_face_is_selected():
    active_plane = SimpleNamespace(origin=(1, 2, 3), normal=(0, 1, 0))
    viewport = SimpleNamespace(
        _selection_mode="face",
        _last_face_pick={"points": [(0, 0, 3), (4, 0, 3), (4, 2, 3)]},
        _sub_pick_actor=None,
        scene=SimpleNamespace(active_plane=active_plane),
    )
    window = _main_window_stub(viewport)

    MainWindow._open_sketch(window)

    window._start_embedded_sketch.assert_called_once_with((1, 2, 3), (0, 1, 0))
    window._open_reference_plane_dialog.assert_not_called()


def test_stale_face_record_without_marker_falls_back_to_active_plane():
    active_plane = SimpleNamespace(origin=(1, 2, 3), normal=(0, 1, 0))
    viewport = SimpleNamespace(
        _selection_mode="face",
        _last_face_pick={"points": [(0, 0, 3), (4, 0, 3), (4, 2, 3)]},
        _sub_pick_actor=None,
        scene=SimpleNamespace(active_plane=active_plane),
    )
    window = _main_window_stub(viewport)

    MainWindow._open_sketch(window)

    window._start_embedded_sketch.assert_called_once_with((1, 2, 3), (0, 1, 0))


def test_sketch_without_active_plane_opens_dialog_in_deferred_start_mode():
    viewport = SimpleNamespace(
        _selection_mode="object",
        _last_face_pick=None,
        scene=SimpleNamespace(active_plane=None),
    )
    window = _main_window_stub(viewport)

    MainWindow._open_sketch(window)

    window._open_reference_plane_dialog.assert_called_once_with(start_sketch=True)
    window._start_embedded_sketch.assert_not_called()


def test_defined_plane_starts_pending_viewport_sketch_after_activation():
    plane = SimpleNamespace(name="Picked face")

    class _Scene:
        reference_planes = []

        def add_reference_plane(self, _name, _origin, _normal, make_active):
            assert make_active is True
            self.reference_planes.append(plane)
            return plane

    viewport = SimpleNamespace(
        scene=_Scene(),
        set_reference_plane=Mock(),
        set_grid=Mock(),
    )
    window = SimpleNamespace(
        _viewport=viewport,
        _sketch_after_plane_defined=True,
        _workspace_size=200.0,
        _grid_spacing=10.0,
        _units="mm",
        _refresh_materials=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
        _start_embedded_sketch=Mock(),
    )

    MainWindow._on_plane_defined(window, (3, 4, 5), (0, 1, 0), "Picked face")

    viewport.set_reference_plane.assert_called_once_with((3, 4, 5), (0, 1, 0))
    window._start_embedded_sketch.assert_called_once_with((3, 4, 5), (0, 1, 0))
    assert window._sketch_after_plane_defined is False


def test_sketch_start_and_finish_switch_toolbar_visibility():
    class _Toolbar:
        def __init__(self, visible=True):
            self.visible = visible

        def setVisible(self, visible):
            self.visible = bool(visible)

        def hide(self):
            self.visible = False

    class _Viewport:
        def __init__(self):
            self._sketch_toolbar = _Toolbar()
            self.start_args = None

        def start_sketch(self, origin, normal):
            self.start_args = (origin, normal)
            self._sketch_toolbar.setVisible(True)

    viewport = _Viewport()
    main_toolbar = _Toolbar()
    sketch_toolbar = _Toolbar(visible=False)
    window = SimpleNamespace(
        _viewport=viewport,
        _main_toolbar=main_toolbar,
        _sketch_context_toolbar=sketch_toolbar,
    )

    MainWindow._start_embedded_sketch(window, (0, 0, 1), (0, 0, 1))

    assert viewport.start_args == ((0, 0, 1), (0, 0, 1))
    assert viewport._sketch_toolbar.visible is False
    assert main_toolbar.visible is False
    assert sketch_toolbar.visible is True

    MainWindow._on_viewport_sketch_finished(window)

    assert main_toolbar.visible is True
    assert sketch_toolbar.visible is False


def test_context_action_returns_keyboard_focus_to_viewport():
    callback = Mock()
    viewport = SimpleNamespace(setFocus=Mock())
    window = SimpleNamespace(_viewport=viewport)

    MainWindow._activate_sketch_toolbar_action(window, callback)

    callback.assert_called_once_with()
    viewport.setFocus.assert_called_once_with()


def test_sketch_tree_edit_restores_saved_definition():
    definition = SketchEngine((1, 2, 3), (0, 1, 0)).to_dict()
    feature = ExtrudedObject("Editable")
    feature.sketch_definition = definition
    feature.set_visible(True)
    other_feature = ExtrudedObject("Other")
    other_feature.set_visible(False)
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[feature, other_feature])),
        _start_embedded_sketch=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
        _sketch_context_toolbar=SimpleNamespace(setVisible=Mock()),
        _main_toolbar=SimpleNamespace(setVisible=Mock()),
    )

    MainWindow._edit_sketch_definition(window, feature)

    assert window._sketch_edit_target is feature
    assert not feature.is_visible()
    assert not other_feature.is_visible()
    window._start_embedded_sketch.assert_called_once_with(
        (1.0, 2.0, 3.0), (0.0, 1.0, 0.0), definition
    )

    MainWindow._on_viewport_sketch_finished(window)

    assert feature.is_visible()
    assert not other_feature.is_visible()
    assert window._sketch_edit_target is None


def test_sketch_toolbar_shows_select_delete_and_exit_actions():
    application = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._viewport = SimpleNamespace(
        _sketch_request_extrude=Mock(),
        _sketch_begin_revolve=Mock(),
        _sketch_request_extruded_cut=Mock(),
        _sketch_set_selection_mode=Mock(),
        _sketch_delete_selected=Mock(),
        _sketch_set_tool=Mock(),
        _sketch_set_rectangle_mode=Mock(),
        _sketch_set_construction_mode=Mock(),
        set_sketch_vertex_snap=Mock(),
        _sketch_begin_dimension=Mock(),
        fit_all=Mock(), fit_selection=Mock(), isometric_view=Mock(),
        is_parallel_projection=Mock(return_value=False),
        set_parallel_projection=Mock(),
        projection_changed=Mock(),
        exit_sketch=Mock(),
    )
    window._start_draw = Mock()
    window._open_region_pml_wizard = Mock()
    window._open_sketch = Mock()
    window._create_plate_from_face = Mock()
    window._bool_cut = Mock()
    window._bool_fuse = Mock()
    window._bool_common = Mock()
    window._scale_selected_objects = Mock()
    window._move_selection_to_plane_origin = Mock()
    window._create_object_pattern = Mock()
    window._bool_dissolve = Mock()
    window._import_step = Mock()
    window._open_simulation_window = Mock()
    window._on_check_simulation = Mock()
    window._on_selection_mode_changed = Mock()
    window._activate_sketch_toolbar_action = MethodType(
        MainWindow._activate_sketch_toolbar_action, window
    )
    window._viewport.setFocus = Mock()
    window._sync_projection_action = MethodType(
        MainWindow._sync_projection_action, window
    )

    MainWindow._build_toolbar(window)

    edit_group = next(
        widget for action in window._sketch_context_toolbar.actions()
        if (widget := window._sketch_context_toolbar.widgetForAction(action)) is not None
        and widget.property("toolbarGroupTitle") == "Edit"
    )
    actions = [button.defaultAction() for button in edit_group.findChildren(QToolButton)]
    assert [action.text() for action in actions] == ["Select", "Delete", "Exit Sketch"]
    assert all(not action.icon().isNull() for action in actions)
    actions[0].trigger()
    actions[1].trigger()
    actions[2].trigger()
    window._viewport._sketch_set_selection_mode.assert_called_once()
    window._viewport._sketch_delete_selected.assert_called_once()
    window._viewport.exit_sketch.assert_called_once_with(commit=False)
    assert application is not None


def test_extrude_request_updates_existing_feature_and_sketch_definition():
    feature = ExtrudedObject("Editable")
    viewport = SimpleNamespace(sketch_definition=lambda: {"entities": [{"id": "edited"}]})
    window = SimpleNamespace(
        _sketch_edit_target=feature,
        _viewport=viewport,
        _finish_sketch_feature_edit=Mock(),
    )

    MainWindow._on_extrude_requested(
        window, [(0, 0), (3, 0), (0, 2)], 7.0, (1, 2, 3), (0, 0, 1)
    )

    assert feature._profile_pts == [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)]
    assert feature._depth == 7.0
    assert feature._plane_origin == (1.0, 2.0, 3.0)
    assert feature.sketch_definition == {"entities": [{"id": "edited"}]}
    window._finish_sketch_feature_edit.assert_called_once_with(feature)


def test_edit_extrude_opens_only_height_dialog_and_preserves_saved_formula(monkeypatch):
    application = QApplication.instance() or QApplication([])
    feature = ExtrudedObject(
        "FormulaExtrude",
        profile_pts=[(0, 0), (3, 0), (3, 2), (0, 2)],
        depth=4.0,
    )
    feature.param_formulas = {"Depth": "ExtrusionDepth"}
    original_profile = list(feature._profile_pts)
    window = QMainWindow()
    window._units = "mm"
    window._resolve_formula_text = lambda expression: {
        "ExtrusionDepth": 6.5,
    }[expression]
    window._finish_sketch_feature_edit = Mock()
    window._edit_extrusion_depth = MethodType(MainWindow._edit_extrusion_depth, window)
    monkeypatch.setattr(
        main_window_module, "SketchDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Edit Extrude must not open the sketch editor")
        ),
    )

    MainWindow._edit_sketch_feature(window, feature)

    dialog = window._extrusion_depth_dialog
    application.processEvents()
    depth_input = dialog.findChild(main_window_module.QDoubleSpinBox)
    assert dialog.windowTitle() == "Edit Extrude Height"
    assert depth_input.formula_text() == "ExtrusionDepth"
    assert "ExtrusionDepth" in depth_input.lineEdit().text()
    assert feature._depth == pytest.approx(4.0)
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    assert feature._depth == pytest.approx(6.5)
    assert feature.param_formulas == {"Depth": "ExtrusionDepth"}
    assert feature._profile_pts == original_profile
    window._finish_sketch_feature_edit.assert_called_once_with(feature)
    assert dialog.result() == QDialog.Accepted
    assert application is not None


def test_persisted_sketch_recompute_keeps_previous_feature_on_invalid_profile():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    entity = sketch.add_polyline([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    sketch.add_dimension(
        "linear", (sketch.point_ref(entity, 0), sketch.point_ref(entity, 1)),
        expression="0", axis="u",
    )
    sketch.add_dimension(
        "linear", (sketch.point_ref(entity, 1), sketch.point_ref(entity, 2)),
        expression="0", axis="v",
    )
    sketch.add_dimension(
        "linear", (sketch.point_ref(entity, 0), sketch.point_ref(entity, 3)),
        expression="0", axis="v",
    )
    feature = ExtrudedObject(profile_pts=sketch.build_profile())
    feature.sketch_definition = sketch.to_dict()
    previous_definition = deepcopy(feature.sketch_definition)
    previous_profile = list(feature._profile_pts)
    window = SimpleNamespace(_info_bar=SimpleNamespace(set_info=Mock()))

    assert not MainWindow._recompute_persisted_sketch(
        window, feature, feature.sketch_definition, {}
    )

    assert feature.sketch_definition == previous_definition
    assert feature._profile_pts == previous_profile
    window._info_bar.set_info.assert_called_once()


def test_persisted_multi_region_extrusion_rebuilds_selected_regions():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    sketch.add_rectangle((0, 0), (2, 2))
    sketch.add_rectangle((4, 0), (6, 2))
    sketch.select_region_at_uv((1, 1))
    sketch.select_region_at_uv((5, 1), additive=True, toggle=True)
    regions = sketch.operation_regions()
    feature = ExtrudedObject(profile_pts=regions, depth=3)
    feature.sketch_definition = sketch.to_dict()
    window = SimpleNamespace(_info_bar=SimpleNamespace(set_info=Mock()))

    assert MainWindow._recompute_persisted_sketch(
        window, feature, feature.sketch_definition, {}
    )

    assert len(ExtrudedObject._parse_profile_groups(feature._profile_pts)[1]) == 2
    assert feature._extrude.GetOutput().GetBounds() == pytest.approx((0, 2, 0, 6, 0, 3))


def test_scaling_multi_region_extrusion_scales_nested_profile_coordinates():
    regions = [
        [[(0, 0), (2, 0), (2, 2), (0, 2)]],
        [[(4, 0), (5, 0), (5, 2), (4, 2)]],
    ]
    feature = ExtrudedObject(profile_pts=regions, depth=3)

    assert MainWindow._scale_by_attributes(SimpleNamespace(), feature, 2.0)

    assert feature._extrude.GetOutput().GetBounds() == pytest.approx((0, 4, 0, 10, 0, 6))


def test_scaling_box_keeps_its_center_fixed():
    box = BoxObject("Box", 10, 20, 30, 20, 30, 40)

    assert MainWindow._scale_by_attributes(SimpleNamespace(), box, 2.0)

    assert box.actor.GetBounds() == pytest.approx((5, 25, 15, 35, 25, 45))


def test_scaling_step_mesh_uses_actor_transform_and_keeps_center_fixed():
    source = vtk.vtkCubeSource()
    source.SetBounds(0, 2, 0, 4, 0, 6)
    source.Update()
    mesh = MeshObject("STEP solid", source.GetOutput(), step_source_path="source.step")
    original_polydata = mesh.actor.GetMapper().GetInput()
    center_before = tuple(
        (mesh.actor.GetBounds()[axis * 2] + mesh.actor.GetBounds()[axis * 2 + 1]) / 2
        for axis in range(3)
    )
    window = SimpleNamespace()
    window._scale_mesh_object = lambda obj, factor: MainWindow._scale_mesh_object(
        window, obj, factor
    )

    assert MainWindow._scale_by_attributes(window, mesh, 2.0)

    assert mesh.actor.GetScale() == pytest.approx((2.0, 2.0, 2.0))
    assert mesh.actor.GetMapper().GetInput() is original_polydata
    center_after = tuple(
        (mesh.actor.GetBounds()[axis * 2] + mesh.actor.GetBounds()[axis * 2 + 1]) / 2
        for axis in range(3)
    )
    assert center_after == pytest.approx(center_before)


def test_moving_scaled_step_mesh_uses_world_space_delta():
    source = vtk.vtkCubeSource()
    source.SetBounds(0, 2, 0, 4, 0, 6)
    source.Update()
    mesh = MeshObject("STEP solid", source.GetOutput(), step_source_path="source.step")
    window = SimpleNamespace()
    window._scale_mesh_object = lambda obj, factor: MainWindow._scale_mesh_object(
        window, obj, factor
    )
    MainWindow._scale_by_attributes(window, mesh, 2.0)
    bounds_before = mesh.actor.GetBounds()
    center_before = tuple(
        (bounds_before[axis * 2] + bounds_before[axis * 2 + 1]) / 2
        for axis in range(3)
    )

    assert MainWindow._translate_mesh_object(window, mesh, 3.0, -2.0, 1.0)

    bounds_after = mesh.actor.GetBounds()
    center_after = tuple(
        (bounds_after[axis * 2] + bounds_after[axis * 2 + 1]) / 2
        for axis in range(3)
    )
    assert center_after == pytest.approx(tuple(
        center_before[axis] + (3.0, -2.0, 1.0)[axis]
        for axis in range(3)
    ))


def test_scaling_revolved_object_keeps_center_and_enlarges_geometry():
    feature = RevolvedObject(
        "Revolve", [(1, 0), (2, 0), (2, 3), (1, 3)],
        axis_pt1=(10, 20, 30), axis_pt2=(11, 20, 30), plane_origin=(10, 20, 30),
    )

    assert MainWindow._scale_by_attributes(SimpleNamespace(), feature, 1.5)

    assert feature.actor.GetBounds() == pytest.approx((9.25, 13.75, 17, 23, 27, 33))
