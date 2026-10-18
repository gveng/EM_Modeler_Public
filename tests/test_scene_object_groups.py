import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import vtk
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from em3d_modeler.emerge.project_file import ProjectFile
from em3d_modeler.scene.em_objects import BoxObject
from em3d_modeler.scene.scene_manager import SceneManager
from em3d_modeler.ui.body_properties_widget import BodyPropertiesWidget
from em3d_modeler.ui.main_window import MainWindow
from em3d_modeler.ui.materials_widget import (
    MaterialsWidget,
    _ROLE_GROUP_ID,
    _ROLE_OBJ_ID,
)


@pytest.fixture
def scene():
    return SceneManager(vtk.vtkRenderer())


def _add_objects(scene, *names):
    objects = [BoxObject(name) for name in names]
    for obj in objects:
        scene.add_object(obj)
    return objects


def test_group_and_subgroup_names_are_unique_only_within_their_parent(scene):
    first, second = _add_objects(scene, "First", "Second")
    group_a = scene.create_object_group("Board A", [first])
    group_b = scene.create_object_group("Board B", [second])

    subgroup_a = scene.create_object_subgroup(group_a, "Signals", [first])
    subgroup_b = scene.create_object_subgroup(group_b, "Signals", [second])

    assert subgroup_a.name == subgroup_b.name
    with pytest.raises(ValueError, match="already exists"):
        scene.create_object_group("board a", [second])
    with pytest.raises(ValueError, match="already exists"):
        scene.create_object_subgroup(group_a, "signals", [second])


def test_creating_groups_moves_objects_without_changing_object_identity(scene):
    first, second = _add_objects(scene, "First", "Second")
    original_group = scene.create_object_group("Original", [first, second])
    subgroup = scene.create_object_subgroup(original_group, "One", [first])

    replacement = scene.create_object_group("Replacement", [first])

    assert first in replacement.objects
    assert first not in subgroup.objects
    assert second in original_group.objects
    assert scene.objects == [first, second]


def test_deleting_subgroup_promotes_objects_and_deleting_group_ungroups_all(scene):
    direct, nested = _add_objects(scene, "Direct", "Nested")
    group = scene.create_object_group("Group", [direct])
    subgroup = scene.create_object_subgroup(group, "Child", [nested])

    scene.delete_object_subgroup(group, subgroup)

    assert group.objects == [direct, nested]
    assert group.subgroups == []
    assert scene.group_for_object(nested) == (group, None)

    scene.delete_object_group(group)

    assert scene.object_groups == []
    assert scene.group_for_object(direct) is None
    assert scene.group_for_object(nested) is None
    assert scene.objects == [direct, nested]


def test_group_and_subgroup_members_must_have_one_material(scene):
    first, second = _add_objects(scene, "First", "Second")
    second.material = "Copper"
    with pytest.raises(ValueError, match="same material"):
        scene.create_object_group("Mixed", [first, second])

    group = scene.create_object_group("One Material", [first])
    with pytest.raises(ValueError, match="same material"):
        scene.create_object_subgroup(group, "Mixed", [second])
    with pytest.raises(ValueError, match="same material"):
        scene.assign_objects_to_group([second], group)


def test_material_change_validation_blocks_mixed_group_and_allows_uniform_repair(scene):
    first, second, third = _add_objects(scene, "First", "Second", "Third")
    group = scene.create_object_group("Legacy Mixed", [first])
    subgroup = scene.create_object_subgroup(group, "Existing", [second, third])
    second.material = "Copper"
    third.material = "Copper"

    assert scene.material_assignment_error([first], "Copper") is None
    error = scene.material_assignment_error([first], "Aluminum")
    assert error is not None and "Legacy Mixed" in error
    assert scene.material_assignment_error([first, second, third], "Aluminum") is None
    with pytest.raises(ValueError, match="same material"):
        scene.set_object_group_priority(subgroup, 2)
    for obj in (first, second, third):
        obj.material = "Aluminum"
    assert scene.set_object_group_priority(subgroup, 2) is group
    assert scene.group_object_priorities() == {
        "First": 2,
        "Second": 2,
        "Third": 2,
    }


