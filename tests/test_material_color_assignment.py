import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import vtk
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from em3d_modeler.ui import body_properties_widget
from em3d_modeler.ui.body_properties_widget import BodyPropertiesWidget
from em3d_modeler.scene.em_objects import MeshObject
from em3d_modeler.scene.param_expr import evaluate_expression


class _TestObject:
    def __init__(self):
        self.name = "Test"
        self.material = "PEC"
        self.opacity = 0.85
        self.custom_color = None
        self.is_model = True
        self.param_formulas = {}

    def get_parameters(self):
        params = {"Material": self.material, "Opacity": self.opacity}
        if self.custom_color is not None:
            params["Color"] = QColor.fromRgbF(*self.custom_color).name()
        return params

    def set_parameters(self, params):
        self.material = params.get("Material", self.material)
        self.opacity = params.get("Opacity", self.opacity)
        color = QColor(params.get("Color", ""))
        if color.isValid():
            self.custom_color = (color.redF(), color.greenF(), color.blueF())

    def _base_color(self):
        return self.custom_color or (0.75, 0.75, 0.85)

    def refresh_appearance(self):
        pass


def test_changing_material_applies_its_library_color():
    app = QApplication.instance() or QApplication([])
    widget = BodyPropertiesWidget()
    widget.set_materials(["PEC", "Copper"])
    widget.set_material_colors({"Copper": "#123456"})
    obj = _TestObject()

    widget.set_object(obj)
    widget._mat_combo.setCurrentText("Copper")

    assert obj.material == "Copper"
    assert QColor.fromRgbF(*obj.custom_color).name() == "#123456"
    assert widget._color_hex == "#123456"
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_changing_material_preserves_native_ipc_geometry(monkeypatch):
    app = QApplication.instance() or QApplication([])
    widget = BodyPropertiesWidget()
    widget.set_materials(["PEC", "Copper"])
    widget.set_formula_resolver(
        lambda expression: evaluate_expression(expression, {})
    )
    geometry = {
        "schema": "ipc2581-profile-v1",
        "solids": [{"kind": "cylinder_tube", "outer_radius": 0.185}],
    }
    obj = MeshObject(
        "Imported via",
        vtk.vtkPolyData(),
        ipc2581_step_geometry=geometry,
    )
    warnings = []
    monkeypatch.setattr(
        body_properties_widget.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    widget.set_object(obj)
    widget._mat_combo.setCurrentText("Copper")

    assert obj.material == "Copper"
    assert obj.ipc2581_step_geometry == geometry
    assert "IPC2581StepGeometry" not in {
        widget._table.item(row, 0).text()
        for row in range(widget._table.rowCount())
    }
    assert not warnings
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_bulk_apply_uses_library_color_for_uniform_material_selection():
    app = QApplication.instance() or QApplication([])
    widget = BodyPropertiesWidget()
    widget.set_materials(["PEC", "Copper"])
    widget.set_material_colors({"Copper": "#123456"})
    objects = [_TestObject(), _TestObject()]
    for obj in objects:
        obj.material = "Copper"
        obj.custom_color = (1.0, 0.0, 0.0)

    widget.set_selection(objects)
    widget._apply_bulk()

    assert all(QColor.fromRgbF(*obj.custom_color).name() == "#123456" for obj in objects)
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_new_material_is_registered_before_assignment(monkeypatch):
    app = QApplication.instance() or QApplication([])
    widget = BodyPropertiesWidget()
    obj = _TestObject()
    widget.set_object(obj)
    widget.material_added.connect(
        lambda name: widget.set_material_colors({name: "#123456"})
    )
    monkeypatch.setattr(
        body_properties_widget.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Copper", True),
    )

    widget._on_add_material()

    assert obj.material == "Copper"
    assert QColor.fromRgbF(*obj.custom_color).name() == "#123456"
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_individual_material_change_cannot_break_group_material_uniformity(monkeypatch):
    app = QApplication.instance() or QApplication([])
    widget = BodyPropertiesWidget()
    widget.set_materials(["PEC", "Copper"])
    obj = _TestObject()
    widget.set_object(obj)
    widget.set_material_assignment_validator(
        lambda _objects, _material: "Group members must use the same material."
    )
    warnings = []
    monkeypatch.setattr(
        body_properties_widget.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    widget._mat_combo.setCurrentText("Copper")

    assert obj.material == "PEC"
    assert widget._mat_combo.currentText() == "PEC"
    assert warnings
    widget.close()
    widget.deleteLater()
    app.processEvents()
