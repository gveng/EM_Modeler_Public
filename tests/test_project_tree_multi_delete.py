from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget


def _make_tree():
    app = QApplication.instance() or QApplication([])
    tree = ProjectTreeWidget()
    tree.load_settings({
        "ports": [
            {"name": "Port A", "number": 11, "type": "LumpedPort", "custom": "A"},
            {"name": "Port B", "number": 41, "type": "LumpedPort", "custom": "B"},
            {"name": "Port C", "number": 93, "type": "LumpedPort", "custom": "C"},
        ],
        "simulations": [
            {"name": "Sweep A", "type": "Sweep"},
            {"name": "Sweep B", "type": "Sweep"},
            {"name": "Sweep C", "type": "Sweep"},
        ],
        "outputs": [
            {"name": "Output A", "simulation": "Sweep A"},
            {"name": "Output B", "simulation": "Sweep B"},
            {"name": "Output C", "simulation": "Sweep C"},
        ],
        "mesh": {
            "object_resolutions": {"Keep me": 0.2},
            "local_refinements": [
                {"object": "Repeated", "mode": "face", "faces": ["+x"], "size_mm": 0.1},
                {"object": "Repeated", "mode": "face", "faces": ["-y"], "size_mm": 0.2},
                {"object": "Repeated", "mode": "face", "faces": ["+z"], "size_mm": 0.3},
            ],
        },
    })
    return app, tree


def _select_rows(tree, parent, indices):
    for index in indices:
        parent.child(index).setSelected(True)
    tree._tree.setFocus()


def test_delete_key_removes_only_selected_ports_and_emits_once(monkeypatch):
    app, tree = _make_tree()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._p_node, [0, 2])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    assert tree.get_settings()["ports"] == [
        {"name": "Port B", "number": 41, "type": "LumpedPort", "custom": "B"}
    ]
    assert tree._p_node.childCount() == 1
    assert changes == [True]
    assert app is not None
    tree.close()


def test_delete_key_removes_only_selected_local_refinements(monkeypatch):
    app, tree = _make_tree()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._m_local_node, [0, 2])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    settings = tree.get_settings()
    assert settings["mesh"]["local_refinements"] == [
        {
            "object": "Repeated",
            "enabled": True,
            "mode": "face",
            "faces": ["-y"],
            "size_mm": 0.2,
            "growth_rate": 3.0,
            "max_size_mm": None,
        }
    ]
    assert settings["mesh"]["object_resolutions"] == {"Keep me": 0.2}
    assert tree._m_local_node.childCount() == 1
    assert tree._m_local_node.isExpanded()
    assert changes == [True]
    assert app is not None
    tree.close()


def test_delete_key_removes_selected_mesh_assignments(monkeypatch):
    app, tree = _make_tree()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    mesh_cfg = tree._settings["mesh"]
    mesh_cfg["object_resolutions"].update({
        "Delete A": 0.1,
        "Delete B": 0.3,
    })
    tree._refresh_object_mesh_assignments()
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._m_obj_node, [1, 2])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    assert tree.get_settings()["mesh"]["object_resolutions"] == {"Keep me": 0.2}
    assert tree._m_obj_node.childCount() == 2
    assert changes == [True]
    assert app is not None
    tree.close()


def test_delete_key_preserves_a_simulation_and_prunes_its_removed_outputs(monkeypatch):
    app, tree = _make_tree()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._s_node, [0, 1, 2])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    settings = tree.get_settings()
    assert len(settings["simulations"]) == 1
    assert settings["simulations"][0]["name"] == "Simulation_1"
    assert settings["outputs"] == []
    assert tree._s_node.childCount() == 1
    assert changes == [True]
    assert app is not None
    tree.close()


def test_batch_simulation_delete_keeps_outputs_for_remaining_simulations(monkeypatch):
    app, tree = _make_tree()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    _select_rows(tree, tree._s_node, [0, 2])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    settings = tree.get_settings()
    assert [item["name"] for item in settings["simulations"]] == ["Sweep B"]
    assert [item["name"] for item in settings["outputs"]] == ["Output B"]
    assert app is not None
    tree.close()


def test_delete_key_ignores_mixed_deletable_row_types():
    app, tree = _make_tree()
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._p_node, [0])
    tree._m_local_node.child(0).setSelected(True)

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    assert len(tree.get_settings()["ports"]) == 3
    assert len(tree.get_settings()["mesh"]["local_refinements"]) == 3
    assert changes == []
    assert app is not None
    tree.close()


def test_delete_key_ignores_mixed_deletable_and_non_deletable_rows():
    app, tree = _make_tree()
    changes = []
    tree.settings_changed.connect(lambda: changes.append(True))
    _select_rows(tree, tree._m_obj_node, [0, 1])

    QTest.keyClick(tree._tree, Qt.Key_Delete)

    assert tree.get_settings()["mesh"]["object_resolutions"] == {"Keep me": 0.2}
    assert changes == []
    assert app is not None
    tree.close()


def test_context_selection_is_scoped_to_clicked_row_when_it_is_not_selected():
    app, tree = _make_tree()
    selected = tree._p_node.child(0)
    clicked = tree._p_node.child(2)
    selected.setSelected(True)

    assert tree._context_selection_indices(clicked, "__port_idx__") == [2]

    assert app is not None
    tree.close()


def test_context_selection_collects_only_selected_rows_of_clicked_type():
    app, tree = _make_tree()
    first = tree._p_node.child(0)
    second = tree._p_node.child(1)
    first.setSelected(True)
    second.setSelected(True)

    assert tree._context_selection_indices(second, "__port_idx__") == [0, 1]

    assert app is not None
    tree.close()
