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
      ├── MaxCellSize
      ├── MinCellSize
      └── LinesPerWavelength
"""
from __future__ import annotations
from typing import Any, Dict

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QPushButton, QInputDialog, QComboBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QMenu, QAction, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QLocale


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

_LOG_VERBOSITY_LEVELS = ["Debug", "Info", "Warning", "Error"]
_SIMULATION_TYPES = ["Sweep", "Eigenmode", "Parametric"]


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
        "LogVerbosity": "Info",
    }

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "boundaries": {k: "PML" for k in _BOUNDARY_KEYS},
    "ports":      [],
    "object_boundaries": [],
    "simulation": {"Fmin_GHz": 0.1, "Fmax_GHz": 10.0, "Fstep_GHz": 0.1, "LogVerbosity": "Info"},
    "simulations": [_default_simulation_item()],
    "mesh":       {"MaxCellSize": 1.0, "MinCellSize": 0.05,
                   "LinesPerWavelength": 10},
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

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_name = "Untitled"
        self._settings: Dict[str, Any] = _deep_copy(_DEFAULT_SETTINGS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["", ""])
        
        from PyQt5.QtWidgets import QHeaderView
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

        self._settings = merged
        self._sync_legacy_simulation_from_list()
        self._populate()

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

        # Mesh
        mesh = self._settings["mesh"]
        for k, v in mesh.items():
            self._make_leaf(self._m_node, k, str(v), editable=True)

        self._tree.expandAll()

    def _make_section(self, parent: QTreeWidgetItem,
                      label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, ""])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
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
            if obj_name:
                txt = f"Port {i+1}: {port.get('type', '?')} -> {obj_name}"
            else:
                txt = (f"Port {i+1}: {port.get('type','?')} "
                       f"@ [{port.get('x',0)},{port.get('y',0)},{port.get('z',0)}]")
            row = QTreeWidgetItem([txt, ""])
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

        key = item.text(0)
        val = item.text(1)

        # Boundary
        if parent is self._b_node:
            self._edit_boundary(item, key)
        # Simulation numeric
        elif parent is self._s_node:
            return
        # Mesh numeric
        elif parent is self._m_node:
            self._edit_numeric(item, key, val,
                               self._settings["mesh"])

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
        self.settings_changed.emit()

    def _edit_simulation_dialog(self, index: int) -> None:
        sims = self._settings.get("simulations", [])
        if not isinstance(sims, list) or not (0 <= index < len(sims)):
            return
        current = _deep_copy(sims[index]) if isinstance(sims[index], dict) else _default_simulation_item()
        updated = self._simulation_dialog_data(current, title="Edit Simulation")
        if updated is None:
            return
        sims[index] = updated
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
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
        self._sync_legacy_simulation_from_list()
        self._refresh_simulations()
        self.settings_changed.emit()

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

        _update_visibility(current_type)
        cb_type.currentTextChanged.connect(_update_visibility)

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

        from PyQt5.QtWidgets import QLabel as _QLabel
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
        if changed:
            self._refresh_ports()
            self._refresh_object_boundaries()
            self.settings_changed.emit()


# ─────────────────────────────────────────────────────────────────────────────
def _deep_copy(d):
    import json
    return json.loads(json.dumps(d))
