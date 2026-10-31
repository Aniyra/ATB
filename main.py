# -*- coding: utf-8 -*-
"""ATB — All Tool Box 命令行入口。

用法：
    python main.py read <文件> [--json|--md] [--format F] [--max-chars N]
    python main.py text <文件>
    python main.py info <文件>
    python main.py scan <目录> [--no-recursive] [--json]
    python main.py formats [--json]
    python main.py doctor [--json]
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
import traceback

import config
from atb import dispatch, output, ui
from atb.errors import AtbError, ERRORS, log_error, lookup
from atb.mcp import format_matrix, main as mcp_main
from atb.models import ReadOptions, human_size
from atb.scanner import scan_directory

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
    sp.add_argument("keyword", nargs="?")
    sp.add_argument("--list", action="store_true")

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
    )


# ── 子命令实现 ───────────────────────────────────────────────
def cmd_read(a) -> int:
    opts = opts_from_args(a)
    doc = dispatch.read_file(a.path, opts, a.format)
    mode = "json" if a.json else "md" if a.md else "text"
    text = output.render(doc, mode, opts.max_chars)
    _emit(text, a.out)
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
    opts = ReadOptions(
        frames=a.frames or 12,
        start=a.start,
        end=a.end,
        frame_width=a.frame_width or 768,
        frame_quality=a.frame_quality or 80,
        include_audio=a.include_audio,
        frame_dir=a.frame_dir,
        sampling="keyframe" if a.scene else "uniform",
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
    if a.list or not a.keyword:
        rows = [[code, info["title"], info["solution"].replace("{prog}", config.PROG)[:60]]
                for code, info in ERRORS.items()]
        print(ui.banner())
        print(ui.table(["编号", "问题", "解决方案"], rows, max_col=60))
        return 0
    found = lookup(a.keyword, config.PROG)
    if not found:
        ui.error(f"未找到与 {a.keyword!r} 匹配的错误码")
        return 1
    print(ui.kv_block({"编号": found["code"], "类别": found["category"],
                       "问题": found["title"], "解决方案": found["solution"]}))
    return 0


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
        cmd_err(argparse.Namespace(keyword=None, list=True))
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
            scene=False, json=False, out=None))
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
    ui.init()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False):
        ui._COLOR["on"] = False

    if not args.cmd or args.cmd == "menu":
        return interactive()

    handlers = {
        "read": cmd_read, "text": cmd_text, "info": cmd_info, "video": cmd_video,
        "scan": cmd_scan, "formats": cmd_formats, "doctor": cmd_doctor,
        "mcp": cmd_mcp, "err": cmd_err,
    }
    try:
        return handlers[args.cmd](args)
    except AtbError as e:
        _print_error(e)
        return 2
    except KeyboardInterrupt:
        print()
        ui.warn("已中断")
        return 130
    except Exception as e:
        err = AtbError("ATB-E00001", f"{type(e).__name__}: {e}")
        _print_error(err)
        if os.environ.get("ATB_DEBUG"):
            traceback.print_exc()
        else:
            print(ui.paint("   提示   设置 ATB_DEBUG=1 可查看完整 traceback", ui.C.GREY))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
