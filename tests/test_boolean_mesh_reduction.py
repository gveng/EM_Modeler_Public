import math

import pytest
import vtk
from unittest.mock import Mock

from em3d_modeler.emerge.occ_export_builder import build_occ_shape_for_export
from em3d_modeler.scene.boolean_ops import _decimate_polydata, boolean_many, fuse_many
from em3d_modeler.scene.em_objects import BoxObject, MeshObject


def _sphere(resolution: int = 40) -> vtk.vtkPolyData:
    source = vtk.vtkSphereSource()
    source.SetThetaResolution(resolution)
    source.SetPhiResolution(resolution)
    source.Update()
    polydata = vtk.vtkPolyData()
    polydata.DeepCopy(source.GetOutput())
    return polydata


def _mesh_volume(polydata: vtk.vtkPolyData) -> float:
    triangles = vtk.vtkTriangleFilter()
    triangles.SetInputData(polydata)
    triangles.Update()
    mass = vtk.vtkMassProperties()
    mass.SetInputConnection(triangles.GetOutputPort())
    mass.Update()
    return mass.GetVolume()


def test_decimation_reduces_triangle_count_and_keeps_surface():
    source = _sphere()

    result = _decimate_polydata(source, 0.5)

    assert 0 < result.GetNumberOfPolys() < source.GetNumberOfPolys()
    assert result.GetBounds() == pytest.approx(source.GetBounds(), abs=0.03)


def test_decimation_welds_split_normal_vertices_before_reducing():
    source = _sphere()
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(source)
    normals.SplittingOn()
    normals.SetFeatureAngle(0.0)
    normals.Update()
    split_mesh = normals.GetOutput()

    result = _decimate_polydata(split_mesh, 0.35)

    assert result.GetNumberOfPolys() <= split_mesh.GetNumberOfPolys() * 0.65 + 1
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


@pytest.mark.parametrize("target_reduction", [0.0, 0.2])
def test_cut_scaled_mesh_tool_removes_contained_volume(monkeypatch, target_reduction):
    def box(bounds):
        source = vtk.vtkCubeSource()
        source.SetBounds(*bounds)
        source.Update()
        polydata = vtk.vtkPolyData()
        polydata.DeepCopy(source.GetOutput())
        return polydata

    base = MeshObject("Base", box((0, 10, 0, 10, 0, 10)))
    tool = MeshObject("Scaled tool", box((-1, 1, -1, 1, -1, 1)))
    tool.actor.SetScale(2, 2, 2)
    tool.actor.SetPosition(5, 5, 5)
    monkeypatch.setattr("em3d_modeler.scene.boolean_ops._occ_boolean", lambda *_: None)

    result = boolean_many("cut", [base, tool], target_reduction=target_reduction)

    assert _mesh_volume(result) == pytest.approx(936.0, abs=1e-3)


def test_cut_scaled_curved_mesh_tool_preserves_cavity():
    base_source = vtk.vtkCubeSource()
    base_source.SetBounds(0, 10, 0, 10, 0, 10)
    base_source.Update()
    base = MeshObject("Base", base_source.GetOutput())

    tool_source = vtk.vtkCylinderSource()
    tool_source.SetRadius(1)
    tool_source.SetHeight(2)
    tool_source.SetResolution(64)
    tool_source.Update()
    tool = MeshObject("Scaled tool", tool_source.GetOutput())
    tool.actor.SetScale(2, 2, 2)
    tool.actor.SetPosition(5, 5, 5)

    result = boolean_many("cut", [base, tool])

    distance = vtk.vtkImplicitPolyDataDistance()
    distance.SetInput(result)
    assert _mesh_volume(result) == pytest.approx(1000.0 - math.pi * 16.0, abs=0.5)
    assert distance.EvaluateFunction((1, 1, 1)) < 0.0
    assert distance.EvaluateFunction((5, 5, 5)) > 0.0


def test_cut_scaled_step_tools_preserve_multiple_holes(tmp_path, monkeypatch):
    pytest.importorskip("OCP")
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    from em3d_modeler.emerge import occ_export_builder
    from em3d_modeler.emerge.step_importer import _ocp_shape_to_vtk
    from em3d_modeler.ui.main_window import MainWindow
    from types import SimpleNamespace

    step_shape = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.5, 12.0
    ).Shape()
    BRepMesh_IncrementalMesh(step_shape, 0.1, False, 0.2, True)
    step_path = tmp_path / "pin.step"
    writer = STEPControl_Writer()
    writer.Transfer(step_shape, STEPControl_AsIs)
    assert int(writer.Write(str(step_path))) == 1

    source_mesh = _ocp_shape_to_vtk(step_shape)
    base = BoxObject("Air", 0, 0, 0, 20, 20, 20)
    tools = []
    window = SimpleNamespace()
    window._scale_mesh_object = lambda obj, factor: MainWindow._scale_mesh_object(
        window, obj, factor
    )
    for index, (x, y) in enumerate(((4, 4), (16, 4), (4, 16), (16, 16))):
        tool = MeshObject(
            f"Pin {index}",
            source_mesh,
            step_source_path=str(step_path),
            step_solid_name="STEP_Solid_1",
        )
        tool.actor.SetPosition(x, y, 4.0)
        assert MainWindow._scale_by_attributes(window, tool, 2.0)
        assert tool.actor.GetScale() == pytest.approx((2.0, 2.0, 2.0))
        tools.append(tool)

    read_step = Mock(wraps=occ_export_builder._read_step_shape)
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", read_step)
    result = boolean_many("cut", [base, *tools])

    read_step.assert_called_once_with(str(step_path))
    distance = vtk.vtkImplicitPolyDataDistance()
    distance.SetInput(result)
    expected_volume = 20.0 ** 3 - 4.0 * math.pi * 20.0
    assert _mesh_volume(result) == pytest.approx(expected_volume, abs=0.5)
    assert distance.EvaluateFunction((10, 10, 10)) < 0.0
    for x, y in ((4, 4), (16, 4), (4, 16), (16, 16)):
        assert distance.EvaluateFunction((x, y, 10)) > 0.0


@pytest.mark.parametrize("operation", ["fuse", "cut", "common"])
def test_boolean_many_applies_requested_reduction(operation, monkeypatch):
    source = _sphere(12)
    result = vtk.vtkPolyData()
    decimator = Mock(return_value=result)
    monkeypatch.setattr("em3d_modeler.scene.boolean_ops._occ_boolean", lambda op, objects: source)
    monkeypatch.setattr("em3d_modeler.scene.boolean_ops._postprocess", lambda poly: poly)
    monkeypatch.setattr("em3d_modeler.scene.boolean_ops._decimate_polydata", decimator)

    actual = boolean_many(operation, [object(), object()], target_reduction=0.35)

    assert actual is result
    decimator.assert_called_once_with(source, 0.35)


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