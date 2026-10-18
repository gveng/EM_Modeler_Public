import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from em3d_modeler.emerge.material_store import MaterialRecord, MaterialStore
from em3d_modeler.ui import material_library_dialog
from em3d_modeler.ui.material_library_dialog import MaterialLibraryDialog


def test_set_and_reload_global_db_select_existing_file(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "materials.json"
    db_path.write_text(
        json.dumps({"materials": [{"name": "Copper", "family": "Metals"}]}),
        encoding="utf-8",
    )
    store = MaterialStore()
    dialog = MaterialLibraryDialog(None, store, lambda: None)
    dialog._mark_changed = lambda: None
    monkeypatch.setattr(
        "em3d_modeler.ui.material_library_dialog.QFileDialog.getOpenFileName",
        lambda *_args: (str(db_path), "JSON (*.json)"),
    )

    dialog._set_global_db()
    assert store.global_db_path == str(db_path)
    assert "Copper" in store.global_records()
    dialog._reload_global_db()
    assert store.global_db_path == str(db_path)
    assert "Copper" in store.global_records()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_new_material_is_saved_to_global_library(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "materials.json"
    db_path.write_text(json.dumps({"materials": []}), encoding="utf-8")
    store = MaterialStore()
    store.set_global_db_path(str(db_path), create_if_missing=False)
    dialog = MaterialLibraryDialog(None, store, lambda: None)
    record = MaterialRecord("new-id", "Copper", "Metals", 1.0, 1.0, 0.0, 5.8e7)

    class AcceptedEditor:
        result_record = record

        def __init__(self, *_args):
            pass

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(material_library_dialog, "_MaterialEditorDialog", AcceptedEditor)

    dialog._new_material()

    assert dialog._source_combo.currentIndex() == 1
    assert dialog._current_record().name == "Copper"
    saved_data = json.loads(db_path.read_text(encoding="utf-8"))
    assert saved_data["materials"][0]["name"] == "Copper"
    assert saved_data["materials"][0]["source"] == "global"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_global_material_can_be_edited_and_saved(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    db_path = tmp_path / "materials.json"
    original = MaterialRecord("copper-id", "Copper", "Metals", 1.0, 1.0, 0.0, 5.8e7)
    db_path.write_text(
        json.dumps({"materials": [original.to_dict()]}),
        encoding="utf-8",
    )
    store = MaterialStore()
    store.set_global_db_path(str(db_path), create_if_missing=False)
    dialog = MaterialLibraryDialog(None, store, lambda: None)
    dialog._source_combo.setCurrentIndex(1)

    updated = MaterialRecord("copper-id", "Copper Alloy", "Metals", 1.0, 1.0, 0.0, 6.0e7)

    class AcceptedEditor:
        result_record = updated

        def __init__(self, *_args):
            pass

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(material_library_dialog, "_MaterialEditorDialog", AcceptedEditor)

    assert dialog._edit_btn.isEnabled()
    assert dialog._edit_btn.text() == "Edit Global Material..."
    dialog._edit_material()

    saved_data = json.loads(db_path.read_text(encoding="utf-8"))
    assert len(saved_data["materials"]) == 1
    assert saved_data["materials"][0]["name"] == "Copper Alloy"
    assert saved_data["materials"][0]["sigma"] == 6.0e7
    assert saved_data["materials"][0]["source"] == "global"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_project_material_color_can_be_edited(monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = MaterialStore()
    original = store.add_project_material("Copper", family="Metals", color="#112233")
    dialog = MaterialLibraryDialog(None, store, lambda: None)
    updated = MaterialRecord(
        original.uid,
        original.name,
        original.family,
        original.er,
        original.ur,
        original.tan_d,
        original.sigma,
        color="#445566",
        opacity=original.opacity,
        source="project",
    )

    class AcceptedEditor:
        result_record = updated

        def __init__(self, *_args):
            pass

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(material_library_dialog, "_MaterialEditorDialog", AcceptedEditor)
    copper_row = next(
        index
        for index in range(dialog._list.count())
        if dialog._list.item(index).text() == "Copper"
    )
    dialog._list.setCurrentRow(copper_row)
    dialog._edit_material()

    assert store.project_records()["Copper"].color == "#445566"
    assert store.project_materials_to_json()[0]["color"] == "#445566"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_builtin_material_can_be_copied_to_project_then_color_edited(monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = MaterialStore()
    dialog = MaterialLibraryDialog(None, store, lambda: None)
    pec_row = next(
        index
        for index in range(dialog._list.count())
        if dialog._list.item(index).text() == "PEC"
    )
    dialog._list.setCurrentRow(pec_row)

    assert dialog._current_record().source == "builtin"
    assert not dialog._edit_btn.isEnabled()
    assert dialog._copy_btn.isEnabled()

    dialog._copy_to_project()
    copied_record = store.get_record("PEC")
    assert copied_record.source == "project"
    assert dialog._edit_btn.isEnabled()
    assert not dialog._copy_btn.isEnabled()

    recolored = MaterialRecord(
        copied_record.uid,
        copied_record.name,
        copied_record.family,
        copied_record.er,
        copied_record.ur,
        copied_record.tan_d,
        copied_record.sigma,
        color="#123456",
        opacity=copied_record.opacity,
        source="project",
    )

    class AcceptedEditor:
        result_record = recolored

        def __init__(self, *_args):
            pass

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(material_library_dialog, "_MaterialEditorDialog", AcceptedEditor)
    dialog._edit_material()

    assert store.get_record("PEC").color == "#123456"
    assert store._builtin["PEC"].color != "#123456"
    assert store.project_materials_to_json()[0]["color"] == "#123456"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()