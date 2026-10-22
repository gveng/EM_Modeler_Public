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

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Optional
import uuid

from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .formula_widgets import FormulaDoubleSpinBox as QDoubleSpinBox

from ..emerge.material_store import DEFAULT_COLOR, MaterialRecord, MaterialStore


_FAMILIES = ["Common", "Metals", "Dielectrics", "Foams", "Semiconductors", "Liquids"]


class _FrequencyDataDialog(QDialog):
    """Edit isotropic material data in GHz; values returned to the model use Hz."""

    def __init__(self, parent=None, frequency_data=None, property_name: str = "er"):
        super().__init__(parent)
        self.setWindowTitle(f"{property_name} Frequency Data")
        self.resize(460, 300)
        self.frequency_data_hz: list[dict[str, float]] | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Enter frequency in GHz and the real and imaginary components."
        ))

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Frequency [GHz]", "Real", "Imaginary"])
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 1)

        row_buttons = QHBoxLayout()
        self._add_row_btn = QPushButton("Add Row")
        self._remove_row_btn = QPushButton("Remove Row")
        self._add_row_btn.clicked.connect(self._add_row)
        self._remove_row_btn.clicked.connect(self._remove_row)
        row_buttons.addWidget(self._add_row_btn)
        row_buttons.addWidget(self._remove_row_btn)
        row_buttons.addStretch(1)
        layout.addLayout(row_buttons)

        for point in frequency_data or []:
            self._add_row(
                float(point["frequency_hz"]) / 1e9,
                float(point["real"]),
                float(point["imag"]),
            )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(
        self, frequency_ghz: float = 1.0, real: float = 1.0, imag: float = 0.0
    ) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for column, value in enumerate((frequency_ghz, real, imag)):
            self._table.setItem(row, column, QTableWidgetItem(format(value, ".12g")))

    def _remove_row(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _accept(self) -> None:
        points: list[dict[str, float]] = []
        try:
            for row in range(self._table.rowCount()):
                values = []
                for column in range(3):
                    item = self._table.item(row, column)
                    if item is None or not item.text().strip():
                        raise ValueError(f"Row {row + 1} must have all three values.")
                    value = float(item.text())
                    if not math.isfinite(value):
                        raise ValueError(f"Row {row + 1} values must be finite.")
                    values.append(value)

                frequency_hz = values[0] * 1e9
                if not math.isfinite(frequency_hz) or frequency_hz <= 0:
                    raise ValueError(
                        f"Row {row + 1} frequency must be positive and finite."
                    )
                points.append({
                    "frequency_hz": frequency_hz,
                    "real": values[1],
                    "imag": values[2],
                })
        except ValueError as exc:
            QMessageBox.warning(self, "Frequency Data", str(exc))
            return

        points.sort(key=lambda point: point["frequency_hz"])
        if any(
            points[index - 1]["frequency_hz"] == points[index]["frequency_hz"]
            for index in range(1, len(points))
        ):
            QMessageBox.warning(
                self, "Frequency Data", "Frequencies must be unique."
            )
            return

        self.frequency_data_hz = points
        self.accept()


class _MaterialEditorDialog(QDialog):
    def __init__(self, parent=None, record: MaterialRecord | None = None):
        super().__init__(parent)
        self.setWindowTitle("Material")
        self.resize(460, 520)

        self._record = record
        self._color = str(record.color if record is not None else DEFAULT_COLOR)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self._name = QLineEdit(str(record.name) if record is not None else "")
        form.addRow("Name", self._name)

        self._family = QComboBox()
        self._family.addItems(_FAMILIES)
        family = str(record.family) if record is not None else "Common"
        idx = self._family.findText(family)
        self._family.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Family", self._family)

        self._er = QDoubleSpinBox()
        self._er.setRange(0.0, 1e9)
        self._er.setDecimals(6)
        self._er.setValue(float(record.er) if record is not None else 1.0)
        form.addRow("er", self._er)
        self._er_frequency_data = [
            dict(point) for point in record.er_frequency_data
        ] if record is not None else []
        self._er_frequency_btn = QPushButton()
        self._er_frequency_btn.clicked.connect(
            lambda: self._edit_frequency_data("er")
        )
        form.addRow("er frequency data", self._er_frequency_btn)
        self._refresh_frequency_button("er")

        self._ur = QDoubleSpinBox()
        self._ur.setRange(0.0, 1e9)
        self._ur.setDecimals(6)
        self._ur.setValue(float(record.ur) if record is not None else 1.0)
        form.addRow("ur", self._ur)
        self._ur_frequency_data = [
            dict(point) for point in record.ur_frequency_data
        ] if record is not None else []
        self._ur_frequency_btn = QPushButton()
        self._ur_frequency_btn.clicked.connect(
            lambda: self._edit_frequency_data("ur")
        )
        form.addRow("ur frequency data", self._ur_frequency_btn)
        self._refresh_frequency_button("ur")

        self._tan_d = QDoubleSpinBox()
        self._tan_d.setRange(0.0, 1.0)
        self._tan_d.setDecimals(8)
        self._tan_d.setSingleStep(0.0001)
        self._tan_d.setValue(float(record.tan_d) if record is not None else 0.0)
        form.addRow("tan d", self._tan_d)

        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.0, 1e12)
        self._sigma.setDecimals(6)
        self._sigma.setValue(float(record.sigma) if record is not None else 0.0)
        form.addRow("sigma [S/m]", self._sigma)

        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.0, 1.0)
        self._opacity.setDecimals(2)
        self._opacity.setSingleStep(0.05)
        self._opacity.setValue(float(record.opacity) if record is not None else 0.85)
        form.addRow("Opacity", self._opacity)

        color_row = QHBoxLayout()
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(20, 20)
        self._color_preview.setFrameShape(QFrame.Box)
        self._color_label = QLabel(self._color)
        pick_btn = QPushButton("Pick...")
        pick_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self._color_preview)
        color_row.addWidget(self._color_label, 1)
        color_row.addWidget(pick_btn)
        form.addRow("Color", color_row)
        self._update_color_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.result_record: MaterialRecord | None = None

    def _refresh_frequency_button(self, property_name: str) -> None:
        points = getattr(self, f"_{property_name}_frequency_data")
        button = getattr(self, f"_{property_name}_frequency_btn")
        button.setText(
            f"Edit... ({len(points)} point{'s' if len(points) != 1 else ''})"
        )

    def _edit_frequency_data(self, property_name: str) -> None:
        points = getattr(self, f"_{property_name}_frequency_data")
        dialog = _FrequencyDataDialog(self, points, property_name)
        if dialog.exec_() != QDialog.Accepted or dialog.frequency_data_hz is None:
            return
        setattr(self, f"_{property_name}_frequency_data", dialog.frequency_data_hz)
        self._refresh_frequency_button(property_name)

    def _update_color_preview(self) -> None:
        self._color_preview.setStyleSheet(f"background:{self._color};")
        self._color_label.setText(self._color)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if not color.isValid():
            return
        self._color = color.name()
        self._update_color_preview()

    def _accept(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Material", "Material name is required.")
            return

        self.result_record = MaterialRecord(
            uid=str(self._record.uid if self._record is not None else uuid.uuid4()),
            name=name,
            family=self._family.currentText(),
            er=float(self._er.value()),
            ur=float(self._ur.value()),
            tan_d=float(self._tan_d.value()),
            sigma=float(self._sigma.value()),
            color=self._color,
            opacity=float(self._opacity.value()),
            source=self._record.source if self._record is not None else "project",
            priority=self._record.priority if self._record is not None else 0,
            er_frequency_data=self._er_frequency_data,
            ur_frequency_data=self._ur_frequency_data,
        )
        self.accept()


class MaterialLibraryDialog(QDialog):
    def __init__(self, parent, store: MaterialStore, on_store_changed: Callable[[], None]):
        super().__init__(parent)
        self.setWindowTitle("Material Library")
        self.resize(920, 560)

        self._store = store
        self._on_store_changed = on_store_changed
        self._changed = False

        root = QVBoxLayout(self)

        top_row = QHBoxLayout()
        root.addLayout(top_row)

        self._source_combo = QComboBox()
        self._source_combo.addItems(["Project Library", "Global Library"])
        self._source_combo.currentIndexChanged.connect(self._refresh_list)
        top_row.addWidget(QLabel("Source"))
        top_row.addWidget(self._source_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search material")
        self._search.textChanged.connect(self._refresh_list)
        top_row.addWidget(self._search, 1)

        self._global_path = QLabel()
        self._global_path.setWordWrap(True)
        root.addWidget(self._global_path)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.currentItemChanged.connect(self._update_details)
        body.addWidget(self._list, 1)

        details_box = QWidget()
        details_layout = QVBoxLayout(details_box)
        details_layout.setContentsMargins(0, 0, 0, 0)

        self._title = QLabel("No material selected")
        self._title.setStyleSheet("font-size: 14px; font-weight: 600;")
        details_layout.addWidget(self._title)

        self._family = QLabel("Family: -")
        self._source = QLabel("Source: -")
        self._er = QLabel("er: -")
        self._ur = QLabel("ur: -")
        self._tan_d = QLabel("tan d: -")
        self._sigma = QLabel("sigma [S/m]: -")
        self._opacity = QLabel("opacity: -")
        details_layout.addWidget(self._family)
        details_layout.addWidget(self._source)
        details_layout.addWidget(self._er)
        details_layout.addWidget(self._ur)
        details_layout.addWidget(self._tan_d)
        details_layout.addWidget(self._sigma)
        details_layout.addWidget(self._opacity)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(18, 18)
        self._color_preview.setFrameShape(QFrame.Box)
        self._color_value = QLabel("-")
        color_row.addWidget(self._color_preview)
        color_row.addWidget(self._color_value, 1)
        details_layout.addLayout(color_row)
        details_layout.addStretch(1)
        body.addWidget(details_box, 1)

        actions = QHBoxLayout()
        root.addLayout(actions)

        self._new_btn = QPushButton("New Material...")
        self._new_btn.clicked.connect(self._new_material)
        actions.addWidget(self._new_btn)

        self._edit_btn = QPushButton("Edit Material...")
        self._edit_btn.clicked.connect(self._edit_material)
        actions.addWidget(self._edit_btn)

        self._copy_btn = QPushButton("Copy to Project Library")
        self._copy_btn.clicked.connect(self._copy_to_project)
        actions.addWidget(self._copy_btn)

        self._delete_btn = QPushButton("Delete Project Material")
        self._delete_btn.clicked.connect(self._delete_material)
        actions.addWidget(self._delete_btn)

        self._append_btn = QPushButton("Append Global to Project")
        self._append_btn.clicked.connect(self._append_global)
        actions.addWidget(self._append_btn)

        actions.addStretch(1)

        self._set_global_btn = QPushButton("Set Global DB...")
        self._set_global_btn.clicked.connect(self._set_global_db)
        actions.addWidget(self._set_global_btn)

        self._reload_global_btn = QPushButton("Reload Global DB")
        self._reload_global_btn.clicked.connect(self._reload_global_db)
        actions.addWidget(self._reload_global_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self._refresh_list()

    @property
    def changed(self) -> bool:
        return self._changed

    def _project_view_records(self) -> dict[str, MaterialRecord]:
        return self._store.project_records()

    def _global_view_records(self) -> dict[str, MaterialRecord]:
        return self._store.global_records()

    def _active_records(self) -> dict[str, MaterialRecord]:
        if self._source_combo.currentIndex() == 1:
            return self._global_view_records()
        return self._project_view_records()

    def _current_record(self) -> Optional[MaterialRecord]:
        item = self._list.currentItem()
        if item is None:
            return None
        return self._active_records().get(item.text())

    def _refresh_global_path(self) -> None:
        path = self._store.global_db_path or "Not configured"
        self._global_path.setText(f"Global DB: {path}")

    def _refresh_list(self) -> None:
        selected = self._list.currentItem().text() if self._list.currentItem() is not None else ""
        query = self._search.text().strip().lower()
        self._refresh_global_path()

        self._list.blockSignals(True)
        try:
            self._list.clear()
            for rec in sorted(self._active_records().values(), key=lambda item: item.name.lower()):
                if query and query not in rec.name.lower():
                    continue
                self._list.addItem(QListWidgetItem(rec.name))
            if selected:
                for idx in range(self._list.count()):
                    item = self._list.item(idx)
                    if item.text() == selected:
                        self._list.setCurrentItem(item)
                        break
            if self._list.currentItem() is None and self._list.count() > 0:
                self._list.setCurrentRow(0)
        finally:
            self._list.blockSignals(False)

        self._update_details()

    def _update_details(self) -> None:
        rec = self._current_record()
        if rec is None:
            self._title.setText("No material selected")
            self._family.setText("Family: -")
            self._source.setText("Source: -")
            self._er.setText("er: -")
            self._ur.setText("ur: -")
            self._tan_d.setText("tan d: -")
            self._sigma.setText("sigma [S/m]: -")
            self._opacity.setText("opacity: -")
            self._color_preview.setStyleSheet("")
            self._color_value.setText("-")
        else:
            self._title.setText(rec.name)
            self._family.setText(f"Family: {rec.family}")
            self._source.setText(f"Source: {rec.source}")
            self._er.setText(f"er: {rec.er:.6g}")
            self._ur.setText(f"ur: {rec.ur:.6g}")
            self._tan_d.setText(f"tan d: {rec.tan_d:.6g}")
            self._sigma.setText(f"sigma [S/m]: {rec.sigma:.6g}")
            self._opacity.setText(f"opacity: {rec.opacity:.2f}")
            self._color_preview.setStyleSheet(f"background:{rec.color};")
            self._color_value.setText(rec.color)

        editable = rec is not None and rec.source in {"project", "global"}
        project_custom = rec is not None and rec.source == "project"
        global_selected = rec is not None and self._source_combo.currentIndex() == 1
        builtin_selected = rec is not None and rec.source == "builtin" and self._source_combo.currentIndex() == 0
        self._edit_btn.setText(
            "Edit Global Material..." if rec is not None and rec.source == "global"
            else "Edit Project Material..."
        )
        self._edit_btn.setEnabled(editable)
        self._copy_btn.setEnabled(builtin_selected)
        self._delete_btn.setEnabled(project_custom)
        self._append_btn.setEnabled(global_selected)

    def _mark_changed(self) -> None:
        self._changed = True
        self._on_store_changed()
        self._refresh_list()

    def _new_material(self) -> None:
        if not self._store.global_db_path:
            QMessageBox.warning(
                self,
                "Global Material Database",
                "Select a Global DB before creating a material.",
            )
            return
        dlg = _MaterialEditorDialog(self)
        if dlg.exec_() != QDialog.Accepted or dlg.result_record is None:
            return
        try:
            self._store.upsert_global_record(dlg.result_record)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Global Material Database", f"Unable to save material: {exc}")
            return
        self._source_combo.setCurrentIndex(1)
        self._mark_changed()

    def _edit_material(self) -> None:
        rec = self._current_record()
        if rec is None or rec.source not in {"project", "global"}:
            return
        dlg = _MaterialEditorDialog(self, rec)
        if dlg.exec_() != QDialog.Accepted or dlg.result_record is None:
            return
        try:
            if rec.source == "global":
                self._store.upsert_global_record(dlg.result_record)
            else:
                self._store.upsert_project_record(dlg.result_record)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Material Library", f"Unable to save material: {exc}")
            return
        self._mark_changed()

    def _delete_material(self) -> None:
        rec = self._current_record()
        if rec is None or rec.source != "project":
            return
        if QMessageBox.question(
            self,
            "Delete Material",
            f"Delete project material '{rec.name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        if self._store.delete_project_record(rec.name):
            self._mark_changed()

    def _copy_to_project(self) -> None:
        rec = self._current_record()
        if rec is None or rec.source != "builtin" or self._source_combo.currentIndex() != 0:
            return
        try:
            self._store.copy_builtin_to_project(rec.name)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Material Library", f"Unable to copy material: {exc}")
            return
        self._mark_changed()

    def _append_global(self) -> None:
        rec = self._current_record()
        if rec is None:
            return
        added = self._store.append_global_to_project([rec.name])
        if added:
            self._source_combo.setCurrentIndex(0)
            self._mark_changed()

    def _set_global_db(self) -> None:
        suggested = self._store.global_db_path or str(Path.home() / "em3d_materials_global.json")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._store.set_global_db_path(path, create_if_missing=False)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Global Material Database", f"Unable to load {path}: {exc}")
            return
        self._mark_changed()

    def _reload_global_db(self) -> None:
        suggested = self._store.global_db_path or str(Path.home() / "em3d_materials_global.json")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._store.set_global_db_path(path, create_if_missing=False)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Global Material Database", f"Unable to load {path}: {exc}")
            return
        self._mark_changed()
