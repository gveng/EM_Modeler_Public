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

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMainWindow, QToolButton

import em3d_modeler.ui.main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.drawing.sketch_engine import SketchEngine
from em3d_modeler.scene.em_objects import BoxObject, ExtrudedObject, MeshObject, RevolvedObject
from em3d_modeler.scene.scene_manager import SceneManager


def _main_window_stub(viewport):
    return SimpleNamespace(
        _viewport=viewport,
        _planar_face_sketch_plane=MainWindow._planar_face_sketch_plane,
        _start_embedded_sketch=Mock(),
        _open_reference_plane_dialog=Mock(),
    )


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


def test_loading_legacy_touchstone_appends_to_source_chart(tmp_path, monkeypatch):
    legacy_path = tmp_path / "previous_run.s2p"
    legacy_path.write_text(
        "# GHZ S RI R 50\n1 0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="ascii",
    )
    chart_view = SimpleNamespace(
        selected_parameters=Mock(return_value=["S21", "S11"]),
        configured_parameters=Mock(return_value=["S11"]),
        add_file_data=Mock(),
    )
    window = SimpleNamespace(
        _simulation_bundle_dir=Mock(return_value=tmp_path),
        _plot_views={"Sweep::Configured output": chart_view},
    )
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        Mock(return_value=(str(tmp_path / "Touchstone" / legacy_path.name), "")),
    )

    MainWindow._load_touchstone_for_chart(window, "Sweep::Configured output")

    chart_view.add_file_data.assert_called_once()
    x_values, series = chart_view.add_file_data.call_args.args
    assert [item["label"] for item in series] == [
        "S11", "S12", "S21", "S22",
    ]
    assert x_values == [1.0]
    assert [item["visible"] for item in series] == [True, False, True, False]
    assert {item["file_name"] for item in series} == {legacy_path.name}
    assert (tmp_path / "Touchstone" / legacy_path.name).is_file()


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
    window._copy_selected_object_by_vertices = Mock()
    window._bool_dissolve = Mock()
    window._import_step = Mock()
    window._open_simulation_window = Mock()
    window._open_measure_tool = Mock()
    window._open_project_parameters = Mock()
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
