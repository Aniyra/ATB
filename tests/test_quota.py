# -*- coding: utf-8 -*-
"""额度查询：配置读写、密钥脱敏、响应解析（不访问网络）。"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb import quota  # noqa: E402


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "config.json")
        self.patches = [
            mock.patch.object(quota, "CONFIG_PATH", self.cfg),
            mock.patch.object(quota, "CONFIG_DIR", self.tmp.name),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_set_get_remove(self):
        quota.set_key("deepseek", "sk-test-1234567890")
        with open(self.cfg, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["quota"]["deepseek"]["api_key"], "sk-test-1234567890")
        key, source = quota.get_key("deepseek")
        self.assertEqual((key, source), ("sk-test-1234567890", "config"))

        quota.remove_key("deepseek")
        key, source = quota.get_key("deepseek")
        self.assertFalse(key)
        self.assertIsNone(source)

    def test_env_fallback(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=False):
            key, source = quota.get_key("deepseek")
        self.assertEqual((key, source), ("sk-env", "env"))

    def test_mask(self):
        self.assertEqual(quota.mask(""), "-")
        self.assertEqual(quota.mask("short"), "sh***")
        self.assertEqual(quota.mask("sk-1234567890abcdef"), "sk-123...cdef")

    def test_unconfigured_query(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            r = quota.query("deepseek")
        self.assertFalse(r["ok"])
        self.assertFalse(r["configured"])
        self.assertIn("未配置密钥", r["error"])

    def test_parsers(self):
        self.assertEqual(quota._parse_deepseek(
            {"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "12.34",
                 "granted_balance": "0.00", "topped_up_balance": "12.34"}]})[0], "12.34")
        self.assertEqual(quota._parse_moonshot(
            {"data": {"available_balance": 9.5}})[0], "9.5")
        self.assertEqual(quota._parse_siliconflow(
            {"data": {"totalBalance": "8.88"}})[0], "8.88")
        balance, currency, _ = quota._parse_openrouter(
            {"data": {"total_credits": 10, "total_usage": 3.5}})
        self.assertEqual((balance, currency), ("6.50", "USD"))

    def test_format_rows(self):
        header, rows = quota.format_rows([
            {"provider": "deepseek", "label": "DeepSeek", "ok": True,
             "balance": "1.00", "currency": "CNY", "source": "env"},
            {"provider": "moonshot", "label": "Moonshot", "ok": False,
             "configured": False, "error": "未配置密钥"},
        ])
        self.assertEqual(header[0], "提供商")
        self.assertEqual(rows[0][1], "1.00")
        self.assertEqual(rows[1][1], "-")
        self.assertIn("未配置", rows[1][4])


if __name__ == "__main__":
    unittest.main()
