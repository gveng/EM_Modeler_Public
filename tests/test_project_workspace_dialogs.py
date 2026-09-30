from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QSplitter
from PySide6.QtWidgets import QWidget

from em3d_modeler.ui import main_window as main_window_module
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.ui.body_properties_widget import BodyPropertiesWidget
from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget, _parametric_range_values


def test_open_project_uses_configured_workspace_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getOpenFileName",
        lambda *args: (calls.append(args) or ("", "")),
    )
    window = SimpleNamespace(
        _workspace_path=str(tmp_path),
        _load_project_path=Mock(),
    )
    window._workspace_dialog_directory = MethodType(
        MainWindow._workspace_dialog_directory, window
    )

    MainWindow._open_project(window)

    assert calls[0][2] == str(tmp_path.resolve())
    window._load_project_path.assert_not_called()


def test_save_as_uses_configured_workspace_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args: (calls.append(args) or ("", "")),
    )
    window = SimpleNamespace(
        _workspace_path=str(tmp_path),
        _project_name="Coax",
    )
    window._workspace_dialog_directory = MethodType(
        MainWindow._workspace_dialog_directory, window
    )

    MainWindow._save_project_as(window)

    assert calls[0][2] == str(tmp_path / "Coax.em3d")


def test_save_as_uses_open_project_directory(monkeypatch, tmp_path):
    calls = []
    project_directory = tmp_path / "opened-project"
    project_directory.mkdir()
    project_path = project_directory / "Existing.em3d"
    project_path.touch()
    workspace_directory = tmp_path / "workspace"
    workspace_directory.mkdir()
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *args: (calls.append(args) or ("", "")),
    )
    window = SimpleNamespace(
        _workspace_path=str(workspace_directory),
        _project_path=str(project_path),
        _project_name="Existing",
    )
    window._workspace_dialog_directory = MethodType(
        MainWindow._workspace_dialog_directory, window
    )

    MainWindow._save_project_as(window)

    assert calls[0][2] == str(project_directory / "Existing.em3d")


def test_save_as_to_new_path_invalidates_simulation_asset_cache(monkeypatch, tmp_path):
    target_path = tmp_path / "Renamed.em3d"
    window = SimpleNamespace(
        _project_path=str(tmp_path / "Original.em3d"),
        _project_name="Original",
        _sim_steps_dirty=False,
        _sim_script_dirty=False,
        _sim_step_bundle_ready=True,
        _sim_step_bundle_cache={"entries": [{"step_file": "stale.step"}], "skipped": []},
        _sim_full_scene_step_dirty=False,
        _sim_cached_script="stale script",
        _sim_cached_script_bundle={"master": "stale script", "scripts": []},
        setWindowTitle=Mock(),
        _project_tree=SimpleNamespace(set_project_name=Mock()),
        _do_save=Mock(),
    )
    window._reset_simulation_cache = MethodType(MainWindow._reset_simulation_cache, window)
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(target_path), "EM3D Project (*.em3d)"),
    )

    MainWindow._save_project_as(window)

    assert window._project_path == str(target_path.resolve())
    assert window._project_name == "Renamed"
    assert window._sim_steps_dirty
    assert window._sim_script_dirty
    assert not window._sim_step_bundle_ready
    assert window._sim_step_bundle_cache == {"entries": [], "skipped": []}
    assert window._sim_full_scene_step_dirty
    assert window._sim_cached_script_bundle == {"master": "", "scripts": []}


def test_missing_workspace_falls_back_to_existing_dialog_default():
    window = SimpleNamespace(_workspace_path="")

    assert MainWindow._workspace_dialog_directory(window) == ""


def test_left_splitter_collapses_empty_panel_and_shows_project_variables():
    app = QApplication.instance() or QApplication([])
    splitter = QSplitter(Qt.Vertical)
    tree = QWidget()
    properties = BodyPropertiesWidget()
    splitter.addWidget(tree)
    splitter.addWidget(properties)
    splitter.resize(420, 720)
    splitter.show()
    window = SimpleNamespace(_left_splitter=splitter, _body_props=properties)

    MainWindow._update_left_splitter_layout(window)
    assert not properties.isVisible()
    assert splitter.sizes()[1] == 0

    properties.set_project_parameters([{"name": "width", "value": 12.5, "unit": "mm"}])
    MainWindow._update_left_splitter_layout(window)

    assert properties.isVisible()
    assert properties._title.text() == "Project Variables"
    assert properties._table.item(0, 0).text() == "width"
    assert splitter.sizes()[1] >= 170
    assert splitter.sizes()[0] < 720
    assert app is not None


