# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets/characters', 'assets/characters'), ('assets/big_blue_fat_fish', 'assets/big_blue_fat_fish'), ('pet/menu_templates', 'pet/menu_templates')]
binaries = [('D:/python/miniconda/envs/py12/Library/bin/sqlite3.dll', '.'), ('D:/python/miniconda/envs/py12/Library/bin/ffi.dll', '.'), ('D:/python/miniconda/envs/py12/Library/bin/liblzma.dll', '.'), ('D:/python/miniconda/envs/py12/Library/bin/libbz2.dll', '.'), ('D:/python/miniconda/envs/py12/Library/bin/libexpat.dll', '.'), ('D:/python/miniconda/envs/py12/msvcp140_1.dll', '.')]
hiddenimports = []
tmp_ret = collect_all('imageio_ffmpeg')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('certifi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['packaging/pet_entry.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='desktop-pet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='desktop-pet',
)
