import os
import json
import math
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy

import pytest
import vtk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMainWindow, QMenu, QToolButton

import em3d_modeler.ui.main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow, _BooleanCutSetupDialog
from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene.em_objects import (
    BoxObject, CylinderObject, ExtrudedObject, MeshObject, PlateObject, RevolvedObject,
)
from em3d_modeler.scene.scene_manager import SceneManager


def _main_window_stub(viewport):
    return SimpleNamespace(
        _viewport=viewport,
        _planar_face_sketch_plane=MainWindow._planar_face_sketch_plane,
        _start_embedded_sketch=Mock(),
        _open_reference_plane_dialog=Mock(),
    )


def test_recompute_model_updates_geometry_and_invalidates_simulation_assets():
    calls = []
    settings = {"parameters": []}
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: settings),
        _recompute_simulation_parameters=lambda passed_settings: calls.append(
            ("simulation parameters", passed_settings)
        ),
        _recompute_parametric_objects=lambda: calls.append(("geometry",)),
        recompute_sketch_dimensions=lambda: calls.append(("sketch dimensions",)),
        _mark_simulation_dirty=lambda **kwargs: calls.append(("dirty", kwargs)),
        _info_bar=SimpleNamespace(set_info=lambda message: calls.append(("info", message))),
    )

    MainWindow._recompute_model(window)

    assert calls == [
        ("simulation parameters", settings),
        ("geometry",),
        ("sketch dimensions",),
        ("dirty", {"steps": True, "script": True}),
        ("info", "Model recomputed."),
    ]


def test_cylinder_center_values_survive_regeneration_without_snap():
    cylinder = CylinderObject("Cylinder", cx=8, cy=9, cz=10)
    cylinder.creation_history = {
        "mode": "cylinder",
        "points": [{"value": [1, 2, 3], "snap": {"kind": "grid"}}],
    }
    window = SimpleNamespace(
        _resolve_creation_snap=lambda _snap, _objects: None,
    )

    MainWindow._regenerate_snap_dependent_objects(window, [cylinder])

    assert cylinder.get_parameters()["CenterX"] == pytest.approx(8)
    assert cylinder.get_parameters()["CenterY"] == pytest.approx(9)
    assert cylinder.get_parameters()["CenterZ"] == pytest.approx(10)


def test_snapped_cylinder_center_follows_source_without_accumulating():
    cylinder = CylinderObject("Cylinder", cx=1, cy=2, cz=8, height=10)
    cylinder.creation_history = {
        "mode": "cylinder",
        "points": [{
            "value": [1, 2, 3],
            "snap": {"kind": "vertex", "object": "Source"},
        }],
    }
    window = SimpleNamespace(
        _resolve_creation_snap=lambda _snap, _objects: [4, 5, 6],
    )

    MainWindow._regenerate_snap_dependent_objects(window, [cylinder])
    first_center = tuple(
        cylinder.get_parameters()[key] for key in ("CenterX", "CenterY", "CenterZ")
    )
    MainWindow._regenerate_snap_dependent_objects(window, [cylinder])
    second_center = tuple(
        cylinder.get_parameters()[key] for key in ("CenterX", "CenterY", "CenterZ")
    )

    assert first_center == pytest.approx((4, 5, 11))
    assert second_center == pytest.approx(first_center)


def test_snap_resolution_uses_matching_duplicate_name_source():
    target = [0.023301949171014445, -0.0022979770003264126, 1.55]
    sources = []
    for position_z in (0.0, 1.35, -1.3):
        actor = vtk.vtkActor()
        actor.SetOrientation(90.0, 0.0, 0.0)
        actor.SetPosition(0.0, 0.0, position_z)
        sources.append(SimpleNamespace(name="Cylinder_1", actor=actor))

    resolved = MainWindow._resolve_creation_snap(
        SimpleNamespace(),
        {
            "kind": "surface",
            "object": "Cylinder_1",
            "local": [0.02330194917101444, 0.2, 0.002297977000326412],
            "point": target,
        },
        {"Cylinder_1": sources},
    )

    assert resolved == pytest.approx(target)

    plate = PlateObject(
        "Plate_2",
        0.023301949171014445,
        -0.0022979770003264126,
        1.55,
        -0.027,
        -0.0061,
        1.625,
    )
    plate.creation_history = {
        "mode": "planar",
        "plane": "XZ",
        "points": [
            {
                "value": target,
                "snap": {
                    "kind": "surface",
                    "object": "Cylinder_1",
                    "local": [0.02330194917101444, 0.2, 0.002297977000326412],
                    "point": target,
                },
            },
            {"value": [-0.027, -0.0061, 1.625], "snap": {"kind": "grid"}},
        ],
    }
    window = SimpleNamespace()
    window._resolve_creation_snap = MethodType(MainWindow._resolve_creation_snap, window)
    MainWindow._regenerate_snap_dependent_objects(window, [plate, *sources])

    assert plate.get_parameters()["Z1"] == pytest.approx(1.55)


def test_boolean_recompute_restores_same_named_sources_individually(monkeypatch):
    from em3d_modeler.scene import boolean_ops

    sources = [
        CylinderObject("Cylinder_1", 0, 0, center_z, 0.1, height)
        for center_z, height in ((0.0, 2.3), (1.35, 0.4), (-1.3, 0.3))
    ]
    scene = SceneManager(vtk.vtkRenderer())
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=scene, _render=Mock()),
        _project_tree=SimpleNamespace(get_settings=lambda: {}),
        _parameter_values=lambda: {},
        _sync_boolean_provenance=lambda: None,
        _process_simulation_preparation_events=Mock(),
        _update_generated_open_region=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )
    window._serialize_object_snapshot = MethodType(MainWindow._serialize_object_snapshot, window)
    window._rebuild_object_from_snapshot = MethodType(MainWindow._rebuild_object_from_snapshot, window)
    snapshots = [window._serialize_object_snapshot(source) for source in sources]
    result = MeshObject(
        "Fused",
        vtk.vtkPolyData(),
        boolean_op="fuse",
        boolean_source_names=["Cylinder_1"] * 3,
        boolean_sources_data=snapshots,
    )
    scene.add_object(result)
    recomputed_sources = []
    window._replace_boolean_result_object = lambda _result, _polydata, restored: recomputed_sources.extend(restored)
    monkeypatch.setattr(boolean_ops, "fuse_many", lambda _sources, **_kwargs: vtk.vtkPolyData())
    window._recompute_pattern_instances = Mock(return_value=False)

    MainWindow._recompute_parametric_objects(window, _regenerate_snaps=False)

    assert len(recomputed_sources) == 3
    assert len({id(source) for source in recomputed_sources}) == 3
    assert [source.get_parameters()["CenterZ"] for source in recomputed_sources] == pytest.approx(
        [0.0, 1.35, -1.3]
    )


