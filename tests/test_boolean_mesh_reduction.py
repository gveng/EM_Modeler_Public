import pytest
import vtk

from em3d_modeler.emerge.occ_export_builder import build_occ_shape_for_export
from em3d_modeler.scene.boolean_ops import _decimate_polydata, fuse_many
from em3d_modeler.scene.em_objects import MeshObject


def _sphere(resolution: int = 40) -> vtk.vtkPolyData:
    source = vtk.vtkSphereSource()
    source.SetThetaResolution(resolution)
    source.SetPhiResolution(resolution)
    source.Update()
    polydata = vtk.vtkPolyData()
    polydata.DeepCopy(source.GetOutput())
    return polydata


def test_decimation_reduces_triangle_count_and_keeps_surface():
    source = _sphere()

    result = _decimate_polydata(source, 0.5)

    assert 0 < result.GetNumberOfPolys() < source.GetNumberOfPolys()
    assert result.GetBounds() == pytest.approx(source.GetBounds(), abs=0.03)


def test_fuse_many_applies_requested_reduction():
    first = MeshObject("First", _sphere(24))
    second = MeshObject("Second", _sphere(24))
    second.actor.SetPosition(3.0, 0.0, 0.0)
    original_polys = (
        first.actor.GetMapper().GetInput().GetNumberOfPolys()
        + second.actor.GetMapper().GetInput().GetNumberOfPolys()
    )

    result = fuse_many([first, second], target_reduction=0.5)

    assert 0 < result.GetNumberOfPolys() < original_polys


@pytest.mark.parametrize("reduction", [0.0, -0.1, 0.81, 1.0])
def test_decimation_rejects_unsafe_reduction(reduction):
    with pytest.raises(ValueError, match="greater than 0 and at most 0.8"):
        _decimate_polydata(_sphere(12), reduction)


def test_boolean_mesh_reduction_round_trips_in_object_parameters():
    original = MeshObject("ReducedFuse", _sphere(), boolean_op="fuse")
    original.boolean_mesh_reduction = 0.5
    restored = MeshObject("RestoredFuse", _sphere(), boolean_op="fuse")

    restored.set_parameters(original.get_parameters())

    assert restored.boolean_mesh_reduction == pytest.approx(0.5)


def test_reduced_boolean_export_does_not_rebuild_exact_source_shape(monkeypatch):
    reduced = MeshObject("ReducedFuse", _sphere(), boolean_op="fuse")
    reduced.boolean_mesh_reduction = 0.5

    def unexpected_rebuild(*_args, **_kwargs):
        pytest.fail("Reduced result should export its stored mesh")

    monkeypatch.setattr(
        "em3d_modeler.emerge.occ_export_builder._boolean_from_sources",
        unexpected_rebuild,
    )
    monkeypatch.setattr(
        "em3d_modeler.emerge.occ_export_builder._boolean_from_source_data",
        unexpected_rebuild,
    )

    assert build_occ_shape_for_export(reduced) is None