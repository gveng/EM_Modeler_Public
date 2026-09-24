from types import SimpleNamespace
from unittest.mock import Mock

from em3d_modeler.ui import main_window


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