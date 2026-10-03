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

"""Generate runnable Python script for EMERGE simulation."""
from __future__ import annotations
import ast
from typing import Any, Dict, List
import json
import locale
import math
import re
from em3d_modeler import __version__ as _em3d_modeler_version

def _detect_emerge_version() -> str:
    """Best-effort resolution of the installed EMERGE version."""
    candidates: List[str] = []

    try:
        import importlib.metadata as importlib_metadata
    except Exception:
        importlib_metadata = None

    if importlib_metadata is not None:
        for pkg in ("emerge", "emerge_iron"):
            try:
                value = str(importlib_metadata.version(pkg))
                if value and value not in candidates:
                    candidates.append(value)
            except Exception:
                pass

    try:
        import pkg_resources  # type: ignore
        try:
            value = str(pkg_resources.get_distribution("emerge").version)
            if value and value not in candidates:
                candidates.append(value)
        except Exception:
            pass
    except Exception:
        pass

    try:
        import emerge as em
        for attr in ("__version__", "version"):
            value = getattr(em, attr, None)
            if value is not None:
                value = str(value)
                if value and value not in candidates:
                    candidates.append(value)
    except Exception:
        pass

    for value in candidates:
        match = re.search(r"(\d+\.\d+\.\d+)", value)
        if match:
            return match.group(1)
    return "unknown"


