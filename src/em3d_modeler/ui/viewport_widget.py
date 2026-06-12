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
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QSizePolicy, QToolBar, QAction, QInputDialog
)
from PyQt5.QtCore    import pyqtSignal, Qt
from PyQt5.QtGui     import QIcon

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except ImportError:
    from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from pathlib import Path

from ..drawing.interactor_style import EMInteractorStyle
from ..drawing.sketch_engine    import (
    SketchEngine, plane_basis, uv_to_world, world_to_uv,
)
from ..scene.scene_manager      import SceneManager
from ..scene.em_objects         import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject,
    PlateObject, PyramidObject, WedgeObject, TorusObject, EllipsoidObject
)
from ..scene.grid_actor         import build_axes_widget


_ICONS_DIR = Path(__file__).parent.parent.parent.parent / "Icons"


def _icon(name: str) -> QIcon:
    for ext in ("svg", "png"):
        p = _ICONS_DIR / f"{name}.{ext}"
        if p.exists():
            return QIcon(str(p))
    return QIcon()


# ──────────────────────────────────────────────────────────────────────────────
PLANE_NORMAL = {"XY": (0, 0, 1), "XZ": (0, 1, 0), "YZ": (1, 0, 0)}
PLANE_ORIGIN = {"XY": (0, 0, 0), "XZ": (0, 0, 0), "YZ": (0, 0, 0)}


