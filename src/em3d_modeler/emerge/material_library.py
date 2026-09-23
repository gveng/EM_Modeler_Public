# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

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
