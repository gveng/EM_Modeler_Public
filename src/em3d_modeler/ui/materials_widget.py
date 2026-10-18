# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Object / Materials tree panel (right column).

Shows user-defined object groups and organizes ungrouped objects by material.
Clicking an item selects the corresponding 3D object or group members.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QBrush, QColor, QAction

from ..scene.em_objects import EMObject, ExtrudedObject, RevolvedObject
from ..scene.scene_manager import SceneObjectGroup

# Roles for QTreeWidgetItem.data()
_ROLE_OBJ_ID      = Qt.UserRole       # int(id(EMObject))
_ROLE_PLANE_ID    = Qt.UserRole + 1   # int(id(ReferencePlane))
_ROLE_PLANES_ROOT = Qt.UserRole + 2   # True on the "Reference Planes" root node
_ROLE_PLANE_TRIAD = Qt.UserRole + 3   # bool on the "Plane XYZ Triad" row
_ROLE_GRID_VISIBLE = Qt.UserRole + 4  # bool on the "Grid" row
_ROLE_MATERIAL     = Qt.UserRole + 5  # str(material_name) on material header row
_ROLE_TRANSFORM_ID = Qt.UserRole + 6  # int(id(EMObject)) on transform child row
_ROLE_GRID_ADAPTIVE = Qt.UserRole + 7  # bool on the Grid row
_ROLE_PATTERN_EDIT = Qt.UserRole + 8  # int(id(EMObject)) on a pattern settings row
_ROLE_FEATURE_EDIT = Qt.UserRole + 9  # int(id(EMObject)) on a feature operation row
_ROLE_SKETCH_EDIT = Qt.UserRole + 10  # int(id(EMObject)) on a sketch row
_ROLE_GROUP_ID = Qt.UserRole + 11  # str(SceneObjectGroup.group_id)
_ROLE_GROUPS_ROOT = Qt.UserRole + 12  # True on the object groups header
_ROLE_IS_SUBGROUP = Qt.UserRole + 13  # bool on a group/subgroup row


