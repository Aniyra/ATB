# -*- coding: utf-8 -*-
"""ATB 错误码体系。

编号格式：ATB-E<2 位类别><3 位流水>，例如 ATB-E01003。
每个错误码都带有「成因 + 分步解决方案」，CLI 与 MCP 输出都会带上，
便于 Agent 自行决策下一步。
"""

import json
import os
from typing import Dict, Optional

# 类别: E00 通用 / E01 文件访问 / E02 格式识别 / E03 文本编码 /
#       E04 办公文档 / E05 PDF / E06 图片音视频 / E07 压缩包 /
#       E08 结构化数据 / E09 输出写入 / E10 MCP 协议 / E11 依赖环境
ERRORS: Dict[str, Dict[str, str]] = {
    "ATB-E00001": {
        "title": "未捕获异常",
        "solution": "这是兜底错误。请带上完整 traceback 与文件路径反馈；"
                    "可先运行 `{prog} doctor` 检查环境。",
    },
    "ATB-E00002": {
        "title": "用户中断",
        "solution": "操作已被手动取消，无需处理。",
    },
    "ATB-E00003": {
        "title": "参数无效",
        "solution": "检查命令参数拼写，运行 `{prog} --help` 或 `{prog} <子命令> --help`。",
    },
    "ATB-E00004": {
        "title": "文件过大",
        "solution": "该文件超过解析上限。可提高 --max-bytes，或先用 `{prog} info` 看元数据，"
                    "再对局部内容单独处理。",
    },
    "ATB-E01001": {
        "title": "路径不存在",
        "solution": "确认路径拼写、盘符与引号；Windows 路径含空格时需整体加引号。",
    },
    "ATB-E01002": {
        "title": "权限不足",
        "solution": "文件/目录被系统或其他进程保护。尝试以管理员身份运行，"
                    "或先把文件复制到可读目录再解析。",
    },
    "ATB-E01003": {
        "title": "文件被占用",
        "solution": "目标文件正被其他程序独占打开。关闭对应程序后重试。",
    },
    "ATB-E01004": {
        "title": "目标是目录而非文件",
        "solution": "读取文件请用 `{prog} read <文件>`；扫描目录请用 `{prog} scan <目录>`。",
    },
    "ATB-E02001": {
        "title": "无法识别文件格式",
        "solution": "扩展名缺失或伪造，且魔术字节未命中。可用 `--format <格式>` 显式指定，"
                    "或先运行 `{prog} info` 查看十六进制头。",
    },
    "ATB-E02002": {
        "title": "扩展名与实际内容不一致",
        "solution": "已按实际内容（魔术字节）选择解析器。若结果异常，用 `--format` 强制指定。",
    },
    "ATB-E03001": {
        "title": "文本解码失败",
        "solution": "编码探测未命中。用 `--encoding gbk|utf-8|gb18030|latin-1` 指定，"
                    "或先用 `{prog} info` 查看 BOM。",
    },
    "ATB-E03002": {
        "title": "文件疑似二进制",
        "solution": "该扩展名预期为文本但内容含大量不可打印字节。"
                    "可改用 `{prog} info` 或指定正确的 --format。",
    },
    "ATB-E04001": {
        "title": "办公文档解析失败",
        "solution": "文件可能损坏或为加密文档。尝试用 Office/WPS 另存为 docx/xlsx/pptx 后重试，"
                    "或确认未设置打开密码。",
    },
    "ATB-E04002": {
        "title": "旧版 Office 格式仅支持浅层提取",
        "solution": "doc/xls/ppt（OLE 复合文档）仅能提取可见字符串。"
                    "建议先转换为 docx/xlsx/pptx 以获得结构化内容。",
    },
    "ATB-E05001": {
        "title": "PDF 解析失败",
        "solution": "文件可能加密或结构损坏。若设置了打开密码，用 --password 提供；"
                    "也可先用 `{prog} info` 确认是否为真实 PDF。",
    },
    "ATB-E05002": {
        "title": "PDF 无文本层",
        "solution": "扫描件/图片型 PDF 需要 OCR。安装 Tesseract 并加入 PATH 后使用 --ocr，"
                    "或改用视觉模型处理页面截图。",
    },
    "ATB-E06001": {
        "title": "图片解析失败",
        "solution": "格式扩展名可能与实际不符（如 WEBP 改名 png）。用 `{prog} info` 查看魔术字节。"
                    "HEIC 需要 pillow-heif：pip install pillow-heif。",
    },
    "ATB-E06002": {
        "title": "音视频元数据不可读",
        "solution": "部分容器需要 ffprobe。安装 ffmpeg 并加入 PATH 后可获得时长/码率/流信息；"
                    "否则仅返回文件级信息。",
    },
    "ATB-E07001": {
        "title": "压缩包解析失败",
        "solution": "确认压缩包未损坏、未加密。RAR 解压需系统 unrar/7z；"
                    "7z 需 py7zr。列目录失败时可尝试 `{prog} info`。",
    },
    "ATB-E07002": {
        "title": "压缩包已加密",
        "solution": "用 --password 提供密码；RAR 需系统已安装 unrar 才能解压。",
    },
    "ATB-E07003": {
        "title": "归档条目越界（疑似 Zip Slip）",
        "solution": "归档内含绝对路径或 ../ 跳转条目，已拒绝解压以保护磁盘安全。",
    },
    "ATB-E08001": {
        "title": "结构化数据解析失败",
        "solution": "JSON/YAML/TOML/CSV 语法错误。检查文件是否被截断，"
                    "或用 `{prog} read --format text` 按纯文本查看原始内容。",
    },
    "ATB-E08002": {
        "title": "SQLite 数据库不可读",
        "solution": "文件可能不是 SQLite（检查头部是否为 'SQLite format 3'），"
                    "或已被加密（SQLCipher）。",
    },
    "ATB-E09001": {
        "title": "输出写入失败",
        "solution": "检查 --out 目标目录是否存在、是否有写权限、磁盘是否已满。",
    },
    "ATB-E10001": {
        "title": "MCP 协议错误",
        "solution": "请求不是合法 JSON-RPC 2.0。检查 message 是否为单行 JSON，"
                    "且 Content-Length 分帧（如使用）设置正确。",
    },
    "ATB-E10002": {
        "title": "MCP 工具参数错误",
        "solution": "调用 tools/call 时 arguments 需为对象，path 必填且为绝对路径。",
    },
    "ATB-E11001": {
        "title": "缺少可选依赖",
        "solution": "该格式的高级解析需要额外依赖。运行 `{prog} doctor` 查看缺失项，"
                    "或 pip install -r requirements.txt。",
    },
    "ATB-E12001": {
        "title": "额度查询失败",
        "solution": "检查密钥是否有效、账户是否有余额查询权限、网络是否可达；"
                    "可用 `{prog} quota --show` 确认配置来源，或用 --provider 单独测试。",
    },
    "ATB-E13001": {
        "title": "电脑控制不可用",
        "solution": "该能力（实验性）仅支持 Windows。其他平台请使用系统自带自动化工具；"
                    "核心文件读取能力不受影响。",
    },
    "ATB-E13002": {
        "title": "未找到目标窗口或应用",
        "solution": "先用 `{prog} cu list` 查看可用的 app_id / 窗口标题 / 进程名，"
                    "再把这些值传给 app 参数；启动应用可用 `{prog} cu launch <exe>`。",
    },
    "ATB-E13003": {
        "title": "UI 元素定位不可用或已失效",
        "solution": "按 element_id 操作需要可选依赖 uiautomation（pip install uiautomation）；"
                    "未安装时请改用屏幕坐标 x/y，或重新获取应用状态后再操作。",
    },
    "ATB-E13004": {
        "title": "电脑操作执行失败",
        "solution": "确认目标窗口未最小化、未被其他窗口遮挡、当前权限足够；"
                    "必要时用管理员身份运行，或改用截图 + 坐标点击。",
    },
    "ATB-E14001": {
        "title": "历史对话解析失败",
        "solution": "支持的格式：json（含 messages 数组）、jsonl（每行一条消息）、"
                    "markdown / 纯文本（'用户：' / 'User:' 开头）。"
                    "请确认文件包含 role 与 content，或先用 `{prog} read` 查看结构。",
    },
}

