# -*- coding: utf-8 -*-
"""格式探测测试：扩展名、魔术字节、ZIP 家族细分。"""

import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.detect import detect  # noqa: E402


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data: bytes) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_pdf_by_magic(self):
        path = self._write("fake.txt", b"%PDF-1.7\n1 0 obj\n")
        det = detect(path)
        self.assertEqual(det.format, "pdf")
        self.assertEqual(det.mime, "application/pdf")
        self.assertIn("magic", det.evidence)

    def test_png_by_magic(self):
        path = self._write("x.dat", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        self.assertEqual(detect(path).format, "image")

    def test_plain_text_by_extension(self):
        path = self._write("a.md", "# 标题\n\n正文".encode("utf-8"))
        det = detect(path)
        self.assertEqual(det.format, "text")

    def test_unknown_binary(self):
        path = self._write("blob.zzz", bytes(range(0, 32)) * 4)
        self.assertEqual(detect(path).format, "binary")

    def test_docx_zip_subtype(self):
        path = os.path.join(self.dir, "a.docx")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<w:document/>")
        det = detect(path)
        self.assertEqual(det.format, "office")
        self.assertEqual(det.subtype, "docx")

    def test_epub_zip_subtype(self):
        path = os.path.join(self.dir, "a.epub")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", "<container/>")
        det = detect(path)
        self.assertEqual(det.format, "ebook")
        self.assertEqual(det.subtype, "epub")

    def test_force_format(self):
        path = self._write("a.bin", b"%PDF-1.4\n")
        self.assertEqual(detect(path, "text").format, "text")

    def test_gbk_text(self):
        path = self._write("gbk.txt", "中文内容测试".encode("gbk"))
        self.assertEqual(detect(path).format, "text")


if __name__ == "__main__":
    unittest.main()
