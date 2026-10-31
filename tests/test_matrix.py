# -*- coding: utf-8 -*-
"""真实格式端到端测试：现场生成文档再读回来。

依赖缺失时对应用例自动跳过（不影响其他用例）。
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.dispatch import read_file  # noqa: E402
from atb.models import ReadOptions  # noqa: E402


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


@unittest.skipUnless(has("pymupdf") or has("fitz"), "需要 PyMuPDF")
class PdfTest(unittest.TestCase):
    def test_pdf_pages_and_metadata(self):
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pdf")
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Quarterly Report 2026", fontsize=16)
            page.insert_text((72, 120), "Revenue grew by 18 percent.", fontsize=11)
            pdf.new_page().insert_text((72, 72), "Second page content.", fontsize=11)
            pdf.set_metadata({"title": "季度报告"})
            pdf.save(path)
            pdf.close()

            doc = read_file(path)
            self.assertEqual(doc.format, "pdf")
            self.assertEqual(doc.metadata["pages"], 2)
            self.assertIn("Quarterly Report", doc.text())
            self.assertIn("Second page", doc.text())
            pages = {b.page for b in doc.blocks if b.page}
            self.assertEqual(pages, {1, 2})


@unittest.skipUnless(has("docx"), "需要 python-docx")
class DocxTest(unittest.TestCase):
    def test_docx_structure(self):
        from docx import Document as Docx
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.docx")
            d_ = Docx()
            d_.add_heading("项目报告", level=1)
            d_.add_paragraph("这是正文第一段。")
            table = d_.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "名称"
            table.cell(0, 1).text = "数量"
            table.cell(1, 0).text = "苹果"
            table.cell(1, 1).text = "3"
            d_.save(path)

            doc = read_file(path)
            self.assertEqual(doc.format, "office")
            self.assertIn("项目报告", doc.text())
            self.assertIn("这是正文第一段", doc.text())
            headings = [b for b in doc.blocks if b.type == "heading"]
            self.assertEqual(headings[0].level, 1)
            tables = [b for b in doc.blocks if b.type == "table"]
            self.assertEqual(tables[0].meta["rows"][1], ["苹果", "3"])


@unittest.skipUnless(has("openpyxl"), "需要 openpyxl")
class XlsxTest(unittest.TestCase):
    def test_xlsx_sheets(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "销售"
            ws.append(["地区", "金额"])
            ws.append(["华东", 120])
            ws2 = wb.create_sheet("库存")
            ws2.append(["SKU", "数量"])
            ws2.append(["A-1", 8])
            wb.save(path)

            doc = read_file(path)
            self.assertEqual(doc.metadata["sheets"], ["销售", "库存"])
            self.assertIn("华东", doc.text())

            only = read_file(path, ReadOptions(sheet="库存"))
            self.assertIn("A-1", only.text())
            self.assertNotIn("华东", only.text())


@unittest.skipUnless(has("pptx"), "需要 python-pptx")
class PptxTest(unittest.TestCase):
    def test_pptx_slides(self):
        from pptx import Presentation
        from pptx.util import Inches
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pptx")
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = "年度总结"
            slide.placeholders[1].text = "增长 18%"
            prs.save(path)

            doc = read_file(path)
            self.assertEqual(doc.metadata["slides"], 1)
            self.assertIn("年度总结", doc.text())
            self.assertEqual(doc.blocks[0].page, 1)


@unittest.skipUnless(has("PIL"), "需要 Pillow")
class ImageTest(unittest.TestCase):
    def test_png_metadata(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.png")
            Image.new("RGB", (120, 80), (20, 120, 200)).save(path)
            doc = read_file(path)
            self.assertEqual(doc.format, "image")
            self.assertEqual(doc.metadata["width"], 120)
            self.assertEqual(doc.metadata["height"], 80)


class EpubTest(unittest.TestCase):
    def test_epub_chapters(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.epub")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml",
                           '<?xml version="1.0"?><container version="1.0" '
                           'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                           '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                           'media-type="application/oebps-package+xml"/></rootfiles></container>')
                z.writestr("OEBPS/content.opf",
                           '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                           'version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                           '<dc:title>ATB 电子书</dc:title><dc:creator>LFTAniyara</dc:creator>'
                           '</metadata><manifest><item id="c1" href="chapter1.xhtml" '
                           'media-type="application/xhtml+xml"/></manifest>'
                           '<spine><itemref idref="c1"/></spine></package>')
                z.writestr("OEBPS/chapter1.xhtml",
                           '<html><head><title>第一章</title></head><body>'
                           '<h1>第一章</h1><p>电子书正文内容。</p></body></html>')
            doc = read_file(path)
            self.assertEqual(doc.format, "ebook")
            self.assertEqual(doc.metadata["title"], "ATB 电子书")
            self.assertIn("电子书正文内容", doc.text())


class SqliteTest(unittest.TestCase):
    def test_sqlite_tables(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, name TEXT)")
            conn.executemany("INSERT INTO user (name) VALUES (?)", [("张三",), ("李四",)])
            conn.commit()
            conn.close()

            doc = read_file(path)
            self.assertEqual(doc.metadata["tables"], ["user"])
            self.assertIn("张三", doc.text())


class HtmlTest(unittest.TestCase):
    def test_html_extract(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html><head><title>页面标题</title></head><body>"
                        "<h1>主标题</h1><p>第一段内容。</p>"
                        "<table><tr><th>列</th></tr><tr><td>值</td></tr></table></body></html>")
            doc = read_file(path)
            self.assertEqual(doc.metadata["title"], "页面标题")
            self.assertIn("第一段内容", doc.text())
            tables = [b for b in doc.blocks if b.type == "table"]
            self.assertEqual(tables[0].meta["rows"][1], ["值"])


class JsonRoundtripTest(unittest.TestCase):
    def test_to_dict_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"k": "值", "n": [1, 2]}, f, ensure_ascii=False)
            doc = read_file(path)
            payload = json.dumps(doc.to_dict(), ensure_ascii=False)
            self.assertIn("值", payload)


if __name__ == "__main__":
    unittest.main()
