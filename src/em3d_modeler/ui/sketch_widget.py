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

"""Parametric 2-D Sketch widget.

Provides a simple 2-D canvas where the user can draw:
  • Lines (click start → click end)
  • Rectangles (click two opposite corners)
  • Circles / arcs (click centre → drag radius)
  • Freehand polyline

After sketching the user can either:
  • Extrude → creates an ExtrudedObject placed on the current reference plane
  • Revolve → creates a RevolvedObject revolving the profile around the Y axis

The result is emitted via the *extrude_requested* / *revolve_requested* signals
so the main window can add the new object to the scene.
"""
from __future__ import annotations
import math
from typing import Callable, List, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QToolBar,
    QSplitter, QGroupBox, QFormLayout,
    QLabel, QPushButton, QWidget, QSizePolicy,
    QDialogButtonBox, QComboBox, QColorDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QLineEdit, QMessageBox,
)
from .formula_widgets import FormulaDoubleSpinBox as QDoubleSpinBox
from PySide6.QtCore import Qt, QPointF, QRectF, QSizeF, Signal
from PySide6.QtGui  import QPainter, QPen, QBrush, QColor, QMouseEvent, QAction

Point2D = Tuple[float, float]


# ─────────────────────────────────────────────────── Canvas ─────────────────
class SketchCanvas(QWidget):
    """Simple 2-D drawing canvas inside the sketch dialog."""

    sketch_changed = Signal()

    # Each element: ("line"|"rect"|"circle"|"polyline", data)
    # line    : [(x1,y1),(x2,y2)]
    # rect    : [(x1,y1),(x2,y2)]
    # circle  : [(cx,cy), radius]
    # polyline: [(x0,y0),(x1,y1),...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._mode    = "polyline"   # current drawing tool
        self._elements: List = []    # finished elements
        self._current: List[Point2D] = []  # in-progress element
        self._cursor  = QPointF(0, 0)

        # View transform
        self._scale = 4.0            # pixels per unit
        self._origin = QPointF(200, 200)  # canvas origin in widget coords

        self._grid_spacing = 5.0     # grid in logical units
        self._snap_enabled = True

    # ── public API ───────────────────────────────────────────────────────────
    def set_tool(self, mode: str) -> None:
        self._mode = mode
        self._current = []

    def clear(self) -> None:
        self._elements.clear()
        self._current = []
        self.update()
        self.sketch_changed.emit()

    def undo(self) -> None:
        if self._elements:
            self._elements.pop()
            self.update()
            self.sketch_changed.emit()

    def get_polyline(self) -> List[Point2D]:
        """Return a flat ordered polyline from all elements (for extrude/revolve)."""
        pts: List[Point2D] = []
        for kind, data in self._elements:
            if kind == "line":
                pts.extend(data)
            elif kind == "rect":
                (x1,y1),(x2,y2) = data
                pts += [(x1,y1),(x2,y1),(x2,y2),(x1,y2),(x1,y1)]
            elif kind == "circle":
                (cx,cy),r = data
                N = 48
                for i in range(N+1):
                    a = 2*math.pi*i/N
                    pts.append((cx + r*math.cos(a), cy + r*math.sin(a)))
            elif kind == "polyline":
                pts.extend(data)
        if not pts:
            pts = [(0,0),(10,0),(10,10),(0,10),(0,0)]
        return pts

    def set_profile(self, profile_pts: List[Point2D]) -> bool:
        """Replace the sketch with an existing profile made of finite 2-D points."""
        try:
            points = [(float(x), float(y)) for x, y in profile_pts]
        except (TypeError, ValueError):
            return False
        if any(not math.isfinite(x) or not math.isfinite(y) for x, y in points):
            return False

        self._elements = [("polyline", points)] if points else []
        self._current = []
        self.update()
        self.sketch_changed.emit()
        return True

    @staticmethod
    def _element_points(kind: str, data: List) -> List[Point2D]:
        if kind == "rect":
            (x1, y1), (x2, y2) = data
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        if kind == "circle":
            (cx, cy), radius = data
            return [
                (cx + radius * math.cos(2 * math.pi * i / 48),
                 cy + radius * math.sin(2 * math.pi * i / 48))
                for i in range(49)
            ]
        return list(data)

    def get_segments(self) -> List[Tuple[int, int, Point2D, Point2D, float]]:
        """Return (element, segment, start, end, measured length) entries."""
        segments = []
        for element_index, (kind, data) in enumerate(self._elements):
            points = self._element_points(kind, data)
            for segment_index, (start, end) in enumerate(zip(points, points[1:])):
                segments.append((
                    element_index, segment_index, start, end,
                    math.hypot(end[0] - start[0], end[1] - start[1]),
                ))
        return segments

    def set_segment_length(self, element_index: int, segment_index: int,
                           target_length: float) -> bool:
        """Set a segment length and translate its downstream vertices by its endpoint delta."""
        try:
            target_length = float(target_length)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(target_length) or target_length <= 0:
            return False
        if not 0 <= element_index < len(self._elements):
            return False

        kind, data = self._elements[element_index]
        points = self._element_points(kind, data)
        if not 0 <= segment_index < len(points) - 1:
            return False

        start = points[segment_index]
        old_end = points[segment_index + 1]
        dx, dy = old_end[0] - start[0], old_end[1] - start[1]
        old_length = math.hypot(dx, dy)
        if old_length == 0 or not math.isfinite(old_length):
            return False

        new_end = (
            start[0] + (dx / old_length) * target_length,
            start[1] + (dy / old_length) * target_length,
        )
        delta = (new_end[0] - old_end[0], new_end[1] - old_end[1])
        updated_points = list(points)
        for point_index in range(segment_index + 1, len(updated_points)):
            point = updated_points[point_index]
            updated_points[point_index] = (point[0] + delta[0], point[1] + delta[1])
        if any(not math.isfinite(x) or not math.isfinite(y)
               for x, y in updated_points):
            return False

        self._elements[element_index] = ("polyline", updated_points)
        self.update()
        self.sketch_changed.emit()
        return True

    # ── coordinate helpers ───────────────────────────────────────────────────
    def _to_world(self, widget_pt: QPointF) -> Point2D:
        x = (widget_pt.x() - self._origin.x()) / self._scale
        y = -(widget_pt.y() - self._origin.y()) / self._scale
        return (x, y)

    def _to_widget(self, world_pt: Point2D) -> QPointF:
        return QPointF(
            self._origin.x() + world_pt[0] * self._scale,
            self._origin.y() - world_pt[1] * self._scale,
        )

    def _snap(self, pt: Point2D) -> Point2D:
        if not self._snap_enabled:
            return pt
        s = self._grid_spacing
        return (round(pt[0]/s)*s, round(pt[1]/s)*s)

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Background
        p.fillRect(self.rect(), QColor(30, 30, 40))

        # Grid
        self._draw_grid(p)

        # Axes
        p.setPen(QPen(QColor(80, 80, 220), 1.5))
        ox, oy = int(self._origin.x()), int(self._origin.y())
        p.drawLine(0, oy, self.width(), oy)
        p.drawLine(ox, 0, ox, self.height())

        # Finished elements
        p.setPen(QPen(QColor(100, 220, 100), 1.5))
        p.setBrush(Qt.NoBrush)
        self._paint_elements(p, self._elements)

        # In-progress element
        if self._current:
            p.setPen(QPen(QColor(255, 200, 50), 1.0, Qt.DashLine))
            self._paint_inprogress(p)

    def _draw_grid(self, p: QPainter) -> None:
        p.setPen(QPen(QColor(55, 55, 65), 1))
        s = self._grid_spacing * self._scale
        if s < 4:
            return
        x = self._origin.x() % s
        while x < self.width():
            p.drawLine(int(x), 0, int(x), self.height())
            x += s
        y = self._origin.y() % s
        while y < self.height():
            p.drawLine(0, int(y), self.width(), int(y))
            y += s

    def _paint_elements(self, p: QPainter, elements: List) -> None:
        for kind, data in elements:
            if kind == "line":
                (x1,y1),(x2,y2) = data
                a, b = self._to_widget((x1,y1)), self._to_widget((x2,y2))
                p.drawLine(a, b)
            elif kind == "rect":
                (x1,y1),(x2,y2) = data
                tl = self._to_widget((min(x1,x2), max(y1,y2)))
                br = self._to_widget((max(x1,x2), min(y1,y2)))
                p.drawRect(QRectF(tl, br))
            elif kind == "circle":
                (cx,cy),r = data
                c = self._to_widget((cx,cy))
                rw = r * self._scale
                p.drawEllipse(c, rw, rw)
            elif kind == "polyline" and len(data) > 1:
                pts = [self._to_widget(pt) for pt in data]
                for i in range(len(pts)-1):
                    p.drawLine(pts[i], pts[i+1])

    def _paint_inprogress(self, p: QPainter) -> None:
        if self._mode == "polyline" and len(self._current) >= 1:
            pts = [self._to_widget(pt) for pt in self._current]
            pts.append(self._cursor)
            for i in range(len(pts)-1):
                p.drawLine(pts[i], pts[i+1])
        elif self._mode == "line" and len(self._current) == 1:
            p.drawLine(self._to_widget(self._current[0]), self._cursor)
        elif self._mode == "rect" and len(self._current) == 1:
            tl = self._to_widget(self._current[0])
            p.drawRect(QRectF(tl, self._cursor))
        elif self._mode == "circle" and len(self._current) == 1:
            c = self._to_widget(self._current[0])
            r = math.hypot(self._cursor.x()-c.x(), self._cursor.y()-c.y())
            p.drawEllipse(c, r, r)

    # ── events ────────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        self._cursor = ev.pos()
        self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() != Qt.LeftButton:
            return
        wp = self._snap(self._to_world(QPointF(ev.pos())))

        if self._mode == "polyline":
            if ev.modifiers() & Qt.ShiftModifier:
                # Shift+click → close / finish polyline
                if len(self._current) >= 2:
                    self._elements.append(("polyline", list(self._current)))
                    self._current = []
                    self.sketch_changed.emit()
            else:
                self._current.append(wp)
        elif self._mode == "line":
            self._current.append(wp)
            if len(self._current) == 2:
                self._elements.append(("line", list(self._current)))
                self._current = []
                self.sketch_changed.emit()
        elif self._mode == "rect":
            self._current.append(wp)
            if len(self._current) == 2:
                self._elements.append(("rect", list(self._current)))
                self._current = []
                self.sketch_changed.emit()
        elif self._mode == "circle":
            self._current.append(wp)
            if len(self._current) == 2:
                r = math.hypot(wp[0]-self._current[0][0],
                                wp[1]-self._current[0][1])
                self._elements.append(("circle", [self._current[0], r]))
                self._current = []
                self.sketch_changed.emit()
        self.update()

    def wheelEvent(self, ev) -> None:
        factor = 1.15 if ev.angleDelta().y() > 0 else 1.0/1.15
        self._scale = max(0.5, min(50.0, self._scale * factor))
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:
        # Double-click → finish polyline
        if self._mode == "polyline" and len(self._current) >= 2:
            self._elements.append(("polyline", list(self._current)))
            self._current = []
            self.sketch_changed.emit()
            self.update()


