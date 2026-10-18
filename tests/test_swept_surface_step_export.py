import math

import pytest

from em3d_modeler.emerge.step_bundle_exporter import (
    _count_open_or_nonmanifold_edges,
    _repair_polydata_for_step,
)
from em3d_modeler.emerge.occ_export_builder import build_occ_swept_surface_shape
from em3d_modeler.emerge import occ_export_builder, step_bundle_exporter
from em3d_modeler.scene.em_objects import (
    BoxObject,
    MeshObject,
    PlateObject,
    SweptSurfaceObject,
)


def test_swept_surface_step_repair_preserves_open_rims():
    surface = SweptSurfaceObject(
        profile_pts=[
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (-1.0, 0.0, 0.0),
            (0.0, -1.0, 0.0),
        ],
        displacement=(0.0, 0.0, 2.0),
        closed=True,
    )
    polydata = surface.actor.GetMapper().GetInput()

    open_surface, open_stats = _repair_polydata_for_step(
        polydata,
        preserve_open=True,
    )
    capped_surface, capped_stats = _repair_polydata_for_step(polydata)

    assert open_stats["in_open_edges"] > 0
    assert open_stats["out_open_edges"] > 0
    assert _count_open_or_nonmanifold_edges(open_surface) > 0
    assert capped_stats["out_open_edges"] == 0
    assert _count_open_or_nonmanifold_edges(capped_surface) == 0


def test_topods_cast_supports_static_suffixed_bindings():
    class StaticTopods:
        Solid_s = object()

    class LegacyTopods:
        Solid = object()

    assert occ_export_builder._topods_cast(StaticTopods, "Solid") is StaticTopods.Solid_s
    assert occ_export_builder._topods_cast(LegacyTopods, "Solid") is LegacyTopods.Solid


def test_exact_closed_wire_extrusion_is_valid_open_shell_with_coincident_rim():
    pytest.importorskip("OCP")
    from OCP.BRep import BRep_Tool
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    profile = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    source_polygon = BRepBuilderAPI_MakePolygon()
    for point in profile:
        source_polygon.Add(gp_Pnt(*point))
    source_polygon.Close()
    source_wire = source_polygon.Wire()
    source_face = BRepBuilderAPI_MakeFace(source_wire).Face()
    surface = SweptSurfaceObject(
        "ExactSweep",
        profile_pts=profile,
        displacement=(0.0, 0.0, 3.0),
        closed=True,
    )

    swept_shape = build_occ_swept_surface_shape(
        surface,
        [("Source", source_face)],
    )

    assert swept_shape is not None
    assert BRepCheck_Analyzer(swept_shape).IsValid()
    shells = TopExp_Explorer(swept_shape, TopAbs_SHELL)
    assert shells.More()
    cast_shell = occ_export_builder._topods_cast(TopoDS, "Shell")
    shell = cast_shell(shells.Current())
    assert BRep_Tool.IsClosed_s(shell) is False
    solids = TopExp_Explorer(swept_shape, TopAbs_SOLID)
    assert not solids.More()

    cast_edge = occ_export_builder._topods_cast(TopoDS, "Edge")
    source_edges = []
    edge_explorer = TopExp_Explorer(source_wire, TopAbs_EDGE)
    while edge_explorer.More():
        source_edges.append(cast_edge(edge_explorer.Current()))
        edge_explorer.Next()
    swept_edges = []
    edge_explorer = TopExp_Explorer(swept_shape, TopAbs_EDGE)
    while edge_explorer.More():
        swept_edges.append(cast_edge(edge_explorer.Current()))
        edge_explorer.Next()
    assert len(source_edges) == 4
    for source_edge in source_edges:
        source_properties = GProp_GProps()
        BRepGProp.LinearProperties_s(source_edge, source_properties)
        source_length = source_properties.Mass()
        has_coincident_copy = False
        for swept_edge in swept_edges:
            swept_properties = GProp_GProps()
            BRepGProp.LinearProperties_s(swept_edge, swept_properties)
            if abs(swept_properties.Mass() - source_length) > 1e-9:
                continue
            edge_coincidence = BRepExtrema_DistShapeShape(source_edge, swept_edge)
            edge_coincidence.Perform()
            if edge_coincidence.IsDone() and edge_coincidence.Value() <= 1e-9:
                has_coincident_copy = True
                break
        assert has_coincident_copy

    coincidence = BRepExtrema_DistShapeShape(source_wire, swept_shape)
    coincidence.Perform()
    assert coincidence.IsDone()
    assert coincidence.Value() == pytest.approx(0.0, abs=1e-9)