@pytest.mark.parametrize("keep_live_sources", [False, True])
def test_dissolve_restores_all_same_named_boolean_snapshots(monkeypatch, keep_live_sources):
    from PySide6.QtWidgets import QMessageBox

    sources = [
        CylinderObject("Cylinder_1", 0, 0, center_z, 0.1, height)
        for center_z, height in ((0.0, 2.3), (1.35, 0.4), (-1.3, 0.3))
    ]
    scene = SceneManager(vtk.vtkRenderer())
    result = MeshObject(
        "Fused",
        vtk.vtkPolyData(),
        boolean_op="fuse",
        boolean_source_names=["Cylinder_1"] * 3,
        boolean_sources_data=[
            {
                "type": type(source).__name__,
                "name": source.name,
                "params": source.get_parameters(),
                "visible": True,
                "is_model": True,
                "param_formulas": {},
                "creation_history": {},
                "actor_transform": {
                    "origin": list(source.actor.GetOrigin()),
                    "position": list(source.actor.GetPosition()),
                    "orientation": list(source.actor.GetOrientation()),
                    "scale": list(source.actor.GetScale()),
                },
            }
            for source in sources
        ],
    )
    result.source_objects = sources if keep_live_sources else []
    scene.add_object(result)
    scene.select(result)
    viewport = SimpleNamespace(
        scene=scene,
        object_selected=SimpleNamespace(emit=Mock()),
        selection_changed=SimpleNamespace(emit=Mock()),
        scene_changed=SimpleNamespace(emit=Mock()),
        _render=Mock(),
    )
    window = SimpleNamespace(
        _viewport=viewport,
        _rebuild_object_from_snapshot=MethodType(
            MainWindow._rebuild_object_from_snapshot, SimpleNamespace(_viewport=viewport)
        ),
        _refresh_materials=Mock(),
        _mark_broken_creation_references=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )
    monkeypatch.setattr(main_window_module.QMessageBox, "question", lambda *_args: QMessageBox.Yes)

    MainWindow._bool_dissolve(window)

    assert len(scene.objects) == 3
    assert len({id(source) for source in scene.objects}) == 3
    assert {source.name for source in scene.objects} == {
        "Cylinder_1", "Cylinder_2", "Cylinder_3",
    }


def test_scene_add_object_uses_the_first_available_name_index():
    scene = SceneManager(vtk.vtkRenderer())
    first = CylinderObject("Cylinder_1", 0, 0, 0, 1, 1)
    third = CylinderObject("Cylinder_3", 0, 0, 0, 1, 1)
    new_object = CylinderObject("Cylinder_9", 0, 0, 0, 1, 1)
    scene.add_object(first)
    scene.add_object(third)
    scene.add_object(new_object)

    assert third.name == "Cylinder_2"
    assert new_object.name == "Cylinder_3"
    scene.remove_object(third)
    replacement = CylinderObject("Cylinder_1", 0, 0, 0, 1, 1)
    scene.add_object(replacement)

    assert replacement.name == "Cylinder_2"


def test_object_rename_conflict_warns_without_changing_name_or_references(monkeypatch):
    existing = SimpleNamespace(name="Cylinder_1")
    target = SimpleNamespace(name="Cylinder_2")
    warnings = []
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[existing, target])),
        _project_tree=SimpleNamespace(rename_object_references=Mock()),
        _history_record=Mock(),
        _refresh_materials=Mock(),
        _materials=SimpleNamespace(highlight=Mock()),
        _body_props=SimpleNamespace(set_object=Mock()),
        _info_bar=SimpleNamespace(set_info=Mock()),
        _mark_simulation_dirty=Mock(),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    MainWindow._on_materials_rename(window, target, "Cylinder_1")

    assert target.name == "Cylinder_2"
    assert len(warnings) == 1
    window._project_tree.rename_object_references.assert_not_called()
    window._history_record.assert_not_called()


def test_bulk_rename_conflict_warns_without_renaming_any_object(monkeypatch):
    existing = SimpleNamespace(name="Body_2")
    first = SimpleNamespace(name="Old_A")
    second = SimpleNamespace(name="Old_B")
    warnings = []
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[existing, first, second])),
        _project_tree=SimpleNamespace(rename_object_references=Mock()),
        _history_record=Mock(),
        _refresh_materials=Mock(),
        _materials=SimpleNamespace(highlight=Mock()),
        _info_bar=SimpleNamespace(set_info=Mock()),
        _mark_simulation_dirty=Mock(),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    MainWindow._on_materials_bulk_rename(window, [first, second], "Body", 1)

    assert (first.name, second.name) == ("Old_A", "Old_B")
    assert len(warnings) == 1
    window._project_tree.rename_object_references.assert_not_called()


def test_boolean_cut_dialog_starts_with_first_base_and_moves_multiple_objects():
    application = QApplication.instance() or QApplication([])
    objects = [
        SimpleNamespace(name=f"Body_{index}")
        for index in range(1, 5)
    ]
    dialog = _BooleanCutSetupDialog(objects)

    assert dialog.base_objects == [objects[0]]
    assert dialog.tool_objects == objects[1:]
    assert dialog.keep_tools is False
    dialog.keep_tools_checkbox.setChecked(True)
    assert dialog.keep_tools is True
    dialog.keep_tools_checkbox.setChecked(False)

    dialog.tools_list.item(0).setSelected(True)
    dialog.tools_list.item(1).setSelected(True)
    dialog.to_base_button.click()

    assert dialog.base_objects == [objects[0], objects[1], objects[2]]
    assert dialog.tool_objects == [objects[3]]

    dialog.base_list.item(1).setSelected(True)
    dialog.base_list.item(2).setSelected(True)
    dialog.to_tools_button.click()

    assert dialog.base_objects == [objects[0]]
    assert dialog.tool_objects == [objects[3], objects[1], objects[2]]
    dialog.close()
    assert application is not None


@pytest.mark.parametrize("keep_tools", [False, True])
def test_boolean_cut_applies_each_tool_to_every_base(monkeypatch, keep_tools):
    from em3d_modeler.scene import boolean_ops

    bases = [
        BoxObject("Base_1", 0, 0, 0, 2, 2, 2),
        BoxObject("Base_2", 4, 0, 0, 6, 2, 2),
    ]
    tools = [BoxObject("Tool", 1, 0, 0, 3, 2, 2)]
    scene = SceneManager(vtk.vtkRenderer())
    for obj in bases + tools:
        scene.add_object(obj)
    scene.select(bases[0])
    scene.select_add(bases[1])
    scene.select_add(tools[0])
    boolean_calls = []

    class CutDialog:
        def __init__(self, _objects, _parent):
            self.base_objects = bases
            self.tool_objects = tools
            self.keep_tools = keep_tools

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(main_window_module, "_BooleanCutSetupDialog", CutDialog)
    monkeypatch.setattr(
        boolean_ops,
        "boolean_many",
        lambda op, objects, **_kwargs: (
            boolean_calls.append((op, list(objects))) or vtk.vtkPolyData()
        ),
    )
    window = SimpleNamespace(
        _viewport=SimpleNamespace(
            scene=scene,
            object_selected=SimpleNamespace(emit=Mock()),
            selection_changed=SimpleNamespace(emit=Mock()),
            scene_changed=SimpleNamespace(emit=Mock()),
            _render=Mock(),
        ),
        _boolean_decimation_enabled=False,
        _is_plate_role_object=lambda _obj: False,
        _serialize_object_snapshot=lambda _obj: {},
        _refresh_materials=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )
    window._serialize_object_snapshot = lambda obj: {
        "name": obj.name,
        "type": type(obj).__name__,
        "params": obj.get_parameters(),
    }

    MainWindow._do_boolean(window, "cut", "Cut")

    assert boolean_calls == [
        ("cut", [bases[0], tools[0]]),
        ("cut", [bases[1], tools[0]]),
    ]
    results = [obj for obj in scene.objects if getattr(obj, "boolean_op", None) == "cut"]
    assert len(results) == 2
    assert all(result.source_objects[1] is tools[0] for result in results)
    assert (tools[0] in scene.objects) is keep_tools


