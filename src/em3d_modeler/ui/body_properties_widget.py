"""Body Properties panel: shows / edits parameters of one or more selected EM objects.

Single selection  → full geometry + name + material + opacity editor
Multi-selection   → shows object count, common material combo, opacity slider,
                    and an "Apply to all" button
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QSlider, QPushButton, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal

from ..scene.em_objects import EMObject, MATERIAL_COLORS

_ALL_MATERIALS = list(MATERIAL_COLORS.keys()) + ["Custom"]


class BodyPropertiesWidget(QWidget):
    """Bottom-left panel: body name, material, opacity and geometry parameters."""

    params_changed = pyqtSignal(object, dict)          # (EMObject, new_params)  – single
    bulk_material_changed = pyqtSignal(str, list)      # (material, [EMObject])  – multi

    def __init__(self, parent=None):
        super().__init__(parent)
        self._obj: Optional[EMObject] = None
        self._selection: List[EMObject] = []
        self._blocked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        # ── title
        self._title = QLabel("Body Properties")
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
        self._mat_combo.addItems(_ALL_MATERIALS)
        self._mat_combo.currentTextChanged.connect(self._material_changed)
        mat_row.addWidget(self._mat_combo)
        layout.addLayout(mat_row)

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

        # ── Apply-to-all button (visible in multi-select mode)
        self._apply_btn = QPushButton("Apply material to all selected")
        self._apply_btn.clicked.connect(self._apply_bulk)
        layout.addWidget(self._apply_btn)

        # ── Geometry parameters table (single selection only)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._table_item_changed)
        layout.addWidget(self._table)

        self._set_no_selection()

    # ─────────────────────────────────────────────────── public API
    def set_object(self, obj: Optional[EMObject]) -> None:
        """Single-object mode."""
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

    # ─────────────────────────────────────────────────── display modes
    def _set_no_selection(self) -> None:
        self._title.setText("Body Properties")
        self._name_widget.setVisible(False)
        self._name_edit.setText("")
        self._table.setRowCount(0)
        self._table.setVisible(False)
        self._mat_combo.setEnabled(False)
        self._opacity_slider.setEnabled(False)
        self._apply_btn.setVisible(False)

    def _refresh_single(self, obj: EMObject) -> None:
        self._title.setText(f"{type(obj).__name__}")
        self._name_widget.setVisible(True)
        self._name_edit.setText(obj.name)
        self._mat_combo.setEnabled(True)
        self._opacity_slider.setEnabled(True)
        self._apply_btn.setVisible(False)

        idx = self._mat_combo.findText(obj.material)
        self._mat_combo.setCurrentIndex(max(idx, 0))
        self._opacity_slider.setValue(int(obj.opacity * 100))
        self._opacity_label.setText(f"{obj.opacity:.2f}")

        params = obj.get_parameters()
        exclude = {"Material", "Opacity"}
        rows = [(k, v) for k, v in params.items() if k not in exclude]
        self._table.setRowCount(len(rows))
        self._table.setVisible(bool(rows))
        for row, (k, v) in enumerate(rows):
            key_item = QTableWidgetItem(k)
            key_item.setFlags(Qt.ItemIsEnabled)
            val_item = QTableWidgetItem(str(v))
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, val_item)

    def _refresh_multi(self, objects: List[EMObject]) -> None:
        self._title.setText(f"{len(objects)} objects selected")
        self._name_widget.setVisible(False)
        self._table.setRowCount(0)
        self._table.setVisible(False)
        self._mat_combo.setEnabled(True)
        self._opacity_slider.setEnabled(True)
        self._apply_btn.setVisible(True)
        # Show material of the last selected
        idx = self._mat_combo.findText(objects[-1].material)
        self._mat_combo.setCurrentIndex(max(idx, 0))
        self._opacity_slider.setValue(int(objects[-1].opacity * 100))

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
        for row in range(self._table.rowCount()):
            k = self._table.item(row, 0)
            v = self._table.item(row, 1)
            if k and v:
                try:
                    params[k.text()] = float(v.text())
                except ValueError:
                    params[k.text()] = v.text()
        params["Material"] = self._mat_combo.currentText()
        params["Opacity"]  = self._opacity_slider.value() / 100.0
        return params

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._blocked or self._obj is None or item.column() != 1:
            return
        self._apply_single()

    def _material_changed(self, _text: str) -> None:
        if self._blocked:
            return
        if len(self._selection) == 1 and self._obj:
            self._apply_single()
        # For multi: user must click "Apply to all"

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
        params = self._collect_params()
        self._obj.set_parameters(params)
        self.params_changed.emit(self._obj, params)

    def _apply_bulk(self) -> None:
        material = self._mat_combo.currentText()
        opacity  = self._opacity_slider.value() / 100.0
        for o in self._selection:
            o.material = material
            o.opacity  = opacity
            o.refresh_appearance()
        self.bulk_material_changed.emit(material, list(self._selection))
