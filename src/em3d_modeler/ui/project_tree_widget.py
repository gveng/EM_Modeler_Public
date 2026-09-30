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

"""Project Tree: EMERGE simulation settings editable tree.

Structure
─────────
  [Project Name]
  ├── Boundaries
  │   ├── Xmin  [PML|PEC|PMC|Open|Periodic]
  │   ├── Xmax  …
  │   ├── Ymin / Ymax
  │   └── Zmin / Zmax
  ├── Ports
    │   └── [Port items; use the section context menu to add]
  ├── Simulation
  │   ├── Fmin  [GHz]
  │   ├── Fmax  [GHz]
  │   └── Fstep [GHz]
  └── Mesh
      └── Assigned To Objects (resolution 1/λ)
"""
from __future__ import annotations
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QPushButton, QInputDialog, QComboBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QMenu, QMessageBox, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem,
    QStyle,
)
from PySide6.QtCore import Qt, Signal, QLocale
from PySide6.QtGui import QColor, QBrush, QAction, QIcon
from .formula_widgets import FormulaDoubleSpinBox as QDoubleSpinBox
from ..scene.param_expr import evaluate_expression


# ──────────────────────────────────────────────────────────────────── constants
_BOUNDARY_TYPES = ["PML", "PEC", "PMC", "Open", "Radiation", "Periodic"]
_BOUNDARY_KEYS  = ["Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax"]
_PORT_TYPES = ["WaveguidePort", "LumpedPort", "PlaneWave"]

_PORT_TYPE_DEFAULT_PARAMS = {
    "WaveguidePort": {"Mode": "TE10", "Impedance_Ohm": 50.0, "Excitation": 1.0},
    "LumpedPort": {"Resistance_Ohm": 50.0, "Voltage_V": 1.0,
                   "Direction_X": 0.0, "Direction_Y": 0.0, "Direction_Z": 0.0},
    "PlaneWave": {"Theta_Deg": 0.0, "Phi_Deg": 0.0, "Polarization": "Ex"},
}

_OBJECT_BC_TYPES = ["PML", "PEC", "PMC", "Open", "Periodic", "Radiation"]
_OBJECT_BC_DEFAULT_PARAMS = {
    "PML": {"Layers": 8},
    "PEC": {},
    "PMC": {},
    "Open": {},
    "Periodic": {"PairAxis": "X"},
    "Radiation": {"Order": 1},
}

_LOG_VERBOSITY_LEVELS = ["Trace", "Debug", "Info", "Warning", "Error"]
_SIMULATION_TYPES = ["Sweep", "Eigenmode", "Parametric"]
_OUTPUT_PLOT_TYPES = ["plot_sp", "plot_vswr", "smith", "plot", "plot_ff", "plot_ff_polar", "plot_ff_3d"]

_SECTION_STYLES = {
    "Boundaries": (QStyle.SP_MessageBoxWarning, QColor("#d98c3f"), QColor("#3a2d20")),
    "Assigned To Objects": (QStyle.SP_FileLinkIcon, QColor("#4aa3a2"), QColor("#1e3030")),
    "Ports": (QStyle.SP_DriveNetIcon, QColor("#5b9bd5"), QColor("#1d2a3a")),
    "Simulation": (QStyle.SP_MediaPlay, QColor("#74b86b"), QColor("#213323")),
    "Outputs": (QStyle.SP_FileDialogContentsView, QColor("#b084cc"), QColor("#30243a")),
    "Mesh": (QStyle.SP_DialogApplyButton, QColor("#7f9db9"), QColor("#25303a")),
}

_SECTION_THEME_ICONS = {
    "Outputs": ("office-chart-line", "view-statistics", "x-office-chart"),
}


def _default_simulation_item() -> Dict[str, Any]:
    return {
        "name": "Simulation_1",
        "type": "Sweep",
        "enabled": True,
        "Fmin_GHz": 0.1,
        "Fmax_GHz": 10.0,
        "Fstep_GHz": 0.1,
        "NumberOfPoints": 21,
        "EigenmodeCount": 5,
        "ParamName": "",
        "ParamValues": "",
        "ParamValuesMode": "range",
        "ParamStart": "0",
        "ParamEnd": "1",
        "ParamStep": "1",
        "progressive_sparams_enabled": False,
        "progressive_sparams_chunk_size": 10,
        "sparam_fitting": {"enabled": False, "points": 1001},
        "LogVerbosity": "Info",
    }


def _default_output_item(simulation_name: str, idx: int) -> Dict[str, Any]:
    return {
        "name": f"Output_{idx}",
        "simulation": simulation_name,
        "plot_type": "plot_sp",
        "plot_mode": "final",
        "enabled": True,
    }

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "boundaries": {k: "PML" for k in _BOUNDARY_KEYS},
    "open_region": {"enabled": False, "object": ""},
    "ports":      [],
    "object_boundaries": [],
    "simulation": {"Fmin_GHz": 0.1, "Fmax_GHz": 10.0, "Fstep_GHz": 0.1, "NumberOfPoints": 100, "LogVerbosity": "Info"},
    "simulations": [_default_simulation_item()],
    "outputs": [],
    "mesh":       {"default_fraction": 0.3, "emerge_scale_factor": 1.0, "object_resolutions": {}, "local_refinements": []},
    "runtime": {
        "solver": "PARDISO",
        "parallel_enabled": True,
        "pardiso_threads": 8,
        "acc_threads": 10,
        "plot_sparams_after_sim": True,
        "export_sparams_after_sim": True,
    },
    "material_priorities": {},  # Maps material_name -> priority_value
}


def _parametric_range_values(start: float, end: float, step: float) -> list[str]:
    try:
        first = Decimal(str(start))
        last = Decimal(str(end))
        increment = Decimal(str(step))
    except InvalidOperation as exc:
        raise ValueError("Parameter range values must be numeric.") from exc
    if not all(value.is_finite() for value in (first, last, increment)):
        raise ValueError("Parameter range values must be finite.")
    if increment == 0 or (last > first and increment < 0) or (last < first and increment > 0):
        raise ValueError("Step must be non-zero and point from start toward end.")
    count = int(abs((last - first) / increment)) + 1
    if count > 100000:
        raise ValueError("Parameter range produces more than 100,000 values.")
    values = [first + index * increment for index in range(count)]
    return [format(value.normalize(), "f") for value in values]

_NUMERIC_LOCALE = QLocale.c()


def set_numeric_locale(locale: QLocale) -> None:
    global _NUMERIC_LOCALE
    _NUMERIC_LOCALE = QLocale(locale)