def test_parametric_recompute_recenters_generated_air_and_pml_regions():
    model = BoxObject("Model", 10, 20, 30, 40, 60, 80, "Copper")
    air = BoxObject("Air_Region", -10, -10, -10, 60, 70, 90, "AIR")
    outer = BoxObject("PML_Region", -20, -20, -20, 70, 80, 100, "PML")
    settings = {
        "open_region": {
            "enabled": True,
            "object": "Air_Region",
            "auto_update": True,
            "distance_mm": 5,
        },
        "pml": {
            "enabled": True,
            "auto_update": True,
            "air_object": "Air_Region",
            "outer_object": "PML_Region",
            "thickness_mm": 3,
        },
    }
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: settings),
        _viewport=SimpleNamespace(
            scene=SimpleNamespace(objects=[model, air, outer]),
        ),
    )

    MainWindow._update_generated_open_region(window)

    assert (air.x1, air.x2, air.y1, air.y2, air.z1, air.z2) == pytest.approx(
        (5, 45, 15, 65, 25, 85)
    )
    assert (outer.x1, outer.x2, outer.y1, outer.y2, outer.z1, outer.z2) == pytest.approx(
        (2, 48, 12, 68, 22, 88)
    )


def test_legacy_open_region_extension_is_inferred_saved_and_reused():
    model = BoxObject("Model", 10, 20, 30, 40, 60, 80, "Copper")
    air = BoxObject("Air_Region", 3, 13, 23, 47, 67, 87, "AIR")
    stored_settings = {
        "open_region": {"enabled": True, "object": "Air_Region"},
        "pml": {
            "enabled": False,
            "air_object": "Air_Region",
        },
    }
    tree = SimpleNamespace(
        get_settings=lambda: deepcopy(stored_settings),
        load_settings=lambda settings: stored_settings.update(deepcopy(settings)),
        settings_changed=SimpleNamespace(emit=Mock()),
    )
    window = SimpleNamespace(
        _project_tree=tree,
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=[model, air])),
    )

    MainWindow._update_generated_open_region(window)

    assert stored_settings["open_region"]["distance_mm"] == pytest.approx(7)
    assert (air.x1, air.x2, air.y1, air.y2, air.z1, air.z2) == pytest.approx(
        (3, 47, 13, 67, 23, 87)
    )

    model.set_parameters({"X1": 0, "X2": 100, "Y1": 5, "Y2": 45, "Z1": 8, "Z2": 88})
    MainWindow._update_generated_open_region(window)

    assert stored_settings["open_region"]["distance_mm"] == pytest.approx(7)
    assert (air.x1, air.x2, air.y1, air.y2, air.z1, air.z2) == pytest.approx(
        (-7, 107, -2, 52, 1, 95)
    )
    tree.settings_changed.emit.assert_called_once_with()


def test_progressive_stdout_parser_buffers_split_json_lines():
    payload = {
        "simulation": "Sweep",
        "frequencies": [1e9],
        "s_matrices": [[[[0.1, 0.0]]]],
        "completed_samples": 1,
        "expected_samples": 1,
        "complete": True,
    }
    process = SimpleNamespace(readAllStandardOutput=Mock(side_effect=[
        b"EM3D_SPARAM_PROG",
        ("RESS:" + json.dumps(payload) + "\n[info] done\n").encode(),
    ]))
    window = SimpleNamespace(
        _sim_process=process,
        _sim_stdout_buffer="",
        _update_progressive_sparams_plot=Mock(),
        _append_sim_log=Mock(),
    )
    window._process_sim_stdout_line = MethodType(MainWindow._process_sim_stdout_line, window)

    MainWindow._on_sim_process_output(window)
    assert window._sim_stdout_buffer == "EM3D_SPARAM_PROG"
    MainWindow._on_sim_process_output(window)

    window._update_progressive_sparams_plot.assert_called_once_with(payload)
    window._append_sim_log.assert_called_once_with("[info] done")
    assert window._sim_stdout_buffer == ""


def test_completed_job_stdout_event_dispatches_final_output_plotting():
    window = SimpleNamespace(
        _plot_completed_simulation_outputs=Mock(),
        _append_sim_log=Mock(),
    )

    MainWindow._process_sim_stdout_line(
        window,
        'EM3D_JOB_COMPLETED:{"name":"Sweep","filename":"sweep.py"}',
    )

    window._plot_completed_simulation_outputs.assert_called_once_with("Sweep")
    window._append_sim_log.assert_not_called()


def test_progressive_plot_preserves_s_output_input_orientation():
    show_chart = Mock()
    window = SimpleNamespace(
        _sim_progressive_plot_data=None,
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{
                "name": "Sweep",
                "type": "Sweep",
                "progressive_sparams_enabled": True,
                "Fmin_GHz": 0.1,
                "Fmax_GHz": 10.0,
            }],
            "outputs": [{
                "name": "Selected Sij",
                "simulation": "Sweep",
                "plot_type": "plot_sp",
                "params": {"s_parameters": ["S12", "S21"]},
            }],
        }),
        _show_chart=show_chart,
    )
    payload = {
        "simulation": "Sweep",
        "frequencies": [1e9],
        "s_matrices": [[[[0.1, 0.0], [0.2, 0.0]], [[0.3, 0.0], [0.4, 0.0]]]],
        "completed_samples": 1,
        "expected_samples": 1,
        "complete": True,
    }

    MainWindow._update_progressive_sparams_plot(window, payload)

    series = show_chart.call_args.args[1]["series"]
    assert {item["label"]: item["values"] for item in series} == {
        "S11": [0.1 + 0.0j],
        "S12": [0.2 + 0.0j],
        "S21": [0.3 + 0.0j],
        "S22": [0.4 + 0.0j],
    }
    assert [item["visible"] for item in series] == [False, True, True, False]
    assert show_chart.call_args.args[1]["x_range"] == (0.1, 10.0)
    assert show_chart.call_args.args[1]["title"] == "Selected Sij (1/1 samples)"
    assert show_chart.call_args.kwargs == {"progressive": True}


def test_parametric_progressive_events_add_one_trace_per_parameter_value(monkeypatch):
    show_chart = Mock()
    monkeypatch.setattr(MainWindow, "_refresh_simulation_status", Mock())
    window = SimpleNamespace(
        _sim_progressive_plot_data=None,
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{
                "name": "Parametric",
                "type": "Parametric",
                "progressive_sparams_enabled": True,
                "Fmin_GHz": 0.1,
                "Fmax_GHz": 10.0,
            }],
            "outputs": [{
                "name": "Width sweep",
                "simulation": "Parametric",
                "plot_type": "plot_sp",
                "params": {"s_parameters": ["S11"]},
            }],
        }),
        _show_chart=show_chart,
    )
    window._update_progressive_parametric_sparams_plot = MethodType(
        MainWindow._update_progressive_parametric_sparams_plot, window
    )

    for index, value in enumerate(("0.2", "0.4"), start=1):
        for chunk_start, frequencies, values in (
            (0, [1e9], [index / 10]),
            (1, [2e9], [index / 5]),
        ):
            MainWindow._update_progressive_sparams_plot(window, {
                "simulation": "Parametric",
                "parameter_name": "width",
                "parameter_value": value,
                "parameter_index": index,
                "completed_parameters": index,
                "expected_parameters": 2,
                "chunk_start": chunk_start,
                "completed_samples": chunk_start + 1,
                "expected_samples": 2,
                "frequencies": frequencies,
                "s_matrices": [[[[sample_value, 0.0]]] for sample_value in values],
                "complete": chunk_start == 1,
            })
            if index == 2 and chunk_start == 0:
                partial_chart = show_chart.call_args.args[1]
                assert [item["x_values"] for item in partial_chart["series"]] == [
                    [1.0, 2.0], [1.0],
                ]
                assert partial_chart["x_range"] == (0.1, 10.0)
                assert "width=0.4, 1/2 samples" in partial_chart["title"]

    chart_data = show_chart.call_args.args[1]
    assert [item["file_name"] for item in chart_data["series"]] == [
        "width=0.2", "width=0.4",
    ]
    assert [item["values"] for item in chart_data["series"]] == [
        [0.1 + 0j, 0.2 + 0j], [0.2 + 0j, 0.4 + 0j],
    ]
    assert show_chart.call_args.kwargs == {"progressive": True}


