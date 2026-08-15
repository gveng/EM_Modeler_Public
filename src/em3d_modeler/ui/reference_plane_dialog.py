"""Reference Plane dialog.

Allows the user to define a custom drawing plane by:
  1. Picking an existing Face (normal taken from face)
  2. Picking a Vertex + Face  (origin = vertex, normal from face)
  3. Custom offset (X/Y/Z) and normal-axis orientation with rotation

The result is a (origin, normal) pair that is forwarded to the viewport.
"""
from __future__ import annotations
from typing import Optional, Tuple

import math

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton,
    QDoubleSpinBox, QLabel, QPushButton, QButtonGroup, QFormLayout,
    QSizePolicy, QDialogButtonBox, QComboBox, QLineEdit,
)
from PySide6.QtCore import Qt, Signal


Vec3 = Tuple[float, float, float]


class ReferencePlaneDialog(QDialog):
    """Configure the active drawing reference plane.

    Signals
    -------
    plane_defined(origin, normal) : tuple, tuple
        Emitted when the user confirms a plane.
    """

    plane_defined = Signal(tuple, tuple, str)

    def __init__(self, parent=None,
                 current_origin: Vec3 = (0, 0, 0),
                 current_normal: Vec3 = (0, 0, 1),
                 viewport=None):
        super().__init__(parent)
        self.setWindowTitle("Reference Plane")
        # Non-modal so user can interact with the 3D viewport while picking
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.resize(380, 520)

        self._viewport = viewport
        self._origin = list(current_origin)
        self._normal = list(current_normal)
        # Storage for 3-point plane definition
        self._three_pts: list = []

        layout = QVBoxLayout(self)

        # ── Name ────────────────────────────────────────────────
        name_box = QGroupBox("Plane name")
        name_layout = QHBoxLayout(name_box)
        self._name_edit = QLineEdit("Plane")
        self._name_edit.setPlaceholderText("Will appear under 'Reference Planes' in the right panel")
        name_layout.addWidget(self._name_edit)
        layout.addWidget(name_box)

        # ── Preset axis ────────────────────────────────────────────
        preset_group = QGroupBox("Preset axis-aligned planes")
        preset_form  = QHBoxLayout(preset_group)
        for label, nm in [("XY (Z=0)", (0,0,1)), ("XZ (Y=0)", (0,1,0)), ("YZ (X=0)", (1,0,0))]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, n=nm: self._set_preset(n))
            preset_form.addWidget(btn)
        layout.addWidget(preset_group)
        # ── Pick from 3D viewport ──────────────────────────────────
        pick_group = QGroupBox("Pick from 3D viewport")
        pick_layout = QVBoxLayout(pick_group)

        row1 = QHBoxLayout()
        self._btn_pick_origin = QPushButton("\u2316 Pick origin (vertex)")
        self._btn_pick_origin.setToolTip("Click on a vertex of an existing object to set the plane origin.")
        self._btn_pick_origin.clicked.connect(self._pick_origin)
        row1.addWidget(self._btn_pick_origin)

        self._btn_pick_point = QPushButton("\u2316 Pick origin (any point)")
        self._btn_pick_point.setToolTip("Click anywhere on existing geometry to set the plane origin.")
        self._btn_pick_point.clicked.connect(self._pick_point_origin)
        row1.addWidget(self._btn_pick_point)
        pick_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._btn_pick_normal = QPushButton("\u2316 Pick normal from face")
        self._btn_pick_normal.setToolTip("Click on a face to take its outward normal as the plane normal.")
        self._btn_pick_normal.clicked.connect(self._pick_face_normal)
        row2.addWidget(self._btn_pick_normal)

        self._btn_pick_face = QPushButton("\u2316 Pick face (origin + normal)")
        self._btn_pick_face.setToolTip("Click on a face: origin will be set to the click position, normal to the face normal.")
        self._btn_pick_face.clicked.connect(self._pick_face_full)
        row2.addWidget(self._btn_pick_face)
        pick_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._btn_pick_3pts = QPushButton("\u2316 Pick 3 points to define plane")
        self._btn_pick_3pts.setToolTip("Click three points on existing geometry; the plane is fitted through them.")
        self._btn_pick_3pts.clicked.connect(self._pick_three_points)
        row3.addWidget(self._btn_pick_3pts)
        pick_layout.addLayout(row3)

        if self._viewport is None:
            for b in (self._btn_pick_origin, self._btn_pick_point,
                      self._btn_pick_normal, self._btn_pick_face,
                      self._btn_pick_3pts):
                b.setEnabled(False)
                b.setToolTip("3D viewport not available.")
        layout.addWidget(pick_group)
        # ── Custom origin ──────────────────────────────────────────
        origin_group = QGroupBox("Plane origin (X, Y, Z)")
        origin_form  = QFormLayout(origin_group)
        self._ox = self._make_spin(current_origin[0])
        self._oy = self._make_spin(current_origin[1])
        self._oz = self._make_spin(current_origin[2])
        origin_form.addRow("X:", self._ox)
        origin_form.addRow("Y:", self._oy)
        origin_form.addRow("Z:", self._oz)
        layout.addWidget(origin_group)

        # ── Custom normal ──────────────────────────────────────────
        normal_group = QGroupBox("Plane normal (direction)")
        normal_form  = QFormLayout(normal_group)
        self._nx = self._make_spin(current_normal[0], lo=-1.0, hi=1.0, step=0.01)
        self._ny = self._make_spin(current_normal[1], lo=-1.0, hi=1.0, step=0.01)
        self._nz = self._make_spin(current_normal[2], lo=-1.0, hi=1.0, step=0.01)
        normal_form.addRow("NX:", self._nx)
        normal_form.addRow("NY:", self._ny)
        normal_form.addRow("NZ:", self._nz)
        layout.addWidget(normal_group)

        # ── Rotation offset ─────────────────────────────────────────
        rot_group = QGroupBox("In-plane rotation offset (degrees)")
        rot_form  = QFormLayout(rot_group)
        self._rot_spin = self._make_spin(0.0, lo=-360, hi=360, step=5)
        self._rot_axis_combo = QComboBox()
        self._rot_axis_combo.addItems(["Normal axis", "X axis", "Y axis", "Z axis"])
        rot_form.addRow("Rotation:", self._rot_spin)
        rot_form.addRow("Around:",  self._rot_axis_combo)
        layout.addWidget(rot_group)

        # ── Buttons ────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ─────────────────────────────────────── helpers
    @staticmethod
    def _make_spin(value: float, lo: float = -10000, hi: float = 10000,
                   step: float = 1.0) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(4)
        sp.setValue(value)
        return sp

    def _set_preset(self, normal: tuple) -> None:
        self._nx.setValue(normal[0])
        self._ny.setValue(normal[1])
        self._nz.setValue(normal[2])

    # ─────────────────────────────────────────────────── 3D pick handlers
    def _arm_pick(self, kind: str, callback) -> None:
        if self._viewport is None:
            return
        # Hide the dialog while the user clicks in the 3D viewport
        self.hide()
        self._viewport.request_pick(kind, callback)

    def _set_origin(self, pt) -> None:
        self._ox.setValue(float(pt[0]))
        self._oy.setValue(float(pt[1]))
        self._oz.setValue(float(pt[2]))

    def _set_normal(self, n) -> None:
        self._nx.setValue(float(n[0]))
        self._ny.setValue(float(n[1]))
        self._nz.setValue(float(n[2]))

    def _pick_origin(self) -> None:
        def cb(pt):
            self._set_origin(pt)
            self.show(); self.raise_(); self.activateWindow()
        self._arm_pick("vertex", cb)

    def _pick_point_origin(self) -> None:
        def cb(pt):
            self._set_origin(pt)
            self.show(); self.raise_(); self.activateWindow()
        self._arm_pick("point", cb)

    def _pick_face_normal(self) -> None:
        def cb(n):
            self._set_normal(n)
            self.show(); self.raise_(); self.activateWindow()
        self._arm_pick("face_normal", cb)

    def _pick_face_full(self) -> None:
        def cb(pt, n):
            self._set_origin(pt)
            self._set_normal(n)
            self.show(); self.raise_(); self.activateWindow()
        self._arm_pick("face_origin_normal", cb)

    def _pick_three_points(self) -> None:
        self._three_pts = []
        self._collect_next_three_point()

    def _collect_next_three_point(self) -> None:
        idx = len(self._three_pts) + 1
        def cb(pt):
            self._three_pts.append(pt)
            if len(self._three_pts) < 3:
                # Brief flash of the dialog so the user sees progress, then re-arm
                self.show(); self.raise_(); self.activateWindow()
                # Re-arm immediately (without waiting for a new button click)
                self._collect_next_three_point()
            else:
                p1, p2, p3 = self._three_pts
                e1 = (p2[0]-p1[0], p2[1]-p1[1], p2[2]-p1[2])
                e2 = (p3[0]-p1[0], p3[1]-p1[1], p3[2]-p1[2])
                nx = e1[1]*e2[2] - e1[2]*e2[1]
                ny = e1[2]*e2[0] - e1[0]*e2[2]
                nz = e1[0]*e2[1] - e1[1]*e2[0]
                mag = math.sqrt(nx*nx + ny*ny + nz*nz)
                if mag < 1e-9:
                    self.show(); self.raise_(); self.activateWindow()
                    return
                self._set_origin(p1)
                self._set_normal((nx/mag, ny/mag, nz/mag))
                self._three_pts = []
                self.show(); self.raise_(); self.activateWindow()
        self._arm_pick("point", cb)

    # ─────────────────────────────────────── slots
    def _accept(self) -> None:
        origin = (self._ox.value(), self._oy.value(), self._oz.value())
        nx, ny, nz = self._nx.value(), self._ny.value(), self._nz.value()
        mag = math.sqrt(nx*nx + ny*ny + nz*nz)
        if mag < 1e-6:
            # Default to Z-normal
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx/mag, ny/mag, nz/mag

        # Apply in-plane rotation if requested
        angle_deg = self._rot_spin.value()
        if abs(angle_deg) > 1e-4:
            rot_axis = self._rot_axis_combo.currentText()
            nx, ny, nz = self._rotate_normal(nx, ny, nz, angle_deg, rot_axis)

        self.plane_defined.emit(origin, (nx, ny, nz), self._name_edit.text().strip() or "Plane")
        self.accept()

    @staticmethod
    def _rotate_normal(nx, ny, nz, angle_deg, axis_name):
        """Rotate the normal vector by *angle_deg* around the chosen axis."""
        import math
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        if axis_name == "X axis":
            ny2 = ny*ca - nz*sa
            nz2 = ny*sa + nz*ca
            return nx, ny2, nz2
        elif axis_name == "Y axis":
            nx2 = nx*ca + nz*sa
            nz2 = -nx*sa + nz*ca
            return nx2, ny, nz2
        elif axis_name == "Z axis":
            nx2 = nx*ca - ny*sa
            ny2 = nx*sa + ny*ca
            return nx2, ny2, nz
        else:
            # Rotate around the normal itself → no-op (it stays the same)
            return nx, ny, nz