def test_exact_open_profile_exports_only_uncapped_lateral_faces():
    pytest.importorskip("OCP")
    from OCP.BRep import BRep_Tool
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    sheet = SweptSurfaceObject(
        "OpenProfile",
        profile_pts=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)],
        displacement=(0.0, 0.0, 3.0),
        closed=False,
    )
    metadata = {}

    shape = build_occ_swept_surface_shape(
        sheet,
        [],
        connection_metadata=metadata,
    )

    assert BRepCheck_Analyzer(shape).IsValid()
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    face_count = 0
    while faces.More():
        face_count += 1
        faces.Next()
    assert face_count == 2  # One lateral face per open profile segment.
    solids = TopExp_Explorer(shape, TopAbs_SOLID)
    assert not solids.More()
    shells = TopExp_Explorer(shape, TopAbs_SHELL)
    assert shells.More()
    cast_shell = occ_export_builder._topods_cast(TopoDS, "Shell")
    assert BRep_Tool.IsClosed_s(cast_shell(shells.Current())) is False
    assert metadata["repair_method"] == "direct_open_profile"
    assert metadata["profile_perimeter_mm"] == pytest.approx(3.0)
    assert metadata["extrusion_length_mm"] == pytest.approx(3.0)


def test_exact_step_circle_exports_analytic_coincident_open_sheet(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Circle
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    center = (2.0, -3.0, 5.0)
    radius = 1.25
    profile_curve = {
        "kind": "circle",
        "center": list(center),
        "radius": radius,
        "normal": [0.0, 0.0, 1.0],
        "x_axis": [1.0, 0.0, 0.0],
    }
    source_circle = gp_Circ(
        gp_Ax2(gp_Pnt(*center), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0)),
        radius,
    )
    source_edge = BRepBuilderAPI_MakeEdge(source_circle).Edge()
    profile = SweptSurfaceObject.sample_profile_curve(profile_curve, 48)
    sheet = SweptSurfaceObject(
        "ExactStepCircle",
        profile_pts=profile,
        displacement=(0.0, 0.0, 4.0),
        closed=True,
        profile_curve=profile_curve,
    )
    metadata = {}

    shape = build_occ_swept_surface_shape(sheet, [], connection_metadata=metadata)

    assert BRepCheck_Analyzer(shape).IsValid()
    assert metadata["repair_method"] == "exact_step_circle"
    assert metadata["profile_perimeter_mm"] == pytest.approx(2.0 * math.pi * radius)
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    assert faces.More()
    solids = TopExp_Explorer(shape, TopAbs_SOLID)
    assert not solids.More()
    shells = TopExp_Explorer(shape, TopAbs_SHELL)
    assert shells.More()
    cast_shell = occ_export_builder._topods_cast(TopoDS, "Shell")
    assert BRep_Tool.IsClosed_s(cast_shell(shells.Current())) is False

    sheet_edges = TopExp_Explorer(shape, TopAbs_EDGE)
    found_exact_base = False
    cast_edge = occ_export_builder._topods_cast(TopoDS, "Edge")
    while sheet_edges.More():
        edge = cast_edge(sheet_edges.Current())
        sheet_edges.Next()
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            continue
        circle = curve.Circle()
        if (
            math.dist(
                (circle.Location().X(), circle.Location().Y(), circle.Location().Z()),
                center,
            )
            > 1e-9
            or abs(circle.Radius() - radius) > 1e-9
        ):
            continue
        coincidence = BRepExtrema_DistShapeShape(source_edge, edge)
        coincidence.Perform()
        if coincidence.IsDone() and coincidence.Value() <= 1e-9:
            properties = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, properties)
            assert properties.Mass() == pytest.approx(2.0 * math.pi * radius)
            found_exact_base = True
            break
    assert found_exact_base

    bundle = step_bundle_exporter.export_objects_to_step_bundle(
        [sheet],
        tmp_path / "exact_circle_bundle",
    )
    assert len(bundle["entries"]) == 1
    step_path = tmp_path / "exact_circle_bundle" / bundle["entries"][0]["step_file"]
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    assert reader.ReadFile(str(step_path)) == IFSelect_RetDone
    assert reader.TransferRoots() > 0
    imported_shape = reader.OneShape()
    imported_edges = TopExp_Explorer(imported_shape, TopAbs_EDGE)
    found_imported_circle = False
    while imported_edges.More():
        edge = cast_edge(imported_edges.Current())
        imported_edges.Next()
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            continue
        circle = curve.Circle()
        if (
            math.dist(
                (circle.Location().X(), circle.Location().Y(), circle.Location().Z()),
                center,
            )
            <= 1e-7
            and abs(circle.Radius() - radius) <= 1e-7
        ):
            found_imported_circle = True
            break
    assert found_imported_circle
    imported_solids = TopExp_Explorer(imported_shape, TopAbs_SOLID)
    assert not imported_solids.More()


