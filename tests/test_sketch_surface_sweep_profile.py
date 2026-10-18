import pytest

from em3d_modeler.drawing.sketch_engine import SketchEngine


def test_sheet_sweep_profile_joins_reversed_open_sketch_segments():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    sketch.add_line((0, 0), (1, 0))
    sketch.add_line((2, 0), (1, 0))

    profile, closed = sketch.operation_sweep_contour()

    assert profile == [(0, 0), (1, 0), (2, 0)]
    assert closed is False


def test_sheet_sweep_profile_returns_closed_circle_without_duplicate_endpoint():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    sketch.add_circle((4, -2), 3)

    profile, closed = sketch.operation_sweep_contour()

    assert closed is True
    assert len(profile) > 32
    assert profile[0] != profile[-1]


def test_sheet_sweep_profile_rejects_disconnected_open_contours():
    sketch = SketchEngine((0, 0, 0), (0, 0, 1))
    sketch.add_line((0, 0), (1, 0))
    sketch.add_line((3, 0), (4, 0))

    with pytest.raises(ValueError, match="exactly one connected"):
        sketch.operation_sweep_contour()
