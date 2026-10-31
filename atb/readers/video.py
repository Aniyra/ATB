# -*- coding: utf-8 -*-
"""视频解析器：元数据 + 关键抽帧 + 可选音轨导出。

设计目标：让具备原生视觉能力的 Agent「真正看懂视频」——
把视频切成若干带时间戳的画面帧，MCP 侧再以 image content 回传，
模型即可直接看到画面；不具备视觉能力的模型会收到显式提示。

不设硬性 MB 上限，但会估算图像 token 与传输体积并给出风险提示。
"""

import base64
import hashlib
import math
import os
from typing import List, Optional

import config
from ..models import Block, Document, ReadOptions
from .base import BaseReader, new_document

MAX_FRAMES = 240           # 参数安全阈值（非硬性内容上限）
MIN_FRAMES = 1


class VideoReader(BaseReader):
    name = "video"
    formats = ("video",)

    def read(self, path: str, det, opts: ReadOptions) -> Document:
        doc = new_document(path, det, "video.frames")
        info = probe_video(path)
        if info:
            doc.metadata.update(info)
        else:
            doc.warnings.append("未安装 PyAV/ffprobe，只能返回文件级信息；"
                                "pip install av 后可抽帧理解画面")

        duration = float(info.get("duration") or 0) if info else 0.0
        start, end = _window(opts, duration)
        frame_count = max(MIN_FRAMES, min(int(opts.frames or 12), MAX_FRAMES))
        if int(opts.frames or 12) > MAX_FRAMES:
            doc.warnings.append(f"frames 已按安全阈值截到 {MAX_FRAMES}；"
                                f"更多画面请用 --start/--end 分段读取")

        doc.metadata["window"] = {"start": round(start, 2), "end": round(end, 2)}
        doc.metadata["sampling"] = opts.sampling

        frames: List[dict] = []
        if info and info.get("has_video"):
            out_dir = _frame_dir(path, opts.frame_dir)
            prog = getattr(opts, "progress", None)
            try:
                frames = _extract_frames(path, duration, start, end, frame_count,
                                         opts.frame_width, opts.frame_quality,
                                         opts.sampling, out_dir, doc,
                                         progress=prog, total_frames=info.get("frames_total"))
            except Exception as e:  # 抽帧失败不应让整个解析失败
                doc.warnings.append(f"抽帧失败：{type(e).__name__}: {e}")
                out_dir = None
                if prog is not None:
                    prog.close()
            if frames:
                doc.metadata["frames"] = frames
                doc.metadata["frame_dir"] = out_dir
            if opts.include_audio:
                audio = _extract_audio(path, out_dir) if out_dir else None
                if audio:
                    doc.metadata["audio_path"] = audio
                elif info.get("audio_codec"):
                    doc.warnings.append("音轨导出失败（编码不受容器支持），已跳过")
                else:
                    doc.warnings.append("该视频没有音轨，未导出音频")

        _build_blocks(doc, info, frames, duration)
        _attach_budget(doc, frames)
        return doc


# ── 探测器 ───────────────────────────────────────────────────
def probe_video(path: str) -> Optional[dict]:
    """优先 PyAV，其次 ffprobe；都不可用返回 None。"""
    info = _probe_pyav(path)
    if info:
        return info
    try:
        from .media import _ffprobe
        raw = _ffprobe(path)
    except Exception:
        raw = None
    if not raw:
        return None
    streams = raw.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": round(float(raw.get("duration") or 0), 2),
        "format_name": raw.get("format_name"),
        "bit_rate": raw.get("bit_rate"),
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_video": bool(video),
        "probe": "ffprobe",
        "tags": raw.get("tags") or {},
    }


def _probe_pyav(path: str) -> Optional[dict]:
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video else None
            a = c.streams.audio[0] if c.streams.audio else None
            duration = float(c.duration / av.time_base) if c.duration else 0.0
            fps = None
            if v is not None and v.average_rate:
                fps = round(float(v.average_rate), 3)
            return {
                "duration": round(duration, 2),
                "width": v.codec_context.width if v is not None else None,
                "height": v.codec_context.height if v is not None else None,
                "fps": fps,
                "frames_total": v.frames if v is not None else None,
                "video_codec": v.codec_context.name if v is not None else None,
                "pix_fmt": v.codec_context.pix_fmt if v is not None else None,
                "audio_codec": a.codec_context.name if a is not None else None,
                "audio_channels": a.codec_context.channels if a is not None else None,
                "has_video": v is not None,
                "bit_rate": c.bit_rate,
                "format_name": c.format.name,
                "tags": {k: str(val) for k, val in (c.metadata or {}).items()},
                "probe": "pyav",
            }
    except Exception:
        return None


