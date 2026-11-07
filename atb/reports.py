# -*- coding: utf-8 -*-
"""reports 目录的 TTL 清理。

ATB 的视频抽帧、分镜、局部放大、电脑截图都会写到 reports/ 下；
这里按文件修改时间清理过期产物，避免长期运行后无限增长。
"""

import os
import time

import config

# 只清理这些自动生成的媒体目录；错误日志等文件不动
CLEAN_ROOTS = ("frames", "storyboards", "zoom", "computer")
DEFAULT_TTL_DAYS = 7


def cleanup(ttl_days: int = DEFAULT_TTL_DAYS, dry_run: bool = False,
            base_dir: str = None, now: float = None) -> dict:
    """删除超过 ttl_days 的产物，并清掉随之变空的目录。

    ttl_days=0 表示全部清理；dry_run 只统计不删除。
    """
    base = os.path.abspath(base_dir or config.REPORT_DIR)
    ttl = max(0, int(ttl_days))
    cutoff = (time.time() if now is None else now) - ttl * 86400
    result = {
        "base": base,
        "ttl_days": ttl,
        "dry_run": bool(dry_run),
        "removed_files": 0,
        "removed_dirs": 0,
        "freed_bytes": 0,
        "kept_files": 0,
        "scanned_roots": [],
    }
    if not os.path.isdir(base):
        return result

    for name in CLEAN_ROOTS:
        root = os.path.join(base, name)
        if not os.path.isdir(root):
            continue
        result["scanned_roots"].append(name)
        for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if ttl > 0 and stat.st_mtime >= cutoff:
                    result["kept_files"] += 1
                    continue
                if not dry_run:
                    try:
                        os.remove(path)
                    except OSError:
                        continue
                result["removed_files"] += 1
                result["freed_bytes"] += stat.st_size
            if not dry_run:
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                        result["removed_dirs"] += 1
                except OSError:
                    pass
    return result


def human_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"
