import math
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
import vtk

from em3d_modeler.ui.viewport_widget import Viewport3DWidget
from em3d_modeler.ui.main_window import MainWindow


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, value):
        self.values.append(value)


def _camera_viewport(camera):
    renderer = vtk.vtkRenderer()
    renderer.SetActiveCamera(camera)
    viewport = SimpleNamespace(
        _renderer=renderer,
        _render=Mock(),
        projection_changed=_Signal(),
    )
    viewport.set_parallel_projection = MethodType(
        Viewport3DWidget.set_parallel_projection, viewport
    )
    viewport.is_parallel_projection = MethodType(
        Viewport3DWidget.is_parallel_projection, viewport
    )
    viewport.get_camera_state = MethodType(Viewport3DWidget.get_camera_state, viewport)
    viewport.set_camera_state = MethodType(Viewport3DWidget.set_camera_state, viewport)
    return viewport


def test_projection_toggle_preserves_framing_and_round_trips_camera_state():
    camera = vtk.vtkCamera()
    camera.SetPosition(30.0, -40.0, 50.0)
    camera.SetFocalPoint(0.0, 0.0, 0.0)
    camera.SetViewUp(0.0, 0.0, 1.0)
    camera.SetViewAngle(30.0)
    viewport = _camera_viewport(camera)
    initial_position = camera.GetPosition()
    initial_distance = math.sqrt(sum(value * value for value in initial_position))
    half_view_angle_tangent = math.tan(math.radians(15.0))

    viewport.set_parallel_projection(True)

    assert viewport.is_parallel_projection()
    assert camera.GetParallelScale() == pytest.approx(
        initial_distance * half_view_angle_tangent
    )
    saved_state = viewport.get_camera_state()

    restored_camera = vtk.vtkCamera()
    restored_viewport = _camera_viewport(restored_camera)
    restored_viewport.set_camera_state(saved_state)
    assert restored_viewport.is_parallel_projection()
    assert restored_camera.GetParallelScale() == pytest.approx(camera.GetParallelScale())

    viewport.set_parallel_projection(False)

    restored_distance = math.sqrt(sum(value * value for value in camera.GetPosition()))
    assert not viewport.is_parallel_projection()
    assert restored_distance == pytest.approx(initial_distance)
    assert camera.GetPosition() == pytest.approx(initial_position)
    assert viewport.projection_changed.values == [True, False]
    assert restored_viewport.projection_changed.values == [True]


def test_top_and_bottom_views_are_opposites_and_fit_visible_scene():
    camera = SimpleNamespace(
        SetPosition=Mock(),
        SetFocalPoint=Mock(),
        SetViewUp=Mock(),
    )
    viewport = SimpleNamespace(
        _renderer=SimpleNamespace(
            GetActiveCamera=lambda: camera,
            ResetCameraClippingRange=Mock(),
        ),
        _render=Mock(),
        fit_all=Mock(),
    )
    window = SimpleNamespace(_viewport=viewport)

    MainWindow._set_view(window, "top")
    assert camera.SetPosition.call_args.args == (0, 0, 300)
    camera.SetPosition.reset_mock()
    camera.SetViewUp.reset_mock()

    MainWindow._set_view(window, "bottom")
    assert camera.SetPosition.call_args.args == (0, 0, -300)
    assert camera.SetViewUp.call_args.args == (0, 1, 0)
    assert viewport.fit_all.call_count == 2