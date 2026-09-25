import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from em3d_modeler.ui.sketch_widget import SketchDialog


_QT_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    return QApplication.instance()


def test_extrude_edit_submits_existing_profile_and_depth(qapp):
    profile = [(0, 0), (4, 0), (4, 3), (0, 3)]
    origin = (2, 3, 4)
    normal = (0, 1, 0)
    dialog = SketchDialog(
        plane_origin=origin,
        plane_normal=normal,
        profile_pts=profile,
        operation_mode="extrude",
        extrusion_depth=12.5,
    )
    submitted = []
    dialog.set_edit_apply_callback(lambda *args: submitted.append(args) or True)

    dialog._do_extrude()

    assert submitted == [(profile, 12.5, origin, normal)]
    assert dialog.result() == QDialog.Accepted


def test_revolve_edit_preserves_custom_axis_and_plane(qapp):
    profile = [(1, 0), (3, 0), (3, 5)]
    origin = (2, 3, 4)
    normal = (1, 0, 0)
    axis_start = (2, 4, 4)
    axis_end = (3, 4, 6)
    dialog = SketchDialog(
        plane_origin=origin,
        plane_normal=normal,
        profile_pts=profile,
        operation_mode="revolve",
        revolve_angle=205,
        revolve_axis_pt1=axis_start,
        revolve_axis_pt2=axis_end,
    )
    submitted = []
    dialog.set_edit_apply_callback(lambda *args: submitted.append(args) or True)

    assert dialog._rev_axis.currentText() == "Custom axis"
    dialog._do_revolve()

    assert submitted == [(profile, 205, axis_start, axis_end, origin, normal)]
    assert dialog.result() == QDialog.Accepted


def test_rejected_feature_update_keeps_dialog_open(qapp):
    dialog = SketchDialog(
        profile_pts=[(0, 0), (4, 0), (4, 3), (0, 3)],
        operation_mode="extrude",
        extrusion_depth=5,
    )
    dialog.set_edit_apply_callback(lambda *_args: False)

    dialog._do_extrude()

    assert dialog.result() != QDialog.Accepted


def test_new_revolve_axis_preset_matches_displayed_direction(qapp):
    dialog = SketchDialog(plane_origin=(3, 5, 7))

    assert dialog._rev_axis.currentText() == "Y axis (local)"
    assert tuple(field.value() for field in dialog._axis_fields[:3]) == (3, 5, 7)
    assert tuple(field.value() for field in dialog._axis_fields[3:]) == (3, 6, 7)


def test_segment_length_editor_changes_selected_segment_exactly(qapp):
    dialog = SketchDialog(profile_pts=[(0, 0), (3, 0), (3, 4), (0, 4)])

    assert dialog._canvas.set_segment_length(0, 0, 7.25)

    segments = dialog._canvas.get_segments()
    assert segments[0][4] == pytest.approx(7.25)
    assert [segment[4] for segment in segments[1:]] == pytest.approx([4, 3])