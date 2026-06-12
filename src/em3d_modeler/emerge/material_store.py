"""Material storage and catalog utilities.

Provides three sources:
- Builtin library (from emsutil.lib, if available)
- Project-local custom database (saved in .em3d file)
- Optional external global database (JSON path persisted in project)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import uuid


DEFAULT_COLOR = "#bebebe"


@dataclass
class MaterialRecord:
    uid: str
    name: str
    family: str
    er: float
    tan_d: float
    sigma: float
    color: str = DEFAULT_COLOR
    opacity: float = 0.85
    source: str = "project"  # builtin | project | global

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "name": self.name,
            "family": self.family,
            "er": self.er,
            "tan_d": self.tan_d,
            "sigma": self.sigma,
            "color": self.color,
            "opacity": self.opacity,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any], source_fallback: str = "project") -> "MaterialRecord":
        return MaterialRecord(
            uid=str(data.get("uid") or uuid.uuid4()),
            name=str(data.get("name") or "Unnamed").strip() or "Unnamed",
            family=str(data.get("family") or "Common").strip() or "Common",
            er=float(data.get("er", 1.0)),
            tan_d=float(data.get("tan_d", 0.0)),
            sigma=float(data.get("sigma", 0.0)),
            color=str(data.get("color") or DEFAULT_COLOR),
            opacity=float(data.get("opacity", 0.85)),
            source=str(data.get("source") or source_fallback),
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
        self.global_db_path = path or None
        self._global = {}
        if not self.global_db_path:
            return

        db_path = Path(self.global_db_path)
        if not db_path.exists():
            if create_if_missing:
                self._write_global_db([])
            else:
                return

        self._read_global_db()

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
        tan_d: float = 0.0,
        sigma: float = 0.0,
        color: str = DEFAULT_COLOR,
        opacity: float = 0.85,
    ) -> MaterialRecord:
        rec = MaterialRecord(
            uid=str(uuid.uuid4()),
            name=name.strip(),
            family=family.strip() or "Common",
            er=float(er),
            tan_d=float(tan_d),
            sigma=float(sigma),
            color=color or DEFAULT_COLOR,
            opacity=float(opacity),
            source="project",
        )
        self._project[rec.name] = rec
        return rec

    def upsert_project_record(self, record: MaterialRecord) -> MaterialRecord:
        rec = MaterialRecord(
            uid=str(record.uid or uuid.uuid4()),
            name=str(record.name).strip(),
            family=str(record.family or "Common").strip() or "Common",
            er=float(record.er),
            tan_d=float(record.tan_d),
            sigma=float(record.sigma),
            color=str(record.color or DEFAULT_COLOR),
            opacity=float(record.opacity),
            source="project",
        )
        self._project[rec.name] = rec
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
                tan_d=rec.tan_d,
                sigma=rec.sigma,
                color=rec.color,
                opacity=rec.opacity,
                source="project",
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
        self._global = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            rec = MaterialRecord.from_dict(item, source_fallback="global")
            rec.source = "global"
            self._global[rec.name] = rec

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
            "PEC": MaterialRecord(str(uuid.uuid4()), "PEC", "Metals", 1.0, 0.0, 1e9, "#bebee6", 0.9, "builtin"),
            "PMC": MaterialRecord(str(uuid.uuid4()), "PMC", "Common", 1.0, 0.0, 0.0, "#d0d0d0", 0.9, "builtin"),
            "PML": MaterialRecord(str(uuid.uuid4()), "PML", "Common", 1.0, 0.0, 0.0, "#dfdf9b", 0.6, "builtin"),
            "Air": MaterialRecord(str(uuid.uuid4()), "Air", "Common", 1.0, 0.0, 0.0, "#b4d8ff", 0.2, "builtin"),
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
                    er=air.er, tan_d=air.tan_d, sigma=air.sigma,
                    color=air.color, opacity=air.opacity, source="builtin",
                )
        except Exception:
            records.update(fallback)

        if not records:
            records.update(fallback)
        return records
