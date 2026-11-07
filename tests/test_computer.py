# -*- coding: utf-8 -*-
"""UI 精准编辑（实验性）测试：只验证不产生副作用的路径。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb.errors import AtbError  # noqa: E402
from atb.experimental import computer  # noqa: E402

WINDOWS = sys.platform == "win32"


class ActionsTest(unittest.TestCase):
    def test_actions_are_reduced(self):
        self.assertEqual(computer.ACTIONS,
                         ("available", "state", "ground", "set_value",
                          "select", "action"))

    def test_desktop_automation_actions_removed(self):
        for action in ("list", "launch", "click", "scroll", "drag",
                       "type", "key"):
            with self.assertRaises(AtbError) as ctx:
                computer.dispatch(action)
            self.assertEqual(ctx.exception.code, "ATB-E00003")


@unittest.skipUnless(WINDOWS, "UI 精准编辑仅支持 Windows")
class WindowsApiTest(unittest.TestCase):
    def test_available(self):
        info = computer.available()
        self.assertIn("ok", info)
        self.assertEqual(info["platform"], "win32")
        self.assertIn("uiautomation", info)
        self.assertNotIn("screenshot", info)

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
