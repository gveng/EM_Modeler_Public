import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import vtk
from PySide6.QtCore import QPoint, Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGroupBox, QMainWindow, QMenu, QMessageBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from em3d_modeler.ui import main_window
from em3d_modeler.emerge import step_bundle_exporter, step_importer
from em3d_modeler.ui.info_bar_widget import InfoBarWidget


def _workspace_area_stub(window):
    area = main_window._PlotMdiArea(window)
    window._workspace_area = area
    window._plot_mdi_area = area
    window._workspace_windows = {}
    window._plot_subwindows = {}
    window._plot_views = {}
    model_page = QWidget()
    model_window = main_window._PlotSubWindow(area)
    model_window.setWindowTitle("Model")
    model_window.setWidget(model_page)
    area.addSubWindow(model_window)
    window._model_page = model_page
    window._model_window = model_window
    window._workspace_windows["Model"] = model_window
    return area


def _attach_step_export_cache(window):
    window._simulation_step_export_cache = {}
    window._simulation_step_cache_key = lambda **kwargs: (
        main_window.MainWindow._simulation_step_cache_key(window, **kwargs)
    )
    window._get_cached_simulation_step_bundle = lambda *args: (
        main_window.MainWindow._get_cached_simulation_step_bundle(window, *args)
    )
    window._cached_simulation_step_bundle = lambda **kwargs: (
        main_window.MainWindow._cached_simulation_step_bundle(window, **kwargs)
    )


def test_simulation_step_export_stops_when_all_model_objects_are_skipped(monkeypatch, tmp_path):
    log_export = Mock()
    monkeypatch.setattr(
        main_window,
        "export_objects_to_step_bundle",
        lambda **_kwargs: {
            "entries": [],
            "skipped": ["Body (MeshObject: no actor)"],
        },
    )
    window = SimpleNamespace(
        _normalize_log_level=lambda level: level.upper(),
        _append_step_export_log=log_export,
    )

    with pytest.raises(RuntimeError, match="STEP export incomplete"):
        main_window.MainWindow._export_simulation_step_bundle(
            window,
            objects=[SimpleNamespace(name="Body")],
            bundle_dir=tmp_path / "NewProject_EmergeSim" / "NewProject_Simulation_1",
            material_priorities={},
            excluded_object_names=set(),
        )

    assert any(
        "Body (MeshObject: no actor)" in call.args[0]
        for call in log_export.call_args_list
    )


def test_export_model_step_uses_scene_export_without_simulation(monkeypatch, tmp_path):
    scene_objects = [SimpleNamespace(name="Body")]
    export_scene = Mock(return_value={"exported": 1})
    info_bar = SimpleNamespace(set_info=Mock())
    simulation = Mock()
    window = SimpleNamespace(
        _project_name="Model",
        _project_path=None,
        _workspace_dialog_directory=lambda: str(tmp_path),
        _viewport=SimpleNamespace(scene=SimpleNamespace(objects=scene_objects)),
        _info_bar=info_bar,
        _generate_simulation_assets=simulation,
        _on_sim_run=simulation,
    )
    monkeypatch.setattr(main_window, "export_debug_scene_step", export_scene)
    monkeypatch.setattr(
        main_window.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(tmp_path / "standalone-model"), "STEP Files (*.step *.stp)"),
    )

    main_window.MainWindow._export_model_step(window)

    export_scene.assert_called_once_with(
        objects=scene_objects,
        step_path=tmp_path / "standalone-model.step",
        mm_per_unit=1.0,
    )
    info_bar.set_info.assert_called_once()
    assert "standalone-model.step" in info_bar.set_info.call_args.args[0]
    simulation.assert_not_called()


def test_simulation_step_cache_key_includes_scene_unit_scale(tmp_path):
    common = {
        "bundle_dir": tmp_path,
        "material_priorities": {},
        "excluded_object_names": set(),
    }
    millimetre_key = main_window.MainWindow._simulation_step_cache_key(
        SimpleNamespace(), mm_per_unit=1.0, **common
    )
    inch_key = main_window.MainWindow._simulation_step_cache_key(
        SimpleNamespace(), mm_per_unit=25.4, **common
    )

    assert millimetre_key != inch_key


