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

# probe が値を出すのは、種類を表す欄（type・phase・status・MCP の server と tool の名前・版など）の文字列だけで、
# それも英数字 40 文字までの形のときだけ。ほかの欄は、値ではなく型（(str)・(list)・number など）か、長さ・件数・有無の区分を出す。
# Codex の構造（item・payload・session_meta・turn_context・info）のキー名は出す。入れ子の dict のキー名は KNOWN_KEYS の名前だけ出す。
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}$")
_ROLLOUT_DATE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2})T")
# 入れ子の値のキー名のうち、Codex か MCP の型で決まっているもの（duration、FileChange の変更、MCP の結果、session_meta の source）
KNOWN_KEYS = {"secs", "nanos", "type", "content", "unified_diff", "move_path", "kind", "isError", "structuredContent",
              "_meta", "subagent"}


def _kind(value):
    """値の型だけ。"""
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "(bool)"
    if isinstance(value, (int, float)):
        return "number"
    return "(%s)" % type(value).__name__


def _enum(value):
    """種類を表す欄の値。決まった形の文字列だけを出す。"""
    if isinstance(value, str):
        return value if _SAFE_NAME.match(value) else "(other)"
    return _kind(value)


def _keys(obj):
    """Codex の構造のキー名。"""
    return [_enum(k) for k in obj] if isinstance(obj, dict) else []


def _shape(value):
    """入れ子の値の形。dict なら KNOWN_KEYS の名前の並び（ほかのキーは (other)）、ほかは型。"""
    if isinstance(value, dict):
        return "{%s}" % "+".join(sorted({k if k in KNOWN_KEYS else "(other)" for k in value}))
    return _kind(value)


def _enum_or_shape(value):
    return _shape(value) if isinstance(value, dict) else _enum(value)


def _length(text):
    if not isinstance(text, str):
        return _kind(text)
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


def _sized(value):
    """list か dict なら件数の区分、ほかは型。"""
    return _count(len(value)) if isinstance(value, (list, dict)) else _kind(value)


def _path_state(value):
    """パスの形と、今そのファイルがあるか。パスそのものは出さない。"""
    if not isinstance(value, str):
        return _kind(value)
    if not value:
        return "len=0"
    path, form = value, "relative"
    if value.startswith("file://"):
        from urllib.parse import unquote, urlparse
        try:
            url = urlparse(value)
        except ValueError:
            return "file-uri,invalid"  # 例外のメッセージには値が入るので、出さない
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
        return _kind(value)
    for prefix in ("data:", "https:", "http:", "file:"):
        if value.startswith(prefix):
            return prefix.rstrip(":")
    return "(other)"


def _types(values):
    """list の各要素の type（dict でなければ型）。"""
    for v in values if isinstance(values, list) else []:
        yield _enum(v.get("type")) if isinstance(v, dict) else _kind(v)


