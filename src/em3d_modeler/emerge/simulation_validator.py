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
from typing import Any, Iterable

import vtk


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
) -> list[ValidationFinding]:
    """Validate project data and world-space port connectivity before export."""
    findings: list[ValidationFinding] = []
    objects = list(objects)
    material_catalog = material_catalog or {}
    model_objects = [obj for obj in objects if bool(getattr(obj, "is_model", True))]
    object_by_name = {str(getattr(obj, "name", "")).strip(): obj for obj in objects}

    volume_objects = [
        obj for obj in model_objects
        if type(obj).__name__ != "PlateObject" and not bool(getattr(obj, "plate_role", False))
    ]
    if not volume_objects:
        findings.append(ValidationFinding("ERROR", "Geometry", "No 3D MODEL objects are available for simulation."))
    else:
        findings.append(ValidationFinding("OK", "Geometry", f"{len(volume_objects)} MODEL geometry object(s) available."))

    for obj in model_objects:
        name = str(getattr(obj, "name", "")).strip() or type(obj).__name__
        material = str(getattr(obj, "material", "")).strip()
        poly = _world_polydata(obj)
        if poly is None:
            findings.append(ValidationFinding("ERROR", "Geometry", f"{name}: missing or empty geometry."))
        elif type(obj).__name__ != "PlateObject" and not bool(getattr(obj, "plate_role", False)):
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

    simulations = settings.get("simulations", [])
    enabled_simulations = [item for item in simulations if isinstance(item, dict) and bool(item.get("enabled", True))] if isinstance(simulations, list) else []
    if not enabled_simulations:
        findings.append(ValidationFinding("ERROR", "Simulation", "No enabled simulation job is configured."))
    estimated_frequency_solves = 0
    highest_configured_frequency = 0.0
    for simulation in enabled_simulations:
        name = str(simulation.get("name", "Simulation"))
        try:
            fmin = float(simulation.get("Fmin_GHz", 0.0))
            fmax = float(simulation.get("Fmax_GHz", 0.0))
            points = int(simulation.get("NumberOfPoints", 0))
            if not math.isfinite(fmin) or not math.isfinite(fmax) or fmin <= 0.0 or fmax <= fmin or points < 2:
                raise ValueError
        except (OverflowError, TypeError, ValueError):
            findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: invalid frequency range or point count."))
            continue
        highest_configured_frequency = max(highest_configured_frequency, fmax)

        sim_type = str(simulation.get("type", "Sweep")).strip().lower()
        if sim_type not in {"sweep", "eigenmode", "parametric"}:
            findings.append(ValidationFinding("ERROR", "Simulation", f"{name}: unsupported simulation type '{sim_type}'."))
            continue
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

    mesh_cfg = settings.get("mesh", {}) if isinstance(settings.get("mesh", {}), dict) else {}
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
        for object_name, raw_fraction in object_resolutions.items():
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
    if solver not in {"PARDISO", "SUPERLU", "UMFPACK", "CUDSS", "AASDS", "MUMPS"}:
        findings.append(ValidationFinding("ERROR", "Solver", "No supported solver is configured."))
    else:
        findings.append(ValidationFinding("OK", "Solver", f"Solver: {solver}."))

    boundaries = settings.get("boundaries", {}) if isinstance(settings.get("boundaries", {}), dict) else {}
    for boundary_name, boundary_type in boundaries.items():
        normalized_type = str(boundary_type).strip().title()
        if normalized_type == "Periodic":
            findings.append(ValidationFinding(
                "ERROR",
                "Boundaries",
                f"{boundary_name}: global {boundary_type} boundary export is not implemented; choose Open/Radiation, PEC or PMC.",
            ))
    needs_open_region = any(str(value).strip().title() in {"Open", "Radiation", "Pml"} for value in boundaries.values())
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
            findings.append(ValidationFinding("ERROR", "Boundaries", "Open/Radiation boundaries require one MODEL AIR simulation region."))
        elif str(getattr(air_object, "material", "")).strip().upper() != "AIR":
            findings.append(ValidationFinding("ERROR", "Boundaries", f"Configured simulation region '{air_name}' is not AIR."))
        else:
            findings.append(ValidationFinding("OK", "Boundaries", f"Open-region domain: {getattr(air_object, 'name', 'AIR')}."))
            air_actor = getattr(air_object, "actor", None)
            air_bounds = air_actor.GetBounds() if air_actor is not None else None
            if air_bounds is not None:
                air_tolerance = _model_tolerance(model_objects)
                for obj in volume_objects:
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
    sweep_jobs = [item for item in enabled_simulations if str(item.get("type", "Sweep")).strip().lower() in {"sweep", "parametric"}]
    if sweep_jobs and not ports:
        findings.append(ValidationFinding("ERROR", "Ports", "Sweep/Parametric simulation requires at least one port."))

    port_object_names = {str(port.get("object", "")).strip() for port in ports if isinstance(port, dict)}
    conductors = [
        obj for obj in model_objects
        if str(getattr(obj, "name", "")).strip() not in port_object_names
        and type(obj).__name__ != "PlateObject"
        and not bool(getattr(obj, "plate_role", False))
        and _is_conductor(obj, material_catalog)
    ]
    conductor_polydata = [(obj, _world_polydata(obj)) for obj in conductors]
    conductor_polydata = [(obj, poly) for obj, poly in conductor_polydata if poly is not None]
    tolerance = _model_tolerance(model_objects)
    seen_port_objects: set[str] = set()

    for index, port in enumerate(ports, start=1):
        if not isinstance(port, dict):
            findings.append(ValidationFinding("ERROR", "Ports", f"Port entry {index} is invalid."))
            continue
        name = str(port.get("name", f"Port {index}")).strip() or f"Port {index}"
        port_type = str(port.get("type", "LumpedPort")).strip()
        object_name = str(port.get("object", "")).strip()
        port_object = object_by_name.get(object_name)
        if not object_name or port_object is None:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: assigned object '{object_name}' does not exist."))
            continue
        if object_name in seen_port_objects:
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate '{object_name}' is assigned to more than one port."))
        seen_port_objects.add(object_name)
        if type(port_object).__name__ != "PlateObject" and not bool(getattr(port_object, "plate_role", False)):
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: assigned object '{object_name}' is not a Plate."))
            continue
        if not bool(getattr(port_object, "is_model", True)):
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate '{object_name}' is NON MODEL."))
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

        params = port.get("params", {}) if isinstance(port.get("params", {}), dict) else {}
        if port_type == "PlaneWave":
            findings.append(ValidationFinding("ERROR", "Ports", f"{name}: PlaneWave export is not implemented by the current script exporter."))
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

            contacts = [
                str(getattr(obj, "name", ""))
                for obj, poly in conductor_polydata
                if _touches(plate_poly, poly, tolerance)
            ]
            if not contacts:
                findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate '{object_name}' is not connected to conductive geometry."))
            elif len(contacts) < 2:
                findings.append(ValidationFinding("ERROR", "Ports", f"{name}: Plate touches only one conductor ({contacts[0]}); a LumpedPort must connect signal and reference conductors."))
            else:
                findings.append(ValidationFinding("OK", "Ports", f"{name}: connected to {', '.join(contacts)}."))

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

    if not any(item.severity == "ERROR" for item in findings):
        findings.append(ValidationFinding("OK", "Summary", "No blocking simulation configuration errors found."))
    return findings
