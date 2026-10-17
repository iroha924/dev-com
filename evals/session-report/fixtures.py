"""session-report の作り物の会話 6 本と、その正解の追記で、extract・check・render を通す。

    python evals/session-report/fixtures.py check            # 6 本とも、正解の追記が check を通るか
    python evals/session-report/fixtures.py build <フォルダ>  # 6 本の report.json と正解の追記をフォルダに書く
    python evals/session-report/fixtures.py week [--twice] [--out フォルダ]  # 6 本を 1 週間として週の材料・確かめ・HTML を通す
    python evals/session-report/fixtures.py render [--twice] [--out フォルダ]  # 6 本の HTML を書く（--twice は 2 回書いて比べる）

会話は持ち主の実際の記録ではなく、ここで組み立てる。正解の追記（gold/<名前>.json）は、会話の全文を読んで書いたもの。
source_digest は作るたびに変わる（記録のパスが入る）ので、gold には "<digest>" と書き、build で埋める。
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "skills", "session-report", "scripts", "session_report.py")
GOLD = os.path.join(HERE, "gold")
CWD = "C:\\work\\ci-pipelines"
ADO = 'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\\Users\\me\\.agents\\skills\\ado\\scripts\\ado.ps1" '


class Rollout:
    """作り物の会話を 1 行ずつ組み立てる。行ごとに時刻を進める。"""

    def __init__(self, tid, start, branch="fix/test-results"):
        self.tid = tid
        self.now = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
        self.lines = []
        self.turn_id = None
        self.n = 0
        self.add("session_meta", {"id": tid, "cwd": CWD, "originator": "codex_cli_rs", "cli_version": "0.162.1",
                                  "git": {"branch": branch}, "source": "cli"})

    def add(self, typ, payload, minutes=1):
        self.lines.append(json.dumps({"timestamp": self.now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "type": typ,
                                      "payload": payload}, ensure_ascii=False))
        self.now += datetime.timedelta(minutes=minutes)
        return self

    def turn(self, model="gpt-6.1-sol", effort="medium"):
        self.n += 1
        self.turn_id = "t%d" % self.n
        self.add("turn_context", {"turn_id": self.turn_id, "cwd": CWD, "model": model, "effort": effort}, 0)
        return self.add("event_msg", {"type": "task_started", "turn_id": self.turn_id}, 0)

    def item(self, it, minutes=1):
        return self.add("event_msg", {"type": "item_completed", "turn_id": self.turn_id, "item": it}, minutes)

    def user(self, mid, text):
        return self.item({"type": "UserMessage", "id": mid, "content": [{"type": "text", "text": text}]})

    def say(self, mid, text, final=False):
        return self.item({"type": "AgentMessage", "id": mid, "phase": "final_answer" if final else "commentary",
                          "content": [{"type": "Text", "text": text}]})

    def cmd(self, oid, script, code, output="", minutes=1):
        return self.item({"type": "CommandExecution", "id": oid, "command": ["pwsh", "-NoProfile", "-Command", script],
                          "cwd": CWD, "parsed_cmd": [], "source": "unified_exec_startup",
                          "status": "completed" if code == 0 else "failed", "aggregated_output": output, "exit_code": code,
                          "duration": {"secs": 3, "nanos": 0}}, minutes)

    def patch(self, oid, path, diff):
        return self.item({"type": "FileChange", "id": oid, "status": "completed",
                          "changes": {path: {"type": "update", "unified_diff": diff, "move_path": None}}})

    def end(self, last=None, aborted=False):
        if aborted:
            return self.add("event_msg", {"type": "turn_aborted", "turn_id": self.turn_id, "reason": "interrupted"}, 0)
        return self.add("event_msg", {"type": "task_complete", "turn_id": self.turn_id, "last_agent_message": last}, 0)

    def text(self):
        return "\n".join(self.lines) + "\n"


def short():
    r = Rollout("10000000-0000-4000-8000-000000000001", "2026-10-14T01:12:00Z").turn()
    r.user("u1", "pytest を流すと DeprecationWarning が 3 件出る。警告を消して。テストの中身は変えないで。")
    r.cmd("c1", "python -m pytest -q", 0, "....\n3 warnings in 2.10s\nDeprecationWarning: datetime.utcnow() is deprecated")
    r.patch("f1", CWD + "\\src\\clock.py", "--- a/src/clock.py\n+++ b/src/clock.py\n@@ -1 +1 @@\n-now = datetime.utcnow()\n+now = datetime.now(timezone.utc)\n")
    r.cmd("c2", "python -m pytest -q", 0, "....\n4 passed in 2.05s")
    r.say("a1", "datetime.utcnow() を datetime.now(timezone.utc) に置き換えました。テストは変えていません。警告は 0 件になりました。", final=True)
    return r.end("datetime.utcnow() を datetime.now(timezone.utc) に置き換えました。テストは変えていません。警告は 0 件になりました。")


def long():
    r = Rollout("10000000-0000-4000-8000-000000000002", "2026-10-14T00:00:00Z")
    topics = [("ステージ", "stages"), ("ジョブ", "jobs"), ("キャッシュ", "Cache@2")]
    n = 0
    for chapter, (word, key) in enumerate(topics):
        for k in range(15):
            n += 1
            r.turn()
            r.user("u%d" % n, "%s の書き方 %d: %s をどう書く？" % (word, k + 1, key))
            r.say("a%d" % n, "%s は %s の下に並べます（例 %d）。" % (word, key, k + 1), final=True)
            r.end("%s は %s の下に並べます（例 %d）。" % (word, key, k + 1))
    return r


def condition_change():
    r = Rollout("10000000-0000-4000-8000-000000000003", "2026-10-14T02:00:00Z").turn()
    r.user("u1", "ビルドのログから失敗したテストの名前だけを抜き出すスクリプトを書いて。")
    r.say("a1", "scripts/failed_tests.py を書きます。JUnit XML を読んで失敗の名前を並べます。")
    r.patch("f1", CWD + "\\scripts\\failed_tests.py", "--- /dev/null\n+++ b/scripts/failed_tests.py\n@@ -0,0 +1,3 @@\n+import sys\n+import xml.etree.ElementTree as ET\n+print('x')\n")
    r.end("scripts/failed_tests.py を書きました。")
    r.turn()
    r.user("u2", "やっぱり出力は JSON にして。あと、さっき頼んだ Markdown の表は要らない。")
    r.patch("f2", CWD + "\\scripts\\failed_tests.py", "--- a/scripts/failed_tests.py\n+++ b/scripts/failed_tests.py\n@@ -3 +3 @@\n-print('x')\n+print(json.dumps(names))\n")
    r.cmd("c1", "python scripts/failed_tests.py test-results/junit.xml", 0, '["tests/test_retry.py::test_x"]')
    r.say("a2", "出力を JSON の配列にしました。test-results/junit.xml で試すと [\"tests/test_retry.py::test_x\"] が出ました。", final=True)
    return r.end("出力を JSON の配列にしました。")


def fail_then_fix():
    r = Rollout("10000000-0000-4000-8000-000000000004", "2026-10-14T03:00:00Z").turn()
    r.user("u1", "test_retry が CI でだけ落ちる。原因を調べて直して。")
    r.cmd("c1", "python -m pytest -q tests/test_retry.py", 1, "F\nFAILED tests/test_retry.py::test_x - TimeoutError: 2.0s")
    r.say("a1", "CI のエージェントは遅いので、2 秒のタイムアウトに届いていません。")
    r.patch("f1", CWD + "\\tests\\test_retry.py", "--- a/tests/test_retry.py\n+++ b/tests/test_retry.py\n@@ -5 +5 @@\n-TIMEOUT = 2.0\n+TIMEOUT = float(os.environ.get('RETRY_TIMEOUT', '10'))\n")
    r.cmd("c2", "python -m pytest -q tests/test_retry.py", 0, ".\n1 passed in 3.1s")
    r.say("a2", "タイムアウトを環境変数 RETRY_TIMEOUT で変えられるようにし、既定を 10 秒にしました。手元では通りました。CI ではまだ流していません。", final=True)
    return r.end("タイムアウトを環境変数で変えられるようにしました。")


def waiting_approval():
    r = Rollout("10000000-0000-4000-8000-000000000005", "2026-10-14T04:00:00Z").turn()
    r.user("u1", "Work Item #1287 を Resolved にして、コメントに PR #482 を書いて。")
    r.cmd("c1", ADO + "write _apis/wit/workitems/1287 -Method PATCH -BodyFile body.json -DryRun", 0,
          '{"approve":"9f3c","method":"PATCH","path":"_apis/wit/workitems/1287"}')
    r.say("a1", "送る内容は次のとおりです。State を Resolved に、コメントに PR #482 を入れます。承認の ID は 9f3c です。送ってよいですか？", final=True)
    return r.end("送ってよいですか？")


def aborted():
    r = Rollout("10000000-0000-4000-8000-000000000006", "2026-10-14T05:00:00Z").turn()
    r.user("u1", "パイプラインの YAML を全部 templates に分けて。")
    r.say("a1", "azure-pipelines.yml を読みます。")
    r.cmd("c1", "Get-Content azure-pipelines.yml", 0, "stages:\n- stage: build")
    return r.end(aborted=True)


SESSIONS = {"short": short, "long": long, "condition_change": condition_change, "fail_then_fix": fail_then_fix,
            "waiting_approval": waiting_approval, "aborted": aborted}


def run(args, env):
    r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout + r.stderr


def build(out):
    """6 本の記録を一時的な CODEX_HOME に書き、report.json と正解の追記を out/<名前>/ に置く。"""
    home = tempfile.mkdtemp(prefix="session-report-fixtures-")
    env = dict(os.environ, CODEX_HOME=home, HIRATA_CODEX_REPORT_REEXEC="fixtures", TZ="Asia/Tokyo")
    try:
        day = os.path.join(home, "sessions", "2026", "10", "14")
        os.makedirs(day)
        for name, make in SESSIONS.items():
            r = make()
            with open(os.path.join(day, "rollout-2026-10-14T00-00-00-%s.jsonl" % r.tid), "w", encoding="utf-8", newline="\n") as f:
                f.write(r.text())
            target = os.path.join(out, name)
            code, text = run(["extract", r.tid, "--out", target], env)
            if code:
                raise SystemExit("%s: extract が失敗した: %s" % (name, text))
            with open(os.path.join(target, "report.json"), encoding="utf-8") as f:
                digest = json.load(f)["digest"]
            with open(os.path.join(GOLD, name + ".json"), encoding="utf-8") as f:
                gold = f.read().replace("<digest>", digest)
            with open(os.path.join(target, "annotations.json"), "w", encoding="utf-8", newline="\n") as f:
                f.write(gold)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return env


def build_week(out):
    """6 本を、会話ごとのレポートの既定の置き場所（HIRATA_CODEX_DATA の下）に作り、週の材料と週の正解の追記を置く。
    週の期間は 2026-10-14 の 1 日（6 本とも、この日の日本時間に始まる）。返り値は週のフォルダと env。"""
    home = tempfile.mkdtemp(prefix="session-report-fixtures-")
    env = dict(os.environ, CODEX_HOME=home, HIRATA_CODEX_DATA=os.path.join(out, "data"), HIRATA_CODEX_REPORT_REEXEC="fixtures",
               TZ="Asia/Tokyo")
    day = os.path.join(home, "sessions", "2026", "10", "14")
    os.makedirs(day)
    for name, make in SESSIONS.items():
        r = make()
        with open(os.path.join(day, "rollout-2026-10-14T00-00-00-%s.jsonl" % r.tid), "w", encoding="utf-8", newline="\n") as f:
            f.write(r.text())
        target = os.path.join(out, "data", "session-report", r.tid)
        code, text = run(["extract", r.tid, "--out", target], env)
        if code:
            raise SystemExit("%s: extract が失敗した: %s" % (name, text))
        with open(os.path.join(target, "report.json"), encoding="utf-8") as f:
            digest = json.load(f)["digest"]
        with open(os.path.join(GOLD, name + ".json"), encoding="utf-8") as f:
            gold = f.read().replace("<digest>", digest)
        with open(os.path.join(target, "annotations.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(gold)
        code, text = run(["render", "--dir", target], env)
        if code:
            raise SystemExit("%s: render が失敗した: %s" % (name, text))
    week = os.path.join(out, "week")
    code, text = run(["week", "--since", "2026-10-14", "--until", "2026-10-14", "--dir", week], env)
    shutil.rmtree(home, ignore_errors=True)
    if code:
        raise SystemExit("week が失敗した: %s" % text)
    with open(os.path.join(week, "week.json"), encoding="utf-8") as f:
        digest = json.load(f)["digest"]
    with open(os.path.join(GOLD, "week.json"), encoding="utf-8") as f:
        gold = f.read().replace("<digest>", digest)
    with open(os.path.join(week, "week-annotations.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(gold)
    return week, env


def cmd_week(args):
    out = args.out or tempfile.mkdtemp(prefix="session-report-fixtures-week-")
    week, env = build_week(out)
    failed = 0
    code, text = run(["week-check", "--dir", week], env)
    print("week-check: %s" % ("違反 0 件" if code == 0 else "違反あり\n" + text))
    failed += code != 0
    results = []
    for _ in range(2 if args.twice else 1):
        code, text = run(["week-render", "--dir", week], env)
        if code:
            print("week-render が失敗した\n" + text)
            failed += 1
            break
        with open(os.path.join(week, "week.html"), "rb") as f:
            results.append(f.read())
    if results:
        same = "" if not args.twice else "、同じバイト列: %s" % ("はい" if results[0] == results[-1] else "いいえ")
        failed += bool(args.twice and results[0] != results[-1])
        print("week.html: %s（%d バイト%s）" % (os.path.join(week, "week.html"), len(results[0]), same))
    if not args.out:
        shutil.rmtree(out, ignore_errors=True)
    return 1 if failed else 0


def cmd_check(_args):
    out = tempfile.mkdtemp(prefix="session-report-fixtures-out-")
    failed = 0
    try:
        env = build(out)
        for name in SESSIONS:
            code, text = run(["check", "--dir", os.path.join(out, name)], env)
            print("%s: %s" % (name, "違反 0 件" if code == 0 else "違反あり\n" + text))
            failed += code != 0
    finally:
        shutil.rmtree(out, ignore_errors=True)
    return 1 if failed else 0


def cmd_render(args):
    out = args.out or tempfile.mkdtemp(prefix="session-report-fixtures-html-")
    env = build(out)
    failed = 0
    for name in SESSIONS:
        target = os.path.join(out, name)
        for extra, filename in (([], "report.html"),):
            results = []
            for _ in range(2 if args.twice else 1):
                code, text = run(["render", "--dir", target] + extra, env)
                if code:
                    print("%s: render が失敗した\n%s" % (name, text))
                    failed += 1
                    break
                with open(os.path.join(target, filename), "rb") as f:
                    results.append(f.read())
            else:
                same = "" if not args.twice else "、同じバイト列: %s" % ("はい" if results[0] == results[1] else "いいえ")
                failed += bool(args.twice and results[0] != results[1])
                print("%s: %s（%d バイト%s）" % (name, os.path.join(target, filename), len(results[0]), same))
    if not args.out:
        shutil.rmtree(out, ignore_errors=True)
    return 1 if failed else 0


def cmd_build(args):
    build(args.out)
    print("書いた: %s（%s）" % (args.out, "・".join(SESSIONS)))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    rd = sub.add_parser("render")
    rd.add_argument("--twice", action="store_true")
    rd.add_argument("--out")
    rd.set_defaults(func=cmd_render)
    wk = sub.add_parser("week")
    wk.add_argument("--twice", action="store_true")
    wk.add_argument("--out")
    wk.set_defaults(func=cmd_week)
    b = sub.add_parser("build")
    b.add_argument("out")
    b.set_defaults(func=cmd_build)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
