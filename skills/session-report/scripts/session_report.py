"""Codex の 1 つの会話を JSON にまとめ、決まったテンプレートの単体の HTML にする。

    python session_report.py probe [--since 2026-10-01]
    python session_report.py extract <thread_id> | --last | --current [--out DIR]

probe: 記録の形（行の種類、項目の種類とキー名、件数、有無）だけを数えて出す。本文・コマンド・出力・パス・引数・検索語・
差分は出さない。出す値は、Codex のコードと設定が決める種類の値（type・phase・status・MCP の server と tool の名前・版）で、
英数字の 40 文字までの形のものだけ。
会社の PC の記録が、この PC の記録と同じ形かを、中身を見せずに確かめるために使う。

記録の読み方と伏せ字は session-search のものを使う（同じ skill 集の ../../session-search/scripts/session_search.py）。
"""

import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_SCRIPT = os.path.normpath(os.path.join(HERE, "..", "..", "session-search", "scripts", "session_search.py"))
REEXEC_ENV = "HIRATA_CODEX_REPORT_REEXEC"
PROBE_VERSION = "1"
SCHEMA_VERSION = "1"
EXTRACT_VERSION = "1"


def load_search():
    """session-search の session_search.py を読み込む。無ければ None。"""
    if not os.path.isfile(SEARCH_SCRIPT):
        return None
    spec = importlib.util.spec_from_file_location("session_search", SEARCH_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ss = load_search()


def maybe_reexec(paths, argv):
    """圧縮された記録を読めない Python で動いたら、読める Python で起動し直す（session-search と同じ探し方）。"""
    if os.environ.get(REEXEC_ENV) or ss.zstd is not None:
        return None
    if not any(p.endswith(".zst") for p in paths):
        return None
    for cmd in ss.zstd_interpreters():
        try:
            ok = subprocess.run(cmd + ["-c", "import compression.zstd"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            continue
        if ok:
            env = dict(os.environ, **{REEXEC_ENV: " ".join(cmd)})
            return subprocess.run(cmd + [os.path.abspath(__file__)] + argv, env=env).returncode
    return None


# --- probe ---------------------------------------------------------------------------------------

# 出してよい名前の形。記録の値のうち、種類を表すもの（type・phase・status・MCP の server と tool の名前など）だけを
# この形のときに出す。合わない値（日本語、空白、長いもの）は (other) にまとめる。
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}$")
_ROLLOUT_DATE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2})T")


def _name(value):
    if isinstance(value, str):
        return value if _SAFE_NAME.match(value) else "(other)"
    if value is None:
        return "(none)"
    return "(%s)" % type(value).__name__


def _keys(obj):
    return [_name(k) for k in obj] if isinstance(obj, dict) else []


def _shape(value, enum=False):
    """dict ならキー名の並び、数なら number。文字列は、種類の値の欄（enum=True）だけ _name を通し、ほかは (str)。"""
    if isinstance(value, dict):
        return "{%s}" % "+".join(sorted(_keys(value)))
    if isinstance(value, bool):
        return "(bool)"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and not enum:
        return "(str)"
    return _name(value)


def _length(text):
    if not isinstance(text, str):
        return _name(text)
    n = len(text)
    for limit, label in ((0, "len=0"), (2000, "len<=2k"), (20000, "len<=20k"), (200000, "len<=200k")):
        if n <= limit:
            return label
    return "len>200k"


def _count(n):
    for limit, label in ((0, "n=0"), (1, "n=1"), (10, "n<=10"), (100, "n<=100")):
        if n <= limit:
            return label
    return "n>100"


def _path_state(value):
    """パスの形と、今そのファイルがあるか。パスそのものは出さない。"""
    if not isinstance(value, str) or not value:
        return _name(value)
    path, form = value, "relative"
    if value.startswith("file://"):
        from urllib.parse import unquote, urlparse
        url = urlparse(value)
        path, form = unquote(url.path), "file-uri"
        if url.netloc and url.netloc.lower() != "localhost":
            path = "//" + url.netloc + path  # file://server/share/a.png は \\server\share\a.png
        elif re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
    elif os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value):
        form = "absolute"
    return "%s,%s" % (form, "exists" if os.path.exists(path) else "missing")


def _image_url(value):
    if not isinstance(value, str):
        return _name(value)
    for prefix in ("data:", "https:", "http:", "file:"):
        if value.startswith(prefix):
            return prefix.rstrip(":")
    return "(other)"