def test_step_import_and_export_scale_geometry_without_mutating_scene(monkeypatch, tmp_path):
    cube = vtk.vtkCubeSource()
    cube.SetXLength(1.0)
    cube.SetYLength(1.0)
    cube.SetZLength(1.0)
    cube.Update()

    scaled = step_importer._scale_imported_solids(
        [{"name": "Body", "polydata": cube.GetOutput()}],
        1.0 / 25.4,
    )[0]["polydata"]
    assert scaled.GetBounds() == pytest.approx(tuple(value / 25.4 for value in cube.GetOutput().GetBounds()))

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    model = SimpleNamespace(name="Body", actor=actor, material="PEC", boolean_op="")
    written_bounds = []

    def write_mesh(polydata, step_path, log_callback=None):
        written_bounds.append(polydata.GetBounds())
        step_path.write_text("STEP", encoding="ascii")

    monkeypatch.setattr(step_bundle_exporter, "build_occ_shape_for_export", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(step_bundle_exporter, "_write_step_from_polydata", write_mesh)
    result = step_bundle_exporter.export_objects_to_step_bundle(
        [model],
        tmp_path / "bundle",
        mm_per_unit=25.4,
    )

    assert result["entries"][0]["bounds_mm"] == pytest.approx((-12.7, 12.7, -12.7, 12.7, -12.7, 12.7))
    assert written_bounds[0] == pytest.approx(result["entries"][0]["bounds_mm"])
    assert cube.GetOutput().GetBounds() == pytest.approx((-0.5, 0.5, -0.5, 0.5, -0.5, 0.5))


def test_tools_menu_contains_standalone_step_export():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._viewport = SimpleNamespace(
        cancel_draw=Mock(),
        measurement_mode_changed=Mock(),
        reset_camera=Mock(),
        reset_reference_plane=Mock(),
    )
    for name in (
        "_new_project", "_close_project", "_open_project", "_save_project",
        "_save_project_as", "_import_step", "_refresh_recent_projects",
        "_export_emerge", "_set_global_material_db", "_reload_global_material_db",
        "_delete_selected", "_set_cancel_drawing_enabled", "_undo", "_redo",
        "_update_history_actions", "_set_view", "_open_reference_plane_dialog",
        "_toggle_grid", "_rebuild_window_menu", "_open_settings_dialog",
        "_open_material_library_dialog", "_open_project_parameters", "_export_model_step",
        "_open_help", "_show_about",
    ):
        setattr(window, name, Mock())

    main_window.MainWindow._build_menus(window)

    tools_menu = next(
        menu for menu in window.findChildren(QMenu)
        if menu.title() == "&Tools"
    )
    assert "Export Model as &STEP..." in [action.text() for action in tools_menu.actions()]
    assert app is not None
    window.close()


def test_script_refresh_with_cached_steps_does_not_export_debug_step(monkeypatch):
    debug_export = Mock()
    monkeypatch.setattr(main_window, "export_debug_scene_step", debug_export)
    cached_script = {"master": "cached script", "scripts": []}
    window = SimpleNamespace(
        _sim_steps_dirty=False,
        _sim_step_bundle_ready=True,
        _sim_step_bundle_cache={"entries": [], "skipped": []},
        _sim_script_dirty=False,
        _sim_cached_script_bundle=cached_script,
        _project_name="CachedProject",
        _simulation_model_objects=lambda: [],
        _build_simulation_script_bundle=lambda entries, **kwargs: {
            "master": "refreshed script",
            "scripts": [],
        },
        _simulation_validation_findings=lambda: [],
        _append_sim_log=lambda message: None,
        _update_simulation_script_tabs=lambda bundle: None,
    )

    result = main_window.MainWindow._generate_simulation_assets(
        window,
        show_progress=False,
        force_script=True,
    )

    assert result == "refreshed script"
    debug_export.assert_not_called()


def test_complete_scene_step_can_export_without_reexporting_bundle(monkeypatch, tmp_path):
    debug_export = Mock(return_value={"exported": 3})
    bundle_export = Mock()
    monkeypatch.setattr(main_window, "export_debug_scene_step", debug_export)
    monkeypatch.setattr(main_window, "export_objects_to_step_bundle", bundle_export)
    window = SimpleNamespace(
        _sim_steps_dirty=False,
        _sim_step_bundle_ready=True,
        _sim_step_bundle_cache={"entries": [], "skipped": []},
        _sim_script_dirty=False,
        _sim_cached_script_bundle={"master": "cached script", "scripts": []},
        _sim_full_scene_step_dirty=True,
        _export_full_scene_step=True,
        _project_name="FullSceneProject",
        _simulation_model_objects=lambda: [],
        _simulation_bundle_dir=lambda: tmp_path,
        _build_simulation_script_bundle=lambda entries, **kwargs: {
            "master": "refreshed script",
            "scripts": [],
        },
        _simulation_validation_findings=lambda: [],
        _append_sim_log=lambda message: None,
        _update_simulation_script_tabs=lambda bundle: None,
    )

    result = main_window.MainWindow._generate_simulation_assets(
        window,
        show_progress=False,
        force_script=True,
    )

    assert result == "refreshed script"
    assert window._sim_full_scene_step_dirty is False
    debug_export.assert_called_once()
    bundle_export.assert_not_called()


def test_simulation_asset_prep_defers_step_export_to_simulation_folders(monkeypatch, tmp_path):
    bundle_export = Mock(side_effect=AssertionError("shared root export should not run"))
    build_bundle = Mock(return_value={"master": "master", "scripts": []})
    monkeypatch.setattr(main_window, "export_objects_to_step_bundle", bundle_export)
    window = SimpleNamespace(
        _sim_script_dirty=True,
        _sim_cached_script_bundle={"master": "", "scripts": []},
        _simulation_validation_findings=lambda: [],
        _append_sim_log=Mock(),
        _simulation_model_objects=lambda: [],
        _sim_steps_dirty=True,
        _sim_step_bundle_ready=False,
        _sim_full_scene_step_dirty=False,
        _export_full_scene_step=False,
        _simulation_bundle_dir=lambda: tmp_path,
        _reset_step_export_log=Mock(),
        _append_step_export_log=Mock(),
        _sim_step_bundle_cache={"entries": [], "skipped": []},
        _project_name="Project",
        _info_bar=SimpleNamespace(set_info=Mock()),
        _build_simulation_script_bundle=build_bundle,
        _update_simulation_script_tabs=Mock(),
    )

    result = main_window.MainWindow._generate_simulation_assets(
        window, show_progress=False, force_script=True
    )

    assert result == "master"
    bundle_export.assert_not_called()
    assert build_bundle.call_args.args[0] == []


def test_master_script_uses_frozen_worker_for_child_jobs():
    script = main_window.MainWindow._build_master_simulation_script(
        SimpleNamespace(),
        [{"name": "Sweep", "filename": "sweep.py"}],
    )

    assert "'--em3d-run-script', child_path" in script
    assert "[sys.executable, '-u', child_path]" in script


def test_parametric_bundle_exports_one_geometry_and_worker_per_value(monkeypatch, tmp_path):
    geometry_state = {"width": 0.1}
    model_object = SimpleNamespace(name="Body")
    settings = {
        "parameters": [{"name": "width", "value": 0.1}],
        "simulations": [{
            "name": "Simulation_2",
            "type": "Parametric",
            "enabled": True,
            "ParamName": "width",
            "ParamValues": "0.2,0.4",
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 3,
        }],
        "mesh": {},
        "runtime": {},
        "outputs": [],
    }
    step_exports = []
    worker_calls = []
    recompute_calls = []
    progress_updates = []

    def export_steps(*, objects, bundle_dir, **_kwargs):
        step_dir = Path(bundle_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        step_exports.append((step_dir, geometry_state["width"]))
        filename = f"Body_{geometry_state['width']}.step"
        (step_dir / filename).write_text("STEP", encoding="utf-8")
        progress_callback = _kwargs.get("progress_callback")
        if progress_callback is not None:
            progress_callback(1, 1, "Body")
        return {"entries": [{"step_file": filename}], "skipped": []}

    def export_worker(**kwargs):
        worker_calls.append(kwargs)
        return f"worker-{kwargs['simulation_override']['ParamValues']}"

    def recompute(*, parameter_values):
        recompute_calls.append(parameter_values["width"])
        geometry_state["width"] = parameter_values["width"]

    monkeypatch.setattr(main_window, "export_objects_to_step_bundle", export_steps)
    monkeypatch.setattr(main_window, "export_emerge_python_script", export_worker)
    monkeypatch.setattr(
        main_window,
        "build_clean_emerge_python_script",
        lambda script, _simulation_type: f"clean:{script}",
    )
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: settings),
        _sim_solver="PARDISO",
        _sim_parallel_enabled=False,
        _sim_pardiso_threads=1,
        _sim_acc_threads=1,
        _sim_plot_sparams_after_sim=False,
        _sim_export_sparams_after_sim=False,
        _mesh_resolution=0.2,
        _curved_boundary_resolution=20,
        _project_name="Model",
        _units="mm",
        _material_store=SimpleNamespace(material_export_catalog=lambda: {}),
        _simulation_bundle_dir=lambda: tmp_path,
        _parameter_values=lambda: {"width": 0.1},
        _enabled_simulations=lambda current_settings: main_window.MainWindow._enabled_simulations(
            SimpleNamespace(), current_settings
        ),
        _safe_script_token=lambda value: main_window.MainWindow._safe_script_token(
            SimpleNamespace(), value
        ),
        _simulation_model_objects=lambda: [model_object],
        _is_plate_role_object=lambda _obj: False,
        _recompute_parametric_objects=recompute,
        _collect_plate_lumped_ports=lambda: [{"origin": geometry_state["width"]}],
        _collect_emerge_plates=lambda: [{"origin": geometry_state["width"]}],
        _build_master_simulation_script=lambda jobs: main_window.MainWindow._build_master_simulation_script(
            SimpleNamespace(), jobs
        ),
    )
    window._export_simulation_step_bundle = lambda **kwargs: (
        main_window.MainWindow._export_simulation_step_bundle(window, **kwargs)
    )
    _attach_step_export_cache(window)

    bundle = main_window.MainWindow._build_simulation_script_bundle(
        window,
        [],
        show_model=False,
        show_mesh=False,
        run_sweep=True,
        progress_callback=lambda *args: progress_updates.append(args),
    )
    main_window.MainWindow._mark_simulation_dirty(window, steps=False)
    main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )

    assert [value for _path, value in step_exports] == [0.2, 0.4]
    assert recompute_calls == [0.2, 0.4, 0.1]
    assert [call["simulation_override"]["ParamValues"] for call in worker_calls[:2]] == [
        "0.2", "0.4",
    ]
    assert all(call["export_sparams_after_sim"] is True for call in worker_calls)
    assert all(len(call["step_entries"]) == 1 for call in worker_calls)
    assert [call["lumped_ports"][0]["origin"] for call in worker_calls] == [
        0.2, 0.4, 0.2, 0.4,
    ]
    assert [call["plate_entries"][0]["origin"] for call in worker_calls] == [
        0.2, 0.4, 0.2, 0.4,
    ]
    assert geometry_state["width"] == 0.1
    assert [script["parameter_index"] for script in bundle["scripts"]] == [1, 2]
    assert [script["completion_name"] for script in bundle["scripts"]] == ["", "Simulation_2"]
    assert all(Path(script["filename"]).parts[0] == "Model_Simulation_2" for script in bundle["scripts"])
    assert "EM3D_RUN_ID" in bundle["master"]
    progress_messages = [update[0] for update in progress_updates]
    assert any(message.startswith("Recomputing geometry 1/2") for message in progress_messages)
    assert any(message.startswith("STEP 1/2: Body") for message in progress_messages)
    assert any(message.startswith("Generating script 1/2") for message in progress_messages)
    assert any(message.startswith("Generated script 2/2") for message in progress_messages)
    assert "EM3D_PARAMETRIC_INDEX" in bundle["master"]

    window._sim_cached_script_bundle = bundle
    window._simulation_script_path = lambda: tmp_path / "Model_master.py"
    script_path = main_window.MainWindow._write_cached_simulation_scripts(window)
    assert script_path == tmp_path / "Model_master.py"
    assert all((tmp_path / script["filename"]).is_file() for script in bundle["scripts"])
    assert all((tmp_path / script["clean_filename"]).is_file() for script in bundle["scripts"])


