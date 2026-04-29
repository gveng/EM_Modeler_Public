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
)
from PyQt5.QtCore import Qt, pyqtSignal


# ──────────────────────────────────────────────────────────────────── constants
_BOUNDARY_TYPES = ["PML", "PEC", "PMC", "Open", "Periodic"]
_BOUNDARY_KEYS  = ["Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax"]

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "boundaries": {k: "PML" for k in _BOUNDARY_KEYS},
    "ports":      [],
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

        lbl = QLabel("EMERGE Settings")
        lbl.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(lbl)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Parameter", "Value"])
        self._tree.header().setDefaultSectionSize(110)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
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
        self._settings = _deep_copy(settings)
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
            txt = (f"Port {i+1}: {port.get('type','?')} "
                   f"@ [{port.get('x',0)},{port.get('y',0)},{port.get('z',0)}]")
            self._p_node.addChild(QTreeWidgetItem([txt, ""]))
        # Add-port hint
        hint = QTreeWidgetItem(["[+ double-click to add port]", ""])
        hint.setData(0, Qt.UserRole, "__add_port__")
        self._p_node.addChild(hint)

    # ─────────────────────────────────────────────────── editing
    def _on_double_click(self, item: QTreeWidgetItem, col: int) -> None:
        parent = item.parent()
        if parent is None:
            return

        # Add-port action
        if item.data(0, Qt.UserRole) == "__add_port__":
            self._add_port_dialog()
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
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Port")
        form = QFormLayout(dlg)
        le_name = QLineEdit("Port")
        le_type = QComboBox()
        le_type.addItems(["WaveguidePort", "LumpedPort", "PlaneWave"])
        le_x = QLineEdit("0")
        le_y = QLineEdit("0")
        le_z = QLineEdit("0")
        form.addRow("Name", le_name)
        form.addRow("Type", le_type)
        form.addRow("X",    le_x)
        form.addRow("Y",    le_y)
        form.addRow("Z",    le_z)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec_() == QDialog.Accepted:
            try:
                port = {
                    "name": le_name.text(),
                    "type": le_type.currentText(),
                    "x":    float(le_x.text()),
                    "y":    float(le_y.text()),
                    "z":    float(le_z.text()),
                }
                self._settings["ports"].append(port)
                self._refresh_ports()
                self.settings_changed.emit()
            except ValueError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
def _deep_copy(d):
    import json
    return json.loads(json.dumps(d))
