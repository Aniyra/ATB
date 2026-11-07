# -*- coding: utf-8 -*-
"""实验性：Windows UI 精准编辑。

本模块从早期的「电脑操作 / Computer Use」收窄而来：
- 已移除：屏幕坐标点击、拖拽、滚动、键鼠注入、启动应用、窗口列表等桌面自动化；
- 保留：基于 UI Automation 的语义化操作——读取元素树、按文本定位、
  设置值、选择文本、执行元素动作。

这样既避免误点/误输入带来的不可审计风险，又保留可精确到元素的编辑能力。
仅 Windows；uiautomation 缺失时只读状态退化为子窗口枚举，写操作会明确报错。
"""

import ctypes
import os
import sys
from typing import Optional

from ..errors import AtbError

IS_WINDOWS = sys.platform == "win32"

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


# ── 基础 ─────────────────────────────────────────────────────
def _require_windows() -> None:
    if not IS_WINDOWS:
        raise AtbError("ATB-E13001",
                       f"当前平台为 {sys.platform}；UI 精准编辑仅支持 Windows")


def available() -> dict:
    """返回本机 UI 精准编辑能力（供 doctor / MCP 说明用）。"""
    uia = _uia() is not None
    if not IS_WINDOWS:
        return {"ok": False, "platform": sys.platform, "uiautomation": uia,
                "reason": "仅支持 Windows"}
    return {"ok": uia, "platform": sys.platform, "uiautomation": uia,
            "reason": "" if uia else
            "未安装 uiautomation：只能读取子窗口，不能设置值/选择文本/执行动作"}


def _uia():
    try:
        import uiautomation as auto  # type: ignore
        auto.SetGlobalSearchTimeout(1.5)
        return auto
    except Exception:
        return None


# ── 窗口定位（只读） ─────────────────────────────────────────
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


def _window_info(hwnd: int) -> Optional[dict]:
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
    }


def _visible_windows(limit: int = 400) -> list:
    out = []
    for hwnd in _iter_top_windows():
        if not _u32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            continue
        ex_style = _u32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
            continue
        info = _window_info(hwnd)
        if info:
            out.append(info)
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

    windows = _visible_windows()
    lowered = text.lower()
    for item in windows:
        if item["app_id"].lower() == lowered:
            return item
    for item in windows:
        if lowered in item["process"].lower() or lowered in item["title"].lower():
            return item
    raise AtbError("ATB-E13002",
                   f"未找到应用 {text!r}；请传窗口标题片段、进程名或 hwnd:123")


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
        nodes.append({
            "element_id": f"hwnd:{hwnd}",
            "name": _window_title(hwnd),
            "type": _window_class(hwnd) or "ChildWindow",
            "automation_id": "",
            "bounds": _window_bounds(hwnd),
            "enabled": True,
        })
    return nodes


def get_app_state(app, depth: int = DEFAULT_DEPTH,
                  max_nodes: int = DEFAULT_MAX_NODES) -> dict:
    _require_windows()
    info = _resolve_app(app)
    auto = _uia()
    nodes, source = [], "win32-children"
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
        "hint": "element_id 只在同一窗口的当前状态有效；界面变化后请重新获取状态。",
    }
    if auto is None:
        payload["warning"] = ("未安装 uiautomation：只能读取子窗口，"
                              "不能设置值/选择文本/执行动作。")
    return payload


def _find_by_path(info: dict, element_id: str) -> dict:
    if element_id.startswith("hwnd:"):
        hwnd = int(element_id.split(":", 1)[1])
        if not _u32.IsWindow(hwnd):
            raise AtbError("ATB-E13002", f"子窗口不存在：{hwnd}")
        return {"hwnd": hwnd, "bounds": _window_bounds(hwnd),
                "name": _window_title(hwnd), "control": None}
    auto = _uia()
    if auto is None:
        raise AtbError("ATB-E13003",
                       "按 element_id 精细操作需要可选依赖 uiautomation："
                       "pip install uiautomation")
    try:
        ctrl = auto.ControlFromHandle(info["hwnd"])
        for idx in element_id.split(".")[1:]:
            ctrl = ctrl.GetChildren()[int(idx)]
        r = ctrl.BoundingRectangle
        return {"hwnd": info["hwnd"],
                "bounds": [r.left, r.top, r.right - r.left, r.bottom - r.top],
                "name": ctrl.Name or "", "control": ctrl}
    except Exception as e:
        raise AtbError("ATB-E13003", f"element_id {element_id!r} 已失效：{e}；"
                                     f"请重新调用 state")


