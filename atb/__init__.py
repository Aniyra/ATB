# -*- coding: utf-8 -*-
"""ATB — All Tool Box：面向 AI Agent 的全格式文件读取工具箱。

对外主要入口：
    atb.read_file(path)      读取单个文件，返回统一 Document
    atb.extract_text(path)   只取纯文本
    atb.scan_directory(path) 扫描目录并统计
"""

from .models import Block, Document, ReadOptions  # noqa: F401
from .errors import AtbError, ERRORS  # noqa: F401
from .detect import detect  # noqa: F401
from .dispatch import read_file, extract_text, file_info  # noqa: F401
from .scanner import scan_directory  # noqa: F401

__version__ = "1.0.0"
