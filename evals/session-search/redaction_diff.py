"""伏せ字の規則を変えたとき、前のバージョンでは伏せていて、新しいバージョンで見えるようになった部分を数える。

  python redaction_diff.py --old <前の session_search.py> [--codex-home <CODEX_HOME>] [--corpus corpus.json] [--tests]

文は 3 か所から集める。--codex-home は記録の発言、--corpus はモックの会話、--tests はテストの中で redact に渡す文字列。
出すのは件数と、見えるようになった部分の文字の種類の形（英字・数字・記号の別）だけで、本文は出さない。
見えるようになった部分が 1 件でもあれば、終了コード 1 で終わる（伏せすぎを直した結果なら、形を見て確かめる）。
"""

import argparse
import ast
import glob
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
NEW = os.path.join(ROOT, "skills", "session-search", "scripts", "session_search.py")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def texts_from_home(ss, home):
    for path in glob.glob(os.path.join(home, "**", "*.jsonl*"), recursive=True):
        data = ss.read_rollout_bytes(path)
        if data is None:
            continue
        for m in ss.parse_rollout(path, data)[1]:
            yield m["text"]


def texts_from_corpus(path):
    for thread in json.load(open(path, encoding="utf-8"))["threads"]:
        for turn in thread.get("turns", []):
            for value in (turn.values() if isinstance(turn, dict) else turn):
                if isinstance(value, str):
                    yield value


def texts_from_tests():
    """テストの中の文字列の定数（redact に渡す例は、どれも文字列の定数で書いてある）。"""
    tree = ast.parse(open(os.path.join(ROOT, "tests", "test_session_search.py"), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) >= 6:
            yield node.value


def variants(text):
    """前の直しで漏れた形の変化を、自動で作る（前に記号を付ける、引用符の中に \\"・""・\\' を入れる）。"""
    yield text
    for head in ("-", ".", "+", "x"):
        yield head + text
    for quote, inserts in (('"', ('\\"', '""')), ("'", ("\\'", "''"))):
        at = text.find(quote)
        if at >= 0 and text.find(quote, at + 1) > at + 1:
            for ins in inserts:
                yield text[:at + 2] + ins + "Zq9w" + text[at + 2:]


def shape(s):
    return re.sub(r"(.)\1+", r"\1", "".join("a" if c.islower() else "A" if c.isupper() else "9" if c.isdigit() else c
                                             for c in s))[:40]


def revealed(text, old_out, new_out):
    """old で伏せていて new で見える、元の文の英数字の並び（4 文字以上）。"""
    words = set(re.findall(r"[A-Za-z0-9]{4,}", text))
    return sorted(w for w in words if w in new_out and w not in old_out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="比べる前のバージョンの session_search.py")
    ap.add_argument("--codex-home")
    ap.add_argument("--corpus")
    ap.add_argument("--tests", action="store_true")
    args = ap.parse_args()
    old, new = load(args.old, "ss_old"), load(NEW, "ss_new")
    sources = []
    if args.codex_home:
        sources.append(("記録", texts_from_home(new, args.codex_home)))
    if args.corpus:
        sources.append(("モック", texts_from_corpus(args.corpus)))
    if args.tests:
        sources.append(("テスト", texts_from_tests()))
    bad = 0
    for name, texts in sources:
        n = changed = 0
        shapes = []
        for text in (v for base in texts for v in variants(base)):
            n += 1
            o, w = old.redact(text), new.redact(text)
            if o == w:
                continue
            changed += 1
            for word in revealed(text, o, w):
                shapes.append(shape(word))
        bad += len(shapes)
        print("%s: %d 件のうち出力が変わった %d 件、見えるようになった語 %d 個" % (name, n, changed, len(shapes)))
        for s in sorted(set(shapes))[:30]:
            print("  ", s)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
