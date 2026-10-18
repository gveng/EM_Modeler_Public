from types import SimpleNamespace
from unittest.mock import Mock

from em3d_modeler.ui.main_window import MainWindow


def test_new_project_starts_with_pardiso_not_previous_solver():
    class ProjectTree:
        def __init__(self):
            self.settings = {}

        def set_project_name(self, _name):
            pass

        def reset_settings(self):
            self.settings = {"runtime": {"solver": "PARDISO"}}

        def set_runtime_settings(self, values):
            self.settings["runtime"].update(values)

    project_tree = ProjectTree()
    scene = SimpleNamespace(clear=Mock())
    window = SimpleNamespace(
        _exit_isolate=Mock(),
        _close_simulation_panel=Mock(),
        _close_all_plot_windows=Mock(),
        _close_floating_dialogs=Mock(),
        _close_farfield_displays=Mock(),
        _project_name="Existing",
        _project_path="existing.em3d",
        _material_store=None,
        _global_material_db_path="",
        _reset_simulation_cache=Mock(),
        setWindowTitle=Mock(),
        _project_tree=project_tree,
        _sync_project_variable_names=Mock(),
        _sim_solver="SUPERLU",
        _sim_parallel_enabled=False,
        _sim_worker_processes=2,
        _sim_pardiso_threads=8,
        _sim_acc_threads=10,
        _sim_plot_sparams_after_sim=True,
        _sim_export_sparams_after_sim=True,
        _sync_log_verbosity_from_settings=Mock(),
        _viewport=SimpleNamespace(
            scene=scene,
            _render=Mock(),
        ),
        _sync_active_reference_plane_to_viewport=Mock(),
        _body_props=SimpleNamespace(set_object=Mock()),
        _sync_material_choices=Mock(),
        _sync_port_reference_state=Mock(),
        _refresh_materials=Mock(),
        _history_reset=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )

    MainWindow._new_project(window)

    assert project_tree.settings["runtime"]["solver"] == "PARDISO"


def test_settings_dialog_uses_current_project_solver():
    settings_dialog = SimpleNamespace(set_values=Mock())
    window = SimpleNamespace(
        _settings_dlg=settings_dialog,
        _project_tree=SimpleNamespace(
            get_settings=lambda: {"runtime": {"solver": "MUMPS"}}
        ),
        _sim_solver="PARDISO",
        _units="mm",
        _decimal_separator=".",
        _workspace_size=200.0,
        _grid_spacing=10.0,
        _adaptive_grid_margin=0.0,
        _plane_triad_size=1.0,
        _selection_color=(1.0, 0.0, 0.0),
        _ui_locale=None,
        _sim_parallel_enabled=False,
        _sim_worker_processes=2,
        _sim_pardiso_threads=8,
        _sim_acc_threads=10,
        _mesh_resolution=0.3,
        _curved_boundary_resolution=20,
        _refinement_boundary_size_mm=0.25,
        _refinement_face_size_mm=0.1,
        _refinement_growth_rate=3.0,
        _refinement_max_size_mm=0.0,
        _save_mesh_after_sweep=False,
        _allow_oversized_mesh=False,
        _sim_plot_sparams_after_sim=True,
        _sim_export_sparams_after_sim=True,
        _export_full_scene_step=False,
        _boolean_decimation_enabled=False,
        _workspace_path="",
        _global_material_db_path="",
    )

    MainWindow._sync_settings_dialog_values(window)

    assert settings_dialog.set_values.call_args.kwargs["solver"] == "MUMPS"
