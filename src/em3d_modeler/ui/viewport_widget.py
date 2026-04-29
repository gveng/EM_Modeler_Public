"""3D Viewport widget: VTK + drawing engine with grid snap and live preview.

Drawing modes
─────────────
  box       : click1 = first corner on plane,  click2 = opposite corner on plane,
              move   = height preview,          click3 = finalise
  cylinder  : click1 = BASE centre on plane,   move   = radius preview,
              click2 = set radius,              move   = height preview upward,
              click3 = finalise
  cone      : same as cylinder (base centre, then radius, then height)
  sphere    : click1 = centre on plane,         move   = radius preview,
              click2 = finalise

Mouse controls (always active, even during drawing)
────────────────────────────────────────────────────
  Middle button drag → Rotate
  Right  button drag → Pan
  Scroll wheel       → Zoom
  Left   click       → drawing / selection step
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import vtk
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt5.QtCore    import pyqtSignal, Qt

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except ImportError:
    from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from ..drawing.interactor_style import EMInteractorStyle
from ..scene.scene_manager      import SceneManager
from ..scene.em_objects         import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject
)
from ..scene.grid_actor         import build_axes_widget


# ──────────────────────────────────────────────────────────────────────────────
PLANE_NORMAL = {"XY": (0, 0, 1), "XZ": (0, 1, 0), "YZ": (1, 0, 0)}
PLANE_ORIGIN = {"XY": (0, 0, 0), "XZ": (0, 0, 0), "YZ": (0, 0, 0)}


class Viewport3DWidget(QWidget):
    """Central 3D viewport widget."""

    # Emitted when an object is selected / deselected
    object_selected   = pyqtSignal(object)   # EMObject | None
    # Emitted when the scene changes (add/remove objects)
    scene_changed     = pyqtSignal()
    # Status-bar message
    status_message    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ── VTK setup ──────────────────────────────────────────────────────────
        self._vtk_widget   = QVTKRenderWindowInteractor(self)
        self._renderer     = vtk.vtkRenderer()
        self._render_window = self._vtk_widget.GetRenderWindow()
        self._render_window.AddRenderer(self._renderer)
        self._interactor   = self._render_window.GetInteractor()

        # Custom interactor style
        self._style = EMInteractorStyle()
        self._style.left_press_callback  = self._on_left_press
        self._style.mouse_move_callback  = self._on_mouse_move
        self._interactor.SetInteractorStyle(self._style)

        # Axes orientation widget
        self._axes_widget = build_axes_widget(self._interactor)

        # Scene manager
        self.scene = SceneManager(self._renderer)

        # Default camera
        self._reset_camera()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._vtk_widget)

        self._vtk_widget.Initialize()
        self._vtk_widget.Start()

        # ── Drawing state ──────────────────────────────────────────────────────
        self._draw_mode: Optional[str] = None   # 'box','cylinder','cone','sphere'
        self._draw_plane: str = "XY"
        self._draw_material: str = "PEC"
        self._draw_state: int = 0               # step counter within current shape
        self._draw_pts: list = []               # world-coord points collected so far
        self._preview_actor: Optional[vtk.vtkActor] = None

        # Grid settings
        self._grid_size    = 200.0
        self._grid_spacing = 10.0
        self._units        = "mm"

    # ──────────────────────────────────────────────────────── public API
    def start_draw(self, mode: str, plane: str = "XY",
                   material: str = "PEC") -> None:
        self._cancel_draw()
        self._draw_mode     = mode
        self._draw_plane    = plane
        self._draw_material = material
        self._draw_state    = 0
        self._draw_pts      = []
        self.setCursor(Qt.CrossCursor)
        self.status_message.emit(
            f"Drawing {mode} on {plane} plane  |  left-click to place points"
        )

    def cancel_draw(self) -> None:
        self._cancel_draw()
        self.status_message.emit("Drawing cancelled.")

    def set_grid(self, size: float, spacing: float,
                 plane: str, units: str = "mm") -> None:
        self._grid_size    = size
        self._grid_spacing = spacing
        self._units        = units
        self.scene.update_grid(size, spacing, plane)
        self._render()

    def reset_camera(self) -> None:
        self._reset_camera()
        self._render()

    # ──────────────────────────────────────────────────────── camera
    def _reset_camera(self) -> None:
        cam = self._renderer.GetActiveCamera()
        cam.SetPosition(100, -150, 120)
        cam.SetFocalPoint(0, 0, 0)
        cam.SetViewUp(0, 0, 1)
        self._renderer.ResetCamera()
        self._renderer.ResetCameraClippingRange()

    # ──────────────────────────────────────────────────────── render helpers
    def _render(self) -> None:
        if self._render_window:
            self._render_window.Render()

    # ──────────────────────────────────────────────────────── coordinate utils
    def _ray_plane_intersect(
        self, screen_x: int, screen_y: int
    ) -> Optional[Tuple[float, float, float]]:
        """Project screen point onto the current drawing plane (world coords)."""
        # Step 1: pick a world-space position using WorldPointPicker
        picker = vtk.vtkWorldPointPicker()
        picker.Pick(screen_x, screen_y, 0, self._renderer)
        ray_end = picker.GetPickPosition()

        cam_pos = self._renderer.GetActiveCamera().GetPosition()

        # Ray direction
        rd = [ray_end[i] - cam_pos[i] for i in range(3)]
        length = math.sqrt(sum(v * v for v in rd))
        if length < 1e-12:
            return None
        rd = [v / length for v in rd]

        normal = PLANE_NORMAL[self._draw_plane]
        origin = PLANE_ORIGIN[self._draw_plane]

        denom = sum(normal[i] * rd[i] for i in range(3))
        if abs(denom) < 1e-10:
            return None

        numer = sum(normal[i] * (origin[i] - cam_pos[i]) for i in range(3))
        t = numer / denom
        return tuple(cam_pos[i] + t * rd[i] for i in range(3))

    def _snap(self, pt: tuple) -> tuple:
        """Snap world point to grid."""
        s = self._grid_spacing
        return tuple(round(v / s) * s for v in pt)

    def _height_from_cursor(
        self, screen_x: int, screen_y: int, base_z: float
    ) -> float:
        """For height step: project cursor to vertical axis through base."""
        picker = vtk.vtkWorldPointPicker()
        picker.Pick(screen_x, screen_y, 0, self._renderer)
        p = picker.GetPickPosition()
        # Height axis: Z for XY plane, Y for XZ plane, X for YZ plane
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        return p[axis_idx] - base_z

    def _plane_radius(self, pt: tuple, center: tuple) -> float:
        """2D distance on the drawing plane (plane-aware)."""
        if self._draw_plane == "XY":
            return math.sqrt((pt[0] - center[0])**2 + (pt[1] - center[1])**2)
        elif self._draw_plane == "XZ":
            return math.sqrt((pt[0] - center[0])**2 + (pt[2] - center[2])**2)
        else:  # YZ
            return math.sqrt((pt[1] - center[1])**2 + (pt[2] - center[2])**2)

    def _geom_center_from_base(
        self, base_pt: tuple, h: float, axis: str
    ) -> tuple:
        """Offset base-centre by h/2 along the axis to get VTK geometric centre."""
        bx, by, bz = base_pt
        if axis == "Z":
            return (bx, by, bz + h / 2)
        elif axis == "Y":
            return (bx, by + h / 2, bz)
        else:  # X
            return (bx + h / 2, by, bz)

    # ──────────────────────────────────────────────────────── preview actors
    def _remove_preview(self) -> None:
        if self._preview_actor:
            self._renderer.RemoveActor(self._preview_actor)
            self._preview_actor = None

    def _set_preview(self, actor: vtk.vtkActor) -> None:
        self._remove_preview()
        self._preview_actor = actor
        actor.GetProperty().SetOpacity(0.35)
        actor.GetProperty().EdgeVisibilityOn()
        actor.GetProperty().SetEdgeColor(1.0, 0.5, 0.0)
        actor.GetProperty().SetColor(0.9, 0.7, 0.2)
        actor.PickableOff()
        self._renderer.AddActor(actor)

    def _preview_box(self, x1, y1, z1, x2, y2, z2) -> None:
        src = vtk.vtkCubeSource()
        src.SetBounds(
            min(x1, x2), max(x1, x2),
            min(y1, y2), max(y1, y2),
            min(z1, z2), max(z1, z2),
        )
        src.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        self._set_preview(actor)

    def _preview_cylinder(self, cx, cy, cz, radius, height, axis) -> None:
        src = vtk.vtkCylinderSource()
        src.SetResolution(36)
        src.SetCenter(0, 0, 0)
        src.SetRadius(max(abs(radius), 0.01))
        src.SetHeight(max(abs(height), 0.01))
        src.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        rot = CylinderObject.AXIS_ROTATION.get(axis, (90, 0, 0))
        actor.SetPosition(cx, cy, cz)
        actor.SetOrientation(*rot)
        self._set_preview(actor)

    def _preview_cone(self, cx, cy, cz, radius, height, axis) -> None:
        src = vtk.vtkConeSource()
        src.SetResolution(36)
        src.SetCenter(0, 0, 0)
        src.SetRadius(max(abs(radius), 0.01))
        src.SetHeight(max(abs(height), 0.01))
        src.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        rot = ConeObject.AXIS_ROTATION.get(axis, (0, 0, -90))
        actor.SetPosition(cx, cy, cz)
        actor.SetOrientation(*rot)
        self._set_preview(actor)

    def _preview_sphere(self, cx, cy, cz, radius) -> None:
        src = vtk.vtkSphereSource()
        src.SetPhiResolution(24)
        src.SetThetaResolution(24)
        src.SetCenter(cx, cy, cz)
        src.SetRadius(max(abs(radius), 0.01))
        src.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        self._set_preview(actor)

    # ──────────────────────────────────────────────────────── mouse events
    def _on_left_press(self, sx: int, sy: int) -> None:
        if self._draw_mode:
            self._drawing_click(sx, sy)
        else:
            self._selection_click(sx, sy)

    def _on_mouse_move(self, sx: int, sy: int) -> None:
        if self._draw_mode and self._draw_state > 0:
            self._drawing_preview(sx, sy)

    # ──────────────────────────────────────────────────────── selection
    def _selection_click(self, sx: int, sy: int) -> None:
        obj = self.scene.pick_at(sx, sy)
        self.scene.select(obj)
        self.object_selected.emit(obj)
        self._render()

    # ──────────────────────────────────────────────────────── drawing FSM
    def _drawing_click(self, sx: int, sy: int) -> None:
        pt = self._ray_plane_intersect(sx, sy)
        if pt is None:
            return
        pt = self._snap(pt)

        mode = self._draw_mode
        state = self._draw_state

        if mode == "box":
            self._fsm_box_click(pt, sx, sy, state)
        elif mode == "cylinder":
            self._fsm_cylinder_click(pt, sx, sy, state)
        elif mode == "cone":
            self._fsm_cone_click(pt, sx, sy, state)
        elif mode == "sphere":
            self._fsm_sphere_click(pt, sx, sy, state)

    def _drawing_preview(self, sx: int, sy: int) -> None:
        mode  = self._draw_mode
        state = self._draw_state
        if mode == "box":
            self._fsm_box_preview(sx, sy, state)
        elif mode == "cylinder":
            self._fsm_cylinder_preview(sx, sy, state)
        elif mode == "cone":
            self._fsm_cone_preview(sx, sy, state)
        elif mode == "sphere":
            self._fsm_sphere_preview(sx, sy, state)
        self._render()

    # ────────────── BOX FSM ──────────────
    def _fsm_box_click(self, pt, sx, sy, state):
        if state == 0:          # first corner on plane
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(
                f"Box: click opposite corner  [{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]"
            )
        elif state == 1:        # second corner → base defined, now drag height
            self._draw_pts.append(pt)
            self._draw_state = 2
            self.status_message.emit("Box: move mouse to set height, then click")
        elif state == 2:        # finalise height
            p1 = self._draw_pts[0]
            p2 = self._draw_pts[1]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, p1[axis_idx]) or 1.0
            # Build second z/y/x coordinate
            p2 = list(p2)
            p2[axis_idx] = p1[axis_idx] + h
            obj = BoxObject(
                material=self._draw_material,
                x1=p1[0], y1=p1[1], z1=p1[2],
                x2=p2[0], y2=p2[1], z2=p2[2],
            )
            self._finish_object(obj)

    def _fsm_box_preview(self, sx, sy, state):
        if state == 1:
            pt = self._ray_plane_intersect(sx, sy)
            if pt is None:
                return
            pt = self._snap(pt)
            p1 = self._draw_pts[0]
            # Show flat base rect (height = 0)
            self._preview_box(p1[0], p1[1], p1[2], pt[0], pt[1], pt[2])
            coord_str = f"[{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]"
            self.status_message.emit(f"Box: second corner {coord_str}")
        elif state == 2:
            p1 = self._draw_pts[0]
            p2 = list(self._draw_pts[1])
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, p1[axis_idx]) or 0.1
            p2[axis_idx] = p1[axis_idx] + h
            self._preview_box(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2])
            self.status_message.emit(f"Box: height = {h:.2f} {self._units}")

    # ────────────── CYLINDER FSM ──────────────
    def _fsm_cylinder_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]   # base centre
            self._draw_state = 1
            self.status_message.emit(
                f"Cylinder: base at [{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]  "
                "— move mouse to set radius, then click"
            )
        elif state == 1:
            ctr = self._draw_pts[0]
            r = self._plane_radius(pt, ctr) or 1.0
            self._draw_pts.append(r)
            self._draw_state = 2
            self.status_message.emit(
                f"Cylinder: r = {r:.2f} {self._units}  "
                "— move mouse upward to set height, then click"
            )
        elif state == 2:
            base  = self._draw_pts[0]
            r     = self._draw_pts[1]
            axis  = {"XY": "Z", "XZ": "Y", "YZ": "X"}[self._draw_plane]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 1.0
            geom = self._geom_center_from_base(base, h, axis)
            obj = CylinderObject(
                material=self._draw_material,
                cx=geom[0], cy=geom[1], cz=geom[2],
                radius=r, height=abs(h),
                axis=axis,
            )
            self._finish_object(obj)

    def _fsm_cylinder_preview(self, sx, sy, state):
        base = self._draw_pts[0]
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[self._draw_plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        if state == 1:
            pt = self._ray_plane_intersect(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, base) or 0.1
            # Show thin disk at the base (h≈0 so no offset needed)
            self._preview_cylinder(base[0], base[1], base[2], r, max(r * 0.05, 0.05), axis)
            self.status_message.emit(f"Cylinder: radius = {r:.2f} {self._units}")
        elif state == 2:
            r = self._draw_pts[1]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 0.1
            geom = self._geom_center_from_base(base, h, axis)
            self._preview_cylinder(geom[0], geom[1], geom[2], r, abs(h), axis)
            self.status_message.emit(f"Cylinder: height = {h:.2f} {self._units}")

    # ────────────── CONE FSM ──────────────
    def _fsm_cone_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]   # base centre
            self._draw_state = 1
            self.status_message.emit(
                f"Cone: base at [{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]  "
                "— move mouse to set base radius, then click"
            )
        elif state == 1:
            ctr = self._draw_pts[0]
            r = self._plane_radius(pt, ctr) or 1.0
            self._draw_pts.append(r)
            self._draw_state = 2
            self.status_message.emit(
                f"Cone: r = {r:.2f} {self._units}  "
                "— move mouse upward to set height, then click"
            )
        elif state == 2:
            base  = self._draw_pts[0]
            r     = self._draw_pts[1]
            axis  = {"XY": "Z", "XZ": "Y", "YZ": "X"}[self._draw_plane]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 1.0
            geom = self._geom_center_from_base(base, h, axis)
            obj = ConeObject(
                material=self._draw_material,
                cx=geom[0], cy=geom[1], cz=geom[2],
                radius=r, height=abs(h),
                axis=axis,
            )
            self._finish_object(obj)

    def _fsm_cone_preview(self, sx, sy, state):
        base = self._draw_pts[0]
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[self._draw_plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        if state == 1:
            pt = self._ray_plane_intersect(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, base) or 0.1
            self._preview_cone(base[0], base[1], base[2], r, max(r * 0.5, 0.1), axis)
            self.status_message.emit(f"Cone: radius = {r:.2f} {self._units}")
        elif state == 2:
            r = self._draw_pts[1]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 0.1
            geom = self._geom_center_from_base(base, h, axis)
            self._preview_cone(geom[0], geom[1], geom[2], r, abs(h), axis)
            self.status_message.emit(f"Cone: height = {h:.2f} {self._units}")

    # ────────────── SPHERE FSM ──────────────
    def _fsm_sphere_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(
                f"Sphere: centre at [{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]  "
                "— move mouse to set radius, then click"
            )
        elif state == 1:
            ctr = self._draw_pts[0]
            r = self._plane_radius(pt, ctr) or 1.0
            obj = SphereObject(
                material=self._draw_material,
                cx=ctr[0], cy=ctr[1], cz=ctr[2],
                radius=r,
            )
            self._finish_object(obj)

    def _fsm_sphere_preview(self, sx, sy, state):
        ctr = self._draw_pts[0]
        if state == 1:
            pt = self._ray_plane_intersect(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, ctr) or 0.1
            self._preview_sphere(ctr[0], ctr[1], ctr[2], r)
            self.status_message.emit(f"Sphere: radius = {r:.2f} {self._units}")

    # ──────────────────────────────────────────────────────── finish / cancel
    def _finish_object(self, obj: EMObject) -> None:
        self._remove_preview()
        self.scene.add_object(obj)
        self.scene.select(obj)
        self.object_selected.emit(obj)
        self.scene_changed.emit()
        self.status_message.emit(
            f"Created {type(obj).__name__}: {obj.name}"
        )
        self._draw_mode  = None
        self._draw_state = 0
        self._draw_pts   = []
        self.setCursor(Qt.ArrowCursor)
        self._render()

    def _cancel_draw(self) -> None:
        self._remove_preview()
        self._draw_mode  = None
        self._draw_state = 0
        self._draw_pts   = []
        self.setCursor(Qt.ArrowCursor)
        self._render()

    # ──────────────────────────────────────────────────────── Qt overrides
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._render_window:
            self._render_window.SetSize(self.width(), self.height())
