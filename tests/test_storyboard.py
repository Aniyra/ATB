# -*- coding: utf-8 -*-
"""场景分镜功能测试：现场编码带切点的视频，验证分格、出图与 MCP 回传。"""

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
    """分镜/宫格图写入临时目录，避免污染项目 reports/。"""
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
        self.assertNotIn("experimental", sb)
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
        self.assertTrue(any("启发式" in w for w in doc.warnings))

    def test_subtitles_align_to_cells(self):
        srt = os.path.splitext(self.video)[0] + ".srt"
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,200 --> 00:00:01,800\n第一段台词\n\n"
                    "2\n00:00:02,200 --> 00:00:04,500\n第二段台词\n")
        doc = read_file(self.video, ReadOptions(storyboard=True, cells=3))
        sb = doc.metadata["storyboard"]
        self.assertTrue(sb["has_subtitles"])
        lines = [line for c in sb["cell_table"] for line in c.get("lines", [])]
        self.assertIn("第一段台词", lines)
        self.assertIn("第二段台词", lines)

    def test_zoom_region(self):
        from atb.readers.video import export_zoom

        result = export_zoom(self.video, 1.0, [0.1, 0.1, 0.4, 0.4], width=400)
        self.assertTrue(os.path.exists(result["path"]))
        self.assertEqual(result["width"], 400)
        self.assertGreater(result["height"], 0)
        self.assertLessEqual(result["tokens_estimate"], 400)

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
        self.assertIn("视频分镜", text)
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["data"])[:3], b"\xff\xd8\xff")


def make_av_video(path: str, seconds: int = 2, size=(160, 96), fps: int = 10) -> None:
    """生成带 PCM 音轨的 MKV，用于验证音频包络与音轨导出。"""
    import math

    import av
    from PIL import Image

    c = av.open(path, mode="w")
    vs = c.add_stream("mpeg4", rate=fps)
    vs.width, vs.height = size
    vs.pix_fmt = "yuv420p"
    aus = c.add_stream("pcm_s16le", rate=44100)
    aus.layout = "mono"

    for sec in range(seconds):
        n = 4410
        frame = av.AudioFrame(format="s16", layout="mono", samples=n)
        frame.sample_rate = 44100
        data = bytearray()
        for i in range(n):
            val = int(6000 * math.sin(2 * math.pi * 440 * i / 44100))
            data += int(val).to_bytes(2, "little", signed=True)
        frame.planes[0].update(bytes(data))
        frame.pts = sec * n
        for pkt in aus.encode(frame):
            c.mux(pkt)

    for i in range(seconds * fps):
        img = Image.new("RGB", size, ((i * 9) % 256, 60, 180))
        for pkt in vs.encode(av.VideoFrame.from_image(img)):
            c.mux(pkt)
    for pkt in aus.encode():
        c.mux(pkt)
    for pkt in vs.encode():
        c.mux(pkt)
    c.close()


@unittest.skipUnless(has("av") and has("PIL"), "需要 PyAV 与 Pillow")
class StoryboardAudioTest(unittest.TestCase):
    def test_audio_envelope_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "av.mkv")
            make_av_video(path)

            doc = read_file(path, ReadOptions(storyboard=True, cells=2))
            sb = doc.metadata["storyboard"]
            self.assertTrue(sb["has_audio"])

            doc2 = read_file(path, ReadOptions(frames=2, include_audio=True,
                                               frame_width=128))
            audio = doc2.metadata.get("audio_path")
            self.assertTrue(audio, "音轨应成功导出")
            self.assertTrue(os.path.exists(audio))
            self.assertGreater(os.path.getsize(audio), 0)


if __name__ == "__main__":
    unittest.main()
