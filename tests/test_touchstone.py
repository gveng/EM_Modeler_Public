from pathlib import Path

import pytest

from em3d_modeler.ui.chart_data import make_sparameter_plot_data
from em3d_modeler.ui.touchstone import (
    find_latest_touchstone,
    find_touchstones,
    prepare_touchstone_directory,
    read_touchstone_ri,
)


def test_read_touchstone_ri_preserves_two_port_order_and_frequency_units(tmp_path):
    path = tmp_path / "coupler.s2p"
    path.write_text(
        "! generated sample\n# MHZ S RI R 50\n1000 1 2 3 4\n 5 6 7 8 ! wrapped data\n",
        encoding="ascii",
    )

    parsed = read_touchstone_ri(path)
    chart_data = make_sparameter_plot_data(
        {
            "name": path.stem,
            "plot_type": "plot_sp",
            "params": {"s_parameters": ["S11", "S21", "S12", "S22"]},
        },
        parsed["frequencies"],
        parsed["s_matrices"],
    )

    assert chart_data["x_values"] == [1.0]
    assert [series["values"] for series in chart_data["series"]] == [
        [1 + 2j],
        [3 + 4j],
        [5 + 6j],
        [7 + 8j],
    ]


@pytest.mark.parametrize(
    ("option_line", "values", "expected"),
    [
        ("# GHZ S MA R 50", "2 90", 2j),
        ("# GHZ S DB R 50", "0 0", 1 + 0j),
    ],
)
def test_read_touchstone_ri_converts_magnitude_formats(tmp_path, option_line, values, expected):
    path = tmp_path / "one_port.s1p"
    path.write_text(f"{option_line}\n1 {values}\n", encoding="ascii")

    parsed = read_touchstone_ri(path)

    assert parsed["s_matrices"] == [[[pytest.approx(expected)]]]


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("network.s2p", "# HZ S RI R 50\n1 0 0 0 0\n", "Incomplete"),
        ("network.s1p", "# HZ S RI R 50\n1 nan 0\n", "finite"),
        ("network.s1p", "# HZ S RI R 50\n1 0 nope\n", "Invalid numeric"),
        ("network.s1p", "# HZ S Z RI R 50\n1 0 0\n", "Only S-parameter"),
        ("network.ts", "# HZ S RI R 50\n1 0 0\n", "Not a Touchstone"),
    ],
)
def test_read_touchstone_ri_rejects_invalid_files(tmp_path, filename, content, message):
    path = tmp_path / filename
    path.write_text(content, encoding="ascii")

    with pytest.raises(ValueError, match=message):
        read_touchstone_ri(path)


def test_prepare_touchstone_directory_moves_legacy_files_and_preserves_collisions(tmp_path):
    legacy = tmp_path / "old.s2p"
    legacy.write_text("legacy", encoding="ascii")
    duplicate = tmp_path / "same.s1p"
    duplicate.write_text("newer", encoding="ascii")
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    (touchstone_dir / "same.s1p").write_text("existing", encoding="ascii")
    (tmp_path / "run.py").write_text("# keep in root", encoding="ascii")

    prepared = prepare_touchstone_directory(tmp_path)

    assert prepared == touchstone_dir
    assert not legacy.exists()
    assert (touchstone_dir / "old.s2p").read_text(encoding="ascii") == "legacy"
    assert not duplicate.exists()
    assert (touchstone_dir / "same_legacy_1.s1p").read_text(encoding="ascii") == "newer"
    assert (tmp_path / "run.py").exists()


def test_prepare_touchstone_directory_handles_empty_bundle(tmp_path):
    touchstone_dir = prepare_touchstone_directory(Path(tmp_path))

    assert touchstone_dir.is_dir()
    assert list(touchstone_dir.iterdir()) == []


def test_find_latest_touchstone_prefers_simulation_and_fitted_export(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    old_fit = touchstone_dir / "Model_Sweep_20260928-120000_fit.s2p"
    old_original = touchstone_dir / "Model_Sweep_20260928-120000_original.s2p"
    new_other = touchstone_dir / "Model_Parametric_20260929-120000_fit.s2p"
    latest_sweep = touchstone_dir / "Model_Sweep_20260929-110000.s2p"
    for path in (old_fit, old_original, new_other, latest_sweep):
        path.write_text("# GHZ S RI R 50\n1 0 0\n", encoding="ascii")

    assert find_latest_touchstone(tmp_path, "Model", "Sweep") == latest_sweep


def test_find_latest_touchstone_prefers_fit_for_same_run(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    original = touchstone_dir / "Model_Sweep_20260929-120000_original.s2p"
    fitted = touchstone_dir / "Model_Sweep_20260929-120000_fit.s2p"
    original.write_text("# GHZ S RI R 50\n1 0 0\n", encoding="ascii")
    fitted.write_text("# GHZ S RI R 50\n1 0 0\n", encoding="ascii")

    assert find_latest_touchstone(tmp_path, "Model", "Sweep") == fitted


def test_find_latest_touchstone_falls_back_to_legacy_name(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    legacy = touchstone_dir / "Model_20260929-120000_fit.s2p"
    legacy.write_text("# GHZ S RI R 50\n1 0 0\n", encoding="ascii")

    assert find_latest_touchstone(tmp_path, "Model", "Sweep") == legacy


def test_find_latest_touchstone_does_not_select_another_simulation(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    other_job = touchstone_dir / "Model_Parametric_20260929-120000_fit.s2p"
    other_job.write_text("# GHZ S RI R 50\n1 0 0\n", encoding="ascii")

    assert find_latest_touchstone(tmp_path, "Model", "Sweep") is None


def test_find_touchstones_returns_all_files_from_newest_simulation_run(tmp_path):
    touchstone_dir = tmp_path / "Touchstone"
    touchstone_dir.mkdir()
    old = touchstone_dir / "Model_Parametric_width_0_2_20260928-120000.s1p"
    first = touchstone_dir / "Model_Parametric_width_0_2_20260929-120000.s1p"
    second = touchstone_dir / "Model_Parametric_width_0_4_20260929-120000.s1p"
    for path in (old, first, second):
        path.write_text("# GHZ S RI R 50\n1 0.1 0\n", encoding="ascii")

    assert find_touchstones(tmp_path, "Model", "Parametric") == [first, second]


def test_find_touchstones_collects_nested_parametric_steps_from_one_run(tmp_path):
    old_folder = tmp_path / "Model_Parametric" / "Step_001_old" / "Touchstone"
    first_folder = tmp_path / "Model_Parametric" / "Step_001_width_0_2" / "Touchstone"
    second_folder = tmp_path / "Model_Parametric" / "Step_002_width_0_4" / "Touchstone"
    for folder in (old_folder, first_folder, second_folder):
        folder.mkdir(parents=True)
    old = old_folder / "Model_Parametric_width_0_2_20260928-120000.s1p"
    first = first_folder / "Model_Parametric_width_0_2_20260929-120000.s1p"
    second = second_folder / "Model_Parametric_width_0_4_20260929-120000.s1p"
    for path in (old, first, second):
        path.write_text("# GHZ S RI R 50\n1 0.1 0\n", encoding="ascii")

    assert find_touchstones(tmp_path, "Model", "Parametric") == [first, second]
