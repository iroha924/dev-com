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
                        HIRATA_CODEX_REPORT_REEXEC="test", TZ="Asia/Tokyo")  # 表示の時刻を、流す PC の時差によらず決める

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

    def test_models_and_types_are_redacted_but_ids_are_kept(self):
        # ID は Codex が作る値で、秘密は入らない。伏せ字をかけると別の ID が同じ文字になり、対応が崩れる
        tok = "ghp_" + "A1b2C3d4E5" * 3 + "f6g7h8"
        tok2 = "ghp_" + "Z9y8X7w6V5" * 3 + "u4t3s2"
        r = Rollout(T1).turn("t-" + tok, model=tok, effort=tok)
        r.user("u-" + tok, "a").item({"type": tok, "id": "x"}).agent("a1", "b").end("b")
        r.turn("t-" + tok2).user("u-" + tok2, "c").end("d")
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertEqual([t["id"] for t in rep["turns"]], ["t-" + tok, "t-" + tok2])
        self.assertEqual([m["turn_id"] for m in rep["messages"] if m["kind"] == "user"], ["t-" + tok, "t-" + tok2])
        self.assertEqual(rep["turns"][0]["model"], "[REDACTED-TOKEN]")
        self.assertNotIn(tok, json.dumps({k: v for k, v in rep["coverage"].items()}, ensure_ascii=False))

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

    def test_long_revert_chain_is_read_to_the_start(self):
        tids = ["%08d-0000-4000-8000-%012d" % (n, n) for n in range(12)]
        prev, prev_len = None, 0
        for n, rid in enumerate(tids):
            r = Rollout(T1)
            meta = {"id": T1, "cwd": "C:\\w"}
            if prev:
                meta["history_base"] = {"thread_id": prev, "end_ordinal_exclusive": 1, "end_byte_offset": prev_len}
            r.lines[0] = line(r.ts(), "session_meta", meta)
            r.turn("t%d" % n).user("u%d" % n, "依頼 %d" % n).end("答え %d" % n)
            path = self.write(rid, r.text())
            os.utime(path, (1000 + n, 1000 + n))
            prev, prev_len = rid, len(r.text().encode("utf-8"))
        rep = self.extract(T1)
        self.assertEqual([m["text"] for m in rep["messages"] if m["kind"] == "user"], ["依頼 %d" % n for n in range(12)])
        self.assertFalse(rep["coverage"]["prefix_missing"])

    def test_short_prefix_file_is_reported(self):
        first = Rollout(T1).turn("t1").user("u1", "前").end("前の答え")
        prefix = self.write(T1, first.text())
        cont = Rollout(T1)
        cont.lines[0] = line(cont.ts(), "session_meta", {"id": T1, "cwd": "C:\\w", "history_base": {
            "thread_id": T1, "end_ordinal_exclusive": 9, "end_byte_offset": len(first.text().encode("utf-8")) + 500}})
        cont.turn("t2").user("u2", "後").end("後の答え")
        newer = self.write(T2, cont.text())
        os.utime(prefix, (1000, 1000))
        os.utime(newer, (2000, 2000))
        self.assertTrue(self.extract(T1)["coverage"]["prefix_missing"])

    def test_revert_keeps_the_continuation_metadata(self):
        first = Rollout(T1, cwd="C:\\old", branch="old").turn("t1").user("u1", "前").end("前の答え")
        cut = len(first.text().encode("utf-8"))
        prefix = self.write(T1, first.text())
        cont = Rollout(T1)
        cont.lines[0] = line(cont.ts(), "session_meta", {"id": T1, "cwd": "C:\\new", "git": {"branch": "new"},
                                                         "cli_version": "0.163.0", "history_mode": "paginated",
                                                         "history_base": {"thread_id": T1, "end_ordinal_exclusive": 3,
                                                                          "end_byte_offset": cut}})
        cont.turn("t2").user("u2", "後").end("後の答え")
        newer = self.write(T2, cont.text())
        os.utime(prefix, (1000, 1000))
        os.utime(newer, (2000, 2000))
        s = self.extract(T1)["session"]
        self.assertEqual((s["cwd"], s["branch"], s["cli_version"]), ("C:\\new", "new", "0.163.0"))

    def test_incomplete_last_line_is_reported(self):
        self.write(T1, sample(T1).text() + '{"timestamp": "x", "type": "ev')
        self.assertEqual(self.extract(T1)["coverage"]["incomplete_tail"], 1)

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


def cmd(script, code, shell=("/bin/zsh", "-lc"), status=None, output="", oid="c1"):
    return {"type": "CommandExecution", "id": oid, "command": list(shell) + [script], "cwd": "C:\\w", "parsed_cmd": [],
            "source": "unified_exec_startup", "status": status or ("completed" if code == 0 else "failed"),
            "aggregated_output": output, "exit_code": code, "duration": {"secs": 38, "nanos": 400000000}}


def mcp(tool, server="ado", result=None, status="completed", read_only=None, oid="m1"):
    it = {"type": "McpToolCall", "id": oid, "server": server, "tool": tool, "arguments": {}, "status": status,
          "result": result if result is not None else {"content": [{"type": "text", "text": "ok"}]}}
    if read_only is not None:
        it["read_only_hint"] = read_only
    return it


