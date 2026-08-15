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
    eff_pardiso_threads = max(1, int(pardiso_threads if parallel_enabled else 1))
    eff_acc_threads = max(1, int(acc_threads if parallel_enabled else 1))
    used_materials = sorted({str(e.get("material", "PEC")) for e in step_entries})
    detected_emerge_version = _detect_emerge_version()
    farfield_outputs = []
    raw_outputs = settings.get("outputs", []) if isinstance(settings, dict) else []
    for output in raw_outputs if isinstance(raw_outputs, list) else []:
        if not isinstance(output, dict) or not bool(output.get("enabled", True)):
            continue
        plot_type = str(output.get("plot_type", "")).strip()
        if plot_type not in {"plot_ff", "plot_ff_polar"}:
            continue
        params = output.get("params", {}) if isinstance(output.get("params", {}), dict) else {}
        farfield_outputs.append({
            "name": str(output.get("name", "FarField")).strip() or "FarField",
            "plot_type": plot_type,
            "component": str(params.get("far_field_component", "E")).strip() or "E",
            "db": bool(params.get("far_field_db", False)),
            "points": max(8, _to_int(params.get("far_field_points", 361), 361)),
            "phi_deg": _to_float(params.get("far_field_phi_deg", params.get("far_field_theta_deg", 0.0)), 0.0),
            "frequency_ghz": _to_float(params.get("far_field_frequency_ghz", fmax), fmax),
        })

    lines: List[str] = [
        "# Auto-generated EMERGE Python script from EM 3D Modeler",
        "# Single simulation job script.",
        "",
        "import os",
        "import sys",
        "import traceback",
        "",
        "try:",
        "    import emerge_iron  # optional compatibility bootstrap",
        "except Exception:",
        "    pass",
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
        "# Ensure Unicode output does not crash on Windows cp1252 consoles.",
        "try:",
        "    sys.stdout.reconfigure(encoding='utf-8', errors='replace')",
        "except Exception:",
        "    pass",
        "try:",
        "    sys.stderr.reconfigure(encoding='utf-8', errors='replace')",
        "except Exception:",
        "    pass",
        "",
        "from emerge_config import config",
        f"config.set_pardiso_threads({eff_pardiso_threads})",
        f"config.set_acc_threads({eff_acc_threads})",
        "",
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
        f"EIGENMODE_COUNT = {mode_count}",
        f"PARAM_NAME = {_q(param_name)}",
        f"PARAM_VALUES = {_q(param_values)}",
        f"PLOT_SPARAMS_AFTER_SIM = {bool(plot_sparams_after_sim)}",
        f"EXPORT_SPARAMS_AFTER_SIM = {bool(export_sparams_after_sim)}",
        f"SPARAM_FIT_ENABLED = {fit_enabled}",
        f"SPARAM_FIT_POINTS = {fit_points}",
        f"FARFIELD_OUTPUTS = {repr(farfield_outputs)}",
        "print(f\"[job] {JOB_NAME} | type={JOB_TYPE} | range={FMIN_GHZ}..{FMAX_GHZ} GHz step {FSTEP_GHZ}\")",
        "",
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
        "",
        "def _safe_token(v: str) -> str:",
        "    t = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '_' for ch in str(v))",
        "    t = t.strip('_')",
        "    return t or 'simulation'",
        "",
        "def _try_methods(target, method_names, args_variants, kwargs_variants):",
        "    if target is None:",
        "        return (False, None, None)",
        "    for _name in method_names:",
        "        _fn = getattr(target, _name, None)",
        "        if not callable(_fn):",
        "            continue",
        "        for _args in args_variants:",
        "            try:",
        "                _ret = _fn(*_args)",
        "                return (True, _name, _ret)",
        "            except TypeError:",
        "                pass",
        "            except Exception:",
        "                pass",
        "        for _kwargs in kwargs_variants:",
        "            try:",
        "                _ret = _fn(**_kwargs)",
        "                return (True, _name, _ret)",
        "            except TypeError:",
        "                pass",
        "            except Exception:",
        "                pass",
        "    return (False, None, None)",
        "",
        "def _postprocess_sparams(sim_obj, sim_result):",
        "    if JOB_TYPE not in ('sweep', 'parametric'):",
        "        return",
        "    out_base = os.path.join(SCRIPT_DIR, f\"{_safe_token(PROJECT_NAME)}_{_safe_token(JOB_NAME)}\")",
        "",
        "    if EXPORT_SPARAMS_AFTER_SIM:",
        "        exported = False",
        "        # Resolve MWData: run_sweep() returns it directly; also available via sim_obj.data.mw",
        "        _mwdata_candidates = []",
        "        if sim_result is not None and hasattr(sim_result, 'scalar'):",
        "            _mwdata_candidates.append(sim_result)",
        "        try:",
        "            _d = sim_obj.data",
        "            if hasattr(_d, 'mw') and hasattr(_d.mw, 'scalar'):",
        "                _mwdata_candidates.append(_d.mw)",
        "        except Exception:",
        "            pass",
        "        for _mwd in _mwdata_candidates:",
        "            try:",
        "                _spgrid = _mwd.scalar.grid",
        "                if _spgrid is not None and hasattr(_spgrid, 'export_touchstone'):",
        "                    _spgrid.export_touchstone(out_base, Z0ref=50, format='RI', funit='GHz')",
        "                    import glob as _glob",
        "                    _written = _glob.glob(out_base + '.s*p')",
        "                    _out = _written[0] if _written else out_base",
        "                    print(f'[job] S-parameters exported: {_out}')",
        "                    exported = True",
        "                    break",
        "            except Exception as _ex:",
        "                print(f'[job][warn] export_touchstone failed: {_ex}')",
        "        if not exported:",
        "            print('[job][warn] Could not export S-parameters: no compatible EMERGE API found.')",
        "",
        "    if PLOT_SPARAMS_AFTER_SIM:",
        "        _mwdata_plot = []",
        "        if sim_result is not None and hasattr(sim_result, 'scalar'):",
        "            _mwdata_plot.append(sim_result)",
        "        try:",
        "            _dp = sim_obj.data",
        "            if hasattr(_dp, 'mw') and hasattr(_dp.mw, 'scalar'):",
        "                _mwdata_plot.append(_dp.mw)",
        "        except Exception:",
        "            pass",
        "        for _mwd in _mwdata_plot:",
        "            try:",
        "                from emerge.plot import plot_sp as _plot_sp",
        "                _sg = _mwd.scalar.grid",
        "                _nports = len(getattr(_sg, 'ports', [])) or 1",
        "                if SPARAM_FIT_ENABLED and hasattr(_sg, 'dense_f') and hasattr(_sg, 'model_S'):",
        "                    _freqs = _sg.dense_f(SPARAM_FIT_POINTS)",
        "                    _curves = [_sg.model_S(i, j, _freqs) for i in range(1, _nports + 1) for j in range(1, _nports + 1)]",
        "                else:",
        "                    _freqs = _sg.freq if hasattr(_sg, 'freq') else _mwd.scalar.grid.freq",
        "                    _curves = [_sg.S(i, j) for i in range(1, _nports + 1) for j in range(1, _nports + 1)]",
        "                _labels = [f'S{i}{j}' for i in range(1, _nports+1) for j in range(1, _nports+1)]",
        "                _plot_sp(_freqs, _curves, labels=_labels)",
        "                print('[job] S-parameters plotted')",
        "                break",
        "            except Exception as _pex:",
        "                print(f'[job][warn] plot_sp failed: {_pex}')",
        "                break",
        "",
        "def _postprocess_farfields(sim_obj):",
        "    if not FARFIELD_OUTPUTS:",
        "        return",
        "    import numpy as _np",
        "    try:",
        "        from emerge.plot import plot_ff as _plot_ff, plot_ff_polar as _plot_ff_polar",
        "        _field_dataset = sim_obj.data.mw.field",
        "        _surfaces = globals().get('_farfield_surfaces', [])",
        "        if not _surfaces:",
        "            raise RuntimeError('No integration surface was generated for far-field calculation.')",
        "        _faces = _surfaces[0]",
        "        if len(_surfaces) > 1:",
        "            try:",
        "                from emerge._emerge.geo.operations import GeoSurface as _GeoSurface",
        "                _faces = _GeoSurface.merged(_surfaces)",
        "            except Exception:",
        "                pass",
        "        for _cfg in FARFIELD_OUTPUTS:",
        "            _frequency_ghz = float(_cfg.get('frequency_ghz', FMAX_GHZ))",
        "            _field = _field_dataset.find(freq=_frequency_ghz * 1e9)",
        "            if _field is None:",
        "                raise RuntimeError(f'No saved microwave field found at {_frequency_ghz} GHz.')",
        "            _n = int(_cfg.get('points', 361))",
        "            _theta = _np.linspace(0.0, 2.0 * _np.pi, _n)",
        "            _phi = _np.full_like(_theta, _np.deg2rad(float(_cfg.get('phi_deg', 0.0))))",
        "            _e, _h, _ptot = _field.farfield(_theta, _phi, _faces)",
        "            _component = str(_cfg.get('component', 'E')).lower()",
        "            if _component in ('e_theta', 'etheta'):",
        "                _values = _e[0]",
        "            elif _component in ('e_phi', 'ephi'):",
        "                _values = _e[1]",
        "            else:",
        "                _values = _np.sqrt(_np.sum(_np.abs(_e) ** 2, axis=0))",
        "            _values = _np.abs(_values)",
        "            _base = os.path.join(SCRIPT_DIR, _safe_token(_cfg.get('name', 'FarField')))",
        "            _np.savetxt(_base + '.farfield.txt', _np.column_stack((_theta, _values)), header='theta_rad value')",
        "            if _cfg.get('plot_type') == 'plot_ff_polar':",
        "                _plot_ff_polar(_theta, _values, dB=bool(_cfg.get('db', False)), labels=[str(_cfg.get('name', 'FarField'))])",
        "            else:",
        "                _plot_ff(_theta, _values, dB=bool(_cfg.get('db', False)), labels=[str(_cfg.get('name', 'FarField'))])",
        "            print(f'[job] Far-field generated: {_base}.farfield.txt')",
        "    except Exception as _ff_exc:",
        "        print(f'[job][warn] Far-field generation failed: {_ff_exc}')",
        "",
    ]

    lines += _material_block(used_materials, materials_catalog)

    lines += [
        "# One STEP file per object, exported in this bundle directory.",
        "_farfield_surfaces = []",
        "",
    ]

    if step_entries:
        for entry in step_entries:
            obj_name = str(entry.get("object_name", "Object"))
            step_file = str(entry.get("step_file", ""))
            material = str(entry.get("material", "PEC"))
            priority = int(entry.get("priority", 5000))

            lines += [
                f"stepObjectGroup = em.geo.step.STEPItems(name={_q(obj_name)}, filename=os.path.join(SCRIPT_DIR, {_q(step_file)}), unit=mm)",
                "for geoObj in stepObjectGroup.objects:",
                f"    geoObj.prio_set({priority})",
                f"    _mat = materials.get({_q(material)})",
                "    if _mat is not None:",
                "        geoObj.set_material(_mat)",
                "try:",
                "    _farfield_surfaces.append(stepObjectGroup.as_surface())",
                "except Exception as _surface_exc:",
                "    print(f'[job][warn] Could not register far-field surface: {_surface_exc}')",
                "",
            ]
    else:
        lines += [
            "print('No STEP entries generated from current scene.')",
            "",
        ]

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
                f"_port_{idx}_origin = ({_q(origin[0])}, {_q(origin[1])}, {_q(origin[2])})",
                f"_port_{idx}_u = ({_q(u[0])}, {_q(u[1])}, {_q(u[2])})",
                f"_port_{idx}_v = ({_q(v[0])}, {_q(v[1])}, {_q(v[2])})",
                f"port[{idx}]['object'] = em.geo.Plate(",
                f"    name={_q(name)},",
                f"    origin=_port_{idx}_origin,",
                f"    u=_port_{idx}_u,",
                f"    v=_port_{idx}_v,",
                ")",
                "",
            ]

    lines += [
        "simulationObj.commit_geometry()",
    ]
    if show_model:
        lines += [
            "simulationObj.view()",
        ]
    lines += [
        "simulationObj.mw.set_frequency_range(FMIN_GHZ * 1e9, FMAX_GHZ * 1e9, NPOINTS)",
        "simulationObj.mw.set_resolution(MESH_RESOLUTION)",
        "try:",
        "    simulationObj.data.mw.field.save_fields = True",
        "except Exception as _field_cfg_exc:",
        "    print(f'[job][warn] Could not enable microwave field saving: {_field_cfg_exc}')",
        "",
    ]

    if ports:
        lines += [
            "# Apply lumped ports immediately before mesh generation",
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

    lines += [
        "simulationObj.generate_mesh()",
        "",
    ]
    if show_mesh:
        lines += [
            "simulationObj.view(plot_mesh=True)",
            "",
        ]

    lines += [
        "simulationResult = None",
        "if JOB_TYPE == 'sweep':",
        "    simulationResult = simulationObj.mw.run_sweep()",
        "    print(f\"[job] Sweep completed: {JOB_NAME}\")",
        "elif JOB_TYPE == 'eigenmode':",
        "    _done = False",
        "    for _method in ('run_eigenmode', 'eigenmode', 'solve_eigenmode'):",
        "        _fn = getattr(simulationObj.mw, _method, None)",
        "        if not callable(_fn):",
        "            continue",
        "        try:",
        "            simulationResult = _fn(EIGENMODE_COUNT)",
        "            print(f\"[job] Eigenmode completed ({_method}) count={EIGENMODE_COUNT}\")",
        "            _done = True",
        "            break",
        "        except TypeError:",
        "            try:",
        "                simulationResult = _fn()",
        "                print(f\"[job] Eigenmode completed ({_method})\")",
        "                _done = True",
        "                break",
        "            except Exception:",
        "                pass",
        "    if not _done:",
        "        raise RuntimeError('No compatible eigenmode API found in EMERGE.')",
        "elif JOB_TYPE == 'parametric':",
        "    values = [v.strip() for v in PARAM_VALUES.split(',') if v.strip()]",
        "    if not values:",
        "        values = ['default']",
        "    for _value in values:",
        "        if PARAM_NAME:",
        "            _applied = False",
        "            _num = None",
        "            try:",
        "                _num = float(_value)",
        "            except Exception:",
        "                _num = None",
        "            for _target in (simulationObj, getattr(simulationObj, 'mw', None), getattr(simulationObj, 'settings', None)):",
        "                if _target is None:",
        "                    continue",
        "                if hasattr(_target, PARAM_NAME):",
        "                    try:",
        "                        setattr(_target, PARAM_NAME, _num if _num is not None else _value)",
        "                        _applied = True",
        "                        break",
        "                    except Exception:",
        "                        pass",
        "            if not _applied:",
        "                print(f\"[job][warn] Parameter not applied directly: {PARAM_NAME}\")",
        "        simulationResult = simulationObj.mw.run_sweep()",
        "        print(f\"[job] Parametric sweep value={_value} completed\")",
        "else:",
        "    raise ValueError(f\"Unsupported job type: {JOB_TYPE}\")",
        "",
        "_postprocess_sparams(simulationObj, simulationResult)",
        "_postprocess_farfields(simulationObj)",
        "",
        "simulationObj.save()",
        "print(f\"[job] Saved project for {JOB_NAME}\")",
    ]

    return "\n".join(lines) + "\n"
