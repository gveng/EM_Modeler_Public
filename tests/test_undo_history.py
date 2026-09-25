from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import vtk

from em3d_modeler.scene.em_objects import MeshObject
from em3d_modeler.scene.scene_manager import SceneManager
from em3d_modeler.ui.main_window import MainWindow


class _Signal:
    def __init__(self, callback):
        self._callback = callback

    def emit(self):
        self._callback()


def _history_window(scene_state):
    scene = SimpleNamespace(
        to_json=lambda **_kwargs: [{"state": dict(scene_state)}],
        refresh_adaptive_grid=Mock(),
        selection=[],
        remove_object=Mock(),
        deselect_all=Mock(),
    )
    viewport = SimpleNamespace(scene=scene, _render=Mock())
    window = SimpleNamespace(
        _viewport=viewport,
        _history_undo=[],
        _history_redo=[],
        _history_limit=10,
        _history_restoring=False,
        _act_undo=Mock(),
        _act_redo=Mock(),
        _body_props=SimpleNamespace(set_object=Mock()),
        _info_bar=SimpleNamespace(set_info=Mock()),
        _sync_boolean_provenance=Mock(),
        _sync_port_reference_state=Mock(),
        _refresh_materials=Mock(),
        _mark_simulation_dirty=Mock(),
    )
    window._history_record = MethodType(MainWindow._history_record, window)
    window._update_history_actions = MethodType(MainWindow._update_history_actions, window)
    window._restore_history_snapshot = lambda snapshot: None
    viewport.scene_changed = _Signal(lambda: MainWindow._on_scene_changed(window))
    return window


def test_parameter_edits_are_undoable_one_at_a_time():
    state = {"value": 0}
    window = _history_window(state)
    MainWindow._history_reset(window)
    restored = []
    window._restore_history_snapshot = restored.append

    state["value"] = 1
    MainWindow._on_params_changed(window, None, {})
    state["value"] = 2
    MainWindow._on_params_changed(window, None, {})

    MainWindow._undo(window)

    assert restored == [[{"state": {"value": 1}}]]


def test_deleting_selected_object_records_an_undo_state():
    state = {"objects": ["Part"]}
    window = _history_window(state)
    MainWindow._history_reset(window)
    part = SimpleNamespace(name="Part")
    window._viewport.scene.selection = [part]
    window._viewport.scene.remove_object.side_effect = lambda _obj: state.update(objects=[])

    MainWindow._delete_selected(window)

    assert window._history_undo == [
        [{"state": {"objects": ["Part"]}}],
        [{"state": {"objects": []}}],
    ]


def test_history_serialization_caches_unchanged_mesh_and_boolean_provenance(monkeypatch):
    scene = SceneManager(vtk.vtkRenderer())
    source = vtk.vtkCubeSource()
    source.Update()
    mesh = MeshObject("Part", source.GetOutput())
    scene.add_object(mesh)
    boolean_result = MeshObject("Cut", vtk.vtkPolyData(), boolean_op="cut")
    boolean_result.boolean_sources_data = [{"name": "Part", "mesh": {"points": [[1, 2, 3]]}}]
    scene.add_object(boolean_result)

    original_serializer = SceneManager._polydata_to_json
    serialized_meshes = []

    def track_serialization(polydata):
        serialized_meshes.append(polydata)
        return original_serializer(polydata)

    monkeypatch.setattr(SceneManager, "_polydata_to_json", staticmethod(track_serialization))
    first = scene.to_json(cache_meshes=True)
    second = scene.to_json(cache_meshes=True)

    assert len(serialized_meshes) == 2
    assert first[0]["mesh"] is second[0]["mesh"]
    assert first[1]["params"]["BooleanSourcesData"][0] is second[1]["params"]["BooleanSourcesData"][0]

    mesh.actor.GetMapper().GetInput().Modified()
    scene.to_json(cache_meshes=True)

    assert len(serialized_meshes) == 3