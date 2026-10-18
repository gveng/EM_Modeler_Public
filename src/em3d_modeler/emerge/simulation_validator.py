# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Preflight validation for EMERGE simulation projects."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Callable, Iterable

import vtk


_BREP_CONNECTION_TOLERANCE_MM = 1e-4
_MINIMUM_SWEPT_SURFACE_EMERGE_API_VERSION = (3, 0, 0)


def _supports_swept_surface_emerge_api(
    import_status: str | None,
    version: str | None,
) -> bool:
    """Fail closed unless the runtime meets the STEP surface import API floor."""
    if import_status != "Imported successfully" or version is None:
        return False
    match = re.fullmatch(
        r"\s*[vV]?(\d+)\.(\d+)\.(\d+)"
        r"(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?"
        r"(?:\+[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?\s*",
        str(version),
        re.IGNORECASE,
    )
    return bool(
        match
        and tuple(int(part) for part in match.groups())
        >= _MINIMUM_SWEPT_SURFACE_EMERGE_API_VERSION
    )


@dataclass(frozen=True)
class ValidationFinding:
    severity: str
    category: str
    message: str


def _world_polydata(obj: Any) -> vtk.vtkPolyData | None:
    actor = getattr(obj, "actor", None)
    mapper = actor.GetMapper() if actor is not None else None
    source = mapper.GetInput() if mapper is not None else None
    if source is None or source.GetNumberOfPoints() <= 0:
        return None

    transform = vtk.vtkTransform()
    transform.SetMatrix(actor.GetMatrix())
    transformed = vtk.vtkTransformPolyDataFilter()
    transformed.SetTransform(transform)
    transformed.SetInputData(source)
    transformed.Update()

    triangles = vtk.vtkTriangleFilter()
    triangles.SetInputData(transformed.GetOutput())
    triangles.Update()
    result = vtk.vtkPolyData()
    result.DeepCopy(triangles.GetOutput())
    return result


def _open_or_nonmanifold_edge_count(poly: vtk.vtkPolyData) -> int:
    if poly.GetNumberOfPoints() <= 0:
        return 0
    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(poly)
    clean.Update()
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(clean.GetOutput())
    edges.BoundaryEdgesOn()
    edges.NonManifoldEdgesOn()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.Update()
    return int(edges.GetOutput().GetNumberOfCells())


def _closed_volume_bounds(poly: vtk.vtkPolyData) -> tuple[float, ...] | None:
    """Return bounds only for a closed, non-degenerate 3-D triangle mesh."""
    if poly.GetNumberOfPolys() < 4 or _open_or_nonmanifold_edge_count(poly) != 0:
        return None
    mass = vtk.vtkMassProperties()
    mass.SetInputData(poly)
    mass.Update()
    volume = abs(float(mass.GetVolume()))
    bounds = tuple(float(value) for value in poly.GetBounds())
    diagonal = math.sqrt(sum(
        (bounds[axis + 1] - bounds[axis]) ** 2 for axis in (0, 2, 4)
    ))
    if (
        not math.isfinite(volume)
        or volume <= max(diagonal ** 3 * 1e-12, 1e-18)
        or not all(math.isfinite(value) for value in bounds)
    ):
        return None
    return bounds


def _parametric_value_count(value: Any) -> int:
    values = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return max(1, len(values))


def _bounds_gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    squared = 0.0
    for axis in range(3):
        amin, amax = a[axis * 2], a[axis * 2 + 1]
        bmin, bmax = b[axis * 2], b[axis * 2 + 1]
        gap = max(0.0, bmin - amax, amin - bmax)
        squared += gap * gap
    return math.sqrt(squared)


def _minimum_surface_distance(a: vtk.vtkPolyData, b: vtk.vtkPolyData) -> float:
    minimum = float("inf")
    for source, target in ((a, b), (b, a)):
        distance = vtk.vtkImplicitPolyDataDistance()
        distance.SetInput(target)
        points = source.GetPoints()
        if points is None:
            continue
        for index in range(points.GetNumberOfPoints()):
            value = abs(float(distance.EvaluateFunction(points.GetPoint(index))))
            minimum = min(minimum, value)
    return minimum


def _surfaces_intersect(a: vtk.vtkPolyData, b: vtk.vtkPolyData) -> bool:
    intersection = vtk.vtkIntersectionPolyDataFilter()
    intersection.SetInputData(0, a)
    intersection.SetInputData(1, b)
    intersection.Update()
    return intersection.GetOutput(0).GetNumberOfCells() > 0


def _touches(a: vtk.vtkPolyData, b: vtk.vtkPolyData, tolerance: float) -> bool:
    bounds_a = a.GetBounds()
    bounds_b = b.GetBounds()
    if bounds_a is None or bounds_b is None:
        return False
    if _bounds_gap(bounds_a, bounds_b) > tolerance:
        return False
    if _minimum_surface_distance(a, b) <= tolerance:
        return True
    return _surfaces_intersect(a, b)


def _exact_brep_contact_measure(
    a: Any,
    b: Any,
    shape_cache: dict[str, Any],
    distance_tolerance: float | None = None,
) -> tuple[float, float | None] | None:
    from .occ_export_builder import build_occ_shape_for_export

    shape_a = build_occ_shape_for_export(a, shape_cache)
    shape_b = build_occ_shape_for_export(b, shape_cache)
    if shape_a is None or shape_b is None:
        return None

    distance = _occ_shape_distance(shape_a, shape_b)
    if distance is None:
        return None
    if distance > (
        _exact_contact_epsilon((a, b))
        if distance_tolerance is None
        else distance_tolerance
    ):
        return distance, None

    common_area = _occ_shape_common_area(shape_a, shape_b)
    if common_area is None:
        return None
    return distance, common_area


def _occ_shape_distance(shape_a: Any, shape_b: Any) -> float | None:
    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.BRepExtrema import BRepExtrema_DistShapeShape
                from OCP.BRep import BRep_Builder
                from OCP.TopAbs import TopAbs_FACE
                from OCP.TopExp import TopExp_Explorer
                from OCP.TopoDS import TopoDS_Compound
            else:
                from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
                from OCC.Core.BRep import BRep_Builder
                from OCC.Core.TopAbs import TopAbs_FACE
                from OCC.Core.TopExp import TopExp_Explorer
                from OCC.Core.TopoDS import TopoDS_Compound
        except ImportError:
            continue

        def surface_shape(shape: Any) -> Any:
            compound = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(compound)
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            face_count = 0
            while explorer.More():
                builder.Add(compound, explorer.Current())
                face_count += 1
                explorer.Next()
            return compound if face_count else shape

        distance = BRepExtrema_DistShapeShape(
            surface_shape(shape_a),
            surface_shape(shape_b),
        )
        distance.Perform()
        if not distance.IsDone():
            return None
        return float(distance.Value())
    return None


