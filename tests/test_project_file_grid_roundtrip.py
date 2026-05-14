from pathlib import Path

from em3d_modeler.emerge.project_file import ProjectFile


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