class OperationTest(_Base):
    def op(self, it):
        return sr.operation(it, "2026-10-14T01:20:00.000Z", "t1")

    def test_command_status(self):
        cases = [
            (cmd("python -m pytest -q", 0), "ok"),
            (cmd('rg "testResultsFiles" -g "*.yml"', 1), "no_match"),
            (cmd("rg -n -i foo src", 1), "no_match"),
            (cmd("grep -rn foo .", 1), "no_match"),
            (cmd('findstr /s /i "foo" *.yml', 1, shell=("cmd.exe", "/c")), "no_match"),
            (cmd("rg -n foo | head", 1), "failed"),
            (cmd("rg foo && echo x", 1), "failed"),
            (cmd("rg --files-without-match foo", 1), "failed"),
            (cmd("grep foo x", 2), "failed"),
            (cmd("Select-String -Pattern foo x.txt", 1, shell=("pwsh", "-NoProfile", "-Command")), "failed"),
            (cmd('rg "--files-without-match" foo x', 1, shell=("pwsh", "-NoProfile", "-Command")), "failed"),
            (cmd('rg -n "foo bar" x', 1, shell=("pwsh", "-NoProfile", "-Command")), "no_match"),
            (cmd("python x.py", 1), "failed"),
            (cmd("git push", None, status="declined"), "declined"),
        ]
        for it, want in cases:
            with self.subTest(script=it["command"][-1]):
                self.assertEqual(self.op(it)["status"], want)
        o = self.op(cmd("python -m pytest -q", 0))
        self.assertEqual((o["route"], o["target"], o["duration_ms"], o["exit_code"]), ("command", "python -m pytest -q", 38400, 0))
        o = self.op(cmd("Get-ChildItem", 0, shell=("pwsh", "-NoProfile", "-Command")))
        self.assertEqual(o["target"], "Get-ChildItem")

    def test_external_purpose(self):
        ado = 'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\\Users\\me\\.agents\\skills\\ado\\scripts\\ado.ps1" '
        cases = [
            (cmd(ado + "get _apis/wit/workitems/1287", 0), ("ado.ps1 get", "参照")),
            (cmd(ado + "query _apis/wit/wiql -BodyFile q.json", 0), ("ado.ps1 query", "参照")),
            (cmd(ado + "write _apis/git/repositories/ci/pullrequests -Method POST -BodyFile b.json -DryRun", 0), ("ado.ps1 write", "参照")),
            (cmd(ado + "write _apis/git/repositories/ci/pullrequests -Method POST -BodyFile b.json -Approve abc", 0), ("ado.ps1 write", "作成")),
            (cmd(ado + "write _apis/wit/workitems/1287 -Method PATCH -BodyFile b.json -Approve abc", 0), ("ado.ps1 write", "更新")),
            (cmd(ado + "write _apis/wit/workitems/1287 -Method POST -BodyFile b.json -Approve abc", 0), ("ado.ps1 write", "不明")),
            (cmd(ado + "write _apis/wit/workitems/$Bug -Method POST -BodyFile b.json -Approve abc", 0), ("ado.ps1 write", "作成")),
            (cmd("az repos pr create --title x", 0), ("az repos pr create", "作成")),
            (cmd("az pipelines run --name python-test", 1), ("az pipelines run", "作成")),
            (cmd("az boards iteration team list", 0), ("az boards iteration team", "不明")),
            # MCP は確かめた組み合わせ（read_only_hint と MCP_PURPOSE の表）だけを分類する。名前の動詞からは推測しない
            (mcp("repo_create_pull_request"), ("ado.repo_create_pull_request", "不明")),
            (mcp("repo_get_or_create_pull_request"), ("ado.repo_get_or_create_pull_request", "不明")),
            (mcp("create_thing", server="shadow"), ("shadow.create_thing", "不明")),
            (mcp("fetch_file", server="github", read_only=True), ("github.fetch_file", "参照")),
            # 連結したコマンドは、どれか 1 つの目的に決めない
            (cmd(ado + "get _apis/wit/workitems/1; " + ado + "write _apis/wit/workitems/1 -Method PATCH -Approve a", 0), ("ado.ps1 get", "不明")),
            (cmd("az boards query --wiql x && az boards work-item delete --id 1", 0), ("az boards query", "不明")),
            # 値として渡した "-DryRun" はスイッチではない
            (cmd('.\\ado.ps1 write _apis/wit/workitems/1 -Method PATCH -BodyFile b.json -OutFile "-DryRun" -Approve abc', 0,
                 shell=("pwsh", "-NoProfile", "-Command")), ("ado.ps1 write", "更新")),
            (cmd("& 'C:\\x\\ado.ps1' get _apis/projects", 0, shell=("pwsh", "-NoProfile", "-Command")), ("ado.ps1 get", "参照")),
            # Windows の引用符で囲んだパス
            (cmd('powershell -File "C:\\Program Files\\ado.ps1" write _apis/wit/workitems/1 -Method PATCH -Approve a', 0,
                 shell=("pwsh", "-NoProfile", "-Command")), ("ado.ps1 write", "更新")),
            (mcp("create_issue", server="github"), ("github.create_issue", "不明")),
        ]
        for it, (tool, purpose) in cases:
            with self.subTest(tool=tool, target=str(it.get("command", ""))[-60:]):
                o = self.op(it)
                self.assertEqual((o["tool"], o["purpose"], o["external"]), (tool, purpose, True))
        self.assertEqual(self.op(cmd(ado + "write _apis/wit/workitems/1287 -Method PATCH", 0))["method"], "PATCH")
        self.assertFalse(self.op(cmd("python x.py", 0))["external"])
        # ado.ps1 を実行していないコマンドは、ADO の操作として分類しない
        self.assertFalse(self.op(cmd("git rm ado.ps1 get", 0))["external"])
        self.assertFalse(self.op(cmd("echo -File ado.ps1 get _apis/projects", 0))["external"])
        self.assertFalse(self.op(cmd("git diff -- . ado.ps1 get", 0))["external"])
        # 表に無い引数の名前（省略した名前など）があれば、目的は不明
        abbr = self.op(cmd('.\\ado.ps1 write _apis/wit/workitems/1 -Method PATCH -OutF "-DryRun" -Approve abc', 0,
                           shell=("pwsh", "-NoProfile", "-Command")))
        self.assertEqual(abbr["purpose"], "不明")
        for script in ("pwsh ./ado.ps1 get _apis/projects", "powershell -NoProfile -File ado.ps1 get _apis/projects"):
            with self.subTest(script=script):
                self.assertEqual(self.op(cmd(script, 0))["purpose"], "参照")
        self.assertEqual(self.op(mcp("x", result={"content": [], "isError": True}))["status"], "failed")

    def test_files_web_and_images(self):
        o = self.op({"type": "FileChange", "id": "f1", "status": "completed", "changes": {
            "C:\\w\\b.yml": {"type": "update", "unified_diff": "--- a\n+++ b\n@@\n-old\n+new\n+more\n ctx\n", "move_path": None},
            "C:\\w\\a.md": {"type": "add", "content": "1\n2\n3\n"},
            "C:\\w\\c.py": {"type": "delete"}}})
        self.assertEqual([(f["path"], f["kind"], f["added"], f["removed"]) for f in o["files"]],
                         [("C:\\w\\a.md", "add", 3, 0), ("C:\\w\\b.yml", "update", 2, 1), ("C:\\w\\c.py", "delete", None, None)])
        # 中身が -- や ++ で始まる変更の行も数える
        d = self.op({"type": "FileChange", "id": "f3", "status": "completed", "changes": {
            "x": {"type": "update", "unified_diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n---old\n+++new\n"}}})["files"][0]
        self.assertEqual((d["added"], d["removed"]), (1, 1))
        big = self.op({"type": "FileChange", "id": "f2", "status": "completed",
                       "changes": {"x": {"type": "update", "unified_diff": "+a\n" * 10000}}})["files"][0]
        self.assertTrue(big["diff_cut"])
        self.assertEqual((big["added"], big["diff_chars"]), (10000, 30000))
        self.assertLess(len(big["diff"]), 20100)
        w = self.op({"type": "WebSearch", "id": "w1", "query": "PublishTestResults default", "action": {"type": "search"}})
        self.assertEqual((w["route"], w["target"], w["purpose"]), ("web", "PublishTestResults default", "参照"))
        i = self.op({"type": "ImageView", "id": "i1", "path": "file:///C:/Users/me/shot.png"})
        self.assertEqual((i["route"], i["target"], i["body"]), ("image", "shot.png", False))

    def test_output_is_cut_and_redacted(self):
        out = "A" * 3000 + " password=Sup3rS3cretValue9 " + "Z" * 3000
        o = self.op(cmd("python x.py --password Sup3rS3cretValue9", 0, output=out))
        self.assertTrue(o["output_cut"])
        self.assertEqual(o["output_chars"], len(out))
        self.assertTrue(o["output"].startswith("A" * 2000) and o["output"].endswith("Z" * 2000))
        self.assertNotIn("Sup3rS3cretValue9", json.dumps(o))
        # 切れ目（先頭の 2,000 文字の終わり）をまたぐトークンも伏せる
        tok = "ghp_" + "A1b2C3d4E5" * 3 + "f6g7h8"
        across = self.op(cmd("x", 0, output="A" * 1990 + " " + tok + " " + "Z" * 5000))
        self.assertNotIn(tok[:9], across["output"])
        # 長い出力（余白より長い）でも、残す部分の切れ目をまたぐトークンを伏せる
        long = self.op(cmd("x", 0, output="A" * 1990 + " " + tok + " " + "M" * 100000 + " " + tok + " " + "Z" * 1990))
        self.assertNotIn(tok[:9], long["output"])
        self.assertEqual(long["output_chars"], 1990 * 2 + 100000 + 2 * len(tok) + 4)
        # 対象のコマンドの文も、伏せ字をかけてから切る
        url = self.op(cmd("echo " + "x " * 185 + " https://user:shortsecret@host/", 0))
        self.assertNotIn("shortsecret", json.dumps(url))
        web = self.op({"type": "WebSearch", "id": "w9", "query": "q " * 195 + " https://user:shortsecret@host/", "action": {}})
        self.assertNotIn("shortsecret", json.dumps(web))
        # 余白より長い語の途中で切るときは、その語を捨てる
        # 空白の無い長い出力（1 行の JSON など）は、伏せ字が全体を見ていれば先頭も残す
        blob = self.op(cmd("x", 0, output="{" + '"k":"v",' * 3000 + "}"))
        self.assertTrue(blob["output"].startswith('{"k":"v"'))
        huge = "https://user:shortsecret" + "x" * 21000 + "@host/"
        self.assertNotIn("shortsecret", json.dumps(self.op(cmd("echo " + huge, 0))))
        self.assertNotIn("shortsecret", json.dumps(self.op(cmd("x", 0, output="A " * 900 + huge + " Z" * 30000))))
        self.assertNotIn("shortsecret", json.dumps(self.op(cmd("x", 0, output="A " * 30000 + huge + " Z" * 900))))
        # 伏せ字で文字列が縮んでも、範囲の端をまたぐ語の切れ端は捨てる
        shrink = "password=" + "a" * 20200 + " https://user:shortsecret" + "b" * 21000 + "@host/"
        self.assertNotIn("shortsecret", sr._short(shrink))
        self.assertNotIn("shortsecret", json.dumps(self.op(cmd("echo " + shrink, 0))))
        tail = "https://user:shortsecret" + "b" * 21000 + "@host/ " + "password=" + "a" * 20200
        self.assertNotIn("shortsecret", json.dumps(self.op(cmd("x", 0, output="A " * 30000 + tail))))
        short = self.op(cmd("echo", 0, output="token=Sup3rS3cretValue9"))
        self.assertNotIn("Sup3rS3cretValue9", json.dumps(short))

    def test_extract_operations_and_artifacts(self):
        r = Rollout(T1).turn("t1")
        r.user("u1", "PR を作って")
        r.item(cmd("python -m pytest -q", 1, oid="c1"))
        r.item(cmd("python -m pytest -q", 0, oid="c1"))  # 同じ ID の項目は最後のもの
        pr = {"pullRequestId": 482, "title": "テスト結果を Tests タブに出す", "status": "active"}
        r.item(mcp("repo_create_pull_request", result={"content": [{"type": "text", "text": json.dumps(pr, ensure_ascii=False)}]}))
        wi = {"id": 1287, "fields": {"System.WorkItemType": "Bug", "System.Title": "CI のテスト結果が見えない", "System.State": "Active"}}
        r.item(cmd("ado.ps1 get _apis/wit/workitems/1287", 0, oid="c2", output=json.dumps(wi, ensure_ascii=False)))
        r.item(cmd("git push", 0, oid="c3", output="remote: https://tfs/x/_git/ci/pullrequest/482"))
        r.add("event_msg", {"type": "exec_command_end", "call_id": "old"})
        r.agent("a1", "作りました").end("作りました")
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertEqual([(o["id"], o["route"], o["status"]) for o in rep["operations"]],
                         [("c1", "command", "ok"), ("m1", "mcp", "ok"), ("c2", "command", "ok"), ("c3", "command", "ok")])
        self.assertEqual([(a["kind"], a["id"], a["title"], a["status"], a["operations"]) for a in rep["artifacts"]],
                         [("PR", 482, "テスト結果を Tests タブに出す", "active", ["m1", "c3"]),
                          ("Work Item", 1287, "CI のテスト結果が見えない", "Active", ["c2"])])
        self.assertEqual(rep["coverage"]["legacy_operations"], {"exec_command_end": 1})
        self.assertEqual(rep["operations"][0]["turn_id"], "t1")


