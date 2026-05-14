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
from PyQt5.QtCore import Qt, pyqtSignal


# ──────────────────────────────────────────────────────────────────── constants
_BOUNDARY_TYPES = ["PML", "PEC", "PMC", "Open", "Periodic"]
_BOUNDARY_KEYS  = ["Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax"]
_PORT_TYPES = ["WaveguidePort", "LumpedPort", "PlaneWave"]

_PORT_TYPE_DEFAULT_PARAMS = {
    "WaveguidePort": {"Mode": "TE10", "Impedance_Ohm": 50.0, "Excitation": 1.0},
    "LumpedPort": {"Resistance_Ohm": 50.0, "Voltage_V": 1.0},
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

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "boundaries": {k: "PML" for k in _BOUNDARY_KEYS},
    "ports":      [],
    "object_boundaries": [],
    "simulation": {"Fmin_GHz": 0.0, "Fmax_GHz": 10.0, "Fstep_GHz": 0.1},
    "mesh":       {"MaxCellSize": 1.0, "MinCellSize": 0.05,
                   "LinesPerWavelength": 10},
}


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
        self._tree.setHeaderHidden(True)
        self._tree.header().setDefaultSectionSize(110)
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

        self._settings = merged
        self._populate()

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

        # Simulation
        sim = self._settings["simulation"]
        for k, v in sim.items():
            self._make_leaf(self._s_node, k, str(v), editable=True)

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

        if isinstance(role, tuple) and len(role) == 2:
            tag, idx = role
            if tag == "__port_idx__":
                self._edit_port_dialog(int(idx))
                return
            if tag == "__bc_idx__":
                self._edit_object_boundary_dialog(int(idx))
                return

        key = item.text(0)
        val = item.text(1)

        # Boundary
        if parent is self._b_node:
            self._edit_boundary(item, key)
        # Simulation numeric
        elif parent is self._s_node:
            self._edit_numeric(item, key, val,
                               self._settings["simulation"])
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
                parsed = float(new_val.strip())
                target_dict[key] = parsed
                item.setText(1, str(parsed))
                self.settings_changed.emit()
            except ValueError:
                pass

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
        port = self._port_dialog_data(
            initial={"name": f"Port_{obj_name}", "type": "WaveguidePort", "x": 0.0, "y": 0.0, "z": 0.0},
            fixed_object=obj_name,
            title=f"Assign Port - {obj_name}",
        )
        if port is None:
            return
        self._settings["ports"].append(port)
        self._refresh_ports()
        self.settings_changed.emit()

    def _port_dialog_data(self, initial: dict, fixed_object: str | None, title: str) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)

        current_type = str(initial.get("type", "WaveguidePort"))
        if current_type not in _PORT_TYPES:
            current_type = "WaveguidePort"

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

        params_container = QWidget(dlg)
        params_form = QFormLayout(params_container)
        param_edits: Dict[str, QLineEdit] = {}

        def _rebuild_param_fields(port_type: str) -> None:
            while params_form.rowCount() > 0:
                params_form.removeRow(0)
            param_edits.clear()

            defaults = _PORT_TYPE_DEFAULT_PARAMS.get(port_type, {})
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
        form.addRow("X",    le_x)
        form.addRow("Y",    le_y)
        form.addRow("Z",    le_z)
        form.addRow("Type Parameters", params_container)
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
                        parsed_params[key] = int(raw)
                    elif isinstance(default_val, float):
                        parsed_params[key] = float(raw)
                    else:
                        parsed_params[key] = raw

                port = {
                    "name": le_name.text().strip() or "Port",
                    "type": port_type,
                    "x": float(le_x.text()),
                    "y": float(le_y.text()),
                    "z": float(le_z.text()),
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
                        parsed_params[key] = int(raw)
                    elif isinstance(default_val, float):
                        parsed_params[key] = float(raw)
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