def test_sweep_bundle_places_worker_and_geometry_under_simulation_folder(monkeypatch, tmp_path):
    settings = {
        "simulations": [{
            "name": "Simulation_1",
            "type": "Sweep",
            "enabled": True,
        }],
        "mesh": {},
        "runtime": {},
        "material_priorities": {},
    }
    model_object = SimpleNamespace(name="Body")
    preparation_order = []
    export_calls = []
    worker_calls = []

    def export_steps(*, objects, bundle_dir, **_kwargs):
        step_dir = Path(bundle_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        preparation_order.append("export")
        export_calls.append((step_dir, objects))
        (step_dir / "Body.step").write_text("STEP", encoding="utf-8")
        return {"entries": [{"step_file": "Body.step"}], "skipped": []}

    def export_worker(**kwargs):
        worker_calls.append(kwargs)
        return "sweep worker"

    monkeypatch.setattr(main_window, "export_objects_to_step_bundle", export_steps)
    monkeypatch.setattr(main_window, "export_emerge_python_script", export_worker)
    monkeypatch.setattr(
        main_window,
        "build_clean_emerge_python_script",
        lambda script, _simulation_type: f"clean:{script}",
    )
    window = SimpleNamespace(
        _project_tree=SimpleNamespace(get_settings=lambda: settings),
        _sim_solver="PARDISO",
        _sim_parallel_enabled=False,
        _sim_pardiso_threads=1,
        _sim_acc_threads=1,
        _sim_plot_sparams_after_sim=False,
        _sim_export_sparams_after_sim=False,
        _mesh_resolution=0.2,
        _curved_boundary_resolution=20,
        _project_name="Project",
        _units="mm",
        _material_store=SimpleNamespace(material_export_catalog=lambda: {}),
        _simulation_bundle_dir=lambda: tmp_path,
        _enabled_simulations=lambda current_settings: main_window.MainWindow._enabled_simulations(
            SimpleNamespace(), current_settings
        ),
        _safe_script_token=lambda value: main_window.MainWindow._safe_script_token(
            SimpleNamespace(), value
        ),
        _simulation_model_objects=lambda: [model_object],
        _missing_boolean_source_links=lambda: main_window.MainWindow._missing_boolean_source_links(window),
        _ask_missing_boolean_source_links=lambda missing, allow_retry: main_window.MainWindow._ask_missing_boolean_source_links(
            window, missing, allow_retry=allow_retry
        ),
        _confirm_faceted_step_export=lambda issues: main_window.MainWindow._confirm_faceted_step_export(
            window, issues
        ),
        _parameter_values=lambda: {},
        _recompute_parametric_objects=lambda **_kwargs: preparation_order.append("recompute"),
        _is_plate_role_object=lambda _obj: False,
        _collect_plate_lumped_ports=lambda: [],
        _collect_emerge_plates=lambda: [],
        _build_master_simulation_script=lambda jobs: main_window.MainWindow._build_master_simulation_script(
            SimpleNamespace(), jobs
        ),
    )
    window._export_simulation_step_bundle = lambda **kwargs: (
        main_window.MainWindow._export_simulation_step_bundle(window, **kwargs)
    )
    _attach_step_export_cache(window)

    bundle = main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )
    assert preparation_order[:2] == ["recompute", "export"]
    main_window.MainWindow._mark_simulation_dirty(window, steps=False)
    main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )

    expected_directory = tmp_path / "Project_Simulation_1"
    assert export_calls == [(expected_directory, [model_object])]
    assert worker_calls[0]["step_entries"] == [{"step_file": "Body.step"}]
    worker_path = Path(bundle["scripts"][0]["filename"])
    assert worker_path.parts == (
        "Project_Simulation_1", "Project_01_Simulation_1_emerge_run.py"
    )

    main_window.MainWindow._mark_simulation_dirty(window, steps=True)
    main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )
    assert len(export_calls) == 2


