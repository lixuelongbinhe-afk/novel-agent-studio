# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH)
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
hiddenimports = sorted(
    set(
        collect_submodules("app")
        + collect_submodules("uvicorn")
        + collect_submodules("alembic")
        + collect_submodules("webview")
        + collect_submodules("pystray")
        + [
            "sqlalchemy.dialects.sqlite",
            "tiktoken_ext.openai_public",
            "tiktoken_ext",
        ]
    )
)
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend-dist"),
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "alembic.ini"), "."),
] + collect_data_files("tiktoken") + collect_data_files("certifi") + collect_data_files("reportlab")

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff", "tkinter", "IPython", "notebook"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NovelAgentStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(ROOT / "build" / "windows-version.txt"),
)
console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NovelAgentStudioConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    version=str(ROOT / "build" / "windows-version.txt"),
)
coll = COLLECT(
    gui,
    console,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NovelAgentStudio",
)