def test_reset_progressive_plot_clears_existing_live_output_chart():
    chart_view = Mock()
    window = SimpleNamespace(
        _sim_progressive_plot_data={"simulation": "Sweep"},
        _sim_stdout_buffer="partial line",
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{
                "name": "Sweep",
                "type": "Sweep",
                "progressive_sparams_enabled": True,
            }],
            "outputs": [{
                "name": "Output_1",
                "simulation": "Sweep",
                "plot_type": "plot_sp",
                "enabled": True,
            }],
        }),
        _plot_views={"Sweep::Output_1": chart_view},
    )

    MainWindow._reset_progressive_sparams_plot(window)

    assert window._sim_progressive_plot_data is None
    assert window._sim_stdout_buffer == ""
    chart_view.clear_data.assert_called_once_with()


def test_final_plot_uses_first_results_directory_with_simdata():
    empty_results = Path("unused.EMResults")
    valid_results = Path("project.EMResults")
    plot_output = Mock()
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "outputs": [{"name": "Output_1", "simulation": "Sweep", "enabled": True}],
        }),
        _candidate_results_dirs_for_sim=lambda _name: [empty_results, valid_results],
        _simdata_file_in_dir=lambda path: Path(path) == valid_results,
        _on_output_plot_requested=plot_output,
        _append_sim_log=Mock(),
    )

    MainWindow._plot_completed_simulation_outputs(window, "Sweep")

    assert plot_output.call_args.kwargs == {"results_dir": valid_results}


def test_completed_parametric_job_keeps_existing_live_chart():
    plot_output = Mock()
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{
                "name": "Parametric",
                "type": "Parametric",
                "progressive_sparams_enabled": True,
            }],
            "outputs": [{
                "name": "Width sweep",
                "simulation": "Parametric",
                "plot_mode": "live",
                "enabled": True,
            }],
        }),
        _candidate_results_dirs_for_sim=lambda _name: [Path("results")],
        _simdata_file_in_dir=lambda path: Path(path),
        _simulation_bundle_dir=lambda: Path("bundle"),
        _plot_views={"Parametric::Width sweep": object()},
        _sim_progressive_plot_data={
            "simulation": "Parametric",
            "expected_parameters": 1,
            "expected_samples": 2,
            "parameter_runs": {
                1: {"value": "0.2", "frequencies": [1e9, 2e9]},
            },
        },
        _on_output_plot_requested=plot_output,
        _append_sim_log=Mock(),
    )

    MainWindow._plot_completed_simulation_outputs(window, "Parametric")

    plot_output.assert_not_called()


def test_completed_parametric_job_reloads_incomplete_live_chart():
    plot_output = Mock()
    result_dir = Path("results")
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "outputs": [{
                "name": "Width sweep",
                "simulation": "Parametric",
                "plot_mode": "live",
                "enabled": True,
            }],
        }),
        _candidate_results_dirs_for_sim=lambda _name: [result_dir],
        _simdata_file_in_dir=lambda path: Path(path),
        _simulation_bundle_dir=lambda: Path("bundle"),
        _plot_views={"Parametric::Width sweep": object()},
        _sim_progressive_plot_data={
            "simulation": "Parametric",
            "expected_parameters": 1,
            "expected_samples": 4,
            "parameter_runs": {
                1: {"value": "0.2", "frequencies": [1e9, 2e9]},
            },
        },
        _on_output_plot_requested=plot_output,
        _append_sim_log=Mock(),
    )

    MainWindow._plot_completed_simulation_outputs(window, "Parametric")

    assert plot_output.call_args.kwargs == {"results_dir": result_dir}


@pytest.mark.parametrize(
    ("selected_parameters", "expected_visibility"),
    [
        (["S21", "S11"], [True, False, True, False]),
        ([], [False, False, False, False]),
    ],
)
def test_loading_legacy_touchstone_appends_to_source_chart(
    tmp_path, monkeypatch, selected_parameters, expected_visibility
):
    legacy_path = tmp_path / "previous_run.s2p"
    legacy_path.write_text(
        "# GHZ S RI R 50\n1 0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="ascii",
    )
    chart_view = SimpleNamespace(
        selected_parameters=Mock(return_value=selected_parameters),
        configured_parameters=Mock(return_value=["S11"]),
        add_file_data=Mock(),
    )
    window = SimpleNamespace(
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _project_name="Model",
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{"name": "Sweep"}],
            "outputs": [{
                "name": "Configured output",
                "simulation": "Sweep",
            }],
        }),
        _plot_views={"Sweep::Configured output": chart_view},
    )
    window._touchstone_directory_for_chart = (
        MainWindow._touchstone_directory_for_chart.__get__(window, MainWindow)
    )
    file_dialog = Mock(return_value=(
        str(tmp_path / "Touchstone" / legacy_path.name), ""
    ))
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        file_dialog,
    )

    MainWindow._load_touchstone_for_chart(window, "Sweep::Configured output")

    assert file_dialog.call_args.args[2] == str(tmp_path / "Touchstone")
    chart_view.add_file_data.assert_called_once()
    x_values, series = chart_view.add_file_data.call_args.args
    assert [item["label"] for item in series] == [
        "S11", "S12", "S21", "S22",
    ]
    assert x_values == [1.0]
    assert [item["visible"] for item in series] == expected_visibility
    assert {item["file_name"] for item in series} == {legacy_path.name}
    assert (tmp_path / "Touchstone" / legacy_path.name).is_file()


def test_loading_touchstone_starts_in_the_source_simulation_folder(
    tmp_path, monkeypatch
):
    bundle_dir = tmp_path / "bundle"
    source_simulation_dir = bundle_dir / "Model_Sweep_B"
    other_simulation_dir = bundle_dir / "Model_Sweep_A"
    source_simulation_dir.mkdir(parents=True)
    other_simulation_dir.mkdir(parents=True)
    legacy_bundle_file = bundle_dir / "legacy.s1p"
    legacy_bundle_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_bundle_file.write_text("# legacy input\n", encoding="ascii")
    chart_view = SimpleNamespace(
        selected_parameters=Mock(return_value=[]),
        configured_parameters=Mock(return_value=[]),
        add_file_data=Mock(),
    )
    window = SimpleNamespace(
        _simulation_bundle_dir=Mock(return_value=bundle_dir),
        _project_name="Model",
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [
                {"name": "Sweep A"},
                {"name": "Sweep B"},
            ],
            "outputs": [
                {"name": "Configured output", "simulation": "Sweep A"},
                {"name": "Configured output", "simulation": "Sweep B"},
            ],
        }),
        _plot_views={"Sweep B::Configured output": chart_view},
    )
    window._touchstone_directory_for_chart = (
        MainWindow._touchstone_directory_for_chart.__get__(window, MainWindow)
    )
    file_dialog = Mock(return_value=("", ""))
    monkeypatch.setattr(
        main_window_module.QFileDialog, "getOpenFileName", file_dialog
    )

    MainWindow._load_touchstone_for_chart(window, "Sweep B::Configured output")

    assert file_dialog.call_args.args[2] == str(
        source_simulation_dir / "Touchstone"
    )
    assert (source_simulation_dir / "Touchstone").is_dir()
    assert (bundle_dir / "Touchstone" / legacy_bundle_file.name).is_file()
    assert not legacy_bundle_file.exists()
    assert not (other_simulation_dir / "Touchstone").exists()


