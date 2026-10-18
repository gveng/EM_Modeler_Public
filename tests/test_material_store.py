import json

import pytest

from em3d_modeler.emerge.material_store import MaterialRecord, MaterialStore


def test_global_db_switch_preserves_current_database_on_invalid_json(tmp_path):
    current_db = tmp_path / "current.json"
    current_db.write_text(
        json.dumps({"materials": [{"name": "Copper", "family": "Metals"}]}),
        encoding="utf-8",
    )
    invalid_db = tmp_path / "invalid.json"
    invalid_db.write_text("{invalid json", encoding="utf-8")

    store = MaterialStore()
    store.set_global_db_path(str(current_db), create_if_missing=False)
    original_records = store.global_records()

    with pytest.raises(json.JSONDecodeError):
        store.set_global_db_path(str(invalid_db), create_if_missing=False)

    assert store.global_db_path == str(current_db)
    assert store.global_records() == original_records


def test_upsert_global_record_persists_material_immediately(tmp_path):
    db_path = tmp_path / "materials.json"
    db_path.write_text(json.dumps({"materials": []}), encoding="utf-8")
    store = MaterialStore()
    store.set_global_db_path(str(db_path), create_if_missing=False)

    saved = store.upsert_global_record(
        MaterialRecord("test-id", "Copper", "Metals", 1.0, 1.0, 0.0, 5.8e7)
    )

    reloaded = MaterialStore()
    reloaded.set_global_db_path(str(db_path), create_if_missing=False)
    assert saved.source == "global"
    assert reloaded.global_records()["Copper"].sigma == 5.8e7


def test_upsert_global_record_requires_configured_database():
    store = MaterialStore()
    record = MaterialRecord("test-id", "Copper", "Metals", 1.0, 1.0, 0.0, 5.8e7)

    with pytest.raises(ValueError, match="No global material database"):
        store.upsert_global_record(record)


def test_add_global_material_uses_defaults_and_persists(tmp_path):
    db_path = tmp_path / "materials.json"
    db_path.write_text(json.dumps({"materials": []}), encoding="utf-8")
    store = MaterialStore()
    store.set_global_db_path(str(db_path), create_if_missing=False)

    record = store.add_global_material("New dielectric")

    reloaded = MaterialStore()
    reloaded.set_global_db_path(str(db_path), create_if_missing=False)
    assert record.source == "global"
    assert record.family == "Common"
    assert "New dielectric" in reloaded.global_records()


def test_copy_builtin_material_to_project_preserves_builtin_and_copies_properties():
    store = MaterialStore()
    builtin = store.get_record("PEC")

    copied = store.copy_builtin_to_project("PEC")

    assert copied.source == "project"
    assert copied.name == builtin.name
    assert copied.uid != builtin.uid
    assert copied.color == builtin.color
    assert copied.er == builtin.er
    assert store._builtin["PEC"] is builtin
    assert store.project_materials_to_json()[0]["name"] == "PEC"


def test_copy_builtin_material_rejects_missing_or_existing_project_record():
    store = MaterialStore()
    with pytest.raises(ValueError, match="does not exist"):
        store.copy_builtin_to_project("Missing")

    store.add_project_material("PEC")
    with pytest.raises(ValueError, match="already exists"):
        store.copy_builtin_to_project("PEC")
