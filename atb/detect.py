# -*- coding: utf-8 -*-
"""文件格式探测：扩展名映射 + 魔术字节（magic bytes）双重判定。

优先级：强制格式 > 魔术字节 > 扩展名 > 内容启发式。
即使文件被改错扩展名（例如把 .pdf 改成 .txt），也能正确识别。
"""

import ctypes
import os
import sys
import zipfile
from dataclasses import dataclass
from typing import Optional

from .errors import AtbError

# ── 扩展名 → 格式 ────────────────────────────────────────────
EXT_MAP = {
    # 纯文本
    **{e: "text" for e in (
        "txt", "text", "md", "markdown", "mdx", "rst", "log", "ini", "cfg", "conf",
        "env", "properties", "gitignore", "editorconfig", "license", "readme",
    )},
    # 源码
    **{e: "code" for e in (
        "py", "pyw", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "c", "h",
        "cc", "cpp", "cxx", "hpp", "hh", "cs", "go", "rs", "rb", "php", "swift", "kt",
        "kts", "scala", "sh", "bash", "zsh", "fish", "ps1", "psm1", "bat", "cmd", "sql",
        "lua", "r", "m", "mm", "pl", "pm", "dart", "vue", "svelte", "astro", "css",
        "scss", "sass", "less", "styl", "gradle", "cmake", "mk", "dockerfile",
        "makefile", "asm", "s", "v", "sv", "vhd", "vhdl", "tex", "bib", "proto",
    )},
    # 网页 / 标记
    **{e: "html" for e in ("html", "htm", "xhtml", "shtml", "xml", "xsl", "xslt",
                           "rss", "atom", "svg", "plist")},
    # 结构化数据
    **{e: "data" for e in ("json", "jsonl", "ndjson", "json5", "geojson", "yaml",
                           "yml", "toml", "csv", "tsv", "psv", "sqlite", "sqlite3",
                           "db", "db3", "parquet", "arrow", "feather", "ipynb")},
    # Office / OpenDocument
    **{e: "office" for e in ("docx", "docm", "dotx", "xlsx", "xlsm", "xltx", "xltm",
                             "pptx", "pptm", "potx", "odt", "ods", "odp", "rtf",
                             "doc", "xls", "ppt", "wps", "et", "dps")},
    "pdf": "pdf",
    # 图片
    **{e: "image" for e in ("png", "jpg", "jpeg", "jpe", "gif", "bmp", "webp", "tif",
                            "tiff", "ico", "heic", "heif", "avif", "psd", "icns",
                            "jfif", "jxl")},
    # 音频
    **{e: "audio" for e in ("mp3", "flac", "wav", "m4a", "aac", "ogg", "oga", "opus",
                            "wma", "aiff", "aif", "ape", "wv", "mid", "midi", "amr")},
    # 视频
    **{e: "video" for e in ("mp4", "m4v", "mkv", "avi", "mov", "webm", "flv", "wmv",
                            "mpg", "mpeg", "ts", "m2ts", "3gp", "3g2", "rmvb", "rm",
                            "vob", "ogv")},
    # 压缩包
    **{e: "archive" for e in ("zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
                              "7z", "rar", "jar", "war", "ear", "apk", "aab", "whl",
                              "egg", "nupkg", "vsix", "iso", "cab", "arj", "lzh")},
    # 电子书
    **{e: "ebook" for e in ("epub", "mobi", "azw", "azw3", "fb2", "cbz", "cbr", "djvu")},
    # 字幕 / 邮件 / 日历 / 联系人
    **{e: "subtitle" for e in ("srt", "vtt", "ass", "ssa", "sub", "lrc")},
    **{e: "email" for e in ("eml", "msg", "mbox")},
    **{e: "calendar" for e in ("ics", "vcf")},
    # 字体 / 可执行 / 其他二进制
    **{e: "font" for e in ("ttf", "otf", "woff", "woff2", "eot")},
    **{e: "pe" for e in ("exe", "dll", "sys", "msi", "scr", "cpl", "ocx")},
    **{e: "binary" for e in ("bin", "dat", "elf", "so", "dylib", "wasm", "class",
                             "pyc", "pyd", "o", "a", "lib", "obj", "dex", "img")},
}