def probe_item(c, it):
    t = _name(it.get("type"))
    c["item.type", t] += 1
    for k in _keys(it):
        c["item.%s.key" % t, k] += 1
    if "status" in it:
        st = it["status"]
        c["item.%s.status" % t, _shape(st, enum=True)] += 1
    if t == "AgentMessage":
        c["item.AgentMessage.phase", _name(it.get("phase"))] += 1
    elif t == "UserMessage":
        for el in it.get("content") if isinstance(it.get("content"), list) else []:
            et = _name(el.get("type")) if isinstance(el, dict) else _name(el)
            c["item.UserMessage.content.type", et] += 1
            if isinstance(el, dict) and et == "local_image":
                c["item.UserMessage.local_image.path", _path_state(el.get("path"))] += 1
            elif isinstance(el, dict) and et == "image":
                c["item.UserMessage.image.url", _image_url(el.get("image_url"))] += 1
    elif t == "CommandExecution":
        cmd = it.get("command")
        c["item.CommandExecution.command", "list,%s" % _count(len(cmd)) if isinstance(cmd, list) else _shape(cmd)] += 1
        for pc in it.get("parsed_cmd") if isinstance(it.get("parsed_cmd"), list) else []:
            c["item.CommandExecution.parsed_cmd.type", _name(pc.get("type")) if isinstance(pc, dict) else _name(pc)] += 1
        code = it.get("exit_code")
        c["item.CommandExecution.exit_code", str(code) if code in (0, 1) else ("other" if isinstance(code, int) else _name(code))] += 1
        c["item.CommandExecution.aggregated_output", _length(it.get("aggregated_output"))] += 1
        c["item.CommandExecution.duration", _shape(it.get("duration"))] += 1
        c["item.CommandExecution.source", _name(it.get("source"))] += 1
    elif t == "FileChange":
        changes = it.get("changes")
        c["item.FileChange.changes", _count(len(changes)) if isinstance(changes, dict) else _name(changes)] += 1
        for ch in changes.values() if isinstance(changes, dict) else []:
            c["item.FileChange.change.shape", _shape(ch)] += 1
            if isinstance(ch, dict) and "type" in ch:
                c["item.FileChange.change.type", _name(ch.get("type"))] += 1
    elif t == "McpToolCall":
        c["item.McpToolCall.server", _name(it.get("server"))] += 1
        c["item.McpToolCall.tool", _name(it.get("tool"))] += 1
        c["item.McpToolCall.result", _shape(it.get("result"))] += 1
    elif t == "WebSearch":
        act = it.get("action")
        c["item.WebSearch.action.type", _name(act.get("type")) if isinstance(act, dict) else _name(act)] += 1
        c["item.WebSearch.results", _count(len(it["results"])) if isinstance(it.get("results"), list) else _name(it.get("results"))] += 1
    elif t == "ImageView":
        c["item.ImageView.path", _path_state(it.get("path"))] += 1


def probe_file(c, data):
    counters = {"incomplete_tail": 0}
    seen_ids = collections.Counter()
    for _, raw in ss._byte_lines(data, counters):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            c["lines", "bad_json"] += 1
            continue
        if not isinstance(item, dict):
            c["lines", "bad_json"] += 1
            continue
        c["lines", "read"] += 1
        typ = _name(item.get("type"))
        c["line.type", typ] += 1
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if typ == "event_msg":
            et = _name(payload.get("type"))
            c["event_msg.type", et] += 1
            for k in _keys(payload):
                c["event_msg.%s.key" % et, k] += 1
            if et == "token_count" and isinstance(payload.get("info"), dict):
                for k in _keys(payload["info"]):
                    c["event_msg.token_count.info.key", k] += 1
            it = payload.get("item")
            if et in ("item_completed", "item_started") and isinstance(it, dict):
                if et == "item_completed":
                    probe_item(c, it)
                    if isinstance(it.get("id"), str):
                        seen_ids[it["id"]] += 1
                    else:
                        c["ids.item_completed", "missing"] += 1
                else:
                    c["item_started.type", _name(it.get("type"))] += 1
        elif typ == "response_item":
            rt = _name(payload.get("type"))
            c["response_item.type", rt] += 1
            if rt == "message":
                c["response_item.message.role", _name(payload.get("role"))] += 1
            elif rt in ("function_call", "custom_tool_call", "local_shell_call"):
                c["response_item.%s.name" % rt, _name(payload.get("name"))] += 1
        elif typ in ("session_meta", "turn_context"):
            for k in _keys(payload):
                c["%s.key" % typ, k] += 1
            if typ == "session_meta":
                c["session_meta.originator", _name(payload.get("originator"))] += 1
                c["session_meta.cli_version", _name(payload.get("cli_version"))] += 1
                c["session_meta.source", _shape(payload.get("source"), enum=True)] += 1
            else:
                c["turn_context.effort", _name(payload.get("effort"))] += 1
    c["ids.item_completed", "duplicated"] += sum(1 for n in seen_ids.values() if n > 1)
    c["ids.item_completed", "unique"] += len(seen_ids)
    if counters["incomplete_tail"]:
        c["files", "incomplete_tail"] += 1


