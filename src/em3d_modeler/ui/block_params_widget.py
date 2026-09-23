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

"""Block Parameters panel: shows / edits parameters of selected EM object."""
from __future__ import annotations
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox,
    QSlider, QHBoxLayout,
)
from PySide6.QtCore import Qt, Signal

from ..scene.em_objects import EMObject, MATERIAL_COLORS

_ALL_MATERIALS = list(MATERIAL_COLORS.keys()) + ["Custom"]


class BlockParamsWidget(QWidget):
    """Bottom-left panel: editable parameters for the selected 3D object."""

    params_changed = Signal(object, dict)   # (EMObject, new_params)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._obj: Optional[EMObject] = None
        self._blocked = False
        self._formula_resolver = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        self._title = QLabel("Block Parameters")
        self._title.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(self._title)

        # Material row
        mat_row = QHBoxLayout()
        mat_row.addWidget(QLabel("Material:"))
        self._mat_combo = QComboBox()
        self._mat_combo.addItems(_ALL_MATERIALS)
        self._mat_combo.currentTextChanged.connect(self._material_changed)
        mat_row.addWidget(self._mat_combo)
        layout.addLayout(mat_row)

        # Opacity row
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Opacity:"))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(5, 100)
        self._opacity_slider.setValue(85)
        self._opacity_slider.valueChanged.connect(self._opacity_changed)
        op_row.addWidget(self._opacity_slider)
        self._opacity_label = QLabel("0.85")
        op_row.addWidget(self._opacity_label)
        layout.addLayout(op_row)

        # Parameters table
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._table_item_changed)
        layout.addWidget(self._table)

        self._set_no_selection()

    # ─────────────────────────────────────────────────── public API
    def set_object(self, obj: Optional[EMObject]) -> None:
        self._obj = obj
        self._blocked = True
        try:
            if obj is None:
                self._set_no_selection()
            else:
                self._refresh(obj)
        finally:
            self._blocked = False

    def set_formula_resolver(self, resolver) -> None:
        self._formula_resolver = resolver

    # ─────────────────────────────────────────────────── display
    def _set_no_selection(self) -> None:
        self._title.setText("Block Parameters")
        self._table.setRowCount(0)
        self._mat_combo.setEnabled(False)
        self._opacity_slider.setEnabled(False)

    def _refresh(self, obj: EMObject) -> None:
        self._title.setText(f"{type(obj).__name__}:  {obj.name}")
        self._mat_combo.setEnabled(True)
        self._opacity_slider.setEnabled(True)

        # Material combo
        idx = self._mat_combo.findText(obj.material)
        self._mat_combo.setCurrentIndex(max(idx, 0))

        # Opacity slider
        self._opacity_slider.setValue(int(obj.opacity * 100))
        self._opacity_label.setText(f"{obj.opacity:.2f}")

        # Parameters table (exclude Material and Opacity – shown above)
        params = obj.get_parameters()
        exclude = {"Material", "Opacity"}
        rows = [(k, v) for k, v in params.items() if k not in exclude]
        self._table.setRowCount(len(rows))
        for row, (k, v) in enumerate(rows):
            key_item = QTableWidgetItem(k)
            key_item.setFlags(Qt.ItemIsEnabled)          # read-only key
            val_item = QTableWidgetItem(str(v))
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, val_item)

    # ─────────────────────────────────────────────────── editing
    def _collect_params(self) -> Dict[str, Any]:
        """Read all rows from table + material + opacity."""
        params: Dict[str, Any] = {}
        metadata_keys = {
            "StepSourcePath",
            "StepSolidName",
            "StepGeometryModified",
            "StepExportOffset",
            "BooleanOperation",
            "BooleanSourceNames",
            "BooleanSourcesData",
        }
        for row in range(self._table.rowCount()):
            k = self._table.item(row, 0)
            v = self._table.item(row, 1)
            if k and v:
                if k.text() in metadata_keys:
                    params[k.text()] = self._obj.get_parameters().get(k.text(), v.text())
                    continue
                try:
                    params[k.text()] = float(v.text())
                except ValueError:
                    if self._formula_resolver is None:
                        params[k.text()] = v.text()
                    else:
                        params[k.text()] = self._formula_resolver(v.text())
        params["Material"] = self._mat_combo.currentText()
        params["Opacity"]  = self._opacity_slider.value() / 100.0
        return params

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._blocked or self._obj is None or item.column() != 1:
            return
        self._apply_params()

    def _material_changed(self, _text: str) -> None:
        if self._blocked or self._obj is None:
            return
        self._apply_params()

    def _opacity_changed(self, value: int) -> None:
        self._opacity_label.setText(f"{value / 100:.2f}")
        if self._blocked or self._obj is None:
            return
        self._apply_params()

    def _apply_params(self) -> None:
        if self._obj is None:
            return
        params = self._collect_params()
        self._obj.set_parameters(params)
        self.params_changed.emit(self._obj, params)
