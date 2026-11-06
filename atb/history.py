# -*- coding: utf-8 -*-
"""历史对话压缩：把长对话压成可续接的结构化摘要。

不调用任何模型，纯确定性处理：
  1. 解析 json / jsonl / markdown / 纯文本 四种对话存档；
  2. 保留系统提示与最近若干轮原文，早期轮次压成单行；
  3. 提取目标、决策、文件、命令、错误码等关键信息；
  4. 按目标 token 预算反复裁剪，输出可直接喂给下一个 Agent 的 Markdown。
"""

import json
import os
import re
from typing import Optional

from .errors import AtbError

STRONG_WORDS = (
    "决定", "确定", "采用", "改为", "不要", "必须", "禁止", "需求", "目标",
    "结论", "方案", "注意", "风险", "优先", "已完成", "已修复", "待办",
    "下一步", "TODO", "FIXME",
)
ROLE_ALIASES = {
    "user": "user", "human": "user", "用户": "user", "我": "user",
    "assistant": "assistant", "ai": "assistant", "model": "assistant",
    "gpt": "assistant", "助手": "assistant", "你": "assistant",
    "system": "system", "系统": "system", "developer": "system",
    "tool": "tool", "function": "tool", "工具": "tool", "工具结果": "tool",
}
_TRANSCRIPT_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(user|human|assistant|ai|model|system|"
    r"developer|tool|function|用户|助手|系统|工具)\s*(?:\*\*)?\s*[:：]\s*(.*)$",
    re.IGNORECASE)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s\"'<>|，。；）)]+|/(?:[\w.-]+/)+[\w.-]+)")
_ERR_RE = re.compile(r"ATB-E\d{5}")
_URL_RE = re.compile(r"https?://[^\s\"'<>）)]+")
_CMD_RE = re.compile(r"^\s*(?:atb |python main\.py|pip install|git |npm |npx |uv )", re.I)


# ── token 估算 ──────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 约 1 字/token，其余约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff"
              or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af")
    return max(1, int(cjk + (len(text) - cjk) / 4.0))


