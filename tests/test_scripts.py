from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

# Git の hook から走ると、Git が渡した GIT_INDEX_FILE などを使い捨てのリポジトリの git まで引き継ぐので消す。
for variable in subprocess.run(
    ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, encoding="utf-8",
).stdout.split():
    os.environ.pop(variable, None)

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

    def write_case(self, d, case, outputs):
        cases = os.path.join(d, "cases.json")
        with open(cases, "w", encoding="utf-8") as f:
            json.dump({"cases": [case]}, f)
        for sub, text in outputs.items():
            os.makedirs(os.path.join(d, sub), exist_ok=True)
            if text is not None:
                with open(os.path.join(d, sub, case["id"] + ".txt"), "w", encoding="utf-8") as f:
                    f.write(text)
        return cases

    def test_case_without_any_check_is_rejected(self):
        # 何も確かめないケースが、空の出力でも通ったことにならない
        for expect in (None, {}, {"must_include": []}, {"json": False}, {"must_include": [], "regex": [], "json_required_keys": []}):
            with self.subTest(expect=expect), tempfile.TemporaryDirectory() as d:
                case = {"id": "sample"} if expect is None else {"id": "sample", "expect": expect}
                cases = self.write_case(d, case, {"after": ""})
                with self.assertRaises(check_outputs.CasesError):
                    check_outputs.load_cases(cases)
                self.assertEqual(check_outputs.main([cases, os.path.join(d, "after")]), 2)

    def test_bad_check_value_is_rejected(self):
        for expect in ({"must_include": "OK"}, {"must_not_include": [1]}, {"regex": ["("]}, {"json": "yes"},
                       {"json_required_keys": "a"}, {"max_chars": "800"}, {"max_chars": True}, {"max_chars": -1}, []):
            with self.subTest(expect=expect), tempfile.TemporaryDirectory() as d:
                cases = self.write_case(d, {"id": "sample", "expect": expect}, {})
                with self.assertRaises(check_outputs.CasesError):
                    check_outputs.load_cases(cases)

    def test_lost_output_is_a_regression(self):
        with tempfile.TemporaryDirectory() as d:
            cases = self.write_case(d, {"id": "sample", "expect": {"must_include": ["OK"]}}, {"before": "OK", "after": None})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_outputs.main([cases, os.path.join(d, "after"), "--baseline", os.path.join(d, "before")])
            result = json.loads(out.getvalue())
            self.assertEqual((code, result["failed"]), (1, ["sample"]))
            self.assertEqual(result["regressions"], [{"case": "sample", "check": "must_include:OK"}])

    def test_no_baseline_output_is_not_a_regression(self):
        with tempfile.TemporaryDirectory() as d:
            cases = self.write_case(d, {"id": "sample", "expect": {"must_include": ["OK"]}}, {"before": None, "after": None})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_outputs.main([cases, os.path.join(d, "after"), "--baseline", os.path.join(d, "before")])
            result = json.loads(out.getvalue())
            self.assertEqual((code, result["failed"], result["regressions"]), (1, ["sample"], []))


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

    def test_non_interactive_runs_are_told_not_to_stop(self):
        meta = {"type": "session_meta", "payload": {"id": "t1", "originator": "codex_exec", "source": "exec"}}
        path = self.rollout([meta, self.limits(85.0)])
        text = self.run_hook(json.dumps({"transcript_path": path}))["additionalContext"]
        self.assertIn("非対話の実行", text)
        self.assertNotIn("作業の前に案内する", text)
        self.assertNotIn("必ずそれを案内", text)
        meta["payload"]["originator"] = "codex-tui"
        path = self.rollout([meta, self.limits(10.0)])
        self.assertIn("作業の前に案内する", self.run_hook(json.dumps({"transcript_path": path}))["additionalContext"])

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


