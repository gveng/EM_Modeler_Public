import ast
import os
from types import SimpleNamespace

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


def _touchstone_writer(tmp_path, fit_enabled, monkeypatch):
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
    writer = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_write_sputility_touchstone"
    )
    namespace = {
        "datetime": _FixedDateTime,
        "os": os,
        "SCRIPT_DIR": str(tmp_path),
        "PROJECT_NAME": "TouchstoneTest",
        "SPARAM_FIT_ENABLED": fit_enabled,
        "SPARAM_FIT_POINTS": 3,
        "_number_of_ports": lambda grid: int(grid.Smat.shape[1]),
        "_safe_token": lambda value: value,
    }
    exec(compile(ast.Module(body=[writer], type_ignores=[]), "generated_script.py", "exec"), namespace)
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
    assert [set(row[1::2]) for row in fitted_rows] == [
        {111.0, 112.0, 121.0, 122.0},
        {112.0, 113.0, 122.0, 123.0},
        {113.0, 114.0, 123.0, 124.0},
    ]
    assert [set(row[2::2]) for row in fitted_rows] == [{0.5}] * 3
    assert [set(row[1::2]) for row in original_rows] == [
        {11.1, 12.1, 21.1, 22.1},
        {11.2, 12.2, 21.2, 22.2},
    ]
    assert [set(row[2::2]) for row in original_rows] == [{0.01}, {0.02}]


def test_unfitted_touchstone_export_keeps_single_file_behavior(tmp_path, monkeypatch):
    writer = _touchstone_writer(tmp_path, fit_enabled=False, monkeypatch=monkeypatch)

    output_path = writer(_FakeGrid())

    assert output_path.endswith("TouchstoneTest_20260925-123456_fit.s2p")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "TouchstoneTest_20260925-123456_fit.s2p",
    ]
    rows = _read_touchstone(tmp_path / os.path.basename(output_path))
    assert [row[0] for row in rows] == [1.0, 2.0]
    assert [set(row[1::2]) for row in rows] == [
        {11.1, 12.1, 21.1, 22.1},
        {11.2, 12.2, 21.2, 22.2},
    ]