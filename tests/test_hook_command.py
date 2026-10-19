"""hooks/New-HookCommand.ps1 が作るフックのコマンドを、実際にシェルから動かす。PowerShell が無ければ飛ばす。

Codex はフックを、セッションのシェルで起動する（PowerShell なら -Command、無ければ cmd.exe /C）。
ここでは、Windows 向けの形を PowerShell の -Command と、cmd の代わりのシェル（POSIX では sh -c、Windows では cmd /C）の両方から流す。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(ROOT, "hooks", "New-HookCommand.ps1")
HOOK = os.path.join(ROOT, "hooks", "codex_status.py")
INSTALL = os.path.join(ROOT, "install.ps1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powershell  # noqa: E402

POWERSHELL = powershell.PATH


def ps_quote(s):
    # PowerShell は ' のほかに ‘ ’ ‚ ‛ も単一引用符として扱うので、どれも重ねてエスケープする。
    for q in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        s = s.replace(q, q + q)
    return "'" + s + "'"


@unittest.skipUnless(POWERSHELL, "PowerShell が無い")
class HookCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def build(self, python_call, script, windows=True, ps_path=None):
        args = "@(" + ", ".join(ps_quote(p) for p in python_call) + ")"
        line = ". %s; New-HookCommand -PythonCall %s -Script %s -Windows:$%s" % (
            ps_quote(BUILDER), args, ps_quote(script), "true" if windows else "false")
        if ps_path:
            line += " -PowerShellPath " + ps_quote(ps_path)
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", line],
                           capture_output=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def run_command(self, command):
        """生成したコマンドを、PowerShell の -Command と cmd 相当のシェルの両方から流す。"""
        stdin = json.dumps({"model": "gpt-6-luna", "hook_event_name": "UserPromptSubmit",
                            "prompt": "日本語の依頼"})
        shell = ["cmd.exe", "/C", command] if os.name == "nt" else ["sh", "-c", command]
        results = []
        for argv in ([POWERSHELL, "-NoProfile", "-Command", command], shell):
            r = subprocess.run(argv, input=stdin, capture_output=True, encoding="utf-8")
            results.append(r)
        return results

    def copy_hook(self, dirname):
        d = os.path.join(self.tmp.name, dirname)
        os.makedirs(d)
        dest = os.path.join(d, "codex_status.py")
        shutil.copy(HOOK, dest)
        return dest

    def test_plain_paths_are_called_directly(self):
        cmd = self.build(["C:\\Python311\\python.exe"], "C:\\Users\\me\\.codex\\hooks\\hirata-codex\\codex_status.py")
        self.assertEqual(cmd, "C:\\Python311\\python.exe C:\\Users\\me\\.codex\\hooks\\hirata-codex\\codex_status.py")
        cmd = self.build(["C:\\Windows\\py.exe", "-3"], "C:\\h\\codex_status.py")
        self.assertEqual(cmd, "C:\\Windows\\py.exe -3 C:\\h\\codex_status.py")

    def test_other_paths_use_the_full_path_of_powershell(self):
        cmd = self.build(["C:\\Program Files\\Python\\python.exe"], "C:\\h\\codex_status.py", ps_path=POWERSHELL_PLAIN)
        self.assertTrue(cmd.startswith(POWERSHELL_PLAIN + " -NoProfile -NonInteractive -EncodedCommand "))
        self.assertNotIn("Program Files", cmd)

    def test_refuses_a_bare_or_unplain_powershell_path(self):
        line = ". %s; New-HookCommand -PythonCall @('C:\\a b\\python.exe') -Script 'C:\\h\\x.py' -Windows -PowerShellPath 'powershell.exe '" % ps_quote(BUILDER)
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", line], capture_output=True, encoding="utf-8")
        self.assertNotEqual(r.returncode, 0)

    @unittest.skipIf(os.name == "nt", "パスに曲がった引用符や空白を含むフォルダを作る試験は POSIX でだけ流す")
    def test_encoded_command_runs_from_both_shells(self):
        for dirname in ("with space", "日本語", "O'Brien", "O\u2019Neil"):
            with self.subTest(dirname=dirname):
                script = self.copy_hook(dirname)
                cmd = self.build([sys.executable], script, ps_path=POWERSHELL_PLAIN)
                self.assertIn("-EncodedCommand", cmd)
                for r in self.run_command(cmd):
                    self.assertEqual(r.returncode, 0, r.stderr)
                    out = json.loads(r.stdout)["hookSpecificOutput"]
                    self.assertIn("gpt-6-luna", out["additionalContext"])

    def test_encoded_command_never_blocks_the_prompt(self):
        missing = os.path.join(self.tmp.name, "no such dir", "codex_status.py")
        cmd = self.build([sys.executable], missing, ps_path=POWERSHELL_PLAIN)
        for r in self.run_command(cmd):
            self.assertEqual(r.returncode, 0, r.stderr)


POWERSHELL_PLAIN = None
if POWERSHELL:
    # 試験で使う PowerShell のフルパス。空白などがあると New-HookCommand は受け付けないので、無ければ短い名前のリンクを作る。
    POWERSHELL_PLAIN = os.path.realpath(POWERSHELL)
    import re
    if not re.match(r"^[A-Za-z/][A-Za-z0-9_.:\\/~-]*$", POWERSHELL_PLAIN):
        POWERSHELL_PLAIN = None


MARKER = "hirata-codex usage status"


@unittest.skipUnless(POWERSHELL, "PowerShell が無い")
class _InstallBase(unittest.TestCase):
    """install.ps1 を一時フォルダの HOME で流す。流す前に、子の PowerShell の $HOME がそこを指すことを確かめる。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(os.path.realpath(self.tmp.name), "home")
        self.codex_home = os.path.join(self.home, ".codex")
        os.makedirs(self.codex_home)
        self.env = dict(os.environ)
        self.env.pop("CODEX_HOME", None)
        if os.name == "nt":
            # Windows の $HOME は USERPROFILE から決まる（about_Automatic_Variables）
            drive, rest = os.path.splitdrive(self.home)
            self.env.update(USERPROFILE=self.home, HOMEDRIVE=drive, HOMEPATH=rest)
        else:
            bindir = os.path.join(self.tmp.name, "bin")
            os.makedirs(bindir)
            os.symlink(sys.executable, os.path.join(bindir, "python"))
            self.env.update(HOME=self.home, PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "$HOME"],
                           capture_output=True, encoding="utf-8", env=self.env)
        # 本物の HOME へ入れないため、向けられなければここで止める
        self.assertEqual(os.path.normcase(r.stdout.strip()), os.path.normcase(self.home), r.stderr)
        self.hooks_json = os.path.join(self.codex_home, "hooks.json")

    def install(self, *args, script=INSTALL):
        argv = [POWERSHELL, "-NoProfile", "-NonInteractive"]
        if os.name == "nt":
            argv += ["-ExecutionPolicy", "Bypass"]
        r = subprocess.run(argv + ["-File", script] + list(args), capture_output=True, encoding="utf-8", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout


class InstallHooksJsonTest(_InstallBase):
    def test_writes_once_then_leaves_it(self):
        out = self.install()
        with open(self.hooks_json, "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM が付いている")
        handler = json.loads(raw.decode("utf-8"))["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(handler["statusMessage"], MARKER)
        self.assertIn("installed  " + self.hooks_json, out)
        again = self.install()
        self.assertIn("unchanged  " + self.hooks_json, again)
        self.assertNotIn("is not active yet", again)

    def current_handler(self):
        """install.ps1 が今作るハンドラー（空の CODEX_HOME に 1 度書かせて読む）。"""
        self.install()
        with open(self.hooks_json, encoding="utf-8") as f:
            handler = json.load(f)["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        os.remove(self.hooks_json)
        return handler

    def assert_left_alone(self, text, *, force=False):
        """hooks.json がそのままで、フックを使える状態と表示しない。"""
        with open(self.hooks_json, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        out = self.install(*(["-Force"] if force else []))
        self.assertIn("skipped    " + self.hooks_json, out)
        self.assertNotIn("unchanged  " + self.hooks_json, out)
        self.assertIn("is not active yet", out)
        with open(self.hooks_json, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), text)
        return out

    def test_existing_files_are_not_touched(self):
        for content in ("", "null", "{}", '{"hooks": {}}', '{"hooks": {"UserPromptSubmit": [{}]}}', "{broken",
                        '{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}}'):
            with self.subTest(content=content):
                self.assert_left_alone(content)

    def test_a_marker_that_is_not_a_matching_handler_is_not_ready(self):
        good = self.current_handler()

        def config(*handlers, event="UserPromptSubmit"):
            return json.dumps({"hooks": {event: [{"hooks": list(handlers)}]}}, ensure_ascii=False)

        cases = [
            ("古い command", config(dict(good, command='"C:\\old\\python.exe" "C:\\old\\codex_status.py"'))),
            ("大文字小文字だけ違う command", config(dict(good, command=good["command"].swapcase()))),
            ("別のイベントの目印", config(good, event="Stop")),
            ("目印が 2 件", config(good, good)),
            ("正しい 1 件と type 違いの目印", config(good, dict(good, type="prompt"))),
            ("type 違い", config(dict(good, type="prompt"))),
            ("command が文字列でない", config(dict(good, command=["python", "x.py"]))),
            ("commandWindows あり", config(dict(good, commandWindows=good["command"]))),
            ("目印が説明の文字列にあるだけ", json.dumps({"description": MARKER, "hooks": {}})),
        ]
        for name, text in cases:
            with self.subTest(name=name):
                self.assert_left_alone(text)
        old = '"C:\\old\\python.exe" "C:\\old\\codex_status.py"'
        out = self.assert_left_alone(config(dict(good, command=old)), force=True)
        self.assertIn(old, out)  # 差し替える前の command と、新しい command を並べて見せる
        self.assertIn(good["command"], out)

    def test_the_same_definition_is_left_alone(self):
        good = self.current_handler()
        text = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}],
                                     "UserPromptSubmit": [{"hooks": [good]}]}}, indent=2)
        with open(self.hooks_json, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        out = self.install()
        self.assertIn("unchanged  " + self.hooks_json, out)
        self.assertNotIn("is not active yet", out)
        with open(self.hooks_json, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), text)
