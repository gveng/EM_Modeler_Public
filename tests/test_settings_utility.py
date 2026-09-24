from PySide6.QtWidgets import QApplication

from em3d_modeler.ui.settings_dialog import SettingsDialog


def test_utility_settings_are_exposed_and_default_off():
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog()

    values = dialog.values()

    assert values["export_full_scene_step"] is False
    assert values["boolean_decimation_enabled"] is False
    assert [dialog._tabs.tabText(index) for index in range(dialog._tabs.count())] == [
        "Display",
        "Simulation",
        "Mesh",
        "Utility",
    ]

    dialog.set_values(
        units="mm",
        decimal_separator=".",
        workspace_size=50.0,
        grid_size=0.1,
        plane_triad_size=1.0,
        selection_color=(0.62, 0.34, 0.85),
        locale=dialog._workspace_spin.locale(),
        export_full_scene_step=True,
        boolean_decimation_enabled=True,
    )

    updated = dialog.values()
    assert updated["export_full_scene_step"] is True
    assert updated["boolean_decimation_enabled"] is True