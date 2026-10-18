import ast

import pytest

from em3d_modeler.emerge.python_script_exporter import (
    build_clean_emerge_python_script,
    export_emerge_python_script,
)
from em3d_modeler.emerge.mesh_algorithms import MESH_ALGORITHM_NAMES


@pytest.mark.parametrize(
    ("solver", "expected_mp_solver"),
    [
        ("AASDS", "AASDS"),
        ("UMFPACK", "UMFPACK"),
        ("MUMPS", "UNNAMED"),
        ("SPARTA", "Sparta"),
    ],
)
def test_supported_solver_sweep_uses_process_pool_in_generated_and_clean_scripts(
    solver, expected_mp_solver
):
    script = export_emerge_python_script(
        project_name="ParallelSweepTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver=solver,
        parallel_enabled=True,
        worker_processes=4,
        pardiso_threads=6,
        acc_threads=12,
    )
    clean_script = build_clean_emerge_python_script(script, "sweep")

    for generated in (script, clean_script):
        ast.parse(generated)
        assert "MULTIPROCESSING_ENABLED = True" in generated
        assert f"simulationObj.set_solver(em.EMSolver.{solver})" in generated
        assert "WORKER_PROCESSES = 4" in generated
        assert "FREQUENCY_GROUP_SIZE = 8" in generated
        assert "config.set_pardiso_threads(6)" in generated
        assert "config.set_acc_threads(12)" in generated
        assert "parallel=True" in generated
        assert "n_workers=WORKER_PROCESSES" in generated
        assert "multi_processing=True" in generated
        assert "frequency_groups=FREQUENCY_GROUP_SIZE" in generated
        assert "_run_configured_sweep()" in generated
        assert (
            f"os.environ['EMERGE_MP_SOLVER'] = \"{expected_mp_solver}\""
            in generated
        )
        assert generated.index("EMERGE_MP_SOLVER") < generated.index(
            "import emerge as em"
        )
        assert generated.index("config.set_pardiso_threads(6)") < generated.index(
            "import emerge as em"
        )


@pytest.mark.parametrize("solver", ["PARDISO", "SUPERLU", "CUDSS"])
def test_unsupported_solver_sweep_uses_standard_run(solver):
    script = export_emerge_python_script(
        project_name="SingleSweepTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver=solver,
        parallel_enabled=True,
        worker_processes=4,
        pardiso_threads=6,
        acc_threads=12,
    )
    clean_script = build_clean_emerge_python_script(script, "sweep")

    for generated in (script, clean_script):
        ast.parse(generated)
        assert "MULTIPROCESSING_ENABLED = False" in generated
        assert "WORKER_PROCESSES = 4" in generated
        assert "config.set_pardiso_threads(6)" in generated
        assert "config.set_acc_threads(12)" in generated
        assert "EMERGE_MP_SOLVER" not in generated
        assert "parallel=True" in generated
        assert "multi_processing=True" in generated
        assert "if MULTIPROCESSING_ENABLED:" in generated
        assert "return simulationObj.mw.run_sweep()" in generated


def test_supported_solver_can_disable_process_pool():
    script = export_emerge_python_script(
        project_name="DisabledParallelSweepTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver="UMFPACK",
        parallel_enabled=False,
        worker_processes=4,
    )

    ast.parse(script)
    assert "MULTIPROCESSING_ENABLED = False" in script
    assert "EMERGE_MP_SOLVER" not in script
    assert "parallel=True" in script
    assert "if MULTIPROCESSING_ENABLED:" in script
    assert "return simulationObj.mw.run_sweep()" in script


@pytest.mark.parametrize("precision", ["single", "double"])
def test_cudss_precision_is_set_before_solver_selection_in_generated_scripts(
    precision,
):
    script = export_emerge_python_script(
        project_name="CUDSSPrecisionTest",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver="CUDSS",
        cudss_precision=precision,
    )
    clean_script = build_clean_emerge_python_script(script, "sweep")
    precision_line = (
        "simulationObj.mw.solveroutine.solvers[em.EMSolver.CUDSS]."
        f"set_precision('{precision}')"
    )
    solver_line = "simulationObj.set_solver(em.EMSolver.CUDSS)"

    for generated in (script, clean_script):
        ast.parse(generated)
        assert precision_line in generated
        assert generated.index(precision_line) < generated.index(solver_line)


def test_cudss_precision_defaults_to_emerge_auto_and_does_not_apply_to_sparta():
    automatic_script = export_emerge_python_script(
        project_name="CUDSSAutomaticPrecision",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver="CUDSS",
    )
    sparta_script = export_emerge_python_script(
        project_name="SpartaPrecision",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
        solver="SPARTA",
        cudss_precision="double",
    )

    assert ".set_precision(" not in automatic_script
    assert "EMSolver.CUDSS].set_precision(" not in sparta_script


def test_curved_boundary_meshing_call_is_omitted_when_disabled():
    script = export_emerge_python_script(
        project_name="CurvedBoundaryDisabled",
        settings={
            "mesh": {"curved_boundary_meshing_enabled": False},
            "simulations": [{"name": "Sweep", "type": "Sweep"}],
        },
        step_entries=[],
    )

    ast.parse(script)
    assert "simulationObj.mesher.set_curved_boundary_meshing(" not in script


def test_curved_boundary_meshing_call_remains_enabled_by_default():
    script = export_emerge_python_script(
        project_name="CurvedBoundaryDefault",
        settings={"simulations": [{"name": "Sweep", "type": "Sweep"}]},
        step_entries=[],
    )

    assert "simulationObj.mesher.set_curved_boundary_meshing(CURVED_BOUNDARY_RESOLUTION)" in script
    assert "simulationObj.mesher.set_algorithm(em.Algorithm3D.DELAUNAY)" in script


@pytest.mark.parametrize("algorithm", sorted(MESH_ALGORITHM_NAMES))
def test_selected_3d_meshing_algorithm_is_emitted_as_enum_in_generated_and_clean_scripts(
    algorithm,
):
    script = export_emerge_python_script(
        project_name=f"Mesher{algorithm}",
        settings={
            "mesh": {"algorithm": algorithm},
            "simulations": [{"name": "Sweep", "type": "Sweep"}],
        },
        step_entries=[],
    )
    clean_script = build_clean_emerge_python_script(script, "sweep")
    expected_call = (
        f"simulationObj.mesher.set_algorithm(em.Algorithm3D.{algorithm})"
    )

    for generated in (script, clean_script):
        ast.parse(generated)
        assert expected_call in generated
        assert f"set_algorithm('{algorithm}')" not in generated


def test_invalid_3d_meshing_algorithm_falls_back_to_delaunay():
    script = export_emerge_python_script(
        project_name="InvalidMesher",
        settings={
            "mesh": {"algorithm": "not-a-mesher"},
            "simulations": [{"name": "Sweep", "type": "Sweep"}],
        },
        step_entries=[],
    )

    assert (
        "simulationObj.mesher.set_algorithm(em.Algorithm3D.DELAUNAY)"
        in script
    )
