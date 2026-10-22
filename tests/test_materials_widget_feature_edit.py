import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from em3d_modeler.scene.em_objects import (
    BoxObject,
    ExtrudedObject,
    RevolvedObject,
    SweptSurfaceObject,
)
from em3d_modeler.scene.scene_manager import SceneObjectGroup
from em3d_modeler.ui import materials_widget
from em3d_modeler.ui.materials_widget import (
    MaterialsWidget,
    _ROLE_FEATURE_EDIT,
    _ROLE_GROUP_ID,
    _ROLE_GROUPS_ROOT,
    _ROLE_MATERIAL,
    _ROLE_OBJ_ID,
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


def test_swept_surface_appears_as_selectable_material_tree_object_row(qapp):
    sheet = SweptSurfaceObject(
        "SurfaceSheet",
        profile_pts=[(0, 0, 0), (2, 0, 0)],
        displacement=(0, 0, 3),
        closed=False,
    )
    widget = MaterialsWidget()
    widget.refresh({"PEC": [sheet]})

    material_row = widget._tree.topLevelItem(0)
    object_row = material_row.child(0)
    assert object_row.text(0) == "SurfaceSheet"
    assert object_row.data(0, _ROLE_OBJ_ID) == id(sheet)

    widget.highlight(sheet)
    assert object_row.isSelected()
    widget.close()


def test_refresh_preserves_material_and_group_expansion_state(qapp):
    copper_object = BoxObject("Copper object")
    pec_object = BoxObject("PEC object")
    grouped_object = BoxObject("Grouped object")
    group = SceneObjectGroup("My group", objects=[grouped_object])
    by_material = {"Copper": [copper_object], "PEC": [pec_object]}

    widget = MaterialsWidget()
    widget.refresh(by_material, groups=[group])

    material_rows = {
        widget._tree.topLevelItem(index).data(0, _ROLE_MATERIAL):
        widget._tree.topLevelItem(index)
        for index in range(widget._tree.topLevelItemCount())
        if widget._tree.topLevelItem(index).data(0, _ROLE_MATERIAL) is not None
    }
    groups_root = next(
        widget._tree.topLevelItem(index)
        for index in range(widget._tree.topLevelItemCount())
        if widget._tree.topLevelItem(index).data(0, _ROLE_GROUPS_ROOT)
    )
    group_row = next(
        groups_root.child(index)
        for index in range(groups_root.childCount())
        if groups_root.child(index).data(0, _ROLE_GROUP_ID) == group.group_id
    )
    material_rows["Copper"].setExpanded(False)
    material_rows["PEC"].setExpanded(True)
    groups_root.setExpanded(False)
    group_row.setExpanded(False)

    widget.refresh(by_material, groups=[group])

    material_rows = {
        widget._tree.topLevelItem(index).data(0, _ROLE_MATERIAL):
        widget._tree.topLevelItem(index)
        for index in range(widget._tree.topLevelItemCount())
        if widget._tree.topLevelItem(index).data(0, _ROLE_MATERIAL) is not None
    }
    groups_root = next(
        widget._tree.topLevelItem(index)
        for index in range(widget._tree.topLevelItemCount())
        if widget._tree.topLevelItem(index).data(0, _ROLE_GROUPS_ROOT)
    )
    group_row = next(
        groups_root.child(index)
        for index in range(groups_root.childCount())
        if groups_root.child(index).data(0, _ROLE_GROUP_ID) == group.group_id
    )

    assert not material_rows["Copper"].isExpanded()
    assert material_rows["PEC"].isExpanded()
    assert not groups_root.isExpanded()
    assert not group_row.isExpanded()
    widget.close()