# -*- coding: utf-8 -*-
"""图片 / 音频 / 视频 / 字体 / PE 可执行文件解析器。"""

import json
import os
import shutil
import struct
import subprocess
from typing import Optional

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import BaseReader, clean_text, new_document


class ImageReader(BaseReader):
    name = "image"
    formats = ("image",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "image.pillow")
        try:
            from PIL import Image, ExifTags
        except ImportError:
            raise AtbError("ATB-E11001", "解析图片需要 Pillow：pip install pillow")
        try:
            with Image.open(path) as im:
                w, h = im.size
                doc.metadata.update({
                    "format": im.format, "mode": im.mode, "width": w, "height": h,
                    "frames": getattr(im, "n_frames", 1),
                    "megapixels": round(w * h / 1_000_000, 2),
                    "animated": getattr(im, "is_animated", False),
                    "info": {k: str(v) for k, v in im.info.items()
                             if k in ("dpi", "gamma", "progressive", "transparency")},
                })
                exif = {}
                try:
                    raw_exif = im.getexif()
                    for k, v in raw_exif.items():
                        exif[ExifTags.TAGS.get(k, str(k))] = str(v)
                except Exception:
                    pass
                if exif:
                    doc.metadata["exif"] = {k: v for k, v in list(exif.items())[:60]}
                doc.blocks.append(Block("image", f"图片 {w}×{h} {im.format}",
                                        meta={"width": w, "height": h,
                                              "format": im.format}))
                if opts.ocr:
                    doc.blocks.extend(_ocr(path, doc))
        except AtbError:
            raise
        except Exception as e:
            raise AtbError("ATB-E06001", f"{path} ({e})")
        return doc


class MediaReader(BaseReader):
    name = "media"
    formats = ("audio",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, f"media.{det.format}")
        info = _ffprobe(path)
        if info:
            doc.metadata["ffprobe"] = info
            doc.handler = "media.ffprobe"
        else:
            try:
                import mutagen
                mf = mutagen.File(path)
                if mf is not None:
                    doc.metadata.update({
                        "duration": round(getattr(mf.info, "length", 0), 2),
                        "bitrate": getattr(mf.info, "bitrate", None),
                        "sample_rate": getattr(mf.info, "sample_rate", None),
                        "channels": getattr(mf.info, "channels", None),
                        "tags": {k: str(v)[:500] for k, v in (mf.tags or {}).items()}
                        if mf.tags else {},
                    })
                    doc.handler = "media.mutagen"
            except ImportError:
                doc.warnings.append("未安装 mutagen / ffprobe，仅返回文件级信息")
            except Exception as e:
                doc.warnings.append(f"元数据读取失败：{e}")

        title = doc.metadata.get("tags", {}).get("title") if isinstance(doc.metadata.get("tags"), dict) else None
        doc.blocks.append(Block("heading", title or os.path.basename(path), level=1))
        doc.blocks.append(Block("kv", "", meta={"metadata": _public_meta(doc.metadata)}))
        return doc


class FontReader(BaseReader):
    name = "font"
    formats = ("font",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "font.sfnt")
        try:
            with open(path, "rb") as f:
                data = f.read(min(os.path.getsize(path), 4 * 1024 * 1024))
            doc.metadata.update(_sfnt_info(data))
        except Exception as e:
            doc.warnings.append(f"字体结构解析失败：{e}")
        name = doc.metadata.get("family") or os.path.basename(path)
        doc.blocks.append(Block("heading", str(name), level=1))
        doc.blocks.append(Block("kv", "", meta={"metadata": _public_meta(doc.metadata)}))
        return doc


class PeReader(BaseReader):
    name = "pe"
    formats = ("pe",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "pe.header")
        try:
            with open(path, "rb") as f:
                data = f.read(min(os.path.getsize(path), 8 * 1024 * 1024))
            doc.metadata.update(_pe_info(data))
        except Exception as e:
            doc.warnings.append(f"PE 头解析失败：{e}")
        doc.blocks.append(Block("heading", os.path.basename(path), level=1))
        doc.blocks.append(Block("kv", "", meta={"metadata": _public_meta(doc.metadata)}))
        return doc


