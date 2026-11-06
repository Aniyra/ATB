# -*- coding: utf-8 -*-
"""MCP Server 协议测试（不依赖网络与第三方 MCP SDK）。"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.mcp import McpServer  # noqa: E402


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.txt = os.path.join(self.tmp.name, "a.txt")
        with open(self.txt, "w", encoding="utf-8") as f:
            f.write("hello atb\n第二行\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, messages, framing="line"):
        stdin = io.StringIO()
        for msg in messages:
            data = json.dumps(msg, ensure_ascii=False)
            if framing == "line":
                stdin.write(data + "\n")
            else:
                stdin.write(f"Content-Length: {len(data.encode('utf-8'))}\r\n\r\n" + data)
        stdin.seek(0)
        stdout = io.StringIO()
        McpServer(stdin=stdin, stdout=stdout).serve_forever()
        results = {}
        for line in stdout.getvalue().splitlines():
            if line.strip():
                obj = json.loads(line)
                results[obj.get("id")] = obj
        return results

    def _base(self):
        return [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]

    def test_initialize_and_tools(self):
        res = self._run(self._base())
        self.assertEqual(res[1]["result"]["serverInfo"]["name"], "atb")
        self.assertEqual(res[1]["result"]["protocolVersion"], "2024-11-05")
        names = {t["name"] for t in res[2]["result"]["tools"]}
        self.assertIn("atb_read_file", names)
        self.assertIn("atb_scan_directory", names)
        self.assertIn("atb_extract_text", names)
        self.assertIn("atb_video_zoom", names)
        self.assertIn("atb_query_quota", names)
        self.assertIn("atb_compress_history", names)
        self.assertIn("atb_computer_use", names)

    def test_tool_call_extract_text(self):
        msgs = self._base() + [
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "atb_extract_text", "arguments": {"path": self.txt}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "atb_read_file", "arguments": {"path": self.txt}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "atb_file_info", "arguments": {"path": self.txt}}},
        ]
        res = self._run(msgs)
        self.assertIn("hello atb", res[3]["result"]["content"][0]["text"])
        payload = json.loads(res[4]["result"]["content"][0]["text"])
        self.assertEqual(payload["format"], "text")
        info = json.loads(res[5]["result"]["content"][0]["text"])
        self.assertEqual(info["format"], "text")

    def test_tool_compress_history(self):
        path = os.path.join(self.tmp.name, "h.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(6):
                f.write(json.dumps({"role": "user" if i % 2 == 0 else "assistant",
                                    "content": f"第 {i} 条，决定保留 ATB-E01001"},
                                   ensure_ascii=False) + "\n")
        msgs = self._base() + [
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "atb_compress_history",
                        "arguments": {"path": path, "target_tokens": 800}}},
        ]
        res = self._run(msgs)
        text = res[7]["result"]["content"][0]["text"]
        self.assertIn("历史对话压缩包", text)
        self.assertIn("ATB-E01001", text)

    @unittest.skipUnless(sys.platform == "win32", "电脑控制仅支持 Windows")
    def test_tool_computer_available(self):
        msgs = self._base() + [
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
             "params": {"name": "atb_computer_use",
                        "arguments": {"action": "available"}}},
        ]
        res = self._run(msgs)
        payload = json.loads(res[8]["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])

    def test_tool_error_is_reported_not_crashing(self):
        msgs = self._base() + [
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "atb_extract_text",
                        "arguments": {"path": os.path.join(self.tmp.name, "nope.txt")}}},
        ]
        res = self._run(msgs)
        self.assertTrue(res[6]["result"]["isError"])
        self.assertIn("ATB-E01001", res[6]["result"]["content"][0]["text"])

    def test_unknown_method(self):
        res = self._run([{"jsonrpc": "2.0", "id": 9, "method": "no/such"}])
        self.assertEqual(res[9]["error"]["code"], -32601)

    def test_content_length_framing(self):
        res = self._run(self._base(), framing="content-length")
        self.assertEqual(res[1]["result"]["serverInfo"]["name"], "atb")


if __name__ == "__main__":
    unittest.main()
