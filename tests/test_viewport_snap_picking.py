from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
import vtk

from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.ui import viewport_widget as viewport_module
from em3d_modeler.ui.viewport_widget import Viewport3DWidget


class _PointPicker:
    def __init__(self, actor, dataset, point_id=0):
        self.actor = actor
        self.dataset = dataset
        self.point_id = point_id

    def SetTolerance(self, _tolerance):
        pass

    def Pick(self, *_args):
        pass

    def GetActor(self):
        return self.actor

    def GetDataSet(self):
        return self.dataset

    def GetPointId(self):
        return self.point_id


class _CellPicker:
    def __init__(self, actor, dataset, position, cell_id=0):
        self.actor = actor
        self.dataset = dataset
        self.position = position
        self.cell_id = cell_id

    def SetTolerance(self, _tolerance):
        pass

    def Pick(self, *_args):
        pass

    def GetActor(self):
        return self.actor

    def GetDataSet(self):
        return self.dataset

    def GetCellId(self):
        return self.cell_id

    def GetPickPosition(self):
        return self.position


def _bare_viewport(**attributes):
    defaults = {
        "_renderer": object(),
        "_render_window": None,
        "_snap_records": [],
        "_selection_mode": "object",
        "_draw_plane": "XY",
        "_last_drawing_snap_kind": "grid",
        "scene": SimpleNamespace(objects=[]),
    }
    defaults.update(attributes)
    viewport = SimpleNamespace(**defaults)
    viewport._pick_tolerance_for_mode = lambda _mode: 0.01
    viewport._actor_point_to_world = lambda actor, point: Viewport3DWidget._actor_point_to_world(actor, point)
    viewport._actor_point_to_local = lambda actor, point: Viewport3DWidget._actor_point_to_local(actor, point)
    viewport._owner_for_actor = MethodType(Viewport3DWidget._owner_for_actor, viewport)
    viewport._snap_to_visible_geometry = MethodType(
        Viewport3DWidget._snap_to_visible_geometry, viewport
    )
    viewport._drawing_snap_point = MethodType(
        Viewport3DWidget._drawing_snap_point, viewport
    )
    viewport._snap = MethodType(Viewport3DWidget._snap, viewport)
    viewport._height_from_cursor = MethodType(
        Viewport3DWidget._height_from_cursor, viewport
    )
    viewport._active_plane_axis_index = lambda: 2
    return viewport


def _polydata_with_point(point):
    points = vtk.vtkPoints()
    points.InsertNextPoint(*point)
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    return data


def test_drawing_vertex_snap_works_off_plane_and_keeps_owner_and_transforms(monkeypatch):
    actor = vtk.vtkActor()
    actor.SetPosition(10.0, 20.0, 30.0)
    primary_actor = vtk.vtkActor()
    owner = SimpleNamespace(name="Assembly", actor=primary_actor, all_actors=[primary_actor, actor])
    viewport = _bare_viewport(
        _selection_mode="vertex",
        _ray_plane_intersect=lambda *_args: pytest.fail("plane fallback should not run"),
    )
    viewport.scene.objects = [owner]
    dataset = _polydata_with_point((1.0, 2.0, 3.0))
    monkeypatch.setattr(
        viewport_module.vtk, "vtkPointPicker", lambda: _PointPicker(actor, dataset)
    )

    snapped = viewport._drawing_snap_point(400, 300)

    assert snapped == pytest.approx((11.0, 22.0, 33.0))
    assert viewport._last_drawing_snap_kind == "vertex"
    assert viewport._snap_records[-1] == {
        "kind": "vertex",
        "object": "Assembly",
        "point_id": 0,
        "local": [1.0, 2.0, 3.0],
        "point": [11.0, 22.0, 33.0],
    }


