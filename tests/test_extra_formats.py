# -*- coding: utf-8 -*-
"""README 声称但此前未完整实现的格式：mbox / HEIC / WOFF / WOFF2 / ISO9660。"""

import io
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.dispatch import read_file  # noqa: E402
from atb.models import ReadOptions  # noqa: E402


# ── 构造工具：name 表 / WOFF / WOFF2 / ISO9660 ───────────────
def name_table(family: str = "ATB Test", subfamily: str = "Regular",
               version: str = "Version 1.0") -> bytes:
    entries = [(1, family), (2, subfamily), (4, family), (5, version), (6, family)]
    string_offset = 6 + 12 * len(entries)
    offset, records, strings = 0, b"", b""
    for name_id, text in entries:
        data = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(data), offset)
        strings += data
        offset += len(data)
    return struct.pack(">HHH", 0, len(entries), string_offset) + records + strings


def woff1(family: str) -> bytes:
    table = name_table(family)
    comp = zlib.compress(table)
    header = bytearray(b"wOFF")
    header += struct.pack(">I", 0x00010000)
    header += struct.pack(">I", 0)              # length（稍后回填）
    header += struct.pack(">HH", 1, 0)          # numTables, reserved
    header += struct.pack(">I", 12 + 16 + len(table))
    header += struct.pack(">HH", 1, 0)          # major, minor
    header += struct.pack(">IIIII", 0, 0, 0, 0, 0)
    offset = len(header) + 20
    directory = b"name" + struct.pack(">IIII", offset, len(comp), len(table), 0)
    data = header + directory + comp
    data[8:12] = struct.pack(">I", len(data))
    return bytes(data)


def base128(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def woff2(family: str) -> bytes:
    try:
        import brotli
    except ImportError:  # pragma: no cover
        raise unittest.SkipTest("需要 brotli 才能构造 WOFF2 样本")
    table = name_table(family)
    comp = brotli.compress(table)
    header = bytearray(b"wOF2")
    header += struct.pack(">I", 0x00010000)
    header += struct.pack(">I", 0)
    header += struct.pack(">HH", 1, 0)
    header += struct.pack(">I", 12 + 16 + len(table))
    header += struct.pack(">I", len(comp))
    header += struct.pack(">HH", 1, 0)
    header += struct.pack(">IIIII", 0, 0, 0, 0, 0)
    directory = bytes([5]) + base128(len(table))   # tagIndex 5 = name
    data = header + directory + comp
    data[8:12] = struct.pack(">I", len(data))
    return bytes(data)


def iso_record(name: bytes, lba: int, length: int, is_dir: bool) -> bytes:
    rec = bytearray(b"\x00\x00")
    rec += struct.pack("<I", lba) + struct.pack(">I", lba)
    rec += struct.pack("<I", length) + struct.pack(">I", length)
    rec += b"\x00" * 7
    rec.append(0x02 if is_dir else 0x00)
    rec += b"\x00\x00"
    rec += struct.pack("<H", 1) + struct.pack(">H", 1)
    rec.append(len(name))
    rec += name
    if len(rec) % 2:
        rec.append(0)
    rec[0] = len(rec)
    return bytes(rec)


def make_iso(path: str) -> bytes:
    sector = 2048
    img = bytearray(sector * 22)
    pvd = bytearray(sector)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[40:72] = b"ATB_TEST".ljust(32)
    pvd[80:88] = struct.pack("<I", 22) + struct.pack(">I", 22)
    root = iso_record(b"\x00", 20, sector, True)
    pvd[156:156 + len(root)] = root
    img[16 * sector:17 * sector] = pvd
    term = bytearray(sector)
    term[0] = 255
    term[1:6] = b"CD001"
    term[6] = 1
    img[17 * sector:18 * sector] = term
    content = b"hello atb!\n"
    directory = bytearray()
    directory += iso_record(b"\x00", 20, sector, True)
    directory += iso_record(b"\x01", 20, sector, True)
    directory += iso_record(b"HELLO.TXT;1", 21, len(content), False)
    img[20 * sector:20 * sector + len(directory)] = directory
    img[21 * sector:21 * sector + len(content)] = content
    with open(path, "wb") as f:
        f.write(img)
    return content


class ExtraFormatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _path(self, name: str) -> str:
        return os.path.join(self.tmp.name, name)

    def test_mbox_parses_all_messages(self):
        path = self._path("box.mbox")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(3):
                f.write(f"From sender{i}@example.com Thu Jan  1 00:00:00 2026\n")
                f.write(f"Subject: mbox {i}\nFrom: a{i}@x.com\nTo: b@y.com\n")
                f.write("Date: Thu, 01 Jan 2026 00:00:00 +0000\n\n")
                f.write(f"Body {i}\n\n")
        doc = read_file(path)
        self.assertEqual(doc.format, "email")
        self.assertEqual(doc.metadata["messages"], 3)
        self.assertIn("mbox 1", doc.metadata["subjects"])
        text = doc.text()
        self.assertIn("Body 0", text)
        self.assertIn("Body 2", text)

    def test_heic_image(self):
        try:
            import pillow_heif
            from PIL import Image
        except ImportError:  # pragma: no cover
            self.skipTest("需要 pillow-heif")
        pillow_heif.register_heif_opener()
        path = self._path("test.heic")
        Image.new("RGB", (64, 48), (20, 140, 200)).save(path, format="HEIF")
        doc = read_file(path)
        self.assertEqual(doc.format, "image")
        self.assertEqual(doc.metadata["width"], 64)
        self.assertEqual(doc.metadata["height"], 48)

    def test_woff1_name_table(self):
        path = self._path("font.woff")
        with open(path, "wb") as f:
            f.write(woff1("ATB Test"))
        doc = read_file(path)
        self.assertEqual(doc.format, "font")
        self.assertEqual(doc.metadata["container"], "woff")
        self.assertEqual(doc.metadata["family"], "ATB Test")

    def test_woff2_name_table(self):
        path = self._path("font.woff2")
        try:
            data = woff2("ATB WOFF2")
        except unittest.SkipTest:
            self.skipTest("需要 brotli")
        with open(path, "wb") as f:
            f.write(data)
        doc = read_file(path)
        self.assertEqual(doc.format, "font")
        self.assertEqual(doc.metadata["container"], "woff2")
        self.assertEqual(doc.metadata["family"], "ATB WOFF2")

    def test_iso9660_listing_and_member(self):
        path = self._path("disc.iso")
        content = make_iso(path)
        doc = read_file(path)
        self.assertEqual(doc.format, "archive")
        self.assertEqual(doc.metadata["filesystem"], "ISO9660")
        self.assertEqual(doc.metadata["volume_label"], "ATB_TEST")
        self.assertIn("HELLO.TXT", doc.text())

        doc2 = read_file(path, ReadOptions(member="HELLO.TXT"))
        self.assertIn(content.decode("utf-8").strip(), doc2.text())

    def test_tar_member_content(self):
        import tarfile

        path = self._path("a.tar")
        payload = b"tar member text"
        with tarfile.open(path, "w") as tf:
            info = tarfile.TarInfo("inside.txt")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        doc = read_file(path, ReadOptions(member="inside.txt"))
        self.assertIn("tar member text", doc.text())

    def test_gzip_stream_preview(self):
        import gzip

        path = self._path("a.txt.gz")
        with gzip.open(path, "wb") as f:
            f.write(b"gzip content here")
        doc = read_file(path)
        self.assertIn("gzip content here", doc.text())


if __name__ == "__main__":
    unittest.main()
