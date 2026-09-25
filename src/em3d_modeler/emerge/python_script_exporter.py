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
from typing import Any, Dict, List
import json
import locale
import re


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
                    "ParamName": str(item.get("ParamName", "")).strip(),
                    "ParamValues": str(item.get("ParamValues", "")).strip(),
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
        object_mesh_sizes[object_name] = fraction_value * wavelength_at_fmax
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
        size_m = max(1e-9, _to_float(item.get("size_mm", 0.25 if mode == "boundary" else 0.1), 0.25)) * 0.001
        growth_rate = max(1.001, _to_float(item.get("growth_rate", 3.0), 3.0))
        raw_max_size = item.get("max_size_mm")
        max_size_m = None
        if raw_max_size not in (None, "", 0, 0.0):
            max_size_m = max(1e-9, _to_float(raw_max_size, 0.0) * 0.001)
        local_mesh_refinements.append({
            "object": object_name,
            "mode": mode,
            "faces": faces,
            "size_m": size_m,
            "growth_rate": growth_rate,
            "max_size_m": max_size_m,
        })
    boundary_assignments = [
        {
            "object": str(item.get("object", "")).strip(),
            "type": str(item.get("type", "")).strip(),
        }
        for item in object_boundaries
        if (
            isinstance(item, dict)
            and str(item.get("object", "")).strip() in valid_boundary_names
            and str(item.get("object", "")).strip() not in port_surface_names
            and str(item.get("object", "")).strip() != air_volume_name
        )
    ]
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
        f"EXPORT_SPARAMS_AFTER_SIM = {bool(export_sparams_after_sim)}",
        f"SPARAM_FIT_ENABLED = {fit_enabled}",
        f"SPARAM_FIT_POINTS = {fit_points}",
        f"OUTPUT_CONFIGS = {repr(output_configs)}",
        "SAVE_FARFIELDS = any(output.get('plot_type') in ('plot_ff', 'plot_ff_polar', 'plot_ff_3d') for output in OUTPUT_CONFIGS)",
        "print(f\"[job] {JOB_NAME} | type={JOB_TYPE} | range={FMIN_GHZ}..{FMAX_GHZ} GHz step {FSTEP_GHZ}\")",
        "",
        "# =============================================================================",
        "# [3] EMERGE SETUP",
        "# =============================================================================",
        "simulationObj = em.Simulation(PROJECT_NAME, save_file=True, write_log=True)",
        f"simulationObj.set_solver(em.EMSolver.{str(solver).strip().upper()})",
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
        "def _boundary_faces(geometry_group):",
        "    geometry_objects = list(geometry_group.objects)",
        "    if not geometry_objects:",
        "        raise RuntimeError(f'Geometry group has no boundary objects: {geometry_group}')",
        "    faces = geometry_objects[0].boundary()",
        "    for geometry_object in geometry_objects[1:]:",
        "        faces = faces + geometry_object.boundary()",
        "    return faces",
        "",
        "def _write_sputility_touchstone(grid):",
        "    nports = _number_of_ports(grid)",
        "    extension = f'.s{nports}p'",
        "    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')",
        "    output_base = os.path.join(SCRIPT_DIR, f'{_safe_token(PROJECT_NAME)}_{timestamp}')",
        "    def _write_touchstone(output_path, frequencies, curves):",
        "        with open(output_path, 'w', encoding='ascii', newline='\\n') as touchstone_file:",
        "            touchstone_file.write('# HZ S RI R 50.0\\n')",
        "            for sample_index, frequency in enumerate(frequencies):",
        "                row = [f'{float(frequency):.16g}']",
        "                for output_port in range(1, nports + 1):",
        "                    for input_port in range(1, nports + 1):",
        "                        value = complex(curves[(output_port, input_port)][sample_index])",
        "                        row.extend((f'{value.real:.16g}', f'{value.imag:.16g}'))",
        "                touchstone_file.write(' '.join(row) + '\\n')",
        "    output_path = output_base + '_fit' + extension",
        "    if SPARAM_FIT_ENABLED:",
        "        fit_frequencies = grid.dense_f(SPARAM_FIT_POINTS)",
        "        fit_curves = {(i, j): grid.model_S(i, j, fit_frequencies) for i in range(1, nports + 1) for j in range(1, nports + 1)}",
        "        _write_touchstone(output_path, fit_frequencies, fit_curves)",
        "        original_frequencies = grid.freq",
        "        original_curves = {(i, j): grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)}",
        "        original_path = output_base + '_original' + extension",
        "        _write_touchstone(original_path, original_frequencies, original_curves)",
        "        print(f'[job] Original SPUtility Touchstone exported: {original_path}')",
        "    else:",
        "        original_frequencies = grid.freq",
        "        original_curves = {(i, j): grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)}",
        "        _write_touchstone(output_path, original_frequencies, original_curves)",
        "    print(f'[job] SPUtility Touchstone exported: {output_path}')",
        "    return output_path",
        "",
        "def _postprocess_sparams(sim_obj, sim_result):",
        "    if JOB_TYPE not in ('sweep', 'parametric'):",
        "        return",
        "    out_base = os.path.join(SCRIPT_DIR, f\"{_safe_token(PROJECT_NAME)}_{_safe_token(JOB_NAME)}\")",
        "",
        "    if EXPORT_SPARAMS_AFTER_SIM:",
        "        _write_sputility_touchstone(sim_result.scalar.grid)",
        "",
        "    grid = sim_result.scalar.grid",
        "",
        "    if PLOT_SPARAMS_AFTER_SIM:",
        "        from emerge.plot import plot_sp",
        "        nports = _number_of_ports(grid)",
        "        if SPARAM_FIT_ENABLED:",
        "            frequencies = grid.dense_f(SPARAM_FIT_POINTS)",
        "            curves = [grid.model_S(i, j, frequencies) for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        else:",
        "            frequencies = grid.freq",
        "            curves = [grid.S(i, j) for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        labels = [f'S{i}{j}' for i in range(1, nports + 1) for j in range(1, nports + 1)]",
        "        plot_sp(frequencies, curves, labels=labels)",
        "        print('[job] S-parameters plotted')",
        "",
        "    if OUTPUT_CONFIGS:",
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
        air_width = (air_bounds[1] - air_bounds[0]) * 0.001
        air_depth = (air_bounds[3] - air_bounds[2]) * 0.001
        air_height = (air_bounds[5] - air_bounds[4]) * 0.001
        air_position = (air_bounds[0] * 0.001, air_bounds[2] * 0.001, air_bounds[4] * 0.001)
        lines += [
            "# Native EMerge PML volumes; the visual AIR/PML STEP boxes are not imported.",
            "_pml_geometry = em.geo.pmlbox(",
            f"    width={air_width!r},",
            f"    depth={air_depth!r},",
            f"    height={air_height!r},",
            f"    position={air_position!r},",
            f"    material=materials[{_q(str(next((entry.get('material', 'AIR') for entry in step_entries if str(entry.get('object_name', '')).strip() == air_volume_name), 'AIR')))}],",
            f"    thickness={pml_setup['thickness_m']!r},",
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
                f"geometry_group = em.geo.step.STEPItems(name={_q(obj_name)}, filename=os.path.join(SCRIPT_DIR, {_q(step_file)}), unit=mm)",
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
            origin = tuple(float(value) * 0.001 for value in plate.get("origin", (0.0, 0.0, 0.0)))
            u = tuple(float(value) * 0.001 for value in plate.get("u", (0.0, 0.0, 0.0)))
            v = tuple(float(value) * 0.001 for value in plate.get("v", (0.0, 0.0, 0.0)))
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
            origin = [_to_float(v, 0.0) * 0.001 for v in p.get("origin", [0.0, 0.0, 0.0])]
            u = [_to_float(v, 0.0) * 0.001 for v in p.get("u", [0.0, 0.0, 0.0])]
            v = [_to_float(v, 0.0) * 0.001 for v in p.get("v", [0.0, 0.0, 0.0])]
            width = abs(_to_float(p.get("width", 0.0), 0.0) * 0.001)
            height = abs(_to_float(p.get("height", 0.0), 0.0) * 0.001)
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
        lines += [
            "simulationObj.commit_geometry()",
        ]
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
            "# Assign global boundary settings to the six outer faces of the open-region domain.",
            f"_open_region_objects = list(geometry_groups[{_q(air_volume_name)}].objects)",
            "if len(_open_region_objects) != 1:",
            f"    raise RuntimeError('Open-region domain {_q(air_volume_name)} must contain exactly one geometry object')",
            "_open_region_object = _open_region_objects[0]",
        ]
        boundary_groups: Dict[str, List[str]] = {}
        for boundary_key, face_selector, boundary_type in domain_boundaries:
            if face_selector in waveguide_face_selectors:
                lines.append(
                    f"print({_q(f'[info] Global boundary {boundary_key} skipped because the face is used by a WaveguidePort')})"
                )
                continue
            if boundary_type in {"Open", "Radiation", "Pec", "Pmc"}:
                var_name = f"_open_face_{boundary_key.lower()}"
                lines.append(f"{var_name} = _open_region_object.face({_q(face_selector)})")
                group_type = "Absorbing" if boundary_type in {"Open", "Radiation"} else boundary_type
                boundary_groups.setdefault(group_type, []).append(var_name)
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
        for boundary_type, face_vars in boundary_groups.items():
            selection_expression = " + ".join(face_vars)
            if boundary_type == "Absorbing":
                lines.append(f"simulationObj.mw.bc.AbsorbingBoundary({selection_expression})")
            elif boundary_type == "Pec":
                lines.append(f"simulationObj.mw.bc.PEC({selection_expression})")
            elif boundary_type == "Pmc":
                lines.append(f"simulationObj.mw.bc.PMC({selection_expression})")
        lines.append("")

    lines += [
        "# =============================================================================",
        "# [8] MESH GENERATION",
        "# =============================================================================",
        "simulationObj.mesher.set_curved_boundary_meshing(CURVED_BOUNDARY_RESOLUTION)",
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
        lines.append("")

    if run_sweep:
        lines += [
            "# =============================================================================",
            "# [10] RUN SIMULATION",
            "# =============================================================================",
            "simulationResult = None",
            "if JOB_TYPE == 'sweep':",
            "    simulationResult = simulationObj.mw.run_sweep()",
            "    print(f\"[job] Sweep completed: {JOB_NAME}\")",
            "elif JOB_TYPE == 'eigenmode':",
            "    simulationResult = simulationObj.mw.run_eigenmode(EIGENMODE_COUNT)",
            "    print(f\"[job] Eigenmode completed: {JOB_NAME}\")",
            "elif JOB_TYPE == 'parametric':",
            "    values = [v.strip() for v in PARAM_VALUES.split(',') if v.strip()]",
            "    if not values:",
            "        values = ['default']",
            "    for _value in values:",
            "        if PARAM_NAME:",
            "            setattr(simulationObj, PARAM_NAME, _value)",
            "        simulationResult = simulationObj.mw.run_sweep()",
            "        print(f\"[job] Parametric sweep value={_value} completed\")",
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
