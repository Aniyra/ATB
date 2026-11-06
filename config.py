# -*- coding: utf-8 -*-
"""ATB 全局配置与项目元信息"""

import os
import sys

APP_NAME = "ATB (All Tool Box)"
APP_SHORT = "ATB"
APP_VERSION = "1.3.1"
APP_AUTHOR = "LFTAniyra"
APP_DESC = "面向 AI Agent 的全格式文件读取工具箱"

# 程序调用名：打包后为 ATB.exe，源码运行为 python main.py
PROG = "ATB" if getattr(sys, "frozen", False) else "python main.py"

# 项目根目录（兼容 PyInstaller onefile 解包目录）
def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        # 打包后：报告与配置写在 exe 同级目录，而不是临时解包目录
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()

# 默认读取上限（字符）。0 表示不限制。
DEFAULT_MAX_CHARS = 200_000

# 单个文件参与解析的大小上限（字节），超过则只做浅层元数据 + 头部预览
DEFAULT_MAX_BYTES = 300 * 1024 * 1024

# 目录扫描默认最多返回的文件数
DEFAULT_SCAN_LIMIT = 5000

# 缓存与报告目录
REPORT_DIR = os.path.join(BASE_DIR, "reports")

# reports/ 下自动产物（抽帧/分镜/放大/截图）的保留天数；0 表示关闭自动清理
try:
    REPORT_TTL_DAYS = max(0, int(os.environ.get("ATB_REPORT_TTL_DAYS", "7")))
except ValueError:
    REPORT_TTL_DAYS = 7
