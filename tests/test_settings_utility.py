from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from em3d_modeler.ui import main_window
from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget
from em3d_modeler.ui.settings_dialog import SettingsDialog


def test_save_mesh_after_sweep_setting_persists(monkeypatch):
    stored_values = {
        "mesh/save_after_sweep": 1,
        "mesh/allow_oversized_mesh": 1,
        "mesh/curved_boundary_meshing_enabled": 0,
        "mesh/algorithm": "HXT",
        "simulation/worker_processes": 4,
        "simulation/cudss_precision": "double",
    }

    class MemorySettings:
        def value(self, key, default=None):
            return stored_values.get(key, default)

        def setValue(self, key, value):
            stored_values[key] = value

        def sync(self):
            pass

    monkeypatch.setattr(main_window, "QSettings", MemorySettings)
    window = SimpleNamespace(
        _units="mm",
        _workspace_size=200.0,
        _grid_spacing=10.0,
        _adaptive_grid_enabled=False,
        _adaptive_grid_margin=20.0,
        _plane_triad_size=25.0,
        _decimal_separator=".",
        _selection_color=(0.62, 0.34, 0.85),
        _make_numeric_locale=lambda _separator: None,
        _sim_solver="PARDISO",
        _sim_parallel_enabled=False,
        _sim_worker_processes=2,
        _sim_pardiso_threads=8,
        _sim_acc_threads=10,
        _mesh_resolution=0.3,
        _mesh_algorithm="DELAUNAY",
        _curved_boundary_meshing_enabled=True,
        _curved_boundary_resolution=20,
        _refinement_boundary_size_mm=0.25,
        _refinement_face_size_mm=0.1,
        _refinement_growth_rate=3.0,
        _refinement_max_size_mm=0.0,
        _sim_plot_sparams_after_sim=True,
        _sim_export_sparams_after_sim=True,
        _export_full_scene_step=False,
        _boolean_decimation_enabled=False,
        _save_mesh_after_sweep=False,
        _allow_oversized_mesh=False,
        _workspace_path="",
        _global_material_db_path="",
    )

    main_window.MainWindow._load_app_settings(window)
    assert window._save_mesh_after_sweep is True
    assert window._allow_oversized_mesh is True
    assert window._curved_boundary_meshing_enabled is False
    assert window._mesh_algorithm == "HXT"
    assert window._sim_worker_processes == 4
    assert window._sim_cudss_precision == "double"

    main_window.MainWindow._save_app_settings(window)
    assert stored_values["mesh/save_after_sweep"] == 1
    assert stored_values["mesh/allow_oversized_mesh"] == 1
    assert stored_values["mesh/curved_boundary_meshing_enabled"] == 0
    assert stored_values["mesh/algorithm"] == "HXT"
    assert stored_values["simulation/worker_processes"] == 4
    assert stored_values["simulation/cudss_precision"] == "double"

    del stored_values["mesh/allow_oversized_mesh"]
    del stored_values["mesh/curved_boundary_meshing_enabled"]
    stored_values["mesh/algorithm"] = "unsupported"
    del stored_values["simulation/cudss_precision"]
    main_window.MainWindow._load_app_settings(window)
    assert window._allow_oversized_mesh is False
    assert window._curved_boundary_meshing_enabled is True
    assert window._mesh_algorithm == "DELAUNAY"
    assert window._sim_cudss_precision == "auto"
    main_window.MainWindow._save_app_settings(window)
    assert stored_values["mesh/allow_oversized_mesh"] == 0
    assert stored_values["mesh/curved_boundary_meshing_enabled"] == 1
    assert stored_values["mesh/algorithm"] == "DELAUNAY"
    assert stored_values["simulation/cudss_precision"] == "auto"


