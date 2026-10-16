"""Azure Pipelines のジョブログから、エラーの行と前後の文脈を抜き出す。

入力はログのテキストか、Build Logs API が JSON で返す {"count": N, "value": ["行", ...]}。
--baseline に成功時（または前回失敗時）のログを渡すと、今回にしか無いエラーを分けて出す。
比べるときは行頭のタイムスタンプを除き、数字と 16 進の ID を 0 に寄せた「シグネチャ」で比べる。
"""

import argparse
import json
import re
import sys

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z\s?")
ERROR_LINE = re.compile(
    r"##\[error\]|Traceback \(most recent call last\)|^\s*(E\s{2,}|FAILED\b|FAIL:|ERROR\b|ERROR:)"
    r"|\bfatal:|\b[A-Z][A-Za-z]+(Error|Exception)\b:|exited with code [1-9]|exit code [1-9]",
)
HEX = re.compile(r"\b[0-9a-f]{7,}\b", re.IGNORECASE)
NUMBER = re.compile(r"\d+")


def read_lines(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            return [str(v) for v in data["value"]]
    return text.splitlines()


def strip_ts(line):
    return TIMESTAMP.sub("", line)


def signature(line):
    s = strip_ts(line).strip()
    s = HEX.sub("0", s)
    return NUMBER.sub("0", s)


def extract(lines, context=3):
    hits = []
    for i, line in enumerate(lines):
        if ERROR_LINE.search(strip_ts(line)):
            lo, hi = max(0, i - context), min(len(lines), i + context + 1)
            hits.append({
                "line": i + 1,
                "text": strip_ts(line).rstrip(),
                "signature": signature(line),
                "context": [strip_ts(x).rstrip() for x in lines[lo:hi]],
            })
    return hits


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", help="失敗したジョブのログ")
    p.add_argument("--baseline", help="比べる相手のログ（直近の成功など）")
    p.add_argument("--context", type=int, default=3)
    p.add_argument("--max", type=int, default=30, help="出すエラーの上限")
    args = p.parse_args(argv)

    lines = read_lines(args.log)
    hits = extract(lines, args.context)
    result = {"log": args.log, "lines": len(lines), "error_count": len(hits), "first_error": hits[0] if hits else None}
    if args.baseline:
        base_sigs = {h["signature"] for h in extract(read_lines(args.baseline), 0)}
        result["baseline"] = args.baseline
        result["only_in_log"] = [h for h in hits if h["signature"] not in base_sigs][: args.max]
        result["also_in_baseline"] = sorted({h["signature"] for h in hits if h["signature"] in base_sigs})
    else:
        result["errors"] = hits[: args.max]
    result["truncated"] = len(hits) > args.max
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
