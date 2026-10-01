import ast
import contextlib
import io
import json
import math
import os
import sys
from types import ModuleType
from types import SimpleNamespace

import numpy as np
import pytest
from em3d_modeler import __version__
from em3d_modeler.emerge import python_script_exporter
from em3d_modeler.emerge.python_script_exporter import build_clean_emerge_python_script


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


def test_object_conductor_boundaries_export_with_material_and_si_thickness(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ConductorBoundaryTest",
        settings={"object_boundaries": [
            {"object": "CopperWall", "type": "Surface Impedance"},
            {
                "object": "CopperSheet",
                "type": "Thin Conductor",
                "params": {"Thickness_mm": 0.035},
            },
        ]},
        step_entries=[{
            "object_name": "CopperWall",
            "step_file": "copper-wall.step",
            "material": "Copper",
        }],
        plate_entries=[{
            "object_name": "CopperSheet",
            "origin": (0.0, 0.0, 0.0),
            "u": (0.01, 0.0, 0.0),
            "v": (0.0, 0.01, 0.0),
            "material": "Copper",
        }],
        materials_catalog={"Copper": {"sigma": 5.8e7}},
        run_sweep=False,
    )

    ast.parse(script)
    assert (
        'simulationObj.mw.bc.SurfaceImpedance('
        '_boundary_faces(geometry_groups["CopperWall"]), material=materials["Copper"])'
    ) in script
    assert (
        'simulationObj.mw.bc.ThinConductor('
        'plate_objects["CopperSheet"], material=materials["Copper"], thickness=3.5e-05)'
    ) in script


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
        "PARAM_NAME": "width",
        "PARAM_VALUES": "0.2,0.4",
        "FMIN_GHZ": 1.0,
        "FMAX_GHZ": 2.0,
        "NPOINTS": 2,
        "OUTPUT_CONFIGS": [],
        "SAVE_FARFIELDS": False,
        "math": math,
        "np": np,
        "_number_of_ports": lambda grid: int(grid.Smat.shape[1]),
        "_safe_token": lambda value: value,
    }
    helper_names = {
        "_write_sputility_touchstone",
        "_parametric_result_grids",
        "_postprocess_sparams",
    }
    helpers = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "generated_script.py", "exec"), namespace)
    return script, namespace


def _touchstone_writer(tmp_path, fit_enabled, monkeypatch):
    _, namespace = _generated_namespace(tmp_path, fit_enabled, monkeypatch)
    return namespace["_write_sputility_touchstone"]


def test_generated_simulation_script_uses_configured_log_verbosity(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    script = python_script_exporter.export_emerge_python_script(
        project_name="VerboseTest",
        settings={"simulations": [{"name": "Sweep", "LogVerbosity": "Trace"}]},
        step_entries=[],
        run_sweep=False,
    )

    assert 'em.Simulation(PROJECT_NAME, loglevel="TRACE", save_file=True, write_log=True)' in script


def _progressive_sweep_namespace(script, *, version="3.0.0a16", omit_sample=None):
    class FakeSimulation:
        def __init__(self):
            scalar = SimpleNamespace(_variables=[], _data_entries=[])
            self.mw = SimpleNamespace(data=SimpleNamespace(scalar=scalar))
            self.mw.set_frequency = self.set_frequency
            self.mw.set_frequency_range = self.set_frequency_range
            self.mw.run_sweep = self.run_sweep
            self.range_calls = []
            self.run_calls = 0

        def set_frequency_range(self, fmin, fmax, count):
            self.range_calls.append((fmin, fmax, count))
            self.frequencies = np.linspace(fmin, fmax, count).tolist()

        def set_frequency(self, frequencies):
            self.frequencies = list(frequencies)

        def run_sweep(self):
            self.run_calls += 1
            scalar = self.mw.data.scalar
            for frequency in self.frequencies:
                sample_index = len(scalar._variables)
                if sample_index == omit_sample:
                    continue
                matrix = np.asarray([
                    [complex(sample_index + 1, 1), complex(sample_index + 2, 2)],
                    [complex(sample_index + 3, 3), complex(sample_index + 4, 4)],
                ])
                scalar._variables.append({"freq": frequency})
                scalar._data_entries.append(SimpleNamespace(freq=frequency, Sp=matrix))
            return self.mw.data

    simulation = FakeSimulation()
    namespace = {
        "simulationObj": simulation,
        "RUNTIME_EMERGE_VERSION": version,
        "FMIN_GHZ": 1.0,
        "FMAX_GHZ": 2.0,
        "NPOINTS": 23,
        "PROGRESSIVE_SPARAMS_CHUNK_SIZE": next(
            ast.literal_eval(node.value)
            for node in ast.parse(script).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PROGRESSIVE_SPARAMS_CHUNK_SIZE"
                for target in node.targets
            )
        ),
        "JOB_NAME": "Sweep",
        "json": json,
        "math": math,
        "np": np,
    }
    tree = ast.parse(script)
    helper_names = {"_validate_progressive_samples", "_emit_progressive_sparams", "_run_progressive_sweep"}
    helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in helper_names]
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "generated_script.py", "exec"), namespace)
    return simulation, namespace


