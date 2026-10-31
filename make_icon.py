# -*- coding: utf-8 -*-
"""生成 ATB (All Tool Box) 专属图标（按尺寸自适应绘制）。

设计分三档，保证 16px 下依然可辨识：
  - 大尺寸（>=96）：深色圆角底 + 青蓝渐变工具箱 + 提手 + 锁扣 + "ATB" 字样
  - 中尺寸（48-95）：放大的工具箱 + 提手 + 锁扣（去掉文字）
  - 小尺寸（<=47）：更粗的工具箱轮廓 + 提手（去掉文字与锁扣，塞满画布）

输出：atb.ico（256/128/64/48/32/16）
用法：python make_icon.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

# ── 配色 ─────────────────────────────────────────────────────
BG_COLOR = (20, 26, 43)        # #141A2B
BG_BORDER = (42, 54, 82)       # #2A3652
ACCENT_TOP = (56, 189, 248)    # #38BDF8
ACCENT_BOTTOM = (45, 212, 191)  # #2DD4BF
HANDLE_COLOR = (240, 246, 252)  # 近白，保证小尺寸对比度
DARK = (12, 17, 28)
TEXT_COLOR = (236, 242, 250)

SIZES = [256, 128, 64, 48, 32, 16]


def _vertical_gradient(height: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (1, height))
    px = grad.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad


def _rounded_mask(size: tuple, radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _load_font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf",
                 "msyhbd.ttc", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _base(S: int) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(S * 0.20)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=BG_COLOR)
    bw = max(2, S // 128)
    d.rounded_rectangle([bw, bw, S - 1 - bw, S - 1 - bw],
                        radius=radius - bw, outline=BG_BORDER, width=bw)
    return img


def _draw_toolbox(img: Image.Image, S: int, *, compact: bool,
                  with_text: bool, with_latch: bool) -> None:
    """在底图上绘制工具箱。compact=True 时整体放大、线条加粗，适配小尺寸。"""
    d = ImageDraw.Draw(img)

    if compact:
        bx0, by0, bx1, by1 = int(S * 0.10), int(S * 0.36), int(S * 0.90), int(S * 0.74)
        handle = [int(S * 0.28), int(S * 0.14), int(S * 0.72), int(S * 0.40)]
        handle_w = max(3, int(S * 0.085))
        seam_w = max(2, int(S * 0.035))
    else:
        bx0, by0, bx1, by1 = int(S * 0.16), int(S * 0.37), int(S * 0.84), int(S * 0.71)
        handle = [int(S * 0.33), int(S * 0.235), int(S * 0.67), int(S * 0.405)]
        handle_w = max(2, int(S * 0.048))
        seam_w = max(2, S // 110)

    # 提手（先画，底部被箱体压住）
    d.arc(handle, start=180, end=360, fill=HANDLE_COLOR, width=handle_w)

    # 箱体
    bw_, bh_ = bx1 - bx0, by1 - by0
    radius = int(S * (0.075 if compact else 0.06))
    grad = _vertical_gradient(bh_, ACCENT_TOP, ACCENT_BOTTOM).resize(
        (bw_, bh_), Image.BILINEAR)
    img.paste(grad, (bx0, by0), _rounded_mask((bw_, bh_), radius))

    # 箱盖缝隙
    seam_y = by0 + int(bh_ * (0.24 if compact else 0.22))
    d.line([bx0 + 2, seam_y, bx1 - 2, seam_y], fill=DARK, width=seam_w)

    # 锁扣（小尺寸省略，避免糊成一团）
    if with_latch:
        lw = int(bw_ * (0.20 if compact else 0.16))
        lh = max(3, int(bh_ * (0.26 if compact else 0.22)))
        lx = (S - lw) // 2
        ly = seam_y - lh // 2
        d.rounded_rectangle([lx, ly, lx + lw, ly + lh],
                            radius=max(2, lh // 3), fill=DARK)

    # 底部 ATB
    if with_text:
        font = _load_font(int(S * 0.135))
        text = "ATB"
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        d.text(((S - tw) // 2 - bbox[0], int(S * 0.735)),
               text, fill=TEXT_COLOR, font=font)


def make_icon(size: int) -> Image.Image:
    """按目标尺寸选择布局，返回该尺寸的图标。"""
    if size >= 96:
        # 大尺寸：细节最全
        S = size * 4
        img = _base(S)
        _draw_toolbox(img, S, compact=False, with_text=True, with_latch=True)
    elif size >= 48:
        # 中尺寸：去掉文字，保留锁扣
        S = size * 4
        img = _base(S)
        _draw_toolbox(img, S, compact=True, with_text=False, with_latch=True)
    else:
        # 小尺寸：超采样 + 更粗线条，只保留最关键的轮廓
        S = size * 8
        img = _base(S)
        _draw_toolbox(img, S, compact=True, with_text=False, with_latch=False)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(root, "atb.ico")
    images = [make_icon(s) for s in SIZES]
    images[0].save(
        out_path, format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )

    # 预览：大图 + 小尺寸放大版，便于肉眼检查 16px 是否可辨
    images[0].save(os.path.join(root, "atb_icon_preview.png"), format="PNG")
    sheet = Image.new("RGBA", (256 + 3 * 128 + 40, 256), (28, 32, 44, 255))
    sheet.alpha_composite(images[0], (10, 0))
    for i, s in enumerate([64, 32, 16], start=1):
        x = 256 + 20 + (i - 1) * 128
        up = images[SIZES.index(s)].resize((s * 4, s * 4), Image.NEAREST)
        sheet.alpha_composite(up, (x, 40))
    sheet.save(os.path.join(root, "atb_icon_sizes.png"), format="PNG")

    print(f"图标已生成: {out_path}")
    print(f"包含尺寸: {SIZES}")
    print(f"文件大小: {os.path.getsize(out_path)} bytes")
    print("预览: atb_icon_preview.png / atb_icon_sizes.png")


if __name__ == "__main__":
    main()
