# -*- coding: utf-8 -*-
"""实验性功能：视频场景分镜（Storyboard）。

把一段视频压成「一张可寻址的宫格图」：每格带编号与时间码，格位按剪辑密度分配，
格内取最清晰的一帧，底部叠加与画面同时间轴的音频包络。

算法思路参考开源项目 SAOS-3624/dsh-video-storyboard（Apache-2.0），
本文件为 ATB 基于 PyAV + Pillow 的独立 Python 实现，未复制其源码：
  - HSV 三通道差做场景切换检测
  - 按局部剪辑密度分配格位，并吸附到真实切点
  - 每格取 3 个候选帧，按 Laplacian 方差选最清晰
  - 音频包络与画面同轴绘制
与上游的差异：不依赖 ffmpeg 可执行文件、不含 ASR/台词转写、不支持 URL 输入。

⚠ 实验性：输出结构、参数与阈值都可能随版本调整。
"""

import array
import hashlib
import math
import os
import time
from typing import List, Optional, Tuple

import config
from ..errors import AtbError
from ..models import Block, Document, ReadOptions
from ..readers.base import new_document
from ..readers.video import probe_video

MAX_CELLS = 64
DEFAULT_BUDGET = 1_200_000
BG = (17, 24, 39)
PANEL = (31, 41, 55)
BORDER = (55, 65, 81)
TEXT = (229, 231, 235)
MUTED = (148, 163, 184)
ACCENT = (56, 189, 248)
ACCENT2 = (45, 212, 191)


