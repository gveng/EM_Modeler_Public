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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class ParametersDialog(QDialog):
    """Standalone editor for project variables."""

    def __init__(self, parameters: List[Dict[str, Any]] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parameters")
        self.resize(560, 360)

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
        for column, text in enumerate((name, value, unit)):
            self._table.setItem(row, column, QTableWidgetItem(text))
        return row

    def _add_row(self) -> None:
        row = self._append_row()
        self._table.selectRow(row)
        self._table.editItem(self._table.item(row, 0))

    def _copy_row(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        values = [self._table.item(row, column).text() for column in range(3)]
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
            unit_item = self._table.item(row, 2)
            raw_value = value_item.text().strip() if value_item else "0"
            try:
                value: Any = float(raw_value.replace(",", "."))
            except ValueError:
                value = raw_value
            result.append({
                "name": name_item.text().strip(),
                "value": value,
                "unit": unit_item.text().strip() if unit_item else "",
            })
        return result