class FetchUpdatesTest(unittest.TestCase):
    """deep-latest の記録が、項目を見たことにして落とさないこと。ネットワークには出ない。"""

    def setUp(self) -> None:
        self.fu = load("skills/deep-latest/scripts/fetch_updates.py")
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name) / "seen.json"
        self.result = Path(self.dir.name) / "result.json"
        self.pages: list[tuple[str, str]] = []
        self.fail = False

        def fake_fetch(url: str):
            if self.fail:
                raise OSError("down")
            body = "".join(f"<item><title>{l}</title><link>{l}</link><pubDate>{d}</pubDate></item>" for l, d in self.pages)
            return ET.fromstring(f"<rss><channel>{body}</channel></rss>")

        self.patches = [
            patch.object(self.fu, "SOURCES", [("s", "rss", "https://example.invalid/rss")]),
            patch.object(self.fu, "fetch", fake_fetch),
        ]
        for p_ in self.patches:
            p_.start()

    def tearDown(self) -> None:
        for p_ in self.patches:
            p_.stop()
        self.dir.cleanup()

    def run_once(self) -> dict:
        result = self.fu.check(7, self.state, Path(self.dir.name))
        self.result.write_text(json.dumps(result))
        self.fu.commit(self.result, self.state)
        return result

    def test_item_added_later_with_the_same_date_is_new(self) -> None:
        date = "Tue, 22 Sep 2026 00:00:00 GMT"
        self.pages = [("a", date)]
        self.run_once()
        self.pages = [("a", date), ("b", date)]
        result = self.fu.check(7, self.state, Path(self.dir.name))
        self.assertEqual([i["link"] for i in result["sources"][0]["new_items"]], ["b"])

    def test_every_new_item_is_reported(self) -> None:
        # check() は 7 日の窓の中の記事だけを数えるので、日付を今日から数える
        now = datetime.now(timezone.utc)
        self.pages = [(f"p{i}", format_datetime(now - timedelta(hours=1, minutes=i), usegmt=True)) for i in range(60)]
        first = self.run_once()
        self.assertEqual(len(first["sources"][0]["new_items"]), 60)
        self.assertFalse(self.fu.check(7, self.state, Path(self.dir.name))["any_new"])

    def test_item_that_disappears_and_returns_is_not_reported_again(self) -> None:
        date = "Tue, 22 Sep 2026 00:00:00 GMT"
        for pages in ([("a", date), ("b", date)], [("b", date)]):
            self.pages = pages
            self.run_once()
        self.pages = [("a", date), ("b", date)]
        self.assertFalse(self.fu.check(7, self.state, Path(self.dir.name))["any_new"])

    def test_broken_record_falls_back_to_recent_days(self) -> None:
        for broken in (b'{"sources": []}', b'{"sources": {"s": "not-a-date"}}', b'{"sources": {"s": {"newest": "x"}}}', b"\xff\xfe"):
            with self.subTest(broken=broken):
                self.state.write_bytes(broken)
                self.assertTrue(self.fu.check(7, self.state, Path(self.dir.name))["sources"][0]["since_from"].endswith("days"))

    def test_failed_source_does_not_advance(self) -> None:
        self.pages = [("a", "Tue, 22 Sep 2026 00:00:00 GMT")]
        self.run_once()
        before = self.state.read_text()
        self.fail = True
        result = self.run_once()
        self.assertFalse(result["all_ok"])
        self.assertEqual(self.state.read_text().count("newest"), before.count("newest"))
        self.assertIn("2026-09-22", self.state.read_text())


    def test_bad_date_fails_only_that_source(self) -> None:
        pages = {"https://example.invalid/bad": [("a", "not-a-date")], "https://example.invalid/good": [("b", "Tue, 22 Sep 2026 00:00:00 GMT")]}

        def fetch(url: str):
            body = "".join(f"<item><title>{l}</title><link>{l}</link><pubDate>{d}</pubDate></item>" for l, d in pages[url])
            return ET.fromstring(f"<rss><channel>{body}</channel></rss>")

        sources = [("bad", "rss", "https://example.invalid/bad"), ("good", "rss", "https://example.invalid/good")]
        with patch.object(self.fu, "SOURCES", sources), patch.object(self.fu, "fetch", fetch):
            result = self.fu.check(7, self.state, Path(self.dir.name))
        self.assertEqual([s["ok"] for s in result["sources"]], [False, True])

    def test_response_that_is_not_a_feed_is_a_failure(self) -> None:
        for kind, body in (("rss", "<html><body>Proxy authentication required</body></html>"),):
            with self.subTest(kind=kind), patch.object(self.fu, "SOURCES", [("s", kind, "https://example.invalid/x")]), \
                    patch.object(self.fu, "fetch", lambda url, body=body: ET.fromstring(body)):
                result = self.fu.check(7, self.state, Path(self.dir.name))
                self.assertFalse(result["sources"][0]["ok"], "取得できていないソースを更新なしとして扱った")

    def test_older_feed_does_not_move_the_record_back(self) -> None:
        self.pages = [("newer", "Sun, 18 Oct 2026 00:00:00 GMT"), ("older", "Sat, 17 Oct 2026 00:00:00 GMT")]
        self.run_once()
        self.pages = [("older", "Sat, 17 Oct 2026 00:00:00 GMT")]
        self.run_once()
        self.pages = [("newer", "Sun, 18 Oct 2026 00:00:00 GMT"), ("older", "Sat, 17 Oct 2026 00:00:00 GMT")]
        self.assertFalse(self.fu.check(7, self.state, Path(self.dir.name))["any_new"], "古い一覧で記録が巻き戻り、見た項目をもう一度出した")


