import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(rel):
    path = os.path.join(ROOT, *rel.split("/"))
    spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ado_context = load("skills/ado/scripts/ado_context.py")
prompt_lint = load("skills/prompt-lint/scripts/prompt_lint.py")
extract_errors = load("skills/ci-failure-triage/scripts/extract_errors.py")
check_outputs = load("skills/prompt-regression/scripts/check_outputs.py")
preview_body = load("skills/pipeline-change-check/scripts/preview_body.py")

COLL = "https://tfs.example.local/tfs/DefaultCollection"


class AdoContextTest(unittest.TestCase):
    def test_project_and_repo(self):
        ctx = ado_context.resolve(COLL + "/My%20Project/_git/my-repo", COLL)
        self.assertEqual((ctx["project"], ctx["repo"]), ("My Project", "my-repo"))
        self.assertEqual(ctx["project_path"], "My%20Project")

    def test_project_omitted_when_same_as_repo(self):
        ctx = ado_context.resolve(COLL + "/_git/Tools", COLL)
        self.assertEqual((ctx["project"], ctx["repo"]), ("Tools", "Tools"))

    def test_credentials_in_remote_are_not_echoed(self):
        ctx = ado_context.resolve("https://me:SECRET@tfs.example.local/tfs/DefaultCollection/P/_git/R", COLL)
        self.assertNotIn("SECRET", json.dumps(ctx))
        with self.assertRaises(ado_context.ContextError) as cm:
            ado_context.resolve("https://me:SECRET@tfs.example.local/tfs/DefaultCollection/P/R", COLL)
        self.assertNotIn("SECRET", str(cm.exception))

    def test_userinfo_and_case_are_ignored(self):
        ctx = ado_context.resolve("https://me@TFS.example.local/tfs/defaultcollection/P/_git/R", COLL + "/")
        self.assertEqual((ctx["project"], ctx["repo"]), ("P", "R"))

    def test_ssh_remote(self):
        ctx = ado_context.resolve("ssh://tfs.example.local:22/tfs/DefaultCollection/P/_git/R", COLL)
        self.assertEqual((ctx["project"], ctx["repo"]), ("P", "R"))

    def test_other_host_is_refused(self):
        with self.assertRaises(ado_context.ContextError):
            ado_context.resolve("https://evil.example.com/tfs/DefaultCollection/P/_git/R", COLL)

    def test_other_collection_is_refused(self):
        with self.assertRaises(ado_context.ContextError):
            ado_context.resolve("https://tfs.example.local/tfs/Other/P/_git/R", COLL)

    def test_not_a_git_url(self):
        with self.assertRaises(ado_context.ContextError):
            ado_context.resolve(COLL + "/P/_wiki/R", COLL)


class PromptLintTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, data):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def codes(self, path, **kw):
        findings, used = prompt_lint.check_file(path, **kw)
        return [f.code for f in findings], used

    def test_clean_markdown(self):
        codes, _ = self.codes(self.write("a.md", "# 見出し\n本文\n"))
        self.assertEqual(codes, [])

    def test_bom_and_mixed_newlines(self):
        codes, _ = self.codes(self.write("a.txt", b"\xef\xbb\xbfa\r\nb\n"))
        self.assertEqual(sorted(codes), ["bom", "mixed-newline"])

    def test_not_utf8(self):
        codes, _ = self.codes(self.write("a.txt", "日本語".encode("cp932")))
        self.assertEqual(codes, ["encoding"])

    def test_json_duplicate_key(self):
        codes, _ = self.codes(self.write("a.json", '{"a": 1, "a": 2}'))
        self.assertEqual(codes, ["duplicate-key"])

    def test_json_syntax(self):
        codes, _ = self.codes(self.write("a.json", '{"a": 1,}'))
        self.assertEqual(codes, ["json"])

    def test_jsonl_reports_line(self):
        path = self.write("a.jsonl", '{"a": 1}\n{"b": \n')
        findings, _ = prompt_lint.check_file(path)
        self.assertEqual([(f.code, f.line) for f in findings], [("json", 2)])

    def test_brace_unescaped_json_example(self):
        codes, _ = self.codes(self.write("a.md", '出力例: {"key": 1}\n'), var_style="brace")
        self.assertEqual(codes, ["template"])

    def test_brace_variables_and_undefined(self):
        path = self.write("a.md", "{user} さん、{{literal}} {ticket.id}\n")
        codes, used = self.codes(path, var_style="brace", defined={"user"})
        self.assertEqual(used, {"user", "ticket"})
        self.assertEqual(codes, ["undefined-var"])

    def test_brace_nested_format_spec(self):
        # format(value=1.25) は KeyError: 'width' になる
        codes, used = self.codes(self.write("a.txt", "{value:{width}.{precision}f}"), var_style="brace", defined={"value"})
        self.assertEqual(used, {"value", "width", "precision"})
        self.assertEqual(codes, ["undefined-var", "undefined-var"])

    def test_brace_positional(self):
        codes, _ = self.codes(self.write("a.txt", "値は {} です"), var_style="brace")
        self.assertEqual(codes, ["template"])

    def test_dollar(self):
        _, used = self.codes(self.write("a.txt", "$name と ${other} と $$"), var_style="dollar")
        self.assertEqual(used, {"name", "other"})
        codes, _ = self.codes(self.write("b.txt", "価格 $ 100"), var_style="dollar")
        self.assertEqual(codes, ["template"])

    def test_double_brace(self):
        _, used = self.codes(self.write("a.md", "{{ name }} / {{ item.title }}"), var_style="double-brace")
        self.assertEqual(used, {"name", "item.title"})
        codes, _ = self.codes(self.write("b.md", "{{ name }"), var_style="double-brace")
        self.assertEqual(codes, ["template"])

    def test_json_only_string_values_are_templates(self):
        path = self.write("a.json", json.dumps({"system": "{role} として答える", "n": 3}, ensure_ascii=False))
        codes, used = self.codes(path, var_style="brace")
        self.assertEqual((codes, used), ([], {"role"}))

    def test_main_exit_code(self):
        good = self.write("ok.md", "ok\n")
        bad = self.write("ng.json", "{")
        self.assertEqual(prompt_lint.main([good]), 0)
        self.assertEqual(prompt_lint.main([good, bad]), 1)


class ExtractErrorsTest(unittest.TestCase):
    LOG = [
        "2026-10-16T01:00:00.0000000Z ##[section]Starting: Run tests",
        "2026-10-16T01:00:01.0000000Z collected 12 items",
        "2026-10-16T01:00:02.0000000Z FAILED tests/test_a.py::test_x - AssertionError: 1 != 2",
        "2026-10-16T01:00:03.0000000Z ##[error]Bash exited with code '1'.",
    ]

    def test_extracts_and_strips_timestamp(self):
        hits = extract_errors.extract(self.LOG, context=0)
        self.assertEqual([h["line"] for h in hits], [3, 4])
        self.assertTrue(hits[0]["text"].startswith("FAILED"))

    def test_signature_normalizes_numbers_and_hex(self):
        a = extract_errors.signature("2026-10-16T01:00:00Z error in build 1234 at deadbeef12")
        b = extract_errors.signature("2026-10-17T09:00:00Z error in build 9999 at cafebabe34")
        self.assertEqual(a, b)

    def test_reads_api_json_shape(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"count": 2, "value": ["ok", "##[error]boom"]}, f)
            self.assertEqual(extract_errors.read_lines(path), ["ok", "##[error]boom"])


class CheckOutputsTest(unittest.TestCase):
    def test_evaluate(self):
        expect = {"must_include": ["要約"], "must_not_include": ["TODO"], "json_required_keys": ["a"], "max_chars": 5}
        results = {name: ok for name, ok, _ in check_outputs.evaluate(expect, '{"a": "要約"}')}
        self.assertEqual(results["must_include:要約"], True)
        self.assertEqual(results["must_not_include:TODO"], True)
        self.assertEqual(results["json"], True)
        self.assertEqual(results["json_required_keys:a"], True)
        self.assertEqual(results["max_chars:5"], False)

    def test_regression_against_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            cases = os.path.join(d, "cases.json")
            with open(cases, "w", encoding="utf-8") as f:
                json.dump({"cases": [{"id": "c1", "expect": {"must_include": ["OK"]}}]}, f)
            for sub, text in (("before", "OK"), ("after", "NG")):
                os.mkdir(os.path.join(d, sub))
                with open(os.path.join(d, sub, "c1.txt"), "w", encoding="utf-8") as f:
                    f.write(text)
            self.assertEqual(check_outputs.main([cases, os.path.join(d, "after"), "--baseline", os.path.join(d, "before")]), 1)

    def test_unknown_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cases = os.path.join(d, "cases.json")
            with open(cases, "w", encoding="utf-8") as f:
                json.dump({"cases": [{"id": "c1", "expect": {"must_inculde": ["x"]}}]}, f)
            with self.assertRaises(check_outputs.CasesError):
                check_outputs.load_cases(cases)