def test_append_mode_routes_later_simulation_output_to_active_chart():
    active_chart = SimpleNamespace(
        plot_type="plot_sp",
        append_plot_data=Mock(),
    )
    window = SimpleNamespace(
        _chart_output_name=MainWindow._chart_output_name,
        _append_chart_keys={"Transmission": "Sweep A::Transmission"},
        _append_progressive_run_ids={},
        _append_run_sequence=0,
        _plot_views={"Sweep A::Transmission": active_chart},
    )

    MainWindow._show_chart(
        window,
        "Sweep B::Transmission",
        {
            "title": "Transmission",
            "plot_type": "plot_sp",
            "x_values": [1.0],
            "series": [{"label": "S21", "values": [0.5]}],
        },
    )

    active_chart.append_plot_data.assert_called_once_with(
        [1.0],
        [{"label": "S21", "values": [0.5]}],
        title="Transmission",
        xlabel="",
        ylabel="",
    )


def test_append_mode_replaces_progressive_run_with_final_data():
    saved_overlay_ids = []
    active_chart = SimpleNamespace(
        plot_type="plot_sp",
        _overlay_file_ids={"old", "Sweep B::Transmission::append-run-1::live"},
    )

    def capture_overlay_ids(*_args, **_kwargs):
        saved_overlay_ids.append(set(active_chart._overlay_file_ids))

    active_chart.set_progressive_data = Mock(side_effect=capture_overlay_ids)
    window = SimpleNamespace(
        _chart_output_name=MainWindow._chart_output_name,
        _append_chart_keys={"Transmission": "Sweep A::Transmission"},
        _append_progressive_run_ids={
            "Sweep B::Transmission": "Sweep B::Transmission::append-run-1"
        },
        _append_run_sequence=1,
        _plot_views={"Sweep A::Transmission": active_chart},
    )
    final_data = {
        "title": "Transmission",
        "plot_type": "plot_sp",
        "x_values": [1.0],
        "series": [{"label": "S21", "values": [0.5], "file_id": "touchstone"}],
    }

    MainWindow._show_chart(window, "Sweep B::Transmission", final_data)

    active_chart.set_progressive_data.assert_called_once()
    final_series = active_chart.set_progressive_data.call_args.args[1]
    assert final_series[0]["file_id"].endswith("::touchstone")
    assert active_chart._overlay_file_ids == {"old", final_series[0]["file_id"]}
    assert saved_overlay_ids == [{"old", final_series[0]["file_id"]}]
    assert window._append_progressive_run_ids == {}


def test_generate_plot_uses_saved_touchstone_without_loading_emerge(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    (touchstone_dir / "Model_Sweep_20260929-120000.s2p").write_text(
        "# GHZ S RI R 50\n2 0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="ascii",
    )
    show_chart = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _load_sim_grid_from_results=Mock(side_effect=AssertionError("EMERGE result loader called")),
        _show_chart=show_chart,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _append_sim_log=Mock(),
    )

    MainWindow._on_output_plot_requested(
        window,
        {
            "name": "Transmission",
            "simulation": "Sweep",
            "plot_type": "plot_sp",
            "params": {"s_parameters": ["S21"]},
        },
    )

    chart_key, chart_data = show_chart.call_args.args
    assert chart_key == "Sweep::Transmission"
    assert chart_data["x_values"] == [2.0]
    assert [item["label"] for item in chart_data["series"]] == ["S11", "S12", "S21", "S22"]
    assert [item["visible"] for item in chart_data["series"]] == [False, False, True, False]
    assert chart_data["series"][2]["values"] == [0.2 + 0j]
    assert chart_data["series"][0]["file_name"].endswith(".s2p")
    window._load_sim_grid_from_results.assert_not_called()
    window._append_sim_log.assert_called_once()


def test_generate_plot_ff_3d_opens_emerge_window_at_nearest_solved_frequency():
    farfield = object()
    field_entry = SimpleNamespace(farfield_3d=Mock(return_value=farfield))
    field_data = SimpleNamespace(find=Mock(return_value=field_entry))
    geometry = SimpleNamespace(boundary=Mock(return_value=["face"]))
    plotter = SimpleNamespace()
    display = SimpleNamespace(
        add_object=Mock(),
        add_farfield3d=Mock(),
        show=Mock(),
        _plot=plotter,
        clean=Mock(),
    )
    loaded_sim = SimpleNamespace(
        data=SimpleNamespace(mw=SimpleNamespace(field=field_data)),
        all_geos=lambda: [geometry],
        display=display,
    )
    grid = SimpleNamespace(freq=[0.9e9, 1.0e9, 1.1e9])
    show_chart = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _load_sim_grid_from_results=Mock(return_value=(loaded_sim, grid, "simdata.emerge")),
        _show_chart=show_chart,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _append_sim_log=Mock(),
        _farfield_displays=[],
    )
    window._show_farfield_3d_display = MethodType(
        MainWindow._show_farfield_3d_display,
        window,
    )

    MainWindow._on_output_plot_requested(
        window,
        {
            "name": "Radiation pattern",
            "simulation": "Sweep",
            "plot_type": "plot_ff_3d",
            "params": {"frequency_GHz": 1.04},
        },
    )

    show_chart.assert_not_called()
    field_data.find.assert_called_once_with(freq=1.0e9)
    field_entry.farfield_3d.assert_called_once_with(["face"])
    display.add_farfield3d.assert_called_once_with(
        farfield,
        component="normE",
        quantity="abs",
        dB=True,
        dBfloor=-40,
        rmax=None,
        opacity=0.75,
    )
    display.show.assert_called_once_with()
    assert window._farfield_displays == [display]
    display.clean.assert_not_called()
    assert "at 1 GHz" in window._info_bar.set_info.call_args.args[0]
    assert "at 1 GHz" in window._info_bar.set_info.call_args.args[0]
    assert "at 1 GHz" in window._append_sim_log.call_args.args[0]


def test_generate_plot_opens_empty_chart_when_simulation_has_no_results(tmp_path):
    show_chart = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _candidate_results_dirs_for_sim=Mock(return_value=[tmp_path]),
        _simdata_file_in_dir=Mock(return_value=None),
        _load_sim_grid_from_results=Mock(side_effect=RuntimeError("no simdata")),
        _show_chart=show_chart,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _append_sim_log=Mock(),
    )

    MainWindow._on_output_plot_requested(window, {
        "name": "First-run S-parameters",
        "simulation": "Parametric",
        "plot_type": "plot_sp",
        "params": {"s_parameters": ["S11"]},
    })

    chart_key, chart_data = show_chart.call_args.args
    assert chart_key == "Parametric::First-run S-parameters"
    assert chart_data["x_values"] == []
    assert chart_data["series"] == []
    assert chart_data["plot_type"] == "plot_sp"
    window._load_sim_grid_from_results.assert_called_once()
    window._info_bar.set_info.assert_called_once()