class MaterialsWidget(QWidget):
    """Right-column panel: scene objects grouped by material."""

    object_selected   = Signal(object)   # EMObject  (single click – backward compat)
    selection_changed = Signal(list)     # List[EMObject]  (multi-select)
    plane_make_active = Signal(object)   # ReferencePlane
    plane_view_normal = Signal(object)   # ReferencePlane
    plane_delete      = Signal(object)   # ReferencePlane
    plane_rename      = Signal(object, str)  # ReferencePlane, new_name
    plane_add_requested = Signal()       # user wants to add a new reference plane
    plane_triad_visibility_changed = Signal(bool)  # show/hide plane XYZ triad
    grid_visibility_changed = Signal(bool)  # show/hide grid
    grid_adaptive_changed = Signal(bool)  # fit grid to projected object bounds
    objects_hide      = Signal(list)     # List[EMObject]
    objects_show      = Signal(list)     # List[EMObject]
    objects_isolate   = Signal(list)     # List[EMObject]
    objects_model_role_changed = Signal(list, bool)  # List[EMObject], is_model
    object_rename     = Signal(object, str)  # EMObject, new_name
    objects_bulk_rename = Signal(list, str, int)  # List[EMObject], base_name, start_index
    assign_port_requested = Signal(object, str)   # EMObject, port type
    assign_boundary_requested = Signal(list)  # List[EMObject]
    assign_mesh_resolution_requested = Signal(list)  # List[EMObject]
    assign_mesh_refinement_requested = Signal(list)  # List[EMObject]
    set_open_region_requested = Signal(object)  # AIR EMObject
    material_priority_changed = Signal(str, int)  # material_name, delta (1 for higher, -1 for lower)
    transform_edit_requested = Signal(list)  # List[EMObject]
    pattern_edit_requested = Signal(object)  # EMObject whose pattern definition should be edited
    feature_edit_requested = Signal(object)  # ExtrudedObject or RevolvedObject
    sketch_edit_requested = Signal(object)  # EMObject whose sketch definition should be edited
    group_create_requested = Signal(list, str)
    subgroup_create_requested = Signal(object, list, str)
    group_assign_requested = Signal(list, object, object)
    group_delete_requested = Signal(object)
    subgroup_delete_requested = Signal(object, object)
    group_priority_changed = Signal(object, int)

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
        self._tree.itemDoubleClicked.connect(self._on_item_double_click)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._tree)

        # map (object id → EMObject) for quick lookup
        self._obj_map: Dict[int, EMObject] = {}
        self._plane_map: Dict[int, object] = {}
        self._groups: List[SceneObjectGroup] = []
        self._group_map: Dict[str, SceneObjectGroup] = {}
        self._object_group_map: Dict[int, tuple[SceneObjectGroup, SceneObjectGroup | None]] = {}
        self._material_priorities: Dict[str, int] = {}

    # ─────────────────────────────────────────────────── public API
    def refresh(self, by_material: Dict[str, List[EMObject]],
                planes: Optional[List] = None,
                active_plane=None,
                plane_triad_visible: bool = True,
                grid_visible: bool = True,
                grid_adaptive: bool = False,
                material_priorities: Optional[Dict[str, int]] = None,
                groups: Optional[List[SceneObjectGroup]] = None) -> None:
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
        self._groups = list(groups or [])
        self._group_map.clear()
        self._object_group_map.clear()
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

            triad_marker = "\u2611" if plane_triad_visible else "\u2610"
            triad_item = QTreeWidgetItem([f"{triad_marker} Plane XYZ Triad"])
            triad_item.setData(0, _ROLE_PLANE_TRIAD, bool(plane_triad_visible))
            triad_item.setToolTip(0, "Show/Hide the XYZ triad attached to the active reference plane.")
            planes_root.addChild(triad_item)

            grid_marker = "\u2611" if grid_visible else "\u2610"
            grid_item = QTreeWidgetItem([f"{grid_marker} Grid"])
            grid_item.setData(0, _ROLE_GRID_VISIBLE, bool(grid_visible))
            grid_item.setData(0, _ROLE_GRID_ADAPTIVE, bool(grid_adaptive))
            grid_item.setToolTip(0, "Show/Hide reference-plane grid.")
            planes_root.addChild(grid_item)

            for p in planes:
                is_active = (active_plane is p)
                marker = "\u2605 " if is_active else "   "
                child = QTreeWidgetItem([f"{marker}{p.name}"])
                child.setData(0, _ROLE_PLANE_ID, id(p))
                child.setToolTip(0,
                    f"Origin: ({p.origin[0]:.3f}, {p.origin[1]:.3f}, {p.origin[2]:.3f})\n"
                    f"Normal: ({p.normal[0]:.3f}, {p.normal[1]:.3f}, {p.normal[2]:.3f})\n"
                    f"Right-click → Make Active to use this plane for drawing."
                )
                if is_active:
                    fa = child.font(0); fa.setBold(True); child.setFont(0, fa)
                    child.setForeground(0, QBrush(QColor(255, 165, 0)))
                planes_root.addChild(child)
                self._plane_map[id(p)] = p

        # ── Materials sections (sorted by priority desc, then name) ───────
        priorities = material_priorities or {}
        self._material_priorities = {
            str(name): int(priority)
            for name, priority in priorities.items()
        }
        non_model_objects: List[EMObject] = []

        def add_object_row(
            parent: QTreeWidgetItem,
            obj: EMObject,
            *,
            show_material: bool = False,
        ) -> None:
            label = obj.name
            if not obj.is_visible():
                label += "  [hidden]"
            if show_material:
                label += f"  [{str(getattr(obj, 'material', '')).strip() or 'No Material'}]"
            reference_error = str(getattr(obj, "creation_reference_error", "")).strip()
            if reference_error:
                label += "  [BROKEN REFERENCE]"
            child = QTreeWidgetItem([label])
            child.setData(0, _ROLE_OBJ_ID, id(obj))
            if reference_error:
                child.setForeground(0, QBrush(QColor(220, 40, 40)))
                child.setToolTip(0, reference_error)
            parent.addChild(child)
            self._obj_map[id(obj)] = obj
            actor = getattr(obj, "actor", None)
            if actor is not None:
                position = actor.GetPosition()
                rotation = actor.GetOrientation()
                pivot = actor.GetOrigin()
                transform = QTreeWidgetItem([
                    "Transform: "
                    f"P({position[0]:.3g}, {position[1]:.3g}, {position[2]:.3g}) "
                    f"R({rotation[0]:.3g}, {rotation[1]:.3g}, {rotation[2]:.3g})"
                ])
                transform.setData(0, _ROLE_TRANSFORM_ID, id(obj))
                transform.setToolTip(
                    0,
                    f"Pivot: ({pivot[0]:.6g}, {pivot[1]:.6g}, {pivot[2]:.6g})\n"
                    "Double-click to edit the reference point, position and rotation.",
                )
                transform.setForeground(0, QBrush(QColor(150, 170, 190)))
                child.addChild(transform)
            pattern_definition = getattr(obj, "pattern_definition", None)
            if isinstance(pattern_definition, dict):
                pattern_settings = pattern_definition.get("settings", {})
                pattern_mode = str(pattern_settings.get("mode", "Pattern")) if isinstance(pattern_settings, dict) else "Pattern"
                pattern_item = QTreeWidgetItem([f"Pattern settings ({pattern_mode})"])
                pattern_item.setData(0, _ROLE_PATTERN_EDIT, id(obj))
                pattern_item.setToolTip(0, "Right-click to edit this object's pattern definition.")
                pattern_item.setForeground(0, QBrush(QColor(120, 180, 220)))
                child.addChild(pattern_item)
            if isinstance(getattr(obj, "sketch_definition", None), dict):
                sketch_item = QTreeWidgetItem(["Sketch"])
                sketch_item.setData(0, _ROLE_SKETCH_EDIT, id(obj))
                sketch_item.setToolTip(0, "Double-click or right-click to edit this sketch.")
                sketch_item.setForeground(0, QBrush(QColor(120, 180, 220)))
                child.addChild(sketch_item)
            if isinstance(obj, (ExtrudedObject, RevolvedObject)):
                operation = "Extrude" if isinstance(obj, ExtrudedObject) else "Revolve"
                feature_item = QTreeWidgetItem([f"Operation: {operation}"])
                feature_item.setData(0, _ROLE_FEATURE_EDIT, id(obj))
                feature_item.setToolTip(0, f"Right-click to edit this {operation.lower()} operation.")
                feature_item.setForeground(0, QBrush(QColor(120, 180, 220)))
                child.addChild(feature_item)
            if id(obj) in prev_selected_ids:
                child.setSelected(True)

        grouped_object_ids: set[int] = set()
        if self._groups:
            groups_root = QTreeWidgetItem(["Object Groups"])
            font = groups_root.font(0)
            font.setBold(True)
            groups_root.setFont(0, font)
            groups_root.setData(0, _ROLE_GROUPS_ROOT, True)
            groups_root.setToolTip(0, "Right-click to group selected scene objects.")
            self._tree.addTopLevelItem(groups_root)
            groups_root.setExpanded(True)
            for group in self._groups:
                priority_tag = (
                    f"  (P={group.priority:+d})"
                    if group.priority is not None
                    else ""
                )
                group_item = QTreeWidgetItem([
                    f"{group.name}  [{self._material_summary(self._objects_in_group(group))}]"
                    f"{priority_tag}"
                ])
                group_item.setData(0, _ROLE_GROUP_ID, group.group_id)
                group_item.setData(0, _ROLE_IS_SUBGROUP, False)
                groups_root.addChild(group_item)
                self._group_map[group.group_id] = group
                group_item.setExpanded(True)
                for obj in group.objects:
                    add_object_row(group_item, obj, show_material=True)
                    grouped_object_ids.add(id(obj))
                    self._object_group_map[id(obj)] = (group, None)
                for subgroup in group.subgroups:
                    inherited_priority = (
                        f"  (P={group.priority:+d}, inherited)"
                        if group.priority is not None
                        else ""
                    )
                    subgroup_item = QTreeWidgetItem([
                        f"{subgroup.name}  [{self._material_summary(subgroup.objects)}]"
                        f"{inherited_priority}"
                    ])
                    subgroup_item.setData(0, _ROLE_GROUP_ID, subgroup.group_id)
                    subgroup_item.setData(0, _ROLE_IS_SUBGROUP, True)
                    group_item.addChild(subgroup_item)
                    self._group_map[subgroup.group_id] = subgroup
                    subgroup_item.setExpanded(True)
                    for obj in subgroup.objects:
                        add_object_row(subgroup_item, obj, show_material=True)
                        grouped_object_ids.add(id(obj))
                        self._object_group_map[id(obj)] = (group, subgroup)

        ordered_materials = sorted(
            by_material.items(),
            key=lambda kv: (-int(priorities.get(str(kv[0]), 0)), str(kv[0]).lower()),
        )
        for material, objects in ordered_materials:
            model_objects = []
            for obj in objects:
                if id(obj) in grouped_object_ids:
                    continue
                if bool(getattr(obj, "is_model", True)):
                    model_objects.append(obj)
                else:
                    non_model_objects.append(obj)
            if not model_objects:
                continue
            prio = int(priorities.get(str(material), 0))
            prio_tag = f"  (P={prio:+d})" if prio != 0 else ""
            mat_item = QTreeWidgetItem([f"[{material}]{prio_tag}"])
            mat_item.setData(0, _ROLE_MATERIAL, str(material))
            font = mat_item.font(0)
            font.setBold(True)
            mat_item.setFont(0, font)
            self._tree.addTopLevelItem(mat_item)
            # Always keep expanded (user can manually close)
            mat_item.setExpanded(True)
            for obj in model_objects:
                add_object_row(mat_item, obj)

        if non_model_objects:
            non_model_item = QTreeWidgetItem([f"NON MODEL ({len(non_model_objects)})"])
            font = non_model_item.font(0)
            font.setBold(True)
            non_model_item.setFont(0, font)
            non_model_item.setForeground(0, QBrush(QColor(150, 160, 175)))
            self._tree.addTopLevelItem(non_model_item)
            for obj in sorted(non_model_objects, key=lambda item: str(item.name).casefold()):
                if id(obj) in grouped_object_ids:
                    continue
                add_object_row(non_model_item, obj, show_material=True)
            non_model_item.setExpanded(False)

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
                stack = [top]
                while stack:
                    item = stack.pop()
                    if item.data(0, _ROLE_OBJ_ID) in target_ids:
                        item.setSelected(True)
                        self._tree.scrollToItem(item)
                    stack.extend(
                        item.child(index) for index in range(item.childCount())
                    )
        self._tree.blockSignals(False)
        # Emit selection after restoring state
        self._on_selection_changed()

    # ─────────────────────────────────────────────────── events
    def _on_item_click(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, _ROLE_PATTERN_EDIT) is not None:
            return
        if item.data(0, _ROLE_FEATURE_EDIT) is not None:
            return
        if item.data(0, _ROLE_SKETCH_EDIT) is not None:
            return
        transform_id = item.data(0, _ROLE_TRANSFORM_ID)
        if transform_id is not None:
            return

        triad_flag = item.data(0, _ROLE_PLANE_TRIAD)
        if triad_flag is not None:
            self.plane_triad_visibility_changed.emit(not bool(triad_flag))
            return

        grid_flag = item.data(0, _ROLE_GRID_VISIBLE)
        if grid_flag is not None:
            self.grid_visibility_changed.emit(not bool(grid_flag))
            return

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

    def _on_item_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        sketch_obj_id = item.data(0, _ROLE_SKETCH_EDIT)
        if sketch_obj_id is not None:
            obj = self._obj_map.get(sketch_obj_id)
            if obj is not None:
                self.sketch_edit_requested.emit(obj)
            return
        feature_obj_id = item.data(0, _ROLE_FEATURE_EDIT)
        if feature_obj_id is not None:
            obj = self._obj_map.get(feature_obj_id)
            if obj is not None:
                self.feature_edit_requested.emit(obj)
            return
        pattern_obj_id = item.data(0, _ROLE_PATTERN_EDIT)
        if pattern_obj_id is not None:
            obj = self._obj_map.get(pattern_obj_id)
            if obj is not None:
                self.pattern_edit_requested.emit(obj)
            return
        transform_id = item.data(0, _ROLE_TRANSFORM_ID)
        if transform_id is not None:
            obj = self._obj_map.get(transform_id)
            if obj is not None:
                self.transform_edit_requested.emit([obj])

    def _on_selection_changed(self) -> None:
        selected_items = self._tree.selectedItems()
        selected = []
        selected_ids: set[int] = set()
        for item in selected_items:
            obj_id = item.data(0, _ROLE_OBJ_ID)
            if obj_id is not None and obj_id not in selected_ids:
                obj = self._obj_map.get(obj_id)
                if obj:
                    selected.append(obj)
                    selected_ids.add(obj_id)
            group_id = item.data(0, _ROLE_GROUP_ID)
            group = self._group_map.get(str(group_id)) if group_id is not None else None
            if group is not None:
                for obj in self._objects_in_group(group):
                    if id(obj) not in selected_ids:
                        selected.append(obj)
                        selected_ids.add(id(obj))
        has_operation_row = any(
            item.data(0, role) is not None
            for item in selected_items
            for role in (_ROLE_FEATURE_EDIT, _ROLE_SKETCH_EDIT)
        )
        if selected or not has_operation_row:
            self.selection_changed.emit(selected)

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return

        sketch_obj_id = item.data(0, _ROLE_SKETCH_EDIT)
        if sketch_obj_id is not None:
            obj = self._obj_map.get(sketch_obj_id)
            if obj is None:
                return
            menu = QMenu(self._tree)
            act_edit = QAction("Edit Sketch…", menu)
            act_edit.triggered.connect(lambda: self.sketch_edit_requested.emit(obj))
            menu.addAction(act_edit)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        feature_obj_id = item.data(0, _ROLE_FEATURE_EDIT)
        if feature_obj_id is not None:
            obj = self._obj_map.get(feature_obj_id)
            if obj is None:
                return
            operation = "Extrude" if isinstance(obj, ExtrudedObject) else "Revolve"
            menu = QMenu(self._tree)
            act_edit = QAction(f"Edit {operation} Operation…", menu)
            act_edit.triggered.connect(lambda: self.feature_edit_requested.emit(obj))
            menu.addAction(act_edit)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        pattern_obj_id = item.data(0, _ROLE_PATTERN_EDIT)
        if pattern_obj_id is not None:
            obj = self._obj_map.get(pattern_obj_id)
            if obj is None:
                return
            menu = QMenu(self._tree)
            act_edit = QAction("Edit Pattern Settings…", menu)
            act_edit.triggered.connect(lambda: self.pattern_edit_requested.emit(obj))
            menu.addAction(act_edit)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        obj_id = item.data(0, _ROLE_OBJ_ID)
        if obj_id is not None:
            self._show_object_context_menu(item, pos)
            return

        if item.data(0, _ROLE_GROUPS_ROOT):
            selected = self._selected_objects_for_context(item)
            if selected:
                menu = QMenu(self._tree)
                action = QAction("Create Group from Selected…", menu)
                action.triggered.connect(
                    lambda: self._request_group_create(selected)
                )
                menu.addAction(action)
                menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        if item.data(0, _ROLE_GROUP_ID) is not None:
            self._show_group_context_menu(item, pos)
            return

        triad_flag = item.data(0, _ROLE_PLANE_TRIAD)
        if triad_flag is not None:
            menu = QMenu(self._tree)
            if bool(triad_flag):
                act_toggle = QAction("Hide Plane XYZ Triad", menu)
            else:
                act_toggle = QAction("Show Plane XYZ Triad", menu)
            act_toggle.triggered.connect(lambda: self.plane_triad_visibility_changed.emit(not bool(triad_flag)))
            menu.addAction(act_toggle)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return

        grid_flag = item.data(0, _ROLE_GRID_VISIBLE)
        if grid_flag is not None:
            menu = QMenu(self._tree)
            if bool(grid_flag):
                act_toggle = QAction("Hide Grid", menu)
            else:
                act_toggle = QAction("Show Grid", menu)
            act_toggle.triggered.connect(lambda: self.grid_visibility_changed.emit(not bool(grid_flag)))
            menu.addAction(act_toggle)
            adaptive_flag = bool(item.data(0, _ROLE_GRID_ADAPTIVE))
            menu.addSeparator()
            act_adaptive = QAction("Fit Grid to Objects", menu)
            act_adaptive.setCheckable(True)
            act_adaptive.setChecked(adaptive_flag)
            act_adaptive.toggled.connect(self.grid_adaptive_changed.emit)
            menu.addAction(act_adaptive)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
            return
        # ── Material header row ───────────────────────────────────────────
        material_name = item.data(0, _ROLE_MATERIAL)
        if material_name is not None:
            menu = QMenu(self._tree)
            act_higher = QAction("⬆ Set Higher Priority", menu)
            act_higher.triggered.connect(lambda: self.material_priority_changed.emit(material_name, 1))
            menu.addAction(act_higher)
            act_lower = QAction("⬇ Set Lower Priority", menu)
            act_lower.triggered.connect(lambda: self.material_priority_changed.emit(material_name, -1))
            menu.addAction(act_lower)
            menu.exec_(self._tree.viewport().mapToGlobal(pos))
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

        act_view_normal = QAction("View Normal to Plane", menu)
        act_view_normal.triggered.connect(lambda: self.plane_view_normal.emit(plane))
        menu.addAction(act_view_normal)

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
        seen_ids: set[int] = set()
        for it in self._tree.selectedItems():
            obj_id = it.data(0, _ROLE_OBJ_ID)
            if obj_id is not None:
                obj = self._obj_map.get(obj_id)
                if obj is not None and id(obj) not in seen_ids:
                    selected.append(obj)
                    seen_ids.add(id(obj))
            group_id = it.data(0, _ROLE_GROUP_ID)
            group = self._group_map.get(str(group_id)) if group_id is not None else None
            if group is not None:
                for obj in self._objects_in_group(group):
                    if id(obj) not in seen_ids:
                        selected.append(obj)
                        seen_ids.add(id(obj))
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
        self._add_grouping_menu(menu, objs)
        menu.addSeparator()
        self._add_object_actions(menu, objs)
        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _add_object_actions(self, menu: QMenu, objs: list[EMObject]) -> None:
        edit_menu = menu.addMenu("Edit…")
        act_transform = QAction(
            "Edit Transform…" if len(objs) == 1 else f"Edit Group Transform ({len(objs)} objects)…",
            edit_menu,
        )
        act_transform.triggered.connect(lambda: self.transform_edit_requested.emit(list(objs)))
        edit_menu.addAction(act_transform)

        if len(objs) == 1:
            act_rename = QAction("Rename…", edit_menu)
            act_rename.triggered.connect(lambda: self._rename_object(objs[0]))
            edit_menu.addAction(act_rename)

            if str(getattr(objs[0], "material", "")).strip().upper() == "AIR":
                act_open_region = QAction("Set as Simulation Region", edit_menu)
                act_open_region.triggered.connect(lambda: self.set_open_region_requested.emit(objs[0]))
                edit_menu.addAction(act_open_region)
        else:
            rename_menu = edit_menu.addMenu("Rename Object…")
            for obj in objs:
                action = rename_menu.addAction(obj.name)
                action.triggered.connect(
                    lambda _checked=False, target=obj: self._rename_object(target)
                )

            air_objects = [
                obj for obj in objs
                if str(getattr(obj, "material", "")).strip().upper() == "AIR"
            ]
            if air_objects:
                region_menu = edit_menu.addMenu("Set as Simulation Region")
                for obj in air_objects:
                    action = region_menu.addAction(obj.name)
                    action.triggered.connect(
                        lambda _checked=False, target=obj:
                        self.set_open_region_requested.emit(target)
                    )

        if len(objs) >= 2:
            act_bulk_rename = QAction(f"Bulk Rename {len(objs)} objects…", edit_menu)
            act_bulk_rename.triggered.connect(lambda: self._bulk_rename_dialog(list(objs)))
            edit_menu.addAction(act_bulk_rename)

        port_menu = menu.addMenu("Assign Port…")
        port_types = (
            ("Waveguide Port", "WaveguidePort"),
            ("Lumped Port", "LumpedPort"),
            ("Plane Wave", "PlaneWave"),
        )
        for obj in objs:
            target_menu = (
                port_menu
                if len(objs) == 1
                else port_menu.addMenu(obj.name)
            )
            for label, port_type in port_types:
                action = target_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, target=obj, kind=port_type:
                    self.assign_port_requested.emit(target, kind)
                )

        boundary_menu = menu.addMenu("Assign Boundary Condition…")
        act_assign_bc = boundary_menu.addAction("Assign to Selected Object(s)…")
        act_assign_bc.triggered.connect(
            lambda: self.assign_boundary_requested.emit(list(objs))
        )

        mesh_menu = menu.addMenu("Mesh…")
        act_assign_mesh = QAction("Assign Mesh Resolution (1/λ)…", mesh_menu)
        act_assign_mesh.triggered.connect(lambda: self.assign_mesh_resolution_requested.emit(list(objs)))
        mesh_menu.addAction(act_assign_mesh)

        act_assign_refinement = QAction("Assign Mesh Refinement…", mesh_menu)
        act_assign_refinement.triggered.connect(lambda: self.assign_mesh_refinement_requested.emit(list(objs)))
        mesh_menu.addAction(act_assign_refinement)

        menu.addSeparator()

        act_set_model = QAction("Set as MODEL", menu)
        act_set_model.triggered.connect(lambda: self.objects_model_role_changed.emit(list(objs), True))
        menu.addAction(act_set_model)

        act_set_non_model = QAction("Set as NON MODEL", menu)
        act_set_non_model.triggered.connect(lambda: self.objects_model_role_changed.emit(list(objs), False))
        menu.addAction(act_set_non_model)

        menu.addSeparator()

        act_hide = QAction("Hide Selected", menu)
        act_hide.triggered.connect(lambda: self.objects_hide.emit(list(objs)))
        menu.addAction(act_hide)

        act_show = QAction("Show Selected", menu)
        act_show.triggered.connect(lambda: self.objects_show.emit(list(objs)))
        menu.addAction(act_show)

        act_isolate = QAction("Isolate Selected", menu)
        act_isolate.triggered.connect(lambda: self.objects_isolate.emit(list(objs)))
        menu.addAction(act_isolate)

    def _add_grouping_menu(
        self,
        menu: QMenu,
        objects: list[EMObject],
        *,
        context_group: SceneObjectGroup | None = None,
        context_subgroup: bool = False,
        context_parent: SceneObjectGroup | None = None,
    ) -> None:
        grouping_menu = menu.addMenu("Grouping…")
        action = grouping_menu.addAction("Create Group from Selected…")
        action.triggered.connect(
            lambda: self._request_group_create(list(objects))
        )

        parent_group = (
            context_parent
            if context_subgroup
            else context_group or self._common_parent_group(objects)
        )
        if parent_group is not None and not context_group:
            action = grouping_menu.addAction(
                f"Create Subgroup in '{parent_group.name}'…"
            )
            action.triggered.connect(
                lambda: self._request_subgroup_create(
                    parent_group, list(objects)
                )
            )

        if context_group is not None:
            if objects and (not context_subgroup or context_parent is not None):
                action = grouping_menu.addAction(
                    "Move Selected Here"
                    if context_subgroup
                    else "Move Selected to Group"
                )
                action.triggered.connect(
                    lambda: self.group_assign_requested.emit(
                        list(objects),
                        context_parent if context_subgroup else context_group,
                        context_group if context_subgroup else None,
                    )
                )
            if not context_subgroup:
                action = grouping_menu.addAction("Create Subgroup…")
                action.triggered.connect(
                    lambda: self._request_subgroup_create(context_group, [])
                )
            return

        move_menu = grouping_menu.addMenu("Move Selected to Group")
        self._populate_group_assignment_menu(move_menu, list(objects))

    @staticmethod
    def _objects_in_group(group: SceneObjectGroup) -> list[EMObject]:
        return [
            *group.objects,
            *(obj for subgroup in group.subgroups for obj in subgroup.objects),
        ]

    @staticmethod
    def _material_summary(objects: list[EMObject]) -> str:
        if not objects:
            return "Empty"
        materials = {
            str(getattr(obj, "material", "")).strip()
            for obj in objects
            if str(getattr(obj, "material", "")).strip()
        }
        if not materials:
            return "No Material"
        return next(iter(materials)) if len(materials) == 1 else "Mixed"

    def _common_parent_group(
        self,
        objects: list[EMObject],
    ) -> SceneObjectGroup | None:
        memberships = [self._object_group_map.get(id(obj)) for obj in objects]
        if not memberships or any(membership is None for membership in memberships):
            return None
        parent = memberships[0][0]
        return parent if all(membership[0] is parent for membership in memberships) else None

    def _request_group_create(self, objects: list[EMObject]) -> None:
        name, ok = QInputDialog.getText(self, "Create Group", "Group name:")
        if ok and name.strip():
            self.group_create_requested.emit(list(objects), name.strip())

    def _request_subgroup_create(
        self,
        parent: SceneObjectGroup,
        objects: list[EMObject],
    ) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Create Subgroup",
            f"Subgroup name in '{parent.name}':",
        )
        if ok and name.strip():
            self.subgroup_create_requested.emit(
                parent,
                list(objects),
                name.strip(),
            )

    def _populate_group_assignment_menu(
        self,
        menu: QMenu,
        objects: list[EMObject],
    ) -> None:
        if not self._groups:
            menu.setEnabled(False)
            return
        for group in self._groups:
            group_menu = menu.addMenu(group.name) if group.subgroups else None
            action = (
                menu.addAction(group.name)
                if group_menu is None
                else group_menu.addAction("In group (top level)")
            )
            action.triggered.connect(
                lambda _checked=False, target=group:
                self.group_assign_requested.emit(list(objects), target, None)
            )
            if group_menu is not None:
                for subgroup in group.subgroups:
                    action = group_menu.addAction(subgroup.name)
                    action.triggered.connect(
                        lambda _checked=False, target=group, child=subgroup:
                        self.group_assign_requested.emit(
                            list(objects),
                            target,
                            child,
                        )
                    )

    def _show_group_context_menu(self, item: QTreeWidgetItem, pos) -> None:
        target = self._group_map.get(str(item.data(0, _ROLE_GROUP_ID)))
        if target is None:
            return
        is_subgroup = bool(item.data(0, _ROLE_IS_SUBGROUP))
        selected = self._selected_objects_for_context(item)
        group_objects = (
            list(target.objects)
            if is_subgroup
            else self._objects_in_group(target)
        )
        menu = QMenu(self._tree)

        parent = None
        if is_subgroup:
            parent = next(
                (group for group in self._groups if target in group.subgroups),
                None,
            )
        self._add_grouping_menu(
            menu,
            selected,
            context_group=target,
            context_subgroup=is_subgroup,
            context_parent=parent,
        )

        priority_group = parent if is_subgroup else target
        priority_objects = self._objects_in_group(priority_group) if priority_group else []
        group_materials = {
            str(getattr(obj, "material", "")).strip()
            for obj in priority_objects
            if str(getattr(obj, "material", "")).strip()
        }
        menu.addSeparator()
        if priority_group is not None and len(group_materials) == 1:
            material = next(iter(group_materials))
            current_priority = (
                priority_group.priority
                if priority_group.priority is not None
                else self._material_priorities.get(material, 0)
            )
            priority_menu = menu.addMenu(
                f"Group Priority (current {current_priority:+d})"
            )
            action = priority_menu.addAction("Increase Priority")
            action.triggered.connect(
                lambda _checked=False, root=priority_group:
                self.group_priority_changed.emit(root, 1)
            )
            action = priority_menu.addAction("Decrease Priority")
            action.triggered.connect(
                lambda _checked=False, root=priority_group:
                self.group_priority_changed.emit(root, -1)
            )
        else:
            action = menu.addAction("Group Priority unavailable (requires one material)")
            action.setEnabled(False)

        action = None
        if is_subgroup and parent is not None:
            action = QAction("Delete Subgroup (keep objects in group)", menu)
            action.triggered.connect(
                lambda: self.subgroup_delete_requested.emit(parent, target)
            )
        elif not is_subgroup:
            action = QAction("Delete Group (ungroup all objects)", menu)
            action.triggered.connect(
                lambda: self.group_delete_requested.emit(target)
            )
        if action is not None:
            menu.addAction(action)

        if group_objects:
            menu.addSeparator()
            self._add_object_actions(menu, group_objects)
        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _bulk_rename_dialog(self, objs: list) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QLineEdit, QSpinBox,
            QDialogButtonBox, QLabel,
        )
        dlg = QDialog(None)
        dlg.setWindowTitle(f"Bulk Rename ({len(objs)} objects)")
        form = QFormLayout(dlg)

        le_name = QLineEdit()
        le_name.setPlaceholderText("e.g. Part")
        form.addRow("Base name", le_name)

        sb_start = QSpinBox()
        sb_start.setRange(0, 999999)
        sb_start.setValue(1)
        form.addRow("Start index", sb_start)

        note = QLabel(f"Objects will be renamed: <b>&lt;name&gt;_1</b>, <b>&lt;name&gt;_2</b>, …")
        note.setWordWrap(True)
        form.addRow("", note)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec_() == QDialog.Accepted:
            base = le_name.text().strip()
            if base:
                self.objects_bulk_rename.emit(list(objs), base, int(sb_start.value()))

    def _rename_object(self, obj: EMObject) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename Object", "Name:", text=obj.name
        )
        if ok and new_name.strip() and new_name.strip() != obj.name:
            self.object_rename.emit(obj, new_name.strip())