# 需要 prog 占位符替换的错误码，统一在格式化时处理
_CATEGORY_NAMES = {
    "00": "通用",
    "01": "文件访问",
    "02": "格式识别",
    "03": "文本编码",
    "04": "办公文档",
    "05": "PDF",
    "06": "图片音视频",
    "07": "压缩包",
    "08": "结构化数据",
    "09": "输出写入",
    "10": "MCP 协议",
    "11": "依赖环境",
    "12": "额度服务",
    "13": "电脑控制",
    "14": "上下文压缩",
}


class AtbError(Exception):
    """ATB 业务异常：携带错误码、成因与解决方案。"""

    def __init__(self, code: str, detail: str = "", *, hint: str = ""):
        self.code = code
        self.detail = detail
        self.hint = hint
        info = ERRORS.get(code, {"title": "未知错误", "solution": "暂无解决方案。"})
        self.title = info["title"]
        self.solution = info["solution"]
        super().__init__(f"[{code}] {self.title}" + (f": {detail}" if detail else ""))

    @property
    def category(self) -> str:
        return _CATEGORY_NAMES.get(self.code[5:7], "未知")

    def to_dict(self, prog: str = "python main.py") -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "category": self.category,
            "detail": self.detail,
            "solution": self.solution.replace("{prog}", prog),
            "hint": self.hint,
        }