def probe_paths(home, since=None):
    paths = ss.rollout_files(home)
    if since:
        paths = [p for p in paths if (_ROLLOUT_DATE.search(os.path.basename(p)) or [None, "9999"])[1] >= since]
    return paths


def cmd_probe(args):
    home = ss.codex_home()
    c = collections.Counter()
    c["probe", "version-" + PROBE_VERSION] = 1
    paths = probe_paths(home, args.since)
    for path in paths:
        c["files", "zst" if path.endswith(".zst") else "jsonl"] += 1
        try:
            data = ss.read_rollout_bytes(path)
        except OSError:
            c["files", "unreadable"] += 1
            continue
        if data is None:
            c["files", "zst_unreadable"] += 1
            continue
        probe_file(c, data)
    index = os.path.join(home, "session_index.jsonl")
    if os.path.isfile(index):
        with open(index, "rb") as f:
            for raw in f:
                try:
                    row = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    c["session_index", "bad_json"] += 1
                    continue
                for k in _keys(row):
                    c["session_index.key", k] += 1
    else:
        c["session_index", "missing"] += 1
    for (cat, name), n in sorted(c.items()):
        print("%s\t%s\t%d" % (cat, name, n))
    return 0


# --- extract: 会話の情報と発言 ---------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# 発言として扱う項目と、T03 で操作として扱う項目。ほかは coverage に種類ごとの件数だけ残す
MESSAGE_ITEMS = {"UserMessage", "AgentMessage"}
EVENT_ITEMS = {"Reasoning", "ContextCompaction", "SubAgentActivity", "CollabAgentToolCall",
               "EnteredReviewMode", "ExitedReviewMode"}


def report_dir(thread_id):
    return os.path.join(ss.data_dir(), "session-report", thread_id)


def _first_meta(data):
    """記録の最初の session_meta の payload（無ければ None）。"""
    for _, raw in ss._byte_lines(data, {"incomplete_tail": 0}):
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
            return item["payload"]
    return None


def _same_dir(a, b):
    return isinstance(a, str) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def find_rollout(home, thread_id=None, last_cwd=None, exclude=None):
    """会話の rollout のパスと、同じ会話の別のファイルの数。見つからなければ (None, 0)。

    thread_id を渡せば名前にその ID を持つファイルを、last_cwd を渡せばその作業フォルダで最後に書かれた会話
    （exclude の会話を除く）を選ぶ。同じ ID のファイルが複数あれば、最後に書かれたものを使う。
    """
    paths = sorted(ss.rollout_files(home), key=lambda p: (os.path.getmtime(p), p), reverse=True)
    if thread_id:
        hits = [p for p in paths if ss._thread_id_in_name(p) == thread_id]
        return (hits[0], len(hits) - 1) if hits else (None, 0)
    for p in paths:
        tid = ss._thread_id_in_name(p)
        if not tid or tid == exclude:
            continue
        try:
            data = ss.read_rollout_bytes(p)
        except OSError:
            continue
        meta = _first_meta(data) if data is not None else None
        if not meta or (isinstance(meta.get("source"), dict) and "subagent" in meta["source"]):
            continue  # 子のエージェントの会話は、持ち主の会話として選ばない
        if _same_dir(meta.get("cwd"), last_cwd):
            return p, len([q for q in paths if ss._thread_id_in_name(q) == tid]) - 1
    return None, 0


def thread_title(home, thread_id):
    """session_index.jsonl の最後の名前。"""
    title = None
    path = os.path.join(home, "session_index.jsonl")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        for raw in f:
            try:
                row = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id") == thread_id and isinstance(row.get("thread_name"), str):
                title = row["thread_name"]
    return title


def _red(value):
    return ss.redact(value) if isinstance(value, str) else value