# ── 精准编辑 ─────────────────────────────────────────────────
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
    state = get_app_state(info["app_id"], depth=depth, max_nodes=max_nodes)
    nodes = state["nodes"]
    if state["source"] != "uiautomation":
        return {"found": False, "text": text, "app": info["app_id"],
                "hint": "需要 uiautomation 才能按控件文本精确定位"}
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
                "hint": "可放宽 depth/max_nodes，或换一个更短的可见文本"}
    return {"found": True, "text": text, "app": info["app_id"], **best[1]}


def set_value(element_id: str, value: str, app=None) -> dict:
    """通过 UI Automation ValuePattern 精确设置可编辑元素的值。"""
    _require_windows()
    info = _resolve_app(app)
    target = _find_by_path(info, element_id)
    ctrl = target.get("control")
    if ctrl is None:
        raise AtbError("ATB-E13003",
                       "该 element_id 不是 UI Automation 控件，无法设置值；"
                       "请重新 state 并选择可编辑控件")
    try:
        ctrl.GetValuePattern().SetValue(value)
    except Exception as e:
        raise AtbError("ATB-E13004", f"设置值失败：{e}")
    return {"ok": True, "method": "uiautomation.ValuePattern",
            "element_id": element_id}


def select_text(element_id: str, app=None) -> dict:
    """通过 UI Automation TextPattern 精确选择控件内文本。"""
    _require_windows()
    info = _resolve_app(app)
    target = _find_by_path(info, element_id)
    ctrl = target.get("control")
    if ctrl is None:
        raise AtbError("ATB-E13003",
                       "该 element_id 不是 UI Automation 控件，无法选择文本")
    try:
        ctrl.SetFocus()
        ctrl.GetTextPattern().DocumentRange.Select()
    except Exception as e:
        raise AtbError("ATB-E13004", f"选择文本失败：{e}")
    return {"ok": True, "method": "uiautomation.TextPattern",
            "element_id": element_id}


def perform_action(element_id: str, app=None) -> dict:
    """执行元素的语义动作（Invoke / 默认动作），不做坐标点击。"""
    _require_windows()
    info = _resolve_app(app)
    target = _find_by_path(info, element_id)
    ctrl = target.get("control")
    if ctrl is None:
        raise AtbError("ATB-E13003",
                       "该 element_id 不是 UI Automation 控件，无法执行动作")
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
    raise AtbError("ATB-E13004",
                   "元素不支持 Invoke/默认动作；请改用 set_value 或换一个控件")


# ── 统一分发（CLI / MCP 共用） ────────────────────────────────
ACTIONS = ("available", "state", "ground", "set_value", "select", "action")


def dispatch(action: str, params: dict = None) -> dict:
    params = dict(params or {})
    action = (action or "").lower()
    if action == "available":
        return available()
    if action == "state":
        return get_app_state(params.get("app"),
                             depth=int(params.get("depth") or DEFAULT_DEPTH),
                             max_nodes=int(params.get("max_nodes") or DEFAULT_MAX_NODES))
    if action == "ground":
        return grounding(params["text"], params.get("app"),
                         bool(params.get("exact")),
                         int(params.get("depth") or 4),
                         int(params.get("max_nodes") or 400))
    if action == "set_value":
        return set_value(params["element_id"], params["value"], params.get("app"))
    if action == "select":
        return select_text(params["element_id"], params.get("app"))
    if action == "action":
        return perform_action(params["element_id"], params.get("app"))
    raise AtbError("ATB-E00003",
                   f"未知动作 {action!r}；可用：{', '.join(ACTIONS)}")
