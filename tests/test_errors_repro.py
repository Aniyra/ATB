# -*- coding: utf-8 -*-
"""错误码复现回归：确保业务码不再落进 E00001，且可稳定触发。"""

import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.dispatch import read_file  # noqa: E402
from atb.errors import AtbError  # noqa: E402
from atb.models import ReadOptions  # noqa: E402

WINDOWS = sys.platform == "win32"


class ErrorMappingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _path(self, name: str) -> str:
        return os.path.join(self.tmp.name, name)

    def test_damaged_docx_is_e04001(self):
        path = self._path("broken.docx")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<broken>")
        with self.assertRaises(AtbError) as ctx:
            read_file(path)
        self.assertEqual(ctx.exception.code, "ATB-E04001")

    def test_corrupt_sqlite_is_e08002(self):
        path = self._path("broken.db")
        with open(path, "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\x00" * 200)
        with self.assertRaises(AtbError) as ctx:
            read_file(path)
        self.assertEqual(ctx.exception.code, "ATB-E08002")

    def test_bad_explicit_encoding_is_e03001(self):
        path = self._path("bad.txt")
        with open(path, "wb") as f:
            f.write(b"\xff\xff\xff")
        with self.assertRaises(AtbError) as ctx:
            read_file(path, ReadOptions(encoding="utf-8"))
        self.assertEqual(ctx.exception.code, "ATB-E03001")

    def test_encrypted_7z_requires_password(self):
        import py7zr

        src = self._path("inside.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("secret payload")
        archive = self._path("enc.7z")
        with py7zr.SevenZipFile(archive, "w", password="pw") as z:
            z.write(src, "inside.txt")

        with self.assertRaises(AtbError) as ctx:
            read_file(archive, ReadOptions(member="inside.txt"))
        self.assertEqual(ctx.exception.code, "ATB-E07002")

        doc = read_file(archive, ReadOptions(member="inside.txt", password="pw"))
        self.assertIn("secret payload", doc.text())

    @unittest.skipUnless(WINDOWS, "共享冲突仅 Windows")
    def test_locked_file_is_e01003(self):
        import ctypes
        from ctypes import wintypes

        path = self._path("locked.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("locked")

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        handle = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            self.skipTest("无法创建独占句柄")
        try:
            with self.assertRaises(AtbError) as ctx:
                read_file(path)
            self.assertEqual(ctx.exception.code, "ATB-E01003")
        finally:
            k32.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