def test_step_mesh_pick_resolves_to_unique_exact_brep_circle(monkeypatch):
    pytest.importorskip("OCP")
    import vtk

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    center = (1.0, 2.0, 3.0)
    radius = 2.0
    source = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*center), gp_Dir(0.0, 0.0, 1.0)),
        radius,
        4.0,
    ).Shape()
    points = [
        (
            center[0] + radius * math.cos(2.0 * math.pi * index / 48),
            center[1] + radius * math.sin(2.0 * math.pi * index / 48),
            center[2],
        )
        for index in range(48)
    ]
    vtk_points = vtk.vtkPoints()
    for point in points:
        vtk_points.InsertNextPoint(*point)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(vtk_points)
    mesh = MeshObject(
        "STEP_Solid_1",
        polydata,
        step_source_path="in-memory-source.step",
        step_solid_name="STEP_Solid_1",
    )
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: source)

    profile_curve = occ_export_builder.resolve_exact_step_circle_profile(mesh, points)

    assert profile_curve["kind"] == "circle"
    assert profile_curve["center"] == pytest.approx(center)
    assert profile_curve["radius"] == pytest.approx(radius)
    assert abs(profile_curve["normal"][2]) == pytest.approx(1.0)


def _step_mesh_for_face_intersection():
    import vtk

    points = vtk.vtkPoints()
    points.InsertNextPoint(0.0, 0.0, 0.0)
    points.InsertNextPoint(1.0, 0.0, 0.0)
    points.InsertNextPoint(0.0, 1.0, 0.0)
    triangle = vtk.vtkTriangle()
    triangle.GetPointIds().SetId(0, 0)
    triangle.GetPointIds().SetId(1, 1)
    triangle.GetPointIds().SetId(2, 2)
    cells = vtk.vtkCellArray()
    cells.InsertNextCell(triangle)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return MeshObject(
        "STEP_Solid_1",
        polydata,
        step_source_path="in-memory-source.step",
        step_solid_name="STEP_Solid_1",
    )


def test_step_face_line_intersection_returns_signed_forward_and_backward_hits(
    monkeypatch,
):
    pytest.importorskip("OCP")
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    face = BRepBuilderAPI_MakeFace(
        gp_Pln(gp_Pnt(0.0, 0.0, 2.0), gp_Dir(0.0, 0.0, 1.0)),
        0.0,
        1.0,
        0.0,
        1.0,
    ).Face()
    mesh = _step_mesh_for_face_intersection()
    mesh.actor.SetPosition(3.0, -2.0, 5.0)
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: face)

    forward = occ_export_builder.intersect_step_mesh_face_line(
        mesh, 1, (3.5, -1.5, 0.0), (0.0, 0.0, 2.0)
    )
    backward = occ_export_builder.intersect_step_mesh_face_line(
        mesh, 1, (3.5, -1.5, 10.0), (0.0, 0.0, 2.0)
    )

    assert len(forward) == 1
    assert forward[0][0] == pytest.approx(3.5)
    assert forward[0][1] == pytest.approx((3.5, -1.5, 7.0))
    assert len(backward) == 1
    assert backward[0][0] == pytest.approx(-1.5)
    assert backward[0][1] == pytest.approx((3.5, -1.5, 7.0))
    with pytest.raises(ValueError, match="outside the STEP BREP face range"):
        occ_export_builder.intersect_step_mesh_face_line(
            mesh, 2, (3.5, -1.5, 0.0), (0.0, 0.0, 1.0)
        )


def test_step_curved_face_line_intersection_returns_all_sorted_hits(monkeypatch):
    pytest.importorskip("OCP")
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    shape = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)),
        1.0,
        2.0,
    ).Shape()
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    face_id = 0
    while explorer.More():
        face_id += 1
        face = TopoDS.Face_s(explorer.Current())
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder:
            break
        explorer.Next()
    else:
        pytest.fail("Cylinder BREP did not contain a cylindrical face")

    mesh = _step_mesh_for_face_intersection()
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: shape)

    hits = occ_export_builder.intersect_step_mesh_face_line(
        mesh, face_id, (-2.0, 0.0, 1.0), (2.0, 0.0, 0.0)
    )

    assert [hit[0] for hit in hits] == pytest.approx([0.5, 1.5])
    assert [hit[1] for hit in hits] == pytest.approx(
        [(-1.0, 0.0, 1.0), (1.0, 0.0, 1.0)]
    )


