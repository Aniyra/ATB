# -*- coding: utf-8 -*-
"""PDF 解析器：文本层、元数据、目录、表格（PyMuPDF）。"""

import os
import re
import zlib

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import (BaseReader, clean_text, new_document, parse_page_range,
                   split_paragraphs)


class PdfReader(BaseReader):
    name = "pdf"
    formats = ("pdf",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "pdf.pymupdf")
        try:
            # PyMuPDF 1.28+ 会向 stdout 推荐 pymupdf_layout，污染 --json 输出
            os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
        except ImportError:
            doc.warnings.append("未安装 PyMuPDF，使用内置粗提取（仅能拿到部分文本）")
            return self._fallback(path, doc, opts)

        try:
            pdf = fitz.open(path)
        except Exception as e:
            raise AtbError("ATB-E05001", f"{path} ({e})")

        with pdf:
            if pdf.needs_pass:
                if not opts.password or not pdf.authenticate(opts.password):
                    raise AtbError("ATB-E05001", "PDF 已加密，需要 --password 提供正确密码")
            meta = pdf.metadata or {}
            doc.metadata.update({
                "pages": pdf.page_count,
                "title": (meta.get("title") or "").strip(),
                "author": (meta.get("author") or "").strip(),
                "subject": (meta.get("subject") or "").strip(),
                "creator": (meta.get("creator") or "").strip(),
                "producer": (meta.get("producer") or "").strip(),
                "creationDate": meta.get("creationDate", ""),
                "encrypted": bool(pdf.needs_pass),
                "pdf_version": getattr(pdf, "pdf_version", lambda: "")() if callable(getattr(pdf, "pdf_version", None)) else "",
            })
            try:
                toc = pdf.get_toc(simple=True)
                if toc:
                    doc.metadata["toc"] = [
                        {"level": lvl, "title": title, "page": page} for lvl, title, page in toc
                    ][:200]
            except Exception:
                pass

            page_ids = parse_page_range(opts.pages, pdf.page_count)
            total_chars = 0
            no_text_pages = []
            for pid in page_ids:
                page = pdf.load_page(pid)
                text = clean_text(page.get_text("text")).strip()
                if text:
                    doc.blocks.append(Block("heading", f"第 {pid + 1} 页", level=3, page=pid + 1))
                    for para in split_paragraphs(text):
                        doc.blocks.append(Block("paragraph", para, page=pid + 1))
                        total_chars += len(para)
                        if opts.max_chars and total_chars >= opts.max_chars:
                            doc.truncated = True
                            break
                else:
                    no_text_pages.append(pid + 1)
                    doc.blocks.append(Block("heading", f"第 {pid + 1} 页", level=3, page=pid + 1))
                    doc.blocks.append(Block("raw", "[本页无文本层，可能为扫描图片]",
                                            page=pid + 1, meta={"type": "no_text"}))
                try:
                    tables = page.find_tables()
                    for t in tables.tables[:5]:
                        rows = [[clean_text(str(c)) if c is not None else "" for c in row]
                                for row in t.extract()]
                        if rows:
                            doc.blocks.append(Block("table", "", page=pid + 1,
                                                    meta={"rows": rows}))
                except Exception:
                    pass
                img_count = len(page.get_images(full=True))
                if img_count:
                    doc.blocks.append(Block("image", f"[本页含 {img_count} 张图片]",
                                            page=pid + 1, meta={"count": img_count}))
                if doc.truncated:
                    break

            if no_text_pages and len(no_text_pages) == len(page_ids):
                doc.warnings.append(
                    f"{len(no_text_pages)} 页均无文本层，疑似扫描件；可用 --ocr（需 Tesseract）")
            elif no_text_pages:
                doc.warnings.append(f"{len(no_text_pages)} 页无文本层：{no_text_pages[:20]}")
            if len(page_ids) < pdf.page_count:
                doc.warnings.append(f"仅解析 {len(page_ids)}/{pdf.page_count} 页（--pages 限制）")
        if opts.ocr:
            doc.warnings.append("OCR 需要 Tesseract 与 pytesseract，当前版本仅提示，不做实际识别")
        return doc

    # ── 降级实现 ────────────────────────────────────────────
    def _fallback(self, path: str, doc: Document, opts: ReadOptions) -> Document:
        with open(path, "rb") as f:
            raw = f.read(opts.max_bytes)
        texts = []
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
            chunk = m.group(1)
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error:
                pass
            for t in re.finditer(rb"\((?:\\.|[^\\()])*\)", chunk):
                s = t.group(0)[1:-1]
                s = re.sub(rb"\\([nrtbf()\\])", rb"\1", s)
                texts.append(s.decode("latin-1", "ignore"))
            if sum(len(x) for x in texts) > opts.max_chars:
                break
        text = " ".join(texts)
        for para in split_paragraphs(text)[:500]:
            doc.blocks.append(Block("paragraph", clean_text(para)))
        doc.truncated = True
        doc.warnings.append("降级解析结果不完整，建议安装 PyMuPDF：pip install pymupdf")
        return doc
