"""skills/session-report/scripts/session_report.py を確かめる。

一時的な CODEX_HOME に作り物の rollout を書き、probe・extract・check・render を流す。
"""

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "session-report", "scripts", "session_report.py")
spec = importlib.util.spec_from_file_location("session_report", SCRIPT)
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

T1 = "11111111-1111-4111-8111-111111111111"
# 本文・コマンド・出力・パス・引数・検索語・差分・作業フォルダ・ブランチに入れる目印。probe の出力に出たら、中身を出している。
# type・phase・status・MCP の名前のような種類の値は Codex のコードと設定が決めるもので、英数字の短い形なら出す
CANARY = "CANARYzq7"


def line(ts, typ, payload):
    return json.dumps({"timestamp": ts, "type": typ, "payload": payload}, ensure_ascii=False)


def item(ts, turn, it):
    return line(ts, "event_msg", {"type": "item_completed", "turn_id": turn, "item": it})


def canary_rollout(tid, image_path):
    """全部の種類の項目の値に目印を入れた会話。"""
    ts, turn = "2026-10-14T01:12:00Z", "t1"
    c = CANARY
    return "\n".join([
        line(ts, "session_meta", {"id": tid, "cwd": "C:\\work\\%s" % c, "originator": "codex-tui", "cli_version": "0.162.1",
                                  "git": {"branch": "fix/%s" % c}, "source": "cli"}),
        line(ts, "turn_context", {"turn_id": turn, "cwd": "C:\\work\\%s" % c, "model": "gpt-6.1-sol", "effort": "medium"}),
        item(ts, turn, {"type": "UserMessage", "id": "u1", "content": [
            {"type": "text", "text": "依頼 %s" % c}, {"type": "local_image", "path": "C:\\img\\%s.png" % c},
            {"type": "image", "image_url": "data:image/png;base64,%s" % c}]}),
        item(ts, turn, {"type": "CommandExecution", "id": "c1", "command": ["pwsh", "-c", "echo %s" % c],
                        "cwd": "C:\\work\\%s" % c, "parsed_cmd": [{"type": "unknown", "cmd": c}], "source": "unified_exec_startup",
                        "status": "failed", "aggregated_output": "out %s" % c, "exit_code": 1, "duration": {"secs": 1, "nanos": 0}}),
        item(ts, turn, {"type": "FileChange", "id": "f1", "status": "completed",
                        "changes": {"C:\\work\\%s.py" % c: {"type": "update", "unified_diff": "+%s" % c, "move_path": None}}}),
        item(ts, turn, {"type": "McpToolCall", "id": "m1", "server": "ado", "tool": "create_pull_request",
                        "arguments": {c: c}, "status": "completed", "result": {"content": [{"type": "text", "text": c}]}}),
        item(ts, turn, {"type": "WebSearch", "id": "w1", "query": c, "action": {"type": "search", "query": c}, "results": []}),
        item(ts, turn, {"type": "ImageView", "id": "i1", "path": image_path}),
        item(ts, turn, {"type": "AgentMessage", "id": "a1", "phase": "final_answer", "content": [{"type": "Text", "text": c}]}),
        item(ts, turn, {"type": "AgentMessage", "id": "a1", "phase": "final_answer", "content": [{"type": "Text", "text": c}]}),
        line(ts, "response_item", {"type": "function_call", "name": "shell", "arguments": c}),
        line(ts, "response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": c}]}),
        line(ts, "event_msg", {"type": "task_complete", "turn_id": turn, "last_agent_message": c}),
        line(ts, "event_msg", {"type": "知らない種類"}),
        line(ts, "event_msg", {"type": "x" * 41}),
    ]) + "\n{broken %s\n" % c


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "codex")
        os.makedirs(os.path.join(self.home, "sessions", "2026", "10", "14"))
        self.env = dict(os.environ, CODEX_HOME=self.home, HIRATA_CODEX_DATA=os.path.join(self.tmp.name, "data"),
                        HIRATA_CODEX_REPORT_REEXEC="test")

    def write(self, tid, text, day="2026-10-14"):
        y, m, d = day.split("-")
        path = os.path.join(self.home, "sessions", y, m, d, "rollout-%sT01-00-00-%s.jsonl" % (day, tid))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, encoding="utf-8", env=self.env)
        return r.returncode, r.stdout, r.stderr


