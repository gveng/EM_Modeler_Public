from __future__ import annotations

from collections.abc import Sequence
import hashlib
import importlib
import math
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QSettings, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


_DISPLAY_MODES = (
    "Magnitude (linear)",
    "Magnitude (dB)",
    "Phase (degrees)",
    "Real",
    "Imaginary",
)
_MARKERS = {
    "None": "",
    "Circle": "o",
    "Square": "s",
    "Triangle": "^",
    "Cross": "x",
    "Diamond": "D",
}
_DB_FLOOR = 1e-12
_PG_SYMBOLS = {"": None, "o": "o", "s": "s", "^": "t", "x": "x", "D": "d"}
_TRACE_PALETTE = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
)
_SMITH_RESISTANCE_VALUES = (0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)
_SMITH_REACTANCE_VALUES = _SMITH_RESISTANCE_VALUES
_SMITH_LABEL_VALUES = (0.2, 0.5, 1.0, 2.0, 5.0)


class _AxisRangeDialog(QDialog):
    def __init__(self, x_range, y_range, x_log: bool, y_log: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plot Axes")
        self.x_range = x_range
        self.y_range = y_range
        self.x_auto = QComboBox(self)
        self.y_auto = QComboBox(self)
        self.x_auto.addItems(["Auto", "Manual"])
        self.y_auto.addItems(["Auto", "Manual"])
        self.x_min = QLineEdit(self)
        self.x_max = QLineEdit(self)
        self.y_min = QLineEdit(self)
        self.y_max = QLineEdit(self)
        self._x_log = x_log
        self._y_log = y_log

        for mode, minimum, maximum, current_range in (
            (self.x_auto, self.x_min, self.x_max, x_range),
            (self.y_auto, self.y_min, self.y_max, y_range),
        ):
            mode.setCurrentText("Auto" if current_range is None else "Manual")
            if current_range is not None:
                minimum.setText(f"{current_range[0]:.8g}")
                maximum.setText(f"{current_range[1]:.8g}")
            mode.currentTextChanged.connect(
                lambda _text, combo=mode, min_edit=minimum, max_edit=maximum:
                self._update_range_enabled(combo, min_edit, max_edit)
            )

        form = QFormLayout(self)
        form.addRow("X range", self.x_auto)
        form.addRow("X minimum", self.x_min)
        form.addRow("X maximum", self.x_max)
        form.addRow("Y range", self.y_auto)
        form.addRow("Y minimum", self.y_min)
        form.addRow("Y maximum", self.y_max)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update_range_enabled(self.x_auto, self.x_min, self.x_max)
        self._update_range_enabled(self.y_auto, self.y_min, self.y_max)

    @staticmethod
    def _update_range_enabled(mode, minimum, maximum) -> None:
        manual = mode.currentText() == "Manual"
        minimum.setEnabled(manual)
        maximum.setEnabled(manual)

    def _accept(self) -> None:
        parsed_ranges = []
        for name, mode, minimum, maximum, logarithmic in (
            ("X", self.x_auto, self.x_min, self.x_max, self._x_log),
            ("Y", self.y_auto, self.y_min, self.y_max, self._y_log),
        ):
            if mode.currentText() == "Auto":
                parsed_ranges.append(None)
                continue
            try:
                low, high = float(minimum.text()), float(maximum.text())
            except ValueError:
                QMessageBox.warning(self, "Invalid range", f"Enter numeric {name} limits.")
                return
            if not math.isfinite(low) or not math.isfinite(high) or low >= high:
                QMessageBox.warning(self, "Invalid range", f"{name} minimum must be finite and less than maximum.")
                return
            if logarithmic and low <= 0:
                QMessageBox.warning(self, "Invalid range", f"Logarithmic {name} minimum must be positive.")
                return
            parsed_ranges.append((low, high))
        self.x_range, self.y_range = parsed_ranges
        self.accept()


def _transform_values(values: Sequence[float | complex], mode: str) -> np.ndarray:
    """Convert a complex-valued series to the selected display quantity."""
    values_array = np.asarray(values, dtype=complex)
    if mode == "Magnitude (linear)":
        return np.abs(values_array)
    if mode == "Magnitude (dB)":
        return 20.0 * np.log10(np.maximum(np.abs(values_array), _DB_FLOOR))
    if mode == "Phase (degrees)":
        return np.degrees(np.angle(values_array))
    if mode == "Real":
        return values_array.real
    if mode == "Imaginary":
        return values_array.imag
    if mode == "VSWR":
        magnitude = np.abs(values_array)
        return (1.0 + magnitude) / np.maximum(1.0 - magnitude, _DB_FLOOR)
    raise ValueError(f"Unsupported display mode: {mode}")


class PlotView(QWidget):
    """Reusable embedded chart view for simulation output and live plot data."""

    touchstone_load_requested = Signal()

    def __init__(
        self,
        plot_type: str,
        parent: QWidget | None = None,
        *,
        settings_key: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.plot_type = str(plot_type).strip().lower()
        self._settings_key = str(settings_key or self.plot_type)
        self._trace_color_settings = QSettings()
        self._is_smith = self.plot_type == "smith"
        self._is_vswr = self.plot_type == "plot_vswr"
        self._is_farfield = self.plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}
        self._is_polar = self.plot_type == "plot_ff_polar"
        self._use_pyqtgraph = not self._is_farfield
        self._x_values = np.asarray([], dtype=float)
        self._series_data: list[dict[str, Any]] = []
        self._trace_checkboxes: dict[tuple[str, str], QCheckBox] = {}
        self._curve_items = []
        self._smith_grid_items = []
        self._marker_items = []
        self._x_auto_range = True
        self._y_auto_range = True
        self._x_range = None
        self._y_range = None
        self._progressive_x_range: tuple[float, float] | None = None
        self._has_pg_data = False
        self._title = ""
        self._xlabel = ""
        self._ylabel = ""

        root_layout = QVBoxLayout(self)
        self._root_layout = root_layout
        control_layout = QHBoxLayout()
        display_mode_label = QLabel("Display")
        control_layout.addWidget(display_mode_label)
        self.display_mode_combo = QComboBox(self)
        self.display_mode_combo.setObjectName("displayModeCombo")
        self.display_mode_combo.addItems(_DISPLAY_MODES + (("VSWR",) if self._is_vswr else ()))
        if self._is_vswr:
            self.display_mode_combo.setCurrentText("VSWR")
        elif self.plot_type == "plot_sp":
            self.display_mode_combo.setCurrentText("Magnitude (dB)")
        elif self._is_smith or self._is_farfield:
            self.display_mode_combo.setCurrentText("Real")
            self.display_mode_combo.setEnabled(False)
        control_layout.addWidget(self.display_mode_combo)

        x_scale_label = QLabel("X scale")
        control_layout.addWidget(x_scale_label)
        self.x_scale_combo = QComboBox(self)
        self.x_scale_combo.setObjectName("xScaleCombo")
        self.x_scale_combo.addItems(["Linear", "Log"])
        if self._is_smith or self._is_polar:
            self.x_scale_combo.setEnabled(False)
        control_layout.addWidget(self.x_scale_combo)

        self.y_scale_combo = QComboBox(self)
        self.y_scale_combo.setObjectName("yScaleCombo")
        self.y_scale_combo.addItems(["Linear", "Log"])
        self.y_scale_combo.setToolTip(
            "Y log scale is unavailable for Magnitude (dB), which may contain negative values."
        )
        self.y_scale_combo.setEnabled(
            self._use_pyqtgraph
            and not self._is_smith
            and self.display_mode_combo.currentText() != "Magnitude (dB)"
        )
        y_scale_label = QLabel("Y scale")
        control_layout.addWidget(y_scale_label)
        control_layout.addWidget(self.y_scale_combo)
        if self._is_smith:
            for widget in (
                display_mode_label,
                self.display_mode_combo,
                x_scale_label,
                self.x_scale_combo,
                y_scale_label,
                self.y_scale_combo,
            ):
                widget.hide()

        self.axis_settings_button = None
        self.add_marker_button = None
        self.clear_markers_button = None
        self.fit_view_button = None
        if self._use_pyqtgraph and not self._is_smith:
            self.axis_settings_button = QToolButton(self)
            self.axis_settings_button.setText("Axes")
            self.axis_settings_button.setToolTip("Set automatic or manual axis ranges")
            self.axis_settings_button.clicked.connect(self._open_axis_settings)
            control_layout.addWidget(self.axis_settings_button)

            self.add_marker_button = QToolButton(self)
            self.add_marker_button.setText("Add Marker")
            self.add_marker_button.setToolTip("Add a movable frequency marker")
            self.add_marker_button.clicked.connect(self._add_marker_at_center)
            control_layout.addWidget(self.add_marker_button)

            self.clear_markers_button = QToolButton(self)
            self.clear_markers_button.setText("Clear Markers")
            self.clear_markers_button.clicked.connect(self._clear_markers)
            control_layout.addWidget(self.clear_markers_button)

            self.fit_view_button = QToolButton(self)
            self.fit_view_button.setText("Fit")
            self.fit_view_button.setToolTip("Fit all visible data in the plot")
            self.fit_view_button.clicked.connect(self._fit_view)
            control_layout.addWidget(self.fit_view_button)

        self.load_touchstone_button = None
        if not self._is_farfield:
            self.load_touchstone_button = QToolButton(self)
            self.load_touchstone_button.setText("Load Touchstone")
            self.load_touchstone_button.setToolTip(
                "Add Touchstone curves to this chart"
            )
            self.load_touchstone_button.clicked.connect(self.touchstone_load_requested.emit)
            control_layout.addWidget(self.load_touchstone_button)

        size_digest = hashlib.sha256(self._settings_key.encode("utf-8")).hexdigest()
        self._plot_size_setting_key = f"plots/sizes/{size_digest}"
        default_plot_width, default_plot_height = (700, 700) if self._is_smith else (1000, 600)
        try:
            initial_width = int(
                self._trace_color_settings.value(
                    f"{self._plot_size_setting_key}/width", default_plot_width
                )
            )
        except (TypeError, ValueError):
            initial_width = default_plot_width
        try:
            initial_height = int(
                self._trace_color_settings.value(
                    f"{self._plot_size_setting_key}/height", default_plot_height
                )
            )
        except (TypeError, ValueError):
            initial_height = default_plot_height

        control_layout.addWidget(QLabel("Plot size"))
        self.plot_width_spin = QSpinBox(self)
        self.plot_width_spin.setObjectName("plotWidthSpinBox")
        self.plot_width_spin.setRange(360, 2400)
        self.plot_width_spin.setSingleStep(50)
        self.plot_width_spin.setSuffix(" px")
        self.plot_width_spin.setToolTip("Set the plot canvas width in pixels")
        self.plot_width_spin.setValue(initial_width)
        control_layout.addWidget(QLabel("W"))
        control_layout.addWidget(self.plot_width_spin)

        self.plot_height_spin = QSpinBox(self)
        self.plot_height_spin.setObjectName("plotHeightSpinBox")
        self.plot_height_spin.setRange(240, 1600)
        self.plot_height_spin.setSingleStep(50)
        self.plot_height_spin.setSuffix(" px")
        self.plot_height_spin.setToolTip("Set the plot canvas height in pixels")
        self.plot_height_spin.setValue(initial_height)
        control_layout.addWidget(QLabel("H"))
        control_layout.addWidget(self.plot_height_spin)
        control_layout.addStretch(1)
        root_layout.addLayout(control_layout)

        self._series_container = QWidget(self)
        self._series_layout = QVBoxLayout(self._series_container)
        self._series_layout.setContentsMargins(0, 0, 0, 0)
        self._series_layout.setSpacing(2)
        self._series_scroll = QScrollArea(self)
        self._series_scroll.setWidgetResizable(True)
        self._series_scroll.setMaximumHeight(150)
        self._series_scroll.setWidget(self._series_container)
        root_layout.addWidget(self._series_scroll)

        self._canvas_scroll = QScrollArea(self)
        self._canvas_scroll.setWidgetResizable(False)
        self._canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._use_pyqtgraph:
            self.plot_widget = pg.PlotWidget(self, background="w")
            self.plot_widget.setAntialiasing(True)
            self.plot_widget.showGrid(x=True, y=True, alpha=0.22)
            self.plot_widget.setMenuEnabled(True)
            self.plot_widget.getPlotItem().layout.setContentsMargins(8, 8, 8, 8)
            self.axes = self.plot_widget.getPlotItem()
            self.figure = None
            self.canvas = self.plot_widget
            self._canvas_scroll.setWidget(self.plot_widget)
            root_layout.addWidget(self._canvas_scroll, 1)
            self.plot_widget.scene().sigMouseClicked.connect(self._on_plot_scene_clicked)
        else:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self.figure = Figure(figsize=(7, 4))
            self.axes = self.figure.add_subplot(111, projection="polar" if self._is_polar else None)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self._canvas_scroll.setWidget(self.canvas)
            root_layout.addWidget(self._canvas_scroll, 1)
        self._requested_canvas_size = (
            self.plot_width_spin.value(),
            self.plot_height_spin.value(),
        )
        self.canvas.setFixedSize(*self._requested_canvas_size)
        self._canvas_scroll.viewport().installEventFilter(self)
        self.plot_width_spin.valueChanged.connect(self._apply_plot_size)
        self.plot_height_spin.valueChanged.connect(self._apply_plot_size)

        self.display_mode_combo.currentTextChanged.connect(self._on_display_mode_changed)
        if self._use_pyqtgraph and not self._is_smith:
            self.x_scale_combo.currentTextChanged.connect(self._on_axis_scale_changed)
            self.y_scale_combo.currentTextChanged.connect(self._on_axis_scale_changed)
        elif not self._use_pyqtgraph:
            self.x_scale_combo.currentTextChanged.connect(self._redraw)

    def eventFilter(self, watched, event):
        if (
            watched is self._canvas_scroll.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._fit_canvas_to_viewport()
        return super().eventFilter(watched, event)

    def set_plot_data(
        self,
        x_values: Sequence[float],
        series: Sequence[dict[str, Any]],
        *,
        title: str,
        xlabel: str,
        ylabel: str,
    ) -> None:
        self._progressive_x_range = None
        self._update_data(x_values, series)
        self._title = str(title)
        self._xlabel = str(xlabel)
        self._ylabel = str(ylabel)
        self._redraw()

    def add_file_data(
        self,
        x_values: Sequence[float],
        series: Sequence[dict[str, Any]],
    ) -> None:
        self._progressive_x_range = None
        incoming_ids = {str(item.get("file_id", "default")) for item in series}
        existing = [
            item for item in self._series_data
            if item["file_id"] not in incoming_ids
        ]
        incoming = [dict(item, x_values=item.get("x_values", x_values)) for item in series]
        self._update_data(self._x_values, [*existing, *incoming])
        self._redraw(reset_range=True)

    def selected_parameters(self) -> list[str]:
        return list(dict.fromkeys(
            item["label"] for item in self._series_data if item["visible"]
        ))

    def configured_parameters(self) -> list[str]:
        return list(dict.fromkeys(
            item["label"] for item in self._series_data if item.get("configured", False)
        ))

    def set_progressive_data(
        self,
        x_values: Sequence[float],
        series: Sequence[dict[str, Any]],
        *,
        title: str,
        xlabel: str = "",
        ylabel: str = "",
        x_range: tuple[float, float] | None = None,
    ) -> None:
        if x_range is None:
            self._progressive_x_range = None
        else:
            low, high = float(x_range[0]), float(x_range[1])
            if not math.isfinite(low) or not math.isfinite(high) or low >= high:
                raise ValueError("progressive x range must be finite and increasing")
            self._progressive_x_range = (low, high)
        self._update_data(x_values, series)
        self._title = str(title)
        self._xlabel = str(xlabel)
        self._ylabel = str(ylabel)
        self._redraw()

    def clear_data(self) -> None:
        self._progressive_x_range = None
        self._x_values = np.asarray([], dtype=float)
        self._series_data = []
        if self._use_pyqtgraph:
            self._clear_markers()
        self._sync_series_controls()
        self._redraw(reset_range=True)

    @staticmethod
    def _normalize_color(value: Any, fallback: str = "#1f77b4") -> str:
        color = QColor(str(value))
        return color.name(QColor.NameFormat.HexRgb) if color.isValid() else fallback

    def _update_data(self, x_values: Sequence[float], series: Sequence[dict[str, Any]]) -> None:
        x_array = np.asarray(x_values).reshape(-1)
        previous = {
            (entry["file_id"], entry["label"]): entry for entry in self._series_data
        }
        updated: list[dict[str, Any]] = []
        for index, item in enumerate(series):
            label = str(item["label"])
            values = np.asarray(item["values"]).reshape(-1)
            file_name = str(item.get("file_name", ""))
            file_id = str(item.get("file_id", file_name or "default"))
            series_x = np.asarray(item.get("x_values", x_array)).reshape(-1)
            if len(values) != len(series_x):
                raise ValueError(f"Series '{label}' has {len(values)} values for {len(series_x)} x values")
            old_style = previous.get((file_id, label))
            saved_color = str(
                self._trace_color_settings.value(self._trace_color_setting_key(file_id, label), "")
            )
            if not QColor(saved_color).isValid():
                saved_color = ""
            palette_color = self._normalize_color(_TRACE_PALETTE[index % len(_TRACE_PALETTE)])
            current_color = (
                old_style["color"] if old_style is not None
                else saved_color or palette_color
            )
            current_color = self._normalize_color(current_color, palette_color)
            legend_label = item.get("legend_label")
            if not legend_label:
                legend_label = f"{file_name} - {label}" if file_name else label
            updated.append({
                "label": label,
                "legend_label": str(legend_label),
                "file_name": file_name or "Results",
                "file_id": file_id,
                "x_values": series_x,
                "values": values,
                "visible": old_style["visible"] if old_style is not None else bool(item.get("visible", True)),
                "configured": bool(item.get("configured", False)),
                "color": current_color,
                "marker": old_style["marker"] if old_style is not None else "",
            })
        self._x_values = x_array if x_array.size else (
            updated[0]["x_values"] if updated else np.asarray([], dtype=float)
        )
        self._series_data = updated
        self._sync_series_controls()

    def _trace_color_setting_key(self, file_id: str, label: str) -> str:
        identity = "\x1f".join((self._settings_key, file_id, label))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"plots/trace_colors/{digest}"

    def _sync_series_controls(self) -> None:
        while self._series_layout.count():
            item = self._series_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self._trace_checkboxes = {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in self._series_data:
            grouped.setdefault(entry["file_id"], []).append(entry)

        for entries in grouped.values():
            row_widget = QWidget(self._series_container)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(4, 2, 4, 2)
            row.setSpacing(8)
            file_label = QLabel(entries[0]["file_name"], row_widget)
            file_label.setMinimumWidth(120)
            file_label.setMaximumWidth(220)
            file_label.setToolTip(entries[0]["file_name"])
            file_label.setStyleSheet("font-weight: 600; color: #344054;")
            row.addWidget(file_label)
            for entry in entries:
                key = (entry["file_id"], entry["label"])
                checkbox = QCheckBox(entry["label"], row_widget)
                checkbox.setChecked(entry["visible"])
                checkbox.setToolTip(entry["legend_label"])
                checkbox.setStyleSheet(
                    f"QCheckBox::indicator:checked {{ background-color: {entry['color']}; "
                    "border: 1px solid #667085; }}"
                )
                checkbox.toggled.connect(
                    lambda checked, current_key=key: self._set_visible(current_key, checked)
                )
                checkbox.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                checkbox.customContextMenuRequested.connect(
                    lambda position, current_key=key, control=checkbox:
                    self._show_trace_context_menu(current_key, control, position)
                )
                row.addWidget(checkbox)
                self._trace_checkboxes[key] = checkbox
            row.addStretch(1)
            self._series_layout.addWidget(row_widget)

    def _set_visible(self, key: tuple[str, str], visible: bool) -> None:
        entry = next(
            (item for item in self._series_data if (item["file_id"], item["label"]) == key),
            None,
        )
        if entry is None:
            return
        entry["visible"] = visible
        self._redraw()

    def _set_marker(self, key: tuple[str, str], marker: str) -> None:
        for entry in self._series_data:
            if (entry["file_id"], entry["label"]) == key:
                entry["marker"] = marker
                break
        self._redraw()

    def _choose_color(self, key: tuple[str, str]) -> None:
        entry = next(
            (item for item in self._series_data if (item["file_id"], item["label"]) == key),
            None,
        )
        if entry is None:
            return
        color = QColorDialog.getColor(QColor(entry["color"]), self, f"Series color: {entry['legend_label']}")
        if color.isValid():
            entry["color"] = color.name()
            self._trace_color_settings.setValue(
                self._trace_color_setting_key(*key), entry["color"]
            )
            self._trace_color_settings.sync()
            checkbox = self._trace_checkboxes.get(key)
            if checkbox is not None:
                checkbox.setStyleSheet(
                    f"QCheckBox::indicator:checked {{ background-color: {entry['color']}; "
                    "border: 1px solid #667085; }}"
                )
            self._redraw()

    def _show_trace_context_menu(self, key, widget, position) -> None:
        menu = QMenu(self)
        menu.addAction("Choose color...", lambda: self._choose_color(key))
        marker_menu = menu.addMenu("Marker")
        current_marker = next(
            (item["marker"] for item in self._series_data
             if (item["file_id"], item["label"]) == key),
            "",
        )
        for marker_name, marker in _MARKERS.items():
            action = marker_menu.addAction(marker_name)
            action.setCheckable(True)
            action.setChecked(marker == current_marker)
            action.triggered.connect(
                lambda _checked=False, chosen=marker: self._set_marker(key, chosen)
            )
        menu.exec(widget.mapToGlobal(position))

    def _on_axis_scale_changed(self, _selection: str = "") -> None:
        self._x_auto_range = True
        self._y_auto_range = True
        self._x_range = None
        self._y_range = None
        self._redraw(reset_range=True)

    def _on_display_mode_changed(self, selection: str) -> None:
        decibel_mode = selection == "Magnitude (dB)"
        if self._use_pyqtgraph:
            if decibel_mode and self.y_scale_combo.currentText() == "Log":
                self.y_scale_combo.setCurrentText("Linear")
            self.y_scale_combo.setEnabled(not decibel_mode)
        self._redraw(reset_range=True)

    def _apply_plot_size(self, _value: int = 0) -> None:
        width = self.plot_width_spin.value()
        height = self.plot_height_spin.value()
        self._requested_canvas_size = (width, height)
        self._trace_color_settings.setValue(f"{self._plot_size_setting_key}/width", width)
        self._trace_color_settings.setValue(f"{self._plot_size_setting_key}/height", height)
        self._trace_color_settings.sync()
        self._fit_canvas_to_viewport()

    def _fit_canvas_to_viewport(self) -> None:
        available = self._canvas_scroll.viewport().size()
        if available.width() <= 0 or available.height() <= 0:
            return
        requested_width, requested_height = self._requested_canvas_size
        scale = min(
            1.0,
            available.width() / requested_width,
            available.height() / requested_height,
        )
        self.canvas.setFixedSize(
            max(1, int(requested_width * scale)),
            max(1, int(requested_height * scale)),
        )

    def _open_axis_settings(self) -> None:
        dialog = _AxisRangeDialog(
            self._x_range,
            self._y_range,
            self.x_scale_combo.currentText() == "Log",
            self.y_scale_combo.currentText() == "Log",
            self,
        )
        if dialog.exec():
            self._x_auto_range = dialog.x_range is None
            self._y_auto_range = dialog.y_range is None
            self._x_range = dialog.x_range
            self._y_range = dialog.y_range
            self._redraw(reset_range=True)

    def _fit_view(self) -> None:
        self._x_auto_range = True
        self._y_auto_range = True
        self._x_range = None
        self._y_range = None
        self._redraw(reset_range=True)

    def _on_plot_scene_clicked(self, event) -> None:
        if self._is_smith or not event.double():
            return
        view_box = self.plot_widget.getPlotItem().getViewBox()
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        point = view_box.mapSceneToView(event.scenePos())
        self._add_marker(float(point.x()))

    def _add_marker_at_center(self) -> None:
        if not self._has_pg_data:
            return
        x_range = self.plot_widget.getPlotItem().getViewBox().viewRange()[0]
        self._add_marker((x_range[0] + x_range[1]) / 2.0)

    def _add_marker(self, x_value: float) -> None:
        line = pg.InfiniteLine(
            pos=x_value,
            angle=90,
            movable=True,
            pen=pg.mkPen("#d04a28", width=1.5, style=Qt.PenStyle.DashLine),
        )
        label = pg.TextItem(color="#a83218", anchor=(0.5, 1.0), fill=pg.mkBrush(255, 255, 255, 220))
        self.plot_widget.addItem(line, ignoreBounds=True)
        self.plot_widget.addItem(label, ignoreBounds=True)
        marker = (line, label)
        self._marker_items.append(marker)
        line.sigPositionChanged.connect(lambda _line=line: self._update_marker_label(_line))
        line.sigClicked.connect(
            lambda clicked_line, event, current=marker: self._on_marker_clicked(current, event)
        )
        self._update_marker_label(line)

    def _on_marker_clicked(self, marker, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.plot_widget.removeItem(marker[0])
            self.plot_widget.removeItem(marker[1])
            if marker in self._marker_items:
                self._marker_items.remove(marker)

    def _clear_markers(self) -> None:
        if not self._use_pyqtgraph:
            return
        for line, label in self._marker_items:
            self.plot_widget.removeItem(line)
            self.plot_widget.removeItem(label)
        self._marker_items.clear()

    def _update_marker_label(self, line) -> None:
        for marker_line, label in self._marker_items:
            if marker_line is not line:
                continue
            view_x = float(line.value())
            x_value = 10.0 ** view_x if self.x_scale_combo.currentText() == "Log" else view_x
            lines = [f"{self._xlabel or 'X'}: {x_value:.6g}"]
            display_mode = self.display_mode_combo.currentText()
            for entry in self._series_data:
                if not entry["visible"]:
                    continue
                x_values = np.asarray(entry["x_values"], dtype=float)
                y_values = _transform_values(entry["values"], display_mode)
                valid = np.isfinite(x_values) & np.isfinite(y_values)
                if self.x_scale_combo.currentText() == "Log":
                    valid &= x_values > 0
                if self.y_scale_combo.currentText() == "Log":
                    valid &= y_values > 0
                if np.count_nonzero(valid) == 0:
                    continue
                order = np.argsort(x_values[valid])
                interpolated = np.interp(
                    x_value, x_values[valid][order], y_values[valid][order]
                )
                lines.append(f"{entry['label']}: {interpolated:.4g}")
            label.setText("\n".join(lines))
            y_top = self.plot_widget.getPlotItem().getViewBox().viewRange()[1][1]
            label.setPos(x_value, y_top)
            return

    def _redraw(self, _selection: str = "", *, reset_range: bool = False) -> None:
        if self._use_pyqtgraph:
            self._redraw_pyqtgraph(reset_range=reset_range)
            return
        self._redraw_matplotlib(_selection)

    def _redraw_pyqtgraph(self, *, reset_range: bool = False) -> None:
        if self._is_smith:
            self._redraw_smith()
            return

        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        previous_range = view_box.viewRange() if self._has_pg_data else None
        for curve in self._curve_items:
            plot_item.removeItem(curve)
        self._curve_items.clear()

        legend = getattr(plot_item, "legend", None)
        if legend is not None:
            plot_item.scene().removeItem(legend)
            plot_item.legend = None

        x_log = self.x_scale_combo.currentText() == "Log"
        y_log = self.y_scale_combo.currentText() == "Log"
        plot_item.setLogMode(x=x_log, y=y_log)
        plot_item.setTitle(self._title)
        plot_item.setLabel("bottom", self._xlabel or "Frequency (GHz)")
        mode = self.display_mode_combo.currentText()
        ylabel = "VSWR" if mode == "VSWR" else mode
        plot_item.setLabel("left", ylabel or self._ylabel)
        visible_series = [entry for entry in self._series_data if entry["visible"]]
        if visible_series:
            plot_item.addLegend(offset=(10, 10))

        for entry in visible_series:
            x_values = np.asarray(entry["x_values"], dtype=float)
            y_values = _transform_values(entry["values"], mode)
            valid = np.isfinite(x_values) & np.isfinite(y_values)
            if x_log:
                valid &= x_values > 0
            if y_log:
                valid &= y_values > 0
            if not np.any(valid):
                continue
            symbol = _PG_SYMBOLS.get(entry["marker"])
            curve = self.plot_widget.plot(
                x_values[valid],
                y_values[valid],
                name=entry["legend_label"],
                pen=pg.mkPen(entry["color"], width=2),
                symbol=symbol,
                symbolSize=7,
                symbolBrush=pg.mkBrush(entry["color"]) if symbol else None,
                symbolPen=pg.mkPen(entry["color"]) if symbol else None,
            )
            self._curve_items.append(curve)

        if reset_range or previous_range is None:
            self.plot_widget.autoRange(padding=0.04)
        else:
            view_box.setRange(
                xRange=previous_range[0],
                yRange=previous_range[1],
                padding=0.0,
            )
        if self._progressive_x_range is not None:
            view_box.disableAutoRange(axis=view_box.XAxis)
            self.plot_widget.setXRange(
                *self._range_in_view(self._progressive_x_range, x_log), padding=0.0
            )
        elif self._x_auto_range:
            view_box.enableAutoRange(axis=view_box.XAxis, enable=True)
        elif self._x_range is not None:
            self.plot_widget.setXRange(
                *self._range_in_view(self._x_range, x_log), padding=0.0
            )
        if self._y_auto_range:
            view_box.enableAutoRange(axis=view_box.YAxis, enable=True)
        elif self._y_range is not None:
            self.plot_widget.setYRange(
                *self._range_in_view(self._y_range, y_log), padding=0.0
            )
        self._has_pg_data = bool(self._curve_items)
        for line, _label in self._marker_items:
            self._update_marker_label(line)

    def _redraw_smith(self) -> None:
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        for item in (*self._curve_items, *self._smith_grid_items):
            plot_item.removeItem(item)
        self._curve_items.clear()
        self._smith_grid_items.clear()

        legend = getattr(plot_item, "legend", None)
        if legend is not None:
            plot_item.scene().removeItem(legend)
            plot_item.legend = None

        plot_item.setLogMode(x=False, y=False)
        plot_item.showGrid(x=False, y=False)
        plot_item.setTitle(self._title)
        plot_item.setLabel("bottom", "Re(Γ)")
        plot_item.setLabel("left", "Im(Γ)")
        view_box.setAspectLocked(True, ratio=1.0)
        view_box.disableAutoRange()
        view_box.setRange(xRange=(-1.08, 1.08), yRange=(-1.08, 1.08), padding=0.0)
        plot_item.getAxis("bottom").setTicks([
            [(-1.0, "-1"), (0.0, "0"), (1.0, "1")]
        ])
        plot_item.getAxis("left").setTicks([
            [(-1.0, "-j"), (0.0, "0"), (1.0, "+j")]
        ])

        major_pen = pg.mkPen("#aebbc7", width=1)
        minor_pen = pg.mkPen("#d5dde5", width=1)
        theta = np.linspace(0.0, 2.0 * np.pi, 721)
        for resistance in _SMITH_RESISTANCE_VALUES:
            center = resistance / (1.0 + resistance)
            radius = 1.0 / (1.0 + resistance)
            circle = center + radius * np.exp(1j * theta)
            self._smith_grid_items.append(
                plot_item.plot(
                    circle.real,
                    circle.imag,
                    pen=major_pen if resistance in _SMITH_LABEL_VALUES else minor_pen,
                )
            )

            if resistance in _SMITH_LABEL_VALUES:
                resistance_point = (resistance - 1.0) / (resistance + 1.0)
                label = pg.TextItem(
                    f"{resistance:g}", color="#475467", anchor=(0.5, 0.0)
                )
                label.setPos(resistance_point, -0.045)
                plot_item.addItem(label)
                self._smith_grid_items.append(label)

        resistance_values = np.concatenate(
            ([0.0], np.geomspace(1e-6, 1e6, 1600))
        )
        for reactance in _SMITH_REACTANCE_VALUES:
            for signed_reactance in (reactance, -reactance):
                impedance = resistance_values + 1j * signed_reactance
                reflection = (impedance - 1.0) / (impedance + 1.0)
                self._smith_grid_items.append(
                    plot_item.plot(
                        reflection.real,
                        reflection.imag,
                        pen=major_pen if reactance in _SMITH_LABEL_VALUES else minor_pen,
                    )
                )

                if reactance in _SMITH_LABEL_VALUES:
                    boundary_impedance = 1j * signed_reactance
                    boundary_reflection = (
                        (boundary_impedance - 1.0) / (boundary_impedance + 1.0)
                    )
                    sign = "+" if signed_reactance > 0 else "-"
                    label = pg.TextItem(
                        f"{sign}j{reactance:g}", color="#667085", anchor=(0.5, 0.5)
                    )
                    label.setPos(
                        boundary_reflection.real * 0.94,
                        boundary_reflection.imag * 0.94,
                    )
                    plot_item.addItem(label)
                    self._smith_grid_items.append(label)

        unit_circle = np.exp(1j * theta)
        self._smith_grid_items.append(
            plot_item.plot(unit_circle.real, unit_circle.imag, pen=pg.mkPen("#344054", width=1.5))
        )
        self._smith_grid_items.append(
            plot_item.plot([-1.0, 1.0], [0.0, 0.0], pen=pg.mkPen("#667085", width=1))
        )

        visible_series = [entry for entry in self._series_data if entry["visible"]]
        for entry in visible_series:
            reflection = np.asarray(entry["values"], dtype=complex)
            valid = np.isfinite(reflection.real) & np.isfinite(reflection.imag)
            if not np.any(valid):
                continue
            symbol = _PG_SYMBOLS.get(entry["marker"])
            curve = self.plot_widget.plot(
                reflection.real[valid],
                reflection.imag[valid],
                name=entry["legend_label"],
                pen=pg.mkPen(entry["color"], width=2),
                symbol=symbol,
                symbolSize=7,
                symbolBrush=pg.mkBrush(entry["color"]) if symbol else None,
                symbolPen=pg.mkPen(entry["color"]) if symbol else None,
            )
            self._curve_items.append(curve)
        self._has_pg_data = bool(self._curve_items)

    @staticmethod
    def _range_in_view(axis_range, logarithmic: bool):
        if logarithmic:
            return tuple(float(np.log10(value)) for value in axis_range)
        return axis_range

    def _redraw_matplotlib(self, _selection: str = "") -> None:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        import matplotlib.pyplot as plt

        emerge_plot = importlib.import_module("emerge.plot")
        visible_series = [item for item in self._series_data if item["visible"]]
        labels = [item["legend_label"] for item in visible_series]
        values = [np.asarray(item["values"]) for item in visible_series]
        frequency_hz = [
            np.asarray(item["x_values"], dtype=float) * 1e9 for item in visible_series
        ]
        display_mode = self.display_mode_combo.currentText()
        log_x = self.x_scale_combo.currentText() == "Log"
        figures_before = {
            id(manager.canvas.figure)
            for manager in plt._pylab_helpers.Gcf.get_all_fig_managers()
            if getattr(manager, "canvas", None) is not None
        }

        old_show = plt.show
        plt.show = lambda *_args, **_kwargs: None
        try:
            if not visible_series:
                figure = Figure()
                axes = figure.add_subplot(111, projection="polar" if self._is_polar else None)
                axes.set_title(self._title)
                if self._is_smith:
                    axes.set_xlim(-1.1, 1.1)
                    axes.set_ylim(-1.1, 1.1)
                    axes.set_aspect("equal", adjustable="box")
                elif not self._is_polar:
                    axes.set_xlabel(self._xlabel)
                    axes.set_ylabel(self._ylabel)
            elif self._is_smith:
                emerge_plot.smith(
                    values,
                    f=frequency_hz,
                    labels=labels,
                    title=self._title,
                    colors=[item["color"] for item in visible_series],
                    markers=[item["marker"] or None for item in visible_series],
                )
                figure = plt.gcf()
                axes = figure.axes[0]
            elif self._is_vswr and display_mode == "VSWR":
                figure, axes = emerge_plot.plot_vswr(
                    frequency_hz,
                    values,
                    labels=labels,
                    xunit="GHz",
                    show_plot=False,
                )
            elif self.plot_type == "plot_sp" and display_mode in {
                "Magnitude (dB)", "Phase (degrees)"
            }:
                figure, magnitude_axes, phase_axes = emerge_plot.plot_sp(
                    frequency_hz,
                    values,
                    labels=labels,
                    xunit="GHz",
                    logx=log_x,
                    show_plot=False,
                )
                axes = magnitude_axes if display_mode == "Magnitude (dB)" else phase_axes
                hidden_axes = phase_axes if axes is magnitude_axes else magnitude_axes
                hidden_axes.set_visible(False)
                axes.set_position((0.1, 0.12, 0.85, 0.8))
                axes.set_title(self._title)
            elif self._is_polar:
                emerge_plot.plot_ff_polar(
                    self._x_values,
                    [np.real(item) for item in values],
                    labels=labels,
                    markers=[item["marker"] or None for item in visible_series],
                    dB=display_mode == "Magnitude (dB)",
                    title=self._title,
                )
                figure = plt.gcf()
                axes = figure.axes[0]
            elif self._is_farfield:
                emerge_plot.plot_ff(
                    self._x_values,
                    [np.real(item) for item in values],
                    labels=labels,
                    markers=[item["marker"] or None for item in visible_series],
                    dB=display_mode == "Magnitude (dB)",
                    xlabel=self._xlabel,
                    ylabel=self._ylabel,
                    title=self._title,
                )
                figure = plt.gcf()
                axes = figure.axes[0]
            else:
                display_values = [
                    _transform_values(item, display_mode)
                    for item in values
                ]
                emerge_plot.plot(
                    self._x_values,
                    display_values,
                    labels=labels,
                    xlabel=self._xlabel,
                    ylabel=("VSWR" if self._is_vswr and display_mode == "VSWR" else self._ylabel),
                    logx=log_x,
                    title=self._title,
                )
                figures_after = [
                    manager.canvas.figure
                    for manager in plt._pylab_helpers.Gcf.get_all_fig_managers()
                    if getattr(manager, "canvas", None) is not None
                    and id(manager.canvas.figure) not in figures_before
                ]
                figure = figures_after[-1] if figures_after else plt.gcf()
                axes = figure.axes[0]
        finally:
            plt.show = old_show

        if visible_series and not self._is_smith:
            line_by_label = {item["legend_label"]: item for item in visible_series}
            for line in axes.lines:
                item = line_by_label.get(line.get_label())
                if item is None and len(axes.lines) >= len(visible_series):
                    line_index = axes.lines.index(line)
                    if line_index < len(visible_series):
                        item = visible_series[line_index]
                if item is not None:
                    line.set_color(item["color"])
                    line.set_marker(item["marker"])
        axes.set_title(self._title)
        legend = axes.get_legend()
        if legend is not None:
            legend.remove()
        if visible_series:
            axes.legend()

        new_canvas = FigureCanvasQTAgg(figure)
        self._root_layout.replaceWidget(self.canvas, new_canvas)
        previous_canvas = self.canvas
        previous_figure = self.figure
        self.canvas = new_canvas
        self.figure = figure
        self.axes = axes
        previous_canvas.setParent(None)
        previous_canvas.deleteLater()
        if previous_figure is not figure:
            plt.close(previous_figure)
        plt.close(figure)
        self.canvas.draw_idle()