def test_group_priority_persists_and_round_trips(scene):
    obj = _add_objects(scene, "Part")[0]
    group = scene.create_object_group("Group", [obj])
    group.priority = 3
    serialized = scene.object_groups_to_json()

    restored = SceneManager(vtk.vtkRenderer())
    restored_obj = _add_objects(restored, "Part")[0]
    restored.object_groups_from_json(serialized)

    restored_group = restored.object_groups[0]
    assert restored_group.priority == 3
    assert restored.group_object_priorities() == {restored_obj.name: 3}


def test_priority_change_on_subgroup_updates_parent_from_material_priority(scene):
    obj = _add_objects(scene, "Part")[0]
    obj.material = "Copper"
    group = scene.create_object_group("Group", [obj])
    subgroup = scene.create_object_subgroup(group, "Subgroup")
    scene_changes = []
    messages = []
    window = SimpleNamespace(
        _viewport=SimpleNamespace(
            scene=scene,
            scene_changed=SimpleNamespace(emit=lambda: scene_changes.append(True)),
        ),
        _project_tree=SimpleNamespace(
            get_settings=lambda: {"material_priorities": {"Copper": 4}}
        ),
        _info_bar=SimpleNamespace(set_info=messages.append),
    )

    MainWindow._on_object_group_priority_changed(window, subgroup, 1)

    assert group.priority == 5
    assert scene.group_object_priorities() == {"Part": 5}
    assert scene_changes == [True]
    assert messages == ["Group 'Group' priority set to 5"]


def test_group_membership_tracks_removal_and_survives_name_changes(scene):
    obj = _add_objects(scene, "Before")[0]
    group = scene.create_object_group("Group", [obj])
    subgroup = scene.create_object_subgroup(group, "Child", [obj])

    obj.name = "After"
    stored_groups = scene.object_groups_to_json()

    restored = SceneManager(vtk.vtkRenderer())
    restored_obj = _add_objects(restored, "After")[0]
    restored.object_groups_from_json(stored_groups)

    restored_group = restored.object_groups[0]
    assert restored_group.group_id == group.group_id
    assert restored_group.subgroups[0].group_id == subgroup.group_id
    assert restored.group_for_object(restored_obj) == (
        restored_group,
        restored_group.subgroups[0],
    )

    restored.remove_object(restored_obj)
    assert restored_group.subgroups[0].objects == []


def test_group_membership_round_trips_in_project_file(tmp_path, scene):
    obj = _add_objects(scene, "Part")[0]
    scene.create_object_group("Group", [obj])
    path = tmp_path / "grouped.em3d"

    ProjectFile.save(
        path,
        project_name="grouped",
        settings={},
        objects_json=scene.to_json(),
        object_groups=scene.object_groups_to_json(),
    )

    project = ProjectFile.load(path)
    restored = SceneManager(vtk.vtkRenderer())
    restored.from_json(project["objects"])
    restored.object_groups_from_json(project["object_groups"])

    assert restored.object_groups[0].name == "Group"
    assert restored.object_groups[0].objects[0].name == "Part"


def test_undo_snapshot_tracks_group_membership(scene):
    obj = _add_objects(scene, "Part")[0]
    window = SimpleNamespace(_viewport=SimpleNamespace(scene=scene))
    before = MainWindow._history_snapshot(window)

    scene.create_object_group("Group", [obj])
    after = MainWindow._history_snapshot(window)

    assert before["object_groups"] == []
    assert after["object_groups"][0]["objects"] == ["Part"]


def test_materials_tree_displays_group_hierarchy_and_selects_its_members():
    if QApplication.instance() is None:
        app = QApplication([])
    else:
        app = QApplication.instance()
    first, second = BoxObject("First"), BoxObject("Second")
    first.material = "Copper"
    second.material = "Copper"
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(first)
    scene.add_object(second)
    group = scene.create_object_group("Board", [first])
    subgroup = scene.create_object_subgroup(group, "Signals", [second])
    widget = MaterialsWidget()
    widget.refresh(
        {"PEC": [first, second]},
        groups=scene.object_groups,
    )

    groups_root = widget._tree.topLevelItem(0)
    group_item = groups_root.child(0)
    subgroup_item = group_item.child(1)
    assert groups_root.text(0) == "Object Groups"
    assert group_item.text(0) == "Board  [Copper]"
    assert subgroup_item.text(0) == "Signals  [Copper]"
    assert group_item.data(0, _ROLE_GROUP_ID) == group.group_id
    assert subgroup_item.data(0, _ROLE_GROUP_ID) == subgroup.group_id
    assert group_item.child(0).data(0, _ROLE_OBJ_ID) == id(first)
    assert subgroup_item.child(0).data(0, _ROLE_OBJ_ID) == id(second)
    assert group_item.child(0).text(0) == "First  [Copper]"
    assert subgroup_item.child(0).text(0) == "Second  [Copper]"

    selected = []
    widget.selection_changed.connect(selected.append)
    group_item.setSelected(True)
    widget._on_selection_changed()
    assert selected[-1] == [first, second]

    widget.close()
    app.processEvents()


def test_multi_material_selection_shows_mixed_state_until_target_is_chosen():
    app = QApplication.instance() or QApplication([])
    first, second = BoxObject("First"), BoxObject("Second")
    second.material = "Copper"
    properties = BodyPropertiesWidget()
    properties.set_materials(["PEC", "Copper"])
    properties.set_selection([first, second])

    assert properties._mat_label.text() == "Material to apply (mixed selection):"
    assert properties._mat_combo.currentText() == ""
    properties._mat_combo.setCurrentText("Copper")
    properties._apply_bulk()
    assert first.material == "Copper"
    assert second.material == "Copper"

    properties.close()
    app.processEvents()


def test_object_context_menu_offers_group_creation_and_existing_targets(monkeypatch):
    app = QApplication.instance() or QApplication([])
    first, second = BoxObject("First"), BoxObject("Second")
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(first)
    scene.add_object(second)
    group = scene.create_object_group("Board", [first])
    scene.create_object_subgroup(group, "Signals", [second])
    widget = MaterialsWidget()
    widget.refresh(
        {"PEC": [first, second]},
        groups=scene.object_groups,
    )
    menus = []

    class CapturingMenu(QMenu):
        def exec_(self, *_args):
            def action_tree(menu):
                return [
                    (
                        action.text(),
                        action_tree(action.menu())
                        if action.menu() is not None
                        else [],
                    )
                    for action in menu.actions()
                ]

            menus.append(action_tree(self))

    monkeypatch.setattr("em3d_modeler.ui.materials_widget.QMenu", CapturingMenu)
    first_item = widget._tree.topLevelItem(0).child(0).child(0)
    widget._show_object_context_menu(first_item, QPoint())

    labels = [label for label, _children in menus[0]]
    assert "Grouping…" in labels
    grouping = next(
        children for label, children in menus[0] if label == "Grouping…"
    )
    grouping_labels = [label for label, _children in grouping]
    assert "Create Group from Selected…" in grouping_labels
    assert "Move Selected to Group" in grouping_labels
    group_targets = next(
        children for label, children in grouping
        if label == "Move Selected to Group"
    )
    assert ("Board", [
        ("In group (top level)", []),
        ("Signals", []),
    ]) in group_targets

    widget.close()
    app.processEvents()


