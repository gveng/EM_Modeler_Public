import math
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
import vtk

from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene import em_objects
from em3d_modeler.ui import viewport_widget
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.ui.viewport_widget import Viewport3DWidget


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *values):
        self.values.append(values)


def _extrusion_viewport(engine=None, edge_pick=None):
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _last_edge_pick=edge_pick,
        _units="mm",
        status_message=_Signal(),
        sketch_sweep_requested=_Signal(),
        request_pick=Mock(),
        exit_sketch=Mock(),
        _begin_surface_sweep_preview=Mock(),
        _update_surface_sweep_preview=Mock(return_value=True),
        _hide_surface_sweep_preview=Mock(),
        _clear_surface_sweep_preview=Mock(),
        _last_drawing_snap_kind="vertex",
    )
    viewport._request_surface_sweep = MethodType(
        Viewport3DWidget._request_surface_sweep, viewport
    )
    viewport._fit_complete_edge_circle = Viewport3DWidget._fit_complete_edge_circle
    viewport._surface_sweep_intersection = MethodType(
        Viewport3DWidget._surface_sweep_intersection, viewport
    )
    viewport._actor_point_to_world = Viewport3DWidget._actor_point_to_world
    viewport._line_triangle_parameter = Viewport3DWidget._line_triangle_parameter

    def begin_preview(profile, anchor, closed, normal=None, endpoint_snap_mode=None):
        viewport._surface_sweep_preview_profile = profile
        viewport._surface_sweep_preview_anchor = anchor
        viewport._surface_sweep_preview_normal = normal
        viewport._surface_sweep_preview_closed = closed

    viewport._begin_surface_sweep_preview.side_effect = begin_preview
    viewport.request_pick.side_effect = lambda kind, callback: setattr(
        viewport, "pick_callback", callback
    )
    return viewport


def _circle_points(center=(3.0, -4.0, 7.0), radius=5.0):
    return [
        (
            center[0] + radius * math.cos(2 * math.pi * index / 32),
            center[1] + radius * math.sin(2 * math.pi * index / 32),
            center[2],
        )
        for index in range(32)
    ]


def _mesh_face_target(points, picked_point, owner=None, occ_face_id=None):
    vtk_points = vtk.vtkPoints()
    for point in points:
        vtk_points.InsertNextPoint(*point)
    polygon = vtk.vtkPolygon()
    polygon.GetPointIds().SetNumberOfIds(len(points))
    for index in range(len(points)):
        polygon.GetPointIds().SetId(index, index)
    cells = vtk.vtkCellArray()
    cells.InsertNextCell(polygon)
    data = vtk.vtkPolyData()
    data.SetPoints(vtk_points)
    data.SetPolys(cells)
    actor = vtk.vtkActor()
    target = {
        "actor": actor,
        "data": data,
        "cell_id": 0,
        "picked_point": tuple(picked_point),
        "owner": owner,
        "occ_face_id": occ_face_id,
    }
    return target


def _normal_plane_triangle(anchor, axis, offset):
    first = list(anchor)
    second = list(anchor)
    third = list(anchor)
    first[axis] += offset
    second[axis] += offset
    third[axis] += offset
    other_axes = [index for index in range(3) if index != axis]
    first[other_axes[0]] -= 10
    first[other_axes[1]] -= 10
    second[other_axes[0]] += 10
    second[other_axes[1]] -= 10
    third[other_axes[1]] += 10
    return first, second, third


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_surface_extrusion_uses_profile_normal_sign_and_target_face_intersection(
    monkeypatch, sign
):
    engine = SketchEngine((0, 2, 0), (0, 1, 0))
    engine.add_polyline([(1, 2), (5, 2), (5, 4)])
    viewport = _extrusion_viewport(engine)
    preview = Mock(return_value=viewport_widget.QMessageBox.Yes)
    monkeypatch.setattr(viewport_widget.QMessageBox, "question", preview)

    Viewport3DWidget._request_surface_sweep(viewport)
    assert viewport.request_pick.call_args.args[0] == "surface_sweep_target"
    anchor = viewport._surface_sweep_preview_anchor
    target = _mesh_face_target(
        _normal_plane_triangle(anchor, 1, sign * 5),
        (anchor[0], anchor[1] + sign * 6, anchor[2]),
    )
    viewport.pick_callback(target)

    profile, length, direction, closed, from_sketch, profile_curve = (
        viewport.sketch_sweep_requested.values[0]
    )
    assert profile[0] == pytest.approx((2, 2, -1))
    assert length == pytest.approx(5)
    assert direction == pytest.approx((0, sign, 0))
    assert closed is False
    assert from_sketch is True
    assert profile_curve is None
    assert "Surface Extrusion Preview" in preview.call_args.args[1]
    assert "normal-line intersection" in preview.call_args.args[2]
    assert "not trimmed" in preview.call_args.args[2]
    viewport._begin_surface_sweep_preview.assert_called_once()
    assert viewport._begin_surface_sweep_preview.call_args.kwargs["normal"] == pytest.approx((0, 1, 0))
    viewport.exit_sketch.assert_called_once_with(commit=True)