def test_utility_settings_are_exposed_and_default_off(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()

    values = dialog.values()

    assert values["save_mesh_after_sweep"] is False
    assert values["allow_oversized_mesh"] is False
    assert values["curved_boundary_meshing_enabled"] is True
    assert values["mesh_algorithm"] == "DELAUNAY"
    assert dialog._mesh_algorithm_combo.count() == 6
    assert values["export_full_scene_step"] is False
    assert values["boolean_decimation_enabled"] is False
    assert "progressive_sparams_enabled" not in values
    assert not hasattr(dialog, "_progressive_sparams_check")
    assert values["workspace_path"] == ""
    assert values["global_material_db_path"] == ""
    assert [dialog._tabs.tabText(index) for index in range(dialog._tabs.count())] == [
        "Display",
        "Simulation",
        "Mesh",
        "Utility",
    ]

    dialog.set_values(
        units="mm",
        decimal_separator=".",
        workspace_size=50.0,
        grid_size=0.1,
        plane_triad_size=1.0,
        selection_color=(0.62, 0.34, 0.85),
        locale=dialog._workspace_spin.locale(),
        save_mesh_after_sweep=True,
        allow_oversized_mesh=True,
        curved_boundary_meshing_enabled=False,
        mesh_algorithm="MMG3D",
        curved_boundary_resolution=64,
        export_full_scene_step=True,
        boolean_decimation_enabled=True,
        workspace_path=str(tmp_path),
        global_material_db_path=str(tmp_path / "materials.json"),
    )

    updated = dialog.values()
    assert updated["save_mesh_after_sweep"] is True
    assert updated["allow_oversized_mesh"] is True
    assert updated["curved_boundary_meshing_enabled"] is False
    assert updated["mesh_algorithm"] == "MMG3D"
    assert updated["curved_boundary_resolution"] == 64
    assert dialog._curved_boundary_resolution_spin.isEnabled()
    assert updated["export_full_scene_step"] is True
    assert updated["boolean_decimation_enabled"] is True
    assert "progressive_sparams_enabled" not in updated
    assert updated["workspace_path"] == str(tmp_path)
    assert updated["global_material_db_path"] == str(tmp_path / "materials.json")


def test_loaded_project_mesh_algorithm_is_normalized_and_used_for_script():
    from em3d_modeler.emerge.python_script_exporter import export_emerge_python_script

    app = QApplication.instance() or QApplication([])
    project = ProjectTreeWidget()
    project.load_settings({"mesh": {"algorithm": "hxt"}})

    assert project.get_settings()["mesh"]["algorithm"] == "HXT"
    script = export_emerge_python_script(
        project_name="LoadedProjectAlgorithm",
        settings={
            **project.get_settings(),
            "simulations": [{"name": "Sweep", "type": "Sweep"}],
        },
        step_entries=[],
    )
    assert "simulationObj.mesher.set_algorithm(em.Algorithm3D.HXT)" in script


def test_workspace_folder_picker_updates_the_selected_path(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()
    monkeypatch.setattr(
        "em3d_modeler.ui.settings_dialog.QFileDialog.getExistingDirectory",
        lambda *_args: str(tmp_path),
    )

    dialog._browse_workspace()

    assert dialog.values()["workspace_path"] == str(tmp_path)


def test_material_database_picker_updates_the_selected_path(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()
    db_path = tmp_path / "materials.json"
    monkeypatch.setattr(
        "em3d_modeler.ui.settings_dialog.QFileDialog.getOpenFileName",
        lambda *_args: (str(db_path), "JSON (*.json)"),
    )

    dialog._browse_material_db()

    assert dialog.values()["global_material_db_path"] == str(db_path)


def test_multiprocessing_controls_follow_solver_support():
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()

    assert dialog._solver_combo.currentText() == "PARDISO"
    assert not dialog._parallel_check.isEnabled()
    assert not dialog._parallel_check.isChecked()
    assert not dialog._worker_processes_spin.isEnabled()
    assert dialog._worker_processes_spin.value() == 2
    assert dialog._pardiso_threads_spin.isEnabled()
    assert dialog._acc_threads_spin.isEnabled()

    for solver in ("UMFPACK", "AASDS", "MUMPS", "SPARTA"):
        dialog._solver_combo.setCurrentText(solver)
        assert dialog._parallel_check.isEnabled()
        assert dialog._worker_processes_spin.isEnabled()

    for solver in ("PARDISO", "SUPERLU", "CUDSS"):
        dialog._parallel_check.setChecked(True)
        dialog._solver_combo.setCurrentText(solver)
        assert not dialog._parallel_check.isEnabled()
        assert not dialog._parallel_check.isChecked()
        assert not dialog._worker_processes_spin.isEnabled()
        assert dialog._cudss_precision_combo.isEnabled() == (solver == "CUDSS")

    assert dialog._pardiso_threads_spin.isEnabled()
    assert dialog._acc_threads_spin.isEnabled()
    assert dialog._cudss_precision_combo.currentData() == "auto"
    dialog._cudss_precision_combo.setCurrentIndex(
        dialog._cudss_precision_combo.findData("double")
    )
    assert dialog.values()["cudss_precision"] == "double"

    dialog._solver_combo.setCurrentText("SPARTA")
    assert not dialog._cudss_precision_combo.isEnabled()

    dialog._solver_combo.setCurrentText("UMFPACK")
    assert dialog._parallel_check.isEnabled()
    assert dialog._worker_processes_spin.isEnabled()
    assert not dialog._parallel_check.isChecked()


def test_worker_processes_round_trip_and_project_default():
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()
    dialog.set_values(
        units="mm",
        decimal_separator=".",
        workspace_size=50.0,
        grid_size=0.1,
        plane_triad_size=1.0,
        selection_color=(0.62, 0.34, 0.85),
        locale=dialog._workspace_spin.locale(),
        solver="UMFPACK",
        cudss_precision="double",
        parallel_enabled=True,
        worker_processes=6,
    )

    values = dialog.values()
    assert values["parallel_enabled"] is True
    assert values["worker_processes"] == 6
    assert values["cudss_precision"] == "double"

    project_tree = ProjectTreeWidget()
    project_tree.load_settings({})
    runtime = project_tree.get_settings()["runtime"]
    assert runtime["worker_processes"] == 2
    assert runtime["parallel_enabled"] is False
    project_tree.set_runtime_settings(values)
    assert project_tree.get_settings()["runtime"]["worker_processes"] == 6
    project_tree.load_settings({"runtime": {"solver": "SPARTA"}})
    assert project_tree.get_settings()["runtime"]["solver"] == "SPARTA"
    assert project_tree.get_settings()["runtime"]["cudss_precision"] == "auto"
    project_tree.load_settings(
        {"runtime": {"solver": "CUDSS", "cudss_precision": "single"}}
    )
    assert project_tree.get_settings()["runtime"]["cudss_precision"] == "single"