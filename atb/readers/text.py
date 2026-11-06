# -*- coding: utf-8 -*-
"""纯文本 / 源码 / Markdown 解析器。"""

import os
import re

from ..models import Block, Document, ReadOptions
from .base import BaseReader, clean_text, new_document, read_text_file, split_paragraphs

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_FENCE = re.compile(r"^```(\w*)\s*$")


class TextReader(BaseReader):
    name = "text"
    formats = ("text", "code")

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        stats = {}
        text, encoding, warnings, truncated = read_text_file(path, opts, stats)
        doc = new_document(path, det, f"{self.name}.plain")
        doc.metadata = {
            "encoding": encoding,
            "lines": stats.get("lines", 0),
            "characters": stats.get("characters", 0),
            "extension": os.path.splitext(path)[1].lower(),
        }
        doc.warnings.extend(warnings)
        doc.truncated = truncated

        ext = det.ext.lower()
        if det.format == "code":
            doc.blocks.append(Block("code", text, meta={"language": _lang_of(ext)}))
        elif ext in ("md", "markdown", "mdx"):
            doc.blocks.extend(_markdown_blocks(text))
        else:
            doc.blocks.extend(Block("paragraph", clean_text(p)) for p in split_paragraphs(text))
            if not doc.blocks and text.strip():
                doc.blocks.append(Block("paragraph", clean_text(text.strip())))
        return doc


def _lang_of(ext: str) -> str:
    alias = {
        "py": "python", "pyw": "python", "js": "javascript", "mjs": "javascript",
        "ts": "typescript", "tsx": "tsx", "jsx": "jsx", "cs": "csharp", "rb": "ruby",
        "sh": "bash", "ps1": "powershell", "bat": "batch", "cmd": "batch",
        "yml": "yaml", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "rs": "rust",
        "go": "go", "java": "java", "kt": "kotlin", "php": "php", "sql": "sql",
        "html": "html", "htm": "html", "css": "css", "scss": "scss", "lua": "lua",
    }
    return alias.get(ext, ext or "text")


def _markdown_blocks(text: str):
    """把 Markdown 拆成标题 / 代码块 / 段落，让 Agent 看到结构。"""
    blocks = []
    buf = []
    fence = None
    fence_lang = ""

    def flush():
        if buf:
            content = "\n".join(buf).strip()
            if content:
                blocks.append(Block("paragraph", clean_text(content)))
            buf.clear()

    for line in text.split("\n"):
        if fence is not None:
            if line.strip().startswith("```"):
                blocks.append(Block("code", "\n".join(buf), meta={"language": fence_lang}))
                buf.clear()
                fence = None
            else:
                buf.append(line)
            continue

        m = _MD_FENCE.match(line.strip())
        if m:
            flush()
            fence = "```"
            fence_lang = m.group(1) or ""
            continue

        h = _MD_HEADING.match(line)
        if h:
            flush()
            blocks.append(Block("heading", h.group(2).strip(), level=len(h.group(1))))
            continue

        if not line.strip():
            flush()
        else:
            buf.append(line)
    if fence is not None:
        blocks.append(Block("code", "\n".join(buf), meta={"language": fence_lang}))
    else:
        flush()
    return blocks or [Block("paragraph", clean_text(text.strip()))]
