import importlib.util
import json
import os
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


if __name__ == "__main__":
    unittest.main()