def fake_tag(*numbers: int, suffix: str = "") -> str:
    """偽のタグ。番号をソースに書くと VersionPinTest に当たるので、数値から組み立てる。"""
    return "rust-v" + ".".join(map(str, numbers)) + suffix


class CodexCliTagsTest(unittest.TestCase):
    """codex-cli のソースは、ほかのソースと同じく既読記録で前回からの新しい正式タグを出す。ネットワークには出ない。"""

    def setUp(self) -> None:
        self.fu = load("skills/deep-latest/scripts/fetch_updates.py")
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name) / "seen.json"
        self.tags = [fake_tag(0, n, 0) for n in range(150, 157)] + [
            fake_tag(0, 157, 9), fake_tag(0, 157, 10), fake_tag(0, 159, 0, suffix="-alpha.8"), "v1.0.0",
        ]
        self.ls_remote_failed = False
        self.patches = [
            patch.object(self.fu, "SOURCES", [("codex-cli", "git-tags", "https://github.com/openai/codex")]),
            patch.object(self.fu.subprocess, "run", self.fake_ls_remote),
        ]
        for p_ in self.patches:
            p_.start()

    def fake_ls_remote(self, *args, **kwargs) -> subprocess.CompletedProcess:
        if self.ls_remote_failed:
            return subprocess.CompletedProcess([], 128, "", "fatal: unable to access")
        return subprocess.CompletedProcess([], 0, "".join(f"{'0' * 40}\trefs/tags/{t}\n" for t in self.tags), "")

    def tearDown(self) -> None:
        for p_ in self.patches:
            p_.stop()
        self.dir.cleanup()

    def check(self) -> dict:
        return self.fu.check(7, self.state, Path(self.dir.name))

    def commit(self, result: dict, hold: list[str] | None = None) -> None:
        path = Path(self.dir.name) / "result.json"
        path.write_text(json.dumps(result))
        self.fu.commit(path, self.state, hold or [])

    def titles(self, result: dict) -> list[str]:
        return [i["title"] for i in result["sources"][0]["new_items"]]

    def test_first_run_lists_the_five_newest_stable_tags(self) -> None:
        # 末尾が 10 のタグと 9 のタグは、文字列で比べると逆に並ぶ。プレリリースと別の形のタグは出さない
        self.assertEqual(self.titles(self.check()), [
            fake_tag(0, 157, 10), fake_tag(0, 157, 9), fake_tag(0, 156, 0), fake_tag(0, 155, 0), fake_tag(0, 154, 0),
        ])

    def test_commit_marks_checked_tags_as_seen(self) -> None:
        self.commit(self.check())
        self.assertEqual(self.titles(self.check()), [], "初回に出さなかった古いタグも含め、既読にしたタグを出した")

    def test_tag_added_after_check_is_reported_later(self) -> None:
        result = self.check()
        self.tags.append(fake_tag(0, 158, 0))
        self.commit(result)
        self.assertEqual(self.titles(self.check()), [fake_tag(0, 158, 0)])

    def test_hold_keeps_the_record(self) -> None:
        self.commit(self.check())
        self.tags.append(fake_tag(0, 158, 0))
        self.commit(self.check(), hold=["codex-cli"])
        self.assertEqual(self.titles(self.check()), [fake_tag(0, 158, 0)], "ノートを読み切れなかった回に記録を進めた")

    def test_tag_that_disappears_and_returns_is_not_reported_again(self) -> None:
        self.commit(self.check())
        removed = self.tags.pop(0)
        self.commit(self.check())
        self.tags.insert(0, removed)
        self.assertEqual(self.titles(self.check()), [])

    def test_broken_record_falls_back_to_the_first_run(self) -> None:
        self.state.write_text(json.dumps({"sources": {"codex-cli": {"seen_tags": "broken"}}}))
        self.assertEqual(len(self.titles(self.check())), 5)

    def test_failures_are_not_reported_as_no_updates_and_keep_the_record(self) -> None:
        self.commit(self.check())
        before = self.state.read_text()
        for broken in ("ls-remote", "no-stable-tag"):
            with self.subTest(broken=broken):
                kept, self.tags = self.tags, [fake_tag(0, 159, 0, suffix="-alpha.8")] if broken == "no-stable-tag" else self.tags
                self.ls_remote_failed = broken == "ls-remote"
                result = self.check()
                self.assertFalse(result["sources"][0]["ok"])
                self.assertNotIn("new_count", result["sources"][0])
                self.commit(result)
                self.assertEqual(json.loads(self.state.read_text())["sources"], json.loads(before)["sources"])
                self.tags, self.ls_remote_failed = kept, False

    def test_other_sources_keep_their_records(self) -> None:
        self.state.write_text(json.dumps({"sources": {"codex": {"newest": "2026-10-01T00:00:00+00:00", "links_at_newest": ["x"]}}}))
        self.commit(self.check(), hold=["codex-cli"])
        self.commit(self.check())
        self.assertIn("codex", json.loads(self.state.read_text())["sources"])


