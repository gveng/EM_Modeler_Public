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

"""Dialog for editing project Variables."""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from .unit_options import UNIT_OPTIONS

_MM_PER_UNIT = {
    "mm": 1.0,
    "um": 0.001,
    "cm": 10.0,
    "m": 1000.0,
    "mil": 0.0254,
    "inch": 25.4,
}


class ParametersDialog(QDialog):
    """Standalone editor for project variables."""

    def __init__(
        self,
        parameters: List[Dict[str, Any]] | None = None,
        parent=None,
        units: str = "mm",
    ):
        super().__init__(parent)
        self.setWindowTitle("Parameters")
        self.resize(560, 360)
        self._default_unit = units if units in UNIT_OPTIONS else UNIT_OPTIONS[0]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Define named variables for formulas used by object dimensions."))

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["Name", "Value", "Unit"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        actions = QHBoxLayout()
        add_button = QPushButton("Add")
        copy_button = QPushButton("Copy")
        remove_button = QPushButton("Remove")
        add_button.clicked.connect(self._add_row)
        copy_button.clicked.connect(self._copy_row)
        remove_button.clicked.connect(self._remove_row)
        actions.addWidget(add_button)
        actions.addWidget(copy_button)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for entry in parameters if isinstance(parameters, list) else []:
            if not isinstance(entry, dict):
                continue
            self._append_row(
                str(entry.get("name", "")),
                str(entry.get("value", 0)),
                str(entry.get("unit", "")),
            )
        if self._table.rowCount() == 0:
            self._add_row()

    def _append_row(self, name: str = "", value: str = "", unit: str = "") -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for column, text in enumerate((name, value)):
            self._table.setItem(row, column, QTableWidgetItem(text))
        unit_combo = QComboBox(self._table)
        unit_combo.addItems(UNIT_OPTIONS)
        initial_unit = unit if unit in UNIT_OPTIONS else self._default_unit
        unit_combo.setCurrentText(initial_unit)
        unit_combo.setProperty("previous_unit", initial_unit)
        unit_combo.currentTextChanged.connect(
            lambda new_unit, combo=unit_combo: self._on_unit_changed(combo, new_unit)
        )
        self._table.setCellWidget(row, 2, unit_combo)
        return row

    def _on_unit_changed(self, unit_combo: QComboBox, new_unit: str) -> None:
        old_unit = str(unit_combo.property("previous_unit") or "")
        unit_combo.setProperty("previous_unit", new_unit)
        if old_unit not in _MM_PER_UNIT or new_unit not in _MM_PER_UNIT:
            return
        row = next(
            (index for index in range(self._table.rowCount())
             if self._table.cellWidget(index, 2) is unit_combo),
            -1,
        )
        if row < 0:
            return
        value_item = self._table.item(row, 1)
        if value_item is None:
            return
        raw_value = value_item.text().strip()
        try:
            value = float(raw_value.replace(",", "."))
        except ValueError:
            return
        factor = _MM_PER_UNIT[old_unit] / _MM_PER_UNIT[new_unit]
        value_item.setText(str(value * factor))

    def _add_row(self) -> None:
        row = self._append_row()
        self._table.selectRow(row)
        self._table.editItem(self._table.item(row, 0))

    def _copy_row(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        values = [self._table.item(row, column).text() for column in range(2)]
        unit_combo = self._table.cellWidget(row, 2)
        values.append(unit_combo.currentText() if isinstance(unit_combo, QComboBox) else self._default_unit)
        new_row = self._append_row(*values)
        self._table.selectRow(new_row)

    def _remove_row(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def result_parameters(self) -> List[Dict[str, Any]]:
        result = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None or not name_item.text().strip():
                continue
            value_item = self._table.item(row, 1)
            unit_combo = self._table.cellWidget(row, 2)
            raw_value = value_item.text().strip() if value_item else "0"
            try:
                value: Any = float(raw_value.replace(",", "."))
            except ValueError:
                value = raw_value
            result.append({
                "name": name_item.text().strip(),
                "value": value,
                "unit": unit_combo.currentText() if isinstance(unit_combo, QComboBox) else self._default_unit,
            })
        return result