def test_boolean_step_export_issues_detect_faceted_boolean_and_large_mesh():
    issues = main_window._boolean_step_export_issues({
        "entries": [
            {
                "object_name": "Fuse_1",
                "boolean_op": "fuse",
                "export_mode": "mesh_roundtrip",
                "solid_count": -1,
                "poly_polys": 5024,
            },
            {
                "object_name": "ImportedMesh",
                "export_mode": "mesh_roundtrip",
                "poly_polys": 2000,
            },
            {
                "object_name": "CompactBody",
                "export_mode": "brep_direct",
                "solid_count": 1,
                "poly_polys": 5024,
            },
        ]
    })

    assert len(issues) == 2
    assert "Fuse_1" in issues[0]
    assert "ImportedMesh" in issues[1]


def test_missing_boolean_link_prompt_can_retry_geometry_rebuild(monkeypatch):
    source_a = SimpleNamespace(name="Source_A")
    source_b = SimpleNamespace(name="Source_B")
    boolean_object = SimpleNamespace(
        name="Fuse_1",
        boolean_op="fuse",
        boolean_source_names=["Source_A", "Source_B"],
        boolean_sources_data=[],
        source_objects=[],
    )
    warning_calls = []

    def warning(_parent, _title, text, buttons, _default):
        warning_calls.append((text, buttons))
        return main_window.QMessageBox.Retry

    monkeypatch.setattr(main_window.QMessageBox, "warning", warning)
    window = SimpleNamespace(_simulation_model_objects=lambda: [boolean_object])

    missing = main_window.MainWindow._missing_boolean_source_links(window)
    choice = main_window.MainWindow._ask_missing_boolean_source_links(
        window, missing, allow_retry=True
    )

    assert missing == ["Fuse_1 (0/2 sources linked)"]
    assert choice == QMessageBox.Retry
    assert "repair" in warning_calls[0][0]
    assert warning_calls[0][1] & QMessageBox.Ignore
    assert warning_calls[0][1] & QMessageBox.Cancel


def test_duplicate_named_boolean_source_instances_count_as_distinct_links():
    sources = [SimpleNamespace(name="Cylinder_1") for _ in range(3)]
    boolean_object = SimpleNamespace(
        name="Fuse_Cylinder_1",
        boolean_op="fuse",
        boolean_source_names=["Cylinder_1"] * 3,
        boolean_sources_data=[{"name": "Cylinder_1"} for _ in range(3)],
        source_objects=sources,
    )
    window = SimpleNamespace(_simulation_model_objects=lambda: [boolean_object])

    missing = main_window.MainWindow._missing_boolean_source_links(window)

    assert missing == []


