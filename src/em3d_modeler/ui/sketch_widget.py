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
from typing import List, Optional, Tuple

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QToolBar, QAction,
    QSplitter, QGroupBox, QFormLayout, QDoubleSpinBox,
    QLabel, QPushButton, QWidget, QSizePolicy,
    QDialogButtonBox, QComboBox, QColorDialog,
)
from PyQt5.QtCore import Qt, QPointF, QRectF, QSizeF, pyqtSignal
from PyQt5.QtGui  import QPainter, QPen, QBrush, QColor, QMouseEvent

Point2D = Tuple[float, float]


# ─────────────────────────────────────────────────── Canvas ─────────────────
class SketchCanvas(QWidget):
    """Simple 2-D drawing canvas inside the sketch dialog."""

    sketch_changed = pyqtSignal()

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
    revolve_requested(profile_pts, angle, axis_pt1, axis_pt2)
    """

    extrude_requested = pyqtSignal(list, float, tuple, tuple)
    revolve_requested = pyqtSignal(list, float, tuple, tuple)

    def __init__(self, parent=None,
                 plane_origin=(0.0, 0.0, 0.0),
                 plane_normal=(0.0, 0.0, 1.0)):
        super().__init__(parent)
        self.setWindowTitle("Parametric Sketch")
        self.setModal(False)           # allow interaction with 3D view
        self.resize(700, 560)

        self._origin = tuple(plane_origin)
        self._normal = tuple(plane_normal)


        # ── main layout ─────────────────────────────────────────────────────
        layout = QVBoxLayout(self)

        # ── canvas ───────────────────────────────────────────────────────────
        self._canvas = SketchCanvas()
        layout.addWidget(self._canvas, stretch=1)

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
        ext_group = QGroupBox("Extrude")
        ext_form  = QFormLayout(ext_group)
        self._ext_depth = QDoubleSpinBox()
        self._ext_depth.setRange(0.001, 1e6)
        self._ext_depth.setValue(10.0)
        self._ext_depth.setSuffix(" units")
        ext_form.addRow("Depth:", self._ext_depth)
        btn_ext = QPushButton("Create Extruded Body")
        btn_ext.clicked.connect(self._do_extrude)
        ext_form.addRow(btn_ext)
        ctrl_layout.addWidget(ext_group)

        # Revolve group
        rev_group = QGroupBox("Revolve")
        rev_form  = QFormLayout(rev_group)
        self._rev_angle = QDoubleSpinBox()
        self._rev_angle.setRange(1.0, 360.0)
        self._rev_angle.setValue(360.0)
        self._rev_angle.setSuffix(" °")
        rev_form.addRow("Sweep angle:", self._rev_angle)
        self._rev_axis = QComboBox()
        self._rev_axis.addItems(["Y axis (local)", "X axis (local)", "Z axis (local)"])
        rev_form.addRow("Revolution axis:", self._rev_axis)
        btn_rev = QPushButton("Create Revolved Body")
        btn_rev.clicked.connect(self._do_revolve)
        rev_form.addRow(btn_rev)
        ctrl_layout.addWidget(rev_group)

        layout.addWidget(ctrl_widget)

        # ── Close ─────────────────────────────────────────────────────────────
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    # ─────────────────────────────────────────────── actions
    def _do_extrude(self) -> None:
        profile = self._canvas.get_polyline()
        depth   = self._ext_depth.value()
        self.extrude_requested.emit(profile, depth, self._origin, self._normal)
        self.accept()

    def _do_revolve(self) -> None:
        profile = self._canvas.get_polyline()
        angle   = self._rev_angle.value()
        # axis: origin to origin+axis_dir
        axis_map = {
            "Y axis (local)": (0,1,0),
            "X axis (local)": (1,0,0),
            "Z axis (local)": (0,0,1),
        }
        ax = axis_map[self._rev_axis.currentText()]
        o  = self._origin
        self.revolve_requested.emit(
            profile, angle,
            o,
            (o[0]+ax[0], o[1]+ax[1], o[2]+ax[2])
        )
        self.accept()
