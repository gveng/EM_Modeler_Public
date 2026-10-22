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


def test_legacy_material_json_and_positional_constructor_remain_compatible():
    legacy = {
        "uid": "legacy-id",
        "name": "Legacy",
        "family": "Common",
        "er": 2.5,
        "ur": 1.0,
        "tan_d": 0.0,
        "sigma": 0.0,
    }

    record = MaterialRecord("old-id", "Old", "Common", 2.5, 1.0, 0.0, 0.0)
    loaded = MaterialRecord.from_dict(legacy)

    assert record.er_frequency_data == []
    assert record.ur_frequency_data == []
    assert loaded.er_frequency_data == []
    assert loaded.ur_frequency_data == []
    assert loaded.to_dict()["er_frequency_data"] == []
    assert loaded.to_dict()["ur_frequency_data"] == []


def test_frequency_data_round_trips_project_and_global_json(tmp_path):
    er_data = [
        {"frequency_hz": 1e9, "real": 4.2, "imag": -0.03},
        {"frequency_hz": 2e9, "real": 4.0, "imag": -0.05},
    ]
    ur_data = [{"frequency_hz": 1.5e9, "real": 1.2, "imag": 0.01}]
    store = MaterialStore()
    project = MaterialRecord(
        "freq-id", "Frequency Material", "Dielectrics", 4.0, 1.0, 0.0, 0.0,
        er_frequency_data=er_data,
        ur_frequency_data=ur_data,
    )
    store.upsert_project_record(project)
    project_json = store.project_materials_to_json()
    reloaded_project = MaterialStore()
    reloaded_project.load_project_materials(project_json)
    assert reloaded_project.get_record("Frequency Material").er_frequency_data == er_data
    assert reloaded_project.get_record("Frequency Material").ur_frequency_data == ur_data

    db_path = tmp_path / "frequency-materials.json"
    db_path.write_text(json.dumps({"materials": []}), encoding="utf-8")
    store.set_global_db_path(str(db_path), create_if_missing=False)
    store.upsert_global_record(project)
    reloaded_global = MaterialStore()
    reloaded_global.set_global_db_path(str(db_path), create_if_missing=False)
    saved = reloaded_global.global_records()["Frequency Material"]
    assert saved.er_frequency_data == er_data
    assert saved.ur_frequency_data == ur_data


def test_frequency_data_is_preserved_when_copying_or_appending_materials(tmp_path):
    points = [{"frequency_hz": 1e9, "real": 3.0, "imag": -0.1}]
    store = MaterialStore()
    source = MaterialRecord(
        "frequency-id", "Frequency Material", "Dielectrics", 3.0, 1.0, 0.0, 0.0,
        er_frequency_data=points,
    )
    copied = store.upsert_project_record(source)
    assert copied.er_frequency_data == points

    db_path = tmp_path / "frequency-materials.json"
    db_path.write_text(json.dumps({"materials": [source.to_dict()]}), encoding="utf-8")
    store.set_global_db_path(str(db_path), create_if_missing=False)
    added = store.append_global_to_project(["Frequency Material"])
    assert added == ["Frequency Material"]
    assert store.get_record("Frequency Material").er_frequency_data == points


@pytest.mark.parametrize(
    "points, message",
    [
        ([{"frequency_hz": 0, "real": 1, "imag": 0}], "must be positive"),
        ([{"frequency_hz": float("inf"), "real": 1, "imag": 0}], "must be finite"),
        ([{"frequency_hz": 1, "real": float("nan"), "imag": 0}], "must be finite"),
        (
            [
                {"frequency_hz": 2, "real": 1, "imag": 0},
                {"frequency_hz": 1, "real": 1, "imag": 0},
            ],
            "sorted in strictly increasing",
        ),
        (
            [
                {"frequency_hz": 1, "real": 1, "imag": 0},
                {"frequency_hz": 1, "real": 2, "imag": 0},
            ],
            "sorted in strictly increasing",
        ),
    ],
)
def test_frequency_data_validation_rejects_invalid_points(points, message):
    with pytest.raises(ValueError, match=message):
        MaterialRecord(
            "invalid", "Invalid", "Common", 1.0, 1.0, 0.0, 0.0,
            er_frequency_data=points,
        )
