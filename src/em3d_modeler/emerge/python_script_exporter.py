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
            lines.append(f"materials[{_q(name)}] = em.lib.PML")
            continue

        rec = (materials_catalog or {}).get(name, {})
        er = _to_float(rec.get("er", 1.0), 1.0)
        tan_d = _to_float(rec.get("tan_d", 0.0), 0.0)
        sigma = _to_float(rec.get("sigma", 0.0), 0.0)
        color = str(rec.get("color", "#bebebe"))
        opacity = _to_float(rec.get("opacity", 0.85), 0.85)

        lines += [
            f"materials[{_q(name)}] = em.Material(name={_q(name)}, er={er}, ur=1.0, tand={tan_d}, cond={sigma})",
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
    npoints = int(max(2, round((fmax - fmin) / max(fstep, 1e-9)) + 1))
    fit_cfg = sim.get("sparam_fitting", {}) if isinstance(sim.get("sparam_fitting", {}), dict) else {}
    fit_enabled = _to_bool(fit_cfg.get("enabled", False))
    fit_points = max(8, _to_int(fit_cfg.get("points", 1001), 1001))

    mode_count = max(1, _to_int(sim.get("EigenmodeCount", 5), 5))
    param_name = str(sim.get("ParamName", "")).strip()
    param_values = str(sim.get("ParamValues", "")).strip()

    mesh_resolution = max(0.01, min(1.0, float(mesh_resolution_fraction)))
    mesh_cfg = settings.get("mesh", {}) if isinstance(settings, dict) else {}
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
    valid_boundary_names = exported_object_names | port_surface_names | plate_names
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
        )
    ]
    eff_pardiso_threads = max(1, int(pardiso_threads if parallel_enabled else 1))
    eff_acc_threads = max(1, int(acc_threads if parallel_enabled else 1))
    used_materials = sorted({str(e.get("material", "PEC")) for e in [*step_entries, *plates]})
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
        f"OBJECT_MESH_SIZES_M = {repr(object_mesh_sizes)}",
        f"EIGENMODE_COUNT = {mode_count}",
        f"PARAM_NAME = {_q(param_name)}",
        f"PARAM_VALUES = {_q(param_values)}",
        f"PLOT_SPARAMS_AFTER_SIM = {bool(plot_sparams_after_sim)}",
        "EXPORT_SPARAMS_AFTER_SIM = True",
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
        "",
        "def _safe_token(v: str) -> str:",
        "    t = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '_' for ch in str(v))",
        "    t = t.strip('_')",
        "    return t or 'simulation'",
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
        "    frequencies = grid.dense_f(SPARAM_FIT_POINTS) if SPARAM_FIT_ENABLED else grid.freq",
        "    curves = {}",
        "    for output_port in range(1, nports + 1):",
        "        for input_port in range(1, nports + 1):",
        "            if SPARAM_FIT_ENABLED:",
        "                curves[(output_port, input_port)] = grid.model_S(output_port, input_port, frequencies)",
        "            else:",
        "                curves[(output_port, input_port)] = grid.S(output_port, input_port)",
        "    extension = f'.s{nports}p'",
        "    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')",
        "    output_base = os.path.join(SCRIPT_DIR, f'{_safe_token(PROJECT_NAME)}_{timestamp}_fit')",
        "    output_path = output_base + extension",
        "    with open(output_path, 'w', encoding='ascii', newline='\\n') as touchstone_file:",
        "        touchstone_file.write('# HZ S RI R 50.0\\n')",
        "        for sample_index, frequency in enumerate(frequencies):",
        "            row = [f'{float(frequency):.16g}']",
        "            for output_port in range(1, nports + 1):",
        "                for input_port in range(1, nports + 1):",
        "                    value = curves[(output_port, input_port)][sample_index]",
        "                    value = complex(value)",
        "                    row.extend((f'{value.real:.16g}', f'{value.imag:.16g}'))",
        "            touchstone_file.write(' '.join(row) + '\\n')",
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
        "            output_port, input_port = _selected_s_parameter(output, grid)",
        "            selected_curve = grid.S(output_port, input_port)",
        "            if kind == 'plot_sp':",
        "                curves = [selected_curve]",
        "                labels = [str(output.get('s_parameter', f'S{output_port}{input_port}'))]",
        "                plot_sp(frequencies, curves, labels=labels)",
        "            elif kind == 'plot_vswr':",
        "                curves = [selected_curve]",
        "                labels = [f'VSWR{output_port}{input_port}']",
        "                plot_vswr(frequencies, curves, labels=labels)",
        "            elif kind == 'smith':",
                "                smith([selected_curve], f=frequencies, labels=[str(output.get('s_parameter', f'S{output_port}{input_port}'))])",
        "            elif kind == 'plot':",
        "                import numpy as np",
                "                curves = [20.0 * np.log10(np.maximum(np.abs(selected_curve), 1e-12))]",
                "                labels = [f'|{output.get(\"s_parameter\", f\"S{output_port}{input_port}\").strip()}| dB']",
        "                plot(frequencies, curves, labels=labels, xlabel='Frequency (Hz)', ylabel='Magnitude (dB)')",
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

    if step_entries:
        for entry in step_entries:
            obj_name = str(entry.get("object_name", "Object"))
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
                f"port[{idx}]['w'] = {width}",
                f"port[{idx}]['h'] = {height}",
                f"port[{idx}]['portR'] = {z0}",
                f"port[{idx}]['portDirection'] = ({direction[0]}, {direction[1]}, {direction[2]})",
                f"port[{idx}]['portExcitationAmplitude'] = {power}",
                f"_port_{idx}_origin = ({_q(origin[0])}, {_q(origin[1])}, {_q(origin[2])})",
                f"_port_{idx}_u = ({_q(u[0])}, {_q(u[1])}, {_q(u[2])})",
                f"_port_{idx}_v = ({_q(v[0])}, {_q(v[1])}, {_q(v[2])})",
                f"port_surfaces[{_q(str(p.get('plate_name', name)))}] = em.geo.Plate(",
                f"    name={_q(name)},",
                f"    origin=_port_{idx}_origin,",
                f"    u=_port_{idx}_u,",
                f"    v=_port_{idx}_v,",
                ")",
                f"port[{idx}]['object'] = port_surfaces[{_q(str(p.get('plate_name', name)))}]",
                "",
            ]

    lines += [
        "# =============================================================================",
        "# [7] GEOMETRY COMMIT AND SIMULATION SETUP",
        "# =============================================================================",
        "simulationObj.commit_geometry()",
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

    if ports:
        lines += [
            "# Assign LumpedPort excitations to the already-created and committed Plates",
        ]
        for p in ports:
            idx = int(p.get("index", 1))
            lines += [
                f"simulationObj.mw.bc.LumpedPort(",
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

    if show_model:
        lines += [
            "# Show the committed geometry after port plates and LumpedPorts are defined.",
            "simulationObj.view()",
            "",
        ]

    lines += [
        "# =============================================================================",
        "# [8] MESH GENERATION",
        "# =============================================================================",
        "simulationObj.generate_mesh()",
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
            elif boundary_type in {"Open", "Radiation", "PML"}:
                lines.append(f"simulationObj.mw.bc.AbsorbingBoundary({boundary_target})")
        lines.append("")

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

    return "\n".join(lines) + "\n"