# ── 抽帧 ─────────────────────────────────────────────────────
def _window(opts: ReadOptions, duration: float):
    start = max(0.0, float(opts.start or 0.0))
    end = float(opts.end) if opts.end is not None else duration
    if duration and end > duration:
        end = duration
    if end <= start:
        end = duration if duration else start + 1
    return start, end


def _frame_dir(path: str, custom: Optional[str]) -> str:
    if custom:
        out = os.path.abspath(custom)
    else:
        try:
            st = os.stat(path)
            key = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            key = os.path.abspath(path)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(config.REPORT_DIR, "frames", f"{stem}_{digest}")
    os.makedirs(out, exist_ok=True)
    # 清掉上次生成的帧，避免新旧混杂
    for name in os.listdir(out):
        if name.startswith("frame_") or name.startswith("audio."):
            try:
                os.remove(os.path.join(out, name))
            except OSError:
                pass
    return out


def _extract_frames(path, duration, start, end, count, width, quality,
                    sampling, out_dir, doc: Document, progress=None,
                    total_frames=None) -> List[dict]:
    try:
        import av
        from PIL import Image
    except ImportError:
        doc.warnings.append("抽帧需要 PyAV 与 Pillow：pip install av pillow")
        return []

    width = max(128, min(int(width or 768), 1920))
    quality = max(50, min(int(quality or 80), 95))
    count = max(MIN_FRAMES, min(int(count), MAX_FRAMES))

    if sampling == "keyframe":
        times = _keyframe_times(path, start, end, count)
        if len(times) < 2:
            times = _uniform_times(start, end, count)
    else:
        times = _uniform_times(start, end, count)

    # 注意：PyAV 在同一容器上反复 seek 并不可靠（实测会跳到错误位置），
    # 因此统一采用「单次顺序解码 + 命中目标时间点取帧」，稳定且可预测。
    frames: List[dict] = []
    pending = sorted(times)
    idx = 0
    if progress is not None:
        progress.stage("解码视频", total=total_frames)
    try:
        with av.open(path) as c:
            stream = c.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in c.decode(stream):
                t_frame = _frame_time(frame, stream)
                if t_frame is None:
                    continue
                if progress is not None:
                    progress.update(note=f"{t_frame:.0f}s")
                while pending and t_frame + 1e-3 >= pending[0]:
                    target = pending.pop(0)
                    idx += 1
                    img = frame.to_image().convert("RGB")
                    img.thumbnail((width, width), Image.LANCZOS)
                    name = f"frame_{idx:03d}_t{target:07.2f}s.jpg"
                    fp = os.path.join(out_dir, name)
                    img.save(fp, "JPEG", quality=quality, optimize=True)
                    frames.append({
                        "index": idx,
                        "time": round(target, 2),
                        "path": fp,
                        "width": img.width,
                        "height": img.height,
                        "bytes": os.path.getsize(fp),
                    })
                if not pending:
                    break
    finally:
        if progress is not None:
            progress.done(f"{len(frames)} 帧")
    if pending:
        doc.warnings.append(f"{len(pending)} 个目标时间点超出可解码范围，已跳过")
    if not frames:
        doc.warnings.append("未取到任何画面帧（时间窗可能超出视频范围）")
    return frames


def _frame_time(frame, stream) -> Optional[float]:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is not None and stream.time_base:
        return float(frame.pts * stream.time_base)
    return None


def _uniform_times(start: float, end: float, count: int) -> List[float]:
    span = max(end - start, 0.001)
    return [round(start + (i + 0.5) * span / count, 3) for i in range(count)]


def _keyframe_times(path: str, start: float, end: float, count: int) -> List[float]:
    try:
        import av
    except ImportError:
        return []
    times: List[float] = []
    try:
        with av.open(path) as c:
            stream = c.streams.video[0]
            stream.thread_type = "AUTO"
            stream.codec_context.skip_frame = "NONKEY"
            for frame in c.decode(stream):
                t = _frame_time(frame, stream)
                if t is None:
                    continue
                if t > end:
                    break
                if t >= start:
                    times.append(round(t, 3))
    except Exception:
        return []
    if not times:
        return []
    if len(times) <= count:
        return times
    step = (len(times) - 1) / (count - 1)
    return [times[round(i * step)] for i in range(count)]


def _extract_audio(path: str, out_dir: str) -> Optional[str]:
    """把音轨原样导出（stream copy），供具备音频能力的模型或外部工具使用。

    PyAV 19 起 add_stream(template=...) 改为 add_stream_from_template()，
    旧版本仍用 template 关键字；这里做兼容。容器不兼容时按候选扩展名重试。
    """
    try:
        import av
    except ImportError:
        return None

    for ext in _audio_exts(path):
        out_path = os.path.join(out_dir, "audio" + ext)
        try:
            with av.open(path) as src:
                if not src.streams.audio:
                    return None
                stream = src.streams.audio[0]
                with av.open(out_path, "w") as dst:
                    out_stream = _add_template_stream(dst, stream)
                    for packet in src.demux(stream):
                        if packet.dts is None:
                            continue
                        packet.stream = out_stream
                        dst.mux(packet)
            if os.path.getsize(out_path) > 0:
                return out_path
        except Exception:
            pass
        try:
            os.remove(out_path)
        except OSError:
            pass
    return None


