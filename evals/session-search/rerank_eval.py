"""並べ直しのモデル・件数・渡す文字数を比べる（README の「並べ直しは…を比べ」の行を再現する道具）。

run_eval.py が作った索引（--work の index.sqlite）を使い、本番の検索（並べ直しなし）の上位 K 件を、
指定した reranker で並べ直したときの順位を数える。

  python rerank_eval.py --queries queries.json --db <work>/index.sqlite --codex-home <work>/codex_home \\
      --models <モデルのフォルダ>,<モデルのフォルダ> --k 10 --window 1000 [--mock]

--mock: モックの問い（正解が t001 などの名前）のとき。make_rollouts が付けた会話の ID に置き換える。
"""

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "skills", "session-search", "scripts"))
sys.path.insert(0, HERE)
import session_search as ss  # noqa: E402


def threads_of(rows):
    out = []
    for r in rows:
        if r["thread_id"] not in out:
            out.append(r["thread_id"])
    return out


def report(name, ranked, base):
    ranks = []
    for (q, _, _), rows in zip(base, ranked):
        gold = set(q["gold"])
        ranks.append(next((i for i, t in enumerate(threads_of(rows), 1) if t in gold), None))
    n = len(ranks)
    print("%-48s MRR %.3f  R@1 %.3f  R@5 %.3f" % (
        name, sum(1.0 / r for r in ranks if r) / n, sum(1 for r in ranks if r == 1) / n,
        sum(1 for r in ranks if r and r <= 5) / n), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--queries", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--codex-home", required=True)
    p.add_argument("--models", required=True, help="reranker のフォルダをカンマで区切って")
    p.add_argument("--k", type=int, default=ss.RERANK_K)
    p.add_argument("--window", type=int, default=ss.RERANK_WINDOW)
    p.add_argument("--mock", action="store_true")
    args = p.parse_args()
    qs = json.load(open(args.queries, encoding="utf-8"))["queries"]
    for q in qs:
        q["gold"] = q["gold"] if isinstance(q["gold"], list) else [q["gold"]]
    if args.mock:
        import make_rollouts
        for q in qs:
            q["gold"] = [make_rollouts.thread_uuid(g) for g in q["gold"]]
    idx = ss.Index(args.db)
    home = os.path.abspath(args.codex_home)
    sem = ss.Semantic()
    base = []
    for q in qs:
        terms = ss.parse_query(q["keywords"])
        res, _, _ = idx.search(home, terms, ss.DEFAULT_KINDS, question=q["question"], limit=args.k, sem=sem, rerank=False)
        base.append((q, terms, [r for _, r in res]))
    report("並べ直しなし（上位 %d 件）" % args.k, [rows for _, _, rows in base], base)
    from sentence_transformers import CrossEncoder
    for path in args.models.split(","):
        model = CrossEncoder(path, max_length=512, device="cpu")
        times, ranked = [], []
        for q, terms, rows in base:
            pairs = [(q["question"], ss.rerank_window(r["text"], terms, args.window)) for r in rows]
            start = time.time()
            scores = model.predict(pairs, batch_size=16, show_progress_bar=False) if pairs else []
            times.append(time.time() - start)
            ranked.append([rows[i] for i in sorted(range(len(rows)), key=lambda i: -scores[i])])
        name = os.path.basename(path.rstrip("/").split("/snapshots/")[0]).replace("models--", "")
        report("%s（並べ直しの時間の中央値 %.2f 秒）" % (name, statistics.median(times) if times else 0), ranked, base)


if __name__ == "__main__":
    main()
