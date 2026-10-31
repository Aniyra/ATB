# -*- coding: utf-8 -*-
"""输出渲染：text（人读）/ markdown（Agent 读）/ json（程序读）。"""

import json
import os
from typing import Iterable

from .models import Block, Document, human_size


def render(doc: Document, mode: str = "text", max_chars: int = 0) -> str:
    if mode == "json":
        return json.dumps(doc.to_dict(include_text=True, max_chars=max_chars or None),
                          ensure_ascii=False, indent=2, default=str)
    if mode == "md":
        return render_markdown(doc, max_chars)
    return render_text(doc, max_chars)


# ── 纯文本 ───────────────────────────────────────────────────
def render_text(doc: Document, max_chars: int = 0) -> str:
    lines = []
    name = os.path.basename(doc.path)
    lines.append(f"文件   {name}")
    lines.append(f"路径   {doc.path}")
    lines.append(f"格式   {doc.format}" + (f" / {doc.metadata.get('subtype')}" if doc.metadata.get("subtype") else ""))
    lines.append(f"处理器 {doc.handler}")
    lines.append(f"大小   {human_size(doc.size)}   MIME {doc.mime}")
    lines.append(f"修改   {doc.mtime}")
    if doc.sha256:
        lines.append(f"SHA256 {doc.sha256}")
    meta = _flat_meta(doc.metadata)
    if meta:
        lines.append("")
        lines.append("── 元数据 ──")
        for k, v in meta.items():
            lines.append(f"  {k}: {v}")
    budget = doc.metadata.get("budget")
    if budget:
        lines.append("")
        lines.append(f"── 画面预算 ── {budget['frames']} 帧 · "
                     f"{budget['image_bytes_human']} · base64 {budget['base64_human']} · "
                     f"约 {budget['tokens_estimate']} tokens")
    if doc.warnings:
        lines.append("")
        lines.append("── 提示 ──")
        for w in doc.warnings:
            lines.append(f"  ! {w}")
    if doc.error:
        lines.append("")
        lines.append(f"错误 {doc.error.get('code')} {doc.error.get('title')}: {doc.error.get('detail')}")
        lines.append(f"建议 {doc.error.get('solution')}")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"── 正文（{len(doc.blocks)} 块）──")
    body = []
    for b in doc.blocks:
        body.append(_block_text(b))
    text = "\n".join(x for x in body if x.strip())
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n…（已截断）"
    lines.append(text)
    return "\n".join(lines)


def _block_text(b: Block) -> str:
    if b.type == "table":
        rows = b.meta.get("rows") or []
        if not rows:
            return ""
        return "\n".join("\t".join(str(c) for c in row) for row in rows)
    if b.type == "heading":
        title = b.text
        if b.page:
            title = f"[p{b.page}] {title}"
        return f"\n{title}\n" + "─" * min(len(title) + 2, 60)
    if b.type == "code":
        lang = b.meta.get("language")
        return f"```{lang or ''}\n{b.text}\n```" if b.text else ""
    if b.type == "image":
        path = b.meta.get("path")
        return f"[图片] {b.text}" + (f"  → {path}" if path else "")
    if b.type == "kv":
        meta = b.meta.get("metadata") or b.meta
        head = f"{b.text}\n" if b.text else ""
        return head + "\n".join(f"  {k}: {v}" for k, v in meta.items())
    return b.text


# ── Markdown ────────────────────────────────────────────────
def render_markdown(doc: Document, max_chars: int = 0) -> str:
    out = []
    title = doc.metadata.get("title") or os.path.basename(doc.path)
    out.append(f"# {title}")
    out.append("")
    out.append(f"> 格式 `{doc.format}` · 处理器 `{doc.handler}` · 大小 `{human_size(doc.size)}` "
               f"· 块数 `{len(doc.blocks)}`")
    out.append(f"> 路径 `{doc.path}`")
    meta = _flat_meta(doc.metadata)
    if meta:
        out.append("")
        out.append("## 元数据")
        out.append("")
        out.append("| 字段 | 值 |")
        out.append("| --- | --- |")
        for k, v in meta.items():
            out.append(f"| {k} | {_md_cell(v)} |")
    if doc.warnings:
        out.append("")
        out.append("## 提示")
        out.append("")
        for w in doc.warnings:
            out.append(f"- {w}")
    out.append("")
    out.append("## 正文")
    out.append("")
    for b in doc.blocks:
        out.append(_block_md(b))
    text = "\n".join(x for x in out if x is not None)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n\n…（已截断）"
    return text