def test_step_face_line_intersection_rejects_invalid_face_ids_and_lines():
    mesh = _step_mesh_for_face_intersection()

    with pytest.raises(ValueError, match="OCCFaceId"):
        occ_export_builder.intersect_step_mesh_face_line(
            mesh, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
        )
    with pytest.raises(ValueError, match="Line origin"):
        occ_export_builder.intersect_step_mesh_face_line(
            mesh, 1, (0.0, float("nan"), 0.0), (0.0, 0.0, 1.0)
        )
    with pytest.raises(ValueError, match="Line direction"):
        occ_export_builder.intersect_step_mesh_face_line(
            mesh, 1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        )


def test_exact_circle_resolution_ignores_mesh_bounds_within_tessellation_deflection(
    monkeypatch,
):
    pytest.importorskip("OCP")
    import vtk

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    center = (1.0, 2.0, 3.0)
    radius = 2.0
    source = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*center), gp_Dir(0.0, 0.0, 1.0)),
        radius,
        4.0,
    ).Shape()
    profile_points = []
    mesh_points = vtk.vtkPoints()
    for z_value in (center[2], center[2] + 4.0):
        for index in range(35):
            angle = 2.0 * math.pi * index / 35 + 0.03
            point = (
                center[0] + radius * math.cos(angle),
                center[1] + radius * math.sin(angle),
                z_value,
            )
            mesh_points.InsertNextPoint(*point)
            if z_value == center[2]:
                profile_points.append(point)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(mesh_points)
    mesh = MeshObject(
        "STEP_Solid_1",
        polydata,
        step_source_path="in-memory-source.step",
        step_solid_name="STEP_Solid_1",
    )
    mesh.step_geometry_modified = True
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: source)

    profile_curve = occ_export_builder.resolve_exact_step_circle_profile(
        mesh, profile_points
    )

    assert profile_curve["center"] == pytest.approx(center)
    assert profile_curve["radius"] == pytest.approx(radius)


def test_exact_circle_resolution_accepts_split_arcs_and_disambiguates_nearby_rims(
    monkeypatch,
):
    pytest.importorskip("OCP")
    import vtk

    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    center = (1.0, 2.0, 3.0)
    radius = 2.0
    source = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(source)
    for circle_radius in (radius, radius + 0.04, radius + 0.08):
        circle = gp_Circ(
            gp_Ax2(gp_Pnt(*center), gp_Dir(0.0, 0.0, 1.0)),
            circle_radius,
        )
        first_arc = BRepBuilderAPI_MakeEdge(circle, 0.0, math.pi).Edge()
        second_arc = BRepBuilderAPI_MakeEdge(
            circle, math.pi, 2.0 * math.pi
        ).Edge()
        builder.Add(source, first_arc)
        builder.Add(source, second_arc)
        if circle_radius == radius:
            builder.Add(source, first_arc)
            builder.Add(source, second_arc)

    profile_points = []
    mesh_points = vtk.vtkPoints()
    for index in range(35):
        angle = 2.0 * math.pi * index / 35 + 0.03
        point = (
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
            center[2],
        )
        mesh_points.InsertNextPoint(*point)
        profile_points.append(point)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(mesh_points)
    mesh = MeshObject(
        "STEP_Solid_1",
        polydata,
        step_source_path="in-memory-source.step",
        step_solid_name="STEP_Solid_1",
    )
    mesh.step_geometry_modified = True
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: source)

    profile_curve = occ_export_builder.resolve_exact_step_circle_profile(
        mesh, profile_points
    )

    assert profile_curve["center"] == pytest.approx(center)
    assert profile_curve["radius"] == pytest.approx(radius)


def test_exact_circle_resolution_rejects_duplicate_partial_step_arcs(monkeypatch):
    pytest.importorskip("OCP")
    import vtk

    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    center = (1.0, 2.0, 3.0)
    radius = 2.0
    circle = gp_Circ(gp_Ax2(gp_Pnt(*center), gp_Dir(0.0, 0.0, 1.0)), radius)
    half_arc = BRepBuilderAPI_MakeEdge(circle, 0.0, math.pi).Edge()
    source = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(source)
    builder.Add(source, half_arc)
    builder.Add(source, half_arc)

    points = [
        (
            center[0] + radius * math.cos(2.0 * math.pi * index / 48),
            center[1] + radius * math.sin(2.0 * math.pi * index / 48),
            center[2],
        )
        for index in range(48)
    ]
    mesh_points = vtk.vtkPoints()
    for point in points:
        mesh_points.InsertNextPoint(*point)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(mesh_points)
    mesh = MeshObject(
        "STEP_Solid_1",
        polydata,
        step_source_path="in-memory-source.step",
        step_solid_name="STEP_Solid_1",
    )
    monkeypatch.setattr(occ_export_builder, "_read_step_shape", lambda _path: source)

    with pytest.raises(ValueError, match="co-circular edge chain"):
        occ_export_builder.resolve_exact_step_circle_profile(mesh, points)