# ─────────────────────────────────────────────── SketchDialog ─────────────────
class SketchDialog(QDialog):
    """Full sketch + extrude / revolve dialog.

    Signals
    -------
    extrude_requested(profile_pts, depth, origin, normal)
    revolve_requested(profile_pts, angle, axis_pt1, axis_pt2, plane_origin, plane_normal)
    """

    extrude_requested = Signal(list, float, tuple, tuple)
    revolve_requested = Signal(list, float, tuple, tuple, tuple, tuple)

    def __init__(self, parent=None,
                 plane_origin=(0.0, 0.0, 0.0),
                 plane_normal=(0.0, 0.0, 1.0),
                 profile_pts: Optional[List[Point2D]] = None,
                 operation_mode: Optional[str] = None,
                 extrusion_depth: Optional[float] = None,
                 revolve_angle: Optional[float] = None,
                 revolve_axis_pt1: Optional[Tuple[float, float, float]] = None,
                 revolve_axis_pt2: Optional[Tuple[float, float, float]] = None):
        super().__init__(parent)
        self.setWindowTitle("Parametric Sketch")
        self.setModal(False)           # allow interaction with 3D view
        self.resize(900, 600)

        self._origin = tuple(plane_origin)
        self._normal = tuple(plane_normal)
        self._edit_apply_callback: Optional[Callable[..., bool]] = None


        # ── main layout ─────────────────────────────────────────────────────
        layout = QVBoxLayout(self)

        # ── canvas and segment editor ────────────────────────────────────────
        canvas_splitter = QSplitter(Qt.Horizontal)
        self._canvas = SketchCanvas()
        canvas_splitter.addWidget(self._canvas)

        segment_group = QGroupBox("Segment lengths")
        segment_layout = QVBoxLayout(segment_group)
        self._segment_table = QTableWidget(0, 2)
        self._segment_table.setHorizontalHeaderLabels(["Segment", "Length"])
        self._segment_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._segment_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._segment_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._segment_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._segment_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        segment_layout.addWidget(self._segment_table, stretch=1)
        edit_layout = QHBoxLayout()
        self._segment_length = QLineEdit()
        self._segment_length.setPlaceholderText("Exact length")
        self._segment_length.setToolTip("Enter a finite length greater than zero")
        self._apply_segment_length = QPushButton("Set Length")
        edit_layout.addWidget(self._segment_length, stretch=1)
        edit_layout.addWidget(self._apply_segment_length)
        segment_layout.addLayout(edit_layout)
        self._segment_status = QLabel("")
        segment_layout.addWidget(self._segment_status)
        canvas_splitter.addWidget(segment_group)
        canvas_splitter.setStretchFactor(0, 1)
        canvas_splitter.setStretchFactor(1, 0)
        layout.addWidget(canvas_splitter, stretch=1)

        # ── toolbar ──────────────────────────────────────────────────────────
        tb = QToolBar()
        for label, mode, tip in [
            ("Line",      "line",      "Draw line (click start, click end)"),
            ("Rectangle", "rect",      "Draw rectangle (two opposite corners)"),
            ("Circle",    "circle",    "Draw circle (click centre, click edge)"),
            ("Polyline",  "polyline",  "Draw polyline (click vertices, Shift+click or dbl-click to close)"),
        ]:
            act = QAction(label, self)
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, m=mode: self._canvas.set_tool(m))
            tb.addAction(act)
        tb.addSeparator()
        act_undo  = QAction("Undo",  self);  act_undo.triggered.connect(self._canvas.undo)
        act_clear = QAction("Clear", self); act_clear.triggered.connect(self._canvas.clear)
        tb.addAction(act_undo)
        tb.addAction(act_clear)
        layout.insertWidget(0, tb)

        # ── Extrude / Revolve controls ────────────────────────────────────────
        ctrl_widget = QWidget()
        ctrl_layout = QHBoxLayout(ctrl_widget)

        # Extrude group
        self._ext_group = QGroupBox("Extrude")
        ext_form  = QFormLayout(self._ext_group)
        self._ext_depth = QDoubleSpinBox()
        self._ext_depth.setRange(1e-12, 1e100)
        self._ext_depth.setDecimals(12)
        self._ext_depth.setValue(10.0)
        self._ext_depth.setSuffix(" units")
        ext_form.addRow("Depth:", self._ext_depth)
        self._btn_ext = QPushButton("Create Extruded Body")
        self._btn_ext.clicked.connect(self._do_extrude)
        ext_form.addRow(self._btn_ext)
        ctrl_layout.addWidget(self._ext_group)

        # Revolve group
        rev_group = QGroupBox("Revolve")
        rev_form  = QFormLayout(rev_group)
        self._rev_angle = QDoubleSpinBox()
        self._rev_angle.setRange(1e-6, 360.0)
        self._rev_angle.setDecimals(12)
        self._rev_angle.setValue(360.0)
        self._rev_angle.setSuffix(" °")
        rev_form.addRow("Sweep angle:", self._rev_angle)
        self._rev_axis = QComboBox()
        self._rev_axis.addItems([
            "Y axis (local)", "X axis (local)", "Z axis (local)",
            "-Y axis (local)", "-X axis (local)", "-Z axis (local)",
            "Custom axis",
        ])
        rev_form.addRow("Revolution axis:", self._rev_axis)
        self._axis_fields = []
        axis_start_row = QHBoxLayout()
        axis_end_row = QHBoxLayout()
        for row, fields in ((axis_start_row, self._axis_fields), (axis_end_row, self._axis_fields)):
            for axis_label in ("X", "Y", "Z"):
                row.addWidget(QLabel(axis_label))
                field = QDoubleSpinBox()
                field.setRange(-1e9, 1e9)
                field.setDecimals(12)
                row.addWidget(field)
                fields.append(field)
        rev_form.addRow("Axis start:", axis_start_row)
        rev_form.addRow("Axis end:", axis_end_row)
        self._btn_rev = QPushButton("Create Revolved Body")
        self._btn_rev.clicked.connect(self._do_revolve)
        rev_form.addRow(self._btn_rev)
        self._rev_group = rev_group
        ctrl_layout.addWidget(self._rev_group)
        self._rev_axis.currentTextChanged.connect(self._on_revolve_axis_preset_changed)

        layout.addWidget(ctrl_widget)

        # ── Close ─────────────────────────────────────────────────────────────
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._segment_rows: List[Tuple[int, int]] = []
        self._segment_table.itemSelectionChanged.connect(
            self._on_segment_selection_changed
        )
        self._apply_segment_length.clicked.connect(self._apply_selected_segment_length)
        self._canvas.sketch_changed.connect(self._refresh_segment_table)
        self._refresh_segment_table()
        if profile_pts is not None:
            self.set_profile(profile_pts)
        axis_start = revolve_axis_pt1 or self._origin
        axis_end = revolve_axis_pt2 or (axis_start[0], axis_start[1] + 1.0, axis_start[2])
        self._set_revolve_axis_points(axis_start, axis_end)
        if extrusion_depth is not None:
            self._ext_depth.setValue(float(extrusion_depth))
        if revolve_angle is not None:
            self._rev_angle.setValue(float(revolve_angle))
        if revolve_axis_pt1 is not None and revolve_axis_pt2 is not None:
            self._select_axis_preset(revolve_axis_pt1, revolve_axis_pt2)
        else:
            self._select_axis_preset(axis_start, axis_end)
        if operation_mode in {"extrude", "revolve"}:
            self.setWindowTitle("Edit Extrude Operation" if operation_mode == "extrude" else "Edit Revolve Operation")
            self._ext_group.setVisible(operation_mode == "extrude")
            self._rev_group.setVisible(operation_mode == "revolve")
            self._btn_ext.setText("Update Extrusion")
            self._btn_rev.setText("Update Revolution")

    def set_profile(self, profile_pts: List[Point2D]) -> bool:
        """Load a 2-D profile into the dialog for editing or extrusion/revolution."""
        return self._canvas.set_profile(profile_pts)

    def set_edit_apply_callback(self, callback: Callable[..., bool]) -> None:
        """Set the synchronous updater used instead of creation signals in edit mode."""
        self._edit_apply_callback = callback

    def _refresh_segment_table(self) -> None:
        selected_row = self._segment_table.currentRow()
        segments = self._canvas.get_segments()
        self._segment_rows = [(item[0], item[1]) for item in segments]
        self._segment_table.setRowCount(len(segments))
        for row, (element_index, segment_index, _start, _end, length) in enumerate(segments):
            self._segment_table.setItem(
                row, 0,
                QTableWidgetItem(
                    f"Element {element_index + 1} / Segment {segment_index + 1}"
                ),
            )
            self._segment_table.setItem(row, 1, QTableWidgetItem(f"{length:.12g}"))
        if 0 <= selected_row < len(segments):
            self._segment_table.selectRow(selected_row)

    def _on_segment_selection_changed(self) -> None:
        row = self._segment_table.currentRow()
        if 0 <= row < len(self._segment_rows):
            self._segment_length.setText(self._segment_table.item(row, 1).text())
            self._segment_status.clear()

    def _apply_selected_segment_length(self) -> None:
        row = self._segment_table.currentRow()
        if not 0 <= row < len(self._segment_rows):
            self._segment_status.setText("Select a segment first.")
            return
        try:
            target_length = float(self._segment_length.text().strip())
        except ValueError:
            target_length = math.nan
        if not math.isfinite(target_length) or target_length <= 0:
            self._segment_status.setText("Enter a finite length greater than zero.")
            return

        element_index, segment_index = self._segment_rows[row]
        if not self._canvas.set_segment_length(
                element_index, segment_index, target_length):
            self._segment_status.setText(
                "This segment cannot be resized (it may have zero length)."
            )

    def _set_revolve_axis_points(self, start, end) -> None:
        for field, value in zip(self._axis_fields, (*start, *end)):
            field.setValue(float(value))

    def _select_axis_preset(self, start, end) -> None:
        direction = tuple(float(end[index]) - float(start[index]) for index in range(3))
        magnitude = math.hypot(*direction)
        presets = {
            "Y axis (local)": (0.0, 1.0, 0.0),
            "X axis (local)": (1.0, 0.0, 0.0),
            "Z axis (local)": (0.0, 0.0, 1.0),
            "-Y axis (local)": (0.0, -1.0, 0.0),
            "-X axis (local)": (-1.0, 0.0, 0.0),
            "-Z axis (local)": (0.0, 0.0, -1.0),
        }
        selected = "Custom axis"
        if magnitude > 0.0:
            unit = tuple(value / magnitude for value in direction)
            for label, preset in presets.items():
                if all(abs(unit[index] - preset[index]) <= 1e-6 for index in range(3)):
                    selected = label
                    break
        self._rev_axis.blockSignals(True)
        self._rev_axis.setCurrentText(selected)
        self._rev_axis.blockSignals(False)

    def _on_revolve_axis_preset_changed(self, text: str) -> None:
        direction_map = {
            "Y axis (local)": (0, 1, 0),
            "X axis (local)": (1, 0, 0),
            "Z axis (local)": (0, 0, 1),
            "-Y axis (local)": (0, -1, 0),
            "-X axis (local)": (-1, 0, 0),
            "-Z axis (local)": (0, 0, -1),
        }
        direction = direction_map.get(text)
        if direction is None:
            return
        start = tuple(self._axis_fields[index].value() for index in range(3))
        end = tuple(start[index] + direction[index] for index in range(3))
        self._set_revolve_axis_points(start, end)

    # ─────────────────────────────────────────────── actions
    def _do_extrude(self) -> None:
        profile = self._canvas.get_polyline()
        depth   = self._ext_depth.value()
        if self._edit_apply_callback is not None:
            if self._edit_apply_callback(profile, depth, self._origin, self._normal):
                self.accept()
            return
        self.extrude_requested.emit(profile, depth, self._origin, self._normal)
        self.accept()

    def _do_revolve(self) -> None:
        profile = self._canvas.get_polyline()
        angle   = self._rev_angle.value()
        axis_pt1 = tuple(field.value() for field in self._axis_fields[:3])
        axis_pt2 = tuple(field.value() for field in self._axis_fields[3:])
        if math.hypot(*(axis_pt2[index] - axis_pt1[index] for index in range(3))) <= 1e-12:
            QMessageBox.warning(self, "Revolve", "Revolution axis endpoints must be different.")
            return
        if self._edit_apply_callback is not None:
            if self._edit_apply_callback(
                    profile, angle, axis_pt1, axis_pt2, self._origin, self._normal):
                self.accept()
            return
        self.revolve_requested.emit(
            profile, angle, axis_pt1, axis_pt2, self._origin, self._normal
        )
        self.accept()
