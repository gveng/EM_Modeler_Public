"""STEP file importer.

Tries the following backends in order:
  1. OCP            – modern OpenCASCADE Python bindings (bundled with cadquery)
  2. pythonOCC-core – classic OCC.Core.* bindings (conda only on Windows)
  3. cadquery       – fallback via STL round-trip

Usage
-----
    from em3d_modeler.emerge.step_importer import import_step
    solids = import_step("part.step")
    # solids: List[dict]  with keys "name", "polydata" (vtkPolyData)
"""
from __future__ import annotations
from typing import List, Dict, Any
from pathlib import Path

import vtk


def import_step(filepath: str) -> List[Dict[str, Any]]:
    """Import a STEP file and return a list of solid dicts.

    Each dict has:
      - "name"     : str          – auto-generated solid name
      - "polydata" : vtkPolyData

    Raises
    ------
    RuntimeError if no STEP-capable library is found.
    FileNotFoundError if the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"STEP file not found: {filepath}")

    ext = path.suffix.lower()
    if ext not in (".step", ".stp"):
        raise ValueError(f"Unsupported file extension: {ext}")

    last_err: Exception | None = None

    # ── 1. Try OCP (modern, ships with cadquery) ──────────────────────────────
    try:
        return _import_via_ocp(filepath)
    except ImportError as e:
        last_err = e

    # ── 2. Try pythonOCC-core (classic) ───────────────────────────────────────
    try:
        return _import_via_occ(filepath)
    except ImportError as e:
        last_err = e

    # ── 3. Try cadquery STL round-trip ────────────────────────────────────────
    try:
        return _import_via_cadquery(filepath)
    except ImportError as e:
        last_err = e

    raise RuntimeError(
        "No STEP library found.\n\n"
        "Install one of:\n"
        "    pip install cadquery       (recommended, ships with OCP)\n"
        "    conda install -c conda-forge pythonocc-core\n\n"
        f"Last error: {last_err}"
    )


# ──────────────────────────────────────────────── OCP backend ───────────────
def _import_via_ocp(filepath: str) -> List[Dict[str, Any]]:
    """Use the OCP package (CadQuery's OpenCASCADE bindings)."""
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopExp      import TopExp_Explorer
    from OCP.TopAbs      import TopAbs_SOLID
    from OCP.BRepMesh    import BRepMesh_IncrementalMesh

    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    # IFSelect_RetDone == 1
    if int(status) != 1:
        raise RuntimeError(f"STEPControl_Reader failed (status={status})")
    reader.TransferRoots()

    results: List[Dict[str, Any]] = []
    n_shapes = reader.NbShapes()

    for i in range(1, n_shapes + 1):
        shape = reader.Shape(i)

        # Tessellate (linear deflection 0.1, angular 0.5 rad)
        BRepMesh_IncrementalMesh(shape, 0.1, False, 0.5, True)

        # Iterate over solids inside this shape – each becomes a separate body
        solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        idx = 0
        while solid_explorer.More():
            idx += 1
            solid = solid_explorer.Current()
            poly = _ocp_shape_to_vtk(solid)
            if poly.GetNumberOfPoints() > 0:
                name = (
                    f"STEP_Solid_{i}_{idx}" if n_shapes > 1
                    else f"STEP_Solid_{idx}"
                )
                results.append({"name": name, "polydata": poly})
            solid_explorer.Next()

        # If no solid was found, mesh the whole shape as faces
        if idx == 0:
            poly = _ocp_shape_to_vtk(shape)
            if poly.GetNumberOfPoints() > 0:
                results.append({
                    "name":     f"STEP_Shape_{i}",
                    "polydata": poly,
                })

    if not results:
        raise RuntimeError("STEP file contained no usable geometry.")
    return results


def _ocp_shape_to_vtk(shape) -> vtk.vtkPolyData:
    """Convert an OCP/OCC tessellated shape into a vtkPolyData."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.BRep   import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    vtk_pts   = vtk.vtkPoints()
    vtk_cells = vtk.vtkCellArray()
    pt_offset = 0

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc  = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, loc)
        if triangulation is None:
            exp.Next()
            continue

        trsf = loc.Transformation()
        n_nodes = triangulation.NbNodes()
        n_tris  = triangulation.NbTriangles()

        # Insert nodes (1-indexed in OCC)
        for j in range(1, n_nodes + 1):
            pnt = triangulation.Node(j).Transformed(trsf)
            vtk_pts.InsertNextPoint(pnt.X(), pnt.Y(), pnt.Z())

        # Insert triangles
        for k in range(1, n_tris + 1):
            tri = triangulation.Triangle(k)
            n1, n2, n3 = tri.Get()
            tri_cell = vtk.vtkTriangle()
            tri_cell.GetPointIds().SetId(0, pt_offset + n1 - 1)
            tri_cell.GetPointIds().SetId(1, pt_offset + n2 - 1)
            tri_cell.GetPointIds().SetId(2, pt_offset + n3 - 1)
            vtk_cells.InsertNextCell(tri_cell)

        pt_offset += n_nodes
        exp.Next()

    poly = vtk.vtkPolyData()
    poly.SetPoints(vtk_pts)
    poly.SetPolys(vtk_cells)

    # Compute smooth normals for nicer rendering
    if poly.GetNumberOfPolys() > 0:
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.SplittingOff()
        normals.ConsistencyOn()
        normals.AutoOrientNormalsOn()
        normals.Update()
        out = vtk.vtkPolyData()
        out.DeepCopy(normals.GetOutput())
        return out

    return poly


# ──────────────────────────────────────────────── pythonOCC backend ─────────
def _import_via_occ(filepath: str) -> List[Dict[str, Any]]:
    """Use classic pythonOCC-core (OCC.Core.*)."""
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.IFSelect    import IFSelect_RetDone
    from OCC.Core.BRepMesh    import BRepMesh_IncrementalMesh

    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != IFSelect_RetDone:
        raise RuntimeError("STEPControl_Reader failed to read the file.")
    reader.TransferRoots()

    results = []
    n_shapes = reader.NbShapes()
    for i in range(1, n_shapes + 1):
        shape = reader.Shape(i)
        BRepMesh_IncrementalMesh(shape, 0.1, False, 0.5)
        polydata = _occ_shape_to_vtk(shape)
        if polydata.GetNumberOfPoints() > 0:
            results.append({
                "name":     f"STEP_Solid_{i}",
                "polydata": polydata,
            })
    if not results:
        raise RuntimeError("STEP file contained no usable geometry.")
    return results


def _occ_shape_to_vtk(shape) -> vtk.vtkPolyData:
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.BRep   import BRep_Tool
    from OCC.Core.TopLoc import TopLoc_Location

    vtk_pts   = vtk.vtkPoints()
    vtk_cells = vtk.vtkCellArray()
    pt_offset = 0

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = exp.Current()
        loc  = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)
        if triangulation is None:
            exp.Next()
            continue

        trsf = loc.Transformation()
        n_nodes = triangulation.NbNodes()
        n_tris  = triangulation.NbTriangles()

        for j in range(1, n_nodes + 1):
            pnt = triangulation.Node(j).Transformed(trsf)
            vtk_pts.InsertNextPoint(pnt.X(), pnt.Y(), pnt.Z())

        for k in range(1, n_tris + 1):
            tri = triangulation.Triangle(k)
            n1, n2, n3 = tri.Get()
            tri_cell = vtk.vtkTriangle()
            tri_cell.GetPointIds().SetId(0, pt_offset + n1 - 1)
            tri_cell.GetPointIds().SetId(1, pt_offset + n2 - 1)
            tri_cell.GetPointIds().SetId(2, pt_offset + n3 - 1)
            vtk_cells.InsertNextCell(tri_cell)

        pt_offset += n_nodes
        exp.Next()

    poly = vtk.vtkPolyData()
    poly.SetPoints(vtk_pts)
    poly.SetPolys(vtk_cells)
    return poly


# ──────────────────────────────────────────────── cadquery backend ──────────
def _import_via_cadquery(filepath: str) -> List[Dict[str, Any]]:
    """Use cadquery's STEP importer + STL round-trip."""
    import cadquery as cq
    import os, tempfile

    result = cq.importers.importStep(filepath)
    solids = result.vals()
    out = []
    for i, solid in enumerate(solids):
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cq.exporters.export(cq.Workplane().add(solid), tmp_path,
                                cq.exporters.ExportTypes.STL)
            reader = vtk.vtkSTLReader()
            reader.SetFileName(tmp_path)
            reader.Update()
            polydata = vtk.vtkPolyData()
            polydata.DeepCopy(reader.GetOutput())
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        out.append({"name": f"STEP_Solid_{i+1}", "polydata": polydata})
    return out
