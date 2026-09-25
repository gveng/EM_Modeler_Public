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

"""Body Properties panel: shows / edits parameters of one or more selected EM objects.

Single selection  → full geometry + name + material + opacity editor
Multi-selection   → shows object count, common material combo, opacity slider,
                    and an "Apply to all" button
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QSlider, QPushButton, QFrame, QInputDialog, QColorDialog, QCheckBox, QMessageBox,
    QCompleter, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence

from ..scene.em_objects import EMObject, ExtrudedObject, MeshObject, RevolvedObject
from ..scene.param_expr import evaluate_expression


class _VariableCompleterLineEdit(QLineEdit):
    def __init__(self, variables: List[str], parent=None):
        super().__init__(parent)
        self._completer = QCompleter(variables, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchStartsWith)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setWidget(self)
        self._completer.activated[str].connect(self._insert_completion)
        self.textEdited.connect(self._update_completions)
        self._token_start = 0
        self._token_end = 0

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.InsertParagraphSeparator):
            popup = self._completer.popup()
            if popup.isVisible() and self._completer.currentCompletion():
                self._insert_completion(self._completer.currentCompletion())
                event.accept()
                return
        if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
            self._update_completions(self.text(), show_all=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_completions(self, _text: str, *, show_all: bool = False) -> None:
        cursor = self.cursorPosition()
        prefix_text = self.text()[:cursor]
        match = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", prefix_text)
        if match is None and not show_all:
            self._completer.popup().hide()
            return
        self._token_start = match.start() if match is not None else cursor
        self._token_end = cursor
        prefix = match.group(0) if match is not None else ""
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        popup = self._completer.popup()
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width())
        self._completer.complete(rect)

    def _insert_completion(self, completion: str) -> None:
        text = self.text()
        self.setText(text[:self._token_start] + completion + text[self._token_end:])
        self.setCursorPosition(self._token_start + len(completion))
        self._completer.popup().hide()


class _VariableCompleterDelegate(QStyledItemDelegate):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def createEditor(self, parent, option, index):
        if self._owner._project_mode or index.column() != 1:
            return super().createEditor(parent, option, index)
        return _VariableCompleterLineEdit(self._owner._project_variable_names, parent)


class BodyPropertiesWidget(QWidget):
    """Bottom-left panel: body name, material, opacity and geometry parameters."""

    params_changed = Signal(object, dict)          # (EMObject, new_params)  – single
    bulk_material_changed = Signal(str, list)      # (material, [EMObject])  – multi
    bulk_style_changed = Signal(str, str, list)    # (material, color_hex, [EMObject])
    model_role_changed = Signal(object, bool)      # (EMObject, is_model)
    material_added = Signal(str)
    material_picker_requested = Signal(str, list)
    project_parameters_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._obj: Optional[EMObject] = None
        self._selection: List[EMObject] = []
        self._blocked = False
        self._project_mode = False
        self._formula_resolver = None
        self._project_variable_names: List[str] = []
        self._materials: List[str] = ["PEC"]
        self._color_hex: str = "#bebee6"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        # ── title
        self._title = QLabel("Properties")
        self._title.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(self._title)

        # ── Name row (single selection only)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("object name")
        self._name_edit.editingFinished.connect(self._name_changed)
        name_row.addWidget(self._name_edit)
        self._name_widget = QWidget()
        self._name_widget.setLayout(name_row)
        layout.addWidget(self._name_widget)

        # ── separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # ── Material row
        mat_row = QHBoxLayout()
        mat_row.addWidget(QLabel("Material:"))
        self._mat_combo = QComboBox()
        self._mat_combo.addItems(self._materials)
        self._mat_combo.currentTextChanged.connect(self._material_changed)
        mat_row.addWidget(self._mat_combo)
        self._pick_mat_btn = QPushButton("...")
        self._pick_mat_btn.setToolTip("Open material selector")
        self._pick_mat_btn.setFixedWidth(30)
        self._pick_mat_btn.clicked.connect(self._request_material_picker)
        mat_row.addWidget(self._pick_mat_btn)
        self._add_mat_btn = QPushButton("+")
        self._add_mat_btn.setToolTip("Add a new material to this project")
        self._add_mat_btn.setFixedWidth(28)
        self._add_mat_btn.clicked.connect(self._on_add_material)
        mat_row.addWidget(self._add_mat_btn)
        layout.addLayout(mat_row)

        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Simulation role:"))
        self._model_role_combo = QComboBox()
        self._model_role_combo.addItems(["MODEL", "NON MODEL"])
        self._model_role_combo.currentTextChanged.connect(self._model_role_changed)
        role_row.addWidget(self._model_role_combo)
        role_row.addStretch(1)
        layout.addLayout(role_row)

        # ── Opacity row
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Opacity:"))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(5, 100)
        self._opacity_slider.setValue(85)
        self._opacity_slider.valueChanged.connect(self._opacity_changed)
        op_row.addWidget(self._opacity_slider)
        self._opacity_label = QLabel("0.85")
        self._opacity_label.setFixedWidth(34)
        op_row.addWidget(self._opacity_label)
        layout.addLayout(op_row)

        # ── Color row (single + multi)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(22, 14)
        self._color_preview.setStyleSheet("border:1px solid #666; background:#bebee6;")
        color_row.addWidget(self._color_preview)
        self._color_value = QLabel(self._color_hex)
        self._color_value.setMinimumWidth(70)
        color_row.addWidget(self._color_value)
        self._pick_color_btn = QPushButton("Pick")
        self._pick_color_btn.setFixedWidth(46)
        self._pick_color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self._pick_color_btn)
        color_row.addStretch(1)
        layout.addLayout(color_row)

        self._apply_color_bulk_chk = QCheckBox("Apply color in bulk")
        self._apply_color_bulk_chk.setChecked(True)
        self._apply_color_bulk_chk.setToolTip(
            "When enabled, Apply updates color for all selected objects.\n"
            "When disabled, existing colors are preserved."
        )
        layout.addWidget(self._apply_color_bulk_chk)

        # ── Apply-to-all button (visible in multi-select mode)
        self._apply_btn = QPushButton("Apply material to all selected")
        self._apply_btn.clicked.connect(self._apply_bulk)
        layout.addWidget(self._apply_btn)

        # ── Geometry parameters table (single selection only)
        self._table = QTableWidget(0, 3)
        self._table.setItemDelegateForColumn(1, _VariableCompleterDelegate(self, self._table))
        self._table.setHorizontalHeaderLabels(["Parameter", "Value", "Resolved"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._table_item_changed)
        self._table.cellDoubleClicked.connect(self._table_cell_double_clicked)
        layout.addWidget(self._table)

        self._set_no_selection()

    # ─────────────────────────────────────────────────── public API
    def set_object(self, obj: Optional[EMObject]) -> None:
        """Single-object mode."""
        self._project_mode = False
        self._selection = [obj] if obj else []
        self._obj = obj
        self._blocked = True
        try:
            if obj is None:
                self._set_no_selection()
            else:
                self._refresh_single(obj)
        finally:
            self._blocked = False

    def set_selection(self, objects: List[EMObject]) -> None:
        """Multi-selection mode."""
        self._project_mode = False
        self._selection = [o for o in objects if o is not None]
        if len(self._selection) == 0:
            self._obj = None
            self._set_no_selection()
        elif len(self._selection) == 1:
            self._obj = self._selection[0]
            self._blocked = True
            try:
                self._refresh_single(self._obj)
            finally:
                self._blocked = False
        else:
            self._obj = self._selection[-1]
            self._blocked = True
            try:
                self._refresh_multi(self._selection)
            finally:
                self._blocked = False

    def set_project_parameters(self, parameters: List[Dict[str, Any]] | None) -> None:
        """Show project-scoped parameters in an editable table."""
        self._project_mode = True
        self._set_content_visible(False)
        self._title.setVisible(True)
        self._table.setVisible(True)
        self._selection = []
        self._obj = None
        self._blocked = True
        try:
            self._title.setText("Project Variables")
            self._name_widget.setVisible(False)
            self._mat_combo.setEnabled(False)
            self._model_role_combo.setEnabled(False)
            self._opacity_slider.setEnabled(False)
            self._pick_color_btn.setEnabled(False)
            self._apply_color_bulk_chk.setVisible(False)
            self._apply_btn.setVisible(False)
            self._table.setColumnCount(3)
            self._table.setHorizontalHeaderLabels(["Name", "Value", "Unit"])
            self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
            self._table.setColumnWidth(2, 58)
            rows = []
            for entry in parameters if isinstance(parameters, list) else []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if name:
                    rows.append((name, str(entry.get("value", 0)), str(entry.get("unit", ""))))
            self._table.setRowCount(len(rows) or 1)
            if not rows:
                rows = [("", "", "")]
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    self._table.setItem(row, column, QTableWidgetItem(value))
            self._table.setVisible(True)
        finally:
            self._blocked = False

    def set_formula_resolver(self, resolver) -> None:
        self._formula_resolver = resolver

    def set_project_variable_names(self, names: List[str]) -> None:
        self._project_variable_names = list(dict.fromkeys(
            str(name).strip() for name in names if str(name).strip()
        ))

    def _project_parameters_from_table(self) -> List[Dict[str, Any]]:
        entries = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None or not name_item.text().strip():
                continue
            value_text = (self._table.item(row, 1).text() if self._table.item(row, 1) else "0").strip()
            try:
                value = float(value_text.replace(",", "."))
            except ValueError:
                value = 0.0
            unit = self._table.item(row, 2).text().strip() if self._table.item(row, 2) else ""
            entries.append({"name": name_item.text().strip(), "value": value, "unit": unit})
        return entries

    def set_materials(self, materials: List[str]) -> None:
        """Update material combo values while preserving current text when possible."""
        unique = []
        seen = set()
        for m in materials:
            ms = str(m).strip()
            if ms and ms not in seen:
                seen.add(ms)
                unique.append(ms)
        if not unique:
            unique = ["PEC"]

        current = self._mat_combo.currentText()
        self._materials = unique
        self._blocked = True
        try:
            self._mat_combo.clear()
            self._mat_combo.addItems(self._materials)
            idx = self._mat_combo.findText(current)
            if idx >= 0:
                self._mat_combo.setCurrentIndex(idx)
        finally:
            self._blocked = False

    def set_selected_material(self, material_name: str) -> None:
        idx = self._mat_combo.findText(material_name)
        if idx < 0:
            self.set_materials(self._materials + [material_name])
            idx = self._mat_combo.findText(material_name)
        if idx >= 0:
            self._mat_combo.setCurrentIndex(idx)

    # ─────────────────────────────────────────────────── display modes
    def _set_content_visible(self, visible: bool) -> None:
        for child in self.children():
            if isinstance(child, QWidget):
                child.setVisible(visible)

    def _set_no_selection(self) -> None:
        self._project_mode = False
        self._set_content_visible(False)
        self._title.setText("Properties")
        self._name_widget.setVisible(False)
        self._name_edit.setText("")
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value", "Resolved"])
        self._table.setRowCount(0)
        self._table.setVisible(False)
        self._mat_combo.setEnabled(False)
        self._model_role_combo.setEnabled(False)
        self._opacity_slider.setEnabled(False)
        self._pick_color_btn.setEnabled(False)
        self._apply_color_bulk_chk.setEnabled(False)
        self._apply_color_bulk_chk.setVisible(False)
        self._apply_btn.setVisible(False)

    def _refresh_single(self, obj: EMObject) -> None:
        self._set_content_visible(True)
        self._title.setText("Properties")
        self._name_widget.setVisible(True)
        self._name_edit.setText(obj.name)
        self._mat_combo.setEnabled(True)
        self._model_role_combo.setEnabled(True)
        self._opacity_slider.setEnabled(True)
        self._pick_color_btn.setEnabled(True)
        self._apply_color_bulk_chk.setEnabled(False)
        self._apply_color_bulk_chk.setVisible(False)
        self._apply_btn.setVisible(False)

        if self._mat_combo.findText(obj.material) < 0:
            self.set_materials(self._materials + [obj.material])
        idx = self._mat_combo.findText(obj.material)
        self._mat_combo.setCurrentIndex(max(idx, 0))
        self._opacity_slider.setValue(int(obj.opacity * 100))
        self._opacity_label.setText(f"{obj.opacity:.2f}")
        self._model_role_combo.setCurrentText("MODEL" if bool(getattr(obj, "is_model", True)) else "NON MODEL")
        if hasattr(obj, "_base_color"):
            r, g, b = obj._base_color()
            self._set_color_hex(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")

        params = obj.get_parameters()
        exclude = {"Material", "Opacity"}
        position_rows = []
        if isinstance(obj, ExtrudedObject):
            exclude.update({
                "PlaneOriginX", "PlaneOriginY", "PlaneOriginZ",
                "PlaneNormalX", "PlaneNormalY", "PlaneNormalZ", "ProfilePts",
            })
            position_rows = [
                (f"Position{axis}", value)
                for axis, value in zip("XYZ", obj.sketch_reference_point())
            ]
        elif isinstance(obj, MeshObject) and bool(getattr(obj, "circular_plate", False)):
            exclude.update({
                "CircularPlate",
                "CircularPlateNormalX", "CircularPlateNormalY", "CircularPlateNormalZ",
            })
        elif isinstance(obj, RevolvedObject):
            exclude.update({
                "AxisPt1X", "AxisPt1Y", "AxisPt1Z",
                "AxisPt2X", "AxisPt2Y", "AxisPt2Z",
                "PlaneOriginX", "PlaneOriginY", "PlaneOriginZ",
                "PlaneNormalX", "PlaneNormalY", "PlaneNormalZ", "ProfilePts",
            })
            position_rows = [
                (f"Position{axis}", value)
                for axis, value in zip("XYZ", obj.sketch_reference_point())
            ]
        rows = position_rows + [(k, v) for k, v in params.items() if k not in exclude]
        # Always expose Color in Properties so user can assign it even when
        # the object currently uses only material-based coloring.
        if not any(k == "Color" for k, _ in rows):
            if hasattr(obj, "_base_color"):
                r, g, b = obj._base_color()
                rows.append(("Color", f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"))
        self._table.setRowCount(len(rows))
        self._table.setVisible(bool(rows))
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value", "Resolved"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for row, (k, v) in enumerate(rows):
            key_item = QTableWidgetItem(k)
            key_item.setFlags(Qt.ItemIsEnabled)
            formulas = getattr(obj, "param_formulas", {}) or {}
            formula = formulas.get(k)
            val_item = QTableWidgetItem(str(formula if formula is not None else v))
            if formula is not None:
                val_item.setForeground(QColor("#3a7bd5"))
                val_item.setToolTip(f"Formula: {formula}\nResolved value: {v}")
            resolved_item = QTableWidgetItem(str(v) if formula is not None else "")
            resolved_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if k == "Color":
                # For Color parameter, add a color preview button
                val_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if isinstance(v, str) and v.startswith("#"):
                    qcolor = QColor(v)
                    val_item.setBackground(qcolor)
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, val_item)
            self._table.setItem(row, 2, resolved_item)

    def _refresh_multi(self, objects: List[EMObject]) -> None:
        self._set_content_visible(True)
        self._title.setText("Properties")
        self._name_widget.setVisible(False)
        self._table.setRowCount(0)
        self._table.setVisible(False)
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value", "Resolved"])
        self._mat_combo.setEnabled(True)
        self._model_role_combo.setEnabled(False)
        self._opacity_slider.setEnabled(True)
        self._pick_color_btn.setEnabled(True)
        self._apply_color_bulk_chk.setEnabled(True)
        self._apply_color_bulk_chk.setVisible(True)
        self._apply_btn.setVisible(True)
        # Show material of the last selected
        if self._mat_combo.findText(objects[-1].material) < 0:
            self.set_materials(self._materials + [objects[-1].material])
        idx = self._mat_combo.findText(objects[-1].material)
        self._mat_combo.setCurrentIndex(max(idx, 0))
        self._opacity_slider.setValue(int(objects[-1].opacity * 100))
        if hasattr(objects[-1], "_base_color"):
            r, g, b = objects[-1]._base_color()
            self._set_color_hex(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")

    def _set_color_hex(self, color_hex: str) -> None:
        self._color_hex = color_hex
        self._color_value.setText(color_hex)
        self._color_preview.setStyleSheet(f"border:1px solid #666; background:{color_hex};")

    # ─────────────────────────────────────────────────── editing
    def _name_changed(self) -> None:
        if self._blocked or self._obj is None:
            return
        new_name = self._name_edit.text().strip()
        if new_name and new_name != self._obj.name:
            self._obj.name = new_name
            self.params_changed.emit(self._obj, self._obj.get_parameters())

    def _collect_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        formulas: Dict[str, str] = {}
        metadata_keys = {
            "StepSourcePath",
            "StepSolidName",
            "StepGeometryModified",
            "StepExportOffset",
            "BooleanOperation",
            "BooleanSourceNames",
            "BooleanSourcesData",
        }
        original_params = self._obj.get_parameters() if self._obj is not None else {}
        for row in range(self._table.rowCount()):
            k = self._table.item(row, 0)
            v = self._table.item(row, 1)
            if k and v:
                if k.text() == "Color":
                    params[k.text()] = v.text()
                    continue
                if k.text() in metadata_keys:
                    params[k.text()] = original_params.get(k.text(), v.text())
                    continue
                original_value = original_params.get(k.text())
                if isinstance(original_value, bool):
                    normalized = v.text().strip().lower()
                    if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                        raise ValueError(f"{k.text()} must be True or False")
                    params[k.text()] = normalized in {"true", "1", "yes", "on"}
                    continue
                try:
                    params[k.text()] = float(v.text())
                except ValueError:
                    if isinstance(original_value, str) or self._formula_resolver is None:
                        params[k.text()] = v.text()
                    else:
                        params[k.text()] = self._formula_resolver(v.text())
                        formulas[k.text()] = v.text()
        params["Material"] = self._mat_combo.currentText()
        params["Opacity"]  = self._opacity_slider.value() / 100.0
        params["Color"] = self._color_hex
        return params, formulas

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._blocked:
            return
        if self._project_mode:
            self.project_parameters_changed.emit(self._project_parameters_from_table())
            return
        if self._blocked or self._obj is None or item.column() != 1:
            return
        # Skip color cells – they are handled by double-click
        if self._table.item(item.row(), 0).text() == "Color":
            return
        self._apply_single()

    def _table_cell_double_clicked(self, row: int, col: int) -> None:
        """Open color picker for Color parameter cells."""
        if col != 1:
            return
        param_item = self._table.item(row, 0)
        val_item = self._table.item(row, 1)
        if param_item is None or param_item.text() != "Color":
            return
        
        current_color = QColor()
        if val_item is not None and val_item.text().startswith("#"):
            current_color.setNamedColor(val_item.text())
        else:
            current_color.setRgb(190, 190, 230)  # default PEC-like color
        
        color = QColorDialog.getColor(current_color, self, "Choose Color")
        if color.isValid():
            hex_color = color.name()
            val_item.setText(hex_color)
            val_item.setBackground(color)
            self._set_color_hex(hex_color)
            self._blocked = True
            try:
                self._apply_single()
            finally:
                self._blocked = False

    def _pick_color(self) -> None:
        if not self._selection:
            return
        current = QColor()
        current.setNamedColor(self._color_hex)
        color = QColorDialog.getColor(current, self, "Choose Color")
        if not color.isValid():
            return
        self._set_color_hex(color.name())
        if len(self._selection) == 1 and self._obj is not None:
            self._apply_single()

    def _material_changed(self, _text: str) -> None:
        if self._blocked:
            return
        if len(self._selection) == 1 and self._obj:
            self._apply_single()
        # For multi: user must click "Apply to all"

    def _model_role_changed(self, text: str) -> None:
        if self._blocked or self._obj is None or len(self._selection) != 1:
            return
        is_model = str(text).strip().upper() != "NON MODEL"
        if bool(getattr(self._obj, "is_model", True)) == is_model:
            return
        self._obj.is_model = is_model
        self.model_role_changed.emit(self._obj, is_model)

    def _request_material_picker(self) -> None:
        current = self._mat_combo.currentText()
        self.material_picker_requested.emit(current, list(self._selection))

    def _opacity_changed(self, value: int) -> None:
        self._opacity_label.setText(f"{value / 100:.2f}")
        if self._blocked:
            return
        if len(self._selection) == 1 and self._obj:
            self._apply_single()
        elif len(self._selection) > 1:
            # Apply opacity to all immediately
            for o in self._selection:
                o.opacity = value / 100.0
                o.refresh_appearance()
            self.params_changed.emit(self._selection[-1], {})

    def _apply_single(self) -> None:
        if self._obj is None:
            return
        try:
            params, formulas = self._collect_params()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Formula", str(exc))
            return
        self._obj.set_parameters(params)
        self._obj.param_formulas = formulas
        self.params_changed.emit(self._obj, params)

    def _apply_bulk(self) -> None:
        material = self._mat_combo.currentText()
        opacity  = self._opacity_slider.value() / 100.0
        apply_color = self._apply_color_bulk_chk.isChecked()

        rr = gg = bb = None
        color_hex = self._color_hex
        if apply_color:
            rr = int(color_hex[1:3], 16) / 255.0
            gg = int(color_hex[3:5], 16) / 255.0
            bb = int(color_hex[5:7], 16) / 255.0

        for o in self._selection:
            o.material = material
            o.opacity  = opacity
            if apply_color:
                o.custom_color = (rr, gg, bb)
            o.refresh_appearance()

        if apply_color:
            self.bulk_style_changed.emit(material, color_hex, list(self._selection))
        else:
            self.bulk_material_changed.emit(material, list(self._selection))

    def _on_add_material(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Add Material",
            "Material name:",
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        if self._mat_combo.findText(name) < 0:
            self.set_materials(self._materials + [name])
        idx = self._mat_combo.findText(name)
        if idx >= 0:
            self._mat_combo.setCurrentIndex(idx)
        self.material_added.emit(name)