def test_progressive_sweep_reuses_accumulated_data_and_restores_full_band(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ProgressiveTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        progressive_sparams_enabled=True,
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
        run_sweep=True,
    )
    simulation, namespace = _progressive_sweep_namespace(script)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        result = namespace["_run_progressive_sweep"]()

    events = [
        json.loads(line.removeprefix("EM3D_SPARAM_PROGRESS:"))
        for line in output.getvalue().splitlines()
        if line.startswith("EM3D_SPARAM_PROGRESS:")
    ]
    assert result is simulation.mw.data
    assert simulation.run_calls == 3
    assert simulation.range_calls == [(1e9, 2e9, 23), (1e9, 2e9, 23)]
    assert script.count("simulationObj.generate_mesh()") == 1
    assert script.rindex("simulationObj.generate_mesh()") < script.index("simulationResult = _run_progressive_sweep()")
    assert [len(event["frequencies"]) for event in events] == [10, 10, 3]
    assert [event["completed_samples"] for event in events] == [10, 20, 23]
    assert events[-1]["complete"] is True
    assert events[0]["s_matrices"][0] == [
        [[1.0, 1.0], [2.0, 2.0]],
        [[3.0, 3.0], [4.0, 4.0]],
    ]


def test_progressive_sweep_uses_configured_update_interval(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0a16")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ProgressiveIntervalTest",
        settings={"simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "progressive_sparams_enabled": True,
            "progressive_sparams_chunk_size": 7,
        }]},
        step_entries=[],
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
    )
    simulation, namespace = _progressive_sweep_namespace(script)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        namespace["_run_progressive_sweep"]()

    events = [
        json.loads(line.removeprefix("EM3D_SPARAM_PROGRESS:"))
        for line in output.getvalue().splitlines()
        if line.startswith("EM3D_SPARAM_PROGRESS:")
    ]
    assert "PROGRESSIVE_SPARAMS_CHUNK_SIZE = 7" in script
    assert simulation.run_calls == 4
    assert [len(event["frequencies"]) for event in events] == [7, 7, 7, 2]
    assert [event["completed_samples"] for event in events] == [7, 14, 21, 23]


def test_progressive_sweep_rejects_unverified_version_and_missing_samples(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ProgressiveTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        progressive_sparams_enabled=True,
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
        run_sweep=False,
    )

    simulation, namespace = _progressive_sweep_namespace(script, version="3.0.0")
    with pytest.raises(RuntimeError, match="requires EMERGE 3.0.0a16"):
        namespace["_run_progressive_sweep"]()
    assert simulation.run_calls == 0

    simulation, namespace = _progressive_sweep_namespace(script, omit_sample=4)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), pytest.raises(RuntimeError, match="count mismatch"):
        namespace["_run_progressive_sweep"]()
    assert "EM3D_SPARAM_PROGRESS:" not in output.getvalue()