class PreviewBodyTest(unittest.TestCase):
    def test_preview_is_always_true(self):
        body = preview_body.build("steps: []\n", "refs/heads/main", {"env": "dev"})
        self.assertIs(body["previewRun"], True)
        self.assertEqual(body["resources"]["repositories"]["self"]["refName"], "refs/heads/main")
        self.assertEqual(body["templateParameters"], {"env": "dev"})

    def test_bad_param(self):
        with self.assertRaises(ValueError):
            preview_body.parse_params(["novalue"])


class CheckDraftConstraintsTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "skills", "writing-quality", "scripts", "check_draft_constraints.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(text)
        return path

    def run_check(self, *args):
        r = subprocess.run([sys.executable, self.SCRIPT] + list(args), capture_output=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def test_ok_with_template(self):
        tpl = self.write("tpl.md", "## 概要\n\n## 確認したこと\n")
        draft = self.write("d.md", "## 概要\n\nretry を直す。\n\n## 確認したこと\n\nテストを流した。\n", encoding="utf-8-sig")
        self.assertEqual(self.run_check(draft, "--template", tpl)[0], 0)

    def test_missing_heading_and_over_limit(self):
        tpl = self.write("tpl.md", "## 概要\n\n## 確認したこと\n")
        draft = self.write("d.md", "## 概要\n\n" + "あ" * 50 + "\n")
        code, out = self.run_check(draft, "--template", tpl, "--max-chars", "10")
        self.assertEqual(code, 1)
        self.assertIn("確認したこと", out)
        self.assertIn("字数超過", out)

    def test_heading_inside_fence_is_not_counted(self):
        tpl = self.write("tpl.md", "## 概要\n")
        draft = self.write("d.md", "```\n## 概要\n```\n")
        self.assertEqual(self.run_check(draft, "--template", tpl)[0], 1)

    def test_heading_inside_longer_fence_is_not_counted(self):
        # 閉じるフェンスは、開いたものと同じ文字で同じ長さ以上、後ろは空白だけ（CommonMark 4.5）。
        tpl = self.write("tpl.md", "## 概要\n")
        draft = self.write("d.md", "````markdown\n```python\n## 概要\n```\n````\n")
        self.assertEqual(self.run_check(draft, "--template", tpl)[0], 1)
        draft2 = self.write("d2.md", "```\n``` not a close\n## 概要\n```\n")
        self.assertEqual(self.run_check(draft2, "--template", tpl)[0], 1)

    def test_not_checked_is_exit_2(self):
        self.assertEqual(self.run_check(self.write("d.html", "<p>x</p>"))[0], 2)
        self.assertEqual(self.run_check(os.path.join(self.tmp.name, "none.md"))[0], 2)
        with open(os.path.join(self.tmp.name, "sjis.md"), "wb") as f:
            f.write("概要".encode("cp932"))
        self.assertEqual(self.run_check(os.path.join(self.tmp.name, "sjis.md"))[0], 2)

    def test_warnings_do_not_fail(self):
        draft = self.write("d.md", "TODO 後で書く\n\n同じ段落\n\n同じ段落\n")
        code, out = self.run_check(draft)
        self.assertEqual(code, 0)
        self.assertIn("TODO", out)
        self.assertIn("2 回", out)



class CodexStatusHookTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "hooks", "codex_status.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def rollout(self, items):
        path = os.path.join(self.tmp.name, "rollout.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item) + "\n")
        return path

    def run_hook(self, stdin, env=None):
        r = subprocess.run([sys.executable, self.SCRIPT], input=stdin, capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0)
        # Windows では powershell.exe を通るので、出力は ASCII だけにする（日本語は \uXXXX）。
        r.stdout.encode("ascii")
        return json.loads(r.stdout)["hookSpecificOutput"]

    @staticmethod
    def limits(used, resets_at=4102444800):
        return {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": used, "window_minutes": 10080, "resets_at": resets_at},
            "secondary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": resets_at}}}}

    def test_reports_latest_turn_and_limits(self):
        path = self.rollout([
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "medium"}},
            self.limits(40.0),
            {"type": "turn_context", "payload": {"model": "gpt-6-luna", "effort": "low"}},
            self.limits(55.0),
        ])
        out = self.run_hook(json.dumps({"transcript_path": path, "hook_event_name": "UserPromptSubmit"}))
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        text = out["additionalContext"]
        self.assertIn("gpt-6-luna", text)
        self.assertIn("effort low", text)
        self.assertNotIn("直前", text)
        self.assertIn("週の枠 55% 使用", text)
        self.assertIn("5 時間の枠 12% 使用", text)
        self.assertNotIn("必ずそれを案内", text)

    def test_active_model_from_input_wins(self):
        path = self.rollout([{"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "medium"}}])
        text = self.run_hook(json.dumps({"transcript_path": path, "model": "gpt-6-luna"}))["additionalContext"]
        self.assertIn("モデル gpt-6-luna", text)

    def test_tight_limit_adds_the_reminder(self):
        path = self.rollout([self.limits(85.0)])
        self.assertIn("必ずそれを案内", self.run_hook(json.dumps({"transcript_path": path}))["additionalContext"])

    def test_values_from_before_a_reset_are_not_used(self):
        path = self.rollout([self.limits(95.0, resets_at=1)])
        text = self.run_hook(json.dumps({"transcript_path": path}))["additionalContext"]
        self.assertIn("リセット済み", text)
        self.assertNotIn("95%", text)
        self.assertNotIn("必ずそれを案内", text)

    def test_subagents_get_nothing(self):
        r = subprocess.run([sys.executable, self.SCRIPT], input=json.dumps({"agent_id": "a1", "agent_type": "researcher"}),
                           capture_output=True, encoding="utf-8")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_event_name_defaults_to_user_prompt_submit(self):
        self.assertEqual(self.run_hook("{}")["hookEventName"], "UserPromptSubmit")

    def test_reset_time_is_local(self):
        path = self.rollout([self.limits(10.0, resets_at=4102444800)])  # 2100-01-01T00:00:00Z
        env = dict(os.environ, TZ="Asia/Tokyo")
        if not hasattr(__import__("time"), "tzset"):
            self.skipTest("TZ を切り替えられない環境（Windows）")
        self.assertIn("リセット 01/01 09:00", self.run_hook(json.dumps({"transcript_path": path}), env=env)["additionalContext"])

    def test_malformed_values_still_exit_0(self):
        for bad in ({"used_percent": "abc", "window_minutes": 10080}, {"used_percent": 50, "resets_at": 1e400},
                    {"used_percent": 50, "resets_at": 10 ** 30}):
            with self.subTest(bad=bad):
                path = self.rollout([{"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {"primary": bad}}}])
                self.assertIn("[利用状況]", self.run_hook(json.dumps({"transcript_path": path}))["additionalContext"])

    def test_failures_still_exit_0(self):
        for stdin in ("", "garbage", "[]", json.dumps({"transcript_path": os.path.join(self.tmp.name, "none.jsonl")}),
                      json.dumps({"transcript_path": self.tmp.name})):
            with self.subTest(stdin=stdin):
                self.assertIn("不明", self.run_hook(stdin)["additionalContext"])

    def test_broken_lines_and_tail_read(self):
        codex_status = load("hooks/codex_status.py")
        path = self.rollout([{"type": "turn_context", "payload": {"model": "old", "effort": "high"}}] * 50
                            + [self.limits(30.0), {"type": "turn_context", "payload": {"model": "new", "effort": "low"}}])
        with open(path, "a", encoding="utf-8") as f:
            f.write("{broken\n")
        turn, limits = codex_status.latest_status(codex_status.read_tail_lines(path, tail_bytes=400))
        self.assertEqual(turn["model"], "new")
        self.assertEqual(limits["primary"]["used_percent"], 30.0)


if __name__ == "__main__":
    unittest.main()
