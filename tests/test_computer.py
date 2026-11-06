# -*- coding: utf-8 -*-
"""电脑控制（实验性）测试：只验证不产生副作用的路径。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.errors import AtbError  # noqa: E402
from atb.experimental import computer  # noqa: E402

WINDOWS = sys.platform == "win32"


class KeyParseTest(unittest.TestCase):
    def test_combo(self):
        self.assertEqual(computer.parse_keys("ctrl+shift+s"), [0x11, 0x10, 0x53])

    def test_plus_key(self):
        self.assertEqual(computer.parse_keys("ctrl++"), [0x11, 0xBB])

    def test_function_key(self):
        self.assertEqual(computer.parse_keys("f12"), [0x7B])

    def test_unknown_key(self):
        with self.assertRaises(AtbError) as ctx:
            computer.parse_keys("ctrl+不存在的键")
        self.assertEqual(ctx.exception.code, "ATB-E00003")


@unittest.skipUnless(WINDOWS, "电脑控制仅支持 Windows")
class WindowsApiTest(unittest.TestCase):
    def test_available(self):
        info = computer.available()
        self.assertTrue(info["ok"])
        self.assertEqual(info["platform"], "win32")
        self.assertIn("uiautomation", info)
        self.assertIn("screenshot", info)

    def test_list_apps_schema(self):
        apps = computer.list_apps(limit=3)
        self.assertLessEqual(len(apps), 3)
        for item in apps:
            for key in ("app_id", "hwnd", "title", "pid", "process", "bounds"):
                self.assertIn(key, item)
            self.assertEqual(len(item["bounds"]), 4)

    def test_missing_app(self):
        with self.assertRaises(AtbError) as ctx:
            computer._resolve_app("ATB-绝对不存在的窗口名称-zzz")
        self.assertEqual(ctx.exception.code, "ATB-E13002")

    def test_dispatch_unknown_action(self):
        with self.assertRaises(AtbError) as ctx:
            computer.dispatch("no_such_action")
        self.assertEqual(ctx.exception.code, "ATB-E00003")


if __name__ == "__main__":
    unittest.main()