def probe_item(c, it):
    t = _enum(it.get("type"))
    c["item.type", t] += 1
    for k in _keys(it):
        c["item.%s.key" % t, k] += 1
    if "status" in it:
        c["item.%s.status" % t, _enum_or_shape(it["status"])] += 1
    if t == "AgentMessage":
        c["item.AgentMessage.phase", _enum(it.get("phase"))] += 1
    elif t == "UserMessage":
        content = it.get("content")
        if not isinstance(content, list):
            c["item.UserMessage.content", _kind(content)] += 1
        for el, et in zip(content if isinstance(content, list) else [], _types(content)):
            c["item.UserMessage.content.type", et] += 1
            if isinstance(el, dict) and et == "local_image":
                c["item.UserMessage.local_image.path", _path_state(el.get("path"))] += 1
            elif isinstance(el, dict) and et == "image":
                c["item.UserMessage.image.url", _image_url(el.get("image_url"))] += 1
    elif t == "CommandExecution":
        cmd = it.get("command")
        c["item.CommandExecution.command", "list,%s" % _count(len(cmd)) if isinstance(cmd, list) else _kind(cmd)] += 1
        if not isinstance(it.get("parsed_cmd"), list):
            c["item.CommandExecution.parsed_cmd", _kind(it.get("parsed_cmd"))] += 1
        for pt in _types(it.get("parsed_cmd")):
            c["item.CommandExecution.parsed_cmd.type", pt] += 1
        code = it.get("exit_code")
        is_int = isinstance(code, int) and not isinstance(code, bool)
        c["item.CommandExecution.exit_code", str(code) if is_int and code in (0, 1) else ("other" if is_int else _kind(code))] += 1
        c["item.CommandExecution.aggregated_output", _length(it.get("aggregated_output"))] += 1
        c["item.CommandExecution.duration", _shape(it.get("duration"))] += 1
        c["item.CommandExecution.source", _enum_or_shape(it.get("source"))] += 1
    elif t == "FileChange":
        changes = it.get("changes")
        c["item.FileChange.changes", _sized(changes)] += 1
        for ch in changes.values() if isinstance(changes, dict) else []:
            c["item.FileChange.change.shape", _shape(ch)] += 1
            if isinstance(ch, dict) and "type" in ch:
                c["item.FileChange.change.type", _enum(ch.get("type"))] += 1
    elif t == "McpToolCall":
        c["item.McpToolCall.server", _enum(it.get("server"))] += 1
        c["item.McpToolCall.tool", _enum(it.get("tool"))] += 1
        c["item.McpToolCall.result", _shape(it.get("result"))] += 1
    elif t == "WebSearch":
        act = it.get("action")
        c["item.WebSearch.action.type", _enum(act.get("type")) if isinstance(act, dict) else _kind(act)] += 1
        res = it.get("results")
        c["item.WebSearch.results", _count(len(res)) if isinstance(res, list) else _kind(res)] += 1
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
        typ = _enum(item.get("type"))
        c["line.type", typ] += 1
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if typ == "event_msg":
            et = _enum(payload.get("type"))
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
                    c["item_started.type", _enum(it.get("type"))] += 1
        elif typ == "response_item":
            rt = _enum(payload.get("type"))
            c["response_item.type", rt] += 1
            if rt == "message":
                c["response_item.message.role", _enum(payload.get("role"))] += 1
            elif rt in ("function_call", "custom_tool_call", "local_shell_call"):
                c["response_item.%s.name" % rt, _enum(payload.get("name"))] += 1
        elif typ in ("session_meta", "turn_context"):
            for k in _keys(payload):
                c["%s.key" % typ, k] += 1
            if typ == "session_meta":
                c["session_meta.originator", _enum(payload.get("originator"))] += 1
                c["session_meta.cli_version", _enum(payload.get("cli_version"))] += 1
                c["session_meta.source", _enum_or_shape(payload.get("source"))] += 1
            else:
                c["turn_context.effort", _enum(payload.get("effort"))] += 1
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
        try:
            probe_file(c, data)
        except Exception as e:  # 例外のメッセージには記録の値が入りうるので、種類の名前だけを数える
            c["files", "probe_error." + type(e).__name__] += 1
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


class UnreadableRecords(Exception):
    """選んだ会話より新しい記録を読めず、正しい会話を選べたか分からない。"""


def find_rollout(home, thread_id=None, last_cwd=None, exclude=None):
    """会話の rollout のパスと、同じ会話の別のファイルの数。見つからなければ (None, 0)。

    会話の ID は、記録の最初の session_meta の id（無ければファイル名の ID）。revert は同じ会話の続きを別の名前の
    ファイルに書くので、ファイル名では選ばない。thread_id を渡せばその会話の、last_cwd を渡せばその作業フォルダで最後に
    書かれた会話（exclude の会話と子のエージェントを除く）の、最後に書かれたファイルを使う。
    選んだものより新しい記録を読めなければ UnreadableRecords を投げる（黙って古い会話を選ばない）。
    """
    paths = sorted(ss.rollout_files(home), key=lambda p: (os.path.getmtime(p), p), reverse=True)
    metas = []
    for p in paths:
        try:
            data = ss.read_rollout_bytes(p)
        except Exception:  # compression.zstd の ZstdError は OSError ではない
            data = None
        metas.append((p, None if data is None else (_first_meta(data) or {})))

    def tid_of(p, meta):
        return ss._str(meta.get("id")) or ss._thread_id_in_name(p)

    chosen = None
    for n, (p, meta) in enumerate(metas):
        if meta is None:
            continue
        tid = tid_of(p, meta)
        if thread_id:
            if tid == thread_id:
                chosen = (n, tid)
                break
            continue
        if not tid or tid == exclude:
            continue
        if isinstance(meta.get("source"), dict) and "subagent" in meta["source"]:
            continue  # 子のエージェントの会話は、持ち主の会話として選ばない
        if _same_dir(meta.get("cwd"), last_cwd):
            chosen = (n, tid)
            break
    limit = chosen[0] if chosen else len(metas)
    unreadable = [p for p, meta in metas[:limit] if meta is None]
    if unreadable:
        raise UnreadableRecords(unreadable)
    if chosen is None:
        return None, 0
    others = sum(1 for p, meta in metas if meta is not None and p != metas[chosen[0]][0] and tid_of(p, meta) == chosen[1])
    return metas[chosen[0]][0], others


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


