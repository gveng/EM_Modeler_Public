"""Object / Materials tree panel (right column).

Shows all scene objects grouped by material.
Clicking an item selects the corresponding 3D object.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem
)
from PyQt5.QtCore import Qt, pyqtSignal

from ..scene.em_objects import EMObject


class MaterialsWidget(QWidget):
    """Right-column panel: scene objects grouped by material."""

    object_selected = pyqtSignal(object)   # EMObject

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        lbl = QLabel("Object / Materials")
        lbl.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(lbl)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemClicked.connect(self._on_item_click)
        layout.addWidget(self._tree)

        # map (object id → EMObject) for quick lookup
        self._obj_map: Dict[int, EMObject] = {}

    # ─────────────────────────────────────────────────── public API
    def refresh(self, by_material: Dict[str, List[EMObject]]) -> None:
        """Rebuild the tree from the scene manager's by_material dict."""
        self._obj_map.clear()
        self._tree.clear()
        for material, objects in by_material.items():
            mat_item = QTreeWidgetItem([f"[{material}]"])
            font = mat_item.font(0)
            font.setBold(True)
            mat_item.setFont(0, font)
            mat_item.setExpanded(True)
            self._tree.addTopLevelItem(mat_item)
            for obj in objects:
                child = QTreeWidgetItem([obj.name])
                child.setData(0, Qt.UserRole, id(obj))
                mat_item.addChild(child)
                self._obj_map[id(obj)] = obj

    def highlight(self, obj: Optional[EMObject]) -> None:
        """Visually select the row matching *obj*."""
        self._tree.clearSelection()
        if obj is None:
            return
        target_id = id(obj)
        for i in range(self._tree.topLevelItemCount()):
            mat_item = self._tree.topLevelItem(i)
            for j in range(mat_item.childCount()):
                child = mat_item.child(j)
                if child.data(0, Qt.UserRole) == target_id:
                    child.setSelected(True)
                    self._tree.scrollToItem(child)
                    return

    # ─────────────────────────────────────────────────── events
    def _on_item_click(self, item: QTreeWidgetItem, _col: int) -> None:
        obj_id = item.data(0, Qt.UserRole)
        if obj_id is None:
            return
        obj = self._obj_map.get(obj_id)
        if obj:
            self.object_selected.emit(obj)
