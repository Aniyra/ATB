# -*- coding: utf-8 -*-
"""视频抽帧测试：现场编码一个小视频，再走读取 / MCP 两条链路。

依赖 PyAV 与 Pillow；缺失时自动跳过。
"""

import base64
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from atb.dispatch import read_file  # noqa: E402
from atb.models import ReadOptions  # noqa: E402

_REPORT_TMP = None
_OLD_REPORT_DIR = None


def setUpModule():
    """把抽帧产物写入临时目录，避免污染项目 reports/。"""
    global _REPORT_TMP, _OLD_REPORT_DIR
    _REPORT_TMP = tempfile.mkdtemp(prefix="atb_test_reports_")
    _OLD_REPORT_DIR = config.REPORT_DIR
    config.REPORT_DIR = _REPORT_TMP


def tearDownModule():
    config.REPORT_DIR = _OLD_REPORT_DIR
    if _REPORT_TMP:
        shutil.rmtree(_REPORT_TMP, ignore_errors=True)


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def make_video(path: str, seconds: int = 3, fps: int = 10, size=(160, 120)) -> None:
    import av
    from PIL import Image

    container = av.open(path, mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    frames = seconds * fps
    for i in range(frames):
        img = Image.new("RGB", size, ((i * 7) % 256, 40 + (i * 3) % 120, 200 - (i * 5) % 150))
        frame = av.VideoFrame.from_image(img)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


@unittest.skipUnless(has("av") and has("PIL"), "需要 PyAV 与 Pillow")
class VideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.video = os.path.join(self.tmp.name, "clip.mp4")
        make_video(self.video)

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_and_frames(self):
        opts = ReadOptions(frames=4, frame_width=160)
        doc = read_file(self.video, opts)
        self.assertEqual(doc.format, "video")
        self.assertAlmostEqual(doc.metadata["duration"], 3.0, delta=0.3)
        self.assertEqual(doc.metadata["width"], 160)

        frames = doc.metadata["frames"]
        self.assertEqual(len(frames), 4)
        for f in frames:
            self.assertTrue(os.path.exists(f["path"]))
            self.assertGreater(f["bytes"], 0)
            self.assertLessEqual(max(f["width"], f["height"]), 160)

        budget = doc.metadata["budget"]
        self.assertEqual(budget["frames"], 4)
        self.assertGreater(budget["tokens_estimate"], 0)
        self.assertGreater(budget["base64_bytes"], budget["image_bytes"])

    def test_window(self):
        doc = read_file(self.video, ReadOptions(frames=2, start=1.0, end=2.0))
        self.assertEqual(doc.metadata["window"], {"start": 1.0, "end": 2.0})
        times = [f["time"] for f in doc.metadata["frames"]]
        self.assertTrue(all(1.0 <= t <= 2.05 for t in times), times)

    def test_frames_are_distinct(self):
        doc = read_file(self.video, ReadOptions(frames=3, frame_width=128))
        sizes = {f["bytes"] for f in doc.metadata["frames"]}
        self.assertGreater(len(sizes), 1, "不同时间点的画面不应完全相同")

    def test_keyframe_sampling_fallback(self):
        doc = read_file(self.video, ReadOptions(frames=3, sampling="keyframe"))
        self.assertGreaterEqual(len(doc.metadata.get("frames", [])), 1)

    def test_read_file_metadata_only_recommends_video_tool(self):
        from atb.mcp import McpServer

        stdin, stdout = io.StringIO(), io.StringIO()
        server = McpServer(stdin=stdin, stdout=stdout)
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "atb_read_video",
                        "arguments": {"path": self.video, "frames": 2, "width": 128}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "atb_video_guide", "arguments": {}}},
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
        text_parts = [c for c in content if c["type"] == "text"]
        images = [c for c in content if c["type"] == "image"]
        self.assertTrue(text_parts)
        self.assertIn("原生图像输入", text_parts[0]["text"])
        self.assertEqual(len(images), 2)
        raw = base64.b64decode(images[0]["data"])
        self.assertEqual(raw[:3], b"\xff\xd8\xff")
        self.assertTrue(results[3]["result"]["content"][0]["text"].startswith("# ATB 视频"))


if __name__ == "__main__":
    unittest.main()
