# -*- mode: python ; coding: utf-8 -*-

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
        print(f"[spec] optional runtime collection skipped for {_package}: {_error}")

_runtime_datas = list(dict.fromkeys(_runtime_datas))
_runtime_binaries = list(dict.fromkeys(_runtime_binaries))
_runtime_hiddenimports = sorted({
    _module for _module in _runtime_hiddenimports
    if ".tests" not in _module and not _module.endswith(".conftest")
})

_gmsh_dll = Path(sys.prefix) / "Lib" / "gmsh-4.14.dll"
if _gmsh_dll.exists():
    _runtime_binaries.append((str(_gmsh_dll), "bin"))


a = Analysis(
    ['D:\\\\Visual_Studio_Code\\\\EM_3D_Modeler\\\\src\\\\em3d_modeler\\\\main.py'],
    pathex=['D:\\\\Visual_Studio_Code\\\\EM_3D_Modeler\\\\src'],
    binaries=_runtime_binaries,
    datas=_runtime_datas + [('D:\\\\Visual_Studio_Code\\\\EM_3D_Modeler\\\\docs', 'docs'), ('D:\\\\Visual_Studio_Code\\\\EM_3D_Modeler\\\\Icons', 'Icons')],
    hiddenimports=_runtime_hiddenimports,
    hookspath=[],
    hooksconfig={},
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
    name='EM3D_Modeler_1.2.6',
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
    name='EM3D_Modeler_1.2.6',
)