# 格式 → MIME
FORMAT_MIME = {
    "text": "text/plain", "code": "text/plain", "html": "text/html",
    "data": "application/json", "office": "application/vnd.openxmlformats-officedocument",
    "pdf": "application/pdf", "image": "image/*", "audio": "audio/*",
    "video": "video/*", "archive": "application/zip", "ebook": "application/epub+zip",
    "subtitle": "text/vtt", "email": "message/rfc822", "calendar": "text/calendar",
    "font": "font/ttf", "pe": "application/vnd.microsoft.portable-executable",
    "binary": "application/octet-stream",
}

# 具体 MIME（按扩展名，优先于上面的粗粒度映射）
EXT_MIME = {
    "pdf": "application/pdf", "zip": "application/zip", "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar", "tar": "application/x-tar", "gz": "application/gzip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword", "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint", "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip", "json": "application/json", "csv": "text/csv",
    "html": "text/html", "xml": "application/xml", "md": "text/markdown",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "svg": "image/svg+xml", "tif": "image/tiff", "tiff": "image/tiff",
    "heic": "image/heic", "bmp": "image/bmp", "ico": "image/x-icon",
    "mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav", "ogg": "audio/ogg",
    "m4a": "audio/mp4", "mp4": "video/mp4", "mkv": "video/x-matroska",
    "webm": "video/webm", "avi": "video/x-msvideo", "mov": "video/quicktime",
    "sqlite": "application/vnd.sqlite3", "db": "application/vnd.sqlite3",
    "parquet": "application/vnd.apache.parquet", "ipynb": "application/x-ipynb+json",
    "eml": "message/rfc822", "msg": "application/vnd.ms-outlook",
    "ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2",
    "exe": "application/vnd.microsoft.portable-executable",
}

ZIP_FAMILY = {"docx", "xlsx", "pptx", "docm", "xlsm", "pptm", "odt", "ods", "odp",
              "epub", "jar", "war", "apk", "aab", "whl", "nupkg", "vsix", "cbz"}

TEXT_FORMATS = {"text", "code", "data", "html", "subtitle", "calendar", "email"}


@dataclass
class Detection:
    format: str
    mime: str
    confidence: float
    evidence: str
    ext: str = ""
    subtype: Optional[str] = None   # 例如 zip 家族里的 docx / apk / epub

    def to_dict(self) -> dict:
        return {
            "format": self.format, "mime": self.mime, "confidence": self.confidence,
            "evidence": self.evidence, "ext": self.ext, "subtype": self.subtype,
        }


def _ext_of(path: str) -> str:
    name = os.path.basename(path).lower()
    if "." not in name:
        # 无扩展名的常见文本文件名
        if name in {"dockerfile", "makefile", "license", "readme", "changelog"}:
            return {"dockerfile": "dockerfile", "makefile": "makefile"}.get(name, "txt")
        return ""
    return name.rsplit(".", 1)[1]


def is_sharing_violation(exc: BaseException) -> bool:
    """Windows 共享冲突（WinError 32）会以 PermissionError 形式抛出。"""
    winerr = getattr(exc, "winerror", None)
    if winerr is None and sys.platform == "win32":
        try:
            winerr = ctypes.windll.kernel32.GetLastError()
        except Exception:
            winerr = None
    return winerr == 32


def _read_head(path: str, n: int = 4096) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except PermissionError as e:
        raise AtbError("ATB-E01003" if is_sharing_violation(e) else "ATB-E01002",
                       f"{path} ({e})")
    except FileNotFoundError:
        raise AtbError("ATB-E01001", path)
    except OSError as e:
        raise AtbError("ATB-E01003", f"{path} ({e})")