def fail(code: str, detail: str = "", *, hint: str = "") -> "AtbError":
    """抛出 AtbError 的简写。"""
    raise AtbError(code, detail, hint=hint)


def normalize_code(raw: str) -> Optional[str]:
    """把 '1003' / 'E1003' / 'atb-e01003' 归一化为 'ATB-E01003'。"""
    s = str(raw).strip().upper().replace("ATB-", "").replace("E", "", 1)
    if not s.isdigit():
        return None
    if len(s) == 4:
        s = "0" + s          # 1003 -> 01003 / 5002 -> 05002
    elif len(s) != 5:
        return None
    code = f"ATB-E{s}"
    return code if code in ERRORS else None


def lookup(raw: str, prog: str = "python main.py") -> Optional[dict]:
    """按编号或关键词查询错误码。"""
    key = str(raw).strip()
    code = normalize_code(key)
    if code:
        info = ERRORS[code]
        return {
            "code": code,
            "title": info["title"],
            "category": _CATEGORY_NAMES.get(code[5:7], "未知"),
            "solution": info["solution"].replace("{prog}", prog),
        }
    hits = []
    for c, info in ERRORS.items():
        if key and (key in info["title"] or key in info["solution"]):
            hits.append({
                "code": c,
                "title": info["title"],
                "category": _CATEGORY_NAMES.get(c[5:7], "未知"),
                "solution": info["solution"].replace("{prog}", prog),
            })
    return hits[0] if hits else None


def search_errors(keyword: str, prog: str = "python main.py") -> list:
    """按关键词搜索错误码（标题 / 解决方案 / 编号），返回全部命中。"""
    key = str(keyword).strip().lower()
    if not key:
        return []
    hits = []
    for code, info in ERRORS.items():
        if key in code.lower() or key in info["title"].lower() or key in info["solution"].lower():
            hits.append(_entry(code, prog))
    return hits


def _entry(code: str, prog: str = "python main.py") -> dict:
    info = ERRORS[code]
    return {
        "code": code,
        "category_id": code[5:7],
        "category": _CATEGORY_NAMES.get(code[5:7], "未知"),
        "title": info["title"],
        "solution": info["solution"].replace("{prog}", prog),
    }


def list_categories() -> dict:
    """按类别分组：{ '01': {'name': '文件访问', 'codes': [...]}, ... }"""
    out = {}
    for code in ERRORS:
        cat = code[5:7]
        out.setdefault(cat, {"name": _CATEGORY_NAMES.get(cat, "未知"), "codes": []})
        out[cat]["codes"].append(code)
    return dict(sorted(out.items()))


def match_category(keyword: str) -> Optional[str]:
    """把 '01' / '1' / '文件访问' / 'pdf' 匹配到类别编号。"""
    key = str(keyword).strip().lower()
    if not key:
        return None
    if key.isdigit():
        key = key.zfill(2)
        return key if key in list_categories() else None
    for cat, info in list_categories().items():
        if key == info["name"].lower() or key in info["name"].lower():
            return cat
    return None


def all_errors(prog: str = "python main.py") -> list:
    return [_entry(code, prog) for code in ERRORS]


def _log_path() -> str:
    from config import REPORT_DIR

    os.makedirs(REPORT_DIR, exist_ok=True)
    return os.path.join(REPORT_DIR, "atb_error_log.json")


def log_error(err: AtbError, context: dict = None) -> None:
    """把最近错误写入 reports/atb_error_log.json（最多保留 50 条）。"""
    try:
        path = _log_path()
        records = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f) or []
        import datetime

        records.append({
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": err.code,
            "title": err.title,
            "detail": err.detail,
            "context": context or {},
        })
        records = records[-50:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