def _attachments(content):
    """UserMessage の添付の名前（本体は持たない）。"""
    out = []
    for el in content if isinstance(content, list) else []:
        if not isinstance(el, dict):
            continue
        if el.get("type") in ("local_image", "local_audio") and isinstance(el.get("path"), str):
            out.append({"kind": el["type"].split("_")[1], "name": _red(re.split(r"[\\/]", el["path"])[-1]), "body": False})
        elif el.get("type") == "image":
            out.append({"kind": "image", "name": "貼り付けた画像", "body": False})
        elif el.get("type") == "audio":
            out.append({"kind": "audio", "name": "貼り付けた音声", "body": False})
    return out


def scan_rollout(data):
    """発言以外の、ターン・添付・項目の種類を 1 回読んで集める。"""
    turns = collections.OrderedDict()
    attachments = {}
    item_types = collections.Counter()
    ids = collections.Counter()
    first_ts = last_ts = None
    order = 0

    def turn(tid):
        if tid not in turns:
            turns[tid] = {"id": tid, "started": None, "ended": None, "model": None, "effort": None, "status": "incomplete"}
        return turns[tid]

    current = None
    for _, raw in ss._byte_lines(data, {"incomplete_tail": 0}):
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        ts = item.get("timestamp") if isinstance(item.get("timestamp"), str) else None
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        typ = item.get("type")
        if typ == "turn_context":
            tid = ss._str(payload.get("turn_id"))
            if tid is None:
                order += 1
                tid = "turn-%d" % order
            current = tid
            t = turn(tid)
            t["model"] = ss._str(payload.get("model")) or t["model"]
            t["effort"] = ss._str(payload.get("effort")) or t["effort"]
            t["started"] = t["started"] or ts
        elif typ == "event_msg":
            et = payload.get("type")
            tid = ss._str(payload.get("turn_id")) or current
            if et == "task_started" and tid:
                current = tid
                t = turn(tid)
                t["started"] = t["started"] or ts
            elif et in ("task_complete", "turn_aborted") and tid:
                t = turn(tid)
                t["ended"] = ts
                t["status"] = "completed" if et == "task_complete" else "aborted"
            elif et == "item_completed" and isinstance(payload.get("item"), dict):
                it = payload["item"]
                item_types[ss._str(it.get("type")) or "(none)"] += 1
                if isinstance(it.get("id"), str):
                    ids[it["id"]] += 1
                if it.get("type") == "UserMessage" and isinstance(it.get("id"), str):
                    found = _attachments(it.get("content"))
                    if found:
                        attachments.setdefault(it["id"], found)
                if tid and tid not in turns:
                    turn(tid)["started"] = ts
    return {"turns": list(turns.values()), "attachments": attachments, "item_types": item_types,
            "duplicated_ids": sum(1 for n in ids.values() if n > 1), "first_ts": first_ts, "last_ts": last_ts}


def _source_name(meta_source):
    if isinstance(meta_source, str):
        return meta_source
    if isinstance(meta_source, dict):
        return "subagent" if "subagent" in meta_source else "(other)"
    return None


