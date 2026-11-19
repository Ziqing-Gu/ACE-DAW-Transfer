# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, copy_metadata
datas = [('templates', 'templates'), ('assets', 'assets')]
datas += copy_metadata('tkinterdnd2')
binaries = []
hiddenimports = []
for package in ('zstandard', 'cbor2'):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
a = Analysis(['app.py'], pathex=[SPECPATH], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['host_native','host_export','host_ui','frida','ace_bridge','ace_insert_transfer'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ACE-DAW-Transfer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          version='version_info.txt', icon='assets/ace-daw-icon.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[],
               name='ACE-DAW-Transfer-1.4.10')
