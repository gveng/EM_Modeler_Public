"""Shared EMERGE 3D meshing algorithm names and validation."""
from __future__ import annotations

MESH_ALGORITHM_OPTIONS = (
    ("Delaunay", "DELAUNAY"),
    ("Initial mesh only", "INITIAL_MESH_ONLY"),
    ("Frontal", "FRONTAL"),
    ("MMG3D", "MMG3D"),
    ("R-tree", "RTREE"),
    ("HXT", "HXT"),
)

MESH_ALGORITHM_NAMES = frozenset(value for _label, value in MESH_ALGORITHM_OPTIONS)
DEFAULT_MESH_ALGORITHM = "DELAUNAY"


def normalize_mesh_algorithm(value: object) -> str:
    """Return a supported Algorithm3D member name, defaulting safely to Delaunay."""
    name = str(value or "").strip().upper()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name if name in MESH_ALGORITHM_NAMES else DEFAULT_MESH_ALGORITHM