class _ReportBase(_Base):
    """作り物の会話を extract した report.json のフォルダを作る土台（テストは持たない）。"""

    def make(self):
        r = Rollout(T1).turn("t1")
        r.user("u1", "Tests タブに何も出ない", [{"type": "local_image", "path": "C:\\a\\tab.png"}])
        r.item({"type": "Reasoning", "id": "r1", "summary": [], "content": [{"type": "text", "text": "推論の中身 %s" % CANARY}]})
        r.item(cmd('rg "testResultsFiles" -g "*.yml"', 1, oid="c1"))
        r.item(cmd("python -m pytest -q", 1, oid="c2", output="....\nFAILED tests/test_retry.py::test_x - AssertionError\n"))
        r.agent("a1", "置き場所がずれていました").end("置き場所がずれていました")
        r.turn("t2").user("u2", "PR を作って").agent("a2", "作りました").end("作りました")
        self.write(T1, r.text())
        self.extract(T1)
        return os.path.join(self.tmp.name, "out")

    def write_ann(self, out, ann):
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump(ann, f, ensure_ascii=False)


class InputTest(_ReportBase):
    def test_input_orders_messages_and_operations(self):
        out = self.make()
        code, stdout, err = self.run_cli("input", "--dir", out)
        self.assertEqual(code, 0, err)
        with open(os.path.join(out, "input-1.md"), encoding="utf-8") as f:
            text = f.read()
        order = [text.index(s) for s in ("[u1]", "[c1]", "[c2]", "[a1]", "[u2]", "[a2]")]
        self.assertEqual(order, sorted(order))
        self.assertIn("（添付: tab.png、本体なし）", text)
        self.assertIn("[c1] 10:15 コマンド 該当なし", text)
        self.assertIn("→ FAILED tests/test_retry.py::test_x - AssertionError", text)
        self.assertIn("終了コード 1", text)
        self.assertNotIn(CANARY, text)  # 推論は入れない
        self.assertIn("持ち主の発言 2 件（この範囲 2 件）", text)

    def test_parts_split_at_entry_boundaries(self):
        entries = [("### [u1] 持ち主\n" + "a" * 50, True), ("- op\n", False), ("### [a1] Codex\n" + "b" * 50, False),
                   ("### [u2] 持ち主\n" + "c" * 50, True), ("### [a2] Codex\n" + "d" * 200, False)]
        parts = sr.split_parts(entries, limit=100)
        self.assertEqual([[e.split("\n")[0] for e in p] for p in parts],
                         [["### [u1] 持ち主", "- op"], ["### [a1] Codex"], ["### [u2] 持ち主"], ["### [a2] Codex"]])

    def test_parts_count_the_joining_newlines(self):
        entries = [("- op\n", False)] * 100
        for p in sr.split_parts(entries, limit=60):
            self.assertLessEqual(len("\n".join(p)), 60)

    def test_parts_keep_the_limit_without_owner_messages(self):
        entries = [("### [u1] 持ち主\n" + "a" * 10, True)] + [("- op %d\n" % n + "x" * 30, False) for n in range(10)]
        parts = sr.split_parts(entries, limit=100)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(sum(len(e) for e in p), 100)

    def test_later_parts_list_pending_items(self):
        out = self.make()
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump({"requests": [
                {"id": "R1", "text": "原因を直す", "lifecycle": "有効", "criteria": [{"status": "できた"}, {"status": "未確認"}]},
                {"id": "R2", "text": "済んだもの", "lifecycle": "有効", "criteria": [{"status": "できた"}]},
                {"id": "R3", "text": "取り下げたもの", "lifecycle": "取り下げ", "criteria": []}],
                "questions": [{"id": "Q1", "text": "再実行すべき？", "status": "未回答"}, {"id": "Q2", "text": "x", "status": "答えた"}]},
                f, ensure_ascii=False)
        report = sr.read_json(os.path.join(out, "report.json"))
        self.assertGreater(len(sr.split_parts(sr.input_entries(report), limit=10)), 1)
        self.assertEqual(sr.pending_items(sr.read_json(os.path.join(out, "annotations.json"))),
                         ["- R1（一部）: 原因を直す", "- Q1（未回答）: 再実行すべき？"])
        code, _, err = self.run_cli("input", "--dir", out, "--part", "2")
        self.assertEqual(code, 1)
        self.assertIn("範囲は 1〜1", err)

    def test_pending_items_are_redacted_and_parts_keep_the_limit(self):
        tok = "ghp_" + "A1b2C3d4E5" * 3 + "f6g7h8"
        self.assertNotIn(tok, "\n".join(sr.pending_items({"requests": [
            {"id": "R1", "text": "使う " + tok, "lifecycle": "有効", "criteria": []}]})))
        # 長い行の切れ目をまたぐトークンも伏せる
        self.assertNotIn(tok[:12], "\n".join(sr.pending_items({"requests": [
            {"id": "R1", "text": "x" * 270 + " " + tok, "lifecycle": "有効", "criteria": []}]})))
        r = Rollout(T1).turn("t1")
        r.user("u1", "a" * 50000).agent("a1", "b" * 50000).user("u2", "c" * 50000).end("x")
        self.write(T1, r.text())
        self.extract(T1)
        out = os.path.join(self.tmp.name, "out")
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump({"requests": [{"id": "R%d" % n, "text": "長い依頼 " + "z" * 300, "lifecycle": "有効", "criteria": []}
                                    for n in range(100)]}, f, ensure_ascii=False)
        n = 1
        while self.run_cli("input", "--dir", out, "--part", str(n))[0] == 0:
            with open(os.path.join(out, "input-%d.md" % n), encoding="utf-8") as f:
                self.assertLessEqual(len(f.read()), sr.INPUT_LIMIT)
            n += 1
        self.assertGreater(n, 2)

    def test_repeated_operation_keeps_its_place(self):
        r = Rollout(T1).turn("t1").user("u1", "a")
        r.item(cmd("python x.py", 1, oid="c1"))
        r.agent("a1", "直します", phase="commentary")
        r.item(cmd("python x.py", 0, oid="c1"))  # 同じ ID の完了の記録がもう一度
        r.agent("a2", "直りました").end("直りました")
        self.write(T1, r.text())
        rep = self.extract(T1)
        self.assertEqual(rep["operations"][0]["status"], "ok")
        text = "".join(e for e, _ in sr.input_entries(rep))
        self.assertLess(text.index("[c1]"), text.index("[a1]"))

    def test_request_status(self):
        cases = [([], "未確認"), (["できた", "できた"], "できた"), (["できた", "未確認"], "一部"), (["一部"], "一部"),
                 (["できていない", "未確認"], "できていない"), (["未確認"], "未確認")]
        for states, want in cases:
            with self.subTest(states=states):
                self.assertEqual(sr.request_status({"criteria": [{"status": s} for s in states]}), want)
        self.assertIsNone(sr.request_status({"lifecycle": "取り下げ", "criteria": [{"status": "できた"}]}))

    def test_show_op(self):
        out = self.make()
        code, stdout, err = self.run_cli("show-op", "c2", "--dir", out)
        self.assertEqual(code, 0, err)
        self.assertIn("FAILED tests/test_retry.py::test_x", stdout)
        code, _, err = self.run_cli("show-op", "nope", "--dir", out)
        self.assertEqual((code, "操作が無い: nope" in err), (1, True))