def _add_template_stream(dst, src_stream):
    """兼容 PyAV 新旧 API：从已有流创建输出流。"""
    fn = getattr(dst, "add_stream_from_template", None)
    if fn is not None:
        return fn(src_stream)
    return dst.add_stream(template=src_stream)


def _audio_exts(path: str):
    """按音频编码给出容器候选顺序。"""
    codec = ""
    try:
        import av
        with av.open(path) as src:
            if src.streams.audio:
                codec = (src.streams.audio[0].codec_context.name or "").lower()
    except Exception:
        pass
    if codec.startswith("pcm"):
        return [".wav", ".mka"]
    if codec.startswith("aac") or codec == "alac":
        return [".m4a", ".mka"]
    if codec.startswith("mp3"):
        return [".mp3", ".mka"]
    if codec in ("opus", "vorbis"):
        return [".ogg", ".mka"]
    if codec.startswith("flac"):
        return [".flac", ".mka"]
    return [".mka", ".wav"]


# ── 输出与预算 ───────────────────────────────────────────────
def _build_blocks(doc: Document, info: Optional[dict], frames: List[dict], duration: float):
    name = os.path.basename(doc.path)
    title = ""
    if info:
        title = (info.get("tags") or {}).get("title") or ""
    doc.blocks.append(Block("heading", title or name, level=1))
    if info:
        doc.blocks.append(Block("kv", "视频信息", meta={"metadata": {
            "时长": f"{duration:.2f}s",
            "分辨率": f"{info.get('width')}×{info.get('height')}",
            "帧率": info.get("fps"),
            "视频编码": info.get("video_codec"),
            "音频编码": info.get("audio_codec") or "无",
            "码率": info.get("bit_rate"),
            "探测器": info.get("probe"),
        }}))
    doc.blocks.append(Block("paragraph",
                            f"已按 {doc.metadata.get('sampling')} 采样抽取 {len(frames)} 帧，"
                            f"时间窗 {doc.metadata['window']['start']}s–"
                            f"{doc.metadata['window']['end']}s。"))
    for f in frames:
        doc.blocks.append(Block(
            "image", f"[{fmt_time(f['time'])}] 第 {f['index']} 帧",
            meta={"path": f["path"], "time": f["time"],
                  "width": f["width"], "height": f["height"]},
        ))


def _attach_budget(doc: Document, frames: List[dict]):
    if not frames:
        return
    tokens = sum(int(round(f["width"] * f["height"] / 750)) for f in frames)
    raw = sum(f["bytes"] for f in frames)
    b64 = int(math.ceil(raw * 4 / 3))
    doc.metadata["budget"] = {
        "frames": len(frames),
        "image_bytes": raw,
        "image_bytes_human": _human(raw),
        "base64_bytes": b64,
        "base64_human": _human(b64),
        "tokens_estimate": tokens,
        "note": "token 估算：宽×高/750；base64 体积按原始字节×1.37",
    }
    if tokens > 32_000:
        doc.warnings.append(
            f"画面预计占用约 {tokens} tokens（超过常见 128k 上下文的 25%）；"
            f"建议减少 frames 或用 --start/--end 分段读取")
    elif tokens > 12_000:
        doc.warnings.append(f"画面预计占用约 {tokens} tokens，注意上下文预算")
    if b64 > 8 * 1024 * 1024:
        doc.warnings.append(f"图片传输体积约 {_human(b64)}，建议降低 --width 或 --frames")
    window = doc.metadata.get("window") or {}
    duration = float(doc.metadata.get("duration") or 0)
    if duration > 600 and window.get("start", 0) <= 0 and window.get("end", 0) >= duration - 1:
        doc.warnings.append("视频超过 10 分钟，整段抽帧可能丢失细节，建议按章节分段读取")


def frames_as_mcp_images(frames: List[dict]):
    """把帧文件读成 MCP image content。"""
    out = []
    for f in frames:
        try:
            with open(f["path"], "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        out.append({
            "type": "image",
            "data": base64.b64encode(data).decode("ascii"),
            "mimeType": "image/jpeg",
        })
    return out


def fmt_time(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((seconds - total) * 10))
    return f"{h}:{m:02d}:{s:02d}.{ms}" if h else f"{m}:{s:02d}.{ms}"


def _human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
