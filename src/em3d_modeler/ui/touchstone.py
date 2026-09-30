"""Read the RI Touchstone files exported by the simulation worker."""

from __future__ import annotations

import math
import re
from pathlib import Path

_TOUCHSTONE_SUFFIX = re.compile(r"\.s(\d+)p$", re.IGNORECASE)
_RUN_TIMESTAMP = re.compile(r"_(\d{8}-\d{6}(?:-\d{6})?)(?:_(?:fit|original))?$", re.IGNORECASE)


def prepare_touchstone_directory(bundle_dir: Path) -> Path:
    """Create the Touchstone folder and move legacy root-level files into it."""
    bundle_dir = Path(bundle_dir)
    touchstone_dir = bundle_dir / "Touchstone"
    touchstone_dir.mkdir(parents=True, exist_ok=True)

    for source in bundle_dir.iterdir():
        if not source.is_file() or not _TOUCHSTONE_SUFFIX.search(source.name):
            continue
        destination = touchstone_dir / source.name
        if destination.exists():
            stem, suffix = source.stem, source.suffix
            duplicate_index = 1
            while destination.exists():
                destination = touchstone_dir / f"{stem}_legacy_{duplicate_index}{suffix}"
                duplicate_index += 1
        try:
            source.replace(destination)
        except OSError:
            continue
    return touchstone_dir


def find_touchstones(
    bundle_dir: Path,
    project_name: str,
    simulation_name: str,
) -> list[Path]:
    """Find exports from the newest run for a simulation."""
    bundle_dir = Path(bundle_dir)
    project_token = _safe_token(project_name).lower()
    simulation_token = _safe_token(simulation_name).lower()
    candidates = []
    try:
        candidates.extend(
            path for path in bundle_dir.rglob("*")
            if path.is_file() and _TOUCHSTONE_SUFFIX.search(path.name)
        )
    except OSError:
        pass

    if not candidates:
        return []

    def _group_key(path: Path) -> str:
        return _TOUCHSTONE_SUFFIX.sub("", path.name).removesuffix("_fit").removesuffix("_original")

    def _best_variant(paths: list[Path]) -> Path:
        fitted = [path for path in paths if path.stem.lower().endswith("_fit")]
        if fitted:
            return fitted[0]
        raw = [path for path in paths if not path.stem.lower().endswith("_original")]
        if raw:
            return raw[0]
        return paths[0]

    grouped: dict[str, list[Path]] = {}
    for path in candidates:
        grouped.setdefault(_group_key(path).lower(), []).append(path)
    run_files = [_best_variant(paths) for paths in grouped.values()]

    def _rank(path: Path, *, specific: bool) -> tuple[str, float, str]:
        timestamp_match = _RUN_TIMESTAMP.search(path.stem)
        timestamp = timestamp_match.group(1) if timestamp_match else ""
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        return timestamp, modified, path.name.lower()

    def _step_rank(path: Path) -> int:
        for parent in path.parents:
            match = re.fullmatch(r"Step_(\d+)(?:_.*)?", parent.name, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 2**31

    specific_prefix = f"{project_token}_{simulation_token}_"
    specific = [path for path in run_files if path.stem.lower().startswith(specific_prefix)]
    if specific:
        newest_timestamp = max(
            (_RUN_TIMESTAMP.search(path.stem).group(1) if _RUN_TIMESTAMP.search(path.stem) else "")
            for path in specific
        )
        newest = [
            path for path in specific
            if (_RUN_TIMESTAMP.search(path.stem).group(1) if _RUN_TIMESTAMP.search(path.stem) else "")
            == newest_timestamp
        ]
        return sorted(
            newest,
            key=lambda path: (_step_rank(path), _rank(path, specific=True)),
        )

    legacy_project_files = [
        path for path in run_files
        if re.fullmatch(
            re.escape(project_token) + r"_\d{8}-\d{6}(?:_(?:fit|original))?",
            path.stem,
            re.IGNORECASE,
        )
    ]
    if legacy_project_files:
        return [max(legacy_project_files, key=lambda path: _rank(path, specific=False))]
    return []


def find_latest_touchstone(
    bundle_dir: Path,
    project_name: str,
    simulation_name: str,
) -> Path | None:
    """Find the newest single Touchstone export for a simulation."""
    paths = find_touchstones(bundle_dir, project_name, simulation_name)
    return paths[-1] if paths else None


def _safe_token(value: str) -> str:
    token = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(value))
    return token.strip("_") or "Project"


def read_touchstone_ri(path: Path) -> dict:
    """Read Touchstone v1 S-parameter files using RI data and standard port ordering."""
    path = Path(path)
    match = _TOUCHSTONE_SUFFIX.search(path.name)
    if match is None:
        raise ValueError(f"Not a Touchstone S-parameter file: {path.name}")
    port_count = int(match.group(1))
    if port_count < 1:
        raise ValueError("Touchstone port count must be positive")

    unit_scale = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
    frequency_unit = "GHZ"
    data_format = "RI"
    records: list[float] = []
    with path.open("r", encoding="ascii", errors="strict") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.split("!", 1)[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                options = line[1:].upper().split()
                if len(options) < 3 or options[0] not in unit_scale:
                    raise ValueError(f"Unsupported Touchstone option line at {line_number}")
                if options[1] != "S" or options[2] not in {"RI", "MA", "DB"}:
                    raise ValueError(f"Only S-parameter RI/MA/DB files are supported (line {line_number})")
                frequency_unit = options[0]
                data_format = options[2]
                continue
            try:
                records.extend(float(token) for token in line.split())
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value in Touchstone line {line_number}") from exc

    values_per_sample = 1 + 2 * port_count * port_count
    if not records or len(records) % values_per_sample:
        raise ValueError(
            f"Incomplete Touchstone sample data: expected groups of {values_per_sample} values"
        )
    if any(not math.isfinite(value) for value in records):
        raise ValueError("Touchstone sample values must be finite")

    frequencies = []
    matrices = []
    for offset in range(0, len(records), values_per_sample):
        sample = records[offset:offset + values_per_sample]
        frequencies.append(sample[0] * unit_scale[frequency_unit])
        matrix = [[0j for _ in range(port_count)] for _ in range(port_count)]
        value_offset = 1
        for input_port in range(port_count):
            for output_port in range(port_count):
                first, second = sample[value_offset:value_offset + 2]
                value_offset += 2
                if data_format == "RI":
                    value = complex(first, second)
                elif data_format == "MA":
                    value = complex(first * math.cos(math.radians(second)), first * math.sin(math.radians(second)))
                else:
                    magnitude = 10.0 ** (first / 20.0)
                    value = complex(magnitude * math.cos(math.radians(second)), magnitude * math.sin(math.radians(second)))
                matrix[output_port][input_port] = value
        matrices.append(matrix)

    return {"frequencies": frequencies, "s_matrices": matrices, "port_count": port_count}
