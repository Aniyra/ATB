# -*- coding: utf-8 -*-
"""CLI 进度显示：单行进度条 / 不确定总量时的 spinner + ETA。

设计参考 D:\\pro\\rs 的 `_emit_progress` / `_draw_byte_progress`：
  - 只在 TTY 下启用，输出到 stderr，绝不影响 --json 的 stdout
  - 用 `\\r` 回到行首 + ANSI EL (`\\033[K`) 清除行尾残留
  - 节流刷新（约 12 次/秒），长任务不拖慢主流程
"""

import shutil
import sys
import time
from typing import Optional

BAR_CHARS = "█░"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Progress:
    """分阶段进度：stage() 开始一段，update() 推进，done() 收尾。"""

    def __init__(self, enabled: bool = True, stream=None, width: Optional[int] = None):
        self.stream = stream or sys.stderr
        self.enabled = bool(enabled)
        self.width = width or self._term_width()
        self._label = ""
        self._total: Optional[int] = None
        self._current = 0
        self._start = time.perf_counter()
        self._last_draw = 0.0
        self._spin = 0
        self._active = False

    # ── 对外接口 ──
    def stage(self, label: str, total: Optional[int] = None) -> "Progress":
        if not self.enabled:
            return self
        self._clear()
        self._label = label
        self._total = total if (total and total > 0) else None
        self._current = 0
        self._start = time.perf_counter()
        self._last_draw = 0.0
        self._active = True
        self._render(force=True)
        return self

    def update(self, n: int = 1, note: str = "") -> None:
        if not self.enabled or not self._active:
            return
        self._current += n
        now = time.perf_counter()
        if now - self._last_draw >= 0.08:
            self._spin += 1
            self._render(note=note)

    def set(self, current: int, note: str = "") -> None:
        if not self.enabled or not self._active:
            return
        self._current = current
        self._render(note=note)

    def done(self, note: str = "") -> None:
        if not self.enabled or not self._active:
            self._active = False
            return
        elapsed = time.perf_counter() - self._start
        if self._total:
            self._current = max(self._current, self._total)
        text = f"  {self._label} 完成 · {_fmt_secs(elapsed)}"
        if note:
            text += f" · {note}"
        self._write(text, newline=True)
        self._active = False

    def close(self) -> None:
        """中断时清掉未完成的行。"""
        if self.enabled:
            self._clear()
        self._active = False

    # ── 渲染 ──
    def _render(self, note: str = "", force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_draw < 0.08:
            return
        self._last_draw = now
        elapsed = max(now - self._start, 1e-6)

        if self._total:
            total = self._total
            ratio = min(self._current / total, 1.0)
            bar_len = 24
            filled = int(bar_len * ratio)
            bar = BAR_CHARS[0] * filled + BAR_CHARS[1] * (bar_len - filled)
            eta = elapsed / ratio - elapsed if ratio > 0 else 0
            pct = ratio * 100
            head = f"  {self._label} [{bar}] {pct:3.0f}%  {self._current}/{total}"
            tail = f"  ETA {_fmt_secs(eta)}"
            line = head + tail
            if note:
                line += f"  {note}"
        else:
            spin = SPINNER[self._spin % len(SPINNER)]
            line = f"  {spin} {self._label}"
            if self._current:
                line += f"  {self._current}"
            line += f"  {_fmt_secs(elapsed)}"
            if note:
                line += f"  {note}"

        if _disp_len(line) > self.width - 1:
            line = _truncate(line, self.width - 1)
        self._write(line)

    def _write(self, text: str, newline: bool = False) -> None:
        try:
            self.stream.write("\r" + text + "\033[K" + ("\n" if newline else ""))
            self.stream.flush()
        except Exception:
            pass

    def _clear(self) -> None:
        if not self.enabled:
            return
        try:
            self.stream.write("\r\033[K")
            self.stream.flush()
        except Exception:
            pass

    @staticmethod
    def _term_width() -> int:
        try:
            return max(60, min(shutil.get_terminal_size((100, 24)).columns, 160))
        except Exception:
            return 100


class NullProgress(Progress):
    """禁用状态的空实现，调用方无需判空。"""

    def __init__(self):
        super().__init__(enabled=False)


NULL = NullProgress()


def make_progress(enabled: Optional[bool] = None, stream=None) -> Progress:
    """enabled=None 时按 stderr 是否为 TTY 自动判断。"""
    stream = stream or sys.stderr
    if enabled is False:
        return NULL
    auto = False
    try:
        auto = bool(stream.isatty())
    except Exception:
        auto = False
    if enabled is None:
        enabled = auto
    return Progress(enabled=bool(enabled), stream=stream)


# ── 文本工具 ─────────────────────────────────────────────────
def _fmt_secs(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _disp_len(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _truncate(text: str, limit: int) -> str:
    acc, buf = 0, []
    for ch in text:
        step = 2 if ord(ch) > 0x2E80 else 1
        if acc + step > limit - 1:
            break
        acc += step
        buf.append(ch)
    return "".join(buf) + "…"