def test_generate_parametric_plot_uses_raw_results_when_grid_is_irregular(tmp_path):
    scalar_data = SimpleNamespace(
        _variables=[
            {"freq": frequency, "width": value}
            for value in (0.2, 0.4)
            for frequency in (1e9, 2e9)
        ],
        _data_entries=[
            SimpleNamespace(freq=frequency, Sp=[[complex(index, 0.5)]])
            for index, frequency in enumerate((1e9, 2e9, 1e9, 2e9), start=1)
        ],
    )
    simulation_data = tmp_path / "simdata.emerge"
    loader = Mock(return_value=(object(), scalar_data, simulation_data))
    show_chart = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{
                "name": "Simulation_2",
                "type": "Parametric",
                "ParamName": "width",
                "ParamValues": "0.2,0.4",
                "NumberOfPoints": 2,
            }],
        }),
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _load_sim_grid_from_results=loader,
        _show_chart=show_chart,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _append_sim_log=Mock(),
    )

    MainWindow._on_output_plot_requested(window, {
        "name": "Output_2",
        "simulation": "Simulation_2",
        "plot_type": "plot_sp",
        "params": {"s_parameters": ["S11"]},
    })

    loader.assert_called_once_with(
        "Simulation_2", results_dir=None, allow_irregular=True
    )
    chart_key, chart_data = show_chart.call_args.args
    assert chart_key == "Simulation_2::Output_2"
    assert [series["file_name"] for series in chart_data["series"]] == [
        "width=0.2", "width=0.4",
    ]
    assert [series["values"] for series in chart_data["series"]] == [
        [1 + 0.5j, 2 + 0.5j], [3 + 0.5j, 4 + 0.5j],
    ]


def test_results_loader_can_return_raw_scalar_for_irregular_parametric_data(tmp_path):
    result_dir = tmp_path / "Model_Simulation_2.EMResults"
    result_dir.mkdir()
    simdata = result_dir / "simdata.emerge"
    simdata.write_text("", encoding="ascii")

    class IrregularScalar:
        _variables = [{"freq": 1e9}]
        _data_entries = [SimpleNamespace(freq=1e9, Sp=[[1 + 0j]])]

        @property
        def grid(self):
            raise ValueError("Data not in regular grid")

    loaded_simulation = SimpleNamespace(
        data=SimpleNamespace(mw=SimpleNamespace(scalar=IrregularScalar()))
    )
    window = SimpleNamespace(
        _resolve_emerge_simulation_ctor=lambda: lambda *_args, **_kwargs: loaded_simulation,
        _simdata_file_in_dir=lambda directory: (
            Path(directory) / "simdata.emerge"
            if (Path(directory) / "simdata.emerge").is_file()
            else None
        ),
        _simulation_bundle_dir=lambda: tmp_path,
    )

    simulation, scalar, loaded_path = MainWindow._load_sim_grid_from_results(
        window, "Simulation_2", result_dir, allow_irregular=True
    )

    assert simulation is loaded_simulation
    assert isinstance(scalar, IrregularScalar)
    assert loaded_path == simdata


def test_completed_parametric_plot_uses_nested_touchstones_without_root_results(tmp_path):
    touchstone_dir = (
        tmp_path / "Model_Simulation_2" / "Step_001_width_0_2" / "Touchstone"
    )
    touchstone_dir.mkdir(parents=True)
    (touchstone_dir / "Model_Simulation_2_width_0_2_20260930-120000.s1p").write_text(
        "# HZ S RI R 50\n1000000000 0.2 0\n", encoding="ascii"
    )
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "simulations": [{"name": "Simulation_2", "type": "Parametric"}],
            "outputs": [{
                "name": "S-parameters",
                "simulation": "Simulation_2",
                "plot_type": "plot_sp",
                "params": {"s_parameters": ["S11"]},
            }],
        }),
        _candidate_results_dirs_for_sim=lambda _name: [],
        _simdata_file_in_dir=lambda _path: None,
        _simulation_bundle_dir=lambda: tmp_path,
        _project_name="Model",
        _append_sim_log=Mock(),
        _on_output_plot_requested=Mock(),
        _plot_views={},
        _sim_progressive_plot_data=None,
    )

    MainWindow._plot_completed_simulation_outputs(window, "Simulation_2")

    window._on_output_plot_requested.assert_called_once_with(
        window._project_tree.get_settings()["outputs"][0],
        results_dir=None,
    )


def test_final_parametric_plot_loads_every_touchstone_from_latest_run(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    first = touchstone_dir / "Model_Parametric_width_0_2_20260929-120000.s1p"
    second = touchstone_dir / "Model_Parametric_width_0_4_20260929-120000.s1p"
    old = touchstone_dir / "Model_Parametric_width_0_2_20260928-120000.s1p"
    for path, value in ((first, 0.2), (second, 0.4), (old, 0.9)):
        path.write_text(f"# GHZ S RI R 50\n1 {value} 0\n", encoding="ascii")
    show_chart = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _load_sim_grid_from_results=Mock(side_effect=AssertionError("EMERGE loader called")),
        _show_chart=show_chart,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _append_sim_log=Mock(),
    )

    MainWindow._on_output_plot_requested(window, {
        "name": "Parametric S-parameters",
        "simulation": "Parametric",
        "plot_type": "plot_sp",
        "params": {"s_parameters": ["S11"]},
    })

    chart_data = show_chart.call_args.args[1]
    assert len(chart_data["series"]) == 2
    assert {series["values"][0] for series in chart_data["series"]} == {0.2 + 0j, 0.4 + 0j}
    assert {series["file_name"] for series in chart_data["series"]} == {first.name, second.name}


def test_planar_face_pick_produces_centroid_and_unit_normal():
    plane = MainWindow._planar_face_sketch_plane({
        "points": [(2, 4, 1), (6, 4, 1), (6, 8, 1), (2, 8, 1)],
    })

    assert plane == ((4.0, 6.0, 1.0), (0.0, 0.0, 1.0))


def test_measure_mode_temporarily_disables_cancel_drawing_shortcut():
    cancel_action = Mock()
    window = SimpleNamespace(_act_cancel_drawing=cancel_action)

    MainWindow._set_cancel_drawing_enabled(window, True)
    cancel_action.setEnabled.assert_called_with(False)

    MainWindow._set_cancel_drawing_enabled(window, False)
    cancel_action.setEnabled.assert_called_with(True)


def test_copy_selected_object_by_vertices_preserves_orientation_and_moves_reference_vertex():
    source_actor = Mock()
    source = SimpleNamespace(name="Part", actor=source_actor)
    clone_actor = Mock()
    clone = SimpleNamespace(name="Part_Copy_2", actor=clone_actor)
    scene = SimpleNamespace(
        selection=[source],
        objects=[source, SimpleNamespace(name="Part_Copy")],
        add_object=Mock(),
        deselect_all=Mock(),
        select_add=Mock(),
    )
    pick_requests = []
    viewport = SimpleNamespace(
        scene=scene,
        request_pick=lambda kind, callback, **kwargs: pick_requests.append((kind, callback, kwargs)),
        _render=Mock(),
        scene_changed=SimpleNamespace(emit=Mock()),
        selection_changed=SimpleNamespace(emit=Mock()),
    )
    snapshot = {
        "name": "Part",
        "params": {"Name": "Part"},
        "pattern_definition": {"pattern_id": "pattern"},
        "pattern_instance": {"instance_index": 1},
    }
    window = SimpleNamespace(
        _viewport=viewport,
        _serialize_object_snapshot=Mock(return_value=snapshot),
        _rebuild_object_from_snapshot=Mock(return_value=clone),
        _refresh_materials=Mock(),
        _sync_port_reference_state=Mock(),
        _mark_simulation_dirty=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )

    MainWindow._copy_selected_object_by_vertices(window)

    assert len(pick_requests) == 1
    kind, pick_reference, options = pick_requests[0]
    assert kind == "vertex"
    assert options == {"actor_filter": source_actor}
    pick_reference((1.0, 2.0, 3.0))
    assert len(pick_requests) == 2
    target_kind, create_copy, target_options = pick_requests[1]
    assert target_kind == "vertex"
    assert target_options == {}

    create_copy((4.5, -1.0, 5.0))

    clone_actor.AddPosition.assert_called_once_with(3.5, -3.0, 2.0)
    clone_data = window._rebuild_object_from_snapshot.call_args.args[0]
    assert clone_data["name"] == "Part_Copy_2"
    assert clone_data["params"]["Name"] == "Part_Copy_2"
    assert clone_data["pattern_definition"] is None
    assert clone_data["pattern_instance"] is None
    scene.add_object.assert_called_once_with(clone)
    scene.select_add.assert_called_once_with(clone)
    viewport.scene_changed.emit.assert_called_once_with()
    viewport.selection_changed.emit.assert_called_once_with([clone])
    window._mark_simulation_dirty.assert_called_once_with(steps=True, script=True)


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


