import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from em3d_modeler.ui.reference_plane_dialog import ReferencePlaneDialog


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def test_xy_plane_enables_only_perpendicular_axis_presets_and_uses_active_origin(application):
    dialog = ReferencePlaneDialog(
        current_origin=(2.5, -3.0, 8.0),
        current_normal=(0.0, 0.0, 1.0),
    )

    assert dialog._perpendicular_buttons["XY"].isEnabled() is False
    assert dialog._perpendicular_buttons["XZ"].isEnabled() is True
    assert dialog._perpendicular_buttons["YZ"].isEnabled() is True

    dialog._perpendicular_buttons["XZ"].click()

    assert tuple(spin.value() for spin in (dialog._ox, dialog._oy, dialog._oz)) == pytest.approx(
        (2.5, -3.0, 8.0)
    )
    assert tuple(spin.value() for spin in (dialog._nx, dialog._ny, dialog._nz)) == pytest.approx(
        (0.0, 1.0, 0.0)
    )
    assert dialog._rot_spin.value() == pytest.approx(0.0)
    dialog.close()


def test_axis_aligned_active_plane_exposes_the_two_perpendicular_orientations(application):
    dialog = ReferencePlaneDialog(current_normal=(1.0, 0.0, 0.0))

    assert dialog._perpendicular_buttons["XY"].isEnabled() is True
    assert dialog._perpendicular_buttons["XZ"].isEnabled() is True
    assert dialog._perpendicular_buttons["YZ"].isEnabled() is False
    dialog.close()


def test_oblique_active_plane_disables_axis_presets_and_explains_why(application):
    dialog = ReferencePlaneDialog(current_normal=(1.0, 2.0, 3.0))

    assert all(not button.isEnabled() for button in dialog._perpendicular_buttons.values())
    assert "No XY, XZ, or YZ orientation" in dialog._perpendicular_status.text()
    dialog.close()


def test_perpendicular_choices_refresh_when_active_plane_changes(application):
    dialog = ReferencePlaneDialog(
        current_origin=(0.0, 0.0, 0.0),
        current_normal=(0.0, 0.0, 1.0),
    )

    dialog.set_active_plane((6.0, 7.0, 8.0), (1.0, 0.0, 0.0))
    assert dialog._perpendicular_buttons["XY"].isEnabled() is True
    assert dialog._perpendicular_buttons["XZ"].isEnabled() is True
    assert dialog._perpendicular_buttons["YZ"].isEnabled() is False

    dialog._perpendicular_buttons["XZ"].click()
    assert tuple(spin.value() for spin in (dialog._ox, dialog._oy, dialog._oz)) == pytest.approx(
        (6.0, 7.0, 8.0)
    )
    dialog.close()


def test_parallel_choice_picks_origin_and_uses_current_active_normal(application):
    class Viewport:
        def request_pick(self, kind, callback):
            self.kind = kind
            self.callback = callback

    dialog = ReferencePlaneDialog(
        current_origin=(1.0, 2.0, 3.0),
        current_normal=(0.0, 0.0, 1.0),
        viewport=Viewport(),
    )
    viewport = dialog._viewport
    dialog.set_active_plane((6.0, -4.0, 8.0), (1.0, 2.0, 3.0))
    dialog._set_origin((100.0, 200.0, 300.0))
    dialog._set_normal((0.0, 0.0, 1.0))
    dialog._rot_spin.setValue(45.0)
    defined = []
    dialog.plane_defined.connect(lambda origin, normal, name: defined.append((origin, normal, name)))

    dialog._parallel_button.click()
    assert viewport.kind == "point"
    viewport.callback((100.0, 200.0, 300.0))
    dialog._accept()

    assert defined == [
        (
            pytest.approx((
                6.0 + 1378.0 / 14.0,
                -4.0 + 2.0 * 1378.0 / 14.0,
                8.0 + 3.0 * 1378.0 / 14.0,
            )),
            pytest.approx((1.0 / 14**0.5, 2.0 / 14**0.5, 3.0 / 14**0.5)),
            "Plane",
        )
    ]
    assert dialog._rot_spin.value() == pytest.approx(0.0)
    dialog.close()