def test_parametric_progress_helper_emits_completed_frequency_sweep(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0a16")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ProgressiveParametricTest",
        settings={"simulations": [{
            "name": "Parametric",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValues": "0.2,0.4",
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 3,
            "progressive_sparams_enabled": True,
            "progressive_sparams_chunk_size": 2,
        }]},
        step_entries=[],
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
        run_sweep=True,
    )
    assert "PROGRESSIVE_SPARAMS_CHUNK_SIZE = 2" in script
    assert "range(0, NPOINTS, PROGRESSIVE_SPARAMS_CHUNK_SIZE)" in script
    assert "set_frequency(expected_parametric_frequencies[_start:_end])" in script
    tree = ast.parse(script)
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_emit_progressive_parametric_sparams"
    )
    namespace = {
        "RUNTIME_EMERGE_VERSION": "3.0.0a16",
        "NPOINTS": 3,
        "FMIN_GHZ": 1.0,
        "FMAX_GHZ": 2.0,
        "JOB_NAME": "Parametric",
        "PARAM_NAME": "width",
        "np": np,
        "json": json,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "generated_script.py", "exec"), namespace)
    frequencies = np.linspace(1e9, 2e9, 3)
    result = SimpleNamespace(scalar=SimpleNamespace(
        _variables=[{"freq": frequency} for frequency in frequencies],
        _data_entries=[
            SimpleNamespace(freq=frequency, Sp=np.asarray([[complex(index, 0.5)]]))
            for index, frequency in enumerate(frequencies, start=1)
        ],
    ))
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        namespace["_emit_progressive_parametric_sparams"](result, "0.4", 2, 2, 0, 2, 0)
        namespace["_emit_progressive_parametric_sparams"](result, "0.4", 2, 2, 2, 3, 0)

    events = [
        json.loads(line.removeprefix("EM3D_SPARAM_PROGRESS:"))
        for line in output.getvalue().splitlines()
        if line.startswith("EM3D_SPARAM_PROGRESS:")
    ]
    assert len(events) == 2
    event = events[0]
    assert event["parameter_name"] == "width"
    assert event["parameter_value"] == "0.4"
    assert event["completed_parameters"] == 2
    assert event["chunk_start"] == 0
    assert event["completed_samples"] == 2
    assert event["complete"] is False
    assert event["frequencies"] == frequencies[:2].tolist()
    assert event["s_matrices"][0] == [[[1.0, 0.5]]]
    assert events[1]["chunk_start"] == 2
    assert events[1]["completed_samples"] == 3
    assert events[1]["complete"] is True


def test_parametric_runner_emits_configured_frequency_chunks(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0a16")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ParametricChunkRunnerTest",
        settings={"simulations": [{
            "name": "Parametric",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValues": "0.2,0.4",
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 3,
            "progressive_sparams_enabled": True,
            "progressive_sparams_chunk_size": 2,
        }]},
        step_entries=[],
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
        run_sweep=True,
    )
    tree = ast.parse(script)
    run_block = next(
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "JOB_TYPE"
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "sweep"
    )

    class FakeSimulation:
        def __init__(self, invalid_sample=None):
            scalar = SimpleNamespace(_variables=[], _data_entries=[])
            self.mw = SimpleNamespace(data=SimpleNamespace(scalar=scalar))
            self.mw.set_frequency_range = self.set_frequency_range
            self.mw.set_frequency = self.set_frequency
            self.mw.run_sweep = self.run_sweep
            self.range_calls = []
            self.run_calls = 0
            self.invalid_sample = invalid_sample

        def set_frequency_range(self, fmin, fmax, count):
            self.range_calls.append((fmin, fmax, count))
            self.frequencies = np.linspace(fmin, fmax, count).tolist()

        def set_frequency(self, frequencies):
            self.frequencies = list(frequencies)

        def run_sweep(self):
            self.run_calls += 1
            scalar = self.mw.data.scalar
            for frequency in self.frequencies:
                scalar._variables.append({"freq": frequency})
                value = (
                    complex(float("nan"), 0.0)
                    if len(scalar._data_entries) == self.invalid_sample
                    else complex(len(scalar._data_entries) + 1, 0.5)
                )
                scalar._data_entries.append(SimpleNamespace(
                    freq=frequency,
                    Sp=np.asarray([[value]]),
                ))
            return self.mw.data

    simulation = FakeSimulation()
    namespace = {
        "JOB_TYPE": "parametric",
        "JOB_NAME": "Parametric",
        "PARAM_NAME": "width",
        "PARAM_VALUES": "0.2,0.4",
        "FMIN_GHZ": 1.0,
        "FMAX_GHZ": 2.0,
        "NPOINTS": 3,
        "PROGRESSIVE_SPARAMS_ENABLED": True,
        "PROGRESSIVE_SPARAMS_HAVE_PORTS": True,
        "PROGRESSIVE_SPARAMS_CHUNK_SIZE": 2,
        "RUNTIME_EMERGE_VERSION": "3.0.0a16",
        "simulationObj": simulation,
        "os": SimpleNamespace(environ={}),
        "np": np,
        "json": json,
        "math": math,
    }
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_emit_progressive_parametric_sparams"
    )
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "generated_script.py", "exec"), namespace)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exec(compile(ast.Module(body=[run_block], type_ignores=[]), "generated_script.py", "exec"), namespace)

    events = [
        json.loads(line.removeprefix("EM3D_SPARAM_PROGRESS:"))
        for line in output.getvalue().splitlines()
        if line.startswith("EM3D_SPARAM_PROGRESS:")
    ]
    assert simulation.run_calls == 4
    assert [len(event["frequencies"]) for event in events] == [2, 1, 2, 1]
    assert [event["parameter_value"] for event in events] == ["0.2", "0.2", "0.4", "0.4"]
    assert [event["completed_samples"] for event in events] == [2, 3, 2, 3]
    assert [event["complete"] for event in events] == [False, True, False, True]

    invalid_simulation = FakeSimulation(invalid_sample=0)
    invalid_namespace = {**namespace, "simulationObj": invalid_simulation}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(ast.Module(body=[run_block], type_ignores=[]), "generated_script.py", "exec"), invalid_namespace)

    assert invalid_simulation.run_calls == 3
    assert "Progressive plot update skipped" in output.getvalue()
    assert "Parametric sweep value=0.4 completed" in output.getvalue()
    assert "EM3D_SPARAM_PROGRESS:" not in output.getvalue()


