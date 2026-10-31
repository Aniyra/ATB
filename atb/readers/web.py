# -*- coding: utf-8 -*-
"""网页 / XML / 电子书解析器。"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import (BaseReader, clean_text, new_document, read_text_file,
                   split_paragraphs)

_BLOCK_TAGS = ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "pre", "blockquote")


class WebReader(BaseReader):
    name = "web"
    formats = ("html", "ebook")

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        ext = det.ext.lower()
        if det.format == "ebook":
            if ext == "epub" or det.subtype == "epub":
                return self._epub(path, det, opts)
            if ext == "fb2":
                return self._fb2(path, det, opts)
            if ext in ("cbz", "cbr"):
                from .archive import ArchiveReader
                doc = ArchiveReader().read(path, det, opts)
                doc.handler = "ebook.comic"
                return doc
            return self._mobi(path, det, opts)
        if ext == "xml" or ext in ("rss", "atom", "xsl", "xslt", "plist"):
            return self._xml(path, det, opts)
        return self._html(path, det, opts)

    # ── HTML ────────────────────────────────────────────────
    def _html(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "web.html")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            doc.warnings.append("未安装 beautifulsoup4，使用正则粗提取")
            plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text,
                           flags=re.S | re.I)
            plain = re.sub(r"<[^>]+>", " ", plain)
            doc.blocks = [Block("paragraph", clean_text(p)) for p in split_paragraphs(plain)]
            return doc

        soup = BeautifulSoup(text, "lxml" if _has_lxml() else "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""
        desc = soup.find("meta", attrs={"name": "description"})
        doc.metadata.update({
            "title": title,
            "description": desc.get("content", "") if desc else "",
            "links": len(soup.find_all("a")),
            "images": len(soup.find_all("img")),
            "tables": len(soup.find_all("table")),
            "h1": [h.get_text(strip=True) for h in soup.find_all("h1")][:10],
        })
        if title:
            doc.blocks.append(Block("heading", title, level=1))

        root = soup.body or soup
        for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            doc.blocks.append(Block("heading", clean_text(el.get_text(" ", strip=True)),
                                    level=int(el.name[1])))
        paragraphs = []
        for el in root.find_all(["p", "li", "blockquote", "pre"]):
            t = clean_text(el.get_text(" ", strip=True))
            if not t:
                continue
            if el.name == "pre":
                doc.blocks.append(Block("code", el.get_text("\n", strip=True)))
            else:
                paragraphs.append(t)
        if paragraphs:
            doc.blocks.append(Block("paragraph", "\n\n".join(paragraphs)))

        for i, table in enumerate(root.find_all("table")[:20], 1):
            rows = []
            for tr in table.find_all("tr"):
                cells = [clean_text(c.get_text(" ", strip=True))
                         for c in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                doc.blocks.append(Block("table", "", meta={"index": i, "rows": rows}))
        return doc

    # ── XML ─────────────────────────────────────────────────
    def _xml(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "web.xml")
        doc.metadata["encoding"] = enc
        doc.truncated = truncated
        doc.warnings.extend(warns)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            doc.warnings.append(f"XML 解析失败：{e}，按纯文本输出")
            doc.blocks = [Block("code", text, meta={"language": "xml"})]
            return doc
        doc.metadata.update({
            "root": root.tag,
            "children": len(list(root)),
            "attributes": dict(root.attrib),
            "namespaces": [v for _, v in root.attrib.items() if _.startswith("{")] or [],
        })
        body = "\n".join(t.strip() for t in root.itertext() if t.strip())
        doc.blocks.append(Block("heading", root.tag, level=1))
        for para in split_paragraphs(body)[:2000]:
            doc.blocks.append(Block("paragraph", clean_text(para)))
        if len(body) > opts.max_chars:
            doc.truncated = True
        return doc

    # ── EPUB ────────────────────────────────────────────────
    def _epub(self, path, det, opts):
        doc = new_document(path, det, "ebook.epub")
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            raise AtbError("ATB-E07001", "EPUB 不是合法 ZIP 容器")
        with zf:
            names = zf.namelist()
            opf_path = _epub_opf(zf)
            if not opf_path:
                doc.warnings.append("未找到 OPF 清单，按文件名顺序读取")
                html_files = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
            else:
                opf = ET.fromstring(zf.read(opf_path))
                ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
                meta = {}
                for tag in ("title", "creator", "publisher", "language", "date", "description"):
                    el = opf.find(f".//dc:{tag}", ns)
                    if el is not None and el.text:
                        meta[tag] = el.text.strip()
                doc.metadata.update(meta)
                base = os.path.dirname(opf_path)
                manifest = {}
                for item in opf.findall(".//opf:manifest/opf:item", ns):
                    manifest[item.get("id")] = item.get("href")
                html_files = []
                for ref in opf.findall(".//opf:spine/opf:itemref", ns):
                    href = manifest.get(ref.get("idref"))
                    if href:
                        html_files.append(os.path.normpath(os.path.join(base, href)).replace("\\", "/"))
            doc.metadata["chapters"] = len(html_files)
            if doc.metadata.get("title"):
                doc.blocks.append(Block("heading", doc.metadata["title"], level=1))

            total = 0
            for idx, name in enumerate(html_files, 1):
                try:
                    raw = zf.read(name)
                except KeyError:
                    continue
                chapter_text, chapter_title = _html_to_text(raw)
                if chapter_title:
                    doc.blocks.append(Block("heading", chapter_title, level=2, meta={"chapter": idx}))
                for para in split_paragraphs(chapter_text):
                    doc.blocks.append(Block("paragraph", para, meta={"chapter": idx}))
                    total += len(para)
                    if opts.max_chars and total >= opts.max_chars:
                        doc.truncated = True
                        break
                if doc.truncated:
                    break
        return doc

    # ── FB2 ─────────────────────────────────────────────────
    def _fb2(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "ebook.fb2")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        clean = re.sub(r"<binary.*?</binary>", "", text, flags=re.S | re.I)
        title = re.search(r"<book-title>(.*?)</book-title>", clean, re.S | re.I)
        author = re.findall(r"<first-name>(.*?)</first-name>|<last-name>(.*?)</last-name>", clean, re.S | re.I)
        doc.metadata["title"] = clean_text(title.group(1)) if title else ""
        doc.metadata["authors"] = [clean_text(" ".join(x for x in t if x)) for t in author][:5]
        if doc.metadata["title"]:
            doc.blocks.append(Block("heading", doc.metadata["title"], level=1))
        for sec in re.finditer(r"<section[^>]*>(.*?)</section>", clean, re.S | re.I):
            body = re.sub(r"<[^>]+>", "\n", sec.group(1))
            for para in split_paragraphs(body):
                para = clean_text(para)
                if para:
                    doc.blocks.append(Block("paragraph", para))
        return doc

    # ── MOBI / AZW3 ─────────────────────────────────────────
    def _mobi(self, path, det, opts):
        doc = new_document(path, det, "ebook.mobi")
        with open(path, "rb") as f:
            head = f.read(512)
        name = head[:32].split(b"\x00")[0].decode("cp1252", "ignore").strip()
        doc.metadata.update({
            "title": name,
            "note": "MOBI/AZW3 仅提取头部元数据；正文需 Calibre 转换为 EPUB/TXT",
        })
        doc.blocks.append(Block("heading", name or os.path.basename(path), level=1))
        doc.warnings.append("MOBI 正文未解析。可用 calibre 的 ebook-convert 转为 epub 后读取")
        return doc


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _epub_opf(zf: zipfile.ZipFile):
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is not None:
            return rootfile.get("full-path")
    except Exception:
        pass
    for n in zf.namelist():
        if n.lower().endswith(".opf"):
            return n
    return None


def _html_to_text(raw: bytes):
    html = raw.decode("utf-8", errors="replace")
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = clean_text(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        body = soup.body or soup
        blocks = []
        for el in body.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "pre"]):
            t = clean_text(el.get_text(" ", strip=True))
            if t:
                blocks.append(t)
        text = "\n\n".join(blocks)
    except ImportError:
        plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
        plain = re.sub(r"<[^>]+>", " ", plain)
        text = "\n\n".join(split_paragraphs(plain))
    return text, title