def build_report(path, data, home, other_files=0):
    meta, messages, counters = ss.parse_rollout(path, data)
    raw_meta = _first_meta(data) or {}
    scan = scan_rollout(data)
    tid = meta["thread_id"]
    out_msgs, seen = [], set()
    for m in messages:
        if m["msg_id"] in seen:
            continue
        seen.add(m["msg_id"])
        msg = {"id": m["msg_id"], "kind": m["kind"], "ts": m["ts"], "turn_id": m["turn_id"], "text": ss.redact(m["text"])}
        if m["kind"] == "user":
            msg["attachments"] = scan["attachments"].get(m["msg_id"], [])
        out_msgs.append(msg)
    unknown = {k: n for k, n in sorted(scan["item_types"].items())
               if k not in MESSAGE_ITEMS and k not in EVENT_ITEMS and k not in OPERATION_ITEMS}
    events = {k: scan["item_types"][k] for k in sorted(EVENT_ITEMS) if scan["item_types"].get(k)}
    report = {
        "schema_version": SCHEMA_VERSION, "extract_version": EXTRACT_VERSION, "redaction_version": ss._RULES_VERSION,
        "tz_offset_minutes": int(datetime.datetime.now().astimezone().utcoffset().total_seconds() // 60),
        "session": {"id": tid, "title": _red(thread_title(home, tid)), "started": scan["first_ts"], "ended": scan["last_ts"],
                    "cwd": _red(meta["cwd"]), "branch": _red(meta["branch"]), "originator": meta["originator"],
                    "source": _source_name(raw_meta.get("source")), "cli_version": meta["cli_version"],
                    "forked_from_id": ss._str(raw_meta.get("forked_from_id")), "agent": meta["agent"]},
        "sources": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        "coverage": {"lines": counters["lines"], "bad_json": counters["bad_json"], "shape_errors": counters["shape_errors"],
                     "incomplete_tail": counters["incomplete_tail"], "duplicated_ids": scan["duplicated_ids"],
                     "other_files": other_files, "unknown_items": unknown, "events": events,
                     "subagents_included": False},
        "turns": scan["turns"],
        "messages": out_msgs,
        "operations": [],
        "artifacts": [],
    }
    return report


# T03 で操作として読む項目（T02 では unknown に数えない）
OPERATION_ITEMS = {"CommandExecution", "FileChange", "McpToolCall", "WebSearch", "ImageView", "Extension"}


def dumps(obj):
    """同じ中身なら同じバイト列になる JSON。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1, separators=(",", ": ")) + "\n"


def digest(report):
    body = dict(report)
    body.pop("digest", None)
    return hashlib.sha256(dumps(body).encode("utf-8")).hexdigest()


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(obj))
    os.replace(tmp, path)


def cmd_extract(args):
    home = ss.codex_home()
    current = os.environ.get("CODEX_THREAD_ID")
    if args.current:
        if not current:
            print("CODEX_THREAD_ID が無い。今の会話の ID が分からないので、ID を渡す", file=sys.stderr)
            return 1
        path, others = find_rollout(home, thread_id=current)
    elif args.last:
        path, others = find_rollout(home, last_cwd=os.getcwd(), exclude=current)
    else:
        path, others = find_rollout(home, thread_id=args.thread_id)
    if path is None:
        print("会話の記録が見つからない（%s）" % (args.thread_id or ("今の会話" if args.current else "この作業フォルダの会話")),
              file=sys.stderr)
        return 1
    data = ss.read_rollout_bytes(path)
    if data is None:
        print("圧縮された記録を読める Python が無い（Python 3.14 以上の compression.zstd が要る）: %s" % path, file=sys.stderr)
        return 1
    report = build_report(path, data, home, others)
    report["digest"] = digest(report)
    out = args.out or report_dir(report["session"]["id"])
    write_json(os.path.join(out, "report.json"), report)
    print("書いた: %s（発言 %d 件、ターン %d 件）" % (os.path.join(out, "report.json"), len(report["messages"]),
                                                 len(report["turns"])))
    return 0


# --- 入口 -----------------------------------------------------------------------------------------


def _date(value):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise argparse.ArgumentTypeError("日付は 2026-10-01 の形で書く")
    return value


def _thread_id(value):
    if not _UUID.fullmatch(value):
        raise argparse.ArgumentTypeError("会話の ID は 019a...-...-... の形（codex resume に渡す ID）")
    return value


def build_parser():
    p = argparse.ArgumentParser(prog="session_report.py", description="Codex の 1 つの会話を単体の HTML にする")
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("probe", help="記録の形だけを数えて出す（本文は出さない）")
    pr.add_argument("--since", type=_date, help="この日付（ファイル名の日付）以降の記録だけを数える")
    pr.set_defaults(func=cmd_probe)
    ex = sub.add_parser("extract", help="1 つの会話から機械で取れるものを report.json に書く")
    which = ex.add_mutually_exclusive_group(required=True)
    which.add_argument("thread_id", nargs="?", type=_thread_id)
    which.add_argument("--last", action="store_true", help="今の作業フォルダの、今の会話を除いた最後の会話")
    which.add_argument("--current", action="store_true", help="今の会話（CODEX_THREAD_ID）のここまで")
    ex.add_argument("--out", help="書き出すフォルダ（既定は %%LOCALAPPDATA%%\\hirata-codex\\session-report\\<thread_id>）")
    ex.set_defaults(func=cmd_extract)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    if ss is None:
        print("session-search の skill が見つからない（%s）。hirata-codex の install.ps1 で session-search も入れる"
              % SEARCH_SCRIPT, file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    if args.command in ("probe", "extract"):
        code = maybe_reexec(probe_paths(ss.codex_home(), getattr(args, "since", None)), argv)
        if code is not None:
            return code
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
