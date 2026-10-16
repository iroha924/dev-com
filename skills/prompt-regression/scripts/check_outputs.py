"""プロンプトの代表ケースについて、モデルの出力が期待条件を満たすかを判定する。

モデルは呼ばない。出力はプロジェクト側の実行手段で <outputs>/<case id>.txt に保存しておく。
--baseline に変更前の出力ディレクトリを渡すと、変更前は通って変更後に落ちた条件（回帰）を分けて出す。

cases ファイルの形:
{
  "cases": [
    {
      "id": "summary-short",
      "input": "入力の本文、または入力ファイルへの相対パス（このスクリプトは読まない）",
      "expect": {
        "must_include": ["..."],
        "must_not_include": ["..."],
        "regex": ["^## 要約"],
        "json": true,
        "json_required_keys": ["summary", "risk"],
        "max_chars": 800
      }
    }
  ]
}
"""

import argparse
import json
import os
import re
import sys

KNOWN_CHECKS = {"must_include", "must_not_include", "regex", "json", "json_required_keys", "max_chars"}


class CasesError(Exception):
    pass


def load_cases(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise CasesError("cases が空か、{\"cases\": [...]} の形ではない")
    ids = set()
    for c in cases:
        cid = c.get("id")
        if not isinstance(cid, str) or not re.match(r"^[A-Za-z0-9_.-]+$", cid):
            raise CasesError("id はファイル名に使える英数字・_ . - だけにする: %r" % cid)
        if cid in ids:
            raise CasesError("id が重複: %s" % cid)
        ids.add(cid)
        unknown = set(c.get("expect", {})) - KNOWN_CHECKS
        if unknown:
            raise CasesError("%s: 知らない条件 %s（綴りを確かめる）" % (cid, ", ".join(sorted(unknown))))
    return cases


def evaluate(expect, text):
    """条件ごとに (名前, 合否, 説明) を返す。"""
    results = []
    for s in expect.get("must_include", []):
        results.append(("must_include:%s" % s, s in text, ""))
    for s in expect.get("must_not_include", []):
        results.append(("must_not_include:%s" % s, s not in text, ""))
    for pattern in expect.get("regex", []):
        results.append(("regex:%s" % pattern, re.search(pattern, text, re.MULTILINE) is not None, ""))
    parsed = None
    if expect.get("json") or expect.get("json_required_keys"):
        try:
            parsed = json.loads(text)
            results.append(("json", True, ""))
        except ValueError as e:
            results.append(("json", False, str(e)))
    for key in expect.get("json_required_keys", []):
        ok = isinstance(parsed, dict) and key in parsed
        results.append(("json_required_keys:%s" % key, ok, ""))
    if "max_chars" in expect:
        results.append(("max_chars:%d" % expect["max_chars"], len(text) <= expect["max_chars"], "%d 文字" % len(text)))
    return results


def run(cases, outputs):
    report = {}
    for c in cases:
        path = os.path.join(outputs, c["id"] + ".txt")
        if not os.path.isfile(path):
            report[c["id"]] = {"missing": True, "checks": []}
            continue
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        report[c["id"]] = {
            "missing": False,
            "checks": [{"check": n, "ok": ok, "note": note} for n, ok, note in evaluate(c.get("expect", {}), text)],
        }
    return report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cases")
    p.add_argument("outputs", help="変更後の出力ディレクトリ")
    p.add_argument("--baseline", help="変更前の出力ディレクトリ")
    args = p.parse_args(argv)
    try:
        cases = load_cases(args.cases)
    except (CasesError, ValueError) as e:
        print("cases ファイルの誤り: %s" % e, file=sys.stderr)
        return 2

    after = run(cases, args.outputs)
    failed = [cid for cid, r in after.items() if r["missing"] or not all(c["ok"] for c in r["checks"])]
    result = {"cases": len(cases), "failed": failed, "results": after}
    if args.baseline:
        before = run(cases, args.baseline)
        regressions = []
        for cid, r in after.items():
            prev = {c["check"]: c["ok"] for c in before[cid]["checks"]}
            for c in r["checks"]:
                if prev.get(c["check"]) is True and not c["ok"]:
                    regressions.append({"case": cid, "check": c["check"]})
        result["regressions"] = regressions
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
