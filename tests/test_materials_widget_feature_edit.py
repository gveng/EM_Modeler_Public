import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from em3d_modeler.scene.em_objects import BoxObject, ExtrudedObject, RevolvedObject
from em3d_modeler.ui import materials_widget
from em3d_modeler.ui.materials_widget import (
    MaterialsWidget,
    _ROLE_FEATURE_EDIT,
    _ROLE_SKETCH_EDIT,
)


_QT_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    return QApplication.instance()


@pytest.mark.parametrize(
    "factory, operation",
    [(ExtrudedObject, "Extrude"), (RevolvedObject, "Revolve")],
)
def test_feature_operation_row_edit_interactions(qapp, factory, operation, monkeypatch):
    feature = factory()
    widget = MaterialsWidget()
    widget.resize(420, 320)
    widget.refresh({"PEC": [feature]})
    widget.show()
    qapp.processEvents()

    material_row = widget._tree.topLevelItem(0)
    object_row = material_row.child(0)
    operation_row = next(
        object_row.child(index)
        for index in range(object_row.childCount())
        if object_row.child(index).data(0, _ROLE_FEATURE_EDIT) is not None
    )
    assert operation_row.text(0) == f"Operation: {operation}"
    assert operation_row.data(0, _ROLE_FEATURE_EDIT) == id(feature)
    assert operation_row.flags() & Qt.ItemIsSelectable

    requested = []
    scene_selection_changes = []
    widget.feature_edit_requested.connect(requested.append)
    widget.selection_changed.connect(scene_selection_changes.append)
    widget.highlight(feature)
    scene_selection_changes.clear()
    widget._tree.expandItem(object_row)
    qapp.processEvents()

    operation_rect = widget._tree.visualItemRect(operation_row)
    QTest.mouseClick(widget._tree.viewport(), Qt.LeftButton, pos=operation_rect.center())
    qapp.processEvents()
    assert scene_selection_changes == []
    assert requested == []

    QTest.mouseDClick(widget._tree.viewport(), Qt.LeftButton, pos=operation_rect.center())
    qapp.processEvents()
    assert requested == [feature]

    class TriggeringMenu(QMenu):
        def exec_(self, *_args):
            self.actions()[0].trigger()

    monkeypatch.setattr(materials_widget, "QMenu", TriggeringMenu)
    widget._on_context_menu(operation_rect.center())
    assert requested == [feature, feature]
    widget.close()


def test_sketch_row_edit_interactions_without_scene_selection(qapp, monkeypatch):
    obj = BoxObject("Sketch owner")
    obj.sketch_definition = {"entities": [{"id": "entity-1"}]}
    widget = MaterialsWidget()
    widget.resize(420, 320)
    widget.refresh({"PEC": [obj]})
    widget.show()
    qapp.processEvents()

    object_row = widget._tree.topLevelItem(0).child(0)
    sketch_row = next(
        object_row.child(index)
        for index in range(object_row.childCount())
        if object_row.child(index).data(0, _ROLE_SKETCH_EDIT) is not None
    )
    assert sketch_row.text(0) == "Sketch"
    assert sketch_row.data(0, _ROLE_SKETCH_EDIT) == id(obj)

    requested = []
    scene_selection_changes = []
    widget.sketch_edit_requested.connect(requested.append)
    widget.selection_changed.connect(scene_selection_changes.append)
    widget.highlight(obj)
    scene_selection_changes.clear()
    widget._tree.expandItem(object_row)
    qapp.processEvents()

    sketch_rect = widget._tree.visualItemRect(sketch_row)
    QTest.mouseClick(widget._tree.viewport(), Qt.LeftButton, pos=sketch_rect.center())
    qapp.processEvents()
    assert scene_selection_changes == []
    assert requested == []

    QTest.mouseDClick(widget._tree.viewport(), Qt.LeftButton, pos=sketch_rect.center())
    qapp.processEvents()
    assert requested == [obj]

    class TriggeringMenu(QMenu):
        def exec_(self, *_args):
            self.actions()[0].trigger()

    monkeypatch.setattr(materials_widget, "QMenu", TriggeringMenu)
    widget._on_context_menu(sketch_rect.center())
    assert requested == [obj, obj]
    widget.close()