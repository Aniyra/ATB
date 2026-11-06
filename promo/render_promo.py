# -*- coding: utf-8 -*-
"""ATB 宣传片渲染脚本（1080p / 30fps / 52s）。

素材：D:\\pro\\sp 三个 Remotion 视频的静帧（B-roll）+ ATB 自身产出的分镜宫格图。
配乐：Python 现场合成（120 BPM，无版权问题）。
输出：promo/ATB-Promo-1080p.mp4、promo/cover.png

用法：
    python promo/render_promo.py --preview 25     # 只渲染某一秒的静帧，检查版式
    python promo/render_promo.py --short          # 渲染 6 秒测试片
    python promo/render_promo.py                  # 完整渲染
"""

import argparse
import array
import math
import os
import random
import struct
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
DUR = 52.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMO = os.path.join(ROOT, "promo")
REPORTS = os.path.join(ROOT, "reports")

BG = (8, 12, 24)
CYAN = (56, 189, 248)
TEAL = (45, 212, 191)
TEXT = (229, 231, 235)
MUTED = (148, 163, 184)
PANEL = (14, 21, 38)

FONT_CN = r"C:\Windows\Fonts\msyh.ttc"
FONT_CN_BD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FONT_MONO_BD = r"C:\Windows\Fonts\consolab.ttf"

BROLL = {
    r"D:\pro\sp\视频-六位数\out\SixDigits.mp4": [8.0, 26.0, 40.0],
    r"D:\pro\sp\视频-接口之战\out\InterfaceWar.mp4": [30.0, 120.0, 240.0],
}
STORYBOARD_SHEET = os.path.join(
    REPORTS, "storyboards", "InterfaceWar_874ffb82", "sheet_01.jpg")


# ── 基础工具 ─────────────────────────────────────────────────
def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def lerp(a, b, t):
    return a + (b - a) * t


_font_cache = {}


def font(path, size):
    key = (path, size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def text_w(fnt, s):
    b = fnt.getbbox(s)
    return b[2] - b[0]


def has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def draw_text(d, xy, s, fnt, fill, alpha=255, anchor="la"):
    d.text(xy, s, font=fnt, fill=fill + (int(alpha),), anchor=anchor)


def spaced_text(d, center_x, y, s, fnt, fill, gap, alpha=255):
    widths = [text_w(fnt, ch) for ch in s]
    total = sum(widths) + gap * (len(s) - 1)
    x = center_x - total / 2
    for ch, w in zip(s, widths):
        draw_text(d, (x, y), ch, fnt, fill, alpha)
        x += w + gap


# ── 背景 ─────────────────────────────────────────────────────
def make_base() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-W * 0.35, -H * 0.55, W * 0.75, H * 0.55], fill=CYAN + (36,))
    gd.ellipse([W * 0.45, H * 0.35, W * 1.35, H * 1.4], fill=TEAL + (22,))
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 60):
        gd.line([x, 0, x, H], fill=(80, 120, 180, 10))
    for y in range(0, H, 60):
        gd.line([0, y, W, y], fill=(80, 120, 180, 10))
    img = Image.alpha_composite(img.convert("RGBA"), grid).convert("RGB")

    # 暗角
    mask = Image.radial_gradient("L").resize((W, H))
    mask = mask.point(lambda v: int((255 - v) * 0.55))
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img = Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), mask)
    return img


BASE = None


def base_copy():
    return BASE.copy()


def draw_broll(img: Image.Image, still: Image.Image, zoom: float, dim: float = 0.82):
    w, h = int(W * zoom), int(H * zoom)
    big = still.resize((w, h), Image.BILINEAR)
    left, top = (w - W) // 2, (h - H) // 2
    big = big.crop((left, top, left + W, top + H))
    dark = Image.new("RGB", (W, H), (5, 8, 16))
    big = Image.blend(big, dark, dim)
    img.paste(big, (0, 0))


def panel(ov, box, alpha=235, radius=18, outline=(45, 60, 90, 255)):
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle(box, radius=radius, fill=PANEL + (alpha,), outline=outline, width=2)


def glow_line(ov, xy, color=CYAN, alpha=180):
    d = ImageDraw.Draw(ov)
    d.line(xy, fill=color + (alpha,), width=3)


