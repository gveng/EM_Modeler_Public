from __future__ import annotations

import ast
from collections.abc import Sequence
import hashlib
import importlib
import json
import logging
import math
import os
from pathlib import Path
import operator
import re
import sys
import tempfile
from typing import Any
import zlib

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, QStandardPaths, Qt, Signal, QSettings
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QMdiArea,
    QMdiSubWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QTabWidget,
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
_CHART_SURFACE_COLOR = "#ffffff"
_PG_SYMBOLS = {"": None, "o": "o", "s": "s", "^": "t", "x": "x", "D": "d"}
_TRACE_PALETTE = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
)
_SMITH_RESISTANCE_VALUES = (0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)
_SMITH_REACTANCE_VALUES = _SMITH_RESISTANCE_VALUES
_SMITH_LABEL_VALUES = (0.2, 0.5, 1.0, 2.0, 5.0)
_LOGGER = logging.getLogger(__name__)
_EQUATION_FILE_ID = "__chart_equations__"
_EQUATION_FUNCTIONS = {
    "abs": np.abs,
    "angle": np.angle,
    "arccos": np.arccos,
    "arcsin": np.arcsin,
    "arctan": np.arctan,
    "conj": np.conjugate,
    "cos": np.cos,
    "exp": np.exp,
    "imag": np.imag,
    "log": np.log,
    "log10": np.log10,
    "maximum": np.maximum,
    "minimum": np.minimum,
    "real": np.real,
    "sin": np.sin,
    "sqrt": np.sqrt,
    "tan": np.tan,
}
_EQUATION_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_EQUATION_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


def _parse_equation_expression(expression: str, allowed_names: set[str]) -> ast.Expression:
    """Parse and restrict an equation to numeric operators and approved functions."""
    if len(expression) > 512:
        raise ValueError("Formula is too long (maximum 512 characters).")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid formula syntax: {exc.msg}.") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 100:
        raise ValueError("Formula is too complex (maximum 100 syntax elements).")
    permitted_names = allowed_names | set(_EQUATION_FUNCTIONS) | {"pi", "e"}
    for node in nodes:
        if isinstance(node, ast.Name):
            if node.id not in permitted_names:
                raise ValueError(f"Unknown formula variable or function '{node.id}'.")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _EQUATION_FUNCTIONS:
                raise ValueError("Only the listed numeric functions may be called.")
            if node.keywords:
                raise ValueError("Formula functions do not accept keyword arguments.")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("Only numeric constants are allowed in formulas.")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _EQUATION_BINARY_OPERATORS:
                raise ValueError("This arithmetic operator is not supported.")
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _EQUATION_UNARY_OPERATORS:
                raise ValueError("This unary operator is not supported.")
        elif isinstance(
            node,
            (
                ast.Expression, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div,
                ast.FloorDiv, ast.Mod, ast.Pow, ast.UAdd, ast.USub,
            ),
        ):
            continue
        else:
            raise ValueError(f"Formula element '{type(node).__name__}' is not allowed.")
    return tree


def _evaluate_equation_expression(
    expression: str,
    variables: dict[str, Any],
) -> np.ndarray:
    """Evaluate a validated element-wise numeric formula without using eval()."""
    tree = _parse_equation_expression(expression, set(variables))

    def calculate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return calculate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "pi":
                return math.pi
            if node.id == "e":
                return math.e
            if node.id in variables:
                return variables[node.id]
            return _EQUATION_FUNCTIONS[node.id]
        if isinstance(node, ast.BinOp):
            left, right = calculate(node.left), calculate(node.right)
            if isinstance(node.op, ast.Pow) and np.isscalar(right):
                if abs(right) > 1000:
                    raise ValueError("Formula exponents must be between -1000 and 1000.")
            return _EQUATION_BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            return _EQUATION_UNARY_OPERATORS[type(node.op)](calculate(node.operand))
        if isinstance(node, ast.Call):
            function = _EQUATION_FUNCTIONS[node.func.id]
            return function(*(calculate(argument) for argument in node.args))
        raise ValueError(f"Formula element '{type(node).__name__}' is not allowed.")

    with np.errstate(all="ignore"):
        result = np.asarray(calculate(tree))
    if result.ndim == 0:
        result = result.reshape(1)
    return result.reshape(-1)


def _chart_data_cache_root() -> Path:
    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if app_data:
        return Path(app_data) / "chart-data"
    return Path.home() / ".em3d_modeler" / "chart-data"


def _chart_logo_path() -> Path | None:
    source_root = Path(__file__).resolve().parents[2]
    roots = (
        Path(getattr(sys, "_MEIPASS", source_root.parent)),
        Path(sys.executable).resolve().parent,
        source_root.parent,
    )
    for root in roots:
        candidate = root / "Icons" / "Emerge_Logo" / "emerge_Logo.png"
        if candidate.is_file():
            return candidate
    return None


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


def _bounded_overlay_position(parent: QWidget, child: QWidget, position: QPoint) -> QPoint:
    bounds = parent.rect()
    return QPoint(
        max(0, min(position.x(), bounds.width() - child.width())),
        max(0, min(position.y(), bounds.height() - child.height())),
    )


def _overlay_scale(host: QWidget) -> float:
    return max(0.7, min(1.2, min(host.width() / 800, host.height() / 600)))


def _scaled_overlay_font(font, scale: float):
    point_size = font.pointSizeF()
    if point_size > 0:
        font.setPointSizeF(max(6.0, point_size * scale))
    elif font.pixelSize() > 0:
        font.setPixelSize(max(7, round(font.pixelSize() * scale)))
    return font


class _TraceSettingsDialog(QDialog):
    def __init__(self, color: str, marker: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trace Settings")
        self.resize(340, 150)
        self.color = color
        self.marker = marker

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.color_button = QPushButton("Choose color...", self)
        self._update_color_button()
        self.color_button.clicked.connect(self._choose_color)
        form.addRow("Color", self.color_button)
        self.marker_combo = QComboBox(self)
        self.marker_combo.addItems(list(_MARKERS))
        self.marker_combo.setCurrentText(
            next(name for name, value in _MARKERS.items() if value == marker)
        )
        form.addRow("Marker", self.marker_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_color_button(self) -> None:
        self.color_button.setText(self.color)
        self.color_button.setStyleSheet(f"background-color: {self.color};")

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color), self, "Trace color")
        if color.isValid():
            self.color = color.name()
            self._update_color_button()

    def _accept(self) -> None:
        self.marker = _MARKERS[self.marker_combo.currentText()]
        self.accept()


