import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from em3d_modeler import main as app_main
from em3d_modeler.ui import main_window


def test_script_worker_executes_script_without_starting_gui(monkeypatch, tmp_path):
    script_path = tmp_path / "simulation.py"
    script_path.write_text("result = 0", encoding="utf-8")
    run_path = Mock(return_value={"result": 0})
    attach_streams = Mock()
    monkeypatch.setattr("runpy.run_path", run_path)
    monkeypatch.setattr(app_main, "_attach_worker_streams", attach_streams)

    assert app_main._run_script_worker(str(script_path)) == 0

    attach_streams.assert_called_once_with()
    run_path.assert_called_once_with(str(script_path), run_name="__main__")


def test_worker_streams_are_reconfigured_for_utf8(monkeypatch):
    class Stream:
        def __init__(self):
            self.settings = None

        def reconfigure(self, **settings):
            self.settings = settings

    stdout = Stream()
    stderr = Stream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    app_main._attach_worker_streams()

    expected = {"encoding": "utf-8", "errors": "replace", "line_buffering": True}
    assert stdout.settings == expected
    assert stderr.settings == expected


def test_entry_point_dispatches_worker_before_qt_startup(monkeypatch, tmp_path):
    script_path = str(tmp_path / "simulation.py")
    worker = Mock(return_value=7)
    monkeypatch.setattr(app_main, "_run_script_worker", worker)
    monkeypatch.setattr(sys, "argv", ["EM3D_Modeler.exe", "--em3d-run-script", script_path])

    with pytest.raises(SystemExit) as exit_info:
        app_main.main()

    assert exit_info.value.code == 7
    worker.assert_called_once_with(script_path)


def test_frozen_entry_point_registers_runtime_dll_directories(monkeypatch, tmp_path):
    runtime_root = tmp_path / "_internal"
    (runtime_root / "bin").mkdir(parents=True)
    add_dll_directory = Mock(side_effect=lambda path: path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(runtime_root), raising=False)
    monkeypatch.setattr(app_main.os, "add_dll_directory", add_dll_directory, raising=False)
    monkeypatch.setattr(app_main, "_DLL_DIRECTORY_HANDLES", [])

    app_main._register_frozen_dll_directories()

    expected = [str(runtime_root / "bin"), str(runtime_root)]
    assert add_dll_directory.call_args_list == [((path,),) for path in expected]
    assert app_main._DLL_DIRECTORY_HANDLES == expected


@pytest.mark.parametrize(
    ("frozen", "expected_arguments"),
    [
        (True, ["--em3d-run-script"]),
        (False, ["-u"]),
    ],
)
def test_run_button_uses_script_runner_arguments(monkeypatch, tmp_path, frozen, expected_arguments):
    class Signal:
        def connect(self, _callback):
            pass

    class Process:
        NotRunning = 0
        MergedChannels = 1

        def __init__(self, _parent):
            self.readyReadStandardOutput = Signal()
            self.finished = Signal()
            self.started = Signal()

        def state(self):
            return self.NotRunning

        def setProgram(self, program):
            self.program = program

        def setArguments(self, arguments):
            self.arguments = arguments

        def setWorkingDirectory(self, _directory):
            pass

        def setProcessChannelMode(self, _mode):
            pass

        def start(self):
            pass

    script_path = tmp_path / "simulation_master.py"
    events = []
    window = SimpleNamespace(
        _save_project=lambda: events.append("save") or True,
        _generate_simulation_assets=lambda **_kwargs: events.append("generate") or "master script",
        _write_cached_simulation_scripts=lambda: script_path,
        _append_sim_log=Mock(),
        _sim_process=None,
        _sim_btn_run=Mock(),
        _sim_btn_stop=Mock(),
        _reset_progressive_sparams_plot=Mock(),
        _on_sim_process_output=Mock(),
        _on_sim_finished=Mock(),
        _attach_simulation_job=Mock(),
    )
    monkeypatch.setattr(main_window, "QProcess", Process)
    monkeypatch.setattr(main_window.sys, "frozen", frozen, raising=False)

    main_window.MainWindow._on_sim_run(window)

    assert events == ["save", "generate"]
    assert window._sim_process.program == sys.executable
    assert window._sim_process.arguments == expected_arguments + [str(script_path)]


def test_simulation_does_not_start_when_project_save_is_cancelled():
    generate_assets = Mock()
    log = Mock()
    window = SimpleNamespace(
        _sim_process=None,
        _save_project=lambda: False,
        _generate_simulation_assets=generate_assets,
        _append_sim_log=log,
    )

    main_window.MainWindow._on_sim_run(window)

    generate_assets.assert_not_called()
    log.assert_called_once_with(
        "[warn] Simulation not started because the project was not saved."
    )