# ── 辅助 ─────────────────────────────────────────────────────
def _public_meta(meta: dict) -> dict:
    out = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, dict)):
            s = json.dumps(v, ensure_ascii=False, default=str)
            out[k] = s[:1500]
    return out


def _ocr(path: str, doc: Document):
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")
        doc.metadata["ocr"] = True
        return [Block("paragraph", clean_text(t)) for t in text.split("\n") if t.strip()]
    except ImportError:
        doc.warnings.append("OCR 需要 pytesseract 与 Tesseract 可执行文件")
    except Exception as e:
        doc.warnings.append(f"OCR 失败：{e}")
    return []


def _ffprobe(path: str) -> Optional[dict]:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout.decode("utf-8", "replace"))
        fmt = data.get("format", {})
        streams = []
        for s in data.get("streams", [])[:10]:
            streams.append({k: s.get(k) for k in
                            ("codec_type", "codec_name", "width", "height",
                             "sample_rate", "channels", "bit_rate", "duration")
                            if s.get(k) is not None})
        return {
            "duration": float(fmt.get("duration") or 0),
            "bit_rate": fmt.get("bit_rate"),
            "format_name": fmt.get("format_name"),
            "size": fmt.get("size"),
            "tags": {k: v for k, v in (fmt.get("tags") or {}).items()},
            "streams": streams,
        }
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def _sfnt_info(data: bytes) -> dict:
    info = {"sfnt_version": data[:4].decode("latin-1", "ignore")}
    num_tables = struct.unpack(">H", data[4:6])[0] if len(data) >= 6 else 0
    tables = {}
    for i in range(min(num_tables, 64)):
        off = 12 + i * 16
        if off + 16 > len(data):
            break
        tag = data[off:off + 4].decode("latin-1", "ignore")
        toff, tlen = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag] = (toff, tlen)
    info["tables"] = sorted(tables)
    # name 表：取 family / subfamily / version
    if "name" in tables:
        off, length = tables["name"]
        names = _parse_name_table(data[off:off + length])
        info.update({k: v for k, v in names.items() if v})
    return info


def _parse_name_table(blob: bytes) -> dict:
    out = {}
    if len(blob) < 6:
        return out
    count, str_offset = struct.unpack(">HH", blob[4:8])
    for i in range(min(count, 64)):
        rec = blob[6 + i * 12: 18 + i * 12]
        if len(rec) < 12:
            break
        platform, enc, lang, name_id, length, off = struct.unpack(">HHHHHH", rec)
        raw = blob[str_offset + off: str_offset + off + length]
        try:
            text = raw.decode("utf-16-be") if platform == 3 else raw.decode("latin-1")
        except Exception:
            continue
        key = {1: "family", 2: "subfamily", 4: "full_name", 5: "version", 6: "postscript"}.get(name_id)
        if key and key not in out:
            out[key] = text.strip("\x00 ").strip()
    return out


def _pe_info(data: bytes) -> dict:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("不是 PE 文件")
    e_lfanew = struct.unpack("<I", data[0x3C:0x40])[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("缺少 PE 签名")
    machine, sections, timestamp = struct.unpack("<HH I", data[e_lfanew + 4: e_lfanew + 12])
    opt_size = struct.unpack("<H", data[e_lfanew + 20: e_lfanew + 22])[0]
    characteristics = struct.unpack("<H", data[e_lfanew + 22: e_lfanew + 24])[0]
    machine_map = {0x14c: "x86", 0x8664: "x64", 0xaa64: "ARM64", 0x1c0: "ARM"}
    import datetime
    res = {
        "machine": machine_map.get(machine, hex(machine)),
        "sections": sections,
        "timestamp": datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
        .strftime("%Y-%m-%d %H:%M:%S UTC") if 0 < timestamp < 4102444800 else str(timestamp),
        "characteristics": hex(characteristics),
        "is_dll": bool(characteristics & 0x2000),
        "is_exe": bool(characteristics & 0x0002),
    }
    sec_off = e_lfanew + 24 + opt_size
    names = []
    for i in range(min(sections, 96)):
        off = sec_off + i * 40
        if off + 8 > len(data):
            break
        names.append(data[off:off + 8].rstrip(b"\x00").decode("latin-1", "ignore"))
    res["section_names"] = names
    return res