def test_source_parameter_edit_rebuilds_pattern_copies_before_history_record():
    scene = SceneManager(vtk.vtkRenderer())
    source = BoxObject("Source", x2=2)
    scene.add_object(source)
    window = SimpleNamespace(_viewport=SimpleNamespace(scene=scene))
    window._serialize_object_snapshot = MethodType(MainWindow._serialize_object_snapshot, window)
    window._rebuild_object_from_snapshot = MethodType(MainWindow._rebuild_object_from_snapshot, window)
    source_snapshot = window._serialize_object_snapshot(source)
    definition = {
        "pattern_id": "linear-source-edit",
        "sources": [source_snapshot],
        "settings": {
            "mode": "Linear",
            "axis_enabled": [True, False, False],
            "expressions": {"axis_count_x": "3", "offset_x": "5"},
            "resolved": {},
        },
    }
    for instance_index, offset in enumerate((5, 10), start=1):
        clone = BoxObject(f"Source_Pattern_{instance_index + 1}", x2=2)
        clone.actor.AddPosition(offset, 0, 0)
        clone.pattern_definition = deepcopy(definition)
        clone.pattern_instance = {"instance_index": instance_index, "source_index": 0}
        scene.add_object(clone)

    history_bounds = []
    window._history_restoring = False
    window._sync_boolean_provenance = Mock()
    window._parameter_values = Mock(return_value={})
    window._history_record = Mock(side_effect=lambda: history_bounds.extend(
        obj.actor.GetBounds()
        for obj in scene.objects
        if getattr(obj, "pattern_instance", None)
    ))
    window._info_bar = SimpleNamespace(set_info=Mock())
    window._refresh_materials = Mock()
    window._viewport._render = Mock()
    scene.refresh_adaptive_grid = Mock()
    window._mark_simulation_dirty = Mock()
    window._recompute_pattern_instances = MethodType(MainWindow._recompute_pattern_instances, window)

    source.set_parameters({**source.get_parameters(), "X2": 4})
    MainWindow._on_params_changed(window, source, source.get_parameters())

    updated = {
        obj.pattern_instance["instance_index"]: obj
        for obj in scene.objects
        if getattr(obj, "pattern_instance", None)
    }
    assert len(updated) == 2
    assert updated[1].actor.GetBounds() == pytest.approx((5, 9, 0, 10, 0, 10))
    assert updated[2].actor.GetBounds() == pytest.approx((10, 14, 0, 10, 0, 10))
    assert all(
        obj.pattern_definition["sources"][0]["params"]["X2"] == 4
        for obj in updated.values()
    )
    assert history_bounds == [
        pytest.approx((5, 9, 0, 10, 0, 10)),
        pytest.approx((10, 14, 0, 10, 0, 10)),
    ]


def test_parametric_pattern_recompute_preserves_temporary_parameter_values():
    scene = SceneManager(vtk.vtkRenderer())
    source = BoxObject("Source", x2=1)
    source.param_formulas = {"X2": "pitch"}
    scene.add_object(source)
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=scene, _render=Mock()),
        _project_tree=SimpleNamespace(get_settings=lambda: {}),
        _parameter_values=lambda: {"pitch": 1.0},
        _sync_boolean_provenance=Mock(),
        _refresh_materials=Mock(),
        _process_simulation_preparation_events=Mock(),
        _update_generated_open_region=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )
    window._serialize_object_snapshot = MethodType(MainWindow._serialize_object_snapshot, window)
    window._rebuild_object_from_snapshot = MethodType(MainWindow._rebuild_object_from_snapshot, window)
    window._recompute_parametric_objects = MethodType(MainWindow._recompute_parametric_objects, window)
    window._recompute_pattern_instances = MethodType(MainWindow._recompute_pattern_instances, window)

    definition = {
        "pattern_id": "parametric-pitch",
        "sources": [window._serialize_object_snapshot(source)],
        "settings": {
            "mode": "Linear",
            "axis_enabled": [True, False, False],
            "expressions": {
                "axis_count_x": "2",
                "offset_x": "pitch",
            },
            "resolved": {},
        },
    }
    clone = BoxObject("Source_Pattern_2", x2=1)
    clone.actor.AddPosition(1, 0, 0)
    clone.pattern_definition = deepcopy(definition)
    clone.pattern_instance = {"instance_index": 1, "source_index": 0}
    scene.add_object(clone)

    window._recompute_parametric_objects(
        _regenerate_snaps=False,
        parameter_values={"pitch": 0.8},
    )

    assert source.x2 == pytest.approx(0.8)
    regenerated = next(obj for obj in scene.objects if getattr(obj, "pattern_instance", None))
    assert regenerated.actor.GetPosition()[0] == pytest.approx(0.8)


def test_unrelated_parameter_edit_does_not_recompute_patterns():
    pattern_source = {"name": "PatternSource"}
    pattern = SimpleNamespace(pattern_definition={"sources": [pattern_source]})
    scene = SimpleNamespace(
        objects=[pattern],
        refresh_adaptive_grid=Mock(),
    )
    window = SimpleNamespace(
        _viewport=SimpleNamespace(scene=scene, _render=Mock()),
        _history_restoring=False,
        _sync_boolean_provenance=Mock(),
        _history_record=Mock(),
        _recompute_pattern_instances=Mock(),
        _refresh_materials=Mock(),
        _mark_simulation_dirty=Mock(),
    )

    MainWindow._on_params_changed(window, SimpleNamespace(name="Unrelated"), {})

    window._recompute_pattern_instances.assert_not_called()