def test_sub_element_vertex_pick_uses_point_picker_actor_transform(monkeypatch):
    actor = vtk.vtkActor()
    actor.SetPosition(10.0, 20.0, 30.0)
    dataset = _polydata_with_point((1.0, 2.0, 3.0))
    coordinates = []
    markers = []

    class _Signal:
        def emit(self, *values):
            coordinates.append(values)

    class _Renderer:
        def AddActor(self, marker):
            markers.append(marker)

    viewport = _bare_viewport(
        _selection_mode="vertex",
        _units="mm",
        _renderer=_Renderer(),
        scene=SimpleNamespace(objects=[]),
        _sub_pick_actor=None,
        _clear_sub_pick_marker=lambda: None,
        _render=lambda: None,
        _build_vertex_marker=lambda point, ref_actor: (point, ref_actor),
        clear_coords_requested=_Signal(),
        picked_coords=_Signal(),
        status_message=_Signal(),
    )
    viewport._sub_element_pick = MethodType(Viewport3DWidget._sub_element_pick, viewport)
    monkeypatch.setattr(
        viewport_module.vtk,
        "vtkPointPicker",
        lambda: _PointPicker(actor, dataset),
    )

    viewport._sub_element_pick(300, 200)

    assert coordinates[0] == pytest.approx((11.0, 22.0, 33.0, "mm"))
    assert markers == [((11.0, 22.0, 33.0), actor)]


def test_vertex_pick_actor_filter_keeps_request_armed_for_other_objects(monkeypatch):
    expected_actor = vtk.vtkActor()
    other_actor = vtk.vtkActor()
    callback = Mock()
    status_message = Mock()
    viewport = _bare_viewport(
        _pick_request=None,
        status_message=status_message,
        setCursor=Mock(),
        unsetCursor=Mock(),
        _cancel_draw=Mock(),
    )
    viewport.request_pick = MethodType(Viewport3DWidget.request_pick, viewport)
    viewport._handle_pick_request = MethodType(
        Viewport3DWidget._handle_pick_request, viewport
    )
    monkeypatch.setattr(
        viewport_module.vtk,
        "vtkCellPicker",
        lambda: _CellPicker(other_actor, None, (0.0, 0.0, 0.0)),
    )

    viewport.request_pick("vertex", callback, actor_filter=expected_actor)

    assert viewport._handle_pick_request(100, 100)

    callback.assert_not_called()
    assert viewport._pick_request == ("vertex", callback, expected_actor)
    assert status_message.emit.call_args.args == (
        "Pick a vertex on the selected object.",
    )


def test_measurement_direct_picks_highlight_features_and_draw_result():
    first = {"kind": "point", "point": (0.0, 0.0, 0.0)}
    second = {"kind": "point", "point": (3.0, 4.0, 0.0)}
    highlights = [object(), object()]
    added_actors = []

    class _Renderer:
        def AddActor(self, actor):
            added_actors.append(actor)

        def RemoveActor(self, _actor):
            pass

    class _Signal:
        def __init__(self):
            self.messages = []
            self.values = []

        def emit(self, message):
            self.messages.append(message)
            self.values.append(message)

    status = _Signal()
    draw_result = Mock()
    viewport = _bare_viewport(
        _measurement_active=True,
        _measurement_features=[],
        _measurement_highlights=[],
        _measurement_actors=[],
        _measurement_escape_shortcut=Mock(),
        _renderer=_Renderer(),
        _units="mm",
        status_message=status,
        _render=Mock(),
        _add_measurement_text=Mock(),
        _draw_measurement_result=draw_result,
        unsetCursor=Mock(),
    )
    features = iter(((first, highlights[0], None), (second, highlights[1], None)))
    viewport._pick_measurement_feature = lambda *_args: next(features)
    viewport._measurement_prompt = MethodType(Viewport3DWidget._measurement_prompt, viewport)
    viewport._measurement_result_text = MethodType(
        Viewport3DWidget._measurement_result_text, viewport
    )

    Viewport3DWidget._measurement_click(viewport, 10, 20)
    assert viewport._measurement_active is True
    assert added_actors == [highlights[0]]
    assert status.messages[-1].startswith("Measure: pick")

    Viewport3DWidget._measurement_click(viewport, 30, 40)

    assert viewport._measurement_active is False
    assert added_actors == highlights
    draw_result.assert_called_once()
    assert draw_result.call_args.args[2]["distance"] == pytest.approx(5.0)


