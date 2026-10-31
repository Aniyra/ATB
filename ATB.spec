# -*- mode: python ; coding: utf-8 -*-
#
# ATB PyInstaller 配置 — 单文件、控制台程序
#
# 关键设计：
#   1. onefile + console=True：保留 CLI 交互与 MCP stdio 传输；
#   2. 解析库用 collect_submodules 按需收集，缺失的包自动跳过（不中断构建）；
#   3. excludes 掉 GUI / 数据科学全家桶，避免无谓膨胀；
#   4. optimize=0：main.py 的模块 docstring 被 --help 使用，不能被 -O 剥离；
#   5. upx=False：稳定性优先，降低杀软误报。
#
# 构建：python -m PyInstaller ATB.spec --noconfirm --clean
# 产物：dist/ATB.exe

import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
    StringStruct, VarFileInfo, VarStruct,
)

# ── 应用元信息（与 config.py 保持一致）──────────────────────
APP_NAME = "ATB (All Tool Box)"
APP_VERSION = "1.0.0.0"
APP_AUTHOR = "LFTAniyra"
APP_COPYRIGHT = "Copyright (c) 2026 LFTAniyra"
ICON_FILE = "atb.ico"

if not os.path.isfile(ICON_FILE):
    raise SystemExit("缺少 atb.ico，请先运行: python make_icon.py")

_ver = tuple(int(x) for x in APP_VERSION.split("."))
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_ver, prodvers=_ver, mask=0x3F, flags=0x0,
                      OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([
            StringTable("080404B0", [
                StringStruct("CompanyName", APP_AUTHOR),
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("InternalName", "ATB"),
                StringStruct("LegalCopyright", APP_COPYRIGHT),
                StringStruct("OriginalFilename", "ATB.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", APP_VERSION),
                StringStruct("Comments", "面向 AI Agent 的全格式文件读取工具箱"),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [2052, 1200])]),
    ],
)

# ── 需要完整收集的解析库（装了就收，没装跳过）────────────────
PACKAGES = [
    "pymupdf",       # PyMuPDF 2.x 主模块名
    "fitz",          # PyMuPDF 1.x 兼容名
    "docx",          # python-docx
    "openpyxl",
    "pptx",
    "odf",
    "olefile",
    "striprtf",
    "xlrd",
    "bs4",
    "lxml",
    "yaml",
    "chardet",
    "PIL",
    "pillow_heif",
    "mutagen",
    "av",            # PyAV（内置 FFmpeg，视频抽帧）
    "py7zr",
    "rarfile",
    "pyarrow",
]

hiddenimports = []
for pkg in PACKAGES:
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# ── 运行期需要读取包元数据的库 ──────────────────────────────
METADATA = [
    "pymupdf", "python-docx", "openpyxl", "python-pptx", "pillow",
    "py7zr", "mutagen", "odfpy", "pyarrow", "rarfile",
]
datas = []
for dist in METADATA:
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "pandas", "scipy",
        "IPython", "jupyter", "notebook", "pytest",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "setuptools", "pip",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ATB",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON_FILE],
    version=VERSION_INFO,
)