def test_surface_extrusion_no_face_intersection_rearms_without_fallback(monkeypatch):
    engine = SketchEngine((0, 2, 0), (0, 1, 0))
    engine.add_polyline([(1, 2), (5, 2), (5, 4)])
    viewport = _extrusion_viewport(engine)
    monkeypatch.setattr(
        viewport_widget.QMessageBox,
        "question",
        lambda *_args: viewport_widget.QMessageBox.Yes,
    )

    Viewport3DWidget._request_surface_sweep(viewport)
    anchor = viewport._surface_sweep_preview_anchor
    target = _mesh_face_target(
        _normal_plane_triangle(anchor, 0, 5),
        (anchor[0], anchor[1] + 5, anchor[2]),
    )
    viewport.pick_callback(target)
    assert "does not intersect" in viewport.status_message.values[-1][0]
    assert viewport.request_pick.call_count == 2
    viewport._update_surface_sweep_preview.assert_not_called()
    assert viewport.sketch_sweep_requested.values == []
    assert viewport.request_pick.call_args.args[0] == "surface_sweep_target"


def test_surface_extrusion_circular_edge_source_is_preserved(monkeypatch):
    edge_pick = {"points": _circle_points(), "owner": object()}
    viewport = _extrusion_viewport(edge_pick=edge_pick)
    viewport._selection_mode = "edge"
    monkeypatch.setattr(
        viewport_widget.QMessageBox,
        "question",
        lambda *_args: viewport_widget.QMessageBox.Yes,
    )

    Viewport3DWidget._request_surface_sweep(viewport)
    anchor = viewport._surface_sweep_preview_anchor
    viewport.pick_callback(_mesh_face_target(
        _normal_plane_triangle(anchor, 2, 3),
        (anchor[0], anchor[1], anchor[2] + 4),
    ))

    profile, length, direction, closed, from_sketch, profile_curve = (
        viewport.sketch_sweep_requested.values[0]
    )
    assert len(profile) == 32
    assert length == pytest.approx(3)
    assert direction == pytest.approx((0, 0, 1))
    assert closed is True
    assert from_sketch is False
    assert profile_curve is None
    assert viewport.request_pick.call_args.args[0] == "surface_sweep_target"
    assert viewport._begin_surface_sweep_preview.call_args.kwargs["normal"] == pytest.approx((0, 0, 1))


