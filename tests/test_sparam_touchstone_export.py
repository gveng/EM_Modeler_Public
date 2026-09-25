import ast
import os
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from em3d_modeler import __version__
from em3d_modeler.emerge import python_script_exporter


class _FixedDateTime:
    @classmethod
    def now(cls):
        return cls()

    def strftime(self, _format):
        return "20260925-123456"


class _FakeGrid:
    Smat = SimpleNamespace(shape=(2, 2, 2))
    freq = [1.0, 2.0]

    def dense_f(self, _points):
        return [1.0, 1.5, 2.0]

    def S(self, output_port, input_port):
        base = output_port * 10 + input_port
        return [complex(base + 0.1, 0.01), complex(base + 0.2, 0.02)]

    def model_S(self, output_port, input_port, frequencies):
        base = output_port * 10 + input_port
        return [complex(base + 100 + index, 0.5) for index, _ in enumerate(frequencies)]


class _FailingFitGrid(_FakeGrid):
    def __init__(self):
        self.model_s_calls = []

    def model_S(self, output_port, input_port, frequencies):
        self.model_s_calls.append((output_port, input_port))
        raise RuntimeError("SVD did not converge")


def _generated_namespace(tmp_path, fit_enabled, monkeypatch, export_enabled=False, plot_enabled=False):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    script = python_script_exporter.export_emerge_python_script(
        project_name="TouchstoneTest",
        settings={"simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "sparam_fitting": {"enabled": fit_enabled, "points": 3},
        }]},
        step_entries=[],
        run_sweep=False,
    )
    module = ast.parse(script)
    namespace = {
        "datetime": _FixedDateTime,
        "os": os,
        "SCRIPT_DIR": str(tmp_path),
        "PROJECT_NAME": "TouchstoneTest",
        "SPARAM_FIT_ENABLED": fit_enabled,
        "SPARAM_FIT_POINTS": 3,
        "EXPORT_SPARAMS_AFTER_SIM": export_enabled,
        "PLOT_SPARAMS_AFTER_SIM": plot_enabled,
        "JOB_TYPE": "sweep",
        "JOB_NAME": "Sweep",
        "OUTPUT_CONFIGS": [],
        "SAVE_FARFIELDS": False,
        "_number_of_ports": lambda grid: int(grid.Smat.shape[1]),
        "_safe_token": lambda value: value,
    }
    helper_names = {"_write_sputility_touchstone", "_postprocess_sparams"}
    helpers = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "generated_script.py", "exec"), namespace)
    return script, namespace


def _touchstone_writer(tmp_path, fit_enabled, monkeypatch):
    _, namespace = _generated_namespace(tmp_path, fit_enabled, monkeypatch)
    return namespace["_write_sputility_touchstone"]


def _read_touchstone(path):
    lines = path.read_text(encoding="ascii").splitlines()
    assert lines[0] == "# HZ S RI R 50.0"
    return [[float(value) for value in line.split()] for line in lines[1:]]


def test_fitted_touchstone_export_writes_fitted_and_original_data(tmp_path, monkeypatch):
    writer = _touchstone_writer(tmp_path, fit_enabled=True, monkeypatch=monkeypatch)

    fitted_path = writer(_FakeGrid())

    assert fitted_path.endswith("TouchstoneTest_20260925-123456_fit.s2p")
    original_path = tmp_path / "TouchstoneTest_20260925-123456_original.s2p"
    assert os.path.isfile(fitted_path)
    assert original_path.is_file()

    fitted_rows = _read_touchstone(tmp_path / os.path.basename(fitted_path))
    original_rows = _read_touchstone(original_path)
    assert [row[0] for row in fitted_rows] == [1.0, 1.5, 2.0]
    assert [row[0] for row in original_rows] == [1.0, 2.0]
    assert [row[1::2] for row in fitted_rows] == [
        [111.0, 121.0, 112.0, 122.0],
        [112.0, 122.0, 113.0, 123.0],
        [113.0, 123.0, 114.0, 124.0],
    ]
    assert [row[2::2] for row in fitted_rows] == [[0.5] * 4] * 3
    assert [row[1::2] for row in original_rows] == [
        [11.1, 21.1, 12.1, 22.1],
        [11.2, 21.2, 12.2, 22.2],
    ]
    assert [row[2::2] for row in original_rows] == [[0.01] * 4, [0.02] * 4]


def test_unfitted_touchstone_export_keeps_single_file_behavior(tmp_path, monkeypatch):
    writer = _touchstone_writer(tmp_path, fit_enabled=False, monkeypatch=monkeypatch)

    output_path = writer(_FakeGrid())

    assert output_path.endswith("TouchstoneTest_20260925-123456_fit.s2p")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "TouchstoneTest_20260925-123456_fit.s2p",
    ]
    rows = _read_touchstone(tmp_path / os.path.basename(output_path))
    assert [row[0] for row in rows] == [1.0, 2.0]
    assert [row[1::2] for row in rows] == [
        [11.1, 21.1, 12.1, 22.1],
        [11.2, 21.2, 12.2, 22.2],
    ]