def good_annotations(report):
    ev = lambda ref, quote: [{"ref": ref, "quote": quote}]
    return {
        "format_version": "1", "source_digest": report["digest"],
        "summary": {"text": "置き場所のずれを直した", "evidence": ev("a1", "置き場所がずれていました")},
        "chapters": [],
        "requests": [{"id": "R1", "text": "Tests タブに出す", "at": "u1", "lifecycle": "有効", "evidence": ev("u1", "Tests タブに何も出ない"),
                      "criteria": [{"text": "原因が分かる", "origin": "持ち主", "status": "できた", "basis": "回答で説明",
                                    "evidence": ev("a1", "ずれていました")},
                                   {"text": "CI で確かめる", "origin": "Codex", "status": "未確認", "basis": "記録なし", "evidence": []}]},
                     {"id": "R2", "text": "PR を作る", "at": "u2", "lifecycle": "有効", "evidence": ev("u2", "PR を作って"),
                      "criteria": [{"text": "PR がある", "origin": "持ち主", "status": "できた", "basis": "最終の回答での報告のみ",
                                    "evidence": ev("a2", "作りました")}]}],
        "questions": [],
        "decisions": [{"id": "D1", "text": "置き場所をそろえる", "at": "a1", "by": "Codex の提案のまま", "why": "既定の探し場所",
                       "alternatives": [], "supersedes": None, "evidence": ev("a1", "置き場所")}],
        "lessons": [{"id": "L1", "text": "置き場所を先に見る", "evidence": ev("c2", "FAILED tests/test_retry.py")}],
        "issues": [{"id": "I1", "kind": "未解決", "text": "test_retry が落ちる", "evidence": ev("c2", "AssertionError")}],
        "command_notes": [{"id": "N1", "failed": "c2", "resolved_by": None, "text": "未解決"}],
        "owner_messages": {"u1": ["R1"], "u2": ["R2"]},
    }


