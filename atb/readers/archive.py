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
        if ext in ("iso", "cab", "arj", "lzh"):
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
                        _append_text_block(doc, f.read(min(opts.max_bytes, 20 * 1024 * 1024)),
                                           opts, f"tar:{opts.member}")
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
                        _append_text_block(doc, data[opts.member].read(), opts,
                                           f"7z:{opts.member}")
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
                    _append_text_block(doc, rf.read(opts.member), opts, f"rar:{opts.member}")
        except AtbError:
            raise
        except Exception as e:
            raise AtbError("ATB-E07001", f"{path} ({e})")
        return doc

    # ── 单文件压缩流（gz/bz2/xz）────────────────────────────
    def _compressed_stream(self, path, det, opts):
        doc = new_document(path, det, "archive.stream")
        raw = open(path, "rb").read(min(opts.max_bytes + 1024, 64 * 1024 * 1024))
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
                            _append_text_block(doc, f.read(min(opts.max_bytes, 20 * 1024 * 1024)),
                                               opts, f"tar:{opts.member}")
                return doc
            except tarfile.TarError:
                pass
        doc.blocks.append(Block("kv", f"内部文件：{name}", meta=doc.metadata))
        _append_text_block(doc, data, opts, name)
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