def fill_turn_ids(data):
    """turn_id の無い turn_context（古い形式）に turn-1, turn-2, ... を入れた記録を返す。

    session-search の parse_rollout は、turn_id の無い turn_context の後も前のターンの ID を使い続けるので、
    読む前に ID を入れておく（行の数は変えない）。
    """
    out, order = [], 0
    for _, raw in ss._byte_lines(data, {"incomplete_tail": 0}):
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            item = None
        if (isinstance(item, dict) and item.get("type") == "turn_context" and isinstance(item.get("payload"), dict)
                and ss._str(item["payload"].get("turn_id")) is None):
            order += 1
            item["payload"]["turn_id"] = "turn-%d" % order
            raw = json.dumps(item, ensure_ascii=False).encode("utf-8")
        out.append(raw)
    return b"\n".join(out) + b"\n" if out else b""


HISTORY_DEPTH = 100  # revert をつなぐ段の上限（輪になった記録で止まらないため）


def effective_record(path, data, home, depth=0):
    """続きだけを書く revert の記録（session_meta.history_base）なら、前半のファイルのその位置までをつないだ記録と、
    読んだファイルの一覧を返す。前半のファイルが無ければ、続きだけで返し、prefix_missing を True にする。"""
    meta = _first_meta(data) or {}
    base = meta.get("history_base") if isinstance(meta.get("history_base"), dict) else None
    sources = [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]
    if not base:
        return data, sources, False
    if depth >= HISTORY_DEPTH:
        return data, sources, True  # これより前は読まない。読み切れなかったことを coverage に残す
    rid, end = ss._str(base.get("thread_id")), base.get("end_byte_offset")
    if not rid or not isinstance(end, int) or isinstance(end, bool) or end < 0:
        return data, sources, True
    prefix_path = next((p for p in sorted(ss.rollout_files(home)) if ss._thread_id_in_name(p) == rid and p != path), None)
    try:
        prefix = ss.read_rollout_bytes(prefix_path) if prefix_path else None
    except Exception:
        prefix = None
    if prefix is None:
        return data, sources, True
    prefix_data, prefix_sources, missing = effective_record(prefix_path, prefix[:end], home, depth + 1)
    prefix_sources[-1].update(size=len(prefix), sha256=hashlib.sha256(prefix).hexdigest())
    if not prefix_data.endswith(b"\n"):
        prefix_data += b"\n"
    return prefix_data + data, prefix_sources + sources, missing


def scan_rollout(data):
    """発言以外の、ターン・添付・項目の種類を 1 回読んで集める。"""
    turns = collections.OrderedDict()
    attachments = {}
    item_types = collections.Counter()
    ids = collections.Counter()
    first_ts = last_ts = None

    def turn(tid):
        if tid not in turns:
            turns[tid] = {"id": tid, "started": None, "ended": None, "model": None, "effort": None, "status": "incomplete"}
        return turns[tid]

    current = None
    for line_no, raw in ss._byte_lines(data, {"incomplete_tail": 0}):
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
            tid = ss._str(payload.get("turn_id")) or "turn-?"
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
                        attachments.setdefault(it["id"], {"items": found, "line": line_no, "ts": ts, "turn_id": tid})
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


# Codex が作る ID の欄。秘密は入らず、伏せ字をかけると別の ID が同じ文字になって対応が崩れるので、かけない
ID_KEYS = {"id", "turn_id", "forked_from_id", "sha256", "operations"}


def redact_tree(obj, key=None):
    """report の文字列（dict のキーも）に伏せ字をかける。ID_KEYS の欄の文字列（と、その list の中の文字列）は除く。"""
    if isinstance(obj, str):
        return obj if key in ID_KEYS else ss.redact(obj)
    if isinstance(obj, list):
        return [redact_tree(v, key) if isinstance(v, str) else redact_tree(v) for v in obj]
    if isinstance(obj, dict):
        return {(ss.redact(k) if isinstance(k, str) else k): redact_tree(v, k) for k, v in obj.items()}
    return obj