def test_failed_fit_still_writes_exact_original_touchstone(tmp_path, monkeypatch, capsys):
    writer = _touchstone_writer(tmp_path, fit_enabled=True, monkeypatch=monkeypatch)

    fitted_path = writer(_FailingFitGrid())

    original_path = tmp_path / "TouchstoneTest_20260925-123456_original.s2p"
    assert original_path.is_file()
    assert not os.path.exists(fitted_path)
    rows = _read_touchstone(original_path)
    assert [row[0] for row in rows] == [1.0, 2.0]
    assert [row[1::2] for row in rows] == [
        [11.1, 21.1, 12.1, 22.1],
        [11.2, 21.2, 12.2, 22.2],
    ]
    assert [row[2::2] for row in rows] == [[0.01] * 4, [0.02] * 4]
    output = capsys.readouterr().out
    assert "fitting failed" in output
    assert "original samples were saved" in output


def test_generated_script_header_includes_em3d_modeler_version(tmp_path, monkeypatch):
    script, _ = _generated_namespace(tmp_path, fit_enabled=False, monkeypatch=monkeypatch)

    assert script.startswith(f"# EM 3D Modeler version: {__version__}\n")
    assert "TARGET_EMERGE_VERSION" in script


@pytest.mark.parametrize("export_enabled", [False, True])
def test_postprocess_plot_falls_back_to_raw_samples_when_fit_fails(
    tmp_path, monkeypatch, capsys, export_enabled
):
    plotted = {}
    plot_module = ModuleType("emerge.plot")
    plot_module.plot_sp = lambda frequencies, curves, labels: plotted.update(
        frequencies=list(frequencies), curves=curves, labels=labels
    )
    emerge_module = ModuleType("emerge")
    emerge_module.__path__ = []
    monkeypatch.setitem(sys.modules, "emerge", emerge_module)
    monkeypatch.setitem(sys.modules, "emerge.plot", plot_module)
    _, namespace = _generated_namespace(
        tmp_path,
        fit_enabled=True,
        monkeypatch=monkeypatch,
        export_enabled=export_enabled,
        plot_enabled=True,
    )
    grid = _FailingFitGrid()
    result = SimpleNamespace(scalar=SimpleNamespace(grid=grid))

    namespace["_postprocess_sparams"](None, result)

    assert plotted["frequencies"] == [1.0, 2.0]
    assert plotted["curves"] == [grid.S(i, j) for i in range(1, 3) for j in range(1, 3)]
    assert plotted["labels"] == ["S11", "S12", "S21", "S22"]
    assert len(grid.model_s_calls) == 1
    assert "S-parameter fitting failed" in capsys.readouterr().out
    if export_enabled:
        assert (tmp_path / "TouchstoneTest_20260925-123456_original.s2p").is_file()
        assert not (tmp_path / "TouchstoneTest_20260925-123456_fit.s2p").exists()


def test_postprocess_reuses_successful_export_fit_for_plot(tmp_path, monkeypatch):
    plotted = {}
    plot_module = ModuleType("emerge.plot")
    plot_module.plot_sp = lambda frequencies, curves, labels: plotted.update(
        frequencies=list(frequencies), curves=curves, labels=labels
    )
    emerge_module = ModuleType("emerge")
    emerge_module.__path__ = []
    monkeypatch.setitem(sys.modules, "emerge", emerge_module)
    monkeypatch.setitem(sys.modules, "emerge.plot", plot_module)
    _, namespace = _generated_namespace(
        tmp_path,
        fit_enabled=True,
        monkeypatch=monkeypatch,
        export_enabled=True,
        plot_enabled=True,
    )
    grid = _FakeGrid()
    model_s = grid.model_S
    model_s_calls = []

    def counted_model_s(output_port, input_port, frequencies):
        model_s_calls.append((output_port, input_port))
        return model_s(output_port, input_port, frequencies)

    grid.model_S = counted_model_s
    result = SimpleNamespace(scalar=SimpleNamespace(grid=grid))

    namespace["_postprocess_sparams"](None, result)

    assert len(model_s_calls) == 4
    assert plotted["frequencies"] == [1.0, 1.5, 2.0]
    assert plotted["curves"] == [
        grid.model_S(i, j, [1.0, 1.5, 2.0]) for i in range(1, 3) for j in range(1, 3)
    ]
    assert (tmp_path / "TouchstoneTest_20260925-123456_original.s2p").is_file()
    assert (tmp_path / "TouchstoneTest_20260925-123456_fit.s2p").is_file()