def test_missing_duplicate_named_boolean_instance_is_still_reported():
    boolean_object = SimpleNamespace(
        name="Fuse_Cylinder_1",
        boolean_op="fuse",
        boolean_source_names=["Cylinder_1"] * 3,
        boolean_sources_data=[{"name": "Cylinder_1"} for _ in range(3)],
        source_objects=[SimpleNamespace(name="Cylinder_1") for _ in range(2)],
    )
    window = SimpleNamespace(_simulation_model_objects=lambda: [boolean_object])

    missing = main_window.MainWindow._missing_boolean_source_links(window)

    assert missing == ["Fuse_Cylinder_1 (2/3 sources linked)"]


@pytest.mark.parametrize(
    ("reply", "accepted"),
    [(QMessageBox.Ignore, True), (QMessageBox.Cancel, False)],
)
def test_faceted_step_validation_prompts_before_simulation(monkeypatch, reply, accepted):
    warning_calls = []
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *args: warning_calls.append(args) or reply,
    )
    append_step_log = Mock()
    append_sim_log = Mock()
    window = SimpleNamespace(
        _append_step_export_log=append_step_log,
        _append_sim_log=append_sim_log,
    )

    result = main_window.MainWindow._confirm_faceted_step_export(
        window, ["Fuse_1: mesh_roundtrip, 5,024 source triangles, -1 STEP solids"]
    )

    assert result is accepted
    assert len(warning_calls) == 1
    assert "very slow" in warning_calls[0][2]
    assert warning_calls[0][3] == QMessageBox.Ignore | QMessageBox.Cancel
    append_step_log.assert_called_once()
    append_sim_log.assert_called()


def test_enabled_simulations_preserves_per_job_progressive_setting():
    settings = {
        "simulations": [
            {"name": "Progressive", "type": "Sweep", "progressive_sparams_enabled": True},
            {"name": "Standard", "type": "Sweep", "progressive_sparams_enabled": False},
        ]
    }

    simulations = main_window.MainWindow._enabled_simulations(SimpleNamespace(), settings)

    assert [item["progressive_sparams_enabled"] for item in simulations] == [True, False]


def test_info_bar_shows_simulation_progress_outside_model_tab():
    app = QApplication.instance() or QApplication([])
    info_bar = InfoBarWidget()
    info_bar.set_info("Selected: Box [BoxObject]")
    info_bar.set_simulation_status(True, completed_jobs=0, total_jobs=2)
    info_bar.set_model_view(False)

    assert info_bar._msg.text() == "Simulation RUN"
    assert info_bar._sim_status.text() == "RUN"
    assert info_bar._sim_progress.value() == 0
    assert info_bar._sim_progress_label.text() == "0%"

    info_bar.set_simulation_status(
        True, completed_jobs=0, total_jobs=2, current_fraction=0.5
    )
    assert info_bar._sim_progress.value() == 25
    assert info_bar._sim_progress_label.text() == "25%"

    info_bar.set_simulation_status(True, completed_jobs=1, total_jobs=2)
    assert info_bar._sim_progress.value() == 50
    assert info_bar._sim_progress_label.text() == "50%"

    info_bar.set_model_view(True)
    assert info_bar._msg.text() == "Selected: Box [BoxObject]"
    info_bar.set_simulation_status(False, completed_jobs=2, total_jobs=2)
    assert not info_bar._sim_progress.isVisible()
    assert app is not None


def test_info_bar_shows_simulation_asset_preparation_progress():
    app = QApplication.instance() or QApplication([])
    info_bar = InfoBarWidget()
    info_bar.set_model_view(False)

    info_bar.set_preparation_progress(
        "STEP: Body (1/2)",
        completed=1,
        total=2,
        current_fraction=0.5,
    )

    assert info_bar._msg.text() == "STEP: Body (1/2)"
    assert info_bar._sim_status.text() == "PREP"
    assert info_bar._sim_progress.value() == 75
    assert info_bar._sim_progress_label.text() == "75%"
    info_bar.set_preparation_progress("")
    assert app is not None


def test_window_menu_lists_workspace_windows_and_close_chart_action():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._window_menu = window.menuBar().addMenu("Window")
    area = _workspace_area_stub(window)
    simulation_window = main_window._PlotSubWindow(area)
    simulation_window.setWindowTitle("Simulation")
    simulation_window.setWidget(QWidget())
    area.addSubWindow(simulation_window)
    window._workspace_windows["Simulation"] = simulation_window
    window.setCentralWidget(area)
    window._close_all_plot_windows = Mock()
    window._tile_plot_windows = Mock()
    window._cascade_plot_windows = Mock()
    window._restore_workspace_defaults = Mock()

    main_window.MainWindow._rebuild_window_menu(window)

    assert [action.text() for action in window._window_menu.actions()] == [
        "Model",
        "Simulation",
        "",
        "Tile Windows",
        "Cascade Windows",
        "Restore Workspace Defaults",
        "",
        "Close All Charts",
    ]
    actions = {action.text(): action for action in window._window_menu.actions()}
    actions["Restore Workspace Defaults"].trigger()
    window._restore_workspace_defaults.assert_called_once_with()
    assert app is not None
    window.close()


def test_restore_workspace_defaults_resets_mdi_window_sizes_and_positions():
    app = QApplication.instance() or QApplication([])
    host = QMainWindow()
    area = _workspace_area_stub(host)
    area.resize(900, 700)

    simulation_window = main_window._PlotSubWindow(area)
    simulation_window.setWindowTitle("Simulation")
    simulation_window.setWidget(QWidget())
    area.addSubWindow(simulation_window)
    host._sim_subwindow = simulation_window
    host._workspace_windows["Simulation"] = simulation_window

    plot_window = main_window._PlotSubWindow(area)
    plot_window.setWindowTitle("S11")
    plot_window.setWidget(QWidget())
    area.addSubWindow(plot_window)
    host._plot_subwindows = {"Sweep::S11": plot_window}
    host._show_workspace_window = Mock()

    for window in (host._model_window, simulation_window, plot_window):
        window.setGeometry(150, 140, 360, 280)

    main_window.MainWindow._restore_workspace_defaults(host)

    assert host._model_window.geometry().getRect() == (24, 24, 700, 520)
    assert simulation_window.geometry().getRect() == (72, 72, 680, 520)
    assert plot_window.geometry().getRect() == (60, 60, 648, 504)
    host._show_workspace_window.assert_called_once_with("Model")
    assert app is not None
    host.close()


