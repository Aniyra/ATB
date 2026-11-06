# -*- coding: utf-8 -*-
"""实验性：电脑控制（复刻 Trae Computer Use 的基础能力）。

设计原则：
- 仅 Windows，用 ctypes 调 Win32 API，**不引入必需的新依赖**；
- UI 元素树优先用可选依赖 uiautomation，缺失时退化为 Win32 子窗口枚举；
- 所有失败都抛 AtbError（带错误码与解决方案），不污染 ATB 的其他能力；
- 实验性：行为可能随版本调整，不保证在所有应用上可用。

能力对照（Trae 工具 → 本模块）：
  list_apps / launch_app / get_app_state / click / scroll / drag / type_text /
  press_key / select_text / set_value / perform_action / grounding  → 已实现基础版
  resolve_recorded_step / verify_recorded_step（录制回放）           → 未实现
"""

import ctypes
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

import config
from ..errors import AtbError

IS_WINDOWS = sys.platform == "win32"

# 子窗口 / 元素树默认上限，避免一次吐爆上下文
DEFAULT_DEPTH = 3
DEFAULT_MAX_NODES = 120

if IS_WINDOWS:
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        _dwm = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover - 极老系统
        _dwm = None

    _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetWindowTextLengthW.restype = ctypes.c_int
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.restype = wintypes.BOOL
    _u32.IsWindow.argtypes = [wintypes.HWND]
    _u32.IsWindow.restype = wintypes.BOOL
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _u32.GetWindowRect.restype = wintypes.BOOL
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.SetForegroundWindow.restype = wintypes.BOOL
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _u32.SetCursorPos.restype = wintypes.BOOL
    _u32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.c_void_p]
    _u32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD,
                                 ctypes.c_void_p]
    _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u32.EnumWindows.restype = wintypes.BOOL
    _u32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
    _u32.EnumChildWindows.restype = wintypes.BOOL
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetWindowLongW.restype = ctypes.c_long

    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    if _dwm is not None:
        _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                               ctypes.c_void_p, wintypes.DWORD]

_DPI_DONE = False


# ── 基础 ─────────────────────────────────────────────────────
def _require_windows() -> None:
    if not IS_WINDOWS:
        raise AtbError("ATB-E13001",
                       f"当前平台为 {sys.platform}；电脑控制仅支持 Windows")


def _enable_dpi() -> None:
    """统一坐标系：让截图、窗口矩形与鼠标坐标都按物理像素计算。"""
    global _DPI_DONE
    if _DPI_DONE or not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_DONE = True


def available() -> dict:
    """返回本机电脑控制能力（供 doctor / MCP 说明用）。"""
    _enable_dpi()
    uia = _uia() is not None
    shot = _pillow() is not None
    if not IS_WINDOWS:
        return {"ok": False, "platform": sys.platform, "uiautomation": uia,
                "screenshot": shot,
                "reason": "仅支持 Windows；其他平台请使用系统自带自动化工具"}
    return {"ok": True, "platform": sys.platform, "uiautomation": uia,
            "screenshot": shot,
            "reason": "" if uia else "未安装可选的 uiautomation，元素树退化为子窗口枚举"}


def _uia():
    try:
        import uiautomation as auto  # type: ignore
        auto.SetGlobalSearchTimeout(1.5)
        return auto
    except Exception:
        return None


def _pillow():
    try:
        from PIL import ImageGrab  # noqa: F401
        return True
    except Exception:
        return False


def _now() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# ── 窗口枚举 ─────────────────────────────────────────────────
def _window_title(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_name(pid: int) -> str:
    handle = _k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        _k32.CloseHandle(handle)
    return ""


def _window_bounds(hwnd: int):
    rect = _RECT()
    if not _u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]


def _is_cloaked(hwnd: int) -> bool:
    if _dwm is None:
        return False
    cloaked = wintypes.DWORD(0)
    try:
        _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                   ctypes.sizeof(cloaked))
        return bool(cloaked.value)
    except Exception:
        return False


