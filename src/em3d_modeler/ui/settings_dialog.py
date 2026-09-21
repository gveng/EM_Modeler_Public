from __future__ import annotations

from typing import Dict, Tuple

from PySide6.QtCore import Qt, QLocale
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QColorDialog,
    QSpinBox,
    QCheckBox,
)


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

        mesh_form.addRow(QLabel("Note: 0.3 = 3.3 lines/wavelength (fine mesh)"))

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

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
            self._triad_size_spin.blockSignals(True)
            self._workspace_spin.setValue(self._workspace_spin.value() * factor)
            self._grid_spin.setValue(self._grid_spin.value() * factor)
            self._triad_size_spin.setValue(self._triad_size_spin.value() * factor)
            self._workspace_spin.blockSignals(False)
            self._grid_spin.blockSignals(False)
            self._triad_size_spin.blockSignals(False)
        self._current_units = units
        self._update_suffixes(units)

    def _on_decimal_changed(self, value: str) -> None:
        locale = QLocale(QLocale.Italian, QLocale.Italy) if value.startswith("Comma") else QLocale.c()
        self._workspace_spin.setLocale(locale)
        self._grid_spin.setLocale(locale)
        self._triad_size_spin.setLocale(locale)

    def _update_suffixes(self, units: str) -> None:
        self._workspace_spin.setSuffix(f" {units}")
        self._grid_spin.setSuffix(f" {units}")
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
        selection_color: Tuple[float, float, float],
        locale: QLocale,
        solver: str = "PARDISO",
        parallel_enabled: bool = True,
        pardiso_threads: int = 8,
        acc_threads: int = 10,
        mesh_resolution: float = 0.3,
        plot_sparams_after_sim: bool = True,
        export_sparams_after_sim: bool = True,
    ) -> None:
        self._units_combo.setCurrentText(units)
        self._current_units = units
        self._decimal_combo.setCurrentText("Comma (,)" if decimal_separator == "," else "Point (.)")
        self._workspace_spin.setLocale(locale)
        self._grid_spin.setLocale(locale)
        self._update_suffixes(units)
        self._workspace_spin.setValue(workspace_size)
        self._grid_spin.setValue(grid_size)
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

    def values(self) -> dict:
        color_name = self._selection_color_combo.currentText()
        color = _SELECTION_COLOR_PRESETS.get(color_name, self._custom_color)
        return {
            "units": self._units_combo.currentText(),
            "decimal_separator": "," if self._decimal_combo.currentText().startswith("Comma") else ".",
            "workspace_size": float(self._workspace_spin.value()),
            "grid_size": float(self._grid_spin.value()),
            "plane_triad_size": float(self._triad_size_spin.value()),
            "selection_color": color,
            "solver": self._solver_combo.currentText(),
            "parallel_enabled": bool(self._parallel_check.isChecked()),
            "pardiso_threads": int(self._pardiso_threads_spin.value()),
            "acc_threads": int(self._acc_threads_spin.value()),
            "mesh_resolution": float(self._mesh_resolution_spin.value()),
            "plot_sparams_after_sim": bool(self._plot_sparams_check.isChecked()),
            "export_sparams_after_sim": bool(self._export_sparams_check.isChecked()),
        }