def build_storyboard(path: str, det, opts: ReadOptions) -> Document:
    """生成分镜图，返回统一 Document（image 块指向生成的分镜图）。"""
    try:
        import av  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        raise AtbError("ATB-E11001", "分镜功能需要 PyAV 与 Pillow：pip install av pillow")

    info = probe_video(path) or {}
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise AtbError("ATB-E06002", f"无法读取视频时长：{path}")

    start = max(0.0, float(opts.start or 0.0))
    end = float(opts.end) if opts.end is not None else duration
    if end > duration:
        end = duration
        if opts.end is not None:
            doc_end_note = f"--end 超出视频时长，已截断到 {duration:.1f}s"
        else:
            doc_end_note = ""
    else:
        doc_end_note = ""
    if end - start < 0.5:
        raise AtbError("ATB-E00003", f"时间窗过短：{start}s – {end}s")

    doc = new_document(path, det, "video.storyboard(experimental)")
    doc.metadata["experimental"] = True
    if doc_end_note:
        doc.warnings.append(doc_end_note)
    doc.metadata.update({k: info.get(k) for k in
                         ("duration", "width", "height", "fps", "video_codec",
                          "audio_codec", "bit_rate")})
    doc.metadata["window"] = {"start": round(start, 2), "end": round(end, 2)}

    prog = getattr(opts, "progress", None)
    workers = _workers(getattr(opts, "threads", 0))
    total_frames = info.get("frames_total")
    window_frames = (int(total_frames * (end - start) / duration)
                     if total_frames and duration else None)

    # 音频包络与切点扫描并行（无音轨时直接跳过）
    audio_pool = None
    audio_future = None
    if info.get("audio_codec"):
        from concurrent.futures import ThreadPoolExecutor
        audio_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atb-audio")

        def _timed_audio():
            t = time.perf_counter()
            env = _audio_envelope(path, start, end, 900)
            return env, time.perf_counter() - t

        audio_future = audio_pool.submit(_timed_audio)

    t_cuts = time.perf_counter()
    cuts = _detect_cuts(path, start, end, doc, progress=prog,
                        total_frames=window_frames)
    cuts_s = round(time.perf_counter() - t_cuts, 2)
    cells = _plan_cells(cuts, start, end, opts.cells)
    requested = max(1, min(int(opts.sheets or 1), 8))
    per_sheet = int(math.ceil(len(cells) / requested))
    sheets_n = int(math.ceil(len(cells) / per_sheet))
    if len(cells) < requested:
        doc.warnings.append(f"请求 {requested} 张图，但只有 {len(cells)} 格，"
                            f"已输出 {sheets_n} 张")

    aspect = (info.get("width") or 16) / max(info.get("height") or 9, 1)
    budget = max(160_000, min(int(opts.storyboard_budget or DEFAULT_BUDGET), 8_000_000))
    cell_w, cell_h = _cell_size(aspect, budget, per_sheet)

    t_grab = time.perf_counter()
    frames = _grab_cell_frames(path, cells, cell_w, cell_h, doc,
                               progress=prog, workers=workers)
    grab_s = round(time.perf_counter() - t_grab, 2)

    t_audio = time.perf_counter()
    if audio_future is not None:
        try:
            envelope, audio_s = audio_future.result()
        except Exception as e:
            envelope, audio_s = None, 0.0
            doc.warnings.append(f"音频包络失败：{type(e).__name__}: {e}")
        audio_pool.shutdown(wait=False)
    else:
        envelope = None
        audio_s = 0.0
    if audio_s == 0.0 and audio_future is not None:
        audio_s = round(time.perf_counter() - t_audio, 2)

    out_dir = _output_dir(path)

    def render_one(si: int):
        begin = si * per_sheet
        part = cells[begin:begin + per_sheet]
        if not part:
            return None
        part_frames = {i - begin: frames[i]
                       for i in range(begin, min(begin + per_sheet, len(cells)))
                       if i in frames}
        return _render_sheet(part, part_frames, si + 1, sheets_n, begin,
                             cell_w, cell_h, start, end, envelope, out_dir)

    t_render = time.perf_counter()
    if prog is not None:
        prog.stage("生成宫格", total=sheets_n)
    sheet_paths = []
    try:
        if workers > 1 and sheets_n > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results = {}
            with ThreadPoolExecutor(max_workers=min(workers, sheets_n),
                                    thread_name_prefix="atb-sheet") as pool:
                futures = {pool.submit(render_one, si): si for si in range(sheets_n)}
                for fut in as_completed(futures):
                    si = futures[fut]
                    results[si] = fut.result()
                    if prog is not None:
                        prog.update(note=f"sheet {si + 1}/{sheets_n}")
            sheet_paths = [results[i] for i in range(sheets_n) if results.get(i)]
        else:
            for si in range(sheets_n):
                fp = render_one(si)
                if fp:
                    sheet_paths.append(fp)
                if prog is not None:
                    prog.update(note=f"sheet {si + 1}/{sheets_n}")
    finally:
        if prog is not None:
            prog.done(f"{len(sheet_paths)} 张")
    render_s = round(time.perf_counter() - t_render, 2)

    _fill_document(doc, info, cells, frames, cuts, sheet_paths, cell_w, cell_h,
                   start, end, envelope, budget,
                   timings={"cuts": cuts_s, "grab": grab_s, "audio": audio_s,
                            "render": render_s},
                   workers=workers)

    total = round(sum(v for k, v in doc.metadata["storyboard"]["timings"].items()
                      if k != "total"), 2)
    doc.metadata["storyboard"]["timings"]["total"] = total
    return doc


def _workers(requested: int = 0) -> int:
    """智能线程数：小任务单线程，多核时最多 4 个。"""
    if requested and requested > 0:
        return max(1, min(int(requested), 8))
    cpus = os.cpu_count() or 1
    return 1 if cpus <= 2 else min(4, cpus)