def build_report(path, data, home, other_files=0):
    # 会話の情報（作業フォルダ・ブランチ・版）と書きかけの最後の行は、選んだファイルそのものから取る。
    # revert の前半をつなぐと前半の session_meta が先に来て、ID を入れ直すと書きかけの行が落ちるため
    meta, _, own_counters = ss.parse_rollout(path, data)
    raw_meta = _first_meta(data) or {}
    data, sources, prefix_missing = effective_record(path, data, home)
    data = fill_turn_ids(data)
    _, messages, counters = ss.parse_rollout(path, data)
    scan = scan_rollout(data)
    tid = meta["thread_id"]
    rows, seen = [], set()
    for m in sorted(messages, key=lambda m: (m["line"], m["seq"])):  # parse_rollout は足した最終の回答を末尾に置く
        if m["msg_id"] in seen:
            continue
        seen.add(m["msg_id"])
        msg = {"id": m["msg_id"], "kind": m["kind"], "ts": m["ts"], "turn_id": m["turn_id"],
               "text": ss.redact(m["text"])}
        if m["kind"] == "user":
            msg["attachments"] = scan["attachments"].get(m["msg_id"], {}).get("items", [])
        rows.append((m["line"], msg))
    for mid, att in scan["attachments"].items():
        if mid not in seen:  # 本文の無い、添付だけの発言（parse_rollout は空の発言を返さない）
            rows.append((att["line"], {"id": mid, "kind": "user", "ts": att["ts"],
                                       "turn_id": att["turn_id"], "text": "",
                                       "attachments": att["items"]}))
    out_msgs = [msg for _, msg in sorted(rows, key=lambda r: r[0])]
    operations, artifacts, legacy_ops = collect_operations(data)
    unknown = {k: n for k, n in sorted(scan["item_types"].items())
               if k not in MESSAGE_ITEMS and k not in EVENT_ITEMS and k not in OPERATION_ITEMS}
    events = {k: scan["item_types"][k] for k in sorted(EVENT_ITEMS) if scan["item_types"].get(k)}
    report = {
        "schema_version": SCHEMA_VERSION, "extract_version": EXTRACT_VERSION, "redaction_version": ss._RULES_VERSION,
        "tz_offset_minutes": int(datetime.datetime.now().astimezone().utcoffset().total_seconds() // 60),
        "session": {"id": tid, "title": _red(thread_title(home, tid)), "started": scan["first_ts"], "ended": scan["last_ts"],
                    "cwd": _red(meta["cwd"]), "branch": _red(meta["branch"]), "originator": _red(meta["originator"]),
                    "source": _red(_source_name(raw_meta.get("source"))), "cli_version": _red(meta["cli_version"]),
                    "forked_from_id": _red(ss._str(raw_meta.get("forked_from_id"))), "agent": _red(meta["agent"])},
        "sources": sources,
        "coverage": {"lines": counters["lines"], "bad_json": counters["bad_json"], "shape_errors": counters["shape_errors"],
                     "incomplete_tail": own_counters["incomplete_tail"], "duplicated_ids": scan["duplicated_ids"],
                     "other_files": other_files, "unknown_items": unknown, "events": events,
                     "prefix_missing": prefix_missing, "legacy_operations": legacy_ops,
                     "subagents_included": False},
        "turns": scan["turns"],
        "messages": out_msgs,
        "operations": operations,
        "artifacts": artifacts,
    }
    return redact_tree(report)


# 操作として読む項目
OPERATION_ITEMS = {"CommandExecution", "FileChange", "McpToolCall", "WebSearch", "ImageView", "Extension"}


# --- extract: 操作と成果物 -------------------------------------------------------------------------

# 手元用に残す上限（plan の「機械で取るもの」）
OUTPUT_HEAD = 2000
OUTPUT_TAIL = 2000
DIFF_LIMIT = 20000
TARGET_LIMIT = 400
REDACT_MARGIN = 20000  # 切る前に伏せ字をかける余白（鍵の PEM や YAML のブロックより長く取る）

# シェルを包む形（bash -lc "..."、pwsh -Command "..."、cmd /c "..."）。中の文を操作の対象として見せる
_WRAPPERS = {"bash": ("-c", "-lc"), "zsh": ("-c", "-lc"), "sh": ("-c", "-lc"),
             "pwsh": ("-command", "-c"), "powershell": ("-command", "-c"), "cmd": ("/c",)}
# 「該当なし」（終了コード 1 が「見つからなかった」の意味）に分類するコマンドと、確かめたオプション。
# 引数を取るオプションは値で終わる。ほかのオプションが 1 つでもあれば分類しない
_SEARCH_OPTIONS = {
    "rg": ({"-n", "-i", "-S", "-F", "-w", "-l", "-c", "-u", "-uu", "--hidden", "--no-heading", "--line-number",
            "--ignore-case", "--fixed-strings", "--smart-case", "--files-with-matches", "--count"},
           {"-e", "-g", "--glob", "-t", "--type", "-C", "-A", "-B", "--regexp"}),
    "grep": ({"-n", "-i", "-r", "-R", "-F", "-E", "-w", "-l", "-c", "-H", "-h", "-s", "--line-number", "--ignore-case",
              "--recursive", "--fixed-strings", "--extended-regexp", "--files-with-matches", "--count"},
             {"-e", "-C", "-A", "-B", "--include", "--exclude", "--regexp"}),
    "findstr": ({"/s", "/i", "/n", "/l", "/r", "/m", "/p", "/b", "/e", "/x"}, set()),
}
_SHELL_META = re.compile(r"[|;&<>`\n]|\$\(")
# az のサブコマンドの目的
_AZ_PURPOSE = {
    ("repos", "pr", "create"): "作成", ("repos", "pr", "update"): "更新", ("repos", "pr", "list"): "参照",
    ("repos", "pr", "show"): "参照", ("boards", "work-item", "create"): "作成", ("boards", "work-item", "update"): "更新",
    ("boards", "work-item", "show"): "参照", ("boards", "work-item", "delete"): "削除", ("boards", "query"): "参照",
    ("pipelines", "run"): "作成", ("pipelines", "list"): "参照", ("pipelines", "show"): "参照",
    ("pipelines", "runs", "list"): "参照", ("pipelines", "runs", "show"): "参照", ("pipelines", "build", "queue"): "作成",
    ("pipelines", "build", "list"): "参照", ("pipelines", "build", "show"): "参照",
}
# ado.ps1 の write の道と方法の目的（skills/ado/scripts/ado.ps1 の書き込みの形）
_ADO_WRITE = [
    (re.compile(r"_apis/git/repositories/[^/]+/pullrequests/?(\?|$)", re.I), "POST", "作成"),
    (re.compile(r"_apis/git/repositories/[^/]+/pullrequests/\d+/threads", re.I), "POST", "作成"),
    (re.compile(r"_apis/git/repositories/[^/]+/pullrequests/\d+/?(\?|$)", re.I), "PATCH", "更新"),
    (re.compile(r"_apis/wit/workitems/\$[^/?]+", re.I), "POST", "作成"),
    (re.compile(r"_apis/wit/workitems/\d+/comments", re.I), "POST", "作成"),
    (re.compile(r"_apis/wit/workitems/\d+/?(\?|$)", re.I), "PATCH", "更新"),
    (re.compile(r"_apis/pipelines/\d+/runs/?(\?|$)", re.I), "POST", "作成"),
    (re.compile(r"_apis/build/builds/?(\?|$)", re.I), "POST", "作成"),
]
# ADO の MCP の tool の名前の動詞（server の名前に ado・azure・devops を含むとき）
_MCP_VERBS = {"get": "参照", "list": "参照", "search": "参照", "read": "参照", "query": "参照", "show": "参照",
              "create": "作成", "add": "作成", "run": "作成", "queue": "作成",
              "update": "更新", "link": "更新", "set": "更新", "vote": "更新", "complete": "更新",
              "delete": "削除", "remove": "削除"}


def _split(script, windows):
    """1 つのコマンドの文を語に分ける。分けられなければ None。"""
    import shlex
    try:
        return shlex.split(script, posix=not windows)
    except ValueError:
        return None


def _base(word):
    return re.split(r"[\\/]", word)[-1].lower().rsplit(".exe", 1)[0]


def command_script(command):
    """記録のコマンド（語の配列）から、見せる文と、それが PowerShell / cmd か。"""
    if isinstance(command, str):
        return command, False
    if not isinstance(command, list) or not all(isinstance(w, str) for w in command):
        return "", False
    if len(command) >= 3:
        name = _base(command[0])
        flags = _WRAPPERS.get(name)
        for i in range(1, len(command) - 1):
            if flags and command[i].lower() in flags:
                return " ".join(command[i + 1:]) if len(command) > i + 2 else command[i + 1], name in ("pwsh", "powershell", "cmd")
            if not command[i].startswith("-") and not command[i].startswith("/"):
                break
    return " ".join(command), False


def _search_no_match(script, windows):
    """終了コード 1 を「該当なし」と読める形か: パイプや連結の無い 1 つの rg / grep / findstr を、確かめたオプションだけで流した。"""
    if _SHELL_META.search(script):
        return False
    words = _split(script, windows)
    if not words:
        return False
    name = _base(words[0])
    if name not in _SEARCH_OPTIONS:
        return False
    flags, with_value = _SEARCH_OPTIONS[name]
    i = 1
    while i < len(words):
        w = words[i]
        low = w.lower() if name == "findstr" else w
        if name == "findstr" and low.startswith("/c:"):
            i += 1
            continue
        if low == "--":
            break
        if low in with_value:
            i += 2
            continue
        if "=" in low and low.split("=", 1)[0] in with_value:
            i += 1
            continue
        if low in flags:
            i += 1
            continue
        if name != "findstr" and re.fullmatch(r"-[A-Za-z]{2,}", w) and all("-" + ch in flags for ch in w[1:]):
            i += 1  # -rn のようにまとめた 1 文字のオプション
            continue
        if w.startswith("-") or (name == "findstr" and w.startswith("/")):
            return False
        i += 1
    return True


def _az_purpose(words):
    if not words or _base(words[0]) != "az":
        return None
    parts = [w for w in words[1:] if not w.startswith("-")]
    for n in (3, 2):
        key = tuple(parts[:n])
        if key in _AZ_PURPOSE:
            return {"route": "command", "tool": "az " + " ".join(key), "method": None, "purpose": _AZ_PURPOSE[key]}
    if parts[:1] and parts[0] in ("repos", "boards", "pipelines", "devops"):
        return {"route": "command", "tool": "az " + " ".join(parts[:3]), "method": None, "purpose": "不明"}
    return None


def _ado_purpose(words):
    """ado.ps1 の呼び出し（powershell -File ...ado.ps1 <mode> <path> [-Method ...] [-DryRun]）の目的。"""
    at = next((i for i, w in enumerate(words) if _base(w) == "ado.ps1" or w.lower().endswith("ado.ps1")), None)
    if at is None or at + 1 >= len(words):
        return None
    mode = words[at + 1].lower()
    rest = words[at + 2:]
    path = rest[0] if rest and not rest[0].startswith("-") else ""
    lower = [w.lower() for w in rest]
    method = rest[lower.index("-method") + 1].upper() if "-method" in lower and lower.index("-method") + 1 < len(rest) else None
    info = {"route": "command", "tool": "ado.ps1 " + mode, "method": method, "path": path}
    if mode in ("get", "query", "preview"):
        info["purpose"] = "参照"
    elif mode == "write" and "-dryrun" in lower:
        info["purpose"] = "参照"  # 承認のハッシュを出すだけで送らない
    elif mode == "write":
        info["purpose"] = next((p for rx, m, p in _ADO_WRITE if method == m and rx.search(path)), "不明")
    else:
        info["purpose"] = "不明"
    return info


def _mcp_purpose(it):
    if it.get("read_only_hint") is True:
        return "参照"
    server, tool = it.get("server"), it.get("tool")
    if isinstance(server, str) and isinstance(tool, str) and re.search(r"ado|azure|devops", server, re.I):
        for word in re.split(r"[_.\-]", tool.lower()):
            if word in _MCP_VERBS:
                return _MCP_VERBS[word]
    return "不明"


def _cut(text, head=OUTPUT_HEAD, tail=OUTPUT_TAIL):
    """伏せ字をかけてから、長い文字列の先頭と末尾を残す。(残した文字列, 切ったか, 元の長さ)

    切ってから伏せ字をかけると、切れ目をまたぐ秘密が半分ずつになって拾えないので、残す部分に REDACT_MARGIN 文字の
    余白を付けた範囲に伏せ字をかけてから切る。全文にかけないのは、数 MB の出力で遅くなるため（実際の記録の 1 本で 17 秒）。
    """
    if not isinstance(text, str):
        return None, False, 0
    if len(text) <= head + tail + 2 * REDACT_MARGIN:
        red = ss.redact(text)
        if len(red) <= head + tail:
            return red, False, len(text)
        start, end = red[:head], (red[-tail:] if tail else "")  # red[-0:] は全文になる
    else:
        start = ss.redact(text[:head + REDACT_MARGIN])[:head]
        end = ss.redact(text[-(tail + REDACT_MARGIN):])[-tail:] if tail else ""
    return start + "\n…（約 %d 文字を省略）…\n" % (len(text) - head - tail) + end, True, len(text)


def _duration_ms(value):
    if isinstance(value, dict) and isinstance(value.get("secs"), (int, float)):
        return int(value["secs"] * 1000 + (value.get("nanos") or 0) / 1e6)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return None


def _ms_to_iso(ms):
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _diff_counts(diff):
    added = removed = 0
    for row in diff.splitlines():
        if row.startswith("+") and not row.startswith("+++"):
            added += 1
        elif row.startswith("-") and not row.startswith("---"):
            removed += 1
    return added, removed


def _file_changes(changes):
    out = []
    for path, ch in sorted(changes.items()) if isinstance(changes, dict) else []:
        ch = ch if isinstance(ch, dict) else {}
        kind = ch.get("type") if ch.get("type") in ("add", "update", "delete") else "unknown"
        diff = ch.get("unified_diff") if isinstance(ch.get("unified_diff"), str) else None
        content = ch.get("content") if isinstance(ch.get("content"), str) else None
        if diff is not None:
            added, removed = _diff_counts(diff)
        elif kind == "add" and content is not None:
            added, removed = len(content.splitlines()), 0
        else:
            added = removed = None
        shown = diff if diff is not None else (content if kind == "add" else None)
        text, cut, size = _cut(shown, DIFF_LIMIT, 0) if shown is not None and len(shown) > DIFF_LIMIT else (
            ss.redact(shown) if shown is not None else None, False, len(shown or ""))
        move = ch.get("move_path") if isinstance(ch.get("move_path"), str) else None
        out.append({"path": _red(path), "kind": kind, "added": added, "removed": removed, "diff": text,
                    "diff_cut": cut, "diff_chars": size, "moved_to": _red(move)})
    return out


def _mcp_text(result):
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return "\n".join(el["text"] for el in result["content"] if isinstance(el, dict) and isinstance(el.get("text"), str))
    return None


def operation(it, ts, turn_id):
    """item_completed の操作の項目を、report.json の 1 件にする。操作でなければ None。"""
    typ = it.get("type")
    if typ not in OPERATION_ITEMS:
        return None
    op = {"id": ss._str(it.get("id")), "type": typ, "ts": ts, "started": _ms_to_iso(it.get("_started_at_ms")),
          "turn_id": turn_id, "route": None, "target": None, "status": "unknown", "exit_code": None, "duration_ms": None,
          "purpose": None, "tool": None, "method": None, "output": None, "output_cut": False, "output_chars": 0,
          "files": [], "machine": True}
    st = it.get("status") if isinstance(it.get("status"), str) else None
    if typ == "CommandExecution":
        script, windows = command_script(it.get("command"))
        code = it.get("exit_code") if isinstance(it.get("exit_code"), int) and not isinstance(it.get("exit_code"), bool) else None
        op.update(route="command", target=ss.redact(script[:TARGET_LIMIT]), exit_code=code,
                  duration_ms=_duration_ms(it.get("duration")))
        if st == "declined":
            op["status"] = "declined"
        elif st == "completed" and code == 0:
            op["status"] = "ok"
        elif code == 1 and _search_no_match(script, windows):
            op["status"] = "no_match"
        elif st in ("failed", "completed") and code is not None:
            op["status"] = "failed"
        elif st == "inProgress" or st == "in_progress":
            op["status"] = "incomplete"
        words = _split(script, windows) or []
        ext = _ado_purpose(words) or _az_purpose(words)
        if ext:
            op.update(route="command", tool=_red(ext["tool"]), method=ext["method"], purpose=ext["purpose"], external=True)
        op["output"], op["output_cut"], op["output_chars"] = _cut(it.get("aggregated_output"))
    elif typ == "McpToolCall":
        server, tool = ss._str(it.get("server")) or "?", ss._str(it.get("tool")) or "?"
        res = it.get("result")
        error = st == "failed" or (isinstance(res, dict) and res.get("isError") is True)
        op.update(route="mcp", target=_red("%s.%s" % (server, tool)), tool=_red("%s.%s" % (server, tool)),
                  purpose=_mcp_purpose(it), status="failed" if error else ("ok" if st == "completed" else "unknown"),
                  duration_ms=_duration_ms(it.get("duration")), external=True)
        op["output"], op["output_cut"], op["output_chars"] = _cut(_mcp_text(res))
    elif typ == "WebSearch":
        act = it.get("action") if isinstance(it.get("action"), dict) else {}
        query = ss._str(it.get("query")) or ss._str(act.get("query")) or ss._str(act.get("url")) or ""
        op.update(route="web", target=ss.redact(query[:TARGET_LIMIT]), tool="web." + (ss._str(act.get("type")) or "?"),
                  purpose="参照", status="ok", external=True)
    elif typ == "FileChange":
        op["files"] = _file_changes(it.get("changes"))
        op.update(route="file", target=", ".join(f["path"] for f in op["files"])[:TARGET_LIMIT],
                  status={"completed": "ok", "failed": "failed", "declined": "declined"}.get(st, "unknown"))
    elif typ == "ImageView":
        path = ss._str(it.get("path")) or ""
        op.update(route="image", target=_red(re.split(r"[\\/]", path)[-1]), status="ok", body=False)
    elif typ == "Extension":
        op.update(route="extension", target=None, status={"completed": "ok", "failed": "failed"}.get(st, "unknown"))
    op.setdefault("external", False)
    return op


_PR_URL = re.compile(r"/_git/[^/\s\"']+/pullrequest/(\d+)", re.I)
_WI_URL = re.compile(r"/_workitems/edit/(\d+)", re.I)


def _artifacts_in(text):
    """MCP の結果とコマンドの出力にはっきり出た PR と Work Item（JSON の形か、ADO の URL）。"""
    found = []
    try:
        data = json.loads(text) if text and text.lstrip()[:1] in "{[" else None
    except ValueError:
        data = None
    for obj in (data if isinstance(data, list) else [data]) if data is not None else []:
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("pullRequestId"), int):
            found.append(("PR", obj["pullRequestId"], ss._str(obj.get("title")), ss._str(obj.get("status"))))
        fields = obj.get("fields") if isinstance(obj.get("fields"), dict) else None
        if fields and isinstance(obj.get("id"), int) and "System.WorkItemType" in fields:
            found.append(("Work Item", obj["id"], ss._str(fields.get("System.Title")), ss._str(fields.get("System.State"))))
    for rx, kind in ((_PR_URL, "PR"), (_WI_URL, "Work Item")):
        for m in rx.finditer(text or ""):
            found.append((kind, int(m.group(1)), None, None))
    return found


