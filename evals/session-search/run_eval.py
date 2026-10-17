"""会話の検索の作り方を、同じ問いと正解で比べる。

    python run_eval.py --corpus corpus.json --queries queries.json --work <作業フォルダ> [--methods product,product-norerank,product-trigram]

問い（queries.json）は {"queries": [{"id", "question", "keywords": [...], "gold": ["t001", ...], "type"}]}。
question は人が言う自然な文、keywords は Codex が検索に打つ語。正解は会話（スレッド）の単位で数える。

指標（会話の単位）: Recall@1 / @5 / @10、MRR@10、nDCG@10。問いの種類ごとにも出す。
埋め込みや形態素解析のライブラリが無い方法は飛ばす。
"""

import argparse
import collections
import datetime
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "session-search", "scripts"))
sys.path.insert(0, HERE)

import session_search as ss  # noqa: E402
import make_rollouts  # noqa: E402

K_RRF = 60


# --- 文書 ----------------------------------------------------------------------------------------


def load_messages(idx, home):
    rows = idx.db.execute(
        "SELECT id, thread_id, kind, text, norm, ts FROM messages WHERE codex_home = ? AND kind IN ('user','final','memory')",
        (home,)).fetchall()
    return [dict(r) for r in rows]


def threads_from_ranked(ranked_msg_ids, msg_thread):
    seen, out = set(), []
    for mid in ranked_msg_ids:
        t = msg_thread[mid]
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# --- 方法: 索引そのもの（本番の検索） -------------------------------------------------------------------


def method_index(idx, home, terms, mode):
    """標準ライブラリだけの方式（trigram の BM25、どれかの単語を含めば当たる）。"""
    results, _, _ = idx.search(home, terms, ss.DEFAULT_KINDS, limit=1000, sem=None)
    return [r["id"] for _, r in results]


# --- 方法: 形態素解析 + BM25 ----------------------------------------------------------------------------


