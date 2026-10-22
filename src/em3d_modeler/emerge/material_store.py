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

"""Material storage and catalog utilities.

Provides three sources:
- Builtin library (from emsutil.lib, if available)
- Project-local custom database (saved in .em3d file)
- Optional external global database (JSON path persisted in application settings)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from numbers import Real
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import uuid


DEFAULT_COLOR = "#bebebe"


def _normalize_frequency_data(value: Any, property_name: str) -> List[Dict[str, float]]:
    """Validate and copy frequency points, requiring strictly increasing frequencies."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{property_name} frequency data must be a list")

    normalized: List[Dict[str, float]] = []
    previous_frequency = 0.0
    for index, point in enumerate(value):
        if not isinstance(point, dict):
            raise ValueError(f"{property_name} frequency point {index + 1} must be an object")

        converted: Dict[str, float] = {}
        for key in ("frequency_hz", "real", "imag"):
            component = point.get(key)
            if isinstance(component, bool) or not isinstance(component, Real):
                raise ValueError(
                    f"{property_name} frequency point {index + 1} {key} must be numeric"
                )
            component = float(component)
            if not math.isfinite(component):
                raise ValueError(
                    f"{property_name} frequency point {index + 1} {key} must be finite"
                )
            converted[key] = component

        frequency = converted["frequency_hz"]
        if frequency <= 0:
            raise ValueError(
                f"{property_name} frequency point {index + 1} frequency_hz must be positive"
            )
        if frequency <= previous_frequency:
            raise ValueError(
                f"{property_name} frequencies must be sorted in strictly increasing order"
            )
        previous_frequency = frequency
        normalized.append(converted)

    return normalized


@dataclass
class MaterialRecord:
    uid: str
    name: str
    family: str
    er: float
    ur: float
    tan_d: float
    sigma: float
    color: str = DEFAULT_COLOR
    opacity: float = 0.85
    source: str = "project"  # builtin | project | global
    priority: int = 0  # priority level for material (higher = higher priority)
    er_frequency_data: List[Dict[str, float]] = field(default_factory=list)
    ur_frequency_data: List[Dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.er_frequency_data = _normalize_frequency_data(
            self.er_frequency_data, "er"
        )
        self.ur_frequency_data = _normalize_frequency_data(
            self.ur_frequency_data, "ur"
        )

    def to_dict(self) -> Dict[str, Any]:
        er_frequency_data = _normalize_frequency_data(self.er_frequency_data, "er")
        ur_frequency_data = _normalize_frequency_data(self.ur_frequency_data, "ur")
        return {
            "uid": self.uid,
            "name": self.name,
            "family": self.family,
            "er": self.er,
            "ur": self.ur,
            "tan_d": self.tan_d,
            "sigma": self.sigma,
            "color": self.color,
            "opacity": self.opacity,
            "source": self.source,
            "priority": self.priority,
            "er_frequency_data": er_frequency_data,
            "ur_frequency_data": ur_frequency_data,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any], source_fallback: str = "project") -> "MaterialRecord":
        return MaterialRecord(
            uid=str(data.get("uid") or uuid.uuid4()),
            name=str(data.get("name") or "Unnamed").strip() or "Unnamed",
            family=str(data.get("family") or "Common").strip() or "Common",
            er=float(data.get("er", 1.0)),
            ur=float(data.get("ur", 1.0)),
            tan_d=float(data.get("tan_d", 0.0)),
            sigma=float(data.get("sigma", 0.0)),
            color=str(data.get("color") or DEFAULT_COLOR),
            opacity=float(data.get("opacity", 0.85)),
            source=str(data.get("source") or source_fallback),
            priority=int(data.get("priority", 0)),
            er_frequency_data=data.get("er_frequency_data", []),
            ur_frequency_data=data.get("ur_frequency_data", []),
        )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _family_from_name(name: str) -> str:
    up = name.upper()
    if up.startswith("DIEL_"):
        return "Dielectrics"
    if up.startswith("MET_") or up in {"PEC", "COPPER", "ALUMINUM", "GOLD", "SILVER"}:
        return "Metals"
    if up.startswith("FOAM_"):
        return "Foams"
    if up.startswith("SEMI_"):
        return "Semiconductors"
    if up.startswith("LIQ_"):
        return "Liquids"
    return "Common"


