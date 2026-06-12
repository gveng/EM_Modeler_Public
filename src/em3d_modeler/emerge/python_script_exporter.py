"""Generate runnable Python script for EMERGE simulation."""
from __future__ import annotations

from typing import Any, Dict, List
import json
import locale


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
    solver: str = "PARDISO",
    parallel_enabled: bool = True,
    pardiso_threads: int = 8,
    acc_threads: int = 10,
    mesh_resolution_fraction: float = 0.3,
    plot_sparams_after_sim: bool = True,
    export_sparams_after_sim: bool = True,
) -> str:
    simulations = _normalize_simulation_configs(settings)
    first_sim = simulations[0] if simulations else {
        "Fmin_GHz": 0.1,
        "Fmax_GHz": 10.0,
        "Fstep_GHz": 0.1,
    }
    sim = settings.get("simulation", {})
    if not isinstance(sim, dict):
        sim = {}
    fmin = _to_float(first_sim.get("Fmin_GHz", sim.get("Fmin_GHz", 0.1)), 0.1)
    fmax = _to_float(first_sim.get("Fmax_GHz", sim.get("Fmax_GHz", 10.0)), 10.0)
    fstep = _to_float(first_sim.get("Fstep_GHz", sim.get("Fstep_GHz", 0.1)), 0.1)

    fit_cfg = sim.get("sparam_fitting", {})
    if not isinstance(fit_cfg, dict):
        fit_cfg = {}
    fit_enabled = _to_bool(
        fit_cfg.get(
            "enabled",
            sim.get(
                "SParamFittingEnabled",
                sim.get("SParameterFittingEnabled", sim.get("EnableSParamFitting", False)),
            ),
        )
    )
    fit_npoints = max(
        2,
        _to_int(
            fit_cfg.get("npoints", sim.get("SParamFitNPoints", sim.get("SParameterFitNPoints", 201))),
            201,
        ),
    )
    fit_poly_order = max(
        1,
        _to_int(
            fit_cfg.get("poly_order", sim.get("SParamFitPolyOrder", sim.get("SParameterFitPolyOrder", 3))),
            3,
        ),
    )

    # Use mesh_resolution_fraction parameter from UI settings.
    mesh_resolution = max(0.01, min(1.0, float(mesh_resolution_fraction)))
    eff_pardiso_threads = max(1, int(pardiso_threads if parallel_enabled else 1))
    eff_acc_threads = max(1, int(acc_threads if parallel_enabled else 1))

    used_materials = sorted({str(e.get("material", "PEC")) for e in step_entries})
    simulation_configs = [s for s in simulations if bool(s.get("enabled", True))]
    has_sweep_like = any(str(s.get("type", "")).strip().title() in {"Sweep", "Parametric"} for s in simulation_configs)

    lines: List[str] = [
        "# Auto-generated EMERGE Python script from EM 3D Modeler",
        "# This script is intended to be run with: python -u <script.py>",
        "",
        "import datetime",
        "import emerge_iron",
        "",
        "from emerge_config import config",
        f"config.set_pardiso_threads({eff_pardiso_threads})",
        f"config.set_acc_threads({eff_acc_threads})",
        "",
        "import math",
        "import numpy as np",
        "import emerge as em",
        "import locale",
        "import os",
        "import tempfile",
        "import shutil",
        "import traceback",
        "",
        "try:",
        "    locale.setlocale(locale.LC_ALL, '')",
        "except Exception as _locale_exc:",
        "    print(f'WARNING: locale setup skipped ({_locale_exc})')",
        "",
        "#######################################################################################################################################",
        "# DEFINE PROJECT NAME",
        "#######################################################################################################################################",
        "#from Plot_Results import ProjectName",
        f"ProjectName = {_q(project_name)}",
        "",
        "",
        "# Change current path to script file folder",
        "abspath = os.path.abspath(__file__)",
        "dname = os.path.dirname(abspath)",
        "os.chdir(dname)",
        "",
        "currDir = os.getcwd()",
        "print(currDir)",
        "## prepare simulation folder, if dir exits remove and create new one to be empty",
        "Sim_Path = os.path.join(currDir, ProjectName)",
        "if os.path.exists(Sim_Path):",
        "    shutil.rmtree(Sim_Path)",
        "os.mkdir(Sim_Path)",
        "",
        "m = 1.0",
        "cm = 0.01",
        "mm = 0.001",
        "um = 0.000001",
        "",
        "PROJECT_NAME = ProjectName",
        f"MODEL_UNITS = {_q(units)}",
        f"FMIN_GHZ = {fmin}",
        f"FMAX_GHZ = {fmax}",
        f"FSTEP_GHZ = {fstep}",
        f"SHOW_MODEL = {bool(show_model)}",
        f"SHOW_MESH = {bool(show_mesh)}",
        f"RUN_SWEEP = {bool(run_sweep)}",
        f"PLOT_SPARAMS_AFTER_SIM = {bool(plot_sparams_after_sim)}",
        f"EXPORT_SPARAMS_AFTER_SIM = {bool(export_sparams_after_sim)}",
        f"SIM_SOLVER = {_q(solver)}",
        f"PARALLEL_ENABLED = {bool(parallel_enabled)}",
        f"SIMULATION_CONFIGS = {repr(simulation_configs)}",
        "TOUCHSTONE_Z0 = 50",
        "TOUCHSTONE_FORMAT = 'RI'",
        "TOUCHSTONE_FUNIT = 'HZ'",
        "",
        "if FSTEP_GHZ <= 0:",
        "    print(f'WARNING: invalid FSTEP_GHZ={FSTEP_GHZ}; using 0.1 GHz')",
        "    FSTEP_GHZ = 0.1",
        "MIN_NONZERO_FMIN_GHZ = 1e-6",
        "if FMIN_GHZ <= 0:",
        "    _old_fmin = FMIN_GHZ",
        "    FMIN_GHZ = MIN_NONZERO_FMIN_GHZ",
        "    print(f'WARNING: FMIN_GHZ={_old_fmin} leads to DC singularity for conductive materials; using {FMIN_GHZ} GHz')",
        "if FMAX_GHZ <= FMIN_GHZ:",
        "    _old_fmax = FMAX_GHZ",
        "    FMAX_GHZ = FMIN_GHZ + max(FSTEP_GHZ, 1e-6)",
        "    print(f'WARNING: invalid frequency range ({FMIN_GHZ}..{_old_fmax}) GHz; using FMAX_GHZ={FMAX_GHZ} GHz')",
        "",
        "npoints = int(max(2, round((FMAX_GHZ - FMIN_GHZ) / max(FSTEP_GHZ, 1e-9)) + 1))",
        "try:",
        "    simulationObj = em.Simulation(PROJECT_NAME, save_file=True, write_log=True)",
        "except TypeError:",
        "    simulationObj = em.Simulation(PROJECT_NAME)",
        "    try:",
        "        simulationObj.save_file = True",
        "    except Exception:",
        "        pass",
        "    try:",
        "        simulationObj.write_log = True",
        "    except Exception:",
        "        pass",
        "TARGET_EMERGE_VERSION = \"2.5.5\"",
        "STRICT_VERSION_CHECK = False",
        "try:",
        "    simulationObj.check_version(TARGET_EMERGE_VERSION)",
        "except Exception as _version_exc:",
        "    if STRICT_VERSION_CHECK:",
        "        raise",
        "    print(f'WARNING: version check skipped ({_version_exc})')",
        "",
    ]

    lines += _material_block(used_materials, materials_catalog)

    lines += [
        "# One STEP file per object, exported in this bundle directory.",
        "",
    ]

    if step_entries:
        for entry in step_entries:
            obj_name = str(entry.get("object_name", "Object"))
            step_file = str(entry.get("step_file", ""))
            material = str(entry.get("material", "PEC"))
            priority = int(entry.get("priority", 5000))

            lines += [
                f"stepObjectGroup = em.geo.step.STEPItems(name={_q(obj_name)}, filename=os.path.join(currDir, {_q(step_file)}), unit=mm)",
                "for geoObj in stepObjectGroup.objects:",
                f"    geoObj.prio_set({priority})",
                f"    _mat = materials.get({_q(material)})",
                "    if _mat is not None:",
                "        geoObj.set_material(_mat)",
                "",
            ]
    else:
        lines += [
            "print('No STEP entries generated from current scene.')",
            "",
        ]

    bounds = settings.get("boundaries", {})
    ports = lumped_ports or []

    if ports:
        lines += [
            "# Lumped ports generated from Plate objects in 3D model",
            "port = {}",
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
                (
                    f"port[{idx}]['object'] = em.geo.Plate(name={_q(name)}, "
                    f"origin=({_q(origin[0])}, {_q(origin[1])}, {_q(origin[2])}), "
                    f"u=({_q(u[0])}, {_q(u[1])}, {_q(u[2])}), "
                    f"v=({_q(v[0])}, {_q(v[1])}, {_q(v[2])}))"
                ),
                "",
            ]

    lines += [
        "# Boundary settings from project tree",
        f"BOUNDARIES = {_q(bounds)}",
        "",
        "# Port settings from project tree (kept for reference/log)",
        f"PORTS = {_q(settings.get('ports', []))}",
        "",
        "simulationObj.commit_geometry()",
        "if SHOW_MODEL:",
        "    simulationObj.view()",
        "simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, npoints)",
        f"simulationObj.mw.set_resolution({mesh_resolution:.6f})",
        "",
    ]

    if ports:
        lines += [
            "# Apply lumped ports immediately before mesh generation",
        ]
        for p in ports:
            idx = int(p.get("index", 1))
            lines.append(
                f"simulationObj.mw.bc.LumpedPort(port[{idx}]['object'], {idx}, width=port[{idx}]['w'], height=port[{idx}]['h'], direction=port[{idx}]['portDirection'], Z0=port[{idx}]['portR'], power=port[{idx}]['portExcitationAmplitude'])"
            )
        lines += [
        "",
        ]

    lines += [
        "simulationObj.generate_mesh()",
        "if SHOW_MESH:",
        "    simulationObj.view(plot_mesh=True)",
        "",
    ]

    lines += [
        "simulationResult = None",
        "_last_sweep_executed = False",
        "if not SIMULATION_CONFIGS:",
        "    print('[sim] No enabled simulations configured. Geometry/mesh prepared, no simulation run executed.')",
        "for _sim_cfg in SIMULATION_CONFIGS:",
        "    _sim_name = str(_sim_cfg.get('name', 'Simulation'))",
        "    _sim_type = str(_sim_cfg.get('type', 'Sweep')).strip().lower()",
        "    _fmin_ghz = float(_sim_cfg.get('Fmin_GHz', FMIN_GHZ))",
        "    _fmax_ghz = float(_sim_cfg.get('Fmax_GHz', FMAX_GHZ))",
        "    _fstep_ghz = float(_sim_cfg.get('Fstep_GHz', FSTEP_GHZ))",
        "    _mode_count = int(max(1, _sim_cfg.get('EigenmodeCount', 5)))",
        "    _param_name = str(_sim_cfg.get('ParamName', '')).strip()",
        "    _param_values = str(_sim_cfg.get('ParamValues', '')).strip()",
        "",
        "    if _fstep_ghz <= 0:",
        "        _fstep_ghz = 0.1",
        "    if _fmin_ghz <= 0:",
        "        _fmin_ghz = 1e-6",
        "    if _fmax_ghz <= _fmin_ghz:",
        "        _fmax_ghz = _fmin_ghz + max(_fstep_ghz, 1e-6)",
        "",
        "    _npoints = int(max(2, round((_fmax_ghz - _fmin_ghz) / max(_fstep_ghz, 1e-9)) + 1))",
        "    print(f\"[sim] Running '{_sim_name}' type={_sim_type}\")",
        "",
        "    if _sim_type == 'sweep':",
        "        if not RUN_SWEEP:",
        "            print(f\"[sim] Skipped sweep '{_sim_name}' because RUN_SWEEP is disabled.\")",
        "            continue",
        "        try:",
        "            simulationObj.mw.set_frequency_range(_fmin_ghz * 1e9, _fmax_ghz * 1e9, _npoints)",
        "            simulationResult = simulationObj.mw.run_sweep()",
        "            _last_sweep_executed = True",
        "            print(f\"[sim] Sweep completed: {_sim_name}\")",
        "            if PLOT_SPARAMS_AFTER_SIM or EXPORT_SPARAMS_AFTER_SIM:",
        "                try:",
        "                    from datetime import datetime as _dt",
        "                    if PLOT_SPARAMS_AFTER_SIM:",
        "                        from emerge.plot import plot_sp",
        "                    _result_time_code = _dt.now().strftime('%Y%m%d-%H%M%S')",
        "                    _safe_name = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in _sim_name) or 'Simulation'",
        "                    _run_tag = f'{_safe_name}_{_result_time_code}'",
        "                    m_post = em.Simulation(PROJECT_NAME, load_file=True)",
        "                    data = m_post.data.mw",
        "                    _freq = data.scalar.grid.freq",
        "                    _s_terms = [data.scalar.grid.S(1,1)]",
        "                    _labels = ['S11']",
        "                    try:",
        "                        _s_terms.append(data.scalar.grid.S(2,1))",
        "                        _labels.append('S21')",
        "                    except Exception:",
        "                        pass",
        "                    if PLOT_SPARAMS_AFTER_SIM:",
        "                        plot_sp(_freq, _s_terms, labels=_labels)",
        "                        print(f'[sim] S-parameter plot completed: {_run_tag}')",
        "                    if EXPORT_SPARAMS_AFTER_SIM:",
        f"                        _touchstone_name = f'{{_run_tag}}.s{port_count}p'",
        "                        data.scalar.grid.export_touchstone(",
        "                            _touchstone_name,",
        "                            Z0ref=TOUCHSTONE_Z0,",
        "                            format=TOUCHSTONE_FORMAT,",
        "                            custom_comments=['EM 3D Modeler', 'EMERGE auto-export', f'Simulation: {_sim_name}'],",
        "                            funit=TOUCHSTONE_FUNIT,",
        "                        )",
        "                        print(f'[sim] S-parameter export completed: {_touchstone_name}')",
        "                except Exception:",
        "                    print(f'[warn] S-parameter post-processing failed for {_sim_name}; continuing.')",
        "                    traceback.print_exc()",
        "        except Exception:",
        "            print(f\"[sim] Sweep failed: {_sim_name}\")",
        "            traceback.print_exc()",
        "            raise",
        "",
        "    elif _sim_type == 'eigenmode':",
        "        _done = False",
        "        for _meth in ('run_eigenmode', 'eigenmode', 'solve_eigenmode'):",
        "            _fn = getattr(simulationObj.mw, _meth, None)",
        "            if callable(_fn):",
        "                try:",
        "                    simulationResult = _fn(_mode_count)",
        "                    _done = True",
        "                    print(f\"[sim] Eigenmode completed via {_meth}: {_sim_name}\")",
        "                    break",
        "                except TypeError:",
        "                    try:",
        "                        simulationResult = _fn()",
        "                        _done = True",
        "                        print(f\"[sim] Eigenmode completed via {_meth}(): {_sim_name}\")",
        "                        break",
        "                    except Exception:",
        "                        pass",
        "                except Exception:",
        "                    pass",
        "        if not _done:",
        "            print(f\"[sim][warn] Eigenmode API not available; skipped '{_sim_name}'.\")",
        "",
        "    elif _sim_type == 'parametric':",
        "        _vals = []",
        "        if _param_values:",
        "            for _tok in _param_values.split(','):",
        "                _tok = _tok.strip()",
        "                if _tok:",
        "                    _vals.append(_tok)",
        "        if not _vals:",
        "            _vals = ['default']",
        "",
        "        for _v in _vals:",
        "            print(f\"[sim] Parametric '{_sim_name}': {_param_name}={_v}\")",
        "            _applied = False",
        "            if _param_name:",
        "                _numeric_v = None",
        "                try:",
        "                    _numeric_v = float(_v)",
        "                except Exception:",
        "                    _numeric_v = None",
        "                for _target in (simulationObj, getattr(simulationObj, 'mw', None), getattr(simulationObj, 'settings', None)):",
        "                    if _target is None:",
        "                        continue",
        "                    if hasattr(_target, _param_name):",
        "                        try:",
        "                            setattr(_target, _param_name, _numeric_v if _numeric_v is not None else _v)",
        "                            _applied = True",
        "                            break",
        "                        except Exception:",
        "                            pass",
        "                if not _applied:",
        "                    print(f\"[sim][warn] Param '{_param_name}' could not be applied directly; running with unchanged model.\")",
        "            if not RUN_SWEEP:",
        "                print(f\"[sim] Skipped parametric sweep '{_sim_name}' because RUN_SWEEP is disabled.\")",
        "                continue",
        "            try:",
        "                simulationObj.mw.set_frequency_range(_fmin_ghz * 1e9, _fmax_ghz * 1e9, _npoints)",
        "                simulationResult = simulationObj.mw.run_sweep()",
        "                _last_sweep_executed = True",
        "            except Exception:",
        "                print(f\"[sim] Parametric sweep failed: {_sim_name} value={_v}\")",
        "                traceback.print_exc()",
        "                raise",
        "    else:",
        "        print(f\"[sim][warn] Unknown simulation type '{_sim_type}' for '{_sim_name}'.\")",
        "",
        "simulationObj.save()",
        "print('Project saved.')",
    ]

    lines += [
        "",
        "if (PLOT_SPARAMS_AFTER_SIM or EXPORT_SPARAMS_AFTER_SIM) and not _last_sweep_executed:",
        "    print('S-parameter post-processing skipped because no sweep simulation was executed.')",
    ]

    if fit_enabled and bool(run_sweep) and has_sweep_like:
        lines += [
            "",
            "# Optional post-processing: fit S-parameters on N points and store NPZ output.",
            f"SPARAM_FIT_NPOINTS = {fit_npoints}",
            f"SPARAM_FIT_POLY_ORDER = {fit_poly_order}",
            "try:",
            "    print(f'[fit] S-parameter fitting enabled (N={SPARAM_FIT_NPOINTS}, order={SPARAM_FIT_POLY_ORDER}).')",
            "",
            "    def _call_if_needed(value):",
            "        if callable(value):",
            "            try:",
            "                return value()",
            "            except Exception:",
            "                return None",
            "        return value",
            "",
            "    def _first_attr(obj, names):",
            "        if obj is None:",
            "            return None",
            "        for name in names:",
            "            try:",
            "                if isinstance(obj, dict) and name in obj:",
            "                    v = _call_if_needed(obj[name])",
            "                    if v is not None:",
            "                        return v",
            "                if hasattr(obj, name):",
            "                    v = _call_if_needed(getattr(obj, name))",
            "                    if v is not None:",
            "                        return v",
            "                getter = f'get_{name}'",
            "                if hasattr(obj, getter):",
            "                    v = _call_if_needed(getattr(obj, getter))",
            "                    if v is not None:",
            "                        return v",
            "            except Exception:",
            "                continue",
            "        return None",
            "",
            "    def _as_1d_freq(raw):",
            "        if raw is None:",
            "            return None",
            "        try:",
            "            arr = np.asarray(raw, dtype=float).reshape(-1)",
            "            if arr.size < 2:",
            "                return None",
            "            return arr",
            "        except Exception:",
            "            return None",
            "",
            "    def _as_s_array(raw):",
            "        if raw is None:",
            "            return None",
            "        try:",
            "            arr = np.asarray(raw)",
            "            if arr.size == 0:",
            "                return None",
            "            if np.isrealobj(arr):",
            "                arr = arr.astype(np.float64) + 0.0j",
            "            else:",
            "                arr = arr.astype(np.complex128)",
            "            return arr",
            "        except Exception:",
            "            return None",
            "",
            "    result_obj = locals().get('simulationResult', None)",
            "    if result_obj is None:",
            "        result_obj = _first_attr(simulationObj, ['result', 'results', 'simulationResult'])",
            "    mw_obj = _first_attr(simulationObj, ['mw'])",
            "    if result_obj is None and mw_obj is not None:",
            "        result_obj = _first_attr(mw_obj, ['result', 'results', 'simulationResult'])",
            "",
            "    freq = _as_1d_freq(_first_attr(result_obj, ['freq_hz', 'frequency_hz', 'freq', 'frequency', 'frequencies', 'f']))",
            "    if freq is None and mw_obj is not None:",
            "        freq = _as_1d_freq(_first_attr(mw_obj, ['freq_hz', 'frequency_hz', 'freq', 'frequency', 'frequencies', 'f']))",
            "    if freq is None:",
            "        freq = np.linspace(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, npoints)",
            "        print('[fit][info] Frequency vector not found in result object. Using sweep range grid.')",
            "",
            "    s_raw = _first_attr(result_obj, ['s_matrix', 'S_matrix', 'sparameters', 'SParameters', 'sparams', 's_parameter', 's', 'S'])",
            "    if s_raw is None and isinstance(result_obj, dict):",
            "        nested = _first_attr(result_obj, ['data', 'results', 'output'])",
            "        s_raw = _first_attr(nested, ['s_matrix', 'S_matrix', 'sparameters', 'SParameters', 'sparams', 's_parameter', 's', 'S'])",
            "    if s_raw is None and mw_obj is not None:",
            "        s_raw = _first_attr(mw_obj, ['s_matrix', 'S_matrix', 'sparameters', 'SParameters', 'sparams', 's_parameter', 's', 'S'])",
            "",
            "    s_arr = _as_s_array(s_raw)",
            "    if s_arr is None:",
            "        print('[fit][warn] Could not locate S-parameter matrix in simulation result. Skipping fit.')",
            "    else:",
            "        if s_arr.ndim == 1:",
            "            s_arr = s_arr[:, np.newaxis, np.newaxis]",
            "        elif s_arr.ndim == 2:",
            "            if s_arr.shape[0] == freq.size:",
            "                s_arr = s_arr[:, :, np.newaxis]",
            "            elif s_arr.shape[1] == freq.size:",
            "                s_arr = s_arr.T[:, :, np.newaxis]",
            "        elif s_arr.shape[0] != freq.size:",
            "            matched_axis = None",
            "            for ax in range(s_arr.ndim):",
            "                if s_arr.shape[ax] == freq.size:",
            "                    matched_axis = ax",
            "                    break",
            "            if matched_axis is not None:",
            "                s_arr = np.moveaxis(s_arr, matched_axis, 0)",
            "",
            "        if s_arr.shape[0] != freq.size:",
            "            print(f'[fit][warn] Frequency size ({freq.size}) does not match S data axis ({s_arr.shape[0]}). Skipping fit.')",
            "        else:",
            "            fit_n = int(max(2, SPARAM_FIT_NPOINTS))",
            "            fit_order = int(max(1, min(SPARAM_FIT_POLY_ORDER, freq.size - 1)))",
            "            fit_freq = np.linspace(float(np.min(freq)), float(np.max(freq)), fit_n)",
            "            s_fit = np.empty((fit_n,) + s_arr.shape[1:], dtype=np.complex128)",
            "            coeff_shape = (fit_order + 1,) + s_arr.shape[1:]",
            "            coeff_re = np.empty(coeff_shape, dtype=np.float64)",
            "            coeff_im = np.empty(coeff_shape, dtype=np.float64)",
            "",
            "            for idx in np.ndindex(s_arr.shape[1:]):",
            "                trace = s_arr[(slice(None),) + idx]",
            "                cre = np.polyfit(freq, np.real(trace), fit_order)",
            "                cim = np.polyfit(freq, np.imag(trace), fit_order)",
            "                coeff_re[(slice(None),) + idx] = cre",
            "                coeff_im[(slice(None),) + idx] = cim",
            "                s_fit[(slice(None),) + idx] = np.polyval(cre, fit_freq) + 1j * np.polyval(cim, fit_freq)",
            "",
            "            fit_dir = os.path.join(currDir, PROJECT_NAME)",
            "            os.makedirs(fit_dir, exist_ok=True)",
            "            out_npz = os.path.join(fit_dir, f'{PROJECT_NAME}_sparam_fit.npz')",
            "            np.savez(",
            "                out_npz,",
            "                freq_hz=np.asarray(freq, dtype=np.float64),",
            "                s_matrix=np.asarray(s_arr, dtype=np.complex128),",
            "                freq_fit_hz=np.asarray(fit_freq, dtype=np.float64),",
            "                s_matrix_fit=np.asarray(s_fit, dtype=np.complex128),",
            "                coeff_real=np.asarray(coeff_re, dtype=np.float64),",
            "                coeff_imag=np.asarray(coeff_im, dtype=np.float64),",
            "                fit_order=np.asarray([fit_order], dtype=np.int32),",
            "            )",
            "            print(f'[fit] Saved fitted S-parameters to: {out_npz}')",
            "            print(f'[fit] Raw shape={tuple(s_arr.shape)}, fitted shape={tuple(s_fit.shape)}')",
            "except Exception:",
            "    print('[fit][warn] S-parameter fitting failed. Continuing without fitted output.')",
            "    traceback.print_exc()",
        ]

    return "\n".join(lines) + "\n"
