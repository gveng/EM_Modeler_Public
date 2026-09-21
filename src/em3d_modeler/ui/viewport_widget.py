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

from collections import deque
import math
from typing import Optional, Tuple

import vtk
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QSizePolicy, QToolBar, QInputDialog
)
from PySide6.QtCore    import Signal, Qt
from PySide6.QtGui     import QIcon, QAction

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
    @staticmethod
    def _axis_plane_from_normal(normal) -> str:
        """Map a plane normal to the closest axis-aligned drawing mode."""
        nx, ny, nz = abs(float(normal[0])), abs(float(normal[1])), abs(float(normal[2]))
        if nz >= nx and nz >= ny:
            return "XY"
        if ny >= nx and ny >= nz:
            return "XZ"
        return "YZ"

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
    object_selected   = Signal(object)        # EMObject | None
    # Emitted when the multi-selection changes
    selection_changed = Signal(list)           # List[EMObject]
    # Emitted when the scene changes (add/remove objects)
    scene_changed     = Signal()
    # Status-bar message
    status_message    = Signal(str)
    # Coordinates for sub-element pick display (X,Y,Z,units)
    picked_coords     = Signal(float, float, float, str)
    clear_coords_requested = Signal()
    # Sketch-mode signals
    sketch_extrude_requested = Signal(list, float, tuple, tuple)
    sketch_revolve_requested = Signal(list, float, tuple, tuple, tuple, tuple)
    sketch_finished          = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ── VTK setup ──────────────────────────────────────────────────────────
        self._vtk_widget   = QVTKRenderWindowInteractor(self)
        self._renderer     = vtk.vtkRenderer()
        self._render_window = self._vtk_widget.GetRenderWindow()
        self._render_window.SetAlphaBitPlanes(1)
        self._render_window.SetMultiSamples(0)
        self._renderer.UseDepthPeelingOn()
        self._renderer.SetMaximumNumberOfPeels(100)
        self._renderer.SetOcclusionRatio(0.1)
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
        self._snap_records: list[dict] = []
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
        self._snap_records  = []
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

        # Keep the displayed world-axis directions stable when switching
        # between the built-in planes.  A generic cross-product basis can
        # legitimately choose -X or -Y, which makes the triad appear to flip.
        if abs(n[2]) > 1.0 - 1e-9:
            return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), n
        if abs(n[1]) > 1.0 - 1e-9:
            return (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), n
        if abs(n[0]) > 1.0 - 1e-9:
            return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), n

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

        # vtkAxesActor assigns red/green/blue to its local X/Y/Z axes.  The
        # plane triad is transformed into world coordinates, so those local
        # colors must be remapped to keep world X/Y/Z consistently red/green/blue.
        world_colors = (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        local_axes = (x_axis, y_axis, z_axis)
        local_labels = ("X", "Y", "Z")
        for axis_index, axis_vector in enumerate(local_axes):
            world_axis_index = max(range(3), key=lambda i: abs(axis_vector[i]))
            color = world_colors[world_axis_index]
            if axis_index == 0:
                shaft = self._plane_triad_actor.GetXAxisShaftProperty()
                tip = self._plane_triad_actor.GetXAxisTipProperty()
                caption = self._plane_triad_actor.GetXAxisCaptionActor2D()
            elif axis_index == 1:
                shaft = self._plane_triad_actor.GetYAxisShaftProperty()
                tip = self._plane_triad_actor.GetYAxisTipProperty()
                caption = self._plane_triad_actor.GetYAxisCaptionActor2D()
            else:
                shaft = self._plane_triad_actor.GetZAxisShaftProperty()
                tip = self._plane_triad_actor.GetZAxisTipProperty()
                caption = self._plane_triad_actor.GetZAxisCaptionActor2D()
            shaft.SetColor(*color)
            tip.SetColor(*color)
            caption.GetTextActor().SetInput(local_labels[world_axis_index])
            caption.GetTextActor().GetTextProperty().SetColor(*color)

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
        self._draw_plane = self._axis_plane_from_normal(normal)
        # Ricostruisce la griglia su questo piano
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
        if mode == "object":
            self.clear_coords_requested.emit()
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
        mode = "vertex" if kind == "vertex" else "face"
        picker.SetTolerance(self._pick_tolerance_for_mode(mode))
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
                pp.SetTolerance(self._pick_tolerance_for_mode("vertex"))
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
            point_picker.SetTolerance(self._pick_tolerance_for_mode("vertex"))
            point_picker.Pick(sx, sy, 0, self._renderer)
            point_actor = point_picker.GetActor()
            point_id = point_picker.GetPointId()
            if point_actor is not None and point_id >= 0 and point_picker.GetDataSet() is not None:
                local = point_picker.GetDataSet().GetPoint(point_id)
                world = point_actor.GetMatrix().MultiplyPoint([local[0], local[1], local[2], 1.0])
                point = tuple(self._project_point_to_draw_plane((world[0], world[1], world[2])))
                source = next((obj for obj in self.scene.objects if obj.actor is point_actor), None)
                self._snap_records.append({
                    "kind": "vertex",
                    "object": str(source.name) if source is not None else "",
                    "point_id": int(point_id),
                    "local": [float(local[i]) for i in range(3)],
                    "point": list(point),
                })
                return (point, "vertex")
            if mode == "vertex":
                return None

        # Edge / face snap: use the picked cell and evaluate based on filter.
        cell_picker = vtk.vtkCellPicker()
        cell_picker.SetTolerance(self._pick_tolerance_for_mode(mode if mode in {"edge", "face"} else "face"))
        cell_picker.Pick(sx, sy, 0, self._renderer)
        actor = cell_picker.GetActor()
        ds = cell_picker.GetDataSet()
        cell_id = cell_picker.GetCellId()
        if actor is None or ds is None or cell_id < 0:
            return None

        pos = cell_picker.GetPickPosition()
        if mode in {"all", "edge"}:
            edge_pick = self._pick_edge_segment_local(ds, cell_id, actor, pos)
            if edge_pick is not None:
                _, closest_local = edge_pick
                m = actor.GetMatrix()
                world4 = m.MultiplyPoint([closest_local[0], closest_local[1], closest_local[2], 1.0])
                w = world4[3] if abs(world4[3]) > 1e-12 else 1.0
                world = (world4[0] / w, world4[1] / w, world4[2] / w)
                point = tuple(self._project_point_to_draw_plane(world))
                source = next((obj for obj in self.scene.objects if obj.actor is actor), None)
                self._snap_records.append({
                    "kind": "edge",
                    "object": str(source.name) if source is not None else "",
                    "cell_id": int(cell_id),
                    "local": [float(closest_local[i]) for i in range(3)],
                    "point": list(point),
                })
                return (point, "edge")
            if mode == "edge":
                return None

        if mode in {"all", "face"}:
            point = tuple(self._project_point_to_draw_plane(pos))
            source = next((obj for obj in self.scene.objects if obj.actor is actor), None)
            inverse = vtk.vtkMatrix4x4()
            vtk.vtkMatrix4x4.Invert(actor.GetMatrix(), inverse)
            local4 = inverse.MultiplyPoint([pos[0], pos[1], pos[2], 1.0])
            self._snap_records.append({
                "kind": "surface",
                "object": str(source.name) if source is not None else "",
                "cell_id": int(cell_id),
                "local": [float(local4[i]) for i in range(3)],
                "point": list(point),
            })
            return (point, "surface")

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

    def _active_drawing_plane(self) -> str:
        """Return the logical plane used for active sketching, including custom planes."""
        if self._custom_plane_active:
            return self._axis_plane_from_normal(self._custom_plane_normal)
        return self._draw_plane

    def _active_plane_axis(self) -> str:
        plane = self._active_drawing_plane()
        return {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]

    def _active_plane_axis_index(self) -> int:
        plane = self._active_drawing_plane()
        return {"XY": 2, "XZ": 1, "YZ": 0}[plane]

    def _height_from_cursor(
        self, screen_x: int, screen_y: int, base_z: float
    ) -> float:
        """For height step: project cursor to vertical axis through base."""
        picker = vtk.vtkWorldPointPicker()
        picker.Pick(screen_x, screen_y, 0, self._renderer)
        p = picker.GetPickPosition()
        axis_idx = self._active_plane_axis_index()
        return p[axis_idx] - base_z

    def _plane_radius(self, pt: tuple, center: tuple) -> float:
        """2D distance on the drawing plane (plane-aware)."""
        plane = self._active_drawing_plane()
        if plane == "XY":
            return math.sqrt((pt[0] - center[0])**2 + (pt[1] - center[1])**2)
        elif plane == "XZ":
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

    def _add_selection_point_marker(
        self,
        world_pt: tuple,
        color: tuple[float, float, float] = (1.0, 0.05, 0.05),
    ) -> None:
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
        actor.GetProperty().SetColor(color[0], color[1], color[2])
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
                from PySide6.QtWidgets import QApplication
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
            self.clear_coords_requested.emit()
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
        picker.SetTolerance(self._pick_tolerance_for_mode(self._selection_mode))
        picker.Pick(sx, sy, 0, self._renderer)
        actor = picker.GetActor()
        if actor is None:
            self._clear_sub_pick_marker()
            self.clear_coords_requested.emit()
            self.status_message.emit("No object under cursor.")
            self._render()
            return

        cell_id = picker.GetCellId()
        pos     = picker.GetPickPosition()
        ds      = picker.GetDataSet()
        if ds is None or cell_id < 0:
            self.clear_coords_requested.emit()
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
            marker = self._build_face_region_marker(ds, cell_id, actor)
            self.picked_coords.emit(float(pos[0]), float(pos[1]), float(pos[2]), self._units)
            self.status_message.emit(
                f"Face picked  cell={cell_id}  on {owner.name if owner else '?'}  "
                f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
            )
        elif mode == "edge":
            edge_pick = self._pick_edge_segment_local(ds, cell_id, actor, pos)
            marker = None
            edge_world = pos
            if edge_pick is not None:
                edge_points, closest_local = edge_pick
                marker = self._build_edge_marker(edge_points, actor)
                w = actor.GetMatrix().MultiplyPoint([closest_local[0], closest_local[1], closest_local[2], 1.0])
                ww = w[3] if abs(w[3]) > 1e-12 else 1.0
                edge_world = (w[0] / ww, w[1] / ww, w[2] / ww)
            self.picked_coords.emit(float(edge_world[0]), float(edge_world[1]), float(edge_world[2]), self._units)
            self.status_message.emit(
                f"Edge picked  on {owner.name if owner else '?'}  "
                f"({edge_world[0]:.2f}, {edge_world[1]:.2f}, {edge_world[2]:.2f})"
            )
        else:  # vertex
            point_picker = vtk.vtkPointPicker()
            point_picker.SetTolerance(self._pick_tolerance_for_mode("vertex"))
            point_picker.Pick(sx, sy, 0, self._renderer)
            pid = point_picker.GetPointId()
            if pid >= 0 and point_picker.GetDataSet() is not None:
                vp_local = point_picker.GetDataSet().GetPoint(pid)
                vp4 = actor.GetMatrix().MultiplyPoint([vp_local[0], vp_local[1], vp_local[2], 1.0])
                w = vp4[3] if abs(vp4[3]) > 1e-12 else 1.0
                vp = (vp4[0] / w, vp4[1] / w, vp4[2] / w)
            else:
                vp = pos
            marker = self._build_vertex_marker(vp, actor)
            self.picked_coords.emit(float(vp[0]), float(vp[1]), float(vp[2]), self._units)
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

    def _build_face_region_marker(self, dataset, cell_id: int, ref_actor) -> Optional[vtk.vtkActor]:
        poly = vtk.vtkPolyData.SafeDownCast(dataset)
        if poly is None:
            return self._build_face_marker(dataset, cell_id, ref_actor)

        face_ids = poly.GetCellData().GetArray("OCCFaceId")
        if face_ids is not None and cell_id < face_ids.GetNumberOfTuples():
            face_id = face_ids.GetValue(cell_id)
            region_ids = {
                cell_index
                for cell_index in range(poly.GetNumberOfCells())
                if face_ids.GetValue(cell_index) == face_id
            }
        else:
            region_ids = self._coplanar_region_cell_ids(poly, cell_id)
        if not region_ids:
            return self._build_face_marker(dataset, cell_id, ref_actor)

        ids = vtk.vtkIdTypeArray()
        for cid in sorted(region_ids):
            ids.InsertNextValue(int(cid))

        sel_node = vtk.vtkSelectionNode()
        sel_node.SetFieldType(vtk.vtkSelectionNode.CELL)
        sel_node.SetContentType(vtk.vtkSelectionNode.INDICES)
        sel_node.SetSelectionList(ids)
        sel = vtk.vtkSelection()
        sel.AddNode(sel_node)

        extract = vtk.vtkExtractSelection()
        extract.SetInputData(0, poly)
        extract.SetInputData(1, sel)
        extract.Update()

        surf = vtk.vtkDataSetSurfaceFilter()
        surf.SetInputConnection(extract.GetOutputPort())
        surf.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(surf.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetUserMatrix(ref_actor.GetMatrix())
        actor.GetProperty().SetColor(1.0, 0.6, 0.0)
        actor.GetProperty().SetOpacity(0.85)
        actor.GetProperty().EdgeVisibilityOn()
        actor.GetProperty().SetEdgeColor(1.0, 0.9, 0.0)
        actor.GetProperty().SetLineWidth(2.0)
        actor.PickableOff()
        return actor

    def _pick_edge_segment_local(self, dataset, cell_id: int, ref_actor, pos):
        """Return the complete nearest feature edge and its closest local point."""
        m = ref_actor.GetMatrix()
        inv = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(m, inv)
        local = inv.MultiplyPoint([pos[0], pos[1], pos[2], 1.0])
        lp = (local[0], local[1], local[2])

        poly = vtk.vtkPolyData.SafeDownCast(dataset)
        best_edge = None
        best_dist = float("inf")

        # Prefer geometric feature edges (creases/boundaries) over triangle edges.
        if poly is not None:
            feat = vtk.vtkFeatureEdges()
            feat.SetInputData(poly)
            feat.BoundaryEdgesOn()
            feat.FeatureEdgesOn()
            feat.NonManifoldEdgesOn()
            feat.ManifoldEdgesOff()
            feat.SetFeatureAngle(35.0)
            feat.Update()

            edge_poly = feat.GetOutput()
            if edge_poly is not None and edge_poly.GetNumberOfCells() > 0:
                for cid in range(edge_poly.GetNumberOfCells()):
                    ec = edge_poly.GetCell(cid)
                    if ec is None:
                        continue
                    npts = ec.GetNumberOfPoints()
                    if npts < 2:
                        continue
                    for i in range(npts - 1):
                        p0 = ec.GetPoints().GetPoint(i)
                        p1 = ec.GetPoints().GetPoint(i + 1)
                        closest_local, d = self._closest_point_on_segment(lp, p0, p1)
                        if d < best_dist:
                            best_dist = d
                            best_edge = (edge_poly, cid, closest_local)

        if best_edge is not None:
            edge_poly, cell_id, closest_local = best_edge
            return self._feature_edge_polyline(edge_poly, cell_id), closest_local

        # Fallback: closest edge on picked triangle/cell.
        cell = dataset.GetCell(cell_id)
        if cell is None or cell.GetNumberOfEdges() == 0:
            return None

        for ei in range(cell.GetNumberOfEdges()):
            e = cell.GetEdge(ei)
            p0 = e.GetPoints().GetPoint(0)
            p1 = e.GetPoints().GetPoint(1)
            closest_local, d = self._closest_point_on_segment(lp, p0, p1)
            if d < best_dist:
                best_dist = d
                best_edge = (p0, p1, closest_local)
        if best_edge is None:
            return None

        p0, p1, closest_local = best_edge
        return [p0, p1], closest_local

    def _feature_edge_polyline(self, edge_poly, cell_id: int) -> list:
        """Join collinear feature segments while stopping at a hard corner."""
        cell = edge_poly.GetCell(cell_id)
        if cell is None or cell.GetNumberOfPoints() < 2:
            return []
        start_id, end_id = cell.GetPointId(0), cell.GetPointId(1)
        adjacency = {}
        for index in range(edge_poly.GetNumberOfCells()):
            segment = edge_poly.GetCell(index)
            if segment is None or segment.GetNumberOfPoints() != 2:
                continue
            first, second = segment.GetPointId(0), segment.GetPointId(1)
            adjacency.setdefault(first, []).append(second)
            adjacency.setdefault(second, []).append(first)

        def extend(previous_id, current_id):
            chain = []
            while True:
                candidates = [item for item in adjacency.get(current_id, []) if item != previous_id]
                if not candidates:
                    break
                previous = edge_poly.GetPoint(previous_id)
                current = edge_poly.GetPoint(current_id)
                incoming = [current[index] - previous[index] for index in range(3)]
                incoming_length = math.sqrt(sum(value * value for value in incoming))
                if incoming_length <= 1e-12:
                    break
                next_id = max(
                    candidates,
                    key=lambda item: sum(
                        incoming[index] * (edge_poly.GetPoint(item)[index] - current[index])
                        for index in range(3)
                    ),
                )
                following = edge_poly.GetPoint(next_id)
                outgoing = [following[index] - current[index] for index in range(3)]
                outgoing_length = math.sqrt(sum(value * value for value in outgoing))
                if outgoing_length <= 1e-12:
                    break
                alignment = sum(incoming[index] * outgoing[index] for index in range(3)) / (incoming_length * outgoing_length)
                if alignment < 0.75:
                    break
                chain.append(next_id)
                previous_id, current_id = current_id, next_id
            return chain

        left = extend(end_id, start_id)
        right = extend(start_id, end_id)
        point_ids = list(reversed(left)) + [start_id, end_id] + right
        return [edge_poly.GetPoint(point_id) for point_id in point_ids]

    def _build_edge_marker(self, edge_points, ref_actor) -> Optional[vtk.vtkActor]:
        if len(edge_points) < 2:
            return None

        pts = vtk.vtkPoints()
        for point in edge_points:
            pts.InsertNextPoint(*point)
        line = vtk.vtkCellArray()
        polyline = vtk.vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(len(edge_points))
        for index in range(len(edge_points)):
            polyline.GetPointIds().SetId(index, index)
        line.InsertNextCell(polyline)
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

    def _pick_tolerance_for_mode(self, mode: str) -> float:
        """Return vtk picker tolerance as screen fraction from pixel target."""
        rw = self._render_window
        width, height = rw.GetSize() if rw is not None else (1280, 720)
        diag = max(float(math.hypot(max(width, 1), max(height, 1))), 1.0)
        px = 7.0
        if mode == "vertex":
            px = 11.0
        elif mode == "edge":
            px = 10.0
        elif mode == "face":
            px = 8.0
        tol = px / diag
        return max(0.0015, min(0.02, tol))

    @staticmethod
    def _cell_normal_local(poly, cell_id: int):
        cell = poly.GetCell(cell_id)
        if cell is None or cell.GetNumberOfPoints() < 3:
            return None, None
        p0 = cell.GetPoints().GetPoint(0)
        p1 = cell.GetPoints().GetPoint(1)
        p2 = cell.GetPoints().GetPoint(2)
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        nx = e1[1] * e2[2] - e1[2] * e2[1]
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        nz = e1[0] * e2[1] - e1[1] * e2[0]
        mag = math.sqrt(nx * nx + ny * ny + nz * nz)
        if mag < 1e-12:
            return None, None
        centroid = [0.0, 0.0, 0.0]
        n = cell.GetNumberOfPoints()
        for i in range(n):
            p = cell.GetPoints().GetPoint(i)
            centroid[0] += p[0]
            centroid[1] += p[1]
            centroid[2] += p[2]
        centroid = (centroid[0] / n, centroid[1] / n, centroid[2] / n)
        return (nx / mag, ny / mag, nz / mag), centroid

    def _coplanar_region_cell_ids(self, poly, seed_cell_id: int) -> set[int]:
        if poly is None or seed_cell_id < 0 or seed_cell_id >= poly.GetNumberOfCells():
            return set()

        seed_normal, seed_centroid = self._cell_normal_local(poly, seed_cell_id)
        if seed_normal is None or seed_centroid is None:
            return {seed_cell_id}

        bounds = [0.0] * 6
        poly.GetBounds(bounds)
        dx = bounds[1] - bounds[0]
        dy = bounds[3] - bounds[2]
        dz = bounds[5] - bounds[4]
        diag = math.sqrt(max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2 + max(dz, 0.0) ** 2)
        dist_tol = max(diag * 1e-4, 1e-5)
        cos_tol = math.cos(math.radians(10.0))
        plane_d = (
            seed_normal[0] * seed_centroid[0]
            + seed_normal[1] * seed_centroid[1]
            + seed_normal[2] * seed_centroid[2]
        )

        visited = set([seed_cell_id])
        q = deque([seed_cell_id])
        neigh_ids = vtk.vtkIdList()

        while q:
            cid = q.popleft()
            cell = poly.GetCell(cid)
            if cell is None:
                continue

            for ei in range(cell.GetNumberOfEdges()):
                edge = cell.GetEdge(ei)
                if edge is None:
                    continue
                poly.GetCellNeighbors(cid, edge.GetPointIds(), neigh_ids)
                for ni in range(neigh_ids.GetNumberOfIds()):
                    nid = int(neigh_ids.GetId(ni))
                    if nid in visited:
                        continue
                    nrm, ctr = self._cell_normal_local(poly, nid)
                    if nrm is None or ctr is None:
                        continue
                    dot = abs(
                        nrm[0] * seed_normal[0]
                        + nrm[1] * seed_normal[1]
                        + nrm[2] * seed_normal[2]
                    )
                    if dot < cos_tol:
                        continue
                    plane_dist = abs(
                        seed_normal[0] * ctr[0]
                        + seed_normal[1] * ctr[1]
                        + seed_normal[2] * ctr[2]
                        - plane_d
                    )
                    if plane_dist > dist_tol:
                        continue
                    visited.add(nid)
                    q.append(nid)

        return visited

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
        plane = self._active_drawing_plane()
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[plane]
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
        plane = self._active_drawing_plane()
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[plane]
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
        plane = self._active_drawing_plane()
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[plane]
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
        plane = self._active_drawing_plane()
        axis = {"XY": "Z", "XZ": "Y", "YZ": "X"}[plane]
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[plane]
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
        plane_name = self._active_drawing_plane()
        origin = self._custom_plane_origin if self._custom_plane_active else PLANE_ORIGIN.get(plane_name, (0.0, 0.0, 0.0))
        normal = self._custom_plane_normal if self._custom_plane_active else PLANE_NORMAL.get(plane_name, (0.0, 0.0, 1.0))
        obj.set_creation_plane(plane_name, origin, normal)
        history_points = []
        for point in self._draw_pts:
            nearest = None
            distance = float("inf")
            for record in self._snap_records:
                candidate = record.get("point", [])
                if len(candidate) != 3:
                    continue
                current_distance = sum((float(point[i]) - float(candidate[i])) ** 2 for i in range(3))
                if current_distance < distance:
                    nearest = record
                    distance = current_distance
            history_points.append({
                "value": [float(point[i]) for i in range(3)],
                "snap": dict(nearest) if nearest is not None and distance < 1e-8 else {"kind": "grid"},
            })
        obj.creation_history = {
            "mode": str(self._draw_mode or ""),
            "plane": plane_name,
            "points": history_points,
        }
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
        self._snap_records = []
        self.setCursor(Qt.ArrowCursor)
        self._render()

    def _cancel_draw(self) -> None:
        was_planar = (self._draw_mode == "planar")
        self._remove_preview()
        self._clear_selection_point_markers()
        if was_planar:
            self._clear_sub_pick_marker()
            self._planar_side_edge = None
            self.clear_coords_requested.emit()
        self._draw_mode  = None
        self._draw_state = 0
        self._draw_pts   = []
        self._snap_records = []
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