class CheckTest(_ReportBase):
    def check(self, mutate=None):
        out = self.make()
        report = sr.read_json(os.path.join(out, "report.json"))
        ann = good_annotations(report)
        if mutate:
            mutate(ann)
        self.write_ann(out, ann)
        code, stdout, err = self.run_cli("check", "--dir", out)
        return code, stdout

    def test_good_annotations_pass(self):
        code, stdout = self.check()
        self.assertEqual(code, 0, stdout)
        self.assertIn("通った: 依頼 2", stdout)

    def test_required_failures(self):
        def quote(a):
            a["decisions"][0]["evidence"][0]["quote"] = "本文に無い引用"

        def ref(a):
            a["lessons"][0]["evidence"][0]["ref"] = "c99"

        def owner(a):
            del a["owner_messages"]["u2"]

        def digest(a):
            a["source_digest"] = "0" * 64
        cases = [(quote, "D1: 引用が a1 の本文に無い"), (ref, "L1: 根拠の ref c99 が report.json に無い"),
                 (owner, "u2: 持ち主の発言の扱いが owner_messages に無い"), (digest, "source_digest: report.json の digest")]
        for mutate, want in cases:
            with self.subTest(want=want):
                code, stdout = self.check(mutate)
                self.assertEqual(code, 1)
                self.assertIn(want, stdout)

    def test_other_failures(self):
        def dup(a):
            a["lessons"].append(dict(a["lessons"][0]))

        def schema(a):
            a["requests"][0]["lifecycle"] = "途中"
            a["issues"][0]["extra"] = 1

        def answer(a):
            a["questions"].append({"id": "Q1", "text": "?", "at": "u1", "status": "答えた", "answer": None, "note": "",
                                   "evidence": [{"ref": "u1", "quote": "Tests"}]})

        def at(a):
            a["requests"][0]["at"] = "a1"

        def label(a):
            a["owner_messages"]["u1"] = ["R9"]
            a["owner_messages"]["a1"] = ["了承のみ"]

        def criteria(a):
            a["requests"][1]["criteria"][0]["evidence"] = []

        def squash(a):
            a["summary"]["evidence"][0]["quote"] = "置き場所が  \n ずれていました"  # 空白の有無は問わない
        def owner_answer(a):
            a["questions"].append({"id": "Q1", "text": "?", "at": "u1", "status": "答えた", "answer": "u2", "note": "",
                                   "evidence": [{"ref": "u1", "quote": "Tests"}]})
        cases = [(dup, ["L1: ID が重複"]), (schema, ["R1（annotations.requests[0]）.lifecycle: 値が", "I1（annotations.issues[0]）: 知らないキー extra"]),
                 (owner_answer, ["Q1: answer の u2 が Codex の発言に無い"]),
                 (answer, ["Q1: 答えた発言（answer）が無い"]), (at, ["R1: at の a1 が持ち主の発言に無い"]),
                 (label, ["u1: owner_messages の R9", "a1: owner_messages のキーが持ち主の発言に無い"]),
                 (criteria, ["R2.criteria[0]: 根拠（evidence）が無い"])]
        for mutate, wants in cases:
            with self.subTest(wants=wants):
                code, stdout = self.check(mutate)
                self.assertEqual(code, 1)
                for want in wants:
                    self.assertIn(want, stdout)
        self.assertEqual(self.check(squash)[0], 0)

    def test_broken_or_missing_file(self):
        out = self.make()
        code, _, err = self.run_cli("check", "--dir", out)
        self.assertEqual(code, 1)
        self.assertIn("annotations.json が無い", err)
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        code, _, err = self.run_cli("check", "--dir", out)
        self.assertIn("JSON として読めない", err)


