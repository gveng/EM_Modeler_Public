"""Assign Material dialog with category list, material list and details pane."""
from __future__ import annotations

from typing import Dict, List, Optional
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QLineEdit, QMessageBox, QFrame,
    QInputDialog, QAbstractItemView,
)

from ..emerge.material_store import MaterialRecord


CATEGORIES = ["Common", "Metals", "Dielectrics", "Foams", "Semiconductors", "Liquids"]


class MaterialAssignDialog(QDialog):
    def __init__(
        self,
        parent=None,
        title: str = "Assign Material",
        project_records: Optional[Dict[str, MaterialRecord]] = None,
        global_records: Optional[Dict[str, MaterialRecord]] = None,
        selected_name: str = "",
        can_append_global: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)

        self._project_records = project_records or {}
        self._global_records = global_records or {}
        self._active_source = "project"
        self._selected_name = selected_name
        self._selected_result: Optional[MaterialRecord] = None
        self._pending_append: List[str] = []

        root = QVBoxLayout(self)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        # Left: category + source buttons
        left = QVBoxLayout()
        body.addLayout(left, 0)

        self._cat_list = QListWidget()
        self._cat_list.addItems(CATEGORIES)
        self._cat_list.setCurrentRow(0)
        self._cat_list.currentTextChanged.connect(self._refresh_material_list)
        left.addWidget(self._cat_list, 1)

        self._btn_project = QPushButton("Project DB")
        self._btn_project.clicked.connect(self._use_project_db)
        left.addWidget(self._btn_project)

        self._btn_global = QPushButton("Global DB")
        self._btn_global.clicked.connect(self._use_global_db)
        left.addWidget(self._btn_global)

        self._btn_append = QPushButton("Append to Project")
        self._btn_append.setEnabled(can_append_global)
        self._btn_append.clicked.connect(self._append_selected_to_project)
        left.addWidget(self._btn_append)

        # Middle: search + material list
        mid = QVBoxLayout()
        body.addLayout(mid, 1)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search material")
        self._search.textChanged.connect(self._refresh_material_list)
        mid.addWidget(self._search)

        self._mat_list = QListWidget()
        self._mat_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._mat_list.currentItemChanged.connect(self._on_material_selected)
        mid.addWidget(self._mat_list, 1)

        # Right: details
        right = QVBoxLayout()
        body.addLayout(right, 1)

        details_box = QFrame()
        details_box.setFrameShape(QFrame.StyledPanel)
        details_box.setObjectName("materialDetailsBox")
        details_layout = QVBoxLayout(details_box)
        details_layout.setContentsMargins(10, 10, 10, 10)
        details_layout.setSpacing(6)

        self._name_title = QLabel("No material selected")
        self._name_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        details_layout.addWidget(self._name_title)

        self._family_label = QLabel("Family: -")
        self._family_label.setStyleSheet("font-style: italic;")
        details_layout.addWidget(self._family_label)

        details_layout.addSpacing(4)

        self._er_label = QLabel("er: -")
        self._tan_d_label = QLabel("tan d: -")
        self._sigma_label = QLabel("sigma [S/m]: -")
        details_layout.addWidget(self._er_label)
        details_layout.addWidget(self._tan_d_label)
        details_layout.addWidget(self._sigma_label)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_caption = QLabel("Color:")
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(14, 14)
        self._color_preview.setStyleSheet("border:1px solid #666;")
        self._color_value = QLabel("-")
        color_row.addWidget(color_caption)
        color_row.addWidget(self._color_preview)
        color_row.addWidget(self._color_value)
        color_row.addStretch(1)
        details_layout.addLayout(color_row)

        self._source_label = QLabel("source: -")
        details_layout.addWidget(self._source_label)
        details_layout.addStretch(1)

        right.addWidget(details_box, 1)

        self._new_btn = QPushButton("New Custom...")
        self._new_btn.clicked.connect(self._on_new_custom)
        right.addWidget(self._new_btn)

        # Bottom actions
        footer = QHBoxLayout()
        root.addLayout(footer)
        footer.addStretch(1)

        self._ok_btn = QPushButton("OK")
        self._ok_btn.clicked.connect(self._accept_selection)
        footer.addWidget(self._ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        self._use_project_db()

    @staticmethod
    def _fmt_number(value: float) -> str:
        abs_v = abs(value)
        if abs_v == 0:
            return "0"
        if abs_v >= 1e5 or abs_v < 1e-3:
            return f"{value:.4e}"
        return f"{value:.6g}"

    @property
    def selected_record(self) -> Optional[MaterialRecord]:
        return self._selected_result

    @property
    def appended_names(self) -> List[str]:
        return list(self._pending_append)

    def _records(self) -> Dict[str, MaterialRecord]:
        if self._active_source == "global":
            return self._global_records
        return self._project_records

    def _use_project_db(self) -> None:
        self._active_source = "project"
        self._new_btn.setEnabled(True)
        self._refresh_material_list()

    def _use_global_db(self) -> None:
        self._active_source = "global"
        self._new_btn.setEnabled(False)
        self._refresh_material_list()

    def _refresh_material_list(self) -> None:
        self._mat_list.blockSignals(True)
        try:
            self._mat_list.clear()
            category = self._cat_list.currentItem().text() if self._cat_list.currentItem() else "Common"
            query = self._search.text().strip().lower()
            for rec in sorted(self._records().values(), key=lambda r: r.name.lower()):
                if rec.family != category:
                    continue
                if query and query not in rec.name.lower():
                    continue
                item = QListWidgetItem(rec.name)
                self._mat_list.addItem(item)

            if self._selected_name:
                for i in range(self._mat_list.count()):
                    it = self._mat_list.item(i)
                    if it.text() == self._selected_name:
                        self._mat_list.setCurrentItem(it)
                        break
            if self._mat_list.currentItem() is None and self._mat_list.count() > 0:
                self._mat_list.setCurrentRow(0)
        finally:
            self._mat_list.blockSignals(False)

        self._update_details()

    def _on_material_selected(self, _current, _previous) -> None:
        self._update_details()

    def _current_record(self) -> Optional[MaterialRecord]:
        item = self._mat_list.currentItem()
        if item is None:
            return None
        return self._records().get(item.text())

    def _update_details(self) -> None:
        rec = self._current_record()
        if rec is None:
            self._name_title.setText("No material selected")
            self._family_label.setText("Family: -")
            self._er_label.setText("er: -")
            self._tan_d_label.setText("tan d: -")
            self._sigma_label.setText("sigma [S/m]: -")
            self._color_value.setText("-")
            self._color_preview.setStyleSheet("border:1px solid #666;")
            self._source_label.setText("source: -")
            return
        self._selected_name = rec.name
        self._name_title.setText(rec.name)
        self._family_label.setText(f"Family: {rec.family}")
        self._er_label.setText(f"er: {self._fmt_number(rec.er)}")
        self._tan_d_label.setText(f"tan d: {self._fmt_number(rec.tan_d)}")
        self._sigma_label.setText(f"sigma [S/m]: {self._fmt_number(rec.sigma)}")
        self._color_value.setText(rec.color)
        self._color_preview.setStyleSheet(
            f"border:1px solid #666; background:{rec.color};"
        )
        self._source_label.setText(f"source: {rec.source}")

    def _accept_selection(self) -> None:
        rec = self._current_record()
        if rec is None:
            QMessageBox.information(self, "Assign Material", "Select a material first.")
            return
        self._selected_result = rec
        self.accept()

    def _on_new_custom(self) -> None:
        if self._active_source != "project":
            return

        name, ok = QInputDialog.getText(self, "New Material", "Name:")
        name = (name or "").strip()
        if not ok or not name:
            return

        family, ok = QInputDialog.getItem(self, "New Material", "Family:", CATEGORIES, editable=False)
        if not ok:
            return

        er = self._ask_float("Relative Permittivity", "er:", 1.0)
        if er is None:
            return
        tan_d = self._ask_float("Loss Tangent", "tan d:", 0.0)
        if tan_d is None:
            return
        sigma = self._ask_float("Conductivity", "sigma [S/m]:", 0.0)
        if sigma is None:
            return

        rec = MaterialRecord(
            uid=str(uuid.uuid4()),
            name=name,
            family=family,
            er=float(er),
            tan_d=float(tan_d),
            sigma=float(sigma),
            source="project",
        )
        self._project_records[rec.name] = rec
        self._selected_name = rec.name
        self._cat_list.setCurrentRow(CATEGORIES.index(family) if family in CATEGORIES else 0)
        self._refresh_material_list()

    def _ask_float(self, title: str, label: str, default: float) -> Optional[float]:
        text, ok = QInputDialog.getText(self, title, label, text=str(default))
        if not ok:
            return None
        try:
            return float(text)
        except ValueError:
            QMessageBox.warning(self, title, "Invalid number")
            return None

    def _append_selected_to_project(self) -> None:
        if self._active_source != "global":
            return
        selected = self._mat_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "Append", "Select one or more materials.")
            return
        self._pending_append = [it.text() for it in selected]
        QMessageBox.information(
            self,
            "Append",
            f"{len(self._pending_append)} material(s) queued for append to project DB. Press OK to confirm.",
        )
