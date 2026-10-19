"""tests/powershell.py が、CI の必須の指定で、PowerShell の不在と版違いを skip で通さないこと。

見つけた結果と版の取得を差し替えて確かめる。実物の PowerShell は使わない。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powershell  # noqa: E402


def which(found):
    return lambda name: found.get(name)


def get_version(v):
    def get(path):
        if isinstance(v, Exception):
            raise v
        return v
    return get


class FindTest(unittest.TestCase):
    def find(self, env, found, v="5.1.26100.4061"):
        return powershell.find(env=env, which=which(found), get_version=get_version(v))

    def test_optional_and_missing_is_skipped(self):
        self.assertEqual(self.find({}, {}), (None, None))

    def test_required_and_missing_fails(self):
        for env in ({"DEV_COM_REQUIRE_POWERSHELL": "1"}, {"DEV_COM_EXPECT_POWERSHELL": "5.1"}):
            with self.subTest(env=env), self.assertRaises(powershell.PowerShellError):
                self.find(env, {})

    def test_windows_powershell_comes_first(self):
        path, v = self.find({}, {"powershell": "/x/powershell", "pwsh": "/y/pwsh"})
        self.assertEqual((path, v), (os.path.abspath("/x/powershell"), None))

    def test_expected_version(self):
        env = {"DEV_COM_EXPECT_POWERSHELL": "5.1"}
        self.assertEqual(self.find(env, {"powershell": "/x/powershell"}), (os.path.abspath("/x/powershell"), "5.1.26100.4061"))
        for v in ("7.6.6", "5.0.10586.117", "15.1.0", powershell.PowerShellError("起動できない")):
            with self.subTest(v=v), self.assertRaises(powershell.PowerShellError):
                self.find(env, {"pwsh": "/y/pwsh"}, v)

    def test_version_of_something_that_is_not_powershell_fails(self):
        with self.assertRaises(powershell.PowerShellError):
            powershell.version(sys.executable)  # python は -NoProfile を知らず、0 以外で終わる
        with self.assertRaises(powershell.PowerShellError):
            powershell.version(os.path.join(os.path.dirname(os.path.abspath(__file__)), "no such powershell"))


if __name__ == "__main__":
    unittest.main()
