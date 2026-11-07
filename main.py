# -*- coding: utf-8 -*-
"""ATB — All Tool Box 命令行入口。

用法：
    python main.py read <文件> [--json|--md] [--format F] [--max-chars N]
    python main.py text <文件>
    python main.py info <文件>
    python main.py scan <目录> [--no-recursive] [--json]
    python main.py formats [--json]
    python main.py doctor [--json]
    python main.py video <视频> [--frames N] [--start S] [--end S]
    python main.py storyboard <视频> [--cells N] [--subs 字幕.srt]
    python main.py zoom <视频> --at 秒 [--box x,y,w,h]
    python main.py quota [--set deepseek=KEY] [--init] [--show]
    python main.py ctx <对话存档> [--target-tokens N]
    python main.py ui <动作> [--app 窗口] [--json]
    python main.py reports [--days N] [--dry-run]
    python main.py mcp                 # 启动 stdio MCP Server
    python main.py err [编号|关键词]    # 错误码速查
    python main.py                     # 交互菜单
"""

import argparse
import importlib
import json
import os
import platform
import shutil
import sys
import time
import traceback

import config
from atb import dispatch, history, output, quota, reports, ui
from atb.errors import (ERRORS, AtbError, all_errors, list_categories, log_error,
                        lookup, match_category, normalize_code, search_errors)
from atb.mcp import format_matrix, main as mcp_main
from atb.models import ReadOptions, human_size
from atb.progress import NULL as NULL_PROGRESS
from atb.progress import make_progress
from atb.readers.video import export_zoom
from atb.scanner import scan_directory

_PROGRESS = NULL_PROGRESS

MENU = {
    "1": ("读取文件（自动识别格式）", "read"),
    "2": ("提取纯文本", "text"),
    "3": ("查看文件信息（轻量探测）", "info"),
    "4": ("扫描目录并统计格式", "scan"),
    "5": ("查看支持的格式", "formats"),
    "6": ("环境自检 doctor", "doctor"),
    "7": ("启动 MCP Server（stdio）", "mcp"),
    "8": ("错误码速查", "err"),
    "9": ("视频抽帧（供视觉模型理解）", "video"),
    "10": ("视频分镜图（一张图看全片）", "storyboard"),
    "11": ("查询 API 额度（可选）", "quota"),
    "12": ("压缩历史对话（上下文续接）", "ctx"),
    "13": ("UI 精准编辑（实验性 · 仅 Windows）", "ui"),
    "0": ("退出", "exit"),
}