class Tokenizer:
    STOP_POS = {"助詞", "助動詞", "補助記号", "記号", "空白", "接頭辞", "接尾辞"}

    def __init__(self, kind):
        self.kind = kind
        if kind == "sudachi":
            from sudachipy import dictionary, tokenizer
            self.tok = dictionary.Dictionary(dict="core").tokenizer(mode=tokenizer.Tokenizer.SplitMode.B)
        else:
            from janome.tokenizer import Tokenizer as JT
            self.tok = JT()

    def __call__(self, text):
        out = []
        text = ss.normalize(text)
        if self.kind == "sudachi":
            for m in self.tok.tokenize(text[:20000]):
                if m.part_of_speech()[0] in self.STOP_POS:
                    continue
                w = m.normalized_form() or m.surface()
                if w.strip():
                    out.append(w)
        else:
            for t in self.tok.tokenize(text[:20000]):
                pos = t.part_of_speech.split(",")[0]
                if pos in ("助詞", "助動詞", "記号"):
                    continue
                w = t.base_form if t.base_form != "*" else t.surface
                if w.strip():
                    out.append(w)
        return out


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [collections.Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = collections.Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def scores(self, query):
        out = []
        for i, c in enumerate(self.tf):
            s = 0.0
            for w in query:
                if w in c:
                    f = c[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            out.append(s)
        return out


# --- 方法: 埋め込み --------------------------------------------------------------------------------

MODELS = {
    "ruri-v3-30m": ("cl-nagoya/ruri-v3-30m", "検索クエリ: ", "検索文書: "),
    "ruri-v3-70m": ("cl-nagoya/ruri-v3-70m", "検索クエリ: ", "検索文書: "),
    "ruri-v3-130m": ("cl-nagoya/ruri-v3-130m", "検索クエリ: ", "検索文書: "),
    "static-ja": ("hotchpotch/static-embedding-japanese", "", ""),
    "e5-small": ("intfloat/multilingual-e5-small", "query: ", "passage: "),
}


def cache_key(name, messages):
    """埋め込みのキャッシュの名前。発言の ID と本文から作る（件数だけだと、中身が変わっても古いものを使う）。"""
    import hashlib
    h = hashlib.sha256()
    for m in messages:
        h.update(("%s\x1f%s\x1e" % (m["id"], m["text"])).encode("utf-8"))
    return "%s-%s" % (name, h.hexdigest()[:16])


class Embedder:
    def __init__(self, name, cache_dir):
        from sentence_transformers import SentenceTransformer
        self.repo, self.qp, self.dp = MODELS[name]
        self.name = name
        self.model = SentenceTransformer(self.repo, device="cpu", trust_remote_code=False)
        self.cache_dir = cache_dir

    def docs(self, messages):
        import numpy as np
        path = os.path.join(self.cache_dir, "emb-%s.npy" % cache_key(self.name, messages))
        if os.path.exists(path):
            return np.load(path)
        t0 = time.time()
        vecs = self.model.encode([self.dp + m["text"][:4000] for m in messages], batch_size=16,
                                 normalize_embeddings=True, show_progress_bar=False)
        self.encode_seconds = time.time() - t0
        np.save(path, vecs)
        return vecs

    def query(self, text):
        return self.model.encode([self.qp + text], normalize_embeddings=True, show_progress_bar=False)[0]


# --- 指標 ----------------------------------------------------------------------------------------


def metrics(ranked_threads, gold):
    gold = set(gold)
    first = next((i for i, t in enumerate(ranked_threads[:10]) if t in gold), None)
    dcg = sum(1 / math.log2(i + 2) for i, t in enumerate(ranked_threads[:10]) if t in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    return {"R@1": float(first is not None and first < 1), "R@5": float(first is not None and first < 5),
            "R@10": float(first is not None), "MRR": 1 / (first + 1) if first is not None else 0.0,
            "nDCG": dcg / idcg if idcg else 0.0}


def rrf(*lists):
    scores = collections.defaultdict(float)
    for lst in lists:
        for rank, item in enumerate(lst[:200]):
            scores[item] += 1.0 / (K_RRF + rank + 1)
    return [m for m, _ in sorted(scores.items(), key=lambda x: -x[1])]


# --- 本体 ----------------------------------------------------------------------------------------


def needs_semantic(wanted):
    """単語の索引と埋め込みを作る要るか（trigram の方式だけなら要らない）。"""
    return not wanted or any(w in ("product", "product-norerank") for w in wanted)


def mock_home(work):
    """モックの CODEX_HOME。相対の --work でも絶対パスにする（rerank_eval が絶対パスで探すので、索引の home をそろえる）。"""
    return os.path.abspath(os.path.join(work, "codex_home"))


def wrrf(lists, weights, k):
    scores = collections.defaultdict(float)
    for lst, w in zip(lists, weights):
        for rank, item in enumerate(lst[:200]):
            scores[item] += w / (k + rank + 1)
    return [m for m, _ in sorted(scores.items(), key=lambda x: -x[1])]


def add_mixtures(rankings, base, models, queries):
    """3 つを混ぜる、重みと k を振る。--methods で一部だけを選んだときは、要る方式がそろった組み合わせだけを作る。"""
    for model in models:
        for lex in ("bm25-sudachi/kw",):
            if lex not in base:
                continue
            e_kw, e_q, e_qk = "embed-%s/kw" % model, "embed-%s/q" % model, "embed-%s/qk" % model
            if not all(n in base for n in (e_kw, e_q, e_qk)):
                continue
            for k in (10, 30, 60):
                for wl, we in ((1, 1), (1, 0.5), (1, 2), (2, 1)):
                    name = "w[%s+%s] k=%d w=%s/%s" % (lex, e_qk, k, wl, we)
                    rankings[name] = {q["id"]: wrrf([base[lex][q["id"]], base[e_qk][q["id"]]], [wl, we], k) for q in queries}
            name = "3way[%s+%s+%s]" % (lex, e_kw, e_q)
            rankings[name] = {q["id"]: rrf(base[lex][q["id"]], base[e_kw][q["id"]], base[e_q][q["id"]]) for q in queries}
            if "index-or/kw" in base:
                name = "3way[%s+index-or/kw+%s]" % (lex, e_qk)
                rankings[name] = {q["id"]: rrf(base[lex][q["id"]], base["index-or/kw"][q["id"]], base[e_qk][q["id"]]) for q in queries}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--methods", default="")
    ap.add_argument("--out")
    ap.add_argument("--codex-home", help="モックの代わりに、実際の CODEX_HOME を使う（gold は実際の thread_id）")
    ap.add_argument("--now", default="2026-10-17T00:00:00+00:00",
                    help="新しさの補正の基準の時刻（流す日で数字が変わらないように固定する。README の数字はこの値）")
    args = ap.parse_args()
    ss.NOW = datetime.datetime.fromisoformat(args.now)
    if args.codex_home:
        home = os.path.abspath(args.codex_home)
    else:
        home = mock_home(args.work)
        if not os.path.exists(home):
            make_rollouts.main(args.corpus, home)
    idx = ss.Index(os.path.join(args.work, "index.sqlite"))
    rep = idx.ingest(home)
    messages = load_messages(idx, home)
    msg_thread = {m["id"]: m["thread_id"] for m in messages}
    with open(args.queries, encoding="utf-8") as f:
        queries = json.load(f)["queries"]
    gold_of = {q["id"]: (list(q["gold"]) if args.codex_home else [make_rollouts.thread_uuid(g) for g in q["gold"]])
               for q in queries}
    print("会話の発言 %d 件、問い %d 件、取り込み %s" % (len(messages), len(queries), rep["updated"]))

    wanted = [m for m in args.methods.split(",") if m] or None
    rankings = {}  # method -> {qid: [message ids]}

    def run(name, fn):
        if wanted and name not in wanted and not any(w.endswith("*") and name.startswith(w[:-1]) for w in wanted):
            return
        t0 = time.time()
        try:
            rankings[name] = {q["id"]: fn(q) for q in queries}
        except ImportError as e:
            print("飛ばした %s: %s" % (name, e))
            return
        print("  %-28s %.2fs" % (name, time.time() - t0))

    for form in ("kw", "q"):
        def terms_of(q, form=form):
            if form == "kw":
                return [ss.normalize(t) for t in q["keywords"]]
            return ss.parse_query([q["question"]])
        run("index-or/%s" % form, lambda q: method_index(idx, home, terms_of(q), "or"))

    # 本番の検索そのもの（session_search.py の search）。README の表の各行はこの 3 つ
    #   product          既定の方式（単語の BM25 + 埋め込み + 並べ直し。setup の環境とモデルが要る）
    #   product-norerank 並べ直しを外したもの
    #   product-trigram  専用の環境が無いときの方式（標準ライブラリだけ）
    sem = ss.Semantic()

    def product_with(semantic, rerank):
        def f(q):
            terms = ss.parse_query(q["keywords"])
            results, _, mode = idx.search(home, terms, ss.DEFAULT_KINDS, question=q["question"], limit=1000,
                                          sem=semantic, rerank=rerank)
            f.mode = mode
            return [r["id"] for _, r in results]
        return f
    if needs_semantic(wanted):
        idx.fill_words(sem.tokenizer(), home)
        idx.fill_vectors(sem.embedder(), home)
    for name, fn in (("product", product_with(sem, True)), ("product-norerank", product_with(sem, False)),
                     ("product-trigram", product_with(None, False))):
        run(name, fn)
        if name in rankings:
            print("  %s の方式: %s" % (name, getattr(fn, "mode", "?")))

    tokenizers = {}
    bm25s = {}
    for kind in ("sudachi", "janome"):
        try:
            tok = Tokenizer(kind)
        except ImportError as e:
            print("飛ばした bm25-%s: %s" % (kind, e))
            continue
        t0 = time.time()
        bm25s[kind] = BM25([tok(m["text"]) for m in messages])
        tokenizers[kind] = tok
        print("  索引 bm25-%s %.1fs" % (kind, time.time() - t0))
        for form in ("kw", "q"):
            def fn(q, kind=kind, form=form):
                text = " ".join(q["keywords"]) if form == "kw" else q["question"]
                sc = bm25s[kind].scores(tokenizers[kind](text))
                order = sorted(range(len(messages)), key=lambda i: -sc[i])
                return [messages[i]["id"] for i in order if sc[i] > 0]
            run("bm25-%s/%s" % (kind, form), fn)

    cache = os.path.join(args.work, "emb-cache")
    os.makedirs(cache, exist_ok=True)
    embedders = {}
    for name in MODELS:
        if wanted and not any(w.startswith("embed-" + name) or w.startswith("hyb") or w == "embed*" for w in wanted):
            continue
        try:
            import numpy as np
            e = Embedder(name, cache)
            vecs = e.docs(messages)
        except Exception as ex:  # モデルが取れない、ライブラリが無い
            print("飛ばした embed-%s: %s" % (name, ex))
            continue
        embedders[name] = (e, vecs)
        print("  索引 embed-%s %.1fs" % (name, getattr(e, "encode_seconds", 0.0)))
        for form in ("kw", "q"):
            def fn(q, e=e, vecs=vecs, form=form):
                text = " ".join(q["keywords"]) if form == "kw" else q["question"]
                sims = vecs @ e.query(text)
                order = np.argsort(-sims)
                return [messages[i]["id"] for i in order[:500]]
            run("embed-%s/%s" % (name, form), fn)

    # 埋め込みに、検索ワードと問いの文をつないで渡す形
    for name, (e, vecs) in embedders.items():
        def fn(q, e=e, vecs=vecs):
            sims = vecs @ e.query(q["question"] + " " + " ".join(q["keywords"]))
            order = np.argsort(-sims)
            return [messages[i]["id"] for i in order[:500]]
        run("embed-%s/qk" % name, fn)

    # ハイブリッド（RRF）
    base = dict(rankings)
    lexes = [k for k in base if k.endswith("/kw") and not k.startswith("embed")]
    embs = [k for k in base if k.startswith("embed-")]
    for lex in lexes:
        for emb in embs:
            name = "hyb[%s+%s]" % (lex, emb)
            rankings[name] = {q["id"]: rrf(base[lex][q["id"]], base[emb][q["id"]]) for q in queries}
    # 3 つを混ぜる、重みと k を振る（2 回目）
    add_mixtures(rankings, base, list(embedders), queries)

    # 集計
    results = {}
    types = sorted({q.get("type", "-") for q in queries})
    for name, per_q in rankings.items():
        agg = collections.defaultdict(list)
        by_type = collections.defaultdict(lambda: collections.defaultdict(list))
        for q in queries:
            m = metrics(threads_from_ranked(per_q[q["id"]], msg_thread), gold_of[q["id"]])
            for k, v in m.items():
                agg[k].append(v)
                by_type[q.get("type", "-")][k].append(v)
        results[name] = {"all": {k: sum(v) / len(v) for k, v in agg.items()},
                         "by_type": {t: {k: sum(v) / len(v) for k, v in d.items()} for t, d in by_type.items()}}
    order = sorted(results, key=lambda n: -(results[n]["all"]["MRR"] + results[n]["all"]["R@5"]))
    print()
    print("%-60s %6s %6s %6s %6s %6s   %s" % ("method", "R@1", "R@5", "R@10", "MRR", "nDCG", "  ".join("MRR:" + t for t in types)))
    for n in order:
        a = results[n]["all"]
        print("%-60s %6.3f %6.3f %6.3f %6.3f %6.3f   %s" % (n, a["R@1"], a["R@5"], a["R@10"], a["MRR"], a["nDCG"],
              "  ".join("%.2f" % results[n]["by_type"].get(t, {}).get("MRR", 0) for t in types)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"results": results, "order": order}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
