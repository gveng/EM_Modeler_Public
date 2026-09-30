import pytest

from em3d_modeler.ui.measurement import (
    calculate_measurement,
    measurement_arrow_size,
    normal_for_planar_points,
)


def test_measurement_arrow_size_uses_smallest_nonzero_component():
    assert measurement_arrow_size((1.0, 0.0, -2.0)) == pytest.approx(1.0 / 50.0)
    assert measurement_arrow_size((0.0, -0.25, 0.0)) == pytest.approx(0.25 / 50.0)
    assert measurement_arrow_size((0.0, 0.0, 0.0)) is None


def test_point_distance_reports_euclidean_and_axis_components():
    result = calculate_measurement(
        {"kind": "point", "point": (1, 2, 3)},
        {"kind": "point", "point": (4, 6, 15)},
    )

    assert result["distance"] == pytest.approx(13.0)
    assert result["components"] == pytest.approx((3.0, 4.0, 12.0))


def test_parallel_surfaces_report_separation_along_normal():
    result = calculate_measurement(
        {"kind": "surface", "origin": (0, 0, 2), "normal": (0, 0, 1)},
        {"kind": "surface", "origin": (4, 5, 7), "normal": (0, 0, -1)},
    )

    assert result["parallel"] is True
    assert result["distance"] == pytest.approx(5.0)
    assert result["components"] == pytest.approx((0.0, 0.0, 5.0))


def test_non_parallel_surfaces_report_no_single_normal_distance():
    result = calculate_measurement(
        {"kind": "surface", "origin": (0, 0, 0), "normal": (0, 0, 1)},
        {"kind": "surface", "origin": (0, 0, 3), "normal": (1, 0, 0)},
    )

    assert result == {"kind": "surface-surface", "parallel": False}


def test_point_to_surface_reports_projected_distance_and_foot():
    result = calculate_measurement(
        {"kind": "point", "point": (2, 3, 5)},
        {"kind": "surface", "origin": (0, 0, 1), "normal": (0, 0, 2)},
    )

    assert result["distance"] == pytest.approx(4.0)
    assert result["components"] == pytest.approx((0.0, 0.0, 4.0))
    assert result["projected_point"] == pytest.approx((2.0, 3.0, 1.0))


def test_planar_surface_normal_rejects_non_planar_points():
    normal = normal_for_planar_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    non_planar = normal_for_planar_points([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])

    assert normal == pytest.approx((0.0, 0.0, 1.0))
    assert non_planar is None