# ── 参数 ─────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=config.PROG,
        description=f"{config.APP_NAME} — {config.APP_DESC}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-v", "--version", action="version",
                   version=f"{config.APP_NAME} {config.APP_VERSION}")
    p.add_argument("--no-color", action="store_true", help="关闭 ANSI 颜色")
    p.add_argument("--no-progress", action="store_true", help="关闭进度条")
    p.add_argument("--threads", type=int, default=0, metavar="N",
                   help="工作线程数，0=自动（最多 4）")
    sub = p.add_subparsers(dest="cmd")

    def _add_video_opts(sp):
        sp.add_argument("--frames", type=int, help="视频抽帧数量（默认 12，安全阈值 240）")
        sp.add_argument("--start", type=float, help="视频起始秒")
        sp.add_argument("--end", type=float, help="视频结束秒")
        sp.add_argument("--width", type=int, dest="frame_width",
                        help="帧长边像素（默认 768）")
        sp.add_argument("--quality", type=int, dest="frame_quality",
                        help="JPEG 质量 50-95（默认 80）")
        sp.add_argument("--audio", action="store_true", dest="include_audio",
                        help="同时导出音轨")
        sp.add_argument("--frame-dir", dest="frame_dir", help="帧输出目录")
        sp.add_argument("--scene", action="store_true",
                        help="按关键帧采样（适合剪辑类视频）")
        _add_runtime_opts(sp)

    def _add_runtime_opts(sp):
        sp.add_argument("--no-progress", action="store_true", default=argparse.SUPPRESS,
                        help="关闭进度条")
        sp.add_argument("--threads", type=int, default=argparse.SUPPRESS,
                        metavar="N", help="工作线程数，0=自动（最多 4）")

    def add_read_opts(sp):
        sp.add_argument("--format", help="强制指定格式（pdf/docx/text/...）")
        sp.add_argument("--max-chars", type=int, default=config.DEFAULT_MAX_CHARS,
                        help="正文最大字符数，0 表示不限制（默认 200000）")
        sp.add_argument("--max-bytes", type=int, default=config.DEFAULT_MAX_BYTES,
                        help="读取的最大字节数")
        sp.add_argument("--encoding", help="强制文本编码，如 gbk / utf-8")
        sp.add_argument("--pages", help="PDF 页码范围，如 1-5,8")
        sp.add_argument("--sheet", help="仅读取指定工作表")
        sp.add_argument("--member", help="读取压缩包内指定条目")
        sp.add_argument("--password", help="加密文档/压缩包密码")
        sp.add_argument("--ocr", action="store_true", help="尝试 OCR（需 Tesseract）")
        sp.add_argument("--out", help="把结果写入文件")
        _add_video_opts(sp)

    sp = sub.add_parser("read", help="读取文件并输出内容")
    sp.add_argument("path")
    add_read_opts(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="输出 JSON（结构化）")
    g.add_argument("--md", action="store_true", help="输出 Markdown")

    sp = sub.add_parser("text", help="只提取纯文本")
    sp.add_argument("path")
    sp.add_argument("--format")
    sp.add_argument("--max-chars", type=int, default=config.DEFAULT_MAX_CHARS)
    sp.add_argument("--pages")
    sp.add_argument("--out")

    sp = sub.add_parser("info", help="轻量探测文件信息")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("video", help="视频抽帧，供视觉模型理解画面")
    sp.add_argument("path")
    _add_video_opts(sp)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--out", help="把时间轴摘要写入文件")

    def _add_storyboard_opts(sp):
        sp.add_argument("--cells", type=int,
                        help="格数（默认按时长与剪辑密度自动，上限 64）")
        sp.add_argument("--sheets", type=int, default=1,
                        help="分几张宫格图（默认 1，上限 8）")
        sp.add_argument("--budget", type=int, dest="storyboard_budget",
                        default=1_200_000, help="单张图像素预算（默认 1200000）")
        sp.add_argument("--start", type=float, help="起始秒")
        sp.add_argument("--end", type=float, help="结束秒")
        sp.add_argument("--subs", dest="subtitles",
                        help="字幕文件（srt/vtt）；默认查找视频同名文件")
        _add_runtime_opts(sp)

    sp = sub.add_parser("storyboard", help="视频场景分镜图（一张图看全片）")
    sp.add_argument("path")
    _add_storyboard_opts(sp)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("zoom", help="按时刻/区域取全分辨率画面（看清细节）")
    sp.add_argument("path")
    sp.add_argument("--at", type=float, required=True, help="时刻（秒）")
    sp.add_argument("--box", help="归一化区域 x,y,w,h（0-1），省略为整帧")
    sp.add_argument("--width", type=int, default=1600, help="输出宽度（默认 1600）")
    sp.add_argument("--quality", type=int, default=88, help="JPEG 质量（默认 88）")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("quota", help="查询 API 额度（可选）")
    sp.add_argument("--provider", help="只查某个提供商：" + "/".join(quota.PROVIDERS))
    sp.add_argument("--set", metavar="PROVIDER=KEY", help="写入密钥到 ~/.atb/config.json")
    sp.add_argument("--unset", metavar="PROVIDER", help="删除已保存的密钥")
    sp.add_argument("--init", action="store_true", help="生成配置文件模板")
    sp.add_argument("--show", action="store_true", help="查看已配置的密钥（脱敏）")
    sp.add_argument("--timeout", type=int, default=12, help="请求超时秒数")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("ctx", help="压缩历史对话，生成可续接摘要")
    sp.add_argument("path", help="对话存档：json / jsonl / markdown / 纯文本")
    sp.add_argument("--target-tokens", type=int, default=6000, dest="target_tokens",
                    help="目标 token 预算（默认 6000）")
    sp.add_argument("--keep-recent", type=int, default=8, dest="keep_recent",
                    help="保留最近多少条原文（默认 8）")
    sp.add_argument("--out", help="把摘要写入文件")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("ui", help="UI 精准编辑（实验性，仅 Windows）")
    sp.add_argument("action", nargs="?", default="available",
                    choices=["available", "state", "ground", "set_value",
                             "select", "action"])
    sp.add_argument("--app", help="app_id / 进程名 / 标题片段 / hwnd:123 / pid:123")
    sp.add_argument("--element-id", dest="element_id", help="state 返回的元素编号")
    sp.add_argument("--text", help="ground 要查找的可见文本")
    sp.add_argument("--value", help="set_value 要写入的值")
    sp.add_argument("--exact", action="store_true", help="ground 精确匹配文本")
    sp.add_argument("--depth", type=int, default=3, help="元素树深度")
    sp.add_argument("--max-nodes", type=int, default=120, dest="max_nodes")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("reports", help="清理 reports 目录中的过期产物（TTL）")
    sp.add_argument("--days", type=int, default=reports.DEFAULT_TTL_DAYS,
                    help=f"保留天数，默认 {reports.DEFAULT_TTL_DAYS}；0=全部清理")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="只统计，不删除")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("scan", help="扫描目录")
    sp.add_argument("path")
    sp.add_argument("--no-recursive", action="store_true")
    sp.add_argument("--limit", type=int, default=config.DEFAULT_SCAN_LIMIT)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-detail", action="store_true")

    sp = sub.add_parser("formats", help="列出支持的格式")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("doctor", help="环境自检")
    sp.add_argument("--json", action="store_true")

    sub.add_parser("mcp", help="启动 MCP Server（stdio）")

    sp = sub.add_parser("err", help="错误码速查")
    sp.add_argument("keyword", nargs="?", help="编号(01001/1001) / 类别(01/文件访问) / 关键词(pdf)")
    sp.add_argument("--all", "--list", action="store_true", dest="all",
                    help="展开全部错误码与完整解决方案")
    sp.add_argument("--json", action="store_true", help="输出 JSON")

    sub.add_parser("menu", help="交互菜单")
    return p