def test_open_profile_bundle_exports_brep_without_a_solid_or_caps(tmp_path):
    pytest.importorskip("OCP")
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    sheet = SweptSurfaceObject(
        "OpenProfileBundle",
        profile_pts=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)],
        displacement=(0.0, 0.0, 3.0),
        closed=False,
    )

    bundle = step_bundle_exporter.export_objects_to_step_bundle(
        [sheet],
        tmp_path / "open_profile_bundle",
    )

    assert len(bundle["entries"]) == 1
    entry = bundle["entries"][0]
    assert entry["object_type"] == "SweptSurfaceObject"
    assert entry["export_mode"] == "brep_direct"
    assert entry["solid_count"] == 0
    assert entry["swept_profile_perimeter_mm"] == pytest.approx(3.0)
    assert entry["swept_extrusion_length_mm"] == pytest.approx(3.0)
    step_path = tmp_path / "open_profile_bundle" / entry["step_file"]
    assert step_path.is_file()

    reader = STEPControl_Reader()
    assert reader.ReadFile(str(step_path)) == IFSelect_RetDone
    assert reader.TransferRoots() > 0
    imported_shape = reader.OneShape()
    imported_faces = TopExp_Explorer(imported_shape, TopAbs_FACE)
    face_count = 0
    while imported_faces.More():
        face_count += 1
        imported_faces.Next()
    assert face_count == 2
    imported_solids = TopExp_Explorer(imported_shape, TopAbs_SOLID)
    assert not imported_solids.More()


def test_patterned_swept_surface_matches_transformed_source_wire():
    pytest.importorskip("OCP")
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    profile = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]

    def make_face(points):
        polygon = BRepBuilderAPI_MakePolygon()
        for point in points:
            polygon.Add(gp_Pnt(*point))
        polygon.Close()
        wire = polygon.Wire()
        return wire, BRepBuilderAPI_MakeFace(wire).Face()

    base_wire, base_face = make_face(profile)
    pattern_profile = [(x + 5.0, y, z) for x, y, z in profile]
    pattern_wire, pattern_face = make_face(pattern_profile)
    surface = SweptSurfaceObject(
        "PatternedSweep",
        profile_pts=profile,
        displacement=(0.0, 0.0, 3.0),
        closed=True,
    )
    surface.actor.SetPosition(5.0, 0.0, 0.0)

    swept_shape = build_occ_swept_surface_shape(
        surface,
        [("BasePogo", base_face), ("PatternedPogo", pattern_face)],
    )

    assert swept_shape is not None
    pattern_coincidence = BRepExtrema_DistShapeShape(pattern_wire, swept_shape)
    pattern_coincidence.Perform()
    assert pattern_coincidence.IsDone()
    assert pattern_coincidence.Value() == pytest.approx(0.0, abs=1e-9)
    base_coincidence = BRepExtrema_DistShapeShape(base_wire, swept_shape)
    base_coincidence.Perform()
    assert base_coincidence.IsDone()
    assert base_coincidence.Value() > 1.0


def test_swept_surface_overlaps_matching_solid_instead_of_only_touching_at_rim():
    pytest.importorskip("OCP")
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt

    profile = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    polygon = BRepBuilderAPI_MakePolygon()
    for point in profile:
        polygon.Add(gp_Pnt(*point))
    polygon.Close()
    solid = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, -2.0), 2.0, 1.0, 2.0).Shape()
    surface = SweptSurfaceObject(
        "ConnectedSweep",
        profile_pts=profile,
        displacement=(0.0, 0.0, 3.0),
        closed=True,
    )

    swept_shape = build_occ_swept_surface_shape(
        surface,
        [("Pogo", solid)],
    )

    assert swept_shape is not None
    common = BRepAlgoAPI_Common(swept_shape, solid)
    common.Build()
    assert common.IsDone()
    common_properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(common.Shape(), common_properties)
    assert common_properties.Mass() > 0.0