def _iter_top_windows() -> list:
    hwnds = []

    @_WNDENUMPROC
    def cb(hwnd, _lparam):
        hwnds.append(int(hwnd))
        return True

    _u32.EnumWindows(cb, 0)
    return hwnds


def _iter_child_windows(hwnd: int) -> list:
    hwnds = []

    @_WNDENUMPROC
    def cb(child, _lparam):
        hwnds.append(int(child))
        return True

    _u32.EnumChildWindows(hwnd, cb, 0)
    return hwnds


def _window_info(hwnd: int, fg: Optional[int] = None) -> Optional[dict]:
    bounds = _window_bounds(hwnd)
    if not bounds or bounds[2] <= 0 or bounds[3] <= 0:
        return None
    pid = _window_pid(hwnd)
    proc = _process_name(pid)
    return {
        "app_id": f"{proc or 'unknown'}:{pid}",
        "window_id": hwnd,
        "hwnd": hwnd,
        "title": _window_title(hwnd),
        "class": _window_class(hwnd),
        "pid": pid,
        "process": proc,
        "bounds": bounds,
        "foreground": hwnd == (fg if fg is not None else _u32.GetForegroundWindow()),
    }


# ── 应用定位 ─────────────────────────────────────────────────
def list_apps(limit: int = 50, include_empty: bool = False) -> list:
    _require_windows()
    _enable_dpi()
    fg = _u32.GetForegroundWindow()
    out = []
    for hwnd in _iter_top_windows():
        if not _u32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            continue
        ex_style = _u32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
            continue
        info = _window_info(hwnd, fg)
        if not info:
            continue
        if not include_empty and not info["title"]:
            continue
        out.append(info)
    out.sort(key=lambda w: (not w["foreground"], w["process"], w["title"]))
    return out[:max(1, limit)]


def _resolve_app(app) -> dict:
    """把 app_id / hwnd / pid / 进程名 / 标题片段 解析为窗口信息。"""
    _require_windows()
    if isinstance(app, int):
        if not _u32.IsWindow(app):
            raise AtbError("ATB-E13002", f"窗口句柄无效：{app}")
        info = _window_info(app)
        if not info:
            raise AtbError("ATB-E13002", f"窗口不可见或尺寸为 0：{app}")
        return info

    text = str(app or "").strip()
    if not text:
        fg = _u32.GetForegroundWindow()
        info = _window_info(fg) if fg else None
        if not info:
            raise AtbError("ATB-E13002", "当前没有前台窗口，请传入 app")
        return info
    if text.startswith("hwnd:"):
        return _resolve_app(int(text.split(":", 1)[1]))
    if text.isdigit():
        return _resolve_app(int(text))

    apps = list_apps(limit=400, include_empty=True)
    lowered = text.lower()
    for item in apps:                      # app_id 精确
        if item["app_id"].lower() == lowered:
            return item
    for item in apps:                      # 进程名 / 标题片段
        if lowered in item["process"].lower() or lowered in item["title"].lower():
            return item
    if text.startswith("pid:"):
        pid = int(text.split(":", 1)[1])
        for item in apps:
            if item["pid"] == pid:
                return item
    raise AtbError("ATB-E13002",
                   f"未找到应用 {text!r}；先用 `atb cu list` 查看 app_id 与窗口标题")


def _activate(info: dict) -> None:
    hwnd = info["hwnd"]
    _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _u32.SetForegroundWindow(hwnd)
    time.sleep(0.08)