def test_closing_new_project_disposes_simulation_panel():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    area = _workspace_area_stub(window)
    simulation_dialog = QWidget()
    log_level_combo = QComboBox(simulation_dialog)
    log_level_combo.addItems(["Trace", "Debug", "Info", "Warning", "Error"])
    simulation_window = main_window._PlotSubWindow(area)
    simulation_window.setWindowTitle("Simulation")
    simulation_window.setWidget(simulation_dialog)
    area.addSubWindow(simulation_window)
    window._workspace_windows["Simulation"] = simulation_window
    window._sim_subwindow = simulation_window
    window._sim_dlg = simulation_dialog
    window._sim_log_level = log_level_combo
    window._sim_log_verbosity = "INFO"
    window._project_tree = SimpleNamespace(get_log_verbosity=lambda: "Warning")
    window._shutdown_simulation_process = Mock()
    window._rebuild_window_menu = Mock()
    window.setCentralWidget(area)

    main_window.MainWindow._close_simulation_panel(window)
    app.processEvents()

    assert simulation_window not in area.subWindowList()
    assert "Simulation" not in window._workspace_windows
    assert window._sim_subwindow is None
    assert window._sim_dlg is None
    assert window._sim_log_level is None
    window._shutdown_simulation_process.assert_called_once_with()
    window._rebuild_window_menu.assert_called_once_with()
    main_window.MainWindow._sync_log_verbosity_from_settings(window)
    window.close()


def test_window_menu_exposes_chart_arrangement_commands():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._window_menu = window.menuBar().addMenu("Window")
    area = _workspace_area_stub(window)
    model_page = window._model_page
    simulation_window = main_window._PlotSubWindow(area)
    simulation_window.setWindowTitle("Simulation")
    simulation_window.setWidget(QWidget())
    area.addSubWindow(simulation_window)
    window._workspace_windows["Simulation"] = simulation_window
    plot_window = main_window._PlotSubWindow(area)
    plot_window.setWindowTitle("Sweep S11")
    plot_window.setWidget(QWidget())
    area.addSubWindow(plot_window)
    window._plot_subwindows = {"Sweep::S11": plot_window}
    window._plot_views = {"Sweep::S11": plot_window.widget()}
    window._tile_plot_windows = Mock()
    window._cascade_plot_windows = Mock()
    window._restore_workspace_defaults = Mock()
    window._show_plot_window = lambda key: main_window.MainWindow._show_plot_window(window, key)
    window._close_all_plot_windows = Mock()
    window.setCentralWidget(area)

    main_window.MainWindow._rebuild_window_menu(window)

    actions = {action.text(): action for action in window._window_menu.actions()}
    assert {"Tile Windows", "Cascade Windows", "Sweep S11", "Close All Charts"}.issubset(actions)
    actions["Tile Windows"].trigger()
    actions["Cascade Windows"].trigger()
    actions["Sweep S11"].trigger()
    window._tile_plot_windows.assert_called_once()
    window._cascade_plot_windows.assert_called_once()
    assert model_page is window._model_page
    assert area.activeSubWindow() is plot_window
    assert app is not None
    window.close()


def test_chart_opens_as_floating_window_and_can_be_closed():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    area = _workspace_area_stub(window)
    window.setCentralWidget(area)
    window._sim_dlg = None
    window._on_plot_subwindow_closed = lambda key, closed: main_window.MainWindow._on_plot_subwindow_closed(
        window, key, closed
    )
    window._rebuild_window_menu = lambda: None
    window.show()
    chart_data = {
        "title": "Sweep S11",
        "plot_type": "plot_sp",
        "xlabel": "Frequency (GHz)",
        "ylabel": "S-parameter",
        "x_values": [1.0, 2.0],
        "series": [{"label": "S11", "values": [0.5 + 0j, 0.25 + 0j]}],
    }

    main_window.MainWindow._show_chart(window, "Sweep::Output", chart_data)

    assert window._workspace_windows["Model"] in area.subWindowList()
    plot_window = window._plot_subwindows["Sweep::Output"]
    assert plot_window.widget() is window._plot_views["Sweep::Output"]
    plot_window.setGeometry(30, 40, 480, 320)
    assert plot_window.geometry().size().width() == 480
    plot_window.close()
    assert window._plot_views == {}
    assert window._plot_subwindows == {}
    assert app is not None
    window.close()


def test_workspace_subwindows_can_move_minimize_and_restore():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    area = main_window._PlotMdiArea(host)
    area.setGeometry(0, 0, 640, 480)
    model_window = main_window._PlotSubWindow(area)
    model_window.setWindowTitle("Model")
    model_window.setWidget(QWidget())
    chart_window = main_window._PlotSubWindow(area)
    chart_window.setWindowTitle("S11")
    chart_window.setWidget(QWidget())
    area.addSubWindow(model_window)
    area.addSubWindow(chart_window)
    model_window.setGeometry(40, 50, 320, 240)
    chart_window.setGeometry(120, 90, 260, 190)
    host.show()
    area.show()
    model_window.show()
    chart_window.show()
    app.processEvents()

    assert area.viewMode() == main_window.QMdiArea.SubWindowView
    assert area.subWindowList() == [model_window, chart_window]
    assert chart_window.parentWidget() is not model_window
    assert chart_window.windowFlags() & Qt.WindowMinimizeButtonHint
    assert chart_window.windowFlags() & Qt.WindowMaximizeButtonHint
    assert chart_window.pos() == QPoint(120, 90)

    chart_window.showMinimized()
    app.processEvents()
    assert chart_window.isMinimized()
    chart_window.showNormal()
    app.processEvents()
    assert chart_window.isVisible()
    assert not chart_window.isMinimized()

    chart_window.close()
    assert chart_window in area.subWindowList()
    chart_window.showNormal()
    assert chart_window.isVisible()
    host.close()
    assert app is not None


