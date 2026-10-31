# -*- coding: utf-8 -*-
"""解析调度：探测 → 选解析器 → 容错 → 统一 Document。"""

import os
from typing import Optional

from .detect import detect, TEXT_FORMATS
from .errors import AtbError, log_error
from .models import Document, ReadOptions
from .readers import READER_BY_FORMAT
from .readers.base import new_document, read_file_guard


def read_file(path: str, options: Optional[ReadOptions] = None,
              force_format: Optional[str] = None, safe: bool = False) -> Document:
    """读取任意文件，返回统一 Document。

    safe=True 时不抛异常，而是返回 ok=False 且带 error 字段的 Document。
    """
    opts = options or ReadOptions()
    try:
        read_file_guard(path)
        det = detect(path, force_format)
        if det.format == "video" and opts.storyboard:
            from .experimental.storyboard import build_storyboard
            doc = build_storyboard(path, det, opts)
            doc.mime = det.mime
            return doc
        reader = READER_BY_FORMAT.get(det.format)
        if reader is None:
            raise AtbError("ATB-E02001", f"{path}（格式 {det.format} 无可用解析器）")
        doc = reader.read(path, det, opts)
        doc.format = det.format
        doc.mime = det.mime
        if det.subtype and det.subtype != det.format:
            doc.metadata.setdefault("subtype", det.subtype)
        if "扩展名" in det.evidence and "以内容为准" in det.evidence:
            doc.warnings.insert(0, det.evidence)
        return doc
    except AtbError as e:
        if not safe:
            raise
        doc = Document(path=os.path.abspath(path), format="error", ok=False,
                       error=e.to_dict())
        return doc
    except PermissionError as e:
        err = AtbError("ATB-E01002", f"{path} ({e})")
    except FileNotFoundError as e:
        err = AtbError("ATB-E01001", f"{path} ({e})")
    except OSError as e:
        err = AtbError("ATB-E01003", f"{path} ({e})")
    except Exception as e:  # 兜底：任何解析器内部异常都不该炸掉整个进程
        err = AtbError("ATB-E00001", f"{path} ({type(e).__name__}: {e})")
        log_error(err, {"path": path})
    if not safe:
        raise err
    return Document(path=os.path.abspath(path), format="error", ok=False, error=err.to_dict())


def extract_text(path: str, options: Optional[ReadOptions] = None,
                 force_format: Optional[str] = None) -> str:
    """只返回纯文本，供 Agent 直接消费。"""
    doc = read_file(path, options, force_format)
    return doc.text(options.max_chars if options else None)


def file_info(path: str) -> dict:
    """只做轻量探测：格式、MIME、大小、哈希，不解析正文。"""
    read_file_guard(path)
    det = detect(path)
    st = os.stat(path)
    doc = new_document(path, det, "detect", sha=True)
    return {
        "ok": True,
        "path": doc.path,
        "format": det.format,
        "subtype": det.subtype,
        "mime": det.mime,
        "confidence": det.confidence,
        "evidence": det.evidence,
        "size": st.st_size,
        "mtime": doc.mtime,
        "sha256": doc.sha256,
        "is_text": det.format in TEXT_FORMATS,
    }
