# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

_runtime_packages = [
    "emerge", "emerge_config", "emerge_iron", "emsutil", "OCP", "scipy", "numpy", "matplotlib",
    "pyvista", "vtk", "trame", "gmsh", "shapely", "cupy", "cupy_backends", "cuda", "nvmath",
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
_runtime_hiddenimports.append("graphlib")

_mkl_bin = Path(sys.prefix) / "Library" / "bin"
_mkl_dlls = []
for _pattern in ("mkl_*.dll", "libiomp5md.dll", "tbb*.dll"):
    _mkl_dlls.extend(_mkl_bin.glob(_pattern))
if not any(_dll.name.lower().startswith("mkl_rt") for _dll in _mkl_dlls):
    raise RuntimeError(f"PARDISO runtime mkl_rt*.dll not found in {_mkl_bin}")
_runtime_binaries.extend((str(_dll), "bin") for _dll in _mkl_dlls)

_nvidia_site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
_cuda12_dlls = {
    "cu12/bin": ("cudss64_0.dll", "cudss_mtlayer_vcomp140.dll"),
    "cublas/bin": ("cublas64_12.dll", "cublasLt64_12.dll"),
    "cuda_runtime/bin": ("cudart64_12.dll",),
    "cusparse/bin": ("cusparse64_12.dll",),
    "nvjitlink/bin": ("nvJitLink_120_0.dll",),
}
_missing_cuda12_dlls = []
for _relative_dir, _dll_names in _cuda12_dlls.items():
    for _dll_name in _dll_names:
        _dll_path = _nvidia_site / _relative_dir / _dll_name
        if not _dll_path.is_file():
            _missing_cuda12_dlls.append(str(_dll_path))
        else:
            _runtime_binaries.append((str(_dll_path), "bin"))
if _missing_cuda12_dlls:
    raise RuntimeError("CuDSS CUDA 12 runtime DLLs missing: " + ", ".join(_missing_cuda12_dlls))

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
    name='EM3D_Modeler_1.3.4_Beta',
    icon='D:\\\\Visual_Studio_Code\\\\EM_3D_Modeler\\\\Icons\\\\SplashScreen\\\\EM_Logo.ico',
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
    name='EM3D_Modeler_1.3.4_Beta',
)