def test_reopening_model_window_renders_existing_viewport():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    area = main_window._PlotMdiArea(host)
    area.setGeometry(0, 0, 640, 480)
    model_window = main_window._PlotSubWindow(area)
    model_window.setWindowTitle("Model")
    model_page = QWidget()
    page_layout = QVBoxLayout(model_page)
    viewport_widget = QWidget(model_page)
    viewport_layout = QVBoxLayout(viewport_widget)
    from em3d_modeler.ui.viewport_widget import _PersistentQVTKRenderWindowInteractor
    finalize_calls = []

    class TrackingInteractor(_PersistentQVTKRenderWindowInteractor):
        def Finalize(self):
            finalize_calls.append(True)

    vtk_widget = TrackingInteractor(viewport_widget)
    viewport_layout.addWidget(vtk_widget)
    page_layout.addWidget(viewport_widget)
    model_window.setWidget(model_page)
    viewport_widget._vtk_widget = vtk_widget
    viewport_widget._render = Mock()
    area.addSubWindow(model_window)
    window = SimpleNamespace(
        _workspace_area=area,
        _workspace_windows={"Model": model_window},
        _model_window=model_window,
        _model_page=model_page,
        _viewport=viewport_widget,
        _info_bar=SimpleNamespace(set_model_view=Mock()),
    )
    host.show()
    area.show()
    model_window.show()
    app.processEvents()

    model_window.close()
    assert not model_window.isVisible()
    assert not vtk_widget.isVisible()
    assert finalize_calls == []
    main_window.MainWindow._show_workspace_window(window, "Model")
    app.processEvents()

    assert window._workspace_windows["Model"] is model_window
    assert model_window.isVisible()
    assert viewport_widget.isVisible()
    assert vtk_widget.isVisible()
    assert finalize_calls == []
    viewport_widget._render.assert_called_once_with()
    host.close()


def test_close_all_plot_windows_preserves_model_and_simulation_windows():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    area = _workspace_area_stub(window)
    simulation_window = main_window._PlotSubWindow(area)
    simulation_window.setWindowTitle("Simulation")
    simulation_window.setWidget(QWidget())
    area.addSubWindow(simulation_window)
    window._workspace_windows["Simulation"] = simulation_window
    first_chart = QWidget()
    second_chart = QWidget()
    window._plot_views = {"S11": first_chart, "S21": second_chart}
    window._plot_mdi_area = area
    window._plot_subwindows = {}
    for key, chart in window._plot_views.items():
        subwindow = main_window._PlotSubWindow(window._plot_mdi_area)
        subwindow.setAttribute(Qt.WA_DeleteOnClose, True)
        subwindow.setWidget(chart)
        window._plot_mdi_area.addSubWindow(subwindow)
        window._plot_subwindows[key] = subwindow
        subwindow.closed.connect(
            lambda closed, plot_key=key: main_window.MainWindow._on_plot_subwindow_closed(
                window, plot_key, closed
            )
        )
    window._rebuild_window_menu = Mock()

    main_window.MainWindow._close_all_plot_windows(window)

    assert set(window._workspace_area.subWindowList()) == {
        window._model_window,
        simulation_window,
    }
    assert window._plot_views == {}
    assert window._plot_subwindows == {}
    window.close()
    assert app is not None


