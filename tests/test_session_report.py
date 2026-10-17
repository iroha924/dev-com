"""skills/session-report/scripts/session_report.py を確かめる。

一時的な CODEX_HOME に作り物の rollout を書き、probe・extract・check・render を流す。
"""

import datetime
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
T2 = "22222222-2222-4222-8222-222222222222"
T3 = "33333333-3333-4333-8333-333333333333"
T4 = "44444444-4444-4444-8444-444444444444"
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
        line(ts, "event_msg", {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1}, "last_token_usage": c}}),
        line(ts, "event_msg", {"type": "item_started", "turn_id": turn, "item": {"type": "CommandExecution", "id": "c9",
                                                                                "command": [c]}}),
        line(ts, "event_msg", {"type": "知らない種類"}),
        line(ts, "event_msg", {"type": "x" * 41}),
    ]) + "\n{broken %s\n" % c


class Rollout:
    """作り物の会話を 1 行ずつ組み立てる。行ごとに時刻を 1 分進める。"""

    def __init__(self, tid, cwd="C:\\work\\ci-pipelines", branch="fix/test-results", source="cli",
                 start="2026-10-14T01:12:00Z"):
        self.tid = tid
        self.now = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
        self.lines = []
        self.turn_id = None
        self.add("session_meta", {"id": tid, "cwd": cwd, "originator": "codex_cli_rs", "cli_version": "0.162.1",
                                  "git": {"branch": branch}, "source": source})

    def ts(self):
        return self.now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def add(self, typ, payload, minutes=1):
        self.lines.append(line(self.ts(), typ, payload))
        self.now += datetime.timedelta(minutes=minutes)
        return self

    def turn(self, turn_id, model="gpt-6.1-sol", effort="medium"):
        self.turn_id = turn_id
        self.add("turn_context", {"turn_id": turn_id, "cwd": "C:\\work\\ci-pipelines", "model": model, "effort": effort}, 0)
        return self.add("event_msg", {"type": "task_started", "turn_id": turn_id}, 0)

    def item(self, it, minutes=1):
        return self.add("event_msg", {"type": "item_completed", "turn_id": self.turn_id, "item": it}, minutes)

    def user(self, mid, text, extra=None):
        return self.item({"type": "UserMessage", "id": mid, "content": [{"type": "text", "text": text}] + (extra or [])})

    def agent(self, mid, text, phase="final_answer"):
        return self.item({"type": "AgentMessage", "id": mid, "phase": phase, "content": [{"type": "Text", "text": text}]})

    def end(self, last=None, aborted=False):
        if aborted:
            return self.add("event_msg", {"type": "turn_aborted", "turn_id": self.turn_id, "reason": "interrupted"}, 0)
        return self.add("event_msg", {"type": "task_complete", "turn_id": self.turn_id, "last_agent_message": last}, 0)

    def text(self):
        return "\n".join(self.lines) + "\n"


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

    def run_cli(self, *args, cwd=None, env=None):
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, encoding="utf-8",
                           env=dict(self.env, **(env or {})), cwd=cwd)
        return r.returncode, r.stdout, r.stderr

    def extract(self, *args, **kw):
        out = os.path.join(self.tmp.name, "out")
        code, stdout, err = self.run_cli("extract", *args, "--out", out, **kw)
        self.assertEqual(code, 0, err)
        with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
            return json.load(f)


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

    def test_no_position_leaks_its_value(self):
        # 記録のどの位置の値を目印に置き換えても、種類の値の欄（ENUM_KEYS）に文字列を入れたとき以外は、出力に目印が出ない。
        # Codex の構造そのもの（payload・item・info）は dict のまま残し、その中の値と、入れ子の dict・list を置き換える
        enum_keys = {"type", "phase", "status", "server", "tool", "cli_version", "originator", "effort", "name", "role", "source"}
        structural = {("payload",), ("payload", "item"), ("payload", "info")}
        base = [json.loads(x) for x in canary_rollout(T1, "x").splitlines() if x.startswith("{") and not x.startswith("{broken")]

        def paths(obj, prefix=()):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield prefix + (k,)
                    yield from paths(v, prefix + (k,))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    yield prefix + (i,)
                    yield from paths(v, prefix + (i,))

        def replaced(obj, path, value):
            obj = json.loads(json.dumps(obj))
            target = obj
            for k in path[:-1]:
                target = target[k]
            target[path[-1]] = value
            return obj

        number = 86753091234
        leaks = []
        for n, row in enumerate(base):
            for path in paths(row):
                for value in (CANARY, {CANARY: CANARY}, [CANARY], {"type": "text", CANARY: CANARY}, number, [number]):
                    if isinstance(value, str) and isinstance(path[-1], str) and path[-1] in enum_keys:
                        continue
                    if not isinstance(value, str) and path in structural:
                        continue
                    data = (json.dumps(replaced(row, path, value)) + "\n").encode()
                    c = sr.collections.Counter()
                    sr.probe_file(c, data)
                    if any(CANARY in a or CANARY in b or str(number) in a + b for a, b in c):
                        leaks.append((n, path, type(value).__name__))
        self.assertEqual(leaks, [])

    def test_broken_file_uri_does_not_print_the_value(self):
        self.write(T1, "\n".join([
            line("2026-10-14T01:12:00Z", "session_meta", {"id": T1}),
            item("2026-10-14T01:12:00Z", "t1", {"type": "ImageView", "id": "i1", "path": "file://%s\uff0fprivate/a.png" % CANARY}),
        ]) + "\n")
        code, out, err = self.run_cli("probe")
        self.assertEqual(code, 0, err)
        self.assertNotIn(CANARY, out + err)
        self.assertIn("item.ImageView.path\tfile-uri,invalid\t1", out)

    def test_session_index_values_do_not_appear(self):
        self.write(T1, canary_rollout(T1, "x"))
        with open(os.path.join(self.home, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": CANARY, "thread_name": CANARY, "updated_at": CANARY}) + "\n{broken %s\n" % CANARY)
        out = self.probe()
        self.assertNotIn(CANARY, out)
        self.assertIn("session_index.key\tthread_name\t1", out)

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


def sample(tid=T1, cwd="C:\\work\\ci-pipelines"):
    r = Rollout(tid, cwd=cwd)
    r.turn("t1")
    r.user("u1", "Tests タブに何も出ない。原因を調べて直して", [{"type": "image", "image_url": "data:image/png;base64,%s" % CANARY},
                                                         {"type": "local_image", "path": "C:\\Users\\me\\tests-tab.png"}])
    r.agent("a1", "結果ファイルの置き場所を見ます", phase="commentary")
    r.agent("a2", "置き場所がずれていました。")
    r.agent("a2", "置き場所がずれていました。")  # 同じ ID の項目がもう一度
    r.item({"type": "Hologram", "id": "h1"})
    r.end("置き場所がずれていました。")
    r.turn("t2", model="gpt-6-luna", effort="low")
    r.user("u2", "password=Sup3rS3cretValue9 を使って PR を作って")
    r.end(aborted=True)
    return r


class ExtractTest(_Base):
    def test_session_turns_and_messages(self):
        path = self.write(T1, sample().text())
        with open(os.path.join(self.home, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": T1, "thread_name": "古い名前"}) + "\n" + json.dumps({"id": T1, "thread_name": "テスト結果の件"}) + "\n")
        rep = self.extract(T1)
        s = rep["session"]
        self.assertEqual((s["id"], s["title"], s["cwd"], s["branch"], s["source"]),
                         (T1, "テスト結果の件", "C:\\work\\ci-pipelines", "fix/test-results", "cli"))
        self.assertEqual((s["started"], s["ended"]), ("2026-10-14T01:12:00.000Z", "2026-10-14T01:19:00.000Z"))
        self.assertEqual([(t["id"], t["model"], t["effort"], t["status"]) for t in rep["turns"]],
                         [("t1", "gpt-6.1-sol", "medium", "completed"), ("t2", "gpt-6-luna", "low", "aborted")])
        self.assertEqual([(m["id"], m["kind"]) for m in rep["messages"]],
                         [("u1", "user"), ("a1", "commentary"), ("a2", "final"), ("u2", "user")])
        # 発言 ID は session-search の参照と同じ
        with open(path, "rb") as f:
            _, msgs, _ = sr.ss.parse_rollout(path, f.read())
        self.assertEqual({m["msg_id"] for m in msgs}, {m["id"] for m in rep["messages"]})
        self.assertEqual(rep["coverage"]["duplicated_ids"], 1)
        self.assertEqual(rep["coverage"]["unknown_items"], {"Hologram": 1})
        self.assertEqual(rep["sources"][0]["path"], path)
        self.assertEqual(rep["digest"], sr.digest(rep))

    def test_attachments_keep_only_names(self):
        self.write(T1, sample().text())
        rep = self.extract(T1)
        self.assertEqual(rep["messages"][0]["attachments"],
                         [{"kind": "image", "name": "貼り付けた画像", "body": False},
                          {"kind": "image", "name": "tests-tab.png", "body": False}])
        with open(os.path.join(self.tmp.name, "out", "report.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(CANARY, text)
        self.assertNotIn("Users", text.replace(self.home, ""))

    def test_secrets_are_redacted(self):
        self.write(T1, sample().text())
        rep = self.extract(T1)
        self.assertNotIn("Sup3rS3cretValue9", json.dumps(rep, ensure_ascii=False))

    def test_same_record_gives_same_bytes(self):
        self.write(T1, sample().text())
        out = os.path.join(self.tmp.name, "out", "report.json")
        self.extract(T1)
        with open(out, "rb") as f:
            first = f.read()
        self.extract(T1)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), first)

    def test_last_and_current(self):
        here = os.path.join(self.tmp.name, "repo")
        os.makedirs(here)
        older = self.write(T1, sample(T1, cwd=here).text())
        newer = self.write(T2, sample(T2, cwd=here).text())
        child = self.write(T3, Rollout(T3, cwd=here, source={"subagent": {"thread_spawn": {}}}).text())
        other = self.write(T4, sample(T4, cwd=os.path.join(self.tmp.name, "elsewhere")).text())
        for n, p in enumerate((older, newer, child, other)):
            os.utime(p, (1000 + n, 1000 + n))
        self.assertEqual(self.extract("--last", cwd=here)["session"]["id"], T2)
        # 今の会話（CODEX_THREAD_ID）は --last で選ばない
        self.assertEqual(self.extract("--last", cwd=here, env={"CODEX_THREAD_ID": T2})["session"]["id"], T1)
        self.assertEqual(self.extract("--current", env={"CODEX_THREAD_ID": T4})["session"]["id"], T4)

    def test_every_string_is_redacted(self):
        tok = "ghp_" + "A1b2C3d4E5" * 3 + "f6g7h8"
        r = Rollout(T1, source=tok)
        r.lines[0] = line(r.ts(), "session_meta", {"id": T1, "cwd": "C:\\%s" % tok, "originator": tok, "agent_role": tok,
                                                   "source": tok, "git": {"branch": tok}})
        r.turn("t1").user("u1", "a").end("b")
        path = os.path.join(self.home, "sessions", tok, "rollout-2026-10-14T01-00-00-%s.jsonl" % T1)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write(r.text())
        self.assertNotIn(tok, json.dumps(self.extract(T1), ensure_ascii=False))

    def test_thread_id_comes_from_session_meta(self):
        # revert は同じ会話の続きを、別の rollout ID の名前のファイルに書く（session-search の test_revert_keeps_the_prefix_file_searchable）
        old = self.write(T1, sample(T1).text())
        new = self.write(T2, sample(T1).user("u3", "続き").text())
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        rep = self.extract(T1)
        self.assertEqual(rep["sources"][0]["path"], new)
        self.assertEqual(rep["coverage"]["other_files"], 1)
        # 今の会話（CODEX_THREAD_ID=T1）は、どちらのファイルでも --last で選ばない
        here = os.path.join(self.tmp.name, "repo")
        os.makedirs(here)
        for p in (old, new):
            os.remove(p)
        a = self.write(T1, sample(T1, cwd=here).text())
        b = self.write(T2, sample(T1, cwd=here).text())
        c = self.write(T3, sample(T3, cwd=here).text())
        for n, p in enumerate((c, a, b)):
            os.utime(p, (1000 + n, 1000 + n))
        self.assertEqual(self.extract("--last", cwd=here, env={"CODEX_THREAD_ID": T1})["session"]["id"], T3)

    def test_attachment_only_message_is_kept(self):
        r = Rollout(T1).turn("t1")
        r.item({"type": "UserMessage", "id": "u1", "content": [{"type": "local_image", "path": "C:\\a\\only.png"}]})
        r.agent("a1", "見ました").end("見ました")
        self.write(T1, r.text())
        msgs = self.extract(T1)["messages"]
        self.assertEqual([(m["id"], m["kind"], m["text"]) for m in msgs], [("u1", "user", ""), ("a1", "final", "見ました")])
        self.assertEqual(msgs[0]["attachments"], [{"kind": "image", "name": "only.png", "body": False}])

    def test_legacy_turns_are_linked_to_messages(self):
        r = Rollout(T1)
        for n in (1, 2):
            r.add("turn_context", {"cwd": "C:\\w", "model": "gpt-6.1-sol"})
            r.add("event_msg", {"type": "user_message", "message": "依頼 %d" % n})
            r.add("event_msg", {"type": "task_complete", "last_agent_message": "答え %d" % n})
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertEqual([t["id"] for t in rep["turns"]], ["turn-1", "turn-2"])
        self.assertEqual([(m["text"], m["turn_id"]) for m in rep["messages"]],
                         [("依頼 1", "turn-1"), ("答え 1", "turn-1"), ("依頼 2", "turn-2"), ("答え 2", "turn-2")])

    def test_last_stops_when_a_newer_record_cannot_be_read(self):
        here = os.path.join(self.tmp.name, "repo")
        os.makedirs(here)
        older = self.write(T1, sample(T1, cwd=here).text())
        newer = os.path.join(os.path.dirname(older), "rollout-2026-10-14T02-00-00-%s.jsonl.zst" % T2)
        with open(newer, "wb") as f:
            f.write(b"not zstd")
        os.utime(older, (1000, 1000))
        os.utime(newer, (2000, 2000))
        real = sr.ss.read_rollout_bytes
        sr.ss.read_rollout_bytes = lambda p: None if p.endswith(".zst") else real(p)
        try:
            with self.assertRaises(sr.UnreadableRecords):
                sr.find_rollout(self.home, last_cwd=here)
        finally:
            sr.ss.read_rollout_bytes = real

    def test_ids_models_and_types_are_redacted(self):
        tok = "ghp_" + "A1b2C3d4E5" * 3 + "f6g7h8"
        r = Rollout(T1).turn("t-" + tok, model=tok, effort=tok)
        r.user("u-" + tok, "a").item({"type": tok, "id": "x"}).agent("a1", "b").end("b")
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertNotIn(tok, json.dumps(rep, ensure_ascii=False))
        # 伏せ字をかけても、発言のターンとの対応は崩れない
        self.assertEqual(rep["messages"][0]["turn_id"], rep["turns"][0]["id"])

    def test_paginated_revert_reads_the_prefix_file(self):
        first = Rollout(T1).turn("t1").user("u1", "最初の依頼").agent("a1", "最初の答え").end("最初の答え")
        cut = len(first.text().encode("utf-8"))
        first.turn("t2").user("u2", "取り消した依頼").agent("a2", "取り消した答え").end("取り消した答え")
        prefix = self.write(T1, first.text())
        cont = Rollout(T1)
        cont.lines[0] = line(cont.ts(), "session_meta", {"id": T1, "cwd": "C:\\w", "history_mode": "paginated",
                                                         "history_base": {"thread_id": T1, "end_ordinal_exclusive": 6,
                                                                          "end_byte_offset": cut}})
        cont.turn("t3").user("u3", "やり直した依頼").agent("a3", "やり直した答え").end("やり直した答え")
        newer = self.write(T2, cont.text())
        os.utime(prefix, (1000, 1000))
        os.utime(newer, (2000, 2000))
        rep = self.extract(T1)
        self.assertEqual([m["text"] for m in rep["messages"]], ["最初の依頼", "最初の答え", "やり直した依頼", "やり直した答え"])
        self.assertEqual([s["path"] for s in rep["sources"]], [prefix, newer])
        self.assertEqual(rep["sources"][0]["bytes"], cut)

    def test_a_broken_unrelated_record_does_not_stop_the_choice(self):
        good = self.write(T1, sample(T1).text())
        broken = self.write(T2, "x")
        os.utime(broken, (1000, 1000))
        os.utime(good, (2000, 2000))
        real = sr.ss.read_rollout_bytes

        def read(p):
            if p == broken:
                raise ValueError("壊れた圧縮")  # compression.zstd の ZstdError は OSError ではない
            return real(p)
        sr.ss.read_rollout_bytes = read
        try:
            self.assertEqual(sr.find_rollout(self.home, thread_id=T1), (good, 0))
        finally:
            sr.ss.read_rollout_bytes = real

    def test_legacy_turn_after_a_modern_turn(self):
        r = Rollout(T1).turn("t1").user("u1", "新しい形の依頼").agent("a1", "新しい形の答え").end("新しい形の答え")
        r.add("turn_context", {"cwd": "C:\\w", "model": "gpt-6.1-sol"})
        r.add("event_msg", {"type": "user_message", "message": "古い形の依頼"})
        r.add("event_msg", {"type": "task_complete", "last_agent_message": "古い形の答え"})
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertEqual([(m["text"], m["kind"], m["turn_id"]) for m in rep["messages"]],
                         [("新しい形の依頼", "user", "t1"), ("新しい形の答え", "final", "t1"),
                          ("古い形の依頼", "user", "turn-1"), ("古い形の答え", "final", "turn-1")])

    def test_missing_thread(self):
        code, _, err = self.run_cli("extract", T1, "--out", self.tmp.name)
        self.assertEqual(code, 1)
        self.assertIn("見つからない", err)
        code, _, err = self.run_cli("extract", "not-an-id")
        self.assertEqual(code, 2)


class SearchModuleTest(unittest.TestCase):
    def test_uses_session_search(self):
        # 写さずに import している（伏せ字の規則が 2 つに分かれない）
        self.assertTrue(sr.ss.__file__.endswith(os.path.join("session-search", "scripts", "session_search.py")))


if __name__ == "__main__":
    unittest.main()
