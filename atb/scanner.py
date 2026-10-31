# -*- coding: utf-8 -*-
"""目录扫描：统计格式分布，给 Agent 一份「这堆文件是什么」的全景图。"""

import os
from collections import Counter, defaultdict
from typing import Optional

import config
from .detect import detect
from .errors import AtbError


def scan_directory(root: str, *, recursive: bool = True, limit: int = None,
                   include_detail: bool = True, skip_hidden: bool = False) -> dict:
    root = os.path.abspath(root)
    if not os.path.exists(root):
        raise AtbError("ATB-E01001", root)
    if not os.path.isdir(root):
        raise AtbError("ATB-E01004", f"{root} 不是目录")
    limit = limit or config.DEFAULT_SCAN_LIMIT

    result = {
        "root": root,
        "recursive": recursive,
        "files": 0,
        "dirs": 0,
        "total_size": 0,
        "skipped": 0,
        "truncated": False,
        "by_format": {},
        "by_extension": {},
        "files_detail": [],
        "errors": [],
    }
    fmt_stats = defaultdict(lambda: {"count": 0, "size": 0})
    ext_stats = Counter()
    errors = []

    if recursive:
        walker = os.walk(root)
    else:
        try:
            names = os.listdir(root)
        except PermissionError:
            raise AtbError("ATB-E01002", root)
        walker = [(root, [], names)]

    for dirpath, dirnames, filenames in walker:
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        result["dirs"] += len(dirnames)
        for name in filenames:
            if skip_hidden and name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError as e:
                result["skipped"] += 1
                errors.append(f"{full}: {e}")
                continue
            if result["files"] >= limit:
                result["truncated"] = True
                break
            try:
                det = detect(full)
                fmt = det.format
                if det.subtype and det.subtype != fmt:
                    fmt = f"{fmt}/{det.subtype}"
                mime = det.mime
            except AtbError as e:
                result["skipped"] += 1
                errors.append(f"{full}: {e.code}")
                continue
            except OSError as e:
                result["skipped"] += 1
                errors.append(f"{full}: {e}")
                continue

            ext = os.path.splitext(name)[1].lower().lstrip(".") or "(无)"
            result["files"] += 1
            result["total_size"] += st.st_size
            fmt_stats[fmt]["count"] += 1
            fmt_stats[fmt]["size"] += st.st_size
            ext_stats[ext] += 1
            if include_detail:
                result["files_detail"].append({
                    "rel": os.path.relpath(full, root).replace("\\", "/"),
                    "name": name,
                    "format": fmt,
                    "mime": mime,
                    "size": st.st_size,
                })
        if result["truncated"]:
            break

    result["by_format"] = dict(sorted(fmt_stats.items(), key=lambda x: x[1]["size"], reverse=True))
    result["by_extension"] = dict(ext_stats.most_common(50))
    result["errors"] = errors[:50]
    return result


def quick_preview(path: str, max_chars: int = 400) -> Optional[str]:
    """只读取文件头部文本，用于目录速览。失败返回 None。"""
    from .dispatch import read_file
    from .models import ReadOptions
    try:
        doc = read_file(path, ReadOptions(max_chars=max_chars, max_bytes=64 * 1024))
        text = doc.text(max_chars).strip()
        return text or None
    except Exception:
        return None
