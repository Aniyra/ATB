# -*- coding: utf-8 -*-
"""读取链路测试：文本 / Markdown / CSV / JSON / 压缩包 / 错误处理。"""

import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.dispatch import extract_text, file_info, read_file  # noqa: E402
from atb.errors import AtbError  # noqa: E402
from atb.models import ReadOptions  # noqa: E402


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
        return path

    def test_text_paragraphs(self):
        path = self._write("a.txt", "第一段\n\n第二段\n")
        doc = read_file(path)
        self.assertTrue(doc.ok)
        self.assertEqual(doc.format, "text")
        self.assertIn("第一段", doc.text())
        self.assertIn("第二段", doc.text())
        self.assertEqual(len(doc.blocks), 2)

    def test_markdown_structure(self):
        path = self._write("a.md", "# 标题\n\n正文内容\n\n```python\nprint(1)\n```\n")
        doc = read_file(path)
        types = [b.type for b in doc.blocks]
        self.assertIn("heading", types)
        self.assertIn("code", types)
        self.assertEqual(doc.blocks[0].level, 1)

    def test_gbk_encoding(self):
        path = self._write("gbk.txt", "中文编码", encoding="gbk")
        self.assertIn("中文编码", extract_text(path))

    def test_csv_table(self):
        path = self._write("a.csv", "name,score\n张三,90\n李四,85\n")
        doc = read_file(path)
        table = [b for b in doc.blocks if b.type == "table"]
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0].meta["rows"][1][0], "张三")
        self.assertEqual(doc.metadata["rows"], 2)

    def test_json_shape(self):
        path = self._write("a.json", json.dumps({"a": 1, "b": [1, 2, 3]}))
        doc = read_file(path)
        self.assertEqual(doc.metadata["type"], "object")
        self.assertIn("a", doc.metadata["keys"])

    def test_jsonl(self):
        path = self._write("a.jsonl", '{"x":1}\n{"x":2}\n')
        doc = read_file(path)
        self.assertEqual(doc.metadata["records"], 2)

    def test_zip_listing_and_member(self):
        path = os.path.join(self.dir, "a.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("inside.txt", "归档内文本")
            z.writestr("dir/other.bin", b"\x00" * 10)
        doc = read_file(path)
        table = [b for b in doc.blocks if b.type == "table"][0]
        self.assertEqual(len(table.meta["rows"]), 2)

        doc2 = read_file(path, ReadOptions(member="inside.txt"))
        self.assertIn("归档内文本", doc2.text())

    def test_missing_file(self):
        with self.assertRaises(AtbError) as ctx:
            read_file(os.path.join(self.dir, "nope.txt"))
        self.assertEqual(ctx.exception.code, "ATB-E01001")

    def test_directory_rejected(self):
        with self.assertRaises(AtbError) as ctx:
            read_file(self.dir)
        self.assertEqual(ctx.exception.code, "ATB-E01004")

    def test_safe_mode(self):
        doc = read_file(os.path.join(self.dir, "nope.txt"), safe=True)
        self.assertFalse(doc.ok)
        self.assertEqual(doc.error["code"], "ATB-E01001")

    def test_file_info(self):
        path = self._write("a.txt", "hello")
        info = file_info(path)
        self.assertEqual(info["format"], "text")
        self.assertEqual(len(info["sha256"]), 64)

    def test_text_stats_before_truncation(self):
        """metadata.lines/characters 应反映截断前的真实规模。"""
        lines = [f"第 {i} 行" for i in range(4000)]
        path = self._write("long.txt", "\n".join(lines))
        doc = read_file(path, ReadOptions(max_chars=1000))
        self.assertLessEqual(len(doc.text()), 1000)
        self.assertEqual(doc.metadata["lines"], 4000)
        self.assertEqual(doc.metadata["characters"], len("\n".join(lines)))
        self.assertTrue(doc.truncated)


if __name__ == "__main__":
    unittest.main()