def test_step_edge_sweep_uses_resolved_exact_circle(monkeypatch):
    from em3d_modeler.emerge import occ_export_builder

    edge_pick = {
        "points": _circle_points(),
        "owner": SimpleNamespace(step_source_path="source.step"),
    }
    viewport = _extrusion_viewport(edge_pick=edge_pick)
    profile_curve = {
        "kind": "circle",
        "center": [3.0, -4.0, 7.0],
        "radius": 5.0,
        "normal": [0.0, 0.0, 1.0],
        "x_axis": [1.0, 0.0, 0.0],
    }
    monkeypatch.setattr(
        occ_export_builder,
        "resolve_exact_step_circle_profile",
        lambda _owner, _points: profile_curve,
    )
    intersect = Mock(return_value=[(3.0, (3.0, -4.0, 10.0))])
    monkeypatch.setattr(
        occ_export_builder, "intersect_step_mesh_face_line", intersect
    )
    monkeypatch.setattr(
        viewport_widget.QMessageBox,
        "question",
        lambda *_args: viewport_widget.QMessageBox.Yes,
    )

    Viewport3DWidget._request_surface_sweep(viewport)
    edge_owner = edge_pick["owner"]
    viewport.pick_callback(_mesh_face_target(
        _normal_plane_triangle((3, -4, 7), 2, 3),
        (3, -4, 10),
        owner=edge_owner,
        occ_face_id=17,
    ))

    profile, length, direction, closed, from_sketch, emitted_curve = (
        viewport.sketch_sweep_requested.values[0]
    )
    expected_profile = em_objects.SweptSurfaceObject.sample_profile_curve(
        profile_curve, len(profile)
    )
    assert len(profile) == len(expected_profile)
    for actual, expected in zip(profile, expected_profile):
        assert actual == pytest.approx(expected)
    assert length == pytest.approx(3.0)
    assert direction == pytest.approx((0.0, 0.0, 1.0))
    assert closed is True
    assert from_sketch is False
    assert emitted_curve == profile_curve
    intersect.assert_called_once_with(
        edge_owner, 17, pytest.approx((3.0, -4.0, 7.0)), pytest.approx((0, 0, 1))
    )


def test_surface_sweep_endpoint_uses_existing_geometry_snaps_then_active_grid():
    snapped_point = (4.0, 5.0, 6.0)
    viewport = SimpleNamespace(
        _selection_mode="edge",
        _snap_to_visible_geometry=Mock(return_value=(snapped_point, "edge")),
        _ray_plane_intersect=Mock(return_value=(0, 0, 0)),
        _snap=Mock(return_value=(0, 0, 0)),
    )

    result = Viewport3DWidget._surface_sweep_endpoint_at_screen(viewport, 12, 34)

    assert result == snapped_point
    viewport._snap_to_visible_geometry.assert_called_once_with(
        12,
        34,
        None,
        snap_mode="edge",
        record_snap=False,
    )
    assert viewport._last_drawing_snap_kind == "edge"


def test_surface_sweep_edge_profile_uses_face_snap_for_terminal_endpoint():
    face_point = (4.0, 5.0, 6.0)
    viewport = SimpleNamespace(
        _selection_mode="edge",
        _surface_sweep_endpoint_snap_mode="face",
        _snap_to_visible_geometry=Mock(return_value=(face_point, "surface")),
        _ray_plane_intersect=Mock(return_value=(0, 0, 0)),
        _snap=Mock(return_value=(0, 0, 0)),
    )

    result = Viewport3DWidget._surface_sweep_endpoint_at_screen(viewport, 12, 34)

    assert result == face_point
    viewport._snap_to_visible_geometry.assert_called_once_with(
        12,
        34,
        None,
        snap_mode="face",
        record_snap=False,
    )


def test_surface_sweep_endpoint_falls_back_to_active_grid_when_no_geometry_snap():
    grid_point = (1.5, 2.5, 3.5)
    viewport = SimpleNamespace(
        _selection_mode="all",
        _snap_to_visible_geometry=Mock(return_value=None),
        _ray_plane_intersect=Mock(return_value=(1.4, 2.4, 3.4)),
        _snap=Mock(return_value=grid_point),
    )

    result = Viewport3DWidget._surface_sweep_endpoint_at_screen(viewport, 12, 34)

    assert result == grid_point
    viewport._snap_to_visible_geometry.assert_called_once_with(
        12,
        34,
        None,
        snap_mode="all",
        record_snap=False,
    )
    viewport._snap.assert_called_once_with((1.4, 2.4, 3.4))
    assert viewport._last_drawing_snap_kind == "grid"


def test_surface_sweep_target_miss_does_not_use_grid_or_consume_request():
    viewport = SimpleNamespace(
        _pick_request=("surface_sweep_target", Mock(), None),
        _surface_sweep_target_at_screen=Mock(return_value=None),
        _hide_surface_sweep_preview=Mock(),
        status_message=_Signal(),
        unsetCursor=Mock(),
    )

    assert Viewport3DWidget._handle_pick_request(viewport, 20, 30)

    viewport._surface_sweep_target_at_screen.assert_called_once_with(20, 30)
    viewport._hide_surface_sweep_preview.assert_called_once()
    assert viewport._pick_request[0] == "surface_sweep_target"
    assert "no grid or closest-point fallback" in viewport.status_message.values[-1][0]