def classify_zip(path: str) -> Detection:
    """判断 ZIP 容器到底属于哪种格式（docx/xlsx/pptx/apk/epub/jar/普通 zip）。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            lower = {n.lower() for n in names}
            if "mimetype" in lower:
                try:
                    mt = z.read("mimetype").decode("ascii", "ignore").strip()
                    if "epub" in mt:
                        return Detection("ebook", "application/epub+zip", 0.99, "zip:mimetype=epub+zip", "epub", "epub")
                    if "opendocument.text" in mt:
                        return Detection("office", "application/vnd.oasis.opendocument.text", 0.99, "zip:mimetype=odt", "odt", "odt")
                    if "opendocument.spreadsheet" in mt:
                        return Detection("office", "application/vnd.oasis.opendocument.spreadsheet", 0.99, "zip:mimetype=ods", "ods", "ods")
                    if "opendocument.presentation" in mt:
                        return Detection("office", "application/vnd.oasis.opendocument.presentation", 0.99, "zip:mimetype=odp", "odp", "odp")
                except Exception:
                    pass
            if "[content_types].xml" in lower:
                if any(n.startswith("word/") for n in lower):
                    return Detection("office", EXT_MIME["docx"], 0.98, "zip:word/", "docx", "docx")
                if any(n.startswith("xl/") for n in lower):
                    return Detection("office", EXT_MIME["xlsx"], 0.98, "zip:xl/", "xlsx", "xlsx")
                if any(n.startswith("ppt/") for n in lower):
                    return Detection("office", EXT_MIME["pptx"], 0.98, "zip:ppt/", "pptx", "pptx")
            if "androidmanifest.xml" in lower and "classes.dex" in lower:
                return Detection("archive", "application/vnd.android.package-archive", 0.97, "zip:AndroidManifest+classes.dex", "apk", "apk")
            if "meta-inf/manifest.mf" in lower:
                return Detection("archive", "application/java-archive", 0.9, "zip:META-INF/MANIFEST.MF", "jar", "jar")
            if any(n.endswith(".dist-info/metadata") for n in lower):
                return Detection("archive", "application/zip", 0.9, "zip:dist-info", "whl", "whl")
            return Detection("archive", "application/zip", 0.95, "zip:PK", "zip", "zip")
    except zipfile.BadZipFile:
        return Detection("archive", "application/zip", 0.6, "zip:bad", "zip", "zip")


def _detect_ole(path: str) -> Optional[Detection]:
    """识别 OLE 复合文档（旧版 doc/xls/ppt/msg）。"""
    try:
        import olefile
        if not olefile.isOleFile(path):
            return None
        with olefile.OleFileIO(path) as ole:
            streams = {s[0].lower() for s in ole.listdir()}
        if "worddocument" in streams:
            return Detection("office", EXT_MIME["doc"], 0.95, "ole:WordDocument", "doc", "doc")
        if "workbook" in streams or "book" in streams:
            return Detection("office", EXT_MIME["xls"], 0.95, "ole:Workbook", "xls", "xls")
        if "powerpoint document" in streams:
            return Detection("office", EXT_MIME["ppt"], 0.95, "ole:PowerPoint Document", "ppt", "ppt")
        if any(s.startswith("__substg1.0_") for s in streams):
            return Detection("email", EXT_MIME["msg"], 0.9, "ole:__substg1.0_", "msg", "msg")
        return Detection("binary", "application/x-ole-storage", 0.8, "ole", "bin", "ole")
    except ImportError:
        return Detection("binary", "application/x-ole-storage", 0.7, "ole:magic", "bin", "ole")
    except Exception:
        return None


def _magic(head: bytes, path: str) -> Optional[Detection]:
    """按魔术字节判断格式。"""
    if len(head) < 4:
        return None
    h4, h8 = head[:4], head[:8]

    if h4 == b"%PDF":
        return Detection("pdf", "application/pdf", 1.0, "magic:%PDF", "pdf")
    if h8.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detection("image", "image/png", 1.0, "magic:PNG", "png")
    if h4[:3] == b"\xff\xd8\xff":
        return Detection("image", "image/jpeg", 1.0, "magic:JPEG", "jpg")
    if h4 in (b"GIF8",):
        return Detection("image", "image/gif", 1.0, "magic:GIF", "gif")
    if h4 == b"RIFF" and head[8:12] == b"WEBP":
        return Detection("image", "image/webp", 1.0, "magic:RIFF WEBP", "webp")
    if h4 == b"RIFF" and head[8:12] == b"WAVE":
        return Detection("audio", "audio/wav", 1.0, "magic:RIFF WAVE", "wav")
    if h4 == b"RIFF" and head[8:12] == b"AVI ":
        return Detection("video", "video/x-msvideo", 1.0, "magic:RIFF AVI", "avi")
    if h4 in (b"II*\x00", b"MM\x00*"):
        return Detection("image", "image/tiff", 1.0, "magic:TIFF", "tif")
    if h4 == b"BM\x00\x00" or (h4[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00"
                                and len(head) > 26):
        return Detection("image", "image/bmp", 0.9, "magic:BM", "bmp")
    if h8 == b"\x00\x00\x01\x00":
        return Detection("image", "image/x-icon", 0.9, "magic:ICO", "ico")
    if h4 == b"8BPS":
        return Detection("image", "image/vnd.adobe.photoshop", 0.95, "magic:PSD", "psd")
    if h4 == b"ORM:" or h4 == b"IIRO":
        return Detection("image", "image/x-raw", 0.5, "magic:RAW", "raw")

    if h4[:2] == b"PK" and h4[2:] in (b"\x03\x04", b"\x05\x06", b"\x07\x08"):
        return classify_zip(path)
    if h4 == b"Rar!":
        return Detection("archive", "application/vnd.rar", 1.0, "magic:Rar!", "rar")
    if h4 == b"7z\xbc\xaf":
        return Detection("archive", "application/x-7z-compressed", 1.0, "magic:7z", "7z")
    if h4[:2] == b"\x1f\x8b":
        return Detection("archive", "application/gzip", 1.0, "magic:GZIP", "gz")
    if h4[:3] == b"BZh":
        return Detection("archive", "application/x-bzip2", 1.0, "magic:BZIP2", "bz2")
    if h4 == b"\xfd7zX":
        return Detection("archive", "application/x-xz", 1.0, "magic:XZ", "xz")
    if head[257:262] == b"ustar":
        return Detection("archive", "application/x-tar", 1.0, "magic:ustar", "tar")

    if head.startswith(b"SQLite format 3\x00"):
        return Detection("data", "application/vnd.sqlite3", 1.0, "magic:SQLite", "sqlite", "sqlite")
    if head.startswith(b"PAR1"):
        return Detection("data", "application/vnd.apache.parquet", 1.0, "magic:PAR1", "parquet", "parquet")

    if h4 == b"\xd0\xcf\x11\xe0":
        return _detect_ole(path) or Detection("binary", "application/x-ole-storage", 0.9, "magic:OLE", "bin", "ole")

    if h4 == b"fLaC":
        return Detection("audio", "audio/flac", 1.0, "magic:FLAC", "flac")
    if h4 == b"OggS":
        return Detection("audio", "audio/ogg", 1.0, "magic:OggS", "ogg")
    if head[:3] == b"ID3" or h4[:2] == b"\xff\xfb" or h4[:2] == b"\xff\xf3":
        return Detection("audio", "audio/mpeg", 0.95, "magic:MP3", "mp3")
    if head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
            return Detection("image", "image/heic", 0.95, f"magic:ftyp {brand.decode(errors='ignore')}", "heic")
        if brand in (b"avif", b"avis"):
            return Detection("image", "image/avif", 0.95, "magic:ftyp avif", "avif")
        if brand in (b"m4a ", b"m4b ", b"m4p "):
            return Detection("audio", "audio/mp4", 0.9, "magic:ftyp m4a", "m4a")
        if brand in (b"qt  ",):
            return Detection("video", "video/quicktime", 0.9, "magic:ftyp qt", "mov")
        return Detection("video", "video/mp4", 0.9, f"magic:ftyp {brand.decode(errors='ignore')}", "mp4")
    if h4 == b"\x1a\x45\xdf\xa3":
        return Detection("video", "video/x-matroska", 0.95, "magic:Matroska", "mkv")
    if head[60:68] == b"BOOKMOBI":
        return Detection("ebook", "application/x-mobipocket-ebook", 0.95, "magic:BOOKMOBI", "mobi")
    if h4 == b"wOFF":
        return Detection("font", "font/woff", 1.0, "magic:wOFF", "woff")
    if h4 == b"wOF2":
        return Detection("font", "font/woff2", 1.0, "magic:wOF2", "woff2")
    if h4 in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return Detection("font", "font/ttf", 0.9, "magic:TTF", "ttf")
    if head[:2] == b"MZ":
        return Detection("pe", "application/vnd.microsoft.portable-executable", 0.9, "magic:MZ", "exe")
    if h4 == b"\x7fELF":
        return Detection("binary", "application/x-elf", 0.95, "magic:ELF", "elf", "elf")
    if h4 == b"\x00asm":
        return Detection("binary", "application/wasm", 0.95, "magic:wasm", "wasm", "wasm")
    if h4 == b"dex\n":
        return Detection("binary", "application/x-dex", 0.95, "magic:dex", "dex", "dex")
    if h4[:3] in (b"CWS", b"FWS", b"ZWS"):
        return Detection("binary", "application/x-shockwave-flash", 0.9, "magic:SWF", "swf", "swf")
    return None


def _looks_text(head: bytes) -> bool:
    if not head:
        return True
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True   # UTF-16 BOM
    if head.startswith(b"\xef\xbb\xbf"):
        return True   # UTF-8 BOM
    sample = head[:2048]
    bad = sum(1 for b in sample if b < 7 or (13 < b < 32) and b not in (27,))
    return bad / max(len(sample), 1) < 0.03


def detect(path: str, force_format: Optional[str] = None) -> Detection:
    """探测文件格式。force_format 优先。"""
    ext = _ext_of(path)
    if force_format:
        fmt = force_format.lower().strip()
        mime = EXT_MIME.get(ext) or FORMAT_MIME.get(fmt, "application/octet-stream")
        return Detection(fmt, mime, 1.0, "forced", ext, fmt if fmt in ZIP_FAMILY else None)

    head = _read_head(path)
    magic = _magic(head, path)
    ext_fmt = EXT_MAP.get(ext)

    if magic:
        # 扩展名与内容冲突时以内容为准，但保留冲突提示
        if ext_fmt and ext_fmt != magic.format and not (
            ext_fmt == "data" and magic.format == "archive"
        ):
            magic.evidence += f" (扩展名 {ext} 指向 {ext_fmt}，以内容为准)"
        if not magic.ext:
            magic.ext = ext
        return magic

    if ext_fmt:
        # .ts 既可能是 TypeScript 也可能是 MPEG-TS：内容像文本就按源码处理
        if ext in ("ts", "mts") and _looks_text(head):
            return Detection("code", "text/plain", 0.75,
                             "sniff:text (.ts 无视频/传输流特征)", ext)
        mime = EXT_MIME.get(ext) or FORMAT_MIME.get(ext_fmt, "application/octet-stream")
        return Detection(ext_fmt, mime, 0.8, f"extension:.{ext}", ext)

    if _looks_text(head):
        return Detection("text", "text/plain", 0.5, "sniff:text", ext)
    return Detection("binary", "application/octet-stream", 0.5, "sniff:binary", ext)
