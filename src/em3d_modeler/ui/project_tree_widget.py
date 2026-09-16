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
  │   └── [Port items, double-click to add/edit]
  ├── Simulation
  │   ├── Fmin  [GHz]
  │   ├── Fmax  [GHz]
  │   └── Fstep [GHz]
  └── Mesh
      └── Assigned To Objects (resolution 1/λ)
"""
from __future__ import annotations
from typing import Any, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QPushButton, QInputDialog, QComboBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QMenu, QMessageBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QStyle,
)
from PySide6.QtCore import Qt, Signal, QLocale
from PySide6.QtGui import QColor, QBrush, QAction, QIcon


# ──────────────────────────────────────────────────────────────────── constants
_BOUNDARY_TYPES = ["PML", "PEC", "PMC", "Open", "Periodic"]
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
        "EigenmodeCount": 5,
        "ParamName": "",
        "ParamValues": "",
        "sparam_fitting": {"enabled": False, "points": 1001},
        "LogVerbosity": "Info",
    }


def _default_output_item(simulation_name: str, idx: int) -> Dict[str, Any]:
    return {
        "name": f"Output_{idx}",
        "simulation": simulation_name,
        "plot_type": "plot_sp",
        "enabled": True,
    }

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "boundaries": {k: "PML" for k in _BOUNDARY_KEYS},
    "ports":      [],
    "object_boundaries": [],
    "simulation": {"Fmin_GHz": 0.1, "Fmax_GHz": 10.0, "Fstep_GHz": 0.1, "LogVerbosity": "Info"},
    "simulations": [_default_simulation_item()],
    "outputs": [],
    "mesh":       {"default_fraction": 0.3, "object_resolutions": {}},
    "material_priorities": {},  # Maps material_name -> priority_value
}

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
        self._tree.setHeaderLabels(["", ""])
        
        from PySide6.QtWidgets import QHeaderView
        header = self._tree.header()
        header.setDefaultSectionSize(180)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self._tree.setHeaderHidden(False)
        header.setVisible(False)
        
        self._tree.setAlternatingRowColors(True)
        self._tree.setIndentation(10)  # Reduce indentation (default is 20)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

        self._populate()

    # ─────────────────────────────────────────────────── public API
    def set_project_name(self, name: str) -> None:
        self._project_name = name
        if self._tree.topLevelItemCount() > 0:
            self._tree.topLevelItem(0).setText(0, name)

    def get_settings(self) -> Dict[str, Any]:
        return _deep_copy(self._settings)

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

        if not isinstance(merged.get("simulations"), list):
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
            lv = str(legacy_sim.get("LogVerbosity", "Info")).strip().title()
            sim["LogVerbosity"] = lv if lv in _LOG_VERBOSITY_LEVELS else "Info"
            merged["simulations"] = [sim]

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

        # Boundaries
        for k in _BOUNDARY_KEYS:
            v = self._settings["boundaries"].get(k, "PML")
            self._make_leaf(self._b_node, k, v, editable=True)

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

    def _refresh_ports(self) -> None:
        self._p_node.takeChildren()
        for i, port in enumerate(self._settings["ports"]):
            obj_name = str(port.get("object", "")).strip()
            missing_obj = bool(obj_name and obj_name not in self._scene_object_names)
            if obj_name:
                txt = f"Port {i+1}: {port.get('type', '?')} -> {obj_name}"
            else:
                txt = (f"Port {i+1}: {port.get('type','?')} "
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
        # Add-port hint
        hint = QTreeWidgetItem(["[+ double-click to add port]", ""])
        hint.setData(0, Qt.UserRole, "__add_port__")
        self._p_node.addChild(hint)

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
            status = "On" if enabled else "Off"

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
            row = QTreeWidgetItem([label, status])
            row.setData(0, Qt.UserRole, ("__sim_idx__", i))
            font = row.font(0)
            font.setPointSize(max(8, font.pointSize() - 1))
            row.setFont(0, font)
            row.setFont(1, font)
            self._s_node.addChild(row)

        hint = QTreeWidgetItem(["Add Simulation... (double-click)", ""])
        hint.setData(0, Qt.UserRole, "__add_sim__")
        self._s_node.addChild(hint)

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

    def _refresh_outputs(self) -> None:
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
            sim_node.setExpanded(sim_name in expanded_simulations)
            rows = grouped.get(sim_name, [])
            if not rows:
                hint = QTreeWidgetItem(["[double-click to add plot]", ""])
                hint.setData(0, Qt.UserRole, ("__add_out__", sim_name))
                sim_node.addChild(hint)
                continue
            for out_idx, output in rows:
                name = str(output.get("name", f"Output_{out_idx+1}")).strip() or f"Output_{out_idx+1}"
                plot_type = str(output.get("plot_type", "plot_sp")).strip()
                enabled = bool(output.get("enabled", True))
                row = QTreeWidgetItem([f"{name} [{plot_type}]", "On" if enabled else "Off"])
                row.setData(0, Qt.UserRole, ("__out_idx__", out_idx))
                sim_node.addChild(row)

        unknown_rows = grouped.get(unknown_key, [])
        if unknown_rows:
            unknown_node = self._make_section(node, "Unmapped Outputs")
            for out_idx, output in unknown_rows:
                name = str(output.get("name", f"Output_{out_idx+1}")).strip() or f"Output_{out_idx+1}"
                plot_type = str(output.get("plot_type", "plot_sp")).strip()
                sim_name = str(output.get("simulation", "?")).strip() or "?"
                row = QTreeWidgetItem([f"{name} [{plot_type}] -> {sim_name}", "Off"])
                row.setData(0, Qt.UserRole, ("__out_idx__", out_idx))
                warn_brush = QBrush(QColor(220, 40, 40))
                row.setForeground(0, warn_brush)
                row.setForeground(1, warn_brush)
                unknown_node.addChild(row)

        add_hint = QTreeWidgetItem(["Add Output Plot... (double-click)", ""])
        add_hint.setData(0, Qt.UserRole, "__add_out_global__")
        node.addChild(add_hint)

    def _refresh_object_boundaries(self) -> None:
        self._b_obj_node.takeChildren()
        for i, bc in enumerate(self._settings.get("object_boundaries", [])):
            obj_name = str(bc.get("object", "?")).strip() or "?"
            bc_type = str(bc.get("type", "?")).strip() or "?"
            txt = f"BC {i+1}: {bc_type} -> {obj_name}"
            row = QTreeWidgetItem([txt, ""])
            row.setData(0, Qt.UserRole, ("__bc_idx__", i))
            self._b_obj_node.addChild(row)

        hint = QTreeWidgetItem(["[assign from Object/Materials right-click]", ""])
        hint.setData(0, Qt.UserRole, "__bc_hint__")
        self._b_obj_node.addChild(hint)

    def _refresh_object_mesh_assignments(self) -> None:
        node = getattr(self, "_m_obj_node", None)
        if node is None:
            return
        node.takeChildren()

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

        hint = QTreeWidgetItem(["[assign from Object/Materials right-click]", ""])
        hint.setData(0, Qt.UserRole, "__mesh_hint__")
        node.addChild(hint)

    # ─────────────────────────────────────────────────── editing
    def _on_double_click(self, item: QTreeWidgetItem, col: int) -> None:
        parent = item.parent()
        if parent is None:
            return

        role = item.data(0, Qt.UserRole)

        # Add-port action
        if role == "__add_port__":
            self._add_port_dialog()
            return
        if role == "__add_sim__":
            self._add_simulation_dialog()
            return
        if role == "__add_out_global__":
            self._add_output_dialog(None)
            return

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
            if tag == "__add_out__":
                self._add_output_dialog(str(idx))
                return
            if tag == "__mesh_default__":
                self._edit_default_mesh_resolution()
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

        elif tag == "__bc_idx__":
            act_edit = QAction("Edit Assignment…", menu)
            act_edit.triggered.connect(lambda: self._edit_object_boundary_dialog(int(idx)))
            menu.addAction(act_edit)

            act_remove = QAction("Remove Assignment", menu)
            act_remove.triggered.connect(lambda: self._remove_object_boundary_assignment(int(idx)))
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
        elif tag == "__mesh_obj__":
            act_remove = QAction("Remove Mesh Assignment", menu)
            act_remove.triggered.connect(lambda: self._remove_object_mesh_assignment(str(idx)))
            menu.addAction(act_remove)
        elif tag == "__mesh_default__":
            act_edit = QAction("Edit Default Mesh Resolution…", menu)
            act_edit.triggered.connect(self._edit_default_mesh_resolution)
            menu.addAction(act_edit)
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
        rows = self._settings.get("object_boundaries", [])
        if not (0 <= index < len(rows)):
            return
        bc = rows[index]
        name = str(bc.get("name", f"BC {index + 1}"))
        reply = QMessageBox.question(
            self,
            "Remove Boundary Assignment",
            f"Remove assignment '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        rows.pop(index)
        self._refresh_object_boundaries()
        self.settings_changed.emit()

    def _remove_object_mesh_assignment(self, obj_name: str) -> None:
        mesh_cfg = self._settings.get("mesh", {})
        if not isinstance(mesh_cfg, dict):
            return
        obj_res = mesh_cfg.get("object_resolutions", {})
        if not isinstance(obj_res, dict):
            return
        name = str(obj_name).strip()
        if not name or name not in obj_res:
            return
        reply = QMessageBox.question(
            self,
            "Remove Mesh Assignment",
            f"Remove mesh assignment for '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        obj_res.pop(name, None)
        self._refresh_object_mesh_assignments()
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
        self._refresh_outputs()
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

        params = initial.get("params", {}) if isinstance(initial.get("params", {}), dict) else {}
        configured_ports = self._settings.get("ports", [])
        port_count = len(configured_ports) if isinstance(configured_ports, list) else 0
        port_count = max(1, port_count)
        s_parameter = QComboBox(dlg)
        s_parameter.addItems([
            f"S{output_port}{input_port}"
            for output_port in range(1, port_count + 1)
            for input_port in range(1, port_count + 1)
        ])
        selected_s_parameter = str(params.get("s_parameter", "S11")).strip().upper() or "S11"
        valid_parameters = {
            f"S{output_port}{input_port}"
            for output_port in range(1, port_count + 1)
            for input_port in range(1, port_count + 1)
        }
        if selected_s_parameter not in valid_parameters:
            selected_s_parameter = "S11"
        s_parameter.setCurrentText(selected_s_parameter)
        s_parameter.setToolTip(f"Select one of the {port_count * port_count} generated S-parameters")
        port_i = QSpinBox(dlg)
        port_i.setRange(1, 64)
        port_i.setValue(max(1, int(params.get("port_i", 1))))
        port_j = QSpinBox(dlg)
        port_j.setRange(1, 64)
        port_j.setValue(max(1, int(params.get("port_j", 1))))

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
        form.addRow("S-parameter", s_parameter)
        form.addRow("Smith/S-parameter port i", port_i)
        form.addRow("Smith/S-parameter port j", port_j)
        form.addRow("Far-field plane", cb_plane)
        form.addRow("Polar view", cb_polar_view)
        form.addRow("Far-field frequency", farfield_frequency)
        smith_rows = (5, 6)
        farfield_rows = (7, 8)

        def update_parameter_visibility(plot_type: str) -> None:
            is_smith = plot_type == "smith"
            for row in smith_rows:
                form.setRowVisible(row, is_smith)
            is_farfield = plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}
            for row in farfield_rows:
                form.setRowVisible(row, is_farfield)

        update_parameter_visibility(cb_type.currentText().strip())
        cb_type.currentTextChanged.connect(update_parameter_visibility)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        return {
            "name": le_name.text().strip() or "Output",
            "simulation": cb_sim.currentText().strip(),
            "plot_type": cb_type.currentText().strip(),
            "enabled": cb_enabled.currentText() == "Enabled",
            "params": {
                "s_parameter": s_parameter.currentText().strip().upper() or "S11",
                "port_i": int(port_i.value()),
                "port_j": int(port_j.value()),
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

        le_fmin = QLineEdit(_format_locale_number(float(initial.get("Fmin_GHz", 0.1))))
        le_fmax = QLineEdit(_format_locale_number(float(initial.get("Fmax_GHz", 10.0))))
        le_fstep = QLineEdit(_format_locale_number(float(initial.get("Fstep_GHz", 0.1))))
        le_modes = QLineEdit(str(int(initial.get("EigenmodeCount", 5))))
        le_param_name = QLineEdit(str(initial.get("ParamName", "")))
        le_param_values = QLineEdit(str(initial.get("ParamValues", "")))
        fit_config = initial.get("sparam_fitting", {}) if isinstance(initial.get("sparam_fitting", {}), dict) else {}
        fit_check = QCheckBox("Enable S-parameter line fitting", dlg)
        fit_check.setChecked(bool(fit_config.get("enabled", False)))
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
        form.addRow("Eigenmode count", le_modes)
        form.addRow("Parametric name", le_param_name)
        form.addRow("Parametric values (CSV)", le_param_values)
        form.addRow("S-parameter fitting", fit_check)
        form.addRow("Fitting points", fit_points)
        form.addRow("Log verbosity", cb_log)

        def _update_visibility(sim_type: str) -> None:
            is_sweep = sim_type == "Sweep"
            is_eigen = sim_type == "Eigenmode"
            is_param = sim_type == "Parametric"
            for w in (le_fmin, le_fmax, le_fstep):
                w.setVisible(is_sweep or is_param)
            le_modes.setVisible(is_eigen)
            le_param_name.setVisible(is_param)
            le_param_values.setVisible(is_param)
            fit_check.setVisible(is_sweep or is_param)
            fit_points.setVisible(is_sweep or is_param)
            fit_points.setEnabled(fit_check.isChecked() and (is_sweep or is_param))

        _update_visibility(current_type)
        cb_type.currentTextChanged.connect(_update_visibility)
        fit_check.toggled.connect(lambda checked: fit_points.setEnabled(bool(checked)))

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        try:
            sim_type = cb_type.currentText().strip().title()
            if sim_type not in _SIMULATION_TYPES:
                sim_type = "Sweep"
            fmin = _parse_locale_float(le_fmin.text())
            fmax = _parse_locale_float(le_fmax.text())
            fstep = _parse_locale_float(le_fstep.text())
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
                "EigenmodeCount": int(modes),
                "ParamName": le_param_name.text().strip(),
                "ParamValues": le_param_values.text().strip(),
                "sparam_fitting": {
                    "enabled": bool(fit_check.isChecked()),
                    "points": int(fit_points.value()),
                },
                "LogVerbosity": log_v,
            }
        except Exception:
            return None

    def _edit_boundary(self, item: QTreeWidgetItem, key: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit boundary – {key}")
        form = QFormLayout(dlg)
        combo = QComboBox()
        combo.addItems(_BOUNDARY_TYPES)
        current = self._settings["boundaries"].get(key, "PML")
        combo.setCurrentText(current)
        form.addRow(key, combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() == QDialog.Accepted:
            new_val = combo.currentText()
            self._settings["boundaries"][key] = new_val
            item.setText(1, new_val)
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
        port = self._port_dialog_data(
            initial={"name": "Port", "type": "WaveguidePort", "x": 0.0, "y": 0.0, "z": 0.0},
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
            initial = {
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
                        val = _parse_locale_float(raw) if raw else None
                        parsed_params[key] = val if val is not None else default_val
                    else:
                        parsed_params[key] = raw if raw else ""

                if port_type == "LumpedPort":
                    # Defensive defaults to avoid accidental impedance reset.
                    parsed_params.setdefault("Resistance_Ohm", float(_PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Resistance_Ohm"]))
                    parsed_params.setdefault("Voltage_V", float(_PORT_TYPE_DEFAULT_PARAMS["LumpedPort"]["Voltage_V"]))

                port = {
                    "name": le_name.text().strip() or "Port",
                    "type": port_type,
                    "x": _parse_locale_float(le_x.text()),
                    "y": _parse_locale_float(le_y.text()),
                    "z": _parse_locale_float(le_z.text()),
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
        bc = self._object_boundary_dialog_data(
            initial={"name": f"BC_{obj_name}", "type": "PML"},
            fixed_object=obj_name,
            title=f"Assign Boundary Condition - {obj_name}",
        )
        if bc is None:
            return
        self._settings["object_boundaries"].append(bc)
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
                        parsed_params[key] = _parse_locale_int(raw)
                    elif isinstance(default_val, float):
                        parsed_params[key] = _parse_locale_float(raw)
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
        if changed:
            self._refresh_ports()
            self._refresh_object_boundaries()
            self._refresh_object_mesh_assignments()
            self.settings_changed.emit()


# ─────────────────────────────────────────────────────────────────────────────
def _deep_copy(d):
    import json
    return json.loads(json.dumps(d))
