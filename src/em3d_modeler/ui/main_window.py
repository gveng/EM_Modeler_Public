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

"""Main window: wires all panels together according to the UI layout.

Layout
������������������
  ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
  ���  ProjectTree    ���                           ���                  ���
  ���  (EMERGE cfg)   ���       3D Viewport         ��� Object/Materials ���
  ���������������������������������������������������������                           ���                  ���
  ���  BlockParams    ���                           ���                  ���
  ���  (selected obj) ���������������������������������������������������������������������������������������                  ���
  ���                 ���     Info / Error bar      ���                  ���
  ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
"""
from __future__ import annotations
from datetime import datetime
from html import escape
from pathlib import Path
from copy import deepcopy
import os
import json
import math
import subprocess
import traceback
import sys
import ctypes
import vtk

# Resolve resources from source, PyInstaller's internal directory, or the EXE folder.
_APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent.parent.parent))
_RESOURCE_ROOTS = [
    _APP_ROOT,
    Path(sys.executable).resolve().parent,
    Path(__file__).parent.parent.parent.parent,
]
_ICONS_DIR = next(
    (root / "Icons" for root in _RESOURCE_ROOTS if (root / "Icons").is_dir()),
    _APP_ROOT / "Icons",
)


def _scale_profile_coordinates(profile, factor: float):
    if (isinstance(profile, (list, tuple)) and len(profile) == 2
            and all(isinstance(value, (int, float)) for value in profile)):
        return (float(profile[0]) * factor, float(profile[1]) * factor)
    if isinstance(profile, (list, tuple)):
        return [_scale_profile_coordinates(item, factor) for item in profile]
    return profile


def _scale_formula_expression(expression: str, factor: float, parameter_names) -> str:
    import re

    scaled = str(expression)
    factors = (
        parameter_names.items()
        if isinstance(parameter_names, dict)
        else ((name, 1.0) for name in parameter_names)
    )
    for name, parameter_factor in sorted(factors, key=lambda item: len(str(item[0])), reverse=True):
        pattern = rf"(?<![\w.]){re.escape(str(name))}(?!\w)"
        scaled = re.sub(pattern, f"({name} / {float(parameter_factor)!r})", scaled)
    return f"({factor!r} * ({scaled}))"


def _scale_numeric_tree(value, factor: float):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) * factor
    if isinstance(value, list):
        return [_scale_numeric_tree(item, factor) for item in value]
    if isinstance(value, tuple):
        return tuple(_scale_numeric_tree(item, factor) for item in value)
    if isinstance(value, dict):
        return {key: _scale_numeric_tree(item, factor) for key, item in value.items()}
    return value


def _scale_sketch_definition(definition: dict, factor: float, parameter_names) -> dict:
    scaled = deepcopy(definition)
    if isinstance(scaled.get("plane_origin"), (list, tuple)):
        scaled["plane_origin"] = _scale_numeric_tree(scaled["plane_origin"], factor)
    for entity in scaled.get("entities", []):
        if isinstance(entity, dict) and isinstance(entity.get("geometry"), (list, tuple)):
            entity["geometry"] = _scale_numeric_tree(entity["geometry"], factor)
    if isinstance(scaled.get("selected_region_anchors"), list):
        scaled["selected_region_anchors"] = _scale_numeric_tree(
            scaled["selected_region_anchors"], factor
        )
    for dimension in scaled.get("dimensions", []):
        if not isinstance(dimension, dict) or str(dimension.get("kind", "")).lower() in {"angle", "angular"}:
            continue
        for key in ("value", "resolved_value"):
            if isinstance(dimension.get(key), (int, float)):
                dimension[key] = float(dimension[key]) * factor
        expression = dimension.get("expression")
        if expression:
            dimension["expression"] = _scale_formula_expression(
                expression, factor, parameter_names
            )
    return scaled


def _scale_creation_history(history: dict, factor: float) -> dict:
    scaled = deepcopy(history)
    for point in scaled.get("points", []):
        if not isinstance(point, dict):
            continue
        value = point.get("value")
        if isinstance(value, (list, tuple)) and len(value) == 3:
            point["value"] = _scale_numeric_tree(value, factor)
        snap = point.get("snap")
        if isinstance(snap, dict):
            local = snap.get("local")
            if isinstance(local, (list, tuple)) and len(local) == 3:
                snap["local"] = _scale_numeric_tree(local, factor)
    for key in ("source_face", "source_edge"):
        source = scaled.get(key)
        if isinstance(source, dict) and isinstance(source.get("points"), list):
            source["points"] = _scale_numeric_tree(source["points"], factor)
    return scaled


def _scale_pattern_settings(settings: dict, factor: float, parameter_names) -> dict:
    scaled = deepcopy(settings)
    spatial_keys = {
        "offset_x", "offset_y", "offset_z", "center_x", "center_y", "center_z",
        "axis_start_x", "axis_start_y", "axis_start_z",
        "axis_end_x", "axis_end_y", "axis_end_z", "radius",
    }
    for key in spatial_keys:
        if isinstance(scaled.get("resolved"), dict) and isinstance(scaled["resolved"].get(key), (int, float)):
            scaled["resolved"][key] = float(scaled["resolved"][key]) * factor
        if isinstance(scaled.get("expressions"), dict) and scaled["expressions"].get(key):
            scaled["expressions"][key] = _scale_formula_expression(
                scaled["expressions"][key], factor, parameter_names
            )
        entry = scaled.get("inputs", {}).get(key) if isinstance(scaled.get("inputs"), dict) else None
        if isinstance(entry, dict):
            if entry.get("type") == "formula" and entry.get("formula"):
                entry["formula"] = _scale_formula_expression(
                    entry["formula"], factor, parameter_names
                )
            if isinstance(entry.get("value"), (int, float)):
                entry["value"] = float(entry["value"]) * factor
    if isinstance(scaled.get("parameter_values"), dict):
        scaled["parameter_values"] = {
            key: float(value) * float(parameter_names.get(key, factor))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else value
            for key, value in scaled["parameter_values"].items()
        }
    return scaled


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(field, ctypes.c_uint64) for field in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _icon(name: str) -> "QIcon":
    """Load SVG/PNG icon by Part_Name from the Icons folder."""
    for ext in ("svg", "png"):
        for p in (_ICONS_DIR / f"{name}.{ext}", _ICONS_DIR / "Sketcher" / f"{name}.{ext}"):
            if p.exists():
                from PySide6.QtGui import QIcon
                return QIcon(str(p))
    from PySide6.QtGui import QIcon
    return QIcon()

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter,
    QFileDialog, QMessageBox, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit,
    QLabel, QDialog, QInputDialog,
    QVBoxLayout, QStackedLayout, QFormLayout, QTextBrowser, QPlainTextEdit,
    QPushButton, QHBoxLayout, QGroupBox, QCheckBox,
    QProgressDialog, QApplication, QTabWidget, QDialogButtonBox, QGridLayout, QToolButton, QToolBar,
    QRadioButton, QTabBar, QMdiArea, QMdiSubWindow, QListWidget, QListWidgetItem,
    QAbstractItemView, QStyle,
)
from PySide6.QtCore import Qt, QProcess, QLocale, QSettings, QSize, QUrl, QTimer, QEventLoop, Signal
from PySide6.QtGui  import QIcon, QKeySequence, QAction, QDesktopServices, QPainter, QPen, QColor, QBrush


class _SimulationPreparationCancelled(Exception):
    pass


class _BooleanCutSetupDialog(QDialog):
    def __init__(self, objects: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Boolean Cut")
        self.resize(620, 360)

        layout = QVBoxLayout(self)
        lists_layout = QHBoxLayout()

        base_layout = QVBoxLayout()
        base_layout.addWidget(QLabel("Objects to cut (Base)", self))
        self.base_list = QListWidget(self)
        self.base_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        base_layout.addWidget(self.base_list)
        lists_layout.addLayout(base_layout, 1)

        move_layout = QVBoxLayout()
        move_layout.addStretch(1)
        self.to_tools_button = QToolButton(self)
        self.to_tools_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowRight))
        self.to_tools_button.setToolTip("Move selected base objects to cutting tools")
        self.to_tools_button.setAccessibleName("Move selected objects to tools")
        self.to_tools_button.clicked.connect(
            lambda: self._move_selected(self.base_list, self.tools_list)
        )
        move_layout.addWidget(self.to_tools_button)
        self.to_base_button = QToolButton(self)
        self.to_base_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowLeft))
        self.to_base_button.setToolTip("Move selected tools to objects being cut")
        self.to_base_button.setAccessibleName("Move selected tools to base")
        self.to_base_button.clicked.connect(
            lambda: self._move_selected(self.tools_list, self.base_list)
        )
        move_layout.addWidget(self.to_base_button)
        move_layout.addStretch(1)
        lists_layout.addLayout(move_layout)

        tools_layout = QVBoxLayout()
        tools_layout.addWidget(QLabel("Cutting tools (Tools)", self))
        self.tools_list = QListWidget(self)
        self.tools_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tools_layout.addWidget(self.tools_list)
        lists_layout.addLayout(tools_layout, 1)
        layout.addLayout(lists_layout)

        for index, obj in enumerate(objects):
            item = QListWidgetItem(str(obj.name))
            item.setData(Qt.UserRole, obj)
            (self.base_list if index == 0 else self.tools_list).addItem(item)

        self.keep_tools_checkbox = QCheckBox("Keep cutting tools in project", self)
        layout.addWidget(self.keep_tools_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _move_selected(source: QListWidget, destination: QListWidget) -> None:
        rows = sorted({source.row(item) for item in source.selectedItems()}, reverse=True)
        moved_items = []
        for row in rows:
            moved_items.append(source.takeItem(row))
        for item in reversed(moved_items):
            destination.addItem(item)

    @staticmethod
    def _objects_in(widget: QListWidget) -> list:
        return [
            widget.item(index).data(Qt.UserRole)
            for index in range(widget.count())
        ]

    @property
    def base_objects(self) -> list:
        return self._objects_in(self.base_list)

    @property
    def tool_objects(self) -> list:
        return self._objects_in(self.tools_list)

    @property
    def keep_tools(self) -> bool:
        return self.keep_tools_checkbox.isChecked()

    def _accept(self) -> None:
        if not self.base_objects or not self.tool_objects:
            QMessageBox.warning(
                self,
                "Boolean Cut",
                "Assign at least one object to cut and one cutting tool.",
            )
            return
        self.accept()


def _boolean_step_export_issues(bundle: dict) -> list[str]:
    issues = []
    entries = bundle.get("entries", []) if isinstance(bundle, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        operation = str(entry.get("boolean_op") or "").strip().lower()
        mode = str(entry.get("export_mode") or "unknown")
        try:
            polygon_count = int(entry.get("poly_polys", 0) or 0)
            solid_count = int(entry.get("solid_count", 0) or 0)
        except (TypeError, ValueError):
            polygon_count = 0
            solid_count = 0
        if operation in {"fuse", "cut", "common"} and (mode != "brep_direct" or solid_count < 1):
            issues.append(
                f"{entry.get('object_name', 'Boolean object')}: {mode}, "
                f"{polygon_count:,} source triangles, {solid_count} STEP solids"
            )
        elif mode != "brep_direct" and polygon_count >= 1000:
            issues.append(
                f"{entry.get('object_name', 'Object')}: {mode}, "
                f"{polygon_count:,} triangles"
            )
    return issues

def _add_toolbar_group(
    toolbar, title: str, actions: list, columns: int = 3, *, compact: bool = False,
    dense: bool = False,
) -> None:
    group = QWidget(toolbar)
    group.setProperty("toolbarGroupTitle", title)
    if compact:
        group.setAccessibleName(f"{title} tools")
        grid = QGridLayout(group)
        grid.setContentsMargins(2, 0, 2, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
    else:
        layout = QVBoxLayout(group)
        horizontal_margin = 1 if dense else 10
        layout.setContentsMargins(horizontal_margin, 0, horizontal_margin, 0)
        layout.setSpacing(2)
        caption = QLabel(title, group)
        caption.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        caption.setStyleSheet("font-size: 9px; font-weight: bold; padding-top: 1px;")
        layout.addWidget(caption)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2 if dense else 4)
        grid.setVerticalSpacing(2)
    columns = max(1, columns)
    for index, action in enumerate(actions):
        if isinstance(action, QAction):
            button = QToolButton(group)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIconSize(toolbar.iconSize())
            if compact:
                button.setFixedSize(24, 24)
                button.setAccessibleName(action.text())
                button.setToolTip(action.toolTip() or action.text())
            elif dense:
                button.setFixedSize(20, 20)
            else:
                button.setFixedSize(30, 27)
            widget = button
        else:
            widget = action
        grid.addWidget(widget, index // columns, index % columns)
    if compact:
        group.setFixedSize(group.sizeHint())
    else:
        layout.addLayout(grid)
        if dense:
            group.setFixedWidth(
                max(caption.sizeHint().width(), grid.sizeHint().width())
                + horizontal_margin * 2
            )
        else:
            margins = layout.contentsMargins()
            group.setFixedWidth(max(122, grid.sizeHint().width() + margins.left() + margins.right()))
    group_action = toolbar.addWidget(group)
    alignment = Qt.AlignVCenter if compact else Qt.AlignTop
    toolbar.layout().setAlignment(toolbar.widgetForAction(group_action), alignment)
    if dense:
        separator_action = toolbar.addSeparator()
        separator_widget = toolbar.widgetForAction(separator_action)
        if separator_widget is not None:
            separator_widget.setFixedWidth(toolbar.iconSize().width())
    else:
        toolbar.addSeparator()


class _PlotMdiArea(QMdiArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QMdiArea.NoFrame)
        self.setViewMode(QMdiArea.SubWindowView)
        self.setActivationOrder(QMdiArea.ActivationHistoryOrder)
        self.setOption(QMdiArea.DontMaximizeSubWindowOnActivation, True)
        self.setBackground(QBrush(QColor("#20242b")))


class _PlotSubWindow(QMdiSubWindow):
    closed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.SubWindow
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit(self)


# Undo/Redo CommandStack


class _PatternPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self._mode = "Linear"
        self._count = 2
        self._offsets = (0.0, 0.0, 0.0)
        self._axis_enabled = (True, False, False)
        self._axis_counts = (2, 1, 1)
        self._axis = "Z"
        self._angle = 360.0

    def set_pattern(self, mode, count, offsets, axis, angle, axis_enabled=None, axis_counts=None):
        self._mode = str(mode)
        self._count = max(1, int(count))
        self._offsets = tuple(float(value) for value in offsets)
        self._axis = str(axis)
        self._angle = float(angle)
        if axis_enabled is not None:
            self._axis_enabled = tuple(bool(value) for value in axis_enabled)
        if axis_counts is not None:
            self._axis_counts = tuple(max(1, int(value)) for value in axis_counts)
        self.update()

    def paintEvent(self, _event):
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#20242b"))
        painter.setPen(QPen(QColor("#596273"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#b9c5d6"))
        painter.drawText(10, 20, "Pattern preview")

        center_x = self.width() * 0.5
        center_y = self.height() * 0.58
        points = [(center_x, center_y)]
        if self._mode == "Linear":
            dx, dy, _dz = self._offsets
            enabled = [index for index, active in enumerate(self._axis_enabled) if active]
            if len(enabled) == 1:
                axis_index = enabled[0]
                step = self._offsets[axis_index] or 1.0
                scale = max(abs(step), 1.0)
                count = self._axis_counts[axis_index]
                delta = step / scale * 42.0
                points = [(center_x + delta * i, center_y) for i in range(count)]
            else:
                points = []
                x_count = self._axis_counts[0] if self._axis_enabled[0] else 1
                y_count = self._axis_counts[1] if self._axis_enabled[1] else 1
                for ix in range(x_count):
                    for iy in range(y_count):
                        points.append((center_x + (ix - (x_count - 1) / 2) * 32, center_y - (iy - (y_count - 1) / 2) * 32))
        else:
            radius = min(self.width(), self.height()) * 0.28
            total = self._angle * 3.141592653589793 / 180.0
            points = []
            for i in range(self._count):
                fraction = i / max(1, self._count - 1)
                angle = total * fraction
                points.append((center_x + radius * math.cos(angle), center_y - radius * math.sin(angle)))

        for index, point in enumerate(points):
            painter.setBrush(QColor("#f2a65a") if index == 0 else QColor("#63b3ed"))
            painter.setPen(QPen(QColor("#e7edf5"), 1))
            painter.drawEllipse(point[0] - 6, point[1] - 6, 12, 12)
        painter.setPen(QColor("#8f9bad"))
        painter.drawText(10, self.height() - 10, f"{self._mode} | {self._count} instances")


from .viewport_widget        import Viewport3DWidget
from .project_tree_widget    import ProjectTreeWidget
from .body_properties_widget import BodyPropertiesWidget
from .materials_widget       import MaterialsWidget
from .info_bar_widget        import InfoBarWidget
from .reference_plane_dialog import ReferencePlaneDialog
from .sketch_widget          import SketchDialog
from .material_assign_dialog import MaterialAssignDialog
from .material_library_dialog import MaterialLibraryDialog
from .settings_dialog        import SettingsDialog
from .parameters_dialog      import ParametersDialog
from .unit_options           import UNIT_OPTIONS
from .formula_widgets        import FormulaDoubleSpinBox as QDoubleSpinBox, FormulaIntSpinBox
from .project_tree_widget    import set_numeric_locale

from ..emerge.project_file    import ProjectFile
from ..emerge.script_exporter import export_emerge_script
from ..emerge.python_script_exporter import (
    build_clean_emerge_python_script,
    export_emerge_python_script,
)
from ..emerge.step_bundle_exporter import export_debug_scene_step, export_objects_to_step_bundle
from ..emerge.step_importer   import import_step
from ..emerge.material_store  import MaterialStore
from ..emerge.simulation_validator import validate_simulation
from ..scene.em_objects       import set_selection_color
from ..scene.param_expr       import evaluate_expression
from .. import __version__, __release_date__, __license__


# ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
_UNITS  = UNIT_OPTIONS
_EMERGE_SOLVERS = frozenset({
    "PARDISO", "SUPERLU", "UMFPACK", "CUDSS", "AASDS", "MUMPS",
})
_DOCS_ROOT = _APP_ROOT / "docs"
_DOCS_HELP = _DOCS_ROOT / "HELP.md"
_DOCS_HTML = _DOCS_ROOT / "HELP.html"
_DOCS_README = _APP_ROOT / "README.md"
_LOG_LEVEL_ORDER = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# Unit conversion: mm per unit (reference base)
_MM_PER_UNIT = {
    "mm": 1.0,
    "um": 0.001,
    "cm": 10.0,
    "m": 1000.0,
    "mil": 0.0254,
    "inch": 25.4,
}


class MainWindow(QMainWindow):
    """EM 3D Modeler - main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowIcon(QApplication.windowIcon())
        self.setWindowTitle("EM 3D Modeler - EMERGE Design Environment")
        self.resize(1400, 860)

        self._project_name  = "Untitled"
        self._project_properties_active = False
        self._project_path  = None
        self._units         = "mm"
        self._workspace_size = 200.0
        self._grid_spacing = 10.0
        self._adaptive_grid_enabled = False
        self._adaptive_grid_margin = 20.0
        self._plane_triad_size = 25.0
        self._decimal_separator = "."
        self._selection_color = (0.62, 0.34, 0.85)
        self._draw_material = "PEC"
        self._material_store = MaterialStore()
        self._sim_steps_dirty = True
        self._sim_script_dirty = True
        self._sim_step_bundle_ready = False
        self._sim_step_bundle_cache: dict = {"entries": [], "skipped": []}
        self._simulation_step_export_cache: dict[tuple, dict] = {}
        self._sim_full_scene_step_dirty = True
        self._export_full_scene_step = False
        self._sim_status_running = False
        self._sim_status_completed_jobs = 0
        self._sim_status_total_jobs = 0
        self._boolean_decimation_enabled = False
        self._workspace_path = ""
        self._global_material_db_path = ""
        self._sim_cached_script = ""
        self._sim_cached_script_bundle: dict = {"master": "", "scripts": []}
        self._workspace_windows: dict[str, _PlotSubWindow] = {}
        self._model_window: _PlotSubWindow | None = None
        self._sim_subwindow: _PlotSubWindow | None = None
        self._plot_views: dict[str, QWidget] = {}
        self._plot_subwindows: dict[str, _PlotSubWindow] = {}
        self._sim_log_verbosity = "INFO"
        self._ui_locale = QLocale.c()

        # Simulation & Mesh settings (app-wide defaults)
        self._sim_solver = "PARDISO"
        self._sim_parallel_enabled = True
        self._sim_pardiso_threads = 8
        self._sim_acc_threads = 10
        self._mesh_resolution = 0.3  # 1/3 wavelength, ~3.3 lines/wavelength
        self._curved_boundary_resolution = 20
        self._refinement_boundary_size_mm = 0.25
        self._refinement_face_size_mm = 0.1
        self._refinement_growth_rate = 3.0
        self._refinement_max_size_mm = 0.0
        self._sim_plot_sparams_after_sim = True
        self._sim_export_sparams_after_sim = True
        self._history_limit = 10
        self._history_undo: list[list[dict]] = []
        self._history_redo: list[list[dict]] = []
        self._history_restoring = False
        self._sketch_after_plane_defined = False

        self._load_app_settings()
        set_selection_color(self._selection_color)
        set_numeric_locale(self._ui_locale)




        self._build_ui()
        self._build_toolbar()
        self._build_menus()
        self._connect_signals()
        self._sync_material_choices()

        self._new_project()
        self._history_reset()


    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� UI construction
    def _build_ui(self) -> None:
        # ������ panels ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
        self._project_tree   = ProjectTreeWidget()
        self._body_props     = BodyPropertiesWidget()
        self._viewport       = Viewport3DWidget()
        self._viewport.set_plane_triad_size(self._plane_triad_size)
        self._viewport.set_adaptive_grid(self._adaptive_grid_enabled, self._adaptive_grid_margin)
        self._materials      = MaterialsWidget(self)
        self._info_bar       = InfoBarWidget()

        # ������ left column: project tree (top) + body props (bottom) ���������������������������������
        left_splitter = QSplitter(Qt.Vertical)
        self._left_splitter = left_splitter
        left_splitter.addWidget(self._project_tree)
        left_splitter.addWidget(self._body_props)
        left_splitter.setSizes([350, 300])
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setCollapsible(0, False)
        left_splitter.setCollapsible(1, True)
        self._body_props.content_changed.connect(self._schedule_left_splitter_layout)
        left_splitter.setMinimumWidth(210)

        # ������ centre column: floating workspace + info bar ����������������������������
        centre_widget = QWidget()
        centre_layout = QVBoxLayout(centre_widget)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        self._workspace_area = _PlotMdiArea(centre_widget)
        self._workspace_area.setObjectName("workspaceArea")
        self._plot_mdi_area = self._workspace_area
        self._workspace_area.subWindowActivated.connect(
            self._on_workspace_window_activated
        )
        model_page = QWidget()
        self._model_page = model_page
        model_layout = QVBoxLayout(model_page)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(0)
        model_layout.addWidget(self._viewport, stretch=1)
        self._model_window = _PlotSubWindow(self._workspace_area)
        self._model_window.setWindowTitle("Model")
        self._model_window.setMinimumSize(480, 360)
        self._model_window.setWidget(model_page)
        self._model_window.closed.connect(self._on_workspace_subwindow_closed)
        self._workspace_area.addSubWindow(self._model_window)
        self._workspace_windows["Model"] = self._model_window
        self._model_window.resize(700, 520)
        self._model_window.move(24, 24)
        centre_layout.addWidget(self._workspace_area, stretch=1)
        centre_layout.addWidget(self._info_bar)
        self._workspace_area.show()
        self._model_window.show()

        # ������ right column ������������������������������������������������������������������������������������������������������������������������������������������������������������������
        self._materials.setMinimumWidth(190)

        # ������ main horizontal splitter ������������������������������������������������������������������������������������������������������������������������������
        main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter = main_splitter
        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(centre_widget)
        main_splitter.addWidget(self._materials)
        main_splitter.setCollapsible(2, False)
        main_splitter.setSizes([230, 900, 210])

        self.setCentralWidget(main_splitter)
        self._update_left_splitter_layout()

    def _schedule_left_splitter_layout(self, _has_content: bool = False) -> None:
        QTimer.singleShot(0, self._update_left_splitter_layout)

    def _update_left_splitter_layout(self) -> None:
        splitter = getattr(self, "_left_splitter", None)
        body_props = getattr(self, "_body_props", None)
        if splitter is None or body_props is None:
            return

        if not body_props.has_content:
            body_props.hide()
            splitter.setSizes([max(1, splitter.height()), 0])
            return

        body_props.show()
        available_height = max(1, splitter.height())
        preferred_height = max(170, body_props.sizeHint().height())
        bottom_height = min(
            preferred_height,
            max(170, int(available_height * 0.5)),
        )
        splitter.setSizes([max(1, available_height - bottom_height), bottom_height])

    def _on_workspace_window_activated(self, window) -> None:
        self._info_bar.set_model_view(
            window is self._model_window or window in self._plot_subwindows.values()
        )
        self._rebuild_window_menu()

    def _refresh_simulation_status(self) -> None:
        info_bar = getattr(self, "_info_bar", None)
        if info_bar is None:
            return
        info_bar.set_simulation_status(
            bool(getattr(self, "_sim_status_running", False)),
            completed_jobs=int(getattr(self, "_sim_status_completed_jobs", 0)),
            total_jobs=int(getattr(self, "_sim_status_total_jobs", 0)),
            current_fraction=getattr(self, "_sim_status_current_fraction", None),
        )


    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� menus
    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("&File")
        self._act_new    = file_menu.addAction("&New Project",    self._new_project,  QKeySequence.New)
        self._act_close  = file_menu.addAction("&Close Project", self._close_project, QKeySequence.Close)
        self._act_open   = file_menu.addAction("&Open Project...",  self._open_project, QKeySequence.Open)
        self._act_save   = file_menu.addAction("&Save Project",   self._save_project, QKeySequence.Save)
        self._act_saveas = file_menu.addAction("Save Project &As...", self._save_project_as)
        file_menu.addSeparator()
        file_menu.addAction("&Import STEP...",            self._import_step)
        file_menu.addSeparator()
        self._recent_menu = file_menu.addMenu("Recent Projects")
        self._refresh_recent_projects()
        act_export = file_menu.addAction("&Export EMERGE Script...", self._export_emerge)
        file_menu.addSeparator()
        file_menu.addAction("Set &Global Material DB...", self._set_global_material_db)
        file_menu.addAction("&Reload Global Material DB", self._reload_global_material_db)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.Quit)

        # Edit
        edit_menu = mb.addMenu("&Edit")
        self._act_del = edit_menu.addAction("&Delete Selected", self._delete_selected, QKeySequence.Delete)
        self._act_cancel_drawing = edit_menu.addAction(
            "&Cancel Drawing", self._viewport.cancel_draw, Qt.Key_Escape
        )
        self._viewport.measurement_mode_changed.connect(
            self._set_cancel_drawing_enabled
        )
        edit_menu.addSeparator()
        self._act_undo = edit_menu.addAction("&Undo", self._undo, QKeySequence.Undo)
        self._act_redo = edit_menu.addAction("&Redo", self._redo, QKeySequence.Redo)
        self._update_history_actions()


        # View
        view_menu = mb.addMenu("&View")
        view_menu.addAction("Reset Camera",       self._viewport.reset_camera, Qt.Key_Home)
        act_view_top = view_menu.addAction("Top (XY)", lambda: self._set_view("top"))
        act_view_top.setShortcut(QKeySequence("Ctrl+1"))
        act_view_bottom = view_menu.addAction("Bottom (-XY)", lambda: self._set_view("bottom"))
        act_view_bottom.setShortcuts([
            QKeySequence("Ctrl+6"),
            QKeySequence("Ctrl+Shift+1"),
        ])
        act_view_front = view_menu.addAction("Front (XZ)", lambda: self._set_view("front"))
        act_view_front.setShortcut(QKeySequence("Ctrl+2"))
        act_view_back = view_menu.addAction("Back (-XZ)", lambda: self._set_view("back"))
        act_view_back.setShortcuts([
            QKeySequence("Ctrl+Shift+2"),
        ])
        act_view_right = view_menu.addAction("Right (YZ)", lambda: self._set_view("right"))
        act_view_right.setShortcut(QKeySequence("Ctrl+3"))
        act_view_left = view_menu.addAction("Left (-YZ)", lambda: self._set_view("left"))
        act_view_left.setShortcut(QKeySequence("Ctrl+Shift+3"))
        act_view_iso = view_menu.addAction("Isometric", lambda: self._set_view("iso"))
        act_view_iso.setShortcut(QKeySequence("Ctrl+4"))
        view_menu.addSeparator()
        view_menu.addAction("Set Reference Plane...", self._open_reference_plane_dialog)
        view_menu.addAction("Reset Reference Plane", self._viewport.reset_reference_plane)

        # Grid visibility toggle
        self._act_grid_toggle = view_menu.addAction("Hide Grid", self._toggle_grid)
        self._act_grid_toggle.setCheckable(True)
        self._act_grid_toggle.setChecked(True)

        self._window_menu = mb.addMenu("&Window")
        self._rebuild_window_menu()

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction("&Settings", self._open_settings_dialog)
        tools_menu.addAction("Material &Library...", self._open_material_library_dialog)
        tools_menu.addAction("&Parameters...", self._open_project_parameters)
        tools_menu.addAction("Export Model as &STEP...", self._export_model_step)

        # Help
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&Help", self._open_help)
        help_menu.addSeparator()
        help_menu.addAction("&About", self._show_about)

    def _rebuild_window_menu(self) -> None:
        menu = getattr(self, "_window_menu", None)
        if menu is None:
            return
        menu.clear()
        area = getattr(self, "_workspace_area", None)
        if area is None:
            return
        active_window = area.activeSubWindow()
        windows = list(getattr(self, "_workspace_windows", {}).items())
        for key, window in windows:
            action = menu.addAction(key)
            action.setCheckable(True)
            action.setChecked(window is active_window)
            action.triggered.connect(
                lambda _checked=False, name=key: self._show_workspace_window(name)
            )
        plot_windows = list(getattr(self, "_plot_subwindows", {}).items())
        if windows or plot_windows:
            menu.addSeparator()
        for key, window in plot_windows:
            action = menu.addAction(window.windowTitle())
            action.setCheckable(True)
            action.setChecked(window is active_window)
            action.triggered.connect(
                lambda _checked=False, plot_key=key: self._show_plot_window(plot_key)
            )
        menu.addAction("Tile Windows", self._tile_plot_windows)
        menu.addAction("Cascade Windows", self._cascade_plot_windows)
        menu.addAction("Restore Workspace Defaults", self._restore_workspace_defaults)
        menu.addSeparator()
        menu.addAction("Close All Charts", self._close_all_plot_windows)

    def _show_workspace_window(self, key: str) -> None:
        window = self._workspace_windows.get(key)
        if window is None:
            return
        self._workspace_area.show()
        window.showNormal()
        self._workspace_area.setActiveSubWindow(window)
        window.raise_()
        self._info_bar.set_model_view(window is self._model_window)
        if window is self._model_window:
            self._model_page.show()
            self._viewport.show()
            self._viewport._vtk_widget.show()
            QTimer.singleShot(0, self._viewport._render)
        elif window is getattr(self, "_sim_subwindow", None):
            simulation_dialog = getattr(self, "_sim_dlg", None)
            if simulation_dialog is not None:
                simulation_dialog.show()

    def _on_workspace_subwindow_closed(self, _window) -> None:
        self._rebuild_window_menu()

    def _close_all_plot_windows(self) -> None:
        for window in list(getattr(self, "_plot_subwindows", {}).values()):
            window.close()
        self._rebuild_window_menu()

    def _on_plot_subwindow_closed(self, key: str, window: _PlotSubWindow) -> None:
        if self._plot_subwindows.get(key) is not window:
            return
        self._plot_subwindows.pop(key, None)
        self._plot_views.pop(key, None)
        self._rebuild_window_menu()

    def _show_plot_window(self, key: str) -> None:
        window = self._plot_subwindows.get(key)
        if window is None:
            return
        self._plot_mdi_area.show()
        window.showNormal()
        self._plot_mdi_area.setActiveSubWindow(window)
        window.raise_()

    def _tile_plot_windows(self) -> None:
        self._plot_mdi_area.tileSubWindows()

    def _cascade_plot_windows(self) -> None:
        self._plot_mdi_area.cascadeSubWindows()

    def _restore_workspace_defaults(self) -> None:
        area = getattr(self, "_workspace_area", None)
        if area is None:
            return

        model_window = getattr(self, "_model_window", None)
        if model_window is not None:
            model_window.showNormal()
            model_window.resize(700, 520)
            model_window.move(24, 24)

        simulation_window = getattr(self, "_sim_subwindow", None)
        if simulation_window is not None:
            simulation_window.showNormal()
            simulation_window.resize(680, 520)
            simulation_window.move(72, 72)

        plot_windows = list(getattr(self, "_plot_subwindows", {}).values())
        for index, window in enumerate(plot_windows):
            window.showNormal()
            offset = 36 * (index % 6 + 1)
            width = min(760, max(420, int(area.width() * 0.72)))
            height = min(560, max(320, int(area.height() * 0.72)))
            window.resize(width, height)
            window.move(24 + offset, 24 + offset)

        if model_window is not None:
            self._show_workspace_window("Model")

    def _show_chart(self, key: str, plot_data: dict, *, progressive: bool = False) -> None:
        view = self._plot_views.get(key)
        window = self._plot_subwindows.get(key)
        is_new = view is None or window is None
        if is_new:
            from .chart_view import PlotView

            title = str(plot_data.get("title", key)).strip() or key
            view = PlotView(
                str(plot_data.get("plot_type", "plot_sp")),
                settings_key=key,
            )
            view.touchstone_load_requested.connect(
                lambda plot_key=key: self._load_touchstone_for_chart(plot_key)
            )
            self._plot_views[key] = view
            window = _PlotSubWindow(self._plot_mdi_area)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.setMinimumSize(360, 280)
            window.setWindowTitle(title)
            window.setWidget(view)
            self._plot_mdi_area.addSubWindow(window)
            window.closed.connect(
                lambda closed_window, plot_key=key: self._on_plot_subwindow_closed(
                    plot_key, closed_window
                )
            )
            offset = 36 * (len(self._plot_subwindows) % 6 + 1)
            width = min(760, max(420, int(self._plot_mdi_area.width() * 0.72)))
            height = min(560, max(320, int(self._plot_mdi_area.height() * 0.72)))
            window.resize(width, height)
            window.move(24 + offset, 24 + offset)
            self._plot_subwindows[key] = window

        title = str(plot_data.get("title", key)).strip() or key
        window.setWindowTitle(title)
        if progressive:
            view.set_progressive_data(
                plot_data["x_values"],
                plot_data["series"],
                title=plot_data["title"],
                xlabel=plot_data.get("xlabel", ""),
                ylabel=plot_data.get("ylabel", ""),
                x_range=plot_data.get("x_range"),
            )
        else:
            view.set_plot_data(
                plot_data["x_values"],
                plot_data["series"],
                title=plot_data["title"],
                xlabel=plot_data.get("xlabel", ""),
                ylabel=plot_data.get("ylabel", ""),
            )
        if is_new:
            self._plot_mdi_area.show()
            window.show()
            self._plot_mdi_area.setActiveSubWindow(window)
        self._rebuild_window_menu()

    def _load_touchstone_for_chart(self, source_chart_key: str) -> None:
        from .chart_data import make_sparameter_plot_data
        from .touchstone import prepare_touchstone_directory, read_touchstone_ri

        try:
            touchstone_dir = prepare_touchstone_directory(self._simulation_bundle_dir())
        except OSError as exc:
            QMessageBox.warning(self, "Touchstone", f"Unable to prepare Touchstone folder: {exc}")
            return

        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Touchstone Results",
            str(touchstone_dir),
            "Touchstone S-parameter files (*.s*p *.S*P)",
        )
        if not path:
            return

        touchstone_path = Path(path)
        try:
            parsed = read_touchstone_ri(touchstone_path)
            parameters = [
                f"S{output_port}{input_port}"
                for output_port in range(1, parsed["port_count"] + 1)
                for input_port in range(1, parsed["port_count"] + 1)
            ]
            plot_data = make_sparameter_plot_data(
                {
                    "name": touchstone_path.stem,
                    "plot_type": "plot_sp",
                    "params": {"s_parameters": parameters},
                },
                parsed["frequencies"],
                parsed["s_matrices"],
                include_all_parameters=True,
                file_name=touchstone_path.name,
                file_id=str(touchstone_path.resolve()),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "Touchstone", f"Unable to load {touchstone_path.name}: {exc}")
            return

        view = self._plot_views.get(source_chart_key)
        if view is None or not hasattr(view, "add_file_data"):
            QMessageBox.warning(self, "Touchstone", "The source chart is no longer available.")
            return
        selected = view.selected_parameters() or view.configured_parameters()
        for series in plot_data["series"]:
            series["visible"] = series["label"] in selected
            series["configured"] = series["visible"]
        view.add_file_data(plot_data["x_values"], plot_data["series"])

    def _recent_project_paths(self) -> list[str]:
        raw = QSettings().value("recent_projects", [], type=list)
        paths = [str(path) for path in raw if str(path).strip()]
        existing = []
        for path in paths:
            resolved = str(Path(path).expanduser().resolve())
            if Path(resolved).is_file():
                existing.append(resolved)
        return existing[:5]

    def _refresh_recent_projects(self) -> None:
        if not hasattr(self, "_recent_menu"):
            return
        self._recent_menu.clear()
        paths = self._recent_project_paths()
        if not paths:
            action = self._recent_menu.addAction("No recent projects")
            action.setEnabled(False)
            return
        for path in paths:
            action = self._recent_menu.addAction(Path(path).stem)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, p=path: self._load_project_path(p))

    def _remember_recent_project(self, path: str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        paths = [resolved] + [item for item in self._recent_project_paths() if item != resolved]
        QSettings().setValue("recent_projects", paths[:5])
        self._refresh_recent_projects()

    def _toggle_grid(self):
        show = self._act_grid_toggle.isChecked()
        self._apply_grid_visibility(show)

    def _open_help(self) -> None:
        if _DOCS_HTML.exists():
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(_DOCS_HTML))):
                return

        for p in (_DOCS_HELP, _DOCS_README):
            if not p.exists():
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception as exc:
                QMessageBox.warning(self, "Help", f"Unable to open help file:\n{p}\n\n{exc}")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Help - {p.name}")
            dlg.resize(920, 700)
            layout = QVBoxLayout(dlg)
            browser = QTextBrowser(dlg)
            browser.setOpenExternalLinks(True)
            if hasattr(browser, "setMarkdown"):
                browser.setMarkdown(content)
            else:
                browser.setPlainText(content)
            layout.addWidget(browser)
            dlg.exec_()
            return

        QMessageBox.warning(
            self,
            "Help",
            f"No help document found.\nExpected:\n{_DOCS_HELP}\n(or fallback {_DOCS_README})",
        )

    def _show_about(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        QMessageBox.about(
            self,
            "About EM 3D Modeler",
            "<h3>EM 3D Modeler</h3>"
            "<p>Author: Gabriele Vittori<br>"
            f"Version: {__version__}<br>"
            f"Release Date: {__release_date__}<br>"
            f"Date: {today}</p><hr>"
            f"<pre style='font-family: monospace; font-size: 8pt'>{escape(__license__)}</pre>",
        )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� toolbar
    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        self._main_toolbar = tb
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)

        def add_group(
            title: str,
            actions: list,
            columns: int = 3,
            toolbar=None,
            compact: bool = False,
        ) -> None:
            target_toolbar = toolbar or tb
            _add_toolbar_group(
                target_toolbar,
                title,
                actions,
                columns,
                compact=compact,
                dense=not compact,
            )

        # ������ Primitive shapes ������������������������������������������������������������������������������������������������������������������������
        _DRAW_ICONS = [
            ("Box",       "box",       "Part_Box"),
            ("Cylinder",  "cylinder",  "Part_Cylinder"),
            ("Cone",      "cone",      "Part_Cone"),
            ("Sphere",    "sphere",    "Part_Sphere"),
        ]
        primitive_actions = []
        for label, mode, icon_name in _DRAW_ICONS:
            act = QAction(_icon(icon_name), label, self)
            act.setToolTip(f"Draw {label}  [click base on viewport to start]")
            act.triggered.connect(lambda checked, m=mode: self._start_draw(m))
            primitive_actions.append(act)
        act_open_region = QAction(_icon("open-region-pml"), "Open Region / PML", self)
        act_open_region.setToolTip("Generate an air region, PML shell, and open/radiation boundaries")
        act_open_region.triggered.connect(self._open_region_pml_wizard)
        primitive_actions.append(act_open_region)
        act_step = QAction(_icon("Part_STEP"), "Import STEP", self)
        act_step.setToolTip("Import a STEP file (.step / .stp)")
        act_step.triggered.connect(self._import_step)
        primitive_actions.append(act_step)
        add_group("3D", primitive_actions, columns=3)

        # Sketch tool
        act_sketch = QAction(_icon("Part_Sketch"), "Sketch", self)
        act_sketch.setToolTip("Open parametric sketch canvas (extrude or revolve)")
        act_sketch.triggered.connect(self._open_sketch)

        act_planar = QAction(_icon("Std_Plane"), "Planar", self)
        act_planar.setToolTip("Define planar structure: pick start/end (vertex/edge/face snap) on active plane")
        act_planar.triggered.connect(lambda: self._start_draw("planar"))
        act_plate_face = QAction(_icon("Std_Plane"), "Plate from Face/Edge", self)
        act_plate_face.setToolTip("Create a thin plate from the last selected face or axis-aligned edge")
        act_plate_face.triggered.connect(self._create_plate_from_face)
        act_circular_plate = QAction(_icon("Sketcher_CreateCircle"), "Circular Plate", self)
        act_circular_plate.setToolTip(
            "Create a circular Plate by snapping its center and radius to the configured grid spacing"
        )
        act_circular_plate.triggered.connect(lambda: self._start_draw("circular_plate"))
        add_group("2D", [act_sketch, act_planar, act_plate_face, act_circular_plate], columns=3)

        # ������ Boolean operations ������������������������������������������������������������������������������������������������������������
        act_cut = QAction(_icon("Part_Cut"), "Cut", self)
        act_cut.setToolTip("Boolean Cut: subtract Tool shape from Base shape")
        act_cut.triggered.connect(self._bool_cut)

        act_fuse = QAction(_icon("Part_Fuse"), "Fuse", self)
        act_fuse.setToolTip("Boolean Fuse (Union): merge two selected shapes")
        act_fuse.triggered.connect(self._bool_fuse)

        act_common = QAction(_icon("Part_Common"), "Common", self)
        act_common.setToolTip("Boolean Common (Intersection): keep overlapping volume")
        act_common.triggered.connect(self._bool_common)
        add_group("Boolean", [act_cut, act_fuse, act_common])
        self._fuse_label = QLabel("Fuse: 0")
        self._fuse_label.setToolTip("Number of objects currently selected for Boolean Fuse")
        self._fuse_label.setStyleSheet("font-size: 10px; color: #666; padding: 0 2px;")
        tb.addWidget(self._fuse_label)

        act_scale = QAction(_icon("image-scaling"), "Scale", self)
        act_scale.setToolTip("Scale selected object(s) by a user-defined factor")
        act_scale.triggered.connect(self._scale_selected_objects)

        act_move_plane = QAction(_icon("Std_TransformManip"), "Move/Rotate", self)
        act_move_plane.setToolTip("Move and rotate the selected object around a reference point")
        act_move_plane.triggered.connect(self._move_selection_to_plane_origin)

        act_pattern = QAction(_icon("LinkArray"), "Pattern", self)
        act_pattern.setToolTip("Create linear or circular instances of the selected object")
        act_pattern.triggered.connect(self._create_object_pattern)

        act_copy_by_vertices = QAction(_icon("edit-copy"), "Copy by Vertices", self)
        act_copy_by_vertices.setToolTip(
            "Copy the selected object from a picked source vertex to a target vertex without changing its orientation"
        )
        act_copy_by_vertices.triggered.connect(self._copy_selected_object_by_vertices)

        act_dissolve_boolean = QAction(_icon("edit-delete"), "Dissolve Boolean", self)
        act_dissolve_boolean.setToolTip("Restore source objects from selected boolean result(s)")
        act_dissolve_boolean.triggered.connect(self._bool_dissolve)
        add_group(
            "Transform",
            [act_scale, act_move_plane, act_pattern, act_copy_by_vertices, act_dissolve_boolean],
            columns=3,
        )

        tb = self._main_toolbar
        self._workspace_toolbar = tb

        act_play = QAction(_icon("media-playback-start"), "Play", self)
        act_play.setToolTip("Open simulation panel (EMERGE Python script + verbose output)")
        act_play.triggered.connect(self._open_simulation_window)

        act_check_simulation = QAction(_icon("dagViewPass"), "Check Simulation", self)
        act_check_simulation.setToolTip("Validate geometry, solver, boundaries, ports and port connectivity")
        act_check_simulation.triggered.connect(self._on_check_simulation)
        add_group("Simulation", [act_check_simulation, act_play], columns=3)

        act_fit_all = QAction(_icon("zoom-all"), "Fit All", self)
        act_fit_all.setToolTip("Fit all visible objects while preserving the current view orientation")
        act_fit_all.triggered.connect(self._viewport.fit_all)

        act_fit_selection = QAction(_icon("zoom-selection"), "Fit Selection", self)
        act_fit_selection.setToolTip("Fit selected object(s) while preserving the current view orientation")
        act_fit_selection.triggered.connect(self._viewport.fit_selection)

        act_isometric = QAction(_icon("view-isometric"), "Isometric View", self)
        act_isometric.setToolTip("Set an isometric view and fit all visible objects")
        act_isometric.triggered.connect(self._viewport.isometric_view)

        act_projection = QAction(_icon("view-perspective"), "Projection", self)
        act_projection.setCheckable(True)
        act_projection.setChecked(self._viewport.is_parallel_projection())
        self._act_projection = act_projection
        act_projection.toggled.connect(self._viewport.set_parallel_projection)
        self._viewport.projection_changed.connect(self._sync_projection_action)
        self._sync_projection_action(act_projection.isChecked())
        act_recompute_model = QAction(_icon("view-refresh"), "", self)
        act_recompute_model.setToolTip("Recompute Model")
        act_recompute_model.setWhatsThis("Recompute parameter-driven geometry and sketch dimensions.")
        act_recompute_model.triggered.connect(self._recompute_model)
        add_group(
            "View",
            [act_fit_all, act_fit_selection, act_isometric, act_projection, act_recompute_model],
            columns=3,
        )

        # ������ Selection mode ���������������������������������������������������������������������������������������������������������������������������
        self._sel_mode_combo = QComboBox()
        self._sel_mode_combo.addItems(["All", "Surface", "Edge", "Vertex", "Grid"])
        self._sel_mode_combo.setFixedWidth(92)
        self._sel_mode_combo.setToolTip(
            "Selection mode:\n"
            "  All    - pick whole bodies\n"
            "  Surface- pick a single face/surface\n"
            "  Edge   - pick a single edge\n"
            "  Vertex - pick a single vertex\n"
            "  Grid   - use the active drawing grid instead of object geometry"
        )
        self._sel_mode_combo.currentTextChanged.connect(self._on_selection_mode_changed)
        add_group("Select", [self._sel_mode_combo], columns=1)

        act_measure = QAction(_icon("view-measurement"), "Measure", self)
        act_measure.setToolTip("Measure distances and axis projections between points and planar surfaces")
        act_measure.triggered.connect(self._open_measure_tool)
        act_parameters = QAction(_icon("table-grid"), "Parameters", self)
        act_parameters.setToolTip("Open the project parameter table")
        act_parameters.triggered.connect(self._open_project_parameters)
        add_group("Tools", [act_measure, act_parameters], columns=2)
        last_action = tb.actions()[-1] if tb.actions() else None
        if last_action is not None and last_action.isSeparator():
            tb.removeAction(last_action)

        sketch_tb = self.addToolBar("Sketch")
        self._sketch_context_toolbar = sketch_tb
        sketch_tb.setObjectName("sketch_context_toolbar")
        sketch_tb.setMovable(False)
        sketch_tb.setIconSize(QSize(18, 18))
        sketch_tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        sketch_tb.hide()

        def sketch_action(label, icon_name, callback, tooltip, *, fallback=None, enabled=True):
            icon = _icon(icon_name)
            if icon.isNull() and fallback:
                icon = _icon(fallback)
            action = QAction(icon, label, self)
            action.setObjectName(f"sketch_{label.lower().replace(' ', '_').replace('-', '_')}")
            action.setToolTip(tooltip)
            action.setEnabled(enabled)
            if callback is not None:
                action.triggered.connect(
                    lambda _checked=False, slot=callback: self._activate_sketch_toolbar_action(slot)
                )
            return action

        supported_2d = "Available in the viewport sketch engine."
        unsupported = "Not yet supported by the viewport sketch engine."
        extrude = sketch_action(
            "Extrude", "Part_Extrude", self._viewport._sketch_request_extrude,
            "Extrude the closed sketch profile.",
        )
        revolve = sketch_action(
            "Revolve", "Part_Revolve", self._viewport._sketch_begin_revolve,
            "Revolve the profile around a selected sketch line.",
        )
        extruded_cut = sketch_action(
            "Extruded Cut", "Part_Cut", self._viewport._sketch_request_extruded_cut,
            "Subtract the sketch extrusion from the single selected base object.",
        )
        self._sketch_context_actions = {
            action.text(): action for action in (extrude, revolve, extruded_cut)
        }
        add_group("3D", [extrude, revolve, extruded_cut], toolbar=sketch_tb, compact=True)

        def set_tool(tool_name):
            return lambda _checked=False: self._viewport._sketch_set_tool(tool_name)

        line = sketch_action(
            "Line", "Sketcher_CreateLine", set_tool("line"),
            "Draw a single line segment (two clicks).",
        )
        polyline = sketch_action(
            "Polyline", "Sketcher_CreatePolyline", set_tool("polyline"),
            "Draw a polyline; press Enter or Escape to finish it.",
        )
        corner_rectangle = sketch_action(
            "Corner Rectangle", "Sketcher_CreateRectangle",
            lambda: self._viewport._sketch_set_rectangle_mode("corner-corner"),
            f"{supported_2d} Define the rectangle by opposite corners.",
        )
        center_rectangle = sketch_action(
            "Center Rectangle", "Sketcher_CreateRectangle_Center",
            lambda: self._viewport._sketch_set_rectangle_mode("center-corner"),
            "Draw a rectangle from its center to one corner.",
        )
        construction = sketch_action(
            "Construction", "Sketcher_ToggleConstruction",
            lambda: self._viewport._sketch_set_construction_mode(
                self._sketch_context_actions["Construction"].isChecked()
            ),
            "Toggle construction geometry for newly drawn entities.",
        )
        construction.setCheckable(True)
        construction.toggled.connect(
            lambda checked: construction.setIcon(_icon(
                "Sketcher_ToggleConstruction_Constr" if checked
                else "Sketcher_ToggleConstruction"
            ))
        )
        vertex_snap = sketch_action(
            "Vertex Snap", "vertex-selection",
            lambda: self._viewport.set_sketch_vertex_snap(
                self._sketch_context_actions["Vertex Snap"].isChecked()
            ),
            "Snap drawing points to existing sketch vertices.",
        )
        vertex_snap.setCheckable(True)
        vertex_snap.setChecked(True)
        circle = sketch_action(
            "Circle", "Sketcher_CreateCircle", set_tool("circle"),
            "Draw a circle by center and radius.",
        )
        three_point_arc = sketch_action(
            "3-Point Arc", "Sketcher_Create3PointArc", set_tool("arc"),
            "Draw an arc by start, end, and midpoint.",
        )
        center_arc = sketch_action(
            "Center Arc", "Sketcher_CreateArc", set_tool("center_arc"),
            "Draw an arc by center, start point, and end angle.",
        )
        self._sketch_context_actions.update({
            action.text(): action
            for action in (
                line, polyline, corner_rectangle, center_rectangle, circle,
                construction, vertex_snap, three_point_arc, center_arc,
            )
        })
        add_group(
            "2D",
            [line, polyline, corner_rectangle, center_rectangle, construction, vertex_snap,
             circle, three_point_arc, center_arc],
            columns=5,
            toolbar=sketch_tb,
            compact=True,
        )

        dimensions = [
            sketch_action("Linear", "Constraint_Dimension",
                          lambda: self._viewport._sketch_begin_dimension("linear"),
                          "Dimension two sketch points along the dominant sketch axis."),
            sketch_action("Angular", "Constraint_InternalAngle",
                          lambda: self._viewport._sketch_begin_dimension("angular"),
                          "Measure two sketch lines; angular dimensions are annotations, not solver constraints."),
            sketch_action("Radius", "Constraint_Radius",
                          lambda: self._viewport._sketch_begin_dimension("radius"),
                          "Set or annotate a sketch circle or arc radius."),
            sketch_action("Diameter", "Constraint_Radiam",
                          lambda: self._viewport._sketch_begin_dimension("diameter"),
                          "Set or annotate a sketch circle or arc diameter."),
            sketch_action("Aligned", "Constraint_Length",
                          lambda: self._viewport._sketch_begin_dimension("aligned"),
                          "Dimension the true distance between two sketch points."),
        ]
        self._sketch_context_actions.update({action.text(): action for action in dimensions})
        add_group(
            "Dimensions", dimensions, columns=3, toolbar=sketch_tb, compact=True,
        )
        add_group(
            "View",
            [act_fit_all, act_fit_selection, act_isometric, act_projection, act_recompute_model],
            columns=5,
            toolbar=sketch_tb,
            compact=True,
        )
        select_sketch = sketch_action(
            "Select", "edit-select-box", self._viewport._sketch_set_selection_mode,
            "Select or deselect sketch geometry.",
        )
        delete_sketch = sketch_action(
            "Delete", "edit-delete", self._viewport._sketch_delete_selected,
            "Delete the selected sketch entity and its dimensions.",
        )
        exit_sketch = sketch_action(
            "Exit Sketch", "Sketcher_LeaveSketch",
            lambda: self._viewport.exit_sketch(commit=False),
            "Exit sketch mode without creating or updating a 3D feature.",
        )
        self._sketch_context_actions.update({
            action.text(): action for action in (select_sketch, delete_sketch, exit_sketch)
        })
        add_group(
            "Edit", [select_sketch, delete_sketch, exit_sketch],
            columns=3, toolbar=sketch_tb, compact=True,
        )

    def _sync_projection_action(self, parallel: bool) -> None:
        action = getattr(self, "_act_projection", None)
        if action is None:
            return
        parallel = bool(parallel)
        action.blockSignals(True)
        try:
            action.setChecked(parallel)
        finally:
            action.blockSignals(False)
        action.setIcon(_icon("view-axonometric" if parallel else "view-perspective"))
        mode = "parallel" if parallel else "perspective"
        target = "perspective" if parallel else "parallel"
        action.setToolTip(
            f"{mode.title()} projection is active. Click to switch to {target}."
        )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� signal wiring
    def _connect_signals(self) -> None:
        # Viewport ��� body props + materials
        self._viewport.object_selected.connect(self._on_object_selected)
        self._viewport.selection_changed.connect(self._on_selection_changed)
        self._viewport.scene_changed.connect(self._on_scene_changed)
        self._viewport.sketch_extrude_requested.connect(self._on_extrude_requested)
        self._viewport.sketch_cut_requested.connect(self._on_extruded_cut_requested)
        self._viewport.sketch_revolve_requested.connect(self._on_revolve_requested)
        self._viewport.sketch_finished.connect(self._on_viewport_sketch_finished)
        self._viewport.status_message.connect(self._info_bar.set_info)
        self._viewport.picked_coords.connect(self._info_bar.set_coords)
        self._viewport.clear_coords_requested.connect(self._info_bar.clear_coords)
        self._viewport.set_sketch_dimension_resolver(self._resolve_formula_text)

        # Body props ��� viewport render
        self._body_props.params_changed.connect(self._on_params_changed)
        self._body_props.bulk_material_changed.connect(self._on_bulk_material)
        self._body_props.bulk_style_changed.connect(self._on_bulk_style)
        self._body_props.model_role_changed.connect(self._on_model_role_changed)
        self._body_props.material_added.connect(self._on_material_added)
        self._body_props.material_picker_requested.connect(self._open_material_picker)
        self._body_props.project_parameters_changed.connect(self._on_project_parameters_changed)
        self._body_props.set_formula_resolver(self._resolve_formula_text)
        self._sync_project_variable_names()

        # Materials tree ��� selection (single + multi)
        self._materials.object_selected.connect(self._on_material_tree_select)
        self._materials.selection_changed.connect(self._on_material_tree_multi_select)
        self._materials.plane_make_active.connect(self._on_plane_make_active)
        self._materials.plane_view_normal.connect(self._on_plane_view_normal)
        self._materials.plane_delete.connect(self._on_plane_delete)
        self._materials.plane_rename.connect(self._on_plane_rename)
        self._materials.plane_add_requested.connect(self._open_reference_plane_dialog)
        self._materials.plane_triad_visibility_changed.connect(self._on_plane_triad_visibility_changed)
        self._materials.grid_visibility_changed.connect(self._on_grid_visibility_changed)
        self._materials.grid_adaptive_changed.connect(self._on_grid_adaptive_changed)
        self._materials.objects_hide.connect(self._on_materials_hide)
        self._materials.objects_show.connect(self._on_materials_show)
        self._materials.transform_edit_requested.connect(self._on_transform_edit_requested)
        self._materials.pattern_edit_requested.connect(self._create_object_pattern)
        self._materials.feature_edit_requested.connect(self._edit_sketch_feature)
        self._materials.sketch_edit_requested.connect(self._edit_sketch_definition)
        self._materials.objects_model_role_changed.connect(self._on_materials_model_role_changed)
        self._materials.object_rename.connect(self._on_materials_rename)
        self._materials.objects_bulk_rename.connect(self._on_materials_bulk_rename)
        self._materials.assign_port_requested.connect(self._on_assign_port_requested)
        self._materials.assign_boundary_requested.connect(self._on_assign_boundary_requested)
        self._materials.assign_mesh_resolution_requested.connect(self._on_assign_mesh_resolution_requested)
        self._materials.assign_mesh_refinement_requested.connect(self._on_assign_mesh_refinement_requested)
        self._materials.set_open_region_requested.connect(self._on_set_open_region_requested)
        self._materials.material_priority_changed.connect(self._on_material_priority_changed)

        # EMERGE settings changed
        self._project_tree.settings_changed.connect(self._on_settings_changed)
        self._project_tree.project_selected.connect(self._on_project_selected)
        self._project_tree.output_plot_requested.connect(self._on_output_plot_requested)

    def _reset_simulation_cache(self) -> None:
        self._sim_steps_dirty = True
        self._sim_script_dirty = True
        self._sim_step_bundle_ready = False
        self._sim_step_bundle_cache = {"entries": [], "skipped": []}
        self._simulation_step_export_cache = {}
        self._sim_full_scene_step_dirty = True
        self._sim_cached_script = ""
        self._sim_cached_script_bundle = {"master": "", "scripts": []}

    def _mark_simulation_dirty(self, *, steps: bool = False, script: bool = True) -> None:
        if steps:
            self._sim_steps_dirty = True
            self._sim_step_bundle_ready = False
            self._simulation_step_export_cache = {}
            self._sim_full_scene_step_dirty = True
        if script:
            self._sim_script_dirty = True

    def _on_scene_changed(self) -> None:
        if not self._history_restoring:
            self._history_record()
        self._viewport.scene.refresh_adaptive_grid()
        self._refresh_materials()
        self._sync_port_reference_state()
        self._mark_simulation_dirty(steps=True, script=True)

    def _history_reset(self) -> None:
        self._history_undo = [self._viewport.scene.to_json(cache_meshes=True)]
        self._history_redo = []
        self._update_history_actions()

    def _history_record(self) -> None:
        current = self._viewport.scene.to_json(cache_meshes=True)
        if self._history_undo and current == self._history_undo[-1]:
            return
        self._history_undo.append(current)
        max_states = self._history_limit + 1
        if len(self._history_undo) > max_states:
            self._history_undo = self._history_undo[-max_states:]
        self._history_redo.clear()
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        if hasattr(self, "_act_undo"):
            self._act_undo.setEnabled(len(self._history_undo) > 1)
            self._act_redo.setEnabled(bool(self._history_redo))

    def _restore_history_snapshot(self, snapshot: list[dict]) -> None:
        self._history_restoring = True
        try:
            self._viewport.scene.from_json(deepcopy(snapshot))
            self._viewport.scene.deselect_all()
            self._refresh_materials()
            self._sync_port_reference_state()
            self._body_props.set_object(None)
            self._viewport._render()
            self._viewport.scene_changed.emit()
        finally:
            self._history_restoring = False
            self._update_history_actions()

    def _undo(self) -> None:
        if len(self._history_undo) <= 1:
            return
        self._history_redo.append(self._history_undo.pop())
        self._restore_history_snapshot(self._history_undo[-1])
        self._info_bar.set_info("Undo")

    def _redo(self) -> None:
        if not self._history_redo:
            return
        snapshot = self._history_redo.pop()
        self._history_undo.append(snapshot)
        self._restore_history_snapshot(snapshot)
        self._info_bar.set_info("Redo")

    def _sync_port_reference_state(self) -> None:
        object_names = [str(o.name) for o in self._viewport.scene.objects if getattr(o, "name", None)]
        self._project_tree.set_scene_object_names(object_names)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� actions
    def _start_draw(self, mode: str) -> None:
        plane    = self._active_draw_plane_name()
        material = self._draw_material
        self._viewport.start_draw(mode, plane, material)

    def _create_plate_from_face(self) -> None:
        self._viewport.create_plate_from_face(self._draw_material)

    def _open_region_pml_wizard(self) -> None:
        from PySide6.QtWidgets import QDoubleSpinBox, QComboBox, QSpinBox
        from ..scene.em_objects import BoxObject

        models = [
            obj for obj in self._viewport.scene.objects
            if bool(getattr(obj, "is_model", True))
            and str(getattr(obj, "material", "")).strip().upper() not in {"AIR", "PML"}
            and getattr(obj, "actor", None) is not None
        ]
        if not models:
            QMessageBox.information(self, "Open Region / PML", "Create or import a model object first.")
            return

        bounds = [obj.actor.GetBounds() for obj in models]
        model_bounds = (
            min(item[0] for item in bounds), max(item[1] for item in bounds),
            min(item[2] for item in bounds), max(item[3] for item in bounds),
            min(item[4] for item in bounds), max(item[5] for item in bounds),
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Open Region / PML Wizard")
        form = QFormLayout(dlg)
        distance = QDoubleSpinBox(dlg); distance.setRange(0.001, 1e6); distance.setDecimals(4); distance.setValue(10.0)
        thickness = QDoubleSpinBox(dlg); thickness.setRange(0.001, 1e6); thickness.setDecimals(4); thickness.setValue(10.0)
        boundary = QComboBox(dlg); boundary.addItems(["Open", "Radiation", "PML"])
        pml_layers = QSpinBox(dlg); pml_layers.setRange(1, 20); pml_layers.setValue(1)
        pml_mesh_layers = QSpinBox(dlg); pml_mesh_layers.setRange(1, 50); pml_mesh_layers.setValue(5)
        pml_exponent = QDoubleSpinBox(dlg); pml_exponent.setRange(0.1, 10.0); pml_exponent.setDecimals(3); pml_exponent.setValue(1.5)
        pml_deltamax = QDoubleSpinBox(dlg); pml_deltamax.setRange(0.1, 100.0); pml_deltamax.setDecimals(3); pml_deltamax.setValue(8.0)
        form.addRow("Air distance", distance)
        form.addRow("PML thickness", thickness)
        form.addRow("Outer boundary", boundary)
        form.addRow("PML geometrical layers", pml_layers)
        form.addRow("PML mesh layers", pml_mesh_layers)
        form.addRow("PML exponent", pml_exponent)
        form.addRow("PML delta max", pml_deltamax)
        pml_controls = (thickness, pml_layers, pml_mesh_layers, pml_exponent, pml_deltamax)
        def _update_pml_controls(value: str) -> None:
            enabled = value == "PML"
            for control in pml_controls:
                control.setEnabled(enabled)
        boundary.currentTextChanged.connect(_update_pml_controls)
        _update_pml_controls(boundary.currentText())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return

        gap = float(distance.value())
        pml = float(thickness.value())
        inner = [
            model_bounds[0] - gap, model_bounds[1] + gap,
            model_bounds[2] - gap, model_bounds[3] + gap,
            model_bounds[4] - gap, model_bounds[5] + gap,
        ]
        outer = [
            inner[0] - pml, inner[1] + pml,
            inner[2] - pml, inner[3] + pml,
            inner[4] - pml, inner[5] + pml,
        ]
        existing = {str(obj.name) for obj in self._viewport.scene.objects}
        air_name = "Air_Region"
        pml_name = "PML_Region"
        index = 2
        while air_name in existing or pml_name in existing:
            air_name = f"Air_Region_{index}"
            pml_name = f"PML_Region_{index}"
            index += 1
        air = BoxObject(air_name, inner[0], inner[2], inner[4], inner[1], inner[3], inner[5], "AIR")
        air.is_model = True
        self._viewport.scene.add_object(air)
        pml_obj = None
        if boundary.currentText() == "PML":
            pml_obj = BoxObject(pml_name, outer[0], outer[2], outer[4], outer[1], outer[3], outer[5], "PML")
            pml_obj.is_model = True
            self._viewport.scene.add_object(pml_obj)
        settings = self._project_tree.get_settings()
        settings.setdefault("boundaries", {})
        for key in ("Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax"):
            settings["boundaries"][key] = boundary.currentText()
        settings["open_region"] = {
            "enabled": True,
            "object": air_name,
            "auto_update": True,
            "distance_mm": gap,
        }
        settings["pml"] = {
            "enabled": boundary.currentText() == "PML",
            "auto_update": pml_obj is not None,
            "air_object": air_name,
            "outer_object": pml_name if pml_obj is not None else "",
            "thickness_mm": pml,
            "layers": int(pml_layers.value()),
            "mesh_layers": int(pml_mesh_layers.value()),
            "exponent": float(pml_exponent.value()),
            "deltamax": float(pml_deltamax.value()),
        }
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()
        created_names = f"{air_name} and {pml_name}" if pml_obj is not None else air_name
        self._info_bar.set_info(f"Created {created_names} with {boundary.currentText()} boundaries")

    def _delete_selected(self) -> None:
        objects = list(self._viewport.scene.selection)
        if not objects:
            obj = self._viewport.scene.selected
            if obj is not None:
                objects = [obj]
        if not objects:
            return
        names = [str(obj.name) for obj in objects]
        for obj in objects:
            self._viewport.scene.remove_object(obj)
        self._viewport.scene.deselect_all()
        self._body_props.set_object(None)
        self._refresh_materials()
        self._sync_port_reference_state()
        self._viewport._render()
        self._viewport.scene_changed.emit()
        self._info_bar.set_info(f"Deleted {len(names)} object(s): {', '.join(names)}")

    def _scale_mesh_object(self, obj, factor: float) -> bool:
        actor = getattr(obj, "actor", None)
        if actor is None:
            return False
        scale = actor.GetScale()
        actor.SetScale(*(float(value) * factor for value in scale))
        return True

    def _scale_by_attributes(self, obj, factor: float, *, preserve_center: bool = True) -> bool:
        t = type(obj).__name__
        actor = getattr(obj, "actor", None)
        bounds_before = actor.GetBounds() if actor is not None else None
        position_before = actor.GetPosition() if actor is not None else None
        origin_before = actor.GetOrigin() if actor is not None else None

        def finish_scale() -> bool:
            if actor is not None and bounds_before is not None:
                if preserve_center:
                    bounds_after = actor.GetBounds()
                    delta = tuple(
                        (float(bounds_before[axis * 2]) + float(bounds_before[axis * 2 + 1])
                         - float(bounds_after[axis * 2]) - float(bounds_after[axis * 2 + 1])) * 0.5
                        for axis in range(3)
                    )
                    position = actor.GetPosition()
                    actor.SetPosition(*(float(position[axis]) + delta[axis] for axis in range(3)))
                elif position_before is not None:
                    actor.SetPosition(*(float(value) * factor for value in position_before))
                    actor.SetOrigin(*(float(value) * factor for value in origin_before))
            return True

        if t in {"BoxObject", "PlateObject"}:
            for name in ("x1", "y1", "z1", "x2", "y2", "z2"):
                setattr(obj, name, float(getattr(obj, name)) * factor)
            obj._refresh_source()
            obj.refresh_appearance()
            return finish_scale()

        if t in {"CylinderObject", "ConeObject"}:
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.radius *= factor
            obj.height *= factor
            obj._refresh_source()
            obj._apply_orientation()
            obj.refresh_appearance()
            return finish_scale()

        if t == "SphereObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.radius *= factor
            obj._refresh_source()
            obj.refresh_appearance()
            return finish_scale()

        if t == "PyramidObject":
            obj.bx1 *= factor
            obj.by1 *= factor
            obj.bx2 *= factor
            obj.by2 *= factor
            obj.bz *= factor
            obj.apex_z *= factor
            obj._refresh_source()
            obj._apply_position()
            obj.refresh_appearance()
            return finish_scale()

        if t == "WedgeObject":
            obj.x1 *= factor
            obj.y1 *= factor
            obj.z1 *= factor
            obj.x2 *= factor
            obj.y2 *= factor
            obj.z2 *= factor
            obj.x3 *= factor
            obj.y3 *= factor
            obj.z_height *= factor
            obj._source = obj._make_wedge_polydata()
            obj._actor.GetMapper().SetInputData(obj._source)
            obj.refresh_appearance()
            return finish_scale()

        if t == "TorusObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.major_radius *= factor
            obj.minor_radius *= factor
            obj._refresh_source()
            obj._actor.SetPosition(obj.cx, obj.cy, obj.cz)
            obj.refresh_appearance()
            return finish_scale()

        if t == "EllipsoidObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.rx *= factor
            obj.ry *= factor
            obj.rz *= factor
            obj._refresh_source()
            obj._apply_scale()
            obj.refresh_appearance()
            return finish_scale()

        if t == "ExtrudedObject":
            obj._profile_pts = _scale_profile_coordinates(obj._profile_pts, factor)
            obj._depth *= factor
            obj._plane_origin = tuple(float(v) * factor for v in obj._plane_origin)
            obj._rebuild()
            return finish_scale()

        if t == "RevolvedObject":
            bounds = obj.actor.GetBounds()
            center = tuple(
                (float(bounds[axis * 2]) + float(bounds[axis * 2 + 1])) * 0.5
                for axis in range(3)
            )

            def scale_world_point(point):
                if not preserve_center:
                    return tuple(float(value) * factor for value in point)
                return tuple(center[axis] + (float(point[axis]) - center[axis]) * factor
                             for axis in range(3))

            obj._profile_pts = _scale_profile_coordinates(obj._profile_pts, factor)
            obj._profile_contours = _scale_profile_coordinates(obj._profile_contours, factor)
            obj._axis_pt1 = scale_world_point(obj._axis_pt1)
            obj._axis_pt2 = scale_world_point(obj._axis_pt2)
            obj._plane_origin = scale_world_point(obj._plane_origin)
            local_pts, user_matrix = obj._profile_local_and_transform()
            obj._revolve.SetInputData(obj._profile_to_polydata(local_pts))
            obj._revolve.SetAngle(obj._angle)
            obj._revolve.Update()
            if obj._actor is not None:
                obj._actor.SetUserMatrix(user_matrix)
            obj.refresh_appearance()
            return True

        if t == "MeshObject":
            return finish_scale() if self._scale_mesh_object(obj, factor) else False

        return False

    def _scale_selected_objects(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(
                self,
                "Scale Objects",
                "Select one or more objects from the Object/Materials tree, then run Scale.",
            )
            return

        default_factor = self._format_locale_number(1.0)
        factor_txt, ok = QInputDialog.getText(
            self,
            "Scale Selected Objects",
            "Uniform scale factor:",
            text=default_factor,
        )
        if not ok:
            self._info_bar.set_info("Scale cancelled.")
            return

        try:
            factor = self._parse_locale_number(factor_txt)
        except ValueError:
            QMessageBox.warning(self, "Scale Objects", "Invalid scale factor for current locale.")
            return

        if factor <= 0.0:
            QMessageBox.warning(self, "Scale Objects", "Scale factor must be greater than zero.")
            return

        scaled = []
        failed = []
        for obj in selection:
            try:
                if self._scale_by_attributes(obj, float(factor)):
                    scaled.append(obj)
                else:
                    failed.append(str(getattr(obj, "name", type(obj).__name__)))
            except Exception as exc:
                failed.append(f"{getattr(obj, 'name', type(obj).__name__)} ({exc})")

        if scaled:
            self._viewport.scene_changed.emit()
            self._refresh_materials()
            self._viewport._render()
            self._viewport.selection_changed.emit(selection)
            self._mark_simulation_dirty(steps=True, script=True)

        msg = f"Scaled {len(scaled)} object(s) by factor {self._format_locale_number(factor)}."
        if failed:
            msg += f" Failed: {', '.join(failed)}"
        self._info_bar.set_info(msg)

    def _translate_mesh_object(self, obj, dx: float, dy: float, dz: float) -> bool:
        actor = getattr(obj, "actor", None)
        if actor is None:
            return False
        position = actor.GetPosition()
        actor.SetPosition(
            float(position[0]) + float(dx),
            float(position[1]) + float(dy),
            float(position[2]) + float(dz),
        )
        return True

    def _translate_object(self, obj, dx: float, dy: float, dz: float) -> bool:
        t = type(obj).__name__
        if t in {"BoxObject", "PlateObject"}:
            obj.x1 += dx; obj.y1 += dy; obj.z1 += dz
            obj.x2 += dx; obj.y2 += dy; obj.z2 += dz
            obj._refresh_source(); obj.refresh_appearance(); return True
        if t in {"CylinderObject", "ConeObject", "SphereObject"}:
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            if t in {"CylinderObject", "ConeObject"}:
                obj._apply_orientation()
            else:
                obj._refresh_source()
            obj.refresh_appearance(); return True
        if t == "PyramidObject":
            obj.bx1 += dx; obj.by1 += dy; obj.bx2 += dx; obj.by2 += dy
            obj.bz += dz; obj.apex_z += dz
            obj._refresh_source(); obj._apply_position(); obj.refresh_appearance(); return True
        if t == "WedgeObject":
            obj.x1 += dx; obj.y1 += dy; obj.z1 += dz
            obj.x2 += dx; obj.y2 += dy; obj.z2 += dz
            obj.x3 += dx; obj.y3 += dy
            obj._source = obj._make_wedge_polydata()
            obj._actor.GetMapper().SetInputData(obj._source)
            obj.refresh_appearance(); return True
        if t == "TorusObject":
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            obj._actor.SetPosition(obj.cx, obj.cy, obj.cz)
            obj.refresh_appearance(); return True
        if t == "EllipsoidObject":
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            obj._apply_scale(); obj.refresh_appearance(); return True
        if t == "ExtrudedObject":
            obj._plane_origin = (obj._plane_origin[0] + dx, obj._plane_origin[1] + dy, obj._plane_origin[2] + dz)
            obj._rebuild(); return True
        if t == "RevolvedObject":
            obj._axis_pt1 = (obj._axis_pt1[0] + dx, obj._axis_pt1[1] + dy, obj._axis_pt1[2] + dz)
            obj._axis_pt2 = (obj._axis_pt2[0] + dx, obj._axis_pt2[1] + dy, obj._axis_pt2[2] + dz)
            obj._plane_origin = (obj._plane_origin[0] + dx, obj._plane_origin[1] + dy, obj._plane_origin[2] + dz)
            local_pts, user_matrix = obj._profile_local_and_transform()
            obj._revolve.SetInputData(obj._profile_to_polydata(local_pts))
            obj._revolve.SetAngle(obj._angle)
            obj._revolve.Update()
            if obj._actor is not None:
                obj._actor.SetUserMatrix(user_matrix)
            obj.refresh_appearance(); return True
        if t == "MeshObject":
            return self._translate_mesh_object(obj, dx, dy, dz)
        return False

    def _move_selection_to_plane_origin(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(self, "Move/Rotate", "Select one or more objects first.")
            return

        bounds_list = [obj.actor.GetBounds() for obj in selection]
        if any(bounds is None for bounds in bounds_list):
            QMessageBox.warning(self, "Move/Rotate", "Unable to determine the selected object bounds.")
            return
        reference = [
            (min(float(bounds[0]) for bounds in bounds_list) + max(float(bounds[1]) for bounds in bounds_list)) * 0.5,
            (min(float(bounds[2]) for bounds in bounds_list) + max(float(bounds[3]) for bounds in bounds_list)) * 0.5,
            (min(float(bounds[4]) for bounds in bounds_list) + max(float(bounds[5]) for bounds in bounds_list)) * 0.5,
        ]

        dlg = QDialog(self)
        dlg.setWindowTitle("Move / Rotate Object")
        dlg.setModal(False)
        dlg.resize(360, 340)
        form = QFormLayout(dlg)

        def spin(value: float) -> QDoubleSpinBox:
            field = QDoubleSpinBox(dlg)
            field.setRange(-1e9, 1e9)
            field.setDecimals(6)
            field.setValue(value)
            return field

        ref_fields = [spin(value) for value in reference]
        target_fields = [spin(value) for value in reference]
        rotation_fields = [spin(0.0) for _ in range(3)]
        for field in rotation_fields:
            field.setRange(-360.0, 360.0)
            field.setSuffix(" deg")

        pick_button = QPushButton("Pick reference point on object", dlg)
        form.addRow(pick_button)
        pick_target_button = QPushButton("Pick target point in viewport", dlg)
        form.addRow(pick_target_button)
        form.addRow("Reference X", ref_fields[0])
        form.addRow("Reference Y", ref_fields[1])
        form.addRow("Reference Z", ref_fields[2])
        form.addRow("Target X", target_fields[0])
        form.addRow("Target Y", target_fields[1])
        form.addRow("Target Z", target_fields[2])
        form.addRow("Rotate X", rotation_fields[0])
        form.addRow("Rotate Y", rotation_fields[1])
        form.addRow("Rotate Z", rotation_fields[2])

        def set_reference(point: tuple[float, float, float]) -> None:
            for field, value in zip(ref_fields, point):
                field.setValue(float(value))

        pick_button.clicked.connect(
            lambda: self._viewport.request_pick("point", set_reference)
        )

        def set_target(point: tuple[float, float, float]) -> None:
            for field, value in zip(target_fields, point):
                field.setValue(float(value))

        pick_target_button.clicked.connect(
            lambda: self._viewport.request_pick("point", set_target)
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        dlg.show()

        def apply_transform() -> None:
            pivot = tuple(float(field.value()) for field in ref_fields)
            target = tuple(float(field.value()) for field in target_fields)
            dx = target[0] - pivot[0]
            dy = target[1] - pivot[1]
            dz = target[2] - pivot[2]
            rotate_x = float(rotation_fields[0].value())
            rotate_y = float(rotation_fields[1].value())
            rotate_z = float(rotation_fields[2].value())
            for selected_obj in selection:
                selected_actor = selected_obj.actor
                selected_actor.SetOrigin(*pivot)
                selected_actor.RotateX(rotate_x)
                selected_actor.RotateY(rotate_y)
                selected_actor.RotateZ(rotate_z)
                selected_actor.AddPosition(dx, dy, dz)
            self._viewport.scene_changed.emit()
            self._refresh_materials()
            self._viewport._render()
            self._viewport.selection_changed.emit(selection)
            self._mark_simulation_dirty(steps=True, script=True)
            self._info_bar.set_info(
                f"Transformed {len(selection)} object(s) around the selected reference point."
            )

        dlg.accepted.connect(apply_transform)
        return

    def _copy_selected_object_by_vertices(self) -> None:
        scene = self._viewport.scene
        selection = list(scene.selection)
        if len(selection) != 1:
            QMessageBox.information(
                self,
                "Copy by Vertices",
                "Select exactly one object to copy.",
            )
            return

        source = selection[0]
        source_actor = getattr(source, "actor", None)
        if source_actor is None:
            QMessageBox.information(
                self,
                "Copy by Vertices",
                "The selected object has no geometry to copy.",
            )
            return

        def pick_target(reference_point: tuple[float, float, float]) -> None:
            self._viewport.request_pick(
                "vertex",
                lambda target_point: create_copy(reference_point, target_point),
            )

        def create_copy(
            reference_point: tuple[float, float, float],
            target_point: tuple[float, float, float],
        ) -> None:
            if source not in scene.objects:
                self._info_bar.set_info("Copy cancelled because the source object was removed.")
                return

            snapshot = deepcopy(self._serialize_object_snapshot(source))
            clone_data = dict(snapshot)
            clone_data["params"] = dict(snapshot.get("params", {}))
            existing_names = {str(getattr(obj, "name", "")) for obj in scene.objects}
            base_name = f"{source.name}_Copy"
            clone_name = base_name
            suffix = 2
            while clone_name in existing_names:
                clone_name = f"{base_name}_{suffix}"
                suffix += 1
            clone_data["name"] = clone_name
            clone_data["params"]["Name"] = clone_name
            clone_data["pattern_definition"] = None
            clone_data["pattern_instance"] = None

            clone = self._rebuild_object_from_snapshot(clone_data)
            if clone is None or getattr(clone, "actor", None) is None:
                self._info_bar.set_info("Could not create a copy of the selected object.")
                return

            offset = tuple(
                float(target_point[index]) - float(reference_point[index])
                for index in range(3)
            )
            clone.actor.AddPosition(*offset)
            scene.add_object(clone)
            scene.deselect_all()
            scene.select_add(clone)
            self._refresh_materials()
            self._sync_port_reference_state()
            self._viewport._render()
            self._viewport.scene_changed.emit()
            self._viewport.selection_changed.emit([clone])
            self._mark_simulation_dirty(steps=True, script=True)
            self._info_bar.set_info(f"Copied {source.name} to {clone_name}.")

        self._viewport.request_pick(
            "vertex",
            pick_target,
            actor_filter=source_actor,
        )

    def _serialize_object_snapshot(self, obj, *, include_mesh: bool = True):
        item = {
            "type": type(obj).__name__,
            "name": str(getattr(obj, "name", type(obj).__name__)),
            "params": obj.get_parameters() if hasattr(obj, "get_parameters") else {},
            "visible": bool(obj.is_visible()) if hasattr(obj, "is_visible") else True,
            "is_model": bool(getattr(obj, "is_model", True)),
            "param_formulas": dict(getattr(obj, "param_formulas", {}) or {}),
            "creation_history": dict(getattr(obj, "creation_history", {}) or {}),
            "pattern_definition": deepcopy(getattr(obj, "pattern_definition", None)),
            "pattern_instance": deepcopy(getattr(obj, "pattern_instance", None)),
            "creation_reference_error": str(getattr(obj, "creation_reference_error", "")),
        }
        if type(obj).__name__ == "MeshObject" and include_mesh:
            mesh_poly = None
            if getattr(obj, "actor", None) is not None and obj.actor.GetMapper() is not None:
                mesh_poly = obj.actor.GetMapper().GetInput()
            item["mesh"] = self._viewport.scene._polydata_to_json(mesh_poly)
        actor = getattr(obj, "actor", None)
        if type(obj).__name__ == "MeshObject" and actor is not None:
            mapper = actor.GetMapper()
            mesh_poly = mapper.GetInput() if mapper is not None else None
            if mesh_poly is not None:
                item["mesh_mtime"] = int(mesh_poly.GetMTime())
        if actor is not None:
            item["actor_transform"] = {
                "origin": list(actor.GetOrigin()),
                "position": list(actor.GetPosition()),
                "orientation": list(actor.GetOrientation()),
                "scale": list(actor.GetScale()),
            }
        return item

    def _rebuild_object_from_snapshot(self, item):
        if not isinstance(item, dict):
            return None

        from ..scene import em_objects as emo

        t = item.get("type")
        cls = getattr(emo, str(t), None)
        if cls is None:
            return None

        name = str(item.get("name", ""))
        p = item.get("params", {})
        if not isinstance(p, dict):
            p = {}

        try:
            if t == "MeshObject":
                mesh_blob = item.get("mesh")
                if mesh_blob is None and str(p.get("StepSourcePath", "") or "").strip():
                    from ..emerge.step_importer import import_step

                    imported_solids = import_step(
                        str(p["StepSourcePath"]),
                        mm_per_unit=_MM_PER_UNIT.get(self._units, 1.0),
                    )
                    solid_name = str(p.get("StepSolidName", "") or "")
                    solid = next(
                        (entry for entry in imported_solids if entry.get("name") == solid_name),
                        imported_solids[0] if len(imported_solids) == 1 else None,
                    )
                    if solid is None:
                        raise ValueError(f"STEP solid '{solid_name}' could not be restored")
                    mesh_poly = solid["polydata"]
                else:
                    mesh_poly = self._viewport.scene._polydata_from_json(mesh_blob)
                obj = cls(
                    name,
                    mesh_poly,
                    p.get("Material", "PEC"),
                    step_source_path=p.get("StepSourcePath"),
                    step_solid_name=p.get("StepSolidName"),
                    plate_role=bool(p.get("PlateRole", False)),
                    boolean_op=p.get("BooleanOperation"),
                    boolean_source_names=p.get("BooleanSourceNames"),
                    boolean_sources_data=p.get("BooleanSourcesData"),
                )
                if hasattr(obj, "set_parameters"):
                    obj.set_parameters(p)
                if (
                    "StepGeometryModified" not in p
                    and mesh_blob is not None
                    and str(p.get("StepSourcePath", "") or "").strip()
                ):
                    obj.step_geometry_modified = True
            else:
                obj = cls(name=name, material=p.get("Material", "PEC"))
                if hasattr(obj, "set_parameters"):
                    obj.set_parameters(p)

            obj.set_visible(bool(item.get("visible", True)))
            obj.is_model = bool(item.get("is_model", True))
            formulas = item.get("param_formulas", {})
            obj.param_formulas = dict(formulas) if isinstance(formulas, dict) else {}
            history = item.get("creation_history", {})
            obj.creation_history = dict(history) if isinstance(history, dict) else {}
            pattern = item.get("pattern_definition")
            obj.pattern_definition = deepcopy(pattern) if isinstance(pattern, dict) else None
            pattern_instance = item.get("pattern_instance")
            obj.pattern_instance = deepcopy(pattern_instance) if isinstance(pattern_instance, dict) else None
            obj.creation_reference_error = str(item.get("creation_reference_error", ""))
            obj.refresh_appearance()
            transform = item.get("actor_transform", {})
            if isinstance(transform, dict) and obj.actor is not None:
                origin = transform.get("origin")
                position = transform.get("position")
                orientation = transform.get("orientation")
                scale = transform.get("scale")
                if isinstance(origin, (list, tuple)) and len(origin) == 3:
                    obj.actor.SetOrigin(*[float(value) for value in origin])
                if isinstance(position, (list, tuple)) and len(position) == 3:
                    obj.actor.SetPosition(*[float(value) for value in position])
                if isinstance(orientation, (list, tuple)) and len(orientation) == 3:
                    obj.actor.SetOrientation(*[float(value) for value in orientation])
                if isinstance(scale, (list, tuple)) and len(scale) == 3:
                    obj.actor.SetScale(*[float(value) for value in scale])
            return obj
        except Exception:
            return None

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� boolean operations
    def _create_object_pattern(self, edit_object=None) -> None:
        import math
        import uuid

        existing_definition = deepcopy(getattr(edit_object, "pattern_definition", None)) if edit_object is not None else None
        editing_pattern = isinstance(existing_definition, dict)
        if editing_pattern:
            stored_sources = existing_definition.get("sources", [])
            selection = [self._rebuild_object_from_snapshot(snapshot) for snapshot in stored_sources]
            selection = [source for source in selection if source is not None]
        else:
            stored_sources = []
            selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(self, "Pattern", "Select one or more objects first.")
            return

        source_bounds = []
        for source in selection:
            actor = getattr(source, "actor", None)
            bounds = actor.GetBounds() if actor is not None else None
            if bounds is None:
                QMessageBox.warning(self, "Pattern", f"Unable to determine the bounds for {source.name}.")
                return
            source_bounds.append(bounds)

        bounds = (
            min(float(item[0]) for item in source_bounds), max(float(item[1]) for item in source_bounds),
            min(float(item[2]) for item in source_bounds), max(float(item[3]) for item in source_bounds),
            min(float(item[4]) for item in source_bounds), max(float(item[5]) for item in source_bounds),
        )

        pivot = tuple((float(bounds[i]) + float(bounds[i + 1])) * 0.5 for i in (0, 2, 4))
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Object Pattern" if editing_pattern else "Create Object Pattern")
        form = QFormLayout(dlg)
        mode = QComboBox(dlg); mode.addItems(["Linear", "Circular"])
        count = FormulaIntSpinBox(dlg); count.setRange(1, 1000); count.setValue(2)
        axis_checks = [QCheckBox(axis_name, dlg) for axis_name in ("X", "Y", "Z")]
        axis_counts = [FormulaIntSpinBox(dlg) for _ in range(3)]
        for index, field in enumerate(axis_counts):
            field.setRange(1, 1000)
            field.setValue(2 if index == 0 else 1)
        axis_checks[0].setChecked(True)
        offsets = [QDoubleSpinBox(dlg) for _ in range(3)]
        for field in offsets:
            field.setRange(-1e9, 1e9); field.setDecimals(6); field.setValue(0.0)
        axis = QComboBox(dlg); axis.addItems(["X", "Y", "Z"])
        angle = QDoubleSpinBox(dlg); angle.setRange(-3600.0, 3600.0); angle.setDecimals(4); angle.setValue(360.0); angle.setSuffix(" deg")
        circular_fields = [QDoubleSpinBox(dlg) for _ in range(9)]
        for field in circular_fields:
            field.setRange(-1e9, 1e9); field.setDecimals(6)
        center_fields = circular_fields[0:3]
        axis_start_fields = circular_fields[3:6]
        axis_end_fields = circular_fields[6:9]
        for field, value in zip(center_fields, pivot): field.setValue(value)
        for field, value in zip(axis_start_fields, pivot): field.setValue(value)
        axis_end_fields[0].setValue(pivot[0]); axis_end_fields[1].setValue(pivot[1]); axis_end_fields[2].setValue(pivot[2] + 1.0)
        default_radius = max((sum((float(bounds[i + 1]) - float(bounds[i])) ** 2 for i in (0, 2, 4))) ** 0.5 * 0.5, 1.0)
        radius = QDoubleSpinBox(dlg); radius.setRange(0.0, 1e9); radius.setDecimals(6); radius.setValue(default_radius)
        formula_restore_widgets = []
        if editing_pattern:
            stored_settings = existing_definition.get("settings", {})
            instance_settings = getattr(edit_object, "pattern_instance", None)
            if isinstance(instance_settings, dict) and isinstance(instance_settings.get("settings"), dict):
                stored_settings = instance_settings["settings"]
            mode.setCurrentText(str(stored_settings.get("mode", "Linear")))
            axis.setCurrentText(str(stored_settings.get("axis", "X")))
            for check, enabled in zip(axis_checks, stored_settings.get("axis_enabled", (True, False, False))):
                check.setChecked(bool(enabled))
            expressions = stored_settings.get("expressions", {})
            numeric_widgets = {
                "count": count,
                "axis_count_x": axis_counts[0], "axis_count_y": axis_counts[1], "axis_count_z": axis_counts[2],
                "offset_x": offsets[0], "offset_y": offsets[1], "offset_z": offsets[2],
                "angle": angle, "center_x": center_fields[0], "center_y": center_fields[1], "center_z": center_fields[2],
                "axis_start_x": axis_start_fields[0], "axis_start_y": axis_start_fields[1], "axis_start_z": axis_start_fields[2],
                "axis_end_x": axis_end_fields[0], "axis_end_y": axis_end_fields[1], "axis_end_z": axis_end_fields[2],
                "radius": radius,
            }
            inputs = stored_settings.get("inputs", {})
            inputs = inputs if isinstance(inputs, dict) else {}
            resolved_values = stored_settings.get("resolved", {})
            resolved_values = resolved_values if isinstance(resolved_values, dict) else {}
            for key, widget in numeric_widgets.items():
                saved_input = inputs.get(key)
                if isinstance(saved_input, dict):
                    if saved_input.get("type") == "formula":
                        widget.set_formula(str(saved_input.get("formula", "")))
                        formula_restore_widgets.append(widget)
                    elif "value" in saved_input:
                        widget.setValue(float(saved_input["value"]))
                    continue
                raw = expressions.get(key)
                if raw is not None:
                    widget.set_formula(str(raw))
                    formula_restore_widgets.append(widget)
                elif key in resolved_values:
                    widget.setValue(float(resolved_values[key]))
        pick_center = QPushButton("Pick center", dlg)
        pick_axis_start = QPushButton("Pick axis point 1", dlg)
        pick_axis_end = QPushButton("Pick axis point 2", dlg)
        preview = _PatternPreview(dlg)
        form.addRow(preview)
        form.addRow("Pattern type", mode)
        form.addRow("Circular instances", count)
        axis_rows = []
        for axis_name, check, number, distance in zip(("X", "Y", "Z"), axis_checks, axis_counts, offsets):
            row = QHBoxLayout()
            row.addWidget(check)
            row.addWidget(QLabel("instances"))
            row.addWidget(number)
            row.addWidget(QLabel("step"))
            row.addWidget(distance)
            form.addRow(f"Linear {axis_name}", row)
            axis_rows.append(row)
        form.addRow("Total angle", angle)
        def coordinate_row(fields, button):
            row = QHBoxLayout()
            for field in fields:
                row.addWidget(field)
            row.addWidget(button)
            return row

        form.addRow("Circle center", coordinate_row(center_fields, pick_center))
        form.addRow("Distance from axis", radius)
        form.addRow("Axis point 1", coordinate_row(axis_start_fields, pick_axis_start))
        form.addRow("Axis point 2", coordinate_row(axis_end_fields, pick_axis_end))

        def set_fields(fields, point):
            for field, value in zip(fields, point):
                field.setValue(float(value))

        def pick_into(fields):
            dlg.hide()
            self._viewport.request_pick("point", lambda point: (set_fields(fields, point), dlg.show(), dlg.raise_(), dlg.activateWindow()))

        pick_center.clicked.connect(lambda: pick_into(center_fields))
        pick_axis_start.clicked.connect(lambda: pick_into(axis_start_fields))
        pick_axis_end.clicked.connect(lambda: pick_into(axis_end_fields))

        def update_visibility(pattern_type: str) -> None:
            linear = pattern_type == "Linear"
            for check, number, field in zip(axis_checks, axis_counts, offsets):
                check.setEnabled(linear); number.setEnabled(linear and check.isChecked()); field.setEnabled(linear and check.isChecked())
            axis.setEnabled(not linear); angle.setEnabled(not linear)
            for field in (*center_fields, *axis_start_fields, *axis_end_fields, radius, pick_center, pick_axis_start, pick_axis_end):
                field.setEnabled(not linear)

        def update_preview(*_args) -> None:
            try:
                resolved_counts = [field.value() for field in axis_counts]
                resolved_offsets = [field.value() for field in offsets]
                circular_count = count.value()
                total_angle = angle.value()
                linear_count = 1
                for check, resolved_count in zip(axis_checks, resolved_counts):
                    if check.isChecked():
                        linear_count *= resolved_count
            except (ArithmeticError, TypeError, ValueError):
                return
            preview.set_pattern(
                mode.currentText(), linear_count if mode.currentText() == "Linear" else circular_count, resolved_offsets,
                axis.currentText(), total_angle,
                [check.isChecked() for check in axis_checks],
                resolved_counts,
            )
        update_visibility(mode.currentText()); mode.currentTextChanged.connect(update_visibility)
        mode.currentTextChanged.connect(update_preview)
        count.valueChanged.connect(update_preview)
        for field in offsets: field.valueChanged.connect(update_preview)
        count.lineEdit().textChanged.connect(update_preview)
        for field in offsets: field.lineEdit().textChanged.connect(update_preview)
        for check in axis_checks: check.toggled.connect(lambda _checked: (update_visibility(mode.currentText()), update_preview()))
        for field in axis_counts: field.valueChanged.connect(update_preview)
        for field in axis_counts: field.lineEdit().textChanged.connect(update_preview)
        axis.currentTextChanged.connect(update_preview)
        angle.valueChanged.connect(update_preview)
        update_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); form.addRow(buttons)
        if formula_restore_widgets:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                25,
                lambda widgets=tuple(formula_restore_widgets): [widget._restore_formula_text() for widget in widgets],
            )
        if dlg.exec_() != QDialog.Accepted:
            return

        try:
            instance_count = count.value()
            linear_counts = [field.value() for field in axis_counts]
            linear_steps = [field.value() for field in offsets]
            total_angle = angle.value()
            circular_center = tuple(field.value() for field in center_fields)
            axis_start = tuple(field.value() for field in axis_start_fields)
            axis_end = tuple(field.value() for field in axis_end_fields)
            requested_radius = radius.value()
        except Exception as exc:
            QMessageBox.warning(dlg, "Invalid Formula", str(exc))
            return

        numeric_widgets = {
            "count": count,
            "axis_count_x": axis_counts[0], "axis_count_y": axis_counts[1], "axis_count_z": axis_counts[2],
            "offset_x": offsets[0], "offset_y": offsets[1], "offset_z": offsets[2],
            "angle": angle, "center_x": center_fields[0], "center_y": center_fields[1], "center_z": center_fields[2],
            "axis_start_x": axis_start_fields[0], "axis_start_y": axis_start_fields[1], "axis_start_z": axis_start_fields[2],
            "axis_end_x": axis_end_fields[0], "axis_end_y": axis_end_fields[1], "axis_end_z": axis_end_fields[2],
            "radius": radius,
        }
        expressions = {
            key: widget.formula_text() if hasattr(widget, "formula_text") else widget.lineEdit().text().strip()
            for key, widget in numeric_widgets.items()
        }
        resolved = {key: float(widget.value()) for key, widget in numeric_widgets.items()}
        inputs = {}
        for key, raw in expressions.items():
            numeric_text = raw
            suffix = numeric_widgets[key].suffix().strip()
            if suffix and numeric_text.lower().endswith(suffix.lower()):
                numeric_text = numeric_text[:-len(suffix)].strip()
            try:
                float(numeric_text.replace(",", "."))
                inputs[key] = {"type": "value", "value": resolved[key]}
            except ValueError:
                inputs[key] = {
                    "type": "formula",
                    "formula": raw,
                    "value": resolved[key],
                }
        pattern_id = str(existing_definition.get("pattern_id")) if editing_pattern else str(uuid.uuid4())
        definition = {
            "pattern_id": pattern_id,
            "sources": deepcopy(stored_sources) if editing_pattern else [self._serialize_object_snapshot(source) for source in selection],
            "settings": {
                "mode": mode.currentText(), "axis": axis.currentText(),
                "axis_enabled": [check.isChecked() for check in axis_checks],
                "expressions": expressions, "inputs": inputs, "resolved": resolved,
                "parameter_values": self._parameter_values(),
            },
        }

        # Ensure Undo returns to the current original objects, even when the
        # pattern is the first operation after they were created.
        self._history_record()
        source_data = [
            (
                source,
                deepcopy(stored_sources[index]) if editing_pattern else self._serialize_object_snapshot(source),
                tuple((float(bounds[index]) + float(bounds[index + 1])) * 0.5 for index in (0, 2, 4)),
            )
            for index, (source, bounds) in enumerate(zip(selection, source_bounds))
        ]
        created = []
        if mode.currentText() == "Linear":
            import itertools
            enabled_axes = [index for index, check in enumerate(axis_checks) if check.isChecked()]
            if not enabled_axes:
                QMessageBox.warning(self, "Pattern", "Enable at least one linear axis.")
                return
            axis_ranges = [range(linear_counts[index]) if index in enabled_axes else range(1) for index in range(3)]
            linear_offsets = [
                (linear_steps[0] * ix, linear_steps[1] * iy, linear_steps[2] * iz)
                for ix, iy, iz in itertools.product(*axis_ranges)
                if (ix, iy, iz) != (0, 0, 0)
            ]
        else:
            linear_offsets = []

        axis_vector = tuple(axis_end[index] - axis_start[index] for index in range(3))
        axis_length = sum(value * value for value in axis_vector) ** 0.5
        if mode.currentText() == "Circular" and axis_length < 1e-12:
            QMessageBox.warning(self, "Pattern", "Rotation axis points must be different.")
            return
        if axis_length > 1e-12:
            axis_vector = tuple(value / axis_length for value in axis_vector)

        source_bounds_center = tuple((float(bounds[i]) + float(bounds[i + 1])) * 0.5 for i in (0, 2, 4))
        radial = tuple(source_bounds_center[index] - circular_center[index] for index in range(3))
        radial_length = sum(value * value for value in radial) ** 0.5
        if mode.currentText() == "Circular" and radial_length < 1e-12:
            radial = (1.0, 0.0, 0.0)
            radial_length = 1.0

        def rotate_vector(vector, axis_value, degrees):
            radians = math.radians(degrees)
            cosine = math.cos(radians)
            sine = math.sin(radians)
            cross = (
                axis_value[1] * vector[2] - axis_value[2] * vector[1],
                axis_value[2] * vector[0] - axis_value[0] * vector[2],
                axis_value[0] * vector[1] - axis_value[1] * vector[0],
            )
            dot = sum(axis_value[index] * vector[index] for index in range(3))
            return tuple(
                vector[index] * cosine + cross[index] * sine + axis_value[index] * dot * (1.0 - cosine)
                for index in range(3)
            )

        instance_specs = (
            enumerate(linear_offsets, start=1)
            if mode.currentText() == "Linear"
            else enumerate(range(1, instance_count), start=1)
        )
        for instance_index, linear_offset in instance_specs:
            for source_index, (source, snapshot, source_center) in enumerate(source_data):
                clone_data = dict(snapshot); clone_data["params"] = dict(snapshot.get("params", {}))
                source_name = str(getattr(source, "name", type(source).__name__))
                clone_data["name"] = f"{source_name}_Pattern_{instance_index + 1}"
                clone_data["params"]["Name"] = clone_data["name"]
                clone_data["pattern_definition"] = deepcopy(definition)
                clone_data["pattern_instance"] = {
                    "instance_index": int(instance_index),
                    "source_index": int(source_index),
                    "source_name": source_name,
                    "settings": deepcopy(definition["settings"]),
                }
                clone = self._rebuild_object_from_snapshot(clone_data)
                if clone is None:
                    continue
                clone_actor = getattr(clone, "actor", None)
                if clone_actor is None:
                    continue
                if mode.currentText() == "Linear":
                    clone_actor.AddPosition(*linear_offset)
                else:
                    step_angle = total_angle * instance_index / max(1, instance_count - 1)
                    direction = tuple(value / radial_length for value in radial)
                    rotated_direction = rotate_vector(direction, axis_vector, step_angle)
                    group_target = tuple(
                        circular_center[index] + rotated_direction[index] * requested_radius
                        for index in range(3)
                    )
                    relative = tuple(source_center[index] - source_bounds_center[index] for index in range(3))
                    rotated_relative = rotate_vector(relative, axis_vector, step_angle)
                    target_center = tuple(group_target[index] + rotated_relative[index] for index in range(3))
                    clone_actor.SetOrigin(*source_center)
                    clone_actor.RotateWXYZ(step_angle, *axis_vector)
                    clone_bounds = clone_actor.GetBounds()
                    clone_center = tuple((float(clone_bounds[index]) + float(clone_bounds[index + 1])) * 0.5 for index in (0, 2, 4))
                    clone_actor.AddPosition(*(target_center[index] - clone_center[index] for index in range(3)))
                if not editing_pattern:
                    self._viewport.scene.add_object(clone)
                created.append(clone)

        if not created:
            QMessageBox.warning(self, "Pattern", "No pattern instances could be created.")
            return
        if editing_pattern:
            for obj in list(self._viewport.scene.objects):
                obj_definition = getattr(obj, "pattern_definition", None)
                if isinstance(obj_definition, dict) and obj_definition.get("pattern_id") == pattern_id:
                    self._viewport.scene.remove_object(obj)
            for clone in created:
                self._viewport.scene.add_object(clone)
        self._viewport.scene.deselect_all()
        for clone in created: self._viewport.scene.select_add(clone)
        self._refresh_materials(); self._sync_port_reference_state(); self._viewport._render()
        self._viewport.scene_changed.emit(); self._viewport.selection_changed.emit(created)
        self._mark_simulation_dirty(steps=True, script=True)
        self._info_bar.set_info(f"Created {len(created)} pattern instance(s) from {len(selection)} object(s).")

    def _do_boolean(self, op: str, label: str) -> None:
        from ..scene.boolean_ops import boolean_many, fuse_many
        from ..scene.em_objects import MeshObject

        sel = list(self._viewport.scene.selection)
        if len(sel) < 2:
            QMessageBox.information(
                self,
                f"Boolean {label}",
                "Select at least 2 objects (viewport or Object/Materials tree), then run the operation.\n\n"
                "Seleziona da Object/Materials tree con Ctrl/Shift-click per multi-selezione.\n"
                "First selected = Base, all others = Tools.",
            )
            return

        if op == "cut":
            setup = _BooleanCutSetupDialog(sel, self)
            if setup.exec_() != QDialog.Accepted:
                self._info_bar.set_info(f"Boolean {label} cancelled.")
                return
            bases = setup.base_objects
            tools = setup.tool_objects
            keep_tools = setup.keep_tools
        else:
            bases = [sel[0]]
            tools = sel[1:]
        base = bases[0]
        names = ", ".join(o.name for o in tools[:8])
        if len(tools) > 8:
            names += f", ... (+{len(tools)-8} more)"

        if op != "cut":
            reply = QMessageBox.question(
                self,
                f"Confirm Boolean {label}",
                f"Perform Boolean {label}?\n\n"
                f"Base : {base.name}\n"
                f"Tools: {len(tools)} object(s)\n"
                f"{names}\n\n"
                "All source objects will be removed and replaced by one result.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                self._info_bar.set_info(f"Boolean {label} cancelled.")
                return

        mesh_reduction = 0.0
        if self._boolean_decimation_enabled:
            reduction_options = [f"{value}%" for value in range(0, 81, 5)]
            reduction_choice, accepted = QInputDialog.getItem(
                self,
            f"Simplify {label} Mesh",
                "Reduce result triangles by this percentage (0 keeps full detail):",
                reduction_options,
                0,
                False,
            )
            if not accepted:
                self._info_bar.set_info(f"Boolean {label} cancelled.")
                return
            mesh_reduction = int(reduction_choice[:-1]) / 100.0

        result_polys = []
        try:
            for current_base in bases:
                operands = [current_base] + tools
                if op == "fuse":
                    current_poly = fuse_many(operands, target_reduction=mesh_reduction)
                elif mesh_reduction > 0.0:
                    current_poly = boolean_many(op, operands, target_reduction=mesh_reduction)
                else:
                    current_poly = boolean_many(op, operands)
                result_polys.append(current_poly)
        except Exception as exc:
            QMessageBox.critical(self, f"Boolean {label} failed", str(exc))
            self._info_bar.set_info(f"Boolean {label} failed: {exc}")
            return

        scene = self._viewport.scene
        results = []
        for current_base, current_poly in zip(bases, result_polys):
            sources = [current_base] + tools
            result = MeshObject(
                name=f"{label}_{current_base.name}",
                polydata=current_poly,
                material=current_base.material,
                plate_role=(op == "cut" and self._is_plate_role_object(current_base)),
                boolean_op=op,
                boolean_source_names=[obj.name for obj in sources],
            )
            result.source_objects = list(sources)
            result.boolean_mesh_reduction = mesh_reduction
            result.boolean_sources_data = [
                self._serialize_object_snapshot(obj) for obj in sources
            ]
            result.refresh_appearance()
            scene.add_object(result)
            results.append(result)

        objects_to_remove = list(bases) + ([] if keep_tools else tools)
        for obj in objects_to_remove:
            scene.remove_object(obj)
        scene.deselect_all()
        for result in results:
            scene.select_add(result)

        self._viewport.object_selected.emit(results[-1])
        self._viewport.selection_changed.emit(results)
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()
        self._info_bar.set_info(
            f"Boolean {label}: created {len(results)} result(s) from "
            f"{len(bases)} base object(s) and {len(tools)} tool(s)"
            + ("; tools retained" if keep_tools else "")
        )

    def _bool_cut(self) -> None:
        self._do_boolean("cut", "Cut")

    def _bool_fuse(self) -> None:
        self._do_boolean("fuse", "Fuse")

    def _bool_common(self) -> None:
        self._do_boolean("common", "Common")

    def _bool_dissolve(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(
                self,
                "Dissolve Boolean",
                "Select one or more boolean result objects, then run Dissolve Boolean.",
            )
            return

        candidates = []
        unavailable = []
        for obj in selection:
            op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
            sources = list(getattr(obj, "source_objects", []) or [])
            source_data = list(getattr(obj, "boolean_sources_data", []) or [])
            if op in {"fuse", "cut", "common"} and (sources or source_data):
                candidates.append((obj, sources, source_data))
            else:
                unavailable.append(str(getattr(obj, "name", type(obj).__name__)))

        if not candidates:
            detail = ""
            if unavailable:
                detail = "\n\nUnavailable: " + ", ".join(unavailable)
            QMessageBox.information(
                self,
                "Dissolve Boolean",
                "Selected objects do not contain dissolvable boolean provenance." + detail,
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm Dissolve Boolean",
            f"Dissolve {len(candidates)} boolean result(s) and restore their source objects?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._info_bar.set_info("Dissolve Boolean cancelled.")
            return

        scene = self._viewport.scene
        dissolved_names = {
            str(getattr(result_obj, "name", "")).strip()
            for result_obj, _sources, _source_data in candidates
            if str(getattr(result_obj, "name", "")).strip()
        }
        restored = []
        restored_ids = {id(obj) for obj in scene.objects}

        for result_obj, sources, source_data in candidates:
            live_sources_by_name = {}
            for source_obj in sources:
                if source_obj is result_obj:
                    continue
                live_sources_by_name.setdefault(
                    str(getattr(source_obj, "name", "")), []
                ).append(source_obj)
            source_occurrences = {}
            snapshot_sources = []
            for item in source_data:
                source_name = str(item.get("name", "")).strip() if isinstance(item, dict) else ""
                occurrence = source_occurrences.get(source_name, 0)
                source_occurrences[source_name] = occurrence + 1
                live_matches = live_sources_by_name.get(source_name, [])
                snapshot_sources.append(
                    live_matches[occurrence] if occurrence < len(live_matches) else None
                )

            # Remove result first so source names equal to result name can be restored.
            result_name = str(getattr(result_obj, "name", ""))
            scene.remove_object(result_obj)
            restored_ids.discard(id(result_obj))

            added_this_result = 0
            if sources:
                for source_obj in sources:
                    # Guard against corrupted provenance where the result object
                    # is accidentally present in its own source list.
                    if source_obj is result_obj:
                        continue
                    if id(source_obj) in restored_ids:
                        # Already in scene – still count it so selection works
                        if source_obj in scene.objects:
                            restored.append(source_obj)
                            added_this_result += 1
                        continue
                    scene.add_object(source_obj)
                    source_obj.refresh_appearance()
                    restored.append(source_obj)
                    restored_ids.add(id(source_obj))
                    added_this_result += 1

            # Rebuild each missing source from its snapshot. After reopening a
            # project, retained tools may be live while the removed base is not.
            for source_index, item in enumerate(source_data):
                if not isinstance(item, dict):
                    continue
                source_obj = (
                    snapshot_sources[source_index]
                    if source_index < len(snapshot_sources) else None
                )
                if source_obj is not None:
                    if source_obj in scene.objects and source_obj not in restored:
                        restored.append(source_obj)
                    continue
                rebuilt = self._rebuild_object_from_snapshot(item)
                if rebuilt is None:
                    continue
                scene.add_object(rebuilt)
                restored.append(rebuilt)
                restored_ids.add(id(rebuilt))

        # Rebuild the tree FIRST so that highlight() can find the restored objects.
        self._viewport.scene_changed.emit()
        self._refresh_materials()

        if restored:
            scene.deselect_all()
            for source_obj in restored:
                scene.select_add(source_obj)
            self._viewport.object_selected.emit(restored[-1])
            self._viewport.selection_changed.emit(restored)
        else:
            scene.select(None)
            self._viewport.object_selected.emit(None)
            self._viewport.selection_changed.emit([])

        self._mark_broken_creation_references(dissolved_names)
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()

        message = f"Dissolved {len(candidates)} boolean result(s); restored {len(restored)} source object(s)"
        if unavailable:
            message += f". Skipped: {', '.join(unavailable)}"
        self._info_bar.set_info(message)

    def _mark_broken_creation_references(self, removed_names: set[str] | None = None) -> None:
        """Mark objects whose vertex/edge/surface creation references no longer resolve."""
        removed_names = {str(name).strip() for name in (removed_names or set()) if str(name).strip()}
        existing_names = {str(getattr(obj, "name", "")).strip() for obj in self._viewport.scene.objects}
        for obj in self._viewport.scene.objects:
            obj.creation_reference_error = ""
            history = getattr(obj, "creation_history", {})
            points = history.get("points", []) if isinstance(history, dict) else []
            broken = []
            for item in points if isinstance(points, list) else []:
                if not isinstance(item, dict):
                    continue
                snap = item.get("snap", {})
                if not isinstance(snap, dict):
                    continue
                source_name = str(snap.get("object", "")).strip()
                if not source_name:
                    continue
                if source_name in removed_names or source_name not in existing_names:
                    broken.append(source_name)
            if broken:
                obj.creation_reference_error = (
                    "Creation reference lost after Dissolve Boolean: "
                    + ", ".join(sorted(set(broken)))
                )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� object selection
    def _on_project_selected(self) -> None:
        self._project_properties_active = True
        parameters = self._project_tree.get_settings().get("parameters", [])
        self._materials.highlight([])
        if hasattr(self._viewport.scene, "deselect_all"):
            self._viewport.scene.deselect_all()
        self._body_props.set_project_parameters(parameters)
        self._sync_project_variable_names(parameters)
        self._info_bar.set_info(f"Project: {self._project_name}")

    def _on_project_parameters_changed(self, parameters: list) -> None:
        settings = self._project_tree.get_settings()
        settings["parameters"] = parameters
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._recompute_simulation_parameters(settings)
        self._recompute_parametric_objects()
        self.recompute_sketch_dimensions()
        self._mark_simulation_dirty(steps=True, script=True)

    def _recompute_simulation_parameters(self, settings: dict | None = None) -> None:
        settings = settings if isinstance(settings, dict) else self._project_tree.get_settings()
        simulations = settings.get("simulations", [])
        if not isinstance(simulations, list):
            return
        for simulation in simulations:
            if not isinstance(simulation, dict):
                continue
            for formula_key, value_key in (
                ("FminFormula", "Fmin_GHz"),
                ("FmaxFormula", "Fmax_GHz"),
                ("FstepFormula", "Fstep_GHz"),
                ("NumberOfPointsFormula", "NumberOfPoints"),
            ):
                formula = str(simulation.get(formula_key, "")).strip()
                if not formula:
                    continue
                try:
                    value = self._resolve_formula_text(formula)
                    simulation[value_key] = max(2, int(round(value))) if value_key == "NumberOfPoints" else float(value)
                except Exception as exc:
                    self._info_bar.set_info(f"{simulation.get('name', 'Simulation')}: {exc}")
            if "NumberOfPointsFormula" in simulation and simulation.get("NumberOfPointsFormula"):
                points = max(2, int(simulation.get("NumberOfPoints", 2)))
                fmin = float(simulation.get("Fmin_GHz", 0.1))
                fmax = float(simulation.get("Fmax_GHz", 10.0))
                simulation["Fstep_GHz"] = (fmax - fmin) / (points - 1)
        self._project_tree.load_settings(settings)

    def _open_project_parameters(self) -> None:
        settings = self._project_tree.get_settings()
        dialog = ParametersDialog(settings.get("parameters", []), self, units=self._units)
        if dialog.exec() != QDialog.Accepted:
            return
        settings["parameters"] = dialog.result_parameters()
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._recompute_parametric_objects()
        self.recompute_sketch_dimensions()
        self._mark_simulation_dirty(steps=True, script=True)

    def _open_measure_tool(self) -> None:
        self._viewport.start_measurement()

    def _set_cancel_drawing_enabled(self, measurement_active: bool) -> None:
        self._act_cancel_drawing.setEnabled(not measurement_active)

    def _parameter_values(self) -> dict[str, float]:
        values = {}
        parameters = self._project_tree.get_settings().get("parameters", [])
        for entry in parameters if isinstance(parameters, list) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            try:
                values[name] = float(entry.get("value", 0.0))
            except (TypeError, ValueError):
                continue
        return values

    def _sync_project_variable_names(self, parameters: list | None = None) -> None:
        if parameters is None:
            parameters = self._project_tree.get_settings().get("parameters", [])
        names = [
            str(entry.get("name", "")).strip()
            for entry in parameters if isinstance(entry, dict)
        ] if isinstance(parameters, list) else []
        self._body_props.set_project_variable_names(names)

    def _resolve_formula_text(self, text: str) -> float:
        return evaluate_expression(text, self._parameter_values())

    def recompute_sketch_dimensions(self) -> dict:
        """Re-resolve active sketch dimensions against project parameters."""
        return self._viewport.recompute_sketch_dimensions(self._resolve_formula_text)

    def _recompute_model(self) -> None:
        """Recalculate formula-driven geometry and invalidate generated simulation assets."""
        settings = self._project_tree.get_settings()
        self._recompute_simulation_parameters(settings)
        self._recompute_parametric_objects()
        self.recompute_sketch_dimensions()
        self._mark_simulation_dirty(steps=True, script=True)
        self._info_bar.set_info("Model recomputed.")

    def _resolve_creation_snap(self, snap: dict, objects_by_name: dict) -> list[float] | None:
        sources = objects_by_name.get(str(snap.get("object", "")))
        if sources is None:
            return None
        if not isinstance(sources, (list, tuple)):
            sources = [sources]
        kind = str(snap.get("kind", "")).lower()
        local = snap.get("local")
        point_id = snap.get("point_id")
        if kind != "vertex" and (not isinstance(local, (list, tuple)) or len(local) != 3):
            return None

        resolved = []
        for source in sources:
            actor = getattr(source, "actor", None)
            if actor is None:
                continue
            if kind == "vertex":
                mapper = actor.GetMapper()
                dataset = mapper.GetInput() if mapper is not None else None
                if (
                    dataset is None
                    or not isinstance(point_id, int)
                    or not (0 <= point_id < dataset.GetNumberOfPoints())
                ):
                    continue
                source_local = dataset.GetPoint(point_id)
            else:
                source_local = local
            world = actor.GetMatrix().MultiplyPoint([
                float(source_local[0]), float(source_local[1]), float(source_local[2]), 1.0,
            ])
            resolved.append([float(world[index]) for index in range(3)])
        if not resolved:
            return None

        reference = snap.get("point")
        if not isinstance(reference, (list, tuple)) or len(reference) != 3:
            return resolved[0]
        target = tuple(float(value) for value in reference)
        return min(
            resolved,
            key=lambda point: sum((point[index] - target[index]) ** 2 for index in range(3)),
        )

    def _regenerate_snap_dependent_objects(self, objects: list) -> None:
        objects_by_name = {}
        for obj in objects:
            objects_by_name.setdefault(str(obj.name), []).append(obj)
        for obj in objects:
            history = getattr(obj, "creation_history", {})
            raw_points = history.get("points", []) if isinstance(history, dict) else []
            if not raw_points:
                continue
            if not any(
                isinstance(item, dict)
                and isinstance(item.get("snap"), dict)
                and str(item["snap"].get("object", "")).strip()
                for item in raw_points
            ):
                continue
            points = []
            resolved_points = []
            point_items = []
            for item in raw_points:
                if not isinstance(item, dict):
                    continue
                point = item.get("value")
                snap = item.get("snap", {})
                resolved = self._resolve_creation_snap(snap, objects_by_name) if isinstance(snap, dict) else None
                current_point = resolved if resolved is not None else point
                if not isinstance(current_point, (list, tuple)) or len(current_point) != 3:
                    continue
                points.append(current_point)
                resolved_points.append(resolved)
                point_items.append(item)
            if not points:
                continue
            params = obj.get_parameters()
            formulas = getattr(obj, "param_formulas", {}) or {}
            mode = str(history.get("mode", "")).lower()
            if type(obj).__name__ in {"BoxObject", "PlateObject"} and len(points) >= 2:
                first, second = points[0], points[1]
                plane = str(history.get("plane", getattr(obj, "creation_plane", "XY"))).upper()
                in_plane_axes = {
                    "XY": (0, 1),
                    "XZ": (0, 2),
                    "YZ": (1, 2),
                }.get(plane, (0, 1, 2))
                coordinate_keys = ("X1", "Y1", "Z1"), ("X2", "Y2", "Z2")
                for keys, point in zip(coordinate_keys, (first, second)):
                    for axis_index in in_plane_axes:
                        key = keys[axis_index]
                        if key not in formulas:
                            params[key] = float(point[axis_index])
            elif mode in {"cylinder", "cone", "sphere", "ellipsoid", "torus"}:
                resolved = resolved_points[0]
                original = point_items[0].get("value")
                if (
                    resolved is None
                    or not isinstance(original, (list, tuple))
                    or len(original) != 3
                ):
                    continue
                for axis_index, key in enumerate(("CenterX", "CenterY", "CenterZ")):
                    if key in params and key not in formulas:
                        params[key] = float(params[key]) + float(resolved[axis_index]) - float(original[axis_index])
                point_items[0]["value"] = [float(value) for value in resolved]
            else:
                continue
            obj.set_parameters(params)
            obj.refresh_appearance()

    def _sync_boolean_provenance(self, changed_source=None) -> None:
        for result in list(self._viewport.scene.objects):
            if str(getattr(result, "boolean_op", "") or "").strip().lower() not in {"fuse", "cut", "common"}:
                continue
            live_sources = {}
            for source in list(getattr(result, "source_objects", []) or []):
                live_sources.setdefault(str(getattr(source, "name", "")), []).append(source)
            snapshots = list(getattr(result, "boolean_sources_data", []) or [])
            source_occurrences = {}
            for index, snapshot in enumerate(snapshots):
                if not isinstance(snapshot, dict):
                    continue
                source_name = str(snapshot.get("name", "")).strip()
                occurrence = source_occurrences.get(source_name, 0)
                source_occurrences[source_name] = occurrence + 1
                candidates = live_sources.get(source_name, [])
                source = candidates[occurrence] if occurrence < len(candidates) else None
                if source is None or (changed_source is not None and source is not changed_source):
                    continue
                actor = getattr(source, "actor", None)
                mapper = actor.GetMapper() if actor is not None else None
                mesh_poly = mapper.GetInput() if mapper is not None else None
                mesh_mtime = int(mesh_poly.GetMTime()) if mesh_poly is not None else None
                same_mesh = (
                    type(source).__name__ == "MeshObject"
                    and mesh_mtime is not None
                    and snapshot.get("mesh_mtime") == mesh_mtime
                )
                updated = self._serialize_object_snapshot(
                    source,
                    include_mesh=not same_mesh,
                )
                if same_mesh and "mesh" in snapshot:
                    updated["mesh"] = snapshot["mesh"]
                snapshots[index] = updated
            result.boolean_sources_data = snapshots

    def _replace_boolean_result_geometry(self, result, polydata) -> None:
        """Replace a boolean result's VTK pipeline after a CSG recomputation."""
        import vtk

        result._polydata = polydata
        actor = getattr(result, "actor", None)
        if actor is None:
            return
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        mapper.ScalarVisibilityOff()
        mapper.Update()
        actor.SetMapper(mapper)
        actor.Modified()
        result.refresh_appearance()

    def _replace_boolean_result_object(self, result, polydata, sources):
        """Update a boolean result without invalidating dependent source references."""
        self._replace_boolean_result_geometry(result, polydata)
        result.source_objects = list(sources)
        result.boolean_source_names = [str(source.name) for source in sources]
        result.boolean_sources_data = [
            self._serialize_object_snapshot(source)
            for source in sources
        ]
        return result

    def _recompute_pattern_instances(
        self, values: dict[str, float], *, source_name: str | None = None
    ) -> bool:
        import itertools
        import math

        scene = self._viewport.scene
        groups = {}
        objects_by_name = {str(obj.name): obj for obj in scene.objects}
        for obj in list(scene.objects):
            definition = getattr(obj, "pattern_definition", None)
            pattern_id = definition.get("pattern_id") if isinstance(definition, dict) else None
            if pattern_id:
                groups.setdefault(str(pattern_id), []).append(obj)

        changed = False
        for pattern_id, old_instances in groups.items():
            definition = deepcopy(old_instances[0].pattern_definition)
            settings = definition.get("settings", {})
            source_snapshots = definition.get("sources", [])
            if not isinstance(settings, dict) or not isinstance(source_snapshots, list):
                continue
            if source_name is not None and not any(
                isinstance(snapshot, dict)
                and str(snapshot.get("name", "")).strip() == source_name
                for snapshot in source_snapshots
            ):
                continue
            expressions = settings.get("expressions", {})
            expressions = expressions if isinstance(expressions, dict) else {}
            resolved = settings.get("resolved", {})
            resolved = resolved if isinstance(resolved, dict) else {}

            def resolve_setting(key: str, default: float) -> float:
                raw = str(expressions.get(key, "")).strip()
                if not raw:
                    raw = str(resolved.get(key, default)).strip()
                if key == "angle" and raw.lower().endswith("deg"):
                    raw = raw[:-3].strip()
                try:
                    value = float(raw.replace(",", "."))
                except ValueError:
                    value = float(evaluate_expression(raw, values))
                if not math.isfinite(value):
                    raise ValueError(f"Pattern setting '{key}' must be finite")
                return value

            try:
                mode = str(settings.get("mode", "Linear"))
                if mode not in {"Linear", "Circular"}:
                    raise ValueError(f"Unknown pattern mode: {mode}")
                count_value = resolve_setting("count", 2.0)
                instance_count = int(round(count_value))
                if abs(count_value - instance_count) > 1e-9 or not 1 <= instance_count <= 1000:
                    raise ValueError("Circular instance count must be a whole number from 1 to 1000")
                linear_counts = []
                for key, default in zip(("axis_count_x", "axis_count_y", "axis_count_z"), (2, 1, 1)):
                    value = resolve_setting(key, float(default))
                    count = int(round(value))
                    if abs(value - count) > 1e-9 or not 1 <= count <= 1000:
                        raise ValueError("Linear instance counts must be whole numbers from 1 to 1000")
                    linear_counts.append(count)
                linear_steps = [
                    resolve_setting(key, 0.0)
                    for key in ("offset_x", "offset_y", "offset_z")
                ]
                total_angle = resolve_setting("angle", 360.0)
                requested_radius = max(0.0, resolve_setting("radius", 1.0))
                axis_enabled = settings.get("axis_enabled", [True, False, False])
                axis_enabled = [bool(value) for value in axis_enabled[:3]]
                axis_enabled += [False] * (3 - len(axis_enabled))
                circular_center = tuple(
                    resolve_setting(key, 0.0)
                    for key in ("center_x", "center_y", "center_z")
                )
                axis_start = tuple(
                    resolve_setting(key, 0.0)
                    for key in ("axis_start_x", "axis_start_y", "axis_start_z")
                )
                axis_end = tuple(
                    resolve_setting(key, default)
                    for key, default in zip(
                        ("axis_end_x", "axis_end_y", "axis_end_z"), (0.0, 0.0, 1.0)
                    )
                )
                axis_name = str(settings.get("axis", "X"))
                if mode == "Linear" and not any(axis_enabled):
                    raise ValueError("At least one linear pattern axis must be enabled")
                axis_vector = tuple(axis_end[i] - axis_start[i] for i in range(3))
                axis_length = math.sqrt(sum(value * value for value in axis_vector))
                if mode == "Circular" and axis_length < 1e-12:
                    raise ValueError("Rotation axis points must be different")
                if axis_length > 1e-12:
                    axis_vector = tuple(value / axis_length for value in axis_vector)

                source_data = []
                for source_index, snapshot in enumerate(source_snapshots):
                    if not isinstance(snapshot, dict):
                        continue
                    source_object_name = str(snapshot.get("name", "")).strip()
                    source = objects_by_name.get(source_object_name)
                    if source in old_instances:
                        source = None
                    if source is None:
                        source = self._rebuild_object_from_snapshot(snapshot)
                        if source is None:
                            raise ValueError(f"Pattern source '{source_object_name}' could not be restored")
                        params = source.get_parameters()
                        for key, formula in (getattr(source, "param_formulas", {}) or {}).items():
                            params[key] = evaluate_expression(str(formula), values)
                        source.set_parameters(params)
                    actor = getattr(source, "actor", None)
                    if actor is None:
                        raise ValueError(f"Pattern source '{source_object_name}' has no geometry")
                    bounds = actor.GetBounds()
                    center = tuple(
                        (float(bounds[index]) + float(bounds[index + 1])) * 0.5
                        for index in (0, 2, 4)
                    )
                    source_data.append((source_index, source, center))
                if not source_data:
                    raise ValueError("Pattern has no valid source objects")

                source_bounds = [source.actor.GetBounds() for _, source, _ in source_data]
                bounds = (
                    min(float(item[0]) for item in source_bounds), max(float(item[1]) for item in source_bounds),
                    min(float(item[2]) for item in source_bounds), max(float(item[3]) for item in source_bounds),
                    min(float(item[4]) for item in source_bounds), max(float(item[5]) for item in source_bounds),
                )
                source_bounds_center = tuple(
                    (float(bounds[index]) + float(bounds[index + 1])) * 0.5
                    for index in (0, 2, 4)
                )
                radial = tuple(source_bounds_center[i] - circular_center[i] for i in range(3))
                radial_length = math.sqrt(sum(value * value for value in radial))
                if mode == "Circular" and radial_length < 1e-12:
                    radial = (1.0, 0.0, 0.0)
                    radial_length = 1.0

                enabled_indices = [index for index, enabled in enumerate(axis_enabled) if enabled]
                linear_offsets = [
                    (linear_steps[0] * ix, linear_steps[1] * iy, linear_steps[2] * iz)
                    for ix, iy, iz in itertools.product(*[
                        range(linear_counts[index]) if axis_enabled[index] else range(1)
                        for index in range(3)
                    ])
                    if (ix, iy, iz) != (0, 0, 0)
                ] if mode == "Linear" else []
                instance_specs = (
                    enumerate(linear_offsets, start=1)
                    if mode == "Linear"
                    else enumerate(range(1, instance_count), start=1)
                )
                old_by_key = {
                    (
                        int(getattr(obj, "pattern_instance", {}).get("instance_index", -1)),
                        int(getattr(obj, "pattern_instance", {}).get("source_index", -1)),
                    ): obj
                    for obj in old_instances
                    if isinstance(getattr(obj, "pattern_instance", None), dict)
                }
                definition["sources"] = [
                    self._serialize_object_snapshot(source)
                    for _, source, _ in source_data
                ]
                settings["resolved"] = {
                    key: resolve_setting(key, float(resolved.get(key, default)))
                    for key, default in (
                        ("count", 2.0), ("axis_count_x", 2.0), ("axis_count_y", 1.0), ("axis_count_z", 1.0),
                        ("offset_x", 0.0), ("offset_y", 0.0), ("offset_z", 0.0), ("angle", 360.0),
                        ("center_x", circular_center[0]), ("center_y", circular_center[1]), ("center_z", circular_center[2]),
                        ("axis_start_x", axis_start[0]), ("axis_start_y", axis_start[1]), ("axis_start_z", axis_start[2]),
                        ("axis_end_x", axis_end[0]), ("axis_end_y", axis_end[1]), ("axis_end_z", axis_end[2]),
                        ("radius", requested_radius),
                    )
                }
                typed_inputs = settings.get("inputs", {})
                if not isinstance(typed_inputs, dict):
                    typed_inputs = {}
                for key, raw_value in expressions.items():
                    input_data = typed_inputs.get(key)
                    if not isinstance(input_data, dict):
                        raw_text = str(raw_value).strip()
                        try:
                            float(raw_text.replace(",", "."))
                            typed_inputs[key] = {
                                "type": "value",
                                "value": settings["resolved"].get(key, resolved.get(key)),
                            }
                        except ValueError:
                            typed_inputs[key] = {
                                "type": "formula",
                                "formula": raw_text,
                                "value": settings["resolved"].get(key, resolved.get(key)),
                            }
                            input_data = typed_inputs[key]
                    if isinstance(input_data, dict) and key in settings["resolved"]:
                        input_data["value"] = settings["resolved"][key]
                settings["inputs"] = typed_inputs
                settings["parameter_values"] = dict(values)
                new_instances = []
                for instance_index, linear_offset in instance_specs:
                    for source_index, source, source_center in source_data:
                        snapshot = self._serialize_object_snapshot(source)
                        clone_data = dict(snapshot)
                        clone_data["params"] = dict(snapshot.get("params", {}))
                        old_instance = old_by_key.get((instance_index, source_index))
                        clone_source_name = str(source.name)
                        clone_name = (
                            str(old_instance.name) if old_instance is not None
                            else f"{clone_source_name}_Pattern_{instance_index + 1}"
                        )
                        clone_data["name"] = clone_name
                        clone_data["params"]["Name"] = clone_name
                        clone_data["pattern_definition"] = deepcopy(definition)
                        clone_data["pattern_instance"] = {
                            "instance_index": int(instance_index),
                            "source_index": int(source_index),
                            "source_name": clone_source_name,
                            "settings": deepcopy(settings),
                        }
                        clone = self._rebuild_object_from_snapshot(clone_data)
                        if clone is None or clone.actor is None:
                            raise ValueError(f"Pattern replica '{clone_name}' could not be rebuilt")
                        if mode == "Linear":
                            clone.actor.AddPosition(*linear_offset)
                        else:
                            step_angle = total_angle * instance_index / max(1, instance_count - 1)
                            cosine = math.cos(math.radians(step_angle))
                            sine = math.sin(math.radians(step_angle))

                            def rotate(vector):
                                cross = (
                                    axis_vector[1] * vector[2] - axis_vector[2] * vector[1],
                                    axis_vector[2] * vector[0] - axis_vector[0] * vector[2],
                                    axis_vector[0] * vector[1] - axis_vector[1] * vector[0],
                                )
                                dot = sum(axis_vector[i] * vector[i] for i in range(3))
                                return tuple(
                                    vector[i] * cosine + cross[i] * sine + axis_vector[i] * dot * (1.0 - cosine)
                                    for i in range(3)
                                )

                            direction = tuple(value / radial_length for value in radial)
                            rotated_direction = rotate(direction)
                            target_center = tuple(
                                circular_center[i] + rotated_direction[i] * requested_radius
                                + rotate(tuple(source_center[j] - source_bounds_center[j] for j in range(3)))[i]
                                for i in range(3)
                            )
                            clone.actor.SetOrigin(*source_center)
                            clone.actor.RotateWXYZ(step_angle, *axis_vector)
                            clone_bounds = clone.actor.GetBounds()
                            clone_center = tuple(
                                (float(clone_bounds[index]) + float(clone_bounds[index + 1])) * 0.5
                                for index in (0, 2, 4)
                            )
                            clone.actor.AddPosition(*(target_center[i] - clone_center[i] for i in range(3)))
                        new_instances.append((instance_index, source_index, clone))
                replacements = {
                    old_by_key[key]: clone
                    for instance_index, source_index, clone in new_instances
                    if (key := (instance_index, source_index)) in old_by_key
                }
            except Exception as exc:
                self._info_bar.set_info(f"Pattern {pattern_id}: recomputation failed: {exc}")
                continue

            for obj in old_instances:
                scene.remove_object(obj)
            for _, _, clone in new_instances:
                scene.add_object(clone)
                objects_by_name[clone.name] = clone
            for obj in list(scene.objects):
                sources = list(getattr(obj, "source_objects", []) or [])
                if not sources:
                    continue
                updated_sources = [replacements.get(source, source) for source in sources]
                if updated_sources != sources:
                    obj.source_objects = updated_sources
                    obj.boolean_source_names = [str(source.name) for source in updated_sources]
                    obj.boolean_sources_data = [
                        self._serialize_object_snapshot(source) for source in updated_sources
                    ]
            changed = True

        if changed:
            self._refresh_materials()
        return changed

    def _recompute_parametric_objects(
        self,
        *,
        _patterns_recomputed: bool = False,
        _regenerate_snaps: bool = True,
        parameter_values: dict[str, float] | None = None,
    ) -> None:
        from ..scene.boolean_ops import boolean_dependency_order, boolean_many, fuse_many

        values = self._parameter_values()
        if parameter_values:
            values.update(parameter_values)
        self._sync_boolean_provenance()
        scene_objects = list(self._viewport.scene.objects)
        objects_by_name = {str(obj.name): obj for obj in scene_objects}
        objects = []
        seen: set[int] = set()
        visiting: set[int] = set()

        def collect(obj) -> None:
            if obj is None or id(obj) in seen:
                return
            if id(obj) in visiting:
                raise ValueError(f"Boolean dependency cycle detected at {getattr(obj, 'name', 'object')}")
            visiting.add(id(obj))
            try:
                operation = str(getattr(obj, "boolean_op", "") or "").strip().lower()
                if operation in {"fuse", "cut", "common"}:
                    live_sources = list(getattr(obj, "source_objects", []) or [])
                    snapshots = [
                        snapshot
                        for snapshot in list(getattr(obj, "boolean_sources_data", []) or [])
                        if isinstance(snapshot, dict) and str(snapshot.get("name", "")).strip()
                    ]
                    source_names = list(getattr(obj, "boolean_source_names", []) or [])
                    if not source_names:
                        source_names = [str(source.name) for source in live_sources]
                        if not source_names:
                            source_names = [str(snapshot.get("name", "")) for snapshot in snapshots]

                    ordered_sources = []
                    name_counts = {
                        name: source_names.count(name) for name in set(source_names)
                    }
                    for source_index, source_name in enumerate(source_names):
                        snapshot = (
                            snapshots[source_index]
                            if source_index < len(snapshots)
                            and str(snapshots[source_index].get("name", "")).strip() == source_name
                            else next((
                                item for item in snapshots
                                if str(item.get("name", "")).strip() == source_name
                            ), None)
                        )
                        source = None
                        if name_counts[source_name] > 1 and snapshot is not None:
                            source = self._rebuild_object_from_snapshot(snapshot)
                        elif source_index < len(live_sources) and str(
                            getattr(live_sources[source_index], "name", "")
                        ) == source_name:
                            source = live_sources[source_index]
                        else:
                            source = next((
                                item for item in live_sources
                                if str(getattr(item, "name", "")) == source_name
                            ), None)
                            source = source or objects_by_name.get(source_name)
                        if source is None and snapshot is not None:
                            source = self._rebuild_object_from_snapshot(snapshot)
                            if source is not None and name_counts[source_name] == 1:
                                objects_by_name.setdefault(source_name, source)
                        if source is None:
                            raise ValueError(
                                f"{obj.name}: boolean source '{source_name}' could not be restored"
                            )
                        ordered_sources.append(source)

                    if len(ordered_sources) < 2:
                        raise ValueError(f"{obj.name}: at least two boolean sources are required")
                    obj.source_objects = ordered_sources
                    obj.boolean_source_names = [str(source.name) for source in ordered_sources]
                    for source in ordered_sources:
                        collect(source)

                seen.add(id(obj))
                objects.append(obj)
            finally:
                visiting.remove(id(obj))

        for scene_obj in scene_objects:
            try:
                collect(scene_obj)
            except Exception as exc:
                self._info_bar.set_info(f"{getattr(scene_obj, 'name', 'Object')}: dependency restore failed: {exc}")

        def apply_formulas(obj) -> None:
            formulas = getattr(obj, "param_formulas", {}) or {}
            if not formulas:
                return
            params = obj.get_parameters()
            for key, formula in formulas.items():
                params[key] = evaluate_expression(formula, values)
            obj.set_parameters(params)
            obj.refresh_appearance()

        formula_failures: set[int] = set()
        for obj in objects:
            try:
                apply_formulas(obj)
            except Exception as exc:
                formula_failures.add(id(obj))
                self._info_bar.set_info(f"{obj.name}: {exc}")
            self._process_simulation_preparation_events()

        for obj in objects:
            definition = getattr(obj, "sketch_definition", None)
            if isinstance(definition, dict):
                self._recompute_persisted_sketch(obj, definition, values)
            self._process_simulation_preparation_events()
        if _regenerate_snaps:
            self._regenerate_snap_dependent_objects(objects)
        self._process_simulation_preparation_events()

        try:
            ordered_booleans = boolean_dependency_order(objects)
        except ValueError as exc:
            self._info_bar.set_info(str(exc))
            return

        boolean_failures: set[int] = set()
        for result in ordered_booleans:
            operation = str(getattr(result, "boolean_op", "") or "").strip().lower()
            ordered_sources = list(getattr(result, "source_objects", []) or [])
            if len(ordered_sources) < 2:
                self._info_bar.set_info(f"{result.name}: at least two boolean sources are required")
                self._process_simulation_preparation_events()
                continue
            failed_sources = [
                source for source in ordered_sources
                if id(source) in formula_failures or id(source) in boolean_failures
            ]
            if failed_sources:
                boolean_failures.add(id(result))
                self._info_bar.set_info(
                    f"{result.name}: recomputation skipped because a source failed: "
                    + ", ".join(str(source.name) for source in failed_sources)
                )
                self._process_simulation_preparation_events()
                continue
            try:
                reduction = float(getattr(result, "boolean_mesh_reduction", 0.0) or 0.0)
                if operation == "fuse":
                    polydata = (
                        fuse_many(ordered_sources, target_reduction=reduction)
                        if reduction > 0.0 else fuse_many(ordered_sources)
                    )
                elif reduction > 0.0:
                    polydata = boolean_many(
                        operation,
                        ordered_sources,
                        target_reduction=reduction,
                    )
                else:
                    polydata = boolean_many(operation, ordered_sources)
                self._replace_boolean_result_object(result, polydata, ordered_sources)
            except Exception as exc:
                boolean_failures.add(id(result))
                self._info_bar.set_info(f"{result.name}: boolean recompute failed: {exc}")
            self._process_simulation_preparation_events()
        pattern_recompute = getattr(self, "_recompute_pattern_instances", None)
        if (
            not _patterns_recomputed
            and callable(pattern_recompute)
            and pattern_recompute(values)
        ):
            self._process_simulation_preparation_events()
            self._recompute_parametric_objects(
                _patterns_recomputed=True,
                _regenerate_snaps=_regenerate_snaps,
                parameter_values=values,
            )
            return
        self._update_generated_open_region()
        self._viewport._render()

    def _update_generated_open_region(self) -> None:
        from ..scene.em_objects import BoxObject

        settings = self._project_tree.get_settings()
        open_region = settings.get("open_region", {})
        if not isinstance(open_region, dict) or not open_region.get("enabled"):
            return

        scene_objects = list(self._viewport.scene.objects)
        objects_by_name = {str(obj.name): obj for obj in scene_objects}
        air_name = str(open_region.get("object", "")).strip()
        air = objects_by_name.get(air_name)
        if not isinstance(air, BoxObject):
            return

        pml_settings = settings.get("pml", {})
        pml_settings = pml_settings if isinstance(pml_settings, dict) else {}
        auto_update = bool(open_region.get("auto_update", False))
        if not auto_update:
            auto_update = (
                air_name.startswith("Air_Region")
                and str(pml_settings.get("air_object", "")) == air_name
            )
        if not auto_update:
            return

        models = [
            obj for obj in scene_objects
            if bool(getattr(obj, "is_model", True))
            and str(getattr(obj, "material", "")).strip().upper() not in {"AIR", "PML"}
            and getattr(obj, "actor", None) is not None
        ]
        if not models:
            return

        bounds = [obj.actor.GetBounds() for obj in models]
        model_bounds = (
            min(item[0] for item in bounds), max(item[1] for item in bounds),
            min(item[2] for item in bounds), max(item[3] for item in bounds),
            min(item[4] for item in bounds), max(item[5] for item in bounds),
        )
        try:
            gap = float(open_region["distance_mm"])
            if not math.isfinite(gap) or gap < 0.0:
                raise ValueError("invalid open-region distance")
        except (KeyError, TypeError, ValueError):
            previous_air_bounds = air.actor.GetBounds()
            extensions = (
                model_bounds[0] - previous_air_bounds[0],
                previous_air_bounds[1] - model_bounds[1],
                model_bounds[2] - previous_air_bounds[2],
                previous_air_bounds[3] - model_bounds[3],
                model_bounds[4] - previous_air_bounds[4],
                previous_air_bounds[5] - model_bounds[5],
            )
            gap = max(0.0, sum(extensions) / len(extensions))
            open_region["distance_mm"] = gap
            settings["open_region"] = open_region
            self._project_tree.load_settings(settings)
            self._project_tree.settings_changed.emit()
        air_bounds = (
            model_bounds[0] - gap, model_bounds[1] + gap,
            model_bounds[2] - gap, model_bounds[3] + gap,
            model_bounds[4] - gap, model_bounds[5] + gap,
        )
        air.set_parameters(dict(zip(
            ("X1", "X2", "Y1", "Y2", "Z1", "Z2"),
            air_bounds,
        )))

        if not pml_settings.get("enabled"):
            return
        outer_name = str(pml_settings.get("outer_object", "")).strip()
        update_pml = bool(pml_settings.get("auto_update", False))
        if not update_pml:
            update_pml = (
                outer_name.startswith("PML_Region")
                and str(pml_settings.get("air_object", "")) == air_name
            )
        if not update_pml:
            return
        outer = objects_by_name.get(outer_name)
        if not isinstance(outer, BoxObject):
            return
        try:
            thickness = max(0.0, float(pml_settings.get("thickness_mm", 10.0)))
        except (TypeError, ValueError):
            thickness = 10.0
        outer_bounds = (
            air_bounds[0] - thickness, air_bounds[1] + thickness,
            air_bounds[2] - thickness, air_bounds[3] + thickness,
            air_bounds[4] - thickness, air_bounds[5] + thickness,
        )
        outer.set_parameters(dict(zip(
            ("X1", "X2", "Y1", "Y2", "Z1", "Z2"),
            outer_bounds,
        )))

    def _on_object_selected(self, obj) -> None:
        self._project_properties_active = False
        self._body_props.set_object(obj)
        self._materials.highlight(obj)
        # Update Fuse counter when single object selected
        self._fuse_label.setText("Fuse: 1")
        self._fuse_label.setStyleSheet("font-size: 10px; color: #666;")
        if obj:
            self._info_bar.set_info(f"Selected: {obj.name}  [{type(obj).__name__}]")

    def _on_selection_changed(self, objects: list) -> None:
        self._viewport._last_face_pick = None
        if self._project_properties_active:
            self._body_props.set_project_parameters(self._project_tree.get_settings().get("parameters", []))
            self._materials.highlight([])
            return
        self._body_props.set_selection(objects)
        self._materials.highlight(objects)
        # Update Fuse counter in toolbar
        if len(objects) >= 2:
            self._fuse_label.setText(f"Fuse: {len(objects)}")
            self._fuse_label.setStyleSheet("font-size: 10px; color: #0a0; font-weight: bold;")
        else:
            self._fuse_label.setText("Fuse: 0")
            self._fuse_label.setStyleSheet("font-size: 10px; color: #666;")
        if len(objects) > 1:
            self._info_bar.set_info(f"{len(objects)} objects selected (Ctrl+click to extend)")

    def _on_params_changed(self, obj, _params) -> None:
        self._sync_boolean_provenance(obj)
        if not self._history_restoring:
            source_name = str(getattr(obj, "name", "")).strip()
            if source_name and any(
                isinstance(definition, dict)
                and isinstance(definition.get("sources"), list)
                and any(
                    isinstance(snapshot, dict)
                    and str(snapshot.get("name", "")).strip() == source_name
                    for snapshot in definition["sources"]
                )
                for instance in self._viewport.scene.objects
                for definition in [getattr(instance, "pattern_definition", None)]
            ):
                self._recompute_pattern_instances(
                    self._parameter_values(), source_name=source_name
                )
            self._history_record()
        self._viewport.scene.refresh_adaptive_grid()
        self._viewport._render()
        self._refresh_materials()
        self._mark_simulation_dirty(steps=True, script=True)

    def _recompute_persisted_sketch(self, obj, definition: dict, values: dict) -> bool:
        from ..drawing.sketch_engine import SketchEngine
        from ..scene.em_objects import ExtrudedObject, RevolvedObject

        try:
            engine = SketchEngine.from_dict(definition)
            dimensions = list(engine.dimensions)
            for dimension in dimensions:
                if not dimension.expression:
                    continue
                engine.dimensions = [dimension]
                try:
                    engine.recompute_dimensions(
                        lambda expression: evaluate_expression(expression, values)
                    )
                except Exception as exc:
                    dimension.resolved_value = None
                    self._info_bar.set_info(
                        f"{obj.name}: sketch dimension unresolved: {exc}"
                    )

            engine.dimensions = dimensions
            engine.dimensions = [
                dimension for dimension in dimensions
                if dimension.kind != "angular" and dimension.resolved_value is not None
            ]
            for dimension in engine.dimensions:
                if not engine.apply_dimension(dimension):
                    raise ValueError("a sketch dimension could not be applied")
            engine.dimensions = dimensions
            if isinstance(obj, ExtrudedObject):
                regions = engine.operation_regions()
                if any(not region or len(region[0]) < 3 for region in regions):
                    raise ValueError("the sketch no longer forms a closed profile")
                if len(regions) == 1:
                    profile = regions[0][0] if len(regions[0]) == 1 else regions[0]
                else:
                    profile = regions
                params = obj.get_parameters()
                params["ProfilePts"] = profile
                obj.set_parameters(params)
            elif isinstance(obj, RevolvedObject):
                profile = engine.operation_profile()
                outer = profile[0] if profile and isinstance(profile[0][0], (list, tuple)) else profile
                if len(outer) < 3:
                    raise ValueError("the sketch no longer forms a closed profile")
                params = obj.get_parameters()
                params["ProfilePts"] = profile
                obj.set_parameters(params)
            obj.sketch_definition = engine.to_dict()
            return True
        except Exception as exc:
            self._info_bar.set_info(f"{obj.name}: sketch recomputation failed: {exc}")
            return False

    def _on_bulk_material(self, material: str, objects: list) -> None:
        self._history_record()
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(f"Material '{material}' applied to {len(objects)} objects")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_bulk_style(self, material: str, color_hex: str, objects: list) -> None:
        self._history_record()
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(
            f"Applied material '{material}' and color {color_hex} to {len(objects)} objects"
        )
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_material_added(self, name: str) -> None:
        if not name:
            return
        if self._material_store.get_record(name, source="project") is None:
            try:
                if self._material_store.get_record(name, source="global") is None:
                    self._material_store.add_global_material(name=name)
                self._material_store.append_global_to_project([name])
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(
                    self,
                    "Global Material Database",
                    f"Unable to save material: {exc}",
                )
        self._sync_material_choices()

    def _open_material_picker(self, current_name: str, selected_objects: list) -> None:
        dlg = MaterialAssignDialog(
            self,
            project_records=self._material_store.project_records(),
            global_records=self._material_store.global_records(),
            selected_name=current_name,
            can_append_global=bool(self._material_store.global_records()),
        )
        if dlg.exec_() != QDialog.Accepted:
            return

        rec = dlg.selected_record
        if rec is None:
            return

        if dlg.appended_names:
            added = self._material_store.append_global_to_project(dlg.appended_names)
            if added:
                self._sync_material_choices()
                self._info_bar.set_info(f"Appended {len(added)} material(s) to project DB")

        if rec.source == "global" and self._material_store.get_record(rec.name, source="project") is None:
            self._material_store.append_global_to_project([rec.name])
            self._sync_material_choices()
        elif rec.source == "project" and self._material_store.get_record(rec.name, source="project") is None:
            try:
                self._material_store.upsert_global_record(rec)
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(
                    self,
                    "Global Material Database",
                    f"Unable to save material: {exc}",
                )
                return
            self._material_store.append_global_to_project([rec.name])
            self._sync_material_choices()

        name = rec.name
        self._draw_material = name

        if len(selected_objects) > 1:
            for obj in selected_objects:
                obj.material = name
                obj.refresh_appearance()
            self._history_record()
            self._viewport._render()
            self._refresh_materials()
            self._info_bar.set_info(f"Material '{name}' applied to {len(selected_objects)} objects")
            self._mark_simulation_dirty(steps=True, script=True)
            return

        self._body_props.set_selected_material(name)

    def _on_material_tree_select(self, obj) -> None:
        self._viewport.scene.select(obj)
        self._body_props.set_object(obj)
        self._viewport._render()

    def _on_material_tree_multi_select(self, objects: list) -> None:
        # Update SceneManager selection to match tree selection
        self._viewport.scene.deselect_all()
        if not objects:
            self._body_props.set_object(None)
            self._viewport._render()
            return
        for o in objects:
            self._viewport.scene.select_add(o)
        self._body_props.set_selection(objects)
        self._viewport._render()

    def _on_materials_hide(self, objects: list) -> None:
        if not objects:
            return
        self._viewport.scene.set_visibility(objects, False)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(objects)  # Preserve selection after refresh
        self._viewport._render()
        self._info_bar.set_info(f"Hidden {len(objects)} object(s)")

    def _on_materials_show(self, objects: list) -> None:
        if not objects:
            return
        self._viewport.scene.set_visibility(objects, True)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(objects)  # Preserve selection after refresh
        self._viewport._render()
        self._info_bar.set_info(f"Shown {len(objects)} object(s)")

    def _on_transform_edit_requested(self, objects) -> None:
        if not isinstance(objects, (list, tuple)):
            objects = [objects]
        objects = [obj for obj in objects if obj is not None and getattr(obj, "actor", None) is not None]
        if not objects:
            return
        self._viewport.scene.deselect_all()
        for obj in objects:
            self._viewport.scene.select_add(obj)
        self._body_props.set_selection(objects)
        self._move_selection_to_plane_origin()

    def _on_materials_model_role_changed(self, objects: list, is_model: bool) -> None:
        if not objects:
            return
        for obj in objects:
            obj.is_model = bool(is_model)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(objects)
        self._viewport._render()
        role_txt = "MODEL" if is_model else "NON MODEL"
        self._info_bar.set_info(f"Set {len(objects)} object(s) as {role_txt}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_set_open_region_requested(self, obj) -> None:
        if obj is None or str(getattr(obj, "material", "")).strip().upper() != "AIR":
            return
        settings = self._project_tree.get_settings()
        settings["open_region"] = {
            "enabled": True,
            "object": str(getattr(obj, "name", "")).strip(),
        }
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._mark_simulation_dirty(steps=False, script=True)
        self._info_bar.set_info(f"Simulation region set to: {obj.name}")

    def _on_model_role_changed(self, obj, is_model: bool) -> None:
        if obj is None:
            return
        obj.is_model = bool(is_model)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(obj)
        self._viewport._render()
        role_txt = "MODEL" if is_model else "NON MODEL"
        self._info_bar.set_info(f"{obj.name}: {role_txt}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_materials_rename(self, obj, new_name: str) -> None:
        if obj is None or not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if any(
            candidate is not obj
            and str(getattr(candidate, "name", "")).strip() == new_name
            for candidate in self._viewport.scene.objects
        ):
            QMessageBox.warning(
                self,
                "Object Name Already Used",
                f"An object named '{new_name}' already exists. Choose a different name.",
            )
            return
        old_name = obj.name
        obj.name = new_name
        self._project_tree.rename_object_references(old_name, new_name)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(obj)  # Preserve selection after refresh
        if self._viewport.scene.selected is obj:
            self._body_props.set_object(obj)
        self._viewport._render()
        self._info_bar.set_info(f"Renamed to: {new_name}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_materials_bulk_rename(self, objects: list, base_name: str, start_index: int) -> None:
        if not objects or not base_name:
            return
        selected_ids = {id(obj) for obj in objects}
        occupied_names = {
            str(getattr(obj, "name", "")).strip()
            for obj in self._viewport.scene.objects
            if id(obj) not in selected_ids
        }
        proposed_names = [
            f"{base_name.strip()}_{start_index + index}"
            for index in range(len(objects))
        ]
        if (
            len(proposed_names) != len(set(proposed_names))
            or any(name in occupied_names for name in proposed_names)
        ):
            QMessageBox.warning(
                self,
                "Object Name Already Used",
                "At least one proposed name is already used by another object. "
                "No objects were renamed.",
            )
            return
        for i, obj in enumerate(objects):
            old_name = obj.name
            new_name = proposed_names[i]
            obj.name = new_name
            self._project_tree.rename_object_references(old_name, new_name)
        self._history_record()
        self._refresh_materials()
        self._materials.highlight(objects)
        self._viewport._render()
        self._info_bar.set_info(
            f"Renamed {len(objects)} object(s): {base_name}_{start_index} – {base_name}_{start_index + len(objects) - 1}"
        )
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_assign_port_requested(self, obj) -> None:
        if obj is None:
            return
        self._project_tree.assign_port_to_object(obj.name)
        self._info_bar.set_info(f"Port assignment updated for {obj.name}")

    def _on_assign_boundary_requested(self, objects: list) -> None:
        if not objects:
            return
        names = [str(obj.name).strip() for obj in objects if getattr(obj, "name", "")]
        if not names:
            return
        self._project_tree.assign_boundary_to_objects(names)
        self._info_bar.set_info(f"Boundary assignment updated for {len(names)} object(s)")

    def _on_assign_mesh_resolution_requested(self, objects: list) -> None:
        if not objects:
            return
        settings = self._project_tree.get_settings()
        mesh_cfg = settings.get("mesh")
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            settings["mesh"] = mesh_cfg

        current_default = mesh_cfg.get("default_fraction", 0.3)
        try:
            current_default = float(current_default)
        except Exception:
            current_default = 0.3
        current_default = max(0.01, min(1.0, current_default))

        value, ok = QInputDialog.getDouble(
            self,
            "Assign Mesh Resolution",
            "Resolution fraction (1/λ):",
            current_default,
            0.01,
            1.0,
            4,
        )
        if not ok:
            return

        mesh_assign = mesh_cfg.get("object_resolutions")
        if not isinstance(mesh_assign, dict):
            mesh_assign = {}
            mesh_cfg["object_resolutions"] = mesh_assign

        names: list[str] = []
        seen: set[str] = set()
        for obj in objects:
            name = str(getattr(obj, "name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)

        for name in names:
            mesh_assign[name] = float(value)
        mesh_cfg["default_fraction"] = float(value)

        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._mark_simulation_dirty(steps=False, script=True)
        if len(names) == 1:
            self._info_bar.set_info(f"Mesh 1/λ set to {value:.4g} for {names[0]}")
        else:
            self._info_bar.set_info(f"Mesh 1/λ set to {value:.4g} for {len(names)} objects")

    def _on_assign_mesh_refinement_requested(self, objects: list) -> None:
        objects = [obj for obj in objects if obj is not None and str(getattr(obj, "name", "")).strip()]
        if not objects:
            return

        settings = self._project_tree.get_settings()
        mesh_cfg = settings.setdefault("mesh", {})
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            settings["mesh"] = mesh_cfg
        refinements = mesh_cfg.get("local_refinements", [])
        if not isinstance(refinements, list):
            refinements = []

        names = [str(obj.name).strip() for obj in objects]
        existing = next(
            (
                item for item in refinements
                if isinstance(item, dict) and str(item.get("object", "")).strip() == names[0]
            ),
            None,
        ) if len(names) == 1 else None
        all_plates = all(self._is_plate_role_object(obj) for obj in objects)
        initial_mode = str((existing or {}).get("mode", "face" if all_plates else "boundary"))

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Assign Mesh Refinement ({len(names)} object{'s' if len(names) != 1 else ''})")
        form = QFormLayout(dlg)

        enabled = QCheckBox("Enabled", dlg)
        enabled.setChecked(bool((existing or {}).get("enabled", True)))
        form.addRow("", enabled)

        mode = QComboBox(dlg)
        mode.addItem("Boundary edges", "boundary")
        mode.addItem("Face", "face")
        mode_index = mode.findData(initial_mode)
        mode.setCurrentIndex(max(0, mode_index))
        form.addRow("Mode", mode)

        faces = QLineEdit(dlg)
        default_faces = "" if all_plates and initial_mode == "face" else "-z,+z"
        faces.setText(",".join((existing or {}).get("faces", [])) or default_faces)
        faces.setPlaceholderText("-z,+z (empty = whole Plate for Face mode)")
        form.addRow("Faces", faces)

        size = QDoubleSpinBox(dlg)
        size.setDecimals(6)
        size.setRange(0.000001, 1e6)
        default_size = self._refinement_face_size_mm if initial_mode == "face" else self._refinement_boundary_size_mm
        size.setValue(float((existing or {}).get("size_mm", default_size)))
        size.setSuffix(" mm")
        form.addRow("Minimum size", size)

        growth = QDoubleSpinBox(dlg)
        growth.setDecimals(3)
        growth.setRange(1.001, 100.0)
        growth.setValue(float((existing or {}).get("growth_rate", self._refinement_growth_rate)))
        form.addRow("Growth rate", growth)

        max_size = QDoubleSpinBox(dlg)
        max_size.setDecimals(6)
        max_size.setRange(0.0, 1e6)
        max_size.setSpecialValueText("Automatic")
        max_size.setSuffix(" mm")
        existing_max = (existing or {}).get("max_size_mm", self._refinement_max_size_mm)
        max_size.setValue(float(existing_max or 0.0))
        form.addRow("Maximum size", max_size)

        if existing is None:
            def _use_mode_default(_index: int) -> None:
                selected_mode = str(mode.currentData())
                size.setValue(
                    self._refinement_face_size_mm
                    if selected_mode == "face"
                    else self._refinement_boundary_size_mm
                )
                if selected_mode == "face" and all_plates:
                    faces.clear()
                elif not faces.text().strip():
                    faces.setText("-z,+z")

            mode.currentIndexChanged.connect(_use_mode_default)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return

        valid_faces = {"-x", "+x", "-y", "+y", "-z", "+z"}
        selected_faces = [part.strip().lower() for part in faces.text().split(",") if part.strip()]
        invalid_faces = [part for part in selected_faces if part not in valid_faces]
        selected_mode = str(mode.currentData())
        if invalid_faces:
            QMessageBox.warning(self, "Mesh Refinement", f"Invalid face selector(s): {', '.join(invalid_faces)}")
            return
        if selected_mode == "boundary" and not selected_faces:
            QMessageBox.warning(self, "Mesh Refinement", "Boundary edges mode requires at least one face selector.")
            return
        if selected_mode == "face" and not selected_faces and not all_plates:
            QMessageBox.warning(self, "Mesh Refinement", "Face mode requires face selectors for non-Plate objects.")
            return
        if selected_mode == "face" and all_plates:
            selected_faces = []

        assigned_refinements = []
        for name in names:
            assigned_refinements.append({
                "object": name,
                "enabled": bool(enabled.isChecked()),
                "mode": selected_mode,
                "faces": list(selected_faces),
                "size_mm": float(size.value()),
                "growth_rate": float(growth.value()),
                "max_size_mm": float(max_size.value()) if max_size.value() > 0.0 else None,
            })
        self._project_tree.assign_local_mesh_refinements(assigned_refinements)
        saved_refinements = self._project_tree.get_settings().get("mesh", {}).get("local_refinements", [])
        saved_names = {
            str(item.get("object", "")).strip()
            for item in saved_refinements
            if isinstance(item, dict)
        }
        missing_names = [name for name in names if name not in saved_names]
        if missing_names:
            QMessageBox.critical(
                self,
                "Mesh Refinement",
                f"The refinement assignment was not retained for: {', '.join(missing_names)}",
            )
            return
        self._mark_simulation_dirty(steps=False, script=True)
        self._info_bar.set_info(
            f"Local mesh refinement assigned to {len(names)} object(s): {', '.join(names)}"
        )

    def _on_material_priority_changed(self, material_name: str, delta: int) -> None:
        """Handle material priority change: delta is 1 for higher, -1 for lower."""
        if not material_name:
            return
        # Get current priorities from project settings
        settings = self._project_tree.get_settings()
        priorities = settings.get("material_priorities")
        if not isinstance(priorities, dict):
            priorities = {}
            settings["material_priorities"] = priorities
        # Update priority
        current_priority = priorities.get(material_name, 0)
        new_priority = current_priority + delta
        priorities[material_name] = new_priority
        # Apply the full settings payload back to the widget.
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._refresh_materials()
        self._reset_simulation_cache()
        self._info_bar.set_info(f"Material '{material_name}' priority set to {new_priority}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� STEP import
    def _import_step(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import STEP File", "",
            "STEP Files (*.step *.stp);;All Files (*)"
        )
        if not path:
            return
        try:
            solids = import_step(
                path,
                mm_per_unit=_MM_PER_UNIT.get(self._units, 1.0),
            )
        except RuntimeError as exc:
            QMessageBox.warning(
                self, "STEP Import",
                str(exc)
            )
            return
        except Exception as exc:
            QMessageBox.critical(self, "STEP Import Error", str(exc))
            return

        from ..scene.em_objects import MeshObject
        
        added = []
        for solid in solids:
            obj = MeshObject(
                name     = solid["name"],
                polydata = solid["polydata"],
                material = self._draw_material,
                color    = solid.get("color"),
                step_source_path = path,
                step_solid_name = solid["name"],
            )
            self._viewport.scene.add_object(obj)
            added.append(obj)

        if added:
            self._viewport.scene.select(added[-1])
            self._viewport.object_selected.emit(added[-1])
            self._viewport.selection_changed.emit([added[-1]])
            self._viewport.scene_changed.emit()
            self._viewport._render()
            self._refresh_materials()
            self._info_bar.set_info(
                f"STEP imported: {len(added)} solid(s) from {Path(path).name}"
            )

    def _open_simulation_window(self) -> None:
        dlg = getattr(self, "_sim_dlg", None)
        if dlg is None:
            dlg = QWidget(self)

            root = QVBoxLayout(dlg)

            log_box = QGroupBox("Verbose execution log")
            log_layout = QVBoxLayout(log_box)
            log_top_row = QHBoxLayout()
            log_top_row.addStretch()
            _btn_clear_log = QPushButton("Clear Log")
            _btn_clear_log.setFixedWidth(90)
            _btn_clear_log.setToolTip("Clear the verbose execution log")
            _btn_clear_log.clicked.connect(lambda: self._sim_log_view.clear() if hasattr(self, "_sim_log_view") else None)
            log_top_row.addWidget(_btn_clear_log)
            log_layout.addLayout(log_top_row)
            self._sim_log_view = QPlainTextEdit(log_box)
            self._sim_log_view.setReadOnly(True)
            self._sim_log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
            log_layout.addWidget(self._sim_log_view)

            self._sim_output_tabs = QTabWidget(dlg)
            self._sim_output_tabs.addTab(log_box, "Execution Log")

            btn_row = QHBoxLayout()
            self._sim_chk_run_sweep = QCheckBox("Run Sweep")
            self._sim_chk_run_sweep.setChecked(True)
            self._sim_chk_run_sweep.setToolTip("Run the configured solver after mesh generation")
            self._sim_chk_run_sweep.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_run_sweep)

            self._sim_chk_show_mesh = QCheckBox("Show Mesh")
            self._sim_chk_show_mesh.setChecked(False)
            self._sim_chk_show_mesh.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_show_mesh)

            self._sim_chk_show_model = QCheckBox("Show Model")
            self._sim_chk_show_model.setChecked(False)
            self._sim_chk_show_model.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_show_model)

            self._sim_chk_preview_only = QCheckBox("Preview Export (no mesh/run)")
            self._sim_chk_preview_only.setChecked(False)
            self._sim_chk_preview_only.setToolTip(
                "Open the raw exported geometry in Gmsh before EMERGE commit; do not mesh or run the simulation"
            )
            self._sim_chk_preview_only.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_preview_only)

            self._sim_chk_boolean_debug = QCheckBox("Boolean Debug", dlg)
            self._sim_chk_boolean_debug.setChecked(False)
            self._sim_chk_boolean_debug.setToolTip("Export only boolean source objects, shifted apart for visual inspection")
            self._sim_chk_boolean_debug.toggled.connect(self._on_sim_option_changed)
            self._sim_chk_boolean_debug.hide()

            self._sim_btn_check = QPushButton("Check Simulation")
            self._sim_btn_check.clicked.connect(self._on_check_simulation)
            btn_row.addWidget(self._sim_btn_check)

            self._sim_btn_generate = QPushButton("Generate Script")
            self._sim_btn_generate.clicked.connect(self._on_sim_generate)
            btn_row.addWidget(self._sim_btn_generate)

            self._sim_btn_save = QPushButton("Save Script")
            self._sim_btn_save.clicked.connect(self._on_sim_save_script)
            btn_row.addWidget(self._sim_btn_save)

            btn_row.addWidget(QLabel("LOG"))
            self._sim_log_level = QComboBox()
            self._sim_log_level.addItems(["Trace", "Debug", "Info", "Warning", "Error"])
            self._sim_log_level.setCurrentText(self._sim_log_verbosity.title())
            self._sim_log_level.currentTextChanged.connect(self._on_sim_log_level_changed)
            btn_row.addWidget(self._sim_log_level)

            self._sim_btn_run = QPushButton("Run")
            self._sim_btn_run.clicked.connect(self._on_sim_run)
            btn_row.addWidget(self._sim_btn_run)

            self._sim_btn_stop = QPushButton("Stop")
            self._sim_btn_stop.clicked.connect(self._on_sim_stop)
            self._sim_btn_stop.setEnabled(False)
            btn_row.addWidget(self._sim_btn_stop)

            btn_row.addStretch(1)

            root.addLayout(btn_row)
            script_box = QGroupBox("Generated Scripts", dlg)
            script_layout = QVBoxLayout(script_box)
            self._sim_script_tabs = QTabWidget(script_box)
            script_layout.addWidget(self._sim_script_tabs)
            root.addWidget(script_box, stretch=3)
            root.addWidget(self._sim_output_tabs, stretch=2)

            self._sim_dlg = dlg
            self._sim_subwindow = _PlotSubWindow(self._workspace_area)
            self._sim_subwindow.setWindowTitle("Simulation")
            self._sim_subwindow.setMinimumSize(500, 360)
            self._sim_subwindow.setWidget(dlg)
            self._sim_subwindow.closed.connect(self._on_workspace_subwindow_closed)
            self._workspace_area.addSubWindow(self._sim_subwindow)
            self._workspace_windows["Simulation"] = self._sim_subwindow
            self._sim_subwindow.resize(680, 520)
            self._sim_subwindow.move(72, 72)

        self._sim_log_view.clear()
        self._show_workspace_window("Simulation")
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        cached_script = getattr(self, "_sim_cached_script_bundle", {}).get("master", "")
        if getattr(self, "_sim_script_dirty", True) or not cached_script:
            try:
                master_script = self._generate_simulation_assets(
                    show_progress=True,
                    force_script=False,
                )
                if master_script:
                    script_path = self._write_cached_simulation_scripts()
                    self._append_sim_log(f"[info] Scripts regenerated and saved: {script_path.parent}")
            except Exception as exc:
                self._append_sim_log(f"[error] Failed to generate script: {exc}")

    def _on_sim_option_changed(self, _checked: bool) -> None:
        self._mark_simulation_dirty(steps=False, script=True)
        info_bar = getattr(self, "_info_bar", None)
        if info_bar is not None:
            info_bar.set_info("Simulation options changed. Generate Script or Run to apply.")

    def _on_check_simulation(self) -> None:
        findings = self._simulation_validation_findings()
        counts = {
            severity: sum(1 for finding in findings if finding.severity == severity)
            for severity in ("ERROR", "WARNING", "OK")
        }
        colors = {"ERROR": "#ef6b73", "WARNING": "#e8b45c", "OK": "#82d39b"}
        symbols = {"ERROR": "ERROR", "WARNING": "WARNING", "OK": "OK"}
        rows = []
        for finding in findings:
            color = colors.get(finding.severity, "#e8edf5")
            symbol = symbols.get(finding.severity, finding.severity)
            rows.append(
                "<tr>"
                f"<td style='color:{color};font-weight:600;padding:5px 10px'>{symbol}</td>"
                f"<td style='padding:5px 10px'>{escape(finding.category)}</td>"
                f"<td style='padding:5px 10px'>{escape(finding.message)}</td>"
                "</tr>"
            )

        dlg = QDialog(self)
        dlg.setWindowTitle("Simulation Check")
        dlg.resize(900, 560)
        layout = QVBoxLayout(dlg)
        summary = QLabel(
            f"Errors: {counts['ERROR']}    Warnings: {counts['WARNING']}    Passed: {counts['OK']}",
            dlg,
        )
        summary.setStyleSheet("font-weight: bold; padding: 6px;")
        layout.addWidget(summary)
        report = QTextBrowser(dlg)
        report.setHtml(
            "<table cellspacing='0' width='100%' style='color:#dce4ef'>"
            "<tr><th align='left'>Status</th><th align='left'>Category</th><th align='left'>Check</th></tr>"
            + "".join(rows)
            + "</table>"
        )
        layout.addWidget(report)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dlg)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        self._append_sim_log(
            f"[check] Simulation validation: {counts['ERROR']} error(s), "
            f"{counts['WARNING']} warning(s), {counts['OK']} passed."
        )
        dlg.exec()

    def _simulation_validation_findings(self):
        return validate_simulation(
            self._viewport.scene.objects,
            self._project_tree.get_settings(),
            self._material_store.material_export_catalog(),
            self._collect_plate_lumped_ports(),
        )

    def _normalize_log_level(self, value: str) -> str:
        level = str(value or "").strip().upper()
        return level if level in _LOG_LEVEL_ORDER else "INFO"

    def _step_log_enabled(self, level: str) -> bool:
        want = _LOG_LEVEL_ORDER.get(self._normalize_log_level(level), 20)
        current = _LOG_LEVEL_ORDER.get(self._normalize_log_level(self._sim_log_verbosity), 20)
        return want >= current

    def _sync_log_verbosity_from_settings(self) -> None:
        try:
            self._sim_log_verbosity = self._normalize_log_level(self._project_tree.get_log_verbosity())
        except Exception:
            self._sim_log_verbosity = "INFO"

        combo = getattr(self, "_sim_log_level", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentText(self._sim_log_verbosity.title())
            combo.blockSignals(False)

    def _on_sim_log_level_changed(self, text: str) -> None:
        self._sim_log_verbosity = self._normalize_log_level(text)
        try:
            self._project_tree.set_log_verbosity(self._sim_log_verbosity.title())
        except Exception:
            pass
        self._append_sim_log(f"[info] STEP export log verbosity: {self._sim_log_verbosity}")

    @staticmethod
    def _is_plate_role_object(obj) -> bool:
        return type(obj).__name__ == "PlateObject" or bool(getattr(obj, "plate_role", False))

    def _collect_plate_lumped_ports(self) -> list[dict]:
        settings_ports = self._project_tree.get_settings().get("ports", [])
        if not isinstance(settings_ports, list):
            return []

        plate_by_name = {
            str(obj.name): obj
            for obj in self._viewport.scene.objects
            if self._is_plate_role_object(obj) and bool(getattr(obj, "is_model", True))
        }
        air_objects = [
            obj for obj in self._viewport.scene.objects
            if str(getattr(obj, "material", "")).strip().upper() == "AIR"
            and getattr(obj, "actor", None) is not None
        ]
        air_min = air_max = None
        if air_objects:
            bounds = [obj.actor.GetBounds() for obj in air_objects]
            air_min = [min(float(item[axis * 2]) for item in bounds) for axis in range(3)]
            air_max = [max(float(item[axis * 2 + 1]) for item in bounds) for axis in range(3)]

        entries: list[dict] = []
        port_index = 1
        for port in settings_ports:
            if not isinstance(port, dict):
                continue
            port_type = str(port.get("type", "")).strip()
            if port_type not in {"LumpedPort", "WaveguidePort"}:
                continue

            obj_name = str(port.get("object", "")).strip()
            if not obj_name:
                continue
            plate = plate_by_name.get(obj_name)
            if plate is None:
                continue

            if type(plate).__name__ == "PlateObject":
                p = plate.get_parameters()
                xmin, xmax = sorted([float(p.get("X1", 0.0)), float(p.get("X2", 0.0))])
                ymin, ymax = sorted([float(p.get("Y1", 0.0)), float(p.get("Y2", 0.0))])
                zmin, zmax = sorted([float(p.get("Z1", 0.0)), float(p.get("Z2", 0.0))])
                points_are_world = False
            else:
                bounds = plate.actor.GetBounds()
                xmin, xmax, ymin, ymax, zmin, zmax = [float(value) for value in bounds]
                points_are_world = True

            spans = [xmax - xmin, ymax - ymin, zmax - zmin]
            normal_axis = min(range(3), key=lambda i: abs(spans[i]))
            tangent_axes = [a for a in (0, 1, 2) if a != normal_axis]

            mins = [xmin, ymin, zmin]
            maxs = [xmax, ymax, zmax]
            corners = {
                0: ((xmin, ymin, zmin), (xmin, ymax, zmin), (xmin, ymax, zmax), (xmin, ymin, zmax)),
                1: ((xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymin, zmax), (xmin, ymin, zmax)),
                2: ((xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymax, zmin), (xmin, ymax, zmin)),
            }[normal_axis]
            matrix = plate.actor.GetMatrix()
            world = [(*point, 1.0) for point in corners] if points_are_world else [matrix.MultiplyPoint([*point, 1.0]) for point in corners]
            origin = list(world[0][:3])
            u = [world[1][axis] - world[0][axis] for axis in range(3)]
            v = [world[3][axis] - world[0][axis] for axis in range(3)]

            normal_local = [0.0, 0.0, 0.0]
            normal_local[normal_axis] = 1.0
            normal_tip_local = [
                origin_local + component
                for origin_local, component in zip(corners[0], normal_local)
            ]
            normal_origin_world = (*corners[0], 1.0) if points_are_world else matrix.MultiplyPoint([*corners[0], 1.0])
            normal_tip_world = (*normal_tip_local, 1.0) if points_are_world else matrix.MultiplyPoint([*normal_tip_local, 1.0])
            normal_vector = [
                normal_tip_world[axis] - normal_origin_world[axis]
                for axis in range(3)
            ]

            params = port.get("params", {}) if isinstance(port.get("params", {}), dict) else {}
            configured_direction = [
                float(params.get("Direction_X", 0.0)),
                float(params.get("Direction_Y", 0.0)),
                float(params.get("Direction_Z", 0.0)),
            ]
            direction_magnitude = sum(value * value for value in configured_direction) ** 0.5
            if direction_magnitude > 1e-9:
                # An explicit port direction is part of the user's excitation
                # definition and must be preserved in the generated script.
                direction = [value / direction_magnitude for value in configured_direction]
            else:
                normal_length = sum(value * value for value in normal_vector) ** 0.5
                direction = (
                    [value / normal_length for value in normal_vector]
                    if normal_length > 1e-12
                    else [0.0, 0.0, 1.0]
                )
            if port_type == "WaveguidePort":
                mode_text = str(params.get("Mode", "TE10")).strip().upper()
                mode_type = "TM" if mode_text.startswith("TM") else "TE"
                digits = mode_text[2:] if mode_text.startswith(("TE", "TM")) else "10"
                try:
                    mode = (int(digits[0]), int(digits[1]))
                except (ValueError, IndexError):
                    mode = (1, 0)
                z0 = float(params.get("Impedance_Ohm", 50.0))
                power = float(params.get("Excitation", 1.0))
            else:
                mode_type = "TE"
                mode = (1, 0)
                z0 = float(params.get("Resistance_Ohm", 50.0))
                power = float(params.get("Voltage_V", 1.0))
            entry = {
                    # EMERGE requires contiguous technical port IDs (1..N).
                    # The user-selected number remains available as metadata/display.
                    "index": port_index,
                    "display_number": port.get("number", port_index),
                    "name": str(port.get("name", f"Port_{port_index}")),
                    "type": port_type,
                    "plate_name": obj_name,
                    "origin": origin,
                    "u": u,
                    "v": v,
                    "width": sum(value * value for value in u) ** 0.5,
                    "height": sum(value * value for value in v) ** 0.5,
                    "direction": direction,
                    "z0": z0,
                    "power": power,
                }
            entry["mode_type"] = mode_type
            entry["mode"] = mode
            if port_type == "WaveguidePort" and air_min is not None and air_max is not None:
                center = [
                    origin[axis] + 0.5 * u[axis] + 0.5 * v[axis]
                    for axis in range(3)
                ]
                normal_axis = max(range(3), key=lambda axis: abs(float(direction[axis])))
                distance_to_min = abs(center[normal_axis] - air_min[normal_axis])
                distance_to_max = abs(air_max[normal_axis] - center[normal_axis])
                axis_name = ("x", "y", "z")[normal_axis]
                entry["face_name"] = (
                    f"-{axis_name}" if distance_to_min <= distance_to_max else f"+{axis_name}"
                )
            entries.append(entry)
            port_index += 1
        return entries

    def _collect_emerge_plates(self) -> list[dict]:
        port_names = {
            str(port.get("object", "")).strip()
            for port in self._project_tree.get_settings().get("ports", [])
            if isinstance(port, dict) and str(port.get("object", "")).strip()
        }
        entries = []
        for obj in self._viewport.scene.objects:
            if not self._is_plate_role_object(obj) or not bool(getattr(obj, "is_model", True)):
                continue
            if str(getattr(obj, "name", "")).strip() in port_names:
                continue
            if type(obj).__name__ == "PlateObject":
                params = obj.get_parameters()
                x1, x2 = sorted((float(params.get("X1", 0.0)), float(params.get("X2", 0.0))))
                y1, y2 = sorted((float(params.get("Y1", 0.0)), float(params.get("Y2", 0.0))))
                z1, z2 = sorted((float(params.get("Z1", 0.0)), float(params.get("Z2", 0.0))))
                points_are_world = False
            else:
                x1, x2, y1, y2, z1, z2 = [float(value) for value in obj.actor.GetBounds()]
                points_are_world = True
            spans = (x2 - x1, y2 - y1, z2 - z1)
            normal_axis = min(range(3), key=lambda index: abs(spans[index]))
            corners = {
                0: ((x1, y1, z1), (x1, y2, z1), (x1, y2, z2), (x1, y1, z2)),
                1: ((x1, y1, z1), (x2, y1, z1), (x2, y1, z2), (x1, y1, z2)),
                2: ((x1, y1, z1), (x2, y1, z1), (x2, y2, z1), (x1, y2, z1)),
            }[normal_axis]
            matrix = obj.actor.GetMatrix()
            world = [(*point, 1.0) for point in corners] if points_are_world else [matrix.MultiplyPoint([*point, 1.0]) for point in corners]
            origin = world[0][:3]
            u = tuple(world[1][index] - world[0][index] for index in range(3))
            v = tuple(world[3][index] - world[0][index] for index in range(3))
            entries.append({"object_name": str(obj.name), "material": str(obj.material), "origin": origin, "u": u, "v": v})
        return entries

    def _enabled_simulations(self, settings: dict) -> list[dict]:
        sims = settings.get("simulations", [])
        out: list[dict] = []
        if isinstance(sims, list):
            for idx, item in enumerate(sims, start=1):
                if not isinstance(item, dict):
                    continue
                if not bool(item.get("enabled", True)):
                    continue
                name = str(item.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
                out.append({
                    "name": name,
                    "type": str(item.get("type", "Sweep")).strip() or "Sweep",
                    "enabled": True,
                    "Fmin_GHz": item.get("Fmin_GHz", 0.1),
                    "Fmax_GHz": item.get("Fmax_GHz", 10.0),
                    "Fstep_GHz": item.get("Fstep_GHz", 0.1),
                    "NumberOfPoints": item.get("NumberOfPoints", 100),
                    "FminFormula": item.get("FminFormula", ""),
                    "FmaxFormula": item.get("FmaxFormula", ""),
                    "FstepFormula": item.get("FstepFormula", ""),
                    "NumberOfPointsFormula": item.get("NumberOfPointsFormula", ""),
                    "ActiveVariable": item.get("ActiveVariable", ""),
                    "EigenmodeCount": item.get("EigenmodeCount", 5),
                    "ParamName": item.get("ParamName", ""),
                    "ParamValues": item.get("ParamValues", ""),
                    "progressive_sparams_enabled": bool(item.get("progressive_sparams_enabled", False)),
                    "progressive_sparams_chunk_size": max(
                        1, int(item.get("progressive_sparams_chunk_size", 10))
                    ),
                    "sparam_fitting": dict(item.get("sparam_fitting", {})) if isinstance(item.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
                    "LogVerbosity": item.get("LogVerbosity", "Info"),
                })

        if out:
            return out

        legacy = settings.get("simulation", {})
        if not isinstance(legacy, dict):
            legacy = {}
        return [{
            "name": "Simulation_1",
            "type": "Sweep",
            "enabled": True,
            "Fmin_GHz": legacy.get("Fmin_GHz", 0.1),
            "Fmax_GHz": legacy.get("Fmax_GHz", 10.0),
            "Fstep_GHz": legacy.get("Fstep_GHz", 0.1),
            "NumberOfPoints": legacy.get("NumberOfPoints", 100),
            "FminFormula": legacy.get("FminFormula", ""),
            "FmaxFormula": legacy.get("FmaxFormula", ""),
            "FstepFormula": legacy.get("FstepFormula", ""),
            "NumberOfPointsFormula": legacy.get("NumberOfPointsFormula", ""),
            "ActiveVariable": legacy.get("ActiveVariable", ""),
            "EigenmodeCount": legacy.get("EigenmodeCount", 5),
            "ParamName": legacy.get("ParamName", ""),
            "ParamValues": legacy.get("ParamValues", ""),
            "progressive_sparams_enabled": bool(legacy.get("progressive_sparams_enabled", False)),
            "progressive_sparams_chunk_size": max(
                1, int(legacy.get("progressive_sparams_chunk_size", 10))
            ),
            "sparam_fitting": dict(legacy.get("sparam_fitting", {})) if isinstance(legacy.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
            "LogVerbosity": legacy.get("LogVerbosity", "Info"),
        }]

    def _safe_script_token(self, value: str) -> str:
        token = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value))
        token = token.strip("_")
        return token or "Simulation"

    def _build_master_simulation_script(
        self,
        jobs: list[dict],
    ) -> str:
        job_lines = []
        for job in jobs:
            job_lines.append(
                "        {"
                f"'name': {repr(str(job.get('name', 'Simulation')))}, "
                f"'type': {repr(str(job.get('type', 'Sweep')).strip().lower())}, "
                f"'filename': {repr(str(job.get('filename', 'simulation_emerge_run.py')))}, "
                f"'completion_name': {repr(str(job.get('completion_name', job.get('name', 'Simulation'))))}, "
                f"'parameter_index': {repr(job.get('parameter_index'))}, "
                f"'parameter_total': {repr(job.get('parameter_total'))}"
                "},"
            )
        jobs_literal = "\n".join(job_lines) if job_lines else ""
        return (
            "# Auto-generated EMERGE master script\n"
            "import os\n"
            "import sys\n"
            "import json\n"
            "import subprocess\n\n"
            "import threading\n\n"
            "import time\n\n"
            "STOP_ON_ERROR = True\n\n"
            "def _run_children() -> int:\n"
            "    script_dir = os.path.dirname(os.path.abspath(__file__))\n"
            "    cancel_file = os.path.join(script_dir, '.em3d_simulation_cancel')\n"
            "    active_pid_file = os.path.join(script_dir, '.em3d_simulation_active.pid')\n"
            "    if hasattr(sys.stdout, 'reconfigure'):\n"
            "        sys.stdout.reconfigure(errors='replace')\n"
            "    jobs = [\n"
            f"{jobs_literal}\n"
            "    ]\n"
            "    run_id = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns() % 1000000:06d}'\n"
            "    for job in jobs:\n"
            "        if os.path.exists(cancel_file):\n"
            "            print('[master] Cancellation requested; remaining jobs will not start.')\n"
            "            return 130\n"
            "        child = str(job.get('filename', ''))\n"
            "        child_path = os.path.join(script_dir, child)\n"
            "        env = os.environ.copy()\n"
            "        env['PYTHONIOENCODING'] = 'utf-8'\n"
            "        env['PYTHONUTF8'] = '1'\n"
            "        env['EM3D_RUN_IN_APP'] = '1'\n"
            "        env['EM3D_RUN_ID'] = run_id\n"
            "        if job.get('parameter_index') is not None:\n"
            "            env['EM3D_PARAMETRIC_INDEX'] = str(job['parameter_index'])\n"
            "            env['EM3D_PARAMETRIC_TOTAL'] = str(job['parameter_total'])\n"
            "        print(f\"[master] Running job '{job.get('name')}' from {child_path}\")\n"
            "        command = ([sys.executable, '--em3d-run-script', child_path] if getattr(sys, 'frozen', False) else [sys.executable, '-u', child_path])\n"
            "        process = subprocess.Popen(\n"
            "            command, cwd=script_dir, env=env, stdout=subprocess.PIPE,\n"
            "            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1,\n"
            "        )\n"
            "        def _forward_output():\n"
            "            if process.stdout is None:\n"
            "                return\n"
            "            with process.stdout:\n"
            "                for line in process.stdout:\n"
            "                    print(line, end='', flush=True)\n"
            "        output_thread = threading.Thread(target=_forward_output, daemon=True)\n"
            "        output_thread.start()\n"
            "        with open(active_pid_file, 'w', encoding='ascii') as pid_file:\n"
            "            pid_file.write(str(process.pid))\n"
            "        while process.poll() is None:\n"
            "            if os.path.exists(cancel_file):\n"
            "                print(f\"[master] Cancellation requested; stopping job '{job.get('name')}'.\")\n"
            "                process.terminate()\n"
            "                try:\n"
            "                    process.wait(timeout=5)\n"
            "                except subprocess.TimeoutExpired:\n"
            "                    process.kill()\n"
            "                output_thread.join()\n"
            "                return 130\n"
            "            time.sleep(0.2)\n"
            "        output_thread.join()\n"
            "        try:\n"
            "            os.remove(active_pid_file)\n"
            "        except FileNotFoundError:\n"
            "            pass\n"
            "        if process.returncode != 0:\n"
            "            print(f\"[master] Job failed ({process.returncode}): {job.get('name')}\")\n"
            "            if STOP_ON_ERROR:\n"
            "                return int(process.returncode)\n"
            "        else:\n"
            "            print('EM3D_JOB_COMPLETED:' + json.dumps({'name': str(job.get('completion_name', job.get('name', ''))), 'filename': child}, separators=(',', ':')), flush=True)\n"
            "    print('[master] All simulations completed successfully.')\n"
            "    return 0\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(_run_children())\n"
        )

    def _export_simulation_step_bundle(
        self,
        *,
        objects: list,
        bundle_dir: Path,
        material_priorities: dict,
        excluded_object_names: set[str],
        progress_callback=None,
        mm_per_unit: float = 1.0,
    ) -> dict:
        def log_export(level: str, message: str) -> None:
            normalized_level = self._normalize_log_level(level)
            if (
                normalized_level == "DEBUG"
                and message.startswith("object_start")
                and progress_callback is not None
            ):
                object_name = message.partition("name=")[2].partition(" type=")[0]
                progress_callback(0, len(objects), object_name)
            if normalized_level in {"WARNING", "ERROR"}:
                self._append_step_export_log(
                    f"[{normalized_level.lower()}] {message}",
                    level=normalized_level,
                )

        result = export_objects_to_step_bundle(
            objects=objects,
            bundle_dir=bundle_dir,
            material_priorities=material_priorities,
            excluded_object_names=excluded_object_names,
            progress_callback=progress_callback,
            log_callback=log_export,
            mm_per_unit=mm_per_unit,
        )
        skipped = list(result.get("skipped", []))
        missing_files = [
            str(entry.get("step_file", ""))
            for entry in result.get("entries", [])
            if not (bundle_dir / str(entry.get("step_file", ""))).is_file()
        ]
        if skipped or missing_files:
            details = []
            if skipped:
                details.append(f"{len(skipped)} object(s) skipped: {', '.join(skipped)}")
            if missing_files:
                details.append(f"STEP file(s) missing: {', '.join(missing_files)}")
            message = f"STEP export incomplete in {bundle_dir}: {'; '.join(details)}"
            self._append_step_export_log(f"[error] {message}", level="ERROR")
            raise RuntimeError(message)
        return result

    def _cached_simulation_step_bundle(
        self,
        *,
        objects: list,
        bundle_dir: Path,
        material_priorities: dict,
        excluded_object_names: set[str],
        cache_token: tuple | None = None,
        progress_callback=None,
        mm_per_unit: float = 1.0,
    ) -> dict:
        cache = getattr(self, "_simulation_step_export_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._simulation_step_export_cache = cache
        cache_key = self._simulation_step_cache_key(
            bundle_dir=bundle_dir,
            material_priorities=material_priorities,
            excluded_object_names=excluded_object_names,
            cache_token=cache_token,
            mm_per_unit=mm_per_unit,
        )
        cached = self._get_cached_simulation_step_bundle(cache_key, bundle_dir)
        if cached is not None and not _boolean_step_export_issues(cached):
            return cached
        if cached is not None:
            self._append_step_export_log(
                "[warning] Cached STEP bundle contains faceted geometry; rebuilding after geometry recomputation.",
                level="WARNING",
            )

        result = self._export_simulation_step_bundle(
            objects=objects,
            bundle_dir=bundle_dir,
            material_priorities=material_priorities,
            excluded_object_names=excluded_object_names,
            progress_callback=progress_callback,
            mm_per_unit=mm_per_unit,
        )
        cache[cache_key] = result
        return result

    def _simulation_step_cache_key(
        self,
        *,
        bundle_dir: Path,
        material_priorities: dict,
        excluded_object_names: set[str],
        cache_token: tuple | None = None,
        mm_per_unit: float = 1.0,
    ) -> tuple:
        priority_key = tuple(
            sorted((str(name), repr(value)) for name, value in material_priorities.items())
        ) if isinstance(material_priorities, dict) else ()
        return (
            str(bundle_dir.expanduser().resolve()),
            tuple(sorted(str(name) for name in excluded_object_names)),
            priority_key,
            float(mm_per_unit),
            cache_token,
        )

    def _get_cached_simulation_step_bundle(
        self, cache_key: tuple, bundle_dir: Path
    ) -> dict | None:
        cache = getattr(self, "_simulation_step_export_cache", None)
        if not isinstance(cache, dict):
            return None
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            entries = cached.get("entries", [])
            if isinstance(entries, list) and all(
                isinstance(entry, dict)
                and str(entry.get("step_file", "")).strip()
                and (bundle_dir / str(entry["step_file"])).is_file()
                for entry in entries
            ):
                return cached
        return None

    def _missing_boolean_source_links(self) -> list[str]:
        from collections import Counter

        missing = []
        for obj in self._simulation_model_objects():
            operation = str(getattr(obj, "boolean_op", "") or "").strip().lower()
            if operation not in {"fuse", "cut", "common"}:
                continue
            live_sources = list(getattr(obj, "source_objects", []) or [])
            unique_live_sources = list({id(source): source for source in live_sources}.values())
            live_names = Counter(
                str(getattr(source, "name", "")).strip()
                for source in unique_live_sources
                if str(getattr(source, "name", "")).strip()
            )
            expected_names = [
                str(name).strip()
                for name in list(getattr(obj, "boolean_source_names", []) or [])
                if str(name).strip()
            ]
            if not expected_names:
                expected_names = [
                    str(snapshot.get("name", "")).strip()
                    for snapshot in list(getattr(obj, "boolean_sources_data", []) or [])
                    if isinstance(snapshot, dict) and str(snapshot.get("name", "")).strip()
                ]
            expected_counts = Counter(expected_names)
            linked_count = len(unique_live_sources)
            expected_count = max(2, len(expected_names))
            if linked_count < expected_count or any(
                live_names[name] < count for name, count in expected_counts.items()
            ):
                missing.append(
                    f"{getattr(obj, 'name', 'Boolean object')} "
                    f"({linked_count}/{expected_count} sources linked)"
                )
        return missing

    def _ask_missing_boolean_source_links(
        self, missing: list[str], *, allow_retry: bool
    ) -> QMessageBox.StandardButton:
        choices = QMessageBox.Ignore | QMessageBox.Cancel
        if allow_retry:
            choices |= QMessageBox.Retry
        retry_text = "Retry rebuilds the Boolean geometry. " if allow_retry else ""
        text = (
            "Some Boolean objects still have missing source links after geometry recomputation:\n\n"
            + "\n".join(missing)
            + "\n\n"
            + retry_text
            + "Continue keeps the scene mesh and may create a "
            "faceted STEP that meshes slowly. Cancel stops script generation so the Boolean sources "
            "can be repaired in the project."
        )
        return QMessageBox.warning(
            self,
            "Boolean Source Links Missing",
            text,
            choices,
            QMessageBox.Cancel,
        )

    def _confirm_faceted_step_export(self, issues: list[str]) -> bool:
        self._append_step_export_log(
            "[warning] STEP export validation found faceted or non-solid geometry: "
            + "; ".join(issues),
            level="WARNING",
        )
        self._append_sim_log(
            "[warn] STEP validation found faceted/non-solid geometry: " + "; ".join(issues)
        )
        reply = QMessageBox.warning(
            self,
            "STEP Export Validation",
            "The sweep STEP bundle contains faceted or non-solid geometry:\n\n"
            + "\n".join(issues)
            + "\n\nContinue may make EMerge 2D meshing very slow. Continue with this STEP export, "
            "or cancel and repair/rebuild the Boolean geometry first?",
            QMessageBox.Ignore | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Ignore:
            self._append_sim_log("[warn] User accepted the faceted STEP export for sweep preparation.")
            return True
        self._append_sim_log("[info] Sweep preparation cancelled after STEP export validation.")
        return False

    def _build_simulation_script_bundle(self, step_entries: list[dict], show_model: bool, show_mesh: bool, run_sweep: bool, preview_only: bool = False, progress_callback=None) -> dict:
        settings = self._project_tree.get_settings()
        mesh_cfg = settings.get("mesh", {}) if isinstance(settings, dict) else {}
        runtime_cfg = settings.get("runtime", {}) if isinstance(settings, dict) else {}
        if not isinstance(runtime_cfg, dict):
            runtime_cfg = {}
        runtime_solver = str(runtime_cfg.get("solver", self._sim_solver)).strip().upper()
        if runtime_solver not in _EMERGE_SOLVERS:
            runtime_solver = self._sim_solver
        runtime_parallel = bool(runtime_cfg.get("parallel_enabled", self._sim_parallel_enabled))
        runtime_pardiso_threads = max(1, int(runtime_cfg.get("pardiso_threads", self._sim_pardiso_threads)))
        runtime_acc_threads = max(1, int(runtime_cfg.get("acc_threads", self._sim_acc_threads)))
        runtime_plot_sparams = bool(runtime_cfg.get("plot_sparams_after_sim", self._sim_plot_sparams_after_sim))
        runtime_export_sparams = bool(runtime_cfg.get("export_sparams_after_sim", self._sim_export_sparams_after_sim))
        mesh_fraction = self._mesh_resolution
        if isinstance(mesh_cfg, dict):
            try:
                mesh_fraction = max(0.01, min(1.0, float(mesh_cfg.get("default_fraction", mesh_fraction))))
            except Exception:
                pass

        enabled_sims = self._enabled_simulations(settings)
        work_total = sum(
            max(1, len([
                value for value in str(sim_cfg.get("ParamValues", "")).split(",")
                if value.strip()
            ]))
            if str(sim_cfg.get("type", "Sweep")).strip().lower() == "parametric"
            else 1
            for sim_cfg in enabled_sims
        ) or 1
        work_completed = 0

        def report_progress(message: str, current_fraction: float | None = None, *, log: bool = False) -> None:
            if progress_callback is not None:
                progress_callback(
                    message,
                    work_completed,
                    work_total,
                    current_fraction,
                    log,
                )

        scripts: list[dict] = []
        sweep_geometry_recomputed = False
        for idx, sim_cfg in enumerate(enabled_sims, start=1):
            sim_name = str(sim_cfg.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
            child_settings = dict(settings)
            child_mesh_cfg = dict(mesh_cfg) if isinstance(mesh_cfg, dict) else {}
            child_mesh_cfg["curved_boundary_resolution"] = self._curved_boundary_resolution
            child_settings["mesh"] = child_mesh_cfg
            child_settings["simulations"] = [dict(sim_cfg)]
            child_settings["simulation"] = {
                "Fmin_GHz": sim_cfg.get("Fmin_GHz", 0.1),
                "Fmax_GHz": sim_cfg.get("Fmax_GHz", 10.0),
                "Fstep_GHz": sim_cfg.get("Fstep_GHz", 0.1),
                "LogVerbosity": sim_cfg.get("LogVerbosity", "Info"),
            }
            safe_project = self._safe_script_token(self._project_name)
            safe_sim = self._safe_script_token(sim_name)
            filename = f"{safe_project}_{idx:02d}_{safe_sim}_emerge_run.py"

            if str(sim_cfg.get("type", "Sweep")).strip().lower() == "parametric":
                parameter_name = str(sim_cfg.get("ParamName", "")).strip()
                parameter_values = [
                    value.strip()
                    for value in str(sim_cfg.get("ParamValues", "")).split(",")
                    if value.strip()
                ] or ["default"]
                original_parameter_values = self._parameter_values()
                port_metadata_key = json.dumps(
                    settings.get("ports", []), sort_keys=True, default=str
                )
                simulation_root = self._simulation_bundle_dir() / f"{safe_project}_{safe_sim}"
                geometry_recomputed = False
                try:
                    for parameter_index, parameter_value in enumerate(parameter_values, start=1):
                        report_progress(
                            f"Preparing STEP {parameter_index}/{len(parameter_values)}: {sim_name} ({parameter_name}={parameter_value})",
                            0.0,
                            log=True,
                        )
                        value_token = self._safe_script_token(parameter_value)
                        step_dir_name = f"Step_{parameter_index:03d}_{value_token}"
                        step_dir = simulation_root / step_dir_name
                        step_parameter_values = dict(original_parameter_values)
                        if parameter_name:
                            try:
                                step_parameter_values[parameter_name] = float(parameter_value)
                            except ValueError:
                                pass

                        material_priorities = settings.get("material_priorities", {})
                        plate_names = {
                            str(obj.name).strip()
                            for obj in self._simulation_model_objects()
                            if self._is_plate_role_object(obj)
                        }
                        cache_token = (parameter_name, parameter_value)
                        cache_key = self._simulation_step_cache_key(
                            bundle_dir=step_dir,
                            material_priorities=material_priorities,
                            excluded_object_names=plate_names,
                            cache_token=cache_token,
                            mm_per_unit=_MM_PER_UNIT.get(getattr(self, "_units", "mm"), 1.0),
                        )
                        step_bundle = self._get_cached_simulation_step_bundle(
                            cache_key, step_dir
                        )
                        metadata = (
                            step_bundle.get("_parametric_port_metadata", {})
                            if isinstance(step_bundle, dict)
                            else {}
                        )
                        port_metadata = metadata.get(port_metadata_key) if isinstance(metadata, dict) else None
                        if (
                            not isinstance(port_metadata, dict)
                            or not isinstance(port_metadata.get("lumped_ports"), list)
                            or not isinstance(port_metadata.get("plate_entries"), list)
                            or step_bundle is None
                        ):
                            def report_step_progress(done: int, total: int, object_name: str) -> None:
                                fraction = done / total if total else None
                                report_progress(
                                    f"STEP {parameter_index}/{len(parameter_values)}: {object_name} ({done}/{total})",
                                    fraction,
                                )

                            report_progress(
                                f"Recomputing geometry {parameter_index}/{len(parameter_values)}: {parameter_name}={parameter_value}",
                                0.0,
                                log=True,
                            )
                            geometry_recomputed = True
                            self._recompute_parametric_objects(
                                parameter_values=step_parameter_values
                            )
                            port_metadata = {
                                "lumped_ports": self._collect_plate_lumped_ports(),
                                "plate_entries": self._collect_emerge_plates(),
                            }
                            if step_bundle is None:
                                report_progress("Parametric geometry ready; exporting STEP...", 0.0, log=True)
                                step_objects = self._simulation_model_objects()
                                plate_names = {
                                    str(obj.name).strip()
                                    for obj in step_objects
                                    if self._is_plate_role_object(obj)
                                }
                                step_bundle = self._cached_simulation_step_bundle(
                                    objects=step_objects,
                                    bundle_dir=step_dir,
                                    material_priorities=material_priorities,
                                    excluded_object_names=plate_names,
                                    cache_token=cache_token,
                                    progress_callback=report_step_progress,
                                    mm_per_unit=_MM_PER_UNIT.get(getattr(self, "_units", "mm"), 1.0),
                                )
                            metadata = step_bundle.setdefault("_parametric_port_metadata", {})
                            metadata[port_metadata_key] = port_metadata
                        else:
                            report_progress("Reusing cached STEP and port geometry", 1.0)

                        step_simulation = dict(sim_cfg)
                        step_simulation["ParamValues"] = parameter_value
                        child_settings = dict(child_settings)
                        child_settings["simulations"] = [step_simulation]
                        child_simulation_config = dict(child_settings["simulation"])
                        child_simulation_config.update({
                            "Fmin_GHz": step_simulation.get("Fmin_GHz", 0.1),
                            "Fmax_GHz": step_simulation.get("Fmax_GHz", 10.0),
                            "Fstep_GHz": step_simulation.get("Fstep_GHz", 0.1),
                            "LogVerbosity": step_simulation.get("LogVerbosity", "Info"),
                        })
                        child_settings["simulation"] = child_simulation_config
                        step_filename = f"{safe_project}_{idx:02d}_{safe_sim}_step_{parameter_index:03d}.py"
                        relative_filename = str(Path(safe_project + "_" + safe_sim) / step_dir_name / step_filename)
                        report_progress(
                            f"Generating script {parameter_index}/{len(parameter_values)}: {sim_name}",
                            0.9,
                            log=True,
                        )
                        script_text = export_emerge_python_script(
                            project_name=self._project_name,
                            settings=child_settings,
                            step_entries=step_bundle.get("entries", []),
                            units=self._units,
                            materials_catalog=self._material_store.material_export_catalog(),
                            show_model=show_model,
                            preview_only=preview_only,
                            show_mesh=show_mesh,
                            run_sweep=run_sweep,
                            lumped_ports=port_metadata["lumped_ports"],
                            plate_entries=port_metadata["plate_entries"],
                            solver=runtime_solver,
                            parallel_enabled=runtime_parallel,
                            pardiso_threads=runtime_pardiso_threads,
                            acc_threads=runtime_acc_threads,
                            mesh_resolution_fraction=mesh_fraction,
                            plot_sparams_after_sim=runtime_plot_sparams,
                            progressive_sparams_enabled=bool(step_simulation.get("progressive_sparams_enabled", False)),
                            export_sparams_after_sim=True,
                            simulation_override=step_simulation,
                        )
                        clean_filename = f"{Path(step_filename).stem}_clean.py"
                        clean_script_text = build_clean_emerge_python_script(
                            script_text,
                            "parametric",
                        )
                        scripts.append({
                            "name": f"{sim_name} ({parameter_name}={parameter_value})" if parameter_name else f"{sim_name} ({parameter_value})",
                            "job_name": sim_name,
                            "type": "parametric",
                            "index": idx,
                            "parameter_index": parameter_index,
                            "parameter_total": len(parameter_values),
                            "completion_name": sim_name if parameter_index == len(parameter_values) else "",
                            "filename": relative_filename,
                            "content": script_text,
                            "clean_filename": str(Path(relative_filename).with_name(clean_filename)),
                            "clean_content": clean_script_text,
                        })
                        work_completed += 1
                        report_progress(
                            f"Generated script {parameter_index}/{len(parameter_values)}: {sim_name}",
                            log=True,
                        )
                finally:
                    if geometry_recomputed:
                        self._recompute_parametric_objects(
                            parameter_values=original_parameter_values
                        )
                continue

            simulation_root = self._simulation_bundle_dir() / f"{safe_project}_{safe_sim}"
            if not sweep_geometry_recomputed:
                report_progress("Recomputing geometry before sweep STEP export...", 0.0, log=True)
                self._recompute_parametric_objects(parameter_values=self._parameter_values())
                sweep_geometry_recomputed = True
                missing_sources = self._missing_boolean_source_links()
                if missing_sources:
                    choice = self._ask_missing_boolean_source_links(missing_sources, allow_retry=True)
                    if choice == QMessageBox.Retry:
                        self._recompute_parametric_objects(parameter_values=self._parameter_values())
                        missing_sources = self._missing_boolean_source_links()
                        if missing_sources:
                            choice = self._ask_missing_boolean_source_links(missing_sources, allow_retry=False)
                        else:
                            choice = QMessageBox.Retry
                    if choice != QMessageBox.Retry and choice != QMessageBox.Ignore:
                        raise _SimulationPreparationCancelled
                    if choice == QMessageBox.Ignore:
                        self._append_sim_log(
                            "[warn] Continuing sweep preparation with unresolved Boolean source links; STEP may be faceted."
                        )
            report_progress(f"Preparing STEP: {sim_name}", 0.0, log=True)
            plate_names = {
                str(obj.name).strip()
                for obj in self._simulation_model_objects()
                if self._is_plate_role_object(obj)
            }
            simulation_step_bundle = self._cached_simulation_step_bundle(
                objects=self._simulation_model_objects(),
                bundle_dir=simulation_root,
                material_priorities=settings.get("material_priorities", {}),
                excluded_object_names=plate_names,
                progress_callback=lambda done, total, object_name: report_progress(
                    f"STEP: {object_name} ({done}/{total})",
                    done / total if total else None,
                ),
                mm_per_unit=_MM_PER_UNIT.get(getattr(self, "_units", "mm"), 1.0),
            )
            export_issues = _boolean_step_export_issues(simulation_step_bundle)
            if export_issues and not self._confirm_faceted_step_export(export_issues):
                raise _SimulationPreparationCancelled
            report_progress(f"Generating script: {sim_name}", 0.9, log=True)
            script_text = export_emerge_python_script(
                project_name=self._project_name,
                settings=child_settings,
                step_entries=simulation_step_bundle.get("entries", []),
                units=self._units,
                materials_catalog=self._material_store.material_export_catalog(),
                show_model=show_model,
                preview_only=preview_only,
                show_mesh=show_mesh,
                run_sweep=run_sweep,
                lumped_ports=self._collect_plate_lumped_ports(),
                plate_entries=self._collect_emerge_plates(),
                solver=runtime_solver,
                parallel_enabled=runtime_parallel,
                pardiso_threads=runtime_pardiso_threads,
                acc_threads=runtime_acc_threads,
                mesh_resolution_fraction=mesh_fraction,
                plot_sparams_after_sim=runtime_plot_sparams,
                progressive_sparams_enabled=bool(sim_cfg.get("progressive_sparams_enabled", False)),
                export_sparams_after_sim=runtime_export_sparams,
                simulation_override=sim_cfg,
            )
            clean_filename = f"{Path(filename).stem}_clean.py"
            clean_script_text = build_clean_emerge_python_script(
                script_text,
                str(sim_cfg.get("type", "Sweep")).strip().lower(),
            )
            scripts.append(
                {
                    "name": sim_name,
                    "job_name": sim_name,
                    "type": str(sim_cfg.get("type", "Sweep")).strip().lower(),
                    "index": idx,
                    "completion_name": sim_name,
                    "filename": str(Path(safe_project + "_" + safe_sim) / filename),
                    "content": script_text,
                    "clean_filename": str(Path(safe_project + "_" + safe_sim) / clean_filename),
                    "clean_content": clean_script_text,
                }
            )
            work_completed += 1
            report_progress(f"Generated script: {sim_name}", log=True)
        master_script = self._build_master_simulation_script(
            scripts,
        )
        return {"master": master_script, "scripts": scripts}

    def _update_simulation_script_tabs(self, script_bundle: dict) -> None:
        tabs = getattr(self, "_sim_script_tabs", None)
        if tabs is None:
            return

        while tabs.count() > 0:
            w = tabs.widget(0)
            tabs.removeTab(0)
            if w is not None:
                w.deleteLater()

        master_editor = QPlainTextEdit(tabs)
        master_editor.setReadOnly(True)
        master_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        master_editor.setPlainText(str(script_bundle.get("master", "")))
        tabs.addTab(master_editor, "Master")
        self._sim_script_view = master_editor

        for item in script_bundle.get("scripts", []):
            editor = QPlainTextEdit(tabs)
            editor.setReadOnly(True)
            editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            editor.setPlainText(str(item.get("content", "")))
            job_name = str(item.get("job_name", item.get("name", "Simulation"))).strip() or "Simulation"
            idx = int(item.get("index", 0))
            tab_name = f"{idx:02d} {job_name}" if idx > 0 else job_name
            tabs.addTab(editor, tab_name)

    def _simulation_model_objects(self) -> list:
        return [obj for obj in self._viewport.scene.objects if bool(getattr(obj, "is_model", True))]

    def _set_simulation_preparation_progress(
        self,
        message: str,
        completed: int = 0,
        total: int = 0,
        current_fraction: float | None = None,
        log: bool = False,
    ) -> None:
        if log and message:
            self._append_sim_log(f"[prep] {message}")
        info_bar = getattr(self, "_info_bar", None)
        update_progress = getattr(info_bar, "set_preparation_progress", None)
        if callable(update_progress):
            update_progress(
                message,
                completed=completed,
                total=total,
                current_fraction=current_fraction,
            )
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _process_simulation_preparation_events(self) -> None:
        if getattr(self, "_sim_preparation_active", False):
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _generate_simulation_assets(self, show_progress: bool = True, force_script: bool = False, force_step_export: bool = False) -> str | None:
        need_script = force_script or self._sim_script_dirty or (not self._sim_cached_script_bundle.get("master", ""))
        if need_script:
            findings = self._simulation_validation_findings()
            errors = [finding for finding in findings if finding.severity == "ERROR"]
            for finding in errors:
                self._append_sim_log(f"[error] Preflight {finding.category}: {finding.message}")
            if errors:
                self._append_sim_log("[error] Script generation stopped: resolve preflight errors first.")
                return None

        progress_callback = self._set_simulation_preparation_progress if show_progress else None
        if progress_callback is not None:
            progress_callback("Preparing simulation assets...", log=True)
        sim_objects = self._simulation_model_objects()

        need_step_export = force_step_export or self._sim_steps_dirty or (not self._sim_step_bundle_ready)
        need_full_scene_step_export = bool(
            getattr(self, "_export_full_scene_step", False)
            and (getattr(self, "_sim_full_scene_step_dirty", True) or need_step_export)
        )

        previous_preparation_active = getattr(self, "_sim_preparation_active", False)
        if show_progress:
            self._sim_preparation_active = True
        try:
            if need_step_export:
                self._simulation_step_export_cache = {}
                self._reset_step_export_log()
                self._append_step_export_log(
                    "[info] Geometry export is performed inside each Project_SimulationN folder.",
                    level="INFO",
                )
                bundle = {"entries": [], "skipped": []}
                self._sim_step_bundle_cache = bundle
                self._sim_step_bundle_ready = True
                self._sim_steps_dirty = False
                self._append_sim_log(
                    f"[info] Per-simulation STEP export prepared under {self._simulation_bundle_dir()}"
                )
                self._info_bar.set_info("Per-simulation geometry export prepared.")
            else:
                bundle = self._sim_step_bundle_cache

            if need_full_scene_step_export:
                safe_project_name = "".join(
                    char if char.isalnum() or char in ("-", "_") else "_"
                    for char in self._project_name
                ) or "Project"
                debug_step_path = self._simulation_bundle_dir() / f"{safe_project_name}_Debug_All.step"
                try:
                    if progress_callback is not None:
                        progress_callback("Exporting complete-scene STEP...", log=True)
                    debug_result = export_debug_scene_step(
                        objects=sim_objects,
                        step_path=debug_step_path,
                        mm_per_unit=_MM_PER_UNIT.get(getattr(self, "_units", "mm"), 1.0),
                        log_callback=lambda level, message: self._append_step_export_log(
                            f"[{level.lower()}] {message}", level=self._normalize_log_level(level)
                        ),
                    )
                    self._append_sim_log(
                        f"[info] Complete scene STEP exported: {debug_result['exported']} object(s) to {debug_step_path}"
                    )
                    if progress_callback is not None:
                        progress_callback("Complete-scene STEP export finished.", log=True)
                    self._sim_full_scene_step_dirty = False
                except Exception as exc:
                    warning = f"[warn] Debug STEP export failed: {exc}"
                    self._append_sim_log(warning)
                    self._append_step_export_log(warning, level="WARNING")

            if need_script:
                show_model = bool(getattr(self, "_sim_chk_show_model", None) and self._sim_chk_show_model.isChecked())
                preview_only = bool(getattr(self, "_sim_chk_preview_only", None) and self._sim_chk_preview_only.isChecked())
                show_mesh = bool(getattr(self, "_sim_chk_show_mesh", None) and self._sim_chk_show_mesh.isChecked())
                run_sweep = bool(getattr(self, "_sim_chk_run_sweep", None) and self._sim_chk_run_sweep.isChecked())

                script_bundle = self._build_simulation_script_bundle(
                    bundle.get("entries", []),
                    show_model=show_model,
                    preview_only=preview_only,
                    show_mesh=show_mesh,
                    run_sweep=run_sweep,
                    progress_callback=progress_callback,
                )
                self._sim_cached_script_bundle = script_bundle
                script = str(script_bundle.get("master", ""))
                self._sim_cached_script = script
                self._sim_script_dirty = False
                self._append_sim_log(f"[info] Script bundle generated ({len(script_bundle.get('scripts', []))} simulation scripts + master).")
            else:
                script_bundle = self._sim_cached_script_bundle
                script = str(script_bundle.get("master", ""))

            self._update_simulation_script_tabs(script_bundle)
            return script
        except _SimulationPreparationCancelled:
            self._append_sim_log("[info] Simulation asset preparation cancelled by user.")
            return None
        finally:
            self._sim_preparation_active = previous_preparation_active
            if progress_callback is not None:
                progress_callback("")

    def _on_sim_generate(self, show_progress: bool = True, force_script: bool = True) -> None:
        try:
            self._generate_simulation_assets(show_progress=show_progress, force_script=force_script)
        except Exception as exc:
            self._append_sim_log(f"[error] Failed to generate script: {exc}")

    def _simulation_project_dir(self) -> Path:
        if self._project_path:
            project_path = Path(self._project_path).expanduser().resolve()
            if project_path.exists():
                if project_path.is_file():
                    return project_path.parent
                if project_path.is_dir():
                    return project_path

            if project_path.suffix.lower() == ".em3d":

                # Recover a stale relative/launcher path by resolving the
                # project file by name inside the workspace.
                workspace_root = Path(__file__).resolve().parents[3]
                matches = list(workspace_root.rglob(project_path.name))
                for match in matches:
                    if match.is_file() and match.suffix.lower() == ".em3d":
                        self._project_path = str(match.resolve())
                        return match.resolve().parent

        # main_window.py lives in <workspace>/src/em3d_modeler/ui.  Resolve
        # first so the result never depends on the launcher's current folder.
        return Path(__file__).resolve().parents[3]

    def _simulation_bundle_dir(self) -> Path:
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self._project_name)
        safe_name = safe_name or "Project"
        out_dir = self._simulation_project_dir() / f"{safe_name}_EmergeSim"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _step_export_log_path(self) -> Path:
        return self._simulation_bundle_dir() / "StepExport.log"

    def _reset_step_export_log(self) -> Path:
        log_path = self._step_export_log_path()
        log_path.write_text("", encoding="utf-8")
        return log_path

    def _append_step_export_log(self, message: str, level: str = "INFO") -> None:
        if not message:
            return
        if not self._step_log_enabled(level):
            return
        try:
            with self._step_export_log_path().open("a", encoding="utf-8") as handle:
                handle.write(message.rstrip("\n") + "\n")
        except Exception:
            pass

    def _simulation_script_path(self) -> Path:
        out_dir = self._simulation_bundle_dir()
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self._project_name)
        safe_name = safe_name or "project"
        return out_dir / f"{safe_name}_master_emerge_run.py"

    def _write_cached_simulation_scripts(self) -> Path:
        bundle = self._sim_cached_script_bundle
        script_path = self._simulation_script_path()
        script_path.write_text(str(bundle.get("master", "")), encoding="utf-8")
        for item in bundle.get("scripts", []):
            child_name = str(item.get("filename", "simulation_emerge_run.py"))
            child_path = script_path.parent / child_name
            child_path.parent.mkdir(parents=True, exist_ok=True)
            child_path.write_text(str(item.get("content", "")), encoding="utf-8")
            clean_name = str(item.get("clean_filename", ""))
            clean_content = str(item.get("clean_content", ""))
            if clean_name and clean_content:
                clean_path = script_path.parent / clean_name
                clean_path.parent.mkdir(parents=True, exist_ok=True)
                clean_path.write_text(clean_content, encoding="utf-8")
        return script_path

    def _on_sim_save_script(self) -> None:
        script_bundle = self._sim_cached_script_bundle
        if getattr(self, "_sim_script_dirty", False) or not str(script_bundle.get("master", "")).strip():
            try:
                master_script = self._generate_simulation_assets()
                if not master_script:
                    return
                script_bundle = self._sim_cached_script_bundle
            except Exception as exc:
                self._append_sim_log(f"[error] Failed to generate script: {exc}")
                return

        default_path = str(self._simulation_script_path())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save EMERGE Python Script",
            default_path,
            "Python (*.py);;All Files (*)",
        )
        if not path:
            return
        master_path = Path(path)
        target_dir = master_path.parent
        master_path.write_text(str(script_bundle.get("master", "")), encoding="utf-8")
        self._append_sim_log(f"[info] Master script saved: {master_path}")
        for item in script_bundle.get("scripts", []):
            child_name = str(item.get("filename", "simulation_emerge_run.py"))
            child_path = target_dir / child_name
            child_path.parent.mkdir(parents=True, exist_ok=True)
            child_path.write_text(str(item.get("content", "")), encoding="utf-8")
            self._append_sim_log(f"[info] Child script saved: {child_path}")
            clean_name = str(item.get("clean_filename", ""))
            clean_content = str(item.get("clean_content", ""))
            if clean_name and clean_content:
                clean_path = target_dir / clean_name
                clean_path.parent.mkdir(parents=True, exist_ok=True)
                clean_path.write_text(clean_content, encoding="utf-8")
                self._append_sim_log(f"[info] Clean EMERGE script saved: {clean_path}")

    def _close_simulation_job(self) -> None:
        job_handle = getattr(self, "_sim_job_handle", None)
        if not job_handle:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(job_handle)
        except (AttributeError, OSError):
            pass
        self._sim_job_handle = None

    def _attach_simulation_job(self, process: QProcess) -> None:
        """Place the simulation master and every descendant in one Windows job."""
        if os.name != "nt" or process is not getattr(self, "_sim_process", None):
            return
        process_id = int(process.processId())
        if process_id <= 0:
            return

        self._close_simulation_job()
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.OpenProcess.restype = ctypes.c_void_p
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            self._append_sim_log("[warn] Could not create the simulation process job.")
            return

        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            ctypes.c_void_p(job_handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        process_handle = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA,
            False,
            process_id,
        )
        assigned = bool(process_handle) and bool(
            kernel32.AssignProcessToJobObject(ctypes.c_void_p(job_handle), ctypes.c_void_p(process_handle))
        )
        if process_handle:
            kernel32.CloseHandle(ctypes.c_void_p(process_handle))
        if configured and assigned:
            self._sim_job_handle = job_handle
            self._append_sim_log("[info] Simulation workers are tracked by a Windows process job.")
            return

        kernel32.CloseHandle(ctypes.c_void_p(job_handle))
        self._append_sim_log("[warn] Could not assign simulation to a Windows process job; using PID fallback.")

    def _terminate_simulation_job(self) -> bool:
        job_handle = getattr(self, "_sim_job_handle", None)
        if not job_handle:
            return False
        try:
            terminated = bool(ctypes.windll.kernel32.TerminateJobObject(ctypes.c_void_p(job_handle), 130))
        except (AttributeError, OSError):
            terminated = False
        self._close_simulation_job()
        return terminated

    def _on_sim_run(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is not None and proc.state() != QProcess.NotRunning:
            self._append_sim_log("[warn] A simulation process is already running.")
            return

        try:
            if not self._save_project():
                self._append_sim_log("[warn] Simulation not started because the project was not saved.")
                return
        except Exception as exc:
            self._append_sim_log(f"[error] Simulation not started because project save failed: {exc}")
            return

        try:
            master_script = self._generate_simulation_assets(force_script=False)
        except Exception as exc:
            self._append_sim_log(f"[error] Failed to prepare simulation assets: {exc}")
            return
        if not master_script:
            return

        script_path = self._write_cached_simulation_scripts()
        cancel_file = script_path.parent / ".em3d_simulation_cancel"
        active_pid_file = script_path.parent / ".em3d_simulation_active.pid"
        for control_file in (cancel_file, active_pid_file):
            try:
                control_file.unlink(missing_ok=True)
            except OSError as exc:
                self._append_sim_log(f"[warn] Could not reset simulation control file {control_file.name}: {exc}")
        self._append_sim_log(f"[info] Running master script: {script_path}")

        self._sim_status_total_jobs = max(
            1,
            len(getattr(self, "_sim_cached_script_bundle", {}).get("scripts", [])),
        )
        self._sim_status_completed_jobs = 0
        self._sim_status_current_fraction = None
        self._sim_status_running = True
        MainWindow._refresh_simulation_status(self)

        self._reset_progressive_sparams_plot()
        self._sim_process = QProcess(self)
        self._sim_process.setProgram(sys.executable)
        if getattr(sys, "frozen", False):
            self._sim_process.setArguments(["--em3d-run-script", str(script_path)])
        else:
            self._sim_process.setArguments(["-u", str(script_path)])
        self._sim_process.setWorkingDirectory(str(script_path.parent))
        self._sim_process.setProcessChannelMode(QProcess.MergedChannels)
        self._sim_process.readyReadStandardOutput.connect(self._on_sim_process_output)
        self._sim_process.finished.connect(self._on_sim_finished)
        error_signal = getattr(self._sim_process, "errorOccurred", None)
        if error_signal is not None:
            error_signal.connect(self._on_sim_process_error)
        self._sim_process.started.connect(lambda process=self._sim_process: self._attach_simulation_job(process))
        self._sim_process.start()

        self._sim_btn_run.setEnabled(False)
        self._sim_btn_stop.setEnabled(True)

    def _on_sim_stop(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is None or proc.state() == QProcess.NotRunning:
            return

        process_id = int(proc.processId())
        script_path = self._simulation_script_path()
        cancel_file = script_path.parent / ".em3d_simulation_cancel"
        active_pid_file = script_path.parent / ".em3d_simulation_active.pid"
        try:
            cancel_file.write_text("cancelled\n", encoding="ascii")
        except OSError as exc:
            self._append_sim_log(f"[warn] Could not signal simulation cancellation: {exc}")

        process_ids = {process_id} if process_id > 0 else set()
        try:
            active_pid = int(active_pid_file.read_text(encoding="ascii").strip())
            if active_pid > 0:
                process_ids.add(active_pid)
        except (OSError, ValueError):
            pass

        stopped_tree = False
        if self._terminate_simulation_job():
            stopped_tree = True
        if not stopped_tree and os.name == "nt" and process_ids:
            for target_pid in process_ids:
                try:
                    result = subprocess.run(
                        ["taskkill", "/PID", str(target_pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    stopped_tree = result.returncode == 0 or stopped_tree
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout).strip()
                        self._append_sim_log(f"[warn] Could not stop simulation PID {target_pid}: {detail}")
                except OSError as exc:
                    self._append_sim_log(f"[warn] Process-tree stop failed for PID {target_pid}: {exc}")

        if not stopped_tree and proc.state() != QProcess.NotRunning:
            proc.kill()

        self._append_sim_log("[info] Cancellation requested; active and queued simulation jobs were stopped.")

    def _on_sim_process_output(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is None:
            return
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not data:
            return
        lines = (getattr(self, "_sim_stdout_buffer", "") + data).split("\n")
        self._sim_stdout_buffer = lines.pop()
        for line in lines:
            self._process_sim_stdout_line(line.rstrip("\r"))

    def _process_sim_stdout_line(self, line: str) -> None:
        prefix = "EM3D_SPARAM_PROGRESS:"
        completed_prefix = "EM3D_JOB_COMPLETED:"
        if line.startswith(completed_prefix):
            try:
                job = json.loads(line[len(completed_prefix):])
                simulation_name = str(job.get("name", "")).strip()
                if simulation_name:
                    self._plot_completed_simulation_outputs(simulation_name)
                self._sim_status_completed_jobs = min(
                    int(getattr(self, "_sim_status_total_jobs", 0)),
                    int(getattr(self, "_sim_status_completed_jobs", 0)) + 1,
                )
                self._sim_status_current_fraction = None
                MainWindow._refresh_simulation_status(self)
            except (TypeError, ValueError, KeyError) as exc:
                self._append_sim_log(f"[warn] Invalid completed-job event: {exc}")
            return
        if not line.startswith(prefix):
            self._append_sim_log(line)
            return
        try:
            payload = json.loads(line[len(prefix):])
            self._update_progressive_sparams_plot(payload)
        except (TypeError, ValueError, KeyError) as exc:
            self._append_sim_log(f"[warn] Invalid progressive S-parameter update: {exc}")

    def _ensure_progressive_sparams_plot(self) -> bool:
        if getattr(self, "_sim_live_plot_canvas", None) is not None:
            return True
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError as exc:
            self._sim_live_plot_status.setText(f"Matplotlib plot unavailable: {exc}")
            return False
        self._sim_live_plot_figure = Figure(figsize=(7, 4))
        self._sim_live_plot_axes = self._sim_live_plot_figure.add_subplot(111)
        self._sim_live_plot_canvas = FigureCanvasQTAgg(self._sim_live_plot_figure)
        self._sim_live_plot_layout.addWidget(self._sim_live_plot_canvas)
        return True

    def _reset_progressive_sparams_plot(self) -> None:
        self._sim_progressive_plot_data = None
        self._sim_stdout_buffer = ""
        settings = self._project_tree.get_settings()
        simulations = settings.get("simulations", [])
        outputs = settings.get("outputs", [])
        from .chart_data import supports_live_plot

        progressive_simulations = {
            str(simulation.get("name", "")).strip(): simulation
            for simulation in simulations
            if isinstance(simulation, dict)
            and bool(simulation.get("progressive_sparams_enabled", False))
        }
        for output in outputs:
            if not isinstance(output, dict):
                continue
            simulation_name = str(output.get("simulation", "")).strip()
            simulation = progressive_simulations.get(simulation_name)
            if simulation is None or not bool(output.get("enabled", True)):
                continue
            live_output = {**output, "plot_mode": "live"}
            if not supports_live_plot(live_output, simulation):
                continue
            key = f"{simulation_name}::{output.get('name', 'Output')}"
            view = self._plot_views.get(key)
            if view is not None:
                view.clear_data()

    def _update_progressive_sparams_plot(self, payload: dict) -> None:
        if "parameter_index" in payload:
            self._update_progressive_parametric_sparams_plot(payload)
            return

        simulation = str(payload.get("simulation", "Simulation"))
        frequencies = payload.get("frequencies")
        matrices = payload.get("s_matrices")
        expected = payload.get("expected_samples")
        completed = payload.get("completed_samples")
        if not isinstance(frequencies, list) or not frequencies or not isinstance(matrices, list) or len(frequencies) != len(matrices):
            raise ValueError("frequency and S-matrix chunk sizes do not match")
        if not isinstance(expected, int) or not isinstance(completed, int) or expected < 1 or completed > expected:
            raise ValueError("sample progress metadata is invalid")

        previous = getattr(self, "_sim_progressive_plot_data", None)
        if previous is None or previous["simulation"] != simulation:
            previous = {"simulation": simulation, "expected": expected, "frequencies": [], "curves": {}, "matrices": []}
        if previous["expected"] != expected or completed != len(previous["frequencies"]) + len(frequencies):
            raise ValueError("sample progress is not contiguous")

        parsed_frequencies = [float(value) for value in frequencies]
        if any(not math.isfinite(value) for value in parsed_frequencies):
            raise ValueError("chunk contains a non-finite frequency")
        if any(right <= left for left, right in zip(parsed_frequencies, parsed_frequencies[1:])):
            raise ValueError("chunk frequencies are not strictly increasing")
        if previous["frequencies"] and parsed_frequencies and parsed_frequencies[0] <= previous["frequencies"][-1]:
            raise ValueError("chunk frequencies are not strictly increasing")
        if bool(payload.get("complete")) != (completed == expected):
            raise ValueError("completion state does not match sample progress")

        parsed_matrices = []
        port_count = None
        for raw_matrix in matrices:
            if not isinstance(raw_matrix, list) or not raw_matrix:
                raise ValueError("chunk contains an empty S-matrix")
            matrix = []
            for row in raw_matrix:
                if not isinstance(row, list) or len(row) != len(raw_matrix):
                    raise ValueError("chunk contains a non-square S-matrix")
                parsed_row = []
                for pair in row:
                    if not isinstance(pair, list) or len(pair) != 2:
                        raise ValueError("S-matrix entry must contain real and imaginary values")
                    real, imaginary = float(pair[0]), float(pair[1])
                    if not math.isfinite(real) or not math.isfinite(imaginary):
                        raise ValueError("chunk contains non-finite S-parameter values")
                    parsed_row.append(complex(real, imaginary))
                matrix.append(parsed_row)
            if port_count is None:
                port_count = len(matrix)
            if len(matrix) != port_count:
                raise ValueError("S-matrix port count changed within the chunk")
            parsed_matrices.append(matrix)

        if previous["curves"] and port_count != int(math.sqrt(len(previous["curves"]))):
            raise ValueError("S-matrix port count changed between chunks")
        if not previous["curves"] and port_count is not None:
            previous["curves"] = {
                f"S{output_port}{input_port}": []
                for output_port in range(1, port_count + 1)
                for input_port in range(1, port_count + 1)
            }
        previous["frequencies"].extend(parsed_frequencies)
        for matrix in parsed_matrices:
            previous["matrices"].append(matrix)
            for output_port, row in enumerate(matrix, start=1):
                for input_port, value in enumerate(row, start=1):
                    previous["curves"][f"S{output_port}{input_port}"].append(value)

        self._sim_progressive_plot_data = previous
        self._sim_status_current_fraction = completed / expected
        MainWindow._refresh_simulation_status(self)
        from .chart_data import make_sparameter_plot_data, supports_live_plot

        settings = self._project_tree.get_settings()
        simulations = settings.get("simulations", [])
        simulation_config = next(
            (
                item for item in simulations
                if isinstance(item, dict) and str(item.get("name", "")).strip() == simulation
            ),
            {},
        )
        for output in settings.get("outputs", []):
            if not isinstance(output, dict) or str(output.get("simulation", "")).strip() != simulation:
                continue
            if not bool(output.get("enabled", True)):
                continue
            if not supports_live_plot({**output, "plot_mode": "live"}, simulation_config):
                continue
            chart_data = make_sparameter_plot_data(
                output,
                previous["frequencies"],
                previous["matrices"],
                include_all_parameters=True,
                file_name=str(output.get("name", "Results")),
                file_id=f"{simulation}::{output.get('name', 'Output')}",
            )
            chart_data["x_range"] = (
                float(simulation_config.get("Fmin_GHz", 0.1)),
                float(simulation_config.get("Fmax_GHz", 10.0)),
            )
            chart_data["title"] = f"{chart_data['title']} ({completed}/{expected} samples)"
            key = f"{simulation}::{output.get('name', 'Output')}"
            self._show_chart(key, chart_data, progressive=True)

    def _update_progressive_parametric_sparams_plot(self, payload: dict) -> None:
        simulation = str(payload.get("simulation", "Simulation"))
        parameter_name = str(payload.get("parameter_name", "parameter")).strip() or "parameter"
        parameter_value = str(payload.get("parameter_value", "")).strip()
        parameter_index = payload.get("parameter_index")
        completed = payload.get("completed_parameters")
        expected = payload.get("expected_parameters")
        frequencies = payload.get("frequencies")
        matrices = payload.get("s_matrices")
        sample_start = payload.get("chunk_start")
        completed_samples = payload.get("completed_samples")
        expected_samples = payload.get("expected_samples")
        if (
            not parameter_value
            or not isinstance(parameter_index, int)
            or not isinstance(completed, int)
            or not isinstance(expected, int)
            or expected < 1
            or completed != parameter_index
            or not 1 <= parameter_index <= expected
            or not isinstance(frequencies, list)
            or not frequencies
            or not isinstance(matrices, list)
            or len(frequencies) != len(matrices)
            or not isinstance(sample_start, int)
            or not isinstance(completed_samples, int)
            or not isinstance(expected_samples, int)
            or expected_samples < 1
            or sample_start < 0
            or completed_samples != sample_start + len(frequencies)
            or completed_samples > expected_samples
            or bool(payload.get("complete")) != (completed_samples == expected_samples)
        ):
            raise ValueError("parametric S-parameter progress metadata is invalid")

        previous = getattr(self, "_sim_progressive_plot_data", None)
        if previous is None or previous.get("simulation") != simulation or "parameter_runs" not in previous:
            previous = {
                "simulation": simulation,
                "expected_parameters": expected,
                "expected_samples": expected_samples,
                "parameter_runs": {},
            }
        if (
            previous["expected_parameters"] != expected
            or previous["expected_samples"] != expected_samples
        ):
            raise ValueError("parametric progress count changed during the run")

        parsed_frequencies = [float(value) for value in frequencies]
        if any(not math.isfinite(value) for value in parsed_frequencies):
            raise ValueError("parametric sweep contains a non-finite frequency")
        if any(right <= left for left, right in zip(parsed_frequencies, parsed_frequencies[1:])):
            raise ValueError("parametric sweep frequencies are not strictly increasing")

        from .chart_data import make_sparameter_plot_data, supports_live_plot

        settings = self._project_tree.get_settings()
        simulations = settings.get("simulations", [])
        simulation_config = next(
            (
                item for item in simulations
                if isinstance(item, dict) and str(item.get("name", "")).strip() == simulation
            ),
            {},
        )
        if str(simulation_config.get("type", "")).strip().lower() != "parametric":
            raise ValueError("parametric progress event targets a non-parametric simulation")
        run = previous["parameter_runs"].get(parameter_index)
        if run is None:
            if parameter_index != len(previous["parameter_runs"]) + 1 or sample_start != 0:
                raise ValueError("parametric parameter values or frequency chunks are out of order")
            if previous["parameter_runs"]:
                prior_run = previous["parameter_runs"][parameter_index - 1]
                if len(prior_run["frequencies"]) != expected_samples:
                    raise ValueError("next parameter value started before the prior sweep completed")
            run = {"value": parameter_value, "frequencies": [], "matrices": []}
            previous["parameter_runs"][parameter_index] = run
        if run["value"] != parameter_value or len(run["frequencies"]) != sample_start:
            raise ValueError("parametric frequency chunks are not contiguous")
        if run["frequencies"] and parsed_frequencies[0] <= run["frequencies"][-1]:
            raise ValueError("parametric sweep frequencies are not strictly increasing")
        run["frequencies"].extend(parsed_frequencies)
        run["matrices"].extend(matrices)
        if len(run["frequencies"]) != completed_samples:
            raise ValueError("parametric frequency progress does not match accumulated data")
        self._sim_progressive_plot_data = previous
        self._sim_status_current_fraction = (
            (completed - 1) + completed_samples / expected_samples
        ) / expected
        MainWindow._refresh_simulation_status(self)

        for output in settings.get("outputs", []):
            if not isinstance(output, dict) or str(output.get("simulation", "")).strip() != simulation:
                continue
            if not bool(output.get("enabled", True)):
                continue
            if not supports_live_plot({**output, "plot_mode": "live"}, simulation_config):
                continue

            all_series = []
            chart_data = None
            for index, run in sorted(previous["parameter_runs"].items()):
                file_name = f"{parameter_name}={run['value']}"
                run_data = make_sparameter_plot_data(
                    output,
                    run["frequencies"],
                    run["matrices"],
                    include_all_parameters=True,
                    file_name=file_name,
                    file_id=f"{simulation}::{output.get('name', 'Output')}::{index}",
                )
                if chart_data is None:
                    chart_data = run_data
                all_series.extend(
                    {**series, "x_values": run_data["x_values"]}
                    for series in run_data["series"]
                )
            if chart_data is None:
                continue
            chart_data["series"] = all_series
            chart_data["x_range"] = (
                float(simulation_config.get("Fmin_GHz", 0.1)),
                float(simulation_config.get("Fmax_GHz", 10.0)),
            )
            chart_data["title"] = (
                f"{output.get('name', 'S-parameter Plot')} "
                f"({parameter_name}={parameter_value}, "
                f"{completed_samples}/{expected_samples} samples; "
                f"value {parameter_index}/{expected})"
            )
            key = f"{simulation}::{output.get('name', 'Output')}"
            self._show_chart(key, chart_data, progressive=True)

    def _on_sim_finished(self, exit_code: int, _status) -> None:
        self._on_sim_process_output()
        pending = getattr(self, "_sim_stdout_buffer", "")
        self._sim_stdout_buffer = ""
        if pending:
            self._process_sim_stdout_line(pending.rstrip("\r"))
        self._close_simulation_job()
        self._sim_status_running = False
        self._sim_status_current_fraction = None
        MainWindow._refresh_simulation_status(self)
        self._append_sim_log(f"[info] Simulation finished with exit code {exit_code}.")
        if hasattr(self, "_sim_btn_run"):
            self._sim_btn_run.setEnabled(True)
        if hasattr(self, "_sim_btn_stop"):
            self._sim_btn_stop.setEnabled(False)

    def _on_sim_process_error(self, _error) -> None:
        process = getattr(self, "_sim_process", None)
        if process is None or process.state() != QProcess.NotRunning:
            return
        self._sim_status_running = False
        self._sim_status_current_fraction = None
        MainWindow._refresh_simulation_status(self)
        self._append_sim_log(f"[error] Simulation process error: {process.errorString()}")
        if hasattr(self, "_sim_btn_run"):
            self._sim_btn_run.setEnabled(True)
        if hasattr(self, "_sim_btn_stop"):
            self._sim_btn_stop.setEnabled(False)

    def _append_sim_log(self, message: str) -> None:
        if not hasattr(self, "_sim_log_view"):
            return
        if message:
            self._sim_log_view.appendPlainText(message)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� sketch
    @staticmethod
    def _planar_face_sketch_plane(face_pick):
        if not isinstance(face_pick, dict):
            return None
        try:
            points = [tuple(float(value) for value in point) for point in face_pick["points"]]
        except (KeyError, TypeError, ValueError):
            return None
        if len(points) < 3 or any(len(point) != 3 for point in points):
            return None

        import math

        if any(not math.isfinite(value) for point in points for value in point):
            return None
        spans = [max(point[axis] for point in points) - min(point[axis] for point in points) for axis in range(3)]
        scale = max(spans)
        if scale <= 0.0:
            return None

        first = points[0]
        normal = None
        minimum_cross = max(scale * scale * 1e-10, 1e-30)
        for first_index in range(1, len(points) - 1):
            first_edge = tuple(points[first_index][axis] - first[axis] for axis in range(3))
            for second_index in range(first_index + 1, len(points)):
                second_edge = tuple(points[second_index][axis] - first[axis] for axis in range(3))
                cross = (
                    first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
                    first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
                    first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0],
                )
                length = math.sqrt(sum(value * value for value in cross))
                if length > minimum_cross:
                    normal = tuple(value / length for value in cross)
                    break
            if normal is not None:
                break
        if normal is None:
            return None

        tolerance = max(scale * 1e-6, 1e-12)
        if any(
            abs(sum((point[axis] - first[axis]) * normal[axis] for axis in range(3))) > tolerance
            for point in points
        ):
            return None
        origin = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))
        return origin, normal

    def _open_sketch(self) -> None:
        vp = self._viewport
        face_plane = None
        if (
            getattr(vp, "_selection_mode", None) == "face"
            and getattr(vp, "_sub_pick_actor", None) is not None
        ):
            face_plane = self._planar_face_sketch_plane(getattr(vp, "_last_face_pick", None))
        if face_plane is not None:
            self._start_embedded_sketch(*face_plane)
            return

        active_plane = getattr(vp.scene, "active_plane", None)
        if active_plane is not None:
            self._start_embedded_sketch(active_plane.origin, active_plane.normal)
            return

        self._open_reference_plane_dialog(start_sketch=True)

    def _start_embedded_sketch(self, origin, normal, sketch_definition=None) -> None:
        if sketch_definition is None:
            self._viewport.start_sketch(tuple(origin), tuple(normal))
        else:
            self._viewport.start_sketch(tuple(origin), tuple(normal), sketch_definition)
        self._viewport._sketch_toolbar.hide()
        self._main_toolbar.setVisible(False)
        self._workspace_toolbar.setVisible(False)
        self._sketch_context_toolbar.setVisible(True)

    def _edit_sketch_definition(self, obj) -> None:
        definition = getattr(obj, "sketch_definition", None)
        if not isinstance(definition, dict):
            self._info_bar.set_info("This object has no editable sketch definition.")
            return
        try:
            origin = tuple(float(value) for value in definition["plane_origin"])
            normal = tuple(float(value) for value in definition["plane_normal"])
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Edit Sketch", f"Invalid sketch plane: {exc}")
            return
        scene_objects = list(getattr(self._viewport.scene, "objects", []))
        self._sketch_edit_visibility = [
            (scene_obj, scene_obj.is_visible())
            for scene_obj in scene_objects
            if callable(getattr(scene_obj, "is_visible", None))
            and callable(getattr(scene_obj, "set_visible", None))
        ]
        self._sketch_edit_target = obj
        for scene_obj, _was_visible in self._sketch_edit_visibility:
            scene_obj.set_visible(False)
        try:
            self._start_embedded_sketch(origin, normal, definition)
        except Exception:
            for scene_obj, was_visible in self._sketch_edit_visibility:
                scene_obj.set_visible(was_visible)
            self._sketch_edit_visibility = []
            self._sketch_edit_target = None
            raise
        self._info_bar.set_info(f"Editing sketch for {obj.name}")

    def _on_viewport_sketch_finished(self) -> None:
        self._sketch_context_toolbar.setVisible(False)
        self._main_toolbar.setVisible(True)
        self._workspace_toolbar.setVisible(True)
        for obj, was_visible in getattr(self, "_sketch_edit_visibility", []):
            obj.set_visible(was_visible)
        self._sketch_edit_visibility = []
        self._sketch_edit_target = None

    def _activate_sketch_toolbar_action(self, callback) -> None:
        callback()
        self._viewport.setFocus()

    def _edit_sketch_feature(self, obj) -> None:
        import ast
        from ..scene.em_objects import ExtrudedObject, RevolvedObject

        if not isinstance(obj, (ExtrudedObject, RevolvedObject)):
            return
        if isinstance(obj, ExtrudedObject):
            self._edit_extrusion_depth(obj)
            return
        params = obj.get_parameters()
        try:
            profile = ast.literal_eval(params.get("ProfilePts", ""))
        except (SyntaxError, ValueError) as exc:
            QMessageBox.warning(self, "Edit Feature", f"Unable to read the sketch profile: {exc}")
            return

        plane_origin = tuple(
            float(params[f"PlaneOrigin{axis}"]) for axis in "XYZ"
        )
        plane_normal = tuple(
            float(params[f"PlaneNormal{axis}"]) for axis in "XYZ"
        )
        is_extrude = isinstance(obj, ExtrudedObject)
        axis_pt1 = tuple(float(params[f"AxisPt1{axis}"]) for axis in "XYZ") if not is_extrude else None
        axis_pt2 = tuple(float(params[f"AxisPt2{axis}"]) for axis in "XYZ") if not is_extrude else None
        dlg = SketchDialog(
            self,
            plane_origin=plane_origin,
            plane_normal=plane_normal,
            profile_pts=profile,
            operation_mode="extrude" if is_extrude else "revolve",
            extrusion_depth=float(params.get("Depth", 10.0)) if is_extrude else None,
            revolve_angle=float(params.get("Angle", 360.0)) if not is_extrude else None,
            revolve_axis_pt1=axis_pt1,
            revolve_axis_pt2=axis_pt2,
        )

        def update_extrusion(profile_pts, depth, origin, normal) -> bool:
            try:
                obj.set_parameters({
                    "ProfilePts": profile_pts,
                    "Depth": depth,
                    **{f"PlaneOrigin{axis}": origin[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneNormal{axis}": normal[index] for index, axis in enumerate("XYZ")},
                })
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(dlg, "Edit Extrusion", str(exc))
                return False
            self._finish_sketch_feature_edit(obj)
            return True

        def update_revolution(profile_pts, angle, start, end, origin, normal) -> bool:
            try:
                obj.set_parameters({
                    "ProfilePts": profile_pts,
                    "Angle": angle,
                    **{f"AxisPt1{axis}": start[index] for index, axis in enumerate("XYZ")},
                    **{f"AxisPt2{axis}": end[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneOrigin{axis}": origin[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneNormal{axis}": normal[index] for index, axis in enumerate("XYZ")},
                })
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(dlg, "Edit Revolution", str(exc))
                return False
            self._finish_sketch_feature_edit(obj)
            return True

        dlg.set_edit_apply_callback(update_extrusion if is_extrude else update_revolution)
        dlg.show()

    def _edit_extrusion_depth(self, obj) -> None:
        params = obj.get_parameters()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Extrude Height")
        form = QFormLayout(dlg)
        depth_input = QDoubleSpinBox(dlg, resolver=self._resolve_formula_text)
        depth_input.setRange(0.000001, 1e9)
        depth_input.setDecimals(6)
        depth_input.setValue(float(params.get("Depth", 10.0)))
        direction_input = QComboBox(dlg)
        direction_input.addItem("Along sketch normal", "Normal")
        direction_input.addItem("Against sketch normal", "Reverse")
        direction_index = direction_input.findData(params.get("Direction", "Normal"))
        direction_input.setCurrentIndex(max(direction_index, 0))
        symmetric_input = QCheckBox("Both directions (total depth, centered)", dlg)
        symmetric_input.setChecked(bool(params.get("Symmetric", False)))
        direction_input.setEnabled(not symmetric_input.isChecked())
        symmetric_input.toggled.connect(lambda checked: direction_input.setEnabled(not checked))
        unit_label = f"Extrusion height ({self._units})" if self._units else "Extrusion height"
        form.addRow(f"{unit_label} (total when centered)", depth_input)
        form.addRow("Direction:", direction_input)
        form.addRow("", symmetric_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        form.addRow(buttons)
        saved_formula = str((getattr(obj, "param_formulas", {}) or {}).get("Depth", "")).strip()
        if saved_formula:
            depth_input.set_formula(saved_formula)

        def apply_depth() -> None:
            raw_value = depth_input.formula_text().strip()
            try:
                depth = float(depth_input.value())
                obj.set_parameters({
                    "Depth": depth,
                    "Direction": direction_input.currentData(),
                    "Symmetric": symmetric_input.isChecked(),
                })
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(dlg, "Invalid Extrusion Height", str(exc))
                return
            formulas = dict(getattr(obj, "param_formulas", {}) or {})
            try:
                float(raw_value.replace(",", "."))
            except ValueError:
                formulas["Depth"] = raw_value
            else:
                formulas.pop("Depth", None)
            obj.param_formulas = formulas
            self._finish_sketch_feature_edit(obj)
            dlg.accept()

        buttons.accepted.connect(apply_depth)
        buttons.rejected.connect(dlg.reject)
        self._extrusion_depth_dialog = dlg
        dlg.show()

    def _finish_sketch_feature_edit(self, obj) -> None:
        self._viewport.scene.select(obj)
        self._viewport.scene_changed.emit()
        self._viewport.object_selected.emit(obj)
        self._viewport.selection_changed.emit([obj])
        self._viewport._render()
        self._refresh_materials()
        self._mark_simulation_dirty(steps=True, script=True)
        self._info_bar.set_info(f"Updated feature operation: {obj.name}")

    def _on_extrude_requested(
        self, profile, depth, origin, normal, direction="Normal", symmetric=False
    ) -> None:
        from ..scene.em_objects import ExtrudedObject
        target = getattr(self, "_sketch_edit_target", None)
        sketch_definition = self._viewport.sketch_definition()
        if isinstance(target, ExtrudedObject):
            try:
                target.set_parameters({
                    "ProfilePts": profile,
                    "Depth": depth,
                    "Direction": direction,
                    "Symmetric": symmetric,
                    **{f"PlaneOrigin{axis}": origin[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneNormal{axis}": normal[index] for index, axis in enumerate("XYZ")},
                })
            except (TypeError, ValueError) as exc:
                self._info_bar.set_info(f"Unable to update extrusion: {exc}")
                return
            target.sketch_definition = sketch_definition
            self._finish_sketch_feature_edit(target)
            return
        obj = ExtrudedObject(
            profile_pts  = profile,
            depth        = depth,
            plane_origin = origin,
            plane_normal = normal,
            material     = self._draw_material,
            direction    = direction,
            symmetric    = symmetric,
        )
        obj.sketch_definition = sketch_definition
        self._viewport.scene.add_object(obj)
        self._viewport.scene.select(obj)
        self._viewport.object_selected.emit(obj)
        self._viewport.selection_changed.emit([obj])
        self._viewport.scene_changed.emit()
        self._viewport._render()
        self._refresh_materials()
        self._info_bar.set_info(f"Extruded body created: {obj.name}")

    def _on_extruded_cut_requested(self, profile, depth, origin, normal) -> None:
        from ..scene.boolean_ops import boolean_many
        from ..scene.em_objects import ExtrudedObject, MeshObject

        target = getattr(self, "_sketch_edit_target", None)
        sketch_definition = self._viewport.sketch_definition()
        if isinstance(target, MeshObject) and target.boolean_op == "cut":
            sources = list(getattr(target, "source_objects", []) or [])
            snapshots = list(getattr(target, "boolean_sources_data", []) or [])
            base = sources[0] if sources else None
            if base is None and snapshots:
                base = self._rebuild_object_from_snapshot(snapshots[0])
            if base is None:
                self._info_bar.set_info(
                    f"Unable to edit {target.name}: its original cut source is unavailable."
                )
                return
            tool_name = (
                str(sources[1].name) if len(sources) > 1
                else str(snapshots[1].get("name", f"SketchCut_{base.name}"))
                if len(snapshots) > 1
                else f"SketchCut_{base.name}"
            )
            try:
                tool = ExtrudedObject(
                    name=tool_name,
                    profile_pts=profile,
                    depth=float(depth),
                    plane_origin=origin,
                    plane_normal=normal,
                    material=base.material,
                )
                tool.sketch_definition = sketch_definition
                result_poly = boolean_many("cut", [base, tool])
            except Exception as exc:
                QMessageBox.critical(self, "Extruded Cut failed", str(exc))
                self._info_bar.set_info(f"Extruded Cut failed: {exc}")
                return
            target.sketch_definition = sketch_definition
            self._replace_boolean_result_object(target, result_poly, [base, tool])
            self._finish_sketch_feature_edit(target)
            return

        scene = self._viewport.scene
        selection = list(scene.selection)
        if len(selection) != 1:
            self._info_bar.set_info("Extruded Cut requires exactly one selected base object.")
            return
        base = selection[0]
        try:
            tool = ExtrudedObject(
                name=f"SketchCut_{base.name}",
                profile_pts=profile,
                depth=float(depth),
                plane_origin=origin,
                plane_normal=normal,
                material=base.material,
            )
            tool.sketch_definition = sketch_definition
            result_poly = boolean_many("cut", [base, tool])
        except Exception as exc:
            QMessageBox.critical(self, "Extruded Cut failed", str(exc))
            self._info_bar.set_info(f"Extruded Cut failed: {exc}")
            return

        result = MeshObject(
            name=f"ExtrudedCut_{base.name}",
            polydata=result_poly,
            material=base.material,
            plate_role=self._is_plate_role_object(base),
            boolean_op="cut",
            boolean_source_names=[base.name, tool.name],
        )
        result.source_objects = [base, tool]
        result.boolean_mesh_reduction = 0.0
        result.boolean_sources_data = [
            self._serialize_object_snapshot(source) for source in (base, tool)
        ]
        result.sketch_definition = sketch_definition
        result.refresh_appearance()
        scene.add_object(result)
        scene.remove_object(base)
        scene.select(result)
        self._viewport.object_selected.emit(result)
        self._viewport.selection_changed.emit([result])
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()
        self._info_bar.set_info(f"Extruded Cut created: {result.name}")

    def _on_revolve_requested(self, profile, angle, axis_pt1, axis_pt2,
                              plane_origin, plane_normal) -> None:
        from ..scene.em_objects import RevolvedObject
        target = getattr(self, "_sketch_edit_target", None)
        sketch_definition = self._viewport.sketch_definition()
        if isinstance(target, RevolvedObject):
            try:
                target.set_parameters({
                    "ProfilePts": profile,
                    "Angle": angle,
                    **{f"AxisPt1{axis}": axis_pt1[index] for index, axis in enumerate("XYZ")},
                    **{f"AxisPt2{axis}": axis_pt2[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneOrigin{axis}": plane_origin[index] for index, axis in enumerate("XYZ")},
                    **{f"PlaneNormal{axis}": plane_normal[index] for index, axis in enumerate("XYZ")},
                })
            except (TypeError, ValueError) as exc:
                self._info_bar.set_info(f"Unable to update revolution: {exc}")
                return
            target.sketch_definition = sketch_definition
            self._finish_sketch_feature_edit(target)
            return
        obj = RevolvedObject(
            profile_pts = profile,
            angle       = angle,
            axis_pt1    = axis_pt1,
            axis_pt2    = axis_pt2,
            plane_origin = plane_origin,
            plane_normal = plane_normal,
            material    = self._draw_material,
        )
        obj.sketch_definition = sketch_definition
        self._viewport.scene.add_object(obj)
        self._viewport.scene.select(obj)
        self._viewport.object_selected.emit(obj)
        self._viewport.selection_changed.emit([obj])
        self._viewport.scene_changed.emit()
        self._viewport._render()
        self._refresh_materials()
        self._info_bar.set_info(f"Revolved body created: {obj.name}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� reference plane
    def _open_reference_plane_dialog(self, start_sketch: bool = False) -> None:
        vp = self._viewport
        self._sketch_after_plane_defined = bool(start_sketch)
        active_plane = vp.scene.active_plane
        current_origin = (
            active_plane.origin if active_plane is not None else vp._custom_plane_origin
        )
        current_normal = (
            active_plane.normal if active_plane is not None else vp._custom_plane_normal
        )
        # Reuse a single dialog instance so it survives hide/show during 3D picking
        dlg = getattr(self, "_ref_plane_dlg", None)
        if dlg is None:
            dlg = ReferencePlaneDialog(
                self,
                current_origin=current_origin,
                current_normal=current_normal,
                viewport=vp,
            )
            dlg.plane_defined.connect(self._on_plane_defined)
            dlg.finished.connect(self._on_reference_plane_dialog_finished)
            self._ref_plane_dlg = dlg
        else:
            dlg.set_active_plane(current_origin, current_normal)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_plane_defined(self, origin, normal, name) -> None:
        """Slot for ReferencePlaneDialog.plane_defined."""
        start_sketch = self._sketch_after_plane_defined
        self._sketch_after_plane_defined = False
        scene = self._viewport.scene
        plane = scene.add_reference_plane(
            name or f"Plane {len(scene.reference_planes)+1}",
            origin, normal, make_active=True,
        )
        self._viewport.set_reference_plane(origin, normal)
        self._viewport.set_grid(
            self._workspace_size,
            self._grid_spacing,
            "CUSTOM",
            self._units,
        )
        self._refresh_materials()
        self._info_bar.set_info(f"Reference plane '{plane.name}' added and made active.")
        if start_sketch:
            self._start_embedded_sketch(origin, normal)

    def _on_reference_plane_dialog_finished(self, _result: int) -> None:
        self._sketch_after_plane_defined = False

    def _on_plane_make_active(self, plane) -> None:
        scene = self._viewport.scene
        scene.set_active_plane(plane)
        self._sync_active_reference_plane_to_viewport()
        self._refresh_materials()
        self._info_bar.set_info(f"Active reference plane: {plane.name}")

    def _on_plane_view_normal(self, plane) -> None:
        """Look at the selected reference plane along its positive normal."""
        import math

        normal = tuple(float(value) for value in plane.normal)
        length = math.sqrt(sum(value * value for value in normal))
        if length < 1e-12:
            return
        normal = tuple(value / length for value in normal)
        origin = tuple(float(value) for value in plane.origin)

        camera = self._viewport._renderer.GetActiveCamera()
        current_position = camera.GetPosition()
        distance = math.sqrt(sum(
            (current_position[index] - origin[index]) ** 2
            for index in range(3)
        ))
        distance = max(distance, float(self._workspace_size), 1.0)
        camera.SetPosition(*(
            origin[index] + normal[index] * distance
            for index in range(3)
        ))
        camera.SetFocalPoint(*origin)

        # Prefer world Z as the vertical direction, unless it is parallel to
        # the view direction; then use world Y to keep the view upright.
        view_up = (0.0, 0.0, 1.0)
        if abs(sum(view_up[index] * normal[index] for index in range(3))) > 0.95:
            view_up = (0.0, 1.0, 0.0)
        camera.SetViewUp(*view_up)
        self._viewport._renderer.ResetCameraClippingRange()
        self._viewport._render()
        self._info_bar.set_info(f"View normal to plane: {plane.name}")

    def _on_plane_delete(self, plane) -> None:
        scene = self._viewport.scene
        # Don't allow deleting the last remaining plane
        if len(scene.reference_planes) <= 1:
            QMessageBox.warning(self, "Delete Plane",
                                "Cannot delete the last reference plane.")
            return
        was_active = (scene.active_plane is plane)
        scene.remove_reference_plane(plane)
        if was_active and scene.active_plane is not None:
            self._sync_active_reference_plane_to_viewport()
        self._refresh_materials()
        self._info_bar.set_info(f"Plane deleted: {plane.name}")

    def _on_plane_rename(self, plane, new_name: str) -> None:
        plane.name = new_name
        self._refresh_materials()

    def _on_plane_triad_visibility_changed(self, visible: bool) -> None:
        self._viewport.set_plane_triad_visible(bool(visible))
        self._refresh_materials()
        state = "shown" if visible else "hidden"
        self._info_bar.set_info(f"Reference-plane XYZ triad {state}.")

    def _apply_grid_visibility(self, visible: bool) -> None:
        self._viewport.set_grid_visible(bool(visible))
        self._act_grid_toggle.setChecked(bool(visible))
        self._act_grid_toggle.setText("Hide Grid" if visible else "Show Grid")
        self._refresh_materials()

    def _on_grid_visibility_changed(self, visible: bool) -> None:
        self._apply_grid_visibility(bool(visible))
        state = "shown" if visible else "hidden"
        self._info_bar.set_info(f"Reference-plane grid {state}.")

    def _on_grid_adaptive_changed(self, enabled: bool) -> None:
        self._adaptive_grid_enabled = bool(enabled)
        self._viewport.set_adaptive_grid(
            self._adaptive_grid_enabled,
            self._adaptive_grid_margin,
        )
        self._save_app_settings()
        self._refresh_materials()
        state = "enabled" if enabled else "disabled"
        self._info_bar.set_info(f"Adaptive grid {state}.")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� grid / units / workspace
    @staticmethod
    def _axis_plane_from_normal(normal) -> str:
        """Map a plane normal to the closest axis-aligned drawing mode."""
        nx, ny, nz = abs(float(normal[0])), abs(float(normal[1])), abs(float(normal[2]))
        if nz >= nx and nz >= ny:
            return "XY"
        if ny >= nx and ny >= nz:
            return "XZ"
        return "YZ"

    def _active_draw_plane_name(self) -> str:
        scene = self._viewport.scene
        if scene.active_plane is None:
            return "XY"
        return self._axis_plane_from_normal(scene.active_plane.normal)

    def _sync_active_reference_plane_to_viewport(self) -> None:
        """Keep viewport drawing grid/reference aligned with the active tree plane."""
        scene = self._viewport.scene
        if scene.active_plane is None:
            return
        self._viewport.set_reference_plane(
            scene.active_plane.origin,
            scene.active_plane.normal,
        )
        self._viewport.set_grid(
            self._workspace_size,
            self._grid_spacing,
            self._active_draw_plane_name(),
            self._units,
        )

    def _on_grid_changed(self, value: float) -> None:
        self._grid_spacing = float(value)
        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._sync_settings_dialog_values()

    def _on_workspace_changed(self, value: float) -> None:
        self._workspace_size = float(value)
        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._sync_settings_dialog_values()
        self._info_bar.set_info(f"Workspace size: {self._format_locale_number(self._workspace_size)} {self._units}")

    def _parse_locale_number(self, text: str) -> float:
        val, ok = self._ui_locale.toDouble(str(text).strip())  # FIX: val is float, ok is bool!
        if ok:
            return float(val)
        normalized = str(text).strip().replace(" ", "").replace("\u00a0", "")
        if "," in normalized and "." in normalized:
            if normalized.rfind(",") > normalized.rfind("."):
                normalized = normalized.replace(".", "").replace(",", ".")
            else:
                normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(",", ".")
        try:
            return float(normalized)
        except Exception as exc:
            raise ValueError("invalid locale number") from exc

    def _format_locale_number(self, value: float) -> str:
        return self._ui_locale.toString(float(value), 'g', 12)

    def _make_numeric_locale(self, decimal_separator: str) -> QLocale:
        if decimal_separator == ",":
            return QLocale(QLocale.Italian, QLocale.Italy)
        return QLocale.c()

    def _load_app_settings(self) -> None:
        settings = QSettings()

        units = str(settings.value("display/units", self._units))
        if units in _UNITS:
            self._units = units

        try:
            self._workspace_size = float(settings.value("display/workspace_size", self._workspace_size))
        except Exception:
            pass

        try:
            self._grid_spacing = float(settings.value("display/grid_size", self._grid_spacing))
        except Exception:
            pass

        try:
            self._adaptive_grid_enabled = bool(int(settings.value("display/adaptive_grid_enabled", 0)))
        except Exception:
            pass

        try:
            self._adaptive_grid_margin = max(
                0.0,
                float(settings.value("display/adaptive_grid_margin", self._adaptive_grid_margin)),
            )
        except Exception:
            pass

        try:
            self._plane_triad_size = float(settings.value("display/plane_triad_size", self._plane_triad_size))
        except Exception:
            pass

        decimal_separator = str(settings.value("display/decimal_separator", self._decimal_separator))
        if decimal_separator in {".", ","}:
            self._decimal_separator = decimal_separator

        color_text = str(settings.value("display/selection_color", ""))
        if color_text:
            parts = color_text.split(",")
            if len(parts) == 3:
                try:
                    self._selection_color = tuple(float(p) for p in parts)
                except Exception:
                    pass

        self._ui_locale = self._make_numeric_locale(self._decimal_separator)

        # Load simulation & mesh settings
        solver = str(settings.value("simulation/solver", self._sim_solver)).strip().upper()
        if solver in _EMERGE_SOLVERS:
            self._sim_solver = solver

        try:
            self._sim_parallel_enabled = bool(int(settings.value("simulation/parallel_enabled", 1)))
        except Exception:
            pass

        try:
            self._sim_pardiso_threads = max(1, int(settings.value("simulation/pardiso_threads", 8)))
        except Exception:
            pass

        try:
            self._sim_acc_threads = max(1, int(settings.value("simulation/acc_threads", 10)))
        except Exception:
            pass

        try:
            self._mesh_resolution = max(0.01, min(1.0, float(settings.value("mesh/resolution", 0.3))))
        except Exception:
            pass

        try:
            self._curved_boundary_resolution = max(3, int(settings.value("mesh/curved_boundary_resolution", 20)))
        except Exception:
            pass

        try:
            self._refinement_boundary_size_mm = max(1e-6, float(settings.value("mesh/refinement_boundary_size_mm", 0.25)))
            self._refinement_face_size_mm = max(1e-6, float(settings.value("mesh/refinement_face_size_mm", 0.1)))
            self._refinement_growth_rate = max(1.001, float(settings.value("mesh/refinement_growth_rate", 3.0)))
            self._refinement_max_size_mm = max(0.0, float(settings.value("mesh/refinement_max_size_mm", 0.0)))
        except Exception:
            pass

        try:
            self._sim_plot_sparams_after_sim = bool(int(settings.value("simulation/plot_sparams_after_sim", 1)))
        except Exception:
            pass

        try:
            self._sim_export_sparams_after_sim = bool(int(settings.value("simulation/export_sparams_after_sim", 1)))
        except Exception:
            pass

        try:
            self._export_full_scene_step = bool(int(settings.value("utility/export_full_scene_step", 0)))
        except Exception:
            pass

        try:
            self._boolean_decimation_enabled = bool(int(settings.value("utility/boolean_decimation_enabled", 0)))
        except Exception:
            pass
        self._workspace_path = str(settings.value("utility/workspace_path", self._workspace_path)).strip()
        self._global_material_db_path = str(
            settings.value("utility/global_material_db_path", self._global_material_db_path)
        ).strip()

    def _save_app_settings(self) -> None:
        settings = QSettings()
        settings.setValue("display/units", self._units)
        settings.setValue("display/decimal_separator", self._decimal_separator)
        settings.setValue("display/workspace_size", self._workspace_size)
        settings.setValue("display/grid_size", self._grid_spacing)
        settings.setValue("display/adaptive_grid_enabled", int(self._adaptive_grid_enabled))
        settings.setValue("display/adaptive_grid_margin", self._adaptive_grid_margin)
        settings.setValue("display/plane_triad_size", self._plane_triad_size)
        settings.setValue(
            "display/selection_color",
            ",".join(str(float(v)) for v in self._selection_color),
        )

        # Save simulation & mesh settings
        settings.setValue("simulation/solver", self._sim_solver)
        settings.setValue("simulation/parallel_enabled", int(self._sim_parallel_enabled))
        settings.setValue("simulation/pardiso_threads", int(self._sim_pardiso_threads))
        settings.setValue("simulation/acc_threads", int(self._sim_acc_threads))
        settings.setValue("mesh/resolution", float(self._mesh_resolution))
        settings.setValue("mesh/curved_boundary_resolution", int(self._curved_boundary_resolution))
        settings.setValue("mesh/refinement_boundary_size_mm", float(self._refinement_boundary_size_mm))
        settings.setValue("mesh/refinement_face_size_mm", float(self._refinement_face_size_mm))
        settings.setValue("mesh/refinement_growth_rate", float(self._refinement_growth_rate))
        settings.setValue("mesh/refinement_max_size_mm", float(self._refinement_max_size_mm))
        settings.setValue("simulation/plot_sparams_after_sim", int(self._sim_plot_sparams_after_sim))
        settings.setValue("simulation/export_sparams_after_sim", int(self._sim_export_sparams_after_sim))
        settings.setValue("utility/export_full_scene_step", int(self._export_full_scene_step))
        settings.setValue("utility/boolean_decimation_enabled", int(self._boolean_decimation_enabled))
        settings.setValue("utility/workspace_path", self._workspace_path)
        settings.setValue("utility/global_material_db_path", self._global_material_db_path)
        settings.sync()

    def _sync_settings_dialog_values(self) -> None:
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is None:
            return
        dlg.set_values(
            units=self._units,
            decimal_separator=self._decimal_separator,
            workspace_size=self._workspace_size,
            grid_size=self._grid_spacing,
            adaptive_grid_margin=self._adaptive_grid_margin,
            plane_triad_size=self._plane_triad_size,
            selection_color=self._selection_color,
            locale=self._ui_locale,
            solver=self._sim_solver,
            parallel_enabled=self._sim_parallel_enabled,
            pardiso_threads=self._sim_pardiso_threads,
            acc_threads=self._sim_acc_threads,
            mesh_resolution=self._mesh_resolution,
            curved_boundary_resolution=self._curved_boundary_resolution,
            refinement_boundary_size_mm=self._refinement_boundary_size_mm,
            refinement_face_size_mm=self._refinement_face_size_mm,
            refinement_growth_rate=self._refinement_growth_rate,
            refinement_max_size_mm=self._refinement_max_size_mm,
            plot_sparams_after_sim=self._sim_plot_sparams_after_sim,
            export_sparams_after_sim=self._sim_export_sparams_after_sim,
            export_full_scene_step=self._export_full_scene_step,
            boolean_decimation_enabled=self._boolean_decimation_enabled,
            workspace_path=self._workspace_path,
            global_material_db_path=self._global_material_db_path,
        )

    def _convert_scene_units(self, old_units: str, new_units: str) -> None:
        if old_units not in _MM_PER_UNIT or new_units not in _MM_PER_UNIT:
            return
        factor = _MM_PER_UNIT[old_units] / _MM_PER_UNIT[new_units]
        if math.isclose(factor, 1.0, rel_tol=0.0, abs_tol=1e-15):
            return

        self._history_record()
        settings = self._project_tree.get_settings()
        parameters = settings.get("parameters", [])
        parameter_factors = {}
        for entry in parameters if isinstance(parameters, list) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            source_units = str(entry.get("unit", "") or old_units)
            if source_units not in _MM_PER_UNIT:
                source_units = old_units
            parameter_factor = _MM_PER_UNIT[source_units] / _MM_PER_UNIT[new_units]
            parameter_factors[name] = parameter_factor
            try:
                entry["value"] = float(entry.get("value", 0.0)) * parameter_factor
            except (TypeError, ValueError):
                pass
            entry["unit"] = new_units
        settings["parameters"] = parameters

        scene_objects = list(self._viewport.scene.objects)
        for obj in scene_objects:
            try:
                if not self._scale_by_attributes(obj, factor, preserve_center=False):
                    continue
                if hasattr(obj, "creation_plane_origin"):
                    obj.creation_plane_origin = tuple(
                        float(value) * factor for value in obj.creation_plane_origin
                    )
                history = getattr(obj, "creation_history", None)
                if isinstance(history, dict):
                    obj.creation_history = _scale_creation_history(history, factor)
                formulas = getattr(obj, "param_formulas", None)
                if isinstance(formulas, dict):
                    obj.param_formulas = {
                        key: _scale_formula_expression(value, factor, parameter_factors)
                        for key, value in formulas.items()
                    }
                definition = getattr(obj, "sketch_definition", None)
                if isinstance(definition, dict):
                    obj.sketch_definition = _scale_sketch_definition(
                        definition, factor, parameter_factors
                    )
                pattern = getattr(obj, "pattern_definition", None)
                if isinstance(pattern, dict):
                    pattern = deepcopy(pattern)
                    pattern["settings"] = _scale_pattern_settings(
                        pattern.get("settings", {}), factor, parameter_factors
                    )
                    obj.pattern_definition = pattern
                instance = getattr(obj, "pattern_instance", None)
                if isinstance(instance, dict) and isinstance(instance.get("settings"), dict):
                    instance = deepcopy(instance)
                    instance["settings"] = _scale_pattern_settings(
                        instance["settings"], factor, parameter_factors
                    )
                    obj.pattern_instance = instance
            except Exception as exc:
                self._info_bar.set_info(
                    f"{getattr(obj, 'name', 'Object')}: unit conversion failed: {exc}"
                )

        objects_by_name = {str(getattr(obj, "name", "")): obj for obj in scene_objects}
        for obj in scene_objects:
            definition = getattr(obj, "pattern_definition", None)
            if not isinstance(definition, dict) or not isinstance(definition.get("sources"), list):
                continue
            definition = deepcopy(definition)
            definition["sources"] = [
                self._serialize_object_snapshot(objects_by_name[str(snapshot.get("name", ""))])
                if isinstance(snapshot, dict)
                and str(snapshot.get("name", "")) in objects_by_name
                else snapshot
                for snapshot in definition["sources"]
            ]
            obj.pattern_definition = definition

        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._sync_boolean_provenance()
        self._recompute_parametric_objects(_regenerate_snaps=False)
        self.recompute_sketch_dimensions()
        selection = list(getattr(self._viewport.scene, "selection", []) or [])
        if selection:
            self._body_props.set_selection(selection)
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()
        self._mark_simulation_dirty(steps=True, script=True)

    def _apply_display_settings(self, values: dict) -> None:
        units = str(values.get("units", self._units))
        decimal_separator = str(values.get("decimal_separator", self._decimal_separator))
        selection_color = tuple(values.get("selection_color", self._selection_color))

        if decimal_separator not in {".", ","}:
            decimal_separator = "."

        old_units = self._units
        old_locale = self._ui_locale
        old_mesh_resolution = self._mesh_resolution
        old_curved_boundary_resolution = self._curved_boundary_resolution
        old_export_full_scene_step = self._export_full_scene_step

        if units != old_units:
            self._convert_scene_units(old_units, units)
        self._units = units
        if units != old_units:
            self._reset_simulation_cache()
        self._decimal_separator = decimal_separator
        self._ui_locale = self._make_numeric_locale(decimal_separator)
        QLocale.setDefault(self._ui_locale)
        set_numeric_locale(self._ui_locale)

        self._grid_spacing = float(values.get("grid_size", self._grid_spacing))
        self._adaptive_grid_margin = max(
            0.0,
            float(values.get("adaptive_grid_margin", self._adaptive_grid_margin)),
        )
        self._workspace_size = float(values.get("workspace_size", self._workspace_size))
        self._plane_triad_size = max(1e-6, float(values.get("plane_triad_size", self._plane_triad_size)))

        # Apply simulation & mesh settings
        solver = str(values.get("solver", self._sim_solver)).strip().upper()
        if solver in _EMERGE_SOLVERS:
            self._sim_solver = solver
        self._sim_parallel_enabled = bool(values.get("parallel_enabled", self._sim_parallel_enabled))
        self._sim_pardiso_threads = max(1, int(values.get("pardiso_threads", self._sim_pardiso_threads)))
        self._sim_acc_threads = max(1, int(values.get("acc_threads", self._sim_acc_threads)))
        self._mesh_resolution = max(0.01, min(1.0, float(values.get("mesh_resolution", self._mesh_resolution))))
        self._curved_boundary_resolution = max(3, int(values.get("curved_boundary_resolution", self._curved_boundary_resolution)))
        self._refinement_boundary_size_mm = max(1e-6, float(values.get("refinement_boundary_size_mm", self._refinement_boundary_size_mm)))
        self._refinement_face_size_mm = max(1e-6, float(values.get("refinement_face_size_mm", self._refinement_face_size_mm)))
        self._refinement_growth_rate = max(1.001, float(values.get("refinement_growth_rate", self._refinement_growth_rate)))
        self._refinement_max_size_mm = max(0.0, float(values.get("refinement_max_size_mm", self._refinement_max_size_mm)))
        self._sim_plot_sparams_after_sim = bool(values.get("plot_sparams_after_sim", self._sim_plot_sparams_after_sim))
        self._sim_export_sparams_after_sim = bool(values.get("export_sparams_after_sim", self._sim_export_sparams_after_sim))
        self._export_full_scene_step = bool(values.get("export_full_scene_step", self._export_full_scene_step))
        self._boolean_decimation_enabled = bool(values.get("boolean_decimation_enabled", self._boolean_decimation_enabled))
        self._workspace_path = str(values.get("workspace_path", self._workspace_path)).strip()
        global_material_db_path = str(
            values.get("global_material_db_path", self._global_material_db_path)
        ).strip()
        if global_material_db_path != self._global_material_db_path:
            self._set_global_material_db_path(global_material_db_path, persist=False)
        if self._export_full_scene_step and not old_export_full_scene_step:
            self._sim_full_scene_step_dirty = True
        self._project_tree.set_runtime_settings({
            "solver": self._sim_solver,
            "parallel_enabled": self._sim_parallel_enabled,
            "pardiso_threads": self._sim_pardiso_threads,
            "acc_threads": self._sim_acc_threads,
            "plot_sparams_after_sim": self._sim_plot_sparams_after_sim,
            "export_sparams_after_sim": self._sim_export_sparams_after_sim,
        })
        if (
            self._mesh_resolution != old_mesh_resolution
            or self._curved_boundary_resolution != old_curved_boundary_resolution
        ):
            self._mark_simulation_dirty(script=True)

        if selection_color != self._selection_color:
            self._selection_color = selection_color
            set_selection_color(self._selection_color)
            for obj in self._viewport.scene.objects:
                obj.refresh_appearance()

        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._viewport.set_adaptive_grid(
            self._adaptive_grid_enabled,
            self._adaptive_grid_margin,
        )
        self._viewport.set_plane_triad_size(self._plane_triad_size)
        self._sync_settings_dialog_values()
        self._viewport._render()
        self._save_app_settings()

        if old_locale != self._ui_locale:
            self._refresh_materials()

    def _open_settings_dialog(self) -> None:
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is None:
            dlg = SettingsDialog(self)
            self._settings_dlg = dlg
        self._sync_settings_dialog_values()
        if dlg.exec_() == QDialog.Accepted:
            self._apply_display_settings(dlg.values())
            self._info_bar.set_info("Application settings updated.")

    def _on_units_changed(self, units: str) -> None:
        workspace_size = self._workspace_size
        grid_size = self._grid_spacing
        adaptive_grid_margin = self._adaptive_grid_margin
        plane_triad_size = self._plane_triad_size
        if units != self._units and self._units in _MM_PER_UNIT and units in _MM_PER_UNIT:
            factor = _MM_PER_UNIT[self._units] / _MM_PER_UNIT[units]
            workspace_size *= factor
            grid_size *= factor
            adaptive_grid_margin *= factor
            plane_triad_size *= factor
        self._apply_display_settings(
            {
                "units": units,
                "decimal_separator": self._decimal_separator,
                "workspace_size": workspace_size,
                "grid_size": grid_size,
                "adaptive_grid_margin": adaptive_grid_margin,
                "plane_triad_size": plane_triad_size,
                "selection_color": self._selection_color,
            }
        )
        self._info_bar.set_info(f"Units changed to {units}")

    def _on_selection_mode_changed(self, mode: str) -> None:
        """Switch viewport between Object / Face / Edge / Vertex picking."""
        m = mode.lower()
        # "All" in the combo means "whole object"
        if m == "all":
            m = "object"
        elif m == "surface":
            m = "face"
        self._viewport.set_selection_mode(m)
        self._info_bar.set_info(f"Selection mode: {mode}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� scene refresh
    def _refresh_materials(self) -> None:
        scene = self._viewport.scene
        by_mat = scene.by_material()
        settings = self._project_tree.get_settings()
        material_priorities = settings.get("material_priorities", {})
        if not isinstance(material_priorities, dict):
            material_priorities = {}
        self._materials.refresh(
            by_mat,
            planes=scene.reference_planes,
            active_plane=scene.active_plane,
            plane_triad_visible=self._viewport.is_plane_triad_visible(),
            grid_visible=self._viewport.is_grid_visible(),
            grid_adaptive=self._adaptive_grid_enabled,
            material_priorities=material_priorities,
        )

    def _on_settings_changed(self) -> None:
        self._sync_project_variable_names()
        self._sync_log_verbosity_from_settings()
        self._sync_port_reference_state()
        self._info_bar.set_info("EMERGE settings updated.")
        self._mark_simulation_dirty(steps=False, script=True)

    def _simdata_file_in_dir(self, results_dir: Path) -> Path | None:
        if not results_dir.exists() or not results_dir.is_dir():
            return None
        for name in ("simdata.emerge", "simdata.EMERGE"):
            p = results_dir / name
            if p.exists() and p.is_file():
                return p
        return None

    def _candidate_results_dirs_for_sim(self, simulation_name: str) -> list[Path]:
        bundle_dir = self._simulation_bundle_dir()
        safe_project = self._safe_script_token(self._project_name)
        safe_sim = self._safe_script_token(simulation_name)

        sim_index = None
        settings = self._project_tree.get_settings()
        sims = settings.get("simulations", []) if isinstance(settings, dict) else []
        if isinstance(sims, list):
            for idx, sim in enumerate(sims, start=1):
                if not isinstance(sim, dict):
                    continue
                name = str(sim.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
                if name == simulation_name:
                    sim_index = idx
                    break

        candidates: list[Path] = []
        simulation_root = bundle_dir / f"{safe_project}_{safe_sim}"
        if simulation_root.is_dir():
            try:
                candidates.extend(
                    sorted(
                        simulation_root.rglob("*.EMResults"),
                        key=lambda path: path.stat().st_mtime,
                        reverse=True,
                    )
                )
            except OSError:
                pass
        if sim_index is not None:
            candidates.append(bundle_dir / f"{safe_project}_{sim_index:02d}_{safe_sim}.EMResults")
        candidates.append(bundle_dir / f"{safe_project}.EMResults")

        try:
            all_res = sorted(bundle_dir.glob("*.EMResults"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            all_res = []

        sim_token = safe_sim.lower()
        for p in all_res:
            stem = p.stem.lower()
            if sim_token and sim_token in stem and p not in candidates:
                candidates.append(p)
        for p in all_res:
            if p not in candidates:
                candidates.append(p)

        # Existing results may have been generated before the project path was
        # corrected. Recover only matching EMERGE result folders in the
        # workspace so the plot command can still open the latest run.
        if not any(self._simdata_file_in_dir(path) for path in candidates):
            workspace_root = Path(__file__).resolve().parents[3]
            for simdata in workspace_root.rglob("simdata.emerge"):
                result_dir = simdata.parent
                if result_dir not in candidates and safe_project.lower() in result_dir.parent.name.lower():
                    candidates.append(result_dir)
        return candidates

    def _resolve_emerge_simulation_ctor(self):
        # EMERGE API differs across versions/distributions.
        # Some builds require importing emerge_iron before emerge symbols are exposed.
        import importlib
        import pkgutil

        for bootstrap in ("emerge_iron",):
            try:
                importlib.import_module(bootstrap)
            except Exception:
                pass

        self._import_installed_emerge()

        tried: list[str] = []

        def _try(mod_name: str, attr_name: str):
            tried.append(f"{mod_name}.{attr_name}")
            try:
                mod = importlib.import_module(mod_name)
                ctor = getattr(mod, attr_name, None)
                if callable(ctor):
                    return ctor
            except Exception:
                return None
            return None

        for mod_name, attr_name in (
            ("emerge", "Simulation"),
            ("emerge.core", "Simulation"),
            ("emerge.ext", "Simulation"),
            ("emerge._emerge.simmodel", "Simulation"),
            ("emerge", "SimulationBeta"),
        ):
            ctor = _try(mod_name, attr_name)
            if ctor is not None:
                return ctor

        # Dynamic fallback: search emerge submodules for a callable Simulation symbol.
        em = None
        em_file = "<unknown>"
        em_attrs = []
        try:
            em = importlib.import_module("emerge")
            em_file = str(getattr(em, "__file__", "<unknown>"))
            em_attrs = [a for a in dir(em) if "sim" in a.lower()][:20]
        except Exception:
            em = None

        if em is not None:
            # Check already imported child modules first.
            for attr_name in dir(em):
                try:
                    obj = getattr(em, attr_name, None)
                    ctor = getattr(obj, "Simulation", None)
                    if callable(ctor):
                        return ctor
                except Exception:
                    continue

            # Then scan package modules.
            em_path = getattr(em, "__path__", None)
            if em_path is not None:
                try:
                    for mod_info in pkgutil.walk_packages(em_path, em.__name__ + "."):
                        mod_name = mod_info.name
                        lname = mod_name.lower()
                        if not ("sim" in lname or "core" in lname or "ext" in lname):
                            continue
                        tried.append(f"{mod_name}.Simulation")
                        try:
                            mod = importlib.import_module(mod_name)
                            ctor = getattr(mod, "Simulation", None)
                            if callable(ctor):
                                return ctor
                        except Exception:
                            continue
                except Exception:
                    pass

        raise RuntimeError(
            "Unable to resolve EMERGE Simulation class. "
            f"Tried: {', '.join(tried)}. "
            f"emerge module: {em_file}; sim-like attrs: {em_attrs}"
        )

    @staticmethod
    def _import_installed_emerge():
        """Import site-packages EMERGE, not the local ``emerge`` helper package."""
        import importlib
        import sys
        from pathlib import Path

        local_emerge = Path(__file__).resolve().parents[1] / "emerge"
        local_root = str(local_emerge.parent).lower()
        current = sys.modules.get("emerge")
        current_file = str(getattr(current, "__file__", "")).lower()
        if current is not None and current_file.startswith(str(local_emerge).lower()):
            for name in list(sys.modules):
                if name == "emerge" or name.startswith("emerge."):
                    del sys.modules[name]

        old_path = list(sys.path)
        sys.path[:] = [
            entry for entry in sys.path
            if str(Path(entry or ".").resolve()).lower() != local_root
        ]
        try:
            module = importlib.import_module("emerge")
            module_file = str(getattr(module, "__file__", "")).lower()
            if module_file.startswith(str(local_emerge).lower()):
                raise RuntimeError(f"Local EMERGE helper package was imported: {module_file}")
            return module
        finally:
            sys.path[:] = old_path

    def _load_sim_grid_from_results(
        self,
        simulation_name: str,
        results_dir: Path | None = None,
        *,
        allow_irregular: bool = False,
    ):
        sim_ctor = self._resolve_emerge_simulation_ctor()

        candidates = (
            [Path(results_dir)]
            if results_dir is not None
            else self._candidate_results_dirs_for_sim(simulation_name)
        )
        last_err = None
        for candidate_dir in candidates:
            simdata = self._simdata_file_in_dir(candidate_dir)
            if simdata is None:
                continue
            results_dir = candidate_dir.resolve()
            model_name = str(results_dir.parent / results_dir.stem)
            cwd_prev = os.getcwd()
            try:
                os.chdir(str(results_dir.parent))
                try:
                    sim = sim_ctor(model_name, load_file=True)
                except TypeError:
                    sim = sim_ctor(model_name)
                    load_fn = getattr(sim, "load", None)
                    if callable(load_fn):
                        try:
                            load_fn()
                        except TypeError:
                            load_fn(str(results_dir))
                mw_data = getattr(getattr(sim, "data", None), "mw", None)
                if mw_data is None:
                    raise RuntimeError("Loaded simulation has no microwave data.")
                scalar = getattr(mw_data, "scalar", None)
                try:
                    grid = getattr(scalar, "grid", None)
                except ValueError:
                    if not allow_irregular or not hasattr(scalar, "_data_entries"):
                        raise
                    grid = scalar
                if grid is None:
                    raise RuntimeError("Loaded simulation has no scalar.grid data.")
                return sim, grid, simdata
            except Exception as exc:
                last_err = exc
            finally:
                os.chdir(cwd_prev)

        if last_err is not None:
            raise RuntimeError(f"Failed to load results for '{simulation_name}': {last_err}") from last_err
        raise RuntimeError(
            f"No simdata.emerge found for simulation '{simulation_name}' in {self._simulation_bundle_dir()}"
        )

    def _grid_port_numbers(self, grid) -> list[int]:
        port_map = getattr(grid, "_portmap", None)
        if isinstance(port_map, dict) and port_map:
            out: list[int] = []
            for k in port_map.keys():
                try:
                    out.append(int(k))
                except Exception:
                    pass
            if out:
                return sorted(set(out))
        try:
            smat = grid.Smat
            n_ports = int(smat.shape[1]) if len(getattr(smat, "shape", ())) >= 3 else 1
            return list(range(1, max(1, n_ports) + 1))
        except Exception:
            return [1]

    def _track_output_plot_windows(self, existing_figures: set[int]) -> None:
        """Release result arrays retained by Matplotlib after a plot is closed."""
        try:
            import gc
            import weakref
            import matplotlib.pyplot as plt
            from matplotlib._pylab_helpers import Gcf
        except ImportError:
            return

        for manager in Gcf.get_all_fig_managers():
            figure = getattr(getattr(manager, "canvas", None), "figure", None)
            if figure is None or id(figure) in existing_figures:
                continue

            figure_ref = weakref.ref(figure)
            def make_release(current_figure_ref):
                released = False

                def release(*_args):
                    nonlocal released
                    if released:
                        return
                    released = True
                    plot_figure = current_figure_ref()
                    if plot_figure is not None:
                        try:
                            plt.close(plot_figure)
                            plot_figure.clear()
                        except Exception:
                            pass
                    gc.collect()

                return release

            release = make_release(figure_ref)
            figure.canvas.mpl_connect("close_event", release)
            window = getattr(manager, "window", None)
            destroyed = getattr(window, "destroyed", None)
            if destroyed is not None:
                destroyed.connect(release)

    def _run_output_plot(self, callback) -> None:
        """Generate one plot and register deterministic cleanup for its window."""
        try:
            from matplotlib._pylab_helpers import Gcf
            existing_figures = {
                id(manager.canvas.figure)
                for manager in Gcf.get_all_fig_managers()
                if getattr(getattr(manager, "canvas", None), "figure", None) is not None
            }
        except ImportError:
            existing_figures = set()
        callback()
        self._track_output_plot_windows(existing_figures)

    def _on_output_plot_requested(self, payload: dict, *, results_dir: Path | None = None) -> None:
        name = str(payload.get("name", "Output")).strip() or "Output"
        sim_name = str(payload.get("simulation", "")).strip()
        plot_type = str(payload.get("plot_type", "plot_sp")).strip() or "plot_sp"
        plot_params = payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {}
        if not sim_name:
            QMessageBox.warning(self, "Output", "Output has no simulation assigned.")
            return

        if plot_type in {"plot_sp", "plot_vswr", "smith", "plot"}:
            import re

            from .chart_data import make_sparameter_plot_data
            from .touchstone import find_touchstones, read_touchstone_ri

            try:
                saved_paths = find_touchstones(
                    self._simulation_bundle_dir(), self._project_name, sim_name
                )
                if saved_paths:
                    raw_parameters = plot_params.get("s_parameters", [])
                    if not isinstance(raw_parameters, list) or not raw_parameters:
                        raw_parameters = [plot_params.get("s_parameter", "S11")]
                    parameters = []
                    for raw_parameter in raw_parameters:
                        parameter = str(raw_parameter).strip().upper()
                        if not re.fullmatch(r"S\d+[,:/_-]?\d+", parameter):
                            parameter = f"S{max(1, int(plot_params.get('port_i', 1)))}{max(1, int(plot_params.get('port_j', 1)))}"
                        if parameter not in parameters:
                            parameters.append(parameter)
                    chart_data = None
                    all_series = []
                    for saved_path in saved_paths:
                        parsed = read_touchstone_ri(saved_path)
                        file_data = make_sparameter_plot_data(
                            {
                                "name": name,
                                "plot_type": plot_type,
                                "params": {"s_parameters": parameters},
                            },
                            parsed["frequencies"],
                            parsed["s_matrices"],
                            include_all_parameters=True,
                            file_name=saved_path.name,
                            file_id=str(saved_path.resolve()),
                        )
                        if chart_data is None:
                            chart_data = file_data
                        all_series.extend(file_data["series"])
                    chart_data["series"] = all_series
                    self._show_chart(f"{sim_name}::{name}", chart_data)
                    source_names = ", ".join(path.name for path in saved_paths)
                    self._info_bar.set_info(
                        f"Output plotted: {name} ({plot_type}) from saved Touchstone {source_names}"
                    )
                    self._append_sim_log(
                        f"[info] Output plotted: {name} ({plot_type}) from saved Touchstone {source_names}"
                    )
                    return
            except (OSError, UnicodeError, ValueError) as exc:
                self._append_sim_log(
                    f"[warn] Saved Touchstone for '{name}' could not be used; loading EMERGE results: {exc}"
                )

        project_tree = getattr(self, "_project_tree", None)
        project_settings = project_tree.get_settings() if project_tree is not None else {}
        simulation_config = next(
            (
                item for item in project_settings.get("simulations", [])
                if isinstance(item, dict)
                and str(item.get("name", "")).strip() == sim_name
            ),
            {},
        )
        is_parametric_sparameter = (
            str(simulation_config.get("type", "")).strip().lower() == "parametric"
            and plot_type in {"plot_sp", "plot_vswr", "smith", "plot"}
        )
        try:
            loaded_sim, grid, simdata_path = self._load_sim_grid_from_results(
                sim_name,
                results_dir=results_dir,
                allow_irregular=is_parametric_sparameter,
            )
        except Exception as exc:
            result_dirs = (
                [Path(results_dir)]
                if results_dir is not None
                else self._candidate_results_dirs_for_sim(sim_name)
            )
            if not any(self._simdata_file_in_dir(path) is not None for path in result_dirs):
                chart_key = f"{sim_name}::{name}"
                self._show_chart(
                    chart_key,
                    {
                        "title": name,
                        "plot_type": plot_type,
                        "xlabel": "Frequency (GHz)" if plot_type in {"plot_sp", "plot_vswr", "smith", "plot"} else "",
                        "ylabel": "S-parameter" if plot_type in {"plot_sp", "plot_vswr", "smith", "plot"} else "",
                        "x_values": [],
                        "series": [],
                    },
                )
                self._info_bar.set_info(
                    f"Empty plot opened for {name}; it will populate when {sim_name} produces results."
                )
                self._append_sim_log(
                    f"[info] Empty plot opened for {name}; waiting for the first {sim_name} result."
                )
                return
            QMessageBox.warning(self, "Output", str(exc))
            self._append_sim_log(f"[warn] Output '{name}' failed: {exc}")
            return

        import numpy as np

        if is_parametric_sparameter and hasattr(grid, "_data_entries"):
            from .chart_data import make_parametric_sparameter_plot_data

            parameter_values = [
                value.strip()
                for value in str(simulation_config.get("ParamValues", "")).split(",")
                if value.strip()
            ]
            try:
                chart_data = make_parametric_sparameter_plot_data(
                    {"name": name, "plot_type": plot_type, "params": plot_params},
                    grid,
                    str(simulation_config.get("ParamName", "")),
                    parameter_values,
                    expected_frequency_count=max(
                        1, int(simulation_config.get("NumberOfPoints", 0))
                    ),
                    simulation_name=sim_name,
                )
            except (TypeError, ValueError, AttributeError) as exc:
                QMessageBox.warning(
                    self,
                    "Output",
                    f"Parametric S-parameter data could not be plotted: {exc}",
                )
                self._append_sim_log(
                    f"[warn] Output '{name}' could not be built from raw Parametric data: {exc}"
                )
                return
            self._show_chart(f"{sim_name}::{name}", chart_data)
            self._info_bar.set_info(
                f"Output plotted: {name} ({plot_type}) from raw Parametric results"
            )
            self._append_sim_log(
                f"[info] Output plotted: {name} ({plot_type}) from raw Parametric results in {simdata_path}"
            )
            return

        if plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}:
            plane = str(plot_params.get("plane", "XY")).strip().upper() or "XY"
            polar_3d = bool(plot_params.get("polar_3d", False))
            theta = getattr(grid, "theta", None)
            phi = getattr(grid, "phi", None)
            ff_data = getattr(grid, "ff", None)
            if ff_data is None and hasattr(grid, "E"):
                ff_data = getattr(grid, "E")
            if ff_data is None and hasattr(grid, "farfield"):
                ff_data = getattr(grid, "farfield")
            if ff_data is None:
                mw_data = getattr(getattr(loaded_sim, "data", None), "mw", None)
                field_data = getattr(mw_data, "field", None)
                frequencies = np.asarray(getattr(grid, "freq", []))
                if field_data is not None and frequencies.size:
                    requested_frequency_ghz = float(plot_params.get("frequency_GHz", 0.0) or 0.0)
                    requested_frequency = requested_frequency_ghz * 1e9
                    if requested_frequency <= 0.0:
                        requested_frequency = (float(frequencies[0]) + float(frequencies[-1])) / 2.0
                    selected_frequency = float(
                        frequencies[int(np.argmin(np.abs(frequencies - requested_frequency)))]
                    )
                    field_entry = field_data.find(freq=selected_frequency)
                    boundary_geometries = [
                        geometry
                        for geometry in loaded_sim.all_geos()
                        if callable(getattr(geometry, "boundary", None))
                    ]
                    if not boundary_geometries:
                        raise RuntimeError(
                            "Far-field plotting requires at least one geometry with boundary faces."
                        )
                    faces = boundary_geometries[0].boundary()
                    for geometry in boundary_geometries[1:]:
                        faces = faces + geometry.boundary()
                    if plot_type == "plot_ff_3d":
                        farfield = field_entry.farfield_3d(faces)
                        display = loaded_sim.display
                        model_display_state = {
                            str(getattr(obj, "name", "")).strip(): (
                                bool(obj.is_visible()),
                                max(0.0, min(1.0, float(getattr(obj, "opacity", 0.85)))),
                            )
                            for obj in self._viewport.scene.objects
                            if bool(getattr(obj, "is_model", True))
                        }
                        for geometry in loaded_sim.all_geos():
                            geometry_name = str(getattr(geometry, "name", "")).strip()
                            source_name = geometry_name.split("_OCC", 1)[0].strip()
                            display_state = model_display_state.get(source_name)
                            if display_state is None:
                                display_state = model_display_state.get(geometry_name)
                            if display_state is not None:
                                _visible, source_opacity = display_state
                                display.add_object(
                                    geometry,
                                    opacity=source_opacity if _visible else 0.0,
                                )
                        mesh_nodes = np.asarray(getattr(loaded_sim.mesh, "nodes", []))
                        rmax = None
                        if mesh_nodes.ndim == 2 and mesh_nodes.shape[0] == 3 and mesh_nodes.shape[1]:
                            rmax = max(float(np.ptp(mesh_nodes, axis=1).max()) * 2.0, 1e-6)
                        display.add_farfield3d(
                            farfield,
                            component="normE",
                            quantity="abs",
                            dB=True,
                            dBfloor=-40,
                            rmax=rmax,
                            opacity=0.75,
                        )
                        display.show()
                        self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
                        self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")
                        return
                    else:
                        plane_axes = {
                            "XY": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
                            "XZ": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                            "YZ": ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
                        }
                        ref_direction, plane_normal = plane_axes.get(plane, plane_axes["XY"])
                        farfield = field_entry.farfield_2d(ref_direction, plane_normal, faces)
                        theta = farfield.ang
                    ff_data = farfield.gain.norm
            if ff_data is None:
                raise RuntimeError(
                    "This result does not contain saved E/H fields for far-field plotting. "
                    "Enable a far-field output and rerun the simulation."
                )

            value = ff_data
            if isinstance(ff_data, dict):
                for key in ("E", "field", "value", "magnitude", "Et", "Etheta", "Ephi"):
                    if key in ff_data:
                        value = ff_data[key]
                        break
                if isinstance(value, dict):
                    for key in ("theta", "phi", "ang", "angles"):
                        if key in value:
                            theta = value[key]
                            break

            theta_arr = np.asarray(theta) if theta is not None else None
            phi_arr = np.asarray(phi) if phi is not None else None
            values_arr = np.asarray(value)
            if values_arr.ndim > 1 and theta_arr is not None and values_arr.size == theta_arr.size:
                values_arr = values_arr.reshape(theta_arr.shape)
            if theta_arr is None and phi_arr is None and values_arr.ndim >= 1:
                theta_arr = np.linspace(0.0, 2.0 * np.pi, values_arr.size)

            if plot_type in {"plot_ff", "plot_ff_polar"}:
                if theta_arr is None or values_arr.size == 0:
                    raise RuntimeError("Far-field result does not expose angle and magnitude data for a polar plot.")
                self._show_chart(
                    f"{sim_name}::{name}",
                    {
                        "plot_type": plot_type,
                        "title": f"{name} - {plane} plane",
                        "xlabel": "Theta (rad)",
                        "ylabel": "Magnitude",
                        "x_values": np.asarray(theta_arr).reshape(-1),
                        "series": [{"label": name, "values": values_arr.reshape(-1)}],
                    },
                )
            self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
            self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")
            return

        freq = getattr(grid, "freq", None)
        if freq is None:
            QMessageBox.warning(self, "Output", "Loaded simdata has no frequency axis.")
            return
        ports = self._grid_port_numbers(grid)
        import re
        raw_parameters = plot_params.get("s_parameters", [])
        if not isinstance(raw_parameters, list) or not raw_parameters:
            raw_parameters = [plot_params.get("s_parameter", "S11")]
        selected_parameters = []
        for raw_parameter in raw_parameters:
            parameter = str(raw_parameter).strip().upper()
            match = re.fullmatch(r"S(\d+)[,:/_-]?(\d+)", parameter)
            if match:
                output_port = int(match.group(1))
                input_port = int(match.group(2))
            else:
                output_port = max(1, int(plot_params.get("port_i", 1)))
                input_port = max(1, int(plot_params.get("port_j", 1)))
                parameter = f"S{output_port}{input_port}"
            if (output_port, input_port, parameter) not in selected_parameters:
                selected_parameters.append((output_port, input_port, parameter))
        invalid = [parameter for output_port, input_port, parameter in selected_parameters
                   if output_port not in ports or input_port not in ports]
        if invalid:
            QMessageBox.warning(
                self,
                "Output",
                f"S-parameter(s) {', '.join(invalid)} are not available for this simulation ({len(ports)} port(s)).",
            )
            return
        try:
            if plot_type not in {"plot_sp", "plot_vswr", "smith", "plot"}:
                QMessageBox.warning(self, "Output", f"Unsupported plot type: {plot_type}")
                return
            port_curves = {
                (output_port, input_port): np.asarray(grid.S(output_port, input_port)).reshape(-1)
                for output_port in ports
                for input_port in ports
            }
            raw_matrices = [
                [
                    [port_curves[(output_port, input_port)][frequency_index] for input_port in ports]
                    for output_port in ports
                ]
                for frequency_index in range(len(freq))
            ]
            from .chart_data import make_sparameter_plot_data

            chart_data = make_sparameter_plot_data(
                {"name": name, "plot_type": plot_type, "params": plot_params},
                freq,
                raw_matrices,
                include_all_parameters=True,
                file_name=Path(simdata_path).name,
                file_id=f"{sim_name}::{name}",
            )
            self._show_chart(f"{sim_name}::{name}", chart_data)
        except Exception as exc:
            QMessageBox.warning(self, "Output", f"Failed to generate plot '{name}': {exc}")
            self._append_sim_log(f"[warn] Output '{name}' plot error: {exc}")
            return

        self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
        self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")

    def _plot_completed_simulation_outputs(self, simulation_name: str) -> None:
        settings = self._project_tree.get_settings()
        outputs = settings.get("outputs", []) if isinstance(settings, dict) else []
        simulations = settings.get("simulations", []) if isinstance(settings, dict) else []
        simulation_config = next(
            (
                item for item in simulations
                if isinstance(item, dict)
                and str(item.get("name", "")).strip() == simulation_name
            ),
            {},
        )
        try:
            candidates = self._candidate_results_dirs_for_sim(simulation_name)
            result_dir = next(
                (candidate for candidate in candidates if self._simdata_file_in_dir(candidate)),
                None,
            )
            has_parametric_touchstones = False
            if (
                result_dir is None
                and str(simulation_config.get("type", "")).strip().lower() == "parametric"
            ):
                from .touchstone import find_touchstones

                has_parametric_touchstones = bool(
                    find_touchstones(
                        self._simulation_bundle_dir(),
                        self._project_name,
                        simulation_name,
                    )
                )
            if result_dir is None and not has_parametric_touchstones:
                raise RuntimeError(
                    f"No EMERGE result data was found for '{simulation_name}' in {self._simulation_bundle_dir()}"
                )
        except Exception as exc:
            self._append_sim_log(f"[warn] Could not locate results for '{simulation_name}': {exc}")
            return

        for output in outputs:
            if not isinstance(output, dict) or not bool(output.get("enabled", True)):
                continue
            if str(output.get("simulation", "")).strip() != simulation_name:
                continue
            if str(output.get("plot_mode", "final")).strip().lower() == "live":
                chart_key = f"{simulation_name}::{output.get('name', 'Output')}"
                progressive_data = getattr(self, "_sim_progressive_plot_data", None)
                parametric_runs = (
                    progressive_data.get("parameter_runs", {})
                    if isinstance(progressive_data, dict)
                    else {}
                )
                if parametric_runs:
                    expected_parameters = int(progressive_data.get("expected_parameters", 0))
                    expected_samples = int(progressive_data.get("expected_samples", 0))
                    progressive_complete = (
                        len(parametric_runs) == expected_parameters
                        and expected_parameters > 0
                        and expected_samples > 0
                        and all(
                            len(run.get("frequencies", [])) == expected_samples
                            for run in parametric_runs.values()
                        )
                    )
                else:
                    progressive_complete = (
                        isinstance(progressive_data, dict)
                        and len(progressive_data.get("frequencies", []))
                        == int(progressive_data.get("expected", 0))
                        and int(progressive_data.get("expected", 0)) > 0
                    )
                if (
                    chart_key in getattr(self, "_plot_views", {})
                    and isinstance(progressive_data, dict)
                    and progressive_data.get("simulation") == simulation_name
                    and progressive_complete
                ):
                    continue
            payload = dict(output)
            try:
                self._on_output_plot_requested(payload, results_dir=result_dir)
            except Exception as exc:
                self._append_sim_log(
                    f"[warn] Final plot for '{output.get('name', 'Output')}' failed: {exc}"
                )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� camera views
    def _set_view(self, view: str) -> None:
        cam = self._viewport._renderer.GetActiveCamera()
        if view == "top":
            cam.SetPosition(0, 0, 300)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 1, 0)
        elif view == "bottom":
            cam.SetPosition(0, 0, -300)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 1, 0)
        elif view == "front":
            cam.SetPosition(0, -300, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "back":
            cam.SetPosition(0, 300, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "right":
            cam.SetPosition(300, 0, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "left":
            cam.SetPosition(-300, 0, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        else:  # iso
            cam.SetPosition(150, -200, 150)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        self._viewport.fit_all()

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� project file
    def _new_project(self) -> None:
        self._close_simulation_panel()
        self._project_name = "Untitled"
        self._project_path = None
        self._material_store = MaterialStore()
        if self._global_material_db_path:
            try:
                self._material_store.set_global_db_path(
                    self._global_material_db_path,
                    create_if_missing=False,
                )
            except (OSError, ValueError, TypeError) as exc:
                self._info_bar.set_info(f"Unable to load global material DB: {exc}")
        self._reset_simulation_cache()
        self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
        self._project_tree.set_project_name(self._project_name)
        self._project_tree.reset_settings()
        self._sync_project_variable_names()
        self._project_tree.set_runtime_settings({
            "solver": self._sim_solver,
            "parallel_enabled": self._sim_parallel_enabled,
            "pardiso_threads": self._sim_pardiso_threads,
            "acc_threads": self._sim_acc_threads,
            "plot_sparams_after_sim": self._sim_plot_sparams_after_sim,
            "export_sparams_after_sim": self._sim_export_sparams_after_sim,
        })
        self._sync_log_verbosity_from_settings()
        self._viewport.scene.clear()
        self._sync_active_reference_plane_to_viewport()
        self._body_props.set_object(None)
        self._sync_material_choices()
        self._sync_port_reference_state()
        self._refresh_materials()
        self._viewport._render()
        self._history_reset()
        self._info_bar.set_info("New project created.")

    def _close_simulation_panel(self) -> None:
        simulation_window = getattr(self, "_sim_subwindow", None)
        if simulation_window is None:
            return

        self._shutdown_simulation_process()
        self._workspace_windows.pop("Simulation", None)
        self._sim_log_level = None
        simulation_window.setAttribute(Qt.WA_DeleteOnClose, True)
        simulation_window.close()
        simulation_window.deleteLater()
        self._sim_subwindow = None
        self._sim_dlg = None
        self._rebuild_window_menu()

    def _close_project(self) -> None:
        has_project = self._project_path is not None or bool(self._viewport.scene.objects)
        if has_project:
            reply = QMessageBox.question(
                self,
                "Close Project",
                "Close the current project and discard any unsaved changes?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._new_project()
        self._info_bar.set_info("Project closed.")

    def _workspace_dialog_directory(self) -> str:
        configured_path = str(getattr(self, "_workspace_path", "")).strip()
        if not configured_path:
            return ""
        directory = Path(configured_path).expanduser()
        return str(directory.resolve()) if directory.is_dir() else ""

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            self._workspace_dialog_directory(),
            "EM3D Project (*.em3d);;All Files (*)",
        )
        if not path:
            return
        self._load_project_path(path)

    def _load_project_path(self, path: str) -> None:
        try:
            data = ProjectFile.load(path)
            loaded_name = str(data.get("project_name", "")).strip()
            self._project_name = loaded_name if loaded_name and loaded_name != "Untitled" else Path(path).stem
            self._project_path = str(Path(path).expanduser().resolve())
            self._reset_simulation_cache()
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
            self._project_tree.set_project_name(self._project_name)
            project_settings = data.get("emerge_settings", {})
            if not isinstance(project_settings, dict):
                project_settings = {}
            if not isinstance(project_settings.get("runtime"), dict):
                project_settings = dict(project_settings)
                project_settings["runtime"] = {
                    "solver": self._sim_solver,
                    "parallel_enabled": self._sim_parallel_enabled,
                    "pardiso_threads": self._sim_pardiso_threads,
                    "acc_threads": self._sim_acc_threads,
                    "plot_sparams_after_sim": self._sim_plot_sparams_after_sim,
                    "export_sparams_after_sim": self._sim_export_sparams_after_sim,
                }
            self._project_tree.load_settings(project_settings)
            runtime = self._project_tree.get_settings().get("runtime", {})
            solver = str(runtime.get("solver", self._sim_solver)).strip().upper()
            if solver in _EMERGE_SOLVERS:
                self._sim_solver = solver
            self._sim_parallel_enabled = bool(runtime.get("parallel_enabled", self._sim_parallel_enabled))
            self._sim_pardiso_threads = max(1, int(runtime.get("pardiso_threads", self._sim_pardiso_threads)))
            self._sim_acc_threads = max(1, int(runtime.get("acc_threads", self._sim_acc_threads)))
            self._sim_plot_sparams_after_sim = bool(runtime.get("plot_sparams_after_sim", self._sim_plot_sparams_after_sim))
            self._sim_export_sparams_after_sim = bool(runtime.get("export_sparams_after_sim", self._sim_export_sparams_after_sim))
            self._recompute_simulation_parameters()
            self._sync_log_verbosity_from_settings()
            self._viewport.scene.from_json(data.get("objects", []))
            self._viewport.scene.reference_planes_from_json(
                data.get("reference_planes", []),
                data.get("active_plane_name"),
            )
            self._material_store.load_project_materials(data.get("project_materials", []))
            project_global_db_path = data.get("global_material_db_path")
            selected_global_db_path = self._global_material_db_path or str(
                project_global_db_path or ""
            ).strip()
            try:
                self._material_store.set_global_db_path(
                    selected_global_db_path or None,
                    create_if_missing=False,
                )
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(
                    self,
                    "Global Material Database",
                    f"Unable to load {selected_global_db_path}: {exc}",
                )
            else:
                if not self._global_material_db_path and selected_global_db_path:
                    self._global_material_db_path = selected_global_db_path
                    self._save_app_settings()
            self._sync_material_choices()
            grid = data.get("grid", {})
            self._units = data.get("units", "mm")
            self._workspace_size = float(grid.get("size", 200.0))
            self._grid_spacing = float(grid.get("spacing", 10.0))

            ds = data.get("display_settings", {})
            if ds:
                self._apply_display_settings({
                    "units": ds.get("units", self._units),
                    "decimal_separator": ds.get("decimal_separator", self._decimal_separator),
                    "workspace_size": float(ds.get("workspace_size", self._workspace_size)),
                    "grid_size": float(ds.get("grid_size", self._grid_spacing)),
                    "plane_triad_size": float(ds.get("plane_triad_size", self._plane_triad_size)),
                    "selection_color": tuple(ds.get("selection_color", list(self._selection_color))),
                })

            self._sync_settings_dialog_values()

            loaded_plane = str(grid.get("plane", "")).upper().strip()
            if loaded_plane not in {"XY", "XZ", "YZ"}:
                loaded_plane = self._active_draw_plane_name()

            self._viewport.set_grid(
                self._workspace_size,
                self._grid_spacing,
                loaded_plane,
                self._units,
            )
            self._sync_active_reference_plane_to_viewport()
            default_plane = self._viewport.scene.active_plane.name if self._viewport.scene.active_plane is not None else loaded_plane
            plane_name = str(default_plane).split()[0].upper() if default_plane else loaded_plane
            if plane_name not in {"XY", "XZ", "YZ"}:
                plane_name = loaded_plane
            normal_map = {"XY": (0.0, 0.0, 1.0), "XZ": (0.0, 1.0, 0.0), "YZ": (1.0, 0.0, 0.0)}
            for obj in self._viewport.scene.objects:
                if obj.creation_plane == "XY" and obj.creation_plane_normal == (0.0, 0.0, 1.0):
                    obj.set_creation_plane(plane_name, (0.0, 0.0, 0.0), normal_map.get(plane_name, (0.0, 0.0, 1.0)))
            self._viewport.set_camera_state(data.get("camera", {}))
            self._body_props.set_object(None)
            self._sync_port_reference_state()
            self._refresh_materials()
            self._viewport._render()
            self._history_reset()
            self._close_all_plot_windows()
            self._info_bar.set_info(f"Project loaded: {path}")
            self._remember_recent_project(path)

        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _save_project(self) -> bool:
        if self._project_path is None:
            return self._save_project_as()
        return self._do_save(self._project_path)

    def _save_project_as(self) -> bool:
        filename = f"{self._project_name}.em3d"
        project_path = str(getattr(self, "_project_path", "") or "").strip()
        if project_path:
            initial_path = str(Path(project_path).expanduser().resolve().parent / filename)
        else:
            workspace_directory = self._workspace_dialog_directory()
            initial_path = str(Path(workspace_directory) / filename) if workspace_directory else filename
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", initial_path,
            "EM3D Project (*.em3d);;All Files (*)"
        )
        if not path:
            return False
        target_path = Path(path).expanduser().resolve()
        current_path = (
            Path(self._project_path).expanduser().resolve()
            if self._project_path
            else None
        )
        if target_path != current_path:
            self._reset_simulation_cache()
        self._project_path = str(target_path)
        self._project_name = Path(path).stem
        self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
        self._project_tree.set_project_name(self._project_name)
        return self._do_save(path)

    def _do_save(self, path: str) -> bool:
        try:
            self._project_name = Path(path).stem
            ProjectFile.save(
                path,
                project_name  = self._project_name,
                settings      = self._project_tree.get_settings(),
                objects_json  = self._viewport.scene.to_json(),
                units         = self._units,
                grid_size     = self._viewport.scene._grid_size,
                grid_spacing  = self._viewport.scene._grid_spacing,
                grid_plane    = self._viewport.scene._grid_plane,
                reference_planes = self._viewport.scene.reference_planes_to_json(),
                active_plane_name = (
                    self._viewport.scene.active_plane.name
                    if self._viewport.scene.active_plane is not None else None
                ),
                project_materials = self._material_store.project_materials_to_json(),
                global_material_db_path = self._material_store.global_db_path,
                camera = self._viewport.get_camera_state(),
                display_settings = {
                    "units": self._units,
                    "decimal_separator": self._decimal_separator,
                    "workspace_size": self._workspace_size,
                    "grid_size": self._grid_spacing,
                    "plane_triad_size": self._plane_triad_size,
                    "selection_color": list(self._selection_color),
                },
            )
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
            self._info_bar.set_info(f"Saved: {path}")
            self._remember_recent_project(path)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return False

    def _export_emerge(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export EMERGE Script",
            f"{self._project_name}.em",
            "EMERGE Script (*.em);;Text (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            script = export_emerge_script(
                project_name = self._project_name,
                settings     = self._project_tree.get_settings(),
                objects      = self._simulation_model_objects(),
                units        = self._units,
                materials_catalog = self._material_store.material_export_catalog(),
            )
            Path(path).write_text(script, encoding="utf-8")
            self._info_bar.set_info(f"EMERGE script exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _export_model_step(self) -> None:
        initial_path = f"{self._project_name}.step"
        project_path = str(getattr(self, "_project_path", "") or "").strip()
        if project_path:
            initial_path = str(Path(project_path).expanduser().resolve().parent / initial_path)
        else:
            workspace_directory = self._workspace_dialog_directory()
            if workspace_directory:
                initial_path = str(Path(workspace_directory) / initial_path)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Model as STEP",
            initial_path,
            "STEP Files (*.step *.stp);;All Files (*)",
        )
        if not path:
            return
        step_path = Path(path).expanduser().resolve()
        if step_path.suffix.lower() not in {".step", ".stp"}:
            step_path = step_path.with_suffix(".step")

        try:
            result = export_debug_scene_step(
                objects=self._viewport.scene.objects,
                step_path=step_path,
                mm_per_unit=_MM_PER_UNIT.get(getattr(self, "_units", "mm"), 1.0),
            )
            self._info_bar.set_info(
                f"STEP model exported: {step_path} ({result['exported']} object(s))"
            )
        except Exception as exc:
            QMessageBox.critical(self, "STEP Export Error", str(exc))

    def _set_global_material_db(self) -> None:
        suggested = self._material_store.global_db_path or str(Path.home() / "em3d_materials_global.json")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._set_global_material_db_path(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Global Material Database", f"Unable to load {path}: {exc}")

    def _set_global_material_db_path(self, path: str, *, persist: bool = True) -> None:
        normalized_path = str(path or "").strip()
        self._material_store.set_global_db_path(
            normalized_path or None,
            create_if_missing=False,
        )
        self._global_material_db_path = normalized_path
        if persist:
            self._save_app_settings()
        self._sync_material_choices()
        if normalized_path:
            self._info_bar.set_info(f"Global material DB: {normalized_path}")
        else:
            self._info_bar.set_info("Global material DB cleared")

    def _reload_global_material_db(self) -> None:
        suggested = self._material_store.global_db_path or str(Path.home() / "em3d_materials_global.json")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self._set_global_material_db_path(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Global Material Database", f"Unable to load {path}: {exc}")

    def _open_material_library_dialog(self) -> None:
        dlg = MaterialLibraryDialog(self, self._material_store, self._sync_material_choices)
        dlg.exec_()
        self._global_material_db_path = str(self._material_store.global_db_path or "").strip()
        self._save_app_settings()
        self._sync_material_choices()
        if dlg.changed:
            self._info_bar.set_info("Material library updated")

    def _sync_material_choices(self) -> None:
        names = self._material_store.all_project_names()
        self._body_props.set_materials(names)
        if self._draw_material not in names and names:
            self._draw_material = names[0]

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� close
    def _shutdown_simulation_process(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is None or proc.state() == QProcess.NotRunning:
            return
        self._on_sim_stop()
        if not proc.waitForFinished(3000) and proc.state() != QProcess.NotRunning:
            proc.kill()
            proc.waitForFinished(1000)

    def closeEvent(self, event) -> None:
        reply = QMessageBox.question(
            self, "Quit", "Save project before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        if reply == QMessageBox.Save:
            self._shutdown_simulation_process()
            self._save_project()
            self._save_app_settings()
            event.accept()
        elif reply == QMessageBox.Discard:
            self._shutdown_simulation_process()
            self._save_app_settings()
            event.accept()
        else:
            event.ignore()
