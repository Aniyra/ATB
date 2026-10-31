# -*- coding: utf-8 -*-
"""终端 UI：ANSI 颜色、横幅、表格对齐。参考 D:\\pro\\rs 的 CLI 视觉风格。"""

import os
import shutil
import sys

from config import APP_DESC, APP_NAME, APP_VERSION


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"
    WHITE = "\033[97m"


def enable_ansi() -> None:
    """Windows 控制台开启 VT 序列支持（失败则静默降级）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False


_COLOR = {"on": True}


def init() -> None:
    enable_ansi()
    _COLOR["on"] = color_enabled()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def paint(text: str, *styles: str) -> str:
    if not _COLOR["on"] or not styles:
        return text
    return "".join(styles) + str(text) + C.RESET


def width(default: int = 92) -> int:
    return max(60, min(shutil.get_terminal_size((default, 24)).columns, 160))


def banner() -> str:
    w = width()
    title = f"{APP_NAME}  v{APP_VERSION}"
    sub = APP_DESC
    line = "─" * (w - 2)
    return "\n".join([
        paint("┌" + line + "┐", C.CYAN),
        paint("│", C.CYAN) + paint(" " + _fit(title, w - 3), C.BOLD, C.CYAN)
        + paint("│", C.CYAN),
        paint("│", C.CYAN) + paint(" " + _fit(sub, w - 3), C.GREY) + paint("│", C.CYAN),
        paint("└" + line + "┘", C.CYAN),
    ])


def hr(char: str = "─") -> str:
    return paint(char * width(), C.GREY)


def _disp_len(s: str) -> int:
    """中文按 2 列计算宽度。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def _pad(s: str, target: int) -> str:
    return str(s) + " " * max(0, target - _disp_len(s))


def _fit(s: str, target: int) -> str:
    """按显示宽度补空格；超宽时截断并加省略号。"""
    s = str(s)
    if _disp_len(s) <= target:
        return _pad(s, target)
    acc, buf = 0, []
    for ch in s:
        step = 2 if ord(ch) > 0x2E80 else 1
        if acc + step > target - 1:
            break
        acc += step
        buf.append(ch)
    return _pad("".join(buf) + "…", target)


def wrap_text(text: str, indent: str = "", limit: int = None) -> list:
    """按显示宽度换行（中文按 2 列），用于长句、解决方案等。"""
    limit = limit or max(40, width() - _disp_len(indent) - 2)
    out = []
    for para in str(text).split("\n"):
        if not para:
            out.append(indent)
            continue
        cur, cur_w = "", 0
        for ch in para:
            step = 2 if ord(ch) > 0x2E80 else 1
            if cur_w + step > limit:
                out.append(indent + cur)
                cur, cur_w = ch, step
            else:
                cur += ch
                cur_w += step
        out.append(indent + cur)
    return out


def table(headers, rows, max_col: int = 60) -> str:
    """简易等宽表格（支持中文对齐）。"""
    cells = [[("" if c is None else str(c)) for c in row] for row in rows]
    all_rows = [list(map(str, headers))] + cells if headers else cells
    cols = max((len(r) for r in all_rows), default=0)
    widths = []
    for i in range(cols):
        w = max((_disp_len(r[i]) if i < len(r) else 0) for r in all_rows)
        widths.append(min(max(w, 3), max_col))

    def render_row(row, style=""):
        out = []
        for i in range(cols):
            v = row[i] if i < len(row) else ""
            v = str(v)
            if _disp_len(v) > widths[i]:
                # 截断到列宽（按显示宽度）
                acc, buf = 0, []
                for ch in v:
                    step = 2 if ord(ch) > 0x2E80 else 1
                    if acc + step > widths[i] - 1:
                        break
                    acc += step
                    buf.append(ch)
                v = "".join(buf) + "…"
            out.append(_pad(v, widths[i]))
        return "  ".join(out)

    lines = []
    if headers:
        lines.append(paint(render_row(list(map(str, headers))), C.BOLD, C.CYAN))
        lines.append(paint("  ".join("─" * w for w in widths), C.GREY))
    for row in cells:
        lines.append(render_row(row))
    return "\n".join(lines)


def kv_block(data: dict, indent: int = 2) -> str:
    if not data:
        return ""
    key_w = max((_disp_len(k) for k in data), default=0)
    out = []
    for k, v in data.items():
        out.append(" " * indent + paint(_pad(k, key_w + 2), C.GREY) + paint(str(v), C.WHITE))
    return "\n".join(out)


def info(msg: str) -> None:
    print(paint("[i] ", C.CYAN) + msg)


def ok(msg: str) -> None:
    print(paint("[✓] ", C.GREEN) + msg)


def warn(msg: str) -> None:
    print(paint("[!] ", C.YELLOW) + msg, file=sys.stderr)


def error(msg: str) -> None:
    print(paint("[×] ", C.RED) + msg, file=sys.stderr)
