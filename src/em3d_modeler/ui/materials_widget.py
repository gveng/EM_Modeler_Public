"""Object / Materials tree panel (right column).

Shows all scene objects grouped by material.
Clicking an item selects the corresponding 3D object.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QMenu, QAction, QInputDialog,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui  import QBrush, QColor

from ..scene.em_objects import EMObject

# Roles for QTreeWidgetItem.data()
_ROLE_OBJ_ID      = Qt.UserRole       # int(id(EMObject))
_ROLE_PLANE_ID    = Qt.UserRole + 1   # int(id(ReferencePlane))
_ROLE_PLANES_ROOT = Qt.UserRole + 2   # True on the "Reference Planes" root node


class MaterialsWidget(QWidget):
    """Right-column panel: scene objects grouped by material."""

    object_selected   = pyqtSignal(object)   # EMObject  (single click – backward compat)
    selection_changed = pyqtSignal(list)     # List[EMObject]  (multi-select)
    plane_make_active = pyqtSignal(object)   # ReferencePlane
    plane_delete      = pyqtSignal(object)   # ReferencePlane
    plane_rename      = pyqtSignal(object, str)  # ReferencePlane, new_name
    plane_add_requested = pyqtSignal()       # user wants to add a new reference plane
    objects_hide      = pyqtSignal(list)     # List[EMObject]
    objects_show      = pyqtSignal(list)     # List[EMObject]
    object_rename     = pyqtSignal(object, str)  # EMObject, new_name
    assign_port_requested = pyqtSignal(object)   # EMObject
    assign_boundary_requested = pyqtSignal(object)  # EMObject

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
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemClicked.connect(self._on_item_click)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._tree)

        # map (object id → EMObject) for quick lookup
        self._obj_map: Dict[int, EMObject] = {}
        self._plane_map: Dict[int, object] = {}

    # ─────────────────────────────────────────────────── public API
    def refresh(self, by_material: Dict[str, List[EMObject]],
                planes: Optional[List] = None,
                active_plane=None) -> None:
        """Rebuild the tree.

        Parameters
        ----------
        by_material  : dict[material_name -> list of EMObject]
        planes       : optional list of ReferencePlane
        active_plane : the currently active ReferencePlane (highlighted)
        """
        # Preserve current tree selection
        prev_selected_ids = set()
        for it in self._tree.selectedItems():
            obj_id = it.data(0, _ROLE_OBJ_ID)
            if obj_id is not None:
                prev_selected_ids.add(obj_id)

        self._obj_map.clear()
        self._plane_map.clear()
        self._tree.blockSignals(True)
        self._tree.clear()

        # ── Reference planes section ───────────────────────────────────────
        if planes:
            planes_root = QTreeWidgetItem(["\U0001F4D0 Reference Planes"])
            f = planes_root.font(0); f.setBold(True); planes_root.setFont(0, f)
            planes_root.setData(0, _ROLE_PLANES_ROOT, True)
            planes_root.setToolTip(0, "Right-click to add a new reference plane.")
            self._tree.addTopLevelItem(planes_root)
            # Always keep expanded (user can manually close)
            planes_root.setExpanded(True)
            for p in planes:
                is_active = (active_plane is p)
                marker = "\u2605 " if is_active else "   "
                child = QTreeWidgetItem([f"{marker}{p.name}"])
                child.setData(0, _ROLE_PLANE_ID, id(p))
                child.setToolTip(0,
                    f"Origin: ({p.origin[0]:.2f}, {p.origin[1]:.2f}, {p.origin[2]:.2f})\n"
                    f"Normal: ({p.normal[0]:.3f}, {p.normal[1]:.3f}, {p.normal[2]:.3f})\n"
                    f"Right-click → Make Active to use this plane for drawing."
                )
                if is_active:
                    fa = child.font(0); fa.setBold(True); child.setFont(0, fa)
                    child.setForeground(0, QBrush(QColor(255, 165, 0)))
                planes_root.addChild(child)
                self._plane_map[id(p)] = p

        # ── Materials sections ─────────────────────────────────────────────
        for material, objects in by_material.items():
            mat_item = QTreeWidgetItem([f"[{material}]"])
            font = mat_item.font(0)
            font.setBold(True)
            mat_item.setFont(0, font)
            self._tree.addTopLevelItem(mat_item)
            # Always keep expanded (user can manually close)
            mat_item.setExpanded(True)
            for obj in objects:
                label = obj.name if obj.is_visible() else f"{obj.name}  [hidden]"
                child = QTreeWidgetItem([label])
                child.setData(0, _ROLE_OBJ_ID, id(obj))
                mat_item.addChild(child)
                self._obj_map[id(obj)] = obj
                if id(obj) in prev_selected_ids:
                    child.setSelected(True)

        self._tree.blockSignals(False)

    def highlight(self, objects) -> None:
        """Visually select row(s) matching *objects* (EMObject or List[EMObject])."""
        self._tree.blockSignals(True)
        self._tree.clearSelection()
        if objects is not None:
            if not isinstance(objects, list):
                objects = [objects]
            target_ids = {id(o) for o in objects}
            for i in range(self._tree.topLevelItemCount()):
                top = self._tree.topLevelItem(i)
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child.data(0, _ROLE_OBJ_ID) in target_ids:
                        child.setSelected(True)
                        self._tree.scrollToItem(child)
        self._tree.blockSignals(False)
        # Emit selection after restoring state
        self._on_selection_changed()

    # ─────────────────────────────────────────────────── events
    def _on_item_click(self, item: QTreeWidgetItem, _col: int) -> None:
        obj_id = item.data(0, _ROLE_OBJ_ID)
        if obj_id is None:
            return
        # When multiple rows are selected (Ctrl/Shift), avoid emitting
        # single-object selection that would collapse scene selection.
        if len(self._tree.selectedItems()) > 1:
            return
        obj = self._obj_map.get(obj_id)
        if obj:
            self.object_selected.emit(obj)

    def _on_selection_changed(self) -> None:
        selected = []
        for item in self._tree.selectedItems():
            obj_id = item.data(0, _ROLE_OBJ_ID)
            if obj_id is not None:
                obj = self._obj_map.get(obj_id)
                if obj:
                    selected.append(obj)
        self.selection_changed.emit(selected)

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return

        obj_id = item.data(0, _ROLE_OBJ_ID)
        if obj_id is not None:
            self._show_object_context_menu(item, pos)
            return

        # ── Reference Planes root header ──────────────────────────────────
        if item.data(0, _ROLE_PLANES_ROOT):
            menu = QMenu(self._tree)
            act_add = QAction("\u2795 Add Reference Plane\u2026", menu)
            act_add.triggered.connect(self.plane_add_requested.emit)
            menu.addAction(act_add)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        plane_id = item.data(0, _ROLE_PLANE_ID)
        if plane_id is None:
            return  # not a plane row
        plane = self._plane_map.get(plane_id)
        if plane is None:
            return

        menu = QMenu(self._tree)
        act_add = QAction("\u2795 Add Reference Plane\u2026", menu)
        act_add.triggered.connect(self.plane_add_requested.emit)
        menu.addAction(act_add)
        menu.addSeparator()
        act_active = QAction("\u2605 Make Active", menu)
        act_active.triggered.connect(lambda: self.plane_make_active.emit(plane))
        menu.addAction(act_active)

        act_rename = QAction("Rename\u2026", menu)
        act_rename.triggered.connect(lambda: self._rename_plane(plane))
        menu.addAction(act_rename)

        menu.addSeparator()
        act_del = QAction("Delete", menu)
        act_del.triggered.connect(lambda: self.plane_delete.emit(plane))
        menu.addAction(act_del)

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _rename_plane(self, plane) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename Reference Plane", "Name:", text=plane.name
        )
        if ok and new_name.strip():
            self.plane_rename.emit(plane, new_name.strip())

    def _selected_objects_for_context(self, clicked_item: QTreeWidgetItem) -> List[EMObject]:
        selected = []
        for it in self._tree.selectedItems():
            obj_id = it.data(0, _ROLE_OBJ_ID)
            if obj_id is not None:
                obj = self._obj_map.get(obj_id)
                if obj is not None:
                    selected.append(obj)
        if selected:
            return selected
        obj_id = clicked_item.data(0, _ROLE_OBJ_ID)
        obj = self._obj_map.get(obj_id)
        return [obj] if obj is not None else []

    def _show_object_context_menu(self, item: QTreeWidgetItem, pos) -> None:
        objs = self._selected_objects_for_context(item)
        if not objs:
            return
        menu = QMenu(self._tree)

        if len(objs) == 1:
            act_rename = QAction("Rename…", menu)
            act_rename.triggered.connect(lambda: self._rename_object(objs[0]))
            menu.addAction(act_rename)

            menu.addSeparator()
            act_assign_port = QAction("Assign Port…", menu)
            act_assign_port.triggered.connect(lambda: self.assign_port_requested.emit(objs[0]))
            menu.addAction(act_assign_port)

            act_assign_bc = QAction("Assign Boundary Condition…", menu)
            act_assign_bc.triggered.connect(lambda: self.assign_boundary_requested.emit(objs[0]))
            menu.addAction(act_assign_bc)

            menu.addSeparator()

        act_hide = QAction("Hide Selected", menu)
        act_hide.triggered.connect(lambda: self.objects_hide.emit(list(objs)))
        menu.addAction(act_hide)

        act_show = QAction("Show Selected", menu)
        act_show.triggered.connect(lambda: self.objects_show.emit(list(objs)))
        menu.addAction(act_show)

        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _rename_object(self, obj: EMObject) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename Object", "Name:", text=obj.name
        )
        if ok and new_name.strip() and new_name.strip() != obj.name:
            self.object_rename.emit(obj, new_name.strip())