class OverrideTest(_ReportBase):
    def setup_ann(self):
        out = self.make()
        self.write_ann(out, good_annotations(sr.read_json(os.path.join(out, "report.json"))))
        return out

    def test_override_and_clear(self):
        out = self.setup_ann()
        code, _, err = self.run_cli("override", "R1.2", "--dir", out, "--status", "できた", "--reason", "CI の結果を持ち主が見た")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("override", "R2", "--dir", out, "--status", "未確認", "--reason", "PR を見ていない")
        self.assertEqual(code, 0, err)
        ov = sr.read_json(os.path.join(out, "overrides.json"))
        self.assertEqual(ov["R1.2"], {"status": "できた", "reason": "CI の結果を持ち主が見た", "by": "持ち主", "original": "未確認",
                                      "target": "CI で確かめる"})
        eff = sr.effective_statuses(sr.read_json(os.path.join(out, "annotations.json")), ov)
        self.assertEqual(eff["R2"], {"status": "未確認", "original": "できた", "reason": "PR を見ていない", "overridden": True})
        self.assertEqual(eff["R1"]["status"], "一部")  # 依頼の状態は Codex の条件の判定から集める（訂正した条件は R1 に効かない）
        code, _, _ = self.run_cli("override", "R2", "--dir", out, "--clear")
        self.assertEqual(code, 0)
        self.assertNotIn("R2", sr.read_json(os.path.join(out, "overrides.json")))

    def test_old_override_is_not_applied_to_a_changed_item(self):
        out = self.setup_ann()
        self.run_cli("override", "R1.2", "--dir", out, "--status", "できた", "--reason", "CI を見た")
        ann = sr.read_json(os.path.join(out, "annotations.json"))
        ov = sr.read_json(os.path.join(out, "overrides.json"))
        # 追記を作り直して、条件の並びが変わった（R1.2 が別の条件になった）
        ann["requests"][0]["criteria"].insert(0, {"text": "新しい条件", "origin": "Codex", "status": "未確認", "basis": "", "evidence": []})
        eff = sr.effective_statuses(ann, ov)
        self.assertEqual((eff["R1.2"]["status"], eff["R1.2"]["overridden"]), ("できた", False))
        self.assertEqual(sr.stale_overrides(ann, ov), ["R1.2"])
        # 同じ条件で Codex の判定だけが変わったときは、訂正したときの元の判定を出す
        ann = sr.read_json(os.path.join(out, "annotations.json"))
        ann["requests"][0]["criteria"][1]["status"] = "一部"
        eff = sr.effective_statuses(ann, ov)
        self.assertEqual((eff["R1.2"]["status"], eff["R1.2"]["original"]), ("できた", "未確認"))

    def test_override_rejects_bad_input(self):
        out = self.setup_ann()
        cases = [(["R9", "--status", "できた", "--reason", "x"], 1, "直せる項目に無い: R9"),
                 (["R1", "--status", "答えた", "--reason", "x"], 2, "R1 の状態は"),
                 (["R1", "--status", "できた"], 2, "--reason に、直す理由"),
                 (["R1", "--status", "できた", "--reason", "  "], 2, "--reason に、直す理由")]
        for argv, want_code, want in cases:
            with self.subTest(argv=argv):
                code, _, err = self.run_cli("override", *argv[:1], "--dir", out, *argv[1:])
                self.assertEqual(code, want_code)
                self.assertIn(want, err)
        self.assertFalse(os.path.exists(os.path.join(out, "overrides.json")))


