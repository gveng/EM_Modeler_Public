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

Shows all scene objects grouped by material.
Clicking an item selects the corresponding 3D object.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QBrush, QColor, QAction

from ..scene.em_objects import EMObject

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
    objects_model_role_changed = Signal(list, bool)  # List[EMObject], is_model
    object_rename     = Signal(object, str)  # EMObject, new_name
    objects_bulk_rename = Signal(list, str, int)  # List[EMObject], base_name, start_index
    assign_port_requested = Signal(object)   # EMObject
    assign_boundary_requested = Signal(list)  # List[EMObject]
    assign_mesh_resolution_requested = Signal(list)  # List[EMObject]
    assign_mesh_refinement_requested = Signal(list)  # List[EMObject]
    set_open_region_requested = Signal(object)  # AIR EMObject
    material_priority_changed = Signal(str, int)  # material_name, delta (1 for higher, -1 for lower)
    transform_edit_requested = Signal(list)  # List[EMObject]
    pattern_edit_requested = Signal(object)  # EMObject whose pattern definition should be edited

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

    # ─────────────────────────────────────────────────── public API
    def refresh(self, by_material: Dict[str, List[EMObject]],
                planes: Optional[List] = None,
                active_plane=None,
                plane_triad_visible: bool = True,
                grid_visible: bool = True,
                grid_adaptive: bool = False,
                material_priorities: Optional[Dict[str, int]] = None) -> None:
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
                    f"Origin: ({p.origin[0]:.2f}, {p.origin[1]:.2f}, {p.origin[2]:.2f})\n"
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
        non_model_objects: List[EMObject] = []

        def add_object_row(parent: QTreeWidgetItem, obj: EMObject) -> None:
            label = obj.name
            if not obj.is_visible():
                label += "  [hidden]"
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
            if id(obj) in prev_selected_ids:
                child.setSelected(True)

        ordered_materials = sorted(
            by_material.items(),
            key=lambda kv: (-int(priorities.get(str(kv[0]), 0)), str(kv[0]).lower()),
        )
        for material, objects in ordered_materials:
            model_objects = []
            for obj in objects:
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
                add_object_row(non_model_item, obj)
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
        if item.data(0, _ROLE_PATTERN_EDIT) is not None:
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

        act_transform = QAction(
            "Edit Transform…" if len(objs) == 1 else f"Edit Group Transform ({len(objs)} objects)…",
            menu,
        )
        act_transform.triggered.connect(lambda: self.transform_edit_requested.emit(list(objs)))
        menu.addAction(act_transform)

        if len(objs) == 1:

            menu.addSeparator()
            act_rename = QAction("Rename…", menu)
            act_rename.triggered.connect(lambda: self._rename_object(objs[0]))
            menu.addAction(act_rename)

            menu.addSeparator()
            act_assign_port = QAction("Assign Port…", menu)
            act_assign_port.triggered.connect(lambda: self.assign_port_requested.emit(objs[0]))
            menu.addAction(act_assign_port)

            if str(getattr(objs[0], "material", "")).strip().upper() == "AIR":
                act_open_region = QAction("Set as Simulation Region", menu)
                act_open_region.triggered.connect(lambda: self.set_open_region_requested.emit(objs[0]))
                menu.addAction(act_open_region)

            menu.addSeparator()

        act_assign_bc = QAction("Assign Boundary Condition…", menu)
        act_assign_bc.triggered.connect(lambda: self.assign_boundary_requested.emit(list(objs)))
        menu.addAction(act_assign_bc)

        if len(objs) >= 2:
            act_bulk_rename = QAction(f"Bulk Rename {len(objs)} objects…", menu)
            act_bulk_rename.triggered.connect(lambda: self._bulk_rename_dialog(list(objs)))
            menu.addAction(act_bulk_rename)
            menu.addSeparator()

        act_assign_mesh = QAction("Assign Mesh Resolution (1/λ)…", menu)
        act_assign_mesh.triggered.connect(lambda: self.assign_mesh_resolution_requested.emit(list(objs)))
        menu.addAction(act_assign_mesh)

        act_assign_refinement = QAction("Assign Mesh Refinement…", menu)
        act_assign_refinement.triggered.connect(lambda: self.assign_mesh_refinement_requested.emit(list(objs)))
        menu.addAction(act_assign_refinement)

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
