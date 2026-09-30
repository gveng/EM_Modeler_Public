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
from typing import Callable, Optional, Tuple

import vtk
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QSizePolicy, QToolBar, QInputDialog, QDialog,
    QFormLayout, QDoubleSpinBox, QComboBox, QCheckBox, QDialogButtonBox,
)
from PySide6.QtCore    import Signal, Qt
from PySide6.QtGui     import QIcon, QAction, QKeySequence, QShortcut

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except ImportError:
    from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from pathlib import Path

from ..drawing.interactor_style import EMInteractorStyle
from ..drawing.sketch_engine    import (
    SketchEngine, _rect_polyline, plane_basis, uv_to_world, world_to_uv,
)
from ..scene.scene_manager      import SceneManager
from ..scene.em_objects         import (
    EMObject, BoxObject, CylinderObject, ConeObject, SphereObject,
    PlateObject, PyramidObject, WedgeObject, TorusObject, EllipsoidObject
)
from ..scene import em_objects as scene_objects
from ..scene.grid_actor         import build_axes_widget, plane_basis_from_normal
from .measurement import measurement_arrow_size, normal_for_planar_points


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
    projection_changed = Signal(bool)
    measurement_mode_changed = Signal(bool)
    # Status-bar message
    status_message    = Signal(str)
    # Coordinates for sub-element pick display (X,Y,Z,units)
    picked_coords     = Signal(float, float, float, str)
    clear_coords_requested = Signal()
    # Sketch-mode signals
    sketch_extrude_requested = Signal(list, float, tuple, tuple, str, bool)
    sketch_cut_requested     = Signal(list, float, tuple, tuple)
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
        self._style.right_press_callback = self._on_sketch_right_press
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

        # Selection mode also controls drawing snap: 'grid' bypasses geometry picks.
        self._selection_mode: str = "object"
        self._sub_pick_actor: Optional[vtk.vtkActor] = None
        self._last_face_pick: dict | None = None
        self._last_edge_pick: dict | None = None
        self._last_drawing_snap_kind: str = "grid"

        # One-shot pick request from external dialogs
        # tuple (kind, callback) where kind ∈ {'point','vertex','face_normal','face_origin_normal'}
        self._pick_request = None
        self._measurement_active = False
        self._measurement_features: list[dict] = []
        self._measurement_highlights: list = []
        self._measurement_actors: list = []
        self._measurement_escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._measurement_escape_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._measurement_escape_shortcut.setEnabled(False)
        self._measurement_escape_shortcut.activated.connect(self._finish_measurement)

        # ── Sketch state ──────────────────────────────────────────────────────
        self._sketch_engine: Optional[SketchEngine] = None
        self._sketch_lines_actor: Optional[vtk.vtkActor]   = None
        self._sketch_construction_actor: Optional[vtk.vtkActor] = None
        self._sketch_region_actor: Optional[vtk.vtkActor] = None
        self._sketch_hi_actor:    Optional[vtk.vtkActor]   = None
        self._sketch_preview_actor: Optional[vtk.vtkActor] = None
        self._sketch_dimension_actors: list = []
        self._sketch_axis_pick_mode: bool = False
        self._sketch_axis_highlight: Optional[Tuple[Tuple[float, float],
                                                    Tuple[float, float]]] = None
        self._sketch_dimension_kind: Optional[str] = None
        self._sketch_dimension_picks: list = []
        self._sketch_dimension_resolver: Optional[Callable[[str], float]] = None
        self._last_sketch_definition: dict = {}
        self._sketch_vertex_snap_enabled = True
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
        elif mode == "circular_plate":
            self.status_message.emit(
                f"Circular Plate: click center, then radius (grid snap {self._grid_spacing:g} {self._units})"
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

    def set_adaptive_grid(self, enabled: bool, margin: float) -> None:
        self.scene.set_adaptive_grid(enabled, margin)
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

    def is_parallel_projection(self) -> bool:
        return bool(self._renderer.GetActiveCamera().GetParallelProjection())

    def set_parallel_projection(self, enabled: bool) -> None:
        cam = self._renderer.GetActiveCamera()
        enabled = bool(enabled)
        if bool(cam.GetParallelProjection()) == enabled:
            return

        position = cam.GetPosition()
        focal_point = cam.GetFocalPoint()
        direction = tuple(position[index] - focal_point[index] for index in range(3))
        distance = math.sqrt(sum(component * component for component in direction))
        tangent = math.tan(math.radians(float(cam.GetViewAngle())) * 0.5)
        if distance > 1e-12 and tangent > 1e-12:
            if enabled:
                cam.SetParallelScale(distance * tangent)
            else:
                distance = float(cam.GetParallelScale()) / tangent
                direction_length = math.sqrt(sum(component * component for component in direction))
                cam.SetPosition(*(
                    focal_point[index] + direction[index] / direction_length * distance
                    for index in range(3)
                ))

        cam.SetParallelProjection(enabled)
        self._renderer.ResetCameraClippingRange()
        self._render()
        self.projection_changed.emit(enabled)

    def fit_all(self) -> None:
        self._fit_camera_to_objects(self.scene.objects)

    def fit_selection(self) -> None:
        self._fit_camera_to_objects(self.scene.selection)

    def isometric_view(self) -> None:
        objects = [obj for obj in self.scene.objects if obj.is_visible()]
        bounds = self._visible_objects_bounds(objects)
        if bounds is None:
            return
        center = ((bounds[0] + bounds[1]) * 0.5,
                  (bounds[2] + bounds[3]) * 0.5,
                  (bounds[4] + bounds[5]) * 0.5)
        extent = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4], 1.0)
        camera = self._renderer.GetActiveCamera()
        camera.SetFocalPoint(*center)
        camera.SetPosition(center[0] + extent, center[1] - extent, center[2] + extent)
        camera.SetViewUp(0.0, 0.0, 1.0)
        self._renderer.ResetCamera(*bounds)
        self._renderer.ResetCameraClippingRange()
        self._render()

    def _fit_camera_to_objects(self, objects) -> None:
        bounds = self._visible_objects_bounds(objects)
        if bounds is None:
            return
        self._renderer.ResetCamera(*bounds)
        self._renderer.ResetCameraClippingRange()
        self._render()

    @staticmethod
    def _visible_objects_bounds(objects) -> tuple | None:
        object_bounds = [
            obj.actor.GetBounds()
            for obj in objects
            if obj.actor is not None and obj.is_visible()
        ]
        if not object_bounds:
            return None
        return (
            min(bounds[0] for bounds in object_bounds), max(bounds[1] for bounds in object_bounds),
            min(bounds[2] for bounds in object_bounds), max(bounds[3] for bounds in object_bounds),
            min(bounds[4] for bounds in object_bounds), max(bounds[5] for bounds in object_bounds),
        )

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
        self.projection_changed.emit(bool(cam.GetParallelProjection()))

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
        """Set selection and drawing-snap mode, including a geometry-independent grid snap."""
        if mode not in ("object", "face", "edge", "vertex", "grid"):
            return
        self._selection_mode = mode
        self._clear_sub_pick_marker()
        if mode in ("object", "grid"):
            self.clear_coords_requested.emit()
        self._render()

    def _clear_sub_pick_marker(self) -> None:
        if self._sub_pick_actor is not None:
            self._renderer.RemoveActor(self._sub_pick_actor)
            self._sub_pick_actor = None

    def start_measurement(self) -> None:
        """Activate direct viewport measurement using the current selection snap mode."""
        if self._sketch_engine is not None:
            self.status_message.emit("Finish or exit the sketch before measuring.")
            return
        self._cancel_draw()
        self._clear_measurement_actors()
        self._measurement_features.clear()
        self._measurement_active = True
        self._measurement_escape_shortcut.setEnabled(True)
        self.measurement_mode_changed.emit(True)
        self.setFocus(Qt.OtherFocusReason)
        self.setCursor(Qt.CrossCursor)
        self.status_message.emit(self._measurement_prompt(1))

    def _finish_measurement(self) -> None:
        if not (
            self._measurement_active
            or self._measurement_highlights
            or self._measurement_actors
        ):
            return
        self._measurement_active = False
        self._measurement_escape_shortcut.setEnabled(False)
        self._clear_measurement_actors()
        self._measurement_features.clear()
        self.unsetCursor()
        self._render()
        self.measurement_mode_changed.emit(False)
        self.status_message.emit("Measurement finished; annotations cleared.")

    def _measurement_prompt(self, index: int) -> str:
        prompts = {
            "vertex": "pick a snapped vertex",
            "edge": "pick a snapped edge point",
            "face": "pick a planar surface",
            "object": "pick a point on geometry",
            "grid": "pick a snapped point on the drawing grid",
        }
        prompt = prompts.get(self._selection_mode, prompts["object"])
        return f"Measure: {prompt} ({index}/2). Esc to cancel and clear."

    def _clear_measurement_actors(self) -> None:
        for actor in (*self._measurement_highlights, *self._measurement_actors):
            self._renderer.RemoveActor(actor)
        self._measurement_highlights.clear()
        self._measurement_actors.clear()

    def _measurement_click(self, sx: int, sy: int) -> None:
        feature, highlight, error = self._pick_measurement_feature(sx, sy)
        if error:
            self.status_message.emit(error)
            return
        if feature is None:
            return

        if highlight is not None:
            self._renderer.AddActor(highlight)
            self._measurement_highlights.append(highlight)
        self._measurement_features.append(feature)
        self._add_measurement_text(
            f"{'A' if len(self._measurement_features) == 1 else 'B'}",
            feature.get("point", feature.get("origin")),
            (1.0, 0.65, 0.12),
        )
        self._render()

        if len(self._measurement_features) == 1:
            self.status_message.emit(self._measurement_prompt(2))
            return

        from .measurement import calculate_measurement

        try:
            result = calculate_measurement(*self._measurement_features)
        except (KeyError, TypeError, ValueError) as exc:
            self.status_message.emit(f"Measurement failed: {exc}")
            return
        self._draw_measurement_result(*self._measurement_features, result)
        self._measurement_active = False
        self.unsetCursor()
        self.status_message.emit(self._measurement_result_text(result))

    def _pick_measurement_feature(self, sx: int, sy: int):
        mode = self._selection_mode
        if mode == "grid":
            plane_point = self._ray_plane_intersect(sx, sy)
            if plane_point is None:
                return None, None, "Could not project cursor onto the active drawing plane."
            point = self._snap(plane_point)
            return {"kind": "point", "point": point}, self._build_vertex_marker(point, None), None
        if mode == "vertex":
            picker = vtk.vtkPointPicker()
            picker.SetTolerance(self._pick_tolerance_for_mode("vertex"))
            picker.Pick(sx, sy, 0, self._renderer)
            actor = picker.GetActor()
            dataset = picker.GetDataSet()
            point_id = picker.GetPointId()
            if actor is None or dataset is None or point_id < 0:
                return None, None, "No snapped vertex under cursor; try again."
            point = self._actor_point_to_world(actor, dataset.GetPoint(point_id))
            return {"kind": "point", "point": point}, self._build_vertex_marker(point, actor), None

        picker = vtk.vtkCellPicker()
        picker.SetTolerance(self._pick_tolerance_for_mode(mode if mode in ("face", "edge") else "face"))
        picker.Pick(sx, sy, 0, self._renderer)
        actor = picker.GetActor()
        dataset = picker.GetDataSet()
        cell_id = picker.GetCellId()
        if actor is None or dataset is None or cell_id < 0:
            return None, None, "No geometry under cursor; try again."
        position = tuple(float(value) for value in picker.GetPickPosition())

        if mode == "face":
            region_ids = self._face_region_cell_ids(dataset, cell_id)
            points = self._face_region_points_world(dataset, region_ids, actor)
            normal = normal_for_planar_points(points)
            if normal is None:
                return None, None, "That face is not planar; pick a planar surface."
            owner = self._owner_for_actor(actor)
            feature = {
                "kind": "surface",
                "origin": position,
                "normal": normal,
                "name": str(owner.name) if owner is not None else "Surface",
            }
            highlight = self._build_face_region_marker(dataset, cell_id, actor, region_ids)
            return feature, highlight, None

        if mode == "edge":
            edge_pick = self._pick_edge_segment_local(dataset, cell_id, actor, position)
            if edge_pick is None:
                return None, None, "No edge snap found under cursor; try again."
            edge_points, local_point = edge_pick
            point = self._actor_point_to_world(actor, local_point)
            return {"kind": "point", "point": point}, self._build_edge_marker(edge_points, actor), None

        return {"kind": "point", "point": position}, self._build_vertex_marker(position, actor), None

    def _add_measurement_vector(
        self, start, end, label: str, color, label_offset=(0, 18), arrow_size=None
    ) -> None:
        start = tuple(float(value) for value in start)
        end = tuple(float(value) for value in end)
        delta = tuple(end[index] - start[index] for index in range(3))
        length = math.sqrt(sum(value * value for value in delta))
        if length <= 1e-10:
            return

        line = vtk.vtkLineSource()
        line.SetPoint1(*start)
        line.SetPoint2(*end)
        line.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(line.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetLineWidth(2.5)
        actor.PickableOff()
        self._renderer.AddActor(actor)
        self._measurement_actors.append(actor)

        direction = tuple(value / length for value in delta)
        tip_height = arrow_size if arrow_size is not None else length / 50.0
        for tip_position, tip_direction in (
            (start, tuple(-value for value in direction)),
            (end, direction),
        ):
            tip_center = tuple(
                tip_position[index] - tip_direction[index] * tip_height * 0.5
                for index in range(3)
            )
            cone = vtk.vtkConeSource()
            cone.SetCenter(*tip_center)
            cone.SetDirection(*tip_direction)
            cone.SetHeight(tip_height)
            cone.SetRadius(tip_height * 0.32)
            cone.SetResolution(12)
            cone.Update()
            cone_mapper = vtk.vtkPolyDataMapper()
            cone_mapper.SetInputConnection(cone.GetOutputPort())
            cone_actor = vtk.vtkActor()
            cone_actor.SetMapper(cone_mapper)
            cone_actor.GetProperty().SetColor(*color)
            cone_actor.PickableOff()
            self._renderer.AddActor(cone_actor)
            self._measurement_actors.append(cone_actor)

        midpoint = tuple((start[index] + end[index]) * 0.5 for index in range(3))
        self._add_measurement_text(label, midpoint, color, display_offset=label_offset)

    def _add_measurement_text(
        self, text: str, position, color, display_offset=(0, 14)
    ) -> None:
        if position is None:
            return
        actor = vtk.vtkBillboardTextActor3D()
        actor.SetInput(str(text))
        actor.SetPosition(*position)
        actor.SetDisplayOffset(*display_offset)
        text_property = actor.GetTextProperty()
        text_property.SetColor(*color)
        text_property.SetFontSize(16)
        text_property.SetBold(True)
        text_property.SetShadow(True)
        text_property.SetBackgroundColor(0.08, 0.1, 0.12)
        text_property.SetBackgroundOpacity(0.82)
        text_property.SetFrame(True)
        text_property.SetFrameColor(*color)
        actor.PickableOff()
        self._renderer.AddActor(actor)
        self._measurement_actors.append(actor)

    def _draw_measurement_result(self, first, second, result) -> None:
        kind = result["kind"]
        if kind == "point-point":
            start, end = first["point"], second["point"]
            distance = result["distance"]
            arrow_size = measurement_arrow_size(result["components"])
            self._add_measurement_vector(
                start, end, f"d = {distance:.6g} {self._units}",
                (1.0, 0.9, 0.2), (0, 24), arrow_size
            )
            x_end = (end[0], start[1], start[2])
            y_end = (end[0], end[1], start[2])
            component_colors = ((0.95, 0.3, 0.3), (0.3, 0.85, 0.45), (0.3, 0.65, 1.0))
            axes = (
                (start, x_end, result["components"][0], "X", component_colors[0]),
                (x_end, y_end, result["components"][1], "Y", component_colors[1]),
                (y_end, end, result["components"][2], "Z", component_colors[2]),
            )
            for axis_start, axis_end, component, axis, color in axes:
                label = f"d{axis} = {component:+.6g} {self._units}"
                if abs(component) > 1e-10:
                    label_offset = (0, -18) if axis in ("X", "Z") else (0, 20)
                    self._add_measurement_vector(
                        axis_start, axis_end, label, color, label_offset, arrow_size
                    )
                else:
                    self._add_measurement_text(
                        label, start, color, display_offset=(0, -20)
                    )
            return

        if kind == "point-surface":
            if first["kind"] == "point":
                point_feature = first
            else:
                point_feature = second
            point = point_feature["point"]
            foot = result["projected_point"]
            label = f"d = {result['distance']:.6g} {self._units}"
            arrow_size = measurement_arrow_size(result["components"])
            self._add_measurement_vector(
                point, foot, label, (1.0, 0.35, 0.85), (0, 22), arrow_size
            )
            self._add_measurement_text(
                f"X/Y/Z = {self._format_measurement_components(result['components'])}",
                point,
                (1.0, 0.75, 0.95),
                display_offset=(0, -24),
            )
            return

        if not result["parallel"]:
            midpoint = tuple(
                (first["origin"][index] + second["origin"][index]) * 0.5
                for index in range(3)
            )
            self._add_measurement_text(
                "Surfaces are not parallel", midpoint, (1.0, 0.75, 0.2),
                display_offset=(0, 20)
            )
            return

        origin = first["origin"]
        normal = tuple(float(value) for value in first["normal"])
        signed_distance = result["signed_distance"]
        arrow_size = measurement_arrow_size(result["components"])
        foot = tuple(origin[index] + normal[index] * signed_distance for index in range(3))
        self._add_measurement_vector(
            foot,
            second["origin"],
            f"d = {result['distance']:.6g} {self._units}",
            (1.0, 0.35, 0.85),
            (0, 22),
            arrow_size,
        )
        self._add_measurement_text(
            f"X/Y/Z = {self._format_measurement_components(result['components'])}",
            second["origin"],
            (1.0, 0.75, 0.95),
            display_offset=(0, -24),
        )

    def _format_measurement_components(self, components) -> str:
        return ", ".join(f"{value:+.5g}" for value in components) + f" {self._units}"

    def _measurement_result_text(self, result) -> str:
        if result["kind"] == "surface-surface" and not result["parallel"]:
            return "Surfaces are not parallel; result shown in the viewer. Esc to clear."
        return f"Measurement: {result['distance']:.6g} {self._units}; vectors and dimensions shown in the viewer. Esc to clear."
    # ────────────────────────────────────────────────── one-shot pick API
    def request_pick(self, kind: str, callback, *, actor_filter=None) -> None:
        """Arm a one-shot pick. The next left-click will invoke *callback*.

        Parameters
        ----------
        kind : 'point' | 'vertex' | 'face_normal' | 'face_origin_normal' | 'plane'
            - 'point'              : callback(world_xyz)
            - 'vertex'             : callback(world_xyz)  (snaps to nearest mesh vertex)
            - 'face_normal'        : callback(world_normal_xyz)
            - 'face_origin_normal' : callback(world_xyz, world_normal_xyz)
            - 'plane'              : callback(surface_plane_dict) for a planar face
        callback : callable
        actor_filter : vtk.vtkActor, optional
            If provided, only accept a pick on this actor.
        """
        if kind not in ("point", "vertex", "face_normal", "face_origin_normal", "plane"):
            raise ValueError(f"Unknown pick kind: {kind}")
        self._cancel_draw()
        self._pick_request = (kind, callback, actor_filter)
        self.setCursor(Qt.CrossCursor)
        msg = {
            "point":              "Click on geometry to pick a point",
            "vertex":             "Click near a vertex to snap to it",
            "face_normal":        "Click on a face to capture its normal",
            "face_origin_normal": "Click on a face to set origin + normal",
            "plane":              "Click on a planar surface",
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
        kind, callback, actor_filter = self._pick_request

        picker = vtk.vtkCellPicker()
        mode = "vertex" if kind == "vertex" else "face"
        picker.SetTolerance(self._pick_tolerance_for_mode(mode))
        picker.Pick(sx, sy, 0, self._renderer)
        actor = picker.GetActor()
        if actor is None:
            self.status_message.emit("Nothing under cursor – try again.")
            return True   # keep request armed
        if actor_filter is not None and actor is not actor_filter:
            self.status_message.emit("Pick a vertex on the selected object.")
            return True

        pos = picker.GetPickPosition()

        # Compute world-space normal of the picked cell (face)
        normal_world = None
        cell_id = picker.GetCellId()
        ds = picker.GetDataSet()
        plane_feature = None
        if kind == "plane" and (cell_id < 0 or ds is None):
            self.status_message.emit("Could not identify a planar surface. Pick a planar face.")
            return True
        if kind == "plane":
            region_ids = self._face_region_cell_ids(ds, cell_id)
            face_points = self._face_region_points_world(ds, region_ids, actor)
            normal = normal_for_planar_points(face_points)
            if normal is None:
                self.status_message.emit("The picked surface is not planar. Pick a planar face.")
                return True
            plane_feature = {
                "kind": "surface",
                "origin": tuple(pos),
                "normal": normal,
                "name": "Surface",
            }
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
            elif kind == "plane":
                callback(plane_feature)
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
        """Snap a point to the active drawing grid, including custom-plane grids."""
        spacing = abs(float(self._grid_spacing))
        if spacing <= 1e-12:
            return tuple(float(value) for value in pt)
        if hasattr(self, "_active_draw_origin_normal"):
            origin, normal = self._active_draw_origin_normal()
        else:
            if getattr(self, "_custom_plane_active", False):
                origin, normal = self._custom_plane_origin, self._custom_plane_normal
            else:
                origin, normal = PLANE_ORIGIN[self._draw_plane], PLANE_NORMAL[self._draw_plane]
        u_axis, v_axis = plane_basis_from_normal(normal)
        uv = world_to_uv(pt, origin, u_axis, v_axis)
        snapped_uv = (
            round(uv[0] / spacing) * spacing,
            round(uv[1] / spacing) * spacing,
        )
        return uv_to_world(snapped_uv, origin, u_axis, v_axis)

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

    @staticmethod
    def _actor_point_to_world(actor, point) -> tuple:
        world = actor.GetMatrix().MultiplyPoint([point[0], point[1], point[2], 1.0])
        weight = world[3] if abs(world[3]) > 1e-12 else 1.0
        return tuple(float(world[index] / weight) for index in range(3))

    @staticmethod
    def _actor_point_to_local(actor, point) -> tuple:
        inverse = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(actor.GetMatrix(), inverse)
        local = inverse.MultiplyPoint([point[0], point[1], point[2], 1.0])
        weight = local[3] if abs(local[3]) > 1e-12 else 1.0
        return tuple(float(local[index] / weight) for index in range(3))

    def _owner_for_actor(self, actor):
        return next((
            obj for obj in self.scene.objects
            if getattr(obj, "actor", None) is actor
            or any(candidate is actor for candidate in getattr(obj, "all_actors", ()))
        ), None)

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
            point_data = point_picker.GetDataSet()
            if point_actor is not None and point_id >= 0 and point_data is not None:
                local = point_data.GetPoint(point_id)
                point = self._actor_point_to_world(point_actor, local)
                source = self._owner_for_actor(point_actor)
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
                point = self._actor_point_to_world(actor, closest_local)
                source = self._owner_for_actor(actor)
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
            point = tuple(pos)
            source = self._owner_for_actor(actor)
            local = self._actor_point_to_local(actor, pos)
            self._snap_records.append({
                "kind": "surface",
                "object": str(source.name) if source is not None else "",
                "cell_id": int(cell_id),
                "local": list(local),
                "point": list(point),
            })
            return (point, "surface")

        return None

    def _drawing_snap_point(self, sx: int, sy: int) -> Optional[tuple]:
        """Return a drawing point using either the selected geometry filter or grid only."""
        if self._selection_mode == "grid":
            plane_pt = self._ray_plane_intersect(sx, sy)
            if plane_pt is None:
                return None
            self._last_drawing_snap_kind = "grid"
            return self._snap(plane_pt)

        snap_mode = self._selection_mode if self._selection_mode != "object" else "all"
        geometry_pt = self._snap_to_visible_geometry(sx, sy, None, snap_mode=snap_mode)
        if geometry_pt is not None:
            pt, kind = geometry_pt
            self._last_drawing_snap_kind = str(kind)
            return pt

        plane_pt = self._ray_plane_intersect(sx, sy)
        if plane_pt is None:
            return None
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
        if self._selection_mode == "grid":
            axis_idx = self._active_plane_axis_index()
            base_point = list(getattr(self, "_draw_pts", [])[0]) if getattr(self, "_draw_pts", None) else [0.0, 0.0, 0.0]
            base_point[axis_idx] = float(base_z)
            renderer = self._renderer
            renderer.SetDisplayPoint(float(screen_x), float(screen_y), 0.0)
            renderer.DisplayToWorld()
            near = renderer.GetWorldPoint()
            renderer.SetDisplayPoint(float(screen_x), float(screen_y), 1.0)
            renderer.DisplayToWorld()
            far = renderer.GetWorldPoint()
            near_w = near[3] if abs(near[3]) > 1e-12 else 1.0
            far_w = far[3] if abs(far[3]) > 1e-12 else 1.0
            ray_origin = [float(near[i]) / near_w for i in range(3)]
            ray_end = [float(far[i]) / far_w for i in range(3)]
            ray = [ray_end[i] - ray_origin[i] for i in range(3)]
            ray_length = math.sqrt(sum(value * value for value in ray))
            if ray_length <= 1e-12:
                return 0.0
            ray = [value / ray_length for value in ray]
            axis = [0.0, 0.0, 0.0]
            axis[axis_idx] = 1.0
            offset = [ray_origin[i] - base_point[i] for i in range(3)]
            ray_axis_dot = sum(ray[i] * axis[i] for i in range(3))
            ray_offset_dot = sum(ray[i] * offset[i] for i in range(3))
            axis_offset_dot = sum(axis[i] * offset[i] for i in range(3))
            denominator = 1.0 - ray_axis_dot * ray_axis_dot
            if denominator <= 1e-10:
                return 0.0
            height = (axis_offset_dot - ray_axis_dot * ray_offset_dot) / denominator
            spacing = abs(float(self._grid_spacing))
            if spacing > 1e-12:
                height = round(height / spacing) * spacing
            self._last_drawing_snap_kind = "grid"
            return height

        snap_mode = self._selection_mode if self._selection_mode != "object" else "all"
        geometry_pt = self._snap_to_visible_geometry(
            screen_x, screen_y, None, snap_mode=snap_mode
        )
        axis_idx = self._active_plane_axis_index()
        if geometry_pt is not None:
            point, kind = geometry_pt
            self._last_drawing_snap_kind = str(kind)
            return point[axis_idx] - base_z

        self._last_drawing_snap_kind = "grid"
        picker = vtk.vtkWorldPointPicker()
        picker.Pick(screen_x, screen_y, 0, self._renderer)
        p = picker.GetPickPosition()
        height = p[axis_idx] - base_z
        spacing = abs(float(self._grid_spacing))
        if spacing > 1e-12:
            height = round(height / spacing) * spacing
        return height

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

    def _preview_circular_plate(self, center, radius, normal) -> None:
        source = vtk.vtkRegularPolygonSource()
        source.SetNumberOfSides(96)
        source.SetRadius(radius)
        source.SetCenter(*center)
        source.SetNormal(*normal)
        source.GeneratePolygonOn()
        source.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(source.GetOutputPort())
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
        if getattr(self, "_measurement_active", False):
            self._measurement_click(sx, sy)
            return
        if self._sketch_engine is not None:
            self._sketch_left_press(sx, sy, ctrl=ctrl)
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

    def _on_sketch_right_press(self, _sx: int, _sy: int) -> bool:
        if self._sketch_engine is None:
            return False
        has_active_tool = (
            self._sketch_engine.tool is not None
            or self._sketch_axis_pick_mode
            or self._sketch_dimension_kind is not None
        )
        if not has_active_tool:
            return False
        self._sketch_cancel_tool()
        return True

    # ──────────────────────────────────────────────────────── selection
    def _selection_click(self, sx: int, sy: int, ctrl: bool = False) -> None:
        # Object-level selection (default)
        if self._selection_mode in ("object", "grid"):
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
        mode = self._selection_mode
        vertex_point = None
        if mode == "vertex":
            picker = vtk.vtkPointPicker()
            picker.SetTolerance(self._pick_tolerance_for_mode("vertex"))
            picker.Pick(sx, sy, 0, self._renderer)
            actor = picker.GetActor()
            ds = picker.GetDataSet()
            point_id = picker.GetPointId()
            if actor is None:
                self._clear_sub_pick_marker()
                self.clear_coords_requested.emit()
                self.status_message.emit("No object under cursor.")
                self._render()
                return
            if ds is None or point_id < 0:
                self.clear_coords_requested.emit()
                self.status_message.emit("Pick failed.")
                return
            vertex_point = self._actor_point_to_world(actor, ds.GetPoint(point_id))
            pos = vertex_point
            cell_id = -1
        else:
            picker = vtk.vtkCellPicker()
            picker.SetTolerance(self._pick_tolerance_for_mode(mode))
            picker.Pick(sx, sy, 0, self._renderer)
            actor = picker.GetActor()
            if actor is None:
                self._clear_sub_pick_marker()
                self.clear_coords_requested.emit()
                self.status_message.emit("No object under cursor.")
                self._render()
                return

            cell_id = picker.GetCellId()
            pos = picker.GetPickPosition()
            ds = picker.GetDataSet()
            if ds is None or cell_id < 0:
                self.clear_coords_requested.emit()
                self.status_message.emit("Pick failed.")
                return

        # Find owning EMObject (for status)
        owner = self._owner_for_actor(actor)

        self._clear_sub_pick_marker()

        if mode == "face":
            self._last_edge_pick = None
            region_ids = self._face_region_cell_ids(ds, cell_id)
            marker = self._build_face_region_marker(ds, cell_id, actor, region_ids)
            face_points = self._face_region_points_world(ds, region_ids, actor)
            self._last_face_pick = {
                "owner": owner,
                "points": face_points,
                "cell_ids": sorted(region_ids),
                "cell_id": int(cell_id),
            }
            self.picked_coords.emit(float(pos[0]), float(pos[1]), float(pos[2]), self._units)
            self.status_message.emit(
                f"Face picked  cell={cell_id}  on {owner.name if owner else '?'}  "
                f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
            )
        elif mode == "edge":
            self._last_face_pick = None
            self._last_edge_pick = None
            edge_pick = self._pick_edge_segment_local(ds, cell_id, actor, pos)
            marker = None
            edge_world = pos
            if edge_pick is not None:
                edge_points, closest_local = edge_pick
                marker = self._build_edge_marker(edge_points, actor)
                edge_points_world = []
                for edge_point in edge_points:
                    point4 = actor.GetMatrix().MultiplyPoint([*edge_point, 1.0])
                    point_w = point4[3] if abs(point4[3]) > 1e-12 else 1.0
                    edge_points_world.append(tuple(float(point4[index] / point_w) for index in range(3)))
                self._last_edge_pick = {
                    "owner": owner,
                    "points": edge_points_world,
                    "cell_id": int(cell_id),
                }
                w = actor.GetMatrix().MultiplyPoint([closest_local[0], closest_local[1], closest_local[2], 1.0])
                ww = w[3] if abs(w[3]) > 1e-12 else 1.0
                edge_world = (w[0] / ww, w[1] / ww, w[2] / ww)
            self.picked_coords.emit(float(edge_world[0]), float(edge_world[1]), float(edge_world[2]), self._units)
            self.status_message.emit(
                f"Edge picked  on {owner.name if owner else '?'}  "
                f"({edge_world[0]:.2f}, {edge_world[1]:.2f}, {edge_world[2]:.2f})"
            )
        else:  # vertex
            self._last_face_pick = None
            self._last_edge_pick = None
            vp = vertex_point
            marker = self._build_vertex_marker(vp, actor)
            self.picked_coords.emit(float(vp[0]), float(vp[1]), float(vp[2]), self._units)
            self.status_message.emit(
                f"Vertex picked  ({vp[0]:.2f}, {vp[1]:.2f}, {vp[2]:.2f})"
            )

        if marker is not None:
            self._renderer.AddActor(marker)
            self._sub_pick_actor = marker
        self._render()

    def create_plate_from_face(self, material: str = "PEC") -> Optional[EMObject]:
        """Create an axis-aligned thin plate from the last selected face or edge."""
        pick = self._last_face_pick
        points = pick.get("points", []) if isinstance(pick, dict) else []
        if len(points) < 3:
            return self._create_plate_from_edge(material)

        from ..scene.em_objects import PlateObject

        bounds = [
            min(point[axis] for point in points)
            for axis in range(3)
        ] + [
            max(point[axis] for point in points)
            for axis in range(3)
        ]
        spans = [bounds[axis + 3] - bounds[axis] for axis in range(3)]
        normal_axis = min(range(3), key=lambda axis: spans[axis])
        bounds[normal_axis + 3] = bounds[normal_axis]
        obj = PlateObject(
            material=material,
            x1=bounds[0], y1=bounds[1], z1=bounds[2],
            x2=bounds[3], y2=bounds[4], z2=bounds[5],
        )
        owner = pick.get("owner") if isinstance(pick, dict) else None
        obj.creation_history = {
            "mode": "face",
            "source_face": {
                "object": str(getattr(owner, "name", "")),
                "cell_id": int(pick.get("cell_id", -1)),
                "points": [list(point) for point in points],
            },
        }
        self._finish_object(obj)
        self.status_message.emit(f"Created Plate from selected face: {obj.name}")
        return obj

    def _create_plate_from_edge(self, material: str) -> Optional[EMObject]:
        pick = self._last_edge_pick
        points = pick.get("points", []) if isinstance(pick, dict) else []
        owner = pick.get("owner") if isinstance(pick, dict) else None
        actor = getattr(owner, "actor", None)
        if len(points) < 2 or actor is None:
            self.status_message.emit("Select a face or edge first, then create Plate from Face/Edge.")
            return None

        edge_min = [min(point[axis] for point in points) for axis in range(3)]
        edge_max = [max(point[axis] for point in points) for axis in range(3)]
        edge_spans = [edge_max[axis] - edge_min[axis] for axis in range(3)]
        edge_axis = max(range(3), key=lambda axis: edge_spans[axis])
        edge_length = edge_spans[edge_axis]
        alignment_tolerance = max(edge_length * 1e-4, 1e-6)
        if edge_length <= 1e-9 or any(
            edge_spans[axis] > alignment_tolerance
            for axis in range(3)
            if axis != edge_axis
        ):
            self.status_message.emit("Plate from Edge currently requires a straight axis-aligned edge.")
            return None

        owner_bounds = actor.GetBounds()
        owner_spans = [
            float(owner_bounds[axis * 2 + 1]) - float(owner_bounds[axis * 2])
            for axis in range(3)
        ]
        remaining_axes = [axis for axis in range(3) if axis != edge_axis]
        thickness_axis = min(remaining_axes, key=lambda axis: owner_spans[axis])
        normal_axis = next(axis for axis in remaining_axes if axis != thickness_axis)
        if owner_spans[thickness_axis] <= 1e-9:
            self.status_message.emit("The selected edge owner has no measurable thickness for a port Plate.")
            return None

        bounds_min = list(edge_min)
        bounds_max = list(edge_max)
        bounds_min[thickness_axis] = float(owner_bounds[thickness_axis * 2])
        bounds_max[thickness_axis] = float(owner_bounds[thickness_axis * 2 + 1])
        bounds_min[normal_axis] = edge_min[normal_axis]
        bounds_max[normal_axis] = edge_min[normal_axis]

        from ..scene.em_objects import PlateObject

        obj = PlateObject(
            material=material,
            x1=bounds_min[0], y1=bounds_min[1], z1=bounds_min[2],
            x2=bounds_max[0], y2=bounds_max[1], z2=bounds_max[2],
        )
        obj.creation_history = {
            "mode": "edge",
            "source_edge": {
                "object": str(getattr(owner, "name", "")),
                "cell_id": int(pick.get("cell_id", -1)),
                "points": [list(point) for point in points],
                "edge_axis": int(edge_axis),
                "thickness_axis": int(thickness_axis),
            },
        }
        self._finish_object(obj)
        self.status_message.emit(f"Created Plate from selected edge: {obj.name}")
        return obj

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
        actor.GetProperty().SetOpacity(0.4)
        actor.GetProperty().EdgeVisibilityOff()
        actor.PickableOff()
        return actor

    def _face_region_cell_ids(self, dataset, cell_id: int) -> set[int]:
        poly = vtk.vtkPolyData.SafeDownCast(dataset)
        if poly is None:
            return {int(cell_id)}

        face_ids = poly.GetCellData().GetArray("OCCFaceId")
        if face_ids is not None and cell_id < face_ids.GetNumberOfTuples():
            face_id = face_ids.GetValue(cell_id)
            return {
                cell_index
                for cell_index in range(poly.GetNumberOfCells())
                if face_ids.GetValue(cell_index) == face_id
            }
        return self._coplanar_region_cell_ids(poly, cell_id)

    def _face_region_points_world(self, dataset, cell_ids, ref_actor) -> list[tuple]:
        point_ids = set()
        for cell_id in cell_ids:
            cell = dataset.GetCell(cell_id)
            if cell is None:
                continue
            point_ids.update(
                int(cell.GetPointId(index))
                for index in range(cell.GetNumberOfPoints())
            )
        return [
            self._actor_point_to_world(ref_actor, dataset.GetPoint(point_id))
            for point_id in sorted(point_ids)
        ]

    def _build_face_region_marker(
        self, dataset, cell_id: int, ref_actor, region_ids=None
    ) -> Optional[vtk.vtkActor]:
        poly = vtk.vtkPolyData.SafeDownCast(dataset)
        if poly is None:
            return self._build_face_marker(dataset, cell_id, ref_actor)

        if region_ids is None:
            region_ids = self._face_region_cell_ids(poly, cell_id)
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
        actor.GetProperty().SetOpacity(0.4)
        actor.GetProperty().EdgeVisibilityOff()
        actor.PickableOff()
        return actor
    def _pick_edge_segment_local(self, dataset, cell_id: int, ref_actor, pos):
        """Return the complete nearest feature edge and its closest local point."""
        lp = self._actor_point_to_local(ref_actor, pos)

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

        def extend(previous_id, current_id, visited_ids):
            chain = []
            max_steps = max(edge_poly.GetNumberOfPoints(), 1)
            for _ in range(max_steps):
                candidates = [
                    item for item in adjacency.get(current_id, [])
                    if item != previous_id and item not in visited_ids
                ]
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
                visited_ids.add(next_id)
                previous_id, current_id = current_id, next_id
            return chain

        visited_ids = {start_id, end_id}
        left = extend(end_id, start_id, visited_ids)
        right = extend(start_id, end_id, visited_ids)
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
        e1 = (p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2])
        e2 = (p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2])
        nx = e1[1]*e2[2] - e1[2]*e2[1]
        ny = e1[2]*e2[0] - e1[0]*e2[2]
        nz = e1[0]*e2[1] - e1[1]*e2[0]
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

            edge_point_ids = []
            for ei in range(cell.GetNumberOfEdges()):
                edge = cell.GetEdge(ei)
                if edge is None:
                    continue
                edge_point_ids.append(tuple(
                    int(edge.GetPointId(point_index))
                    for point_index in range(edge.GetNumberOfPoints())
                ))

            for edge_points in edge_point_ids:
                edge_ids = vtk.vtkIdList()
                for point_id in edge_points:
                    edge_ids.InsertNextId(point_id)
                neigh_ids.Reset()
                poly.GetCellNeighbors(cid, edge_ids, neigh_ids)
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
        mode = self._draw_mode
        state = self._draw_state
        height_states = {
            "box": 2,
            "cylinder": 2,
            "cone": 2,
            "pyramid": 2,
            "wedge": 3,
            "ellipsoid": 2,
        }
        if self._draw_pts and height_states.get(mode) == state:
            pt = self._draw_pts[-1]
        else:
            pt = self._drawing_snap_point(sx, sy)
            if pt is None:
                return

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
        elif mode == "circular_plate":
            self._fsm_circular_plate_click(pt, state)

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
        elif mode == "circular_plate":
            self._fsm_circular_plate_preview(sx, sy, state)
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

    def _circular_plate_radius(self, center, point):
        _, normal = self._active_draw_origin_normal()
        normal_length = math.sqrt(sum(float(value) ** 2 for value in normal))
        if normal_length <= 1e-12:
            return 0.0, tuple(center), (0.0, 0.0, 1.0)
        unit_normal = tuple(float(value) / normal_length for value in normal)
        delta = tuple(float(point[index]) - float(center[index]) for index in range(3))
        axial = sum(delta[index] * unit_normal[index] for index in range(3))
        radial = tuple(delta[index] - axial * unit_normal[index] for index in range(3))
        raw_radius = math.sqrt(sum(value * value for value in radial))
        if raw_radius <= 1e-12:
            return 0.0, tuple(center), unit_normal

        spacing = abs(float(self._grid_spacing))
        radius = math.floor(raw_radius / spacing + 0.5) * spacing if spacing > 1e-12 else raw_radius
        endpoint = tuple(
            float(center[index]) + radial[index] * radius / raw_radius
            for index in range(3)
        )
        return radius, endpoint, unit_normal

    def _fsm_circular_plate_click(self, point, state):
        if state == 0:
            center = self._project_point_to_draw_plane(point)
            self._draw_pts = [center]
            self._draw_state = 1
            self.status_message.emit(
                f"Circular Plate: center [{center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f}]"
                " — click radius"
            )
            return

        center = self._draw_pts[0]
        point = self._project_point_to_draw_plane(point)
        radius, endpoint, normal = self._circular_plate_radius(center, point)
        if radius <= 1e-12:
            self.status_message.emit(
                f"Circular Plate radius must be at least half the grid snap ({self._grid_spacing:g} {self._units})"
            )
            return

        source = vtk.vtkRegularPolygonSource()
        source.SetNumberOfSides(96)
        source.SetRadius(radius)
        source.SetCenter(*center)
        source.SetNormal(*normal)
        source.GeneratePolygonOn()
        source.Update()
        polydata = vtk.vtkPolyData()
        polydata.DeepCopy(source.GetOutput())
        from ..scene.em_objects import MeshObject
        obj = MeshObject(
            name=scene_objects._auto_name("CircularPlate"),
            polydata=polydata,
            material=self._draw_material,
            plate_role=True,
        )
        thickness = max(abs(float(self._grid_spacing)) * 0.01, 1e-6)
        obj.set_parameters({
            **obj.get_parameters(),
            "CircularPlate": True,
            "CenterX": center[0],
            "CenterY": center[1],
            "CenterZ": center[2],
            "Radius": radius,
            "Thickness": thickness,
            "CircularPlateNormalX": normal[0],
            "CircularPlateNormalY": normal[1],
            "CircularPlateNormalZ": normal[2],
        })
        self._draw_pts.append(endpoint)
        self._finish_object(obj)

    def _fsm_circular_plate_preview(self, sx, sy, state):
        if state != 1:
            return
        point = self._drawing_snap_point(sx, sy)
        if point is None:
            return
        center = self._draw_pts[0]
        point = self._project_point_to_draw_plane(point)
        radius, _endpoint, normal = self._circular_plate_radius(center, point)
        if radius <= 1e-12:
            return
        self._preview_circular_plate(center, radius, normal)
        self.status_message.emit(
            f"Circular Plate: radius = {radius:.3f} {self._units}"
            f" (snap {self._grid_spacing:g}){self._drawing_snap_status_suffix()}"
        )

    # ────────────── PLATE FSM (thin box) ──────────────────────────────
    def _fsm_plate_click(self, pt, sx, sy, state):
        if state == 0:
            self._draw_pts = [pt]
            self._draw_state = 1
            self.status_message.emit(f"Plate: click opposite corner")
        elif state == 1:
            self._draw_pts.append(pt)
            p1 = self._draw_pts[0]
            p2 = list(self._draw_pts[1])
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            p2[axis_idx] = p1[axis_idx]
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
            p2[axis_idx] = p1[axis_idx]
            self._preview_box(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2])
            self.status_message.emit(f"Plate: zero thickness{self._drawing_snap_status_suffix()}")

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
        if state == 3:
            base = self._draw_pts[0]
            axis_idx = self._active_plane_axis_index()
            height = self._height_from_cursor(sx, sy, base[axis_idx])
            self.status_message.emit(
                f"Wedge: height = {abs(height):.2f} {self._units}"
                f"{self._drawing_snap_status_suffix()}"
            )
        elif state >= 1:
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
            axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
            p2[axis_idx] = p1[axis_idx]

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
        axis_idx = {"XY": 2, "XZ": 1, "YZ": 0}[self._draw_plane]
        p2[axis_idx] = p1[axis_idx]
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
        obj.creation_history = {
            "mode": str(self._draw_mode or ""),
            "plane": plane_name,
            "points": self._creation_history_points(self._draw_pts, self._snap_records),
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

    @staticmethod
    def _creation_history_points(draw_points: list, snap_records: list[dict]) -> list[dict]:
        history_points = []
        for point in draw_points:
            if not isinstance(point, (tuple, list)) or len(point) != 3:
                continue
            nearest = None
            distance = float("inf")
            for record in snap_records:
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
        return history_points

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
        if event.key() == Qt.Key_Escape and getattr(self, "_measurement_active", False):
            self._finish_measurement()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self._sketch_engine is not None:
            eng = self._sketch_engine
            if (eng.tool is not None or eng.pending or self._sketch_axis_pick_mode
                    or self._sketch_dimension_kind is not None):
                self._sketch_cancel_tool()
            else:
                self.exit_sketch(commit=False)
            event.accept()
            return
        if self._sketch_engine is not None and event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._sketch_delete_selected()
            event.accept()
            return
        if (self._sketch_engine is not None
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and self._sketch_engine.tool is not None):
            self._sketch_engine.finish_current()
            self._sketch_set_selection_mode()
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

        add("Select", "Std_Select", self._sketch_set_selection_mode,
            "Select or deselect sketch entities")
        add("Line",      "Sketch_Line",      lambda: self._sketch_set_tool("line"),
            "Draw a single line segment (2 clicks)")
        add("Polyline",  "Sketch_Polyline",  lambda: self._sketch_set_tool("polyline"),
            "Draw a polyline; click to add points, then press Enter or Esc to finish")
        add("Arc",       "Sketch_Arc",       lambda: self._sketch_set_tool("arc"),
            "Draw a 3-point arc: start, end, mid")
        add("Circle",    "Sketch_Circle",    lambda: self._sketch_set_tool("circle"),
            "Draw a circle (centre + radius)")
        add("Rect",      "Sketch_Rect",      lambda: self._sketch_set_rectangle_mode("corner-corner"),
            "Draw a rectangle (two opposite corners)")
        add("Center Rect", "Sketch_RectCenter",
            lambda: self._sketch_set_rectangle_mode("center-corner"),
            "Draw a rectangle from its center to one corner")
        construction = add("Construction", "Sketch_Construction",
                           self._sketch_toggle_construction,
                           "Toggle construction geometry for new entities")
        construction.setCheckable(True)
        self._sketch_construction_action = construction
        add("Center Arc", "Sketch_ArcCenter", lambda: self._sketch_set_tool("center_arc"),
            "Draw an arc by center, start point, and end angle")
        for label, kind in (("Linear", "linear"), ("Aligned", "aligned"),
                            ("Angular", "angular"), ("Radius", "radius"),
                            ("Diameter", "diameter")):
            add(label, "Sketch_Dimension", lambda k=kind: self._sketch_begin_dimension(k),
                f"Create a {kind} sketch dimension")
        tb.addSeparator()
        add("Fillet",    "Sketch_Fillet",    self._sketch_apply_fillet,
            "Round the last polyline corner")
        add("Chamfer",   "Sketch_Chamfer",   self._sketch_apply_chamfer,
            "Chamfer the last polyline corner")
        add("Delete", "Sketch_Delete", self._sketch_delete_selected,
            "Delete the selected entity and its dimensions")
        add("Close",     "Sketch_Close",     self._sketch_close_profile,
            "Close the active polyline")
        add("Cancel",    "Sketch_Cancel",    self._sketch_cancel_tool,
            "Cancel the current tool")
        tb.addSeparator()
        add("Extrude",   "Part_Extrude",     self._sketch_request_extrude,
            "Extrude the sketch profile")
        add("Extruded Cut", "Part_Cut", self._sketch_request_extruded_cut,
            "Subtract the sketch extrusion from the selected base object")
        add("Revolve",   "Part_Revolve",     self._sketch_begin_revolve,
            "Revolve: pick a sketch line as axis")
        add("Exit",      "Sketch_Exit",      lambda: self.exit_sketch(commit=False),
            "Exit sketch mode without committing")
        return tb

    def start_sketch(self, plane_origin: tuple, plane_normal: tuple,
                     sketch_definition: Optional[dict] = None) -> None:
        self._cancel_draw()
        self.cancel_pick()
        if isinstance(sketch_definition, dict):
            self._sketch_engine = SketchEngine.from_dict(sketch_definition)
            plane_origin = self._sketch_engine.plane_origin
            plane_normal = self._sketch_engine.plane_normal
        else:
            self._sketch_engine = SketchEngine(plane_origin, plane_normal)
        self._last_sketch_definition = {}
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
        if hasattr(self, "_sketch_construction_action"):
            self._sketch_construction_action.setChecked(
                bool(self._sketch_engine.construction_mode)
            )
        # Align the viewport's projection plane with the sketch plane
        self._custom_plane_active = True
        self._custom_plane_origin = tuple(plane_origin)
        self._custom_plane_normal = tuple(plane_normal)
        self._sketch_toolbar.setVisible(True)
        self.setCursor(Qt.ArrowCursor)
        self.setFocus()
        self.status_message.emit(
            "Sketch: Select  |  click geometry to select; Esc to exit"
        )
        self._refresh_sketch_overlay()

    def exit_sketch(self, commit: bool = False) -> None:
        if self._sketch_engine is None:
            return
        self._last_sketch_definition = self._sketch_engine.to_dict()
        self._remove_sketch_actors()
        self._sketch_engine = None
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
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
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
        self._sketch_engine.set_tool(name)
        self.setCursor(Qt.CrossCursor)
        self.status_message.emit(f"Sketch tool: {name}")
        self._refresh_sketch_overlay()

    def _sketch_set_selection_mode(self) -> None:
        if self._sketch_engine is None:
            return
        self._sketch_engine.set_tool(None)
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
        self.setCursor(Qt.ArrowCursor)
        self.status_message.emit("Sketch: Select")
        self._refresh_sketch_overlay()

    def _sketch_delete_selected(self) -> None:
        if self._sketch_engine is None:
            return
        if not self._sketch_engine.delete_selected():
            self.status_message.emit("No sketch entity selected")
            return
        self.status_message.emit("Selected sketch entity deleted")
        self._refresh_sketch_overlay()

    def _sketch_set_rectangle_mode(self, mode: str) -> None:
        if self._sketch_engine is None:
            return
        self._sketch_engine.set_rectangle_mode(mode)
        self._sketch_set_tool("rect")

    def _sketch_set_construction_mode(self, enabled: Optional[bool] = None) -> bool:
        if self._sketch_engine is None:
            return False
        if enabled is None:
            enabled = not self._sketch_engine.construction_mode
        self._sketch_engine.set_construction_mode(bool(enabled))
        if hasattr(self, "_sketch_construction_action"):
            self._sketch_construction_action.setChecked(bool(enabled))
        state = "on" if enabled else "off"
        self.status_message.emit(f"Construction geometry {state} for new entities")
        return bool(enabled)

    def _sketch_toggle_construction(self) -> None:
        self._sketch_set_construction_mode()

    def set_sketch_dimension_resolver(self, resolver: Callable[[str], float]) -> None:
        self._sketch_dimension_resolver = resolver

    def set_sketch_vertex_snap(self, enabled: bool) -> bool:
        self._sketch_vertex_snap_enabled = bool(enabled)
        return self._sketch_vertex_snap_enabled

    def recompute_sketch_dimensions(self, resolver: Optional[Callable[[str], float]] = None) -> dict:
        engine = self._sketch_engine
        resolver = resolver or self._sketch_dimension_resolver
        if engine is None or not engine.dimensions:
            return {"resolved": {}, "unresolved": {}}
        if resolver is None:
            return {"resolved": {}, "unresolved": {"resolver": "No formula resolver is configured"}}

        resolved = {}
        unresolved = {}
        all_dimensions = engine.dimensions
        for dimension in all_dimensions:
            if dimension.expression:
                engine.dimensions = [dimension]
                try:
                    resolved.update(engine.recompute_dimensions(resolver))
                except Exception as exc:
                    dimension.resolved_value = None
                    unresolved[dimension.dimension_id] = str(exc)
        engine.dimensions = all_dimensions

        for dimension in all_dimensions:
            if dimension.kind != "angular" and dimension.resolved_value is not None:
                if not engine.apply_dimension(dimension):
                    unresolved.setdefault(dimension.dimension_id, "Dimension is not supported by the sketch solver")
        self._refresh_sketch_overlay()
        if unresolved:
            self.status_message.emit(
                "Some sketch dimensions remain unresolved: "
                + "; ".join(unresolved.values())
            )
        return {"resolved": resolved, "unresolved": unresolved}

    def _sketch_cancel_tool(self) -> None:
        if self._sketch_engine is None:
            return
        active_polyline_id = self._sketch_engine._active_polyline_id
        if self._sketch_engine.tool == "polyline" and active_polyline_id is not None:
            self._sketch_engine.delete_entity(active_polyline_id)
        else:
            self._sketch_engine.cancel_current()
        self._sketch_axis_pick_mode = False
        self._sketch_axis_highlight = None
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
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
            self.status_message.emit("Fillet requires a polyline with ≥3 points to extrude")
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
            self.status_message.emit("Chamfer requires a polyline with ≥3 points to extrude")
            return
        self._refresh_sketch_overlay()

    def _sketch_request_extrude(self) -> None:
        if self._sketch_engine is None:
            return
        try:
            regions = self._sketch_engine.operation_regions()
        except ValueError as exc:
            self.status_message.emit(str(exc))
            return
        if any(not region or len(region[0]) < 3 for region in regions):
            self.status_message.emit("Every selected sketch region needs at least 3 points to extrude")
            return
        if len(regions) == 1:
            profile = regions[0][0] if len(regions[0]) == 1 else regions[0]
        else:
            profile = regions
        options = self._request_extrude_options()
        if options is None:
            return
        depth, direction, symmetric = options
        origin = self._sketch_engine.plane_origin
        normal = self._sketch_engine.plane_normal
        self.sketch_extrude_requested.emit(
            list(profile), float(depth), tuple(origin), tuple(normal), direction, symmetric
        )
        self.exit_sketch(commit=True)

    def _request_extrude_options(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Extrude")
        form = QFormLayout(dlg)
        depth = QDoubleSpinBox(dlg)
        depth.setRange(0.000001, 1e9)
        depth.setDecimals(6)
        depth.setValue(10.0)
        direction = QComboBox(dlg)
        direction.addItem("Along sketch normal", "Normal")
        direction.addItem("Against sketch normal", "Reverse")
        symmetric = QCheckBox("Both directions (total depth, centered)", dlg)
        form.addRow(f"Depth ({self._units}; total when centered):", depth)
        form.addRow("Direction:", direction)
        form.addRow("", symmetric)
        symmetric.toggled.connect(lambda checked: direction.setEnabled(not checked))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return float(depth.value()), str(direction.currentData()), symmetric.isChecked()

    def _sketch_request_extruded_cut(self) -> None:
        if self._sketch_engine is None:
            return
        if len(self.scene.selection) != 1:
            self.status_message.emit("Select exactly one base object before using Extruded Cut")
            return
        try:
            profile = self._sketch_engine.operation_profile()
        except ValueError as exc:
            self.status_message.emit(str(exc))
            return
        outer = profile[0] if profile and isinstance(profile[0][0], (list, tuple)) else profile
        if len(outer) < 3:
            self.status_message.emit("Sketch region needs at least 3 points to cut")
            return
        depth, ok = QInputDialog.getDouble(
            self, "Extruded Cut", f"Cut depth ({self._units}):",
            10.0, 0.001, 1e6, 3,
        )
        if not ok:
            return
        origin = tuple(self._sketch_engine.plane_origin)
        normal = tuple(self._sketch_engine.plane_normal)
        self.sketch_cut_requested.emit(list(profile), float(depth), origin, normal)
        self.exit_sketch(commit=True)

    @staticmethod
    def _sketch_revolve_axis_segments(engine) -> list:
        engine._ensure_entity_records()
        segments = []
        for entity in engine.entities:
            if entity[0] == "line":
                segments.append((entity[1], entity[2]))
            elif entity[0] == "polyline":
                segments.extend(zip(entity[1], entity[1][1:]))
        return segments

    @staticmethod
    def _sketch_revolve_segments_match(first, second) -> bool:
        tolerance = 1e-8
        return (
            math.dist(first[0], second[0]) <= tolerance
            and math.dist(first[1], second[1]) <= tolerance
        ) or (
            math.dist(first[0], second[1]) <= tolerance
            and math.dist(first[1], second[0]) <= tolerance
        )

    def _sketch_begin_revolve(self) -> None:
        engine = self._sketch_engine
        if engine is None:
            return
        if not engine.selected_region_profile():
            self.status_message.emit(
                "Select a closed sketch region before starting Revolve"
            )
            return
        try:
            profile = engine.operation_profile()
        except ValueError as exc:
            self.status_message.emit(str(exc))
            return
        outer = profile[0] if profile and isinstance(profile[0][0], (list, tuple)) else profile
        if len(outer) < 3:
            self.status_message.emit("Selected sketch region needs at least 3 points to revolve")
            return
        engine.set_tool(None)
        self._sketch_axis_pick_mode = True
        self.status_message.emit(
            "Revolve: region selected; click a separate sketch or construction line for the axis"
        )
        self._refresh_sketch_overlay()

    # ── click / move dispatch ─────────────────────────────────────────
    def _sketch_left_press(self, sx: int, sy: int, ctrl: bool = False) -> None:
        if self._sketch_dimension_kind is not None:
            self._sketch_dimension_left_press(sx, sy)
            return
        uv = self._uv_from_screen(
            sx, sy, snap=(self._sketch_engine.tool is not None and not self._sketch_axis_pick_mode)
        )
        if uv is None:
            return
        if self._sketch_axis_pick_mode:
            tol = max(self._grid_spacing * 0.6, 1e-3)
            line = self._sketch_engine.find_line_at_uv(uv, tol)
            if line is None:
                self.status_message.emit(
                    "No axis line under cursor; click a sketch or construction line"
                )
                return
            if math.dist(line[0], line[1]) <= 1e-12:
                self.status_message.emit("Revolve axis must have two distinct endpoints")
                return
            try:
                profile = self._sketch_engine.operation_profile()
            except ValueError as exc:
                self.status_message.emit(str(exc))
                self._sketch_axis_pick_mode = False
                self._refresh_sketch_overlay()
                return
            outer = profile[0] if profile and isinstance(profile[0][0], (list, tuple)) else profile
            if len(outer) < 3:
                self.status_message.emit("Selected sketch region needs at least 3 points to revolve")
                self._sketch_axis_pick_mode = False
                self._refresh_sketch_overlay()
                return
            boundaries = [
                (start, end)
                for contour in (self._sketch_engine.selected_region_profile() or [])
                for start, end in zip(contour, contour[1:])
            ]
            axis_segments = self._sketch_revolve_axis_segments(self._sketch_engine)
            line_is_boundary = any(
                self._sketch_revolve_segments_match(line, boundary)
                for boundary in boundaries
            )
            has_separate_axis = any(
                not any(
                    self._sketch_revolve_segments_match(segment, boundary)
                    for boundary in boundaries
                )
                for segment in axis_segments
                if math.dist(segment[0], segment[1]) > 1e-12
            )
            if line_is_boundary and has_separate_axis:
                self.status_message.emit(
                    "That line bounds the selected region; choose a separate sketch axis"
                )
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

        if self._sketch_engine.tool == "center_arc":
            self._sketch_center_arc_click(uv)
            return

        if self._sketch_engine.tool is None:
            tolerance = Viewport3DWidget._sketch_entity_pick_tolerance(self, sx, sy, uv)
            if ctrl and self._sketch_engine.select_region_at_uv(
                uv, additive=True, toggle=True
            ):
                message = "Sketch region added to selection"
            else:
                selected = self._sketch_engine.select_entity_at_uv(
                    uv, tolerance, preserve_regions=ctrl
                )
                if selected:
                    message = "Sketch entity selected"
                elif not ctrl and self._sketch_engine.select_region_at_uv(uv):
                    message = "Sketch region selected"
                else:
                    message = "Sketch selection cleared"
            self.status_message.emit(message)
            self._refresh_sketch_overlay()
            return
        active_tool = self._sketch_engine.tool
        self._sketch_engine.on_click(uv)
        if active_tool is not None and self._sketch_engine.tool is None:
            self.setCursor(Qt.ArrowCursor)
            self.status_message.emit("Sketch: Select")
        self._refresh_sketch_overlay()

    def _sketch_mouse_move(self, sx: int, sy: int) -> None:
        if self._sketch_dimension_kind is not None:
            return
        if self._sketch_engine.tool is None and not self._sketch_axis_pick_mode:
            return
        uv = self._uv_from_screen(sx, sy)
        if uv is None:
            return
        self._sketch_engine.on_move(uv)
        self._refresh_sketch_preview()

    def _sketch_center_arc_click(self, uv: tuple) -> None:
        pending = self._sketch_engine.pending
        pending.append(uv)
        if len(pending) < 3:
            self.status_message.emit(
                "Center Arc: " + ("pick the start point" if len(pending) == 1 else "pick the end angle")
            )
            self._refresh_sketch_overlay()
            return
        center, start, end_cursor = pending
        path = self._center_arc_path(center, start, end_cursor)
        if path is None:
            self.status_message.emit("Center Arc needs distinct center, start, and end points")
            self._sketch_engine.pending = []
            self._refresh_sketch_overlay()
            return
        self._sketch_engine.add_arc(path[0], path[-1], path[len(path) // 2])
        self._sketch_engine.pending = []
        self._sketch_engine.preview_uv = None
        self._sketch_set_selection_mode()
        self.status_message.emit("Center Arc created")

    @staticmethod
    def _center_arc_path(center: tuple, start: tuple, end_cursor: tuple) -> Optional[list]:
        radius = math.hypot(start[0] - center[0], start[1] - center[1])
        end_dx, end_dy = end_cursor[0] - center[0], end_cursor[1] - center[1]
        if radius < 1e-9 or math.hypot(end_dx, end_dy) < 1e-9:
            return None
        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        end_angle = math.atan2(end_dy, end_dx)
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
        if sweep < 1e-9:
            return None
        return [
            (center[0] + radius * math.cos(start_angle + sweep * step / 32.0),
             center[1] + radius * math.sin(start_angle + sweep * step / 32.0))
            for step in range(33)
        ]

    def _sketch_begin_dimension(self, kind: str) -> None:
        if self._sketch_engine is None:
            return
        if kind not in {"linear", "aligned", "angular", "radius", "diameter"}:
            raise ValueError(f"Unsupported sketch dimension: {kind}")
        self._sketch_engine.set_tool(None)
        self._sketch_dimension_kind = kind
        self._sketch_dimension_picks = []
        self._sketch_axis_pick_mode = False
        required = 1 if kind in {"radius", "diameter"} else 2
        target = {
            "linear": "sketch vertices or edges",
            "aligned": "sketch vertices or edges",
            "angular": "two sketch lines",
            "radius": "a sketch circle or arc",
            "diameter": "a sketch circle or arc",
        }[kind]
        self.status_message.emit(f"{kind.title()} dimension: pick {required} {target}")

    def _sketch_dimension_left_press(self, sx: int, sy: int) -> None:
        world = self._ray_plane_intersect(sx, sy)
        if world is None:
            return
        external_snap = None
        for snap_mode in ("vertex", "edge"):
            snapped = self._snap_to_visible_geometry(sx, sy, world, snap_mode=snap_mode)
            if snapped is not None:
                world, external_snap = snapped
                world = self._project_point_to_draw_plane(world)
                break
        uv = world_to_uv(
            world, self._sketch_engine.plane_origin,
            self._sketch_engine.u_axis, self._sketch_engine.v_axis,
        )
        tolerance = max(float(self._grid_spacing) * 0.45, 1e-3)
        ref = self._sketch_dimension_ref_at_uv(self._sketch_dimension_kind, uv, tolerance)
        if ref is None:
            if external_snap:
                self.status_message.emit(
                    "External geometry snapped, but dimensions require a persistent sketch reference; "
                    "pick sketch geometry near that snap."
                )
            else:
                self.status_message.emit("No compatible sketch reference under the cursor")
            return

        self._sketch_dimension_picks.append(ref)
        needed = 1 if self._sketch_dimension_kind in {"radius", "diameter"} else 2
        if len(self._sketch_dimension_picks) < needed:
            snap_note = f"; aligned to external {external_snap}" if external_snap else ""
            self.status_message.emit(f"Dimension reference 1/{needed} selected{snap_note}; pick the next reference")
            return
        refs = list(self._sketch_dimension_picks)
        kind = self._sketch_dimension_kind
        self._sketch_dimension_kind = None
        self._sketch_dimension_picks = []
        self._sketch_create_dimension(kind, refs)

    def _sketch_dimension_ref_at_uv(self, kind: str, uv: tuple, tolerance: float):
        engine = self._sketch_engine
        if kind in {"linear", "aligned"}:
            candidates = []
            segments = []
            for index, entity in enumerate(engine.entities):
                tag = entity[0]
                point_indexes = {
                    "line": (0, 1), "rect": (0, 1), "polyline": range(len(entity[1])),
                    "arc": (0, 1, 2), "circle": (0,),
                }.get(tag, ())
                for point_index in point_indexes:
                    position = engine._point_position(engine.point_ref(index, point_index))
                    candidates.append((math.hypot(uv[0] - position[0], uv[1] - position[1]),
                                       engine.point_ref(index, point_index)))
                if tag == "line":
                    point_indexes = (0, 1)
                    segments.append((entity[1], entity[2],
                                     engine.point_ref(index, 0), engine.point_ref(index, 1)))
                elif tag == "rect":
                    corners = _rect_polyline(
                        entity[1], entity[2],
                        getattr(entity, "rectangle_mode", None) or "corner-corner",
                    )
                    for first, second in zip(corners, corners[1:]):
                        segments.append((first, second,
                                         engine.point_ref(index, 0), engine.point_ref(index, 1)))
                elif tag == "polyline":
                    for point_index in range(len(entity[1]) - 1):
                        segments.append((entity[1][point_index], entity[1][point_index + 1],
                                         engine.point_ref(index, point_index),
                                         engine.point_ref(index, point_index + 1)))
            if candidates:
                distance, point_ref = min(candidates, key=lambda item: item[0])
                if distance <= tolerance * 0.35:
                    return point_ref
            edge_candidates = []
            for start, end, first_ref, second_ref in segments:
                projection, distance = self._closest_point_2d(uv, start, end)
                if distance <= tolerance:
                    first = engine._point_position(first_ref)
                    second = engine._point_position(second_ref)
                    edge_candidates.append((distance, first_ref if math.dist(uv, first) <= math.dist(uv, second) else second_ref))
            if edge_candidates:
                return min(edge_candidates, key=lambda item: item[0])[1]
            if candidates:
                distance, point_ref = min(candidates, key=lambda item: item[0])
                if distance <= tolerance:
                    return point_ref
            return None

        best = None
        for index, entity in enumerate(engine.entities):
            tag = entity[0]
            if kind == "angular" and tag not in {"line", "polyline"}:
                continue
            if kind in {"radius", "diameter"} and tag not in {"circle", "arc"}:
                continue
            if tag == "line":
                segments = [(entity[1], entity[2])]
            elif tag == "polyline":
                segments = list(zip(entity[1], entity[1][1:]))
            elif tag == "circle":
                center, radius = entity[1], entity[2]
                distance = abs(math.hypot(uv[0] - center[0], uv[1] - center[1]) - radius)
                segments = []
            else:
                points = self._sketch_arc_points(entity[1], entity[2], entity[3])
                segments = list(zip(points, points[1:]))
            if tag in {"line", "polyline", "arc"}:
                distance = min(
                    (self._closest_point_2d(uv, start, end)[1] for start, end in segments),
                    default=float("inf"),
                )
            if distance <= tolerance and (best is None or distance < best[0]):
                best = (distance, engine.entity_ref(index))
        return None if best is None else best[1]

    @staticmethod
    def _closest_point_2d(point: tuple, start: tuple, end: tuple) -> tuple:
        dx, dy = end[0] - start[0], end[1] - start[1]
        denominator = dx * dx + dy * dy
        factor = 0.0 if denominator < 1e-12 else max(
            0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator)
        )
        closest = (start[0] + factor * dx, start[1] + factor * dy)
        return closest, math.hypot(point[0] - closest[0], point[1] - closest[1])

    @staticmethod
    def _sketch_arc_points(start: tuple, end: tuple, mid: tuple) -> list:
        ax, ay = start
        bx, by = mid
        cx, cy = end
        denominator = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(denominator) < 1e-10:
            return [start, end]
        center_x = ((ax * ax + ay * ay) * (by - cy)
                    + (bx * bx + by * by) * (cy - ay)
                    + (cx * cx + cy * cy) * (ay - by)) / denominator
        center_y = ((ax * ax + ay * ay) * (cx - bx)
                    + (bx * bx + by * by) * (ax - cx)
                    + (cx * cx + cy * cy) * (bx - ax)) / denominator
        radius = math.hypot(ax - center_x, ay - center_y)
        start_angle = math.atan2(ay - center_y, ax - center_x)
        end_angle = math.atan2(cy - center_y, cx - center_x)
        mid_angle = math.atan2(by - center_y, bx - center_x)
        ccw_sweep = (end_angle - start_angle) % (2.0 * math.pi)
        mid_sweep = (mid_angle - start_angle) % (2.0 * math.pi)
        sweep = ccw_sweep if mid_sweep <= ccw_sweep else ccw_sweep - 2.0 * math.pi
        return [
            (center_x + radius * math.cos(start_angle + sweep * step / 32.0),
             center_y + radius * math.sin(start_angle + sweep * step / 32.0))
            for step in range(33)
        ]

    def _sketch_create_dimension(self, kind: str, refs: list) -> None:
        engine = self._sketch_engine
        axis = None
        if kind == "linear":
            first = engine._point_position(refs[0])
            second = engine._point_position(refs[1])
            axis = "u" if abs(second[0] - first[0]) >= abs(second[1] - first[1]) else "v"
        try:
            dimension = engine.add_dimension(kind, refs, axis=axis)
        except (LookupError, TypeError, ValueError) as exc:
            self.status_message.emit(f"Unable to create dimension: {exc}")
            return
        default_expression = f"{dimension.value:.6g}"
        expression, accepted = QInputDialog.getText(
            self, f"{kind.title()} Dimension", "Numeric value or project parameter expression:",
            text=default_expression,
        )
        if not accepted:
            engine.dimensions.remove(dimension)
            self.status_message.emit("Dimension creation cancelled")
            self._refresh_sketch_overlay()
            return
        expression = str(expression).strip()
        dimension.expression = expression or None
        dimension.resolved_value = dimension.value if not expression else None
        if expression:
            result = self.recompute_sketch_dimensions()
            if dimension.dimension_id in result["unresolved"]:
                self.status_message.emit(
                    f"Dimension kept unresolved: {result['unresolved'][dimension.dimension_id]}"
                )
                self._refresh_sketch_overlay()
                return
        elif kind != "angular" and not engine.apply_dimension(dimension):
            self.status_message.emit("This dimension could not be applied to the selected sketch geometry")
        if kind == "angular":
            self.status_message.emit(
                "Angular dimension recorded as a measurement; the current sketch engine has no angular solver"
            )
        else:
            self.status_message.emit(f"{kind.title()} dimension created and applied")
        self._refresh_sketch_overlay()

    def sketch_definition(self) -> dict:
        if self._sketch_engine is not None:
            return self._sketch_engine.to_dict()
        return dict(self._last_sketch_definition)

    def _sketch_dimension_label(self, dimension) -> tuple[str, tuple]:
        engine = self._sketch_engine
        measured = engine.measure_dimension(dimension)
        target = dimension.resolved_value if dimension.resolved_value is not None else dimension.value
        if dimension.kind == "angular":
            if dimension.expression and dimension.resolved_value is None:
                target_text = f"{dimension.expression}: unresolved"
            elif dimension.expression:
                target_text = f"{dimension.expression} = {target:.2f} deg"
            else:
                target_text = "unresolved" if target is None else f"target {target:.2f} deg"
            label = f"{measured:.2f} deg ({target_text}; not driven)"
        elif dimension.expression and dimension.resolved_value is None:
            label = f"{measured:.3f} {self._units} ({dimension.expression}: unresolved)"
        elif dimension.expression:
            label = f"{dimension.expression} = {target:.3f} {self._units}"
        else:
            label = f"{target:.3f} {self._units}"

        points = []
        for ref in dimension.refs:
            if hasattr(ref, "point_index"):
                points.append(engine._point_position(ref))
            else:
                entity = engine._entity_for_ref(ref)
                if entity[0] == "circle":
                    points.append(entity[1])
                elif entity[0] == "arc":
                    points.append(entity[1])
                elif entity[0] == "line":
                    points.append(((entity[1][0] + entity[2][0]) * 0.5,
                                   (entity[1][1] + entity[2][1]) * 0.5))
                elif entity[0] == "polyline" and entity[1]:
                    points.append(entity[1][len(entity[1]) // 2])
        anchor = (sum(point[0] for point in points) / len(points),
                  sum(point[1] for point in points) / len(points))
        return label, anchor

    # ── coordinate helpers ────────────────────────────────────────────
    def _uv_from_screen(self, sx: int, sy: int, *, snap: bool = True) -> Optional[tuple]:
        if self._sketch_engine is None:
            return None
        world = self._ray_plane_intersect(sx, sy)
        if world is None:
            return None
        uv = world_to_uv(world, self._sketch_engine.plane_origin,
                         self._sketch_engine.u_axis,
                         self._sketch_engine.v_axis)
        if not snap:
            return uv
        if self._sketch_vertex_snap_enabled:
            snapped_vertex = self._nearest_sketch_vertex(uv)
            if snapped_vertex is not None:
                return snapped_vertex
        spacing = abs(float(self._grid_spacing))
        if spacing > 1e-12:
            return (round(uv[0] / spacing) * spacing,
                    round(uv[1] / spacing) * spacing)
        return uv

    def _sketch_entity_pick_tolerance(self, sx: int, sy: int, uv: tuple) -> float:
        spacing = abs(float(self._grid_spacing))
        minimum = max(spacing * 0.01, 1e-4)
        maximum = max(minimum, spacing * 0.05)
        fallback = max(minimum, spacing * 0.03)
        try:
            world = self._ray_plane_intersect(sx + 6, sy)
            if world is None:
                return fallback
            offset_uv = world_to_uv(
                world, self._sketch_engine.plane_origin,
                self._sketch_engine.u_axis, self._sketch_engine.v_axis,
            )
            pixel_distance = math.hypot(offset_uv[0] - uv[0], offset_uv[1] - uv[1])
            return max(minimum, min(maximum, pixel_distance))
        except (AttributeError, TypeError, ValueError):
            return fallback

    def _nearest_sketch_vertex(self, uv: tuple) -> Optional[tuple]:
        engine = self._sketch_engine
        if engine is None:
            return None
        candidates = []
        for entity in engine.entities:
            tag = entity[0]
            if tag == "line":
                candidates.extend((entity[1], entity[2]))
            elif tag == "polyline":
                candidates.extend(entity[1])
            elif tag == "rect":
                first, second = entity[1], entity[2]
                if entity.rectangle_mode == "center-corner":
                    first = (2.0 * first[0] - second[0],
                             2.0 * first[1] - second[1])
                left, right = sorted((first[0], second[0]))
                bottom, top = sorted((first[1], second[1]))
                candidates.extend(((left, bottom), (right, bottom),
                                   (right, top), (left, top)))
            elif tag == "arc":
                candidates.extend(entity[1:4])
            elif tag == "circle":
                candidates.append(entity[1])
        if not candidates:
            return None
        distance, point = min(
            (math.hypot(uv[0] - point[0], uv[1] - point[1]), point)
            for point in candidates
        )
        tolerance = max(abs(float(self._grid_spacing)) * 0.45, 1e-3)
        return tuple(point) if distance <= tolerance else None

    def _world_from_uv(self, u: float, v: float) -> tuple:
        if self._sketch_engine is None:
            return (0.0, 0.0, 0.0)
        return uv_to_world((u, v), self._sketch_engine.plane_origin,
                           self._sketch_engine.u_axis,
                           self._sketch_engine.v_axis)

    # ── overlay rendering ─────────────────────────────────────────────
    def _remove_sketch_actors(self) -> None:
        for attr in ("_sketch_lines_actor", "_sketch_construction_actor", "_sketch_hi_actor",
                     "_sketch_region_actor",
                     "_sketch_preview_actor"):
            actor = getattr(self, attr, None)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)
        for actor in self._sketch_dimension_actors:
            self._renderer.RemoveActor(actor)
        self._sketch_dimension_actors = []

    def _refresh_sketch_overlay(self) -> None:
        if self._sketch_engine is None:
            self._render()
            return
        # Remove old line/highlight actors (preview kept until next move)
        for attr in ("_sketch_lines_actor", "_sketch_construction_actor", "_sketch_hi_actor",
                 "_sketch_region_actor",
                 ):
            actor = getattr(self, attr, None)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)

        self._sketch_engine._ensure_entity_records()
        normal_entities = [
            entity for entity in self._sketch_engine.entities
            if not entity.construction
        ]
        normal_engine = SketchEngine.from_dict(self._sketch_engine.to_dict())
        normal_engine.entities = normal_entities
        normal_engine.selected_entity_id = self._sketch_engine.selected_entity_id
        normal_engine.selected_entity_id = self._sketch_engine.selected_entity_id
        normal_poly, hi_poly = normal_engine.to_lines_polydata()
        self._sketch_lines_actor = self._make_line_actor(
            normal_poly, color=(1.0, 0.5, 0.0), width=2.0
        )
        self._renderer.AddActor(self._sketch_lines_actor)
        construction_entities = [
            entity for entity in self._sketch_engine.entities
            if entity.construction
        ]
        if construction_entities:
            construction_engine = SketchEngine.from_dict(self._sketch_engine.to_dict())
            construction_engine.entities = construction_entities
            construction_engine.selected_entity_id = self._sketch_engine.selected_entity_id
            construction_engine.selected_entity_id = self._sketch_engine.selected_entity_id
            construction_poly, construction_hi_poly = construction_engine.to_lines_polydata()
            self._sketch_construction_actor = self._make_line_actor(
                construction_poly, color=(0.35, 0.8, 1.0), width=1.5, dashed=True
            )
            self._renderer.AddActor(self._sketch_construction_actor)
            if construction_hi_poly.GetNumberOfCells() > 0:
                highlights = vtk.vtkAppendPolyData()
                highlights.AddInputData(hi_poly)
                highlights.AddInputData(construction_hi_poly)
                highlights.Update()
                hi_poly = highlights.GetOutput()
        if self._sketch_axis_highlight is not None:
            highlights = vtk.vtkAppendPolyData()
            highlights.AddInputData(hi_poly)
            highlights.AddInputData(self._sketch_uv_path_polydata(
                list(self._sketch_axis_highlight)
            ))
            highlights.Update()
            hi_poly = highlights.GetOutput()
        if hi_poly.GetNumberOfCells() > 0:
            self._sketch_hi_actor = self._make_line_actor(
                hi_poly, color=(1.0, 0.85, 0.0), width=4.0
            )
            self._renderer.AddActor(self._sketch_hi_actor)
        selected_regions = self._sketch_engine.selected_region_profiles()
        if selected_regions:
            selected_surfaces = vtk.vtkAppendPolyData()
            for region in selected_regions:
                contour_points = vtk.vtkPoints()
                contour_lines = vtk.vtkCellArray()
                for contour in region:
                    ids = vtk.vtkIdList()
                    for u, v in contour:
                        ids.InsertNextId(contour_points.InsertNextPoint(u, v, 0.0))
                    contour_lines.InsertNextCell(ids)
                contour_data = vtk.vtkPolyData()
                contour_data.SetPoints(contour_points)
                contour_data.SetLines(contour_lines)
                triangulator = vtk.vtkContourTriangulator()
                triangulator.SetInputData(contour_data)
                triangulator.Update()
                cap = vtk.vtkPolyData()
                cap.DeepCopy(triangulator.GetOutput())
                world_points = vtk.vtkPoints()
                for point_index in range(cap.GetNumberOfPoints()):
                    u, v, _ = cap.GetPoint(point_index)
                    world_points.InsertNextPoint(*self._world_from_uv(u, v))
                cap.SetPoints(world_points)
                selected_surfaces.AddInputData(cap)
            selected_surfaces.Update()
            self._sketch_region_actor = vtk.vtkActor()
            region_mapper = vtk.vtkPolyDataMapper()
            region_mapper.SetInputData(selected_surfaces.GetOutput())
            self._sketch_region_actor.SetMapper(region_mapper)
            region_property = self._sketch_region_actor.GetProperty()
            region_property.SetColor(*scene_objects.SELECTION_COLOR)
            region_property.SetOpacity(0.48)
            region_property.SetRepresentationToSurface()
            region_property.EdgeVisibilityOn()
            region_property.SetEdgeColor(*scene_objects.SELECTION_COLOR)
            region_property.SetLineWidth(2.0)
            self._sketch_region_actor.PickableOff()
            self._renderer.AddActor(self._sketch_region_actor)
        self._refresh_sketch_dimensions()
        self._refresh_sketch_preview()

    def _refresh_sketch_preview(self) -> None:
        if self._sketch_preview_actor is not None:
            self._renderer.RemoveActor(self._sketch_preview_actor)
            self._sketch_preview_actor = None
        if self._sketch_engine is None:
            self._render()
            return
        if (self._sketch_engine.tool == "center_arc"
                and len(self._sketch_engine.pending) == 2
                and self._sketch_engine.preview_uv is not None):
            path = self._center_arc_path(
                self._sketch_engine.pending[0],
                self._sketch_engine.pending[1],
                self._sketch_engine.preview_uv,
            )
            prev = self._sketch_uv_path_polydata(path or [])
        else:
            prev = self._sketch_engine.to_preview_polydata()
        if prev.GetNumberOfCells() > 0:
            self._sketch_preview_actor = self._make_line_actor(
                prev, color=(1.0, 0.5, 0.0), width=1.5, dashed=True
            )
            self._renderer.AddActor(self._sketch_preview_actor)
        self._render()

    def _refresh_sketch_dimensions(self) -> None:
        for actor in self._sketch_dimension_actors:
            self._renderer.RemoveActor(actor)
        self._sketch_dimension_actors = []
        if self._sketch_engine is None:
            return
        for index, dimension in enumerate(self._sketch_engine.dimensions):
            try:
                label, anchor = self._sketch_dimension_label(dimension)
            except (LookupError, TypeError, ValueError, ZeroDivisionError):
                continue
            offset = max(float(self._grid_spacing) * (0.18 + index * 0.08), 0.2)
            world = self._world_from_uv(anchor[0], anchor[1])
            world = tuple(
                world[axis] + self._sketch_engine.v_axis[axis] * offset
                for axis in range(3)
            )
            actor = vtk.vtkBillboardTextActor3D()
            actor.SetInput(label)
            actor.SetPosition(*world)
            text = actor.GetTextProperty()
            text.SetFontSize(14)
            text.SetColor(1.0, 0.9, 0.45)
            text.SetBold(True)
            text.SetBackgroundColor(0.08, 0.08, 0.08)
            text.SetBackgroundOpacity(0.72)
            self._renderer.AddActor(actor)
            self._sketch_dimension_actors.append(actor)

    def _sketch_uv_path_polydata(self, path: list) -> vtk.vtkPolyData:
        points = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        if self._sketch_engine is not None and len(path) >= 2:
            self._sketch_engine._append_polyline_3d(path, points, lines)
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetLines(lines)
        return polydata

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