def test_master_script_forwards_child_stdout_and_stderr(tmp_path):
    child_path = tmp_path / "worker.py"
    child_path.write_text(
        "import sys\nprint('CHILD_STDOUT_MARKER ▮')\n"
        "print('CHILD_STDERR_MARKER', file=sys.stderr)\n",
        encoding="utf-8",
    )
    master_path = tmp_path / "master.py"
    master_path.write_text(
        main_window.MainWindow._build_master_simulation_script(
            SimpleNamespace(),
            [{"name": "Sweep", "filename": child_path.name}],
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, str(master_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert b"CHILD_STDOUT_MARKER ?" in result.stdout
    assert b"CHILD_STDERR_MARKER" in result.stdout
    assert b'EM3D_JOB_COMPLETED:{"name":"Sweep","filename":"worker.py"}' in result.stdout

def test_save_script_exports_clean_worker_beside_full_worker(monkeypatch, tmp_path):
    master_path = tmp_path / "Project_master.py"
    script_bundle = {
        "master": "master runner",
        "scripts": [{
            "filename": "Project_01_Sweep_emerge_run.py",
            "content": "full worker",
            "clean_filename": "Project_01_Sweep_emerge_run_clean.py",
            "clean_content": "clean worker",
        }],
    }
    window = SimpleNamespace(
        _sim_cached_script_bundle=script_bundle,
        _simulation_script_path=lambda: master_path,
        _append_sim_log=Mock(),
    )
    monkeypatch.setattr(
        main_window.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(master_path), "Python (*.py)"),
    )

    main_window.MainWindow._on_sim_save_script(window)

    assert master_path.read_text(encoding="utf-8") == "master runner"
    assert (tmp_path / "Project_01_Sweep_emerge_run.py").read_text(encoding="utf-8") == "full worker"
    assert (tmp_path / "Project_01_Sweep_emerge_run_clean.py").read_text(encoding="utf-8") == "clean worker"


def test_cached_script_write_saves_clean_worker_in_simulation_folder(tmp_path):
    master_path = tmp_path / "Project_master.py"
    window = SimpleNamespace(
        _sim_cached_script_bundle={
            "master": "master runner",
            "scripts": [{
                "filename": "Project_01_Sweep_emerge_run.py",
                "content": "full worker",
                "clean_filename": "Project_01_Sweep_emerge_run_clean.py",
                "clean_content": "clean worker",
            }],
        },
        _simulation_script_path=lambda: master_path,
    )

    result = main_window.MainWindow._write_cached_simulation_scripts(window)

    assert result == master_path
    assert master_path.read_text(encoding="utf-8") == "master runner"
    assert (tmp_path / "Project_01_Sweep_emerge_run.py").read_text(encoding="utf-8") == "full worker"
    assert (tmp_path / "Project_01_Sweep_emerge_run_clean.py").read_text(encoding="utf-8") == "clean worker"


def test_simulation_option_change_defers_script_generation():
    generate = Mock()
    mark_dirty = Mock()
    info_bar = SimpleNamespace(set_info=Mock())
    window = SimpleNamespace(
        _mark_simulation_dirty=mark_dirty,
        _on_sim_generate=generate,
        _info_bar=info_bar,
    )

    main_window.MainWindow._on_sim_option_changed(window, True)

    mark_dirty.assert_called_once_with(steps=False, script=True)
    generate.assert_not_called()
    info_bar.set_info.assert_called_once_with(
        "Simulation options changed. Generate Script or Run to apply."
    )


def test_simulation_dialog_shows_cached_master_and_worker_scripts():
    app = QApplication.instance() or QApplication([])
    bundle = {
        "master": "master code",
        "scripts": [
            {
                "name": "Sweep",
                "job_name": "Sweep",
                "index": 1,
                "content": "full sweep worker",
                "clean_content": "clean sweep worker",
            },
            {
                "name": "Fit",
                "job_name": "Fit",
                "index": 2,
                "content": "full fit worker",
                "clean_content": "clean fit worker",
            },
        ],
    }
    window = QMainWindow()
    area = _workspace_area_stub(window)
    window.setCentralWidget(area)
    window._sim_dlg = None
    window._rebuild_window_menu = lambda: None
    window._on_workspace_subwindow_closed = lambda _window: None
    window._show_workspace_window = lambda key: main_window.MainWindow._show_workspace_window(
        window, key
    )
    window._info_bar = Mock()
    window._sim_cached_script_bundle = bundle
    window._append_sim_log = lambda _message: None
    window._write_cached_simulation_scripts = lambda: None
    window._sim_log_verbosity = "Info"
    for handler_name in (
        "_on_sim_option_changed",
        "_on_sim_log_level_changed",
        "_on_sim_generate",
        "_on_check_simulation",
        "_on_sim_save_script",
        "_on_sim_run",
        "_on_sim_stop",
    ):
        setattr(window, handler_name, lambda *_args: None)

    def generate_assets(**_kwargs):
        assert window._sim_subwindow.isVisible()
        assert window._sim_dlg.isVisible()
        main_window.MainWindow._update_simulation_script_tabs(window, bundle)
        return bundle["master"]

    window._generate_simulation_assets = generate_assets

    window.show()
    main_window.MainWindow._open_simulation_window(window)
    app.processEvents()

    script_group = window._sim_script_tabs.parentWidget()
    assert isinstance(script_group, QGroupBox)
    assert script_group.title() == "Generated Scripts"
    assert [window._sim_script_tabs.tabText(i) for i in range(window._sim_script_tabs.count())] == [
        "Master",
        "01 Sweep",
        "02 Fit",
    ]
    assert window._sim_script_tabs.widget(0).toPlainText() == "master code"
    assert window._sim_script_tabs.widget(1).toPlainText() == "full sweep worker"
    assert window._sim_script_tabs.widget(2).toPlainText() == "full fit worker"
    assert window._sim_subwindow.widget() is window._sim_dlg
    assert window._sim_subwindow in area.subWindowList()
    assert area.activeSubWindow() is window._sim_subwindow

    row = window._sim_dlg.layout().itemAt(0).layout()
    row_labels = [
        row.itemAt(index).widget().text()
        for index in range(8)
    ]
    assert row_labels == [
        "Run Sweep",
        "Show Mesh",
        "Show Model",
        "Preview Export (no mesh/run)",
        "Check Simulation",
        "Generate Script",
        "Save Script",
        "LOG",
    ]
    assert isinstance(row.itemAt(8).widget(), QComboBox)
    assert row.itemAt(9).widget().text() == "Run"
    assert row.itemAt(10).widget().text() == "Stop"
    assert isinstance(window._sim_chk_boolean_debug, QCheckBox)
    assert not window._sim_chk_boolean_debug.isVisible()

    window._sim_subwindow.close()
    assert not window._sim_subwindow.isVisible()
    main_window.MainWindow._open_simulation_window(window)
    assert window._sim_subwindow.widget() is window._sim_dlg
    assert window._sim_subwindow.isVisible()
    assert window._sim_dlg.isVisible()
    assert window._sim_log_view.isVisible()
    assert window._sim_output_tabs.isVisible()
    assert window._sim_script_tabs.isVisible()
    assert window._sim_script_tabs.currentWidget().isVisible()
    assert window._sim_chk_run_sweep.isVisible()
    assert not window._sim_chk_boolean_debug.isVisible()
    assert area.activeSubWindow() is window._sim_subwindow

    window._sim_subwindow.close()
    window.close()


def test_simulation_preflight_errors_stop_asset_generation():
    log = Mock()
    window = SimpleNamespace(
        _sim_script_dirty=True,
        _sim_cached_script_bundle={"master": ""},
        _simulation_validation_findings=lambda: [
            SimpleNamespace(severity="ERROR", category="Ports", message="invalid dimensions")
        ],
        _append_sim_log=log,
    )

    result = main_window.MainWindow._generate_simulation_assets(window)

    assert result is None
    assert any("invalid dimensions" in call.args[0] for call in log.call_args_list)