def opts_from_args(a) -> ReadOptions:
    return ReadOptions(
        max_chars=a.max_chars if a.max_chars and a.max_chars > 0 else 0,
        max_bytes=a.max_bytes,
        encoding=a.encoding,
        pages=a.pages,
        sheet=a.sheet,
        member=getattr(a, "member", None),
        password=a.password,
        ocr=a.ocr,
        frames=getattr(a, "frames", None) or 12,
        start=getattr(a, "start", None),
        end=getattr(a, "end", None),
        frame_width=getattr(a, "frame_width", None) or 768,
        frame_quality=getattr(a, "frame_quality", None) or 80,
        include_audio=bool(getattr(a, "include_audio", False)),
        frame_dir=getattr(a, "frame_dir", None),
        sampling="keyframe" if getattr(a, "scene", False) else "uniform",
        subtitles=getattr(a, "subtitles", None),
        progress=_PROGRESS,
        threads=getattr(a, "threads", 0),
    )


# ── 子命令实现 ───────────────────────────────────────────────
def cmd_read(a) -> int:
    t0 = time.perf_counter()
    opts = opts_from_args(a)
    doc = dispatch.read_file(a.path, opts, a.format)
    mode = "json" if a.json else "md" if a.md else "text"
    text = output.render(doc, mode, opts.max_chars)
    _emit(text, a.out)
    if not a.json:
        ui.note(f"完成 · {time.perf_counter() - t0:.2f}s · {doc.format}")
    return 0


def cmd_text(a) -> int:
    text = dispatch.extract_text(
        a.path, ReadOptions(max_chars=a.max_chars or 0, pages=a.pages), a.format)
    if not text.strip():
        ui.warn("该文件未提取到文本内容（可能是纯图片/空文档）")
    _emit(text, a.out)
    return 0


def cmd_info(a) -> int:
    info = dispatch.file_info(a.path)
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print(ui.banner())
    print(ui.kv_block({
        "路径": info["path"],
        "格式": info["format"] + (f" / {info['subtype']}" if info.get("subtype") else ""),
        "MIME": info["mime"],
        "大小": f"{human_size(info['size'])} ({info['size']} 字节)",
        "修改时间": info["mtime"],
        "识别依据": info["evidence"],
        "置信度": info["confidence"],
        "SHA256": info["sha256"],
    }))
    return 0