# ── 切点检测 ─────────────────────────────────────────────────
def _detect_cuts(path: str, start: float, end: float, doc: Document,
                 progress=None, total_frames: Optional[int] = None) -> List[float]:
    """HSV 三通道差分找场景切换点（C 层 ImageChops，约 6-7 次/秒采样）。"""
    import av
    from PIL import ImageChops, ImageStat

    samples: List[Tuple[float, float]] = []
    prev = None
    prev_t = None
    span = max(end - start, 0.1)
    min_dt = 0.15 if span <= 600 else 0.25
    note = ""
    if progress is not None:
        # 跳过非参考帧后无法预知真实帧数，这里用 spinner + 已用时间，避免虚假 ETA
        progress.stage("扫描切点")
    try:
        try:
            with av.open(path) as c:
                stream = c.streams.video[0]
                stream.thread_type = "AUTO"
                # 切点扫描只需参考帧（I/P）：跳过非参考帧可省近一半解码时间，
                # 采样密度仍远高于 0.15s/次的要求，切点数量实测不受影响。
                try:
                    stream.codec_context.skip_frame = "NONREF"
                except Exception:
                    pass
                for frame in c.decode(stream):
                    t = _frame_time(frame, stream)
                    if t is None:
                        continue
                    if t < start:
                        continue
                    if t > end:
                        break
                    if progress is not None:
                        progress.update(note=f"{t:.0f}s")
                    if prev_t is not None and t - prev_t < min_dt:
                        continue
                    try:
                        img = frame.reformat(width=64, height=36,
                                             format="rgb24").to_image()
                    except Exception:
                        img = frame.to_image().convert("RGB").resize((64, 36))
                    hsv = img.convert("HSV")
                    if prev is not None:
                        stat = ImageStat.Stat(ImageChops.difference(prev, hsv))
                        samples.append((t, sum(stat.mean)))  # |ΔH|+|ΔS|+|ΔV| 均值之和
                    prev, prev_t = hsv, t
        except Exception as e:
            doc.warnings.append(f"切点检测失败，退化为均匀分格：{type(e).__name__}: {e}")
            note = "失败，已退化"
            return []

        if not samples:
            note = "无采样"
            return []

        desired = max(1, min(200, int(span / 1.5)))
        scores = sorted((s for _, s in samples), reverse=True)
        threshold = max(12.0, scores[min(len(scores) - 1, desired - 1)] * 0.85)

        cuts: List[float] = []
        last = -10.0
        for i, (t, score) in enumerate(samples):
            if score < threshold or t - last < 0.35:
                continue
            left = samples[i - 1][1] if i else -1
            right = samples[i + 1][1] if i + 1 < len(samples) else -1
            if score >= left and score >= right:
                cuts.append(round(t, 3))
                last = t
        note = f"{len(cuts)} 个切点"
        return cuts
    finally:
        if progress is not None:
            progress.done(note)


def _frame_time(frame, stream) -> Optional[float]:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is not None and stream.time_base:
        return float(frame.pts * stream.time_base)
    return None


# ── 分格 ─────────────────────────────────────────────────────
def _plan_cells(cuts: List[float], start: float, end: float,
                wanted: Optional[int]) -> List[Tuple[float, float]]:
    span = end - start
    count = int(wanted) if wanted else max(6, min(16, int(round(span / 20)) + 4))
    count = max(1, min(count, MAX_CELLS))

    # 局部剪辑密度 → 权重积分，快切区多分格
    step = max(span / 600, 0.25)
    xs, cum = [], [0.0]
    t = start
    while t < end:
        rate = sum(1 for cut in cuts if t - 2 <= cut < t + 2)
        weight = 1.0 + min(rate, 8) * 1.5
        xs.append(t)
        cum.append(cum[-1] + weight * step)
        t += step
    xs.append(end)
    total_w = cum[-1]

    bounds = [start]
    j = 0
    for i in range(1, count):
        target = total_w * i / count
        while j < len(cum) - 1 and cum[j] < target:
            j += 1
        guess = xs[min(j, len(xs) - 1)]
        # 吸附到最近的切点（位移不超过理想格长的 30%）
        ideal = span / count
        near = min(cuts, key=lambda c: abs(c - guess)) if cuts else None
        if near is not None and abs(near - guess) <= ideal * 0.3:
            guess = near
        if guess > bounds[-1] + 0.2:
            bounds.append(guess)
    bounds.append(end)

    cells = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a >= 0.15:
            cells.append((a, b))
    return cells or [(start, end)]


