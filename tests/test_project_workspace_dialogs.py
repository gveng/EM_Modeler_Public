from types import MethodType, SimpleNamespace
from unittest.mock import Mock

from em3d_modeler.ui import main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow


def test_open_project_uses_configured_workspace_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        lambda *args: (calls.append(args) or ("", "")),
    )
    window = SimpleNamespace(
        _workspace_path=str(tmp_path),
        _load_project_path=Mock(),
    )
    window._workspace_dialog_directory = MethodType(
        MainWindow._workspace_dialog_directory, window
    )

    MainWindow._open_project(window)

    assert calls[0][2] == str(tmp_path.resolve())
    window._load_project_path.assert_not_called()


def test_save_as_uses_configured_workspace_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args: (calls.append(args) or ("", "")),
    )
    window = SimpleNamespace(
        _workspace_path=str(tmp_path),
        _project_name="Coax",
    )
    window._workspace_dialog_directory = MethodType(
        MainWindow._workspace_dialog_directory, window
    )

    MainWindow._save_project_as(window)

    assert calls[0][2] == str(tmp_path / "Coax.em3d")


def test_missing_workspace_falls_back_to_existing_dialog_default():
    window = SimpleNamespace(_workspace_path="")

    assert MainWindow._workspace_dialog_directory(window) == ""