def test_source_edit_updates_every_pattern_using_it_after_multi_source_pattern():
    scene = SceneManager(vtk.vtkRenderer())
    source = BoxObject("SharedSource", x2=2)
    second_source = BoxObject("SecondSource", x1=20, x2=22)
    scene.add_object(source)
    scene.add_object(second_source)
    window = SimpleNamespace(_viewport=SimpleNamespace(scene=scene))
    window._serialize_object_snapshot = MethodType(MainWindow._serialize_object_snapshot, window)
    window._rebuild_object_from_snapshot = MethodType(MainWindow._rebuild_object_from_snapshot, window)
    source_snapshots = [
        window._serialize_object_snapshot(source),
        window._serialize_object_snapshot(second_source),
    ]
    multi_source_definition = {
        "pattern_id": "multi-source-pattern",
        "sources": source_snapshots,
        "settings": {
            "mode": "Linear",
            "axis_enabled": [True, False, False],
            "expressions": {"axis_count_x": "2", "offset_x": "5"},
            "resolved": {},
        },
    }
    single_source_definition = {
        "pattern_id": "single-source-pattern",
        "sources": [source_snapshots[0]],
        "settings": {
            "mode": "Linear",
            "axis_enabled": [False, True, False],
            "expressions": {"axis_count_y": "2", "offset_y": "7"},
            "resolved": {},
        },
    }
    for instance_index in (1,):
        for source_index, (item, snapshot) in enumerate(zip((source, second_source), source_snapshots)):
            clone = BoxObject(f"{item.name}_Multi", x2=2)
            clone.actor.AddPosition(5 * instance_index, 0, 0)
            clone.pattern_definition = deepcopy(multi_source_definition)
            clone.pattern_instance = {"instance_index": instance_index, "source_index": source_index}
            scene.add_object(clone)
    single_clone = BoxObject("SharedSource_Single", x2=2)
    single_clone.actor.AddPosition(0, 7, 0)
    single_clone.pattern_definition = deepcopy(single_source_definition)
    single_clone.pattern_instance = {"instance_index": 1, "source_index": 0}
    scene.add_object(single_clone)

    window._history_restoring = False
    window._sync_boolean_provenance = Mock()
    window._parameter_values = Mock(return_value={})
    window._history_record = Mock()
    window._info_bar = SimpleNamespace(set_info=Mock())
    window._refresh_materials = Mock()
    window._viewport._render = Mock()
    scene.refresh_adaptive_grid = Mock()
    window._mark_simulation_dirty = Mock()
    window._recompute_pattern_instances = MethodType(MainWindow._recompute_pattern_instances, window)

    source.set_parameters({**source.get_parameters(), "X2": 4})
    MainWindow._on_params_changed(window, source, source.get_parameters())

    updated = {
        (obj.pattern_instance["source_index"], obj.pattern_definition["pattern_id"]): obj
        for obj in scene.objects
        if getattr(obj, "pattern_instance", None)
    }
    assert updated[(0, "multi-source-pattern")].actor.GetBounds() == pytest.approx((5, 9, 0, 10, 0, 10))
    assert updated[(1, "multi-source-pattern")].actor.GetBounds() == pytest.approx((25, 27, 0, 10, 0, 10))
    assert updated[(0, "single-source-pattern")].actor.GetBounds() == pytest.approx((0, 4, 7, 17, 0, 10))


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


def test_scene_clear_resets_user_defined_reference_planes():
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_reference_plane("Plane_Reference_Bottom", (0, 0, -5), (0, 0, 1))
    scene.add_reference_plane("Plane_Reference_Top", (0, 0, 5), (0, 0, 1))

    scene.clear()

    assert [plane.name for plane in scene.reference_planes] == [
        "XY (Z=0)",
        "XZ (Y=0)",
        "YZ (X=0)",
    ]
    assert scene.active_plane is scene.reference_planes[0]


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
    workspace_toolbar = _Toolbar()
    sketch_toolbar = _Toolbar(visible=False)
    window = SimpleNamespace(
        _viewport=viewport,
        _main_toolbar=main_toolbar,
        _workspace_toolbar=workspace_toolbar,
        _sketch_context_toolbar=sketch_toolbar,
    )

    MainWindow._start_embedded_sketch(window, (0, 0, 1), (0, 0, 1))

    assert viewport.start_args == ((0, 0, 1), (0, 0, 1))
    assert viewport._sketch_toolbar.visible is False
    assert main_toolbar.visible is False
    assert workspace_toolbar.visible is False
    assert sketch_toolbar.visible is True

    MainWindow._on_viewport_sketch_finished(window)

    assert main_toolbar.visible is True
    assert workspace_toolbar.visible is True
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
        _workspace_toolbar=SimpleNamespace(setVisible=Mock()),
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
    window._copy_selected_object_by_vertices = Mock()
    window._bool_dissolve = Mock()
    window._import_step = Mock()
    window._open_simulation_window = Mock()
    window._open_measure_tool = Mock()
    window._open_project_parameters = Mock()
    window._recompute_model = Mock()
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
    tools_group = next(
        widget for action in window._main_toolbar.actions()
        if (widget := window._main_toolbar.widgetForAction(action)) is not None
        and widget.property("toolbarGroupTitle") == "Tools"
    )
    tools_actions = [button.defaultAction() for button in tools_group.findChildren(QToolButton)]
    assert [action.text() for action in tools_actions] == ["Measure", "Parameters"]
    actions[0].trigger()
    actions[1].trigger()
    actions[2].trigger()
    window._viewport._sketch_set_selection_mode.assert_called_once()
    window._viewport._sketch_delete_selected.assert_called_once()
    window._viewport.exit_sketch.assert_called_once_with(commit=False)
    assert application is not None


def test_tools_menu_does_not_offer_report():
    application = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._viewport = SimpleNamespace(
        cancel_draw=Mock(),
        measurement_mode_changed=Mock(),
        reset_camera=Mock(),
        reset_reference_plane=Mock(),
    )
    for name in (
        "_new_project", "_close_project", "_open_project", "_save_project",
        "_save_project_as", "_import_step", "_export_emerge",
        "_set_global_material_db", "_reload_global_material_db",
        "_delete_selected", "_set_cancel_drawing_enabled", "_undo", "_redo",
        "_update_history_actions", "_set_view", "_open_reference_plane_dialog",
        "_toggle_grid", "_rebuild_window_menu", "_refresh_recent_projects",
        "_open_settings_dialog", "_open_material_library_dialog",
        "_open_project_parameters", "_export_model_step", "_open_help",
        "_show_about",
    ):
        setattr(window, name, Mock())

    MainWindow._build_menus(window)

    tools_menu = next(
        menu for menu in window.findChildren(QMenu)
        if menu.title().replace("&", "") == "Tools"
    )
    assert all("Report" not in action.text() for action in tools_menu.actions())
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


def test_changing_units_converts_geometry_parameters_and_formulas():
    box = BoxObject("Box", 0, 1, 0, 1, 2, 1)
    box.param_formulas = {"X2": "width"}
    box.sketch_definition = {
        "plane_origin": [1, 2, 3],
        "entities": [{"geometry": [1, 2, 3]}],
        "dimensions": [
            {"kind": "linear", "value": 1, "resolved_value": 1, "expression": "width"},
            {"kind": "angular", "value": 90, "resolved_value": 90},
        ],
    }
    settings = {"parameters": [{"name": "width", "value": 1, "unit": "inch"}]}
    project_tree = SimpleNamespace(
        get_settings=lambda: settings,
        load_settings=lambda value: settings.update(value),
        settings_changed=SimpleNamespace(emit=Mock()),
    )
    viewport = SimpleNamespace(
        scene=SimpleNamespace(objects=[box], scene_changed=SimpleNamespace(emit=Mock())),
        scene_changed=SimpleNamespace(emit=Mock()),
        _render=Mock(),
    )
    window = SimpleNamespace(
        _history_record=Mock(),
        _project_tree=project_tree,
        _viewport=viewport,
        _info_bar=SimpleNamespace(set_info=Mock()),
        _sync_boolean_provenance=Mock(),
        _recompute_parametric_objects=Mock(),
        recompute_sketch_dimensions=Mock(),
        _refresh_materials=Mock(),
        _mark_simulation_dirty=Mock(),
    )
    window._scale_by_attributes = lambda obj, factor, *, preserve_center: MainWindow._scale_by_attributes(
        window, obj, factor, preserve_center=preserve_center
    )

    MainWindow._convert_scene_units(window, "inch", "mm")

    assert box.get_parameters()["X2"] == pytest.approx(25.4)
    assert box.actor.GetBounds() == pytest.approx((0, 25.4, 25.4, 50.8, 0, 25.4))
    assert settings["parameters"] == [{"name": "width", "value": 25.4, "unit": "mm"}]
    assert main_window_module.evaluate_expression(
        box.param_formulas["X2"], {"width": 25.4}
    ) == pytest.approx(25.4)
    assert box.sketch_definition["plane_origin"] == pytest.approx([25.4, 50.8, 76.2])
    assert box.sketch_definition["dimensions"][0]["resolved_value"] == pytest.approx(25.4)
    assert box.sketch_definition["dimensions"][1]["resolved_value"] == 90


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
