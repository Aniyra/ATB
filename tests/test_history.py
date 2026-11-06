# -*- coding: utf-8 -*-
"""历史对话压缩测试：解析、去重、预算裁剪与 MCP 回传。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb import history  # noqa: E402


class HistoryParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_jsonl_roles(self):
        path = self._write("a.jsonl", "\n".join([
            json.dumps({"role": "system", "content": "系统提示"}, ensure_ascii=False),
            json.dumps({"role": "user", "content": "你好"}, ensure_ascii=False),
            json.dumps({"role": "assistant", "content": "收到"}, ensure_ascii=False),
        ]))
        messages, fmt = history.load_messages(path)
        self.assertEqual(fmt, "jsonl")
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "assistant"])

    def test_json_content_parts(self):
        payload = {"messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "x"}},
            ]},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search"}}]},
        ]}
        path = self._write("b.json", json.dumps(payload, ensure_ascii=False))
        messages, fmt = history.load_messages(path)
        self.assertEqual(fmt, "json")
        self.assertIn("看图", messages[0]["content"])
        self.assertIn("[图片]", messages[0]["content"])
        self.assertIn("search", messages[1]["content"])

    def test_transcript(self):
        path = self._write("c.md", "# 对话\n\n用户：先做 A\n助手：好的\n\n"
                                     "User: then B\nAssistant: done\n")
        messages, fmt = history.load_messages(path)
        self.assertEqual(fmt, "transcript")
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("先做 A", messages[0]["content"])

    def test_empty_file_reports_error(self):
        path = self._write("d.txt", "   \n\n")
        with self.assertRaises(Exception) as ctx:
            history.load_messages(path)
        self.assertIn("ATB-E14001", str(getattr(ctx.exception, "code", "")))


class HistoryCompressTest(unittest.TestCase):
    def test_budget_and_key_facts(self):
        messages = [{"role": "system", "content": "你是助手，必须每小步总结。"}]
        for i in range(120):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({"role": role, "content": (
                f"第 {i} 轮：决定采用方案 {i % 3}，必须保留 ATB-E01001。"
                + "。" * 40)})
        result = history.compress(messages, "demo", "memory",
                                  target_tokens=1200, keep_recent=4)
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["compressed_tokens"], 1200)
        self.assertLess(result["compressed_tokens"], result["original_tokens"])
        self.assertIn("ATB-E01001", result["markdown"])
        self.assertIn("续接提示", result["markdown"])
        self.assertIn("系统提示", result["markdown"])

    def test_dedupe(self):
        messages = [{"role": "user", "content": "重复"}] * 5
        result = history.compress(messages, "demo", "memory",
                                  target_tokens=2000, keep_recent=2)
        self.assertEqual(result["messages"], 1)
        self.assertEqual(result["deduped"], 4)

    def test_compress_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for i in range(30):
                    f.write(json.dumps({"role": "user" if i % 2 == 0 else "assistant",
                                        "content": f"第 {i} 条 " + "内容" * 30},
                                       ensure_ascii=False) + "\n")
            result = history.compress_file(path, 1000, 4)
            self.assertEqual(result["format"], "jsonl")
            self.assertIn("历史对话压缩包", result["markdown"])
            self.assertTrue(result["within_budget"])


if __name__ == "__main__":
    unittest.main()