def test_escape_clears_completed_measurement_and_disables_shortcut():
    class _Signal:
        def __init__(self):
            self.messages = []

        def emit(self, message):
            self.messages.append(message)

    status = _Signal()
    mode_changes = _Signal()
    shortcut = Mock()
    removed_actors = []

    class _Renderer:
        def RemoveActor(self, actor):
            removed_actors.append(actor)

    highlight = object()
    annotation = object()
    viewport = SimpleNamespace(
        _measurement_active=False,
        _measurement_highlights=[highlight],
        _measurement_actors=[annotation],
        _measurement_features=[{"kind": "point", "point": (1, 2, 3)}],
        _renderer=_Renderer(),
        _measurement_escape_shortcut=shortcut,
        unsetCursor=Mock(),
        _render=Mock(),
        status_message=status,
        measurement_mode_changed=mode_changes,
    )
    viewport._clear_measurement_actors = MethodType(
        Viewport3DWidget._clear_measurement_actors, viewport
    )

    Viewport3DWidget._finish_measurement(viewport)

    assert viewport._measurement_active is False
    shortcut.setEnabled.assert_called_once_with(False)
    assert removed_actors == [highlight, annotation]
    assert viewport._measurement_features == []
    viewport.unsetCursor.assert_called_once_with()
    viewport._render.assert_called_once_with()
    assert status.messages == ["Measurement finished; annotations cleared."]
    assert mode_changes.messages == [False]


def test_measurement_draws_distance_and_axis_vectors_in_viewer():
    first = {"kind": "point", "point": (0.0, 0.0, 0.0)}
    second = {"kind": "point", "point": (3.0, 4.0, 12.0)}
    added_actors = []

    class _Renderer:
        def AddActor(self, actor):
            added_actors.append(actor)

    viewport = _bare_viewport(
        _renderer=_Renderer(),
        _measurement_actors=[],
        _units="mm",
    )
    viewport._add_measurement_text = MethodType(
        Viewport3DWidget._add_measurement_text, viewport
    )
    viewport._add_measurement_vector = MethodType(
        Viewport3DWidget._add_measurement_vector, viewport
    )
    viewport._draw_measurement_result = MethodType(
        Viewport3DWidget._draw_measurement_result, viewport
    )
    from em3d_modeler.ui.measurement import calculate_measurement

    result = calculate_measurement(first, second)
    viewport._draw_measurement_result(first, second, result)

    assert len(viewport._measurement_actors) == 16
    assert len(added_actors) == 16
    labels = [
        actor for actor in viewport._measurement_actors
        if isinstance(actor, vtk.vtkBillboardTextActor3D)
    ]
    assert len(labels) == 4
    assert all(actor.GetDisplayOffset() != (0, 0) for actor in labels)
    assert all(actor.GetTextProperty().GetBackgroundOpacity() > 0.8 for actor in labels)
    arrowheads = [
        actor for actor in viewport._measurement_actors
        if isinstance(actor, vtk.vtkActor)
        and actor.GetMapper().GetInput().GetNumberOfPoints() > 2
    ]
    assert len(arrowheads) == 8
    for arrowhead in arrowheads:
        bounds = arrowhead.GetMapper().GetInput().GetBounds()
        diagonal = sum(
            (bounds[index + 1] - bounds[index]) ** 2
            for index in (0, 2, 4)
        ) ** 0.5
        assert diagonal < 0.1