class ProbeShapeTest(_Base):
    def probe(self, *args):
        code, out, err = self.run_cli("probe", *args)
        self.assertEqual(code, 0, err)
        return out

    def test_values_do_not_appear(self):
        image = os.path.join(self.tmp.name, "%s.png" % CANARY)
        with open(image, "wb") as f:
            f.write(b"x")
        self.write(T1, canary_rollout(T1, pathlib.Path(image).as_uri()))
        out = self.probe()
        self.assertNotIn(CANARY, out)
        self.assertNotIn("work", out)

    def test_string_values_in_shape_fields_do_not_appear(self):
        # command や MCP の result が知らない形（文字列）で来ても、中身を出さない
        ts, turn = "2026-10-14T01:12:00Z", "t1"
        self.write(T1, "\n".join([
            line(ts, "session_meta", {"id": T1}),
            item(ts, turn, {"type": "CommandExecution", "id": "c1", "command": CANARY, "duration": CANARY, "status": "completed"}),
            item(ts, turn, {"type": "McpToolCall", "id": "m1", "server": "ado", "tool": "x", "result": CANARY}),
            item(ts, turn, {"type": "FileChange", "id": "f1", "changes": {"a": CANARY}}),
            line(ts, "session_meta", {"id": T1, "source": {"subagent": CANARY}}),
        ]) + "\n")
        self.assertNotIn(CANARY, self.probe())

    def test_unc_file_uri_keeps_the_server(self):
        seen = []
        real = os.path.exists
        os.path.exists = lambda p: seen.append(p) or False
        try:
            self.assertEqual(sr._path_state("file://server/share/a.png"), "file-uri,missing")
        finally:
            os.path.exists = real
        self.assertEqual(seen, ["//server/share/a.png"])

    def test_every_line_is_category_name_count(self):
        self.write(T1, canary_rollout(T1, "C:\\nowhere\\%s.png" % CANARY))
        out = self.probe()
        lines = out.splitlines()
        self.assertGreater(len(lines), 20)
        for row in lines:
            with self.subTest(row=row):
                self.assertRegex(row, r"^[A-Za-z0-9_.()-]+\t[A-Za-z0-9_.,{}()+=<>-]+\t\d+$")

    def test_counts(self):
        image = os.path.join(self.tmp.name, "seen.png")
        with open(image, "wb") as f:
            f.write(b"x")
        self.write(T1, canary_rollout(T1, pathlib.Path(image).as_uri()))
        rows = {tuple(r.split("\t")[:2]): int(r.split("\t")[2]) for r in self.probe().splitlines()}
        expect = {
            ("item.type", "CommandExecution"): 1, ("item.type", "AgentMessage"): 2,
            ("item.CommandExecution.exit_code", "1"): 1, ("item.CommandExecution.aggregated_output", "len<=2k"): 1,
            ("item.CommandExecution.duration", "{nanos+secs}"): 1,
            ("item.FileChange.change.shape", "{move_path+type+unified_diff}"): 1,
            ("item.McpToolCall.server", "ado"): 1, ("item.McpToolCall.tool", "create_pull_request"): 1,
            ("item.UserMessage.content.type", "local_image"): 1, ("item.UserMessage.local_image.path", "absolute,missing"): 1,
            ("item.UserMessage.image.url", "data"): 1, ("item.ImageView.path", "file-uri,exists"): 1,
            ("ids.item_completed", "duplicated"): 1, ("event_msg.type", "(other)"): 2,
            ("response_item.function_call.name", "shell"): 1, ("lines", "bad_json"): 1, ("files", "jsonl"): 1,
            ("session_meta.cli_version", "0.162.1"): 1, ("turn_context.effort", "medium"): 1,
        }
        for key, n in expect.items():
            with self.subTest(key=key):
                self.assertEqual(rows.get(key), n)

    def test_since_filters_by_file_date(self):
        self.write(T1, canary_rollout(T1, "x"), day="2026-09-30")
        rows = self.probe("--since", "2026-10-01")
        self.assertNotIn("files\tjsonl", rows)
        self.assertIn("files\tjsonl\t1", self.probe())

    def test_bad_since(self):
        code, _, err = self.run_cli("probe", "--since", "10/01")
        self.assertEqual(code, 2)


class SearchModuleTest(unittest.TestCase):
    def test_uses_session_search(self):
        # 写さずに import している（伏せ字の規則が 2 つに分かれない）
        self.assertTrue(sr.ss.__file__.endswith(os.path.join("session-search", "scripts", "session_search.py")))


if __name__ == "__main__":
    unittest.main()
