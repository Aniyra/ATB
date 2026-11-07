# -*- coding: utf-8 -*-
"""压缩包解析器：ZIP / TAR / GZ / BZ2 / XZ / 7Z / RAR / ISO 等。

默认只列目录 + 对单文件压缩流做文本预览，绝不自动解压到磁盘（避免 Zip Slip）。
指定 --member 可读取归档内某个条目的文本内容。
"""

import bz2
import gzip
import io
import lzma
import os
import struct
import tarfile
import zipfile

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import BaseReader, clean_text, decode_bytes, new_document

MAX_ENTRIES = 3000


class ArchiveReader(BaseReader):
    name = "archive"
    formats = ("archive",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        ext = (det.ext or det.subtype or "").lower()
        if ext in ("gz", "tgz", "bz2", "tbz2", "xz", "txz") or ext == "":
            return self._compressed_stream(path, det, opts)
        if ext in ("zip", "jar", "war", "ear", "apk", "aab", "whl", "nupkg", "vsix", "cbz"):
            return self._zip(path, det, opts)
        if ext in ("tar",):
            return self._tar(path, det, opts)
        if ext == "7z":
            return self._7z(path, det, opts)
        if ext in ("rar", "cbr"):
            return self._rar(path, det, opts)
        if ext == "iso":
            return self._iso(path, det, opts)
        if ext in ("cab", "arj", "lzh"):
            from .misc import BinaryReader
            return BinaryReader().read(path, det, opts)
        return self._compressed_stream(path, det, opts)

    # ── ZIP ─────────────────────────────────────────────────
    def _zip(self, path, det, opts):
        doc = new_document(path, det, f"archive.zip")
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise AtbError("ATB-E07001", f"{path} ({e})")
        with zf:
            infos = zf.infolist()
            doc.metadata.update({
                "entries": len(infos),
                "total_uncompressed": sum(i.file_size for i in infos),
                "total_compressed": sum(i.compress_size for i in infos),
                "compression_ratio": round(
                    sum(i.compress_size for i in infos) / max(sum(i.file_size for i in infos), 1), 3),
            })
            rows = []
            for info in infos[:MAX_ENTRIES]:
                rows.append([
                    info.filename, info.file_size, info.compress_size,
                    "dir" if info.is_dir() else "",
                    "encrypted" if info.flag_bits & 0x1 else "",
                ])
            doc.blocks.append(Block("table", "", meta={
                "header": ["name", "size", "compressed", "type", "flags"], "rows": rows}))
            if len(infos) > MAX_ENTRIES:
                doc.warnings.append(f"共 {len(infos)} 个条目，仅列出前 {MAX_ENTRIES} 个")
                doc.truncated = True

            if opts.member:
                doc.blocks.extend(_read_zip_member(zf, opts.member, doc, opts))
            elif len([i for i in infos if not i.is_dir()]) == 1:
                only = [i for i in infos if not i.is_dir()][0]
                if only.file_size <= min(opts.max_bytes, 20 * 1024 * 1024):
                    doc.blocks.extend(_read_zip_member(zf, only.filename, doc, opts))
        return doc

    # ── TAR ─────────────────────────────────────────────────
    def _tar(self, path, det, opts):
        doc = new_document(path, det, "archive.tar")
        try:
            with tarfile.open(path, "r:*") as tf:
                members = tf.getmembers()
                doc.metadata.update({
                    "entries": len(members),
                    "total_uncompressed": sum(m.size for m in members if m.isfile()),
                })
                rows = [[m.name, m.size, "dir" if m.isdir() else "file"] for m in members[:MAX_ENTRIES]]
                doc.blocks.append(Block("table", "", meta={
                    "header": ["name", "size", "type"], "rows": rows}))
                if opts.member:
                    f = tf.extractfile(opts.member)
                    if f is None:
                        doc.warnings.append(f"条目不存在：{opts.member}")
                    else:
                        doc.blocks.extend(_append_text_block(
                            doc, f.read(min(opts.max_bytes, 20 * 1024 * 1024)),
                            opts, f"tar:{opts.member}"))
        except tarfile.TarError as e:
            raise AtbError("ATB-E07001", f"{path} ({e})")
        return doc

    # ── 7Z ──────────────────────────────────────────────────
    def _7z(self, path, det, opts):
        doc = new_document(path, det, "archive.7z")
        try:
            import py7zr
        except ImportError:
            raise AtbError("ATB-E11001", "解析 7z 需要 py7zr：pip install py7zr")
        try:
            with py7zr.SevenZipFile(path, mode="r", password=opts.password) as z:
                infos = z.list()
                doc.metadata.update({
                    "entries": len(infos),
                    "total_uncompressed": sum(i.uncompressed or 0 for i in infos),
                    "encrypted": z.needs_password(),
                })
                rows = [[i.filename, i.uncompressed or 0, "dir" if i.is_directory else "file"]
                        for i in infos[:MAX_ENTRIES]]
                doc.blocks.append(Block("table", "", meta={
                    "header": ["name", "size", "type"], "rows": rows}))
                if opts.member:
                    data = z.read([opts.member])
                    if data and opts.member in data:
                        doc.blocks.extend(_append_text_block(
                            doc, data[opts.member].read(), opts,
                            f"7z:{opts.member}"))
        except AtbError:
            raise
        except Exception as e:
            if "password" in str(e).lower() or "encrypt" in str(e).lower():
                raise AtbError("ATB-E07002", f"{path}（请用 --password）")
            raise AtbError("ATB-E07001", f"{path} ({e})")
        return doc

    # ── RAR ─────────────────────────────────────────────────
    def _rar(self, path, det, opts):
        doc = new_document(path, det, "archive.rar")
        try:
            import rarfile
        except ImportError:
            raise AtbError("ATB-E11001", "解析 RAR 需要 rarfile：pip install rarfile")
        try:
            with rarfile.RarFile(path) as rf:
                infos = rf.infolist()
                doc.metadata.update({
                    "entries": len(infos),
                    "total_uncompressed": sum(i.file_size for i in infos),
                    "encrypted": any(i.needs_password() for i in infos),
                })
                rows = [[i.filename, i.file_size, "dir" if i.isdir() else "file"]
                        for i in infos[:MAX_ENTRIES]]
                doc.blocks.append(Block("table", "", meta={
                    "header": ["name", "size", "type"], "rows": rows}))
                need = any(i.needs_password() for i in infos)
                if need and not opts.password:
                    raise AtbError("ATB-E07002", f"{path} 已加密")
                if opts.member:
                    rf.setpassword(opts.password or None)
                    doc.blocks.extend(_append_text_block(
                        doc, rf.read(opts.member), opts, f"rar:{opts.member}"))
        except AtbError:
            raise
        except Exception as e:
            raise AtbError("ATB-E07001", f"{path} ({e})")
        return doc

    # ── ISO9660 ─────────────────────────────────────────────
    def _iso(self, path, det, opts):
        """只读解析 ISO9660 目录记录，不解压、不挂载。"""
        doc = new_document(path, det, "archive.iso")
        sector = 2048
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                pvd, lba = None, 16
                while lba * sector + sector <= size and lba < 32:
                    f.seek(lba * sector)
                    vd = f.read(sector)
                    if len(vd) < 7:
                        break
                    if vd[0] == 255:
                        break
                    if vd[0] == 1 and vd[1:6] == b"CD001":
                        pvd = vd
                        break
                    lba += 1
                if pvd is None:
                    raise AtbError("ATB-E07001",
                                   f"未找到 ISO9660 主卷描述符：{path}")

                label = pvd[40:72].decode("latin-1", "ignore").strip()
                root = pvd[156:190]
                root_lba = struct.unpack("<I", root[2:6])[0]
                root_len = struct.unpack("<I", root[10:14])[0]

                rows, files, total, seen = [], {}, 0, set()
                stack = [("", root_lba, root_len)]
                while stack and total < MAX_ENTRIES:
                    prefix, extent, length = stack.pop()
                    if extent in seen:
                        continue
                    seen.add(extent)
                    f.seek(extent * sector)
                    data = f.read(length)
                    pos = 0
                    while pos < len(data) and total < MAX_ENTRIES:
                        rec_len = data[pos]
                        if rec_len == 0:
                            pos = (pos // sector + 1) * sector
                            continue
                        rec = data[pos:pos + rec_len]
                        pos += rec_len
                        if len(rec) < 33:
                            continue
                        name_len = rec[32]
                        raw_name = rec[33:33 + name_len]
                        if not raw_name:
                            continue
                        name = raw_name.decode("latin-1", "replace")
                        if name in ("\x00", "\x01"):
                            continue
                        if name.endswith(";1"):
                            name = name[:-2]
                        full = prefix + name
                        ext_lba = struct.unpack("<I", rec[2:6])[0]
                        ext_len = struct.unpack("<I", rec[10:14])[0]
                        is_dir = bool(rec[25] & 0x02)
                        if is_dir:
                            rows.append([full + "/", ext_len, "dir"])
                            stack.append((full + "/", ext_lba, ext_len))
                        else:
                            rows.append([full, ext_len, "file"])
                            files[full] = (ext_lba, ext_len)
                        total += 1

                doc.metadata.update({
                    "entries": total,
                    "filesystem": "ISO9660",
                    "volume_label": label,
                    "volume_bytes": size,
                })
                doc.blocks.append(Block("table", "", meta={
                    "header": ["name", "size", "type"], "rows": rows}))
                if total >= MAX_ENTRIES:
                    doc.warnings.append(f"共 {total}+ 个条目，仅列出前 {MAX_ENTRIES} 个")

                member = (opts.member or "").lstrip("/")
                if member:
                    target = next((k for k in files if k.lower() == member.lower()), None)
                    if target is None:
                        doc.warnings.append(f"条目不存在：{opts.member}")
                    else:
                        ext_lba, ext_len = files[target]
                        f.seek(ext_lba * sector)
                        doc.blocks.extend(_append_text_block(
                            doc,
                            f.read(min(ext_len, opts.max_bytes, 20 * 1024 * 1024)),
                            opts, f"iso:{target}"))
        except AtbError:
            raise
        except Exception as e:
            raise AtbError("ATB-E07001", f"{path} ({e})")
        return doc

    # ── 单文件压缩流（gz/bz2/xz）────────────────────────────
    def _compressed_stream(self, path, det, opts):
        doc = new_document(path, det, "archive.stream")
        with open(path, "rb") as f:
            raw = f.read(min(opts.max_bytes + 1024, 64 * 1024 * 1024))
        name = os.path.basename(path)
        try:
            if det.ext in ("bz2", "tbz2"):
                data = bz2.decompress(raw)
            elif det.ext in ("xz", "txz"):
                data = lzma.decompress(raw)
            else:
                data = gzip.decompress(raw)
                if len(raw) > 10 and raw[:2] == b"\x1f\x8b":
                    # gzip 头里的原始文件名
                    flags = raw[3]
                    if flags & 0x08:
                        nul = raw.find(b"\x00", 10)
                        if nul > 10:
                            name = raw[10:nul].decode("latin-1", "ignore")
        except (OSError, EOFError, lzma.LZMAError) as e:
            raise AtbError("ATB-E07001", f"{path} 解压失败：{e}（可能被截断或不是该格式）")
        doc.metadata.update({
            "original_name": name,
            "compressed_size": len(raw),
            "uncompressed_size": len(data),
            "ratio": round(len(raw) / max(len(data), 1), 3),
        })
        # gz/bz2/xz 里常见 tar 包：认出 ustar 就按 tar 展开目录
        if len(data) > 262 and data[257:262] == b"ustar":
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
                    members = tf.getmembers()
                    doc.handler = "archive.tar-stream"
                    doc.metadata["inner_tar_entries"] = len(members)
                    rows = [[m.name, m.size, "dir" if m.isdir() else "file"]
                            for m in members[:MAX_ENTRIES]]
                    doc.blocks.append(Block("table", "", meta={
                        "header": ["name", "size", "type"], "rows": rows}))
                    if opts.member:
                        f = tf.extractfile(opts.member)
                        if f:
                            doc.blocks.extend(_append_text_block(
                                doc, f.read(min(opts.max_bytes, 20 * 1024 * 1024)),
                                opts, f"tar:{opts.member}"))
                return doc
            except tarfile.TarError:
                pass
        doc.blocks.append(Block("kv", f"内部文件：{name}", meta=doc.metadata))
        doc.blocks.extend(_append_text_block(doc, data, opts, name))
        return doc


def _read_zip_member(zf: zipfile.ZipFile, member: str, doc: Document, opts: ReadOptions):
    try:
        info = zf.getinfo(member)
    except KeyError:
        doc.warnings.append(f"条目不存在：{member}")
        return []
    if info.flag_bits & 0x1:
        if not opts.password:
            doc.warnings.append(f"条目已加密：{member}")
            return []
        data = zf.read(info, pwd=opts.password.encode())
    else:
        data = zf.read(info)
    return _append_text_block(doc, data, opts, f"zip:{member}")


def _append_text_block(doc: Document, data: bytes, opts: ReadOptions, label: str):
    if not data:
        return []
    text, enc, warns = decode_bytes(data[: opts.max_chars or len(data)], opts.encoding)
    bad = text.count("\ufffd")
    if text and bad / len(text) > 0.15:
        doc.warnings.append(f"{label} 疑似二进制，仅返回头部十六进制")
        preview = data[: min(opts.preview_bytes, 512)]
        return [Block("raw", preview.hex(" "), meta={"encoding": "hex", "source": label})]
    doc.warnings.extend(warns)
    lim = opts.max_chars or len(text)
    return [Block("code", clean_text(text[:lim]),
                  meta={"source": label, "encoding": enc})]
