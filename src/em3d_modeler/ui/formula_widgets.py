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

"""Formula-aware numeric widgets used by editor dialogs."""
from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox


class FormulaDoubleSpinBox(QDoubleSpinBox):
    """A spin box that accepts a project-variable arithmetic expression."""

    def __init__(self, parent=None, resolver=None):
        super().__init__(parent)
        if resolver is None:
            current = parent
            while current is not None:
                resolver = getattr(current, "_resolve_formula_text", None)
                if callable(resolver):
                    break
                current = current.parent() if hasattr(current, "parent") else None
        self._formula_resolver = resolver

    def set_formula_resolver(self, resolver) -> None:
        self._formula_resolver = resolver

    def value(self) -> float:  # type: ignore[override]
        text = self.lineEdit().text().strip()
        special_text = self.specialValueText().strip()
        if special_text and text.casefold() == special_text.casefold():
            return super().value()
        suffix = self.suffix().strip()
        if suffix and text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)].strip()
        try:
            return float(text.replace(",", "."))
        except ValueError:
            if self._formula_resolver is None:
                return super().value()
            return float(self._formula_resolver(text))

    def set_formula(self, text: str) -> None:
        self.lineEdit().setText(str(text))