def test_mouse_move_previews_only_the_resolved_face_intersection():
    target = {"picked_point": (0, 0, 6)}
    viewport = SimpleNamespace(
        _pick_request=("surface_sweep_target", Mock(), None),
        _surface_sweep_preview_profile=[(0, 0, 0), (2, 0, 0)],
        _surface_sweep_target_at_screen=Mock(return_value=target),
        _surface_sweep_intersection=Mock(
            return_value=((0, 0, 4), (0, 0, 1), 4)
        ),
        _update_surface_sweep_preview=Mock(return_value=True),
        _hide_surface_sweep_preview=Mock(),
    )

    Viewport3DWidget._on_mouse_move(viewport, 20, 30)

    viewport._surface_sweep_target_at_screen.assert_called_once_with(20, 30)
    viewport._surface_sweep_intersection.assert_called_once_with(target)
    viewport._update_surface_sweep_preview.assert_called_once_with((0, 0, 4))
    viewport._hide_surface_sweep_preview.assert_not_called()


def test_mouse_move_hides_preview_when_picked_face_has_no_intersection():
    viewport = SimpleNamespace(
        _pick_request=("surface_sweep_target", Mock(), None),
        _surface_sweep_preview_profile=[(0, 0, 0), (2, 0, 0)],
        _surface_sweep_target_at_screen=Mock(return_value={"picked_point": (0, 0, 6)}),
        _surface_sweep_intersection=Mock(return_value=None),
        _update_surface_sweep_preview=Mock(),
        _hide_surface_sweep_preview=Mock(),
    )

    Viewport3DWidget._on_mouse_move(viewport, 20, 30)

    viewport._hide_surface_sweep_preview.assert_called_once()
    viewport._update_surface_sweep_preview.assert_not_called()


def test_surface_sweep_live_preview_tracks_endpoint_and_hides_null_vector():
    viewport = SimpleNamespace(
        _renderer=vtk.vtkRenderer(),
        _render=Mock(),
        _surface_sweep_preview_actor=None,
        _surface_sweep_preview_mapper=None,
        _surface_sweep_preview_profile=None,
        _surface_sweep_preview_anchor=None,
        _surface_sweep_preview_closed=False,
    )
    viewport._clear_surface_sweep_preview = lambda: (
        Viewport3DWidget._clear_surface_sweep_preview(viewport)
    )
    viewport._hide_surface_sweep_preview = lambda: (
        Viewport3DWidget._hide_surface_sweep_preview(viewport)
    )
    profile = [(0, 0, 0), (2, 0, 0), (2, 1, 0)]
    anchor = (1, 0.5, 0)

    Viewport3DWidget._begin_surface_sweep_preview(viewport, profile, anchor, True)
    assert not Viewport3DWidget._update_surface_sweep_preview(viewport, anchor)
    assert viewport._surface_sweep_preview_actor is None

    assert Viewport3DWidget._update_surface_sweep_preview(viewport, (1, 0.5, 3))
    actor = viewport._surface_sweep_preview_actor
    mesh = actor.GetMapper().GetInput()
    assert actor.GetVisibility()
    assert mesh.GetNumberOfPolys() == len(profile)
    assert mesh.GetNumberOfPoints() == 2 * len(profile)

    assert not Viewport3DWidget._update_surface_sweep_preview(
        viewport, (float("inf"), 0, 0)
    )
    assert not actor.GetVisibility()
    Viewport3DWidget._clear_surface_sweep_preview(viewport)
    assert viewport._surface_sweep_preview_actor is None
    assert viewport._renderer.GetActors().GetNumberOfItems() == 0


def test_mouse_move_updates_live_surface_preview_during_endpoint_pick():
    viewport = SimpleNamespace(
        _pick_request=("snapped_point", Mock(), None),
        _surface_sweep_preview_profile=[(0, 0, 0), (2, 0, 0)],
        _surface_sweep_endpoint_at_screen=Mock(return_value=(0, 0, 4)),
        _update_surface_sweep_preview=Mock(return_value=True),
        _hide_surface_sweep_preview=Mock(),
    )

    Viewport3DWidget._on_mouse_move(viewport, 20, 30)

    viewport._surface_sweep_endpoint_at_screen.assert_called_once_with(20, 30)
    viewport._update_surface_sweep_preview.assert_called_once_with((0, 0, 4))
    viewport._hide_surface_sweep_preview.assert_not_called()


