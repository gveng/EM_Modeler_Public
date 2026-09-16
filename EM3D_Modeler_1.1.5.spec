# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['d:\\Visual_Studio_Code\\EM_3D_Modeler\\src\\em3d_modeler\\main.py'],
    pathex=['d:\\Visual_Studio_Code\\EM_3D_Modeler\\src'],
    binaries=[],
    datas=[('d:\\Visual_Studio_Code\\EM_3D_Modeler\\docs', 'docs'), ('d:\\Visual_Studio_Code\\EM_3D_Modeler\\Icons', 'Icons')],
    hiddenimports=[],
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
    name='EM3D_Modeler_1.1.9_Beta',
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
    name='EM3D_Modeler_1.1.9_Beta',
)