def test_single_value_parametric_worker_sets_parameter_before_geometry_and_mesh(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0a16")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ParametricStepWorkerTest",
        settings={"simulations": [{
            "name": "Simulation_2",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValues": "0.4",
            "Fmin_GHz": 1.0,
            "Fmax_GHz": 2.0,
            "NumberOfPoints": 3,
        }]},
        step_entries=[{"object_name": "Body", "step_file": "Body.step"}],
        run_sweep=True,
    )

    parameter_assignment = script.index(
        "setattr(simulationObj, PARAM_NAME, _initial_parametric_value)"
    )
    geometry_setup = script.index("# [5] GEOMETRY: ONE STEP FILE PER OBJECT")
    mesh_setup = script.index("# [8] MESH GENERATION")
    assert parameter_assignment < geometry_setup < mesh_setup
    assert 'PARAM_VALUES = "0.4"' in script


def test_parametric_irregular_grid_skips_touchstone_without_failing(monkeypatch, tmp_path):
    _, namespace = _generated_namespace(
        tmp_path, fit_enabled=False, monkeypatch=monkeypatch, export_enabled=True
    )
    namespace["JOB_TYPE"] = "parametric"

    class IrregularScalar:
        @property
        def grid(self):
            raise ValueError("Data not in regular grid")

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        namespace["_postprocess_sparams"](
            None, SimpleNamespace(scalar=IrregularScalar())
        )

    assert "automatic Touchstone export and plotting were skipped" in output.getvalue()


def test_parametric_irregular_grid_exports_one_touchstone_per_value(monkeypatch, tmp_path):
    _, namespace = _generated_namespace(
        tmp_path, fit_enabled=False, monkeypatch=monkeypatch, export_enabled=True
    )
    namespace["JOB_TYPE"] = "parametric"
    frequencies = [1e9, 2e9, 1e9, 2e9]

    class IrregularScalar:
        _variables = [{"freq": frequency} for frequency in frequencies]
        _data_entries = [
            SimpleNamespace(freq=frequency, Sp=np.asarray([[complex(index, 0.0)]]))
            for index, frequency in enumerate(frequencies, start=1)
        ]

        @property
        def grid(self):
            raise ValueError("Data not in regular grid")

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        namespace["_postprocess_sparams"](
            None, SimpleNamespace(scalar=IrregularScalar())
        )

    files = sorted((tmp_path / "Touchstone").glob("*.s1p"))
    assert [path.name.split("_2026", 1)[0] for path in files] == [
        "TouchstoneTest_Sweep_width_0.2", "TouchstoneTest_Sweep_width_0.4",
    ]
    assert all(len(path.read_text(encoding="ascii").splitlines()) == 3 for path in files)
    assert "Post-processed 2 complete parametric S-parameter sweeps" in output.getvalue()