# ── 取帧 ─────────────────────────────────────────────────────
def _grab_cell_frames(path: str, cells, cell_w: int, cell_h: int,
                      doc: Document, progress=None, workers: int = 1) -> dict:
    """每格取 3 个候选帧，按 Laplacian 方差保留最清晰的一张。

    候选帧的缩放与锐度计算可并行：解码仍在主线程顺序进行，
    只把「转图 + 拉普拉斯」提交到线程池，并用在途上限控制内存。
    """
    import av

    pending = []
    for i, (a, b) in enumerate(cells):
        for frac in (0.3, 0.55, 0.8):
            pending.append((a + (b - a) * frac, i))
    pending.sort()
    total = len(pending)

    best = {}
    note = ""
    if progress is not None:
        progress.stage("抽取画面", total=total)

    def merge(item):
        if not item:
            return
        idx, sharp, img, t = item
        if idx not in best or sharp > best[idx][0]:
            best[idx] = (sharp, img, t)
        if progress is not None:
            progress.update(note=f"{len(best)}/{len(cells)} 格")

    def process(frame, target, idx):
        try:
            return _frame_candidate(frame, target, idx, cell_w, cell_h)
        except Exception:
            return None

    try:
        with av.open(path) as c:
            stream = c.streams.video[0]
            stream.thread_type = "AUTO"
            if workers > 1 and total >= 6:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="atb-frame") as pool:
                    inflight = []
                    for frame in c.decode(stream):
                        t = _frame_time(frame, stream)
                        if t is None:
                            continue
                        while pending and t >= pending[0][0] - 0.02:
                            target, idx = pending.pop(0)
                            if t - target > 1.5:
                                continue
                            inflight.append(pool.submit(process, frame, target, idx))
                            if len(inflight) >= workers * 3:
                                for fut in inflight:
                                    merge(fut.result())
                                inflight.clear()
                        if not pending:
                            break
                    for fut in inflight:
                        merge(fut.result())
            else:
                for frame in c.decode(stream):
                    t = _frame_time(frame, stream)
                    if t is None:
                        continue
                    while pending and t >= pending[0][0] - 0.02:
                        target, idx = pending.pop(0)
                        if t - target > 1.5:
                            continue
                        merge(process(frame, target, idx))
                    if not pending:
                        break
        note = f"{len(best)}/{len(cells)} 格"
    except Exception as e:
        doc.warnings.append(f"取帧失败：{type(e).__name__}: {e}")
    finally:
        if progress is not None:
            progress.done(note)
    if len(best) < len(cells):
        doc.warnings.append(f"{len(cells) - len(best)} 个格子未取到画面")
    return best


