from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import uuid

from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QVBoxLayout,
    QWidget,
)

from ..emerge.material_store import DEFAULT_COLOR, MaterialRecord, MaterialStore


_FAMILIES = ["Common", "Metals", "Dielectrics", "Foams", "Semiconductors", "Liquids"]


class _MaterialEditorDialog(QDialog):
    def __init__(self, parent=None, record: MaterialRecord | None = None):
        super().__init__(parent)
        self.setWindowTitle("Material")
        self.resize(360, 260)

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
            tan_d=float(self._tan_d.value()),
            sigma=float(self._sigma.value()),
            color=self._color,
            opacity=float(self._opacity.value()),
            source="project",
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
        self._tan_d = QLabel("tan d: -")
        self._sigma = QLabel("sigma [S/m]: -")
        self._opacity = QLabel("opacity: -")
        details_layout.addWidget(self._family)
        details_layout.addWidget(self._source)
        details_layout.addWidget(self._er)
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

        self._edit_btn = QPushButton("Edit Project Material...")
        self._edit_btn.clicked.connect(self._edit_material)
        actions.addWidget(self._edit_btn)

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
            self._tan_d.setText(f"tan d: {rec.tan_d:.6g}")
            self._sigma.setText(f"sigma [S/m]: {rec.sigma:.6g}")
            self._opacity.setText(f"opacity: {rec.opacity:.2f}")
            self._color_preview.setStyleSheet(f"background:{rec.color};")
            self._color_value.setText(rec.color)

        project_custom = rec is not None and rec.source == "project"
        global_selected = rec is not None and self._source_combo.currentIndex() == 1
        self._edit_btn.setEnabled(project_custom)
        self._delete_btn.setEnabled(project_custom)
        self._append_btn.setEnabled(global_selected)

    def _mark_changed(self) -> None:
        self._changed = True
        self._on_store_changed()
        self._refresh_list()

    def _new_material(self) -> None:
        dlg = _MaterialEditorDialog(self)
        if dlg.exec_() != dlg.Accepted or dlg.result_record is None:
            return
        self._store.upsert_project_record(dlg.result_record)
        self._mark_changed()

    def _edit_material(self) -> None:
        rec = self._current_record()
        if rec is None or rec.source != "project":
            return
        dlg = _MaterialEditorDialog(self, rec)
        if dlg.exec_() != dlg.Accepted or dlg.result_record is None:
            return
        self._store.upsert_project_record(dlg.result_record)
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
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        self._store.set_global_db_path(path, create_if_missing=True)
        self._store.save_global_db()
        self._mark_changed()

    def _reload_global_db(self) -> None:
        self._store.set_global_db_path(self._store.global_db_path, create_if_missing=False)
        self._mark_changed()
