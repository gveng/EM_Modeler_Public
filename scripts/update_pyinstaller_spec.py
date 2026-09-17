from __future__ import annotations

from pathlib import Path
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 fallback
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def version_label(raw: str) -> str:
    raw = raw.strip()
    if "b" in raw:
        base = raw.split("b", 1)[0]
        return f"{base}_Beta"
    return raw


def bundle_name(version: str) -> str:
    label = version_label(version)
    return f"EM3D_Modeler_{label}"


def read_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def esc_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\")


def main() -> None:
    version = read_version()
    name = bundle_name(version)
    SPEC_PATH = ROOT / f"{name}.spec"
    spec = f'''# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

_runtime_packages = [
    "emerge", "emsutil", "scipy", "numpy", "matplotlib",
    "pyvista", "vtk", "trame", "gmsh",
]
_runtime_datas = []
_runtime_binaries = []
_runtime_hiddenimports = []
for _package in _runtime_packages:
    try:
        _datas, _binaries, _hiddenimports = collect_all(_package)
        _runtime_datas.extend(_datas)
        _runtime_binaries.extend(_binaries)
        _runtime_hiddenimports.extend(_hiddenimports)
        _runtime_hiddenimports.extend(collect_submodules(_package))
    except Exception as _error:
        print(f"[spec] optional runtime collection skipped for {{_package}}: {{_error}}")

_runtime_datas = list(dict.fromkeys(_runtime_datas))
_runtime_binaries = list(dict.fromkeys(_runtime_binaries))
_runtime_hiddenimports = sorted(set(_runtime_hiddenimports))

_gmsh_dll = Path(sys.prefix) / "Lib" / "gmsh-4.14.dll"
if _gmsh_dll.exists():
    _runtime_binaries.append((str(_gmsh_dll), "bin"))


a = Analysis(
    [{esc_path(ROOT / 'src' / 'em3d_modeler' / 'main.py')!r}],
    pathex=[{esc_path(ROOT / 'src')!r}],
    binaries=_runtime_binaries,
    datas=_runtime_datas + [({esc_path(ROOT / 'docs')!r}, 'docs'), ({esc_path(ROOT / 'Icons')!r}, 'Icons')],
    hiddenimports=_runtime_hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name={name!r},
)
'''
    SPEC_PATH.write_text(spec, encoding="utf-8")
    print(f"Updated {SPEC_PATH.name} -> {name}")


if __name__ == "__main__":
    main()
