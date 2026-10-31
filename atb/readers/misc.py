# -*- coding: utf-8 -*-
"""字幕 / 邮件 / 日历 / 未知二进制解析器。"""

import email
import email.policy
import math
import os
import re
from collections import Counter

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import BaseReader, clean_text, new_document, read_text_file, split_paragraphs

_SRT_TIME = re.compile(r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})")
_ASS_TIME = re.compile(r"Dialogue:.*?,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),(.*)$", re.M)


class SubtitleReader(BaseReader):
    name = "subtitle"
    formats = ("subtitle",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "subtitle.cue")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        ext = det.ext.lower()

        cues = []
        if ext in ("srt", "vtt"):
            for block in re.split(r"\n\s*\n", text):
                m = _SRT_TIME.search(block)
                if not m:
                    continue
                lines = [l for l in block.split("\n") if l.strip() and not _SRT_TIME.search(l)
                         and not l.strip().isdigit() and l.strip().upper() != "WEBVTT"]
                cues.append((m.group(1), m.group(2), clean_text(" ".join(lines))))
        elif ext in ("ass", "ssa"):
            for m in _ASS_TIME.finditer(text):
                cues.append((m.group(1), m.group(2), clean_text(re.sub(r"\{[^}]*\}", "", m.group(3)))))
        else:  # lrc
            for line in text.split("\n"):
                m = re.match(r"\[(\d{2}:\d{2}(?:\.\d+)?)\](.*)", line)
                if m:
                    cues.append((m.group(1), "", clean_text(m.group(2))))

        doc.metadata.update({"cues": len(cues),
                             "first": cues[0][0] if cues else "",
                             "last": cues[-1][1] or cues[-1][0] if cues else ""})
        doc.blocks.append(Block("heading", os.path.basename(path), level=1))
        for start, end, content in cues[:2000]:
            if content:
                doc.blocks.append(Block("paragraph", content,
                                        meta={"start": start, "end": end}))
        return doc


class EmailReader(BaseReader):
    name = "email"
    formats = ("email",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        if det.ext == "msg" or det.subtype == "msg":
            return self._msg(path, det, opts)
        doc = new_document(path, det, "email.eml")
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
        doc.metadata.update({
            "subject": str(msg.get("subject", "")),
            "from": str(msg.get("from", "")),
            "to": str(msg.get("to", "")),
            "cc": str(msg.get("cc", "")),
            "date": str(msg.get("date", "")),
            "message_id": str(msg.get("message-id", "")),
        })
        body_parts, attachments = [], []
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get_content_disposition()
            if disp == "attachment" or part.get_filename():
                payload = part.get_payload(decode=True) or b""
                attachments.append({"filename": part.get_filename(), "type": ctype,
                                    "size": len(payload)})
                continue
            if ctype == "text/plain":
                body_parts.insert(0, part.get_content())
            elif ctype == "text/html":
                body_parts.append(_html_to_text(part.get_content()))
        doc.metadata["attachments"] = attachments
        doc.blocks.append(Block("heading", doc.metadata["subject"] or os.path.basename(path), level=1))
        doc.blocks.append(Block("kv", "", meta={"headers": {
            k: doc.metadata[k] for k in ("from", "to", "cc", "date") if doc.metadata.get(k)}}))
        for para in split_paragraphs("\n\n".join(body_parts)):
            doc.blocks.append(Block("paragraph", clean_text(para)))
        return doc

    def _msg(self, path, det, opts):
        doc = new_document(path, det, "email.msg")
        try:
            import olefile
        except ImportError:
            raise AtbError("ATB-E11001", "解析 MSG 需要 olefile：pip install olefile")
        keys = {
            "__substg1.0_0037001F": "subject",
            "__substg1.0_0C1F001F": "sender",
            "__substg1.0_0E04001F": "to",
            "__substg1.0_1000001F": "body",
        }
        found = {}
        with olefile.OleFileIO(path) as ole:
            for entry in ole.listdir():
                name = entry[-1]
                if name in keys:
                    try:
                        found[keys[name]] = ole.openstream(entry).read().decode("utf-16-le", "ignore")
                    except Exception:
                        pass
            doc.metadata.update(found)
        doc.blocks.append(Block("heading", found.get("subject") or os.path.basename(path), level=1))
        for para in split_paragraphs(found.get("body", "")):
            doc.blocks.append(Block("paragraph", clean_text(para)))
        if not found.get("body"):
            doc.warnings.append("未提取到正文，可能是 RTF/HTML 编码的 MSG")
        return doc


class CalendarReader(BaseReader):
    name = "calendar"
    formats = ("calendar",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "calendar.ics")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        if det.ext == "vcf":
            name = re.search(r"^FN:(.*)$", text, re.M)
            doc.metadata["name"] = name.group(1).strip() if name else ""
            doc.blocks.append(Block("heading", doc.metadata["name"] or "联系人", level=1))
            for key in ("TEL", "EMAIL", "ORG", "TITLE", "ADR"):
                for m in re.finditer(rf"^{key}[^:]*:(.*)$", text, re.M):
                    doc.blocks.append(Block("kv", f"{key}: {m.group(1).strip()}"))
            return doc

        events = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        doc.metadata["events"] = len(events)
        doc.blocks.append(Block("heading", "日历事件", level=1))
        for ev in events[:500]:
            summary = re.search(r"^SUMMARY:(.*)$", ev, re.M)
            start = re.search(r"^DTSTART[^:]*:(.*)$", ev, re.M)
            end = re.search(r"^DTEND[^:]*:(.*)$", ev, re.M)
            loc = re.search(r"^LOCATION:(.*)$", ev, re.M)
            title = clean_text(summary.group(1)) if summary else "(无标题)"
            meta = {}
            if start:
                meta["start"] = start.group(1).strip()
            if end:
                meta["end"] = end.group(1).strip()
            if loc:
                meta["location"] = clean_text(loc.group(1))
            doc.blocks.append(Block("kv", title, meta=meta))
        return doc


class BinaryReader(BaseReader):
    name = "binary"
    formats = ("binary",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "binary.inspect")
        with open(path, "rb") as f:
            head = f.read(min(os.path.getsize(path), opts.max_bytes, 1024 * 1024))
        doc.metadata.update({
            "hex_head": head[:256].hex(" "),
            "entropy": _entropy(head),
            "printable_ratio": round(sum(32 <= b < 127 for b in head) / max(len(head), 1), 3),
            "detected_format": det.format,
            "evidence": det.evidence,
        })
        doc.blocks.append(Block("heading", os.path.basename(path), level=1))
        doc.blocks.append(Block("raw", head[:256].hex(" "), meta={"encoding": "hex"}))
        strings = _strings(head, 6)[:200]
        if strings:
            doc.blocks.append(Block("code", "\n".join(strings), meta={"note": "可打印字符串"}))
        if det.format == "binary" and doc.metadata["entropy"] > 7.5:
            doc.warnings.append("熵值很高，可能是压缩/加密数据或模型权重")
        return doc


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text("\n", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return round(-sum((c / total) * math.log2(c / total) for c in counts.values()), 3)


def _strings(data: bytes, min_len: int = 6):
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        out.append(m.group(0).decode("latin-1"))
    for m in re.finditer(r"(?:[\x20-\x7e]\x00){%d,}" % min_len, data):
        try:
            out.append(m.group(0).decode("utf-16-le"))
        except Exception:
            pass
    return out