# ── 元素树 ───────────────────────────────────────────────────
def _walk(ctrl, depth: int, max_depth: int, max_nodes: int,
          nodes: list, path: str) -> None:
    if len(nodes) >= max_nodes:
        return
    name = control_type = ""
    bounds = None
    enabled = True
    try:
        name = ctrl.Name or ""
    except Exception:
        pass
    try:
        control_type = ctrl.ControlTypeName or ""
    except Exception:
        pass
    try:
        enabled = bool(ctrl.IsEnabled)
    except Exception:
        pass
    try:
        r = ctrl.BoundingRectangle
        bounds = [r.left, r.top, r.right - r.left, r.bottom - r.top]
    except Exception:
        pass
    nodes.append({
        "element_id": path,
        "name": name,
        "type": control_type.replace("Control", "") or "Unknown",
        "automation_id": getattr(ctrl, "AutomationId", "") or "",
        "bounds": bounds,
        "enabled": enabled,
    })
    if depth >= max_depth:
        return
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for i, child in enumerate(children):
        if len(nodes) >= max_nodes:
            return
        _walk(child, depth + 1, max_depth, max_nodes, nodes, f"{path}.{i}")


def _state_from_children(info: dict, max_nodes: int) -> list:
    nodes = []
    for hwnd in _iter_child_windows(info["hwnd"]):
        if len(nodes) >= max_nodes:
            break
        if not _u32.IsWindowVisible(hwnd):
            continue
        bounds = _window_bounds(hwnd)
        nodes.append({
            "element_id": f"hwnd:{hwnd}",
            "name": _window_title(hwnd),
            "type": _window_class(hwnd) or "ChildWindow",
            "automation_id": "",
            "bounds": bounds,
            "enabled": not (bounds is None),
        })
    return nodes