class RenderTest(_ReportBase):
    def render(self, mutate=None, overrides=None, *args):
        out = self.make()
        ann = good_annotations(sr.read_json(os.path.join(out, "report.json")))
        if mutate:
            mutate(ann)
        self.write_ann(out, ann)
        if overrides is not None:
            with open(os.path.join(out, "overrides.json"), "w", encoding="utf-8") as f:
                json.dump(overrides, f, ensure_ascii=False)
        code, stdout, err = self.run_cli("render", "--dir", out, *args)
        path = os.path.join(out, "report.html")
        html = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        return code, stdout + err, html

    def test_html_is_self_contained(self):
        code, text, html = self.render()
        self.assertEqual(code, 0, text)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("default-src 'none'", html)
        self.assertNotRegex(html, r"(?i)(src|href)\s*=\s*[\"']?(https?:|//)")
        self.assertNotRegex(html, r"(?i)url\(\s*[\"']?(https?:|//)")
        self.assertNotRegex(html, r"(?i)@import|<link\b|<iframe\b|<img\b")
        self.assertNotIn("__SESSION_REPORT_DATA__", html)
        self.assertNotIn("innerHTML", html)  # 記録の文字列は文字のノードとして入れる

    def test_record_text_is_escaped(self):
        nasty = '</script><img src=x onerror="alert(1)">& \u2028'

        def mutate(a):
            a["summary"]["text"] = nasty
        code, text, html = self.render(mutate)
        self.assertEqual(code, 0, text)
        data = html.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("<", data)
        self.assertNotIn(">", data)
        self.assertNotIn("&", data)
        self.assertNotIn("\u2028", data)
        self.assertIn(nasty, json.loads(data)["summary"])
        self.assertEqual(html.count("</script>"), 2)

    def test_check_failure_writes_nothing(self):
        def mutate(a):
            a["lessons"][0]["evidence"][0]["ref"] = "c99"
        code, text, html = self.render(mutate)
        self.assertEqual(code, 1)
        self.assertIn("check が通らないので HTML を書かない", text)
        self.assertIsNone(html)

    def test_stale_ok_and_overrides(self):
        def stale(a):
            a["source_digest"] = "0" * 64
        code, text, html = self.render(stale)
        self.assertEqual((code, html), (1, None))
        code, text, html = self.render(stale, None, "--stale-ok")
        self.assertEqual(code, 0, text)
        view = json.loads(html.split('id="data">', 1)[1].split("</script>", 1)[0])
        self.assertIn("render --stale-ok", view["notices"][0])
        ov = {"R2": {"status": "未確認", "reason": "PR を見ていない", "by": "持ち主", "original": "できた", "target": "PR を作る"},
              "R1.1": {"status": "できていない", "reason": "x", "by": "持ち主", "original": "できた", "target": "別の条件"}}
        code, text, html = self.render(None, ov)
        view = json.loads(html.split('id="data">', 1)[1].split("</script>", 1)[0])
        r2 = next(r for r in view["requests"] if r["id"] == "R2")
        self.assertEqual(r2["status"], {"status": "未確認", "original": "できた", "reason": "PR を見ていない", "overridden": True})
        self.assertTrue(any("R1.1" in n for n in view["notices"]))

    def test_view_contents(self):
        code, text, html = self.render()
        view = json.loads(html.split('id="data">', 1)[1].split("</script>", 1)[0])
        self.assertEqual([t[0] for t in view["tabs"]], ["overview", "chat", "requests", "decisions", "ops", "issues"])
        self.assertEqual(view["meta"]["span"], "2026-10-14 10:12 → 10:20")
        self.assertEqual([c["kind"] for c in view["chat"]], ["me", "tool", "ai", "me", "ai"])
        self.assertEqual(view["chat"][0]["attachments"], ["tab.png"])
        self.assertEqual(view["ops"]["command_totals"], [["失敗", 1], ["該当なし", 1]])
        self.assertEqual(view["requests"][0]["status"]["status"], "一部")
        self.assertEqual([m[0] for m in view["flow"]["marks"]], ["依頼", "決定", "失敗"])


class ViewDetailTest(_Base):
    def test_files_decisions(self):
        r = Rollout(T1).turn("t1").user("u1", "直して")
        diff = lambda a, b: "--- a\n+++ b\n@@\n-%s\n+%s\n" % (a, b)
        r.item({"type": "FileChange", "id": "f1", "status": "completed", "changes": {"C:\\w\\a.py": {"type": "update", "unified_diff": diff("x", "y")}}})
        r.item({"type": "FileChange", "id": "f2", "status": "completed", "changes": {"C:\\w\\a.py": {"type": "update", "unified_diff": diff("y", "z")}}})
        r.item({"type": "FileChange", "id": "f3", "status": "declined", "changes": {"C:\\w\\b.py": {"type": "update", "unified_diff": diff("p", "q")}}})
        r.agent("a1", "方針 A にします。やはり方針 B にします。").end("x")
        self.write(T1, r.text())
        report = self.extract(T1)
        ev = [{"ref": "a1", "quote": "方針"}]
        ann = {"format_version": "1", "source_digest": report["digest"], "summary": {"text": "s", "evidence": ev}, "chapters": [],
               "requests": [], "questions": [], "lessons": [], "issues": [], "command_notes": [], "owner_messages": {"u1": ["了承のみ"]},
               "decisions": [{"id": "D1", "text": "方針 A", "at": "a1", "by": "保留", "why": "", "alternatives": [], "supersedes": None, "evidence": ev},
                             {"id": "D2", "text": "方針 B", "at": "a1", "by": "Codex の提案のまま", "why": "", "alternatives": [],
                              "supersedes": "D1", "evidence": ev}]}
        self.assertEqual(sr.check_annotations(ann, report), [])
        view = sr.build_view(report, ann, {})
        files = {f["path"]: f for f in view["ops"]["files"]}
        self.assertEqual((files["C:\\w\\a.py"]["plus"], files["C:\\w\\a.py"]["minus"]), ("+2", "−2"))
        self.assertIn("+y", files["C:\\w\\a.py"]["diff"])
        self.assertIn("+z", files["C:\\w\\a.py"]["diff"])
        self.assertEqual((files["C:\\w\\b.py"]["kind"], files["C:\\w\\b.py"]["plus"]), ("適用されなかった変更", ""))
        d1 = next(d for d in view["decisions"] if d["id"] == "D1")
        self.assertEqual(d1["replaced_by"], "D2")


