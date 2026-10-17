"""Codex が書いた追記（annotations.json）を、全文を読んで書いた正解（gold）と比べて数える。

    python evals/session-report/compare.py <正解.json> <Codex の追記.json>
    python evals/session-report/compare.py --dir <fixtures.py build のフォルダ>   # <名前>/candidate.json を gold/<名前>.json と比べる

数えるもの（plan の S10）:
  - 依頼の取りこぼし: 正解の依頼（依頼した持ち主の発言 at で対応を付ける）が、追記に無い
  - 条件の取りこぼし: 対応した依頼の中で、正解の条件に近い条件が追記に無い
  - 決定の取りこぼし: 正解の決定に近い決定が、追記に無い
  - 「できた」の誤判定: 正解ができたではない依頼・条件を、追記ができたにした
近さは文の 2 文字ずつの重なり（Jaccard 係数）で測り、SIMILAR 以上を同じとみなす。意味の同じさの判定ではないので、
数は目安で、取りこぼしと出たものは中身を読んで確かめる。
"""

import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SIMILAR = 0.3

_spec = importlib.util.spec_from_file_location("session_report",
                                               os.path.join(ROOT, "skills", "session-report", "scripts", "session_report.py"))
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def _bigrams(text):
    s = "".join((text or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similarity(a, b):
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def _match(gold_items, cand_items, key=lambda x: x["text"]):
    """正解の各項目に、いちばん近い追記の項目を 1 対 1 で付ける（SIMILAR 未満は付けない）。"""
    pairs = sorted(((similarity(key(g), key(c)), gi, ci) for gi, g in enumerate(gold_items) for ci, c in enumerate(cand_items)),
                   reverse=True)
    used_g, used_c, out = set(), set(), {}
    for score, gi, ci in pairs:
        if score < SIMILAR or gi in used_g or ci in used_c:
            continue
        used_g.add(gi)
        used_c.add(ci)
        out[gi] = ci
    return out


def compare(gold, cand):
    result = {"requests": 0, "requests_missed": [], "criteria": 0, "criteria_missed": [], "decisions": 0, "decisions_missed": [],
              "done_misjudged": []}
    cand_by_at, gold_by_at = {}, {}
    for r in cand.get("requests") or []:
        cand_by_at.setdefault(r.get("at"), []).append(r)
    live = [g for g in gold.get("requests") or [] if g.get("lifecycle") != "取り下げ"]
    for g in live:
        gold_by_at.setdefault(g["at"], []).append(g)
    pairs = {}
    for at, golds in gold_by_at.items():  # 同じ発言の依頼どうしは 1 対 1 で対応を付ける（追記の 1 つを 2 度使わない）
        candidates = cand_by_at.get(at, [])
        m = _match(golds, candidates)
        if len(golds) == 1 and not m and len(candidates) == 1:
            m = {0: 0}  # 同じ発言の依頼が 1 つずつなら、文が離れていても同じ依頼とみなす
        for gi, ci in m.items():
            pairs[id(golds[gi])] = candidates[ci]
    for g in live:
        result["requests"] += 1
        result["criteria"] += len(g["criteria"])
        c = pairs.get(id(g))
        if c is None:
            result["requests_missed"].append(g["id"])
            result["criteria_missed"] += ["%s: %s" % (g["id"], x["text"]) for x in g["criteria"]]
            continue
        if sr.request_status(c) == "できた" and sr.request_status(g) != "できた":
            result["done_misjudged"].append("%s（正解: %s）" % (g["id"], sr.request_status(g)))
        crit = _match(g["criteria"], c.get("criteria") or [])
        for gi, gc in enumerate(g["criteria"]):
            if gi not in crit:
                result["criteria_missed"].append("%s: %s" % (g["id"], gc["text"]))
            elif c["criteria"][crit[gi]].get("status") == "できた" and gc["status"] != "できた":
                result["done_misjudged"].append("%s の条件「%s」（正解: %s）" % (g["id"], gc["text"], gc["status"]))
    gd = gold.get("decisions") or []
    result["decisions"] = len(gd)
    dm = _match(gd, cand.get("decisions") or [])
    result["decisions_missed"] = [d["id"] + ": " + d["text"] for i, d in enumerate(gd) if i not in dm]
    return result


def summary_line(name, r):
    return "%s: 依頼 %d のうち取りこぼし %d・条件 %d のうち %d・決定 %d のうち %d・「できた」の誤判定 %d" % (
        name, r["requests"], len(r["requests_missed"]), r["criteria"], len(r["criteria_missed"]), r["decisions"],
        len(r["decisions_missed"]), len(r["done_misjudged"]))


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("gold", nargs="?")
    p.add_argument("candidate", nargs="?")
    p.add_argument("--dir", help="fixtures.py build のフォルダ。<名前>/candidate.json を gold/<名前>.json と比べる")
    p.add_argument("--json", action="store_true", help="中身を JSON で出す")
    args = p.parse_args(argv)
    pairs = []
    if args.dir:
        for name in sorted(os.listdir(args.dir)):
            cand = os.path.join(args.dir, name, "candidate.json")
            if os.path.isfile(cand):
                pairs.append((name, os.path.join(HERE, "gold", name + ".json"), cand))
    elif args.gold and args.candidate:
        pairs.append((os.path.basename(args.candidate), args.gold, args.candidate))
    else:
        p.error("正解と追記のファイルか、--dir を渡す")
    out = {}
    for name, gold, cand in pairs:
        out[name] = compare(read(gold), read(cand))
        if not args.json:
            print(summary_line(name, out[name]))
            for key in ("requests_missed", "criteria_missed", "decisions_missed", "done_misjudged"):
                for x in out[name][key]:
                    print("  %s: %s" % (key, x))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
