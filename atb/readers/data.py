# -*- coding: utf-8 -*-
"""结构化数据解析器：JSON / JSONL / YAML / TOML / CSV / SQLite / Parquet / ipynb。"""

import csv
import io
import json
import os
import sqlite3
import tomllib
from pathlib import Path

from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from .base import (BaseReader, clean_text, new_document, read_text_file,
                   split_paragraphs)

MAX_ROWS = 500
MAX_SAMPLE_ROWS = 20


class DataReader(BaseReader):
    name = "data"
    formats = ("data",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        ext = det.ext.lower() or (det.subtype or "")
        if ext in ("json", "json5", "geojson"):
            return self._json(path, det, opts)
        if ext in ("jsonl", "ndjson"):
            return self._jsonl(path, det, opts)
        if ext in ("yaml", "yml"):
            return self._yaml(path, det, opts)
        if ext == "toml":
            return self._toml(path, det, opts)
        if ext in ("csv", "tsv", "psv"):
            return self._csv(path, det, opts)
        if ext in ("sqlite", "sqlite3", "db", "db3") or det.subtype == "sqlite":
            return self._sqlite(path, det, opts)
        if ext in ("parquet", "arrow", "feather"):
            return self._parquet(path, det, opts)
        if ext == "ipynb":
            return self._notebook(path, det, opts)
        # 未知结构：按文本兜底
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.text")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        doc.blocks = [Block("paragraph", clean_text(p)) for p in split_paragraphs(text)]
        return doc

    # ── JSON ────────────────────────────────────────────────
    def _json(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.json")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            doc.warnings.append(f"JSON 语法错误：{e.msg} (行 {e.lineno} 列 {e.colno})")
            doc.blocks = [Block("code", text, meta={"language": "json"})]
            return doc

        doc.metadata.update(_json_shape(obj))
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        if opts.max_chars and len(pretty) > opts.max_chars:
            pretty = pretty[: opts.max_chars]
            doc.truncated = True
            doc.warnings.append(f"JSON 过于庞大，已截断到 {opts.max_chars} 字符")
        doc.blocks.append(Block("code", pretty, meta={"language": "json"}))
        return doc

    def _jsonl(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.jsonl")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        rows, bad = [], 0
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
        if bad:
            doc.warnings.append(f"{bad} 行不是合法 JSON")
        doc.metadata.update({"records": len(rows), "fields": _json_shape(rows)})
        sample = rows[:MAX_SAMPLE_ROWS]
        doc.blocks.append(Block(
            "code",
            "\n".join(json.dumps(r, ensure_ascii=False) for r in sample),
            meta={"language": "jsonl", "note": f"前 {len(sample)} 条，共 {len(rows)} 条"},
        ))
        return doc

    # ── YAML / TOML ─────────────────────────────────────────
    def _yaml(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.yaml")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        try:
            import yaml
            obj = yaml.safe_load(text)
            doc.metadata.update(_json_shape(obj))
        except ImportError:
            doc.warnings.append("未安装 pyyaml，已按纯文本输出")
        except Exception as e:
            doc.warnings.append(f"YAML 解析失败：{e}")
        body = text if len(text) <= opts.max_chars else text[: opts.max_chars]
        if len(text) > opts.max_chars:
            doc.truncated = True
        doc.blocks.append(Block("code", body, meta={"language": "yaml"}))
        return doc

    def _toml(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.toml")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        try:
            obj = tomllib.loads(text)
            doc.metadata.update(_json_shape(obj))
        except Exception as e:
            doc.warnings.append(f"TOML 解析失败：{e}")
        body = text if len(text) <= opts.max_chars else text[: opts.max_chars]
        doc.blocks.append(Block("code", body, meta={"language": "toml"}))
        return doc

    # ── CSV ─────────────────────────────────────────────────
    def _csv(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.csv")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated

        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            delim = "\t" if det.ext == "tsv" else ","
            dialect = csv.excel
            dialect.delimiter = delim

        reader = csv.reader(io.StringIO(text), dialect)
        rows, total = [], 0
        for row in reader:
            total += 1
            if len(rows) < MAX_ROWS:
                rows.append(row)
            elif total - len(rows) == 1:
                doc.truncated = True
        if not rows:
            doc.warnings.append("CSV 无数据行")
            return doc

        header = rows[0]
        doc.metadata.update({
            "delimiter": getattr(dialect, "delimiter", ","),
            "columns": len(header),
            "column_names": header,
            "rows": max(total - 1, 0),
        })
        doc.blocks.append(Block("table", "", meta={"rows": rows, "header": header}))
        if total - 1 > MAX_ROWS:
            doc.warnings.append(f"共 {total - 1} 行，仅输出前 {MAX_ROWS} 行")
        return doc

    # ── SQLite ──────────────────────────────────────────────
    def _sqlite(self, path, det, opts):
        doc = new_document(path, det, "data.sqlite")
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=3)
        except sqlite3.Error as e:
            raise AtbError("ATB-E08002", f"{path} ({e})")
        try:
            conn.row_factory = sqlite3.Row
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            views = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
            doc.metadata.update({"tables": tables, "views": views, "table_count": len(tables)})
            for table in tables[:50]:
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                cols = [{"name": r["name"], "type": r["type"], "pk": bool(r["pk"])} for r in info]
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                except sqlite3.Error:
                    count = -1
                doc.blocks.append(Block("kv", f"表 {table}", meta={
                    "columns": cols, "rows": count,
                }))
                try:
                    cur = conn.execute(f'SELECT * FROM "{table}" LIMIT {MAX_SAMPLE_ROWS}')
                    rows = [dict(zip([c["name"] for c in cols], r)) for r in cur.fetchall()]
                    if rows:
                        header = [c["name"] for c in cols]
                        doc.blocks.append(Block("table", "", sheet=table, meta={
                            "header": header,
                            "rows": [[r.get(h) for h in header] for r in rows],
                        }))
                except sqlite3.Error as e:
                    doc.warnings.append(f"表 {table} 采样失败：{e}")
        except sqlite3.Error as e:
            raise AtbError("ATB-E08002", f"{path} ({e})")
        finally:
            conn.close()
        return doc

    # ── Parquet ─────────────────────────────────────────────
    def _parquet(self, path, det, opts):
        doc = new_document(path, det, "data.parquet")
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise AtbError("ATB-E11001", "解析 Parquet 需要 pyarrow：pip install pyarrow")
        pf = pq.ParquetFile(path)
        schema = pf.schema_arrow
        doc.metadata.update({
            "rows": pf.metadata.num_rows,
            "row_groups": pf.metadata.num_row_groups,
            "columns": [{"name": f.name, "type": str(f.type)} for f in schema],
            "compression": pf.metadata.row_group(0).column(0).compression
            if pf.metadata.num_row_groups else None,
        })
        table = pf.read_row_group(0).slice(0, MAX_SAMPLE_ROWS)
        header = table.column_names
        rows = [[str(v) for v in row] for row in table.to_pylist()]
        doc.blocks.append(Block("table", "", meta={"header": header, "rows": rows}))
        if pf.metadata.num_rows > MAX_SAMPLE_ROWS:
            doc.warnings.append(f"共 {pf.metadata.num_rows} 行，仅展示前 {MAX_SAMPLE_ROWS} 行")
        return doc

    # ── Jupyter Notebook ────────────────────────────────────
    def _notebook(self, path, det, opts):
        text, enc, warns, truncated = read_text_file(path, opts)
        doc = new_document(path, det, "data.ipynb")
        doc.metadata["encoding"] = enc
        doc.warnings.extend(warns)
        doc.truncated = truncated
        try:
            nb = json.loads(text)
        except json.JSONDecodeError as e:
            raise AtbError("ATB-E08001", f"{path} ({e})")

        cells = nb.get("cells", [])
        kernel = nb.get("metadata", {}).get("kernelspec", {})
        doc.metadata.update({
            "cells": len(cells),
            "kernel": kernel.get("display_name") or kernel.get("name"),
            "language": nb.get("metadata", {}).get("language_info", {}).get("name"),
        })
        for i, cell in enumerate(cells, 1):
            src = "".join(cell.get("source", []))
            lang = cell.get("cell_type")
            doc.blocks.append(Block("code" if lang == "code" else "paragraph", src,
                                    meta={"cell": i, "cell_type": lang}))
            for out in cell.get("outputs", [])[:2]:
                if out.get("output_type") == "stream":
                    doc.blocks.append(Block("raw", "".join(out.get("text", []))[:2000],
                                            meta={"cell": i, "output": "stream"}))
                elif "text/plain" in out.get("data", {}):
                    doc.blocks.append(Block("raw", "".join(out["data"]["text/plain"])[:2000],
                                            meta={"cell": i, "output": "text/plain"}))
        return doc


def _json_shape(obj, depth: int = 0) -> dict:
    """概括 JSON 结构，避免把整个大对象塞进 metadata。"""
    if isinstance(obj, dict):
        keys = list(obj.keys())
        return {"type": "object", "keys": keys[:50], "key_count": len(keys)}
    if isinstance(obj, list):
        first = obj[0] if obj else None
        shape = {"type": "array", "length": len(obj)}
        if isinstance(first, dict):
            shape["item_keys"] = list(first.keys())[:50]
        elif isinstance(first, (str, int, float, bool)) or first is None:
            shape["item_type"] = type(first).__name__
        return shape
    return {"type": type(obj).__name__}