def test_surface_pick_tracks_full_coplanar_region_for_plate_creation(monkeypatch):
    points = vtk.vtkPoints()
    for point in (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ):
        points.InsertNextPoint(*point)
    cells = vtk.vtkCellArray()
    for point_ids in ((0, 1, 2), (0, 2, 3), (0, 4, 1)):
        triangle = vtk.vtkTriangle()
        for index, point_id in enumerate(point_ids):
            triangle.GetPointIds().SetId(index, point_id)
        cells.InsertNextCell(triangle)
    dataset = vtk.vtkPolyData()
    dataset.SetPoints(points)
    dataset.SetPolys(cells)
    dataset.BuildLinks()

    actor = vtk.vtkActor()
    owner = SimpleNamespace(name="Extrusion", actor=actor, all_actors=[actor])
    viewport = _bare_viewport(
        _renderer=vtk.vtkRenderer(),
        _selection_mode="face",
        _sub_pick_actor=None,
        _last_face_pick=None,
        _last_edge_pick=None,
        _units="mm",
        _grid_spacing=0.5,
        scene=SimpleNamespace(objects=[owner]),
        picked_coords=Mock(),
        clear_coords_requested=Mock(),
        status_message=Mock(),
    )
    viewport._cell_normal_local = Viewport3DWidget._cell_normal_local
    viewport._clear_sub_pick_marker = MethodType(
        Viewport3DWidget._clear_sub_pick_marker, viewport
    )
    viewport._coplanar_region_cell_ids = MethodType(
        Viewport3DWidget._coplanar_region_cell_ids, viewport
    )
    viewport._face_region_cell_ids = MethodType(
        Viewport3DWidget._face_region_cell_ids, viewport
    )
    viewport._face_region_points_world = MethodType(
        Viewport3DWidget._face_region_points_world, viewport
    )
    viewport._build_face_region_marker = MethodType(
        Viewport3DWidget._build_face_region_marker, viewport
    )
    viewport._render = lambda: None
    viewport._finish_object = Mock()
    monkeypatch.setattr(
        viewport_module.vtk,
        "vtkCellPicker",
        lambda: _CellPicker(actor, dataset, (1.0, 0.25, 0.0), cell_id=0),
    )

    Viewport3DWidget._sub_element_pick(viewport, 200, 150)
    plate = Viewport3DWidget.create_plate_from_face(viewport)

    assert viewport._last_face_pick["cell_ids"] == [0, 1]
    assert len(viewport._last_face_pick["points"]) == 4
    assert viewport._sub_pick_actor.GetProperty().GetEdgeVisibility() == 0
    assert plate.actor.GetBounds() == pytest.approx((0.0, 2.0, 0.0, 1.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("plane", "normal_axis"),
    [("XY", 2), ("XZ", 1), ("YZ", 0)],
)
def test_drawn_plate_has_zero_extent_along_plane_normal(plane, normal_axis):
    first = (2.0, 3.0, 4.0)
    second = [5.0, 7.0, 9.0]
    second[normal_axis] += 2.0
    viewport = _bare_viewport(
        _draw_pts=[first],
        _draw_plane=plane,
        _draw_material="PEC",
        status_message=Mock(),
        _finish_object=Mock(),
    )

    Viewport3DWidget._fsm_plate_click(viewport, second, 0, 0, state=1)

    plate = viewport._finish_object.call_args.args[0]
    bounds = plate.actor.GetBounds()
    assert bounds[normal_axis * 2] == pytest.approx(first[normal_axis])
    assert bounds[normal_axis * 2 + 1] == pytest.approx(first[normal_axis])


def test_plate_from_edge_has_zero_extent_normal_to_plate():
    source = vtk.vtkCubeSource()
    source.SetBounds(0.0, 3.0, 0.0, 1.0, 0.0, 2.0)
    source.Update()
    actor = vtk.vtkActor()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(source.GetOutputPort())
    actor.SetMapper(mapper)
    owner = SimpleNamespace(name="Part", actor=actor)
    viewport = _bare_viewport(
        _last_edge_pick={
            "points": [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0)],
            "owner": owner,
            "cell_id": 0,
        },
        _grid_spacing=0.5,
        status_message=Mock(),
        _finish_object=Mock(),
    )

    plate = Viewport3DWidget._create_plate_from_edge(viewport, "PEC")

    assert plate.actor.GetBounds() == pytest.approx((0.0, 3.0, 0.0, 1.0, 0.0, 0.0))


def test_planar_tool_creates_zero_thickness_plate():
    first = (1.0, 2.0, 3.0)
    viewport = _bare_viewport(
        _draw_pts=[first],
        _draw_plane="XY",
        _draw_material="PEC",
        status_message=Mock(),
        _add_selection_point_marker=Mock(),
        _finish_object=Mock(),
    )

    Viewport3DWidget._fsm_planar_click(viewport, (5.0, 7.0, 8.0), 0, 0, state=1)

    plate = viewport._finish_object.call_args.args[0]
    assert plate.actor.GetBounds() == pytest.approx((1.0, 5.0, 2.0, 7.0, 3.0, 3.0))


