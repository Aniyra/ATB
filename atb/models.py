# -*- coding: utf-8 -*-
"""统一数据模型：所有格式最终都被归一化为 Document + Block 列表。

这样 Agent 只需理解一套 schema，无需关心底层是 PDF、DOCX 还是压缩包。
"""

import datetime
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import config


@dataclass
class Block:
    """文档中的一个内容块。"""

    type: str                      # heading / paragraph / code / table / image / list / kv / raw / sheet / page
    text: str = ""
    level: Optional[int] = None    # heading 层级
    page: Optional[int] = None     # PDF 页码
    sheet: Optional[str] = None    # Excel 工作表名
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"type": self.type}
        if self.text:
            d["text"] = self.text
        if self.level is not None:
            d["level"] = self.level
        if self.page is not None:
            d["page"] = self.page
        if self.sheet is not None:
            d["sheet"] = self.sheet
        if self.meta:
            d["meta"] = self.meta
        return d


@dataclass
class ReadOptions:
    """读取选项，CLI 与 MCP 共用。"""

    max_chars: int = config.DEFAULT_MAX_CHARS
    max_bytes: int = config.DEFAULT_MAX_BYTES
    encoding: Optional[str] = None
    pages: Optional[str] = None        # "1-5,8" 形式的页码选择
    sheet: Optional[str] = None        # 仅读取指定工作表
    member: Optional[str] = None       # 压缩包内指定条目
    password: Optional[str] = None
    ocr: bool = False
    include_binary: bool = False
    preview_bytes: int = 1024

    # ── 视频抽帧 ──
    frames: int = 12                   # 抽取帧数（1-240，软上限）
    start: Optional[float] = None      # 起始秒
    end: Optional[float] = None        # 结束秒
    frame_width: int = 768             # 长边像素（128-1920）
    frame_quality: int = 80            # JPEG 质量
    include_audio: bool = False        # 是否导出音轨
    frame_dir: Optional[str] = None    # 帧输出目录
    sampling: str = "uniform"          # uniform / keyframe

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ReadOptions":
        data = data or {}
        opts = cls()
        for key, value in data.items():
            if value is None:
                continue
            if hasattr(opts, key):
                setattr(opts, key, value)
        return opts


@dataclass
class Document:
    """单个文件的解析结果。"""

    path: str
    format: str = "unknown"
    handler: str = ""
    mime: str = "application/octet-stream"
    size: int = 0
    mtime: str = ""
    sha256: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    blocks: List[Block] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    truncated: bool = False
    ok: bool = True
    error: Optional[dict] = None

    # ---------- 便捷方法 ----------
    def text(self, max_chars: Optional[int] = None) -> str:
        """把所有块拼接为纯文本。"""
        parts: List[str] = []
        for b in self.blocks:
            if b.type == "table":
                rows = b.meta.get("rows") or []
                parts.append("\n".join("\t".join(str(c) for c in row) for row in rows))
            elif b.text:
                parts.append(b.text)
            if max_chars and sum(len(p) for p in parts) >= max_chars:
                break
        out = "\n\n".join(p for p in parts if p)
        if max_chars and len(out) > max_chars:
            out = out[:max_chars]
        return out

    def word_count(self) -> int:
        text = self.text()
        return len(text.split()) if text else 0

    def to_dict(self, include_text: bool = True, max_chars: Optional[int] = None) -> dict:
        d: Dict[str, Any] = {
            "ok": self.ok,
            "path": self.path,
            "format": self.format,
            "handler": self.handler,
            "mime": self.mime,
            "size": self.size,
            "size_human": human_size(self.size),
            "mtime": self.mtime,
            "sha256": self.sha256,
            "metadata": self.metadata,
            "stats": {
                "blocks": len(self.blocks),
                "words": self.word_count(),
                "chars": len(self.text()),
            },
            "truncated": self.truncated,
            "warnings": self.warnings,
        }
        if self.error:
            d["error"] = self.error
        if include_text:
            d["blocks"] = [b.to_dict() for b in self.blocks]
            text = self.text(max_chars)
            d["text"] = text
        return d


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} PB"


def file_stat(path: str) -> Dict[str, Any]:
    st = os.stat(path)
    return {
        "size": st.st_size,
        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def sha256_of(path: str, limit: Optional[int] = None, chunk: int = 1024 * 1024) -> str:
    """计算 SHA256；limit 不为空时只算前 limit 字节（大文件用）。"""
    h = hashlib.sha256()
    remain = limit if limit is not None else -1
    with open(path, "rb") as f:
        while True:
            if remain == 0:
                break
            size = chunk if remain < 0 else min(chunk, remain)
            data = f.read(size)
            if not data:
                break
            h.update(data)
            if remain > 0:
                remain -= len(data)
    return h.hexdigest()
