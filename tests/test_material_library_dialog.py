import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from em3d_modeler.emerge.material_store import MaterialRecord, MaterialStore
from em3d_modeler.ui import material_library_dialog
from em3d_modeler.ui.material_library_dialog import (
    MaterialLibraryDialog,
    _FrequencyDataDialog,
    _MaterialEditorDialog,
)


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


def test_frequency_data_dialog_adds_edits_sorts_and_converts_ghz_to_hz(monkeypatch):
    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(
        material_library_dialog.QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    editor = _FrequencyDataDialog(property_name="er")

    editor._add_row_btn.click()
    editor._add_row_btn.click()
    editor._table.item(0, 0).setText("3.5")
    editor._table.item(0, 1).setText("4.1")
    editor._table.item(0, 2).setText("-0.02")
    editor._table.item(1, 0).setText("1.25")
    editor._table.item(1, 1).setText("4.4")
    editor._table.item(1, 2).setText("-0.01")

    editor._accept()

    assert editor.result() == QDialog.Accepted
    assert editor.frequency_data_hz == [
        {"frequency_hz": 1.25e9, "real": 4.4, "imag": -0.01},
        {"frequency_hz": 3.5e9, "real": 4.1, "imag": -0.02},
    ]
    assert not warnings
    editor.deleteLater()
    app.processEvents()


def test_frequency_data_dialog_rejects_duplicate_frequency(monkeypatch):
    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(
        material_library_dialog.QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    editor = _FrequencyDataDialog()
    editor._add_row_btn.click()
    editor._add_row_btn.click()
    editor._table.item(0, 0).setText("2.0")
    editor._table.item(1, 0).setText("2.0")

    editor._accept()

    assert editor.result() == 0
    assert editor.frequency_data_hz is None
    assert warnings == ["Frequencies must be unique."]
    editor._table.selectRow(0)
    editor._remove_row()
    assert editor._table.rowCount() == 1
    editor.deleteLater()
    app.processEvents()


def test_material_editor_preserves_and_edits_frequency_data(monkeypatch):
    app = QApplication.instance() or QApplication([])
    original_er = [{"frequency_hz": 1e9, "real": 3.5, "imag": -0.02}]
    original = MaterialRecord(
        "freq-id", "Frequency Material", "Dielectrics", 3.5, 1.0, 0.0, 0.0,
        er_frequency_data=original_er,
    )

    class AcceptedFrequencyDialog:
        frequency_data_hz = [
            {"frequency_hz": 2e9, "real": 3.0, "imag": -0.03}
        ]

        def __init__(self, *_args):
            pass

        def exec_(self):
            return QDialog.Accepted

    monkeypatch.setattr(
        material_library_dialog, "_FrequencyDataDialog", AcceptedFrequencyDialog
    )
    editor = _MaterialEditorDialog(record=original)
    assert editor._er_frequency_btn.text() == "Edit... (1 point)"
    editor._edit_frequency_data("er")
    editor._accept()

    assert editor.result_record is not None
    assert editor.result_record.er_frequency_data == AcceptedFrequencyDialog.frequency_data_hz
    assert editor.result_record.ur_frequency_data == []
    editor.close()
    editor.deleteLater()
    app.processEvents()