class _DragHandle(QLabel):
    def __init__(self, text: str = "Legend", parent=None) -> None:
        super().__init__(text, parent)
        self._drag_offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            panel = self.parentWidget()
            parent = panel.parentWidget() if panel is not None else None
            if panel is None or parent is None:
                super().mousePressEvent(event)
                return
            self._drag_offset = (
                event.globalPosition().toPoint()
                - panel.mapToGlobal(QPoint(0, 0))
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            panel = self.parentWidget()
            parent = panel.parentWidget() if panel is not None else None
            if panel is not None and parent is not None:
                target = parent.mapFromGlobal(
                    event.globalPosition().toPoint() - self._drag_offset
                )
                panel.move(_bounded_overlay_position(parent, panel, target))
                panel.position_changed.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ChartSurface(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chartSurface")
        self.setStyleSheet(
            f"QFrame#chartSurface {{ background-color: {_CHART_SURFACE_COLOR}; border: none; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)


class _MarkerReadout(pg.TextItem):
    moved = Signal()

    def __init__(self, **options) -> None:
        super().__init__("", **options)
        self._manual_position = False
        self._setting_follow_position = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    @property
    def movable(self) -> bool:
        return bool(self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

    @property
    def manually_positioned(self) -> bool:
        return self._manual_position

    def set_follow_position(self, x: float, y: float) -> None:
        if self._manual_position:
            return
        self._setting_follow_position = True
        try:
            self.setPos(x, y)
        finally:
            self._setting_follow_position = False

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._setting_follow_position
        ):
            self._manual_position = True
            self.moved.emit()
        return super().itemChange(change, value)


class _PersistentLegendItem(pg.LegendItem):
    def __init__(self, position_changed, **options) -> None:
        self._position_changed = position_changed
        super().__init__(**options)

    def mouseDragEvent(self, event) -> None:
        super().mouseDragEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._position_changed(self)


class _DraggableLegendPanel(QFrame):
    position_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("floatingChartLegend")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QFrame#floatingChartLegend {{ background-color: {_CHART_SURFACE_COLOR}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 5)
        layout.setSpacing(3)
        self.handle = _DragHandle()
        self.handle.setObjectName("floatingChartLegendHandle")
        self.handle.setCursor(Qt.CursorShape.SizeAllCursor)
        layout.addWidget(self.handle)
        self.list = QListWidget(self)
        self.list.setObjectName("externalChartLegend")
        self.list.setStyleSheet(
            f"QListWidget#externalChartLegend {{ background-color: {_CHART_SURFACE_COLOR}; }}"
        )
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMaximumHeight(180)
        layout.addWidget(self.list)


class _ChartMdiArea(QMdiArea):
    resized = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chart_window_initialized = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        chart_window = getattr(self, "chart_subwindow", None)
        if chart_window is None:
            return
        bounds = self.viewport().rect()
        margin = min(12, max(0, (bounds.width() - 1) // 2), max(0, (bounds.height() - 1) // 2))
        chart_window.setGeometry(
            margin,
            margin,
            max(1, bounds.width() - 2 * margin),
            max(1, bounds.height() - 2 * margin),
        )
        self._chart_window_initialized = True
        self.resized.emit()


class _ChartSubWindow(QMdiSubWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 280)
        self.setWindowFlags(
            Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint
        )


class _PlotSettingsDialog(QDialog):
    def __init__(self, view: PlotView) -> None:
        super().__init__(view)
        self.setObjectName("plotSettingsDialog")
        self.setWindowTitle("Chart Settings")
        self.resize(760, 500)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("plotSettingsTabs")
        layout.addWidget(self.tabs, 1)

        setup_page = QWidget(self.tabs)
        setup_layout = QVBoxLayout(setup_page)
        lists_layout = QHBoxLayout()
        setup_layout.addLayout(lists_layout, 1)

        available_panel = QWidget(setup_page)
        available_layout = QVBoxLayout(available_panel)
        available_layout.setContentsMargins(0, 0, 0, 0)
        available_layout.addWidget(QLabel("Available parameters", available_panel))
        self.load_touchstone_button = None
        if not view._is_farfield:
            self.load_touchstone_button = QPushButton("Load Touchstone file...", available_panel)
            self.load_touchstone_button.setObjectName("loadTouchstoneButton")
            self.load_touchstone_button.clicked.connect(view.touchstone_load_requested.emit)
            available_layout.addWidget(self.load_touchstone_button)
        self.dataset_combo = QComboBox(available_panel)
        self.dataset_combo.setObjectName("traceDatasetCombo")
        self.dataset_combo.setToolTip("Choose the dataset whose available parameters are shown")
        available_layout.addWidget(self.dataset_combo)
        self.remove_dataset_button = QPushButton("Remove dataset", available_panel)
        self.remove_dataset_button.setObjectName("removeDatasetButton")
        self.remove_dataset_button.setToolTip(
            "Unload the selected loaded/appended dataset and all of its parameters"
        )
        self.remove_dataset_button.setEnabled(False)
        available_layout.addWidget(self.remove_dataset_button)
        self.trace_search = QLineEdit(available_panel)
        self.trace_search.setPlaceholderText("Filter parameters")
        self.trace_search.setClearButtonEnabled(True)
        available_layout.addWidget(self.trace_search)
        self.available_list = QListWidget(available_panel)
        self.available_list.setObjectName("availableTraceList")
        self.available_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        available_layout.addWidget(self.available_list, 1)
        lists_layout.addWidget(available_panel, 1)

        transfer_layout = QVBoxLayout()
        transfer_layout.addStretch(1)
        self.add_trace_button = QPushButton("Add >", setup_page)
        self.add_trace_button.setObjectName("addTraceButton")
        self.remove_trace_button = QPushButton("< Remove", setup_page)
        self.remove_trace_button.setObjectName("removeTraceButton")
        transfer_layout.addWidget(self.add_trace_button)
        transfer_layout.addWidget(self.remove_trace_button)
        transfer_layout.addStretch(1)
        lists_layout.addLayout(transfer_layout)

        selected_panel = QWidget(setup_page)
        selected_layout = QVBoxLayout(selected_panel)
        selected_layout.setContentsMargins(0, 0, 0, 0)
        selected_layout.addWidget(QLabel("Selected parameters", selected_panel))
        self.configure_trace_button = QPushButton("Configure selected trace...", selected_panel)
        self.configure_trace_button.setObjectName("configureTraceButton")
        self.configure_trace_button.setEnabled(False)
        selected_layout.addWidget(self.configure_trace_button)
        self.selected_list = QListWidget(selected_panel)
        self.selected_list.setObjectName("selectedTraceList")
        self.selected_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        selected_layout.addWidget(self.selected_list, 1)
        order_layout = QHBoxLayout()
        order_layout.addStretch(1)
        self.move_trace_up_button = QToolButton(selected_panel)
        self.move_trace_up_button.setObjectName("moveTraceUpButton")
        self.move_trace_up_button.setIcon(
            selected_panel.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.move_trace_up_button.setToolTip("Move selected trace up")
        self.move_trace_up_button.setAccessibleName("Move selected trace up")
        self.move_trace_up_button.setEnabled(False)
        self.move_trace_down_button = QToolButton(selected_panel)
        self.move_trace_down_button.setObjectName("moveTraceDownButton")
        self.move_trace_down_button.setIcon(
            selected_panel.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.move_trace_down_button.setToolTip("Move selected trace down")
        self.move_trace_down_button.setAccessibleName("Move selected trace down")
        self.move_trace_down_button.setEnabled(False)
        order_layout.addWidget(self.move_trace_up_button)
        order_layout.addWidget(self.move_trace_down_button)
        selected_layout.addLayout(order_layout)
        lists_layout.addWidget(selected_panel, 1)
        self.tabs.addTab(setup_page, "Setup")

        option_page = QWidget(self.tabs)
        plot_layout = QVBoxLayout(option_page)
        plot_layout.setContentsMargins(12, 12, 12, 12)
        plot_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        plot_form = QFormLayout()
        plot_form.addRow("Display", view.display_mode_combo)
        plot_form.addRow("X scale", view.x_scale_combo)
        plot_form.addRow("Y scale", view.y_scale_combo)
        self.legend_scale_slider = QSlider(Qt.Orientation.Horizontal, option_page)
        self.legend_scale_slider.setObjectName("legendScaleSlider")
        self.legend_scale_slider.setRange(50, 100)
        self.legend_scale_slider.setValue(view._legend_scale_percent)
        self.legend_scale_slider.setEnabled(view._use_pyqtgraph)
        self.legend_scale_slider.setToolTip("Scale the chart legend from 50% to 100%")
        self.legend_scale_slider.setAccessibleName("Legend size")
        self.legend_scale_label = QLabel(f"{view._legend_scale_percent}%", option_page)
        self.legend_scale_slider.valueChanged.connect(view._set_legend_scale)
        self.legend_scale_slider.valueChanged.connect(
            lambda value: self.legend_scale_label.setText(f"{value}%")
        )
        legend_scale_layout = QHBoxLayout()
        legend_scale_layout.addWidget(self.legend_scale_slider, 1)
        legend_scale_layout.addWidget(self.legend_scale_label)
        plot_form.addRow("Legend size", legend_scale_layout)
        self.marker_scale_slider = QSlider(Qt.Orientation.Horizontal, option_page)
        self.marker_scale_slider.setObjectName("markerScaleSlider")
        self.marker_scale_slider.setRange(50, 200)
        self.marker_scale_slider.setValue(view._marker_scale_percent)
        self.marker_scale_slider.setEnabled(view._use_pyqtgraph and not view._is_smith)
        self.marker_scale_slider.setToolTip("Scale the marker readout labels from 50% to 200%")
        self.marker_scale_slider.setAccessibleName("Marker size")
        self.marker_scale_label = QLabel(f"{view._marker_scale_percent}%", option_page)
        self.marker_scale_slider.valueChanged.connect(view._set_marker_scale)
        self.marker_scale_slider.valueChanged.connect(
            lambda value: self.marker_scale_label.setText(f"{value}%")
        )
        marker_scale_layout = QHBoxLayout()
        marker_scale_layout.addWidget(self.marker_scale_slider, 1)
        marker_scale_layout.addWidget(self.marker_scale_label)
        plot_form.addRow("Marker size", marker_scale_layout)
        plot_layout.addLayout(plot_form)
        self.axis_ranges_button = QPushButton("Axis ranges...", option_page)
        self.axis_ranges_button.setObjectName("axisRangesButton")
        self.axis_ranges_button.setEnabled(view._use_pyqtgraph and not view._is_smith)
        self.axis_ranges_button.clicked.connect(view._open_axis_settings)
        plot_layout.addWidget(self.axis_ranges_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.remove_logo_checkbox = QCheckBox("Remove EMERGE Logo", option_page)
        self.remove_logo_checkbox.setObjectName("removeEmergeLogoCheckBox")
        self.remove_logo_checkbox.toggled.connect(view._set_plot_logo_removed)
        plot_layout.addWidget(self.remove_logo_checkbox, alignment=Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(option_page, "Option")

        equation_page = QWidget(self.tabs)
        equation_layout = QVBoxLayout(equation_page)
        equation_layout.addWidget(QLabel(
            "Create calculated traces from parameters plotted on this chart.",
            equation_page,
        ))
        equation_split = QHBoxLayout()
        self.equations_list = QListWidget(equation_page)
        self.equations_list.setObjectName("equationsList")
        self.equations_list.setMinimumWidth(190)
        equation_split.addWidget(self.equations_list, 1)
        equation_editor = QWidget(equation_page)
        editor_layout = QVBoxLayout(equation_editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        equation_form = QFormLayout()
        self.equation_name_edit = QLineEdit(equation_editor)
        self.equation_name_edit.setObjectName("equationNameEdit")
        self.equation_name_edit.setPlaceholderText("e.g. S11_power")
        equation_form.addRow("Result name", self.equation_name_edit)
        self.equation_formula_edit = QLineEdit(equation_editor)
        self.equation_formula_edit.setObjectName("equationFormulaEdit")
        self.equation_formula_edit.setPlaceholderText("e.g. abs(s11) ** 2")
        self.equation_formula_edit.setToolTip(
            "Use +, -, *, /, **, %, parentheses and numeric functions such as abs, real, imag, sqrt, log10, sin, and cos."
        )
        equation_form.addRow("Formula", self.equation_formula_edit)
        editor_layout.addLayout(equation_form)

        variable_row = QHBoxLayout()
        self.equation_parameter_combo = QComboBox(equation_editor)
        self.equation_parameter_combo.setObjectName("equationParameterCombo")
        self.equation_parameter_combo.setToolTip(
            "Select a trace from any dataset currently loaded on this chart."
        )
        variable_row.addWidget(self.equation_parameter_combo, 1)
        self.equation_variable_name_edit = QLineEdit(equation_editor)
        self.equation_variable_name_edit.setObjectName("equationVariableNameEdit")
        self.equation_variable_name_edit.setPlaceholderText("Variable")
        self.equation_variable_name_edit.setMaximumWidth(100)
        variable_row.addWidget(self.equation_variable_name_edit)
        self.add_equation_variable_button = QPushButton("Add variable", equation_editor)
        self.add_equation_variable_button.setObjectName("addEquationVariableButton")
        variable_row.addWidget(self.add_equation_variable_button)
        editor_layout.addLayout(variable_row)
        self.equation_variables_list = QListWidget(equation_editor)
        self.equation_variables_list.setObjectName("equationVariablesList")
        self.equation_variables_list.setMaximumHeight(92)
        self.equation_variables_list.setToolTip(
            "Select a variable and use Remove selected variable to remove it."
        )
        editor_layout.addWidget(self.equation_variables_list)
        self.remove_equation_variable_button = QPushButton("Remove selected variable", equation_editor)
        self.remove_equation_variable_button.setObjectName("removeEquationVariableButton")
        editor_layout.addWidget(
            self.remove_equation_variable_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        constant_row = QHBoxLayout()
        self.equation_constant_name_edit = QLineEdit(equation_editor)
        self.equation_constant_name_edit.setObjectName("equationConstantNameEdit")
        self.equation_constant_name_edit.setPlaceholderText("Constant")
        self.equation_constant_name_edit.setMaximumWidth(100)
        constant_row.addWidget(self.equation_constant_name_edit)
        self.equation_constant_value_edit = QLineEdit(equation_editor)
        self.equation_constant_value_edit.setObjectName("equationConstantValueEdit")
        self.equation_constant_value_edit.setPlaceholderText("Numeric value")
        self.equation_constant_value_edit.setMaximumWidth(120)
        constant_row.addWidget(self.equation_constant_value_edit)
        self.add_equation_constant_button = QPushButton("Add constant", equation_editor)
        self.add_equation_constant_button.setObjectName("addEquationConstantButton")
        constant_row.addWidget(self.add_equation_constant_button)
        editor_layout.addLayout(constant_row)
        self.equation_constants_list = QListWidget(equation_editor)
        self.equation_constants_list.setObjectName("equationConstantsList")
        self.equation_constants_list.setMaximumHeight(72)
        self.equation_constants_list.setToolTip(
            "Select a constant and use Remove selected constant to remove it."
        )
        editor_layout.addWidget(self.equation_constants_list)
        self.remove_equation_constant_button = QPushButton("Remove selected constant", equation_editor)
        self.remove_equation_constant_button.setObjectName("removeEquationConstantButton")
        editor_layout.addWidget(
            self.remove_equation_constant_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        equation_buttons = QHBoxLayout()
        self.new_equation_button = QPushButton("New", equation_editor)
        self.new_equation_button.setObjectName("newEquationButton")
        self.save_equation_button = QPushButton("Add / Update equation", equation_editor)
        self.save_equation_button.setObjectName("saveEquationButton")
        self.remove_equation_button = QPushButton("Remove equation", equation_editor)
        self.remove_equation_button.setObjectName("removeEquationButton")
        equation_buttons.addWidget(self.new_equation_button)
        equation_buttons.addWidget(self.save_equation_button)
        equation_buttons.addWidget(self.remove_equation_button)
        editor_layout.addLayout(equation_buttons)
        self.equation_error_label = QLabel(equation_editor)
        self.equation_error_label.setObjectName("equationErrorLabel")
        self.equation_error_label.setWordWrap(True)
        self.equation_error_label.setStyleSheet("color: #b42318;")
        self.equation_error_label.hide()
        editor_layout.addWidget(self.equation_error_label)
        equation_split.addWidget(equation_editor, 2)
        equation_layout.addLayout(equation_split, 1)
        self.tabs.addTab(equation_page, "Equation")
        self._equation_editor_variables: list[dict[str, str]] = []
        self._equation_editor_constants: dict[str, float] = {}
        self.add_equation_variable_button.clicked.connect(view._add_equation_variable)
        self.add_equation_constant_button.clicked.connect(view._add_equation_constant)
        self.new_equation_button.clicked.connect(view._new_equation_editor)
        self.equations_list.currentRowChanged.connect(view._load_equation_editor)
        self.save_equation_button.clicked.connect(view._save_equation_from_editor)
        self.remove_equation_button.clicked.connect(view._remove_equation_from_editor)
        self.remove_equation_variable_button.clicked.connect(
            lambda: view._remove_selected_equation_item(self.equation_variables_list)
        )
        self.remove_equation_constant_button.clicked.connect(
            lambda: view._remove_selected_equation_item(self.equation_constants_list)
        )

        self.add_trace_button.clicked.connect(lambda: view._move_trace_items(self.available_list, True))
        self.remove_trace_button.clicked.connect(lambda: view._move_trace_items(self.selected_list, False))
        self.available_list.itemDoubleClicked.connect(
            lambda _item: view._move_trace_items(self.available_list, True)
        )
        self.selected_list.itemDoubleClicked.connect(
            lambda _item: view._move_trace_items(self.selected_list, False)
        )
        self.selected_list.currentItemChanged.connect(view._on_selected_trace_changed)
        self.move_trace_up_button.clicked.connect(lambda: view._move_selected_trace(-1))
        self.move_trace_down_button.clicked.connect(lambda: view._move_selected_trace(1))
        self.configure_trace_button.clicked.connect(view._configure_selected_trace)
        self.trace_search.textChanged.connect(view._filter_trace_lists)
        self.dataset_combo.currentIndexChanged.connect(view._populate_available_traces)
        self.dataset_combo.currentIndexChanged.connect(view._update_remove_dataset_button)
        self.remove_dataset_button.clicked.connect(view._remove_selected_dataset)
        self.available_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.selected_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.available_list.customContextMenuRequested.connect(
            lambda position: view._show_trace_list_context_menu(self.available_list, position)
        )
        self.selected_list.customContextMenuRequested.connect(
            lambda position: view._show_trace_list_context_menu(self.selected_list, position)
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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
    append_mode_changed = Signal(bool)

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
        settings_identity = "\x1f".join((self.plot_type, self._settings_key))
        settings_digest = hashlib.sha256(settings_identity.encode("utf-8")).hexdigest()
        self._chart_settings_prefix = f"plots/chart_options/{settings_digest}"
        self._is_smith = self.plot_type == "smith"
        self._is_vswr = self.plot_type == "plot_vswr"
        self._is_farfield = self.plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}
        self._is_polar = self.plot_type == "plot_ff_polar"
        self._use_pyqtgraph = not self._is_farfield
        self._x_values = np.asarray([], dtype=float)
        self._series_data: list[dict[str, Any]] = []
        self._equations: list[dict[str, Any]] = []
        self._equation_errors: list[str] = []
        self._overlay_file_ids: set[str] = set()
        self._appended_file_ids: set[str] = set()
        self._base_file_ids: set[str] = set()
        self._trace_items: dict[tuple[str, str], QListWidgetItem] = {}
        self._append_sequence = 0
        self._curve_items = []
        self._smith_grid_items = []
        self._marker_items = []
        self._plot_logo_item: QGraphicsPixmapItem | None = None
        self._plot_logo_source: QPixmap | None = None
        self._legend_item: pg.LegendItem | None = None
        self._legend_position: tuple[float, float] | None = None
        self._legend_moved = False
        self._legend_scale_percent = 100
        self._marker_scale_percent = 100
        self._remove_logo = False
        self._marker_sequence = 0
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

        self.x_scale_combo = QComboBox(self)
        self.x_scale_combo.setObjectName("xScaleCombo")
        self.x_scale_combo.addItems(["Linear", "Log"])
        self._last_x_log = self.x_scale_combo.currentText() == "Log"
        if self._is_smith or self._is_polar:
            self.x_scale_combo.setEnabled(False)

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
        self._restore_chart_settings()

        self._settings_dialog = _PlotSettingsDialog(self)
        self._available_trace_list = self._settings_dialog.available_list
        self._selected_trace_list = self._settings_dialog.selected_list
        self.load_touchstone_button = self._settings_dialog.load_touchstone_button
        self._refresh_equation_list()
        logo_checkbox = self._settings_dialog.remove_logo_checkbox
        logo_checkbox.blockSignals(True)
        logo_checkbox.setChecked(self._remove_logo)
        logo_checkbox.blockSignals(False)

        control_layout = QHBoxLayout()
        control_layout.setSpacing(4)
        self.settings_button = self._make_action_button(
            "settings",
            "Settings", "settingsButton", self._open_settings,
        )
        self.fit_view_button = self._make_action_button(
            "fit",
            "Fit", "fitViewButton", self._fit_view,
        )
        self.add_marker_button = self._make_action_button(
            "marker-add",
            "Add Marker", "addMarkerButton", self._add_marker_at_center,
        )
        self.remove_markers_button = self._make_action_button(
            "marker-remove",
            "Remove Markers", "removeMarkersButton", self._clear_markers,
        )
        self.append_button = self._make_action_button(
            "chart-add",
            "Append subsequent simulations to this chart",
            "appendToChartButton", lambda _checked=False: None,
        )
        self.append_button.setCheckable(True)
        self.append_button.setToolTip("Append subsequent simulations to this chart")
        self.append_button.setAccessibleName("Append to Chart")
        self.append_button.toggled.connect(self.append_mode_changed.emit)
        self.clear_markers_button = self.remove_markers_button
        marker_actions_enabled = self._use_pyqtgraph and not self._is_smith
        self.add_marker_button.setEnabled(marker_actions_enabled)
        self.remove_markers_button.setEnabled(marker_actions_enabled)
        self._action_buttons = (
            self.settings_button,
            self.fit_view_button,
            self.add_marker_button,
            self.remove_markers_button,
            self.append_button,
        )
        for button in self._action_buttons:
            control_layout.addWidget(button)
        control_layout.addStretch(1)
        root_layout.addLayout(control_layout)

        self._mdi_area = _ChartMdiArea(self)
        self._mdi_area.setObjectName("chartMdiArea")
        self._mdi_area.setFrameShape(QFrame.Shape.NoFrame)
        self._mdi_area.setMinimumSize(380, 300)
        self._mdi_area.setViewMode(QMdiArea.ViewMode.SubWindowView)
        self._mdi_area.setOption(QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation, True)
        self._chart_background = QColor(_CHART_SURFACE_COLOR)
        self._mdi_area.setBackground(QBrush(self._chart_background))
        root_layout.addWidget(self._mdi_area, 1)

        self._chart_subwindow = _ChartSubWindow()
        self._chart_subwindow.setObjectName("chartSubWindow")

        self._chart_surface = _ChartSurface()
        self._canvas_scroll = QScrollArea(self._chart_surface)
        self._canvas_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._canvas_scroll.setWidgetResizable(True)
        if self._use_pyqtgraph:
            self.plot_widget = pg.PlotWidget(self, background=_CHART_SURFACE_COLOR)
            self.plot_widget.setAntialiasing(True)
            self.plot_widget.showGrid(x=True, y=True, alpha=0.22)
            self.plot_widget.setMenuEnabled(True)
            self.plot_widget.getPlotItem().layout.setContentsMargins(8, 8, 8, 8)
            self.axes = self.plot_widget.getPlotItem()
            self.figure = None
            self.canvas = self.plot_widget
            self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._canvas_scroll.setWidget(self.plot_widget)
            self.plot_widget.scene().sigMouseClicked.connect(self._on_plot_scene_clicked)
            self._initialize_plot_logo()
        else:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self.figure = Figure(figsize=(7, 4))
            self.axes = self.figure.add_subplot(111, projection="polar" if self._is_polar else None)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._canvas_scroll.setWidget(self.canvas)

        self._chart_surface.layout().addWidget(self._canvas_scroll, 1)
        self._chart_subwindow.setWidget(self._chart_surface)
        self._mdi_area.addSubWindow(self._chart_subwindow)
        self._mdi_area.chart_subwindow = self._chart_subwindow
        self._chart_subwindow.showNormal()

        self._overlay_host = self.plot_widget.viewport() if self._use_pyqtgraph else self.canvas
        self._legend_panel = None
        self._external_legend = None
        if not self._use_pyqtgraph:
            self._legend_panel = _DraggableLegendPanel(self._overlay_host)
            self._external_legend = self._legend_panel.list
            self._external_legend.setFrameShape(QFrame.Shape.NoFrame)
            self._external_legend.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._legend_panel.position_changed.connect(self._remember_legend_position)
            self._legend_panel.resize(220, 190)
            self._legend_panel.hide()
        self._overlay_host_size = self._overlay_host.size()
        self._overlay_host.installEventFilter(self)
        self._mdi_area.resized.connect(self._position_overlay_widgets)

        self.display_mode_combo.currentTextChanged.connect(self._on_display_mode_changed)
        if self._use_pyqtgraph and not self._is_smith:
            self.x_scale_combo.currentTextChanged.connect(self._on_axis_scale_changed)
            self.y_scale_combo.currentTextChanged.connect(self._on_axis_scale_changed)
        elif not self._use_pyqtgraph:
            self.x_scale_combo.currentTextChanged.connect(self._on_matplotlib_x_scale_changed)

        self._restore_markers()

    def _restore_markers(self) -> None:
        entries = getattr(self, "_pending_marker_positions", [])
        self._pending_marker_positions = []
        if not entries or not self._use_pyqtgraph or self._is_smith:
            return
        for entry in entries:
            self._add_marker(entry["x"])
            if entry.get("manual") and entry.get("pos") is not None:
                self._marker_items[-1][1].setPos(*entry["pos"])

    def _initialize_plot_logo(self) -> None:
        logo_path = _chart_logo_path()
        if logo_path is not None:
            source = QPixmap(str(logo_path))
            if not source.isNull():
                self._plot_logo_source = source
                self._plot_logo_item = QGraphicsPixmapItem(source)
                self._plot_logo_item.setZValue(1000)
                self._plot_logo_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                scene = self.plot_widget.scene()
                scene.addItem(self._plot_logo_item)
                self._plot_logo_item.setVisible(not self._remove_logo)
                view_box = self.plot_widget.getPlotItem().getViewBox()
                view_box.sigResized.connect(self._position_plot_logo)
                self._position_plot_logo()
        self._settings_dialog.remove_logo_checkbox.setEnabled(self._plot_logo_item is not None)

    @staticmethod
    def _setting_bool(value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return fallback

    @staticmethod
    def _setting_range(value: Any, logarithmic: bool) -> tuple[float, float] | None:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return None
        if any(isinstance(bound, bool) for bound in value):
            return None
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(low) or not math.isfinite(high) or low >= high:
            return None
        if logarithmic and low <= 0:
            return None
        return low, high

    def _restore_chart_settings(self) -> None:
        prefix = self._chart_settings_prefix
        settings = self._trace_color_settings

        display_mode = str(settings.value(f"{prefix}/display_mode", ""))
        if self.display_mode_combo.isEnabled() and self.display_mode_combo.findText(display_mode) >= 0:
            self.display_mode_combo.setCurrentText(display_mode)

        x_scale = str(settings.value(f"{prefix}/x_scale", ""))
        if self.x_scale_combo.isEnabled() and self.x_scale_combo.findText(x_scale) >= 0:
            self.x_scale_combo.setCurrentText(x_scale)

        y_scale = str(settings.value(f"{prefix}/y_scale", ""))
        if (
            self._use_pyqtgraph
            and not self._is_smith
            and self.display_mode_combo.currentText() != "Magnitude (dB)"
            and self.y_scale_combo.findText(y_scale) >= 0
        ):
            self.y_scale_combo.setCurrentText(y_scale)
        elif self.display_mode_combo.currentText() == "Magnitude (dB)":
            self.y_scale_combo.setCurrentText("Linear")
        self.y_scale_combo.setEnabled(
            self._use_pyqtgraph
            and not self._is_smith
            and self.display_mode_combo.currentText() != "Magnitude (dB)"
        )

        x_log = self.x_scale_combo.currentText() == "Log"
        y_log = self.y_scale_combo.currentText() == "Log"
        x_range = self._setting_range(settings.value(f"{prefix}/x_range", None), x_log)
        y_range = self._setting_range(settings.value(f"{prefix}/y_range", None), y_log)
        x_auto = self._setting_bool(settings.value(f"{prefix}/x_auto_range", True), True)
        y_auto = self._setting_bool(settings.value(f"{prefix}/y_auto_range", True), True)
        self._x_auto_range = x_auto or x_range is None
        self._y_auto_range = y_auto or y_range is None
        self._x_range = None if self._x_auto_range else x_range
        self._y_range = None if self._y_auto_range else y_range

        raw_legend_scale = settings.value(f"{prefix}/legend_scale_percent", 100)
        try:
            legend_scale = int(raw_legend_scale)
            if isinstance(raw_legend_scale, float) and not raw_legend_scale.is_integer():
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            legend_scale = 100
        self._legend_scale_percent = legend_scale if 50 <= legend_scale <= 100 else 100
        self._legend_position = self._setting_legend_position(
            settings.value(f"{prefix}/legend_position", None)
        )
        self._legend_moved = self._legend_position is not None
        raw_marker_scale = settings.value(f"{prefix}/marker_scale_percent", 100)
        try:
            marker_scale = int(raw_marker_scale)
            if isinstance(raw_marker_scale, float) and not raw_marker_scale.is_integer():
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            marker_scale = 100
        self._marker_scale_percent = marker_scale if 50 <= marker_scale <= 200 else 100
        self._remove_logo = self._setting_bool(
            settings.value(f"{prefix}/remove_logo", False), False
        )
        self._pending_marker_positions = self._setting_marker_positions(
            settings.value(f"{prefix}/marker_positions", "")
        )
        self._equations = self._setting_equations(
            settings.value(f"{prefix}/equations", "")
        )

    @staticmethod
    def _setting_legend_position(value: Any) -> tuple[float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        return x, y

    @staticmethod
    def _setting_marker_positions(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            raw_entries = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(raw_entries, list):
            return []
        entries: list[dict[str, Any]] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            try:
                x_value = float(raw_entry.get("x"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(x_value):
                continue
            pos = None
            raw_pos = raw_entry.get("pos")
            if isinstance(raw_pos, (list, tuple)) and len(raw_pos) == 2:
                try:
                    pos_x, pos_y = float(raw_pos[0]), float(raw_pos[1])
                except (TypeError, ValueError):
                    pos_x = pos_y = None
                if (
                    pos_x is not None
                    and pos_y is not None
                    and math.isfinite(pos_x)
                    and math.isfinite(pos_y)
                ):
                    pos = (pos_x, pos_y)
            entries.append({
                "x": x_value,
                "manual": bool(raw_entry.get("manual")) and pos is not None,
                "pos": pos,
            })
        return entries

    @staticmethod
    def _setting_equations(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            definitions = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(definitions, list):
            return []
        equations: list[dict[str, Any]] = []
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            name = definition.get("name")
            expression = definition.get("expression")
            raw_variables = definition.get("variables")
            raw_constants = definition.get("constants")
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(expression, str) or not expression.strip()
                or not isinstance(raw_variables, list)
                or not isinstance(raw_constants, dict)
            ):
                continue
            variables = []
            for variable in raw_variables:
                if not isinstance(variable, dict):
                    continue
                variable_name = variable.get("name")
                file_id = variable.get("file_id")
                label = variable.get("label")
                if (
                    isinstance(variable_name, str) and _IDENTIFIER.fullmatch(variable_name)
                    and isinstance(file_id, str) and file_id
                    and isinstance(label, str) and label
                ):
                    variables.append({
                        "name": variable_name,
                        "file_id": file_id,
                        "label": label,
                    })
            constants: dict[str, float] = {}
            for constant_name, raw_value in raw_constants.items():
                if not isinstance(constant_name, str) or not _IDENTIFIER.fullmatch(constant_name):
                    continue
                try:
                    constant_value = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if (
                    math.isfinite(constant_value)
                    and constant_name not in _EQUATION_FUNCTIONS
                    and constant_name not in {"pi", "e"}
                ):
                    constants[constant_name] = constant_value
            equations.append({
                "name": name.strip(),
                "expression": expression.strip(),
                "variables": variables,
                "constants": constants,
            })
        return equations

    def _save_chart_settings(self) -> None:
        prefix = self._chart_settings_prefix
        settings = self._trace_color_settings
        settings.setValue(f"{prefix}/display_mode", self.display_mode_combo.currentText())
        settings.setValue(f"{prefix}/x_scale", self.x_scale_combo.currentText())
        settings.setValue(f"{prefix}/y_scale", self.y_scale_combo.currentText())
        settings.setValue(f"{prefix}/x_auto_range", self._x_auto_range)
        settings.setValue(f"{prefix}/y_auto_range", self._y_auto_range)
        settings.setValue(f"{prefix}/x_range", None if self._x_auto_range else list(self._x_range or ()))
        settings.setValue(f"{prefix}/y_range", None if self._y_auto_range else list(self._y_range or ()))
        settings.setValue(f"{prefix}/legend_scale_percent", self._legend_scale_percent)
        settings.setValue(f"{prefix}/marker_scale_percent", self._marker_scale_percent)
        settings.setValue(f"{prefix}/remove_logo", self._remove_logo)
        settings.setValue(f"{prefix}/equations", json.dumps(self._equations))
        if self._legend_position is not None:
            settings.setValue(f"{prefix}/legend_position", list(self._legend_position))
        settings.sync()

    @staticmethod
    def _equation_reserved_names() -> set[str]:
        return set(_EQUATION_FUNCTIONS) | {"pi", "e"}

    def _set_equation_error(self, message: str) -> None:
        label = self._settings_dialog.equation_error_label
        label.setText(message)
        label.setVisible(bool(message))
        label.setToolTip(message)

    def _refresh_equation_list(self, selected_row: int | None = None) -> None:
        if not hasattr(self, "_settings_dialog"):
            return
        widget = self._settings_dialog.equations_list
        widget.blockSignals(True)
        widget.clear()
        for equation in self._equations:
            item = QListWidgetItem(equation["name"])
            item.setToolTip(equation["expression"])
            widget.addItem(item)
        if selected_row is not None and 0 <= selected_row < widget.count():
            widget.setCurrentRow(selected_row)
        widget.blockSignals(False)
        self._sync_equation_source_combo()
        self._load_equation_editor(widget.currentRow())

    def _sync_equation_source_combo(self) -> None:
        if not hasattr(self, "_settings_dialog"):
            return
        combo = self._settings_dialog.equation_parameter_combo
        current_source = combo.currentData()
        sources = [
            entry for entry in self._series_data
            if entry["file_id"] != _EQUATION_FILE_ID
        ]
        combo.blockSignals(True)
        combo.clear()
        for entry in sources:
            combo.addItem(
                f"{entry['file_name']} — {entry['label']}",
                (entry["file_id"], entry["label"]),
            )
        restored_index = combo.findData(current_source)
        if restored_index >= 0:
            combo.setCurrentIndex(restored_index)
        combo.blockSignals(False)

    def _render_equation_editor_mappings(self) -> None:
        dialog = self._settings_dialog
        dialog.equation_variables_list.clear()
        for variable in dialog._equation_editor_variables:
            source = next(
                (
                    entry for entry in self._series_data
                    if entry["file_id"] == variable["file_id"]
                    and entry["label"] == variable["label"]
                ),
                None,
            )
            source_name = source["file_name"] if source is not None else variable["file_id"]
            item = QListWidgetItem(
                f"{variable['name']} ← {source_name} — {variable['label']}"
            )
            item.setData(Qt.ItemDataRole.UserRole, variable)
            dialog.equation_variables_list.addItem(item)
        dialog.equation_constants_list.clear()
        for name, value in dialog._equation_editor_constants.items():
            item = QListWidgetItem(f"{name} = {value:g}")
            item.setData(Qt.ItemDataRole.UserRole, (name, value))
            dialog.equation_constants_list.addItem(item)

    def _add_equation_variable(self) -> None:
        dialog = self._settings_dialog
        name = dialog.equation_variable_name_edit.text().strip()
        source = dialog.equation_parameter_combo.currentData()
        if not _IDENTIFIER.fullmatch(name):
            self._set_equation_error("Enter a valid variable name (letters, digits, and underscores; not starting with a digit).")
            return
        if name in self._equation_reserved_names():
            self._set_equation_error(f"'{name}' is reserved for a formula function or constant.")
            return
        if name in dialog._equation_editor_constants:
            self._set_equation_error(f"'{name}' is already defined as a constant.")
            return
        if any(variable["name"] == name for variable in dialog._equation_editor_variables):
            self._set_equation_error(f"Variable '{name}' is already defined.")
            return
        if not isinstance(source, (tuple, list)) or len(source) != 2:
            self._set_equation_error("Load or plot a parameter before adding a variable.")
            return
        file_id, label = str(source[0]), str(source[1])
        dialog._equation_editor_variables.append({
            "name": name,
            "file_id": file_id,
            "label": label,
        })
        dialog.equation_variable_name_edit.clear()
        self._render_equation_editor_mappings()
        self._set_equation_error("")

    def _add_equation_constant(self) -> None:
        dialog = self._settings_dialog
        name = dialog.equation_constant_name_edit.text().strip()
        if not _IDENTIFIER.fullmatch(name):
            self._set_equation_error("Enter a valid constant name (letters, digits, and underscores; not starting with a digit).")
            return
        if name in self._equation_reserved_names():
            self._set_equation_error(f"'{name}' is reserved for a formula function or constant.")
            return
        if any(variable["name"] == name for variable in dialog._equation_editor_variables):
            self._set_equation_error(f"'{name}' is already defined as a parameter variable.")
            return
        try:
            value = float(dialog.equation_constant_value_edit.text())
        except (TypeError, ValueError, OverflowError):
            self._set_equation_error("Enter a numeric constant value.")
            return
        if not math.isfinite(value):
            self._set_equation_error("Constant values must be finite.")
            return
        dialog._equation_editor_constants[name] = value
        dialog.equation_constant_name_edit.clear()
        dialog.equation_constant_value_edit.clear()
        self._render_equation_editor_mappings()
        self._set_equation_error("")

    def _load_equation_editor(self, row: int) -> None:
        dialog = self._settings_dialog
        if row < 0 or row >= len(self._equations):
            dialog.equation_name_edit.clear()
            dialog.equation_formula_edit.clear()
            dialog._equation_editor_variables = []
            dialog._equation_editor_constants = {}
            dialog.remove_equation_button.setEnabled(False)
        else:
            equation = self._equations[row]
            dialog.equation_name_edit.setText(equation["name"])
            dialog.equation_formula_edit.setText(equation["expression"])
            dialog._equation_editor_variables = [
                dict(variable) for variable in equation["variables"]
            ]
            dialog._equation_editor_constants = dict(equation["constants"])
            dialog.remove_equation_button.setEnabled(True)
        self._render_equation_editor_mappings()
        self._set_equation_error("")

    def _new_equation_editor(self) -> None:
        self._settings_dialog.equations_list.clearSelection()
        self._settings_dialog.equations_list.setCurrentRow(-1)
        self._load_equation_editor(-1)

    def _remove_selected_equation_item(self, widget: QListWidget) -> None:
        if not widget.selectedItems():
            return
        selected = widget.currentItem()
        if selected is None:
            return
        dialog = self._settings_dialog
        if widget is dialog.equation_variables_list:
            variable = selected.data(Qt.ItemDataRole.UserRole)
            dialog._equation_editor_variables = [
                item for item in dialog._equation_editor_variables if item != variable
            ]
        else:
            name, _value = selected.data(Qt.ItemDataRole.UserRole)
            dialog._equation_editor_constants.pop(name, None)
        self._render_equation_editor_mappings()

    def _save_equation_from_editor(self) -> None:
        dialog = self._settings_dialog
        name = dialog.equation_name_edit.text().strip()
        expression = dialog.equation_formula_edit.text().strip()
        if not name:
            self._set_equation_error("Enter a name for the calculated trace.")
            return
        if not expression:
            self._set_equation_error("Enter a formula for the calculated trace.")
            return
        variable_names = [item["name"] for item in dialog._equation_editor_variables]
        if len(variable_names) != len(set(variable_names)):
            self._set_equation_error("Each parameter variable must have a unique name.")
            return
        constants = dialog._equation_editor_constants
        if set(variable_names) & set(constants):
            self._set_equation_error("Parameter variables and constants must have unique names.")
            return
        selected_row = dialog.equations_list.currentRow()
        if any(
            index != selected_row and equation["name"].casefold() == name.casefold()
            for index, equation in enumerate(self._equations)
        ):
            self._set_equation_error(f"An equation named '{name}' already exists.")
            return
        try:
            _parse_equation_expression(
                expression,
                set(variable_names) | set(constants),
            )
        except ValueError as exc:
            self._set_equation_error(str(exc))
            return
        definition = {
            "name": name,
            "expression": expression,
            "variables": [dict(item) for item in dialog._equation_editor_variables],
            "constants": dict(constants),
        }
        if selected_row < 0:
            self._equations.append(definition)
            selected_row = len(self._equations) - 1
        else:
            self._equations[selected_row] = definition
        self._equations_changed(selected_row)

    def _remove_equation_from_editor(self) -> None:
        row = self._settings_dialog.equations_list.currentRow()
        if row < 0 or row >= len(self._equations):
            return
        del self._equations[row]
        next_row = min(row, len(self._equations) - 1) if self._equations else None
        self._equations_changed(next_row)

    def _equations_changed(self, selected_row: int | None = None) -> None:
        self._refresh_equation_list(selected_row)
        self._update_data(self._x_values, self._series_data)
        self._redraw(reset_range=True)
        self._save_chart_settings()

    def _calculate_equation_series(
        self,
        source_series: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        sources = {
            (entry["file_id"], entry["label"]): entry
            for entry in source_series
            if entry["file_id"] != _EQUATION_FILE_ID
        }
        calculated: list[dict[str, Any]] = []
        errors = []
        for equation in self._equations:
            try:
                variables: dict[str, Any] = {}
                x_values = None
                for variable in equation["variables"]:
                    key = (variable["file_id"], variable["label"])
                    source = sources.get(key)
                    if source is None:
                        raise ValueError(
                            f"parameter '{variable['label']}' from its selected dataset is not loaded"
                        )
                    source_x = np.asarray(source["x_values"], dtype=float).reshape(-1)
                    if x_values is None:
                        x_values = source_x
                    elif source_x.shape != x_values.shape or not np.array_equal(source_x, x_values):
                        raise ValueError("all parameter variables must have the same X values")
                    variables[variable["name"]] = source["values"]
                variables.update(equation["constants"])
                if x_values is None:
                    x_values = np.asarray(self._x_values, dtype=float).reshape(-1)
                    if not len(x_values):
                        raise ValueError("add a parameter variable to provide the chart X axis")
                values = _evaluate_equation_expression(equation["expression"], variables)
                if values.size == 1 and x_values.size != 1:
                    values = np.full(x_values.size, values.item())
                elif values.size != x_values.size:
                    raise ValueError(
                        f"formula produced {values.size} values for {x_values.size} X values"
                    )
                if not np.all(np.isfinite(values)):
                    raise ValueError("formula produced non-finite values")
                calculated.append({
                    "label": equation["name"],
                    "file_name": "Equations",
                    "file_id": _EQUATION_FILE_ID,
                    "x_values": x_values,
                    "values": values,
                    "visible": True,
                    "configured": True,
                })
            except (ArithmeticError, TypeError, ValueError, IndexError, OverflowError) as exc:
                errors.append(f"Equation '{equation['name']}': {exc}")
        self._equation_errors = errors
        self._set_equation_error("\n".join(errors))
        return calculated

    def _save_marker_positions(self) -> None:
        prefix = self._chart_settings_prefix
        settings = self._trace_color_settings
        entries = []
        for line, label in self._marker_items:
            entry: dict[str, Any] = {"x": float(line.value())}
            if label.manually_positioned:
                position = label.pos()
                entry["manual"] = True
                entry["pos"] = [float(position.x()), float(position.y())]
            entries.append(entry)
        settings.setValue(f"{prefix}/marker_positions", json.dumps(entries))
        settings.sync()

    def _appended_series_path(self) -> Path:
        chart_digest = self._chart_settings_prefix.rsplit("/", 1)[-1]
        return _chart_data_cache_root() / f"{chart_digest}.json.zlib"

    def _save_appended_series(self) -> None:
        path = self._appended_series_path()
        appended = [
            entry for entry in self._series_data
            if entry["file_id"] in self._appended_file_ids
        ]
        if not appended:
            if path.exists():
                path.unlink()
            return

        serialized = []
        for entry in appended:
            values = np.asarray(entry["values"]).reshape(-1)
            if np.iscomplexobj(values):
                encoded_values = [
                    [float(value.real), float(value.imag)] for value in values
                ]
            else:
                encoded_values = np.asarray(values, dtype=float).tolist()
            serialized.append({
                "file_id": entry["file_id"],
                "label": entry["label"],
                "file_name": entry["file_name"],
                "configured": bool(entry["configured"]),
                "x_values": np.asarray(entry["x_values"], dtype=float).tolist(),
                "complex": bool(np.iscomplexobj(values)),
                "values": encoded_values,
            })

        payload = json.dumps(
            {"version": 1, "series": serialized},
            separators=(",", ":"),
        ).encode("utf-8")
        compressed = zlib.compress(payload, level=9)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(compressed)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _load_appended_series(self) -> list[dict[str, Any]]:
        path = self._appended_series_path()
        if not path.is_file():
            return []
        try:
            payload = json.loads(zlib.decompress(path.read_bytes()).decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("unsupported appended-series cache version")
            raw_series = payload.get("series")
            if not isinstance(raw_series, list):
                raise ValueError("appended-series cache is not a list")

            restored: list[dict[str, Any]] = []
            for item in raw_series:
                if not isinstance(item, dict):
                    raise ValueError("appended-series entry is not an object")
                file_id = item.get("file_id")
                label = item.get("label")
                if not isinstance(file_id, str) or not file_id:
                    raise ValueError("appended-series entry has no file ID")
                if not isinstance(label, str) or not label:
                    raise ValueError("appended-series entry has no label")

                x_values = np.asarray(item.get("x_values"), dtype=float).reshape(-1)
                encoded_values = item.get("values")
                if item.get("complex") is True:
                    if not isinstance(encoded_values, list) or any(
                        not isinstance(value, list) or len(value) != 2
                        for value in encoded_values
                    ):
                        raise ValueError("complex series values are malformed")
                    values = np.asarray(
                        [
                            complex(float(value[0]), float(value[1]))
                            for value in encoded_values
                        ],
                        dtype=complex,
                    )
                else:
                    values = np.asarray(encoded_values, dtype=float).reshape(-1)
                if len(values) != len(x_values):
                    raise ValueError("series value and x-value counts do not match")

                file_name = item.get("file_name", "")
                restored.append({
                    "file_id": file_id,
                    "label": label,
                    "file_name": file_name if isinstance(file_name, str) else "",
                    "configured": bool(item.get("configured", False)),
                    "x_values": x_values,
                    "values": values,
                })
            return restored
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            zlib.error,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:
            _LOGGER.warning("Could not restore appended chart data from %s: %s", path, exc)
            return []

    def _trace_setting_key(self, file_id: str, label: str, setting: str) -> str:
        identity = "\x1f".join((self._settings_key, file_id, label))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"plots/trace_{setting}/{digest}"

    def _save_trace_settings(self, entry: dict[str, Any]) -> None:
        file_id, label = entry["file_id"], entry["label"]
        settings = self._trace_color_settings
        settings.setValue(self._trace_color_setting_key(file_id, label), entry["color"])
        settings.setValue(self._trace_setting_key(file_id, label, "visibility"), entry["visible"])
        settings.setValue(self._trace_setting_key(file_id, label, "marker"), entry["marker"])
        settings.sync()

    def _set_plot_logo_removed(self, removed: bool) -> None:
        self._remove_logo = bool(removed)
        if self._plot_logo_item is not None:
            self._plot_logo_item.setVisible(not removed)
        self._save_chart_settings()

    def _position_plot_logo(self) -> None:
        if self._plot_logo_item is None or self._plot_logo_source is None:
            return
        plot_bounds = self.plot_widget.getPlotItem().getViewBox().sceneBoundingRect()
        if plot_bounds.width() <= 0 or plot_bounds.height() <= 0:
            return
        logo_size = max(16, min(40, round(plot_bounds.width() * 0.04)))
        pixmap = self._plot_logo_source.scaled(
            logo_size,
            logo_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._plot_logo_item.setPixmap(pixmap)
        self._plot_logo_item.setPos(
            plot_bounds.right() - pixmap.width() - 8,
            plot_bounds.bottom() - pixmap.height() - 8,
        )

    def _remember_legend_position(self, legend=None) -> None:
        if legend is not None and legend.parentItem() is not None:
            bounds = legend.parentItem().boundingRect()
            if bounds.width() > 0 and bounds.height() > 0:
                self._legend_position = (
                    legend.pos().x() / bounds.width(),
                    legend.pos().y() / bounds.height(),
                )
        elif self._legend_panel is not None:
            host = self._overlay_host
            if host.width() > 0 and host.height() > 0:
                self._legend_position = (
                    self._legend_panel.x() / host.width(),
                    self._legend_panel.y() / host.height(),
                )
        self._legend_moved = True
        if self._legend_position is not None:
            self._save_chart_settings()

    def _apply_legend_position(self, legend) -> None:
        if self._legend_position is None or legend.parentItem() is None:
            return
        bounds = legend.parentItem().boundingRect()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        legend.anchor((0, 0), self._legend_position)

    def _position_overlay_widgets(self) -> None:
        host = self._overlay_host
        previous_size = self._overlay_host_size
        scale = _overlay_scale(host)
        if self._legend_panel is not None:
            width = min(
                max(1, host.width()),
                max(
                    round(120 * scale),
                    min(round(240 * scale), round(host.width() * 0.25)),
                ),
            )
            height = min(
                max(1, host.height()),
                min(
                    round(210 * scale),
                    max(
                        round(60 * scale),
                        round(self._external_legend.count() * 22 * scale + 42 * scale),
                    ),
                ),
            )
            self._legend_panel.resize(width, height)
            legend_font = _scaled_overlay_font(host.font(), scale)
            self._legend_panel.handle.setFont(legend_font)
            self._external_legend.setFont(legend_font)
            self._legend_panel.layout().setContentsMargins(
                round(5 * scale), round(4 * scale), round(5 * scale), round(5 * scale)
            )
            self._legend_panel.layout().setSpacing(max(1, round(3 * scale)))
            self._external_legend.setMaximumHeight(round(180 * scale))
            if self._legend_position is not None:
                position = QPoint(
                    round(self._legend_position[0] * host.width()),
                    round(self._legend_position[1] * host.height()),
                )
            elif not getattr(self, "_legend_moved", False):
                position = QPoint(host.width() - width - 12, 12)
            else:
                position = QPoint(self._legend_panel.pos())
                if previous_size.width() > 0 and previous_size.height() > 0:
                    position.setX(round(position.x() * host.width() / previous_size.width()))
                    position.setY(round(position.y() * host.height() / previous_size.height()))
            self._legend_panel.move(
                _bounded_overlay_position(host, self._legend_panel, position)
            )
            self._legend_panel.raise_()
        if self._use_pyqtgraph and self._legend_item is not None:
            self._apply_legend_position(self._legend_item)
        marker_font = _scaled_overlay_font(host.font(), scale * (self._marker_scale_percent / 100.0))
        for marker_line, readout in self._marker_items:
            marker_line.setPen(
                pg.mkPen("#d04a28", width=max(1.0, min(2.0, 1.5 * scale)), style=Qt.PenStyle.DashLine)
            )
            readout.setFont(marker_font)
        symbol_size = max(5.0, min(9.0, 7.0 * scale))
        symbol_width = max(0.8, min(1.4, scale))
        for curve in self._curve_items:
            if curve.opts.get("symbol") is not None:
                curve.setSymbolSize(symbol_size)
                curve.setSymbolPen(
                    pg.mkPen(curve.opts["symbolPen"].color(), width=symbol_width)
                )
        self._overlay_host_size = host.size()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlay_widgets()

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "_overlay_host", None):
            if event.type() == QEvent.Type.Resize:
                self._position_overlay_widgets()
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
        self._base_file_ids = {
            str(item.get("file_id", item.get("file_name", "default")))
            for item in series
        }
        appended_series = self._load_appended_series()
        self._appended_file_ids = {
            str(item["file_id"]) for item in appended_series
        }
        self._overlay_file_ids.update(
            str(item["file_id"]) for item in appended_series
        )
        self._update_data(x_values, [*series, *appended_series])
        self._title = str(title)
        self._xlabel = str(xlabel)
        self._ylabel = str(ylabel)
        self._redraw()
        self._save_appended_series()
        self._save_chart_settings()

    def append_plot_data(
        self,
        x_values: Sequence[float],
        series: Sequence[dict[str, Any]],
        *,
        title: str,
        xlabel: str,
        ylabel: str,
    ) -> None:
        self._progressive_x_range = None
        existing_ids = {entry["file_id"] for entry in self._series_data}
        source_ids = {
            str(item.get("file_id", item.get("file_name", "default")))
            for item in series
        }
        next_sequence = self._append_sequence + 1
        while any(f"{source_id}::append-{next_sequence}" in existing_ids for source_id in source_ids):
            next_sequence += 1
        self._append_sequence = next_sequence
        incoming = []
        for item in series:
            copied = dict(item)
            source_id = str(copied.get("file_id", copied.get("file_name", "default")))
            dataset_name = str(copied.get("file_name", "")).strip() or str(title)
            copied["file_id"] = f"{source_id}::append-{self._append_sequence}"
            copied["file_name"] = dataset_name
            copied["legend_label"] = f"{dataset_name} - {copied['label']}"
            copied["x_values"] = copied.get("x_values", x_values)
            incoming.append(copied)
        self._overlay_file_ids.update(str(item["file_id"]) for item in incoming)
        self._appended_file_ids.update(str(item["file_id"]) for item in incoming)
        self._update_data(self._x_values, [*self._series_data, *incoming])
        if incoming:
            dataset_combo = self._settings_dialog.dataset_combo
            dataset_index = dataset_combo.findData(incoming[-1]["file_id"])
            if dataset_index >= 0:
                dataset_combo.setCurrentIndex(dataset_index)
        self._title = str(title)
        self._xlabel = str(xlabel)
        self._ylabel = str(ylabel)
        self._redraw(reset_range=True)
        self._save_appended_series()
        self._save_chart_settings()

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
        self._overlay_file_ids.update(incoming_ids)
        self._appended_file_ids.update(incoming_ids)
        self._update_data(self._x_values, [*existing, *incoming])
        if series:
            preferred_id = str(series[-1].get("file_id", series[-1].get("file_name", "default")))
            dataset_combo = self._settings_dialog.dataset_combo
            dataset_index = dataset_combo.findData(preferred_id)
            if dataset_index >= 0:
                dataset_combo.setCurrentIndex(dataset_index)
        self._redraw(reset_range=True)
        self._save_appended_series()
        self._save_chart_settings()

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
        incoming_keys = {
            (
                str(item.get("file_id", str(item.get("file_name", "")) or "default")),
                str(item["label"]),
            )
            for item in series
        }
        self._appended_file_ids.update(
            file_id
            for file_id, _label in incoming_keys
            if file_id in self._overlay_file_ids and file_id not in self._base_file_ids
        )
        overlays = [
            item for item in self._series_data
            if item["file_id"] in self._overlay_file_ids
            and (item["file_id"], item["label"]) not in incoming_keys
        ]
        self._update_data(x_values, [*overlays, *series])
        self._title = str(title)
        self._xlabel = str(xlabel)
        self._ylabel = str(ylabel)
        self._redraw()
        self._save_appended_series()
        self._save_chart_settings()

    def clear_data(self) -> None:
        self._progressive_x_range = None
        self._x_values = np.asarray([], dtype=float)
        self._series_data = []
        self._overlay_file_ids.clear()
        self._appended_file_ids.clear()
        self._base_file_ids.clear()
        self._calculate_equation_series([])
        if self._use_pyqtgraph:
            self._clear_markers()
        self._sync_series_controls()
        self._redraw(reset_range=True)
        self._save_appended_series()

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
            if str(item.get("file_id", "")) == _EQUATION_FILE_ID:
                continue
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
            saved_visible = self._setting_bool(
                self._trace_color_settings.value(
                    self._trace_setting_key(file_id, label, "visibility"),
                    item.get("visible", True),
                ),
                bool(item.get("visible", True)),
            )
            saved_marker = self._trace_color_settings.value(
                self._trace_setting_key(file_id, label, "marker"), ""
            )
            if saved_marker not in _MARKERS.values():
                saved_marker = ""
            palette_color = self._normalize_color(_TRACE_PALETTE[index % len(_TRACE_PALETTE)])
            current_color = (
                old_style["color"] if old_style is not None
                else saved_color or palette_color
            )
            current_color = self._normalize_color(current_color, palette_color)
            legend_label = f"{label} - {file_name}" if file_name else label
            updated.append({
                "label": label,
                "legend_label": str(legend_label),
                "file_name": file_name or "Results",
                "file_id": file_id,
                "x_values": series_x,
                "values": values,
                "visible": old_style["visible"] if old_style is not None else saved_visible,
                "configured": bool(item.get("configured", False)),
                "color": current_color,
                "marker": old_style["marker"] if old_style is not None else saved_marker,
            })
        equation_start_index = len(updated)
        for equation_index, item in enumerate(self._calculate_equation_series(updated)):
            label = item["label"]
            file_id = item["file_id"]
            key = (file_id, label)
            old_style = previous.get(key)
            saved_color = str(
                self._trace_color_settings.value(
                    self._trace_color_setting_key(file_id, label), ""
                )
            )
            if not QColor(saved_color).isValid():
                saved_color = ""
            saved_visible = self._setting_bool(
                self._trace_color_settings.value(
                    self._trace_setting_key(file_id, label, "visibility"), True
                ),
                True,
            )
            saved_marker = self._trace_color_settings.value(
                self._trace_setting_key(file_id, label, "marker"), ""
            )
            if saved_marker not in _MARKERS.values():
                saved_marker = ""
            palette_color = self._normalize_color(
                _TRACE_PALETTE[
                    (equation_start_index + equation_index) % len(_TRACE_PALETTE)
                ]
            )
            color = old_style["color"] if old_style is not None else saved_color or palette_color
            updated.append({
                **item,
                "legend_label": f"{label} - Equations",
                "visible": old_style["visible"] if old_style is not None else saved_visible,
                "color": self._normalize_color(color, palette_color),
                "marker": old_style["marker"] if old_style is not None else saved_marker,
            })
        self._x_values = x_array if x_array.size else (
            updated[0]["x_values"] if updated else np.asarray([], dtype=float)
        )
        updated_by_key = {
            (entry["file_id"], entry["label"]): entry for entry in updated
        }
        prior_order = [
            (entry["file_id"], entry["label"])
            for entry in self._series_data
        ]
        retained_keys = [key for key in prior_order if key in updated_by_key]
        retained_set = set(retained_keys)
        self._series_data = [
            *(updated_by_key[key] for key in retained_keys),
            *(entry for entry in updated
              if (entry["file_id"], entry["label"]) not in retained_set),
        ]
        self._sync_series_controls()

    def _trace_color_setting_key(self, file_id: str, label: str) -> str:
        identity = "\x1f".join((self._settings_key, file_id, label))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"plots/trace_colors/{digest}"

    def _sync_series_controls(self) -> None:
        selected_keys = {
            item.data(Qt.ItemDataRole.UserRole)
            for trace_list in (self._available_trace_list, self._selected_trace_list)
            for item in trace_list.selectedItems()
        }
        selected_item = next(
            (
                item
                for trace_list in (self._selected_trace_list, self._available_trace_list)
                for item in trace_list.selectedItems()
            ),
            None,
        )
        current_item = selected_item or self._selected_trace_list.currentItem() or self._available_trace_list.currentItem()
        current_key = (
            current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        )
        dataset_combo = self._settings_dialog.dataset_combo
        current_dataset_id = dataset_combo.currentData()
        datasets: dict[str, str] = {}
        for entry in self._series_data:
            datasets.setdefault(entry["file_id"], entry["file_name"])
        dataset_combo.blockSignals(True)
        dataset_combo.clear()
        for file_id, file_name in datasets.items():
            dataset_combo.addItem(file_name, file_id)
        dataset_index = dataset_combo.findData(current_dataset_id)
        if dataset_index < 0 and dataset_combo.count():
            dataset_index = 0
        dataset_combo.setCurrentIndex(dataset_index)
        dataset_combo.blockSignals(False)
        self._available_trace_list.clear()
        self._selected_trace_list.clear()
        self._trace_items = {}
        for entry in self._series_data:
            key = (entry["file_id"], entry["label"])
            item = QListWidgetItem(entry["label"])
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(entry["legend_label"])
            item.setForeground(QBrush(QColor(entry["color"])))
            if entry["visible"]:
                item.setText(f"{entry['label']} - {entry['file_name']}")
                self._selected_trace_list.addItem(item)
                self._trace_items[key] = item
                item.setSelected(key in selected_keys)
                if key == current_key:
                    self._selected_trace_list.setCurrentItem(item)
        self._populate_available_traces()
        self._filter_trace_lists(self._settings_dialog.trace_search.text())
        self._on_selected_trace_changed(self._selected_trace_list.currentItem(), None)
        self._sync_equation_source_combo()

    def _populate_available_traces(self, _index: int = -1) -> None:
        dataset_id = self._settings_dialog.dataset_combo.currentData()
        self._update_remove_dataset_button()
        self._available_trace_list.clear()
        for entry in self._series_data:
            if entry["file_id"] != dataset_id or entry["visible"]:
                continue
            key = (entry["file_id"], entry["label"])
            item = QListWidgetItem(entry["label"])
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(entry["legend_label"])
            item.setForeground(QBrush(QColor(entry["color"])))
            self._available_trace_list.addItem(item)
            self._trace_items[key] = item
        self._filter_trace_lists(self._settings_dialog.trace_search.text())

    def _dataset_is_removable(self, file_id: Any) -> bool:
        if file_id is None:
            return False
        dataset_id = str(file_id)
        return (
            dataset_id != _EQUATION_FILE_ID
            and dataset_id in self._overlay_file_ids
            and dataset_id not in self._base_file_ids
        )

    def _update_remove_dataset_button(self, _index: int = -1) -> None:
        self._settings_dialog.remove_dataset_button.setEnabled(
            self._dataset_is_removable(self._settings_dialog.dataset_combo.currentData())
        )

    def _remove_selected_dataset(self) -> None:
        dataset_id = self._settings_dialog.dataset_combo.currentData()
        if not self._dataset_is_removable(dataset_id):
            return

        dataset_id = str(dataset_id)
        remaining_series = [
            entry for entry in self._series_data
            if entry["file_id"] != dataset_id
        ]
        self._overlay_file_ids.discard(dataset_id)
        self._appended_file_ids.discard(dataset_id)
        self._update_data(self._x_values, remaining_series)
        self._redraw(reset_range=True)
        self._save_appended_series()
        self._save_chart_settings()

    def _filter_trace_lists(self, text: str) -> None:
        query = text.casefold().strip()
        for trace_list in (self._available_trace_list, self._selected_trace_list):
            for index in range(trace_list.count()):
                item = trace_list.item(index)
                item.setHidden(query not in item.text().casefold())

    def _move_trace_items(self, source: QListWidget, visible: bool) -> None:
        keys = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in source.selectedItems()
        ]
        if not keys:
            return
        selected_keys = set(keys)
        for entry in self._series_data:
            if (entry["file_id"], entry["label"]) in selected_keys:
                entry["visible"] = visible
                self._save_trace_settings(entry)
        self._sync_series_controls()
        self._redraw()

    def _on_selected_trace_changed(self, current, _previous) -> None:
        self._settings_dialog.configure_trace_button.setEnabled(current is not None)
        visible_entries = [entry for entry in self._series_data if entry["visible"]]
        selected_index = next(
            (
                index for index, entry in enumerate(visible_entries)
                if (entry["file_id"], entry["label"])
                == (current.data(Qt.ItemDataRole.UserRole) if current is not None else None)
            ),
            -1,
        )
        self._settings_dialog.move_trace_up_button.setEnabled(selected_index > 0)
        self._settings_dialog.move_trace_down_button.setEnabled(
            0 <= selected_index < len(visible_entries) - 1
        )

    def _move_selected_trace(self, offset: int) -> None:
        item = self._selected_trace_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        visible_indices = [
            index for index, entry in enumerate(self._series_data)
            if entry["visible"]
        ]
        selected_index = next(
            (
                index for index, data_index in enumerate(visible_indices)
                if (self._series_data[data_index]["file_id"],
                    self._series_data[data_index]["label"]) == key
            ),
            -1,
        )
        target_index = selected_index + offset
        if selected_index < 0 or not 0 <= target_index < len(visible_indices):
            return
        source_data_index = visible_indices[selected_index]
        target_data_index = visible_indices[target_index]
        self._series_data[source_data_index], self._series_data[target_data_index] = (
            self._series_data[target_data_index], self._series_data[source_data_index]
        )
        self._sync_series_controls()
        moved_item = self._trace_items.get(key)
        if moved_item is not None:
            moved_item.setSelected(True)
            self._selected_trace_list.setCurrentItem(moved_item)
        self._redraw()

    def _configure_selected_trace(self) -> None:
        item = self._selected_trace_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        entry = next(
            (series for series in self._series_data
             if (series["file_id"], series["label"]) == key),
            None,
        )
        if entry is None:
            return
        dialog = _TraceSettingsDialog(entry["color"], entry["marker"], self._settings_dialog)
        dialog.setWindowTitle(f"Trace Settings: {entry['legend_label']}")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry["color"] = dialog.color
        entry["marker"] = dialog.marker
        self._save_trace_settings(entry)
        self._sync_series_controls()
        self._redraw()

    def _show_trace_list_context_menu(self, trace_list: QListWidget, position) -> None:
        item = trace_list.itemAt(position)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self._show_trace_context_menu(key, trace_list, position)

    def _set_visible(self, key: tuple[str, str], visible: bool) -> None:
        entry = next(
            (item for item in self._series_data if (item["file_id"], item["label"]) == key),
            None,
        )
        if entry is None:
            return
        entry["visible"] = visible
        self._save_trace_settings(entry)
        self._sync_series_controls()
        self._redraw()

    def _set_marker(self, key: tuple[str, str], marker: str) -> None:
        if marker not in _MARKERS.values():
            return
        for entry in self._series_data:
            if (entry["file_id"], entry["label"]) == key:
                entry["marker"] = marker
                self._save_trace_settings(entry)
                break
        else:
            return
        self._sync_series_controls()
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
            self._save_trace_settings(entry)
            self._sync_series_controls()
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
        x_is_log = self.x_scale_combo.currentText() == "Log"
        if x_is_log != self._last_x_log:
            was_log = self._last_x_log
            self._last_x_log = x_is_log
            for line, _label in self._marker_items:
                data_x = 10.0 ** float(line.value()) if was_log else float(line.value())
                if x_is_log and data_x > 0:
                    line.setValue(math.log10(data_x))
                elif not x_is_log:
                    line.setValue(data_x)
        self._x_auto_range = True
        self._y_auto_range = True
        self._x_range = None
        self._y_range = None
        self._redraw(reset_range=True)
        self._save_chart_settings()

    def _on_matplotlib_x_scale_changed(self, _selection: str = "") -> None:
        self._redraw()
        self._save_chart_settings()

    def _on_display_mode_changed(self, selection: str) -> None:
        decibel_mode = selection == "Magnitude (dB)"
        if self._use_pyqtgraph:
            if decibel_mode and self.y_scale_combo.currentText() == "Log":
                self.y_scale_combo.setCurrentText("Linear")
            self.y_scale_combo.setEnabled(not decibel_mode)
        self._redraw(reset_range=True)
        self._save_chart_settings()

    def _make_action_button(self, icon_name, tooltip, object_name, callback):
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIcon(self._chart_action_icon(icon_name))
        button.setIconSize(QPixmap(20, 20).size())
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _chart_action_icon(name: str) -> QIcon:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#e4e7eb"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if name == "settings":
            for y, knob_x in ((5, 8), (10, 13), (15, 6)):
                painter.drawLine(3, y, 17, y)
                painter.setBrush(QColor("#34383d"))
                painter.drawEllipse(knob_x - 1.8, y - 1.8, 3.6, 3.6)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        elif name == "fit":
            for x1, y1, x2, y2 in (
                (8, 3, 3, 3), (3, 3, 3, 8),
                (12, 3, 17, 3), (17, 3, 17, 8),
                (3, 12, 3, 17), (3, 17, 8, 17),
                (17, 12, 17, 17), (17, 17, 12, 17),
            ):
                painter.drawLine(x1, y1, x2, y2)
        elif name == "marker-add":
            painter.drawLine(3, 16, 17, 16)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(10, 4, 10, 16)
            painter.setPen(pen)
            painter.drawLine(7, 6, 13, 6)
            painter.drawLine(10, 3, 10, 9)
        elif name == "marker-remove":
            painter.drawLine(3, 16, 17, 16)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(6, 7, 6, 16)
            painter.drawLine(12, 4, 12, 16)
            pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawLine(9, 3, 16, 10)
            painter.drawLine(16, 3, 9, 10)
        elif name == "chart-add":
            painter.drawRect(3, 5, 12, 12)
            painter.drawLine(6, 13, 8, 10)
            painter.drawLine(8, 10, 11, 12)
            painter.drawLine(11, 12, 13, 8)
            painter.drawLine(15, 4, 19, 4)
            painter.drawLine(17, 2, 17, 6)

        painter.end()
        return QIcon(pixmap)

    def _open_settings(self) -> None:
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

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
            self._save_chart_settings()
            self._redraw(reset_range=True)

    def _fit_view(self) -> None:
        self._x_auto_range = True
        self._y_auto_range = True
        self._x_range = None
        self._y_range = None
        self._save_chart_settings()
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
        self.plot_widget.addItem(line, ignoreBounds=True)
        self._marker_sequence += 1
        marker_number = self._marker_sequence
        label = _MarkerReadout(
            color="#7a271a",
            fill=pg.mkBrush("#ffffff"),
            border=pg.mkPen("#e4a79a"),
        )
        label.setObjectName(f"markerReadout{marker_number}")
        self.plot_widget.addItem(label, ignoreBounds=True)
        marker = (line, label)
        self._marker_items.append(marker)
        line.sigPositionChanged.connect(self._on_marker_line_moved)
        line.sigClicked.connect(
            lambda clicked_line, event, current=marker: self._on_marker_clicked(current, event)
        )
        label.moved.connect(self._save_marker_positions)
        self._position_overlay_widgets()
        self._update_marker_label(line)
        self._save_marker_positions()

    def _on_marker_line_moved(self, line) -> None:
        self._update_marker_label(line)
        self._save_marker_positions()

    def _on_marker_clicked(self, marker, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.plot_widget.removeItem(marker[0])
            self.plot_widget.removeItem(marker[1])
            if marker in self._marker_items:
                self._marker_items.remove(marker)
                for marker_line, _label in self._marker_items:
                    self._update_marker_label(marker_line)
                self._save_marker_positions()

    def _clear_markers(self) -> None:
        if not self._use_pyqtgraph:
            return
        for line, label in self._marker_items:
            self.plot_widget.removeItem(line)
            self.plot_widget.removeItem(label)
        self._marker_items.clear()
        self._save_marker_positions()


    def _update_marker_label(self, line) -> None:
        for marker_line, label in self._marker_items:
            if marker_line is not line:
                continue
            view_x = float(line.value())
            x_value = 10.0 ** view_x if self.x_scale_combo.currentText() == "Log" else view_x
            lines = [f"{self._xlabel or 'X'}: {x_value:.6g}"]
            display_mode = self.display_mode_combo.currentText()
            x_log = self.x_scale_combo.currentText() == "Log"
            y_log = self.y_scale_combo.currentText() == "Log"
            for entry in self._series_data:
                if not entry["visible"]:
                    continue
                x_values = np.asarray(entry["x_values"], dtype=float)
                y_values = _transform_values(entry["values"], display_mode)
                valid = np.isfinite(x_values) & np.isfinite(y_values)
                if x_log:
                    valid &= x_values > 0
                if y_log:
                    valid &= y_values > 0
                if np.count_nonzero(valid) == 0:
                    lines.append(f"{entry['label']}: n/a")
                    continue
                sample_x = np.log10(x_values[valid]) if x_log else x_values[valid]
                sample_y = np.log10(y_values[valid]) if y_log else y_values[valid]
                order = np.argsort(sample_x)
                sample_x = sample_x[order]
                sample_y = sample_y[order]
                if view_x < sample_x[0] or view_x > sample_x[-1]:
                    lines.append(f"{entry['label']}: n/a")
                    continue
                interpolated = np.interp(view_x, sample_x, sample_y)
                if y_log:
                    interpolated = 10.0 ** interpolated
                lines.append(f"{entry['label']}: {interpolated:.4g}")
            marker_number = self._marker_items.index((marker_line, label)) + 1
            label.setText(f"Marker {marker_number}\n" + "\n".join(lines))
            view_box = self.plot_widget.getViewBox()
            x_range, y_range = view_box.viewRange()
            x_span = x_range[1] - x_range[0]
            y_span = y_range[1] - y_range[0]
            if x_span and y_span:
                place_right = view_x < x_range[0] + x_span * 0.72
                label.setAnchor((0, 0) if place_right else (1, 0))
                label.set_follow_position(
                    view_x + (x_span * 0.015 if place_right else -x_span * 0.015),
                    y_range[1] - y_span * 0.06,
                )
            return

    def _set_legend_scale(self, percent: int) -> None:
        self._legend_scale_percent = int(percent)
        if self._legend_item is not None:
            self._legend_item.setScale(self._legend_scale_percent / 100.0)
        self._save_chart_settings()

    def _set_marker_scale(self, percent: int) -> None:
        self._marker_scale_percent = int(percent)
        self._position_overlay_widgets()
        self._save_chart_settings()

    def _sync_chart_legend(self, series) -> None:
        if self._use_pyqtgraph:
            plot_item = self.plot_widget.getPlotItem()
            if not series:
                self._legend_item = None
                return
            legend = _PersistentLegendItem(
                self._remember_legend_position,
                brush=pg.mkBrush(255, 255, 255, 235),
                pen=pg.mkPen("#d0d5dd"),
                labelTextColor="#344054",
            )
            legend.setParentItem(plot_item.getViewBox())
            legend.anchor((1, 0), (1, 0), offset=(-10, 10))
            legend.setScale(self._legend_scale_percent / 100.0)
            for entry in series:
                symbol = _PG_SYMBOLS.get(entry["marker"])
                sample = pg.PlotDataItem(
                    pen=pg.mkPen(entry["color"], width=2),
                    symbol=symbol,
                    symbolSize=7,
                    symbolBrush=pg.mkBrush(entry["color"]) if symbol else None,
                    symbolPen=pg.mkPen(entry["color"]) if symbol else None,
                )
                legend_label = (
                    f"{entry['label']} - {entry['file_name']}"
                    if entry["file_name"] else entry["label"]
                )
                legend.addItem(sample, legend_label)
            self._legend_item = legend if series else None
            self._apply_legend_position(legend)
            return

        self._external_legend.clear()
        for entry in series:
            legend_label = (
                f"{entry['label']} - {entry['file_name']}"
                if entry["file_name"] else entry["label"]
            )
            item = QListWidgetItem(legend_label)
            item.setForeground(QBrush(QColor(entry["color"])))
            item.setToolTip(legend_label)
            self._external_legend.addItem(item)
        self._legend_panel.setVisible(bool(series))
        self._position_overlay_widgets()

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

        legend = self._legend_item
        if legend is not None:
            plot_item.scene().removeItem(legend)
            self._legend_item = None

        x_log = self.x_scale_combo.currentText() == "Log"
        y_log = self.y_scale_combo.currentText() == "Log"
        plot_item.setLogMode(x=x_log, y=y_log)
        plot_item.setTitle(self._title)
        plot_item.setLabel("bottom", self._xlabel or "Frequency (GHz)")
        mode = self.display_mode_combo.currentText()
        ylabel = "VSWR" if mode == "VSWR" else mode
        plot_item.setLabel("left", ylabel or self._ylabel)
        view_box.setBorder(pg.mkPen("#7a858f", width=1))
        visible_series = [entry for entry in self._series_data if entry["visible"]]
        self._sync_chart_legend(visible_series)

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

        legend = self._legend_item
        if legend is not None:
            plot_item.scene().removeItem(legend)
            self._legend_item = None

        plot_item.setLogMode(x=False, y=False)
        view_box.setBorder(pg.mkPen("#7a858f", width=1))
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
        self._sync_chart_legend(visible_series)
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
        self._sync_chart_legend(visible_series)
        for spine in axes.spines.values():
            spine.set_color("#7a858f")
            spine.set_linewidth(0.8)

        new_canvas = FigureCanvasQTAgg(figure)
        new_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        previous_canvas = self._canvas_scroll.takeWidget()
        self._canvas_scroll.setWidget(new_canvas)
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