def test_parametric_nonfinite_final_samples_are_not_exported(monkeypatch, tmp_path):
    _, namespace = _generated_namespace(
        tmp_path, fit_enabled=False, monkeypatch=monkeypatch, export_enabled=True
    )
    namespace["JOB_TYPE"] = "parametric"
    frequencies = [1e9, 2e9, 1e9, 2e9]

    class ParametricScalar:
        _variables = [{"freq": frequency} for frequency in frequencies]
        _data_entries = [
            SimpleNamespace(
                freq=frequency,
                Sp=np.asarray([[complex(float("nan") if index == 1 else index, 0.0)]]),
            )
            for index, frequency in enumerate(frequencies, start=1)
        ]

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        namespace["_postprocess_sparams"](
            None, SimpleNamespace(scalar=ParametricScalar())
        )

    assert "non-finite S-parameter values" in output.getvalue()
    assert not list((tmp_path / "Touchstone").glob("*.s1p"))


@pytest.mark.parametrize(
    ("enabled", "legacy_argument"),
    [(True, False), (False, True)],
)
def test_progressive_sweep_setting_is_read_from_simulation_definition(
    monkeypatch, enabled, legacy_argument
):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0a16")
    script = python_script_exporter.export_emerge_python_script(
        project_name="PerSimulationProgressiveTest",
        settings={"simulations": [{
            "name": "Sweep",
            "type": "Sweep",
            "progressive_sparams_enabled": enabled,
        }]},
        step_entries=[],
        progressive_sparams_enabled=legacy_argument,
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
    )

    assert f"PROGRESSIVE_SPARAMS_ENABLED = {enabled}" in script


def test_parametric_values_are_assigned_as_numbers_when_possible(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ParametricValuesTest",
        settings={"simulations": [{
            "name": "Parametric",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValues": "0.2,0.4,0.6",
        }]},
        step_entries=[],
    )

    assert "_parameter_value = float(_value)" in script
    assert "setattr(simulationObj, PARAM_NAME, _parameter_value)" in script

    clean_script = build_clean_emerge_python_script(script, "parametric")
    assert "setattr(simulationObj, 'width', 0.2)" in clean_script


def test_generated_script_keeps_geometry_at_project_scale(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    step_entries = [{
        "object_name": "Conductor",
        "step_file": "Conductor.step",
        "material": "Copper",
        "bounds_mm": (0, 100, 0, 20, 0, 5),
    }]
    script = python_script_exporter.export_emerge_python_script(
        project_name="ScaledGeometryTest",
        settings={},
        step_entries=step_entries,
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "type": "LumpedPort",
            "origin": (0, 0, 0),
            "u": (0, 10, 0),
            "v": (0, 0, 2),
            "width": 10,
            "height": 2,
        }],
        run_sweep=False,
    )

    ast.parse(script)
    assert "GEOMETRY_SCALE_FACTOR = 1.0" in script
    assert "if GEOMETRY_SCALE_FACTOR != 1.0:" in script
    assert "simulationObj.set_scale_factor(GEOMETRY_SCALE_FACTOR)" in script
    assert "unit=mm * GEOMETRY_SCALE_FACTOR)" in script
    assert "port[1]['w'] = 0.01" in script
    assert "port[1]['h'] = 0.002" in script


def test_generated_script_converts_imperial_port_coordinates_to_millimeters(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ImperialGeometryTest",
        settings={},
        step_entries=[],
        units="inch",
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "type": "LumpedPort",
            "origin": (1.0, 0.0, 0.0),
            "u": (1.0, 0.0, 0.0),
            "v": (0.0, 1.0, 0.0),
            "width": 1.0,
            "height": 1.0,
        }],
        run_sweep=False,
    )

    ast.parse(script)
    assert "0.0254" in script
    assert "port[1]['w'] = 0.0254" in script
    assert "port[1]['h'] = 0.0254" in script