def _parse_locale_float(text: str) -> float:
    s = str(text).strip()
    val, ok = _NUMERIC_LOCALE.toDouble(s)  # val is float, ok is bool
    if ok:
        return float(val)

    normalized = s.replace(" ", "").replace("\u00a0", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    else:
        normalized = normalized.replace(",", ".")
    return float(normalized)


def _parse_locale_int(text: str) -> int:
    s = str(text).strip()
    val, ok = _NUMERIC_LOCALE.toInt(s)  # FIX: val is int, ok is bool!
    if ok:
        return int(val)
    return int(round(_parse_locale_float(s)))


def _format_locale_number(value: float) -> str:
    return _NUMERIC_LOCALE.toString(float(value), 'g', 12)


class ProjectTreeWidget(QWidget):
    """Left-top widget: EMERGE simulation settings tree."""

    settings_changed = Signal()
    output_plot_requested = Signal(dict)
    project_selected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_name = "Untitled"
        self._settings: Dict[str, Any] = _deep_copy(_DEFAULT_SETTINGS)
        self._scene_object_names: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Item", "Value"])
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        
        from PySide6.QtWidgets import QHeaderView
        header = self._tree.header()
        header.setDefaultSectionSize(180)
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.resizeSection(1, 90)
        self._tree.setHeaderHidden(False)
        header.setVisible(True)
        
        self._tree.setAlternatingRowColors(True)
        self._tree.setIndentation(10)  # Reduce indentation (default is 20)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

        self._populate()

    def _on_current_item_changed(self, current, _previous) -> None:
        if current is self._tree.topLevelItem(0):
            self.project_selected.emit()

    def _on_item_clicked(self, item, _column) -> None:
        if item is self._tree.topLevelItem(0):
            self.project_selected.emit()

    # ─────────────────────────────────────────────────── public API
    def set_project_name(self, name: str) -> None:
        self._project_name = name
        if self._tree.topLevelItemCount() > 0:
            self._tree.topLevelItem(0).setText(0, name)

    def get_settings(self) -> Dict[str, Any]:
        return _deep_copy(self._settings)

    def assign_local_mesh_refinements(self, refinements: list[Dict[str, Any]]) -> None:
        mesh_cfg = self._settings.setdefault("mesh", {})
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            self._settings["mesh"] = mesh_cfg
        current = mesh_cfg.get("local_refinements", [])
        if not isinstance(current, list):
            current = []

        object_names = {
            str(item.get("object", "")).strip()
            for item in refinements
            if isinstance(item, dict) and str(item.get("object", "")).strip()
        }
        current = [
            item for item in current
            if not isinstance(item, dict) or str(item.get("object", "")).strip() not in object_names
        ]
        current.extend(_deep_copy(refinements))
        mesh_cfg["local_refinements"] = current
        self._populate()
        local_node = getattr(self, "_m_local_node", None)
        if local_node is not None:
            local_node.setExpanded(True)
        self.settings_changed.emit()

    def set_runtime_settings(self, runtime: Dict[str, Any]) -> None:
        current = self._settings.setdefault("runtime", {})
        if not isinstance(current, dict):
            current = {}
            self._settings["runtime"] = current
        current.update(_deep_copy(runtime))
        self.settings_changed.emit()

    def _parse_formula_float(self, text: str) -> float:
        raw = str(text).strip()
        try:
            return _parse_locale_float(raw)
        except (TypeError, ValueError):
            values = {}
            for entry in self._settings.get("parameters", []):
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if name:
                    try:
                        values[name] = float(entry.get("value", 0.0))
                    except (TypeError, ValueError):
                        pass
            return evaluate_expression(raw, values)

    def set_scene_object_names(self, object_names: list[str]) -> None:
        names: set[str] = set()
        for name in object_names or []:
            s = str(name).strip()
            if s:
                names.add(s)
        self._scene_object_names = names
        self._refresh_ports()
        self._refresh_object_mesh_assignments()

    def get_log_verbosity(self) -> str:
        sims = self._settings.get("simulations", [])
        if isinstance(sims, list):
            for sim in sims:
                if not isinstance(sim, dict):
                    continue
                if bool(sim.get("enabled", True)):
                    value = str(sim.get("LogVerbosity", "Info")).strip()
                    return value if value in _LOG_VERBOSITY_LEVELS else "Info"
        value = str(self._settings.get("simulation", {}).get("LogVerbosity", "Info")).strip()
        return value if value in _LOG_VERBOSITY_LEVELS else "Info"

    def set_log_verbosity(self, verbosity: str) -> None:
        value = str(verbosity).strip().title()
        if value not in _LOG_VERBOSITY_LEVELS:
            value = "Info"
        self._settings.setdefault("simulation", {})["LogVerbosity"] = value
        sims = self._settings.get("simulations", [])
        if isinstance(sims, list):
            for sim in sims:
                if isinstance(sim, dict):
                    sim["LogVerbosity"] = value
        self._sync_legacy_simulation_from_list()
        self._populate()
        self.settings_changed.emit()

    def load_settings(self, settings: Dict[str, Any]) -> None:
        loaded = _deep_copy(settings or {})
        merged = _deep_copy(_DEFAULT_SETTINGS)

        for k, v in loaded.items():
            if isinstance(merged.get(k), dict) and isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k] = v

        if not isinstance(merged.get("ports"), list):
            merged["ports"] = []
        if not isinstance(merged.get("object_boundaries"), list):
            merged["object_boundaries"] = []
        if not isinstance(merged.get("open_region"), dict):
            merged["open_region"] = {"enabled": False, "object": ""}
        runtime = merged.get("runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            merged["runtime"] = runtime
        legacy_progressive_sparams_enabled = bool(
            runtime.pop("progressive_sparams_enabled", False)
        )
        solver = str(runtime.get("solver", "PARDISO")).strip().upper()
        runtime["solver"] = solver if solver in {"PARDISO", "SUPERLU", "UMFPACK", "CUDSS", "AASDS", "MUMPS"} else "PARDISO"
        runtime["parallel_enabled"] = bool(runtime.get("parallel_enabled", True))
        try:
            runtime["pardiso_threads"] = max(1, int(runtime.get("pardiso_threads", 8)))
            runtime["acc_threads"] = max(1, int(runtime.get("acc_threads", 10)))
        except (TypeError, ValueError):
            runtime["pardiso_threads"] = 8
            runtime["acc_threads"] = 10
        runtime["plot_sparams_after_sim"] = bool(runtime.get("plot_sparams_after_sim", True))
        runtime["export_sparams_after_sim"] = bool(runtime.get("export_sparams_after_sim", True))

        if not isinstance(merged.get("simulations"), list):
            merged["simulations"] = []
        if not isinstance(loaded.get("simulations"), list):
            merged["simulations"] = []
        if not isinstance(merged.get("outputs"), list):
            merged["outputs"] = []

        mesh_cfg = merged.get("mesh")
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            merged["mesh"] = mesh_cfg

        try:
            mesh_cfg["default_fraction"] = max(0.01, min(1.0, float(mesh_cfg.get("default_fraction", 0.3))))
        except Exception:
            mesh_cfg["default_fraction"] = 0.3
        try:
            emerge_scale_factor = float(mesh_cfg.get("emerge_scale_factor", 1.0))
            if not math.isfinite(emerge_scale_factor):
                emerge_scale_factor = 1.0
            mesh_cfg["emerge_scale_factor"] = max(1.0, min(1_000_000.0, emerge_scale_factor))
        except Exception:
            mesh_cfg["emerge_scale_factor"] = 1.0

        obj_res = mesh_cfg.get("object_resolutions")
        if not isinstance(obj_res, dict):
            obj_res = {}
        normalized_obj_res: Dict[str, float] = {}
        for k, v in obj_res.items():
            name = str(k).strip()
            if not name:
                continue
            try:
                normalized_obj_res[name] = max(0.01, min(1.0, float(v)))
            except Exception:
                continue
        mesh_cfg["object_resolutions"] = normalized_obj_res

        raw_refinements = mesh_cfg.get("local_refinements", [])
        if not isinstance(raw_refinements, list):
            raw_refinements = []
        normalized_refinements = []
        valid_faces = {"-x", "+x", "-y", "+y", "-z", "+z"}
        for item in raw_refinements:
            if not isinstance(item, dict):
                continue
            object_name = str(item.get("object", "")).strip()
            mode = str(item.get("mode", "boundary")).strip().lower()
            if not object_name or mode not in {"boundary", "face"}:
                continue
            faces = [
                str(face).strip().lower()
                for face in item.get("faces", [])
                if str(face).strip().lower() in valid_faces
            ] if isinstance(item.get("faces", []), list) else []
            try:
                size_mm = max(1e-6, float(item.get("size_mm", 0.25 if mode == "boundary" else 0.1)))
                growth_rate = max(1.001, float(item.get("growth_rate", 3.0)))
                raw_max_size = item.get("max_size_mm")
                max_size_mm = max(1e-6, float(raw_max_size)) if raw_max_size not in (None, "", 0, 0.0) else None
            except (TypeError, ValueError):
                continue
            normalized_refinements.append({
                "object": object_name,
                "enabled": bool(item.get("enabled", True)),
                "mode": mode,
                "faces": faces,
                "size_mm": size_mm,
                "growth_rate": growth_rate,
                "max_size_mm": max_size_mm,
            })
        mesh_cfg["local_refinements"] = normalized_refinements

        # Backward compatibility: lift legacy single simulation dict into the new list model.
        if not merged["simulations"]:
            legacy_sim = merged.get("simulation", {})
            if not isinstance(legacy_sim, dict):
                legacy_sim = {}
            sim = _default_simulation_item()
            sim["name"] = "Simulation_1"
            sim["type"] = "Sweep"
            sim["enabled"] = True
            sim["Fmin_GHz"] = float(legacy_sim.get("Fmin_GHz", sim["Fmin_GHz"]))
            sim["Fmax_GHz"] = float(legacy_sim.get("Fmax_GHz", sim["Fmax_GHz"]))
            sim["Fstep_GHz"] = float(legacy_sim.get("Fstep_GHz", sim["Fstep_GHz"]))
            sim["NumberOfPoints"] = max(2, int(legacy_sim.get("NumberOfPoints", round((sim["Fmax_GHz"] - sim["Fmin_GHz"]) / max(sim["Fstep_GHz"], 1e-9)) + 1)))
            sim["progressive_sparams_enabled"] = bool(
                legacy_sim.get("progressive_sparams_enabled", legacy_progressive_sparams_enabled)
            )
            try:
                sim["progressive_sparams_chunk_size"] = max(
                    1, int(legacy_sim.get("progressive_sparams_chunk_size", 10))
                )
            except (TypeError, ValueError):
                sim["progressive_sparams_chunk_size"] = 10
            lv = str(legacy_sim.get("LogVerbosity", "Info")).strip().title()
            sim["LogVerbosity"] = lv if lv in _LOG_VERBOSITY_LEVELS else "Info"
            merged["simulations"] = [sim]

        for sim in merged["simulations"]:
            if not isinstance(sim, dict):
                continue
            sim["progressive_sparams_enabled"] = bool(
                sim.get("progressive_sparams_enabled", legacy_progressive_sparams_enabled)
            )
            try:
                sim["progressive_sparams_chunk_size"] = max(
                    1, int(sim.get("progressive_sparams_chunk_size", 10))
                )
            except (TypeError, ValueError):
                sim["progressive_sparams_chunk_size"] = 10
            try:
                sim["NumberOfPoints"] = max(2, int(sim.get("NumberOfPoints", round((float(sim.get("Fmax_GHz", 10.0)) - float(sim.get("Fmin_GHz", 0.1))) / max(float(sim.get("Fstep_GHz", 0.1)), 1e-9)) + 1)))
            except (TypeError, ValueError, ZeroDivisionError):
                sim["NumberOfPoints"] = 100

        valid_sim_names = {
            str(item.get("name", "")).strip()
            for item in merged["simulations"]
            if isinstance(item, dict)
        }
        valid_sim_names = {name for name in valid_sim_names if name}
        normalized_outputs = []
        for idx, output in enumerate(merged.get("outputs", []), start=1):
            if not isinstance(output, dict):
                continue
            name = str(output.get("name", f"Output_{idx}")).strip() or f"Output_{idx}"
            simulation = str(output.get("simulation", "")).strip()
            if not simulation and valid_sim_names:
                simulation = sorted(valid_sim_names, key=lambda s: s.lower())[0]
            plot_type = str(output.get("plot_type", "plot_sp")).strip()
            if plot_type not in _OUTPUT_PLOT_TYPES:
                plot_type = "plot_sp"
            normalized_outputs.append(
                {
                    "name": name,
                    "simulation": simulation,
                    "plot_type": plot_type,
                    "plot_mode": (
                        "live"
                        if str(output.get("plot_mode", "final")).strip().lower() == "live"
                        else "final"
                    ),
                    "enabled": bool(output.get("enabled", True)),
                    "params": dict(output.get("params", {})) if isinstance(output.get("params", {}), dict) else {},
                }
            )
        merged["outputs"] = normalized_outputs

        self._settings = merged
        self._sync_legacy_simulation_from_list()
        self._populate()

    def reset_settings(self) -> None:
        """Reset all simulation settings to defaults."""
        self._settings = _deep_copy(_DEFAULT_SETTINGS)
        self._sync_legacy_simulation_from_list()
        self._populate()
        self.settings_changed.emit()

    def _sync_legacy_simulation_from_list(self) -> None:
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list) or not sims:
            return
        chosen = None
        for sim in sims:
            if isinstance(sim, dict) and bool(sim.get("enabled", True)):
                chosen = sim
                break
        if chosen is None and isinstance(sims[0], dict):
            chosen = sims[0]
        if not isinstance(chosen, dict):
            return
        self._settings.setdefault("simulation", {})
        self._settings["simulation"].update(
            {
                "Fmin_GHz": float(chosen.get("Fmin_GHz", 0.1)),
                "Fmax_GHz": float(chosen.get("Fmax_GHz", 10.0)),
                "Fstep_GHz": float(chosen.get("Fstep_GHz", 0.1)),
                "NumberOfPoints": max(2, int(chosen.get("NumberOfPoints", 100))),
                "LogVerbosity": str(chosen.get("LogVerbosity", "Info")).strip().title(),
            }
        )

    # ─────────────────────────────────────────────────── build tree
    def _populate(self) -> None:
        self._tree.clear()
        root = QTreeWidgetItem([self._project_name, ""])
        root.setExpanded(True)
        self._tree.addTopLevelItem(root)

        self._b_node   = self._make_section(root, "Boundaries")
        self._p_node   = self._make_section(root, "Ports")
        self._s_node   = self._make_section(root, "Simulation")
        self._o_node   = self._make_section(root, "Outputs")
        self._m_node   = self._make_section(root, "Mesh")
        self._m_scale_item = self._make_leaf(self._m_node, "EMERGE scale factor", "1")
        self._m_scale_item.setData(0, Qt.UserRole, ("__emerge_scale_factor__", 0))
        self._m_scale_item.setToolTip(
            0,
            "Experimental EMERGE global scale. CAD coordinates and mesh constraints scale together; port width/height remain physical. 1 disables scaling.",
        )

        # Boundaries
        open_region = self._settings.get("open_region", {})
        domain_name = str(open_region.get("object", "")).strip() if isinstance(open_region, dict) else ""
        domain_item = self._make_leaf(self._b_node, "Domain", domain_name or "Auto-detect AIR", editable=True)
        domain_item.setData(0, Qt.UserRole, ("__open_region__", 0))
        if domain_name and self._scene_object_names and domain_name not in self._scene_object_names:
            warn_brush = QBrush(QColor(220, 40, 40))
            domain_item.setForeground(0, warn_brush)
            domain_item.setForeground(1, warn_brush)
        for k in _BOUNDARY_KEYS:
            v = self._settings["boundaries"].get(k, "PML")
            boundary_item = self._make_leaf(self._b_node, k, v, editable=True)
            boundary_item.setData(0, Qt.UserRole, ("__boundary_key__", k))

        self._b_obj_node = self._make_section(self._b_node, "Assigned To Objects")
        self._refresh_object_boundaries()

        # Ports
        self._refresh_ports()

        # Simulation list
        self._refresh_simulations()

        # Outputs list
        self._refresh_outputs()

        # Mesh
        self._m_obj_node = self._make_section(self._m_node, "Assigned To Objects")
        self._m_local_node = self._make_section(self._m_node, "Local Refinements")
        self._m_local_node.setExpanded(True)
        self._refresh_object_mesh_assignments()

        self._tree.expandAll()

    def _make_section(self, parent: QTreeWidgetItem,
                      label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, ""])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        style = _SECTION_STYLES.get(label)
        if style is not None:
            icon, foreground, background = style
            themed_icon = QIcon()
            for icon_name in _SECTION_THEME_ICONS.get(label, ()):
                themed_icon = QIcon.fromTheme(icon_name)
                if not themed_icon.isNull():
                    break
            item.setIcon(0, themed_icon if not themed_icon.isNull() else self.style().standardIcon(icon))
            for column in range(2):
                item.setForeground(column, QBrush(foreground))
                item.setBackground(column, QBrush(background))
        parent.addChild(item)
        return item

    def _make_leaf(self, parent: QTreeWidgetItem, key: str,
                   value: str, editable: bool = False) -> QTreeWidgetItem:
        item = QTreeWidgetItem([key, value])
        if editable:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
        parent.addChild(item)
        return item

    @staticmethod
    def _style_disabled_row(item: QTreeWidgetItem, enabled: bool) -> None:
        if enabled:
            return
        muted = QBrush(QColor("#555555"))
        for column in range(item.columnCount()):
            item.setForeground(column, muted)

    def _refresh_ports(self) -> None:
        self._p_node.takeChildren()
        for i, port in enumerate(self._settings["ports"]):
            try:
                port_number = max(1, int(port.get("number", i + 1)))
            except (TypeError, ValueError):
                port_number = i + 1
            obj_name = str(port.get("object", "")).strip()
            missing_obj = bool(obj_name and obj_name not in self._scene_object_names)
            if obj_name:
                txt = f"Port {port_number}: {port.get('type', '?')} -> {obj_name}"
            else:
                txt = (f"Port {port_number}: {port.get('type','?')} "
                       f"@ [{port.get('x',0)},{port.get('y',0)},{port.get('z',0)}]")
            if missing_obj:
                txt += "  [MISSING OBJECT]"
            row = QTreeWidgetItem([txt, ""])
            if missing_obj:
                warn_brush = QBrush(QColor(220, 40, 40))
                row.setForeground(0, warn_brush)
                row.setForeground(1, warn_brush)
            row.setData(0, Qt.UserRole, ("__port_idx__", i))
            self._p_node.addChild(row)

    def _refresh_simulations(self) -> None:
        self._s_node.takeChildren()
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list):
            sims = []

        for i, sim in enumerate(sims):
            if not isinstance(sim, dict):
                continue
            name = str(sim.get("name", f"Simulation_{i+1}")).strip() or f"Simulation_{i+1}"
            sim_type = str(sim.get("type", "Sweep")).strip().title()
            enabled = bool(sim.get("enabled", True))
            if sim_type == "Eigenmode":
                summary = f"modes={int(sim.get('EigenmodeCount', 5))}"
            elif sim_type == "Parametric":
                pname = str(sim.get("ParamName", "")).strip()
                pvals = str(sim.get("ParamValues", "")).strip()
                summary = f"{pname}={pvals}" if pname and pvals else (pname or pvals or "parametric")
            else:
                fmin = sim.get("Fmin_GHz", 0.1)
                fmax = sim.get("Fmax_GHz", 10.0)
                fstep = sim.get("Fstep_GHz", 0.1)
                summary = f"{fmin:g}..{fmax:g} GHz step {fstep:g}"

            label = f"{name} [{sim_type}] | {summary}"
            row = QTreeWidgetItem([label, ""])
            row.setData(0, Qt.UserRole, ("__sim_idx__", i))
            font = row.font(0)
            font.setPointSize(max(8, font.pointSize() - 1))
            row.setFont(0, font)
            row.setFont(1, font)
            self._style_disabled_row(row, enabled)
            self._s_node.addChild(row)

    def _simulation_names(self) -> list[str]:
        sims = self._settings.get("simulations", [])
        names: list[str] = []
        if isinstance(sims, list):
            for i, sim in enumerate(sims):
                if not isinstance(sim, dict):
                    continue
                name = str(sim.get("name", f"Simulation_{i+1}")).strip() or f"Simulation_{i+1}"
                names.append(name)
        return names

    def _refresh_outputs(self, expand_simulation: str | None = None) -> None:
        node = getattr(self, "_o_node", None)
        if node is None:
            return
        expanded_simulations = {
            node.child(i).text(0)
            for i in range(node.childCount())
            if node.child(i).isExpanded()
        }
        node.takeChildren()

        sim_names = self._simulation_names()
        outputs = self._settings.get("outputs", [])
        if not isinstance(outputs, list):
            outputs = []

        grouped: Dict[str, list[tuple[int, dict]]] = {name: [] for name in sim_names}
        unknown_key = "__unknown__"
        grouped[unknown_key] = []
        for idx, output in enumerate(outputs):
            if not isinstance(output, dict):
                continue
            sim_name = str(output.get("simulation", "")).strip()
            key = sim_name if sim_name in grouped else unknown_key
            grouped.setdefault(key, []).append((idx, output))

        for sim_name in sim_names:
            sim_node = self._make_section(node, sim_name)
            sim_node.setData(0, Qt.UserRole, ("__out_sim__", sim_name))
            sim_node.setExpanded(
                sim_name in expanded_simulations or sim_name == expand_simulation
            )
            rows = grouped.get(sim_name, [])
            for out_idx, output in rows:
                name = str(output.get("name", f"Output_{out_idx+1}")).strip() or f"Output_{out_idx+1}"
                plot_type = str(output.get("plot_type", "plot_sp")).strip()
                enabled = bool(output.get("enabled", True))
                row = QTreeWidgetItem([f"{name} [{plot_type}]", ""])
                row.setData(0, Qt.UserRole, ("__out_idx__", out_idx))
                self._style_disabled_row(row, enabled)
                sim_node.addChild(row)

        unknown_rows = grouped.get(unknown_key, [])
        if unknown_rows:
            unknown_node = self._make_section(node, "Unmapped Outputs")
            for out_idx, output in unknown_rows:
                name = str(output.get("name", f"Output_{out_idx+1}")).strip() or f"Output_{out_idx+1}"
                plot_type = str(output.get("plot_type", "plot_sp")).strip()
                sim_name = str(output.get("simulation", "?")).strip() or "?"
                row = QTreeWidgetItem([f"{name} [{plot_type}] -> {sim_name}", ""])
                row.setData(0, Qt.UserRole, ("__out_idx__", out_idx))
                warn_brush = QBrush(QColor(220, 40, 40))
                row.setForeground(0, warn_brush)
                row.setForeground(1, warn_brush)
                unknown_node.addChild(row)

    def _refresh_object_boundaries(self) -> None:
        self._b_obj_node.takeChildren()
        for i, bc in enumerate(self._settings.get("object_boundaries", [])):
            obj_name = str(bc.get("object", "?")).strip() or "?"
            bc_type = str(bc.get("type", "?")).strip() or "?"
            txt = f"BC {i+1}: {bc_type} -> {obj_name}"
            row = QTreeWidgetItem([txt, ""])
            row.setData(0, Qt.UserRole, ("__bc_idx__", i))
            self._b_obj_node.addChild(row)

    def _refresh_object_mesh_assignments(self) -> None:
        node = getattr(self, "_m_obj_node", None)
        if node is None:
            return
        node.takeChildren()
        local_node = getattr(self, "_m_local_node", None)
        if local_node is not None:
            local_node.takeChildren()

        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}

        default_fraction = 0.3
        try:
            default_fraction = float(mesh_cfg.get("default_fraction", 0.3))
        except Exception:
            default_fraction = 0.3
        default_row = QTreeWidgetItem(["Default resolution (1/λ)", _format_locale_number(default_fraction)])
        default_row.setData(0, Qt.UserRole, ("__mesh_default__", 0))
        node.addChild(default_row)

        if hasattr(self, "_m_scale_item"):
            scale_factor = float(mesh_cfg.get("emerge_scale_factor", 1.0))
            self._m_scale_item.setText(1, _format_locale_number(scale_factor))

        obj_res = mesh_cfg.get("object_resolutions", {})
        if not isinstance(obj_res, dict):
            obj_res = {}

        for obj_name in sorted(obj_res.keys(), key=lambda s: str(s).lower()):
            try:
                val = float(obj_res[obj_name])
            except Exception:
                continue
            row = QTreeWidgetItem([str(obj_name), _format_locale_number(val)])
            row.setData(0, Qt.UserRole, ("__mesh_obj__", str(obj_name)))
            if self._scene_object_names and str(obj_name) not in self._scene_object_names:
                warn_brush = QBrush(QColor(220, 40, 40))
                row.setForeground(0, warn_brush)
                row.setForeground(1, warn_brush)
            node.addChild(row)

        if local_node is None:
            return
        refinements = mesh_cfg.get("local_refinements", [])
        if not isinstance(refinements, list):
            return
        for index, refinement in enumerate(refinements):
            if not isinstance(refinement, dict):
                continue
            object_name = str(refinement.get("object", "?")).strip() or "?"
            mode = str(refinement.get("mode", "boundary")).strip().title()
            faces = ",".join(str(face) for face in refinement.get("faces", []))
            size_mm = float(refinement.get("size_mm", 0.0))
            enabled = bool(refinement.get("enabled", True))
            target = f" [{faces}]" if faces else ""
            row = QTreeWidgetItem([
                f"{object_name}: {mode}{target}",
                f"{size_mm:g} mm",
            ])
            self._style_disabled_row(row, enabled)
            row.setData(0, Qt.UserRole, ("__mesh_refinement__", index))
            max_size = refinement.get("max_size_mm")
            row.setToolTip(
                0,
                f"Object: {object_name}\nMode: {mode}\nFaces: {faces or 'Whole object'}\n"
                f"Size: {size_mm:g} mm\nGrowth rate: {float(refinement.get('growth_rate', 3.0)):g}\n"
                f"Maximum size: {float(max_size):g} mm" if max_size is not None else
                f"Object: {object_name}\nMode: {mode}\nFaces: {faces or 'Whole object'}\n"
                f"Size: {size_mm:g} mm\nGrowth rate: {float(refinement.get('growth_rate', 3.0)):g}\n"
                "Maximum size: Automatic"
            )
            if self._scene_object_names and object_name not in self._scene_object_names:
                warn_brush = QBrush(QColor(220, 40, 40))
                row.setForeground(0, warn_brush)
                row.setForeground(1, warn_brush)
            local_node.addChild(row)

    # ─────────────────────────────────────────────────── editing
    def _on_double_click(self, item: QTreeWidgetItem, col: int) -> None:
        parent = item.parent()
        if parent is None:
            return

        role = item.data(0, Qt.UserRole)

        if isinstance(role, tuple) and len(role) == 2:
            tag, idx = role
            if tag == "__port_idx__":
                self._edit_port_dialog(int(idx))
                return
            if tag == "__bc_idx__":
                self._edit_object_boundary_dialog(int(idx))
                return
            if tag == "__sim_idx__":
                self._edit_simulation_dialog(int(idx))
                return
            if tag == "__out_idx__":
                self._run_output_plot(int(idx))
                return
            if tag == "__mesh_default__":
                self._edit_default_mesh_resolution()
                return
            if tag == "__emerge_scale_factor__":
                self._edit_emerge_scale_factor()
                return
            if tag == "__open_region__":
                self._edit_open_region_domain()
                return
            if tag == "__mesh_refinement__":
                self._edit_local_mesh_refinement(int(idx))
                return

        key = item.text(0)
        val = item.text(1)

        # Boundary
        if parent is self._b_node:
            self._edit_boundary(item, key)
        # Simulation numeric
        elif parent is self._s_node:
            return
        # Mesh values are assigned from Object/Materials context menu.
        elif parent is self._m_node:
            return

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return

        role = item.data(0, Qt.UserRole)
        if isinstance(role, tuple) and len(role) == 2 and role[0] in {"__bc_idx__", "__boundary_key__"}:
            tag, value = role
            selected_items = self._tree.selectedItems()
            context_items = selected_items if item in selected_items else [item]
            values = sorted({
                selected.data(0, Qt.UserRole)[1]
                for selected in context_items
                if isinstance(selected.data(0, Qt.UserRole), tuple)
                and len(selected.data(0, Qt.UserRole)) == 2
                and selected.data(0, Qt.UserRole)[0] == tag
            }, key=lambda entry: str(entry).casefold())
            if value not in values:
                values.append(value)
            menu = QMenu(self._tree)
            if tag == "__bc_idx__":
                indices = sorted({int(index) for index in values})
                edit_label = "Edit Assignment…" if len(indices) == 1 else f"Edit {len(indices)} Assignments…"
                act_edit = QAction(edit_label, menu)
                act_edit.triggered.connect(lambda: self._edit_object_boundary_assignments(indices))
                menu.addAction(act_edit)
                remove_label = "Remove Assignment" if len(indices) == 1 else f"Remove {len(indices)} Assignments"
                act_remove = QAction(remove_label, menu)
                act_remove.triggered.connect(lambda: self._remove_object_boundary_assignments(indices))
                menu.addAction(act_remove)
            else:
                keys = [str(key) for key in values]
                edit_label = "Edit Boundary…" if len(keys) == 1 else f"Set {len(keys)} Boundaries…"
                act_edit = QAction(edit_label, menu)
                act_edit.triggered.connect(lambda: self._edit_boundaries(keys))
                menu.addAction(act_edit)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        if isinstance(role, tuple) and len(role) == 2 and role[0] == "__mesh_obj__":
            selected_items = self._tree.selectedItems()
            context_items = selected_items if item in selected_items else [item]
            object_names = sorted({
                str(selected.data(0, Qt.UserRole)[1])
                for selected in context_items
                if isinstance(selected.data(0, Qt.UserRole), tuple)
                and len(selected.data(0, Qt.UserRole)) == 2
                and selected.data(0, Qt.UserRole)[0] == "__mesh_obj__"
            }, key=str.casefold)
            if str(role[1]) not in object_names:
                object_names.append(str(role[1]))

            menu = QMenu(self._tree)
            edit_label = (
                "Set mesh resolution…" if len(object_names) == 1
                else f"Set resolution for {len(object_names)} objects…"
            )
            act_edit = QAction(edit_label, menu)
            act_edit.triggered.connect(
                lambda: self._edit_object_mesh_assignments(object_names)
            )
            menu.addAction(act_edit)

            remove_label = (
                "Remove Mesh Assignment" if len(object_names) == 1
                else f"Remove {len(object_names)} Mesh Assignments"
            )
            act_remove = QAction(remove_label, menu)
            act_remove.triggered.connect(
                lambda: self._remove_object_mesh_assignments(object_names)
            )
            menu.addAction(act_remove)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        menu = QMenu(self._tree)
        if item is self._p_node:
            action = QAction("Add Port…", menu)
            action.triggered.connect(self._add_port_dialog)
            menu.addAction(action)
        elif item is self._s_node:
            action = QAction("Add Simulation…", menu)
            action.triggered.connect(self._add_simulation_dialog)
            menu.addAction(action)
        elif item is self._o_node:
            action = QAction("Add Output Plot…", menu)
            action.triggered.connect(lambda: self._add_output_dialog(None))
            menu.addAction(action)
        else:
            menu = None

        if menu is not None:
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        role = item.data(0, Qt.UserRole)
        if not (isinstance(role, tuple) and len(role) == 2):
            return

        tag, idx = role
        menu = QMenu(self._tree)

        if tag == "__port_idx__":
            act_edit = QAction("Edit Assignment…", menu)
            act_edit.triggered.connect(lambda: self._edit_port_dialog(int(idx)))
            menu.addAction(act_edit)

            act_remove = QAction("Remove Assignment", menu)
            act_remove.triggered.connect(lambda: self._remove_port_assignment(int(idx)))
            menu.addAction(act_remove)

        elif tag == "__sim_idx__":
            act_edit = QAction("Edit Simulation…", menu)
            act_edit.triggered.connect(lambda: self._edit_simulation_dialog(int(idx)))
            menu.addAction(act_edit)

            sims = self._settings.get("simulations", [])
            enabled = False
            if isinstance(sims, list) and 0 <= int(idx) < len(sims) and isinstance(sims[int(idx)], dict):
                enabled = bool(sims[int(idx)].get("enabled", True))
            act_toggle = QAction("Disable" if enabled else "Enable", menu)
            act_toggle.triggered.connect(lambda: self._toggle_simulation_enabled(int(idx)))
            menu.addAction(act_toggle)

            act_remove = QAction("Remove Simulation", menu)
            act_remove.triggered.connect(lambda: self._remove_simulation(int(idx)))
            menu.addAction(act_remove)
        elif tag == "__mesh_default__":
            act_edit = QAction("Edit Default Mesh Resolution…", menu)
            act_edit.triggered.connect(self._edit_default_mesh_resolution)
            menu.addAction(act_edit)
        elif tag == "__mesh_refinement__":
            refinement_index = int(idx)
            refinements = self._settings.get("mesh", {}).get("local_refinements", [])
            enabled = False
            if (
                isinstance(refinements, list)
                and 0 <= refinement_index < len(refinements)
                and isinstance(refinements[refinement_index], dict)
            ):
                enabled = bool(refinements[refinement_index].get("enabled", True))

            act_edit = QAction("Edit Refinement…", menu)
            act_edit.triggered.connect(lambda: self._edit_local_mesh_refinement(refinement_index))
            menu.addAction(act_edit)

            act_toggle = QAction("Disable" if enabled else "Enable", menu)
            act_toggle.triggered.connect(lambda: self._toggle_local_mesh_refinement(refinement_index))
            menu.addAction(act_toggle)

            act_remove = QAction("Remove Refinement", menu)
            act_remove.triggered.connect(lambda: self._remove_local_mesh_refinement(refinement_index))
            menu.addAction(act_remove)
        elif tag == "__out_idx__":
            act_plot = QAction("Generate Plot", menu)
            act_plot.triggered.connect(lambda: self._run_output_plot(int(idx)))
            menu.addAction(act_plot)

            act_edit = QAction("Edit Output…", menu)
            act_edit.triggered.connect(lambda: self._edit_output_dialog(int(idx)))
            menu.addAction(act_edit)

            outputs = self._settings.get("outputs", [])
            enabled = False
            if isinstance(outputs, list) and 0 <= int(idx) < len(outputs) and isinstance(outputs[int(idx)], dict):
                enabled = bool(outputs[int(idx)].get("enabled", True))
            act_toggle = QAction("Disable" if enabled else "Enable", menu)
            act_toggle.triggered.connect(lambda: self._toggle_output_enabled(int(idx)))
            menu.addAction(act_toggle)

            act_remove = QAction("Remove Output", menu)
            act_remove.triggered.connect(lambda: self._remove_output(int(idx)))
            menu.addAction(act_remove)
        elif tag == "__out_sim__":
            act_add = QAction("Add Output Plot…", menu)
            act_add.triggered.connect(lambda: self._add_output_dialog(str(idx)))
            menu.addAction(act_add)
        else:
            return

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _remove_port_assignment(self, index: int) -> None:
        ports = self._settings.get("ports", [])
        if not (0 <= index < len(ports)):
            return
        port = ports[index]
        name = str(port.get("name", f"Port {index + 1}"))
        reply = QMessageBox.question(
            self,
            "Remove Port Assignment",
            f"Remove assignment '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ports.pop(index)
        self._refresh_ports()
        self.settings_changed.emit()

    def _remove_object_boundary_assignment(self, index: int) -> None:
        self._remove_object_boundary_assignments([index])

    def _remove_object_boundary_assignments(self, indices: list[int]) -> None:
        rows = self._settings.get("object_boundaries", [])
        valid_indices = sorted({int(index) for index in indices if 0 <= int(index) < len(rows)})
        if not valid_indices:
            return
        names = [
            str(rows[index].get("name", f"BC {index + 1}"))
            for index in valid_indices
        ]
        if len(names) == 1:
            prompt = f"Remove assignment '{names[0]}'?"
        else:
            prompt = f"Remove {len(names)} boundary assignments?\n\n" + "\n".join(names)
        reply = QMessageBox.question(
            self,
            "Remove Boundary Assignment" if len(names) == 1 else "Remove Boundary Assignments",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for index in reversed(valid_indices):
            rows.pop(index)
        self._refresh_object_boundaries()
        self.settings_changed.emit()

    def _edit_object_boundary_assignments(self, indices: list[int]) -> None:
        rows = self._settings.get("object_boundaries", [])
        valid_indices = sorted({int(index) for index in indices if 0 <= int(index) < len(rows)})
        if not valid_indices:
            return
        if len(valid_indices) == 1:
            self._edit_object_boundary_dialog(valid_indices[0])
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit {len(valid_indices)} Boundary Assignments")
        form = QFormLayout(dlg)
        boundary_type = QComboBox(dlg)
        boundary_type.addItems(_OBJECT_BC_TYPES)
        existing_types = {str(rows[index].get("type", "PML")) for index in valid_indices}
        initial_type = next(iter(existing_types)) if len(existing_types) == 1 else "PML"
        if initial_type in _OBJECT_BC_TYPES:
            boundary_type.setCurrentText(initial_type)

        params_container = QWidget(dlg)
        params_form = QFormLayout(params_container)
        param_edits: Dict[str, QLineEdit] = {}

        def rebuild_params(bc_type: str) -> None:
            while params_form.rowCount() > 0:
                params_form.removeRow(0)
            param_edits.clear()
            for key, default_value in _OBJECT_BC_DEFAULT_PARAMS.get(bc_type, {}).items():
                shared_values = [
                    rows[index].get("params", {}).get(key, default_value)
                    for index in valid_indices
                    if str(rows[index].get("type", "PML")) == bc_type
                    and isinstance(rows[index].get("params", {}), dict)
                ]
                value = default_value
                if len(shared_values) == len(valid_indices) and all(item == shared_values[0] for item in shared_values):
                    value = shared_values[0]
                edit = QLineEdit(str(value), dlg)
                params_form.addRow(key, edit)
                param_edits[key] = edit

        rebuild_params(boundary_type.currentText())
        boundary_type.currentTextChanged.connect(rebuild_params)
        form.addRow(QLabel(f"Applying changes to {len(valid_indices)} selected assignments", dlg))
        form.addRow("Type", boundary_type)
        form.addRow("Type Parameters", params_container)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return

        bc_type = boundary_type.currentText()
        defaults = _OBJECT_BC_DEFAULT_PARAMS.get(bc_type, {})
        try:
            params: Dict[str, Any] = {}
            for key, edit in param_edits.items():
                raw = edit.text().strip()
                default_value = defaults[key]
                if isinstance(default_value, int) and not isinstance(default_value, bool):
                    params[key] = int(round(self._parse_formula_float(raw)))
                elif isinstance(default_value, float):
                    params[key] = self._parse_formula_float(raw)
                else:
                    params[key] = raw
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Boundary Parameters", str(exc))
            return

        self._apply_object_boundary_update(valid_indices, bc_type, params)

    def _apply_object_boundary_update(self, indices: list[int], bc_type: str, params: dict) -> None:
        rows = self._settings.get("object_boundaries", [])
        for index in indices:
            if 0 <= index < len(rows):
                rows[index]["type"] = bc_type
                rows[index]["params"] = _deep_copy(params)
        self._refresh_object_boundaries()
        self.settings_changed.emit()

    def _edit_object_mesh_assignments(self, object_names: list[str]) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            return
        obj_res = mesh_cfg.get("object_resolutions", {})
        if not isinstance(obj_res, dict):
            return
        names = [name for name in object_names if name in obj_res]
        if not names:
            return

        try:
            current = float(obj_res[names[0]])
        except Exception:
            current = 0.3
        value, ok = QInputDialog.getDouble(
            self,
            "Object Mesh Resolution",
            "Resolution (1/λ):",
            max(0.01, min(1.0, current)),
            0.01,
            1.0,
            4,
        )
        if not ok:
            return
        self._apply_object_mesh_resolution(names, float(value))

    def _apply_object_mesh_resolution(self, object_names: list[str], value: float) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            return
        obj_res = mesh_cfg.get("object_resolutions", {})
        if not isinstance(obj_res, dict):
            return
        fraction = max(0.01, min(1.0, float(value)))
        changed = False
        for name in object_names:
            if name in obj_res:
                obj_res[name] = fraction
                changed = True
        if changed:
            self._refresh_object_mesh_assignments()
            self.settings_changed.emit()

    def _remove_object_mesh_assignments(self, object_names: list[str]) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            return
        obj_res = mesh_cfg.get("object_resolutions", {})
        if not isinstance(obj_res, dict):
            return
        names = [str(name).strip() for name in object_names]
        names = [name for name in names if name and name in obj_res]
        if not names:
            return
        target = f"'{names[0]}'" if len(names) == 1 else f"{len(names)} selected objects"
        reply = QMessageBox.question(
            self,
            "Remove Mesh Assignment",
            f"Remove mesh assignment for {target}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for name in names:
            obj_res.pop(name, None)
        self._refresh_object_mesh_assignments()
        self.settings_changed.emit()

    def _local_mesh_refinements(self) -> list:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            return []
        refinements = mesh_cfg.get("local_refinements", [])
        return refinements if isinstance(refinements, list) else []

    def _edit_local_mesh_refinement(self, index: int) -> None:
        refinements = self._local_mesh_refinements()
        if not (0 <= index < len(refinements)) or not isinstance(refinements[index], dict):
            return
        current = _deep_copy(refinements[index])

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit Mesh Refinement - {current.get('object', '')}")
        form = QFormLayout(dlg)

        enabled = QCheckBox("Enabled", dlg)
        enabled.setChecked(bool(current.get("enabled", True)))
        form.addRow("", enabled)

        mode = QComboBox(dlg)
        mode.addItem("Boundary edges", "boundary")
        mode.addItem("Face", "face")
        mode_index = mode.findData(str(current.get("mode", "boundary")).lower())
        mode.setCurrentIndex(max(0, mode_index))
        form.addRow("Mode", mode)

        faces = QLineEdit(dlg)
        faces.setText(",".join(str(face) for face in current.get("faces", [])))
        faces.setPlaceholderText("-z,+z (empty = whole Plate for Face mode)")
        form.addRow("Faces", faces)

        size = QDoubleSpinBox(dlg)
        size.setDecimals(6)
        size.setRange(0.000001, 1e6)
        size.setValue(float(current.get("size_mm", 0.25)))
        size.setSuffix(" mm")
        form.addRow("Minimum size", size)

        growth = QDoubleSpinBox(dlg)
        growth.setDecimals(3)
        growth.setRange(1.001, 100.0)
        growth.setValue(float(current.get("growth_rate", 3.0)))
        form.addRow("Growth rate", growth)

        max_size = QDoubleSpinBox(dlg)
        max_size.setDecimals(6)
        max_size.setRange(0.0, 1e6)
        max_size.setSpecialValueText("Automatic")
        max_size.setSuffix(" mm")
        max_size.setValue(float(current.get("max_size_mm") or 0.0))
        form.addRow("Maximum size", max_size)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return

        valid_faces = {"-x", "+x", "-y", "+y", "-z", "+z"}
        selected_faces = [part.strip().lower() for part in faces.text().split(",") if part.strip()]
        invalid_faces = [part for part in selected_faces if part not in valid_faces]
        selected_mode = str(mode.currentData())
        if invalid_faces:
            QMessageBox.warning(self, "Mesh Refinement", f"Invalid face selector(s): {', '.join(invalid_faces)}")
            return
        if selected_mode == "boundary" and not selected_faces:
            QMessageBox.warning(self, "Mesh Refinement", "Boundary edges mode requires at least one face selector.")
            return

        refinements[index] = {
            "object": str(current.get("object", "")).strip(),
            "enabled": bool(enabled.isChecked()),
            "mode": selected_mode,
            "faces": selected_faces,
            "size_mm": float(size.value()),
            "growth_rate": float(growth.value()),
            "max_size_mm": float(max_size.value()) if max_size.value() > 0.0 else None,
        }
        self._refresh_object_mesh_assignments()
        self._m_local_node.setExpanded(True)
        self.settings_changed.emit()

    def _toggle_local_mesh_refinement(self, index: int) -> None:
        refinements = self._local_mesh_refinements()
        if not (0 <= index < len(refinements)) or not isinstance(refinements[index], dict):
            return
        refinements[index]["enabled"] = not bool(refinements[index].get("enabled", True))
        self._refresh_object_mesh_assignments()
        self._m_local_node.setExpanded(True)
        self.settings_changed.emit()

    def _remove_local_mesh_refinement(self, index: int) -> None:
        refinements = self._local_mesh_refinements()
        if not (0 <= index < len(refinements)) or not isinstance(refinements[index], dict):
            return
        object_name = str(refinements[index].get("object", f"Refinement {index + 1}"))
        reply = QMessageBox.question(
            self,
            "Remove Mesh Refinement",
            f"Remove local mesh refinement for '{object_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        refinements.pop(index)
        self._refresh_object_mesh_assignments()
        self._m_local_node.setExpanded(True)
        self.settings_changed.emit()

    def _edit_default_mesh_resolution(self) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            self._settings["mesh"] = mesh_cfg

        current = 0.3
        try:
            current = float(mesh_cfg.get("default_fraction", 0.3))
        except Exception:
            current = 0.3
        current = max(0.01, min(1.0, current))

        value, ok = QInputDialog.getDouble(
            self,
            "Default Mesh Resolution",
            "Default resolution (1/λ):",
            current,
            0.01,
            1.0,
            4,
        )
        if not ok:
            return

        mesh_cfg["default_fraction"] = float(value)
        self._refresh_object_mesh_assignments()
        self.settings_changed.emit()

    def _edit_emerge_scale_factor(self) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            self._settings["mesh"] = mesh_cfg
        try:
            current = float(mesh_cfg.get("emerge_scale_factor", 1.0))
        except (TypeError, ValueError):
            current = 1.0
        if not math.isfinite(current):
            current = 1.0
        value, ok = QInputDialog.getDouble(
            self,
            "EMERGE Geometry Scale",
            "Experimental scale factor (1 disables scaling):",
            max(1.0, min(1_000_000.0, current)),
            1.0,
            1_000_000.0,
            3,
        )
        if not ok:
            return
        mesh_cfg["emerge_scale_factor"] = float(value)
        self._refresh_object_mesh_assignments()
        self.settings_changed.emit()

    def _add_simulation_dialog(self) -> None:
        sims = self._settings.setdefault("simulations", [])
        if not isinstance(sims, list):
            self._settings["simulations"] = []
            sims = self._settings["simulations"]
        sim = _default_simulation_item()
        sim["name"] = f"Simulation_{len(sims) + 1}"
        created = self._simulation_dialog_data(sim, title="Add Simulation")
        if created is None:
            return
        sims.append(created)
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
        self._refresh_outputs()
        self.settings_changed.emit()

    def _edit_simulation_dialog(self, index: int) -> None:
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list) or not (0 <= index < len(sims)):
            return
        current = _deep_copy(sims[index]) if isinstance(sims[index], dict) else _default_simulation_item()
        old_name = str(current.get("name", "")).strip()
        updated = self._simulation_dialog_data(current, title="Edit Simulation")
        if updated is None:
            return
        sims[index] = updated
        new_name = str(updated.get("name", "")).strip()
        if old_name and new_name and old_name != new_name:
            outputs = self._settings.get("outputs", [])
            if isinstance(outputs, list):
                for output in outputs:
                    if isinstance(output, dict) and str(output.get("simulation", "")).strip() == old_name:
                        output["simulation"] = new_name
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
        self._refresh_outputs()
        self.settings_changed.emit()

    def _toggle_simulation_enabled(self, index: int) -> None:
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list) or not (0 <= index < len(sims)):
            return
        sim = sims[index]
        if not isinstance(sim, dict):
            return
        sim["enabled"] = not bool(sim.get("enabled", True))
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
        self._refresh_outputs()
        self.settings_changed.emit()

    def _remove_simulation(self, index: int) -> None:
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list) or not (0 <= index < len(sims)):
            return
        sim = sims[index] if isinstance(sims[index], dict) else {}
        name = str(sim.get("name", f"Simulation_{index+1}"))
        reply = QMessageBox.question(
            self,
            "Remove Simulation",
            f"Remove simulation '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        sims.pop(index)
        if not sims:
            sims.append(_default_simulation_item())
        valid_sim_names = {
            str(item.get("name", "")).strip()
            for item in sims
            if isinstance(item, dict)
        }
        outputs = self._settings.get("outputs", [])
        if isinstance(outputs, list):
            self._settings["outputs"] = [
                output for output in outputs
                if isinstance(output, dict)
                and str(output.get("simulation", "")).strip() in valid_sim_names
            ]
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
        self._refresh_outputs()
        self.settings_changed.emit()

    def _add_output_dialog(self, simulation_name: str | None) -> None:
        sim_names = self._simulation_names()
        if not sim_names:
            QMessageBox.information(self, "Add Output", "No simulations defined. Add a simulation first.")
            return
        outputs = self._settings.setdefault("outputs", [])
        if not isinstance(outputs, list):
            self._settings["outputs"] = []
            outputs = self._settings["outputs"]
        sim_ref = str(simulation_name or "").strip()
        if sim_ref not in sim_names:
            sim_ref = sim_names[0]
        initial = _default_output_item(sim_ref, len(outputs) + 1)
        created = self._output_dialog_data(initial, title="Add Output Plot")
        if created is None:
            return
        outputs.append(created)
        self._refresh_outputs(expand_simulation=str(created.get("simulation", "")))
        self.settings_changed.emit()

    def _edit_output_dialog(self, index: int) -> None:
        outputs = self._settings.get("outputs", [])
        if not isinstance(outputs, list) or not (0 <= index < len(outputs)):
            return
        current = _deep_copy(outputs[index]) if isinstance(outputs[index], dict) else _default_output_item("", index + 1)
        updated = self._output_dialog_data(current, title="Edit Output Plot")
        if updated is None:
            return
        outputs[index] = updated
        self._refresh_outputs()
        self.settings_changed.emit()

    def _toggle_output_enabled(self, index: int) -> None:
        outputs = self._settings.get("outputs", [])
        if not isinstance(outputs, list) or not (0 <= index < len(outputs)):
            return
        output = outputs[index]
        if not isinstance(output, dict):
            return
        output["enabled"] = not bool(output.get("enabled", True))
        self._refresh_outputs()
        self.settings_changed.emit()

    def _remove_output(self, index: int) -> None:
        outputs = self._settings.get("outputs", [])
        if not isinstance(outputs, list) or not (0 <= index < len(outputs)):
            return
        output = outputs[index] if isinstance(outputs[index], dict) else {}
        name = str(output.get("name", f"Output_{index + 1}"))
        reply = QMessageBox.question(
            self,
            "Remove Output",
            f"Remove output '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        outputs.pop(index)
        self._refresh_outputs()
        self.settings_changed.emit()

    def _run_output_plot(self, index: int) -> None:
        outputs = self._settings.get("outputs", [])
        if not isinstance(outputs, list) or not (0 <= index < len(outputs)):
            return
        output = outputs[index]
        if not isinstance(output, dict):
            return
        if not bool(output.get("enabled", True)):
            QMessageBox.information(self, "Output", "This output is disabled. Enable it first.")
            return
        payload = {
            "name": str(output.get("name", f"Output_{index + 1}")).strip() or f"Output_{index + 1}",
            "simulation": str(output.get("simulation", "")).strip(),
            "plot_type": str(output.get("plot_type", "plot_sp")).strip() or "plot_sp",
            "plot_mode": str(output.get("plot_mode", "final")).strip().lower(),
            "enabled": bool(output.get("enabled", True)),
            "params": dict(output.get("params", {})) if isinstance(output.get("params", {}), dict) else {},
        }
        self.output_plot_requested.emit(payload)

    def _output_dialog_data(self, initial: dict, title: str) -> dict | None:
        sim_names = self._simulation_names()
        if not sim_names:
            QMessageBox.information(self, "Output", "No simulations available.")
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)

        le_name = QLineEdit(str(initial.get("name", "Output")))

        cb_sim = QComboBox(dlg)
        cb_sim.addItems(sim_names)
        current_sim = str(initial.get("simulation", "")).strip()
        cb_sim.setCurrentText(current_sim if current_sim in sim_names else sim_names[0])

        cb_type = QComboBox(dlg)
        cb_type.addItems(_OUTPUT_PLOT_TYPES)
        current_type = str(initial.get("plot_type", "plot_sp")).strip()
        cb_type.setCurrentText(current_type if current_type in _OUTPUT_PLOT_TYPES else "plot_sp")

        cb_enabled = QComboBox(dlg)
        cb_enabled.addItems(["Enabled", "Disabled"])
        cb_enabled.setCurrentText("Enabled" if bool(initial.get("enabled", True)) else "Disabled")

        cb_plot_mode = QComboBox(dlg)
        cb_plot_mode.addItem("After simulation", "final")
        cb_plot_mode.addItem("Live (progressive S-parameters)", "live")
        current_plot_mode = str(initial.get("plot_mode", "final")).strip().lower()
        cb_plot_mode.setCurrentIndex(max(0, cb_plot_mode.findData(current_plot_mode)))

        params = initial.get("params", {}) if isinstance(initial.get("params", {}), dict) else {}
        configured_ports = self._settings.get("ports", [])
        port_count = len(configured_ports) if isinstance(configured_ports, list) else 0
        port_count = max(1, port_count)
        valid_parameters = {
            f"S{output_port}{input_port}"
            for output_port in range(1, port_count + 1)
            for input_port in range(1, port_count + 1)
        }
        stored_parameters = params.get("s_parameters", [])
        if isinstance(stored_parameters, list):
            selected_parameters = {
                str(value).strip().upper()
                for value in stored_parameters
                if str(value).strip().upper() in valid_parameters
            }
        else:
            selected_parameters = set()
        if not selected_parameters:
            legacy_parameter = str(params.get("s_parameter", "S11")).strip().upper() or "S11"
            selected_parameters = {legacy_parameter if legacy_parameter in valid_parameters else "S11"}

        s_parameter_matrix = QTableWidget(port_count, port_count, dlg)
        s_parameter_matrix.setHorizontalHeaderLabels([f"Port {index}" for index in range(1, port_count + 1)])
        s_parameter_matrix.setVerticalHeaderLabels([f"Port {index}" for index in range(1, port_count + 1)])
        s_parameter_matrix.setToolTip("Click each matrix cell to select the S-parameters to plot")
        s_parameter_matrix.setMaximumHeight(48 + port_count * 34)
        for output_port in range(1, port_count + 1):
            for input_port in range(1, port_count + 1):
                parameter_name = f"S{output_port}{input_port}"
                item = QTableWidgetItem(parameter_name)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if parameter_name in selected_parameters else Qt.Unchecked)
                s_parameter_matrix.setItem(output_port - 1, input_port - 1, item)
        s_parameter_matrix.resizeColumnsToContents()

        cb_plane = QComboBox(dlg)
        cb_plane.addItems(["XY", "XZ", "YZ"])
        selected_plane = str(params.get("plane", "XY")).strip().upper()
        if selected_plane not in {"XY", "XZ", "YZ"}:
            selected_plane = "XY"
        cb_plane.setCurrentText(selected_plane)

        cb_polar_view = QComboBox(dlg)
        cb_polar_view.addItems(["2D polar", "3D polar"])
        polar_3d = bool(params.get("polar_3d", False))
        cb_polar_view.setCurrentText("3D polar" if polar_3d else "2D polar")

        farfield_frequency = QDoubleSpinBox(dlg)
        farfield_frequency.setRange(1e-6, 1e6)
        farfield_frequency.setDecimals(6)
        farfield_frequency.setSingleStep(0.1)
        farfield_frequency.setSuffix(" GHz")
        selected_frequency = float(params.get("frequency_GHz", 0.0) or 0.0)
        if selected_frequency <= 0.0:
            selected_sim = next(
                (sim for sim in self._settings.get("simulations", [])
                 if isinstance(sim, dict) and str(sim.get("name", "")).strip() == cb_sim.currentText().strip()),
                {},
            )
            selected_frequency = (
                float(selected_sim.get("Fmin_GHz", 0.1))
                + float(selected_sim.get("Fmax_GHz", 10.0))
            ) / 2.0
        farfield_frequency.setValue(selected_frequency)

        form.addRow("Name", le_name)
        form.addRow("Simulation", cb_sim)
        form.addRow("Plot type", cb_type)
        form.addRow("State", cb_enabled)
        form.addRow("Plot timing", cb_plot_mode)
        form.addRow("S-parameters (output × input)", s_parameter_matrix)
        form.addRow("Far-field plane", cb_plane)
        form.addRow("Polar view", cb_polar_view)
        form.addRow("Far-field frequency", farfield_frequency)
        s_parameter_rows = (5,)
        farfield_rows = (6, 8)
        polar_view_rows = (7,)

        def update_plot_mode_availability(_value: str = "") -> None:
            s_parameter_plot = cb_type.currentText().strip() in {"plot_sp", "plot_vswr", "smith", "plot"}
            selected_simulation = next(
                (
                    sim for sim in self._settings.get("simulations", [])
                    if isinstance(sim, dict)
                    and str(sim.get("name", "")).strip() == cb_sim.currentText().strip()
                ),
                {},
            )
            live_supported = (
                s_parameter_plot
                and str(selected_simulation.get("type", "")).strip().lower()
                in {"sweep", "parametric"}
                and bool(selected_simulation.get("progressive_sparams_enabled", False))
            )
            cb_plot_mode.setEnabled(live_supported)
            cb_plot_mode.setToolTip(
                "Live plots require an S-parameter output and a Sweep or Parametric simulation with progressive plotting enabled."
                if not live_supported
                else "Update this plot as progressive simulation results arrive."
            )
            if not live_supported:
                cb_plot_mode.setCurrentIndex(0)

        update_plot_mode_availability()
        cb_type.currentTextChanged.connect(update_plot_mode_availability)
        cb_sim.currentTextChanged.connect(update_plot_mode_availability)

        def update_parameter_visibility(plot_type: str) -> None:
            is_s_parameter = plot_type in {"plot_sp", "plot_vswr", "smith", "plot"}
            for row in s_parameter_rows:
                form.setRowVisible(row, is_s_parameter)
            is_farfield = plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}
            for row in farfield_rows:
                form.setRowVisible(row, is_farfield)
            form.setRowVisible(4, is_s_parameter)
            is_3d_polar = plot_type == "plot_ff_3d"
            for row in polar_view_rows:
                form.setRowVisible(row, is_3d_polar)

        update_parameter_visibility(cb_type.currentText().strip())
        cb_type.currentTextChanged.connect(update_parameter_visibility)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        selected_parameters = [
            s_parameter_matrix.item(row, column).text()
            for row in range(port_count)
            for column in range(port_count)
            if s_parameter_matrix.item(row, column).checkState() == Qt.Checked
        ]
        if not selected_parameters:
            QMessageBox.warning(dlg, "Output", "Select at least one S-parameter in the matrix.")
            return None
        first_output, first_input = int(selected_parameters[0][1]), int(selected_parameters[0][2])
        return {
            "name": le_name.text().strip() or "Output",
            "simulation": cb_sim.currentText().strip(),
            "plot_type": cb_type.currentText().strip(),
            "plot_mode": str(cb_plot_mode.currentData() or "final"),
            "enabled": cb_enabled.currentText() == "Enabled",
            "params": {
                "s_parameter": selected_parameters[0],
                "s_parameters": selected_parameters,
                "port_i": first_output,
                "port_j": first_input,
                "plane": cb_plane.currentText().strip().upper() or "XY",
                "polar_3d": cb_polar_view.currentText().strip() == "3D polar",
                "frequency_GHz": float(farfield_frequency.value()),
            },
        }

    def _simulation_dialog_data(self, initial: dict, title: str) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)

        le_name = QLineEdit(str(initial.get("name", "Simulation_1")))
        cb_type = QComboBox(dlg)
        cb_type.addItems(_SIMULATION_TYPES)
        current_type = str(initial.get("type", "Sweep")).strip().title()
        if current_type not in _SIMULATION_TYPES:
            current_type = "Sweep"
        cb_type.setCurrentText(current_type)

        cb_enabled = QComboBox(dlg)
        cb_enabled.addItems(["Enabled", "Disabled"])
        cb_enabled.setCurrentText("Enabled" if bool(initial.get("enabled", True)) else "Disabled")

        fmin_formula = str(initial.get("FminFormula") or "").strip()
        fmax_formula = str(initial.get("FmaxFormula") or "").strip()
        fstep_formula = str(initial.get("FstepFormula") or "").strip()
        le_fmin = QLineEdit(fmin_formula or _format_locale_number(float(initial.get("Fmin_GHz", 0.1))))
        le_fmax = QLineEdit(fmax_formula or _format_locale_number(float(initial.get("Fmax_GHz", 10.0))))
        le_fstep = QLineEdit(fstep_formula or _format_locale_number(float(initial.get("Fstep_GHz", 0.1))))
        initial_points = max(2, int(initial.get("NumberOfPoints", round((float(initial.get("Fmax_GHz", 10.0)) - float(initial.get("Fmin_GHz", 0.1))) / max(float(initial.get("Fstep_GHz", 0.1)), 1e-9)) + 1)))
        points_formula = str(initial.get("NumberOfPointsFormula") or "").strip()
        le_points = QLineEdit(points_formula or str(initial_points))
        le_modes = QLineEdit(str(int(initial.get("EigenmodeCount", 5))))
        le_param_values = QLineEdit(str(initial.get("ParamValues", "")))
        values_mode = str(
            initial.get("ParamValuesMode")
            or ("list" if le_param_values.text().strip() else "range")
        ).strip().lower()
        cb_param_values_mode = QComboBox(dlg)
        cb_param_values_mode.setObjectName("parametricValuesMode")
        cb_param_values_mode.addItem("Range", "range")
        cb_param_values_mode.addItem("Explicit values (CSV)", "list")
        cb_param_values_mode.setCurrentIndex(
            max(0, cb_param_values_mode.findData(values_mode))
        )
        le_param_start = QLineEdit(str(initial.get("ParamStart", "0")))
        le_param_start.setObjectName("parametricStart")
        le_param_end = QLineEdit(str(initial.get("ParamEnd", "1")))
        le_param_end.setObjectName("parametricEnd")
        le_param_step = QLineEdit(str(initial.get("ParamStep", "1")))
        le_param_step.setObjectName("parametricStep")
        variables = [
            str(entry.get("name", "")).strip()
            for entry in self._settings.get("parameters", [])
            if isinstance(entry, dict) and str(entry.get("name", "")).strip()
        ]
        cb_param_name = QComboBox(dlg)
        cb_param_name.setObjectName("parametricParameterName")
        cb_param_name.setToolTip("Select a parameter defined in the project Parameters table.")
        cb_param_name.addItem("Select parameter", "")
        for variable in dict.fromkeys(variables):
            cb_param_name.addItem(variable, variable)
        selected_param_index = cb_param_name.findData(str(initial.get("ParamName", "")).strip())
        if selected_param_index >= 0:
            cb_param_name.setCurrentIndex(selected_param_index)
        cb_param_name.setEnabled(bool(variables))
        active_variable = QComboBox(dlg)
        active_variable.addItem("None", "")
        active_variable.addItems(variables)
        active_variable.setCurrentText(str(initial.get("ActiveVariable", "")) or "None")
        active_value = QLabel("-")

        def update_active_value(_text: str = "") -> None:
            name = str(active_variable.currentData() or "").strip()
            entry = next((item for item in self._settings.get("parameters", []) if isinstance(item, dict) and str(item.get("name", "")).strip() == name), None)
            active_value.setText(str(entry.get("value", "-")) if entry is not None else "-")

        active_variable.currentTextChanged.connect(update_active_value)
        update_active_value()
        fit_config = initial.get("sparam_fitting", {}) if isinstance(initial.get("sparam_fitting", {}), dict) else {}
        fit_check = QCheckBox("Enable S-parameter line fitting", dlg)
        fit_check.setChecked(bool(fit_config.get("enabled", False)))
        progressive_check = QCheckBox("Enable progressive S-parameter plotting", dlg)
        progressive_check.setToolTip(
            "Plots Sweep frequency chunks or each completed Parametric frequency sweep. Requires EMERGE 3.0.0a16."
        )
        progressive_check.setChecked(bool(initial.get("progressive_sparams_enabled", False)))
        progressive_chunk_size = QSpinBox(dlg)
        progressive_chunk_size.setObjectName("progressiveSparamsChunkSize")
        progressive_chunk_size.setRange(1, 1000000)
        progressive_chunk_size.setValue(
            max(1, int(initial.get("progressive_sparams_chunk_size", 10)))
        )
        progressive_chunk_size.setToolTip(
            "Emit a chart update after this many frequency points for each sweep, including each Parametric value."
        )
        fit_points = QSpinBox(dlg)
        fit_points.setRange(8, 100001)
        fit_points.setSingleStep(1)
        fit_points.setValue(max(8, int(fit_config.get("points", 1001))))

        cb_log = QComboBox(dlg)
        cb_log.addItems(_LOG_VERBOSITY_LEVELS)
        current_log = str(initial.get("LogVerbosity", "Info")).strip().title()
        cb_log.setCurrentText(current_log if current_log in _LOG_VERBOSITY_LEVELS else "Info")

        form.addRow("Name", le_name)
        form.addRow("Type", cb_type)
        form.addRow("State", cb_enabled)
        form.addRow("Fmin [GHz]", le_fmin)
        form.addRow("Fmax [GHz]", le_fmax)
        form.addRow("Fstep [GHz]", le_fstep)
        form.addRow("Number of points", le_points)
        form.addRow("Eigenmode count", le_modes)
        form.addRow("Parametric name", cb_param_name)
        form.addRow("Parameter values mode", cb_param_values_mode)
        form.addRow("Parameter start", le_param_start)
        form.addRow("Parameter end", le_param_end)
        form.addRow("Parameter step", le_param_step)
        form.addRow("Parametric values (CSV)", le_param_values)
        form.addRow("Active variable", active_variable)
        form.addRow("Variable value", active_value)
        form.addRow("S-parameter fitting", fit_check)
        form.addRow("Fitting points", fit_points)
        form.addRow("Progressive plot", progressive_check)
        form.addRow("Update every N points", progressive_chunk_size)
        form.addRow("Log verbosity", cb_log)

        def _update_visibility(sim_type: str) -> None:
            is_sweep = sim_type == "Sweep"
            is_eigen = sim_type == "Eigenmode"
            is_param = sim_type == "Parametric"
            for w in (le_fmin, le_fmax, le_fstep, le_points):
                w.setVisible(is_sweep or is_param)
            le_modes.setVisible(is_eigen)
            cb_param_name.setVisible(is_param)
            cb_param_values_mode.setVisible(is_param)
            range_mode = cb_param_values_mode.currentData() == "range"
            for widget in (le_param_start, le_param_end, le_param_step):
                widget.setVisible(is_param and range_mode)
            le_param_values.setVisible(is_param and not range_mode)
            fit_check.setVisible(is_sweep or is_param)
            fit_points.setVisible(is_sweep or is_param)
            fit_points.setEnabled(fit_check.isChecked() and (is_sweep or is_param))
            progressive_check.setEnabled(is_sweep or is_param)
            progressive_chunk_size.setEnabled(
                (is_sweep or is_param) and progressive_check.isChecked()
            )

        _update_visibility(current_type)
        cb_type.currentTextChanged.connect(_update_visibility)
        cb_param_values_mode.currentIndexChanged.connect(
            lambda _index: _update_visibility(cb_type.currentText())
        )
        progressive_check.toggled.connect(
            lambda checked: progressive_chunk_size.setEnabled(
                cb_type.currentText() in {"Sweep", "Parametric"} and bool(checked)
            )
        )
        fit_check.toggled.connect(lambda checked: fit_points.setEnabled(bool(checked)))

        syncing_points = {"active": False}

        def update_points_from_step() -> None:
            if syncing_points["active"]:
                return
            try:
                fmin_value = self._parse_formula_float(le_fmin.text())
                fmax_value = self._parse_formula_float(le_fmax.text())
                step_value = self._parse_formula_float(le_fstep.text())
                if step_value <= 0 or fmax_value <= fmin_value:
                    return
                syncing_points["active"] = True
                le_points.setText(str(max(2, round((fmax_value - fmin_value) / step_value) + 1)))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            finally:
                syncing_points["active"] = False

        def update_step_from_points() -> None:
            if syncing_points["active"]:
                return
            try:
                fmin_value = self._parse_formula_float(le_fmin.text())
                fmax_value = self._parse_formula_float(le_fmax.text())
                points_value = max(2, int(round(self._parse_formula_float(le_points.text()))))
                if fmax_value <= fmin_value:
                    return
                syncing_points["active"] = True
                le_fstep.setText(_format_locale_number((fmax_value - fmin_value) / (points_value - 1)))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            finally:
                syncing_points["active"] = False

        le_fstep.editingFinished.connect(update_points_from_step)
        le_points.editingFinished.connect(update_step_from_points)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        parametric_range = {"values": []}

        def _accept_simulation() -> None:
            if (
                cb_type.currentText() == "Parametric"
                and cb_param_values_mode.currentData() == "range"
            ):
                try:
                    start = self._parse_formula_float(le_param_start.text())
                    end = self._parse_formula_float(le_param_end.text())
                    step = self._parse_formula_float(le_param_step.text())
                    parametric_range["values"] = _parametric_range_values(start, end, step)
                except (TypeError, ValueError, OverflowError) as exc:
                    QMessageBox.warning(dlg, "Invalid parameter range", str(exc))
                    return
            dlg.accept()

        btns.accepted.connect(_accept_simulation)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        try:
            sim_type = cb_type.currentText().strip().title()
            if sim_type not in _SIMULATION_TYPES:
                sim_type = "Sweep"
            if sim_type == "Parametric" and not str(cb_param_name.currentData() or "").strip():
                QMessageBox.warning(
                    dlg,
                    "Parameter required",
                    "Select a parameter from the project Parameters table.",
                )
                return
            fmin = self._parse_formula_float(le_fmin.text())
            fmax = self._parse_formula_float(le_fmax.text())
            fstep = self._parse_formula_float(le_fstep.text())
            points = max(2, int(round(self._parse_formula_float(le_points.text()))))
            modes = max(1, _parse_locale_int(le_modes.text()))
            name = le_name.text().strip() or "Simulation"
            log_v = cb_log.currentText().strip().title()
            if log_v not in _LOG_VERBOSITY_LEVELS:
                log_v = "Info"
            return {
                "name": name,
                "type": sim_type,
                "enabled": cb_enabled.currentText() == "Enabled",
                "Fmin_GHz": float(fmin),
                "Fmax_GHz": float(fmax),
                "Fstep_GHz": float(fstep),
                "FminFormula": le_fmin.text().strip() if re.search(r"[A-Za-z_]", le_fmin.text()) else "",
                "FmaxFormula": le_fmax.text().strip() if re.search(r"[A-Za-z_]", le_fmax.text()) else "",
                "FstepFormula": le_fstep.text().strip() if re.search(r"[A-Za-z_]", le_fstep.text()) else "",
                "NumberOfPoints": int(points),
                "NumberOfPointsFormula": le_points.text().strip() if re.search(r"[A-Za-z_]", le_points.text()) else "",
                "ActiveVariable": str(active_variable.currentData() or ""),
                "EigenmodeCount": int(modes),
                "ParamName": str(cb_param_name.currentData() or "").strip(),
                "ParamValues": (
                    ",".join(parametric_range["values"])
                    if sim_type == "Parametric" and cb_param_values_mode.currentData() == "range"
                    else le_param_values.text().strip()
                ),
                "ParamValuesMode": str(cb_param_values_mode.currentData()),
                "ParamStart": le_param_start.text().strip(),
                "ParamEnd": le_param_end.text().strip(),
                "ParamStep": le_param_step.text().strip(),
                "sparam_fitting": {
                    "enabled": bool(fit_check.isChecked()),
                    "points": int(fit_points.value()),
                },
                "progressive_sparams_enabled": bool(progressive_check.isChecked()),
                "progressive_sparams_chunk_size": int(progressive_chunk_size.value()),
                "LogVerbosity": log_v,
            }
        except Exception:
            return None

    def _edit_boundary(self, item: QTreeWidgetItem, key: str) -> None:
        self._edit_boundaries([key])

    def _edit_boundaries(self, keys: list[str]) -> None:
        keys = [key for key in keys if key in self._settings.get("boundaries", {})]
        if not keys:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Boundary" if len(keys) == 1 else f"Edit {len(keys)} Boundaries")
        form = QFormLayout(dlg)
        combo = QComboBox()
        combo.addItems(_BOUNDARY_TYPES)
        existing_types = {self._settings["boundaries"].get(key, "PML") for key in keys}
        current = next(iter(existing_types)) if len(existing_types) == 1 else "PML"
        if current in _BOUNDARY_TYPES:
            combo.setCurrentText(current)
        form.addRow(", ".join(keys), combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() == QDialog.Accepted:
            new_val = combo.currentText()
            for key in keys:
                self._settings["boundaries"][key] = new_val
            for index in range(self._b_node.childCount()):
                boundary_item = self._b_node.child(index)
                role = boundary_item.data(0, Qt.UserRole)
                if isinstance(role, tuple) and role[0] == "__boundary_key__" and role[1] in keys:
                    boundary_item.setText(1, new_val)
            self.settings_changed.emit()

    def _edit_open_region_domain(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Select open-region domain")
        form = QFormLayout(dlg)
        combo = QComboBox(dlg)
        combo.addItem("Auto-detect AIR", "")
        for object_name in sorted(self._scene_object_names, key=str.casefold):
            combo.addItem(object_name, object_name)
        current = self._settings.get("open_region", {})
        current_name = str(current.get("object", "")).strip() if isinstance(current, dict) else ""
        current_index = combo.findData(current_name)
        combo.setCurrentIndex(max(0, current_index))
        form.addRow("Domain", combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() != QDialog.Accepted:
            return
        selected_name = str(combo.currentData() or "")
        self._settings["open_region"] = {
            "enabled": True,
            "object": selected_name,
        }
        self._populate()
        self.settings_changed.emit()

    def _edit_numeric(self, item: QTreeWidgetItem, key: str,
                      current_val: str, target_dict: dict) -> None:
        new_val, ok = QInputDialog.getText(
            self, f"Edit – {key}", f"{key}:", text=current_val
        )
        if ok and new_val.strip():
            try:
                parsed = _parse_locale_float(new_val.strip())
                target_dict[key] = parsed
                item.setText(1, _format_locale_number(parsed))
                self.settings_changed.emit()
            except ValueError:
                pass

    def _edit_choice(self, item: QTreeWidgetItem, key: str, options: list[str], current_val: str, target_dict: dict) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit – {key}")
        form = QFormLayout(dlg)
        combo = QComboBox(dlg)
        combo.addItems(options)
        combo.setCurrentText(current_val if current_val in options else options[0])
        form.addRow(key, combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() == QDialog.Accepted:
            new_val = combo.currentText().strip().title()
            if new_val in options:
                target_dict[key] = new_val
                item.setText(1, new_val)
                self.settings_changed.emit()

    def _add_port_dialog(self) -> None:
        next_number = max(
            [int(port.get("number", index + 1)) for index, port in enumerate(self._settings.get("ports", []))]
            or [0]
        ) + 1
        port = self._port_dialog_data(
            initial={"number": next_number, "name": "Port", "type": "WaveguidePort", "x": 0.0, "y": 0.0, "z": 0.0},
            fixed_object=None,
            title="Add Port",
        )
        if port is None:
            return
        self._settings["ports"].append(port)
        self._refresh_ports()
        self.settings_changed.emit()

    def _edit_port_dialog(self, index: int) -> None:
        ports = self._settings.get("ports", [])
        if not (0 <= index < len(ports)):
            return
        current = _deep_copy(ports[index])
        fixed_object = current.get("object") or None
        updated = self._port_dialog_data(current, fixed_object=fixed_object, title="Edit Port")
        if updated is None:
            return
        ports[index] = updated
        self._refresh_ports()
        self.settings_changed.emit()

    def assign_port_to_object(self, obj_name: str) -> None:
        ports = self._settings.get("ports", [])
        existing_idx = next((i for i, p in enumerate(ports) if str(p.get("object", "")).strip() == obj_name), None)

        if existing_idx is not None:
            initial = _deep_copy(ports[existing_idx])
            title = f"Assign Port - {obj_name} (edit existing)"
        else:
            port_type = "WaveguidePort"
            next_number = max(
                [int(port.get("number", index + 1)) for index, port in enumerate(ports)]
                or [0]
            ) + 1
            initial = {
                "number": next_number,
                "name": f"Port_{obj_name}",
                "type": port_type,
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "params": _deep_copy(_PORT_TYPE_DEFAULT_PARAMS.get(port_type, {})),
            }
            title = f"Assign Port - {obj_name}"

        port = self._port_dialog_data(
            initial=initial,
            fixed_object=obj_name,
            title=title,
        )
        if port is None:
            return

        if existing_idx is not None:
            ports[existing_idx] = port
        else:
            ports.append(port)
        self._refresh_ports()
        self.settings_changed.emit()

    def _port_dialog_data(self, initial: dict, fixed_object: str | None, title: str) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)

        current_type = str(initial.get("type", "WaveguidePort"))
        if current_type not in _PORT_TYPES:
            current_type = "WaveguidePort"

        current_params = initial.get("params", {}) if isinstance(initial.get("params", {}), dict) else {}
        if current_type == "LumpedPort":
            # Backward compatibility: map old waveguide keys if present.
            if "Resistance_Ohm" not in current_params and "Impedance_Ohm" in current_params:
                current_params["Resistance_Ohm"] = current_params.get("Impedance_Ohm")
            if "Voltage_V" not in current_params and "Excitation" in current_params:
                current_params["Voltage_V"] = current_params.get("Excitation")

        le_name = QLineEdit(str(initial.get("name", "Port")))
        sb_number = QSpinBox(dlg)
        sb_number.setRange(1, 9999)
        sb_number.setValue(max(1, int(initial.get("number", 1))))
        le_type = QComboBox()
        le_type.addItems(_PORT_TYPES)
        le_type.setCurrentText(current_type)
        le_obj = QLineEdit(str(fixed_object if fixed_object is not None else initial.get("object", "")))
        if fixed_object is not None:
            le_obj.setReadOnly(True)
        le_x = QLineEdit(str(initial.get("x", 0.0)))
        le_y = QLineEdit(str(initial.get("y", 0.0)))
        le_z = QLineEdit(str(initial.get("z", 0.0)))

        from PySide6.QtWidgets import QLabel as _QLabel
        lbl_x = _QLabel("X")
        lbl_y = _QLabel("Y")
        lbl_z = _QLabel("Z")

        params_container = QWidget(dlg)
        params_form = QFormLayout(params_container)
        params_form.setContentsMargins(0, 0, 0, 0)
        param_edits: Dict[str, QLineEdit] = {}

        def _normalize_params_for_type(port_type: str) -> None:
            """Normalize param keys when switching port types (e.g., WaveguidePort → LumpedPort)."""
            if port_type == "LumpedPort":
                # Map WaveguidePort keys to LumpedPort if needed
                if "Impedance_Ohm" in current_params and "Resistance_Ohm" not in current_params:
                    current_params["Resistance_Ohm"] = current_params["Impedance_Ohm"]
                if "Excitation" in current_params and "Voltage_V" not in current_params:
                    current_params["Voltage_V"] = current_params["Excitation"]
                # Ensure core LumpedPort params exist with correct defaults
                if "Resistance_Ohm" not in current_params:
                    current_params["Resistance_Ohm"] = _PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Resistance_Ohm"]
                if "Voltage_V" not in current_params:
                    current_params["Voltage_V"] = _PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Voltage_V"]
            elif port_type == "WaveguidePort":
                # Map LumpedPort keys to WaveguidePort if needed
                if "Resistance_Ohm" in current_params and "Impedance_Ohm" not in current_params:
                    current_params["Impedance_Ohm"] = current_params["Resistance_Ohm"]
                if "Voltage_V" in current_params and "Excitation" not in current_params:
                    current_params["Excitation"] = current_params["Voltage_V"]
                # Ensure core WaveguidePort params exist with correct defaults
                if "Impedance_Ohm" not in current_params:
                    current_params["Impedance_Ohm"] = _PORT_TYPE_DEFAULT_PARAMS["WaveguidePort"]["Impedance_Ohm"]
                if "Excitation" not in current_params:
                    current_params["Excitation"] = _PORT_TYPE_DEFAULT_PARAMS["WaveguidePort"]["Excitation"]

        def _rebuild_param_fields(port_type: str) -> None:
            while params_form.rowCount() > 0:
                params_form.removeRow(0)
            param_edits.clear()

            # Normalize parameters for the selected type
            _normalize_params_for_type(port_type)

            defaults = _PORT_TYPE_DEFAULT_PARAMS.get(port_type, {})
            for key, default_val in defaults.items():
                value = current_params.get(key, default_val)
                edit = QLineEdit(str(value))
                if key in ("Resistance_Ohm",):
                    edit.setToolTip("Port impedance in Ohm (typically 50)")
                elif key.startswith("Direction_"):
                    axis = key[-1]  # X, Y or Z
                    edit.setToolTip(
                        f"{axis} component of the excitation field direction vector.\n"
                        "Leave all Direction fields at 0 to auto-compute from plate geometry."
                    )
                params_form.addRow(key, edit)
                param_edits[key] = edit

            # Show/hide position fields: not meaningful for LumpedPort
            is_lumped = (port_type == "LumpedPort")
            for w in (lbl_x, le_x, lbl_y, le_y, lbl_z, le_z):
                w.setVisible(not is_lumped)

        _rebuild_param_fields(current_type)
        le_type.currentTextChanged.connect(_rebuild_param_fields)

        form.addRow("Name", le_name)
        form.addRow("Port number", sb_number)
        form.addRow("Type", le_type)
        form.addRow("Object", le_obj)
        form.addRow(lbl_x, le_x)
        form.addRow(lbl_y, le_y)
        form.addRow(lbl_z, le_z)
        form.addRow("Parameters", params_container)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() == QDialog.Accepted:
            try:
                port_type = le_type.currentText()
                defaults = _PORT_TYPE_DEFAULT_PARAMS.get(port_type, {})
                parsed_params: Dict[str, Any] = {}
                for key, edit in param_edits.items():
                    raw = edit.text().strip()
                    default_val = defaults.get(key)
                    if isinstance(default_val, int) and not isinstance(default_val, bool):
                        val = _parse_locale_int(raw) if raw else None
                        parsed_params[key] = val if val is not None else default_val
                    elif isinstance(default_val, float):
                        val = self._parse_formula_float(raw) if raw else None
                        parsed_params[key] = val if val is not None else default_val
                    else:
                        parsed_params[key] = raw if raw else ""

                if port_type == "LumpedPort":
                    # Defensive defaults to avoid accidental impedance reset.
                    parsed_params.setdefault("Resistance_Ohm", float(_PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Resistance_Ohm"]))
                    parsed_params.setdefault("Voltage_V", float(_PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Voltage_V"]))

                port = {
                    "number": int(sb_number.value()),
                    "name": le_name.text().strip() or "Port",
                    "type": port_type,
                    "x": self._parse_formula_float(le_x.text()),
                    "y": self._parse_formula_float(le_y.text()),
                    "z": self._parse_formula_float(le_z.text()),
                    "params": parsed_params,
                }
                obj_txt = le_obj.text().strip()
                if obj_txt:
                    port["object"] = obj_txt
                return port
            except ValueError:
                return None
        return None

    def assign_boundary_to_object(self, obj_name: str) -> None:
        self.assign_boundary_to_objects([obj_name])

    def assign_boundary_to_objects(self, object_names: list[str]) -> None:
        names = [str(name).strip() for name in object_names if str(name).strip()]
        if not names:
            return
        bc = self._object_boundary_dialog_data(
            initial={"name": "BC_Selected", "type": "PML"},
            fixed_object=None,
            title=f"Assign Boundary Condition - {len(names)} objects",
        )
        if bc is None:
            return
        rows = self._settings.setdefault("object_boundaries", [])
        for name in names:
            item = _deep_copy(bc)
            item["object"] = name
            if len(names) == 1:
                item["name"] = bc.get("name", f"BC_{name}")
            else:
                item["name"] = f"{bc.get('name', 'BC_Selected')}_{name}"
            rows.append(item)
        self._refresh_object_boundaries()
        self.settings_changed.emit()

    def _edit_object_boundary_dialog(self, index: int) -> None:
        rows = self._settings.get("object_boundaries", [])
        if not (0 <= index < len(rows)):
            return
        current = _deep_copy(rows[index])
        fixed_object = current.get("object") or None
        updated = self._object_boundary_dialog_data(current, fixed_object=fixed_object, title="Edit Boundary Condition")
        if updated is None:
            return
        rows[index] = updated
        self._refresh_object_boundaries()
        self.settings_changed.emit()

    def _object_boundary_dialog_data(self, initial: dict, fixed_object: str | None, title: str) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)

        current_type = str(initial.get("type", "PML"))
        if current_type not in _OBJECT_BC_TYPES:
            current_type = "PML"

        le_name = QLineEdit(str(initial.get("name", "BoundaryCondition")))
        le_type = QComboBox()
        le_type.addItems(_OBJECT_BC_TYPES)
        le_type.setCurrentText(current_type)
        le_obj = QLineEdit(str(fixed_object if fixed_object is not None else initial.get("object", "")))
        if fixed_object is not None:
            le_obj.setReadOnly(True)

        params_container = QWidget(dlg)
        params_form = QFormLayout(params_container)
        param_edits: Dict[str, QLineEdit] = {}

        def _rebuild_param_fields(bc_type: str) -> None:
            while params_form.rowCount() > 0:
                params_form.removeRow(0)
            param_edits.clear()

            defaults = _OBJECT_BC_DEFAULT_PARAMS.get(bc_type, {})
            current_params = initial.get("params", {}) if isinstance(initial.get("params", {}), dict) else {}
            for key, default_val in defaults.items():
                value = current_params.get(key, default_val)
                edit = QLineEdit(str(value))
                params_form.addRow(key, edit)
                param_edits[key] = edit

        _rebuild_param_fields(current_type)
        le_type.currentTextChanged.connect(_rebuild_param_fields)

        form.addRow("Name", le_name)
        form.addRow("Type", le_type)
        form.addRow("Object", le_obj)
        form.addRow("Type Parameters", params_container)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() == QDialog.Accepted:
            try:
                bc_type = le_type.currentText()
                defaults = _OBJECT_BC_DEFAULT_PARAMS.get(bc_type, {})
                parsed_params: Dict[str, Any] = {}
                for key, edit in param_edits.items():
                    raw = edit.text().strip()
                    default_val = defaults.get(key)
                    if isinstance(default_val, int) and not isinstance(default_val, bool):
                        parsed_params[key] = int(round(self._parse_formula_float(raw)))
                    elif isinstance(default_val, float):
                        parsed_params[key] = self._parse_formula_float(raw)
                    else:
                        parsed_params[key] = raw

                bc = {
                    "name": le_name.text().strip() or "BoundaryCondition",
                    "type": bc_type,
                    "params": parsed_params,
                }
                obj_txt = le_obj.text().strip()
                if obj_txt:
                    bc["object"] = obj_txt
                return bc
            except ValueError:
                return None
        return None

    def rename_object_references(self, old_name: str, new_name: str) -> None:
        changed = False
        open_region = self._settings.get("open_region", {})
        if isinstance(open_region, dict) and str(open_region.get("object", "")) == old_name:
            open_region["object"] = new_name
            changed = True
        for port in self._settings.get("ports", []):
            if str(port.get("object", "")) == old_name:
                port["object"] = new_name
                changed = True
        for bc in self._settings.get("object_boundaries", []):
            if str(bc.get("object", "")) == old_name:
                bc["object"] = new_name
                changed = True
        mesh_cfg = self._settings.get("mesh", {})
        if isinstance(mesh_cfg, dict):
            obj_res = mesh_cfg.get("object_resolutions", {})
            if isinstance(obj_res, dict) and old_name in obj_res:
                obj_res[new_name] = obj_res.pop(old_name)
                changed = True
            refinements = mesh_cfg.get("local_refinements", [])
            if isinstance(refinements, list):
                for refinement in refinements:
                    if isinstance(refinement, dict) and str(refinement.get("object", "")) == old_name:
                        refinement["object"] = new_name
                        changed = True
        if changed:
            self._refresh_ports()
            self._refresh_object_boundaries()
            self._refresh_object_mesh_assignments()
            self.settings_changed.emit()


# ─────────────────────────────────────────────────────────────────────────────
def _deep_copy(d):
    import json
    return json.loads(json.dumps(d))