# ── 读取与解析 ──────────────────────────────────────────────
def _read_text(path: str) -> str:
    if not os.path.exists(path):
        raise AtbError("ATB-E01001", f"文件不存在：{path}")
    if os.path.isdir(path):
        raise AtbError("ATB-E01004", f"这是目录：{path}")
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _flatten(content) -> str:
    """把各种 content 结构压平成文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return _flatten(content["content"])
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = str(item.get("type") or "").lower()
                if kind in ("text", "input_text", "output_text"):
                    parts.append(str(item.get("text") or ""))
                elif kind in ("image", "image_url", "input_image"):
                    parts.append("[图片]")
                elif kind in ("tool_use", "function_call"):
                    name = item.get("name") or item.get("function", {}).get("name")
                    args = item.get("arguments") or item.get("input") or ""
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    parts.append(f"[调用 {name or '工具'}] {args[:200]}")
                elif kind in ("tool_result", "function_result"):
                    body = _flatten(item.get("content") or item.get("result") or "")
                    parts.append(f"[工具结果] {body[:300]}")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _coerce_message(obj: dict) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("message"), dict):
        obj = obj["message"]
    role = str(obj.get("role") or obj.get("from") or obj.get("speaker") or "").strip().lower()
    role = ROLE_ALIASES.get(role, role or "user")
    text = _flatten(obj.get("content", obj.get("text", obj.get("body", ""))))
    calls = obj.get("tool_calls") or obj.get("function_call")
    if calls:
        if isinstance(calls, list):
            for c in calls:
                name = c.get("function", {}).get("name") if isinstance(c, dict) else ""
                text += f"\n[调用 {name or '工具'}]"
        else:
            text += f"\n[调用 {calls.get('name', '工具')}]"
    if not text.strip():
        return None
    return {"role": role, "content": text.strip()}


def _from_json_text(text: str, fmt: str) -> list:
    messages = []
    if fmt == "jsonl":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        for ln in lines:
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, list):
                messages.extend(m for m in (_coerce_message(x) for x in obj) if m)
            else:
                msg = _coerce_message(obj)
                if msg:
                    messages.append(msg)
        if messages:
            return messages
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, dict):
        for key in ("messages", "conversation", "history", "chat", "dialog"):
            if isinstance(obj.get(key), list):
                obj = obj[key]
                break
        else:
            obj = [obj]
    if isinstance(obj, list):
        for item in obj:
            msg = _coerce_message(item) if isinstance(item, dict) else None
            if msg:
                messages.append(msg)
    return messages


def _from_transcript(text: str) -> list:
    messages = []
    role = None
    buf = []
    for line in text.splitlines():
        m = _TRANSCRIPT_RE.match(line)
        if m:
            if role and buf:
                messages.append({"role": role, "content": "\n".join(buf).strip()})
            role = ROLE_ALIASES.get(m.group(1).lower(), "user")
            buf = [m.group(2)] if m.group(2) else []
        elif role:
            buf.append(line)
        elif line.strip():
            if line.lstrip().startswith("#") or line.strip() in ("---", "***"):
                continue  # 文档标题 / 分隔线，不是对话
            role = "user"
            buf = [line]
    if role and buf:
        messages.append({"role": role, "content": "\n".join(buf).strip()})
    return [m for m in messages if m["content"]]


def load_messages(path: str) -> tuple:
    """返回 (messages, 实际格式)。"""
    text = _read_text(path)
    ext = os.path.splitext(path)[1].lower()
    fmt = "jsonl" if ext in (".jsonl", ".ndjson") else "json" if ext == ".json" else "transcript"
    messages = _from_json_text(text, fmt) if fmt in ("json", "jsonl") else []
    if not messages:
        messages = _from_json_text(text, "jsonl")
    if not messages:
        messages = _from_transcript(text)
        fmt = "transcript"
    if not messages:
        raise AtbError("ATB-E14001",
                       "没解析出任何对话消息；支持 json / jsonl / markdown / 纯文本对话记录，"
                       "消息需包含 role 与 content（或 '用户：' / 'User:' 形式）")
    return messages, fmt


# ── 压缩 ────────────────────────────────────────────────────
def _first_sentence(text: str, limit: int = 90) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"[。！？.!?\n]", flat)
    cut = flat[:m.end()] if m and m.end() <= limit else flat[:limit]
    return cut + ("…" if len(flat) > len(cut) else "")


def _dedupe(messages: list) -> tuple:
    out, dropped = [], 0
    last = None
    for msg in messages:
        key = (msg["role"], re.sub(r"\s+", " ", msg["content"])[:200])
        if key == last:
            dropped += 1
            continue
        last = key
        out.append(msg)
    return out, dropped


def _collect(kind: str, messages: list, limit: int = 12) -> list:
    seen, out = set(), []
    for msg in messages:
        content = msg["content"]
        tickets = []
        if kind == "decisions":
            for line in content.splitlines():
                if any(w in line for w in STRONG_WORDS):
                    tickets.append(_first_sentence(line, 120))
        elif kind == "paths":
            tickets = _PATH_RE.findall(content)
        elif kind == "errors":
            tickets = _ERR_RE.findall(content)
        elif kind == "urls":
            tickets = _URL_RE.findall(content)
        elif kind == "commands":
            tickets = [ln.strip() for ln in content.splitlines() if _CMD_RE.match(ln)]
        for t in tickets:
            t = t.strip()
            key = re.sub(r"\d+", "#", t)[:80] if kind == "decisions" else t
            if t and key not in seen:
                seen.add(key)
                out.append(t)
                if len(out) >= limit:
                    return out
    return out


def _summary_line(msg: dict, limit: int = 90) -> str:
    role = {"user": "用户", "assistant": "助手", "system": "系统",
            "tool": "工具"}.get(msg["role"], msg["role"])
    calls = re.findall(r"\[调用 ([^\]]+)\]", msg["content"])
    suffix = f"（调用：{', '.join(calls[:3])}）" if calls else ""
    return f"- [{role}] {_first_sentence(msg['content'], limit)}{suffix}"


def _render(messages: list, older: list, recent: list, systems: list,
            keep_recent: int, recent_chars: int, older_limit: int,
            target_tokens: int, meta: dict, dropped: int) -> str:
    lines = []
    if systems:
        lines.append("## 系统提示（保留）")
        for sysmsg in systems:
            body = sysmsg["content"]
            lines.append(body[:500] + ("…" if len(body) > 500 else ""))
        lines.append("")

    decisions = _collect("decisions", [m for m in messages if m["role"] != "system"], 10)
    if decisions:
        lines.append("## 一、目标 / 决策 / 约束")
        lines.extend(f"- {d}" for d in decisions)
        lines.append("")

    assets = []
    for label, kind in (("文件", "paths"), ("命令", "commands"),
                        ("错误码", "errors"), ("链接", "urls")):
        items = _collect(kind, messages, 8)
        if items:
            assets.append(f"- **{label}**：" + "；".join(f"`{i}`" for i in items[:6]))
    if assets:
        lines.append("## 二、关键文件 / 命令 / 错误")
        lines.extend(assets)
        lines.append("")

    if older:
        lines.append(f"## 三、早期对话摘要（{max(0, len(messages) - keep_recent)} 条）")
        lines.extend(_summary_line(m) for m in older[-older_limit:])
        if len(older) > older_limit:
            lines.append(f"- …另有 {len(older) - older_limit} 条更早消息已省略")
        lines.append("")

    lines.append(f"## 四、最近对话（{len(recent)} 条，已截断）")
    for i, msg in enumerate(recent, 1):
        role = {"user": "用户", "assistant": "助手", "system": "系统",
                "tool": "工具"}.get(msg["role"], msg["role"])
        body = msg["content"].strip()
        if len(body) > recent_chars:
            body = body[:recent_chars] + f"…（截断，原 {len(msg['content'])} 字）"
        lines.append(f"### {i}. {role}")
        lines.append(body)
        lines.append("")
    lines.append("## 五、续接提示")
    lines.append("- 这是压缩后的上下文，不是完整记录；理解冲突时以用户最新指令为准。")
    lines.append("- 不确定的细节先向用户确认，不要臆测缺失内容。")
    lines.append("- 每完成一个小步骤给一句话结论，每个大阶段给一段总结。")
    lines.append("")

    body = "\n".join(lines)
    header_tpl = [
        "# 历史对话压缩包", "",
        f"- 来源：`{meta['source']}`（{meta['format']}）",
        f"- 消息：{meta['count']} 条"
        + (f"，去重 {dropped} 条" if dropped else ""),
        "- 原始约 {original} tokens → 本摘要约 {tokens} tokens（预算 {budget}）",
        "- 压缩方式：确定性抽取（未调用模型），因此摘要不是语义改写",
        "",
    ]
    header = "\n".join(header_tpl)
    header = header.replace("{original}", str(meta["original_tokens"])) \
                   .replace("{budget}", str(target_tokens))
    estimate = estimate_tokens(header.replace("{tokens}", "0") + body)
    header = header.replace("{tokens}", str(estimate))
    return header + body


def compress(messages: list, source: str = "<memory>", fmt: str = "memory",
             target_tokens: int = 6000, keep_recent: int = 8) -> dict:
    messages, dropped = _dedupe(messages)
    original = "\n".join(m["content"] for m in messages)
    original_tokens = estimate_tokens(original)
    keep_recent = max(1, min(int(keep_recent or 8), len(messages)))

    systems = [m for m in messages if m["role"] == "system"][:2]
    body = [m for m in messages if m["role"] != "system"]
    if not body:
        body = messages
    recent = body[-keep_recent:]
    older = body[:-keep_recent]

    meta = {"source": source, "format": fmt, "count": len(messages),
            "original_tokens": original_tokens}

    recent_chars, older_limit, budget = 700, 60, max(600, int(target_tokens or 6000))
    markdown = ""
    for _ in range(8):
        markdown = _render(messages, older, recent, systems, keep_recent,
                           recent_chars, older_limit, budget, meta, dropped)
        tokens = estimate_tokens(markdown)
        if tokens <= budget:
            break
        if recent_chars > 200:
            recent_chars = max(200, int(recent_chars * 0.65))
        elif older_limit > 8:
            older_limit = max(8, int(older_limit * 0.6))
        elif len(recent) > 2:
            older.insert(0, recent.pop(0))
        else:
            break

    compressed_tokens = estimate_tokens(markdown)
    notes = []
    if compressed_tokens > budget:
        notes.append("目标预算过小，已压到当前下限")
    if compressed_tokens >= original_tokens:
        notes.append("原文较短：产物用于结构化交接，篇幅可能不小于原文")
    return {
        "ok": True,
        "source": source,
        "format": fmt,
        "messages": len(messages),
        "deduped": dropped,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "ratio": round(compressed_tokens / original_tokens, 3) if original_tokens else 0.0,
        "target_tokens": budget,
        "within_budget": compressed_tokens <= budget,
        "notes": notes,
        "markdown": markdown,
    }


def compress_file(path: str, target_tokens: int = 6000,
                  keep_recent: int = 8) -> dict:
    messages, fmt = load_messages(path)
    result = compress(messages, source=os.path.abspath(path), fmt=fmt,
                      target_tokens=target_tokens, keep_recent=keep_recent)
    return result