class Viewport3DWidget(QWidget):
    def set_grid_visible(self, visible: bool) -> None:
        """Show or hide the grid actor in the renderer."""
        if self.scene._grid_actor is not None:
            self.scene._grid_actor.SetVisibility(visible)
        self._render()

    def is_grid_visible(self) -> bool:
        if self.scene._grid_actor is None:
            return True
        return bool(self.scene._grid_actor.GetVisibility())

    """Central 3D viewport widget."""

    # Emitted when an object is selected / deselected
    object_selected   = pyqtSignal(object)        # EMObject | None
    # Emitted when the multi-selection changes
    selection_changed = pyqtSignal(list)           # List[EMObject]
    # Emitted when the scene changes (add/remove objects)
    scene_changed     = pyqtSignal()
    # Status-bar message
    status_message    = pyqtSignal(str)
    # Sketch-mode signals
    sketch_extrude_requested = pyqtSignal(list, float, tuple, tuple)
    sketch_revolve_requested = pyqtSignal(list, float, tuple, tuple, tuple, tuple)
    sketch_finished          = pyqtSignal()

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

        # Triad aligned to active reference plane (in-scene actor)
        self._plane_triad_visible: bool = True
        self._plane_triad_size: float = 25.0
        self._plane_triad_actor: Optional[vtk.vtkAxesActor] = None
        self._init_plane_triad_actor()

        # Default camera
        self._reset_camera()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._sketch_toolbar = self._build_sketch_toolbar()
        self._sketch_toolbar.setVisible(False)
        layout.addWidget(self._sketch_toolbar)
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
        self._selection_point_actors: list[vtk.vtkActor] = []

        # Custom reference plane (set via ReferencePlaneDialog)
        self._custom_plane_active: bool = False
        self._custom_plane_origin: Tuple[float,float,float] = (0.0, 0.0, 0.0)
        self._custom_plane_normal: Tuple[float,float,float] = (0.0, 0.0, 1.0)

        # Grid settings
        self._grid_size    = 200.0
        self._grid_spacing = 10.0
        self._units        = "mm"

        # Selection mode: 'object' | 'face' | 'edge' | 'vertex'
        self._selection_mode: str = "object"
        self._sub_pick_actor: Optional[vtk.vtkActor] = None
        self._last_drawing_snap_kind: str = "grid"

        # One-shot pick request from external dialogs
        # tuple (kind, callback) where kind ∈ {'point','vertex','face_normal','face_origin_normal'}
        self._pick_request = None

        # ── Sketch state ──────────────────────────────────────────────────────
        self._sketch_engine: Optional[SketchEngine] = None
        self._sketch_lines_actor: Optional[vtk.vtkActor]   = None
        self._sketch_hi_actor:    Optional[vtk.vtkActor]   = None
        self._sketch_verts_actor: Optional[vtk.vtkActor]   = None
        self._sketch_preview_actor: Optional[vtk.vtkActor] = None
        self._sketch_axis_pick_mode: bool = False
        self._sketch_axis_highlight: Optional[Tuple[Tuple[float, float],
                                                    Tuple[float, float]]] = None
        self.setFocusPolicy(Qt.StrongFocus)

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
        if mode == "planar":
            self.status_message.emit(
                f"Planar: pick start point (vertex/edge/face) on {plane} plane"
            )
        else:
            self.status_message.emit(
                f"Drawing {mode} on {plane} plane  |  left-click to place points"
            )

    def cancel_draw(self) -> None:
        self._cancel_draw()
        self.cancel_pick()
        self.status_message.emit("Drawing cancelled.")

    def set_grid(self, size: float, spacing: float,
                 plane: str, units: str = "mm") -> None:
        self._grid_size    = size
        self._grid_spacing = spacing
        self._units        = units
        self.scene.update_grid(size, spacing, plane)
        self._update_plane_triad_actor()
        self._render()

    def set_plane_triad_visible(self, visible: bool) -> None:
        self._plane_triad_visible = bool(visible)
        self._update_plane_triad_actor()
        self._render()

    def is_plane_triad_visible(self) -> bool:
        return bool(self._plane_triad_visible)

    def set_plane_triad_size(self, size: float) -> None:
        self._plane_triad_size = max(1e-6, float(size))
        self._update_plane_triad_actor()
        self._render()

    def get_plane_triad_size(self) -> float:
        return float(self._plane_triad_size)

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
            self._update_plane_triad_actor()
            self._render_window.Render()

    def _init_plane_triad_actor(self) -> None:
        actor = vtk.vtkAxesActor()
        actor.SetAxisLabels(1)
        actor.SetTotalLength(self._plane_triad_size, self._plane_triad_size, self._plane_triad_size)
        actor.PickableOff()
        self._renderer.AddActor(actor)
        self._plane_triad_actor = actor
        self._update_plane_triad_text_size()

    def _update_plane_triad_text_size(self) -> None:
        if self._plane_triad_actor is None:
            return

        # Keep axis caption text proportional to triad size (1:10 ratio).
        font_size = max(1, int(round(self._plane_triad_size / 10.0)))
        for caption in (
            self._plane_triad_actor.GetXAxisCaptionActor2D(),
            self._plane_triad_actor.GetYAxisCaptionActor2D(),
            self._plane_triad_actor.GetZAxisCaptionActor2D(),
        ):
            text_actor = caption.GetTextActor()
            text_actor.SetTextScaleModeToNone()
            text_actor.GetTextProperty().SetFontSize(font_size)

    def _active_draw_origin_normal(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        if self._custom_plane_active:
            return tuple(self._custom_plane_origin), tuple(self._custom_plane_normal)
        return tuple(PLANE_ORIGIN[self._draw_plane]), tuple(PLANE_NORMAL[self._draw_plane])

    def _plane_basis_from_normal(self, normal: tuple) -> Tuple[tuple, tuple, tuple]:
        nx, ny, nz = [float(v) for v in normal]
        mag = math.sqrt(nx * nx + ny * ny + nz * nz)
        if mag < 1e-12:
            n = (0.0, 0.0, 1.0)
        else:
            n = (nx / mag, ny / mag, nz / mag)

        ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.99 else (0.0, 1.0, 0.0)
        x = (
            ref[1] * n[2] - ref[2] * n[1],
            ref[2] * n[0] - ref[0] * n[2],
            ref[0] * n[1] - ref[1] * n[0],
        )
        xm = math.sqrt(x[0] * x[0] + x[1] * x[1] + x[2] * x[2])
        if xm < 1e-12:
            x = (1.0, 0.0, 0.0)
        else:
            x = (x[0] / xm, x[1] / xm, x[2] / xm)

        y = (
            n[1] * x[2] - n[2] * x[1],
            n[2] * x[0] - n[0] * x[2],
            n[0] * x[1] - n[1] * x[0],
        )
        ym = math.sqrt(y[0] * y[0] + y[1] * y[1] + y[2] * y[2])
        if ym < 1e-12:
            y = (0.0, 1.0, 0.0)
        else:
            y = (y[0] / ym, y[1] / ym, y[2] / ym)
        return x, y, n

    def _update_plane_triad_actor(self) -> None:
        if self._plane_triad_actor is None:
            return

        self._update_plane_triad_text_size()

        self._plane_triad_actor.SetVisibility(1 if self._plane_triad_visible else 0)
        if not self._plane_triad_visible:
            return

        origin, normal = self._active_draw_origin_normal()
        x_axis, y_axis, z_axis = self._plane_basis_from_normal(normal)

        m = vtk.vtkMatrix4x4()
        m.Identity()
        m.SetElement(0, 0, x_axis[0]); m.SetElement(1, 0, x_axis[1]); m.SetElement(2, 0, x_axis[2])
        m.SetElement(0, 1, y_axis[0]); m.SetElement(1, 1, y_axis[1]); m.SetElement(2, 1, y_axis[2])
        m.SetElement(0, 2, z_axis[0]); m.SetElement(1, 2, z_axis[1]); m.SetElement(2, 2, z_axis[2])
        m.SetElement(0, 3, float(origin[0]))
        m.SetElement(1, 3, float(origin[1]))
        m.SetElement(2, 3, float(origin[2]))

        tfm = vtk.vtkTransform()
        tfm.SetMatrix(m)
        self._plane_triad_actor.SetUserTransform(tfm)

        cam = self._renderer.GetActiveCamera()
        cam_pos = cam.GetPosition()
        dist = math.sqrt(
            (cam_pos[0] - origin[0]) ** 2 +
            (cam_pos[1] - origin[1]) ** 2 +
            (cam_pos[2] - origin[2]) ** 2
        )
        desired = dist * 0.08
        min_len = max(self._plane_triad_size * 0.5, 1e-6)
        max_len = max(self._plane_triad_size * 1.5, min_len)
        triad_len = max(min_len, min(max_len, desired))
        self._plane_triad_actor.SetTotalLength(triad_len, triad_len, triad_len)

    # ──────────────────────────────────────────────────────── camera state
    def get_camera_state(self) -> dict:
        """Return serialisable camera state dict."""
        cam = self._renderer.GetActiveCamera()
        return {
            "position":       list(cam.GetPosition()),
            "focal_point":    list(cam.GetFocalPoint()),
            "view_up":        list(cam.GetViewUp()),
            "parallel_scale": cam.GetParallelScale(),
            "parallel_proj":  bool(cam.GetParallelProjection()),
            "clipping_range": list(cam.GetClippingRange()),
        }

    def set_camera_state(self, state: dict) -> None:
        """Restore camera from a dict saved by get_camera_state."""
        if not state:
            return
        cam = self._renderer.GetActiveCamera()
        cam.SetPosition(*state["position"])
        cam.SetFocalPoint(*state["focal_point"])
        cam.SetViewUp(*state["view_up"])
        cam.SetParallelScale(state["parallel_scale"])
        cam.SetParallelProjection(int(state.get("parallel_proj", False)))
        if "clipping_range" in state:
            cam.SetClippingRange(*state["clipping_range"])
        self._renderer.ResetCameraClippingRange()
        self._render()

    # ──────────────────────────────────────────────────────── public reference plane API
    def set_reference_plane(self, origin: tuple, normal: tuple) -> None:
        """Set a custom drawing plane.  Activated immediately. Ricostruisce la griglia su questo piano."""
        self._custom_plane_active = True
        self._custom_plane_origin = tuple(origin)
        self._custom_plane_normal = tuple(normal)
        # Ricostruisci la griglia su questo piano
        self.scene._grid_plane = "CUSTOM"
        self.scene._rebuild_grid()
        self._update_plane_triad_actor()
        self.status_message.emit(
            f"Reference plane set: origin {origin}  normal {normal}"
        )

    def reset_reference_plane(self) -> None:
        """Revert to axis-aligned plane selected in the toolbar combo. Ricostruisce la griglia su XY/XZ/YZ."""
        self._custom_plane_active = False
        # Ricostruisci la griglia sul piano selezionato
        self.scene._grid_plane = self._draw_plane if hasattr(self, '_draw_plane') else "XY"
        self.scene._rebuild_grid()
        self._update_plane_triad_actor()
        self.status_message.emit("Reference plane reset to axis-aligned plane.")

    # ─────────────────────────────────────────────────── selection mode
    def set_selection_mode(self, mode: str) -> None:
        """Set sub-element selection: 'object' | 'face' | 'edge' | 'vertex'."""
        if mode not in ("object", "face", "edge", "vertex"):
            return
        self._selection_mode = mode
        self._clear_sub_pick_marker()
        self._render()

    def _clear_sub_pick_marker(self) -> None:
        if self._sub_pick_actor is not None:
            self._renderer.RemoveActor(self._sub_pick_actor)
            self._sub_pick_actor = None
    # ────────────────────────────────────────────────── one-shot pick API
    def request_pick(self, kind: str, callback) -> None:
        """Arm a one-shot pick. The next left-click will invoke *callback*.

        Parameters
        ----------
        kind : 'point' | 'vertex' | 'face_normal' | 'face_origin_normal'
            - 'point'              : callback(world_xyz)
            - 'vertex'             : callback(world_xyz)  (snaps to nearest mesh vertex)
            - 'face_normal'        : callback(world_normal_xyz)
            - 'face_origin_normal' : callback(world_xyz, world_normal_xyz)
        callback : callable
        """
        if kind not in ("point", "vertex", "face_normal", "face_origin_normal"):
            raise ValueError(f"Unknown pick kind: {kind}")
        self._cancel_draw()
        self._pick_request = (kind, callback)
        self.setCursor(Qt.CrossCursor)
        msg = {
            "point":              "Click on geometry to pick a point",
            "vertex":             "Click near a vertex to snap to it",
            "face_normal":        "Click on a face to capture its normal",
            "face_origin_normal": "Click on a face to set origin + normal",
        }[kind]
        self.status_message.emit(f"{msg}  (Esc to cancel)")

    def cancel_pick(self) -> None:
        if self._pick_request is not None:
            self._pick_request = None
            self.unsetCursor()
            self.status_message.emit("Pick cancelled.")

    def _handle_pick_request(self, sx: int, sy: int) -> bool:
        """If a pick request is armed, fulfil it. Returns True if consumed."""
        if self._pick_request is None:
            return False
        kind, callback = self._pick_request

        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.005)
        picker.Pick(sx, sy, 0, self._renderer)
        actor = picker.GetActor()
        if actor is None:
            self.status_message.emit("Nothing under cursor – try again.")
            return True   # keep request armed

        pos = picker.GetPickPosition()

        # Compute world-space normal of the picked cell (face)
        normal_world = None
        cell_id = picker.GetCellId()
        ds = picker.GetDataSet()
        if cell_id >= 0 and ds is not None:
            cell = ds.GetCell(cell_id)
            if cell is not None and cell.GetNumberOfPoints() >= 3:
                p0 = cell.GetPoints().GetPoint(0)
                p1 = cell.GetPoints().GetPoint(1)
                p2 = cell.GetPoints().GetPoint(2)
                e1 = (p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2])
                e2 = (p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2])
                nx = e1[1]*e2[2] - e1[2]*e2[1]
                ny = e1[2]*e2[0] - e1[0]*e2[2]
                nz = e1[0]*e2[1] - e1[1]*e2[0]
                # Transform local-cell normal to world via actor's matrix
                m = actor.GetMatrix()
                w = m.MultiplyPoint([nx, ny, nz, 0.0])
                mag = math.sqrt(w[0]**2 + w[1]**2 + w[2]**2)
                if mag > 1e-12:
                    normal_world = (w[0]/mag, w[1]/mag, w[2]/mag)

        # Reset state BEFORE invoking callback (callback may re-arm)
        self._pick_request = None
        self.unsetCursor()

        try:
            if kind == "point":
                callback(tuple(pos))
            elif kind == "vertex":
                pp = vtk.vtkPointPicker()
                pp.SetTolerance(0.01)
                pp.Pick(sx, sy, 0, self._renderer)
                pid = pp.GetPointId()
                if pid >= 0 and pp.GetDataSet() is not None:
                    local = pp.GetDataSet().GetPoint(pid)
                    m = actor.GetMatrix()
                    w = m.MultiplyPoint([local[0], local[1], local[2], 1.0])
                    callback((w[0], w[1], w[2]))
                else:
                    callback(tuple(pos))
            elif kind == "face_normal":
                if normal_world is None:
                    self.status_message.emit("Could not compute face normal.")
                    return True
                callback(normal_world)
            elif kind == "face_origin_normal":
                if normal_world is None:
                    self.status_message.emit("Could not compute face normal.")
                    return True
                callback(tuple(pos), normal_world)
        except Exception as exc:
            self.status_message.emit(f"Pick callback error: {exc}")
        return True
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

        # Choose plane: custom or axis-aligned
        if self._custom_plane_active:
            normal = self._custom_plane_normal
            origin = self._custom_plane_origin
        else:
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

    def _project_point_to_draw_plane(self, pt: tuple) -> tuple:
        """Project a world point onto the current drawing plane."""
        if self._custom_plane_active:
            origin = self._custom_plane_origin
            normal = self._custom_plane_normal
        else:
            origin = PLANE_ORIGIN[self._draw_plane]
            normal = PLANE_NORMAL[self._draw_plane]
        ox, oy, oz = origin
        nx, ny, nz = normal
        px, py, pz = pt
        denom = nx * nx + ny * ny + nz * nz
        if denom < 1e-12:
            return pt
        t = (nx * (ox - px) + ny * (oy - py) + nz * (oz - pz)) / denom
        return (px + nx * t, py + ny * t, pz + nz * t)

    @staticmethod
    def _closest_point_on_segment(point, start, end):
        """Return the closest point on segment start-end and its distance."""
        px, py, pz = point
        ax, ay, az = start
        bx, by, bz = end
        dx, dy, dz = bx - ax, by - ay, bz - az
        denom = dx * dx + dy * dy + dz * dz
        if denom < 1e-12:
            closest = (ax, ay, az)
        else:
            t = ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / denom
            t = max(0.0, min(1.0, t))
            closest = (ax + t * dx, ay + t * dy, az + t * dz)
        dist = math.sqrt((px - closest[0])**2 + (py - closest[1])**2 + (pz - closest[2])**2)
        return closest, dist

    def _snap_to_visible_geometry(self, sx: int, sy: int, fallback_pt: tuple, snap_mode: str = "all") -> tuple | None:
        """Try snapping against visible geometry using the requested filter.

        snap_mode: 'all' | 'vertex' | 'edge' | 'face'
        """
        mode = str(snap_mode or "all").strip().lower()
        if mode == "object":
            mode = "all"
        if mode not in {"all", "vertex", "edge", "face"}:
            mode = "all"

        # Vertex-only or ALL: vertex snap has highest priority.
        if mode in {"all", "vertex"}:
            point_picker = vtk.vtkPointPicker()
            point_picker.SetTolerance(0.01)
            point_picker.Pick(sx, sy, 0, self._renderer)
            point_actor = point_picker.GetActor()
            point_id = point_picker.GetPointId()
            if point_actor is not None and point_id >= 0 and point_picker.GetDataSet() is not None:
                local = point_picker.GetDataSet().GetPoint(point_id)
                world = point_actor.GetMatrix().MultiplyPoint([local[0], local[1], local[2], 1.0])
                return (tuple(self._project_point_to_draw_plane((world[0], world[1], world[2]))), "vertex")
            if mode == "vertex":
                return None

        # Edge / face snap: use the picked cell and evaluate based on filter.
        cell_picker = vtk.vtkCellPicker()
        cell_picker.SetTolerance(0.005)
        cell_picker.Pick(sx, sy, 0, self._renderer)
        actor = cell_picker.GetActor()
        ds = cell_picker.GetDataSet()
        cell_id = cell_picker.GetCellId()
        if actor is None or ds is None or cell_id < 0:
            return None

        pos = cell_picker.GetPickPosition()
        cell = ds.GetCell(cell_id)

        if mode in {"all", "edge"} and cell is not None and cell.GetNumberOfEdges() > 0:
            m = actor.GetMatrix()
            inv = vtk.vtkMatrix4x4()
            vtk.vtkMatrix4x4.Invert(m, inv)
            local = inv.MultiplyPoint([pos[0], pos[1], pos[2], 1.0])
            lp = (local[0], local[1], local[2])
            best_edge = None
            best_dist = float("inf")
            for ei in range(cell.GetNumberOfEdges()):
                edge = cell.GetEdge(ei)
                p0 = edge.GetPoints().GetPoint(0)
                p1 = edge.GetPoints().GetPoint(1)
                closest_local, dist = self._closest_point_on_segment(lp, p0, p1)
                if dist < best_dist:
                    best_dist = dist
                    best_edge = closest_local
            edge_threshold = max(self._grid_spacing * 0.05, 0.25)
            if best_edge is not None and best_dist <= edge_threshold:
                world = m.MultiplyPoint([best_edge[0], best_edge[1], best_edge[2], 1.0])
                return (tuple(self._project_point_to_draw_plane((world[0], world[1], world[2]))), "edge")
            if mode == "edge":
                return None

        if mode in {"all", "face"}:
            return (tuple(self._project_point_to_draw_plane(pos)), "surface")

        return None

    def _drawing_snap_point(self, sx: int, sy: int) -> Optional[tuple]:
        """Return a drawing point on the active plane using Select filter for snap."""
        plane_pt = self._ray_plane_intersect(sx, sy)
        if plane_pt is None:
            return None

        snap_mode = self._selection_mode if self._selection_mode != "object" else "all"
        geometry_pt = self._snap_to_visible_geometry(sx, sy, plane_pt, snap_mode=snap_mode)
        if geometry_pt is not None:
            pt, kind = geometry_pt
            self._last_drawing_snap_kind = str(kind)
            return pt

        self._last_drawing_snap_kind = "grid"
        return self._snap(plane_pt)

    def _drawing_snap_status_suffix(self) -> str:
        kind = str(getattr(self, "_last_drawing_snap_kind", "grid")).strip().lower()
        labels = {
            "vertex": "Vertex",
            "edge": "Edge",
            "surface": "Surface",
            "grid": "Grid",
        }
        return f" | snap: {labels.get(kind, kind.title() or 'Grid')}"

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

    def _clear_selection_point_markers(self) -> None:
        for a in self._selection_point_actors:
            self._renderer.RemoveActor(a)
        self._selection_point_actors = []

    def _add_selection_point_marker(self, world_pt: tuple) -> None:
        pts = vtk.vtkPoints()
        pts.InsertNextPoint(world_pt[0], world_pt[1], world_pt[2])
        verts = vtk.vtkCellArray()
        verts.InsertNextCell(1)
        verts.InsertCellPoint(0)
        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetVerts(verts)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, 0.05, 0.05)
        actor.GetProperty().SetPointSize(7.0)
        actor.GetProperty().RenderPointsAsSpheresOn()
        actor.PickableOff()

        self._renderer.AddActor(actor)
        self._selection_point_actors.append(actor)

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
    def _on_left_press(self, sx: int, sy: int, ctrl: bool = False) -> None:
        # Robust Ctrl detection: VTK's GetControlKey() is not always updated
        # when QVTKRenderWindowInteractor forwards mouse events, so fall back
        # to Qt's keyboard modifier state.
        if not ctrl:
            try:
                from PyQt5.QtWidgets import QApplication
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    ctrl = True
            except Exception:
                pass
        # One-shot pick takes priority over everything else
        if self._handle_pick_request(sx, sy):
            return
        if self._sketch_engine is not None:
            self._sketch_left_press(sx, sy)
            return
        if self._draw_mode:
            self._drawing_click(sx, sy)
        else:
            self._selection_click(sx, sy, ctrl)

    def _on_mouse_move(self, sx: int, sy: int) -> None:
        if self._sketch_engine is not None:
            self._sketch_mouse_move(sx, sy)
            return
        if self._draw_mode and self._draw_state > 0:
            self._drawing_preview(sx, sy)

    # ──────────────────────────────────────────────────────── selection
    def _selection_click(self, sx: int, sy: int, ctrl: bool = False) -> None:
        # Object-level selection (default)
        if self._selection_mode == "object":
            self._clear_sub_pick_marker()
            obj = self.scene.pick_at(sx, sy)
            if ctrl and obj:
                self.scene.select_add(obj)
            else:
                self.scene.select(obj)
            self.object_selected.emit(obj)
            self.selection_changed.emit(list(self.scene.selection))
            self._render()
            return

        # Sub-element selection (face / edge / vertex)
        self._sub_element_pick(sx, sy)

    def _sub_element_pick(self, sx: int, sy: int) -> None:
        """Pick a single face / edge / vertex on the topmost actor."""
        picker = vtk.vtkCellPicker()
        picker.SetTolerance(0.005)
        picker.Pick(sx, sy, 0, self._renderer)
        actor = picker.GetActor()
        if actor is None:
            self._clear_sub_pick_marker()
            self.status_message.emit("No object under cursor.")
            self._render()
            return

        cell_id = picker.GetCellId()
        pos     = picker.GetPickPosition()
        ds      = picker.GetDataSet()
        if ds is None or cell_id < 0:
            self.status_message.emit("Pick failed.")
            return

        # Find owning EMObject (for status)
        owner = None
        for o in self.scene.objects:
            if any(a is actor for a in o.all_actors):
                owner = o
                break

        self._clear_sub_pick_marker()
        mode = self._selection_mode

        if mode == "face":
            marker = self._build_face_marker(ds, cell_id, actor)
            self.status_message.emit(
                f"Face picked  cell={cell_id}  on {owner.name if owner else '?'}"
            )
        elif mode == "edge":
            marker = self._build_edge_marker(ds, cell_id, actor, pos)
            self.status_message.emit(
                f"Edge picked  on {owner.name if owner else '?'}"
            )
        else:  # vertex
            point_picker = vtk.vtkPointPicker()
            point_picker.SetTolerance(0.01)
            point_picker.Pick(sx, sy, 0, self._renderer)
            pid = point_picker.GetPointId()
            if pid >= 0 and point_picker.GetDataSet() is not None:
                vp = point_picker.GetDataSet().GetPoint(pid)
            else:
                vp = pos
            marker = self._build_vertex_marker(vp, actor)
            self.status_message.emit(
                f"Vertex picked  ({vp[0]:.2f}, {vp[1]:.2f}, {vp[2]:.2f})"
            )

        if marker is not None:
            self._renderer.AddActor(marker)
            self._sub_pick_actor = marker
        self._render()

    def _build_face_marker(self, dataset, cell_id: int, ref_actor) -> Optional[vtk.vtkActor]:
        ids = vtk.vtkIdTypeArray()
        ids.InsertNextValue(cell_id)
        sel_node = vtk.vtkSelectionNode()
        sel_node.SetFieldType(vtk.vtkSelectionNode.CELL)
        sel_node.SetContentType(vtk.vtkSelectionNode.INDICES)
        sel_node.SetSelectionList(ids)
        sel = vtk.vtkSelection()
        sel.AddNode(sel_node)
        extract = vtk.vtkExtractSelection()
        extract.SetInputData(0, dataset)
        extract.SetInputData(1, sel)
        extract.Update()
        surf = vtk.vtkDataSetSurfaceFilter()
        surf.SetInputConnection(extract.GetOutputPort())
        surf.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(surf.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        # Inherit transform of original actor so marker overlays exactly
        actor.SetUserMatrix(ref_actor.GetMatrix())
        actor.GetProperty().SetColor(1.0, 0.6, 0.0)
        actor.GetProperty().SetOpacity(0.85)
        actor.GetProperty().EdgeVisibilityOn()
        actor.GetProperty().SetEdgeColor(1.0, 0.9, 0.0)
        actor.GetProperty().SetLineWidth(2.0)
        actor.PickableOff()
        return actor

    def _build_edge_marker(self, dataset, cell_id: int, ref_actor, pos) -> Optional[vtk.vtkActor]:
        cell = dataset.GetCell(cell_id)
        if cell is None or cell.GetNumberOfEdges() == 0:
            return None

        # Find the edge of this cell closest to pos (in dataset-local coords)
        # Convert pos (world) to dataset-local using inverse user-matrix
        m = ref_actor.GetMatrix()
        inv = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(m, inv)
        local = inv.MultiplyPoint([pos[0], pos[1], pos[2], 1.0])
        lp = (local[0], local[1], local[2])

        best_edge = None
        best_d    = float("inf")
        for ei in range(cell.GetNumberOfEdges()):
            e = cell.GetEdge(ei)
            p0 = e.GetPoints().GetPoint(0)
            p1 = e.GetPoints().GetPoint(1)
            d = self._point_segment_distance(lp, p0, p1)
            if d < best_d:
                best_d    = d
                best_edge = (p0, p1)
        if best_edge is None:
            return None

        pts = vtk.vtkPoints()
        pts.InsertNextPoint(*best_edge[0])
        pts.InsertNextPoint(*best_edge[1])
        line = vtk.vtkCellArray()
        seg = vtk.vtkLine()
        seg.GetPointIds().SetId(0, 0)
        seg.GetPointIds().SetId(1, 1)
        line.InsertNextCell(seg)
        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(line)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetUserMatrix(ref_actor.GetMatrix())
        actor.GetProperty().SetColor(1.0, 0.85, 0.0)
        actor.GetProperty().SetLineWidth(4.0)
        actor.PickableOff()
        return actor

    def _build_vertex_marker(self, world_pt, ref_actor) -> vtk.vtkActor:
        # Render as a single screen-space point (fixed pixel size).
        # No world-space geometry → always the same visual size regardless of zoom.
        pts = vtk.vtkPoints()
        pts.InsertNextPoint(world_pt[0], world_pt[1], world_pt[2])
        verts = vtk.vtkCellArray()
        verts.InsertNextCell(1)
        verts.InsertCellPoint(0)
        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetVerts(verts)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, 0.05, 0.05)   # bright red
        actor.GetProperty().SetPointSize(2.0)            # fixed screen pixels
        actor.GetProperty().RenderPointsAsSpheresOn()    # round dot
        actor.PickableOff()
        return actor

    @staticmethod
    def _point_segment_distance(p, a, b) -> float:
        ax, ay, az = a
        bx, by, bz = b
        px, py, pz = p
        dx, dy, dz = bx - ax, by - ay, bz - az
        denom = dx*dx + dy*dy + dz*dz
        if denom < 1e-12:
            return math.sqrt((px-ax)**2 + (py-ay)**2 + (pz-az)**2)
        t = ((px-ax)*dx + (py-ay)*dy + (pz-az)*dz) / denom
        t = max(0.0, min(1.0, t))
        cx, cy, cz = ax + t*dx, ay + t*dy, az + t*dz
        return math.sqrt((px-cx)**2 + (py-cy)**2 + (pz-cz)**2)

    # ──────────────────────────────────────────────────────── drawing FSM
    def _drawing_click(self, sx: int, sy: int) -> None:
        pt = self._drawing_snap_point(sx, sy)
        if pt is None:
            return

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
        elif mode == "plate":
            self._fsm_plate_click(pt, sx, sy, state)
        elif mode == "pyramid":
            self._fsm_pyramid_click(pt, sx, sy, state)
        elif mode == "wedge":
            self._fsm_wedge_click(pt, sx, sy, state)
        elif mode == "torus":
            self._fsm_torus_click(pt, sx, sy, state)
        elif mode == "ellipsoid":
            self._fsm_ellipsoid_click(pt, sx, sy, state)
        elif mode == "planar":
            self._fsm_planar_click(pt, sx, sy, state)

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
        elif mode == "plate":
            self._fsm_plate_preview(sx, sy, state)
        elif mode == "pyramid":
            self._fsm_pyramid_preview(sx, sy, state)
        elif mode == "wedge":
            self._fsm_wedge_preview(sx, sy, state)
        elif mode == "torus":
            self._fsm_torus_preview(sx, sy, state)
        elif mode == "ellipsoid":
            self._fsm_ellipsoid_preview(sx, sy, state)
        elif mode == "planar":
            self._fsm_planar_preview(sx, sy, state)
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
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            p1 = self._draw_pts[0]
            # Show flat base rect (height = 0)
            self._preview_box(p1[0], p1[1], p1[2], pt[0], pt[1], pt[2])
            coord_str = f"[{pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f}]"
            self.status_message.emit(f"Box: second corner {coord_str}{self._drawing_snap_status_suffix()}")
        elif state == 2:
            p1 = self._draw_pts[0]
            p2 = list(self._draw_pts[1])
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, p1[axis_idx]) or 0.1
            p2[axis_idx] = p1[axis_idx] + h
            self._preview_box(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2])
            self.status_message.emit(f"Box: height = {h:.2f} {self._units}{self._drawing_snap_status_suffix()}")

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
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, base) or 0.1
            # Show thin disk at the base (h≈0 so no offset needed)
            self._preview_cylinder(base[0], base[1], base[2], r, max(r * 0.05, 0.05), axis)
            self.status_message.emit(f"Cylinder: radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")
        elif state == 2:
            r = self._draw_pts[1]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 0.1
            geom = self._geom_center_from_base(base, h, axis)
            self._preview_cylinder(geom[0], geom[1], geom[2], r, abs(h), axis)
            self.status_message.emit(f"Cylinder: height = {h:.2f} {self._units}{self._drawing_snap_status_suffix()}")

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
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, base) or 0.1
            self._preview_cone(base[0], base[1], base[2], r, max(r * 0.5, 0.1), axis)
            self.status_message.emit(f"Cone: radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")
        elif state == 2:
            r = self._draw_pts[1]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 0.1
            geom = self._geom_center_from_base(base, h, axis)
            self._preview_cone(geom[0], geom[1], geom[2], r, abs(h), axis)
            self.status_message.emit(f"Cone: height = {h:.2f} {self._units}{self._drawing_snap_status_suffix()}")

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
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, ctr) or 0.1
            self._preview_sphere(ctr[0], ctr[1], ctr[2], r)
            self.status_message.emit(f"Sphere: radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")

    # ────────────── PLATE FSM (thin box) ──────────────────────────────
    def _fsm_plate_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Plate: click opposite corner")
        elif state == 1:
            self._draw_pts.append(pt)
            self._draw_state = 2
            self.status_message.emit("Plate: move mouse to set thickness, then click")
        elif state == 2:
            p1 = self._draw_pts[0]
            p2 = self._draw_pts[1]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            thick = self._height_from_cursor(sx, sy, p1[axis_idx]) or 0.1
            p2 = list(p2)
            p2[axis_idx] = p1[axis_idx] + thick
            obj = PlateObject(
                material=self._draw_material,
                x1=p1[0], y1=p1[1], z1=p1[2],
                x2=p2[0], y2=p2[1], z2=p2[2],
            )
            self._finish_object(obj)

    def _fsm_plate_preview(self, sx, sy, state):
        if state == 1:
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            p1 = self._draw_pts[0]
            self._preview_box(p1[0], p1[1], p1[2], pt[0], pt[1], pt[2])
            self.status_message.emit(f"Plate: second corner [{pt[0]:.1f}, {pt[1]:.1f}]{self._drawing_snap_status_suffix()}")
        elif state == 2:
            p1 = self._draw_pts[0]
            p2 = list(self._draw_pts[1])
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            thick = self._height_from_cursor(sx, sy, p1[axis_idx]) or 0.01
            p2[axis_idx] = p1[axis_idx] + thick
            self._preview_box(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2])
            self.status_message.emit(f"Plate: thickness = {abs(thick):.3f} {self._units}{self._drawing_snap_status_suffix()}")

    # ────────────── PYRAMID FSM ──────────────────────────────────
    def _fsm_pyramid_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Pyramid: base centre at [{pt[0]:.1f}, {pt[1]:.1f}]")
        elif state == 1:
            ctr = self._draw_pts[0]
            r = self._plane_radius(pt, ctr) or 1.0
            self._draw_pts.append(r)
            self._draw_state = 2
            self.status_message.emit(f"Pyramid: base radius = {r:.2f} {self._units}  — move to set height")
        elif state == 2:
            base = self._draw_pts[0]
            r = self._draw_pts[1]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 1.0
            obj = PyramidObject(
                material=self._draw_material,
                bx1=base[0]-r, by1=base[1]-r, bz=base[axis_idx],
                bx2=base[0]+r, by2=base[1]+r,
                apex_z=base[axis_idx] + abs(h),
            )
            self._finish_object(obj)

    def _fsm_pyramid_preview(self, sx, sy, state):
        base = self._draw_pts[0]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        if state == 1:
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, base) or 0.1
            self.status_message.emit(f"Pyramid: base radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")
        elif state == 2:
            r = self._draw_pts[1]
            h = self._height_from_cursor(sx, sy, base[axis_idx]) or 0.1
            self.status_message.emit(f"Pyramid: height = {abs(h):.2f} {self._units}{self._drawing_snap_status_suffix()}")

    # ────────────── WEDGE FSM ──────────────────────────────────
    def _fsm_wedge_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Wedge: first triangle corner")
        elif state == 1:
            self._draw_pts.append(pt)
            self._draw_state = 2
            self.status_message.emit(f"Wedge: second triangle corner")
        elif state == 2:
            self._draw_pts.append(pt)
            self._draw_state = 3
            self.status_message.emit(f"Wedge: move mouse to set height, then click")
        elif state == 3:
            p1, p2, p3 = self._draw_pts
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h = self._height_from_cursor(sx, sy, p1[axis_idx]) or 1.0
            obj = WedgeObject(
                material=self._draw_material,
                x1=p1[0], y1=p1[1], z1=p1[2],
                x2=p2[0], y2=p2[1], z2=p2[2],
                x3=p3[0], y3=p3[1],
                z_height=abs(h),
            )
            self._finish_object(obj)

    def _fsm_wedge_preview(self, sx, sy, state):
        if state >= 1:
            self.status_message.emit(f"Wedge: point {state} collected")

    # ────────────── TORUS FSM ──────────────────────────────────
    def _fsm_torus_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Torus: centre at [{pt[0]:.1f}, {pt[1]:.1f}]")
        elif state == 1:
            ctr = self._draw_pts[0]
            major_r = self._plane_radius(pt, ctr) or 5.0
            self._draw_pts.append(major_r)
            self._draw_state = 2
            self.status_message.emit(f"Torus: major radius = {major_r:.2f} {self._units}  — click to set minor radius")
        elif state == 2:
            ctr = self._draw_pts[0]
            pt_ref = (ctr[0] + self._draw_pts[1], ctr[1], ctr[2]) if self._draw_plane == "XY" else (ctr[0], ctr[1] + self._draw_pts[1], ctr[2])
            minor_r = self._plane_radius(pt, pt_ref) or 2.0
            obj = TorusObject(
                material=self._draw_material,
                cx=ctr[0], cy=ctr[1], cz=ctr[2],
                major_radius=max(self._draw_pts[1], minor_r + 0.1),
                minor_radius=min(minor_r, self._draw_pts[1] - 0.1),
            )
            self._finish_object(obj)

    def _fsm_torus_preview(self, sx, sy, state):
        ctr = self._draw_pts[0]
        if state == 1:
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, ctr) or 0.1
            self.status_message.emit(f"Torus: major radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")

    # ────────────── ELLIPSOID FSM ──────────────────────────────────
    def _fsm_ellipsoid_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Ellipsoid: centre at [{pt[0]:.1f}, {pt[1]:.1f}]")
        elif state == 1:
            ctr = self._draw_pts[0]
            plane_r = self._plane_radius(pt, ctr) or 1.0
            self._draw_pts.append(plane_r)
            self._draw_state = 2
            self.status_message.emit(f"Ellipsoid: plane radius = {plane_r:.2f} {self._units}  — move to set height radius")
        elif state == 2:
            ctr = self._draw_pts[0]
            plane_r = self._draw_pts[1]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h_r = self._height_from_cursor(sx, sy, ctr[axis_idx]) or plane_r
            if self._draw_plane == "XY":
                obj = EllipsoidObject(
                    material=self._draw_material,
                    cx=ctr[0], cy=ctr[1], cz=ctr[2],
                    rx=plane_r, ry=plane_r, rz=abs(h_r),
                )
            elif self._draw_plane == "XZ":
                obj = EllipsoidObject(
                    material=self._draw_material,
                    cx=ctr[0], cy=ctr[1], cz=ctr[2],
                    rx=plane_r, ry=abs(h_r), rz=plane_r,
                )
            else:  # YZ
                obj = EllipsoidObject(
                    material=self._draw_material,
                    cx=ctr[0], cy=ctr[1], cz=ctr[2],
                    rx=abs(h_r), ry=plane_r, rz=plane_r,
                )
            self._finish_object(obj)

    def _fsm_ellipsoid_preview(self, sx, sy, state):
        ctr = self._draw_pts[0]
        if state == 1:
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return
            r = self._plane_radius(pt, ctr) or 0.1
            self._preview_sphere(ctr[0], ctr[1], ctr[2], r)
            self.status_message.emit(f"Ellipsoid: plane radius = {r:.2f} {self._units}{self._drawing_snap_status_suffix()}")
        elif state == 2:
            r = self._draw_pts[1]
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            h_r = self._height_from_cursor(sx, sy, ctr[axis_idx]) or r
            self.status_message.emit(f"Ellipsoid: height radius = {abs(h_r):.2f} {self._units}{self._drawing_snap_status_suffix()}")

    # ────────────── PLANAR FSM (2-point planar structure) ─────────────────────
    def _fsm_planar_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self._clear_selection_point_markers()
            self._add_selection_point_marker(pt)
            self.status_message.emit(
                "Planar: drag on plane and pick second point (vertex/edge/face snap)"
            )
        elif state == 1:
            self._draw_pts.append(pt)
            self._add_selection_point_marker(pt)

            p1 = self._draw_pts[0]
            p2 = list(self._draw_pts[1])
            thickness = max(self._grid_spacing * 0.01, 1e-6)
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            p2[axis_idx] = p1[axis_idx] + thickness

            obj = PlateObject(
                material=self._draw_material,
                x1=p1[0], y1=p1[1], z1=p1[2],
                x2=p2[0], y2=p2[1], z2=p2[2],
            )
            self._finish_object(obj)

    def _fsm_planar_preview(self, sx, sy, state):
        if state != 1:
            return
        pt = self._drawing_snap_point(sx, sy)
        if pt is None:
            return

        p1 = self._draw_pts[0]
        p2 = list(pt)
        thickness = max(self._grid_spacing * 0.01, 1e-6)
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        p2[axis_idx] = p1[axis_idx] + thickness
        self._preview_box(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2])
        self.status_message.emit(
            f"Planar: second point [{pt[0]:.2f}, {pt[1]:.2f}, {pt[2]:.2f}]{self._drawing_snap_status_suffix()}"
        )

    # ──────────────────────────────────────────────────────── finish / cancel
    def _finish_object(self, obj: EMObject) -> None:
        self._remove_preview()
        self._clear_selection_point_markers()
        self.scene.add_object(obj)
        self.scene.select(obj)
        self.object_selected.emit(obj)
        self.selection_changed.emit(list(self.scene.selection))
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
        self._clear_selection_point_markers()
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._sketch_engine is not None:
            eng = self._sketch_engine
            if eng.tool is not None or eng.pending or self._sketch_axis_pick_mode:
                eng.cancel_current()
                self._sketch_axis_pick_mode = False
                self._sketch_axis_highlight = None
                self.status_message.emit("Sketch tool cancelled")
                self._refresh_sketch_overlay()
            else:
                self.exit_sketch(commit=False)
            event.accept()
            return
        super().keyPressEvent(event)

    # ─────────────────────────────────────────────────────── SKETCH MODE
    def _build_sketch_toolbar(self) -> QToolBar:
        tb = QToolBar("Sketch", self)
        tb.setMovable(False)

        def add(label: str, icon: str, slot, tip: str = "") -> QAction:
            ic = _icon(icon)
            act = QAction(ic if not ic.isNull() else QIcon(), label, self)
            if ic.isNull():
                act.setIconText(label)
            act.setToolTip(tip or label)
            act.triggered.connect(slot)
            tb.addAction(act)
            return act

        add("Line",      "Sketch_Line",      lambda: self._sketch_set_tool("line"),
            "Draw a single line segment (2 clicks)")
        add("Polyline",  "Sketch_Polyline",  lambda: self._sketch_set_tool("polyline"),
            "Draw a polyline (click to add points; Esc to finish)")
        add("Arc",       "Sketch_Arc",       lambda: self._sketch_set_tool("arc"),
            "Draw a 3-point arc: start, end, mid")
        add("Circle",    "Sketch_Circle",    lambda: self._sketch_set_tool("circle"),
            "Draw a circle (centre + radius)")
        add("Rect",      "Sketch_Rect",      lambda: self._sketch_set_tool("rect"),
            "Draw a rectangle (two opposite corners)")
        tb.addSeparator()
        add("Fillet",    "Sketch_Fillet",    self._sketch_apply_fillet,
            "Round the last polyline corner")
        add("Chamfer",   "Sketch_Chamfer",   self._sketch_apply_chamfer,
            "Chamfer the last polyline corner")
        add("Trim/Del",  "Sketch_Trim",      self._sketch_delete_last,
            "Delete the last entity")
        add("Close",     "Sketch_Close",     self._sketch_close_profile,
            "Close the active polyline")
        add("Cancel",    "Sketch_Cancel",    self._sketch_cancel_tool,
            "Cancel the current tool")
        tb.addSeparator()
        add("Extrude",   "Part_Extrude",     self._sketch_request_extrude,
            "Extrude the sketch profile")
        add("Revolve",   "Part_Revolve",     self._sketch_begin_revolve,
            "Revolve: pick a sketch line as axis")
        add("Exit",      "Sketch_Exit",      lambda: self.exit_sketch(commit=False),
            "Exit sketch mode without committing")
        return tb

    def start_sketch(self, plane_origin: tuple, plane_normal: tuple) -> None:
        self._cancel_draw()
        self.cancel_pick()
        self._sketch_engine = SketchEngine(plane_origin, plane_normal)
        # Align the viewport's projection plane with the sketch plane
        self._custom_plane_active = True
        self._custom_plane_origin = tuple(plane_origin)
        self._custom_plane_normal = tuple(plane_normal)
        self._sketch_toolbar.setVisible(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.status_message.emit(
            "Sketch mode: pick a tool from the toolbar  |  Esc to exit"
        )
        self._refresh_sketch_overlay()

    def exit_sketch(self, commit: bool = False) -> None:
        if self._sketch_engine is None:
            return
        self._remove_sketch_actors()
        self._sketch_engine = None
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self._sketch_toolbar.setVisible(False)
        self.setCursor(Qt.ArrowCursor)
        self.status_message.emit(
            "Exited sketch mode" + (" (committed)" if commit else "")
        )
        self.sketch_finished.emit()
        self._render()

    # ── tool slots ────────────────────────────────────────────────────
    def _sketch_set_tool(self, name: str) -> None:
        if self._sketch_engine is None:
            return
        # Selecting another tool cancels axis-pick
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self._sketch_engine.set_tool(name)
        self.status_message.emit(f"Sketch tool: {name}")
        self._refresh_sketch_overlay()

    def _sketch_cancel_tool(self) -> None:
        if self._sketch_engine is None:
            return
        self._sketch_engine.cancel_current()
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self.status_message.emit("Sketch tool cancelled")
        self._refresh_sketch_overlay()

    def _sketch_delete_last(self) -> None:
        if self._sketch_engine is None:
            return
        self._sketch_engine.delete_last()
        self._refresh_sketch_overlay()

    def _sketch_close_profile(self) -> None:
        if self._sketch_engine is None:
            return
        self._sketch_engine.close_profile()
        self._refresh_sketch_overlay()

    def _sketch_apply_fillet(self) -> None:
        if self._sketch_engine is None:
            return
        r, ok = QInputDialog.getDouble(
            self, "Fillet", f"Radius ({self._units}):",
            self._grid_spacing, 0.001, 1e6, 3,
        )
        if not ok:
            return
        if not self._sketch_engine.apply_fillet(float(r)):
            self.status_message.emit("Fillet requires a polyline with ≥3 points")
            return
        self._refresh_sketch_overlay()

    def _sketch_apply_chamfer(self) -> None:
        if self._sketch_engine is None:
            return
        d, ok = QInputDialog.getDouble(
            self, "Chamfer", f"Distance ({self._units}):",
            self._grid_spacing, 0.001, 1e6, 3,
        )
        if not ok:
            return
        if not self._sketch_engine.apply_chamfer(float(d)):
            self.status_message.emit("Chamfer requires a polyline with ≥3 points")
            return
        self._refresh_sketch_overlay()

    def _sketch_request_extrude(self) -> None:
        if self._sketch_engine is None:
            return
        profile = self._sketch_engine.build_profile()
        if len(profile) < 3:
            self.status_message.emit("Sketch needs at least 3 points to extrude")
            return
        depth, ok = QInputDialog.getDouble(
            self, "Extrude", f"Depth ({self._units}):",
            10.0, -1e6, 1e6, 3,
        )
        if not ok:
            return
        origin = self._sketch_engine.plane_origin
        normal = self._sketch_engine.plane_normal
        self.sketch_extrude_requested.emit(list(profile), float(depth), tuple(origin), tuple(normal))
        self.exit_sketch(commit=True)

    def _sketch_begin_revolve(self) -> None:
        if self._sketch_engine is None:
            return
        if not self._sketch_engine.entities:
            self.status_message.emit("Draw a sketch first, then pick an axis line")
            return
        self._sketch_engine.set_tool(None)
        self._sketch_axis_pick_mode = True
        self.status_message.emit("Revolve: click a sketch line to use as axis")
        self._refresh_sketch_overlay()

    # ── click / move dispatch ─────────────────────────────────────────
    def _sketch_left_press(self, sx: int, sy: int) -> None:
        uv = self._uv_from_screen(sx, sy)
        if uv is None:
            return
        if self._sketch_axis_pick_mode:
            tol = max(self._grid_spacing * 0.6, 1e-3)
            line = self._sketch_engine.find_line_at_uv(uv, tol)
            if line is None:
                self.status_message.emit("No line under cursor – click on a sketch line")
                return
            self._sketch_axis_highlight = line
            self._refresh_sketch_overlay()
            angle, ok = QInputDialog.getDouble(
                self, "Revolve", "Sweep angle (deg):",
                360.0, -360.0, 360.0, 2,
            )
            if not ok:
                self._sketch_axis_pick_mode = False
                self._sketch_axis_highlight = None
                self._refresh_sketch_overlay()
                return
            profile = self._sketch_engine.build_profile()
            if len(profile) < 2:
                self.status_message.emit("Sketch needs at least 2 profile points to revolve")
                self._sketch_axis_pick_mode = False
                self._sketch_axis_highlight = None
                self._refresh_sketch_overlay()
                return
            p1_world = self._world_from_uv(*line[0])
            p2_world = self._world_from_uv(*line[1])
            origin = tuple(self._sketch_engine.plane_origin)
            normal = tuple(self._sketch_engine.plane_normal)
            self.sketch_revolve_requested.emit(
                list(profile), float(angle),
                tuple(p1_world), tuple(p2_world),
                origin, normal,
            )
            self.exit_sketch(commit=True)
            return

        if self._sketch_engine.tool is None:
            return
        self._sketch_engine.on_click(uv)
        self._refresh_sketch_overlay()

    def _sketch_mouse_move(self, sx: int, sy: int) -> None:
        if self._sketch_engine.tool is None and not self._sketch_axis_pick_mode:
            return
        uv = self._uv_from_screen(sx, sy)
        if uv is None:
            return
        self._sketch_engine.on_move(uv)
        self._refresh_sketch_preview()

    # ── coordinate helpers ────────────────────────────────────────────
    def _uv_from_screen(self, sx: int, sy: int) -> Optional[tuple]:
        if self._sketch_engine is None:
            return None
        world = self._ray_plane_intersect(sx, sy)
        if world is None:
            return None
        world = self._snap(world)
        return world_to_uv(world, self._sketch_engine.plane_origin,
                           self._sketch_engine.u_axis,
                           self._sketch_engine.v_axis)

    def _world_from_uv(self, u: float, v: float) -> tuple:
        if self._sketch_engine is None:
            return (0.0, 0.0, 0.0)
        return uv_to_world((u, v), self._sketch_engine.plane_origin,
                           self._sketch_engine.u_axis,
                           self._sketch_engine.v_axis)

    # ── overlay rendering ─────────────────────────────────────────────
    def _remove_sketch_actors(self) -> None:
        for attr in ("_sketch_lines_actor", "_sketch_hi_actor",
                     "_sketch_verts_actor", "_sketch_preview_actor"):
            actor = getattr(self, attr)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)

    def _refresh_sketch_overlay(self) -> None:
        if self._sketch_engine is None:
            self._render()
            return
        # Remove old line/highlight/vertex actors (preview kept until next move)
        for attr in ("_sketch_lines_actor", "_sketch_hi_actor",
                     "_sketch_verts_actor"):
            actor = getattr(self, attr)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)

        normal_poly, hi_poly = self._sketch_engine.to_lines_polydata(
            highlight=self._sketch_axis_highlight
        )
        self._sketch_lines_actor = self._make_line_actor(
            normal_poly, color=(1.0, 0.5, 0.0), width=2.0
        )
        self._renderer.AddActor(self._sketch_lines_actor)
        if hi_poly.GetNumberOfCells() > 0:
            self._sketch_hi_actor = self._make_line_actor(
                hi_poly, color=(1.0, 0.85, 0.0), width=4.0
            )
            self._renderer.AddActor(self._sketch_hi_actor)
        verts_poly = self._sketch_engine.to_vertex_polydata()
        if verts_poly.GetNumberOfCells() > 0:
            self._sketch_verts_actor = self._make_vertex_actor(verts_poly)
            self._renderer.AddActor(self._sketch_verts_actor)
        self._refresh_sketch_preview()

    def _refresh_sketch_preview(self) -> None:
        if self._sketch_preview_actor is not None:
            self._renderer.RemoveActor(self._sketch_preview_actor)
            self._sketch_preview_actor = None
        if self._sketch_engine is None:
            self._render()
            return
        prev = self._sketch_engine.to_preview_polydata()
        if prev.GetNumberOfCells() > 0:
            self._sketch_preview_actor = self._make_line_actor(
                prev, color=(1.0, 0.5, 0.0), width=1.5, dashed=True
            )
            self._renderer.AddActor(self._sketch_preview_actor)
        self._render()

    @staticmethod
    def _make_line_actor(poly: vtk.vtkPolyData, color: tuple,
                         width: float, dashed: bool = False) -> vtk.vtkActor:
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetLineWidth(width)
        prop.SetLighting(False)
        if dashed:
            prop.SetLineStipplePattern(0xF0F0)
            prop.SetLineStippleRepeatFactor(1)
        actor.PickableOff()
        return actor

    def _make_vertex_actor(self, poly: vtk.vtkPolyData) -> vtk.vtkActor:
        size = max(self._grid_spacing * 0.18, 0.4)
        sphere = vtk.vtkSphereSource()
        sphere.SetRadius(size)
        sphere.SetPhiResolution(10)
        sphere.SetThetaResolution(10)
        glyph = vtk.vtkGlyph3D()
        glyph.SetInputData(poly)
        glyph.SetSourceConnection(sphere.GetOutputPort())
        glyph.ScalingOff()
        glyph.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(glyph.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(1.0, 0.5, 0.0)
        prop.SetLighting(False)
        actor.PickableOff()
        return actor