def _frame_candidate(frame, target: float, idx: int, cell_w: int, cell_h: int):
    """把一个候选帧缩到格尺寸并计算清晰度（线程池中执行）。"""
    from PIL import Image, ImageFilter, ImageStat

    try:
        img = frame.reformat(width=cell_w, height=cell_h, format="rgb24").to_image()
    except Exception:
        img = frame.to_image().convert("RGB")
        img.thumbnail((cell_w, cell_h), Image.LANCZOS)
    gray = img.convert("L")
    lap = gray.filter(ImageFilter.Kernel(
        (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
    return idx, ImageStat.Stat(lap).var[0], img, round(target, 2)


# ── 音频包络 ─────────────────────────────────────────────────
def _audio_envelope(path: str, start: float, end: float, buckets: int = 900):
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(path) as c:
            if not c.streams.audio:
                return None
            stream = c.streams.audio[0]
            values = [0.0] * buckets
            counts = [0] * buckets
            span = max(end - start, 0.01)
            for frame in c.decode(stream):
                t = _frame_time(frame, stream)
                if t is None or t < start or t > end:
                    continue
                data = bytes(frame.planes[0])
                rms = _rms(data, frame.format.name)
                idx = min(buckets - 1, max(0, int((t - start) / span * buckets)))
                values[idx] += rms
                counts[idx] += 1
            merged = [v / c if c else 0.0 for v, c in zip(values, counts)]
            peak = max(merged) or 1.0
            return [v / peak for v in merged]
    except Exception:
        return None


def _rms(data: bytes, fmt: str) -> float:
    try:
        if fmt.startswith(("flt", "fltp")):
            n = min(len(data) // 4, 512)
            arr = array.array("f")
            arr.frombytes(data[: n * 4])
        elif fmt.startswith(("s16", "s16p")):
            n = min(len(data) // 2, 512)
            arr = array.array("h")
            arr.frombytes(data[: n * 2])
        elif fmt.startswith(("s32", "s32p")):
            n = min(len(data) // 4, 512)
            arr = array.array("i")
            arr.frombytes(data[: n * 4])
        elif fmt.startswith("u8"):
            n = min(len(data), 512)
            arr = [b - 128 for b in data[:n]]
        else:
            return 0.0
        if not arr:
            return 0.0
        return math.sqrt(sum(float(x) * float(x) for x in arr) / len(arr))
    except Exception:
        return 0.0


# ── 排版与绘制 ───────────────────────────────────────────────
def _cell_size(aspect: float, budget: int, cells_on_sheet: int) -> Tuple[int, int]:
    area = budget / max(cells_on_sheet, 1)
    h = int(math.sqrt(area / max(aspect, 0.2)))
    w = int(h * aspect)
    w = max(160, min(w, 900))
    h = max(90, int(w / max(aspect, 0.2)))
    return w, h


def _render_sheet(cells, frames: dict, sheet_no: int, sheets_n: int, offset: int,
                  cell_w: int, cell_h: int, start: float, end: float,
                  envelope, out_dir: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    n = len(cells)
    cols = min(4, int(math.ceil(math.sqrt(n))))
    rows = int(math.ceil(n / cols))
    gap = 8
    header_h = 20
    label_h = 22
    audio_h = 48 if envelope else 0
    width = cols * cell_w + (cols + 1) * gap
    height = header_h + rows * (cell_h + label_h) + (rows + 1) * gap + audio_h

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)
    font = _font(13)
    small = _font(11)
    bold = _font(12, bold=True)

    draw.text((gap, 4), "ATB STORYBOARD · 实验性", fill=ACCENT, font=small)
    label = f"sheet {sheet_no}/{sheets_n}"
    draw.text((width - 8 - len(label) * 6, 4), label, fill=MUTED, font=small)

    for i, (a, b) in enumerate(cells):
        r, c = divmod(i, cols)
        x = gap + c * (cell_w + gap)
        y = header_h + gap + r * (cell_h + label_h + gap)

        item = frames.get(i)
        if item:
            img = item[1]
            canvas.paste(img, (x + (cell_w - img.width) // 2, y + (cell_h - img.height) // 2))
        else:
            draw.rectangle([x, y, x + cell_w, y + cell_h], fill=PANEL)
            draw.text((x + 8, y + 8), "no frame", fill=MUTED, font=small)
        draw.rectangle([x, y, x + cell_w, y + cell_h], outline=BORDER, width=1)

        # 编号徽标（跨多张时连续编号）
        badge = str(offset + i + 1)
        bw = 8 + len(badge) * 8
        draw.rounded_rectangle([x + 6, y + 6, x + 6 + bw, y + 22], radius=4, fill=(0, 0, 0))
        draw.text((x + 10, y + 8), badge, fill=ACCENT, font=bold)

        label = f"{_tc(a)}–{_tc(b)}"
        draw.text((x, y + cell_h + 4), label, fill=TEXT, font=font)
        if item:
            draw.text((x + cell_w - 74, y + cell_h + 5),
                      f"sharp {item[0]:.0f}", fill=MUTED, font=small)

    # 音频条带（与画面同时间轴）
    if envelope:
        top = height - audio_h
        draw.rectangle([0, top, width, height], fill=PANEL)
        mid = top + audio_h // 2
        span = max(end - start, 0.01)
        n_pts = len(envelope)
        for px in range(width):
            t = start + px / max(width - 1, 1) * span
            idx = min(n_pts - 1, max(0, int((t - start) / span * n_pts)))
            amp = int(envelope[idx] * (audio_h // 2 - 4))
            if amp:
                draw.line([px, mid - amp, px, mid + amp], fill=ACCENT2)
        draw.line([0, mid, width, mid], fill=(15, 20, 32))
        for a, b in cells:
            bx = int((a - start) / span * (width - 1))
            draw.line([bx, top, bx, height], fill=(70, 80, 100))
        draw.text((6, top + 2), "audio", fill=MUTED, font=small)
    elif audio_h == 0:
        draw.text((8, height - 14), "no audio track", fill=MUTED, font=small)

    fn = os.path.join(out_dir, f"sheet_{sheet_no:02d}.jpg")
    canvas.save(fn, "JPEG", quality=86, optimize=True)
    return fn


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["arialbd.ttf", "msyhbd.ttc"] if bold
             else ["arial.ttf", "msyh.ttc", "DejaVuSans.ttf"])
    for name in names + ["arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _tc(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


# ── 输出 ─────────────────────────────────────────────────────
def _output_dir(path: str) -> str:
    try:
        st = os.stat(path)
        key = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        key = os.path.abspath(path)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(config.REPORT_DIR, "storyboards", f"{stem}_{digest}")
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(out):
        if name.startswith("sheet_") and name.endswith(".jpg"):
            try:
                os.remove(os.path.join(out, name))
            except OSError:
                pass
    return out


def _fill_document(doc: Document, info: dict, cells, frames, cuts, sheet_paths,
                   cell_w: int, cell_h: int, start: float, end: float, envelope,
                   budget: int, timings: Optional[dict] = None,
                   workers: int = 1):
    from PIL import Image

    doc.blocks.append(Block("heading", "视频分镜（实验性）", level=1))
    doc.blocks.append(Block("paragraph",
                            f"{_tc(start)}–{_tc(end)} 共 {len(cells)} 格，"
                            f"检测到 {len(cuts)} 个切点，输出 {len(sheet_paths)} 张宫格图。"
                            f"每格 {cell_w}×{cell_h}。"))

    tokens = 0
    for fp in sheet_paths:
        with Image.open(fp) as im:
            w, h = im.size
        tokens += int(round(w * h / 750))
        doc.blocks.append(Block("image", f"分镜图 {os.path.basename(fp)}",
                                meta={"path": fp, "width": w, "height": h}))

    rows = []
    for i, (a, b) in enumerate(cells, 1):
        item = frames.get(i - 1)
        rows.append([i, _tc(a), _tc(b),
                     f"{item[2]}s" if item else "-",
                     f"{item[0]:.0f}" if item else "-"])
    if rows:
        doc.blocks.append(Block("table", "", meta={
            "header": ["格", "开始", "结束", "取帧时间", "清晰度"],
            "rows": rows[:100],
        }))

    doc.metadata["storyboard"] = {
        "experimental": True,
        "cells": len(cells),
        "cuts": len(cuts),
        "sheets": sheet_paths,
        "cell_size": [cell_w, cell_h],
        "budget": budget,
        "tokens_estimate": tokens,
        "has_audio": envelope is not None,
        "cell_table": [
            {"index": i, "start": round(a, 2), "end": round(b, 2)}
            for i, (a, b) in enumerate(cells, 1)
        ],
        "upstream": "https://github.com/SAOS-3624/dsh-video-storyboard",
        "timings": timings or {},
        "workers": workers,
    }
    doc.warnings.append("⚠ 实验性功能：分镜参数与输出结构可能变化")
    doc.warnings.append("本实现不含 ASR/台词转写；音频仅以包络形式呈现")
    if envelope is None:
        doc.warnings.append("未检测到音轨，分镜图底部无音频包络")
    if len(sheet_paths) > 1:
        doc.warnings.append("多图分镜会按张数累计 token，注意上下文预算")
