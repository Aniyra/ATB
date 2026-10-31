# -*- coding: utf-8 -*-
"""办公文档解析器：DOCX / XLSX / PPTX / ODF / RTF / 旧版 OLE 文档。"""

import re
import zipfile
from html import unescape

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import BaseReader, clean_text, new_document, split_paragraphs

MAX_ROWS = 500


class OfficeReader(BaseReader):
    name = "office"
    formats = ("office",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        ext = det.ext.lower() or det.subtype or ""
        if ext in ("docx", "docm", "dotx"):
            return self._docx(path, det, opts)
        if ext in ("xlsx", "xlsm", "xltx", "xltm"):
            return self._xlsx(path, det, opts)
        if ext == "xls":
            return self._xls(path, det, opts)
        if ext in ("pptx", "pptm", "potx"):
            return self._pptx(path, det, opts)
        if ext in ("odt", "ods", "odp"):
            return self._odf(path, det, opts)
        if ext == "rtf":
            return self._rtf(path, det, opts)
        if ext in ("doc", "ppt", "wps", "et", "dps"):
            return self._legacy_ole(path, det, opts)
        raise AtbError("ATB-E04001", f"未知办公格式 {ext}")

    # ── DOCX ────────────────────────────────────────────────
    def _docx(self, path, det, opts):
        doc = new_document(path, det, "office.docx")
        try:
            from docx import Document as DocxDocument
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError:
            doc.warnings.append("未安装 python-docx，使用 ZIP+XML 粗提取")
            return self._docx_fallback(path, doc, opts)

        d = DocxDocument(path)
        props = d.core_properties
        doc.metadata.update({
            "title": props.title or "", "author": props.author or "",
            "subject": props.subject or "", "created": str(props.created or ""),
            "modified": str(props.modified or ""),
            "paragraphs": len(d.paragraphs), "tables": len(d.tables),
            "sections": len(d.sections),
        })
        char_count = 0
        for child in d.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(child, d)
                text = clean_text(p.text.strip())
                if not text:
                    continue
                style = (p.style.name or "") if p.style is not None else ""
                if style.lower().startswith(("heading", "标题")):
                    level = _heading_level(style)
                    doc.blocks.append(Block("heading", text, level=level))
                elif style.lower() in ("list paragraph", "list bullet", "list number"):
                    doc.blocks.append(Block("list", text))
                else:
                    doc.blocks.append(Block("paragraph", text))
                char_count += len(text)
            elif tag == "tbl":
                table = Table(child, d)
                rows = [[clean_text(c.text.strip()) for c in row.cells] for row in table.rows]
                if rows:
                    doc.blocks.append(Block("table", "", meta={"rows": rows,
                                                               "header": rows[0]}))
            if opts.max_chars and char_count >= opts.max_chars:
                doc.truncated = True
                doc.warnings.append(f"文档超过 {opts.max_chars} 字符，已截断")
                break
        if not doc.blocks:
            doc.warnings.append("文档正文为空（可能全部内容在图片或文本框中）")
        return doc

    def _docx_fallback(self, path, doc, opts):
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        for p in paras:
            text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
            text = clean_text(unescape(text)).strip()
            if text:
                doc.blocks.append(Block("paragraph", text))
        doc.warnings.append("python-docx 缺失，未解析表格与样式")
        return doc

    # ── XLSX ────────────────────────────────────────────────
    def _xlsx(self, path, det, opts):
        doc = new_document(path, det, "office.xlsx")
        try:
            from openpyxl import load_workbook
        except ImportError:
            doc.warnings.append("未安装 openpyxl，使用 ZIP+XML 粗提取")
            return self._xlsx_fallback(path, doc, opts)

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = wb.sheetnames
            doc.metadata["sheets"] = sheets
            targets = [opts.sheet] if opts.sheet and opts.sheet in sheets else sheets
            for name in targets:
                ws = wb[name]
                doc.metadata.setdefault("dimensions", {})[name] = ws.calculate_dimension()
                rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_ROWS:
                        doc.truncated = True
                        break
                    if all(v is None for v in row):
                        continue
                    rows.append(["" if v is None else str(v) for v in row])
                if not rows:
                    doc.warnings.append(f"工作表 {name} 为空")
                    continue
                doc.blocks.append(Block("heading", f"工作表: {name}", level=2))
                doc.blocks.append(Block("table", "", sheet=name,
                                        meta={"rows": rows, "header": rows[0]}))
        finally:
            wb.close()
        return doc

    def _xlsx_fallback(self, path, doc, opts):
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                for si in re.findall(r"<si>(.*?)</si>", ss, re.S):
                    shared.append(clean_text("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
            for name in [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]:
                xml = z.read(name).decode("utf-8", "replace")
                rows = []
                for row_xml in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S)[:MAX_ROWS]:
                    cells = []
                    for cell in re.findall(r"<c[^>]*?(?:/>|>.*?</c>)", row_xml, re.S):
                        v = re.search(r"<v>(.*?)</v>", cell)
                        if not v:
                            cells.append("")
                        elif 't="s"' in cell[:cell.find(">")]:
                            idx = int(v.group(1))
                            cells.append(shared[idx] if idx < len(shared) else "")
                        else:
                            cells.append(unescape(v.group(1)))
                    rows.append(cells)
                if rows:
                    doc.blocks.append(Block("table", "", sheet=name, meta={"rows": rows}))
        doc.warnings.append("openpyxl 缺失，工作表名与样式未解析")
        return doc

    # ── XLS（旧版）─────────────────────────────────────────
    def _xls(self, path, det, opts):
        doc = new_document(path, det, "office.xls")
        try:
            import xlrd
        except ImportError:
            return self._legacy_ole(path, det, opts)
        try:
            book = xlrd.open_workbook(path, on_demand=True)
        except Exception as e:
            raise AtbError("ATB-E04001", f"{path} ({e})")
        doc.metadata["sheets"] = book.sheet_names()
        for name in book.sheet_names():
            sheet = book.sheet_by_name(name)
            rows = []
            for r in range(min(sheet.nrows, MAX_ROWS)):
                rows.append([("" if v is None else str(v)) for v in sheet.row_values(r)])
            if rows:
                doc.blocks.append(Block("heading", f"工作表: {name}", level=2))
                doc.blocks.append(Block("table", "", sheet=name, meta={"rows": rows}))
            if sheet.nrows > MAX_ROWS:
                doc.truncated = True
        return doc

    # ── PPTX ────────────────────────────────────────────────
    def _pptx(self, path, det, opts):
        doc = new_document(path, det, "office.pptx")
        try:
            from pptx import Presentation
        except ImportError:
            doc.warnings.append("未安装 python-pptx，使用 ZIP+XML 粗提取")
            return self._pptx_fallback(path, doc, opts)
        prs = Presentation(path)
        doc.metadata["slides"] = len(prs.slides)
        for idx, slide in enumerate(prs.slides, 1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = clean_text(shape.text_frame.text.strip())
                    if t:
                        texts.append(t)
                if getattr(shape, "has_table", False):
                    rows = [[clean_text(c.text.strip()) for c in row.cells]
                            for row in shape.table.rows]
                    if rows:
                        doc.blocks.append(Block("table", "", page=idx,
                                                meta={"rows": rows}))
            doc.blocks.append(Block("heading", f"第 {idx} 页", level=2, page=idx))
            for t in texts:
                for para in split_paragraphs(t):
                    doc.blocks.append(Block("paragraph", para, page=idx))
            if slide.has_notes_slide:
                notes = clean_text(slide.notes_slide.notes_text_frame.text.strip())
                if notes:
                    doc.blocks.append(Block("raw", notes, page=idx,
                                            meta={"note": "speaker notes"}))
        return doc

    def _pptx_fallback(self, path, doc, opts):
        with zipfile.ZipFile(path) as z:
            slides = sorted(n for n in z.namelist()
                            if re.match(r"ppt/slides/slide\d+\.xml$", n))
            doc.metadata["slides"] = len(slides)
            for idx, name in enumerate(slides, 1):
                xml = z.read(name).decode("utf-8", "replace")
                texts = [clean_text(unescape(t)) for t in
                         re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)]
                doc.blocks.append(Block("heading", f"第 {idx} 页", level=2, page=idx))
                for t in texts:
                    if t:
                        doc.blocks.append(Block("paragraph", t, page=idx))
        return doc

    # ── OpenDocument ────────────────────────────────────────
    def _odf(self, path, det, opts):
        doc = new_document(path, det, f"office.odf")
        try:
            from odf import teletype, text as odftext
            from odf.opendocument import load
        except ImportError:
            raise AtbError("ATB-E11001", "解析 ODF 需要 odfpy：pip install odfpy")
        d = load(path)
        if det.ext == "ods":
            from odf import table as odftable
            for i, table in enumerate(d.getElementsByType(odftable.Table), 1):
                name = table.getAttribute("name") or f"Sheet{i}"
                rows = []
                for row in table.getElementsByType(odftable.TableRow):
                    cells = []
                    for cell in row.getElementsByType(odftable.TableCell):
                        cells.append(clean_text(teletype.extractText(cell)))
                    if cells:
                        rows.append(cells)
                    if len(rows) >= MAX_ROWS:
                        break
                if rows:
                    doc.blocks.append(Block("heading", f"工作表: {name}", level=2))
                    doc.blocks.append(Block("table", "", sheet=name, meta={"rows": rows}))
        else:
            for h in d.getElementsByType(odftext.H):
                doc.blocks.append(Block("heading", clean_text(teletype.extractText(h)),
                                        level=int(h.getAttribute("outlinelevel") or 1)))
            for p in d.getElementsByType(odftext.P):
                t = clean_text(teletype.extractText(p)).strip()
                if t:
                    doc.blocks.append(Block("paragraph", t))
            if det.ext == "odp":
                doc.metadata["note"] = "演示文稿按文本顺序提取，未区分页面"
        return doc

    # ── RTF ─────────────────────────────────────────────────
    def _rtf(self, path, det, opts):
        doc = new_document(path, det, "office.rtf")
        with open(path, "rb") as f:
            raw = f.read(opts.max_bytes)
        try:
            from striprtf.striprtf import rtf_to_text
            text = rtf_to_text(raw.decode("latin-1", "ignore"))
        except ImportError:
            text = re.sub(r"\\[a-z]+\d* ?|[{}]", "", raw.decode("latin-1", "ignore"))
            doc.warnings.append("未安装 striprtf，RTF 控制字清理可能不完整")
        for para in split_paragraphs(text):
            doc.blocks.append(Block("paragraph", clean_text(para)))
        if len(raw) >= opts.max_bytes:
            doc.truncated = True
        return doc

    # ── 旧版 OLE（doc/xls/ppt/wps）──────────────────────────
    def _legacy_ole(self, path, det, opts):
        doc = new_document(path, det, "office.ole-strings")
        try:
            import olefile
        except ImportError:
            raise AtbError("ATB-E11001", "解析旧版 Office 需要 olefile：pip install olefile")
        if not olefile.isOleFile(path):
            raise AtbError("ATB-E04001", "不是 OLE 复合文档")
        pieces = []
        with olefile.OleFileIO(path) as ole:
            doc.metadata["streams"] = ["/".join(s) for s in ole.listdir()][:50]
            for entry in ole.listdir():
                if entry[-1] in ("WordDocument", "Workbook", "Book", "PowerPoint Document",
                                 "Contents", "Data"):
                    data = ole.openstream(entry).read(opts.max_bytes)
                    pieces.extend(_extract_strings(data))
        text = "\n".join(pieces)
        for para in split_paragraphs(text)[:2000]:
            doc.blocks.append(Block("paragraph", clean_text(para)))
        doc.warnings.append("旧版 Office 二进制格式仅提取可见字符串，建议先转换为 docx/xlsx/pptx")
        return doc


def _heading_level(style: str) -> int:
    m = re.search(r"(\d+)", style)
    return int(m.group(1)) if m else 1


def _extract_strings(data: bytes, min_len: int = 4):
    """从二进制中抽取 UTF-16LE 与单字节可打印字符串。"""
    out = []
    # UTF-16LE
    try:
        s = data.decode("utf-16-le", "ignore")
        for m in re.finditer(r"[\u0020-\u007e\u4e00-\u9fff\uff00-\uffef]{%d,}" % min_len, s):
            out.append(m.group(0))
    except Exception:
        pass
    # 单字节
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        out.append(m.group(0).decode("latin-1"))
    # 去重且保持顺序
    seen, uniq = set(), []
    for s in out:
        s = s.strip()
        if len(s) >= min_len and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq
