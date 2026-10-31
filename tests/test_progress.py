# -*- coding: utf-8 -*-
"""进度条组件测试：进度/ETA、spinner、禁用态。"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.progress import Progress, _fmt_secs, make_progress  # noqa: E402


class ProgressTest(unittest.TestCase):
    def test_bar_with_eta(self):
        buf = io.StringIO()
        p = Progress(enabled=True, stream=buf)
        p.stage("扫描切点", total=10)
        for i in range(10):
            p._last_draw = 0
            p.update(1, note=f"{i + 1}/10")
        p.done("3 个切点")
        out = buf.getvalue()
        self.assertIn("█", out)
        self.assertIn("%", out)
        self.assertIn("ETA", out)
        self.assertIn("\x1b[K", out)      # 清行尾
        self.assertIn("完成", out)
        self.assertIn("3 个切点", out)

    def test_spinner_when_total_unknown(self):
        buf = io.StringIO()
        p = Progress(enabled=True, stream=buf)
        p.stage("抽取画面")
        p._last_draw = 0
        p.update(1, note="2/9 格")
        p.done("9 格")
        out = buf.getvalue()
        self.assertIn("2/9 格", out)
        self.assertIn("完成", out)

    def test_disabled_is_silent(self):
        buf = io.StringIO()
        p = Progress(enabled=False, stream=buf)
        p.stage("x", total=1)
        p.update(1)
        p.done()
        self.assertEqual(buf.getvalue(), "")

    def test_make_progress_non_tty(self):
        # StringIO 不是 TTY，自动判断应关闭
        p = make_progress(stream=io.StringIO())
        self.assertFalse(p.enabled)

    def test_fmt_secs(self):
        self.assertEqual(_fmt_secs(3.24), "3.2s")
        self.assertEqual(_fmt_secs(65), "1m05s")
        self.assertEqual(_fmt_secs(3661), "1h01m")


if __name__ == "__main__":
    unittest.main()
