"""Generate runnable Python script for EMERGE simulation."""
from __future__ import annotations

from typing import Any, Dict, List
import json


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
        return int(value)
    except Exception:
        return int(default)


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
        er = float(rec.get("er", 1.0))
        tan_d = float(rec.get("tan_d", 0.0))
        sigma = float(rec.get("sigma", 0.0))
        color = str(rec.get("color", "#bebebe"))
        opacity = float(rec.get("opacity", 0.85))

        lines += [
            f"materials[{_q(name)}] = em.Material(name={_q(name)}, er={er}, ur=1.0, tand={tan_d}, cond={sigma})",
            f"materials[{_q(name)}].color = {_q(color)}",
            f"materials[{_q(name)}].opacity = {opacity}",
            "",
        ]

    return lines


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
) -> str:
    sim = settings.get("simulation", {})
    fmin = float(sim.get("Fmin_GHz", 0.0))
    fmax = float(sim.get("Fmax_GHz", 10.0))
    fstep = float(sim.get("Fstep_GHz", 0.1))

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

    mesh = settings.get("mesh", {})
    lines_per_wavelength = float(mesh.get("LinesPerWavelength", 10.0) or 10.0)
    mesh_resolution = 1.0 / max(lines_per_wavelength, 1e-9)

    used_materials = sorted({str(e.get("material", "PEC")) for e in step_entries})

    lines: List[str] = [
        "# Auto-generated EMERGE Python script from EM 3D Modeler",
        "# This script is intended to be run with: python -u <script.py>",
        "",
        "import datetime",
        "import emerge_iron",
        "",
        "from emerge_config import config",
        "config.set_pardiso_threads(8)",
        "config.set_acc_threads(10)",
        "",
        "import math",
        "import numpy as np",
        "import emerge as em",
        "import os",
        "import tempfile",
        "import shutil",
        "import traceback",
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
        "",
        "npoints = int(max(2, round((FMAX_GHZ - FMIN_GHZ) / max(FSTEP_GHZ, 1e-9)) + 1))",
        "simulationObj = em.Simulation(PROJECT_NAME)",
        "simulationObj.check_version(\"2.5.5\")",
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
            origin = [float(v) * 0.001 for v in p.get("origin", [0.0, 0.0, 0.0])]
            u = [float(v) * 0.001 for v in p.get("u", [0.0, 0.0, 0.0])]
            v = [float(v) * 0.001 for v in p.get("v", [0.0, 0.0, 0.0])]
            width = abs(float(p.get("width", 0.0)) * 0.001)
            height = abs(float(p.get("height", 0.0)) * 0.001)
            direction = [float(vd) for vd in p.get("direction", [0.0, 0.0, 1.0])]
            z0 = float(p.get("z0", 50.0))
            power = float(p.get("power", 1.0))

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
        "if RUN_SWEEP:",
        "    try:",
        "        simulationResult = simulationObj.mw.run_sweep()",
        "        print('Simulation completed successfully.')",
        "    except Exception:",
        "        print('Simulation execution failed:')",
        "        traceback.print_exc()",
        "        raise",
        "else:",
        "    print('Run Sweep disabled by user option.')",
        "",
        "simulationObj.save()",
        "print('Project saved.')",
    ]

    if fit_enabled:
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
