"""Codex の 1 つの会話を JSON にまとめ、決まったテンプレートの単体の HTML にする。

    python session_report.py probe [--since 2026-10-01]

probe: 記録の形（行の種類、項目の種類とキー名、件数、有無）だけを数えて出す。本文・コマンド・出力・パス・引数・検索語・
差分は出さない。出す値は、Codex のコードと設定が決める種類の値（type・phase・status・MCP の server と tool の名前・版）で、
英数字の 40 文字までの形のものだけ。
会社の PC の記録が、この PC の記録と同じ形かを、中身を見せずに確かめるために使う。

記録の読み方と伏せ字は session-search のものを使う（同じ skill 集の ../../session-search/scripts/session_search.py）。
"""

import argparse
import collections
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


# --- 入口 -----------------------------------------------------------------------------------------


def _date(value):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise argparse.ArgumentTypeError("日付は 2026-10-01 の形で書く")
    return value


def build_parser():
    p = argparse.ArgumentParser(prog="session_report.py", description="Codex の 1 つの会話を単体の HTML にする")
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("probe", help="記録の形だけを数えて出す（本文は出さない）")
    pr.add_argument("--since", type=_date, help="この日付（ファイル名の日付）以降の記録だけを数える")
    pr.set_defaults(func=cmd_probe)
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
    if args.command == "probe":
        code = maybe_reexec(probe_paths(ss.codex_home(), args.since), argv)
        if code is not None:
            return code
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
