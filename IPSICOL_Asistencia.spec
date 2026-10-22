# -*- mode: python ; coding: utf-8 -*-
# Compilar en Windows para generar Windows .exe. La base NO se empaqueta.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
datas = [(str(root / 'assets'), 'assets')]
for package in ('customtkinter', 'tkcalendar', 'babel', 'tzdata'):
    datas += collect_data_files(package)

analysis = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=['babel.numbers'] + collect_submodules('tkcalendar'),
    hookspath=[], runtime_hooks=[], excludes=['pytest'], noarchive=False,
)
archive = PYZ(analysis.pure)
# Incluir binarios y datos en EXE, sin COLLECT, produce un único archivo.
exe = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name='IPSICOL_Asistencia', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / 'assets' / 'ipsicol.ico'),
)