def _occ_shape_common_area(shape_a: Any, shape_b: Any) -> float | None:
    for package in ("OCP", "OCC.Core"):
        try:
            if package == "OCP":
                from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
                from OCP.BRepGProp import BRepGProp
                from OCP.GProp import GProp_GProps
            else:
                from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
                from OCC.Core.BRepGProp import BRepGProp
                from OCC.Core.GProp import GProp_GProps
        except ImportError:
            continue

        common = BRepAlgoAPI_Common(shape_a, shape_b)
        common.Build()
        if not common.IsDone():
            return None
        properties = GProp_GProps()
        if package == "OCP":
            BRepGProp.SurfaceProperties_s(common.Shape(), properties)
        else:
            BRepGProp.SurfaceProperties(common.Shape(), properties)
        return max(0.0, float(properties.Mass()))
    return None


def _occ_confusion_tolerance() -> float:
    try:
        from OCP.Precision import Precision
    except ImportError:
        try:
            from OCC.Core.Precision import Precision
        except ImportError:
            return 1e-7
        return float(Precision.Confusion())
    return float(Precision.Confusion_s())


def _exact_contact_epsilon(objects: Iterable[Any]) -> float:
    bounds = []
    for obj in objects:
        actor = getattr(obj, "actor", None)
        value = actor.GetBounds() if actor is not None else None
        if value is not None:
            bounds.append(value)
    confusion = _occ_confusion_tolerance()
    if not bounds:
        return max(confusion, 1e-9)
    diagonal = math.sqrt(sum(
        (
            max(float(item[axis * 2 + 1]) for item in bounds)
            - min(float(item[axis * 2]) for item in bounds)
        ) ** 2
        for axis in range(3)
    ))
    return max(diagonal * 1e-12, confusion, 1e-9)


def _brep_connection_tolerance(
    objects: Iterable[Any],
    mm_per_unit: float,
) -> float:
    if not math.isfinite(mm_per_unit) or mm_per_unit <= 0.0:
        raise ValueError("mm_per_unit must be a finite positive number")
    tolerance_in_model_units = _BREP_CONNECTION_TOLERANCE_MM / mm_per_unit
    return max(_exact_contact_epsilon(objects), tolerance_in_model_units)


def _is_conductor(obj: Any, material_catalog: dict[str, dict[str, Any]]) -> bool:
    material = str(getattr(obj, "material", "")).strip()
    upper = material.upper()
    if upper in {"PEC", "COPPER", "ALUMINUM", "GOLD", "SILVER"}:
        return True
    record = material_catalog.get(material, {})
    try:
        return float(record.get("sigma", 0.0)) > 0.0
    except (TypeError, ValueError):
        return False


def _model_tolerance(objects: Iterable[Any]) -> float:
    bounds = []
    for obj in objects:
        actor = getattr(obj, "actor", None)
        value = actor.GetBounds() if actor is not None else None
        if value is not None:
            bounds.append(value)
    if not bounds:
        return 1e-6
    overall = tuple(
        value
        for axis in range(3)
        for value in (
            min(float(item[axis * 2]) for item in bounds),
            max(float(item[axis * 2 + 1]) for item in bounds),
        )
    )
    diagonal = math.sqrt(sum((overall[axis * 2 + 1] - overall[axis * 2]) ** 2 for axis in range(3)))
    return max(diagonal * 1e-5, 1e-6)


