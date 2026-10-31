# -*- coding: utf-8 -*-
"""错误码与目录扫描测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.errors import AtbError, ERRORS, lookup, normalize_code  # noqa: E402
from atb.scanner import scan_directory  # noqa: E402


class ErrorsTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_code("1003"), "ATB-E01003")
        self.assertEqual(normalize_code("E5002"), "ATB-E05002")
        self.assertEqual(normalize_code("atb-e08001"), "ATB-E08001")
        self.assertIsNone(normalize_code("zzz"))

    def test_all_codes_have_solution(self):
        for code, info in ERRORS.items():
            self.assertTrue(code.startswith("ATB-E"))
            self.assertEqual(len(code), 10)
            self.assertTrue(info["title"])
            self.assertIn("solution", info)

    def test_lookup_by_keyword(self):
        hit = lookup("权限")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["code"], "ATB-E01002")

    def test_atb_error_payload(self):
        err = AtbError("ATB-E01001", "C:/nope")
        payload = err.to_dict(prog="ATB")
        self.assertEqual(payload["code"], "ATB-E01001")
        self.assertTrue(payload["solution"])
        self.assertEqual(payload["category"], "文件访问")

        err2 = AtbError("ATB-E02001", "x")
        self.assertIn("ATB", err2.to_dict(prog="ATB")["solution"])


class ScannerTest(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sub"))
            with open(os.path.join(d, "a.txt"), "wb") as f:
                f.write(b"hello")
            with open(os.path.join(d, "b.csv"), "wb") as f:
                f.write(b"x,y\n1,2\n")
            with open(os.path.join(d, "sub", "c.pdf"), "wb") as f:
                f.write(b"%PDF-1.7\n")

            result = scan_directory(d)
            self.assertEqual(result["files"], 3)
            self.assertEqual(result["total_size"], 5 + 8 + 9)
            self.assertIn("text", result["by_format"])
            self.assertIn("data", result["by_format"])
            self.assertIn("pdf", result["by_format"])
            self.assertEqual(len(result["files_detail"]), 3)

    def test_scan_limit(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(10):
                with open(os.path.join(d, f"{i}.txt"), "w") as f:
                    f.write("x")
            result = scan_directory(d, limit=5)
            self.assertEqual(result["files"], 5)
            self.assertTrue(result["truncated"])

    def test_missing_dir(self):
        with self.assertRaises(AtbError):
            scan_directory("Z:/definitely/not/here")


if __name__ == "__main__":
    unittest.main()
