# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
ffmpeg_dir = root / 'ffmpeg'
suffix = '.exe' if sys.platform == 'win32' else ''
ffmpeg_tools = [ffmpeg_dir / f'{name}{suffix}' for name in ('ffmpeg', 'ffprobe')]
if any(tool.exists() for tool in ffmpeg_tools) and not all(tool.is_file() for tool in ffmpeg_tools):
    raise SystemExit('Put both native FFmpeg and FFprobe executables in ffmpeg/, or leave it empty.')
binaries = [(str(tool), 'ffmpeg') for tool in ffmpeg_tools if tool.is_file()]
node = root / 'runtime' / f'node{suffix}'
if node.is_file():
    binaries.append((str(node), 'runtime'))

datas = [(str(root / 'GrabIt.ico'), '.')]
datas += collect_data_files('customtkinter')


a = Analysis(
    [str(root / 'main.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GrabIt', console=False, upx=False)
    collected = COLLECT(exe, a.binaries, a.datas, name='GrabIt', upx=False)
    app = BUNDLE(collected, name='GrabIt.app', icon=str(root / 'GrabIt.ico'),
                 bundle_identifier='com.coderkeshav.grabit')
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name='GrabIt', icon=str(root / 'GrabIt.ico') if sys.platform == 'win32' else None,
        console=False, upx=False,
    )
