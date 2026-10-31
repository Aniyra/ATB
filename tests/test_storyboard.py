# -*- coding: utf-8 -*-
"""实验性分镜功能测试：现场编码带切点的视频，验证分格、出图与 MCP 回传。"""

import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.dispatch import read_file  # noqa: E402
from atb.models import ReadOptions  # noqa: E402


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def make_scene_video(path: str, scenes=3, seconds_per_scene=2, fps=10,
                     size=(320, 180)) -> None:
    """生成由若干纯色场景拼接的视频，制造明确切点。"""
    import av
    from PIL import Image

    colors = [(200, 40, 40), (40, 160, 90), (50, 80, 220), (220, 200, 60),
              (180, 60, 200), (40, 200, 220)]
    container = av.open(path, mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for s in range(scenes):
        img = Image.new("RGB", size, colors[s % len(colors)])
        for _ in range(seconds_per_scene * fps):
            frame = av.VideoFrame.from_image(img)
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


@unittest.skipUnless(has("av") and has("PIL"), "需要 PyAV 与 Pillow")
class StoryboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.video = os.path.join(self.tmp.name, "scenes.mp4")
        make_scene_video(self.video)

    def tearDown(self):
        self.tmp.cleanup()

    def test_storyboard_sheet(self):
        doc = read_file(self.video, ReadOptions(storyboard=True, cells=6))
        self.assertTrue(doc.ok)
        self.assertEqual(doc.format, "video")
        self.assertIn("storyboard", doc.metadata)
        sb = doc.metadata["storyboard"]
        self.assertTrue(sb["experimental"])
        self.assertEqual(sb["cells"], 6)
        self.assertEqual(len(sb["sheets"]), 1)
        self.assertGreater(sb["tokens_estimate"], 0)

        sheet = sb["sheets"][0]
        self.assertTrue(os.path.exists(sheet))
        from PIL import Image
        with Image.open(sheet) as im:
            self.assertGreaterEqual(im.width, 300)
            self.assertGreaterEqual(im.height, 200)

        images = [b for b in doc.blocks if b.type == "image"]
        self.assertEqual(len(images), 1)
        self.assertTrue(any("实验性" in w for w in doc.warnings))

    def test_multiple_sheets(self):
        doc = read_file(self.video, ReadOptions(storyboard=True, cells=6, sheets=2))
        sb = doc.metadata["storyboard"]
        self.assertEqual(len(sb["sheets"]), 2)
        self.assertTrue(all(os.path.exists(p) for p in sb["sheets"]))

    def test_time_window(self):
        doc = read_file(self.video, ReadOptions(storyboard=True, cells=2,
                                               start=1.0, end=3.0))
        self.assertEqual(doc.metadata["window"], {"start": 1.0, "end": 3.0})
        self.assertEqual(doc.metadata["storyboard"]["cells"], 2)

    def test_cut_detection_finds_scene_changes(self):
        doc = read_file(self.video, ReadOptions(storyboard=True, cells=6))
        # 3 个场景 → 至少 2 个切点
        self.assertGreaterEqual(doc.metadata["storyboard"]["cuts"], 2)

    def test_mcp_returns_sheet_image(self):
        from atb.mcp import McpServer

        stdin, stdout = io.StringIO(), io.StringIO()
        server = McpServer(stdin=stdin, stdout=stdout)
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "atb_video_storyboard",
                        "arguments": {"path": self.video, "cells": 4}}},
        ]
        for m in msgs:
            stdin.write(json.dumps(m, ensure_ascii=False) + "\n")
        stdin.seek(0)
        server.serve_forever()

        results = {}
        for line in stdout.getvalue().splitlines():
            if line.strip():
                obj = json.loads(line)
                results[obj.get("id")] = obj

        content = results[2]["result"]["content"]
        text = content[0]["text"]
        images = [c for c in content if c["type"] == "image"]
        self.assertIn("实验性", text)
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["data"])[:3], b"\xff\xd8\xff")


if __name__ == "__main__":
    unittest.main()
