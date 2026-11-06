# -*- coding: utf-8 -*-
"""MCP Server（stdio 传输，零第三方依赖）。

支持两种分帧：
  1. 换行分隔的 JSON（MCP stdio 官方行为）
  2. LSP 风格 `Content-Length: N\\r\\n\\r\\n{json}`

提供工具：atb_read_file / atb_read_video / atb_video_storyboard / atb_video_zoom /
atb_video_guide / atb_query_quota / atb_compress_history / atb_computer_use /
atb_extract_text / atb_file_info / atb_scan_directory / atb_search_text /
atb_read_batch / atb_list_formats。
"""

import base64
import json
import os
import sys
from typing import Dict, List, Optional

import config
from . import dispatch, history, quota
from .detect import EXT_MAP, FORMAT_MIME, ZIP_FAMILY
from .errors import AtbError
from .models import ReadOptions
from .readers import READERS
from .readers.video import export_zoom, fmt_time, frames_as_mcp_images
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
                "max_chars": {"type": "integer",
                              "description": "正文最大字符数，默认 200000；传 0 表示不限制"},
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
        "name": "atb_video_storyboard",
        "description": (
            "场景分镜：把整段视频压成一张可寻址的宫格图（编号 + 时间码 + 音频包络），"
            "以 MCP image content 回传。相比逐帧方案更省 token：一张 1.2M 像素的图约 1600 tokens，"
            "即可覆盖全片；格位按剪辑密度分配，快切区获得更多格。"
            "\n\n参数：cells 格数（默认按时长/密度自动，上限 64）、sheets 张数（默认 1）、"
            "budget 单张像素预算（默认 1200000）、start/end 时间窗、subs 字幕文件。"
            "若视频旁有同名 .srt/.vtt 会自动对齐台词；也可显式传 subs。"
            "\n\n若模型不具备原生图像输入能力，必须告知用户无法查看画面，不要臆测内容。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "视频文件绝对路径"},
                "cells": {"type": "integer", "description": "格数，默认自动（4-16），上限 64"},
                "sheets": {"type": "integer", "description": "输出几张宫格图，默认 1，上限 8"},
                "budget": {"type": "integer", "description": "单张图像素预算，默认 1200000"},
                "start": {"type": "number", "description": "起始秒"},
                "end": {"type": "number", "description": "结束秒"},
                "subs": {"type": "string", "description": "字幕文件路径（srt/vtt），可选"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_video_zoom",
        "description": (
            "取视频某一时刻的全分辨率画面，可按归一化区域裁剪放大，用于看清分镜小格里的"
            "文字、接口、图表细节。返回一张 image content。先用 atb_video_storyboard 定位格子，"
            "再用本工具放大该格时间点。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "视频文件绝对路径"},
                "time": {"type": "number", "description": "时刻（秒）"},
                "box": {
                    "type": "array", "items": {"type": "number"},
                    "description": "归一化区域 [x,y,w,h]，取值 0-1；省略为整帧",
                },
                "width": {"type": "integer", "description": "输出宽度，默认 1600（128-3840）"},
            },
            "required": ["path", "time"],
        },
    },
    {
        "name": "atb_query_quota",
        "description": (
            "查询 AI API 余额/额度（可选功能）。支持 DeepSeek / Moonshot / SiliconFlow / OpenRouter；"
            "密钥从环境变量或 ~/.atb/config.json 读取。未配置时返回配置指引，不要把它当成错误。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string",
                             "description": "deepseek / moonshot / siliconflow / openrouter；省略查全部"},
            },
        },
    },
    {
        "name": "atb_compress_history",
        "description": (
            "压缩长对话历史，输出可续接的结构化摘要（确定性抽取，不调用模型）。"
            "支持 json（messages 数组）/ jsonl（每行一条）/ markdown / 纯文本对话记录。"
            "产物包含：目标与决策、关键文件/命令/错误码、早期对话单行摘要、最近若干轮原文、"
            "以及续接提示。适合上下文将满或需要交接给另一个 Agent 时使用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "对话存档路径"},
                "target_tokens": {"type": "integer",
                                  "description": "目标 token 预算，默认 6000（下限 600）"},
                "keep_recent": {"type": "integer",
                                "description": "保留最近多少条原文，默认 8"},
                "max_chars": {"type": "integer",
                              "description": "返回文本最大字符数，默认 40000"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "atb_computer_use",
        "description": (
            "实验性：操作 Windows 桌面应用（复刻 Trae Computer Use 的基础能力）。"
            "动作：available / list / state / launch / click / scroll / drag / type / key / "
            "select / set_value / action / ground。"
            "\n\n使用顺序建议：list 找 app_id → state 看元素树 → ground 定位 → click/type。"
            "element_id 只在同一窗口当前状态下有效，界面变化后要重新 state。"
            "\n\n⚠ 实验性且仅支持 Windows，不保证在所有应用上可用；失败时如实告知用户，"
            "不要反复盲试。执行点击/输入/启动等有副作用的操作前，先向用户确认目标应用与意图；"
            "每完成一步用一句话说明做了什么。"
            "\n\n缺少 uiautomation 时元素树退化为子窗口枚举，此时请用截图 + 坐标操作。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["available", "list", "state", "launch", "click",
                                    "scroll", "drag", "type", "key", "select",
                                    "set_value", "action", "ground"]},
                "app": {"type": "string",
                        "description": "app_id / 进程名 / 标题片段 / hwnd:123 / pid:123"},
                "x": {"type": "integer", "description": "click 屏幕 X"},
                "y": {"type": "integer", "description": "click 屏幕 Y"},
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "element_id": {"type": "string", "description": "state 返回的元素编号"},
                "text": {"type": "string", "description": "type 输入的文字 / ground 查找的文字"},
                "keys": {"type": "string", "description": "按键组合，如 ctrl+s"},
                "value": {"type": "string", "description": "set_value 的值"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "滚动格数，默认 3"},
                "depth": {"type": "integer", "description": "元素树深度，默认 3"},
                "max_nodes": {"type": "integer", "description": "元素上限，默认 120"},
                "screenshot": {"type": "boolean", "description": "state 时附带回传截图路径"},
                "limit": {"type": "integer", "description": "list 返回窗口数，默认 50"},
                "wait": {"type": "number", "description": "launch 等待窗口秒数，默认 8"},
            },
            "required": ["action"],
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
                "max_chars": {"type": "integer",
                              "description": "最大字符数，默认 200000；传 0 表示不限制"},
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
        "description": "在单个文件（任意格式）中搜索关键词，返回命中片段。"
                       "total_hits 是全部命中数，hits 受 max_hits 限制。",
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
        "description": "批量读取多个文件，返回每个文件的摘要与正文。"
                       "单次最多 200 条；超出部分会返回一条显式的 skipped 记录，不会静默丢弃。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "max_chars": {"type": "integer",
                              "description": "最大字符数，默认 200000；传 0 表示不限制"},
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