def _extract_scalar_property(prop: Any, default: float) -> float:
    """Extract a representative scalar from emsutil properties.

    emsutil MatProperty commonly exposes a scalar in ``value``.
    If unavailable, try callable forms and finally cast directly.
    """
    if hasattr(prop, "value"):
        return _safe_float(getattr(prop, "value"), default)

    if callable(prop):
        try:
            return _safe_float(prop(10e9), default)
        except Exception:
            try:
                return _safe_float(prop({"f": 10e9}), default)
            except Exception:
                return default
    return _safe_float(prop, default)


class MaterialStore:
    VERSION = "1.0"

    def __init__(self) -> None:
        self._builtin: Dict[str, MaterialRecord] = self._load_builtin_library()
        self._project: Dict[str, MaterialRecord] = {}
        self._global: Dict[str, MaterialRecord] = {}
        self.global_db_path: Optional[str] = None

    # -------------------------- load/save
    def load_project_materials(self, records: Iterable[Dict[str, Any]] | None) -> None:
        self._project = {}
        for raw in records or []:
            if not isinstance(raw, dict):
                continue
            rec = MaterialRecord.from_dict(raw, source_fallback="project")
            rec.source = "project"
            self._project[rec.name] = rec

    def project_materials_to_json(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in sorted(self._project.values(), key=lambda x: x.name.lower())]

    def set_global_db_path(self, path: Optional[str], create_if_missing: bool = True) -> None:
        selected_path = path or None
        if not selected_path:
            self.global_db_path = None
            self._global = {}
            return

        db_path = Path(selected_path)
        previous_path = self.global_db_path
        previous_records = self._global
        self.global_db_path = str(db_path)
        if not db_path.exists():
            if create_if_missing:
                try:
                    self._write_global_db([])
                except Exception:
                    self.global_db_path = previous_path
                    self._global = previous_records
                    raise
            else:
                self._global = {}
                return

        try:
            self._read_global_db()
        except Exception:
            self.global_db_path = previous_path
            self._global = previous_records
            raise

    def save_global_db(self) -> None:
        if not self.global_db_path:
            return
        self._write_global_db([r.to_dict() for r in self._global.values()])

    # -------------------------- add/append operations
    def add_project_material(
        self,
        name: str,
        family: str = "Common",
        er: float = 1.0,
        ur: float = 1.0,
        tan_d: float = 0.0,
        sigma: float = 0.0,
        color: str = DEFAULT_COLOR,
        opacity: float = 0.85,
        er_frequency_data: List[Dict[str, float]] | None = None,
        ur_frequency_data: List[Dict[str, float]] | None = None,
    ) -> MaterialRecord:
        rec = MaterialRecord(
            uid=str(uuid.uuid4()),
            name=name.strip(),
            family=family.strip() or "Common",
            er=float(er),
            ur=float(ur),
            tan_d=float(tan_d),
            sigma=float(sigma),
            color=color or DEFAULT_COLOR,
            opacity=float(opacity),
            source="project",
            er_frequency_data=[] if er_frequency_data is None else er_frequency_data,
            ur_frequency_data=[] if ur_frequency_data is None else ur_frequency_data,
        )
        self._project[rec.name] = rec
        return rec

    def add_global_material(
        self,
        name: str,
        family: str = "Common",
        er: float = 1.0,
        ur: float = 1.0,
        tan_d: float = 0.0,
        sigma: float = 0.0,
        color: str = DEFAULT_COLOR,
        opacity: float = 0.85,
        er_frequency_data: List[Dict[str, float]] | None = None,
        ur_frequency_data: List[Dict[str, float]] | None = None,
    ) -> MaterialRecord:
        rec = MaterialRecord(
            uid=str(uuid.uuid4()),
            name=name.strip(),
            family=family.strip() or "Common",
            er=float(er),
            ur=float(ur),
            tan_d=float(tan_d),
            sigma=float(sigma),
            color=color or DEFAULT_COLOR,
            opacity=float(opacity),
            source="global",
            er_frequency_data=[] if er_frequency_data is None else er_frequency_data,
            ur_frequency_data=[] if ur_frequency_data is None else ur_frequency_data,
        )
        return self.upsert_global_record(rec)

    def upsert_project_record(self, record: MaterialRecord) -> MaterialRecord:
        rec = MaterialRecord(
            uid=str(record.uid or uuid.uuid4()),
            name=str(record.name).strip(),
            family=str(record.family or "Common").strip() or "Common",
            er=float(record.er),
            ur=float(record.ur),
            tan_d=float(record.tan_d),
            sigma=float(record.sigma),
            color=str(record.color or DEFAULT_COLOR),
            opacity=float(record.opacity),
            source="project",
            priority=int(record.priority),
            er_frequency_data=record.er_frequency_data,
            ur_frequency_data=record.ur_frequency_data,
        )
        self._project[rec.name] = rec
        return rec

    def copy_builtin_to_project(self, name: str) -> MaterialRecord:
        key = str(name).strip()
        record = self._builtin.get(key)
        if record is None:
            raise ValueError(f"Builtin material '{key}' does not exist")
        if key in self._project:
            raise ValueError(f"Project material '{key}' already exists")
        return self.upsert_project_record(
            MaterialRecord(
                uid=str(uuid.uuid4()),
                name=record.name,
                family=record.family,
                er=record.er,
                ur=record.ur,
                tan_d=record.tan_d,
                sigma=record.sigma,
                color=record.color,
                opacity=record.opacity,
                source="project",
                priority=record.priority,
                er_frequency_data=record.er_frequency_data,
                ur_frequency_data=record.ur_frequency_data,
            )
        )

    def upsert_global_record(self, record: MaterialRecord) -> MaterialRecord:
        if not self.global_db_path:
            raise ValueError("No global material database is configured")
        rec = MaterialRecord(
            uid=str(record.uid or uuid.uuid4()),
            name=str(record.name).strip(),
            family=str(record.family or "Common").strip() or "Common",
            er=float(record.er),
            ur=float(record.ur),
            tan_d=float(record.tan_d),
            sigma=float(record.sigma),
            color=str(record.color or DEFAULT_COLOR),
            opacity=float(record.opacity),
            source="global",
            priority=int(record.priority),
            er_frequency_data=record.er_frequency_data,
            ur_frequency_data=record.ur_frequency_data,
        )
        previous_records = self._global
        self._global = {
            name: existing
            for name, existing in self._global.items()
            if existing.uid != rec.uid and name != rec.name
        }
        self._global[rec.name] = rec
        try:
            self.save_global_db()
        except Exception:
            self._global = previous_records
            raise
        return rec

    def delete_project_record(self, name: str) -> bool:
        key = str(name).strip()
        if not key or key not in self._project:
            return False
        del self._project[key]
        return True

    def append_global_to_project(self, names: Iterable[str]) -> List[str]:
        added: List[str] = []
        for name in names:
            rec = self._global.get(name)
            if rec is None:
                continue
            cloned = MaterialRecord(
                uid=str(uuid.uuid4()),
                name=rec.name,
                family=rec.family,
                er=rec.er,
                ur=rec.ur,
                tan_d=rec.tan_d,
                sigma=rec.sigma,
                color=rec.color,
                opacity=rec.opacity,
                source="project",
                priority=rec.priority,
                er_frequency_data=rec.er_frequency_data,
                ur_frequency_data=rec.ur_frequency_data,
            )
            self._project[cloned.name] = cloned
            added.append(cloned.name)
        return added

    # -------------------------- queries
    def all_project_names(self) -> List[str]:
        names = set(self._builtin.keys()) | set(self._project.keys())
        return sorted(names)

    def project_records(self) -> Dict[str, MaterialRecord]:
        return {**self._builtin, **self._project}

    def global_records(self) -> Dict[str, MaterialRecord]:
        return dict(self._global)

    def get_record(self, name: str, source: str = "project") -> Optional[MaterialRecord]:
        if source == "global":
            return self._global.get(name)
        # project view includes builtin + project custom
        return self._project.get(name) or self._builtin.get(name)

    def material_export_catalog(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for name, rec in self.project_records().items():
            out[name] = rec.to_dict()
        return out

    # -------------------------- internals
    def _read_global_db(self) -> None:
        assert self.global_db_path
        path = Path(self.global_db_path)
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        records = raw.get("materials", []) if isinstance(raw, dict) else []
        loaded_records: Dict[str, MaterialRecord] = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            rec = MaterialRecord.from_dict(item, source_fallback="global")
            rec.source = "global"
            loaded_records[rec.name] = rec
        self._global = loaded_records

    def _write_global_db(self, materials: List[Dict[str, Any]]) -> None:
        assert self.global_db_path
        data = {
            "version": self.VERSION,
            "materials": materials,
        }
        path = Path(self.global_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_builtin_library(self) -> Dict[str, MaterialRecord]:
        records: Dict[str, MaterialRecord] = {}

        # Minimal fallback set if emsutil is unavailable.
        fallback = {
            "PEC": MaterialRecord(str(uuid.uuid4()), "PEC", "Metals", 1.0, 1.0, 0.0, 1e9, "#bebee6", 0.9, "builtin"),
            "PMC": MaterialRecord(str(uuid.uuid4()), "PMC", "Common", 1.0, 1.0, 0.0, 0.0, "#d0d0d0", 0.9, "builtin"),
            "PML": MaterialRecord(str(uuid.uuid4()), "PML", "Common", 1.0, 1.0, 0.0, 0.0, "#dfdf9b", 0.6, "builtin"),
            "Air": MaterialRecord(str(uuid.uuid4()), "Air", "Common", 1.0, 1.0, 0.0, 0.0, "#b4d8ff", 0.2, "builtin"),
        }

        try:
            import emsutil.lib as lib  # type: ignore
            from emsutil.material import Material as EmsMaterial  # type: ignore

            for attr in dir(lib):
                if attr.startswith("_") or not attr.isupper():
                    continue
                value = getattr(lib, attr)
                if not isinstance(value, EmsMaterial):
                    continue

                # Keep the canonical library symbol name (e.g. DIEL_AD10)
                # so it matches expected material naming in UI and scripts.
                name = attr
                records[name] = MaterialRecord(
                    uid=str(uuid.uuid4()),
                    name=name,
                    family=_family_from_name(attr),
                    er=_extract_scalar_property(getattr(value, "er", 1.0), 1.0),
                    ur=_extract_scalar_property(getattr(value, "ur", 1.0), 1.0),
                    tan_d=_extract_scalar_property(getattr(value, "tand", 0.0), 0.0),
                    sigma=_extract_scalar_property(getattr(value, "cond", 0.0), 0.0),
                    color=str(getattr(value, "color", DEFAULT_COLOR) or DEFAULT_COLOR),
                    opacity=_safe_float(getattr(value, "opacity", 0.85), 0.85),
                    source="builtin",
                )

            # Ensure common aliases expected by existing app code.
            if "PEC" not in records:
                records["PEC"] = fallback["PEC"]
            if "Air" not in records and "AIR" in records:
                air = records["AIR"]
                records["Air"] = MaterialRecord(
                    uid=str(uuid.uuid4()), name="Air", family=air.family,
                    er=air.er, ur=air.ur, tan_d=air.tan_d, sigma=air.sigma,
                    color=air.color, opacity=air.opacity, source="builtin",
                )
        except Exception:
            records.update(fallback)

        if not records:
            records.update(fallback)
        return records