def pill(d, x, y, s, fnt, fill=CYAN, fg=BG, pad=18, alpha=255):
    w = text_w(fnt, s) + pad * 2
    h = fnt.size + 14
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                        fill=fill + (alpha,))
    d.text((x + pad, y + h // 2), s, font=fnt, fill=fg + (alpha,), anchor="lm")
    return w, h


# ── B-roll 抽帧 ──────────────────────────────────────────────
def extract_stills():
    out = {}
    for path, times in BROLL.items():
        pending = sorted(times)
        idx = 0
        with av.open(path) as c:
            s = c.streams.video[0]
            s.thread_type = "AUTO"
            for f in c.decode(s):
                if f.time is None:
                    continue
                while idx < len(pending) and f.time >= pending[idx] - 0.06:
                    img = f.to_image().convert("RGB")
                    img.thumbnail((960, 540), Image.LANCZOS)
                    out[(path, pending[idx])] = img
                    idx += 1
                if idx >= len(pending):
                    break
    print(f"[broll] {len(out)} 张静帧")
    return out


# ── 配乐合成 ─────────────────────────────────────────────────
SR = 44100


def synth_music(path: str):
    n = int(SR * DUR)
    left = array.array("f", [0.0]) * n
    right = array.array("f", [0.0]) * n

    def add(start, dur, freq, amp, kind="sine", attack=0.005, decay=None,
            pan=0.0, detune=0.0):
        a = int(start * SR)
        length = min(int(dur * SR), n - a)
        if length <= 0:
            return
        ap = int(attack * SR)
        dec = decay if decay is not None else dur
        dp = int(dec * SR)
        gl = amp * (1 - max(0.0, pan)) ** 0.5
        gr = amp * (1 + min(0.0, pan)) ** 0.5
        for i in range(length):
            t = i / SR
            env = 1.0
            if i < ap and ap > 0:
                env = i / ap
            env *= math.exp(-t / max(dec, 0.01) * 2.2)
            ph = 2 * math.pi * freq * t
            if kind == "saw":
                v = math.sin(ph) + 0.5 * math.sin(2 * ph) + 0.25 * math.sin(3 * ph)
            elif kind == "tri":
                v = 2 / math.pi * math.asin(math.sin(ph))
            else:
                v = math.sin(ph)
            if detune:
                v += 0.5 * math.sin(ph * (1 + detune))
            j = a + i
            left[j] += v * gl * env
            right[j] += v * gr * env

    def add_noise(start, dur, amp, decay=0.05, pan=0.0, low=False):
        a = int(start * SR)
        length = min(int(dur * SR), n - a)
        rnd = random.Random(int(start * 1000))
        prev = 0.0
        gl = amp * (1 - max(0.0, pan)) ** 0.5
        gr = amp * (1 + min(0.0, pan)) ** 0.5
        for i in range(length):
            t = i / SR
            x = rnd.uniform(-1, 1)
            if low:
                prev = prev * 0.92 + x * 0.08
                x = prev * 2.2
            env = math.exp(-t / max(decay, 0.005))
            j = a + i
            left[j] += x * gl * env
            right[j] += x * gr * env

    # 和弦进行 Am - F - C - G
    chords = [
        (55.00, [220.00, 261.63, 329.63]),
        (43.65, [174.61, 220.00, 261.63]),
        (65.41, [196.00, 261.63, 329.63]),
        (49.00, [196.00, 246.94, 293.66]),
    ]
    bar = 2.0                       # 120 BPM 下 4 拍
    start_beat = 4.0
    total_bars = int((DUR - start_beat) / bar)

    # 前奏 riser
    add_noise(0.0, 4.0, 0.06, decay=4.0, low=False)
    add(0.0, 4.0, 110.0, 0.05, "saw", attack=3.0, decay=4.0)
    add(2.0, 2.0, 164.81, 0.03, "sine", attack=1.5, decay=2.0)

    for b in range(total_bars):
        t0 = start_beat + b * bar
        root, notes = chords[b % 4]

        # Pad
        for note in notes:
            add(t0, bar, note, 0.030, "saw", attack=0.25, decay=bar * 0.9,
                pan=-0.25, detune=0.003)
            add(t0, bar, note, 0.030, "saw", attack=0.25, decay=bar * 0.9,
                pan=0.25, detune=-0.003)

        # Bass：8 分音符
        for k in range(8):
            add(t0 + k * 0.25, 0.22, root, 0.10, "saw", attack=0.004, decay=0.18)

        # Kick：4 拍 + 反拍
        for k in range(4):
            add(t0 + k * 0.5, 0.26, 120.0, 0.20, "sine", attack=0.002, decay=0.16)
        # Hat：反拍
        for k in range(4):
            add_noise(t0 + k * 0.5 + 0.25, 0.05, 0.030, decay=0.03, pan=0.2)

        # Arp：16 分音符
        pattern = [notes[0], notes[1], notes[2], notes[1]]
        for k in range(8):
            note = pattern[k % 4] * 2
            add(t0 + k * 0.25, 0.18, note, 0.026, "tri",
                attack=0.003, decay=0.12, pan=-0.3 if k % 2 else 0.3)

    # 结尾重音 + 长和弦
    add(48.0, 0.6, 60.0, 0.22, "sine", attack=0.002, decay=0.5)
    add_noise(48.0, 2.5, 0.05, decay=1.6)
    for note in [220.0, 261.63, 329.63, 440.0]:
        add(48.0, 4.0, note, 0.035, "saw", attack=0.05, decay=3.6, pan=-0.15)
        add(48.0, 4.0, note, 0.035, "saw", attack=0.05, decay=3.6, pan=0.15)

    peak = max(max(abs(v) for v in left), max(abs(v) for v in right), 0.001)
    gain = 0.92 / peak
    pcm = array.array("h")
    for i in range(n):
        # 软限幅：抬高整体响度（目标约 -18 dBFS RMS），同时避免削波
        l = math.tanh(left[i] * gain * 1.75) * 0.96
        r = math.tanh(right[i] * gain * 1.75) * 0.96
        pcm.append(int(l * 32767))
        pcm.append(int(r * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"[music] {path}  {os.path.getsize(path)/1024/1024:.1f} MB")


# ── 场景 ─────────────────────────────────────────────────────
def scene_hook(t, stills):
    assert 0 <= t <= 4.6
    img = base_copy()
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    if t < 2.2:
        a = ease_out(t / 0.6) * (1 - ease_in_out((t - 1.8) / 0.4))
        draw_text(d, (W // 2, H // 2 - 60), "AI 不该被文件格式卡住",
                  font(FONT_CN_BD, 84), TEXT, 255 * a, "mm")
        draw_text(d, (W // 2, H // 2 + 40), "PDF · Excel · 图片 · 压缩包 · 视频",
                  font(FONT_CN, 36), MUTED, 255 * a, "mm")
    else:
        a = ease_out((t - 2.2) / 0.7)
        icon = load_icon().resize((int(180 * (0.85 + 0.15 * a)),) * 2, Image.LANCZOS)
        img.paste(icon, (W // 2 - icon.width // 2, int(H // 2 - 220)),
                  icon if icon.mode == "RGBA" else None)
        draw_text(d, (W // 2, H // 2 + 40), "ATB", font(FONT_CN_BD, 120), TEXT, 255 * a, "mm")
        spaced_text(d, W // 2, H // 2 + 130, "ALL TOOL BOX",
                    font(FONT_MONO_BD, 34), CYAN, 14, 255 * a)
        draw_text(d, (W // 2, H // 2 + 215), "给 AI Agent 的全格式文件读取工具箱",
                  font(FONT_CN, 38), MUTED, 255 * a, "mm")

    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 4.6)


_ICON = None


def load_icon():
    global _ICON
    if _ICON is None:
        p = os.path.join(ROOT, "atb_icon_preview.png")
        _ICON = Image.open(p).convert("RGBA")
    return _ICON


def scene_cli(t, stills):
    assert 0 <= t <= 8.0
    img = base_copy()
    still = stills.get((r"D:\pro\sp\视频-六位数\out\SixDigits.mp4", 8.0))
    if still:
        draw_broll(img, still, 1.02 + 0.04 * t / 8.0, dim=0.86)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # 左侧终端
    box = [110, 250, 1210, 830]
    panel(ov, box, 242)
    d.rounded_rectangle([110, 250, 1210, 306], radius=18, fill=(20, 30, 52, 250))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([140 + i * 28, 268, 158 + i * 28, 286], fill=c + (255,))
    draw_text(d, (300, 278), "atb — PowerShell", font(FONT_MONO, 22), MUTED, 220, "lm")

    cmd = 'atb read "季度报告.pdf" --md'
    n = int(clamp01((t - 0.4) / 1.4) * len(cmd))
    cmd_font = font(FONT_CN, 30)          # 命令含中文，Consolas 没有 CJK 字形
    draw_text(d, (150, 350), "PS>", font(FONT_MONO_BD, 30), TEAL, 255)
    draw_text(d, (225, 350), cmd[:n], cmd_font, TEXT, 255)
    if n < len(cmd) and (t * 3) % 1 < 0.5:
        x = 225 + text_w(cmd_font, cmd[:n])
        d.rectangle([x + 2, 350, x + 14, 388], fill=CYAN + (220,))

    lines = [
        ("格式", "pdf  ·  处理器 pdf.pymupdf"),
        ("页数", "12  ·  表格 8  ·  图片 24"),
        ("输出", "report.md  ·  21.4 KB"),
    ]
    for i, (k, v) in enumerate(lines):
        lt = t - (1.9 + i * 0.55)
        if lt <= 0:
            continue
        a = ease_out(lt / 0.35)
        y = 440 + i * 82
        draw_text(d, (150, y), "√", font(FONT_CN_BD, 28), TEAL, 255 * a)
        draw_text(d, (190, y), k, font(FONT_CN, 26), MUTED, 255 * a)
        draw_text(d, (280, y), v, font(FONT_CN, 26), TEXT, 255 * a)

    # 进度条
    p = clamp01((t - 3.2) / 2.6)
    if p > 0:
        bx, by, bw = 150, 720, 980
        d.rounded_rectangle([bx, by, bx + bw, by + 16], radius=8, fill=(30, 42, 66, 255))
        d.rounded_rectangle([bx, by, bx + int(bw * p), by + 16], radius=8,
                            fill=CYAN + (255,))
        draw_text(d, (bx, by + 34), f"读取中  {int(p*100):3d}%   ETA {max(0.0, 2.6*(1-p)):.1f}s",
                  font(FONT_MONO, 20), MUTED, 220)

    # 右侧数字
    a = ease_out((t - 1.2) / 0.8)
    if a > 0:
        count = int(17 * clamp01((t - 1.2) / 1.8))
        draw_text(d, (1330, 330), f"{count}", font(FONT_CN_BD, 150), CYAN, 255 * a)
        draw_text(d, (1340, 500), "类格式", font(FONT_CN, 34), TEXT, 255 * a)
        count2 = int(250 * clamp01((t - 1.6) / 2.0))
        draw_text(d, (1330, 610), f"{count2}", font(FONT_CN_BD, 150), TEAL, 255 * a)
        draw_text(d, (1340, 780), "个扩展名", font(FONT_CN, 34), TEXT, 255 * a)

    draw_text(d, (W // 2, 960), "一个命令，读遍所有格式", font(FONT_CN_BD, 44),
              TEXT, 255, "mm")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 8.0)


FORMATS = ["PDF", "DOCX", "XLSX", "PPTX", "EPUB", "ZIP / 7Z",
           "SQLite", "Parquet", "MP4", "MP3", "EMAIL", "HTML",
           "JSON", "CSV", "FONT", "PE / BIN"]


def scene_formats(t, stills):
    assert 0 <= t <= 8.0
    img = base_copy()
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    draw_text(d, (W // 2, 150), "17 类格式 · 250 个扩展名", font(FONT_CN_BD, 62),
              TEXT, 255, "mm")
    draw_text(d, (W // 2, 220), "扩展名 + 魔术字节双重识别，改错后缀也能读",
              font(FONT_CN, 30), MUTED, 255, "mm")

    cols, rows = 4, 4
    cw, chh = 380, 130
    gap = 26
    total_w = cols * cw + (cols - 1) * gap
    x0 = (W - total_w) // 2
    y0 = 300
    f = font(FONT_MONO_BD, 34)
    for i, name in enumerate(FORMATS):
        r, c = divmod(i, cols)
        at = 0.35 + i * 0.06
        if t < at:
            continue
        a = ease_out((t - at) / 0.3)
        x = x0 + c * (cw + gap)
        y = y0 + r * (chh + gap) + int((1 - a) * 24)
        d.rounded_rectangle([x, y, x + cw, y + chh], radius=18,
                            fill=(16, 26, 46, int(230 * a)),
                            outline=(45, 70, 110, int(220 * a)), width=2)
        draw_text(d, (x + cw // 2, y + chh // 2), name, f, TEXT, 255 * a, "mm")

    if t > 3.6:
        a = ease_out((t - 3.6) / 0.4)
        draw_text(d, (W // 2, 985), "PDF / Office / 图片 / 音视频 / 压缩包 / 数据库 / 电子书 …",
                  font(FONT_CN, 30), MUTED, 255 * a, "mm")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 8.0)


def scene_storyboard(t, stills):
    assert 0 <= t <= 10.0
    img = base_copy()
    still = stills.get((r"D:\pro\sp\视频-接口之战\out\InterfaceWar.mp4", 120.0))
    if still:
        draw_broll(img, still, 1.02 + 0.03 * t / 10.0, dim=0.90)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    draw_text(d, (90, 110), "视频，也能被读懂", font(FONT_CN_BD, 66), TEXT, 255)
    draw_text(d, (92, 200), "场景分镜 · 一张宫格图覆盖全片", font(FONT_CN, 30), MUTED, 255)

    if os.path.exists(STORYBOARD_SHEET):
        sheet = Image.open(STORYBOARD_SHEET).convert("RGB")
        target_w = 1180
        scale = target_w / sheet.width
        sheet = sheet.resize((target_w, int(sheet.height * scale)), Image.LANCZOS)
        a = ease_out((t - 0.5) / 0.9)
        x = int(lerp(W, 660, a))
        y = 300
        shadow = Image.new("RGBA", (sheet.width + 40, sheet.height + 40), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.rounded_rectangle([10, 10, sheet.width + 30, sheet.height + 30],
                             radius=20, fill=(0, 0, 0, 140))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        ov.alpha_composite(shadow, (x - 20, y - 20))
        ov.paste(sheet, (x, y))
        if a >= 1:
            line_x = x + int((sheet.width) * ((t - 1.4) % 2.4) / 2.4)
            d.line([line_x, y + 8, line_x, y + sheet.height - 8],
                   fill=CYAN + (90,), width=2)

    bullets = [
        ("格位按剪辑密度分配", "快切区获得更短、更密的格子"),
        ("每格取最清晰的一帧", "3 个候选帧 · Laplacian 方差择优"),
        ("编号 + 时间码 + 音频包络", "模型可引用「第 5 格，0:42」"),
    ]
    for i, (head, sub) in enumerate(bullets):
        at = 1.6 + i * 0.7
        if t < at:
            continue
        a = ease_out((t - at) / 0.4)
        y = 330 + i * 150
        d.ellipse([92, y + 8, 108, y + 24], fill=CYAN + (int(255 * a),))
        draw_text(d, (130, y), head, font(FONT_CN_BD, 34), TEXT, 255 * a)
        draw_text(d, (130, y + 50), sub, font(FONT_CN, 24), MUTED, 255 * a)

    if t > 4.0:
        a = ease_out((t - 4.0) / 0.5)
        draw_text(d, (92, 820), "5.5 分钟 → 1 张图 · ≈1.9k tokens",
                  font(FONT_CN_BD, 30), CYAN, 255 * a)
        draw_text(d, (92, 870), "逐帧方案做同样覆盖约需 5.3k tokens",
                  font(FONT_CN, 24), MUTED, 255 * a)
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 10.0)


TOOLS = ["atb_read_file", "atb_read_video", "atb_video_storyboard", "atb_extract_text",
         "atb_file_info", "atb_scan_directory", "atb_search_text", "atb_read_batch",
         "atb_list_formats", "atb_video_guide"]


def scene_mcp(t, stills):
    assert 0 <= t <= 8.0
    img = base_copy()
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    draw_text(d, (110, 120), "接入你的 Agent", font(FONT_CN_BD, 66), TEXT, 255)
    draw_text(d, (112, 212), "stdio MCP Server · 10 个工具 · 零额外依赖", font(FONT_CN, 30),
              MUTED, 255)

    # JSON 面板
    panel(ov, [110, 300, 980, 820], 242)
    code = [
        "{",
        '  "mcpServers": {',
        '    "atb": {',
        '      "command": "ATB.exe",',
        '      "args": ["mcp"]',
        "    }",
        "  }",
        "}",
    ]
    shown = int(clamp01((t - 0.5) / 1.6) * sum(len(x) for x in code))
    acc = 0
    y = 345
    f = font(FONT_MONO, 30)
    for line in code:
        if shown <= 0:
            break
        part = line[:max(0, shown - acc)]
        draw_text(d, (150, y), part, f, TEXT if part else MUTED, 255)
        acc += len(line)
        y += 56

    # 工具列表
    f2 = font(FONT_MONO, 24)
    for i, name in enumerate(TOOLS):
        at = 2.2 + i * 0.18
        if t < at:
            continue
        a = ease_out((t - at) / 0.3)
        x = 1050
        y2 = 300 + i * 56
        color = CYAN if i < 3 else TEXT
        d.rounded_rectangle([x, y2, x + 720, y2 + 44], radius=10,
                            fill=(16, 26, 46, int(210 * a)),
                            outline=(45, 70, 110, int(160 * a)))
        draw_text(d, (x + 20, y2 + 22), name, f2, color, 255 * a, "lm")
        if i < 3 and t > 4.0:
            draw_text(d, (x + 620, y2 + 22), "NEW" if i else "core", font(FONT_MONO_BD, 18),
                      TEAL, 220 * a, "lm")

    draw_text(d, (W // 2, 960), "Trae · Claude Desktop · Cursor 均可直接挂载", font(FONT_CN_BD, 38),
              TEXT, 255, "mm")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 8.0)


def scene_perf(t, stills):
    assert 0 <= t <= 7.0
    img = base_copy()
    still = stills.get((r"D:\pro\sp\视频-六位数\out\SixDigits.mp4", 26.0))
    if still:
        draw_broll(img, still, 1.0, dim=0.92)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    draw_text(d, (W // 2, 140), "分镜处理，快了 3 倍", font(FONT_CN_BD, 68), TEXT, 255, "mm")
    draw_text(d, (W // 2, 215), "332 秒 1080p 视频 · 同机实测", font(FONT_CN, 28), MUTED, 255, "mm")

    base_y = 760
    chart_x = 360
    max_h = 380
    bars = [("v1.1.0", 23.6, (120, 135, 160), 0.0),
            ("v1.2.0", 7.98, CYAN, 0.5)]
    bw = 180
    mx = 23.6
    for i, (label, val, color, delay) in enumerate(bars):
        a = ease_out((t - 0.3 - delay) / 0.9)
        h = int(max_h * val / mx * a)
        x = chart_x + i * 360
        d.rounded_rectangle([x, base_y - h, x + bw, base_y], radius=10, fill=color + (235,))
        draw_text(d, (x + bw // 2, base_y - h - 18), f"{val:.1f}s" if a > 0.9 else "",
                  font(FONT_CN_BD, 40), TEXT, 255, "mb")
        draw_text(d, (x + bw // 2, base_y + 24), label, font(FONT_MONO, 26), MUTED, 255, "ma")

    if t > 2.0:
        a = ease_out((t - 2.0) / 0.5)
        draw_text(d, (1220, 420), "-66%", font(FONT_CN_BD, 150), TEAL, 255 * a, "mm")
        draw_text(d, (1220, 540), "耗时", font(FONT_CN, 30), MUTED, 255 * a, "mm")

    if t > 3.2:
        a = ease_out((t - 3.2) / 0.5)
        draw_text(d, (W // 2, 890), "跳过非参考帧 · C 层差分 · 线程池并行 · 进度可预期",
                  font(FONT_CN, 28), MUTED, 255 * a, "mm")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 7.0)


def scene_cta(t, stills):
    assert 0 <= t <= 6.5
    img = base_copy()
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    a = ease_out(t / 0.7)
    icon = load_icon().resize((220, 220), Image.LANCZOS)
    img.paste(icon, (W // 2 - 110, 250), icon)
    draw_text(d, (W // 2, 560), "ATB", font(FONT_CN_BD, 110), TEXT, 255 * a, "mm")
    spaced_text(d, W // 2, 640, "ALL TOOL BOX", font(FONT_MONO_BD, 30), CYAN, 12, 255 * a)
    draw_text(d, (W // 2, 730), "让 Agent 看见一切文件", font(FONT_CN, 40), MUTED, 255 * a, "mm")

    box = [W // 2 - 430, 810, W // 2 + 430, 890]
    d.rounded_rectangle(box, radius=16, fill=(16, 26, 46, int(230 * a)),
                        outline=CYAN + (int(200 * a),), width=2)
    draw_text(d, (W // 2, 850), "github.com/Aniyra/ATB", font(FONT_MONO_BD, 40),
              CYAN, 255 * a, "mm")
    draw_text(d, (W // 2, 940), "v1.2.0 正式版  ·  Windows 单文件  ·  开源", font(FONT_CN, 26),
              MUTED, 255 * a, "mm")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return fade_edges(img, t, 6.5, fade_in=0.4, fade_out=1.2)


def fade_edges(img, t, dur, fade_in=0.3, fade_out=0.3):
    a = 1.0
    if t < fade_in:
        a = t / fade_in
    if t > dur - fade_out:
        a = (dur - t) / fade_out
    a = clamp01(a)
    if a >= 0.999:
        return img
    black = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.blend(black, img, a)


SCENES = [
    (0.0, 4.6, scene_hook),
    (4.6, 12.6, scene_cli),
    (12.6, 20.6, scene_formats),
    (20.6, 30.6, scene_storyboard),
    (30.6, 38.6, scene_mcp),
    (38.6, 45.6, scene_perf),
    (45.6, 52.0, scene_cta),
]


def frame_at(t, stills):
    for start, end, fn in SCENES:
        if start <= t < end:
            return fn(t - start, stills)
    return SCENES[-1][2](SCENES[-1][1] - SCENES[-1][0] - 0.01, stills)


# ── 封面 ─────────────────────────────────────────────────────
def make_cover(stills):
    img = base_copy()
    still = stills.get((r"D:\pro\sp\视频-接口之战\out\InterfaceWar.mp4", 120.0))
    if still:
        draw_broll(img, still, 1.0, dim=0.90)
    # 左侧压暗，保证文字对比
    scrim = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(scrim)
    for x in range(W):
        a = int(232 * max(0.0, 1 - x / (W * 0.72)) ** 1.2)
        sd.line([x, 0, x, H], fill=a)
    shadow = Image.new("RGBA", (W, H), (3, 6, 14, 255))
    img = Image.composite(shadow.convert("RGB"), img, scrim)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    icon = load_icon().resize((170, 170), Image.LANCZOS)
    img.paste(icon, (120, 120), icon)
    draw_text(d, (325, 150), "ATB", font(FONT_CN_BD, 100), TEXT, 255)
    draw_text(d, (330, 255), "All Tool Box", font(FONT_MONO_BD, 34), CYAN, 255)

    draw_text(d, (120, 420), "给 AI Agent 的", font(FONT_CN, 56), TEXT, 255)
    draw_text(d, (120, 500), "全格式文件读取工具箱", font(FONT_CN_BD, 76), TEXT, 255)

    items = ["17 类格式 / 250 扩展名", "CLI · Python API · MCP Server", "视频分镜：一张图看全片"]
    for i, s in enumerate(items):
        y = 660 + i * 70
        d.rounded_rectangle([120, y, 150, y + 30], radius=8, fill=CYAN + (255,))
        draw_text(d, (170, y - 2), s, font(FONT_CN, 34), TEXT, 255)

    draw_text(d, (120, 930), "github.com/Aniyra/ATB", font(FONT_MONO_BD, 34), TEAL, 255)
    draw_text(d, (120, 985), "v1.2.0  ·  Windows 单文件  ·  开源", font(FONT_CN, 24), MUTED, 255)

    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return img


# ── 渲染 ─────────────────────────────────────────────────────
def render(video_path: str, wav_path: str, short: bool = False):
    total = int((6.0 if short else DUR) * FPS)
    container = av.open(video_path, "w")
    vs = container.add_stream("libx264", rate=FPS)
    vs.width, vs.height = W, H
    vs.pix_fmt = "yuv420p"
    vs.options = {"crf": "19", "preset": "medium", "profile": "high"}

    astream = container.add_stream("aac", rate=SR)
    astream.layout = "stereo"
    astream.bit_rate = 192000

    wf = wave.open(wav_path, "rb")
    pcm = wf.readframes(wf.getnframes())
    audio_pos = 0

    def encode_audio(until_sample):
        nonlocal audio_pos
        while audio_pos < until_sample:
            n = min(1024, until_sample - audio_pos)
            raw = pcm[audio_pos * 4: (audio_pos + n) * 4]
            if len(raw) < n * 4:
                return
            af = av.AudioFrame(format="s16", layout="stereo", samples=n)
            af.sample_rate = SR
            af.planes[0].update(raw)
            af.pts = audio_pos
            for pkt in astream.encode(af):
                container.mux(pkt)
            audio_pos += n

    stills = extract_stills()
    t0 = time.perf_counter()
    for i in range(total):
        t = i / FPS
        img = frame_at(t, stills)
        if short and i == 0:
            img.save(os.path.join(PROMO, "preview_0.png"))
        vf = av.VideoFrame.from_image(img)
        vf.pts = i
        for pkt in vs.encode(vf):
            container.mux(pkt)
        encode_audio(int((i + 1) / FPS * SR))
        if i % 60 == 0:
            elapsed = time.perf_counter() - t0
            print(f"[render] {i}/{total}  {i/max(elapsed,0.001):.1f} fps", flush=True)

    for pkt in vs.encode():
        container.mux(pkt)
    encode_audio(int((6.0 if short else DUR) * SR))
    for pkt in astream.encode():
        container.mux(pkt)
    container.close()
    print(f"[done] {video_path}  {os.path.getsize(video_path)/1024/1024:.1f} MB")


def remux_audio(video_path: str, wav_path: str, out_path: str):
    """保留已渲染画面，只替换音轨（用于调整配乐响度）。"""
    inp = av.open(video_path)
    out = av.open(out_path, "w")
    v_in = inp.streams.video[0]
    v_out = out.add_stream_from_template(v_in)
    a_out = out.add_stream("aac", rate=SR)
    a_out.layout = "stereo"
    a_out.bit_rate = 192000

    with wave.open(wav_path, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())

    v_packets = list(inp.demux(v_in))
    a_frames = []
    pos = 0
    total = len(pcm) // 4
    while pos < total:
        n = min(1024, total - pos)
        af = av.AudioFrame(format="s16", layout="stereo", samples=n)
        af.sample_rate = SR
        af.planes[0].update(pcm[pos * 4:(pos + n) * 4])
        af.pts = pos
        for pkt in a_out.encode(af):
            a_frames.append(pkt)
        pos += n
    for pkt in a_out.encode():
        a_frames.append(pkt)

    # 按时间戳交错写入，避免 mp4 muxer 报非单调 dts
    events = []
    for pkt in v_packets:
        if pkt.dts is not None:
            events.append((float(pkt.dts) * float(v_out.time_base), 0, pkt, v_out))
    for pkt in a_frames:
        if pkt.dts is not None:
            events.append((float(pkt.dts) * float(a_out.time_base), 1, pkt, a_out))
    events.sort(key=lambda x: (x[0], x[1]))
    for _, _, pkt, stream in events:
        pkt.stream = stream
        out.mux(pkt)
    out.close()
    inp.close()
    print(f"[remux] {out_path}  {os.path.getsize(out_path)/1024/1024:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", type=float, nargs="+", help="只渲染若干秒为 preview png")
    ap.add_argument("--short", action="store_true", help="渲染 6 秒测试片")
    ap.add_argument("--remux", action="store_true", help="重做配乐并只替换音轨")
    args = ap.parse_args()

    global BASE
    os.makedirs(PROMO, exist_ok=True)
    BASE = make_base()
    stills = extract_stills()

    if args.preview:
        for t in args.preview:
            img = frame_at(t, stills)
            p = os.path.join(PROMO, f"preview_{t:05.1f}.png")
            img.save(p)
            print(p)
        return

    wav = os.path.join(PROMO, "_music.wav")
    video = os.path.join(PROMO, "ATB-Promo-1080p.mp4")

    if args.remux:
        synth_music(wav)
        tmp = os.path.join(PROMO, "_remux.mp4")
        remux_audio(video, wav, tmp)
        os.replace(tmp, video)
        return

    if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
        synth_music(wav)

    cover = make_cover(stills)
    cover.save(os.path.join(PROMO, "cover.png"))
    cover.save(os.path.join(PROMO, "cover.jpg"), quality=92)
    print("[cover] promo/cover.png")

    out = os.path.join(PROMO, "ATB-Promo-1080p-test.mp4" if args.short
                       else "ATB-Promo-1080p.mp4")
    render(out, wav, short=args.short)


if __name__ == "__main__":
    main()
