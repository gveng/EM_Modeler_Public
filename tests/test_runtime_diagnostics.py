import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QTextBrowser

import em3d_modeler.ui.main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_runtime_diagnostics_report_python_and_imported_emerge(monkeypatch):
    monkeypatch.setattr(main_window_module.sys, "executable", "C:/env/python.exe")
    monkeypatch.setattr(main_window_module.sys, "version", "3.12.4 (test build)")
    monkeypatch.setattr(
        main_window_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__="3.0.0a20")
        if name == "emerge"
        else None,
    )

    rows = dict(main_window_module._runtime_diagnostic_rows())

    assert rows["Python executable"] == "C:/env/python.exe"
    assert rows["Python version"] == "3.12.4"
    assert rows["EMERGE import status"] == "Imported successfully"
    assert rows["EMERGE version"] == "3.0.0a20"


def test_runtime_diagnostics_report_emerge_import_failure_and_distribution_version(monkeypatch):
    def fail_import(name):
        assert name == "emerge"
        raise ModuleNotFoundError("No module named 'emerge'")

    monkeypatch.setattr(main_window_module.importlib, "import_module", fail_import)
    monkeypatch.setattr(
        main_window_module.importlib_metadata,
        "version",
        lambda name: "3.0.0a20" if name == "emerge" else None,
    )

    rows = dict(main_window_module._runtime_diagnostic_rows())

    assert rows["EMERGE import status"].startswith("Import failed: ModuleNotFoundError:")
    assert rows["EMERGE version"] == "3.0.0a20"


def test_simulation_check_blocks_swept_surface_without_supported_emerge_api(qapp, monkeypatch):
    class SweptSurfaceObject:
        name = "StandaloneSheet"

    window = QMainWindow()
    window._viewport = SimpleNamespace(
        scene=SimpleNamespace(objects=[SweptSurfaceObject()])
    )
    window._simulation_validation_findings = lambda: [
        SimpleNamespace(
            severity="ERROR",
            category="Runtime",
            message=(
                "Swept surface simulation requires an importable EMERGE 3.0.0 "
                "or later STEP surface API; refusing to prepare this model."
            ),
        )
    ]
    window._generate_simulation_assets = Mock(return_value="must not be generated")
    window._append_sim_log = lambda _message: None
    monkeypatch.setattr(main_window_module, "_runtime_diagnostic_rows", lambda: [])
    dialogs = []
    monkeypatch.setattr(
        QDialog,
        "exec",
        lambda dialog: dialogs.append(dialog) or QDialog.DialogCode.Rejected,
    )

    MainWindow._on_check_simulation(window)

    window._generate_simulation_assets.assert_not_called()
    report = dialogs[0].findChild(QTextBrowser)
    assert report is not None
    assert "EMERGE 3.0.0 or later STEP surface API" in report.toHtml()
    window.close()


def test_simulation_check_displays_runtime_diagnostics(qapp, monkeypatch):
    window = QMainWindow()
    window._simulation_validation_findings = lambda: []
    window._generate_simulation_assets = lambda **_kwargs: "prepared script"
    window._sim_script_dirty = False
    window._sim_step_bundle_ready = True
    window._append_sim_log = lambda _message: None
    monkeypatch.setattr(
        main_window_module,
        "_runtime_diagnostic_rows",
        lambda: [
            ("Python executable", "C:/env/python.exe"),
            ("Python version", "3.12.4"),
            ("EMERGE import status", "Imported successfully"),
            ("EMERGE version", "3.0.0a20"),
        ],
    )
    dialogs = []
    monkeypatch.setattr(
        QDialog,
        "exec",
        lambda dialog: dialogs.append(dialog) or QDialog.DialogCode.Rejected,
    )

    MainWindow._on_check_simulation(window)

    report = dialogs[0].findChild(QTextBrowser)
    assert report is not None
    html = report.toHtml()
    assert "Runtime diagnostics" in html
    assert "C:/env/python.exe" in html
    assert "3.12.4" in html
    assert "Imported successfully" in html
    assert "3.0.0a20" in html
    window.close()


def test_results_loader_resolves_native_emerge_without_iron(monkeypatch):
    calls = []

    class FakeSimulation:
        pass

    def import_module(name):
        calls.append(name)
        if name == "emerge":
            return SimpleNamespace(Simulation=FakeSimulation)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(main_window_module.importlib, "import_module", import_module)
    window = SimpleNamespace(_import_installed_emerge=lambda: None)

    constructor = MainWindow._resolve_emerge_simulation_ctor(window)

    assert constructor is FakeSimulation
    assert "emerge_iron" not in calls
