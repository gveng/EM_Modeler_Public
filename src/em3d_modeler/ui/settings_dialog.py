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

from typing import Dict, Tuple

from PySide6.QtCore import Qt, QLocale
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    
    QColorDialog,
    QSpinBox,
    QCheckBox,
)
from .formula_widgets import FormulaDoubleSpinBox as QDoubleSpinBox


_SELECTION_COLOR_PRESETS: Dict[str, Tuple[float, float, float]] = {
    "Purple": (0.62, 0.34, 0.85),
    "Orange": (1.0, 0.78, 0.0),
    "Blue": (0.25, 0.55, 0.95),
    "Green": (0.20, 0.75, 0.45),
    "Red": (0.92, 0.25, 0.25),
}

_MM_PER_UNIT = {
    "mm": 1.0,
    "um": 0.001,
    "cm": 10.0,
    "m": 1000.0,
    "mil": 0.0254,
    "inch": 25.4,
}


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 320)

        root = QVBoxLayout(self)
        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs)

        self._display_tab = QWidget(self)
        self._tabs.addTab(self._display_tab, "Display")

        form = QFormLayout(self._display_tab)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self._units_combo = QComboBox(self._display_tab)
        self._units_combo.addItems(["mm", "um", "cm", "m", "mil", "inch"])
        self._units_combo.currentTextChanged.connect(self._on_units_changed)
        form.addRow("Units", self._units_combo)

        self._decimal_combo = QComboBox(self._display_tab)
        self._decimal_combo.addItems(["Point (.)", "Comma (,)"])
        self._decimal_combo.currentTextChanged.connect(self._on_decimal_changed)
        form.addRow("Decimal separation", self._decimal_combo)

        self._workspace_spin = QDoubleSpinBox(self._display_tab)
        self._workspace_spin.setDecimals(3)
        self._workspace_spin.setRange(0.001, 1000000.0)
        form.addRow("Workspace size", self._workspace_spin)

        self._grid_spin = QDoubleSpinBox(self._display_tab)
        self._grid_spin.setDecimals(3)
        self._grid_spin.setRange(0.000001, 1000000.0)
        form.addRow("Grid size", self._grid_spin)

        self._adaptive_grid_margin_spin = QDoubleSpinBox(self._display_tab)
        self._adaptive_grid_margin_spin.setDecimals(3)
        self._adaptive_grid_margin_spin.setRange(0.0, 1000000.0)
        self._adaptive_grid_margin_spin.setSingleStep(10.0)
        form.addRow("Adaptive grid margin", self._adaptive_grid_margin_spin)

        self._triad_size_spin = QDoubleSpinBox(self._display_tab)
        self._triad_size_spin.setDecimals(3)
        self._triad_size_spin.setRange(0.000001, 1000000.0)
        form.addRow("Plane triad size", self._triad_size_spin)

        color_row = QHBoxLayout()
        self._selection_color_combo = QComboBox(self._display_tab)
        self._selection_color_combo.addItems(list(_SELECTION_COLOR_PRESETS.keys()) + ["Custom"])
        self._selection_color_combo.currentTextChanged.connect(self._on_color_choice_changed)
        color_row.addWidget(self._selection_color_combo)

        self._color_preview = QFrame(self._display_tab)
        self._color_preview.setFixedSize(28, 18)
        self._color_preview.setFrameShape(QFrame.Box)
        color_row.addWidget(self._color_preview)

        self._custom_color_btn = QPushButton("Choose…", self._display_tab)
        self._custom_color_btn.clicked.connect(self._choose_custom_color)
        color_row.addWidget(self._custom_color_btn)
        color_row.addStretch(1)
        form.addRow("Selection color", color_row)

        self._custom_color = _SELECTION_COLOR_PRESETS["Purple"]
        self._current_units = "mm"

        # ─── Simulation Tab ───────────────────────────────────────────────
        self._simulation_tab = QWidget(self)
        self._tabs.addTab(self._simulation_tab, "Simulation")

        sim_form = QFormLayout(self._simulation_tab)
        sim_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self._solver_combo = QComboBox(self._simulation_tab)
        self._solver_combo.addItems([
            "PARDISO", "SUPERLU", "UMFPACK", "CUDSS", "AASDS", "MUMPS",
        ])
        sim_form.addRow("Solver", self._solver_combo)

        self._parallel_check = QCheckBox("Enable parallel computation", self._simulation_tab)
        self._parallel_check.setChecked(True)
        sim_form.addRow("", self._parallel_check)

        self._pardiso_threads_spin = QSpinBox(self._simulation_tab)
        self._pardiso_threads_spin.setRange(1, 256)
        self._pardiso_threads_spin.setValue(8)
        sim_form.addRow("PARDISO threads", self._pardiso_threads_spin)

        self._acc_threads_spin = QSpinBox(self._simulation_tab)
        self._acc_threads_spin.setRange(1, 256)
        self._acc_threads_spin.setValue(10)
        sim_form.addRow("ACC threads", self._acc_threads_spin)

        self._plot_sparams_check = QCheckBox("Plot S-parameters after simulation", self._simulation_tab)
        self._plot_sparams_check.setChecked(True)
        sim_form.addRow("", self._plot_sparams_check)

        self._export_sparams_check = QCheckBox("Export S-parameters Touchstone after simulation", self._simulation_tab)
        self._export_sparams_check.setChecked(True)
        sim_form.addRow("", self._export_sparams_check)

        # ─── Mesh Tab ─────────────────────────────────────────────────────
        self._mesh_tab = QWidget(self)
        self._tabs.addTab(self._mesh_tab, "Mesh")

        mesh_form = QFormLayout(self._mesh_tab)
        mesh_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        mesh_form.addRow(QLabel("Mesh Resolution (fraction of wavelength)"))
        
        self._mesh_resolution_spin = QDoubleSpinBox(self._mesh_tab)
        self._mesh_resolution_spin.setDecimals(4)
        self._mesh_resolution_spin.setRange(0.01, 1.0)
        self._mesh_resolution_spin.setValue(0.3)
        self._mesh_resolution_spin.setSingleStep(0.05)
        mesh_form.addRow("Resolution (1/λ)", self._mesh_resolution_spin)

        self._curved_boundary_resolution_spin = QSpinBox(self._mesh_tab)
        self._curved_boundary_resolution_spin.setRange(3, 1000)
        self._curved_boundary_resolution_spin.setValue(20)
        mesh_form.addRow("Curved boundary segments", self._curved_boundary_resolution_spin)

        mesh_form.addRow(QLabel("Note: 0.3 = 3.3 lines/wavelength (fine mesh)"))

        mesh_form.addRow(QLabel("Local Refinement Defaults"))
        mesh_form.addRow(QLabel("Applied only through Object > Assign Mesh Refinement..."))

        self._refinement_boundary_size_spin = QDoubleSpinBox(self._mesh_tab)
        self._refinement_boundary_size_spin.setDecimals(6)
        self._refinement_boundary_size_spin.setRange(0.000001, 1e6)
        self._refinement_boundary_size_spin.setValue(0.25)
        self._refinement_boundary_size_spin.setSuffix(" mm")
        mesh_form.addRow("Boundary size", self._refinement_boundary_size_spin)

        self._refinement_face_size_spin = QDoubleSpinBox(self._mesh_tab)
        self._refinement_face_size_spin.setDecimals(6)
        self._refinement_face_size_spin.setRange(0.000001, 1e6)
        self._refinement_face_size_spin.setValue(0.1)
        self._refinement_face_size_spin.setSuffix(" mm")
        mesh_form.addRow("Face / port size", self._refinement_face_size_spin)

        self._refinement_growth_rate_spin = QDoubleSpinBox(self._mesh_tab)
        self._refinement_growth_rate_spin.setDecimals(3)
        self._refinement_growth_rate_spin.setRange(1.001, 100.0)
        self._refinement_growth_rate_spin.setValue(3.0)
        mesh_form.addRow("Growth rate", self._refinement_growth_rate_spin)

        self._refinement_max_size_spin = QDoubleSpinBox(self._mesh_tab)
        self._refinement_max_size_spin.setDecimals(6)
        self._refinement_max_size_spin.setRange(0.0, 1e6)
        self._refinement_max_size_spin.setValue(0.0)
        self._refinement_max_size_spin.setSpecialValueText("Automatic")
        self._refinement_max_size_spin.setSuffix(" mm")
        mesh_form.addRow("Maximum size", self._refinement_max_size_spin)

        self._utility_tab = QWidget(self)
        self._tabs.addTab(self._utility_tab, "Utility")
        utility_layout = QVBoxLayout(self._utility_tab)
        workspace_row = QHBoxLayout()
        self._workspace_path_edit = QLineEdit(self._utility_tab)
        self._workspace_path_edit.setReadOnly(True)
        self._workspace_path_edit.setPlaceholderText("Not set")
        workspace_row.addWidget(self._workspace_path_edit, 1)
        self._workspace_browse_btn = QPushButton("Browse…", self._utility_tab)
        self._workspace_browse_btn.clicked.connect(self._browse_workspace)
        workspace_row.addWidget(self._workspace_browse_btn)
        utility_layout.addWidget(QLabel("Workspace folder", self._utility_tab))
        utility_layout.addLayout(workspace_row)
        self._export_full_scene_step_check = QCheckBox(
            "Export complete scene STEP",
            self._utility_tab,
        )
        utility_layout.addWidget(self._export_full_scene_step_check)
        self._boolean_decimation_check = QCheckBox(
            "Ask for mesh decimation on Fuse, Cut and Intersect",
            self._utility_tab,
        )
        utility_layout.addWidget(self._boolean_decimation_check)
        utility_layout.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _browse_workspace(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Workspace Folder",
            self._workspace_path_edit.text(),
            QFileDialog.ShowDirsOnly,
        )
        if directory:
            self._workspace_path_edit.setText(directory)

    def _choose_custom_color(self) -> None:
        qcolor = QColorDialog.getColor(self._to_qcolor(self._custom_color), self, "Selection color")
        if not qcolor.isValid():
            return
        self._custom_color = (qcolor.redF(), qcolor.greenF(), qcolor.blueF())
        self._selection_color_combo.setCurrentText("Custom")
        self._update_color_preview(self._custom_color)

    def _on_color_choice_changed(self, value: str) -> None:
        if value == "Custom":
            self._update_color_preview(self._custom_color)
            return
        color = _SELECTION_COLOR_PRESETS.get(value, self._custom_color)
        self._update_color_preview(color)

    def _on_units_changed(self, units: str) -> None:
        old_units = self._current_units
        if old_units == units:
            self._update_suffixes(units)
            return
        if old_units in _MM_PER_UNIT and units in _MM_PER_UNIT:
            factor = _MM_PER_UNIT[old_units] / _MM_PER_UNIT[units]
            self._workspace_spin.blockSignals(True)
            self._grid_spin.blockSignals(True)
            self._adaptive_grid_margin_spin.blockSignals(True)
            self._triad_size_spin.blockSignals(True)
            self._workspace_spin.setValue(self._workspace_spin.value() * factor)
            self._grid_spin.setValue(self._grid_spin.value() * factor)
            self._adaptive_grid_margin_spin.setValue(self._adaptive_grid_margin_spin.value() * factor)
            self._triad_size_spin.setValue(self._triad_size_spin.value() * factor)
            self._workspace_spin.blockSignals(False)
            self._grid_spin.blockSignals(False)
            self._adaptive_grid_margin_spin.blockSignals(False)
            self._triad_size_spin.blockSignals(False)
        self._current_units = units
        self._update_suffixes(units)

    def _on_decimal_changed(self, value: str) -> None:
        locale = QLocale(QLocale.Italian, QLocale.Italy) if value.startswith("Comma") else QLocale.c()
        self._workspace_spin.setLocale(locale)
        self._grid_spin.setLocale(locale)
        self._adaptive_grid_margin_spin.setLocale(locale)
        self._triad_size_spin.setLocale(locale)

    def _update_suffixes(self, units: str) -> None:
        self._workspace_spin.setSuffix(f" {units}")
        self._grid_spin.setSuffix(f" {units}")
        self._adaptive_grid_margin_spin.setSuffix(f" {units}")
        self._triad_size_spin.setSuffix(f" {units}")

    def _update_color_preview(self, color: Tuple[float, float, float]) -> None:
        q = self._to_qcolor(color)
        self._color_preview.setStyleSheet(
            f"background-color: {q.name()}; border: 1px solid #555;"
        )

    @staticmethod
    def _to_qcolor(color: Tuple[float, float, float]) -> QColor:
        return QColor(int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))

    def set_values(
        self,
        *,
        units: str,
        decimal_separator: str,
        workspace_size: float,
        grid_size: float,
        plane_triad_size: float,
        adaptive_grid_margin: float = 20.0,
        selection_color: Tuple[float, float, float],
        locale: QLocale,
        solver: str = "PARDISO",
        parallel_enabled: bool = True,
        pardiso_threads: int = 8,
        acc_threads: int = 10,
        mesh_resolution: float = 0.3,
        curved_boundary_resolution: int = 20,
        refinement_boundary_size_mm: float = 0.25,
        refinement_face_size_mm: float = 0.1,
        refinement_growth_rate: float = 3.0,
        refinement_max_size_mm: float = 0.0,
        plot_sparams_after_sim: bool = True,
        export_sparams_after_sim: bool = True,
        export_full_scene_step: bool = False,
        boolean_decimation_enabled: bool = False,
        workspace_path: str = "",
    ) -> None:
        self._units_combo.setCurrentText(units)
        self._current_units = units
        self._decimal_combo.setCurrentText("Comma (,)" if decimal_separator == "," else "Point (.)")
        self._workspace_spin.setLocale(locale)
        self._grid_spin.setLocale(locale)
        self._update_suffixes(units)
        self._workspace_spin.setValue(workspace_size)
        self._grid_spin.setValue(grid_size)
        self._adaptive_grid_margin_spin.setValue(adaptive_grid_margin)
        self._triad_size_spin.setValue(plane_triad_size)

        chosen_name = None
        for name, preset in _SELECTION_COLOR_PRESETS.items():
            if all(abs(a - b) < 1e-6 for a, b in zip(preset, selection_color)):
                chosen_name = name
                break
        if chosen_name is None:
            chosen_name = "Custom"
            self._custom_color = selection_color
        self._selection_color_combo.setCurrentText(chosen_name)
        self._update_color_preview(selection_color)

        # Simulation settings
        if solver in [self._solver_combo.itemText(i) for i in range(self._solver_combo.count())]:
            self._solver_combo.setCurrentText(solver)
        self._parallel_check.setChecked(bool(parallel_enabled))
        self._pardiso_threads_spin.setValue(int(pardiso_threads))
        self._acc_threads_spin.setValue(int(acc_threads))
        self._plot_sparams_check.setChecked(bool(plot_sparams_after_sim))
        self._export_sparams_check.setChecked(bool(export_sparams_after_sim))

        # Mesh settings
        self._mesh_resolution_spin.setValue(float(mesh_resolution))
        self._curved_boundary_resolution_spin.setValue(int(curved_boundary_resolution))
        self._refinement_boundary_size_spin.setValue(float(refinement_boundary_size_mm))
        self._refinement_face_size_spin.setValue(float(refinement_face_size_mm))
        self._refinement_growth_rate_spin.setValue(float(refinement_growth_rate))
        self._refinement_max_size_spin.setValue(float(refinement_max_size_mm))
        self._export_full_scene_step_check.setChecked(bool(export_full_scene_step))
        self._boolean_decimation_check.setChecked(bool(boolean_decimation_enabled))
        self._workspace_path_edit.setText(str(workspace_path or ""))

    def values(self) -> dict:
        color_name = self._selection_color_combo.currentText()
        color = _SELECTION_COLOR_PRESETS.get(color_name, self._custom_color)
        return {
            "units": self._units_combo.currentText(),
            "decimal_separator": "," if self._decimal_combo.currentText().startswith("Comma") else ".",
            "workspace_size": float(self._workspace_spin.value()),
            "grid_size": float(self._grid_spin.value()),
            "adaptive_grid_margin": float(self._adaptive_grid_margin_spin.value()),
            "plane_triad_size": float(self._triad_size_spin.value()),
            "selection_color": color,
            "solver": self._solver_combo.currentText(),
            "parallel_enabled": bool(self._parallel_check.isChecked()),
            "pardiso_threads": int(self._pardiso_threads_spin.value()),
            "acc_threads": int(self._acc_threads_spin.value()),
            "mesh_resolution": float(self._mesh_resolution_spin.value()),
            "curved_boundary_resolution": int(self._curved_boundary_resolution_spin.value()),
            "refinement_boundary_size_mm": float(self._refinement_boundary_size_spin.value()),
            "refinement_face_size_mm": float(self._refinement_face_size_spin.value()),
            "refinement_growth_rate": float(self._refinement_growth_rate_spin.value()),
            "refinement_max_size_mm": float(self._refinement_max_size_spin.value()),
            "plot_sparams_after_sim": bool(self._plot_sparams_check.isChecked()),
            "export_sparams_after_sim": bool(self._export_sparams_check.isChecked()),
            "export_full_scene_step": bool(self._export_full_scene_step_check.isChecked()),
            "boolean_decimation_enabled": bool(self._boolean_decimation_check.isChecked()),
            "workspace_path": self._workspace_path_edit.text().strip(),
        }