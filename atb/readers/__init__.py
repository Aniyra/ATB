# -*- coding: utf-8 -*-
"""解析器注册表。新增格式只需在这里挂一个 Reader。"""

from .archive import ArchiveReader
from .base import BaseReader
from .data import DataReader
from .misc import (BinaryReader, CalendarReader, EmailReader, SubtitleReader)
from .media import FontReader, ImageReader, MediaReader, PeReader
from .office import OfficeReader
from .pdf import PdfReader
from .text import TextReader
from .video import VideoReader
from .web import WebReader

# 顺序即优先级（同格式取第一个声明者）
READERS = [
    PdfReader(),
    OfficeReader(),
    WebReader(),
    DataReader(),
    TextReader(),
    ImageReader(),
    VideoReader(),
    MediaReader(),
    ArchiveReader(),
    SubtitleReader(),
    EmailReader(),
    CalendarReader(),
    FontReader(),
    PeReader(),
    BinaryReader(),
]

READER_BY_FORMAT = {}
for _reader in READERS:
    for _fmt in _reader.formats:
        READER_BY_FORMAT.setdefault(_fmt, _reader)

__all__ = ["READERS", "READER_BY_FORMAT", "BaseReader"]