def test_group_context_menu_applies_object_actions_to_all_group_members(monkeypatch):
    app = QApplication.instance() or QApplication([])
    first, second = BoxObject("First"), BoxObject("Second")
    first.material = "AIR"
    second.material = "AIR"
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(first)
    scene.add_object(second)
    group = scene.create_object_group("Board", [first])
    scene.create_object_subgroup(group, "Signals", [second])
    widget = MaterialsWidget()
    widget.refresh({"PEC": [first, second]}, groups=scene.object_groups)
    group_item = widget._tree.topLevelItem(0).child(0)
    menus = []
    port_choices = []
    assigned_ports = []
    widget.assign_port_requested.connect(
        lambda target, kind: assigned_ports.append((target, kind))
    )

    class CapturingMenu(QMenu):
        def exec_(self, *_args):
            menus.append(self)
            port_action = next(
                action
                for action in self.actions()
                if action.text() == "Assign Port…"
            )
            port_menu = port_action.menu()
            for target_action in port_menu.actions():
                target_menu = target_action.menu()
                port_choices.append(
                    (target_action.text(), [
                        action.text() for action in target_menu.actions()
                    ])
                )
            port_menu.actions()[1].menu().actions()[1].trigger()

    monkeypatch.setattr("em3d_modeler.ui.materials_widget.QMenu", CapturingMenu)
    widget._show_group_context_menu(group_item, QPoint())

    actions = {action.text(): action for action in menus[0].actions()}
    edit_actions = {
        action.text(): action
        for action in actions["Edit…"].menu().actions()
    }
    assert "Edit Group Transform (2 objects)…" in edit_actions
    assert "Assign Boundary Condition…" in actions
    boundary_actions = {
        action.text(): action
        for action in actions["Assign Boundary Condition…"].menu().actions()
    }
    assert "Assign to Selected Object(s)…" in boundary_actions
    mesh_actions = {
        action.text(): action
        for action in actions["Mesh…"].menu().actions()
    }
    assert "Assign Mesh Resolution (1/λ)…" in mesh_actions
    assert "Assign Mesh Refinement…" in mesh_actions
    assert "Set as MODEL" in actions
    assert "Set as NON MODEL" in actions
    assert "Hide Selected" in actions
    assert "Show Selected" in actions
    assert "Isolate Selected" in actions
    assert "Bulk Rename 2 objects…" in edit_actions
    rename_menu = edit_actions["Rename Object…"].menu()
    assert [action.text() for action in rename_menu.actions()] == ["First", "Second"]
    assert port_choices == [
        ("First", ["Waveguide Port", "Lumped Port", "Plane Wave"]),
        ("Second", ["Waveguide Port", "Lumped Port", "Plane Wave"]),
    ]
    region_menu = edit_actions["Set as Simulation Region"].menu()
    assert [action.text() for action in region_menu.actions()] == ["First", "Second"]
    priority_menu = actions["Group Priority (current +0)"].menu()

    hidden = []
    widget.objects_hide.connect(hidden.append)
    actions["Hide Selected"].trigger()
    assert hidden == [[first, second]]
    assert assigned_ports == [(second, "LumpedPort")]
    regions = []
    widget.set_open_region_requested.connect(regions.append)
    region_menu.actions()[1].trigger()
    assert regions == [second]
    priorities = []
    widget.group_priority_changed.connect(
        lambda target, delta: priorities.append((target, delta))
    )
    priority_menu.actions()[0].trigger()
    assert priorities == [(group, 1)]

    widget.close()
    app.processEvents()


def test_subgroup_context_menu_applies_object_actions_only_to_subgroup_members(monkeypatch):
    app = QApplication.instance() or QApplication([])
    first, second = BoxObject("First"), BoxObject("Second")
    scene = SceneManager(vtk.vtkRenderer())
    scene.add_object(first)
    scene.add_object(second)
    group = scene.create_object_group("Board", [first])
    subgroup = scene.create_object_subgroup(group, "Signals", [second])
    widget = MaterialsWidget()
    widget.refresh({"PEC": [first, second]}, groups=scene.object_groups)
    subgroup_item = widget._tree.topLevelItem(0).child(0).child(1)
    menus = []

    class CapturingMenu(QMenu):
        def exec_(self, *_args):
            menus.append(self)

    monkeypatch.setattr("em3d_modeler.ui.materials_widget.QMenu", CapturingMenu)
    widget._show_group_context_menu(subgroup_item, QPoint())

    actions = {action.text(): action for action in menus[0].actions()}
    edit_actions = {
        action.text(): action
        for action in actions["Edit…"].menu().actions()
    }
    assert "Edit Transform…" in edit_actions
    assert "Edit Group Transform (2 objects)…" not in edit_actions
    hidden = []
    widget.objects_hide.connect(hidden.append)
    actions["Hide Selected"].trigger()
    assert hidden == [[second]]

    widget.close()
    app.processEvents()
