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

import math

from PySide6.QtCore import QTimer
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox


def _without_suffix(widget, text: str) -> str:
    value = str(text).strip()
    suffix = widget.suffix().strip()
    if suffix and value.lower().endswith(suffix.lower()):
        value = value[: -len(suffix)].strip()
    return value


def _resolve_value(widget, text: str) -> float:
    raw = _without_suffix(widget, text)
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        resolver = widget._formula_resolver
        if resolver is None:
            raise
        value = float(resolver(raw))
    if not math.isfinite(value):
        raise ValueError("Formula must evaluate to a finite number")
    return value


def _looks_like_formula(text: str) -> bool:
    return any(character.isalpha() or character in "+-*/%^()" for character in text)


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
        self._formula_text = ""
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._restore_formula_text)

    def _on_text_edited(self, text: str) -> None:
        raw = _without_suffix(self, text)
        try:
            float(raw.replace(",", "."))
            self._formula_text = ""
        except ValueError:
            self._formula_text = raw if _looks_like_formula(raw) else ""

    def _restore_formula_text(self) -> None:
        if self._formula_text and self.lineEdit().text() != self._formula_text:
            self.lineEdit().setText(self._formula_text)

    def formula_text(self) -> str:
        return self._formula_text or self.lineEdit().text().strip()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._formula_text:
            QTimer.singleShot(0, self._restore_formula_text)

    def set_formula_resolver(self, resolver) -> None:
        self._formula_resolver = resolver

    def interpretText(self) -> None:
        text = self.lineEdit().text()
        is_formula = _looks_like_formula(_without_suffix(self, text))
        super().interpretText()
        if is_formula:
            self.lineEdit().setText(text)

    def validate(self, text: str, position: int):
        raw = _without_suffix(self, text)
        special_text = self.specialValueText().strip()
        if special_text and raw.casefold() == special_text.casefold():
            return QValidator.Acceptable, text, position
        if not raw:
            return QValidator.Intermediate, text, position
        try:
            value = _resolve_value(self, raw)
        except Exception:
            if self._formula_resolver is not None and _looks_like_formula(raw):
                return QValidator.Intermediate, text, position
            if raw in {"+", "-", ".", ",", "+.", "-."}:
                return QValidator.Intermediate, text, position
            return QValidator.Invalid, text, position
        if self.minimum() <= value <= self.maximum():
            return QValidator.Acceptable, text, position
        return QValidator.Invalid, text, position

    def valueFromText(self, text: str) -> float:
        try:
            return _resolve_value(self, text)
        except Exception:
            return float(self.minimum())

    def value(self) -> float:  # type: ignore[override]
        text = self.lineEdit().text().strip()
        special_text = self.specialValueText().strip()
        if special_text and text.casefold() == special_text.casefold():
            return super().value()
        try:
            value = _resolve_value(self, text)
        except ValueError:
            if self._formula_resolver is None:
                return super().value()
            raise
        if not self.minimum() <= value <= self.maximum():
            raise ValueError(
                f"Value must be between {self.minimum()} and {self.maximum()}"
            )
        return value

    def set_formula(self, text: str) -> None:
        self._formula_text = _without_suffix(self, str(text))
        self.lineEdit().setText(str(text))


class FormulaIntSpinBox(QSpinBox):
    """An integer spin box that accepts formulas resolving to whole numbers."""

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
        self._formula_text = ""
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._restore_formula_text)

    def _on_text_edited(self, text: str) -> None:
        raw = _without_suffix(self, text)
        try:
            float(raw.replace(",", "."))
            self._formula_text = ""
        except ValueError:
            self._formula_text = raw if _looks_like_formula(raw) else ""

    def _restore_formula_text(self) -> None:
        if self._formula_text and self.lineEdit().text() != self._formula_text:
            self.lineEdit().setText(self._formula_text)

    def formula_text(self) -> str:
        return self._formula_text or self.lineEdit().text().strip()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._formula_text:
            QTimer.singleShot(0, self._restore_formula_text)

    def set_formula_resolver(self, resolver) -> None:
        self._formula_resolver = resolver

    def interpretText(self) -> None:
        text = self.lineEdit().text()
        is_formula = _looks_like_formula(_without_suffix(self, text))
        super().interpretText()
        if is_formula:
            self.lineEdit().setText(text)

    def _resolved_integer(self, text: str) -> int:
        value = _resolve_value(self, text)
        rounded = round(value)
        if abs(value - rounded) > 1e-9:
            raise ValueError("Formula for an instance count must evaluate to a whole number")
        result = int(rounded)
        if not self.minimum() <= result <= self.maximum():
            raise ValueError(
                f"Instance count must be between {self.minimum()} and {self.maximum()}"
            )
        return result

    def validate(self, text: str, position: int):
        raw = _without_suffix(self, text)
        special_text = self.specialValueText().strip()
        if special_text and raw.casefold() == special_text.casefold():
            return QValidator.Acceptable, text, position
        if not raw:
            return QValidator.Intermediate, text, position
        try:
            self._resolved_integer(raw)
        except Exception:
            if self._formula_resolver is not None and _looks_like_formula(raw):
                return QValidator.Intermediate, text, position
            if raw in {"+", "-"}:
                return QValidator.Intermediate, text, position
            return QValidator.Invalid, text, position
        return QValidator.Acceptable, text, position

    def valueFromText(self, text: str) -> int:
        try:
            return self._resolved_integer(text)
        except Exception:
            return self.minimum()

    def value(self) -> int:  # type: ignore[override]
        text = self.lineEdit().text().strip()
        special_text = self.specialValueText().strip()
        if special_text and text.casefold() == special_text.casefold():
            return super().value()
        try:
            return self._resolved_integer(text)
        except ValueError:
            if self._formula_resolver is None:
                return super().value()
            raise

    def set_formula(self, text: str) -> None:
        self._formula_text = _without_suffix(self, str(text))
        self.lineEdit().setText(str(text))