def cmd_video(a) -> int:
    t0 = time.perf_counter()
    opts = ReadOptions(
        frames=a.frames or 12,
        start=a.start,
        end=a.end,
        frame_width=a.frame_width or 768,
        frame_quality=a.frame_quality or 80,
        include_audio=a.include_audio,
        frame_dir=a.frame_dir,
        sampling="keyframe" if a.scene else "uniform",
        progress=_PROGRESS,
        threads=getattr(a, "threads", 0),
    )
    doc = dispatch.read_file(a.path, opts)
    if a.json:
        print(json.dumps(doc.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0
    text = output.render(doc, "text")
    if a.out:
        _emit(text, a.out)
    else:
        print(text)
    budget = doc.metadata.get("budget")
    if budget:
        print()
        ui.info(f"画面预算：{budget['frames']} 帧 · 图片 {budget['image_bytes_human']} · "
                f"base64 {budget['base64_human']} · 约 {budget['tokens_estimate']} tokens")
        ui.info("这些帧已落盘；MCP 模式会把它们作为图片内容直接交给模型")
    ui.note(f"完成 · {time.perf_counter() - t0:.2f}s")
    return 0


def cmd_storyboard(a) -> int:
    t0 = time.perf_counter()
    opts = ReadOptions(
        storyboard=True,
        cells=a.cells,
        sheets=a.sheets,
        storyboard_budget=a.storyboard_budget,
        start=a.start,
        end=a.end,
        subtitles=getattr(a, "subtitles", None),
        progress=_PROGRESS,
        threads=getattr(a, "threads", 0),
    )
    doc = dispatch.read_file(a.path, opts)
    if a.json:
        print(json.dumps(doc.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0
    print(output.render(doc, "text"))
    sb = doc.metadata.get("storyboard") or {}
    if sb:
        t = sb.get("timings") or {}
        print()
        ui.info(f"{sb['cells']} 格 · {sb['cuts']} 个切点 · "
                f"{len(sb['sheets'])} 张图 · 约 {sb['tokens_estimate']} tokens · "
                f"耗时 {t.get('total', '?')}s（切点 {t.get('cuts', '?')}s / "
                f"取帧 {t.get('grab', '?')}s）")
        for p in sb["sheets"]:
            print("  " + p)
    ui.note(f"完成 · {time.perf_counter() - t0:.2f}s · 线程 {sb.get('workers', 1)}")
    return 0


def cmd_zoom(a) -> int:
    box = None
    if a.box:
        try:
            box = [float(x) for x in a.box.split(",")]
        except ValueError:
            raise AtbError("ATB-E00003", f"--box 需为 x,y,w,h 四个 0-1 数字：{a.box}")
        if len(box) != 4:
            raise AtbError("ATB-E00003", f"--box 需为 4 个数字：{a.box}")
    r = export_zoom(a.path, a.at, box, a.width, a.quality)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    ui.info(f"{r['time']}s 的画面 · {r['width']}×{r['height']} · "
            f"约 {r['tokens_estimate']} tokens")
    print("  " + r["path"])
    return 0


def cmd_quota(a) -> int:
    if a.init:
        if os.path.exists(quota.CONFIG_PATH):
            ui.info(f"配置文件已存在：{quota.CONFIG_PATH}")
        else:
            os.makedirs(os.path.dirname(quota.CONFIG_PATH), exist_ok=True)
            with open(quota.CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(quota.config_template())
            ui.info(f"已生成模板：{quota.CONFIG_PATH}")
        ui.note("填入 api_key 后运行 `atb quota`；也可用 --set deepseek=sk-... 直接写入")
        return 0

    if a.set:
        if "=" not in a.set:
            raise AtbError("ATB-E00003", "--set 格式为 PROVIDER=KEY")
        name, key = (s.strip() for s in a.set.split("=", 1))
        if name not in quota.PROVIDERS:
            raise AtbError("ATB-E00003",
                           f"未知提供商 {name}；可选：{', '.join(quota.PROVIDERS)}")
        if not key:
            raise AtbError("ATB-E00003", "密钥不能为空")
        path = quota.set_key(name, key)
        ui.info(f"已保存 {name} 密钥（{quota.mask(key)}）→ {path}")
        return 0

    if a.unset:
        if a.unset not in quota.PROVIDERS:
            raise AtbError("ATB-E00003", f"未知提供商 {a.unset}")
        quota.remove_key(a.unset)
        ui.info(f"已删除 {a.unset} 的密钥")
        return 0

    if a.show:
        names = [a.provider] if a.provider else list(quota.PROVIDERS)
        rows = []
        for n in names:
            if n not in quota.PROVIDERS:
                raise AtbError("ATB-E00003", f"未知提供商 {n}")
            key, source = quota.get_key(n)
            rows.append([n, quota.PROVIDERS[n]["label"], source or "-",
                         quota.mask(key), quota.PROVIDERS[n]["env"]])
        print(ui.table(["provider", "名称", "来源", "密钥", "环境变量"], rows))
        print()
        ui.note(f"配置文件：{quota.CONFIG_PATH}")
        return 0

    providers = [a.provider] if a.provider else None
    if providers and providers[0] not in quota.PROVIDERS:
        raise AtbError("ATB-E00003",
                       f"未知提供商 {providers[0]}；可选：{', '.join(quota.PROVIDERS)}")
    results = quota.query_all(providers, max(3, int(a.timeout)))
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    header, rows = quota.format_rows(results)
    print(ui.table(header, rows))
    if not any(r.get("configured") for r in results):
        print()
        ui.note("尚未配置任何密钥：atb quota --init 生成模板，"
                "或 atb quota --set deepseek=sk-...")
    return 0


def cmd_ctx(a) -> int:
    result = history.compress_file(a.path, a.target_tokens, a.keep_recent)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _emit(result["markdown"], a.out)
    ui.note(f"{result['messages']} 条消息 · {result['original_tokens']} → "
            f"{result['compressed_tokens']} tokens "
            f"（保留 {result['ratio'] * 100:.1f}%）")
    return 0


def cmd_ui(a) -> int:
    from atb.experimental import computer

    params = {
        "app": a.app, "element_id": a.element_id, "text": a.text,
        "value": a.value, "exact": a.exact or None,
        "depth": a.depth, "max_nodes": a.max_nodes,
    }
    result = computer.dispatch(a.action, {k: v for k, v in params.items() if v is not None})
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if a.action == "state":
        app = result["app"]
        print(ui.kv_block({
            "应用": app["app_id"],
            "标题": app["title"],
            "窗口": app["hwnd"],
            "区域": "×".join(str(v) for v in app["bounds"]),
            "元素来源": result["source"],
            "元素数": result["node_count"],
        }))
        rows = [[n["element_id"], n["type"], (n["name"] or "")[:40],
                 "×".join(str(v) for v in n["bounds"]) if n.get("bounds") else "-"]
                for n in result["nodes"][:40]]
        if rows:
            print()
            print(ui.table(["element_id", "类型", "名称", "区域"], rows))
        if result.get("warning"):
            ui.warn(result["warning"])
    elif a.action == "ground":
        if result.get("found"):
            print(ui.kv_block({
                "元素": result["element_id"],
                "名称": result.get("name", ""),
                "类型": result.get("type", ""),
                "区域": "×".join(str(v) for v in (result.get("bounds") or [])),
            }))
        else:
            ui.warn(result.get("hint") or "未找到匹配元素")
    elif a.action == "available":
        state = "可用" if result["ok"] else "不可用"
        print(ui.kv_block({
            "UI 精准编辑": state,
            "平台": result["platform"],
            "UI 自动化": "已安装" if result["uiautomation"] else "未安装",
            "说明": result["reason"] or "语义化编辑能力就绪",
        }))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_reports(a) -> int:
    result = reports.cleanup(a.days, a.dry_run)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    verb = "可清理" if result["dry_run"] else "已清理"
    ui.info(f"{verb} {result['removed_files']} 个文件、{result['removed_dirs']} 个空目录，"
            f"释放 {reports.human_bytes(result['freed_bytes'])}")
    ui.note(f"保留最近 {result['ttl_days']} 天；当前仍有 {result['kept_files']} 个文件，"
            f"目录：{result['base']}")
    return 0


def cmd_scan(a) -> int:
    result = scan_directory(a.path, recursive=not a.no_recursive,
                            limit=a.limit, include_detail=not a.no_detail)
    mode = "json" if a.json else "text"
    print(output.render_scan(result, mode))
    return 0


def cmd_formats(a) -> int:
    matrix = format_matrix()
    if a.json:
        print(json.dumps(matrix, ensure_ascii=False, indent=2))
        return 0
    print(ui.banner())
    rows = []
    for fmt, info in matrix["formats"].items():
        exts = info["extensions"]
        shown = ", ".join(exts[:14]) + ("…" if len(exts) > 14 else "")
        rows.append([fmt, info["reader"], len(exts), shown])
    print(ui.table(["格式", "解析器", "扩展名数", "常见扩展名"], rows))
    print()
    ui.info(f"共 {len(matrix['formats'])} 类格式、{matrix['total_extensions']} 个扩展名；"
            f"ZIP 容器自动细分：{', '.join(matrix['zip_family'])}")
    return 0


def cmd_doctor(a) -> int:
    checks = _doctor()
    if a.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0
    print(ui.banner())
    rows = [[c["item"], c["status"], c["detail"]] for c in checks]
    print(ui.table(["检查项", "状态", "说明"], rows, max_col=70))
    missing_deps = [c for c in checks
                    if c["status"] == "缺失" and c["item"].startswith("依赖 ")]
    missing_opt = [c for c in checks
                   if c["status"] == "缺失" and c["item"].startswith("可选 ")]
    if missing_deps:
        ui.warn(f"有 {len(missing_deps)} 个解析依赖缺失，对应格式会降级解析。")
        ui.info(f"完整安装：{config.PROG} 所在项目内执行 "
                f"python -m pip install -r requirements.txt")
    if missing_opt:
        ui.info("可选外部工具未安装：" + "、".join(c["item"].replace("可选 ", "")
                                              for c in missing_opt)
                + "（仅影响音视频深解析与扫描件 OCR）")
    if not missing_deps and not missing_opt:
        ui.ok("环境完整，所有格式均可完整解析。")
    return 0


def cmd_mcp(a) -> int:
    # stdout 是 MCP 的 JSON-RPC 通道，任何提示都必须走 stderr
    print(ui.paint("[i] ", ui.C.CYAN) + "MCP Server 已启动（stdio，Ctrl+C 退出）",
          file=sys.stderr)
    return mcp_main()


def cmd_err(a) -> int:
    key = (a.keyword or "").strip()
    as_json = bool(getattr(a, "json", False))

    # 1) 展开全部
    if getattr(a, "all", False):
        entries = all_errors(config.PROG)
        if as_json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return 0
        print(ui.banner())
        print(ui.paint(f"全部错误码 · {len(entries)} 条", ui.C.BOLD, ui.C.CYAN))
        current = None
        for e in entries:
            if e["category_id"] != current:
                current = e["category_id"]
                print()
                print(ui.paint(f"【{current}】{e['category']}", ui.C.BOLD, ui.C.CYAN))
            _print_err_detail(e, show_category=False)
        print()
        return 0

    # 2) 无参数：分类总览 + 用法
    if not key:
        cats = list_categories()
        if as_json:
            payload = {
                "total": len(ERRORS),
                "categories": {
                    cid: {"name": info["name"], "count": len(info["codes"]),
                          "codes": info["codes"],
                          "errors": [_err_entry(c) for c in info["codes"]]}
                    for cid, info in cats.items()
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        _print_err_overview(cats)
        return 0

    # 3) 精确编号
    code = normalize_code(key)
    if code:
        entry = _err_entry(code)
        if as_json:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        else:
            print()
            print(ui.paint(f"{entry['code']}  {entry['title']}", ui.C.BOLD, ui.C.RED))
            _print_err_detail(entry, show_code_row=False)
        return 0

    # 4) 类别（01 / 1 / 文件访问 / PDF）
    cat = match_category(key)
    if cat:
        entries = [_err_entry(c) for c in list_categories()[cat]["codes"]]
        if as_json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return 0
        print()
        print(ui.paint(f"[{cat}] {entries[0]['category']}", ui.C.BOLD, ui.C.CYAN)
              + ui.paint(f"  {len(entries)} 条", ui.C.GREY))
        _print_err_rows(entries)
        return 0

    # 5) 关键词搜索
    hits = search_errors(key, config.PROG)
    if hits:
        if as_json:
            print(json.dumps(hits, ensure_ascii=False, indent=2))
            return 0
        print()
        print(ui.paint(f"搜索 “{key}” 命中 {len(hits)} 条", ui.C.BOLD, ui.C.CYAN))
        _print_err_rows(hits)
        return 0

    ui.error(f"未找到与 {key!r} 匹配的错误码")
    print(ui.paint("  试试：", ui.C.GREY) + f"{config.PROG} err 01  /  {config.PROG} err pdf  /  "
          f"{config.PROG} err --all")
    return 1


def _err_entry(code: str) -> dict:
    entry = lookup(code, config.PROG) or {"code": code, "title": "", "category": "",
                                          "solution": ""}
    entry["category_id"] = code[5:7]
    return entry


def _print_err_overview(cats: dict) -> None:
    print(ui.banner())
    print(ui.paint(f"共 {len(ERRORS)} 条错误码 · {len(cats)} 个类别", ui.C.BOLD, ui.C.WHITE))
    for cid, info in cats.items():
        print()
        print(ui.paint(f"[{cid}]", ui.C.CYAN) + " " + ui.paint(info["name"], ui.C.BOLD)
              + ui.paint(f"  {len(info['codes'])} 条", ui.C.GREY))
        names = " · ".join(ERRORS[c]["title"] for c in info["codes"])
        for line in ui.wrap_text(names, indent="      "):
            print(ui.paint(line, ui.C.GREY))
    print()
    print(ui.paint("用法", ui.C.BOLD))
    for tip in (
        f"{config.PROG} err 01001      单条详情（完整解决方案）",
        f"{config.PROG} err 01         列出该类别全部",
        f"{config.PROG} err pdf        关键词搜索",
        f"{config.PROG} err --all      展开全部错误码",
        f"{config.PROG} err --json     结构化输出",
    ):
        print("  " + tip)


def _print_err_rows(entries: list) -> None:
    for e in entries:
        print(f"  {ui.paint(e['code'], ui.C.WHITE)}  {e['title']}")
    print()
    print(ui.paint("查看完整解决方案：", ui.C.GREY)
          + f"{config.PROG} err {entries[0]['code'][5:]}")


def _print_err_detail(e: dict, show_header: bool = False, show_category: bool = True,
                      show_code_row: bool = True) -> None:
    if show_header:
        print()
        print(ui.paint(f"{e['code']}  {e['title']}", ui.C.BOLD, ui.C.RED))
    elif show_code_row:
        print(f"  {ui.paint(e['code'], ui.C.WHITE)}  {ui.paint(e['title'], ui.C.BOLD)}")
    if show_category:
        print(ui.paint(f"  类别  {e['category']}", ui.C.GREY))
    print(ui.paint("  解决方案", ui.C.GREY))
    for line in ui.wrap_text(e["solution"], indent="    "):
        print(line)
    print()


# ── 交互菜单 ─────────────────────────────────────────────────
def interactive() -> int:
    print(ui.banner())
    while True:
        print()
        for key, (label, _) in MENU.items():
            print(f"  {ui.paint(f'[{key}]', ui.C.CYAN)} {label}")
        try:
            choice = input(ui.paint("\n请选择 > ", ui.C.BOLD, ui.C.GREEN)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        action = MENU.get(choice, (None, None))[1]
        if action in (None,):
            ui.warn("无效选项")
            continue
        if action == "exit":
            ui.ok("再见")
            return 0
        try:
            _menu_action(action)
        except AtbError as e:
            _print_error(e)
        except KeyboardInterrupt:
            print()
            ui.warn("已取消")


def _menu_action(action: str) -> None:
    if action == "mcp":
        cmd_mcp(None)
        return
    if action == "formats":
        cmd_formats(argparse.Namespace(json=False))
        return
    if action == "doctor":
        cmd_doctor(argparse.Namespace(json=False))
        return
    if action == "err":
        cmd_err(argparse.Namespace(keyword=None, all=True, json=False))
        return
    if action == "ui":
        print(ui.paint("UI 精准编辑（实验性，仅 Windows）：", ui.C.BOLD))
        for line in (
            "atb ui available                              查看能力",
            "atb ui state --app <标题/进程>                读取元素树",
            "atb ui ground --app <关键词> --text \"保存\"    按文本定位元素",
            "atb ui set_value --app <关键词> --element-id 0.1.2 --value \"文本\"",
            "atb ui select --app <关键词> --element-id 0.1.3",
            "atb ui action --app <关键词> --element-id 0.1.4",
        ):
            print("  " + line)
        return
    if action == "ctx":
        path = input(ui.paint("对话存档路径 > ", ui.C.GREEN)).strip().strip('"')
        if not path:
            ui.warn("未输入路径")
            return
        cmd_ctx(argparse.Namespace(path=path, target_tokens=6000,
                                   keep_recent=8, out=None, json=False))
        return

    path = input(ui.paint("路径 > ", ui.C.GREEN)).strip().strip('"')
    if not path:
        ui.warn("未输入路径")
        return
    if action == "read":
        doc = dispatch.read_file(path, ReadOptions())
        fmt = input(ui.paint("输出格式 text/json/md [text] > ", ui.C.GREEN)).strip() or "text"
        print(output.render(doc, fmt if fmt in ("text", "json", "md") else "text"))
    elif action == "text":
        print(dispatch.extract_text(path))
    elif action == "info":
        cmd_info(argparse.Namespace(path=path, json=False))
    elif action == "video":
        cmd_video(argparse.Namespace(
            path=path, frames=12, start=None, end=None, frame_width=768,
            frame_quality=80, include_audio=False, frame_dir=None,
            scene=False, json=False, out=None, threads=0))
    elif action == "storyboard":
        cmd_storyboard(argparse.Namespace(
            path=path, cells=None, sheets=1, storyboard_budget=1_200_000,
            start=None, end=None, subtitles=None, json=False, threads=0))
    elif action == "quota":
        cmd_quota(argparse.Namespace(
            provider=None, set=None, unset=None, init=False, show=False,
            timeout=12, json=False))
    elif action == "scan":
        result = scan_directory(path)
        print(output.render_scan(result))


# ── doctor 实现 ──────────────────────────────────────────────
def _doctor() -> list:
    checks = []

    def add(item, ok, detail):
        checks.append({"item": item, "status": "正常" if ok else "缺失", "detail": detail})

    py = platform.python_version()
    add("Python", sys.version_info >= (3, 10), f"{py} ({platform.system()} {platform.machine()})")

    deps = [
        ("pymupdf", "PDF"),
        ("docx", "DOCX"),
        ("openpyxl", "XLSX"),
        ("xlrd", "XLS"),
        ("pptx", "PPTX"),
        ("odf", "ODT/ODS/ODP"),
        ("olefile", "旧版 Office / MSG"),
        ("striprtf", "RTF"),
        ("bs4", "HTML/EPUB"),
        ("lxml", "HTML 高性能后端"),
        ("yaml", "YAML"),
        ("PIL", "图片"),
        ("mutagen", "音视频标签"),
        ("av", "视频抽帧"),
        ("py7zr", "7Z"),
        ("rarfile", "RAR"),
        ("chardet", "编码探测"),
        ("pyarrow", "Parquet"),
    ]
    missing = []
    for module, label in deps:
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "") or getattr(mod, "VERSION", "")
            add(f"依赖 {module}", True, f"{label}{(' ' + str(version)) if version else ''}")
        except ImportError:
            missing.append(module)
            add(f"依赖 {module}", False, f"{label} 解析将降级")
    add("可选 ffprobe", bool(shutil.which("ffprobe")),
        shutil.which("ffprobe") or "未安装 ffmpeg，音视频仅用 mutagen")
    add("可选 tesseract", bool(shutil.which("tesseract")),
        shutil.which("tesseract") or "未安装，不能对扫描件 OCR")
    try:
        import uiautomation as _uia  # noqa: F401
        add("可选 uiautomation", True, "UI 精准编辑元素树可用")
    except Exception:
        add("可选 uiautomation", False, "未安装，UI 精准编辑退化为子窗口枚举（仅 Windows）")

    probe = os.path.join(config.REPORT_DIR, ".write_test")
    try:
        os.makedirs(config.REPORT_DIR, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        writable = True
    except OSError:
        writable = False
    add("报告目录可写", writable, config.REPORT_DIR)

    matrix = format_matrix()
    add("格式覆盖", True, f"{len(matrix['formats'])} 类 / {matrix['total_extensions']} 扩展名")
    add("依赖完整性", not missing, ", ".join(missing) if missing else "解析依赖齐全")
    return checks


# ── 公共 ─────────────────────────────────────────────────────
def _auto_cleanup_reports(cmd: str = None) -> None:
    """按 TTL 静默清理 reports/ 下的自动产物；失败不影响主流程。

    常驻的 MCP Server 不自动清理：避免后台服务在无人值守时删文件。
    """
    if cmd == "mcp":
        return
    ttl = getattr(config, "REPORT_TTL_DAYS", 0)
    if ttl <= 0:
        return
    try:
        reports.cleanup(ttl)
    except Exception:
        pass


def _emit(text: str, out_path: str = None) -> None:
    if out_path:
        output.write_output(text, out_path)
        ui.ok(f"已写入 {os.path.abspath(out_path)}")
    else:
        print(text)


def _print_error(err: AtbError) -> None:
    print()
    ui.error(f"{err.code}  {err.title}")
    if err.detail:
        print("   详情   " + err.detail)
    print("   建议   " + err.solution.replace("{prog}", config.PROG))
    log_error(err)


def main(argv=None) -> int:
    global _PROGRESS
    ui.init()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False):
        ui._COLOR["on"] = False
    _PROGRESS = make_progress(False if getattr(args, "no_progress", False) else None)
    _auto_cleanup_reports(args.cmd)

    if not args.cmd or args.cmd == "menu":
        return interactive()

    handlers = {
        "read": cmd_read, "text": cmd_text, "info": cmd_info, "video": cmd_video,
        "storyboard": cmd_storyboard, "zoom": cmd_zoom, "quota": cmd_quota,
        "ctx": cmd_ctx, "ui": cmd_ui, "reports": cmd_reports,
        "scan": cmd_scan, "formats": cmd_formats,
        "doctor": cmd_doctor, "mcp": cmd_mcp, "err": cmd_err,
    }
    try:
        return handlers[args.cmd](args)
    except AtbError as e:
        _PROGRESS.close()
        _print_error(e)
        return 2
    except KeyboardInterrupt:
        _PROGRESS.close()
        print()
        err = AtbError("ATB-E00002")
        ui.warn(f"{err.code}  {err.title}")
        return 130
    except Exception as e:
        _PROGRESS.close()
        err = AtbError("ATB-E00001", f"{type(e).__name__}: {e}")
        _print_error(err)
        if os.environ.get("ATB_DEBUG"):
            traceback.print_exc()
        else:
            print(ui.paint("   提示   设置 ATB_DEBUG=1 可查看完整 traceback", ui.C.GREY))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
