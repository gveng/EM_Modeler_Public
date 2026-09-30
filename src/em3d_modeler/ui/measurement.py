"""Distance and projection calculations for viewport measurements."""
from __future__ import annotations

import math
from typing import Any


def measurement_arrow_size(components) -> float | None:
    """Return one fiftieth of the smallest nonzero axis component."""
    nonzero_components = [
        abs(float(value)) for value in components if abs(float(value)) > 1e-12
    ]
    if not nonzero_components:
        return None
    return min(nonzero_components) / 50.0


def normal_for_planar_points(points) -> tuple[float, float, float] | None:
    """Return a unit normal when the supplied world points lie on one plane."""
    values = [tuple(float(value) for value in point) for point in points]
    if len(values) < 3:
        return None

    origin = values[0]
    first_vector = None
    for point in values[1:]:
        vector = tuple(point[index] - origin[index] for index in range(3))
        if math.sqrt(sum(value * value for value in vector)) > 1e-12:
            first_vector = vector
            break
    if first_vector is None:
        return None

    normal = None
    for point in values[1:]:
        vector = tuple(point[index] - origin[index] for index in range(3))
        candidate = (
            first_vector[1] * vector[2] - first_vector[2] * vector[1],
            first_vector[2] * vector[0] - first_vector[0] * vector[2],
            first_vector[0] * vector[1] - first_vector[1] * vector[0],
        )
        magnitude = math.sqrt(sum(value * value for value in candidate))
        if magnitude > 1e-12:
            normal = tuple(value / magnitude for value in candidate)
            break
    if normal is None:
        return None

    diagonal = max(
        math.sqrt(sum((point[index] - origin[index]) ** 2 for index in range(3)))
        for point in values
    )
    tolerance = max(1e-7, diagonal * 1e-6)
    if any(
        abs(sum((point[index] - origin[index]) * normal[index] for index in range(3))) > tolerance
        for point in values
    ):
        return None
    return normal


def calculate_measurement(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Calculate point-point, point-plane, or parallel plane-plane separation."""
    first_kind = first.get("kind")
    second_kind = second.get("kind")

    if first_kind == second_kind == "point":
        first_point = tuple(float(value) for value in first["point"])
        second_point = tuple(float(value) for value in second["point"])
        components = tuple(second_point[index] - first_point[index] for index in range(3))
        return {
            "kind": "point-point",
            "distance": math.sqrt(sum(value * value for value in components)),
            "components": components,
        }

    if first_kind == second_kind == "surface":
        origin_a = tuple(float(value) for value in first["origin"])
        origin_b = tuple(float(value) for value in second["origin"])
        normal_a = _unit_vector(first["normal"])
        normal_b = _unit_vector(second["normal"])
        alignment = abs(sum(normal_a[index] * normal_b[index] for index in range(3)))
        if alignment < 1.0 - 1e-6:
            return {"kind": "surface-surface", "parallel": False}
        signed_distance = sum(
            (origin_b[index] - origin_a[index]) * normal_a[index]
            for index in range(3)
        )
        components = tuple(value * signed_distance for value in normal_a)
        return {
            "kind": "surface-surface",
            "parallel": True,
            "distance": abs(signed_distance),
            "signed_distance": signed_distance,
            "components": components,
        }

    if {first_kind, second_kind} != {"point", "surface"}:
        raise ValueError("Measurement requires two points or at least one planar surface.")

    surface = first if first_kind == "surface" else second
    point_feature = second if first_kind == "surface" else first
    origin = tuple(float(value) for value in surface["origin"])
    point = tuple(float(value) for value in point_feature["point"])
    normal = _unit_vector(surface["normal"])
    signed_distance = sum(
        (point[index] - origin[index]) * normal[index]
        for index in range(3)
    )
    components = tuple(value * signed_distance for value in normal)
    projected_point = tuple(point[index] - components[index] for index in range(3))
    return {
        "kind": "point-surface",
        "distance": abs(signed_distance),
        "signed_distance": signed_distance,
        "components": components,
        "projected_point": projected_point,
    }


def _unit_vector(vector) -> tuple[float, float, float]:
    values = tuple(float(value) for value in vector)
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude <= 1e-12:
        raise ValueError("Surface normal must have non-zero length.")
    return tuple(value / magnitude for value in values)