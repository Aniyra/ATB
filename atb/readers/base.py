# -*- coding: utf-8 -*-
"""解析器基类与公共工具。"""

import os
import re
import unicodedata
from typing import List, Optional, Tuple

import config
from ..errors import AtbError
from ..models import Block, Document, ReadOptions, file_stat, human_size, sha256_of


class BaseReader:
    """所有解析器的基类。"""

    name = "base"
    formats: Tuple[str, ...] = ()

    def read(self, path: str, det, opts: ReadOptions) -> Document:  # pragma: no cover
        raise NotImplementedError


# ── 编码与文本 ────────────────────────────────────────────────
_BOMS = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]


def decode_bytes(data: bytes, encoding: Optional[str] = None) -> Tuple[str, str, List[str]]:
    """把字节解码为文本，返回 (text, encoding, warnings)。"""
    warnings: List[str] = []
    if encoding:
        try:
            return data.decode(encoding, errors="replace"), encoding, warnings
        except LookupError:
            warnings.append(f"未知编码 {encoding}，改为自动探测")

    for bom, enc in _BOMS:
        if data.startswith(bom):
            return data.decode(enc, errors="replace"), enc, warnings

    for enc in ("utf-8", "gb18030", "big5", "shift_jis"):
        try:
            return data.decode(enc), enc, warnings
        except UnicodeDecodeError:
            continue

    try:
        import chardet
        guess = chardet.detect(data[:200_000])
        enc = (guess.get("encoding") or "").lower()
        conf = guess.get("confidence") or 0
        if enc and conf >= 0.5:
            warnings.append(f"编码自动探测为 {enc}（置信度 {conf:.2f}）")
            return data.decode(enc, errors="replace"), enc, warnings
    except ImportError:
        pass

    warnings.append("编码探测失败，使用 UTF-8 + 替换字符解码")
    return data.decode("utf-8", errors="replace"), "utf-8", warnings


def read_bytes(path: str, opts: ReadOptions) -> bytes:
    size = os.path.getsize(path)
    if size > opts.max_bytes:
        with open(path, "rb") as f:
            return f.read(opts.max_bytes)
    with open(path, "rb") as f:
        return f.read()


def read_text_file(path: str, opts: ReadOptions,
                   stats: dict = None) -> Tuple[str, str, List[str], bool]:
    """读取文本文件，返回 (text, encoding, warnings, truncated)。

    stats 非空时填入截断前的 lines / characters（用于元数据）。
    """
    size = os.path.getsize(path)
    truncated = size > opts.max_bytes
    data = read_bytes(path, opts)
    text, enc, warns = decode_bytes(data, opts.encoding)
    if truncated:
        warns.append(f"文件超过 {human_size(opts.max_bytes)}，仅读取前 {human_size(len(data))}")

    # 二进制兜底：替换字符占比过高说明不是文本
    if text:
        bad = text.count("\ufffd")
        if bad / len(text) > 0.1:
            raise AtbError("ATB-E03002", path)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if stats is not None:
        stats["lines"] = text.count("\n") + 1 if text else 0
        stats["characters"] = len(text)

    if opts.max_chars and len(text) > opts.max_chars:
        text = text[: opts.max_chars]
        truncated = True
        warns.append(f"文本超过 {opts.max_chars} 字符上限，已截断")

    return text, enc, warns, truncated


def split_paragraphs(text: str) -> List[str]:
    """按空行切分段落。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def clean_text(text: str) -> str:
    """去掉零宽字符、统一空白，便于 Agent 阅读。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    return text


# ── 页码 ─────────────────────────────────────────────────────
def parse_page_range(spec: Optional[str], total: int) -> List[int]:
    """把 '1-3, 5, 8-' 解析为 0 基页码列表。"""
    if not spec:
        return list(range(total))
    pages: List[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else total
            pages.extend(range(start - 1, min(end, total)))
        elif part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < total:
                pages.append(idx)
    return sorted(set(p for p in pages if 0 <= p < total))


# ── Document 构造 ─────────────────────────────────────────────
def new_document(path: str, det, reader: str, *, sha: bool = True) -> Document:
    st = file_stat(path)
    size = st["size"]
    digest = ""
    if sha:
        try:
            digest = sha256_of(path, limit=None if size <= 64 * 1024 * 1024 else 16 * 1024 * 1024)
        except OSError:
            digest = ""
    return Document(
        path=os.path.abspath(path),
        format=det.format,
        handler=reader,
        mime=det.mime,
        size=size,
        mtime=st["mtime"],
        sha256=digest + (" (前 16MB)" if size > 64 * 1024 * 1024 else ""),
    )


def blocks_with_pages(pages: List[Tuple[int, str]], page_offset: int = 1) -> List[Block]:
    out: List[Block] = []
    for idx, text in pages:
        for para in split_paragraphs(text):
            out.append(Block("paragraph", clean_text(para), page=idx + page_offset))
    return out


def read_file_guard(path: str) -> None:
    if not os.path.exists(path):
        raise AtbError("ATB-E01001", path)
    if os.path.isdir(path):
        raise AtbError("ATB-E01004", path)
    if not os.access(path, os.R_OK):
        raise AtbError("ATB-E01002", path)
    if os.path.getsize(path) > config.DEFAULT_MAX_BYTES * 10:
        raise AtbError("ATB-E00004", f"{path} ({human_size(os.path.getsize(path))})")