def collect_operations(data):
    """記録の操作を行の順に並べる。同じ ID の項目は、最後のものを使う（作り直された完了の記録）。"""
    order, ops, raw_outputs = [], {}, {}
    current = None
    legacy = collections.Counter()
    for line_no, raw in ss._byte_lines(data, {"incomplete_tail": 0}):
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item.get("type") == "turn_context":
            current = ss._str(payload.get("turn_id")) or current
        if item.get("type") != "event_msg":
            continue
        et = payload.get("type")
        if et in ("exec_command_end", "patch_apply_end", "mcp_tool_call_end", "web_search_end"):
            legacy[et] += 1  # 古い形式の操作。今は取り出さず、coverage に数える
            continue
        if et != "item_completed" or not isinstance(payload.get("item"), dict):
            continue
        it = dict(payload["item"])
        it["_started_at_ms"] = payload.get("started_at_ms")
        ts = item.get("timestamp") if isinstance(item.get("timestamp"), str) else None
        op = operation(it, ts, ss._str(payload.get("turn_id")) or current)
        if op is None:
            continue
        key = op["id"] or "L%d" % line_no
        op["id"] = key
        if key not in ops:
            order.append(key)
        ops[key] = op
        if it.get("type") == "McpToolCall":
            raw_outputs[key] = _mcp_text(it.get("result"))
        elif it.get("type") == "CommandExecution":
            raw_outputs[key] = it.get("aggregated_output") if isinstance(it.get("aggregated_output"), str) else None
    operations = [ops[k] for k in order]
    artifacts = {}
    for op in operations:
        for kind, num, title, status in _artifacts_in(raw_outputs.get(op["id"])):
            a = artifacts.setdefault((kind, num), {"kind": kind, "id": num, "title": None, "status": None, "seen_at": None,
                                                   "operations": []})
            a["title"] = _red(title) or a["title"]
            a["status"] = _red(status) or a["status"]
            a["seen_at"] = op["ts"]
            if op["id"] not in a["operations"]:
                a["operations"].append(op["id"])
    return operations, [artifacts[k] for k in sorted(artifacts)], dict(sorted(legacy.items()))




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
    if args.current and not current:
        print("CODEX_THREAD_ID が無い。今の会話の ID が分からないので、ID を渡す", file=sys.stderr)
        return 1
    try:
        if args.current:
            path, others = find_rollout(home, thread_id=current)
        elif args.last:
            path, others = find_rollout(home, last_cwd=os.getcwd(), exclude=current)
        else:
            path, others = find_rollout(home, thread_id=args.thread_id)
    except UnreadableRecords as e:
        print("読めない記録が %d 本ある（圧縮された記録は Python 3.14 以上の compression.zstd で読む）。正しい会話を選べたか"
              "分からないので止める: %s" % (len(e.args[0]), e.args[0][0]), file=sys.stderr)
        return 1
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
    print("書いた: %s（発言 %d 件、ターン %d 件、操作 %d 件、成果物 %d 件）" % (
        os.path.join(out, "report.json"), len(report["messages"]), len(report["turns"]), len(report["operations"]),
        len(report["artifacts"])))
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