def test_generated_script_applies_emerge_scale_and_keeps_physical_port_dimensions(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "3.0.0")
    script = python_script_exporter.export_emerge_python_script(
        project_name="ScaledGeometryTest",
        settings={"mesh": {"emerge_scale_factor": 1000.0}},
        step_entries=[{
            "object_name": "Conductor",
            "step_file": "Conductor.step",
            "material": "Copper",
        }],
        plate_entries=[{
            "object_name": "Contact",
            "origin": (0.0, 10.0, 0.0),
            "u": (0.0, 0.0, 2.0),
            "v": (0.0, 1.0, 0.0),
            "material": "PEC",
        }],
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "type": "LumpedPort",
            "origin": (0.0, 10.0, 0.0),
            "u": (0.0, 0.0, 2.0),
            "v": (0.0, 1.0, 0.0),
            "width": 2.0,
            "height": 1.0,
        }],
        run_sweep=False,
    )

    ast.parse(script)
    assert "GEOMETRY_SCALE_FACTOR = 1000.0" in script
    assert "simulationObj.set_scale_factor(GEOMETRY_SCALE_FACTOR)" in script
    assert "unit=mm * GEOMETRY_SCALE_FACTOR)" in script
    assert "_port_1_origin = (0.0, 10.0, 0.0)" in script
    assert "port[1]['w'] = 0.002" in script
    assert "port[1]['h'] = 0.001" in script
    assert "_port_bc.width *= GEOMETRY_SCALE_FACTOR" in script
    assert "_port_bc.height *= GEOMETRY_SCALE_FACTOR" in script
    assert "_port_bc.width = _physical_width" in script
    assert "_port_bc.height = _physical_height" in script


def test_clean_emerge_script_keeps_solver_commands_without_runtime_wrapper(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    full_script = python_script_exporter.export_emerge_python_script(
        project_name="CleanExportTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        progressive_sparams_enabled=True,
        lumped_ports=[{"index": 1, "name": "Port_1", "type": "LumpedPort"}],
        run_sweep=True,
    )

    clean_script = build_clean_emerge_python_script(full_script, "sweep")

    ast.parse(clean_script)
    assert "em.Simulation(PROJECT_NAME" in clean_script
    assert "simulationObj.set_scale_factor(GEOMETRY_SCALE_FACTOR)" in clean_script
    assert "if GEOMETRY_SCALE_FACTOR != 1.0:" not in clean_script
    assert "simulationObj.set_solver(em.EMSolver." in clean_script
    assert "importlib.metadata" not in clean_script
    assert "TARGET_EMERGE_VERSION" not in clean_script
    assert "RUNTIME_EMERGE_VERSION" not in clean_script
    assert "class _GeneratedGeometryGroup" not in clean_script
    assert "simulationObj.generate_mesh()" in clean_script
    assert "simulationObj.mw.run_sweep()" in clean_script
    assert "simulationObj.save()" in clean_script
    assert "_postprocess_sparams" not in clean_script
    assert not any(
        line.lstrip().startswith(("if ", "try:", "raise "))
        for line in clean_script.splitlines()
    )
    assert "hasattr(" not in clean_script
    assert "simulationResult" not in clean_script


def test_clean_emerge_script_precomputes_scaled_lumped_port_dimensions(monkeypatch):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    full_script = python_script_exporter.export_emerge_python_script(
        project_name="ScaledCleanExportTest",
        settings={"mesh": {"emerge_scale_factor": 1000.0}},
        step_entries=[],
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "type": "LumpedPort",
            "width": 10.0,
            "height": 2.0,
        }],
        run_sweep=True,
    )

    clean_script = build_clean_emerge_python_script(full_script, "sweep")
    mesh_call = "simulationObj.generate_mesh()"

    ast.parse(clean_script)
    assert "port[1]['bc'].width = 10.0" in clean_script
    assert "port[1]['bc'].height = 2.0" in clean_script
    assert "port[1]['bc'].width = 0.01" in clean_script
    assert "port[1]['bc'].height = 0.002" in clean_script
    assert clean_script.index("port[1]['bc'].width = 10.0") < clean_script.index(mesh_call)
    assert clean_script.index(mesh_call) < clean_script.index("port[1]['bc'].width = 0.01")
    assert "hasattr(" not in clean_script
    assert "_scaled_lumped_port_dimensions" not in clean_script
    assert "try:" not in clean_script


def test_clean_emerge_script_keeps_generated_geometry_group_when_used():
    source = (
        "class _GeneratedGeometryGroup:\n"
        "    def __init__(self, objects):\n"
        "        self.objects = list(objects)\n"
        "geometry_group = _GeneratedGeometryGroup([])\n"
    )

    clean_script = build_clean_emerge_python_script(source, "sweep")

    assert "class _GeneratedGeometryGroup" in clean_script
    assert "geometry_group = _GeneratedGeometryGroup([])" in clean_script


