# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

root = Path(SPECPATH)
if sys.platform == "darwin":
    icon = str(root / "resources" / "app_icon.icns")
elif sys.platform.startswith("win"):
    icon = str(root / "resources" / "app_icon.ico")
else:
    icon = str(root / "resources" / "app_icon.png")

version_file = str(root / "packaging" / "windows" / "version_info.txt") if sys.platform.startswith("win") else None

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "resources"), "resources")],
    hiddenimports=["numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WGMapBackporterStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
    version=version_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WGMapBackporterStudio",
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="WG Map Backporter Studio.app",
        icon=icon,
        bundle_identifier="io.github.rhyshopkins04.wg-map-backporter-studio",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "WG Map Backporter Studio",
            "CFBundleName": "WG Map Backporter Studio",
            "CFBundleShortVersionString": "0.4.1",
            "CFBundleVersion": "0.4.1",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
        },
    )