class _ParseError(Exception):
    """单条消息格式错误：回 -32700 后继续服务，不视为 EOF。"""


class McpServer:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        # 二进制通道优先：Content-Length 是字节数，不能按字符读
        self._in_bin = self._binary_of(self.stdin)
        self._out_bin = self._binary_of(self.stdout)

    @staticmethod
    def _binary_of(stream):
        if stream is None:
            return None
        try:
            if isinstance(stream.read(0), bytes):
                return stream
        except Exception:
            pass
        return getattr(stream, "buffer", None)

    # ── 传输层 ──────────────────────────────────────────────
    def _readline(self) -> str:
        if self._in_bin is not None:
            raw = self._in_bin.readline()
            return raw.decode("utf-8", "replace") if raw else ""
        return self.stdin.readline()

    def _read_body(self, length: int) -> bytes:
        """按字节读取消息体；文本流回退时按 UTF-8 字节数累计。"""
        if self._in_bin is not None:
            return self._in_bin.read(length)
        chunks, total = [], 0
        while total < length:
            ch = self.stdin.read(1)
            if not ch:
                break
            chunks.append(ch)
            total += len(ch.encode("utf-8"))
        return "".join(chunks).encode("utf-8")

    def _read_message(self) -> Optional[dict]:
        headers = {}
        line = self._readline()
        while line and not line.strip():
            line = self._readline()
        if not line:
            return None
        if line.lower().lstrip().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                raise _ParseError(f"Content-Length 无效：{line.strip()!r}")
            if length < 0:
                raise _ParseError(f"Content-Length 为负：{length}")
            while True:
                h = self._readline()
                if not h or h in ("\r\n", "\n"):
                    break
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = self._read_body(length)
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise _ParseError(f"消息体不是合法 UTF-8 JSON：{e}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise _ParseError(f"消息不是合法 JSON：{e}")

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        if self._out_bin is not None:
            self._out_bin.write(data.encode("utf-8"))
            self._out_bin.flush()
        else:
            self.stdout.write(data)
            self.stdout.flush()

    def _result(self, msg_id, result) -> None:
        self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    # ── 主循环 ──────────────────────────────────────────────
    def serve_forever(self) -> None:
        while True:
            try:
                msg = self._read_message()
            except _ParseError as e:
                # 单条坏消息不应终止服务：回 -32700 后继续读下一条
                self._error(None, -32700, f"Parse error: {e}")
                continue
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
                            "\n\natb_video_storyboard 可把整段视频压成一张分镜宫格图，"
                            "token 成本远低于逐帧；适合先建立全片概览，再对关键段用 atb_read_video 细看，"
                            "或用 atb_video_zoom 放大某个时间点的局部细节。"
                            "若视频旁有同名 .srt/.vtt 字幕，分镜会自动对齐台词。"
                            "\n\n可选：atb_query_quota 查询 AI API 余额（需用户自行配置密钥，未配置时按指引说明即可）。"
                            "\n\n上下文将满或需要交接时，可用 atb_compress_history 把长对话压成"
                            "可续接摘要（确定性抽取，不调用模型）。"
                            "\n\n实验性：atb_computer_use 可操作 Windows 桌面应用（仅 Windows，"
                            "不保证兼容）。有副作用的操作先征得用户同意，失败不要反复盲试。"
                            "\n\n【行为约定】禁止闷头干：动手前先说明要做什么；每完成一个小步骤，"
                            "用一句话给出结论；每完成一个大阶段，用一段话总结结果、遗留问题与下一步。"
                            "不确定时先向用户提问，不要臆测缺失信息。"
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
        if name == "atb_video_storyboard":
            return _storyboard_payload(args)
        if name == "atb_video_zoom":
            return _zoom_payload(args)
        if name == "atb_query_quota":
            return _quota_payload(args)
        if name == "atb_compress_history":
            return _history_payload(args)
        if name == "atb_computer_use":
            return _computer_payload(args)
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
            paths = [p for p in (args.get("paths") or []) if isinstance(p, str)]
            opts = ReadOptions(max_chars=_max_chars_arg(args))
            limit = 200
            out = []
            for p in paths[:limit]:
                doc = dispatch.read_file(p, opts, safe=True)
                out.append({
                    "path": p,
                    "ok": doc.ok,
                    "format": doc.format,
                    "error": doc.error,
                    "text": doc.text(opts.max_chars) if doc.ok else "",
                })
            if len(paths) > limit:
                out.append({
                    "path": None,
                    "ok": False,
                    "skipped": True,
                    "error": {
                        "code": "ATB-E00004",
                        "title": f"批量上限 {limit} 条",
                        "detail": f"本次传入 {len(paths)} 条，最后 "
                                  f"{len(paths) - limit} 条未读取；请分批调用",
                    },
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


def _storyboard_payload(args: dict):
    """返回 MCP content：分镜摘要 + 台词 + 宫格图。"""
    opts = ReadOptions(
        storyboard=True,
        cells=args.get("cells"),
        sheets=int(args.get("sheets") or 1),
        storyboard_budget=int(args.get("budget") or 1_200_000),
        start=args.get("start"),
        end=args.get("end"),
        subtitles=args.get("subs"),
    )
    doc = dispatch.read_file(args["path"], opts)
    if doc.format != "video":
        return (f"该文件被识别为 `{doc.format}`，不是视频。请改用 atb_read_file。")

    sb = doc.metadata.get("storyboard") or {}
    lines = ["# 视频分镜", ""]
    lines.append(f"- 时长：{doc.metadata.get('duration')}s；分辨率："
                 f"{doc.metadata.get('width')}×{doc.metadata.get('height')}；"
                 f"格数：{sb.get('cells')}；切点：{sb.get('cuts')}；图：{len(sb.get('sheets', []))} 张")
    lines.append(f"- 单格尺寸：{sb.get('cell_size', ['-', '-'])[0]}×"
                 f"{sb.get('cell_size', ['-', '-'])[1]}；"
                 f"图片 token 估算：约 {sb.get('tokens_estimate')}")
    timings = sb.get("timings") or {}
    if timings:
        lines.append(f"- 耗时：{timings.get('total')}s"
                     f"（切点 {timings.get('cuts')}s / 取帧 {timings.get('grab')}s）")
    if sb.get("has_subtitles"):
        lines.append("- 已对齐字幕台词（见各格 lines）")
    lines.append("")
    lines.append("## 格子时间轴")
    for c in (sb.get("cell_table") or [])[:64]:
        row = f"- 格 {c['index']}：{fmt_time(c['start'])}–{fmt_time(c['end'])}"
        if c.get("lines"):
            row += "｜" + " / ".join(c["lines"][:2])
            if len(c["lines"]) > 2:
                row += f"（+{len(c['lines']) - 2} 行）"
        lines.append(row)
    if doc.warnings:
        lines.append("")
        lines.append("## 提示")
        lines.extend(f"- {w}" for w in doc.warnings)
    lines.append("")
    lines.append("## 使用要求")
    lines.append("1. 具备原生图像输入：先看宫格图建立全片概览，再按编号/时间码追问。")
    lines.append("2. 需要细节时，用 atb_read_video 的 start/end 对关键段加密取帧。")
    lines.append("3. 不具备图像输入：直接告诉用户无法查看画面，不要臆测内容。")

    content = [{"type": "text", "text": "\n".join(lines)}]
    for fp in sb.get("sheets", []):
        try:
            with open(fp, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        content.append({
            "type": "image",
            "data": base64.b64encode(data).decode("ascii"),
            "mimeType": "image/jpeg",
        })
    return content


def _zoom_payload(args: dict):
    """返回一张全分辨率局部画面。"""
    box = args.get("box")
    if box is not None:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise AtbError("ATB-E10002", "box 需为 [x, y, w, h] 四个 0-1 数字")
        box = [float(v) for v in box]
    result = export_zoom(args["path"], float(args["time"]), box,
                         int(args.get("width") or 1600))
    lines = [
        "# 视频局部画面",
        "",
        f"- 时刻：{result['time']}s",
        f"- 源尺寸：{result['source_size'][0]}×{result['source_size'][1]}；"
        f"区域：{result['box']}",
        f"- 输出：{result['width']}×{result['height']}；"
        f"{result['bytes'] / 1024:.0f} KB；约 {result['tokens_estimate']} tokens",
        f"- 文件：{result['path']}",
    ]
    content = [{"type": "text", "text": "\n".join(lines)}]
    try:
        with open(result["path"], "rb") as fh:
            data = fh.read()
        content.append({
            "type": "image",
            "data": base64.b64encode(data).decode("ascii"),
            "mimeType": "image/jpeg",
        })
    except OSError:
        pass
    return content


def _quota_payload(args: dict):
    provider = args.get("provider")
    if provider and provider not in quota.PROVIDERS:
        return (f"未知提供商 `{provider}`；可选：{', '.join(quota.PROVIDERS)}。"
                f"如需配置密钥，请在用户本机运行 `atb quota --set {provider}=sk-...`。")
    results = quota.query_all([provider] if provider else None)
    lines = ["# API 额度", ""]
    lines.append("| 提供商 | 余额 | 货币 | 来源 | 状态 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in results:
        status = "OK" if r.get("ok") else (r.get("error") or "未配置")
        lines.append(f"| {r.get('label', r['provider'])} | "
                     f"{r.get('balance', '-') if r.get('ok') else '-'} | "
                     f"{r.get('currency', '-') if r.get('ok') else '-'} | "
                     f"{r.get('source') or '-'} | {status} |")
    if not any(r.get("configured") for r in results):
        lines += [
            "",
            "## 如何配置（可选）",
            f"- 环境变量：{', '.join(p['env'] for p in quota.PROVIDERS.values())}",
            f"- 配置文件：{quota.CONFIG_PATH}",
            "- 或运行 `atb quota --init` 生成模板，再填入 api_key",
            "- 也可 `atb quota --set deepseek=sk-...` 直接写入",
        ]
    return "\n".join(lines)


def _history_payload(args: dict):
    result = history.compress_file(
        args["path"],
        target_tokens=int(args.get("target_tokens") or 6000),
        keep_recent=int(args.get("keep_recent") or 8),
    )
    markdown = result["markdown"]
    limit = int(args.get("max_chars") or 40000)
    if len(markdown) > limit:
        markdown = markdown[:limit] + f"\n\n…（已按 max_chars={limit} 截断）"
    stats = {k: v for k, v in result.items() if k != "markdown"}
    return (f"```json\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n```\n\n" + markdown)


_COMPUTER_KEYS = ("app", "x", "y", "x1", "y1", "x2", "y2", "element_id", "text",
                  "keys", "value", "direction", "amount", "depth", "max_nodes",
                  "screenshot", "limit", "wait", "args")


def _computer_payload(args: dict):
    from .experimental import computer

    action = str(args.get("action") or "").strip()
    if not action:
        raise AtbError("ATB-E10002", "缺少 action")
    params = {k: args.get(k) for k in _COMPUTER_KEYS if args.get(k) is not None}
    result = computer.dispatch(action, params)
    if action == "list" and isinstance(result.get("apps"), list):
        result = dict(result)
        result["apps"] = result["apps"][:int(args.get("limit") or 50)]
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _opts_from(args: dict) -> ReadOptions:
    return ReadOptions(
        max_chars=_max_chars_arg(args),
        pages=args.get("pages"),
        sheet=args.get("sheet"),
        member=args.get("member"),
        password=args.get("password"),
        encoding=args.get("encoding"),
        ocr=bool(args.get("ocr")),
    )


def _max_chars_arg(args: dict) -> int:
    """None → 默认上限；显式 0 → 不限制（与 CLI 语义一致）。"""
    value = args.get("max_chars")
    if value is None:
        return config.DEFAULT_MAX_CHARS
    return max(0, int(value))


def _search(args: dict) -> str:
    path = args["path"]
    query = args["query"]
    context = int(args.get("context", 80))
    max_hits = int(args.get("max_hits", 20))
    text = dispatch.extract_text(path)
    hits = []
    total = 0
    start = 0
    while True:
        idx = text.find(query, start)
        if idx < 0:
            break
        total += 1
        if len(hits) < max_hits:
            hits.append({
                "offset": idx,
                "match": text[idx: idx + len(query)],
                "before": text[max(0, idx - context): idx],
                "after": text[idx + len(query): idx + len(query) + context],
            })
        start = idx + len(query)
    return json.dumps({"path": path, "query": query, "hits": hits,
                       "total_hits": total, "returned": len(hits),
                       "truncated": total > len(hits)},
                      ensure_ascii=False, indent=2)


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
