import os
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton

from em3d_modeler.scene.em_objects import BoxObject
from em3d_modeler.scene.scene_manager import SceneManager
from em3d_modeler.ui import materials_widget
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.ui.materials_widget import MaterialsWidget


_QT_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _QT_APP
    if QApplication.instance() is None:
        _QT_APP = QApplication([])
    return QApplication.instance()


def test_object_context_menu_isolates_selected_objects_after_show_selected(qapp, monkeypatch):
    first = BoxObject("First")
    second = BoxObject("Second")
    widget = MaterialsWidget()
    widget.refresh({"PEC": [first, second]})
    requested = []
    widget.objects_isolate.connect(requested.append)

    class TriggeringMenu(QMenu):
        def exec_(self, *_args):
            labels = [action.text() for action in self.actions() if not action.isSeparator()]
            assert labels[-3:] == [
                "Hide Selected",
                "Show Selected",
                "Isolate Selected",
            ]
            isolate_action = next(
                action for action in self.actions()
                if action.text() == "Isolate Selected"
            )
            isolate_action.trigger()

    monkeypatch.setattr(materials_widget, "QMenu", TriggeringMenu)
    item = widget._tree.topLevelItem(0).child(0)
    second_item = widget._tree.topLevelItem(0).child(1)
    item.setSelected(True)
    second_item.setSelected(True)
    widget._show_object_context_menu(item, QPoint())

    assert requested == [[first, second]]
    widget.close()


def test_isolate_restores_original_visibility_with_modeless_window(qapp):
    visible = BoxObject("Initially visible")
    hidden = BoxObject("Initially hidden")
    hidden.set_visible(False)
    scene = SceneManager.__new__(SceneManager)
    scene.objects = []
    scene.renderer = Mock()
    scene._adaptive_grid = False
    scene._object_added_callback = None
    scene.add_object(visible)
    scene.add_object(hidden)
    host = QMainWindow()
    host._viewport = SimpleNamespace(scene=scene, _render=Mock())
    host._materials = SimpleNamespace(highlight=Mock())
    host._refresh_materials = Mock()
    host._isolate_visibility_snapshot = None
    host._on_isolate_object_added = MethodType(
        MainWindow._on_isolate_object_added, host
    )
    host._isolate_window = None
    host._exit_isolate = MethodType(MainWindow._exit_isolate, host)

    MainWindow._on_materials_isolate(host, [visible])
    assert visible.is_visible()
    assert not hidden.is_visible()

    isolate_window = host._isolate_window
    assert isolate_window is not None
    assert not isolate_window.isModal()
    assert isolate_window.windowFlags() & Qt.WindowStaysOnTopHint

    # Re-isolating must not replace the snapshot captured on first entry.
    MainWindow._on_materials_isolate(host, [hidden])
    assert not visible.is_visible()
    assert hidden.is_visible()

    added_during_isolate = BoxObject("Added during isolate")
    scene.add_object(added_during_isolate)
    assert not added_during_isolate.is_visible()

    isolate_window.findChild(QPushButton).click()
    assert visible.is_visible()
    assert not hidden.is_visible()
    assert added_during_isolate.is_visible()
    assert scene._object_added_callback is None
    assert host._isolate_visibility_snapshot is None
    assert host._isolate_window is None
    host.close()