def test_project_selection_keeps_variables_visible_when_clearing_material_selection():
    app = QApplication.instance() or QApplication([])
    properties = BodyPropertiesWidget()
    window = SimpleNamespace(
        _project_properties_active=False,
        _project_name="Wave3",
        _project_tree=SimpleNamespace(get_settings=lambda: {
            "parameters": [{"name": "width", "value": 12.5, "unit": "mm"}],
        }),
        _body_props=properties,
        _materials=SimpleNamespace(
            highlight=lambda _objects: MainWindow._on_material_tree_multi_select(
                window, []
            )
        ),
        _viewport=SimpleNamespace(
            scene=SimpleNamespace(deselect_all=Mock()),
            _render=Mock(),
        ),
        _sync_project_variable_names=Mock(),
        _info_bar=SimpleNamespace(set_info=Mock()),
    )

    MainWindow._on_project_selected(window)

    assert properties.has_content
    assert properties._title.text() == "Project Variables"
    assert properties._table.item(0, 0).text() == "width"
    assert window._project_properties_active
    assert app is not None


def test_new_simulation_defaults_to_21_points_and_keeps_user_value(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    editor_initial = {}

    def accept_simulation(initial, title):
        assert title == "Add Simulation"
        editor_initial.update(initial)
        initial["NumberOfPoints"] = 37
        return initial

    monkeypatch.setattr(tree, "_simulation_dialog_data", accept_simulation)

    tree._add_simulation_dialog()

    assert editor_initial["NumberOfPoints"] == 21
    assert tree.get_settings()["simulations"][-1]["NumberOfPoints"] == 37


def test_project_tree_migrates_legacy_progressive_setting_per_simulation():
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    tree.load_settings({
        "runtime": {"progressive_sparams_enabled": True},
        "simulations": [
            {"name": "Sweep A", "type": "Sweep"},
            {"name": "Sweep B", "type": "Sweep", "progressive_sparams_enabled": False},
        ],
    })

    settings = tree.get_settings()
    assert [sim["progressive_sparams_enabled"] for sim in settings["simulations"]] == [True, False]
    assert "progressive_sparams_enabled" not in settings["runtime"]


def test_new_simulation_progressive_setting_defaults_off():
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    tree.load_settings({})

    assert tree.get_settings()["simulations"][0]["progressive_sparams_enabled"] is False
    assert tree.get_settings()["simulations"][0]["progressive_sparams_chunk_size"] == 10


def test_adding_output_expands_its_simulation_group():
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({"simulations": [
        {"name": "Sweep", "type": "Sweep"},
        {"name": "Parametric", "type": "Parametric"},
    ]})
    parametric_group = next(
        tree._o_node.child(index)
        for index in range(tree._o_node.childCount())
        if tree._o_node.child(index).text(0) == "Parametric"
    )
    parametric_group.setExpanded(False)
    tree._output_dialog_data = lambda initial, title: {
        **initial,
        "name": "Parametric S-parameters",
    }

    tree._add_output_dialog("Parametric")

    parametric_group = next(
        tree._o_node.child(index)
        for index in range(tree._o_node.childCount())
        if tree._o_node.child(index).text(0) == "Parametric"
    )
    assert parametric_group.isExpanded()
    assert parametric_group.childCount() == 1
    assert parametric_group.child(0).text(0) == "Parametric S-parameters [plot_sp]"
    assert app is not None


def test_parametric_simulation_enables_progressive_plotting(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({"parameters": [{"name": "width", "value": 1.0}]})

    def accept_progressive_parametric(dialog):
        checkbox = next(
            item for item in dialog.findChildren(QCheckBox)
            if item.text() == "Enable progressive S-parameter plotting"
        )
        chunk_size = dialog.findChild(
            main_window_module.QSpinBox, "progressiveSparamsChunkSize"
        )
        assert checkbox.isEnabled()
        assert not chunk_size.isEnabled()
        dialog.findChild(
            main_window_module.QComboBox, "parametricParameterName"
        ).setCurrentText("width")
        checkbox.setChecked(True)
        assert chunk_size.isEnabled()
        chunk_size.setValue(4)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_progressive_parametric)
    result = tree._simulation_dialog_data(
        {"name": "Parametric", "type": "Parametric", "ParamName": "width"},
        title="Edit Simulation",
    )

    assert result["progressive_sparams_enabled"] is True
    assert result["progressive_sparams_chunk_size"] == 4
    assert result["type"] == "Parametric"
    assert app is not None


def test_parametric_output_can_select_live_plot_mode(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({"simulations": [
        {
            "name": "Parametric",
            "type": "Parametric",
            "progressive_sparams_enabled": True,
        },
    ]})

    def accept_live_output(dialog):
        plot_mode = next(
            combo for combo in dialog.findChildren(main_window_module.QComboBox)
            if combo.findData("live") >= 0
        )
        assert plot_mode.isEnabled()
        plot_mode.setCurrentIndex(plot_mode.findData("live"))
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", accept_live_output)
    result = tree._output_dialog_data(
        {"name": "Live parametric", "simulation": "Parametric", "plot_mode": "live"},
        title="Add Output Plot",
    )

    assert result["plot_mode"] == "live"
    assert app is not None


def test_simulation_definition_edits_progressive_setting(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def accept_with_progressive_enabled(dialog):
        checkbox = next(
            item for item in dialog.findChildren(QCheckBox)
            if item.text() == "Enable progressive S-parameter plotting"
        )
        checkbox.setChecked(True)
        dialog.findChild(
            main_window_module.QSpinBox, "progressiveSparamsChunkSize"
        ).setValue(7)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", accept_with_progressive_enabled)

    result = tree._simulation_dialog_data(
        {"name": "Sweep", "type": "Sweep"},
        title="Edit Simulation",
    )

    assert result["progressive_sparams_enabled"] is True
    assert result["progressive_sparams_chunk_size"] == 7


@pytest.mark.parametrize(
    ("start", "end", "step", "expected"),
    [
        (0, 1, 0.25, ["0", "0.25", "0.5", "0.75", "1"]),
        (1, 0, -0.5, ["1", "0.5", "0"]),
        (0, 1, 0.3, ["0", "0.3", "0.6", "0.9"]),
    ],
)
def test_parametric_range_generates_bounded_decimal_values(start, end, step, expected):
    assert _parametric_range_values(start, end, step) == expected


@pytest.mark.parametrize(
    ("start", "end", "step"),
    [(0, 1, 0), (0, 1, -0.1), (1, 0, 0.1)],
)
def test_parametric_range_rejects_invalid_step_direction(start, end, step):
    with pytest.raises(ValueError, match="Step must be non-zero"):
        _parametric_range_values(start, end, step)


def test_parametric_dialog_saves_range_and_preserves_explicit_csv(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({"parameters": [{"name": "width", "value": 1.0}]})
    dialog_values = {"start": "0.2", "end": "0.8", "step": "0.2"}

    def accept_range(dialog):
        dialog.findChild(main_window_module.QComboBox, "parametricValuesMode").setCurrentIndex(0)
        dialog.findChild(main_window_module.QLineEdit, "parametricStart").setText(dialog_values["start"])
        dialog.findChild(main_window_module.QLineEdit, "parametricEnd").setText(dialog_values["end"])
        dialog.findChild(main_window_module.QLineEdit, "parametricStep").setText(dialog_values["step"])
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_range)
    ranged = tree._simulation_dialog_data(
        {
            "name": "Parametric",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValuesMode": "range",
        },
        title="Edit Simulation",
    )
    assert ranged["ParamValues"] == "0.2,0.4,0.6,0.8"
    assert ranged["ParamStart"] == "0.2"
    assert ranged["ParamEnd"] == "0.8"
    assert ranged["ParamStep"] == "0.2"

    def accept_explicit_values(dialog):
        dialog.findChild(main_window_module.QComboBox, "parametricValuesMode").setCurrentIndex(1)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_explicit_values)
    explicit = tree._simulation_dialog_data(
        {
            "name": "Parametric",
            "type": "Parametric",
            "ParamName": "width",
            "ParamValues": "0.25, 0.5, 1.0",
        },
        title="Edit Simulation",
    )
    assert explicit["ParamValues"] == "0.25, 0.5, 1.0"
    assert explicit["ParamValuesMode"] == "list"
    assert app is not None


def test_parametric_name_selects_a_project_parameter(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({
        "parameters": [
            {"name": "width", "value": 2.0},
            {"name": "height", "value": 1.0},
        ],
    })
    choices = []

    def accept_selected_parameter(dialog):
        selector = dialog.findChild(
            main_window_module.QComboBox, "parametricParameterName"
        )
        choices.extend(
            selector.itemText(index)
            for index in range(selector.count())
            if selector.itemData(index)
        )
        selector.setCurrentText("height")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", accept_selected_parameter)
    result = tree._simulation_dialog_data(
        {"name": "Parametric", "type": "Parametric"},
        title="Edit Simulation",
    )

    assert choices == ["width", "height"]
    assert result["ParamName"] == "height"
    assert app is not None