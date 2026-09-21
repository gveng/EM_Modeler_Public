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
