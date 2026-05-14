"""Custom VTK interactor style for EM 3D Modeler.

Mouse controls:
  Middle button drag  → Rotate (trackball)
  Right  button drag  → Pan
  Scroll wheel        → Zoom
  Left   button       → Draw / Select  (forwarded via callbacks)
All navigation controls remain active DURING drawing operations.
"""

import vtk


class EMInteractorStyle(vtk.vtkInteractorStyleUser):
    """VTK interactor style: middle=rotate, right=pan, wheel=zoom, left=draw/select."""

    def __init__(self):
        super().__init__()
        self._last_x: int = 0
        self._last_y: int = 0
        self._rotating: bool = False
        self._panning: bool = False

        # External callbacks set by the viewport
        self.left_press_callback = None
        self.left_release_callback = None
        self.mouse_move_callback = None

        self.AddObserver("MiddleButtonPressEvent",   self._on_mid_press)
        self.AddObserver("MiddleButtonReleaseEvent", self._on_mid_release)
        self.AddObserver("RightButtonPressEvent",    self._on_right_press)
        self.AddObserver("RightButtonReleaseEvent",  self._on_right_release)
        self.AddObserver("MouseMoveEvent",           self._on_move)
        self.AddObserver("MouseWheelForwardEvent",   self._on_wheel_fwd)
        self.AddObserver("MouseWheelBackwardEvent",  self._on_wheel_bwd)
        self.AddObserver("LeftButtonPressEvent",     self._on_left_press)
        self.AddObserver("LeftButtonReleaseEvent",   self._on_left_release)

    # ------------------------------------------------------------------ helpers
    def _ctrl(self) -> bool:
        """Return True if Ctrl is held."""
        iren = self.GetInteractor()
        return bool(iren and iren.GetControlKey())
    def _ren(self):
        iren = self.GetInteractor()
        if iren:
            return iren.GetRenderWindow().GetRenderers().GetFirstRenderer()
        return None

    # ----------------------------------------------------------- middle = rotate
    def _on_mid_press(self, _obj, _ev):
        iren = self.GetInteractor()
        self._last_x, self._last_y = iren.GetEventPosition()
        self._rotating = True

    def _on_mid_release(self, _obj, _ev):
        self._rotating = False

    # ------------------------------------------------------------- right = pan
    def _on_right_press(self, _obj, _ev):
        iren = self.GetInteractor()
        self._last_x, self._last_y = iren.GetEventPosition()
        self._panning = True

    def _on_right_release(self, _obj, _ev):
        self._panning = False

    # --------------------------------------------------------------- mouse move
    def _on_move(self, _obj, _ev):
        iren = self.GetInteractor()
        x, y = iren.GetEventPosition()
        dx = x - self._last_x
        dy = y - self._last_y
        ren = self._ren()

        if ren:
            if self._rotating:
                self._rotate(dx, dy, ren)
            elif self._panning:
                self._pan(dx, dy, ren)

        self._last_x, self._last_y = x, y

        if self.mouse_move_callback:
            self.mouse_move_callback(x, y)

        iren.GetRenderWindow().Render()

    def _rotate(self, dx: int, dy: int, ren) -> None:
        cam = ren.GetActiveCamera()
        w, h = self.GetInteractor().GetRenderWindow().GetSize()
        cam.Azimuth(-360.0 * dx / max(w, 1))
        cam.Elevation(-360.0 * dy / max(h, 1))   # negated: screen-Y grows downward
        cam.OrthogonalizeViewUp()
        ren.ResetCameraClippingRange()

    def _pan(self, dx: int, dy: int, ren) -> None:
        cam = ren.GetActiveCamera()
        fp = list(cam.GetFocalPoint())
        pos = list(cam.GetPosition())

        ren.SetWorldPoint(fp[0], fp[1], fp[2], 1.0)
        ren.WorldToDisplay()
        dfp = list(ren.GetDisplayPoint())
        dfp[0] += dx
        dfp[1] += dy

        ren.SetDisplayPoint(*dfp)
        ren.DisplayToWorld()
        wfp = list(ren.GetWorldPoint())
        w = wfp[3]
        if abs(w) < 1e-10:
            return
        wfp = [wfp[i] / w for i in range(3)]

        delta = [fp[i] - wfp[i] for i in range(3)]
        cam.SetFocalPoint(*[fp[i]  + delta[i] for i in range(3)])
        cam.SetPosition( *[pos[i] + delta[i] for i in range(3)])

    # ------------------------------------------------------------ wheel = zoom
    def _on_wheel_fwd(self, _obj, _ev):
        self._zoom(1.15)

    def _on_wheel_bwd(self, _obj, _ev):
        self._zoom(1.0 / 1.15)

    def _world_at_display_depth(self, ren, x: int, y: int, z: float):
        """Return world point for display coords (x,y) at display depth z."""
        ren.SetDisplayPoint(float(x), float(y), float(z))
        ren.DisplayToWorld()
        wp = ren.GetWorldPoint()
        w = float(wp[3])
        if abs(w) < 1e-12:
            return None
        return [float(wp[0]) / w, float(wp[1]) / w, float(wp[2]) / w]

    def _zoom(self, factor: float) -> None:
        ren = self._ren()
        if ren is None:
            return
        iren = self.GetInteractor()
        if iren is None:
            return

        x, y = iren.GetEventPosition()
        cam = ren.GetActiveCamera()

        # Keep the world point under cursor stable across zoom.
        fp = cam.GetFocalPoint()
        ren.SetWorldPoint(fp[0], fp[1], fp[2], 1.0)
        ren.WorldToDisplay()
        focal_display_z = float(ren.GetDisplayPoint()[2])

        world_before = self._world_at_display_depth(ren, x, y, focal_display_z)

        if cam.GetParallelProjection():
            cam.SetParallelScale(cam.GetParallelScale() / factor)
        else:
            cam.Dolly(factor)
            ren.ResetCameraClippingRange()

        ren.SetWorldPoint(*cam.GetFocalPoint(), 1.0)
        ren.WorldToDisplay()
        focal_display_z_after = float(ren.GetDisplayPoint()[2])
        world_after = self._world_at_display_depth(ren, x, y, focal_display_z_after)

        if world_before is not None and world_after is not None:
            delta = [world_before[i] - world_after[i] for i in range(3)]
            pos = cam.GetPosition()
            fp2 = cam.GetFocalPoint()
            cam.SetPosition(pos[0] + delta[0], pos[1] + delta[1], pos[2] + delta[2])
            cam.SetFocalPoint(fp2[0] + delta[0], fp2[1] + delta[1], fp2[2] + delta[2])

        ren.ResetCameraClippingRange()
        iren.GetRenderWindow().Render()

    # ----------------------------------------------------------- left = draw/select
    def _on_left_press(self, _obj, _ev):
        x, y = self.GetInteractor().GetEventPosition()
        if self.left_press_callback:
            self.left_press_callback(x, y, self._ctrl())

    def _on_left_release(self, _obj, _ev):
        x, y = self.GetInteractor().GetEventPosition()
        if self.left_release_callback:
            self.left_release_callback(x, y, self._ctrl())