class RepoStateTest(unittest.TestCase):
    """deep-latest の記録が相手のリポジトリごとに分かれ、別の記録を進めないこと。ネットワークには出ない。"""

    def setUp(self) -> None:
        self.fu = load("skills/deep-latest/scripts/fetch_updates.py")
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name).resolve()
        self.patches = [
            patch.object(self.fu, "SOURCES", []),
            patch.object(self.fu, "STATE_DIR", self.tmp / "states"),
        ]
        for p_ in self.patches:
            p_.start()

    def tearDown(self) -> None:
        for p_ in self.patches:
            p_.stop()
        self.dir.cleanup()

    def symlink(self, link: Path, target: Path) -> bool:
        """Windows は開発者モードか管理者の権限が無いと symlink を作れない。作れなければ False。"""
        try:
            link.symlink_to(target)
        except OSError:
            return False
        return True

    def git_repo(self, name: str) -> Path:
        repo = self.tmp / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def checked(self, root: Path, state: Path | None = None) -> Path:
        result = self.fu.check(7, state or self.fu.default_state_path(root), root)
        path = self.tmp / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def test_clones_with_the_same_name_get_different_records(self) -> None:
        a = self.fu.default_state_path(self.tmp / "a" / "dev")
        b = self.fu.default_state_path(self.tmp / "b" / "dev")
        self.assertNotEqual(a, b)
        self.assertTrue(a.name.startswith("dev-") and b.name.startswith("dev-"))

    def test_record_name_is_safe_and_short(self) -> None:
        name = self.fu.default_state_path(self.tmp / ("日本語 の repo" + "x" * 60)).name
        self.assertRegex(name, r"^[A-Za-z0-9._-]{1,40}-[0-9a-f]{12}\.json$")

    def test_subdirectory_and_symlink_find_the_same_root(self) -> None:
        repo = self.git_repo("日本語 の repo")
        (repo / "sub").mkdir()
        link = self.tmp / "link"
        if not self.symlink(link, repo):
            self.skipTest("symlink を作れない")
        self.assertEqual(self.fu.find_repo_root(str(link / "sub")), repo)
        self.assertEqual(self.fu.find_repo_root(str(repo)), repo)

    def test_git_environment_does_not_change_the_repo(self) -> None:
        repo, other = self.git_repo("dev"), self.git_repo("other")
        (other / "AGENTS.md").write_text("", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        env = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other), "GIT_INDEX_FILE": str(other / ".git" / "index")}
        with patch.dict(os.environ, env):
            self.assertEqual(self.fu.find_repo_root(str(repo)), repo)
            self.assertEqual(self.fu.targets(repo, "codex")["classified"], [])

    def test_empty_repo_or_outside_git_stops(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        for repo in ("", str(outside)):
            with self.subTest(repo=repo), self.assertRaises(self.fu.UsageError):
                self.fu.find_repo_root(repo)

    def test_commit_writes_the_record_of_the_checked_repo(self) -> None:
        root = self.git_repo("dev")
        self.fu.commit(self.checked(root))
        self.assertTrue(self.fu.default_state_path(root).is_file())

    def test_tampered_repo_root_does_not_advance_another_record(self) -> None:
        root, other = self.git_repo("dev"), self.git_repo("other")
        path = self.checked(root)
        result = json.loads(path.read_text(encoding="utf-8"))
        result["repo_root"] = str(other)
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(self.fu.UsageError):
            self.fu.commit(path)
        self.assertFalse((self.tmp / "states").exists())

    def test_explicit_state_must_match_the_check(self) -> None:
        root = self.git_repo("dev")
        given = self.tmp / "given.json"
        path = self.checked(root, given)
        for state in (None, self.tmp / "other.json"):
            with self.subTest(state=state), self.assertRaises(self.fu.UsageError):
                self.fu.commit(path, state)
        self.fu.commit(path, given)
        self.assertTrue(given.is_file())

    def test_out_must_not_overwrite_the_record(self) -> None:
        root = self.git_repo("dev")
        state = self.tmp / "s.json"
        self.fu.commit(self.checked(root, state), state, full_done_now=True)
        before = state.read_text(encoding="utf-8")
        link = self.tmp / "link.json"
        hard = self.tmp / "hard.json"
        os.link(state, hard)
        outs = [state, hard] + ([link] if self.symlink(link, state) else [])
        upper = self.tmp / "S.json"
        if upper.exists() and os.path.samefile(upper, state):  # 大文字小文字を区別しないファイルシステムだけ
            outs.append(upper)
        for out in outs:
            argv = ["fetch_updates.py", "check", "--repo", str(root), "--state", str(state), "--out", str(out)]
            with self.subTest(out=out.name), patch.object(sys, "argv", argv), patch("sys.stderr"):
                self.assertEqual(self.fu.main(), 2)
                self.assertEqual(state.read_text(encoding="utf-8"), before)

    @unittest.skipIf(sys.platform == "win32", "Windows のファイル名は末尾の空白・改行を持てない")
    def test_trailing_space_in_the_repo_name_is_kept(self) -> None:
        for name in ("dev ", "dev\n", "dev\r"):
            with self.subTest(name=name):
                repo = self.git_repo(name)
                self.assertEqual(self.fu.find_repo_root(str(repo)), repo)
                self.assertNotEqual(self.fu.default_state_path(repo), self.fu.default_state_path(self.tmp / "dev"))

    def test_full_is_required_until_a_full_run_is_committed(self) -> None:
        root = self.git_repo("dev")
        state = self.fu.default_state_path(root)
        self.assertTrue(self.fu.check(7, state, root)["full_required"])
        self.fu.commit(self.checked(root))
        self.assertTrue(self.fu.check(7, state, root)["full_required"], "全体の突き合わせを終えていない回で初回を外した")
        self.fu.commit(self.checked(root), full_done_now=True)
        self.assertFalse(self.fu.check(7, state, root)["full_required"])
        self.fu.commit(self.checked(root))
        self.assertFalse(self.fu.check(7, state, root)["full_required"], "部分監査の回で全体の突き合わせの記録を消した")
        record = json.loads(state.read_text(encoding="utf-8"))
        record["full_done_at"] = "broken"
        state.write_text(json.dumps(record), encoding="utf-8")
        self.assertTrue(self.fu.check(7, state, root)["full_required"])


class TargetsTest(unittest.TestCase):
    """deep-latest の突き合わせの候補が、置き場所で分かるものだけホストに分かれること。"""

    FILES = {
        ".claude/skills/a/SKILL.md": "",
        ".claude/settings.json": "{}",
        ".claude/rules/sub/r.md": "",
        "CLAUDE.md": "",
        ".mcp.json": "{}",
        ".agents/skills/b/SKILL.md": "",
        ".codex/config.toml": "",
        "AGENTS.md": "",
        "skills/c/SKILL.md": "",
        "skills/c/agents/openai.yaml": "",
        "agents/role.toml": 'developer_instructions = "x"',
        "claude/settings.json": "{}",
        "claude/agents/r.md": "",
        "pyproject.toml": "[project]",
        ".vscode/settings.json": "{}",
        "docs/guide.md": "",
        "skills/c/evals/fixtures/SKILL.md": "",
        "vendor/x/SKILL.md": "",
    }

    def setUp(self) -> None:
        self.fu = load("skills/deep-latest/scripts/fetch_updates.py")
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for name, body in self.FILES.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(body, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_each_host_gets_only_its_own_files(self) -> None:
        claude = self.fu.targets(self.root, "claude")
        codex = self.fu.targets(self.root, "codex")
        self.assertEqual(claude["classified"], sorted([
            ".claude/rules/sub/r.md", ".claude/settings.json", ".claude/skills/a/SKILL.md", ".mcp.json", "CLAUDE.md",
        ]))
        self.assertEqual(codex["classified"], sorted([
            ".agents/skills/b/SKILL.md", ".codex/config.toml", "AGENTS.md", "skills/c/agents/openai.yaml",
        ]))
        self.assertEqual(claude["other_host"], codex["classified"])
        self.assertEqual(codex["other_host"], claude["classified"])

    def test_files_without_a_placement_are_left_unclassified(self) -> None:
        self.assertEqual(self.fu.targets(self.root, "claude")["unclassified"], sorted([
            "agents/role.toml", "claude/agents/r.md", "claude/settings.json", "skills/c/SKILL.md",
        ]))

    def test_placement_that_points_to_both_hosts_is_left_unclassified(self) -> None:
        for name in (".codex/skills/x/CLAUDE.md", ".agents/skills/x/.mcp.json", ".claude/agents/openai.yaml"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                found = self.fu.targets(self.root, host)
                for name in (".codex/skills/x/CLAUDE.md", ".agents/skills/x/.mcp.json", ".claude/agents/openai.yaml"):
                    self.assertIn(name, found["unclassified"])

    @unittest.skipIf(sys.platform == "win32", "Windows のファイル名は CR を持てない")
    def test_file_name_with_cr_is_kept(self) -> None:
        name = ".claude/skills/a\r/SKILL.md"
        (self.root / name).parent.mkdir(parents=True)
        (self.root / name).write_text("", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        self.assertIn(name, self.fu.targets(self.root, "claude")["classified"])

    def test_excluded_and_unrelated_files_are_not_candidates(self) -> None:
        found = self.fu.targets(self.root, "claude")
        listed = set(found["classified"]) | set(found["other_host"]) | set(found["unclassified"])
        for name in ("pyproject.toml", ".vscode/settings.json", "docs/guide.md", "skills/c/evals/fixtures/SKILL.md", "vendor/x/SKILL.md"):
            with self.subTest(name=name):
                self.assertNotIn(name, listed)

if __name__ == "__main__":
    unittest.main()
