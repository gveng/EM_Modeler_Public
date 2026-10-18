import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import vtk
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from em3d_modeler.emerge import step_bundle_exporter
from em3d_modeler.emerge.simulation_validator import validate_simulation
from em3d_modeler.ui import main_window
from em3d_modeler.ui.info_bar_widget import InfoBarWidget


def _settings():
    return {
        "simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "enabled": True,
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 2,
        }],
        "runtime": {"solver": "PARDISO"},
    }


def test_validation_reports_ordered_monotonic_check_progress():
    progress = []

    validate_simulation(
        [],
        _settings(),
        progress_callback=lambda message, fraction: progress.append(
            (message, fraction)
        ),
    )

    fractions = [fraction for _, fraction in progress]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    messages = [message for message, _ in progress]
    assert any("simulation settings" in message.lower() for message in messages)
    assert messages[-1] == "Simulation validation complete"


def test_step_export_reports_actual_ordered_phases(monkeypatch, tmp_path):
    cube = vtk.vtkCubeSource()
    cube.Update()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    obj = SimpleNamespace(name="Body", actor=actor, material="PEC")
    phases = []

    monkeypatch.setattr(
        step_bundle_exporter, "build_occ_shape_for_export", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(step_bundle_exporter, "_count_solids_in_shape", lambda _shape: 1)

    def write_step(_shape, path):
        path.write_text("STEP", encoding="ascii")

    monkeypatch.setattr(step_bundle_exporter, "_write_step_from_occ_shape", write_step)

    result = step_bundle_exporter.export_objects_to_step_bundle(
        [obj],
        tmp_path / "bundle",
        phase_callback=lambda done, total, name, phase, fraction: phases.append(
            (done, total, name, phase, fraction)
        ),
    )

    assert result["entries"][0]["step_file"] == "Body.step"
    assert [phase for _, _, _, phase, _ in phases] == [
        "Reading scene geometry",
        "Scene geometry ready",
        "Building STEP BREP",
        "Writing STEP BREP",
        "STEP file written",
    ]
    assert [fraction for _, _, _, _, fraction in phases] == sorted(
        fraction for _, _, _, _, fraction in phases
    )
    assert phases[-1][0:3] == (1, 1, "Body")


def _check_window(info_bar, generate):
    window = QMainWindow()
    window._sim_ready = False
    window._play_action = Mock()
    window._sim_btn_run = Mock()
    window._simulation_validation_findings = Mock(return_value=[])
    window._generate_simulation_assets = generate
    window._sim_script_dirty = False
    window._sim_step_bundle_ready = True
    window._append_sim_log = Mock()
    window._info_bar = info_bar
    return window


def test_check_progress_is_complete_and_visible_during_report(monkeypatch):
    app = QApplication.instance() or QApplication([])
    info_bar = InfoBarWidget()
    info_bar.set_model_view(True)
    info_bar.show()

    def generate_assets(**_kwargs):
        report = window._sim_check_asset_progress_callback
        report("STEP export: Body — Writing STEP BREP", 0, 1, 0.75, False)
        assert info_bar._sim_progress.value() < 100
        assert info_bar._msg.text().startswith("STEP export: Body")
        report("Generating script: Sweep", 1, 1, 0.95, False)
        assert info_bar._sim_progress.value() < 100
        return "master script"

    window = _check_window(info_bar, generate_assets)
    monkeypatch.setattr(main_window, "_runtime_diagnostic_rows", lambda: [])

    def show_report(_dialog):
        assert info_bar._sim_progress.value() == 100
        assert info_bar._sim_progress_label.text() == "100%"
        assert info_bar._sim_status.text() == "CHECK"
        assert info_bar._msg.text() == "Check complete — ready to run."
        assert info_bar._sim_progress.isVisible()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", show_report)

    main_window.MainWindow._on_check_simulation(window)

    assert not info_bar._sim_progress.isVisible()
    assert info_bar._sim_progress.value() == 0
    assert info_bar._sim_progress_label.text() == "0%"
    assert window._sim_ready is True
    app.processEvents()
    window.close()
    info_bar.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_preparation_completes_report_as_failure(
    monkeypatch, cancelled
):
    app = QApplication.instance() or QApplication([])
    info_bar = InfoBarWidget()
    info_bar.set_model_view(True)
    info_bar.show()

    def generate_assets(**_kwargs):
        report = window._sim_check_asset_progress_callback
        report("STEP export: Body — Writing STEP BREP", 0, 1, 0.6, False)
        if cancelled:
            return None
        raise RuntimeError("STEP writer failed")

    window = _check_window(info_bar, generate_assets)
    monkeypatch.setattr(main_window, "_runtime_diagnostic_rows", lambda: [])
    dialogs = []

    def show_report(dialog):
        assert info_bar._sim_progress.value() == 100
        assert info_bar._sim_status.text() == "FAIL"
        assert "failure" in info_bar._msg.text().lower() or "failed" in info_bar._msg.text().lower()
        assert info_bar._sim_progress.isVisible()
        assert window._sim_ready is False
        report = dialog.findChild(main_window.QTextBrowser)
        assert report is not None
        html = report.toHtml()
        assert "Asset preparation" in html
        assert html.index("Asset preparation") < html.index("Runtime diagnostics")
        if not cancelled:
            assert "STEP writer failed" in html
        dialogs.append(dialog)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", show_report)

    main_window.MainWindow._on_check_simulation(window)

    assert not info_bar._sim_progress.isVisible()
    app.processEvents()
    window.close()
    info_bar.close()
