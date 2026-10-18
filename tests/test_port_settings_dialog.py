import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFormLayout

from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget


@pytest.mark.parametrize(
    ("port_type", "expected_parameters"),
    [
        ("WaveguidePort", {"Mode", "Impedance_Ohm", "Excitation"}),
        (
            "LumpedPort",
            {
                "Resistance_Ohm",
                "Voltage_V",
                "Direction_X",
                "Direction_Y",
                "Direction_Z",
            },
        ),
    ],
)
def test_port_dialog_shows_and_saves_only_selected_type_parameters(
    monkeypatch, port_type, expected_parameters
):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def accept_selected_type(dialog):
        dialog.findChild(QComboBox).setCurrentText(port_type)
        form = dialog.layout()
        parameter_row = next(
            row
            for row in range(form.rowCount())
            if form.itemAt(row, QFormLayout.LabelRole).widget().text()
            == "Parameters"
        )
        parameter_form = form.itemAt(
            parameter_row, QFormLayout.FieldRole
        ).widget().layout()
        visible_parameter_names = {
            parameter_form.itemAt(row, QFormLayout.LabelRole).widget().text()
            for row in range(parameter_form.rowCount())
        }
        assert visible_parameter_names == expected_parameters
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", accept_selected_type)
    port = tree._port_dialog_data(
        {
            "name": "Test Port",
            "number": 1,
            "type": "WaveguidePort",
            "params": {
                "Mode": "TE10",
                "Impedance_Ohm": 75.0,
                "Excitation": 2.0,
                "Resistance_Ohm": 120.0,
                "Voltage_V": 4.0,
                "Direction_X": 1.0,
                "Theta_Deg": 45.0,
                "Phi_Deg": 90.0,
                "Polarization": "Ey",
            },
        },
        fixed_object=None,
        title="Test Port",
    )

    assert port is not None
    assert port["type"] == port_type
    assert set(port["params"]) == expected_parameters
    assert app is not None
    tree.close()


def test_new_port_dialog_does_not_offer_planewave(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def accept_dialog(dialog):
        port_types = dialog.findChild(QComboBox)
        assert port_types.findData("PlaneWave") == -1
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", accept_dialog)
    port = tree._port_dialog_data(
        {"name": "New Port", "number": 1, "type": "WaveguidePort"},
        fixed_object=None,
        title="Add Port",
    )

    assert port is not None
    assert port["type"] == "WaveguidePort"
    assert app is not None
    tree.close()


def test_legacy_planewave_port_remains_editable_without_conversion(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def accept_legacy(dialog):
        port_type = dialog.findChild(QComboBox)
        assert port_type.currentData() == "PlaneWave"
        assert "legacy" in port_type.currentText().lower()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", accept_legacy)
    port = tree._port_dialog_data(
        {
            "name": "Legacy Plane Wave",
            "number": 1,
            "type": "PlaneWave",
            "params": {
                "Theta_Deg": 45.0,
                "Phi_Deg": 90.0,
                "Polarization": "Ey",
            },
        },
        fixed_object=None,
        title="Edit Port",
    )

    assert port is not None
    assert port["type"] == "PlaneWave"
    assert port["params"] == {
        "Theta_Deg": 45.0,
        "Phi_Deg": 90.0,
        "Polarization": "Ey",
    }
    assert app is not None
    tree.close()


def test_assign_port_type_preselects_requested_type(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    captured = {}

    def capture_dialog(initial, fixed_object, title):
        captured.update(
            initial=initial,
            fixed_object=fixed_object,
            title=title,
        )
        return None

    monkeypatch.setattr(tree, "_port_dialog_data", capture_dialog)
    tree.assign_port_to_object("Plate", "LumpedPort")

    assert captured["initial"]["type"] == "LumpedPort"
    assert captured["initial"]["params"] == {
        "Resistance_Ohm": 50.0,
        "Voltage_V": 1.0,
        "Direction_X": 0.0,
        "Direction_Y": 0.0,
        "Direction_Z": 0.0,
    }
    assert captured["fixed_object"] == "Plate"
    assert app is not None
    tree.close()


def test_port_dialog_keeps_parameters_separate_when_switching_types(monkeypatch):
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()

    def edit_and_accept(dialog):
        port_type = dialog.findChild(QComboBox)
        port_type.setCurrentText("LumpedPort")
        form = dialog.layout()
        parameter_row = next(
            row
            for row in range(form.rowCount())
            if form.itemAt(row, QFormLayout.LabelRole).widget().text()
            == "Parameters"
        )
        parameter_form = form.itemAt(
            parameter_row, QFormLayout.FieldRole
        ).widget().layout()
        fields = {
            parameter_form.itemAt(row, QFormLayout.LabelRole).widget().text():
            parameter_form.itemAt(row, QFormLayout.FieldRole).widget()
            for row in range(parameter_form.rowCount())
        }
        assert fields["Resistance_Ohm"].text() == "50.0"
        fields["Resistance_Ohm"].setText("75")

        port_type.setCurrentText("WaveguidePort")
        port_type.setCurrentText("LumpedPort")
        parameter_form = form.itemAt(
            parameter_row, QFormLayout.FieldRole
        ).widget().layout()
        fields = {
            parameter_form.itemAt(row, QFormLayout.LabelRole).widget().text():
            parameter_form.itemAt(row, QFormLayout.FieldRole).widget()
            for row in range(parameter_form.rowCount())
        }
        assert fields["Resistance_Ohm"].text() == "75"
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", edit_and_accept)
    port = tree._port_dialog_data(
        {
            "name": "Test Port",
            "number": 1,
            "type": "WaveguidePort",
            "params": {"Impedance_Ohm": 80.0, "Excitation": 3.0},
        },
        fixed_object=None,
        title="Test Port",
    )

    assert port is not None
    assert port["params"] == {
        "Resistance_Ohm": 75.0,
        "Voltage_V": 1.0,
        "Direction_X": 0.0,
        "Direction_Y": 0.0,
        "Direction_Z": 0.0,
    }
    assert app is not None
    tree.close()