def _q(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def _to_int(value: Any, default: int) -> int:
    try:
        return int(_to_float(value, float(default)))
    except Exception:
        return int(default)


def _to_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return float(default)

        s = s.replace("\u00a0", "").replace(" ", "").replace("'", "")

        try:
            return float(s)
        except Exception:
            pass

        try:
            return float(locale.atof(s))
        except Exception:
            pass

        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            return float(s)
        except Exception:
            return float(default)

    try:
        return float(value)
    except Exception:
        return float(default)


def _material_block(
    used_materials: List[str],
    materials_catalog: Dict[str, Dict[str, Any]] | None,
) -> List[str]:
    lines: List[str] = ["materials = {}", ""]
    for name in used_materials:
        up = name.upper()
        if up == "PEC":
            lines.append(f"materials[{_q(name)}] = em.lib.PEC")
            continue
        if up == "PMC":
            lines.append(f"materials[{_q(name)}] = em.lib.PMC")
            continue
        if up == "PML":
            continue

        rec = (materials_catalog or {}).get(name, {})
        er = _to_float(rec.get("er", 1.0), 1.0)
        ur = _to_float(rec.get("ur", 1.0), 1.0)
        tan_d = _to_float(rec.get("tan_d", 0.0), 0.0)
        sigma = _to_float(rec.get("sigma", 0.0), 0.0)
        color = str(rec.get("color", "#bebebe"))
        opacity = _to_float(rec.get("opacity", 0.85), 0.85)

        lines += [
            f"materials[{_q(name)}] = em.Material(name={_q(name)}, er={er}, ur={ur}, tand={tan_d}, cond={sigma})",
            f"materials[{_q(name)}].color = {_q(color)}",
            f"materials[{_q(name)}].opacity = {opacity}",
            "",
        ]

    return lines


def _normalize_simulation_configs(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    sims_raw = settings.get("simulations", [])
    sims: List[Dict[str, Any]] = []
    if isinstance(sims_raw, list):
        for idx, item in enumerate(sims_raw, start=1):
            if not isinstance(item, dict):
                continue
            sim_type = str(item.get("type", "Sweep")).strip().title()
            if sim_type not in {"Sweep", "Eigenmode", "Parametric"}:
                sim_type = "Sweep"
            sims.append(
                {
                    "name": str(item.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}",
                    "type": sim_type,
                    "enabled": _to_bool(item.get("enabled", True)),
                    "Fmin_GHz": _to_float(item.get("Fmin_GHz", 0.1), 0.1),
                    "Fmax_GHz": _to_float(item.get("Fmax_GHz", 10.0), 10.0),
                    "Fstep_GHz": _to_float(item.get("Fstep_GHz", 0.1), 0.1),
                    "EigenmodeCount": max(1, _to_int(item.get("EigenmodeCount", 5), 5)),
                    "progressive_sparams_chunk_size": max(
                        1, _to_int(item.get("progressive_sparams_chunk_size", 10), 10)
                    ),
                    "ParamName": str(item.get("ParamName", "")).strip(),
                    "ParamValues": str(item.get("ParamValues", "")).strip(),
                    **(
                        {"progressive_sparams_enabled": _to_bool(item["progressive_sparams_enabled"])}
                        if "progressive_sparams_enabled" in item else {}
                    ),
                    "sparam_fitting": dict(item.get("sparam_fitting", {})) if isinstance(item.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
                    "LogVerbosity": str(item.get("LogVerbosity", "Info")).strip().title(),
                }
            )

    if not sims:
        legacy = settings.get("simulation", {})
        if not isinstance(legacy, dict):
            legacy = {}
        sims = [
            {
                "name": "Simulation_1",
                "type": "Sweep",
                "enabled": True,
                "Fmin_GHz": _to_float(legacy.get("Fmin_GHz", 0.1), 0.1),
                "Fmax_GHz": _to_float(legacy.get("Fmax_GHz", 10.0), 10.0),
                "Fstep_GHz": _to_float(legacy.get("Fstep_GHz", 0.1), 0.1),
                "EigenmodeCount": 5,
                "progressive_sparams_chunk_size": max(
                    1, _to_int(legacy.get("progressive_sparams_chunk_size", 10), 10)
                ),
                "ParamName": "",
                "ParamValues": "",
                "sparam_fitting": dict(legacy.get("sparam_fitting", {})) if isinstance(legacy.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
                "LogVerbosity": str(legacy.get("LogVerbosity", "Info")).strip().title(),
            }
        ]

    return sims


def export_emerge_python_script(
    project_name: str,
    settings: Dict[str, Any],
    step_entries: List[Dict[str, Any]],
    units: str = "mm",
    materials_catalog: Dict[str, Dict[str, Any]] | None = None,
    show_model: bool = False,
    preview_only: bool = False,
    show_mesh: bool = False,
    run_sweep: bool = True,
    lumped_ports: List[Dict[str, Any]] | None = None,
    plate_entries: List[Dict[str, Any]] | None = None,
    solver: str = "PARDISO",
    parallel_enabled: bool = True,
    pardiso_threads: int = 8,
    acc_threads: int = 10,
    mesh_resolution_fraction: float = 0.3,
    plot_sparams_after_sim: bool = True,
    progressive_sparams_enabled: bool = False,
    export_sparams_after_sim: bool = True,
    simulation_override: Dict[str, Any] | None = None,
) -> str:
    simulations = _normalize_simulation_configs(settings)
    sim = dict(simulation_override) if isinstance(simulation_override, dict) else None
    if sim is None:
        enabled = [s for s in simulations if bool(s.get("enabled", True))]
        sim = dict(enabled[0]) if enabled else {
            "name": "Simulation_1",
            "type": "Sweep",
            "enabled": True,
            "Fmin_GHz": 0.1,
            "Fmax_GHz": 10.0,
            "Fstep_GHz": 0.1,
            "EigenmodeCount": 5,
            "ParamName": "",
            "ParamValues": "",
            "LogVerbosity": "Info",
        }
    progressive_sparams_enabled = bool(
        sim.get("progressive_sparams_enabled", progressive_sparams_enabled)
    )
    progressive_sparams_chunk_size = max(
        1, _to_int(sim.get("progressive_sparams_chunk_size", 10), 10)
    )

    sim_name = str(sim.get("name", "Simulation_1")).strip() or "Simulation_1"
    sim_type = str(sim.get("type", "Sweep")).strip().lower() or "sweep"
    fmin = _to_float(sim.get("Fmin_GHz", 0.1), 0.1)
    fmax = _to_float(sim.get("Fmax_GHz", 10.0), 10.0)
    fstep = _to_float(sim.get("Fstep_GHz", 0.1), 0.1)
    if fstep <= 0.0:
        fstep = 0.1
    if fmin <= 0.0:
        fmin = 1e-6
    if fmax <= fmin:
        fmax = fmin + max(fstep, 1e-6)
    configured_points = sim.get("NumberOfPoints")
    if configured_points is None:
        npoints = int(max(2, round((fmax - fmin) / max(fstep, 1e-9)) + 1))
    else:
        npoints = max(2, _to_int(configured_points, round((fmax - fmin) / max(fstep, 1e-9)) + 1))
    fstep = (fmax - fmin) / (npoints - 1)
    fit_cfg = sim.get("sparam_fitting", {}) if isinstance(sim.get("sparam_fitting", {}), dict) else {}
    fit_enabled = _to_bool(fit_cfg.get("enabled", False))
    fit_points = max(8, _to_int(fit_cfg.get("points", 1001), 1001))

    mode_count = max(1, _to_int(sim.get("EigenmodeCount", 5), 5))
    param_name = str(sim.get("ParamName", "")).strip()
    param_values = str(sim.get("ParamValues", "")).strip()

    mesh_resolution = max(0.01, min(1.0, float(mesh_resolution_fraction)))
    mesh_cfg = settings.get("mesh", {}) if isinstance(settings, dict) else {}
    if not isinstance(mesh_cfg, dict):
        mesh_cfg = {}
    emerge_scale_factor = _to_float(mesh_cfg.get("emerge_scale_factor", 1.0), 1.0)
    if not math.isfinite(emerge_scale_factor):
        emerge_scale_factor = 1.0
    emerge_scale_factor = max(1.0, min(1_000_000.0, emerge_scale_factor))
    mm_per_scene_unit = {
        "mm": 1.0,
        "um": 0.001,
        "cm": 10.0,
        "m": 1000.0,
        "mil": 0.0254,
        "inch": 25.4,
    }.get(str(units).strip().lower(), 1.0)
    coordinate_scale = 0.001 * emerge_scale_factor
    scene_coordinate_scale = mm_per_scene_unit * coordinate_scale
    curved_boundary_resolution = max(
        3,
        _to_int(mesh_cfg.get("curved_boundary_resolution", 20), 20),
    ) if isinstance(mesh_cfg, dict) else 20
    object_mesh_fractions = mesh_cfg.get("object_resolutions", {}) if isinstance(mesh_cfg, dict) else {}
    if not isinstance(object_mesh_fractions, dict):
        object_mesh_fractions = {}
    object_mesh_sizes: Dict[str, float] = {}
    wavelength_at_fmax = 299_792_458.0 / (fmax * 1e9)
    for entry in step_entries:
        object_name = str(entry.get("object_name", "Object"))
        fraction = object_mesh_fractions.get(object_name)
        if fraction is None:
            continue
        fraction_value = max(0.01, min(1.0, _to_float(fraction, mesh_resolution)))
        object_mesh_sizes[object_name] = fraction_value * wavelength_at_fmax * emerge_scale_factor
    raw_local_refinements = mesh_cfg.get("local_refinements", []) if isinstance(mesh_cfg, dict) else []
    if not isinstance(raw_local_refinements, list):
        raw_local_refinements = []
    ports = lumped_ports or []
    plates = plate_entries or []
    port_surface_names = {
        str(port.get("plate_name", "")).strip()
        for port in ports
        if str(port.get("plate_name", "")).strip()
    }
    object_boundaries = settings.get("object_boundaries", []) if isinstance(settings, dict) else []
    if not isinstance(object_boundaries, list):
        object_boundaries = []
    exported_object_names = {
        str(entry.get("object_name", "")).strip()
        for entry in step_entries
        if str(entry.get("object_name", "")).strip()
    }
    plate_names = {
        str(plate.get("object_name", "")).strip()
        for plate in plates
        if str(plate.get("object_name", "")).strip()
    }
    air_volume_names = [
        str(entry.get("object_name", "")).strip()
        for entry in step_entries
        if str(entry.get("material", "")).strip().upper() == "AIR"
        and str(entry.get("object_name", "")).strip()
    ]
    has_open_region_cfg = isinstance(settings, dict) and "open_region" in settings
    open_region_cfg = settings.get("open_region", {}) if isinstance(settings, dict) else {}
    open_region_enabled = _to_bool(open_region_cfg.get("enabled", False)) if isinstance(open_region_cfg, dict) else False
    configured_air_name = (
        str(open_region_cfg.get("object", "")).strip()
        if isinstance(open_region_cfg, dict)
        else ""
    )
    has_waveguide_ports = any(
        isinstance(port, dict) and str(port.get("type", "")).strip() == "WaveguidePort"
        for port in ports
    )
    if has_open_region_cfg and not open_region_enabled and not has_waveguide_ports:
        air_volume_name = ""
    elif configured_air_name in air_volume_names:
        air_volume_name = configured_air_name
    elif len(air_volume_names) == 1:
        air_volume_name = air_volume_names[0]
    else:
        air_volume_name = ""
    global_boundaries = settings.get("boundaries", {}) if isinstance(settings, dict) else {}
    if not isinstance(global_boundaries, dict):
        global_boundaries = {}
    face_selectors = {
        "Xmin": "-x",
        "Xmax": "+x",
        "Ymin": "-y",
        "Ymax": "+y",
        "Zmin": "-z",
        "Zmax": "+z",
    }
    domain_boundaries = [
        (key, face_selectors[key], str(global_boundaries.get(key, "")).strip().title())
        for key in face_selectors
        if str(global_boundaries.get(key, "")).strip().title() not in {"", "None"}
    ] if air_volume_name else []
    waveguide_face_selectors = {
        str(port.get("face_name", "")).strip().lower()
        for port in ports
        if str(port.get("type", "")).strip() == "WaveguidePort"
        and str(port.get("face_name", "")).strip()
    }
    pml_side_codes = {
        "Xmin": "l",
        "Xmax": "r",
        "Ymin": "f",
        "Ymax": "a",
        "Zmin": "b",
        "Zmax": "t",
    }
    requested_pml_boundaries = [
        (key, face_selectors[key], pml_side_codes[key])
        for key in face_selectors
        if str(global_boundaries.get(key, "")).strip().title() == "Pml"
        and face_selectors[key] not in waveguide_face_selectors
    ]
    pml_entry_names = {
        str(entry.get("object_name", "")).strip()
        for entry in step_entries
        if str(entry.get("material", "")).strip().upper() == "PML"
        and str(entry.get("object_name", "")).strip()
    }
    pml_cfg = settings.get("pml", {}) if isinstance(settings, dict) else {}
    if not isinstance(pml_cfg, dict):
        pml_cfg = {}
    pml_enabled = _to_bool(pml_cfg.get("enabled", False))
    configured_pml_air_name = str(pml_cfg.get("air_object", "")).strip()
    configured_pml_name = str(pml_cfg.get("outer_object", "")).strip()
    pml_name = configured_pml_name if configured_pml_name in pml_entry_names else (
        next(iter(pml_entry_names)) if len(pml_entry_names) == 1 else "PML_Region"
    )
    pml_setup = None
    explicit_pml_geometry = (
        pml_enabled
        and configured_pml_air_name == air_volume_name
        and bool(configured_pml_name)
        and configured_pml_name in pml_entry_names
    )
    if requested_pml_boundaries and explicit_pml_geometry:
        air_entry = next(
            (entry for entry in step_entries if str(entry.get("object_name", "")).strip() == air_volume_name),
            None,
        )
        air_bounds = air_entry.get("bounds_mm") if isinstance(air_entry, dict) else None
        if not isinstance(air_bounds, (list, tuple)) or len(air_bounds) != 6:
            raise ValueError("PML export requires world-space bounds for the AIR simulation region; regenerate the STEP bundle")
        air_bounds = tuple(float(value) for value in air_bounds)
        thickness_mm = _to_float(pml_cfg.get("thickness_mm", 0.0), 0.0)
        if thickness_mm <= 0.0:
            pml_entry = next(
                (entry for entry in step_entries if str(entry.get("object_name", "")).strip() == pml_name),
                None,
            )
            outer_bounds = pml_entry.get("bounds_mm") if isinstance(pml_entry, dict) else None
            if not isinstance(outer_bounds, (list, tuple)) or len(outer_bounds) != 6:
                raise ValueError("PML export requires a positive thickness or bounds for the outer PML object")
            outer_bounds = tuple(float(value) for value in outer_bounds)
            side_thicknesses = {
                "Xmin": air_bounds[0] - outer_bounds[0],
                "Xmax": outer_bounds[1] - air_bounds[1],
                "Ymin": air_bounds[2] - outer_bounds[2],
                "Ymax": outer_bounds[3] - air_bounds[3],
                "Zmin": air_bounds[4] - outer_bounds[4],
                "Zmax": outer_bounds[5] - air_bounds[5],
            }
            selected_thicknesses = [side_thicknesses[key] for key, _, _ in requested_pml_boundaries]
            if any(value <= 0.0 for value in selected_thicknesses):
                raise ValueError("The outer PML object must extend beyond the AIR region on every selected PML side")
            thickness_mm = sum(selected_thicknesses) / len(selected_thicknesses)
            tolerance = max(thickness_mm * 1e-3, 1e-6)
            if any(abs(value - thickness_mm) > tolerance for value in selected_thicknesses):
                raise ValueError("EMerge pmlbox requires a uniform PML thickness on all selected sides")
        pml_setup = {
            "air_bounds_mm": air_bounds,
            "thickness_m": thickness_mm * 0.001,
            "sides": "".join(code for _, _, code in requested_pml_boundaries),
            "layers": max(1, _to_int(pml_cfg.get("layers", 1), 1)),
            "mesh_layers": max(1, _to_int(pml_cfg.get("mesh_layers", 5), 5)),
            "exponent": max(0.1, _to_float(pml_cfg.get("exponent", 1.5), 1.5)),
            "deltamax": max(0.1, _to_float(pml_cfg.get("deltamax", 8.0), 8.0)),
        }
    ignored_step_names = set(pml_entry_names)
    if pml_setup is not None:
        ignored_step_names.add(air_volume_name)
    for ignored_name in ignored_step_names:
        object_mesh_sizes.pop(ignored_name, None)
    valid_boundary_names = exported_object_names | port_surface_names | plate_names
    local_mesh_refinements = []
    valid_face_selectors = {"-x", "+x", "-y", "+y", "-z", "+z"}
    for item in raw_local_refinements:
        if not isinstance(item, dict) or not _to_bool(item.get("enabled", True)):
            continue
        object_name = str(item.get("object", "")).strip()
        mode = str(item.get("mode", "boundary")).strip().lower()
        if object_name not in valid_boundary_names or mode not in {"boundary", "face"}:
            continue
        if object_name in pml_entry_names:
            continue
        faces = [
            str(face).strip().lower()
            for face in item.get("faces", [])
            if str(face).strip().lower() in valid_face_selectors
        ] if isinstance(item.get("faces", []), list) else []
        if mode == "boundary" and not faces:
            continue
        size_m = max(1e-9, _to_float(item.get("size_mm", 0.25 if mode == "boundary" else 0.1), 0.25)) * coordinate_scale
        growth_rate = max(1.001, _to_float(item.get("growth_rate", 3.0), 3.0))
        raw_max_size = item.get("max_size_mm")
        max_size_m = None
        if raw_max_size not in (None, "", 0, 0.0):
            max_size_m = max(1e-9, _to_float(raw_max_size, 0.0) * coordinate_scale)
        local_mesh_refinements.append({
            "object": object_name,
            "mode": mode,
            "faces": faces,
            "size_m": size_m,
            "growth_rate": growth_rate,
            "max_size_m": max_size_m,
        })
    object_materials = {
        str(entry.get("object_name", "")).strip(): str(entry.get("material", "PEC"))
        for entry in step_entries
        if str(entry.get("object_name", "")).strip()
    }
    object_materials.update({
        str(plate.get("object_name", "")).strip(): str(plate.get("material", "PEC"))
        for plate in plates
        if str(plate.get("object_name", "")).strip()
    })
    boundary_assignments = []
    for item in object_boundaries:
        if not isinstance(item, dict):
            continue
        object_name = str(item.get("object", "")).strip()
        if (
            object_name not in valid_boundary_names
            or object_name in port_surface_names
            or object_name == air_volume_name
        ):
            continue
        params = item.get("params", {})
        boundary_assignments.append({
            "object": object_name,
            "type": str(item.get("type", "")).strip(),
            "params": dict(params) if isinstance(params, dict) else {},
            "material": object_materials.get(object_name, "PEC"),
        })
    eff_pardiso_threads = max(1, int(pardiso_threads if parallel_enabled else 1))
    eff_acc_threads = max(1, int(acc_threads if parallel_enabled else 1))
    used_materials = sorted({
        str(e.get("material", "PEC"))
        for e in [*step_entries, *plates]
        if str(e.get("material", "PEC")).strip().upper() != "PML"
    })
    if ports and "PEC" not in used_materials:
        used_materials.append("PEC")
        used_materials.sort()
    detected_emerge_version = _detect_emerge_version()
    output_configs = []
    raw_outputs = settings.get("outputs", []) if isinstance(settings, dict) else []
    for output in raw_outputs if isinstance(raw_outputs, list) else []:
        if not isinstance(output, dict) or not bool(output.get("enabled", True)):
            continue
        output_simulation = str(output.get("simulation", "")).strip()
        if output_simulation and output_simulation != sim_name:
            continue
        plot_type = str(output.get("plot_type", "")).strip()
        params = output.get("params", {}) if isinstance(output.get("params", {}), dict) else {}
        output_config = {
            "name": str(output.get("name", "Output")).strip() or "Output",
            "plot_type": plot_type,
            "s_parameter": str(params.get("s_parameter", "S11")).strip().upper() or "S11",
            "s_parameters": [
                str(value).strip().upper()
                for value in params.get("s_parameters", [])
                if str(value).strip()
            ] if isinstance(params.get("s_parameters", []), list) else [],
            "port_i": max(1, _to_int(params.get("port_i", 1), 1)),
            "port_j": max(1, _to_int(params.get("port_j", 1), 1)),
        }
        if plot_type in {"plot_sp", "plot_vswr", "smith", "plot"}:
            output_configs.append(output_config)
        elif plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}:
            output_configs.append(output_config)

    lines: List[str] = [
        f"# EM 3D Modeler version: {_em3d_modeler_version}",
        "# Auto-generated EMERGE Python script from EM 3D Modeler",
        f"# Simulation: {sim_name}",
        "",
        "# =============================================================================",
        "# [1] IMPORTS AND RUNTIME COMPATIBILITY",
        "# =============================================================================",
        "from datetime import datetime",
        "import os",
        "import re",
        "import math",
        "import json",
        "import numpy as np",
        "",
        "try:",
        "    import importlib.metadata as _importlib_metadata",
        "except Exception:",
        "    _importlib_metadata = None",
        "",
        f"TARGET_EMERGE_VERSION = {_q(detected_emerge_version)}",
        "try:",
        "    if _importlib_metadata is not None:",
        "        try:",
        "            TARGET_EMERGE_VERSION = str(_importlib_metadata.version('emerge'))",
        "        except Exception:",
        "            pass",
        "    if TARGET_EMERGE_VERSION == 'unknown':",
        "        try:",
        "            import emerge as em",
        "            TARGET_EMERGE_VERSION = str(getattr(em, '__version__', getattr(em, 'version', 'unknown')))",
        "        except Exception:",
        "            pass",
        "except Exception:",
        "    pass",
        "",
        "import emerge as em",
        "try:",
        "    RUNTIME_EMERGE_VERSION = str(_importlib_metadata.version('emerge')) if _importlib_metadata is not None else str(getattr(em, '__version__', 'unknown'))",
        "except Exception:",
        "    RUNTIME_EMERGE_VERSION = str(getattr(em, '__version__', 'unknown'))",
        "",
        "from emerge_config import config",
        f"config.set_pardiso_threads({eff_pardiso_threads})",
        f"config.set_acc_threads({eff_acc_threads})",
        "",
        "# =============================================================================",
        "# [2] JOB CONFIGURATION",
        "# =============================================================================",
        f"JOB_NAME = {_q(sim_name)}",
        f"JOB_TYPE = {_q(sim_type)}",
        f"PROJECT_NAME = {_q(project_name)}",
        "SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))",
        "RESULTS_DIR = SCRIPT_DIR",
        "os.chdir(SCRIPT_DIR)",
        "print(f'[job] Results directory: {RESULTS_DIR}')",
        "",
        "mm = 0.001",
        f"GEOMETRY_SCALE_FACTOR = {emerge_scale_factor!r}",
        f"FMIN_GHZ = {fmin}",
        f"FMAX_GHZ = {fmax}",
        f"FSTEP_GHZ = {fstep}",
        f"NPOINTS = {npoints}",
        f"MESH_RESOLUTION = {mesh_resolution:.6f}",
        f"CURVED_BOUNDARY_RESOLUTION = {curved_boundary_resolution}",
        f"OBJECT_MESH_SIZES_M = {repr(object_mesh_sizes)}",
        f"EIGENMODE_COUNT = {mode_count}",
        f"PARAM_NAME = {_q(param_name)}",
        f"PARAM_VALUES = {_q(param_values)}",
        f"PLOT_SPARAMS_AFTER_SIM = {bool(plot_sparams_after_sim)}",
        f"PROGRESSIVE_SPARAMS_ENABLED = {bool(progressive_sparams_enabled)}",
        f"PROGRESSIVE_SPARAMS_CHUNK_SIZE = {progressive_sparams_chunk_size}",
        f"PROGRESSIVE_SPARAMS_HAVE_PORTS = {bool(ports)}",
        f"EXPORT_SPARAMS_AFTER_SIM = {bool(export_sparams_after_sim)}",
        f"SPARAM_FIT_ENABLED = {fit_enabled}",
        f"SPARAM_FIT_POINTS = {fit_points}",
        f"OUTPUT_CONFIGS = {repr(output_configs)}",
        "if PROGRESSIVE_SPARAMS_ENABLED and PROGRESSIVE_SPARAMS_HAVE_PORTS and JOB_TYPE in ('sweep', 'parametric') and RUNTIME_EMERGE_VERSION != '3.0.0a16':",
        "    raise RuntimeError(f'Progressive S-parameter plotting requires EMERGE 3.0.0a16 for verified mesh and result reuse; found {RUNTIME_EMERGE_VERSION}.')",
        "SAVE_FARFIELDS = any(output.get('plot_type') in ('plot_ff', 'plot_ff_polar', 'plot_ff_3d') for output in OUTPUT_CONFIGS)",
        "print(f\"[job] {JOB_NAME} | type={JOB_TYPE} | range={FMIN_GHZ}..{FMAX_GHZ} GHz step {FSTEP_GHZ}\")",
        "",
        "# =============================================================================",
        "# [3] EMERGE SETUP",
        "# =============================================================================",
        f"simulationObj = em.Simulation(PROJECT_NAME, loglevel={_q(str(sim.get('LogVerbosity', 'Info')).strip().upper())}, save_file=True, write_log=True)",
        "if GEOMETRY_SCALE_FACTOR != 1.0:",
        "    simulationObj.set_scale_factor(GEOMETRY_SCALE_FACTOR)",
        f"simulationObj.set_solver(em.EMSolver.{str(solver).strip().upper()})",
        "if JOB_TYPE == 'parametric' and PARAM_NAME:",
        "    _initial_parametric_values = [value.strip() for value in PARAM_VALUES.split(',') if value.strip()]",
        "    if len(_initial_parametric_values) == 1:",
        "        try:",
        "            _initial_parametric_value = float(_initial_parametric_values[0])",
        "        except ValueError:",
        "            _initial_parametric_value = _initial_parametric_values[0]",
        "        setattr(simulationObj, PARAM_NAME, _initial_parametric_value)",
        "",
        "def _safe_token(v: str) -> str:",
        "    t = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '_' for ch in str(v))",
        "    t = t.strip('_')",
        "    return t or 'simulation'",
        "",
        "class _GeneratedGeometryGroup:",
        "    def __init__(self, objects):",
        "        self.objects = list(objects)",
        "",
        "    def as_volume(self):",
        "        if not self.objects:",
        "            raise RuntimeError('Generated geometry group is empty')",
        "        return self.objects[0]",
        "",
        "def _select_single_port_face(volume, face_name, origin, u, v):",
        "    candidates = list(volume.face(face_name).tags)",
        "    if len(candidates) == 1:",
        "        return em.FaceSelection([int(candidates[0])])",
        "    target_center = tuple(origin[i] + 0.5 * u[i] + 0.5 * v[i] for i in range(3))",
        "    target_sizes = (math.sqrt(sum(value * value for value in u)), math.sqrt(sum(value * value for value in v)))",
        "    best = None",
        "    best_score = float('inf')",
        "    for tag in candidates:",
        "        selection = em.FaceSelection([int(tag)])",
        "        points = selection.points",
        "        if points is None or len(points) == 0:",
        "            continue",
        "        bounds_min = [min(float(point[i]) for point in points) for i in range(3)]",
        "        bounds_max = [max(float(point[i]) for point in points) for i in range(3)]",
        "        center = tuple(0.5 * (bounds_min[i] + bounds_max[i]) for i in range(3))",
        "        spans = sorted((bounds_max[i] - bounds_min[i] for i in range(3)), reverse=True)",
        "        size_error = (spans[0] - max(target_sizes)) ** 2 + (spans[1] - min(target_sizes)) ** 2",
        "        center_error = sum((center[i] - target_center[i]) ** 2 for i in range(3))",
        "        score = center_error + size_error",
        "        if score < best_score:",
        "            best_score = score",
        "            best = selection",
        "    if best is None:",
        "        raise ValueError(f'Unable to resolve single face for port on {face_name}')",
        "    return best",
        "",
        "def _number_of_ports(grid):",
        "    smat = grid.Smat",
        "    return int(smat.shape[1]) if len(smat.shape) >= 3 else 1",
        "",
        "def _selected_s_parameter(output, grid):",
        "    match = re.fullmatch(r'S(\\d+)[,:/_-]?(\\d+)', str(output.get('s_parameter', 'S11')).upper())",
        "    if match:",
        "        output_port, input_port = int(match.group(1)), int(match.group(2))",
        "    else:",
        "        output_port = int(output.get('port_i', 1))",
        "        input_port = int(output.get('port_j', 1))",
        "    nports = _number_of_ports(grid)",
        "    if output_port < 1 or input_port < 1 or output_port > nports or input_port > nports:",
        "        return 1, 1",
        "    return output_port, input_port",
        "",
        "def _selected_s_parameters(output, grid):",
        "    selected = []",
        "    for value in output.get('s_parameters', []):",
        "        match = re.fullmatch(r'S(\\d+)[,:/_-]?(\\d+)', str(value).upper())",
        "        if match:",
        "            selected.append((int(match.group(1)), int(match.group(2)), str(value).upper()))",
        "    if not selected:",
        "        output_port, input_port = _selected_s_parameter(output, grid)",
        "        selected = [(output_port, input_port, f'S{output_port}{input_port}')]",
        "    nports = _number_of_ports(grid)",
        "    return [(output_port, input_port, label) for output_port, input_port, label in selected if 1 <= output_port <= nports and 1 <= input_port <= nports] or [(1, 1, 'S11')]",
        "",
        "def _validate_progressive_samples(expected_frequencies, expected_count):",
        "    scalar = simulationObj.mw.data.scalar",
        "    variables = list(scalar._variables)",
        "    entries = list(scalar._data_entries)",
        "    if len(variables) != len(entries) or len(entries) != expected_count:",
        "        raise RuntimeError(f'Progressive sweep data count mismatch: expected {expected_count}, got {len(entries)} entries and {len(variables)} variable records.')",
        "    recorded = []",
        "    for variable, entry in zip(variables, entries):",
        "        if 'freq' not in variable or entry.freq is None:",
        "            raise RuntimeError('Progressive sweep returned a sample without a frequency.')",
        "        variable_frequency = float(variable['freq'])",
        "        entry_frequency = float(entry.freq)",
        "        if not math.isfinite(variable_frequency) or not math.isfinite(entry_frequency) or not math.isclose(variable_frequency, entry_frequency, rel_tol=1e-12, abs_tol=1e-3):",
        "            raise RuntimeError('Progressive sweep frequency metadata does not match its S-parameter entry.')",
        "        recorded.append(variable_frequency)",
        "    recorded = np.asarray(recorded, dtype=float)",
        "    expected = np.asarray(expected_frequencies[:expected_count], dtype=float)",
        "    if recorded.size != expected_count or np.unique(recorded).size != expected_count:",
        "        raise RuntimeError('Progressive sweep contains duplicate or missing frequency samples.')",
        "    if not np.allclose(recorded, expected, rtol=1e-12, atol=1e-3):",
        "        raise RuntimeError('Progressive sweep frequencies are missing, reordered, or outside the configured grid.')",
        "    return variables, entries",
        "",
        "def _emit_progressive_sparams(expected_frequencies, start, end):",
        "    variables, entries = _validate_progressive_samples(expected_frequencies, end)",
        "    chunk_frequencies = []",
        "    chunk_matrices = []",
        "    expected_shape = None",
        "    for variable, entry in zip(variables[start:end], entries[start:end]):",
        "        matrix = np.asarray(entry.Sp, dtype=np.complex128)",
        "        if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[0] != matrix.shape[1]:",
        "            raise RuntimeError('Progressive sweep returned an invalid S-parameter matrix shape.')",
        "        if expected_shape is None:",
        "            expected_shape = matrix.shape",
        "        if matrix.shape != expected_shape or not np.isfinite(matrix.real).all() or not np.isfinite(matrix.imag).all():",
        "            raise RuntimeError('Progressive sweep returned inconsistent or non-finite S-parameter values.')",
        "        chunk_frequencies.append(float(variable['freq']))",
        "        chunk_matrices.append([[[float(value.real), float(value.imag)] for value in row] for row in matrix])",
        "    event = {",
        "        'simulation': JOB_NAME,",
        "        'frequencies': chunk_frequencies,",
        "        's_matrices': chunk_matrices,",
        "        'completed_samples': end,",
        "        'expected_samples': len(expected_frequencies),",
        "        'complete': end == len(expected_frequencies),",
        "    }",
        "    print('EM3D_SPARAM_PROGRESS:' + json.dumps(event, separators=(',', ':')), flush=True)",
        "",
        "def _run_progressive_sweep():",
        "    if RUNTIME_EMERGE_VERSION != '3.0.0a16':",
        "        raise RuntimeError(f'Progressive S-parameter plotting requires EMERGE 3.0.0a16 for verified mesh and result reuse; found {RUNTIME_EMERGE_VERSION}.')",
        "    expected_frequencies = np.linspace(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS).tolist()",
        "    simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
        "    simulation_result = None",
        "    for start in range(0, NPOINTS, PROGRESSIVE_SPARAMS_CHUNK_SIZE):",
        "        end = min(start + PROGRESSIVE_SPARAMS_CHUNK_SIZE, NPOINTS)",
        "        simulationObj.mw.set_frequency(expected_frequencies[start:end])",
        "        simulation_result = simulationObj.mw.run_sweep()",
        "        if simulation_result is not simulationObj.mw.data:",
        "            raise RuntimeError('This EMERGE version did not return the accumulated simulation dataset.')",
        "        _emit_progressive_sparams(expected_frequencies, start, end)",
        "        print(f'[job] Progressive S-parameter sweep: {end}/{NPOINTS} samples', flush=True)",
        "    simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
        "    _validate_progressive_samples(expected_frequencies, NPOINTS)",
        "    return simulation_result",
        "",
        "def _emit_progressive_parametric_sparams(sim_result, parameter_value, completed, total, start, end, sample_offset):",
        "    if RUNTIME_EMERGE_VERSION != '3.0.0a16':",
        "        raise RuntimeError(f'Progressive S-parameter plotting requires EMERGE 3.0.0a16 for verified mesh and result reuse; found {RUNTIME_EMERGE_VERSION}.')",
        "    scalar = sim_result.scalar",
        "    variables = list(scalar._variables)",
        "    entries = list(scalar._data_entries)",
        "    chunk_count = end - start",
        "    if len(variables) != len(entries) or chunk_count < 1:",
        "        raise RuntimeError('Parametric sweep returned invalid S-parameter sample data.')",
        "    samples = list(zip(variables[sample_offset + start:sample_offset + end], entries[sample_offset + start:sample_offset + end]))",
        "    if len(samples) != chunk_count:",
        "        samples = list(zip(variables[-chunk_count:], entries[-chunk_count:]))",
        "    if len(samples) != chunk_count:",
        "        raise RuntimeError(f'Parametric sweep returned fewer than {chunk_count} S-parameter samples in the current chunk.')",
        "    samples.sort(key=lambda sample: float(sample[1].freq))",
        "    expected_frequencies = np.linspace(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)[start:end]",
        "    frequencies = [float(entry.freq) for _, entry in samples]",
        "    if not np.allclose(frequencies, expected_frequencies, rtol=1e-12, atol=1e-3):",
        "        raise RuntimeError('Parametric sweep chunk frequencies are missing, duplicated, or outside the configured grid.')",
        "    matrices = []",
        "    expected_shape = None",
        "    for _, entry in samples:",
        "        matrix = np.asarray(entry.Sp, dtype=np.complex128)",
        "        if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[0] != matrix.shape[1]:",
        "            raise RuntimeError('Parametric sweep returned an invalid S-parameter matrix shape.')",
        "        if expected_shape is None:",
        "            expected_shape = matrix.shape",
        "        matrix_is_finite = bool(np.isfinite(matrix.real).all() and np.isfinite(matrix.imag).all())",
        "        if matrix.shape != expected_shape or not matrix_is_finite:",
        "            raise RuntimeError(f'Parametric sweep returned inconsistent or non-finite S-parameter values: expected shape {expected_shape}, got {matrix.shape}, finite={matrix_is_finite}.')",
        "        matrices.append([[[float(value.real), float(value.imag)] for value in row] for row in matrix])",
        "    event = {",
        "        'simulation': JOB_NAME,",
        "        'frequencies': frequencies,",
        "        's_matrices': matrices,",
        "        'chunk_start': start,",
        "        'completed_samples': end,",
        "        'expected_samples': NPOINTS,",
        "        'complete': end == NPOINTS,",
        "        'parameter_name': PARAM_NAME,",
        "        'parameter_value': str(parameter_value),",
        "        'parameter_index': completed,",
        "        'completed_parameters': completed,",
        "        'expected_parameters': total,",
        "    }",
        "    print('EM3D_SPARAM_PROGRESS:' + json.dumps(event, separators=(',', ':')), flush=True)",
        "",
        "def _boundary_faces(geometry_group):",
        "    geometry_objects = list(geometry_group.objects)",
        "    if not geometry_objects:",
        "        raise RuntimeError(f'Geometry group has no boundary objects: {geometry_group}')",
        "    faces = geometry_objects[0].boundary()",
        "    for geometry_object in geometry_objects[1:]:",
        "        faces = faces + geometry_object.boundary()",
        "    return faces",
        "",
        "def _write_sputility_touchstone(grid, fit_result=None, suffix=''):",
        "    nports = _number_of_ports(grid)",
        "    extension = f'.s{nports}p'",
        "    timestamp = os.environ.get('EM3D_RUN_ID') or globals().setdefault('_TOUCHSTONE_RUN_TIMESTAMP', datetime.now().strftime('%Y%m%d-%H%M%S'))",
        "    touchstone_dir = os.path.join(SCRIPT_DIR, 'Touchstone')",
        "    os.makedirs(touchstone_dir, exist_ok=True)",
        "    suffix_token = f'_{_safe_token(suffix)}' if suffix else ''",
        "    output_base = os.path.join(touchstone_dir, f'{_safe_token(PROJECT_NAME)}_{_safe_token(JOB_NAME)}{suffix_token}_{timestamp}')",
        "    def _write_touchstone(output_path, frequencies, curves):",
        "        with open(output_path, 'w', encoding='ascii', newline='\\n') as touchstone_file:",
        "            touchstone_file.write('# HZ S RI R 50.0\\n')",
        "            for sample_index, frequency in enumerate(frequencies):",
        "                row = [f'{float(frequency):.16g}']",
        "                for input_port in range(1, nports + 1):",
        "                    for output_port in range(1, nports + 1):",
        "                        value = complex(curves[(output_port, input_port)][sample_index])",
        "                        row.extend((f'{value.real:.16g}', f'{value.imag:.16g}'))",
        "                touchstone_file.write(' '.join(row) + '\\n')",
        "    original_frequencies = grid.freq",
        "    original_curves = {(i, j): grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)}",
        "    output_path = output_base + ('_fit' if SPARAM_FIT_ENABLED else '') + extension",
        "    if SPARAM_FIT_ENABLED:",
        "        original_path = output_base + '_original' + extension",
        "        _write_touchstone(original_path, original_frequencies, original_curves)",
        "        print(f'[job] Original SPUtility Touchstone exported: {original_path}')",
        "        if fit_result is not None:",
        "            fit_result['attempted'] = True",
        "        try:",
        "            fit_frequencies = grid.dense_f(SPARAM_FIT_POINTS)",
        "            fit_curves = {(i, j): grid.model_S(i, j, fit_frequencies) for j in range(1, nports + 1) for i in range(1, nports + 1)}",
        "            _write_touchstone(output_path, fit_frequencies, fit_curves)",
        "        except Exception as error:",
        "            if fit_result is not None:",
        "                fit_result['error'] = error",
        "            print(f'[warning] S-parameter fitting failed; original samples were saved to {original_path}. No fitted Touchstone file was created: {error}')",
        "            return output_path",
        "        if fit_result is not None:",
        "            fit_result['frequencies'] = fit_frequencies",
        "            fit_result['curves'] = fit_curves",
        "        print(f'[job] Fitted SPUtility Touchstone exported: {output_path}')",
        "    else:",
        "        _write_touchstone(output_path, original_frequencies, original_curves)",
        "        print(f'[job] SPUtility Touchstone exported: {output_path}')",
        "    return output_path",
        "",
        "def _parametric_result_grids(sim_result):",
        "    scalar = sim_result.scalar",
        "    variables = list(scalar._variables)",
        "    entries = list(scalar._data_entries)",
        "    values = [value.strip() for value in PARAM_VALUES.split(',') if value.strip()] or ['default']",
        "    if len(variables) != len(entries) or len(entries) < NPOINTS:",
        "        raise ValueError('parametric S-parameter samples are incomplete')",
        "    groups = []",
        "    if any(PARAM_NAME in variable for variable in variables):",
        "        for value in values:",
        "            try:",
        "                numeric_value = float(value)",
        "                selected = [(variable, entry) for variable, entry in zip(variables, entries) if PARAM_NAME in variable and math.isclose(float(variable[PARAM_NAME]), numeric_value, rel_tol=1e-12, abs_tol=1e-12)]",
        "            except (TypeError, ValueError):",
        "                selected = [(variable, entry) for variable, entry in zip(variables, entries) if str(variable.get(PARAM_NAME, '')).strip() == value]",
        "            if selected:",
        "                groups.append((value, selected))",
        "    if not groups:",
        "        if len(entries) == len(values) * NPOINTS:",
        "            groups = [(value, list(zip(variables[index * NPOINTS:(index + 1) * NPOINTS], entries[index * NPOINTS:(index + 1) * NPOINTS]))) for index, value in enumerate(values)]",
        "        elif len(entries) == NPOINTS:",
        "            groups = [(values[-1], list(zip(variables, entries)))]",
        "        else:",
        "            raise ValueError(f'expected complete parameter sweeps, found {len(entries)} samples for {len(values)} values')",
        "    expected_frequencies = np.linspace(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
        "    class _SampleGrid:",
        "        def __init__(self, frequencies, matrices):",
        "            self.freq = np.asarray(frequencies, dtype=float)",
        "            self.Smat = np.asarray(matrices, dtype=np.complex128)",
        "        def S(self, output_port, input_port):",
        "            return self.Smat[:, output_port - 1, input_port - 1]",
        "    grids = []",
        "    for value, samples in groups:",
        "        if len(samples) != NPOINTS:",
        "            raise ValueError(f'parameter {value} has {len(samples)} samples; expected {NPOINTS}')",
        "        samples.sort(key=lambda sample: float(sample[1].freq))",
        "        frequencies = [float(entry.freq) for _, entry in samples]",
        "        if not np.allclose(frequencies, expected_frequencies, rtol=1e-12, atol=1e-3):",
        "            raise ValueError(f'parameter {value} has missing, duplicated, or out-of-range frequency samples')",
        "        matrices = [np.asarray(entry.Sp, dtype=np.complex128) for _, entry in samples]",
        "        if any(matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[0] != matrix.shape[1] for matrix in matrices):",
        "            raise ValueError(f'parameter {value} has an invalid S-parameter matrix')",
        "        if any(not np.isfinite(matrix.real).all() or not np.isfinite(matrix.imag).all() for matrix in matrices):",
        "            raise ValueError(f'parameter {value} has non-finite S-parameter values')",
        "        grids.append((value, _SampleGrid(frequencies, matrices)))",
        "    return grids",
        "",
        "def _postprocess_sparams(sim_obj, sim_result):",
        "    if JOB_TYPE not in ('sweep', 'parametric'):",
        "        return",
        "    out_base = os.path.join(SCRIPT_DIR, f\"{_safe_token(PROJECT_NAME)}_{_safe_token(JOB_NAME)}\")",
        "    fit_result = {'attempted': False}",
        "",
        "    if JOB_TYPE == 'parametric':",
        "        try:",
        "            parametric_grids = _parametric_result_grids(sim_result)",
        "        except (TypeError, ValueError, KeyError, AttributeError) as extraction_error:",
        "            print(f'[warning] Parametric results could not be split into complete frequency sweeps; automatic Touchstone export and plotting were skipped: {extraction_error}')",
        "            return",
        "        for parameter_value, parameter_grid in parametric_grids:",
        "            parameter_fit_result = {'attempted': False}",
        "            if EXPORT_SPARAMS_AFTER_SIM:",
        "                suffix = f'{PARAM_NAME}_{parameter_value}' if PARAM_NAME else str(parameter_value)",
        "                _write_sputility_touchstone(parameter_grid, parameter_fit_result, suffix)",
        "            if PLOT_SPARAMS_AFTER_SIM and os.environ.get('EM3D_RUN_IN_APP') != '1':",
        "                from emerge.plot import plot_sp",
        "                nports = _number_of_ports(parameter_grid)",
        "                curves = [parameter_grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "                labels = [f'S{i}{j} {PARAM_NAME}={parameter_value}' for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "                plot_sp(parameter_grid.freq, curves, labels=labels)",
        "            if OUTPUT_CONFIGS and os.environ.get('EM3D_RUN_IN_APP') != '1':",
        "                from emerge.plot import plot_sp, plot_vswr, smith, plot",
        "                for output in OUTPUT_CONFIGS:",
        "                    kind = output.get('plot_type')",
        "                    selected = _selected_s_parameters(output, parameter_grid)",
        "                    curves = [parameter_grid.S(i, j) for i, j, _ in selected]",
        "                    labels = [label for _, _, label in selected]",
        "                    if kind == 'plot_sp':",
        "                        plot_sp(parameter_grid.freq, curves, labels=labels)",
        "                    elif kind == 'plot_vswr':",
        "                        plot_vswr(parameter_grid.freq, curves, labels=[f'VSWR{label[1:]}' for label in labels])",
        "                    elif kind == 'smith':",
        "                        smith(curves, f=parameter_grid.freq, labels=labels)",
        "                    elif kind == 'plot':",
        "                        magnitude_curves = [20.0 * np.log10(np.maximum(np.abs(curve), 1e-12)) for curve in curves]",
        "                        plot(parameter_grid.freq, magnitude_curves, labels=[f'|{label}| dB' for label in labels], xlabel='Frequency (Hz)', ylabel='Magnitude (dB)')",
        "                    print(f\"[job] Output plotted: {output.get('name', 'Output')} ({kind}) for {PARAM_NAME}={parameter_value}\")",
        "        print(f'[info] Post-processed {len(parametric_grids)} complete parametric S-parameter sweeps.')",
        "        return",
        "",
        "    try:",
        "        grid = sim_result.scalar.grid",
        "    except ValueError:",
        "        raise",
        "",
        "    if EXPORT_SPARAMS_AFTER_SIM:",
        "        _write_sputility_touchstone(grid, fit_result)",
        "",
        "    if PLOT_SPARAMS_AFTER_SIM and os.environ.get('EM3D_RUN_IN_APP') != '1':",
        "        from emerge.plot import plot_sp",
        "        nports = _number_of_ports(grid)",
        "        if SPARAM_FIT_ENABLED:",
        "            if not fit_result['attempted']:",
        "                fit_result['attempted'] = True",
        "                try:",
        "                    fit_result['frequencies'] = grid.dense_f(SPARAM_FIT_POINTS)",
        "                    fit_result['curves'] = {(i, j): grid.model_S(i, j, fit_result['frequencies']) for i in range(1, nports + 1) for j in range(1, nports + 1)}",
        "                except Exception as error:",
        "                    fit_result['error'] = error",
        "                    print(f'[warning] S-parameter fitting failed for automatic plotting; original samples will be used: {error}')",
        "            if fit_result.get('curves') is not None:",
        "                frequencies = fit_result['frequencies']",
        "                curves = [fit_result['curves'][(i, j)] for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "            else:",
        "                frequencies = grid.freq",
        "                curves = [grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        else:",
        "            frequencies = grid.freq",
        "            curves = [grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        labels = [f'S{i}{j}' for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        plot_sp(frequencies, curves, labels=labels)",
        "        print('[job] S-parameters plotted')",
        "",
        "    if OUTPUT_CONFIGS and os.environ.get('EM3D_RUN_IN_APP') != '1':",
        "        from emerge.plot import plot_sp, plot_vswr, smith, plot",
        "        nports = _number_of_ports(grid)",
        "        frequencies = grid.freq",
        "        for output in OUTPUT_CONFIGS:",
        "            kind = output.get('plot_type')",
        "            name = str(output.get('name', 'Output'))",
        "            selected = _selected_s_parameters(output, grid)",
        "            curves = [grid.S(output_port, input_port) for output_port, input_port, _ in selected]",
        "            labels = [label for _, _, label in selected]",
        "            if kind == 'plot_sp':",
        "                plot_sp(frequencies, curves, labels=labels)",
        "            elif kind == 'plot_vswr':",
        "                plot_vswr(frequencies, curves, labels=[f'VSWR{label[1:]}' for label in labels])",
        "            elif kind == 'smith':",
        "                smith(curves, f=frequencies, labels=labels)",
        "            elif kind == 'plot':",
        "                import numpy as np",
        "                magnitude_curves = [20.0 * np.log10(np.maximum(np.abs(curve), 1e-12)) for curve in curves]",
        "                plot(frequencies, magnitude_curves, labels=[f'|{label}| dB' for label in labels], xlabel='Frequency (Hz)', ylabel='Magnitude (dB)')",
        "            print(f'[job] Output plotted: {name} ({kind})')",
        "",
    ]

    lines += [
        "# =============================================================================",
        "# [4] MATERIALS",
        "# =============================================================================",
        "",
    ]
    lines += _material_block(used_materials, materials_catalog)

    lines += [
        "# =============================================================================",
        "# [5] GEOMETRY: ONE STEP FILE PER OBJECT",
        "# =============================================================================",
        "geometry_groups = {}",
        "plate_objects = {}",
        "",
    ]
    if pml_setup is not None:
        air_bounds = pml_setup["air_bounds_mm"]
        air_width = (air_bounds[1] - air_bounds[0]) * coordinate_scale
        air_depth = (air_bounds[3] - air_bounds[2]) * coordinate_scale
        air_height = (air_bounds[5] - air_bounds[4]) * coordinate_scale
        air_position = (air_bounds[0] * coordinate_scale, air_bounds[2] * coordinate_scale, air_bounds[4] * coordinate_scale)
        lines += [
            "# Native EMerge PML volumes; the visual AIR/PML STEP boxes are not imported.",
            "_pml_geometry = em.geo.pmlbox(",
            f"    width={air_width!r},",
            f"    depth={air_depth!r},",
            f"    height={air_height!r},",
            f"    position={air_position!r},",
            f"    material=materials[{_q(str(next((entry.get('material', 'AIR') for entry in step_entries if str(entry.get('object_name', '')).strip() == air_volume_name), 'AIR')))}],",
            f"    thickness={pml_setup['thickness_m'] * emerge_scale_factor!r},",
            f"    Nlayers={pml_setup['layers']},",
            f"    N_mesh_layers={pml_setup['mesh_layers']},",
            f"    exponent={pml_setup['exponent']!r},",
            f"    deltamax={pml_setup['deltamax']!r},",
            f"    sides={_q(pml_setup['sides'])},",
            ")",
            f"geometry_groups[{_q(air_volume_name)}] = _GeneratedGeometryGroup([_pml_geometry[0]])",
            f"geometry_groups[{_q(pml_name)}] = _GeneratedGeometryGroup(_pml_geometry[1:])",
            "",
        ]

    if step_entries:
        for entry in step_entries:
            obj_name = str(entry.get("object_name", "Object"))
            if obj_name in ignored_step_names:
                continue
            step_file = str(entry.get("step_file", ""))
            material = str(entry.get("material", "PEC"))
            priority = int(entry.get("priority", 5000))

            lines += [
                f"geometry_group = em.geo.step.STEPItems(name={_q(obj_name)}, filename=os.path.join(SCRIPT_DIR, {_q(step_file)}), unit=mm * GEOMETRY_SCALE_FACTOR)",
                f"geometry_groups[{_q(obj_name)}] = geometry_group",
                "for geometry_obj in geometry_group.objects:",
                f"    geometry_obj.prio_set({priority})",
                f"    geometry_obj.set_material(materials[{_q(material)}])",
                "",
            ]
    else:
        lines += [
            "print('No STEP entries generated from current scene.')",
            "",
        ]
    if plates:
        lines += [
            "# =============================================================================",
            "# [5b] PLANAR PLATES (DIRECT EMERGE GEOMETRY)",
            "# =============================================================================",
        ]
        for plate in plates:
            plate_name = str(plate.get("object_name", "Plate"))
            origin = tuple(float(value) * scene_coordinate_scale for value in plate.get("origin", (0.0, 0.0, 0.0)))
            u = tuple(float(value) * scene_coordinate_scale for value in plate.get("u", (0.0, 0.0, 0.0)))
            v = tuple(float(value) * scene_coordinate_scale for value in plate.get("v", (0.0, 0.0, 0.0)))
            lines += [
                f"plate_objects[{_q(plate_name)}] = em.geo.Plate(name={_q(plate_name)}, origin={origin!r}, u={u!r}, v={v!r})",
                f"plate_objects[{_q(plate_name)}].set_material(materials[{_q(str(plate.get('material', 'PEC')))}])",
                f"geometry_groups[{_q(plate_name)}] = plate_objects[{_q(plate_name)}]",
                "",
            ]
    if ports:
        lines += [
            "# =============================================================================",
            "# [6] LUMPED PORTS",
            "# =============================================================================",
            "# Create every port Plate first; LumpedPort is assigned after geometry commit.",
            "port = {}",
            "port_surfaces = {}",
            "",
        ]
        for p in ports:
            idx = int(p.get("index", 1))
            name = str(p.get("name", f"Port_{idx}"))
            port_type = str(p.get("type", "LumpedPort"))
            origin = [_to_float(v, 0.0) * scene_coordinate_scale for v in p.get("origin", [0.0, 0.0, 0.0])]
            u = [_to_float(v, 0.0) * scene_coordinate_scale for v in p.get("u", [0.0, 0.0, 0.0])]
            v = [_to_float(v, 0.0) * scene_coordinate_scale for v in p.get("v", [0.0, 0.0, 0.0])]
            port_dimension_scale = mm_per_scene_unit * 0.001
            width = abs(_to_float(p.get("width", 0.0), 0.0) * port_dimension_scale)
            height = abs(_to_float(p.get("height", 0.0), 0.0) * port_dimension_scale)
            direction = [_to_float(vd, 0.0) for vd in p.get("direction", [0.0, 0.0, 1.0])]
            z0 = _to_float(p.get("z0", 50.0), 50.0)
            power = _to_float(p.get("power", 1.0), 1.0)

            lines += [
                f"port[{idx}] = {{}}",
                f"port[{idx}]['name'] = {_q(name)}",
                f"port[{idx}]['type'] = {_q(str(p.get('type', 'LumpedPort')))}",
                f"port[{idx}]['w'] = {width}",
                f"port[{idx}]['h'] = {height}",
                f"port[{idx}]['portR'] = {z0}",
                f"port[{idx}]['portDirection'] = ({direction[0]}, {direction[1]}, {direction[2]})",
                f"port[{idx}]['portExcitationAmplitude'] = {power}",
                f"port[{idx}]['mode'] = ({int((p.get('mode') or (1, 0))[0])}, {int((p.get('mode') or (1, 0))[1])})",
                f"port[{idx}]['modeType'] = {_q(str(p.get('mode_type', 'TE')))}",
                f"_port_{idx}_origin = ({_q(origin[0])}, {_q(origin[1])}, {_q(origin[2])})",
                f"_port_{idx}_u = ({_q(u[0])}, {_q(u[1])}, {_q(u[2])})",
                f"_port_{idx}_v = ({_q(v[0])}, {_q(v[1])}, {_q(v[2])})",
                "",
            ]
            if port_type == "WaveguidePort" and air_volume_name:
                face_name = str(p.get("face_name", "")).strip()
                if not face_name:
                    face_name = "+y" if float(origin[1]) > 0.0 else "-y"
                lines += [
                    f"_port_{idx}_volume = geometry_groups[{_q(air_volume_name)}].as_volume()",
                    f"_port_{idx}_face_name = {_q(face_name)}",
                    "",
                ]
            else:
                plate_key = str(p.get("plate_name", name))
                lines += [
                    f"port_surfaces[{_q(plate_key)}] = em.geo.Plate(",
                    f"    name={_q(name)},",
                    f"    origin=_port_{idx}_origin,",
                    f"    u=_port_{idx}_u,",
                    f"    v=_port_{idx}_v,",
                    ")",
                    f"port_surfaces[{_q(plate_key)}].set_material(materials['PEC'])",
                    f"geometry_groups[{_q(plate_key)}] = port_surfaces[{_q(plate_key)}]",
                    f"port[{idx}]['object'] = port_surfaces[{_q(plate_key)}]",
                    "",
                ]

    lines += [
        "# =============================================================================",
        "# [7] GEOMETRY COMMIT AND SIMULATION SETUP",
        "# =============================================================================",
    ]
    if preview_only:
        lines += [
            "# Preview raw exported geometry in Gmsh without commit, meshing or simulation.",
            "simulationObj.view(use_gmsh=True)",
            "print('[job] Geometry preview closed; no commit, mesh or simulation was run.')",
            "raise SystemExit(0)",
        ]
    else:
        lines += ["simulationObj.commit_geometry()"]
    for p in ports:
        idx = int(p.get("index", 1))
        if str(p.get("type", "LumpedPort")) == "WaveguidePort" and air_volume_name:
            lines += [
                f"port[{idx}]['object'] = _select_single_port_face(_port_{idx}_volume, _port_{idx}_face_name, _port_{idx}_origin, _port_{idx}_u, _port_{idx}_v)",
                "",
            ]
    lines += [
        "simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
        "simulationObj.mw.set_resolution(MESH_RESOLUTION)",
        "if SAVE_FARFIELDS:",
        "    simulationObj.mw.save_fields = ['E', 'H']",
        "for object_name, mesh_size in OBJECT_MESH_SIZES_M.items():",
        "    geometry_group = geometry_groups[object_name]",
        "    for geometry_obj in geometry_group.objects:",
        "        simulationObj.mesher.set_size(geometry_obj, mesh_size)",
        "# Avoid assigning a boolean to save_fields; EMERGE expects a list of field names or None.",
        "",
    ]

    if local_mesh_refinements:
        lines += [
            "# Local mesh refinements assigned in EM 3D Modeler.",
        ]
        for refinement_index, refinement in enumerate(local_mesh_refinements, start=1):
            object_name = refinement["object"]
            mode = refinement["mode"]
            faces = refinement["faces"]
            size_m = refinement["size_m"]
            growth_rate = refinement["growth_rate"]
            max_size_m = refinement["max_size_m"]
            target_var = f"_mesh_refinement_target_{refinement_index}"
            object_var = f"_mesh_refinement_object_{refinement_index}"
            lines.append(f"{target_var} = geometry_groups[{_q(object_name)}]")
            if mode == "face" and (not faces or object_name in port_surface_names or object_name in plate_names):
                lines.append(f"simulationObj.mesher.set_face_size({target_var}, size={size_m!r})")
                continue
            lines.append(f"for {object_var} in list(getattr({target_var}, 'objects', [{target_var}])):")
            for face_selector in faces:
                face_expr = f"{object_var}.face({_q(face_selector)})"
                if mode == "boundary":
                    max_size_arg = f", max_size={max_size_m!r}" if max_size_m is not None else ""
                    lines.append(
                        f"    simulationObj.mesher.set_boundary_size({face_expr}, size={size_m!r}, growth_rate={growth_rate!r}{max_size_arg})"
                    )
                else:
                    lines.append(f"    simulationObj.mesher.set_face_size({face_expr}, size={size_m!r})")
        lines.append("")

    if ports:
        lines += ["# Assign port excitations to the committed port Plates"]
        for p in ports:
            idx = int(p.get("index", 1))
            if str(p.get("type", "LumpedPort")) == "WaveguidePort":
                lines += [
                    f"port[{idx}]['bc'] = simulationObj.mw.bc.RectangularWaveguide(",
                    f"    port[{idx}]['object'],",
                    f"    {idx},",
                    f"    mode=port[{idx}]['mode'],",
                    f"    mode_type=port[{idx}]['modeType'],",
                    f"    power=port[{idx}]['portExcitationAmplitude'],",
                    ")",
                ]
            else:
                lines += [
                    f"port[{idx}]['bc'] = simulationObj.mw.bc.LumpedPort(",
                    f"    port[{idx}]['object'],",
                    f"    {idx},",
                    f"    width=port[{idx}]['w'],",
                    f"    height=port[{idx}]['h'],",
                    f"    direction=port[{idx}]['portDirection'],",
                    f"    Z0=port[{idx}]['portR'],",
                    f"    power=port[{idx}]['portExcitationAmplitude'],",
                    ")",
                ]
        lines += [
        "",
        ]

    if domain_boundaries:
        lines += [
            "# Assign global boundary settings to the configured outer faces of the open-region domain.",
            f"_open_region_objects = list(geometry_groups[{_q(air_volume_name)}].objects)",
            "if len(_open_region_objects) != 1:",
            f"    raise RuntimeError('Open-region domain {_q(air_volume_name)} must contain exactly one geometry object')",
            "_open_region_object = _open_region_objects[0]",
            "_open_region_face_boundary_types = {}",
            "def _resolve_open_region_boundary_face(face_selector, boundary_type):",
            "    try:",
            "        face_selection = _open_region_object.face(face_selector)",
            "    except ValueError as error:",
            "        available_faces = _open_region_object.all_faces()",
            "        if len(available_faces) != 1:",
            f"            raise RuntimeError('Open-region domain {_q(air_volume_name)} does not expose face ' + repr(face_selector) + '; cannot safely map the boundary') from error",
            "        face_selection = available_faces[0]",
            "        print('[warn] Open-region boundary ' + repr(face_selector) + ' mapped to the geometry object\\'s only face')",
            "    face_key = tuple(sorted(face_selection.tags))",
            "    existing_boundary_type = _open_region_face_boundary_types.get(face_key)",
            "    if existing_boundary_type is not None:",
            "        if existing_boundary_type != boundary_type:",
            "            raise RuntimeError('Open-region face has conflicting global boundaries: ' + existing_boundary_type + ' and ' + boundary_type)",
            "        return None",
            "    _open_region_face_boundary_types[face_key] = boundary_type",
            "    return face_selection",
            "_open_region_boundary_groups = {}",
        ]
        boundary_groups: List[str] = []
        for boundary_key, face_selector, boundary_type in domain_boundaries:
            if face_selector in waveguide_face_selectors:
                lines.append(
                    f"print({_q(f'[info] Global boundary {boundary_key} skipped because the face is used by a WaveguidePort')})"
                )
                continue
            if boundary_type in {"Open", "Radiation", "Pec", "Pmc"}:
                var_name = f"_open_face_{boundary_key.lower()}"
                group_type = "Absorbing" if boundary_type in {"Open", "Radiation"} else boundary_type
                lines.append(
                    f"{var_name} = _resolve_open_region_boundary_face({_q(face_selector)}, {_q(group_type)})"
                )
                lines.append(f"if {var_name} is not None:")
                lines.append(
                    f"    _open_region_boundary_groups.setdefault({_q(group_type)}, []).append({var_name})"
                )
                if group_type not in boundary_groups:
                    boundary_groups.append(group_type)
            elif boundary_type == "Pml" and pml_setup is not None:
                continue
            elif boundary_type == "Pml":
                lines.append(
                    f"print({_q(f'[warn] Global boundary {boundary_key}=PML ignored because no enabled PML shell is configured; default exterior PEC remains active')})"
                )
            elif boundary_type == "Periodic":
                lines.append(
                    f"print({_q(f'[warn] Global boundary {boundary_key}={boundary_type} is not a surface AbsorbingBoundary and was not exported')})"
                )
        for boundary_type in boundary_groups:
            group_expression = f"_open_region_boundary_groups[{_q(boundary_type)}]"
            lines += [
                f"if {group_expression}:",
                f"    _combined_open_region_faces = {group_expression}[0]",
                f"    for _additional_open_region_face in {group_expression}[1:]:",
                "        _combined_open_region_faces = _combined_open_region_faces + _additional_open_region_face",
            ]
            if boundary_type == "Absorbing":
                lines.append("    simulationObj.mw.bc.AbsorbingBoundary(_combined_open_region_faces)")
            elif boundary_type == "Pec":
                lines.append("    simulationObj.mw.bc.PEC(_combined_open_region_faces)")
            elif boundary_type == "Pmc":
                lines.append("    simulationObj.mw.bc.PMC(_combined_open_region_faces)")
        lines.append("")

    lines += [
        "# =============================================================================",
        "# [8] MESH GENERATION",
        "# =============================================================================",
        "simulationObj.mesher.set_curved_boundary_meshing(CURVED_BOUNDARY_RESOLUTION)",
    ]
    lumped_port_indices = [
        int(port.get("index", 1))
        for port in ports
        if str(port.get("type", "LumpedPort")) != "WaveguidePort"
    ]
    if lumped_port_indices:
        lines += [
            "_scaled_lumped_port_dimensions = []",
            "if GEOMETRY_SCALE_FACTOR != 1.0:",
        ]
        for port_index in lumped_port_indices:
            lines += [
                f"    _port_bc = port[{port_index}].get('bc')",
                "    if _port_bc is not None and hasattr(_port_bc, 'width') and hasattr(_port_bc, 'height'):",
                "        _scaled_lumped_port_dimensions.append((_port_bc, _port_bc.width, _port_bc.height))",
                "        _port_bc.width *= GEOMETRY_SCALE_FACTOR",
                "        _port_bc.height *= GEOMETRY_SCALE_FACTOR",
            ]
        lines += [
            "try:",
            "    simulationObj.generate_mesh()",
            "finally:",
            "    for _port_bc, _physical_width, _physical_height in _scaled_lumped_port_dimensions:",
            "        _port_bc.width = _physical_width",
            "        _port_bc.height = _physical_height",
            "",
        ]
    else:
        lines += [
            "simulationObj.generate_mesh()",
            "",
        ]
    if show_model:
        lines += [
            "# Show geometry only after the configured mesh exists; this avoids EMerge quick_mesh().",
            "simulationObj.view(plot_mesh=False)",
            "",
        ]
    if show_mesh:
        lines += [
            "simulationObj.view(plot_mesh=True)",
            "",
        ]

    if boundary_assignments:
        lines += [
            "# =============================================================================",
            "# [9] OBJECT BOUNDARY CONDITIONS",
            "# =============================================================================",
        ]
        for assignment in boundary_assignments:
            object_name = assignment["object"]
            boundary_type = assignment["type"]
            if object_name in port_surface_names:
                boundary_target = f"port_surfaces[{_q(object_name)}].boundary()"
            elif object_name in plate_names:
                boundary_target = f"plate_objects[{_q(object_name)}]"
            else:
                boundary_target = f"_boundary_faces(geometry_groups[{_q(object_name)}])"
            if boundary_type == "PEC":
                lines.append(f"simulationObj.mw.bc.PEC({boundary_target})")
            elif boundary_type == "PMC":
                lines.append(f"simulationObj.mw.bc.PMC({boundary_target})")
            elif boundary_type in {"Open", "Radiation"}:
                lines.append(f"simulationObj.mw.bc.AbsorbingBoundary({boundary_target})")
            elif boundary_type == "PML":
                lines.append(f"print({_q(f'[warn] Object boundary PML on {object_name} requires volumetric PML geometry and was not exported')})")
            elif boundary_type == "Surface Impedance":
                lines.append(
                    f"simulationObj.mw.bc.SurfaceImpedance({boundary_target}, material=materials[{_q(assignment['material'])}])"
                )
            elif boundary_type == "Thin Conductor":
                thickness_mm = _to_float(
                    assignment["params"].get("Thickness_mm", 0.035), 0.035
                )
                if not math.isfinite(thickness_mm) or thickness_mm <= 0.0:
                    thickness_mm = 0.035
                thickness_m = thickness_mm * coordinate_scale
                lines.append(
                    f"simulationObj.mw.bc.ThinConductor({boundary_target}, material=materials[{_q(assignment['material'])}], thickness={thickness_m:.12g})"
                )
        lines.append("")

    if run_sweep:
        lines += [
            "# =============================================================================",
            "# [10] RUN SIMULATION",
            "# =============================================================================",
            "simulationResult = None",
            "if JOB_TYPE == 'sweep':",
            "    if PROGRESSIVE_SPARAMS_ENABLED and PROGRESSIVE_SPARAMS_HAVE_PORTS:",
            "        simulationResult = _run_progressive_sweep()",
            "    else:",
            "        if PROGRESSIVE_SPARAMS_ENABLED:",
            "            print('[warning] Progressive S-parameter plotting requires at least one configured port; running a standard sweep.')",
            "        simulationResult = simulationObj.mw.run_sweep()",
            "    print(f\"[job] Sweep completed: {JOB_NAME}\")",
            "elif JOB_TYPE == 'eigenmode':",
            "    simulationResult = simulationObj.mw.run_eigenmode(EIGENMODE_COUNT)",
            "    print(f\"[job] Eigenmode completed: {JOB_NAME}\")",
            "elif JOB_TYPE == 'parametric':",
            "    values = [v.strip() for v in PARAM_VALUES.split(',') if v.strip()]",
            "    if not values:",
            "        values = ['default']",
            "    expected_parametric_frequencies = np.linspace(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS).tolist()",
            "    simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
            "    _parametric_progressive_active = PROGRESSIVE_SPARAMS_ENABLED and PROGRESSIVE_SPARAMS_HAVE_PORTS",
            "    _global_parameter_total = int(os.environ.get('EM3D_PARAMETRIC_TOTAL', len(values)))",
            "    _global_parameter_offset = int(os.environ.get('EM3D_PARAMETRIC_INDEX', '0'))",
            "    for _local_parameter_index, _value in enumerate(values, start=1):",
            "        _parameter_index = _global_parameter_offset or _local_parameter_index",
            "        try:",
            "            _parameter_value = float(_value)",
            "        except ValueError:",
            "            _parameter_value = _value",
            "        if PARAM_NAME:",
            "            setattr(simulationObj, PARAM_NAME, _parameter_value)",
            "        _parametric_sample_offset = len(simulationObj.mw.data.scalar._data_entries)",
            "        if _parametric_progressive_active:",
            "            for _start in range(0, NPOINTS, PROGRESSIVE_SPARAMS_CHUNK_SIZE):",
            "                _end = min(_start + PROGRESSIVE_SPARAMS_CHUNK_SIZE, NPOINTS)",
            "                simulationObj.mw.set_frequency(expected_parametric_frequencies[_start:_end])",
            "                simulationResult = simulationObj.mw.run_sweep()",
            "                if _parametric_progressive_active:",
            "                    try:",
            "                        _emit_progressive_parametric_sparams(simulationResult, _value, _parameter_index, _global_parameter_total, _start, _end, _parametric_sample_offset)",
            "                    except (RuntimeError, ValueError, TypeError, AttributeError) as error:",
            "                        _parametric_progressive_active = False",
            "                        print(f'[warning] Progressive plot update skipped for {PARAM_NAME}={_value}; simulation will continue and final results will still be processed: {error}')",
            "        else:",
            "            simulationResult = simulationObj.mw.run_sweep()",
            "        print(f\"[job] Parametric sweep value={_value} completed\")",
            "    simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
            "else:",
            "    raise ValueError(f\"Unsupported job type: {JOB_TYPE}\")",
            "",
            "# =============================================================================",
            "# [11] RESULTS AND SAVE",
            "# =============================================================================",
            "simulationObj.save()",
            "print(f\"[job] Saved project for {JOB_NAME}\")",
            "",
            "# Plotting and derived result processing are intentionally last.",
            "_postprocess_sparams(simulationObj, simulationResult)",
        ]
    else:
        lines += [
            "# =============================================================================",
            "# [10] SAVE WITHOUT RUNNING SOLVER",
            "# =============================================================================",
            "simulationObj.save()",
            "print(f\"[job] Saved meshed project without running solver: {JOB_NAME}\")",
        ]

    return "\n".join(lines) + "\n"


def build_clean_emerge_python_script(script: str, simulation_type: str) -> str:
    """Create a clean worker with configuration decisions resolved before runtime."""
    source_lines = script.splitlines()
    tree = ast.parse(script)
    removed_functions = {
        "_safe_token",
        "_number_of_ports",
        "_selected_s_parameter",
        "_selected_s_parameters",
        "_validate_progressive_samples",
        "_emit_progressive_sparams",
        "_emit_progressive_parametric_sparams",
        "_run_progressive_sweep",
        "_write_sputility_touchstone",
        "_parametric_result_grids",
        "_postprocess_sparams",
    }
    removed_assignments = {
        "JOB_NAME",
        "JOB_TYPE",
        "TARGET_EMERGE_VERSION",
        "RUNTIME_EMERGE_VERSION",
        "PLOT_SPARAMS_AFTER_SIM",
        "PROGRESSIVE_SPARAMS_ENABLED",
        "PROGRESSIVE_SPARAMS_CHUNK_SIZE",
        "PROGRESSIVE_SPARAMS_HAVE_PORTS",
        "EXPORT_SPARAMS_AFTER_SIM",
        "SPARAM_FIT_ENABLED",
        "SPARAM_FIT_POINTS",
        "SAVE_FARFIELDS",
        "simulationResult",
        "_scaled_lumped_port_dimensions",
    }
    removed_lines: set[int] = set()
    replacements: dict[int, list[str]] = {}

    def _line_range(node: ast.AST) -> range:
        return range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1)

    def _names(node: ast.AST) -> set[str]:
        return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}

    def _assigned_names(node: ast.AST) -> set[str]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {
            child.id
            for target in targets
            for child in ast.walk(target)
            if isinstance(child, ast.Name)
        }

    def _literal_assignment(name: str, default: Any) -> Any:
        for node in tree.body:
            if not isinstance(node, ast.Assign) or name not in _assigned_names(node):
                continue
            try:
                return ast.literal_eval(node.value)
            except (TypeError, ValueError):
                return default
        return default

    def _port_mapping_target(target: ast.AST) -> tuple[int, str] | None:
        if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Subscript):
            return None
        port_ref = target.value
        if not isinstance(port_ref.value, ast.Name) or port_ref.value.id != "port":
            return None
        try:
            index = ast.literal_eval(port_ref.slice)
            key = ast.literal_eval(target.slice)
        except (TypeError, ValueError):
            return None
        if isinstance(index, int) and isinstance(key, str):
            return index, key
        return None

    def _flatten_body(node: ast.If) -> list[str]:
        if not node.body:
            return []
        body_indent = min(statement.col_offset for statement in node.body)
        result = []
        for statement in node.body:
            for line_number in _line_range(statement):
                line = source_lines[line_number - 1]
                result.append(line[body_indent:] if line.strip() else "")
        return result

    scale_factor = float(_literal_assignment("GEOMETRY_SCALE_FACTOR", 1.0))
    param_name = str(_literal_assignment("PARAM_NAME", "")).strip()
    param_values = [
        value.strip()
        for value in str(_literal_assignment("PARAM_VALUES", "")).split(",")
        if value.strip()
    ] or ["default"]
    output_configs = _literal_assignment("OUTPUT_CONFIGS", [])
    save_farfields = any(
        isinstance(output, dict)
        and output.get("plot_type") in ("plot_ff", "plot_ff_polar", "plot_ff_3d")
        for output in (output_configs if isinstance(output_configs, list) else [])
    )

    port_dimensions: dict[int, dict[str, float]] = {}
    lumped_port_indices: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            for target in targets:
                key = _port_mapping_target(target)
                if key is not None and key[1] in {"w", "h"}:
                    try:
                        port_dimensions.setdefault(key[0], {})[key[1]] = float(ast.literal_eval(node.value))
                    except (TypeError, ValueError):
                        pass
                if key is not None and key[1] == "bc" and isinstance(node.value, ast.Call):
                    if isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "LumpedPort":
                        lumped_port_indices.add(key[0])

    mesh_block_start = None
    mesh_block_end = None
    for index, node in enumerate(tree.body):
        if not isinstance(node, ast.Assign) or "_scaled_lumped_port_dimensions" not in _assigned_names(node):
            continue
        for following in tree.body[index + 1:]:
            if isinstance(following, ast.Try) and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "generate_mesh"
                for child in ast.walk(following)
            ):
                mesh_block_start = node.lineno
                mesh_block_end = getattr(following, "end_lineno", following.lineno)
                break
        break
    if mesh_block_start is not None and mesh_block_end is not None:
        mesh_lines = []
        if scale_factor != 1.0:
            for port_index in sorted(lumped_port_indices):
                dimensions = port_dimensions.get(port_index, {})
                if not {"w", "h"}.issubset(dimensions):
                    raise ValueError(
                        f"LumpedPort {port_index} must have width and height before clean export."
                    )
                mesh_lines.extend([
                    f"port[{port_index}]['bc'].width = {dimensions['w'] * scale_factor!r}",
                    f"port[{port_index}]['bc'].height = {dimensions['h'] * scale_factor!r}",
                ])
        mesh_lines.append("simulationObj.generate_mesh()")
        if scale_factor != 1.0:
            for port_index in sorted(lumped_port_indices):
                dimensions = port_dimensions[port_index]
                mesh_lines.extend([
                    f"port[{port_index}]['bc'].width = {dimensions['w']!r}",
                    f"port[{port_index}]['bc'].height = {dimensions['h']!r}",
                ])
        removed_lines.update(range(mesh_block_start, mesh_block_end + 1))
        replacements[mesh_block_start] = mesh_lines

    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for definition in definitions:
        definition_end = getattr(definition, "end_lineno", definition.lineno)
        if definition.name not in {"_GeneratedGeometryGroup", "_select_single_port_face", "_boundary_faces"}:
            continue
        referenced_elsewhere = any(
            isinstance(node, ast.Name)
            and node.id == definition.name
            and not definition.lineno <= node.lineno <= definition_end
            for node in ast.walk(tree)
        )
        if not referenced_elsewhere:
            removed_lines.update(range(definition.lineno, definition_end + 1))

    for node in tree.body:
        if node.lineno in removed_lines:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in removed_functions:
            removed_lines.update(_line_range(node))
        elif isinstance(node, ast.Import):
            if any(alias.name in {"re", "json"} for alias in node.names):
                removed_lines.update(_line_range(node))
        elif isinstance(node, ast.ImportFrom) and node.module == "datetime":
            removed_lines.update(_line_range(node))
        elif isinstance(node, ast.Try):
            names = _names(node)
            has_metadata_import = any(
                (
                    isinstance(child, ast.Import)
                    and any(alias.name == "importlib.metadata" for alias in child.names)
                )
                or (isinstance(child, ast.ImportFrom) and child.module == "importlib.metadata")
                for child in ast.walk(node)
            )
            if has_metadata_import or names & {"TARGET_EMERGE_VERSION", "RUNTIME_EMERGE_VERSION"}:
                removed_lines.update(_line_range(node))
        elif (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "GEOMETRY_SCALE_FACTOR"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.NotEq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == 1.0
            and not node.orelse
        ):
            body_indent = min(statement.col_offset for statement in node.body)
            flattened_body = []
            for statement in node.body:
                for line_number in _line_range(statement):
                    line = source_lines[line_number - 1]
                    flattened_body.append(line[body_indent:] if line.strip() else "")
            removed_lines.update(_line_range(node))
            replacements[node.lineno] = flattened_body
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if _assigned_names(node) & removed_assignments:
                removed_lines.update(_line_range(node))
        elif isinstance(node, ast.If) and "RUNTIME_EMERGE_VERSION" in _names(node):
            removed_lines.update(_line_range(node))
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "SAVE_FARFIELDS":
            removed_lines.update(_line_range(node))
            if save_farfields:
                replacements[node.lineno] = _flatten_body(node)
        elif isinstance(node, ast.Expr):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "_postprocess_sparams":
                removed_lines.update(_line_range(node))
        elif isinstance(node, ast.If) and "JOB_TYPE" in _names(node):
            kind = str(simulation_type).strip().lower()
            if kind == "sweep":
                runner = ["simulationObj.mw.run_sweep()"]
            elif kind == "eigenmode":
                runner = ["simulationObj.mw.run_eigenmode(EIGENMODE_COUNT)"]
            elif kind == "parametric":
                runner = []
                for value in param_values:
                    try:
                        parameter_value = repr(float(value))
                    except ValueError:
                        parameter_value = repr(value)
                    if param_name:
                        runner.append(
                            f"setattr(simulationObj, {param_name!r}, {parameter_value})"
                        )
                    runner.append("simulationObj.mw.run_sweep()")
            else:
                raise ValueError(f"Unsupported simulation type for clean export: {simulation_type}")
            removed_lines.update(_line_range(node))
            replacements[node.lineno] = runner

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and not node.orelse
            and node.body
            and all(isinstance(statement, ast.Raise) for statement in node.body)
        ):
            removed_lines.update(_line_range(node))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or node.lineno in removed_lines:
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "print":
            line = source_lines[node.lineno - 1]
            indentation = line[:len(line) - len(line.lstrip())]
            removed_lines.update(_line_range(node))
            if node.col_offset:
                replacements[node.lineno] = [f"{indentation}pass"]

    output_lines = []
    for line_number, line in enumerate(source_lines, start=1):
        if line_number in replacements:
            output_lines.extend(replacements[line_number])
        if line_number in removed_lines:
            continue
        if line.lstrip().startswith("#"):
            continue
        output_lines.append(line.rstrip())

    compact_lines = []
    for line in output_lines:
        if line.strip() or not compact_lines or compact_lines[-1].strip():
            compact_lines.append(line)
    return "\n".join(compact_lines).strip() + "\n"