def validate_simulation(
    objects: Iterable[Any],
    settings: dict[str, Any],
    material_catalog: dict[str, dict[str, Any]] | None = None,
    exported_ports: Iterable[dict[str, Any]] | None = None,
    object_priorities: dict[str, int] | None = None,
    mm_per_unit: float = 1.0,
    progress_callback: Callable[[str, float], None] | None = None,
    emerge_import_status: str | None = None,
    emerge_version: str | None = None,
) -> list[ValidationFinding]:
    """Validate project data and world-space port connectivity before export."""
    findings: list[ValidationFinding] = []

    last_progress = 0.0

    def report_progress(message: str, fraction: float) -> None:
        nonlocal last_progress
        if progress_callback is None:
            return
        last_progress = max(last_progress, min(1.0, max(0.0, fraction)))
        progress_callback(message, last_progress)

    report_progress("Reading simulation settings", 0.01)
    objects = list(objects)
    material_catalog = material_catalog or {}
    exported_ports = list(exported_ports or ())
    model_objects = [obj for obj in objects if bool(getattr(obj, "is_model", True))]
    object_by_name = {str(getattr(obj, "name", "")).strip(): obj for obj in objects}

    material_priorities = settings.get("material_priorities", {})
    material_priorities = material_priorities if isinstance(material_priorities, dict) else {}
    object_priorities = object_priorities if isinstance(object_priorities, dict) else {}
    used_materials = {
        str(getattr(obj, "material", "")).strip()
        for obj in model_objects
        if str(getattr(obj, "material", "")).strip()
    }
    missing_priorities = sorted({
        str(getattr(obj, "material", "")).strip()
        for obj in model_objects
        if str(getattr(obj, "material", "")).strip() not in material_priorities
        and str(getattr(obj, "name", "")).strip() not in object_priorities
    })
    if missing_priorities:
        findings.append(ValidationFinding(
            "WARNING",
            "Materials",
            "Material priorities are not set for: " + ", ".join(missing_priorities) + ".",
        ))
    elif used_materials:
        findings.append(ValidationFinding(
            "OK",
            "Materials",
            "Material priorities are set for all MODEL materials.",
        ))
    report_progress("Checking material priorities", 0.04)

    sheet_objects = [
        obj for obj in model_objects
        if type(obj).__name__ == "SweptSurfaceObject"
    ]
    if sheet_objects and not _supports_swept_surface_emerge_api(
        emerge_import_status,
        emerge_version,
    ):
        runtime_detail = (
            f"import status: {emerge_import_status or 'unknown'}, "
            f"version: {emerge_version or 'unknown'}"
        )
        findings.append(ValidationFinding(
            "ERROR",
            "Runtime",
            "Swept surface simulation requires an importable EMERGE 3.0.0 or "
            f"later STEP surface API; refusing to prepare this model ({runtime_detail}).",
        ))
    volume_objects = [
        obj for obj in model_objects
        if type(obj).__name__ not in {"PlateObject", "SweptSurfaceObject"}
        and not bool(getattr(obj, "plate_role", False))
    ]
    if sheet_objects:
        host_volume_bounds = []
        for obj in volume_objects:
            poly = _world_polydata(obj)
            if poly is None:
                continue
            bounds = _closed_volume_bounds(poly)
            if bounds is not None:
                host_volume_bounds.append(bounds)
        if not host_volume_bounds:
            findings.append(ValidationFinding(
                "ERROR",
                "Geometry",
                "Swept sheet surfaces require at least one closed, non-degenerate "
                "3D host volume for EMERGE meshing; a surface mesh or sheet alone "
                "is not a host volume.",
            ))
        else:
            findings.append(ValidationFinding(
                "OK",
                "Geometry",
                f"{len(host_volume_bounds)} closed 3D host volume(s) available for sheet meshing.",
            ))
        for sheet in sheet_objects:
            poly = _world_polydata(sheet)
            if poly is None:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Geometry",
                    f"{getattr(sheet, 'name', 'Open sheet')}: swept surface geometry is empty or unavailable.",
                ))
                continue
            sheet_bounds = tuple(float(value) for value in poly.GetBounds())
            span = max(
                1e-6,
                *(sheet_bounds[axis + 1] - sheet_bounds[axis] for axis in (0, 2, 4)),
            )
            tolerance = max(1e-9, span * 1e-8)
            if not any(
                all(
                    sheet_bounds[axis] >= bounds[axis] - tolerance
                    and sheet_bounds[axis + 1] <= bounds[axis + 1] + tolerance
                    for axis in (0, 2, 4)
                )
                for bounds in host_volume_bounds
            ):
                findings.append(ValidationFinding(
                    "ERROR",
                    "Geometry",
                    f"{getattr(sheet, 'name', 'Open sheet')}: sheet bounds must be contained within a closed 3D volume (such as the AIR region) for EMERGE meshing.",
                ))
    elif not volume_objects:
        findings.append(ValidationFinding("ERROR", "Geometry", "No 3D MODEL objects are available for simulation."))
    else:
        findings.append(ValidationFinding("OK", "Geometry", f"{len(volume_objects)} MODEL geometry object(s) available."))

    report_progress("Checking simulation geometry", 0.06)
    for object_index, obj in enumerate(model_objects, start=1):
        object_name = str(getattr(obj, "name", "")).strip() or type(obj).__name__
        object_fraction = 0.06 + 0.34 * object_index / max(1, len(model_objects))
        report_progress(
            f"Validating geometry and material: {object_name}",
            object_fraction,
        )
        name = str(getattr(obj, "name", "")).strip() or type(obj).__name__
        material = str(getattr(obj, "material", "")).strip()
        poly = _world_polydata(obj)
        native_ipc_geometry = getattr(obj, "ipc2581_step_geometry", None)
        if native_ipc_geometry is not None and (
            not isinstance(native_ipc_geometry, dict)
            or native_ipc_geometry.get("schema") != "ipc2581-profile-v1"
            or not isinstance(native_ipc_geometry.get("solids"), list)
            or not native_ipc_geometry["solids"]
        ):
            findings.append(ValidationFinding(
                "ERROR", "Geometry",
                f"{name}: native IPC-2581 STEP profile data is missing or invalid.",
            ))
        if poly is None:
            findings.append(ValidationFinding("ERROR", "Geometry", f"{name}: missing or empty geometry."))
        elif (
            native_ipc_geometry is None
            and type(obj).__name__ != "PlateObject"
            and type(obj).__name__ != "SweptSurfaceObject"
            and not bool(getattr(obj, "plate_role", False))
        ):
            cell_count = int(poly.GetNumberOfCells())
            if cell_count > 250_000:
                findings.append(ValidationFinding(
                    "WARNING", "Mesh",
                    f"{name}: input geometry has {cell_count:,} surface cells; STEP conversion and volume meshing may be slow or memory-intensive.",
                ))
            edge_count = _open_or_nonmanifold_edge_count(poly)
            if edge_count:
                findings.append(ValidationFinding(
                    "WARNING", "Mesh",
                    f"{name}: {edge_count:,} open or non-manifold surface edge(s); volume meshing may fail or produce an invalid mesh. Repair/close the solid before running.",
                ))
        if not material:
            findings.append(ValidationFinding("ERROR", "Materials", f"{name}: no material assigned."))
        elif material.upper() not in {"PEC", "PMC", "PML", "AIR", "COPPER"} and material not in material_catalog:
            findings.append(ValidationFinding("ERROR", "Materials", f"{name}: material '{material}' is not available in the export catalog."))
    report_progress("Geometry and material checks complete", 0.40)

    simulations = settings.get("simulations", [])
    enabled_simulations = [item for item in simulations if isinstance(item, dict) and bool(item.get("enabled", True))] if isinstance(simulations, list) else []
    if not enabled_simulations:
        findings.append(ValidationFinding("ERROR", "Simulation", "No enabled simulation job is configured."))
    estimated_frequency_solves = 0
    highest_configured_frequency = 0.0
    for simulation_index, simulation in enumerate(enabled_simulations, start=1):
        name = str(simulation.get("name", "Simulation"))
        report_progress(
            f"Validating simulation settings: {name}",
            0.40 + 0.16 * (simulation_index - 1) / max(1, len(enabled_simulations)),
        )
        try:
            fmin = float(simulation.get("Fmin_GHz", 0.0))
            fmax = float(simulation.get("Fmax_GHz", 0.0))
            points = int(simulation.get("NumberOfPoints", 0))
            if not math.isfinite(fmin) or not math.isfinite(fmax) or fmin <= 0.0 or fmax <= fmin or points < 2:
                raise ValueError
        except (OverflowError, TypeError, ValueError):
            findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: invalid frequency range or point count."))
            continue
        sim_type = str(simulation.get("type", "Sweep")).strip().lower()
        if sim_type not in {"sweep", "eigenmode", "parametric", "scatteredfield"}:
            findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: unsupported simulation type '{sim_type}'."))
            continue
        if bool(simulation.get("adaptive_mesh_refinement_enabled", False)):
            if sim_type != "sweep":
                findings.append(ValidationFinding(
                    "ERROR",
                    "Simulation",
                    f"{name}: adaptive mesh refinement is currently supported for Sweep simulations only.",
                ))
            try:
                refinement_frequency = float(
                    simulation.get("adaptive_mesh_refinement_frequency_GHz", 0.0)
                )
                if (
                    not math.isfinite(refinement_frequency)
                    or refinement_frequency <= 0.0
                    or not fmin <= refinement_frequency <= fmax
                ):
                    raise ValueError
            except (OverflowError, TypeError, ValueError):
                findings.append(ValidationFinding(
                    "ERROR",
                    "Simulation",
                    f"{name}: adaptive mesh refinement frequency must be positive and within the configured sweep range.",
                ))
            else:
                findings.append(ValidationFinding(
                    "WARNING",
                    "Runtime",
                    f"{name}: adaptive mesh refinement at {refinement_frequency:g} GHz adds solver passes and may increase runtime and mesh size.",
                ))
        if sim_type == "eigenmode":
            try:
                search_frequency = float(
                    simulation.get("EigenmodeSearchFrequency_GHz", 1.0)
                )
                if not math.isfinite(search_frequency) or search_frequency <= 0.0:
                    raise ValueError
            except (OverflowError, TypeError, ValueError):
                findings.append(ValidationFinding(
                    "ERROR",
                    "Simulation",
                    f"{name}: eigenmode search frequency must be a positive finite value in GHz.",
                ))
            else:
                highest_configured_frequency = max(
                    highest_configured_frequency, search_frequency
                )
        elif sim_type == "scatteredfield":
            highest_configured_frequency = max(highest_configured_frequency, fmax)
            estimated_frequency_solves += points
            if points > 500:
                findings.append(ValidationFinding(
                    "WARNING", "Runtime",
                    f"{name}: {points:,} scattered-field frequency points are configured; large sweeps can take a long time.",
                ))
            definition = str(
                simulation.get("ScatteredDefinition", "EA")
            ).strip().upper()
            if definition not in {"EA", "SPH"}:
                findings.append(ValidationFinding(
                    "ERROR", "Simulation",
                    f"{name}: angular definition must be EA or SPH.",
                ))
            try:
                angles = tuple(
                    float(simulation.get(key, default))
                    for key, default in (
                        ("ScatteredThetaDeg", 90.0),
                        ("ScatteredPhiDeg", 0.0),
                        ("ScatteredPsiDeg", 0.0),
                    )
                )
                power_density = float(
                    simulation.get("ScatteredPowerDensity_W_m2", 1.0)
                )
                if (
                    not all(math.isfinite(value) and abs(value) <= 360.0 for value in angles)
                    or not math.isfinite(power_density)
                    or power_density <= 0.0
                ):
                    raise ValueError
            except (OverflowError, TypeError, ValueError):
                findings.append(ValidationFinding(
                    "ERROR", "Simulation",
                    f"{name}: scattered-field angles must be finite within ±360° and power density must be positive and finite.",
                ))
            if str(simulation.get("ScatteredPhaseOrigin", "GCS")).strip().upper() != "GCS":
                findings.append(ValidationFinding(
                    "ERROR", "Simulation",
                    f"{name}: only the global coordinate system (GCS) phase origin is supported.",
                ))
        elif sim_type in {"sweep", "parametric"}:
            highest_configured_frequency = max(highest_configured_frequency, fmax)

        if sim_type in {"sweep", "parametric"}:
            value_count = _parametric_value_count(simulation.get("ParamValues", "")) if sim_type == "parametric" else 1
            job_samples = points * value_count
            estimated_frequency_solves += job_samples
            if points > 500:
                findings.append(ValidationFinding(
                    "WARNING", "Runtime",
                    f"{name}: {points:,} frequency points are configured; large sweeps can take a long time. Start with a coarser sweep and refine around resonances.",
                ))
            if sim_type == "parametric" and value_count > 20:
                findings.append(ValidationFinding(
                    "WARNING", "Runtime",
                    f"{name}: {value_count:,} parameter values multiply the sweep to about {job_samples:,} frequency solves.",
                ))
            if fmax / fmin > 100.0:
                findings.append(ValidationFinding(
                    "WARNING", "Simulation",
                    f"{name}: the sweep spans more than 100:1 in frequency; a uniform linear sweep may undersample high-frequency resonances.",
                ))
            fit_cfg = simulation.get("sparam_fitting", {})
            if isinstance(fit_cfg, dict) and bool(fit_cfg.get("enabled", False)):
                try:
                    fit_points = int(fit_cfg.get("points", 1001))
                except (OverflowError, TypeError, ValueError):
                    fit_points = 0
                if fit_points < 8:
                    findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: S-parameter fitting requires at least 8 points."))
                elif fit_points > 5_000:
                    findings.append(ValidationFinding(
                        "WARNING", "Runtime",
                        f"{name}: fitting uses {fit_points:,} points; fitting and result storage may use substantial memory.",
                    ))
        else:
            try:
                modes = int(simulation.get("EigenmodeCount", 5))
            except (OverflowError, TypeError, ValueError):
                modes = 0
            if modes < 1:
                findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: eigenmode count must be at least 1."))
            elif modes > 30:
                findings.append(ValidationFinding(
                    "WARNING", "Runtime",
                    f"{name}: {modes} eigenmodes requested; computing many modes can substantially increase solve time and memory use.",
                ))

    if estimated_frequency_solves > 1_000:
        findings.append(ValidationFinding(
            "WARNING", "Runtime",
            f"Enabled sweep jobs request about {estimated_frequency_solves:,} frequency solves in total (including parametric repetitions). Consider reducing points or splitting the study into smaller runs.",
        ))
    report_progress("Simulation settings checks complete", 0.57)

    mesh_cfg = settings.get("mesh", {}) if isinstance(settings.get("mesh", {}), dict) else {}
    try:
        scale_factor = float(mesh_cfg.get("emerge_scale_factor", 1.0))
        if not math.isfinite(scale_factor) or not 1.0 <= scale_factor <= 1_000_000.0:
            raise ValueError
    except (OverflowError, TypeError, ValueError):
        findings.append(ValidationFinding(
            "ERROR", "Mesh",
            "EMERGE scale factor must be a finite value between 1 and 1,000,000.",
        ))

    report_progress("Checking exported port dimensions", 0.58)
    for index, port in enumerate(exported_ports, start=1):
        report_progress(
            f"Checking exported port {index}/{max(1, len(exported_ports))}",
            0.58 + 0.02 * index / max(1, len(exported_ports)),
        )
        if not isinstance(port, dict) or str(port.get("type", "LumpedPort")) == "WaveguidePort":
            continue
        name = str(port.get("name", f"LumpedPort {index}"))
        try:
            width = float(port["width"])
            height = float(port["height"])
            if not math.isfinite(width) or not math.isfinite(height) or width <= 0.0 or height <= 0.0:
                raise ValueError
        except (KeyError, OverflowError, TypeError, ValueError):
            findings.append(ValidationFinding(
                "ERROR", "Ports",
                f"{name}: exported LumpedPort must have finite positive width and height.",
            ))

    try:
        default_fraction = float(mesh_cfg.get("default_fraction", 0.3))
        if not math.isfinite(default_fraction) or not 0.01 <= default_fraction <= 1.0:
            raise ValueError
        if default_fraction < 0.1:
            findings.append(ValidationFinding(
                "WARNING", "Mesh",
                f"Default mesh resolution is {default_fraction:g} λ; values below 0.1 λ can create a very large mesh and long solve times.",
            ))
        elif default_fraction > 0.5:
            findings.append(ValidationFinding(
                "WARNING", "Mesh",
                f"Default mesh resolution is {default_fraction:g} λ; values above 0.5 λ may miss geometric details or field variation.",
            ))
    except (TypeError, ValueError):
        findings.append(ValidationFinding("ERROR", "Mesh", "Default mesh resolution must be a finite fraction between 0.01 and 1.0 λ."))

    object_resolutions = mesh_cfg.get("object_resolutions", {})
    if isinstance(object_resolutions, dict):
        resolution_items = list(object_resolutions.items())
        for resolution_index, (object_name, raw_fraction) in enumerate(resolution_items, start=1):
            report_progress(
                f"Checking mesh resolution {resolution_index}/{max(1, len(resolution_items))}",
                0.61 + 0.04 * resolution_index / max(1, len(resolution_items)),
            )
            name = str(object_name).strip()
            if name not in object_by_name:
                findings.append(ValidationFinding("WARNING", "Mesh", f"Mesh resolution refers to missing object '{name}' and will not be applied."))
                continue
            try:
                fraction = float(raw_fraction)
                if not math.isfinite(fraction) or not 0.01 <= fraction <= 1.0:
                    raise ValueError
                if fraction < 0.1:
                    findings.append(ValidationFinding("WARNING", "Mesh", f"{name}: object mesh size {fraction:g} λ may create a very large mesh."))
                elif fraction > 0.5:
                    findings.append(ValidationFinding("WARNING", "Mesh", f"{name}: object mesh size {fraction:g} λ may be too coarse to capture details."))
            except (TypeError, ValueError):
                findings.append(ValidationFinding("ERROR", "Mesh", f"{name}: object mesh resolution must be a finite fraction between 0.01 and 1.0 λ."))

    refinements = mesh_cfg.get("local_refinements", [])
    if isinstance(refinements, list):
        for index, refinement in enumerate(refinements, start=1):
            if not isinstance(refinement, dict) or not bool(refinement.get("enabled", True)):
                continue
            report_progress(
                f"Checking local mesh refinement {index}",
                0.65 + 0.04 * index / max(1, len(refinements)),
            )
            name = str(refinement.get("object", "")).strip()
            if name not in object_by_name:
                findings.append(ValidationFinding("ERROR", "Mesh", f"Local refinement {index} targets missing object '{name}' and will not be exported."))
                continue
            mode = str(refinement.get("mode", "boundary")).strip().lower()
            if mode not in {"boundary", "face"}:
                findings.append(ValidationFinding("ERROR", "Mesh", f"Local refinement {index} on '{name}' has unsupported mode '{mode}'."))
                continue
            valid_faces = {"-x", "+x", "-y", "+y", "-z", "+z"}
            raw_faces = refinement.get("faces", [])
            selected_faces = {
                str(face).strip().lower() for face in raw_faces
            } if isinstance(raw_faces, list) else set()
            if mode == "boundary" and not selected_faces.intersection(valid_faces):
                findings.append(ValidationFinding("ERROR", "Mesh", f"Local boundary refinement {index} on '{name}' has no face selectors and will be skipped."))
            try:
                size_mm = float(refinement.get("size_mm", 0.0))
                growth_rate = float(refinement.get("growth_rate", 3.0))
                if not math.isfinite(size_mm) or size_mm <= 0.0:
                    raise ValueError
                if not math.isfinite(growth_rate) or growth_rate <= 1.0:
                    findings.append(ValidationFinding("WARNING", "Mesh", f"Local refinement {index} on '{name}' has growth rate <= 1; export will clamp it and the mesh may grow excessively."))
                if highest_configured_frequency > 0.0:
                    wavelength_mm = 299.792458 / highest_configured_frequency
                    if size_mm < wavelength_mm * 0.005:
                        findings.append(ValidationFinding(
                            "WARNING", "Mesh",
                            f"Local refinement {index} on '{name}' requests {size_mm:g} mm ({size_mm / wavelength_mm:.4g} λ at the highest configured frequency); this very fine size may sharply increase mesh size and solve time.",
                        ))
            except (TypeError, ValueError):
                findings.append(ValidationFinding("ERROR", "Mesh", f"Local refinement {index} on '{name}' must have a finite positive size in mm."))

    runtime = settings.get("runtime", {}) if isinstance(settings.get("runtime", {}), dict) else {}
    solver = str(runtime.get("solver", "")).strip().upper()
    if solver not in {"PARDISO", "SUPERLU", "UMFPACK", "CUDSS", "AASDS", "MUMPS", "SPARTA"}:
        findings.append(ValidationFinding("ERROR", "Solver", "No supported solver is configured."))
    else:
        findings.append(ValidationFinding("OK", "Solver", f"Solver: {solver}."))

    boundaries = settings.get("boundaries", {}) if isinstance(settings.get("boundaries", {}), dict) else {}
    report_progress("Checking solver and boundary settings", 0.70)
    for boundary_index, (boundary_name, boundary_type) in enumerate(boundaries.items(), start=1):
        report_progress(
            f"Checking boundary {boundary_name}",
            0.70 + 0.02 * boundary_index / max(1, len(boundaries)),
        )
        normalized_type = str(boundary_type).strip().title()
        if normalized_type == "Periodic":
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{boundary_name}: global {boundary_type} boundary export is not implemented; choose Open/Radiation, PEC or PMC.",
            ))
    object_boundaries = settings.get("object_boundaries", [])
    if not isinstance(object_boundaries, list):
        if object_boundaries:
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                "Object boundary assignments must be a list; no object boundaries can be exported.",
            ))
        object_boundaries = []
    port_object_names = {
        str(port.get("plate_name", port.get("object", ""))).strip()
        for port in exported_ports
        if isinstance(port, dict)
        and str(port.get("plate_name", port.get("object", ""))).strip()
    }
    supported_object_boundaries = {
        "PEC",
        "PMC",
        "Open",
        "Radiation",
        "Surface Impedance",
        "Thin Conductor",
    }
    for index, assignment in enumerate(object_boundaries, start=1):
        report_progress(
            f"Checking object boundary assignment {index}",
            0.72 + 0.02 * index / max(1, len(object_boundaries)),
        )
        if not isinstance(assignment, dict):
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"Object boundary assignment {index} is malformed and cannot be exported.",
            ))
            continue
        assignment_name = str(
            assignment.get("name", assignment.get("object", f"Assignment {index}"))
        ).strip() or f"Assignment {index}"
        object_name = str(assignment.get("object", "")).strip()
        boundary_type = str(assignment.get("type", "")).strip()
        if boundary_type == "Periodic":
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: Periodic object boundary is not supported and will not be exported; remove or replace it.",
            ))
            continue
        if boundary_type == "PML":
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: object PML boundaries require volumetric PML geometry and are not exported.",
            ))
            continue
        if boundary_type not in supported_object_boundaries:
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: unsupported object boundary '{boundary_type or '(empty)'}'; "
                "the exporter would omit it.",
            ))
            continue
        target = object_by_name.get(object_name)
        if not object_name or target is None or not bool(getattr(target, "is_model", True)):
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: object boundary target '{object_name or '(empty)'}' "
                "does not resolve to an exported MODEL object.",
            ))
            continue
        open_region_cfg = (
            settings.get("open_region", {})
            if isinstance(settings.get("open_region", {}), dict)
            else {}
        )
        open_region_boundary_target = str(open_region_cfg.get("object", "")).strip()
        if object_name == open_region_boundary_target and open_region_cfg.get("enabled", False):
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: object boundary on the open-region AIR volume is not exported; configure its global face boundaries instead.",
            ))
            continue
        if object_name in port_object_names and boundary_type != "PEC":
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{assignment_name}: '{boundary_type}' conflicts with the LumpedPort on '{object_name}' and cannot be exported.",
            ))
    scattered_jobs = [
        item for item in enabled_simulations
        if str(item.get("type", "")).strip().lower() == "scatteredfield"
    ]
    needs_open_region = bool(scattered_jobs) or any(
        str(value).strip().title() in {"Open", "Radiation", "Pml"}
        for value in boundaries.values()
    )
    open_region = settings.get("open_region", {}) if isinstance(settings.get("open_region", {}), dict) else {}
    open_region_enabled = bool(open_region.get("enabled", False))
    air_name = str(open_region.get("object", "")).strip()
    air_candidates = [obj for obj in model_objects if str(getattr(obj, "material", "")).strip().upper() == "AIR"]
    configured_ports = settings.get("ports", []) if isinstance(settings.get("ports", []), list) else []
    has_waveguide_ports = any(
        isinstance(port, dict) and str(port.get("type", "")).strip() == "WaveguidePort"
        for port in configured_ports
    )
    if open_region_enabled:
        air_object = object_by_name.get(air_name) if air_name else (
            air_candidates[0] if len(air_candidates) == 1 else None
        )
    elif has_waveguide_ports:
        named_air = object_by_name.get(air_name) if air_name else None
        if named_air is not None and str(getattr(named_air, "material", "")).strip().upper() == "AIR":
            air_object = named_air
        else:
            air_object = air_candidates[0] if len(air_candidates) == 1 else None
    else:
        air_object = None
    if needs_open_region:
        if not open_region_enabled or air_object is None or not bool(getattr(air_object, "is_model", True)):
            message = (
                "ScatteredField simulations require one configured MODEL AIR Open Region."
                if scattered_jobs
                else "Open/Radiation boundaries require one MODEL AIR simulation region."
            )
            findings.append(ValidationFinding("ERROR", "Boundaries", message))
        elif str(getattr(air_object, "material", "")).strip().upper() != "AIR":
            findings.append(ValidationFinding("ERROR", "Boundaries", f"Configured simulation region '{air_name}' is not AIR."))
        else:
            findings.append(ValidationFinding("OK", "Boundaries", f"Open-region domain: {getattr(air_object, 'name', 'AIR')}."))
            air_actor = getattr(air_object, "actor", None)
            air_bounds = air_actor.GetBounds() if air_actor is not None else None
            if air_bounds is not None:
                air_tolerance = _model_tolerance(model_objects)
                for region_index, obj in enumerate(volume_objects, start=1):
                    report_progress(
                        f"Checking object against AIR region: "
                        f"{getattr(obj, 'name', 'Object')}",
                        0.74 + 0.02 * region_index / max(1, len(volume_objects)),
                    )
                    if obj is air_object or str(getattr(obj, "material", "")).strip().upper() == "PML":
                        continue
                    actor = getattr(obj, "actor", None)
                    obj_bounds = actor.GetBounds() if actor is not None else None
                    if obj_bounds is None:
                        continue
                    outside_air = any(
                        float(obj_bounds[axis * 2]) < float(air_bounds[axis * 2]) - air_tolerance
                        or float(obj_bounds[axis * 2 + 1]) > float(air_bounds[axis * 2 + 1]) + air_tolerance
                        for axis in range(3)
                    )
                    if outside_air:
                        findings.append(ValidationFinding(
                            "ERROR", "Boundaries",
                            f"{getattr(obj, 'name', 'Object')}: geometry lies outside the configured AIR simulation region.",
                        ))
    if any(str(value).strip().title() == "Pml" for value in boundaries.values()):
        pml = settings.get("pml", {}) if isinstance(settings.get("pml", {}), dict) else {}
        if not bool(pml.get("enabled", False)):
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                "PML is selected on global faces but no explicit PML shell is enabled. Internal waveguides should use PEC walls and WaveguidePort openings.",
            ))
        else:
            try:
                thickness = float(pml.get("thickness_mm", 0.0))
                layers = int(pml.get("layers", 0))
                mesh_layers = int(pml.get("mesh_layers", 0))
                exponent = float(pml.get("exponent", 0.0))
                deltamax = float(pml.get("deltamax", 0.0))
                if (
                    not all(math.isfinite(value) for value in (thickness, exponent, deltamax))
                    or thickness <= 0.0
                    or layers < 1
                    or mesh_layers < 1
                    or exponent <= 0.0
                    or deltamax <= 0.0
                ):
                    raise ValueError
                findings.append(ValidationFinding(
                    "OK",
                    "Boundaries",
                    f"PML: {thickness:g} mm, {layers} geometrical layer(s), {mesh_layers} mesh layer(s).",
                ))
                if layers > 10 or mesh_layers > 20:
                    findings.append(ValidationFinding(
                        "WARNING", "Mesh",
                        f"PML uses {layers} geometrical and {mesh_layers} mesh layer(s); unusually high layer counts can add substantial mesh and memory cost.",
                    ))
            except (OverflowError, TypeError, ValueError):
                findings.append(ValidationFinding("ERROR", "Boundaries", "PML parameters must be positive and define at least one geometrical and mesh layer."))

    ports = configured_ports
    conventional_ports = [
        port for port in ports
        if isinstance(port, dict)
        and str(port.get("type", "")).strip() in {"LumpedPort", "WaveguidePort"}
    ]
    if scattered_jobs and conventional_ports:
        findings.append(ValidationFinding(
            "ERROR",
            "Ports",
            "ScatteredField simulations cannot be combined with configured conventional ports; remove the ports or use a separate project.",
        ))
    if scattered_jobs and any(
        str(value).strip().title() == "Pml" for value in boundaries.values()
    ):
        findings.append(ValidationFinding(
            "ERROR",
            "Boundaries",
            "ScatteredField owns the outer AIR boundary; global PML boundaries cannot be used in the same simulation.",
        ))
    if scattered_jobs and any(
        str(value).strip().title() in {"Pec", "Pmc"}
        for value in boundaries.values()
    ):
        findings.append(ValidationFinding(
            "ERROR",
            "Boundaries",
            "ScatteredField owns the outer AIR boundary; global PEC/PMC face assignments cannot be used in the same simulation.",
        ))
    scattered_pml = settings.get("pml", {})
    if (
        scattered_jobs
        and isinstance(scattered_pml, dict)
        and bool(scattered_pml.get("enabled", False))
    ):
        findings.append(ValidationFinding(
            "ERROR",
            "Boundaries",
            "ScatteredField does not support an enabled PML shell on the AIR domain.",
        ))
    report_progress("Checking simulation region and port configuration", 0.76)
    sweep_jobs = [item for item in enabled_simulations if str(item.get("type", "Sweep")).strip().lower() in {"sweep", "parametric"}]
    if sweep_jobs and not ports:
        findings.append(ValidationFinding("ERROR", "Ports", "Sweep/Parametric simulation requires at least one port."))

    port_object_names = {str(port.get("object", "")).strip() for port in ports if isinstance(port, dict)}
    conductors = [
        obj for obj in model_objects
        if str(getattr(obj, "name", "")).strip() not in port_object_names
        and not bool(getattr(obj, "plate_role", False))
        and _is_conductor(obj, material_catalog)
    ]
    conductor_polydata = [(obj, _world_polydata(obj)) for obj in conductors]
    conductor_polydata = [(obj, poly) for obj, poly in conductor_polydata if poly is not None]
    tolerance = _model_tolerance(model_objects)
    shape_cache: dict[str, Any] = {}
    seen_port_objects: set[str] = set()

    report_progress("Checking port connectivity", 0.78)
    total_contact_checks = max(1, len(ports) * max(1, len(conductor_polydata)))
    for index, port in enumerate(ports, start=1):
        report_progress(
            f"Validating port {index}/{max(1, len(ports))}",
            0.78 + 0.04 * index / max(1, len(ports)),
        )
        if not isinstance(port, dict):
            findings.append(ValidationFinding("ERROR", "Ports", f"Port entry {index} is invalid."))
            continue
        name = str(port.get("name", f"Port {index}")).strip() or f"Port {index}"
        port_type = str(port.get("type", "LumpedPort")).strip()
        if port_type == "PlaneWave":
            findings.append(ValidationFinding(
                "WARNING" if scattered_jobs else "ERROR",
                "Ports",
                f"{name}: legacy PlaneWave port is preserved but not exported; configure a ScatteredField simulation manually.",
            ))
            continue
        object_name = str(port.get("object", "")).strip()
        port_object = object_by_name.get(object_name)
        if not object_name or port_object is None:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: assigned object '{object_name}' does not exist."))
            continue
        if object_name in seen_port_objects:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: object '{object_name}' is assigned to more than one port."))
        seen_port_objects.add(object_name)
        is_swept_surface = type(port_object).__name__ == "SweptSurfaceObject"
        if is_swept_surface and port_type != "LumpedPort":
            findings.append(ValidationFinding(
                "ERROR",
                "Ports",
                f"{name}: generated surface objects support LumpedPort only.",
            ))
            continue
        if (
            not is_swept_surface
            and type(port_object).__name__ != "PlateObject"
            and not bool(getattr(port_object, "plate_role", False))
        ):
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: assigned object '{object_name}' is not a Plate."))
            continue
        if not bool(getattr(port_object, "is_model", True)):
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: assigned object '{object_name}' is NON MODEL."))
            continue

        params = port.get("params", {}) if isinstance(port.get("params", {}), dict) else {}
        if is_swept_surface:
            geometry = port_object.get_parameters()
            try:
                width = float(geometry.get("ProfilePerimeter", 0.0))
                height = float(geometry.get("ExtrusionLength", 0.0))
                direction = [
                    float(value) for value in geometry.get("Direction", ())
                ]
                override = [
                    float(params.get(f"Direction_{axis}", 0.0))
                    for axis in "XYZ"
                ]
                resistance = float(params.get("Resistance_Ohm", 0.0))
                valid = (
                    math.isfinite(width) and width > 0.0
                    and math.isfinite(height) and height > 0.0
                    and len(direction) == 3
                    and all(math.isfinite(value) for value in direction)
                    and math.sqrt(sum(value * value for value in direction)) > 1e-12
                    and all(math.isfinite(value) for value in override)
                    and math.isfinite(resistance) and resistance > 0.0
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Ports",
                    f"{name}: generated-surface LumpedPort requires a finite positive profile perimeter and extrusion length, a valid extrusion direction, and positive resistance.",
                ))
            else:
                findings.append(ValidationFinding(
                    "OK",
                    "Ports",
                    f"{name}: LumpedPort uses the generated surface with W={width:.6g}, H={height:.6g}, and the signed extrusion direction.",
                ))
            # A generated surface is already the EMERGE port face; it is not
            # a Plate connector and must not be subjected to Plate contact tests.
            continue

        plate_poly = _world_polydata(port_object)
        if plate_poly is None:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate '{object_name}' has empty geometry."))
            continue
        bounds = plate_poly.GetBounds()
        spans = sorted((float(bounds[1]) - float(bounds[0]), float(bounds[3]) - float(bounds[2]), float(bounds[5]) - float(bounds[4])), reverse=True)
        if spans[1] <= tolerance:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate '{object_name}' is geometrically degenerate."))
            continue

        if port_type == "LumpedPort":
            direction = [float(params.get(f"Direction_{axis}", 0.0)) for axis in "XYZ"]
            if math.sqrt(sum(value * value for value in direction)) <= 1e-12:
                findings.append(ValidationFinding("ERROR", "Ports", f"{name}: LumpedPort direction is zero."))
            try:
                if float(params.get("Resistance_Ohm", 0.0)) <= 0.0:
                    findings.append(ValidationFinding("ERROR", "Ports", f"{name}: resistance must be greater than zero."))
            except (TypeError, ValueError):
                findings.append(ValidationFinding("ERROR", "Ports", f"{name}: resistance is invalid."))

            contacts = []
            tangential_contacts = []
            finite_area_contacts = []
            unmeasured_area_contacts = []
            brep_gaps = []
            unverified_breps = []
            for contact_index, (obj, poly) in enumerate(conductor_polydata, start=1):
                completed_contacts = (index - 1) * max(1, len(conductor_polydata)) + contact_index
                report_progress(
                    f"Checking {name} contact {contact_index}/{len(conductor_polydata)}",
                    0.82 + 0.13 * completed_contacts / total_contact_checks,
                )
                connection_tolerance = _brep_connection_tolerance(
                    (port_object, obj),
                    mm_per_unit,
                )
                exact_measure = _exact_brep_contact_measure(
                    port_object,
                    obj,
                    shape_cache,
                    connection_tolerance,
                )
                contact_name = str(getattr(obj, "name", ""))
                if exact_measure is None:
                    has_step_brep = bool(
                        str(getattr(port_object, "step_source_path", "") or "").strip()
                        or str(getattr(obj, "step_source_path", "") or "").strip()
                    )
                    if has_step_brep:
                        unverified_breps.append(contact_name)
                        continue
                    if _touches(plate_poly, poly, tolerance):
                        contacts.append(contact_name)
                    continue

                exact_distance, common_area = exact_measure
                if exact_distance > connection_tolerance:
                    brep_gaps.append((contact_name, exact_distance))
                elif common_area is None:
                    contacts.append(contact_name)
                    unmeasured_area_contacts.append(contact_name)
                elif common_area <= _exact_contact_epsilon((port_object, obj)) ** 2:
                    contacts.append(contact_name)
                    tangential_contacts.append(contact_name)
                else:
                    contacts.append(contact_name)
                    finite_area_contacts.append((contact_name, common_area))
            if not contacts:
                gap_detail = ""
                if brep_gaps:
                    closest_name, gap = min(brep_gaps, key=lambda item: item[1])
                    gap_detail = (
                        f" The closest exact BREP is '{closest_name}', separated by "
                        f"{gap:.6g} model units."
                    )
                if unverified_breps:
                    gap_detail += (
                        " Exact STEP BREP contact could not be verified for "
                        f"{', '.join(unverified_breps)}."
                    )
                findings.append(ValidationFinding(
                    "ERROR",
                    "Ports",
                    f"{name}: Plate '{object_name}' is not connected to conductive geometry.{gap_detail}",
                ))
            elif len(contacts) < 2:
                gap_detail = ""
                if brep_gaps:
                    closest_name, gap = min(brep_gaps, key=lambda item: item[1])
                    gap_detail = (
                        f" The closest other exact BREP is '{closest_name}', separated by "
                        f"{gap:.6g} model units."
                    )
                if unverified_breps:
                    gap_detail += (
                        " Exact STEP BREP contact could not be verified for "
                        f"{', '.join(unverified_breps)}."
                    )
                if tangential_contacts:
                    gap_detail += (
                        " Exact BREP contact is only line/point tangency for "
                        f"{', '.join(tangential_contacts)}."
                    )
                if unmeasured_area_contacts:
                    gap_detail += (
                        " BREP distance is within the 100 nm connection tolerance, "
                        "but common area could not be measured for "
                        f"{', '.join(unmeasured_area_contacts)}."
                    )
                findings.append(ValidationFinding(
                    "ERROR",
                    "Ports",
                    f"{name}: Plate touches only one conductor ({contacts[0]}); a LumpedPort must connect signal and reference conductors.{gap_detail}",
                ))
            else:
                area_detail = ""
                if finite_area_contacts:
                    measured_areas = ", ".join(
                        f"{contact_name}: {area:.6g}"
                        for contact_name, area in finite_area_contacts
                    )
                    area_detail = f" Finite BREP contact area (model units²): {measured_areas}."
                findings.append(ValidationFinding(
                    "OK",
                    "Ports",
                    f"{name}: connected to {', '.join(contacts)}.{area_detail}",
                ))
                if tangential_contacts:
                    findings.append(ValidationFinding(
                        "WARNING",
                        "Ports",
                        f"{name}: BREP proximity is within the 100 nm connection tolerance but has no finite-area common geometry for {', '.join(tangential_contacts)}; verify that the tolerated gap or edge-only contact is intentional.",
                    ))
                if unmeasured_area_contacts:
                    findings.append(ValidationFinding(
                        "WARNING",
                        "Ports",
                        f"{name}: BREP distance is within the 100 nm connection tolerance, but common area could not be measured for {', '.join(unmeasured_area_contacts)}; verify the geometry.",
                    ))

        if air_object is not None:
            air_actor = getattr(air_object, "actor", None)
            air_bounds = air_actor.GetBounds() if air_actor is not None else None
            outside_air = air_bounds is not None and any(
                float(bounds[axis * 2]) < float(air_bounds[axis * 2]) - tolerance
                or float(bounds[axis * 2 + 1]) > float(air_bounds[axis * 2 + 1]) + tolerance
                for axis in range(3)
            )
            if outside_air:
                findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate lies outside the AIR simulation region."))
            elif port_type == "WaveguidePort":
                on_outer_face = any(
                    abs(float(bounds[axis * 2 + side]) - float(air_bounds[axis * 2 + side])) <= tolerance
                    for axis in range(3)
                    for side in (0, 1)
                )
                if on_outer_face:
                    findings.append(ValidationFinding("OK", "Ports", f"{name}: WaveguidePort lies on the AIR outer boundary."))
                else:
                    findings.append(ValidationFinding("ERROR", "Ports", f"{name}: WaveguidePort must lie on an outer face of the AIR simulation region."))
        elif port_type == "WaveguidePort":
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: WaveguidePort requires an AIR simulation region."))

    report_progress("Port connectivity checks complete", 0.96)
    if not any(item.severity == "ERROR" for item in findings):
        findings.append(ValidationFinding("OK", "Summary", "No blocking simulation configuration errors found."))
    simulation_by_name = {
        str(item.get("name", "")).strip(): item
        for item in enabled_simulations
        if isinstance(item, dict)
    }
    farfield_types = {"plot_ff", "plot_ff_polar", "plot_ff_3d"}
    outputs = settings.get("outputs", [])
    if isinstance(outputs, list):
        for index, output in enumerate(outputs, start=1):
            report_progress(
                f"Checking configured output {index}/{max(1, len(outputs))}",
                0.96 + 0.03 * index / max(1, len(outputs)),
            )
            if not isinstance(output, dict) or not bool(output.get("enabled", True)):
                continue
            output_simulation = simulation_by_name.get(
                str(output.get("simulation", "")).strip()
            )
            output_name = str(
                output.get("name", f"Output {index}")
            ).strip() or f"Output {index}"
            plot_type = str(output.get("plot_type", "")).strip().lower()
            if plot_type == "plot_sf_3d":
                if (
                    not isinstance(output_simulation, dict)
                    or str(output_simulation.get("type", "")).strip().lower()
                    != "scatteredfield"
                ):
                    findings.append(ValidationFinding(
                        "ERROR",
                        "Outputs",
                        f"{output_name}: plot_sf_3d is available only for an enabled ScatteredField simulation.",
                    ))
                else:
                    findings.append(ValidationFinding(
                        "OK",
                        "Outputs",
                        f"{output_name}: 3D scattered-field output does not require far-field integration.",
                    ))
                continue
            if (
                not isinstance(output_simulation, dict)
                or str(output_simulation.get("type", "")).strip().lower()
                != "scatteredfield"
            ):
                continue
            if plot_type not in farfield_types:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Outputs",
                    f"{output_name}: ScatteredField supports far-field outputs only; choose a plot_ff output.",
                ))
                continue
            params = output.get("params", {})
            params = params if isinstance(params, dict) else {}
            integration_name = str(
                params.get("integration_object", "")
            ).strip()
            integration_object = object_by_name.get(integration_name)
            if integration_object is None:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Outputs",
                    f"{output_name}: select an existing closed model object for far-field integration.",
                ))
                continue
            if (
                not bool(getattr(integration_object, "is_model", True))
                or type(integration_object).__name__ == "PlateObject"
                or bool(getattr(integration_object, "plate_role", False))
            ):
                findings.append(ValidationFinding(
                    "ERROR",
                    "Outputs",
                    f"{output_name}: far-field integration requires a MODEL volume object, not a plate or NON MODEL object.",
                ))
                continue
            integration_poly = _world_polydata(integration_object)
            if integration_poly is None:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Outputs",
                    f"{output_name}: the selected integration object has no usable boundary surface.",
                ))
                continue
            open_edges = _open_or_nonmanifold_edge_count(integration_poly)
            if open_edges:
                findings.append(ValidationFinding(
                    "ERROR",
                    "Outputs",
                    f"{output_name}: the selected integration surface is not closed ({open_edges:,} open or non-manifold edges).",
                ))
            else:
                findings.append(ValidationFinding(
                    "OK",
                    "Outputs",
                    f"{output_name}: closed far-field integration surface '{integration_name}'.",
                ))

    report_progress("Simulation validation complete", 1.0)
    return findings
