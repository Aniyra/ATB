# -*- coding: utf-8 -*-
"""可选的 API 额度查询。

配置方式（任选其一，环境变量优先于配置文件）：

1. 环境变量：
     DEEPSEEK_API_KEY / MOONSHOT_API_KEY / SILICONFLOW_API_KEY / OPENROUTER_API_KEY

2. 配置文件 ~/.atb/config.json：
     {
       "quota": {
         "deepseek": {"api_key": "sk-..."},
         "moonshot": {"api_key": "sk-..."}
       }
     }

3. 一条命令写入配置：
     atb quota --set deepseek=sk-...

只查询、不在本地缓存余额；密钥不会出现在日志或错误信息里。
"""

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".atb")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

UA = "ATB/1.3 (+https://github.com/Aniyra/ATB)"


# ── 配置 ─────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> str:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return CONFIG_PATH


def set_key(provider: str, key: str) -> str:
    cfg = load_config()
    quota = cfg.setdefault("quota", {})
    item = quota.setdefault(provider, {})
    item["api_key"] = key
    return save_config(cfg)


def remove_key(provider: str) -> str:
    cfg = load_config()
    cfg.get("quota", {}).pop(provider, None)
    return save_config(cfg)


def get_key(provider: str) -> tuple:
    """返回 (密钥, 来源)，来源为 config / env / None。"""
    spec = PROVIDERS.get(provider) or {}
    key = ((load_config().get("quota") or {}).get(provider) or {}).get("api_key")
    if key:
        return key.strip(), "config"
    env = spec.get("env")
    if env and os.environ.get(env):
        return os.environ[env].strip(), "env"
    return "", None


def mask(key: str) -> str:
    if not key:
        return "-"
    if len(key) <= 10:
        return key[:2] + "***"
    return key[:6] + "..." + key[-4:]


# ── 提供商 ───────────────────────────────────────────────────
def _parse_deepseek(data: dict):
    infos = data.get("balance_infos") or []
    if not infos:
        return "-", "CNY", {"is_available": data.get("is_available")}
    b = infos[0]
    return str(b.get("total_balance", "-")), b.get("currency", "CNY"), {
        "granted": b.get("granted_balance"),
        "topped_up": b.get("topped_up_balance"),
        "available": data.get("is_available"),
    }


def _parse_moonshot(data: dict):
    b = data.get("data") or {}
    return str(b.get("available_balance", "-")), "CNY", {
        "voucher": b.get("voucher_balance"),
        "cash": b.get("cash_balance"),
    }


def _parse_siliconflow(data: dict):
    b = data.get("data") or {}
    return str(b.get("totalBalance", "-")), "CNY", {
        "charge": b.get("chargeBalance"),
        "balance": b.get("balance"),
    }


def _parse_openrouter(data: dict):
    b = data.get("data") or {}
    try:
        remain = float(b.get("total_credits") or 0) - float(b.get("total_usage") or 0)
        return f"{remain:.2f}", "USD", {
            "credits": b.get("total_credits"),
            "usage": b.get("total_usage"),
        }
    except (TypeError, ValueError):
        return "-", "USD", b


PROVIDERS: Dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "url": "https://api.deepseek.com/user/balance",
        "docs": "https://platform.deepseek.com/api_keys",
        "parse": _parse_deepseek,
    },
    "moonshot": {
        "label": "Moonshot",
        "env": "MOONSHOT_API_KEY",
        "url": "https://api.moonshot.cn/v1/users/me/balance",
        "docs": "https://platform.moonshot.cn/console/api-keys",
        "parse": _parse_moonshot,
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "env": "SILICONFLOW_API_KEY",
        "url": "https://api.siliconflow.cn/v1/user/info",
        "docs": "https://cloud.siliconflow.cn/account/ak",
        "parse": _parse_siliconflow,
    },
    "openrouter": {
        "label": "OpenRouter",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/credits",
        "docs": "https://openrouter.ai/settings/keys",
        "parse": _parse_openrouter,
    },
}


def _get_json(url: str, key: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def query(provider: str, timeout: int = 12) -> dict:
    """查询单个提供商；永不抛异常，失败信息放在 error 字段。"""
    spec = PROVIDERS.get(provider)
    if not spec:
        return {"provider": provider, "ok": False, "configured": False,
                "error": f"未知提供商；可选：{', '.join(PROVIDERS)}"}
    key, source = get_key(provider)
    base = {"provider": provider, "label": spec["label"], "source": source,
            "configured": bool(key), "docs": spec["docs"]}
    if not key:
        base.update({"ok": False, "error": f"未配置密钥（环境变量 {spec['env']} 或 ~/.atb/config.json）"})
        return base
    try:
        data = _get_json(spec["url"], key, timeout)
        balance, currency, detail = spec["parse"](data)
        base.update({"ok": True, "balance": balance, "currency": currency,
                     "detail": detail})
    except urllib.error.HTTPError as e:
        tip = "密钥无效或无权限" if e.code in (401, 403) else f"HTTP {e.code}"
        base.update({"ok": False, "error": tip})
    except urllib.error.URLError as e:
        base.update({"ok": False, "error": f"网络不可达：{e.reason}"})
    except TimeoutError:
        base.update({"ok": False, "error": f"请求超时（{timeout}s）"})
    except Exception as e:
        base.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return base


def query_all(providers: Optional[List[str]] = None, timeout: int = 12) -> List[dict]:
    names = providers or list(PROVIDERS.keys())
    return [query(name, timeout) for name in names]


def format_rows(results: List[dict]) -> tuple:
    header = ["提供商", "余额", "货币", "密钥来源", "状态"]
    rows = []
    for r in results:
        status = "OK" if r.get("ok") else (r.get("error") or "未配置")
        rows.append([
            r.get("label", r.get("provider", "")),
            r.get("balance", "-") if r.get("ok") else "-",
            r.get("currency", "-") if r.get("ok") else "-",
            r.get("source") or "-",
            status,
        ])
    return header, rows


def config_template() -> str:
    tpl = {"quota": {name: {"api_key": ""} for name in PROVIDERS}}
    return json.dumps(tpl, ensure_ascii=False, indent=2)