def _block_md(b: Block) -> str:
    if b.type == "heading":
        lvl = min(max(b.level or 2, 2), 6)
        prefix = f"`p{b.page}` " if b.page else ""
        return f"\n{'#' * lvl} {prefix}{b.text}\n"
    if b.type == "paragraph":
        return b.text + "\n"
    if b.type == "list":
        return f"- {b.text}\n"
    if b.type == "code":
        lang = b.meta.get("language", "")
        return f"\n```{lang}\n{b.text}\n```\n"
    if b.type == "table":
        rows = b.meta.get("rows") or []
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        norm = [list(map(str, r)) + [""] * (width - len(r)) for r in rows]
        head = norm[0]
        lines = ["| " + " | ".join(_md_cell(c) for c in head) + " |",
                 "| " + " | ".join("---" for _ in head) + " |"]
        for r in norm[1:]:
            lines.append("| " + " | ".join(_md_cell(c) for c in r) + " |")
        return "\n" + "\n".join(lines) + "\n"
    if b.type == "image":
        path = b.meta.get("path")
        if path:
            return f"\n**{b.text}**\n\n`{path}`\n"
        return f"\n> [图片] {b.text}\n"
    if b.type == "kv":
        meta = b.meta.get("metadata") or {}
        body = ", ".join(f"{k}: {v}" for k, v in meta.items())
        return f"\n**{b.text or '信息'}** {body}\n"
    return (b.text + "\n") if b.text else ""


# ── 扫描结果 ─────────────────────────────────────────────────
def render_scan(result: dict, mode: str = "text") -> str:
    if mode == "json":
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    lines = [f"目录   {result['root']}",
             f"文件   {result['files']} 个（{human_size(result['total_size'])}）"
             + (f"，跳过 {result['skipped']} 个" if result.get("skipped") else "")]
    if result.get("by_format"):
        lines.append("")
        lines.append("── 格式分布 ──")
        rows = [[k, v["count"], human_size(v["size"])]
                for k, v in sorted(result["by_format"].items(),
                                   key=lambda x: x[1]["size"], reverse=True)]
        from .ui import table as _t
        lines.append(_t(["格式", "数量", "体积"], rows))
    if result.get("files_detail"):
        lines.append("")
        lines.append("── 文件列表（前 200）──")
        from .ui import table as _t
        rows = [[f["rel"], f["format"], human_size(f["size"])]
                for f in result["files_detail"][:200]]
        lines.append(_t(["相对路径", "格式", "大小"], rows, max_col=70))
    if result.get("errors"):
        lines.append("")
        lines.append(f"── 无法访问 {len(result['errors'])} 项 ──")
        for e in result["errors"][:10]:
            lines.append(f"  ! {e}")
    return "\n".join(lines)


def _flat_meta(meta: dict) -> dict:
    out = {}
    for k, v in meta.items():
        if k in ("frames", "frame_dir"):   # 帧信息已在正文块中逐条列出
            continue
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            s = json.dumps(v, ensure_ascii=False, default=str)
            out[k] = s[:800] + ("…" if len(s) > 800 else "")
        else:
            s = str(v)
            out[k] = s[:800] + ("…" if len(s) > 800 else "")
    return out


def _md_cell(value) -> str:
    s = str(value).replace("\n", "<br>").replace("|", "\\|")
    return s[:500] + ("…" if len(s) > 500 else "")


def write_output(text: str, out_path: str) -> str:
    import os as _os
    from .errors import AtbError
    try:
        _os.makedirs(_os.path.dirname(_os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return out_path
    except OSError as e:
        raise AtbError("ATB-E09001", f"{out_path} ({e})")