def _screenshot(info: dict) -> Optional[dict]:
    if not _pillow():
        return None
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    x, y, w, h = info["bounds"]
    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    except Exception:
        return None
    out_dir = os.path.join(config.REPORT_DIR, "computer",
                           f"{os.path.splitext(info['process'])[0] or 'app'}_{info['pid']}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"state_{_now()}.jpg")
    img.convert("RGB").save(path, "JPEG", quality=85)
    return {"path": path, "width": img.width, "height": img.height,
            "tokens_estimate": max(1, img.width * img.height // 750)}


def get_app_state(app, depth: int = DEFAULT_DEPTH,
                  max_nodes: int = DEFAULT_MAX_NODES,
                  screenshot: bool = False) -> dict:
    _require_windows()
    _enable_dpi()
    info = _resolve_app(app)
    auto = _uia()
    nodes = []
    source = "win32-children"
    if auto is not None:
        try:
            root = auto.ControlFromHandle(info["hwnd"])
            if root is not None:
                _walk(root, 0, max(1, depth), max(1, max_nodes), nodes, "0")
                source = "uiautomation"
        except Exception:
            nodes = []
    if not nodes:
        nodes = _state_from_children(info, max(1, max_nodes))
        source = "win32-children"
    payload = {
        "app": info,
        "source": source,
        "nodes": nodes,
        "node_count": len(nodes),
        "screenshot": _screenshot(info) if screenshot else None,
        "hint": "element_id 仅对同一窗口的当前状态有效；界面变化后请重新获取状态。",
    }
    if auto is None:
        payload["warning"] = ("未安装可选依赖 uiautomation，元素树退化为子窗口枚举，"
                              "只能拿到窗口级控件；装 pip install uiautomation 可获得完整控件树。")
    return payload


def _find_by_path(info: dict, element_id: str) -> dict:
    if element_id.startswith("hwnd:"):
        hwnd = int(element_id.split(":", 1)[1])
        if not _u32.IsWindow(hwnd):
            raise AtbError("ATB-E13002", f"子窗口不存在：{hwnd}")
        bounds = _window_bounds(hwnd)
        return {"hwnd": hwnd, "bounds": bounds, "name": _window_title(hwnd),
                "control": None}
    auto = _uia()
    if auto is None:
        raise AtbError("ATB-E13003",
                       "需要可选依赖 uiautomation 才能按 element_id 定位；"
                       "请安装 pip install uiautomation，或改用屏幕坐标 x/y")
    try:
        ctrl = auto.ControlFromHandle(info["hwnd"])
        for idx in element_id.split(".")[1:]:
            ctrl = ctrl.GetChildren()[int(idx)]
        r = ctrl.BoundingRectangle
        return {"hwnd": info["hwnd"], "bounds": [r.left, r.top, r.right - r.left,
                                                 r.bottom - r.top],
                "name": ctrl.Name or "", "control": ctrl}
    except Exception as e:
        raise AtbError("ATB-E13003", f"element_id {element_id!r} 已失效：{e}；"
                                     f"请重新调用 get_app_state")


# ── 输入 ─────────────────────────────────────────────────────
def _click_point(x: int, y: int) -> None:
    _u32.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    _u32.mouse_event(0x0002, 0, 0, 0, None)  # LEFTDOWN
    time.sleep(0.02)
    _u32.mouse_event(0x0004, 0, 0, 0, None)  # LEFTUP


def click(app=None, x: Optional[int] = None, y: Optional[int] = None,
          element_id: Optional[str] = None) -> dict:
    _require_windows()
    _enable_dpi()
    info = _resolve_app(app) if (app or element_id) else None
    if element_id:
        if info is None:
            raise AtbError("ATB-E00003", "使用 element_id 时必须同时提供 app")
        target = _find_by_path(info, element_id)
        bounds = target["bounds"]
        if not bounds or bounds[2] <= 0 or bounds[3] <= 0:
            raise AtbError("ATB-E13004", f"元素 {element_id} 没有可见区域")
        x = bounds[0] + bounds[2] // 2
        y = bounds[1] + bounds[3] // 2
        if info:
            _activate(info)
    elif x is None or y is None:
        raise AtbError("ATB-E00003", "click 需要 x/y 坐标或 element_id")
    if info:
        _activate(info)
    _click_point(x, y)
    return {"ok": True, "x": int(x), "y": int(y),
            "app": info["app_id"] if info else None}


def scroll(direction: str = "down", amount: int = 3,
           x: Optional[int] = None, y: Optional[int] = None, app=None) -> dict:
    _require_windows()
    _enable_dpi()
    direction = (direction or "down").lower()
    if direction not in ("up", "down", "left", "right"):
        raise AtbError("ATB-E00003", f"direction 需为 up/down/left/right：{direction}")
    info = _resolve_app(app) if app else None
    if info:
        _activate(info)
    if x is not None and y is not None:
        _u32.SetCursorPos(int(x), int(y))
        time.sleep(0.02)
    steps = max(1, int(amount))
    if direction in ("up", "down"):
        delta = 120 * steps * (1 if direction == "up" else -1)
        for _ in range(steps):
            _u32.mouse_event(0x0800, 0, 0, delta // steps, None)  # MOUSEEVENTF_WHEEL
            time.sleep(0.02)
    else:
        delta = 120 * steps * (1 if direction == "right" else -1)
        for _ in range(steps):
            _u32.mouse_event(0x01000, 0, 0, delta // steps, None)  # MOUSEEVENTF_HWHEEL
            time.sleep(0.02)
    return {"ok": True, "direction": direction, "amount": steps}


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> dict:
    _require_windows()
    _enable_dpi()
    _u32.SetCursorPos(int(x1), int(y1))
    time.sleep(0.05)
    _u32.mouse_event(0x0002, 0, 0, 0, None)
    steps = max(4, int(duration * 40))
    for i in range(1, steps + 1):
        _u32.SetCursorPos(int(x1 + (x2 - x1) * i / steps),
                          int(y1 + (y2 - y1) * i / steps))
        time.sleep(duration / steps)
    _u32.mouse_event(0x0004, 0, 0, 0, None)
    return {"ok": True, "from": [x1, y1], "to": [x2, y2]}


VK = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "meta": 0x5B, "cmd": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "printscreen": 0x2C,
    "+": 0xBB, "=": 0xBB, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0,
}


def parse_keys(keys: str) -> list:
    """把 'ctrl+shift+s' 解析为虚拟键列表（含顺序）。"""
    raw = (keys or "").strip().lower()
    if not raw:
        raise AtbError("ATB-E00003", "keys 不能为空")
    if raw.endswith("++"):
        parts = [p for p in raw[:-2].split("+") if p] + ["+"]
    else:
        parts = [p for p in raw.split("+") if p]
    out = []
    for token in parts:
        if token in VK:
            out.append(VK[token])
        elif len(token) == 1 and token.isalnum():
            out.append(ord(token.upper()))
        elif token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
            out.append(0x70 + int(token[1:]) - 1)
        else:
            raise AtbError("ATB-E00003", f"无法识别的按键 {token!r}；"
                                         f"可用 ctrl/alt/shift/win/enter/tab/f1-24 等")
    return out


def press_key(keys: str, app=None, repeat: int = 1) -> dict:
    _require_windows()
    info = _resolve_app(app) if app else None
    if info:
        _activate(info)
    vks = parse_keys(keys)
    for _ in range(max(1, int(repeat))):
        for vk in vks:
            _u32.keybd_event(vk, 0, 0, None)
            time.sleep(0.01)
        for vk in reversed(vks):
            _u32.keybd_event(vk, 0, 0x0002, None)
            time.sleep(0.01)
    return {"ok": True, "keys": keys, "vk": vks, "repeat": repeat}


def type_text(text: str, app=None, interval: float = 0.005) -> dict:
    _require_windows()
    info = _resolve_app(app) if app else None
    if info:
        _activate(info)
    written = 0
    for ch in text:
        for unit in _utf16_units(ch):
            _u32.keybd_event(0xE7, unit & 0xFFFF, 0x0004, None)        # KEYEVENTF_UNICODE
            _u32.keybd_event(0xE7, unit & 0xFFFF, 0x0004 | 0x0002, None)
            written += 1
            if interval:
                time.sleep(interval)
    return {"ok": True, "chars": len(text), "units": written}


def _utf16_units(ch: str) -> list:
    data = ch.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def select_text(app=None, element_id: Optional[str] = None) -> dict:
    """基础实现：聚焦元素后全选（Ctrl+A）。细粒度选择需 uiautomation TextPattern。"""
    _require_windows()
    info = _resolve_app(app) if app else None
    if element_id:
        if info is None:
            raise AtbError("ATB-E00003", "使用 element_id 时必须同时提供 app")
        click(app=info["app_id"], element_id=element_id)
    if info:
        _activate(info)
    press_key("ctrl+a")
    return {"ok": True, "note": "已执行全选；如需按范围选择，请安装 uiautomation 后用 TextPattern"}


def set_value(element_id: str, value: str, app=None) -> dict:
    _require_windows()
    info = _resolve_app(app)
    target = _find_by_path(info, element_id)
    ctrl = target.get("control")
    if ctrl is not None:
        try:
            ctrl.GetValuePattern().SetValue(value)
            return {"ok": True, "method": "uiautomation.ValuePattern",
                    "element_id": element_id}
        except Exception:
            pass
    click(app=info["app_id"], element_id=element_id)
    press_key("ctrl+a")
    type_text(value)
    return {"ok": True, "method": "click+select+type（降级）", "element_id": element_id}


def perform_action(element_id: str, app=None) -> dict:
    _require_windows()
    info = _resolve_app(app)
    target = _find_by_path(info, element_id)
    ctrl = target.get("control")
    if ctrl is not None:
        for attr in ("GetInvokePattern", "GetLegacyIAccessiblePattern"):
            try:
                pattern = getattr(ctrl, attr)()
                if attr == "GetInvokePattern":
                    pattern.Invoke()
                else:
                    pattern.DoDefaultAction()
                return {"ok": True, "method": attr, "element_id": element_id}
            except Exception:
                continue
    click(app=info["app_id"], element_id=element_id)
    return {"ok": True, "method": "click（降级）", "element_id": element_id}


def grounding(text: str, app=None, exact: bool = False,
              depth: int = 4, max_nodes: int = 400) -> dict:
    """按可见文本定位元素，返回 element_id 与屏幕区域。"""
    _require_windows()
    info = _resolve_app(app) if app else None
    if info is None:
        raise AtbError("ATB-E00003", "grounding 需要指定 app")
    needle = (text or "").strip().lower()
    if not needle:
        raise AtbError("ATB-E00003", "grounding 需要 text")
    nodes = get_app_state(info["app_id"], depth=depth, max_nodes=max_nodes)["nodes"]
    best = None
    for node in nodes:
        name = (node.get("name") or "").lower()
        if not name or not node.get("bounds"):
            continue
        hit = (name == needle) if exact else (needle in name)
        if not hit:
            continue
        area = node["bounds"][2] * node["bounds"][3]
        if best is None or 0 < area < best[0]:
            best = (area, node)
    if not best:
        return {"found": False, "text": text, "app": info["app_id"],
                "hint": "可放宽 depth/max_nodes，或改用截图 + 坐标点击"}
    return {"found": True, "text": text, "app": info["app_id"], **best[1]}


def launch_app(app: str, args: Optional[list] = None,
               wait: float = 8.0) -> dict:
    _require_windows()
    args = list(args or [])
    exe = app if os.path.isfile(app) else shutil.which(app)
    if not exe:
        raise AtbError("ATB-E13002",
                       f"找不到可执行文件 {app!r}；请传绝对路径，或确认它在 PATH 中")
    proc = subprocess.Popen([exe] + args, close_fds=True)
    deadline = time.time() + max(0.5, wait)
    window = None
    while time.time() < deadline:
        time.sleep(0.2)
        for item in list_apps(limit=400, include_empty=True):
            if item["pid"] == proc.pid:
                window = item
                break
        if window:
            break
    return {"ok": True, "pid": proc.pid, "exe": exe,
            "window": window,
            "app_id": window["app_id"] if window else None}


# ── 统一分发（CLI / MCP 共用） ────────────────────────────────
ACTIONS = ("available", "list", "state", "launch", "click", "scroll", "drag",
           "type", "key", "select", "set_value", "action", "ground")


def dispatch(action: str, params: dict = None) -> dict:
    params = dict(params or {})
    action = (action or "").lower()
    if action == "available":
        return available()
    if action == "list":
        return {"apps": list_apps(int(params.get("limit") or 50),
                                  bool(params.get("include_empty")))}
    if action == "state":
        return get_app_state(params.get("app"),
                             depth=int(params.get("depth") or DEFAULT_DEPTH),
                             max_nodes=int(params.get("max_nodes") or DEFAULT_MAX_NODES),
                             screenshot=bool(params.get("screenshot")))
    if action == "launch":
        return launch_app(params["app"], params.get("args") or [],
                          float(params.get("wait") or 8))
    if action == "click":
        return click(app=params.get("app"), x=params.get("x"), y=params.get("y"),
                     element_id=params.get("element_id"))
    if action == "scroll":
        return scroll(params.get("direction") or "down", int(params.get("amount") or 3),
                      params.get("x"), params.get("y"), params.get("app"))
    if action == "drag":
        return drag(int(params["x1"]), int(params["y1"]),
                    int(params["x2"]), int(params["y2"]),
                    float(params.get("duration") or 0.4))
    if action == "type":
        return type_text(params["text"], params.get("app"))
    if action == "key":
        return press_key(params["keys"], params.get("app"),
                         int(params.get("repeat") or 1))
    if action == "select":
        return select_text(params.get("app"), params.get("element_id"))
    if action == "set_value":
        return set_value(params["element_id"], params["value"], params.get("app"))
    if action == "action":
        return perform_action(params["element_id"], params.get("app"))
    if action == "ground":
        return grounding(params["text"], params.get("app"),
                         bool(params.get("exact")),
                         int(params.get("depth") or 4),
                         int(params.get("max_nodes") or 400))
    raise AtbError("ATB-E00003",
                   f"未知动作 {action!r}；可用：{', '.join(ACTIONS)}")
