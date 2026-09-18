from pathlib import Path

from em3d_modeler.emerge.project_file import ProjectFile
from em3d_modeler.emerge.python_script_exporter import export_emerge_python_script
from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget


def test_project_file_grid_roundtrip(tmp_path: Path) -> None:
    project_path = tmp_path / "grid_roundtrip.em3d"

    ProjectFile.save(
        path=project_path,
        project_name="GridRoundTrip",
        settings={},
        objects_json=[],
        units="um",
        grid_size=1234.5,
        grid_spacing=0.25,
        grid_plane="XZ",
        reference_planes=[],
        active_plane_name=None,
        project_materials=[],
        global_material_db_path=None,
        camera={},
    )

    data = ProjectFile.load(project_path)

    assert data["units"] == "um"
    assert data["grid"]["size"] == 1234.5
    assert data["grid"]["spacing"] == 0.25
    assert data["grid"]["plane"] == "XZ"


def test_project_tree_accepts_trace_log_verbosity() -> None:
    widget = ProjectTreeWidget()

    widget.set_log_verbosity("Trace")

    assert widget.get_log_verbosity() == "Trace"


def test_generated_script_skips_boundaries_on_port_surfaces() -> None:
    script = export_emerge_python_script(
        project_name="Dipole",
        settings={
            "simulation": {"Fmin_GHz": 0.1, "Fmax_GHz": 1.0, "Fstep_GHz": 0.1, "LogVerbosity": "Info"},
            "object_boundaries": [{"object": "Plate_2", "type": "PEC"}],
        },
        step_entries=[],
        units="mm",
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "plate_name": "Plate_2",
            "origin": [0.0, 0.0, 0.0],
            "u": [10.0, 0.0, 0.0],
            "v": [0.0, 10.0, 0.0],
            "width": 10.0,
            "height": 10.0,
            "direction": [0.0, 0.0, 1.0],
            "z0": 50.0,
            "power": 1.0,
        }],
    )

    assert 'port_surfaces["Plate_2"].boundary()' not in script
    assert 'simulationObj.mw.bc.PEC(port_surfaces["Plate_2"].boundary())' not in script
    assert "def _boundary_faces(geometry_group):" in script
    assert '_boundary_faces(geometry_groups["' in script
    assert 'save_fields = True' not in script


def test_generated_script_creates_port_plate_before_assigning_lumped_port() -> None:
    script = export_emerge_python_script(
        project_name="PortOrder",
        settings={},
        step_entries=[],
        units="mm",
        lumped_ports=[{
            "index": 1,
            "name": "Port_1",
            "plate_name": "Plate_1",
            "origin": [0.0, 0.0, 0.0],
            "u": [10.0, 0.0, 0.0],
            "v": [0.0, 10.0, 0.0],
            "width": 10.0,
            "height": 10.0,
            "direction": [0.0, 0.0, 1.0],
            "z0": 50.0,
            "power": 1.0,
        }],
    )

    plate_index = script.index('port_surfaces["Plate_1"] = em.geo.Plate(')
    commit_index = script.index("simulationObj.commit_geometry()")
    port_index = script.index("simulationObj.mw.bc.LumpedPort(")
    assert plate_index < commit_index < port_index


def test_generated_script_collects_boundaries_from_step_items() -> None:
    script = export_emerge_python_script(
        project_name="Dipole2",
        settings={
            "object_boundaries": [{"object": "Sphere_1", "type": "Open"}],
        },
        step_entries=[{
            "object_name": "Sphere_1",
            "step_file": "Sphere_1.step",
            "material": "PEC",
        }],
        units="mm",
    )

    assert "def _boundary_faces(geometry_group):" in script
    assert 'simulationObj.mw.bc.AbsorbingBoundary(_boundary_faces(geometry_groups["Sphere_1"]))' in script
    compile(script, "generated_emerge.py", "exec")