class ShareTest(_Base):
    def test_share_html_leaves_out_details(self):
        r = Rollout(T1, cwd="C:\\work\\CANARYCWD\\ci-pipelines").turn("t1")
        r.user("u1", "Tests タブに何も出ない。CANARYMSG の件も見て")
        r.item(cmd("python CANARYCMD.py", 1, oid="c1", output="CANARYOUT failed"))
        r.item({"type": "FileChange", "id": "f1", "status": "completed", "changes": {
            "C:\\Users\\me\\CANARYPATH\\x.py": {"type": "update", "unified_diff": "--- a\n+++ b\n@@\n-a\n+CANARYDIFF\n"}}})
        r.agent("a1", "置き場所がずれていました。詳しくは https://tfs.example/CANARYURL/x を見てください").end("x")
        self.write(T1, r.text())
        report = self.extract(T1)
        out = os.path.join(self.tmp.name, "out")
        ann = {"format_version": "1", "source_digest": report["digest"],
               "summary": {"text": "置き場所を直した。",
                           "evidence": [{"ref": "a1", "quote": "置き場所がずれていました"}]},
               "chapters": [], "questions": [], "lessons": [], "command_notes": [],
               "requests": [{"id": "R1", "text": "Tests タブに出す", "at": "u1", "lifecycle": "有効", "criteria": [],
                             "evidence": [{"ref": "u1", "quote": "Tests タブに何も出ない"}]}],
               "decisions": [{"id": "D1", "text": "置き場所をそろえる", "at": "a1", "by": "Codex の提案のまま", "why": "",
                              "alternatives": [], "supersedes": None, "evidence": [{"ref": "a1", "quote": "置き場所がずれていました"}]}],
               "issues": [{"id": "I1", "kind": "未解決", "text": "CANARYCMD.py が落ちる", "evidence": [{"ref": "c1", "quote": "CANARYOUT"}]}],
               "owner_messages": {"u1": ["R1"]}}
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump(ann, f, ensure_ascii=False)
        # 追記の文に URL や絶対パスがあれば、共有用は書かずに止まる（Codex が書き直す）
        ann_risky = json.loads(json.dumps(ann))
        ann_risky["summary"]["text"] = "置き場所を直した。https://tfs.example/CANARYURL2 と C:\\Users\\me\\CANARYPATH2\\y.py"
        ann_risky["decisions"][0]["why"] = "場所は/Users/CANARYPARENT/x.py"
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump(ann_risky, f, ensure_ascii=False)
        code, stdout, err = self.run_cli("render", "--dir", out, "--share")
        self.assertEqual(code, 1)
        self.assertIn("summary: 共有用の文に URL か絶対パスがある", stdout)
        self.assertIn("D1.why: 共有用の文に URL か絶対パスがある", stdout)
        self.assertFalse(os.path.exists(os.path.join(out, "report-share.html")))
        # 記録から来る引用に URL があれば、引用を丸ごと省く
        ann["decisions"][0]["evidence"] = [{"ref": "a1", "quote": "https://tfs.example/CANARYURL/x"}]
        with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
            json.dump(ann, f, ensure_ascii=False)
        code, stdout, err = self.run_cli("render", "--dir", out, "--share")
        self.assertEqual(code, 0, stdout + err)
        html = open(os.path.join(out, "report-share.html"), encoding="utf-8").read()
        for canary in ("CANARYMSG", "CANARYOUT", "CANARYDIFF", "CANARYPATH", "CANARYURL", "CANARYCWD", "tfs.example", "C:\\\\"):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, html)
        view = json.loads(html.split('id="data">', 1)[1].split("</script>", 1)[0])
        self.assertTrue(view["share"])
        self.assertIsNone(view["chat"])
        self.assertIn("共有してよくない内容が残っていないかは、持ち主が確かめる", view["notices"][0])
        self.assertEqual(view["meta"]["cwd"], "ci-pipelines")
        self.assertEqual(view["meta"]["title"], "Tests タブに出す")
        self.assertEqual(view["summary"], "置き場所を直した。")
        self.assertEqual(view["decisions"][0]["quote"], "（パスか URL を含むため省略）")
        self.assertEqual([m[0] for m in view["flow"]["marks"]], ["依頼", "決定", "失敗"])
        self.assertEqual(view["flow"]["marks"][2][1][0]["label"], "コマンドの失敗")
        # 課題の本文（持ち主が読むために Codex が書いた要点）は残す。コマンドの名前が入っていても消さない
        self.assertEqual(view["issues"][0]["text"], "CANARYCMD.py が落ちる")
        # 手元用には詳細が残る
        self.run_cli("render", "--dir", out)
        local = open(os.path.join(out, "report.html"), encoding="utf-8").read()
        self.assertIn("CANARYOUT", local)
        self.assertIn("CANARYMSG", local)


class ShareRiskTest(unittest.TestCase):
    RISKY = ["/workspace/CANARYPARENT/x.py", "C:/Users/CANARYPARENT/x.py", "//server/CANARYSHARE/x.py",
             '"C:\\Users\\Jane Doe\\CANARYPARENT\\x.py"', '"/Users/Jane Doe/CANARYPARENT/x.py"', "参照https://example.test/CANARYURL",
             "場所は/Users/CANARYPARENT/x.py", "確認(https://example.test/x)してから", "\\\\server\\share\\a.txt", "~/CANARY/x",
             "www.example.test/CANARY", "file:///C:/CANARY", "C:\\work\\CANARY"]
    PLAIN = ["tests/test_retry.py は相対", "ratio 1/2 と CI/CD", "src\\clock.py を直した", "テスト/本番の両方", "and/or",
             "_apis/wit/workitems/1287 を PATCH"]

    def test_risky_strings_are_found(self):
        for s in self.RISKY:
            with self.subTest(s=s):
                self.assertTrue(sr.share_risk(s))
        for s in self.PLAIN:
            with self.subTest(s=s):
                self.assertFalse(sr.share_risk(s))


class FixturesTest(unittest.TestCase):
    def test_gold_annotations_pass_check(self):
        # 作り物の会話 6 本の正解の追記が、会話とずれていない（evals/session-report/fixtures.py）
        r = subprocess.run([sys.executable, os.path.join(ROOT, "evals", "session-report", "fixtures.py"), "check"],
                           capture_output=True, encoding="utf-8", env=dict(os.environ, TZ="Asia/Tokyo"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("違反 0 件"), 6)

    def test_same_record_gives_same_html(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "evals", "session-report", "fixtures.py"), "render", "--twice"],
                           capture_output=True, encoding="utf-8", env=dict(os.environ, TZ="Asia/Tokyo"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("同じバイト列: はい"), 12)  # 6 本の手元用と共有用


class SearchModuleTest(unittest.TestCase):
    def test_uses_session_search(self):
        # 写さずに import している（伏せ字の規則が 2 つに分かれない）
        self.assertTrue(sr.ss.__file__.endswith(os.path.join("session-search", "scripts", "session_search.py")))


if __name__ == "__main__":
    unittest.main()