def test_surface_extrusion_cancel_keeps_sketch_unchanged(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_polyline([(1, 2), (5, 2), (5, 4)])
    viewport = _extrusion_viewport(engine)
    definition_before = engine.to_dict()
    monkeypatch.setattr(
        viewport_widget.QMessageBox,
        "question",
        lambda *_args: viewport_widget.QMessageBox.Cancel,
    )

    Viewport3DWidget._request_surface_sweep(viewport)
    anchor = viewport._surface_sweep_preview_anchor
    viewport.pick_callback(_mesh_face_target(
        _normal_plane_triangle(anchor, 2, 5),
        (anchor[0], anchor[1], anchor[2] + 6),
    ))

    assert viewport.sketch_sweep_requested.values == []
    assert engine.to_dict() == definition_before
    viewport.exit_sketch.assert_not_called()
    assert viewport.status_message.values[-1][0].startswith(
        "Surface Extrusion cancelled"
    )


def test_main_window_creates_surface_extrusion_from_geometry_parameters():
    objects = []
    scene = SimpleNamespace(
        add_object=Mock(side_effect=objects.append),
        select=Mock(),
    )
    viewport = SimpleNamespace(
        scene=scene,
        object_selected=_Signal(),
        selection_changed=_Signal(),
        scene_changed=_Signal(),
        _render=Mock(),
    )
    info = SimpleNamespace(set_info=Mock())
    window = SimpleNamespace(
        _viewport=viewport,
        _draw_material="PEC",
        _refresh_materials=Mock(),
        _info_bar=info,
    )

    MainWindow._on_surface_sweep_requested(
        window, [(0, 0, 0), (4, 0, 0)], 3, (0, 0, -1), False, False
    )

    result = objects[0]
    assert isinstance(result, em_objects.SweptSurfaceObject)
    assert result.profile_perimeter == pytest.approx(4)
    assert result.extrusion_length == pytest.approx(3)
    assert result.direction == pytest.approx((0, 0, -1))
    scene.select.assert_called_once_with(result)
    assert info.set_info.call_args.args[0] == f"Surface Extrusion created: {result.name}"


def test_embedded_viewport_toolbar_exposes_surface_extrusion(monkeypatch):
    class _ActionSignal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

    class _Action:
        def __init__(self, _icon, label, _parent):
            self.label = label
            self.triggered = _ActionSignal()

        def setToolTip(self, _tooltip):
            pass

        def setIconText(self, _text):
            pass

        def setCheckable(self, _checkable):
            pass

    class _Toolbar:
        def __init__(self, _title, _parent):
            self.actions = []

        def setMovable(self, _movable):
            pass

        def addAction(self, action):
            self.actions.append(action)

        def addSeparator(self):
            self.actions.append(None)

    monkeypatch.setattr(viewport_widget, "QAction", _Action)
    monkeypatch.setattr(viewport_widget, "QToolBar", _Toolbar)
    viewport = SimpleNamespace(
        _sketch_set_selection_mode=Mock(),
        _sketch_set_tool=Mock(),
        _sketch_set_rectangle_mode=Mock(),
        _sketch_toggle_construction=Mock(),
        _sketch_begin_dimension=Mock(),
        _sketch_apply_fillet=Mock(),
        _sketch_apply_chamfer=Mock(),
        _sketch_delete_selected=Mock(),
        _sketch_close_profile=Mock(),
        _sketch_cancel_tool=Mock(),
        _sketch_request_extrude=Mock(),
        _sketch_request_extruded_cut=Mock(),
        _sketch_begin_revolve=Mock(),
        _request_surface_sweep=Mock(),
        exit_sketch=Mock(),
    )

    toolbar = Viewport3DWidget._build_sketch_toolbar(viewport)
    extrusion_action = next(
        action for action in toolbar.actions
        if action is not None and action.label == "Surface Extrusion"
    )
    assert extrusion_action.triggered.callback == viewport._request_surface_sweep
