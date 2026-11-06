# -*- coding: utf-8 -*-
"""reports TTL 清理测试。"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atb import reports  # noqa: E402


class ReportsCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, sub, name, age_days):
        folder = os.path.join(self.base, sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * 16)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_removes_old_and_keeps_new(self):
        old = self._touch("frames", "old.jpg", 10)
        new = self._touch("frames", "new.jpg", 1)
        result = reports.cleanup(7, base_dir=self.base)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertEqual(result["removed_files"], 1)
        self.assertEqual(result["kept_files"], 1)
        self.assertGreater(result["freed_bytes"], 0)

    def test_dry_run_keeps_files(self):
        old = self._touch("storyboards", "old.jpg", 10)
        result = reports.cleanup(7, dry_run=True, base_dir=self.base)
        self.assertTrue(os.path.exists(old))
        self.assertEqual(result["removed_files"], 1)
        self.assertTrue(result["dry_run"])

    def test_zero_days_removes_everything(self):
        path = self._touch("zoom", "now.jpg", 0)
        reports.cleanup(0, base_dir=self.base)
        self.assertFalse(os.path.exists(path))

    def test_only_media_roots_are_cleaned(self):
        frames = self._touch("frames", "old.jpg", 30)
        log = os.path.join(self.base, "atb_error_log.json")
        with open(log, "w", encoding="utf-8") as f:
            f.write("{}")
        stamp = time.time() - 30 * 86400
        os.utime(log, (stamp, stamp))
        reports.cleanup(7, base_dir=self.base)
        self.assertFalse(os.path.exists(frames))
        self.assertTrue(os.path.exists(log))

    def test_empty_dirs_are_removed(self):
        old = self._touch("computer", "old.jpg", 30)
        folder = os.path.dirname(old)
        reports.cleanup(7, base_dir=self.base)
        self.assertFalse(os.path.exists(folder))

    def test_missing_base_is_safe(self):
        result = reports.cleanup(7, base_dir=os.path.join(self.base, "nope"))
        self.assertEqual(result["removed_files"], 0)


if __name__ == "__main__":
    unittest.main()
