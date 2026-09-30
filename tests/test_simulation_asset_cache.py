import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QGroupBox, QMainWindow, QTabWidget, QWidget

from em3d_modeler.ui import main_window
from em3d_modeler.ui.info_bar_widget import InfoBarWidget


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

    def export_steps(*, objects, bundle_dir, **_kwargs):
        step_dir = Path(bundle_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        step_exports.append((step_dir, geometry_state["width"]))
        filename = f"Body_{geometry_state['width']}.step"
        (step_dir / filename).write_text("STEP", encoding="utf-8")
        return {"entries": [{"step_file": filename}], "skipped": []}

    def export_worker(**kwargs):
        worker_calls.append(kwargs)
        return f"worker-{kwargs['simulation_override']['ParamValues']}"

    def recompute(*, parameter_values):
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
        _collect_plate_lumped_ports=lambda: [],
        _collect_emerge_plates=lambda: [],
        _build_master_simulation_script=lambda jobs: main_window.MainWindow._build_master_simulation_script(
            SimpleNamespace(), jobs
        ),
    )
    window._export_simulation_step_bundle = lambda **kwargs: (
        main_window.MainWindow._export_simulation_step_bundle(window, **kwargs)
    )

    bundle = main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )

    assert [value for _path, value in step_exports] == [0.2, 0.4]
    assert [call["simulation_override"]["ParamValues"] for call in worker_calls] == [
        "0.2", "0.4",
    ]
    assert all(call["export_sparams_after_sim"] is True for call in worker_calls)
    assert all(len(call["step_entries"]) == 1 for call in worker_calls)
    assert geometry_state["width"] == 0.1
    assert [script["parameter_index"] for script in bundle["scripts"]] == [1, 2]
    assert [script["completion_name"] for script in bundle["scripts"]] == ["", "Simulation_2"]
    assert all(Path(script["filename"]).parts[0] == "Model_Simulation_2" for script in bundle["scripts"])
    assert "EM3D_RUN_ID" in bundle["master"]
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
    export_calls = []
    worker_calls = []

    def export_steps(*, objects, bundle_dir, **_kwargs):
        step_dir = Path(bundle_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
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

    bundle = main_window.MainWindow._build_simulation_script_bundle(
        window, [], show_model=False, show_mesh=False, run_sweep=True
    )

    expected_directory = tmp_path / "Project_Simulation_1"
    assert export_calls == [(expected_directory, [model_object])]
    assert worker_calls[0]["step_entries"] == [{"step_file": "Body.step"}]
    worker_path = Path(bundle["scripts"][0]["filename"])
    assert worker_path.parts == (
        "Project_Simulation_1", "Project_01_Simulation_1_emerge_run.py"
    )


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


def test_window_menu_lists_workspace_tabs_and_close_chart_action():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._window_menu = window.menuBar().addMenu("Window")
    window._workspace_tabs = QTabWidget(window)
    window.setCentralWidget(window._workspace_tabs)
    model_tab = QWidget()
    simulation_tab = QWidget()
    window._workspace_tabs.addTab(model_tab, "Model")
    window._workspace_tabs.addTab(simulation_tab, "Simulation")
    window._plot_views = {}
    window._close_all_plot_windows = Mock()

    main_window.MainWindow._rebuild_window_menu(window)

    assert [action.text() for action in window._window_menu.actions()] == [
        "Model",
        "Simulation",
        "",
        "Close All Charts",
    ]
    assert app is not None
    window.close()


def test_chart_opens_as_workspace_tab_and_can_be_closed():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._workspace_tabs = QTabWidget(window)
    window.setCentralWidget(window._workspace_tabs)
    window._workspace_tabs.addTab(QWidget(), "Model")
    window._plot_views = {}
    window._sim_dlg = None
    window._rebuild_window_menu = lambda: None
    chart_data = {
        "title": "Sweep S11",
        "plot_type": "plot_sp",
        "xlabel": "Frequency (GHz)",
        "ylabel": "S-parameter",
        "x_values": [1.0, 2.0],
        "series": [{"label": "S11", "values": [0.5 + 0j, 0.25 + 0j]}],
    }

    main_window.MainWindow._show_chart(window, "Sweep::Output", chart_data)

    assert [window._workspace_tabs.tabText(index) for index in range(window._workspace_tabs.count())] == [
        "Model",
        "Sweep S11",
    ]
    assert window._workspace_tabs.currentWidget() is window._plot_views["Sweep::Output"]
    main_window.MainWindow._close_workspace_tab(window, 1)
    assert window._workspace_tabs.count() == 1
    assert window._plot_views == {}
    assert app is not None
    window.close()


def test_close_all_plot_windows_preserves_model_and_simulation_tabs():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window._workspace_tabs = QTabWidget(window)
    window.setCentralWidget(window._workspace_tabs)
    model_tab = QWidget()
    simulation_tab = QWidget()
    first_chart = QWidget()
    second_chart = QWidget()
    window._workspace_tabs.addTab(model_tab, "Model")
    window._workspace_tabs.addTab(simulation_tab, "Simulation")
    window._workspace_tabs.addTab(first_chart, "S11")
    window._workspace_tabs.addTab(second_chart, "S21")
    window._plot_views = {"S11": first_chart, "S21": second_chart}
    window._rebuild_window_menu = Mock()

    main_window.MainWindow._close_all_plot_windows(window)

    assert [
        window._workspace_tabs.tabText(index)
        for index in range(window._workspace_tabs.count())
    ] == ["Model", "Simulation"]
    assert window._workspace_tabs.indexOf(first_chart) == -1
    assert window._workspace_tabs.indexOf(second_chart) == -1
    assert window._plot_views == {}
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
    window._workspace_tabs = QTabWidget(window)
    window._workspace_tabs.addTab(QWidget(), "Model")
    window.setCentralWidget(window._workspace_tabs)
    window._sim_dlg = None
    window._plot_views = {}
    window._rebuild_window_menu = lambda: None
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
        main_window.MainWindow._update_simulation_script_tabs(window, bundle)
        return bundle["master"]

    window._generate_simulation_assets = generate_assets

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
    assert window._workspace_tabs.widget(1) is window._sim_dlg

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

    main_window.MainWindow._close_workspace_tab(window, 1)
    assert window._workspace_tabs.count() == 1
    main_window.MainWindow._open_simulation_window(window)
    assert window._workspace_tabs.widget(1) is window._sim_dlg
    assert window._workspace_tabs.currentWidget() is window._sim_dlg

    window._sim_dlg.close()


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

    result = main_window.MainWindow._generate_simulation_assets(window, show_progress=False)

    assert result is None
    assert any("invalid dimensions" in call.args[0] for call in log.call_args_list)