@pytest.mark.parametrize("mode", ["edge", "face"])
def test_geometry_snap_records_owner_and_local_world_points(mode, monkeypatch):
    actor = vtk.vtkActor()
    actor.SetPosition(10.0, 20.0, 30.0)
    primary_actor = vtk.vtkActor()
    owner = SimpleNamespace(name="Assembly", actor=primary_actor, all_actors=[primary_actor, actor])
    viewport = _bare_viewport()
    viewport.scene.objects = [owner]
    dataset = vtk.vtkPolyData()
    position = (12.0, 24.0, 36.0)
    monkeypatch.setattr(
        viewport_module.vtk,
        "vtkCellPicker",
        lambda: _CellPicker(actor, dataset, position),
    )
    if mode == "edge":
        viewport._pick_edge_segment_local = lambda *_args: (
            [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
            (2.0, 4.0, 6.0),
        )

    result = viewport._snap_to_visible_geometry(200, 150, None, snap_mode=mode)

    assert result[0] == pytest.approx(position)
    assert viewport._snap_records[-1]["object"] == "Assembly"
    assert viewport._snap_records[-1]["local"] == pytest.approx([2.0, 4.0, 6.0])
    assert viewport._snap_records[-1]["point"] == pytest.approx(position)


def test_drawing_snap_uses_selection_mode_at_each_call():
    modes = []
    viewport = _bare_viewport(
        _ray_plane_intersect=lambda *_args: pytest.fail("geometry snap should win")
    )

    def snap(_sx, _sy, _fallback, snap_mode):
        modes.append(snap_mode)
        return ((1.0, 2.0, 3.0), snap_mode)

    viewport._snap_to_visible_geometry = snap
    viewport._selection_mode = "face"
    assert viewport._drawing_snap_point(20, 30) == (1.0, 2.0, 3.0)
    viewport._selection_mode = "edge"
    assert viewport._drawing_snap_point(20, 30) == (1.0, 2.0, 3.0)

    assert modes == ["face", "edge"]
    assert viewport._last_drawing_snap_kind == "edge"


def test_grid_drawing_snap_bypasses_geometry_and_uses_active_grid():
    viewport = _bare_viewport(
        _selection_mode="grid",
        _grid_spacing=2.0,
        _ray_plane_intersect=lambda *_args: (3.1, 4.9, 0.0),
    )
    viewport._snap_to_visible_geometry = lambda *_args, **_kwargs: pytest.fail(
        "Grid mode must not pick geometry"
    )

    assert viewport._drawing_snap_point(20, 30) == (4.0, 4.0, 0.0)
    assert viewport._last_drawing_snap_kind == "grid"


def test_grid_snap_stays_on_custom_reference_plane():
    origin = (1.0, 2.0, 3.0)
    normal = (0.0, 1.0, 1.0)
    u_axis, v_axis = viewport_module.plane_basis_from_normal(normal)
    point = tuple(
        origin[index] + 3.1 * u_axis[index] + 4.9 * v_axis[index]
        for index in range(3)
    )
    expected = tuple(
        origin[index] + 4.0 * u_axis[index] + 4.0 * v_axis[index]
        for index in range(3)
    )
    viewport = _bare_viewport(
        _grid_spacing=2.0,
        _custom_plane_active=True,
        _custom_plane_origin=origin,
        _custom_plane_normal=normal,
    )

    assert viewport._snap(point) == pytest.approx(expected)


def test_grid_height_snap_uses_cursor_ray_not_underlying_geometry(monkeypatch):
    class _Renderer:
        def __init__(self):
            self.display = None

        def SetDisplayPoint(self, *point):
            self.display = point

        def DisplayToWorld(self):
            pass

        def GetWorldPoint(self):
            depth = self.display[2]
            return (5.0 - 5.0 * depth, 0.0, 10.0 * depth, 1.0)

    viewport = _bare_viewport(
        _selection_mode="grid",
        _grid_spacing=2.0,
        _renderer=_Renderer(),
        _draw_plane="XY",
    )
    viewport._snap_to_visible_geometry = lambda *_args, **_kwargs: pytest.fail(
        "Grid mode must not pick geometry"
    )

    assert viewport._height_from_cursor(20, 30, 0.0) == pytest.approx(10.0)
    assert viewport._last_drawing_snap_kind == "grid"


def test_height_stage_uses_current_geometry_snap_and_preserves_cursor_fallback(monkeypatch):
    viewport = _bare_viewport(_selection_mode="face", _grid_spacing=2.0)
    requested_modes = []

    def snap(_sx, _sy, _fallback, snap_mode):
        requested_modes.append(snap_mode)
        if snap_mode == "face":
            return ((7.0, 8.0, 14.0), "surface")
        return None

    viewport._snap_to_visible_geometry = snap
    assert viewport._height_from_cursor(50, 60, 4.0) == pytest.approx(10.0)
    assert viewport._last_drawing_snap_kind == "surface"

    viewport._selection_mode = "edge"

    class _WorldPointPicker:
        def Pick(self, *_args):
            pass

        def GetPickPosition(self):
            return (0.0, 0.0, 9.1)

    monkeypatch.setattr(viewport_module.vtk, "vtkWorldPointPicker", _WorldPointPicker)
    assert viewport._height_from_cursor(50, 60, 4.0) == pytest.approx(6.0)
    assert requested_modes == ["face", "edge"]
    assert viewport._last_drawing_snap_kind == "grid"


def test_center_rectangle_snap_uses_actual_corners_not_center():
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.set_rectangle_mode("center-corner")
    engine.add_rectangle((2, 2), (3, 3))
    viewport = SimpleNamespace(_sketch_engine=engine, _grid_spacing=1.0)
    viewport._nearest_sketch_vertex = MethodType(
        Viewport3DWidget._nearest_sketch_vertex, viewport
    )

    assert viewport._nearest_sketch_vertex((1.05, 1.0)) == pytest.approx((1, 1))
    assert viewport._nearest_sketch_vertex((2.0, 2.0)) is None


def test_sketch_overlay_hides_vertex_glyphs_but_keeps_geometry(monkeypatch):
    engine = SketchEngine((0, 0, 0), (0, 0, 1))
    engine.add_line((0, 0), (2, 0))
    actors = []
    viewport = SimpleNamespace(
        _sketch_engine=engine,
        _sketch_axis_highlight=None,
        _sketch_lines_actor=None,
        _sketch_construction_actor=None,
        _sketch_hi_actor=None,
        _sketch_region_actor=None,
        _sketch_preview_actor=None,
        _renderer=SimpleNamespace(AddActor=actors.append, RemoveActor=lambda _actor: None),
        _refresh_sketch_dimensions=Mock(),
        _refresh_sketch_preview=Mock(),
        _make_line_actor=lambda poly, **_kwargs: poly,
        _render=Mock(),
    )
    monkeypatch.setattr(
        SketchEngine, "to_vertex_polydata",
        lambda _engine: pytest.fail("sketch vertices should not be drawn"),
    )

    Viewport3DWidget._refresh_sketch_overlay(viewport)

    assert len(actors) == 1
    assert viewport._sketch_lines_actor.GetNumberOfCells() > 0


def test_height_final_click_does_not_require_a_plane_point():
    calls = []
    viewport = _bare_viewport(
        _draw_mode="box",
        _draw_state=2,
        _draw_pts=[(0.0, 0.0, 0.0), (2.0, 3.0, 0.0)],
    )
    viewport._drawing_snap_point = lambda *_args: pytest.fail(
        "height click should not require a reference-plane snap"
    )
    viewport._fsm_box_click = lambda *args: calls.append(args)
    viewport._drawing_click = MethodType(Viewport3DWidget._drawing_click, viewport)

    viewport._drawing_click(100, 120)

    assert calls == [((2.0, 3.0, 0.0), 100, 120, 2)]