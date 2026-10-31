# -*- coding: utf-8 -*-
"""MCP Server（stdio 传输，零第三方依赖）。

支持两种分帧：
  1. 换行分隔的 JSON（MCP stdio 官方行为）
  2. LSP 风格 `Content-Length: N\\r\\n\\r\\n{json}`

提供工具：atb_read_file / atb_read_video / atb_video_guide / atb_extract_text /
atb_file_info / atb_scan_directory / atb_search_text / atb_read_batch / atb_list_formats。
"""

import json
import os
import sys
from typing import Dict, List, Optional

import config
from . import dispatch
from .detect import EXT_MAP, FORMAT_MIME, ZIP_FAMILY
from .errors import AtbError
from .models import ReadOptions
from .readers import READERS
from .readers.video import fmt_time, frames_as_mcp_images
from .scanner import scan_directory

SERVER_NAME = "atb"
SERVER_VERSION = config.APP_VERSION
PROTOCOL_VERSION = "2024-11-05"

TOOLS: List[dict] = [
    {
        "name": "atb_read_file",
        "description": "读取任意格式文件并返回统一结构的完整内容（元数据 + 块 + 文本）。"
                       "支持 PDF/Office/图片/压缩包/数据文件/源码等。"
                       "视频文件请优先使用 atb_read_video（会直接回传画面帧）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "format": {"type": "string", "description": "强制指定格式，如 pdf、docx、text"},
                "max_chars": {"type": "integer", "description": "正文最大字符数，默认 200000"},
                "pages": {"type": "string", "description": "PDF 页码，如 '1-5,8'"},
                "sheet": {"type": "string", "description": "仅读取指定工作表/表格"},
                "member": {"type": "string", "description": "读取压缩包内指定条目"},
                "password": {"type": "string", "description": "加密文档/压缩包密码"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_read_video",
        "description": (
            "读取视频并「真正看懂画面」：按时间抽帧，返回时间轴摘要 + MCP image content（JPEG 帧）。"
            "默认 12 帧、768px、约 4–6k tokens、2–4 MB base64；参数可调，无硬上限，"
            "但你必须先向用户说明上下文窗口与传输体积风险。"
            "\n\n⚠ 能力提示：本工具依赖模型的原生图像输入能力。若当前模型/客户端不支持图像内容，"
            "请直接告诉用户「我无法查看视频画面」，并建议改用支持视觉的模型、由用户描述关键画面，"
            "或先用 CLI `atb video` 导出帧；绝不要凭空猜测视频内容。"
            "\n\n预算公式：单帧 tokens≈宽×高/750；图片 token 建议 ≤ 上下文窗口的 25%。"
            "长视频请用 start/end 分段读取（每次 30–60 秒、8–16 帧）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "视频文件绝对路径"},
                "frames": {"type": "integer", "description": "抽帧数，默认 12，安全阈值 240"},
                "start": {"type": "number", "description": "起始秒，默认 0"},
                "end": {"type": "number", "description": "结束秒，默认视频结尾"},
                "width": {"type": "integer", "description": "帧长边像素，默认 768（128–1920）"},
                "quality": {"type": "integer", "description": "JPEG 质量，默认 80（50–95）"},
                "sampling": {"type": "string", "enum": ["uniform", "keyframe"],
                             "description": "uniform 均匀采样；keyframe 按关键帧（适合剪辑类视频）"},
                "audio": {"type": "boolean", "description": "是否导出音轨文件（供具备音频能力的模型使用）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_video_guide",
        "description": "返回视频读取的预算指南：单帧/整包 MB 建议、token 计算公式、不同上下文窗口的帧数上限、"
                       "分段策略与多模态能力声明要求。不确定参数时先调用它。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "atb_extract_text",
        "description": "只提取文件的纯文本，适合直接喂给模型。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "format": {"type": "string"},
                "max_chars": {"type": "integer"},
                "pages": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_file_info",
        "description": "轻量探测：格式、MIME、大小、修改时间、SHA256，不解析正文。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "atb_scan_directory",
        "description": "扫描目录，统计文件数量、体积与格式分布。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean", "default": True},
                "limit": {"type": "integer"},
                "detail": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_search_text",
        "description": "在单个文件（任意格式）中搜索关键词，返回命中片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "query": {"type": "string"},
                "context": {"type": "integer", "default": 80},
                "max_hits": {"type": "integer", "default": 20},
            },
            "required": ["path", "query"],
        },
    },
    {
        "name": "atb_read_batch",
        "description": "批量读取多个文件，返回每个文件的摘要与正文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "max_chars": {"type": "integer"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "atb_list_formats",
        "description": "列出支持的格式、扩展名与对应解析器能力。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpServer:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout

    # ── 传输层 ──────────────────────────────────────────────
    def _read_message(self) -> Optional[dict]:
        headers = {}
        line = self.stdin.readline()
        while line and not line.strip():
            line = self.stdin.readline()
        if not line:
            return None
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
            while True:
                h = self.stdin.readline()
                if not h or h in ("\r\n", "\n"):
                    break
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = self.stdin.read(length)
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        self.stdout.write(data + "\n")
        self.stdout.flush()

    def _result(self, msg_id, result) -> None:
        self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    # ── 主循环 ──────────────────────────────────────────────
    def serve_forever(self) -> None:
        while True:
            msg = self._read_message()
            if msg is None:
                break
            if not isinstance(msg, dict):
                continue
            method = msg.get("method")
            msg_id = msg.get("id")
            params = msg.get("params") or {}
            if method is None:
                continue  # 响应消息，忽略
            if msg_id is None:
                # 通知：initialized / cancelled 等
                if method == "shutdown":
                    break
                continue
            try:
                if method == "initialize":
                    self._result(msg_id, {
                        "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                        "instructions": (
                            "ATB 提供全格式文件读取能力。先调用 atb_file_info 了解文件，"
                            "再用 atb_read_file / atb_extract_text 读取内容。"
                            "\n\n视频：用 atb_read_video，它会回传真实画面帧。视频理解依赖模型的"
                            "原生图像输入能力——若你不支持图像内容，必须告知用户，不要臆测画面。"
                            "图片 token 建议不超过上下文窗口的 25%，长视频用 start/end 分段读取；"
                            "参数不确定时先调用 atb_video_guide 查看预算指南。"
                        ),
                    })
                elif method == "ping":
                    self._result(msg_id, {})
                elif method == "tools/list":
                    self._result(msg_id, {"tools": TOOLS})
                elif method == "tools/call":
                    self._handle_tool(msg_id, params)
                elif method == "resources/list":
                    self._result(msg_id, {"resources": []})
                elif method == "prompts/list":
                    self._result(msg_id, {"prompts": []})
                elif method == "shutdown":
                    self._result(msg_id, None)
                    break
                else:
                    self._error(msg_id, -32601, f"Method not found: {method}")
            except AtbError as e:
                self._tool_error(msg_id, e)
            except Exception as e:  # 保证服务不中断
                self._error(msg_id, -32603, f"Internal error: {type(e).__name__}: {e}")

    # ── 工具分发 ────────────────────────────────────────────
    def _handle_tool(self, msg_id, params: dict) -> None:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            self._error(msg_id, -32602, "arguments 必须是对象")
            return
        try:
            result = self.call_tool(name, args)
            if isinstance(result, list):
                self._result(msg_id, {"content": result, "isError": False})
            else:
                self._result(msg_id, {"content": [{"type": "text", "text": result}],
                                      "isError": False})
        except AtbError as e:
            self._tool_error(msg_id, e)
        except KeyError as e:
            raise AtbError("ATB-E10002", f"缺少参数 {e}")

    def _tool_error(self, msg_id, err: AtbError) -> None:
        payload = err.to_dict(prog="ATB")
        self._result(msg_id, {
            "content": [{"type": "text",
                         "text": f"[{payload['code']}] {payload['title']}\n"
                                 f"详情：{payload['detail']}\n解决方案：{payload['solution']}"}],
            "isError": True,
        })

    def call_tool(self, name: str, args: dict):
        if name == "atb_read_file":
            opts = _opts_from(args)
            doc = dispatch.read_file(args["path"], opts, args.get("format"))
            payload = doc.to_dict(max_chars=opts.max_chars or None)
            if doc.format == "video":
                payload.setdefault("warnings", []).append(
                    "该视频的画面帧未包含在本次响应中；如需让模型看到画面，请改用 atb_read_video。")
            return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if name == "atb_read_video":
            return _video_payload(args)
        if name == "atb_video_guide":
            return VIDEO_GUIDE
        if name == "atb_extract_text":
            opts = _opts_from(args)
            text = dispatch.extract_text(args["path"], opts, args.get("format"))
            return text
        if name == "atb_file_info":
            return json.dumps(dispatch.file_info(args["path"]), ensure_ascii=False, indent=2)
        if name == "atb_scan_directory":
            result = scan_directory(
                args["path"],
                recursive=args.get("recursive", True),
                limit=args.get("limit"),
                include_detail=args.get("detail", True),
            )
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if name == "atb_search_text":
            return _search(args)
        if name == "atb_read_batch":
            paths = args.get("paths") or []
            opts = ReadOptions(max_chars=int(args.get("max_chars") or config.DEFAULT_MAX_CHARS))
            out = []
            for p in paths[:50]:
                doc = dispatch.read_file(p, opts, safe=True)
                out.append({
                    "path": p,
                    "ok": doc.ok,
                    "format": doc.format,
                    "error": doc.error,
                    "text": doc.text(opts.max_chars) if doc.ok else "",
                })
            return json.dumps(out, ensure_ascii=False, indent=2, default=str)
        if name == "atb_list_formats":
            return json.dumps(format_matrix(), ensure_ascii=False, indent=2)
        raise AtbError("ATB-E10002", f"未知工具 {name}")


VIDEO_GUIDE = """# ATB 视频读取预算指南

## 1. 体积
- 单帧建议 ≤ 1 MB：768px 长边 + JPEG q80 通常 40–150 KB。
- 整包含图片合计建议 ≤ 8 MB（base64 后约 11 MB）；默认 12 帧通常只有 1.5–4 MB。
- 以上只统计抽出的帧，不含视频原文件本身。

## 2. Token 计算
- 单帧 tokens ≈ ceil(宽 × 高 / 750)
  - 768×432 ≈ 442；1024×576 ≈ 787；1280×720 ≈ 1229；1920×1080 ≈ 2765。
- 总 tokens ≈ Σ 单帧 + 文本摘要（通常 < 1k）。
- base64 体积 ≈ 原始字节 × 1.37。

## 3. 上下文窗口 → 建议帧数（图片预算 ≤ 窗口 25%）
| 上下文 | 图片预算 | 768p 帧数 | 1280p 帧数 | 1080p 帧数 |
| --- | --- | --- | --- | --- |
| 32k | 8k | 18 | 6 | 2 |
| 128k | 32k | 72 | 26 | 11 |
| 200k | 50k | 113 | 40 | 18 |
| 1M | 250k | 565 | 203 | 90 |

## 4. 不设硬上限，但必须给足风险提示
- 参数允许 frames 1–240、width 128–1920；超过建议值仍会执行。
- 执行前应向用户说明预计 token 与传输体积，并优先建议分段读取。
- 单次 MCP 响应过大可能被客户端截断或超时；base64 超过约 8 MB 建议拆分。

## 5. 分段策略
- 长视频按 30–60 秒切片，每次 8–16 帧。
- 先粗读全片（12 帧）建立时间轴，再对关键片段用 start/end 加密采样。
- 剪辑类、演示类视频可用 sampling=keyframe。

## 6. 能力声明（必须遵守）
- 只有模型具备原生图像输入能力时，才能声称「看过画面」。
- 不具备时：明确告知用户无法直接查看视频，建议换用视觉模型、由用户描述关键画面，
  或先用 CLI `atb video` 导出帧；不要根据文件名/元数据臆测视频内容。
"""


def _video_payload(args: dict):
    """返回 MCP content 列表：文本时间轴 + 真实图片帧。"""
    opts = ReadOptions(
        frames=int(args.get("frames") or 12),
        start=args.get("start"),
        end=args.get("end"),
        frame_width=int(args.get("width") or 768),
        frame_quality=int(args.get("quality") or 80),
        include_audio=bool(args.get("audio")),
        sampling=args.get("sampling") or "uniform",
    )
    doc = dispatch.read_file(args["path"], opts)
    if doc.format != "video":
        return (f"该文件被识别为 `{doc.format}`，不是视频。请改用 atb_read_file 或 "
                f"atb_extract_text。")

    frames = doc.metadata.get("frames") or []
    budget = doc.metadata.get("budget") or {}
    window = doc.metadata.get("window") or {}
    lines = [f"# 视频时间轴：{os.path.basename(doc.path)}", ""]
    lines.append(f"- 时长：{doc.metadata.get('duration')}s；"
                 f"分辨率：{doc.metadata.get('width')}×{doc.metadata.get('height')}；"
                 f"帧率：{doc.metadata.get('fps')}；编码：{doc.metadata.get('video_codec')}")
    lines.append(f"- 时间窗：{window.get('start')}s – {window.get('end')}s；"
                 f"采样：{doc.metadata.get('sampling')}；帧数：{len(frames)}")
    if doc.metadata.get("audio_path"):
        lines.append(f"- 音轨已导出：{doc.metadata['audio_path']}")
    lines.append("")
    lines.append("## 预算")
    if budget:
        lines.append(f"- 图片 {budget.get('image_bytes_human')}；"
                     f"base64 {budget.get('base64_human')}；"
                     f"预计 {budget.get('tokens_estimate')} tokens（公式：宽×高/750）")
    lines.append("- 无硬上限；若超过上下文 25%，请先告知用户并改用 start/end 分段。")
    lines.append("")
    lines.append("## 帧时间轴")
    for f in frames:
        lines.append(f"- {fmt_time(f['time'])} · {f['width']}×{f['height']} · "
                     f"{f['bytes'] // 1024} KB · `{f['path']}`")
    if doc.warnings:
        lines.append("")
        lines.append("## 提示")
        lines.extend(f"- {w}" for w in doc.warnings)
    lines.append("")
    lines.append("## 使用要求")
    lines.append("1. 具备原生图像输入：逐帧查看画面后再回答；时间轴用于定位。")
    lines.append("2. 不具备图像输入：直接告诉用户无法查看画面，不要臆测内容。")
    lines.append("3. 若用户追问细节，优先用 start/end 增加局部帧，而不是一次性拉满 240 帧。")

    content = [{"type": "text", "text": "\n".join(lines)}]
    content.extend(frames_as_mcp_images(frames))
    return content


def _opts_from(args: dict) -> ReadOptions:
    return ReadOptions(
        max_chars=int(args.get("max_chars") or config.DEFAULT_MAX_CHARS),
        pages=args.get("pages"),
        sheet=args.get("sheet"),
        member=args.get("member"),
        password=args.get("password"),
        encoding=args.get("encoding"),
        ocr=bool(args.get("ocr")),
    )


def _search(args: dict) -> str:
    path = args["path"]
    query = args["query"]
    context = int(args.get("context", 80))
    max_hits = int(args.get("max_hits", 20))
    text = dispatch.extract_text(path)
    hits = []
    start = 0
    while len(hits) < max_hits:
        idx = text.find(query, start)
        if idx < 0:
            break
        hits.append({
            "offset": idx,
            "match": text[idx: idx + len(query)],
            "before": text[max(0, idx - context): idx],
            "after": text[idx + len(query): idx + len(query) + context],
        })
        start = idx + len(query)
    return json.dumps({"path": path, "query": query, "hits": hits,
                       "total_hits": len(hits)}, ensure_ascii=False, indent=2)


def format_matrix() -> dict:
    """格式 → 扩展名 / 解析器 / 是否文本。"""
    by_reader: Dict[str, List[str]] = {}
    for reader in READERS:
        by_reader[reader.name] = list(reader.formats)
    ext_by_format: Dict[str, List[str]] = {}
    for ext, fmt in sorted(EXT_MAP.items()):
        ext_by_format.setdefault(fmt, []).append(ext)
    rows = {}
    for reader in READERS:
        for fmt in reader.formats:
            rows[fmt] = {
                "reader": reader.name,
                "mime": FORMAT_MIME.get(fmt, "application/octet-stream"),
                "extensions": ext_by_format.get(fmt, []),
            }
    return {"formats": rows, "total_extensions": len(EXT_MAP),
            "zip_family": sorted(ZIP_FAMILY)}


def main() -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    McpServer().serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