def test_clean_emerge_script_removes_runtime_guard_that_only_raises():
    source = (
        "geometry_objects = []\n"
        "if len(geometry_objects) != 1:\n"
        "    raise RuntimeError('Expected one geometry object')\n"
        "geometry_object = geometry_objects[0]\n"
    )

    clean_script = build_clean_emerge_python_script(source, "sweep")

    assert "if len(geometry_objects)" not in clean_script
    assert "raise RuntimeError" not in clean_script
    assert "geometry_object = geometry_objects[0]" in clean_script
    assert "_run_progressive_sweep" not in clean_script
    assert "RUNTIME_EMERGE_VERSION" not in clean_script
    assert "print(" not in clean_script
    assert not any(line.lstrip().startswith("#") for line in clean_script.splitlines())


@pytest.mark.parametrize(
    ("simulation_type", "expected_command"),
    [
        ("Eigenmode", "simulationObj.mw.run_eigenmode(EIGENMODE_COUNT)"),
        ("Parametric", "simulationObj.mw.run_sweep()"),
    ],
)
def test_clean_emerge_script_keeps_selected_job_command(
    monkeypatch, simulation_type, expected_command
):
    monkeypatch.setattr(python_script_exporter, "_detect_emerge_version", lambda: "unknown")
    full_script = python_script_exporter.export_emerge_python_script(
        project_name="CleanExportTest",
        settings={"simulations": [{"name": "Job", "type": simulation_type}]},
        step_entries=[],
        run_sweep=True,
    )

    clean_script = build_clean_emerge_python_script(full_script, simulation_type.lower())

    ast.parse(clean_script)
    assert expected_command in clean_script
    assert "_postprocess_sparams" not in clean_script


def _read_touchstone(path):
    lines = path.read_text(encoding="ascii").splitlines()
    assert lines[0] == "# HZ S RI R 50.0"
    return [[float(value) for value in line.split()] for line in lines[1:]]


def test_fitted_touchstone_export_writes_fitted_and_original_data(tmp_path, monkeypatch):
    writer = _touchstone_writer(tmp_path, fit_enabled=True, monkeypatch=monkeypatch)

    fitted_path = writer(_FakeGrid())

    touchstone_dir = tmp_path / "Touchstone"
    assert fitted_path.endswith("Touchstone\\TouchstoneTest_Sweep_20260925-123456_fit.s2p")
    original_path = touchstone_dir / "TouchstoneTest_Sweep_20260925-123456_original.s2p"
    assert os.path.isfile(fitted_path)
    assert original_path.is_file()

    fitted_rows = _read_touchstone(touchstone_dir / os.path.basename(fitted_path))
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

    assert output_path.endswith("Touchstone\\TouchstoneTest_Sweep_20260925-123456.s2p")
    touchstone_dir = tmp_path / "Touchstone"
    assert sorted(path.name for path in touchstone_dir.iterdir()) == [
        "TouchstoneTest_Sweep_20260925-123456.s2p",
    ]
    rows = _read_touchstone(touchstone_dir / os.path.basename(output_path))
    assert [row[0] for row in rows] == [1.0, 2.0]
    assert [row[1::2] for row in rows] == [
        [11.1, 21.1, 12.1, 22.1],
        [11.2, 21.2, 12.2, 22.2],
    ]


def test_failed_fit_still_writes_exact_original_touchstone(tmp_path, monkeypatch, capsys):
    writer = _touchstone_writer(tmp_path, fit_enabled=True, monkeypatch=monkeypatch)

    fitted_path = writer(_FailingFitGrid())

    original_path = tmp_path / "Touchstone" / "TouchstoneTest_Sweep_20260925-123456_original.s2p"
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
        touchstone_dir = tmp_path / "Touchstone"
        assert (touchstone_dir / "TouchstoneTest_Sweep_20260925-123456_original.s2p").is_file()
        assert not (touchstone_dir / "TouchstoneTest_Sweep_20260925-123456_fit.s2p").exists()


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
    touchstone_dir = tmp_path / "Touchstone"
    assert (touchstone_dir / "TouchstoneTest_Sweep_20260925-123456_original.s2p").is_file()
    assert (touchstone_dir / "TouchstoneTest_Sweep_20260925-123456_fit.s2p").is_file()