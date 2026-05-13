"""EMERGE material library (default catalog + helpers)."""
from __future__ import annotations

from typing import Dict, List, Tuple

# name -> (EMERGE Type, parameter lines)
EMERGE_MATERIAL_LIBRARY: Dict[str, Tuple[str, str]] = {
    "PEC": ("PEC", ""),
    "PMC": ("PMC", ""),
    "PML": ("PML", ""),
    "Air": ("Dielectric", "  Epsilon_r = 1.0\n  Mu_r = 1.0\n  SigmaE = 0.0"),
    "Vacuum": ("Dielectric", "  Epsilon_r = 1.0\n  Mu_r = 1.0\n  SigmaE = 0.0"),
    "Dielectric": ("Dielectric", "  Epsilon_r = 4.4\n  Mu_r = 1.0\n  SigmaE = 0.0"),
}


def default_material_names() -> List[str]:
    """Return sorted material names from the EMERGE default catalog."""
    return sorted(EMERGE_MATERIAL_LIBRARY.keys())


def material_definition(name: str) -> Tuple[str, str]:
    """Return (type, params) for a material name.

    Unknown materials are exported as generic dielectric placeholders.
    """
    return EMERGE_MATERIAL_LIBRARY.get(
        name,
        ("Dielectric", "  Epsilon_r = 1.0\n  Mu_r = 1.0\n  SigmaE = 0.0"),
    )
