"""Codex の過去の会話を検索する。索引は SQLite（標準ライブラリだけで動く）。

    python session_search.py search 再実行 方針 [--since 2026-10-01] [--cwd here] [--json]
    python session_search.py search -- --no-verify
    python session_search.py show <参照> [--context 1] [--around 語] [--offset N --length N]
    python session_search.py stats
    python session_search.py forget <thread_id>
    python session_search.py rebuild

取り込むもの（検索のたびに、変わったファイルだけ）:
  - $CODEX_HOME/sessions/**/rollout-*.jsonl(.zst) と $CODEX_HOME/archived_sessions/ の、ユーザーの発言・途中の回答・最終の回答
  - $CODEX_HOME/memories/ の Markdown（Codex の memories が書いた要点）
入れないもの: ツールやコマンドの出力、推論、AGENTS.md などの差し込み。シークレットらしい値は伏せてから入れる。

rollout は公開された仕様ではなく Codex の内部の形式なので、読めなかった行・形の分からない発言は数えて毎回報告する。
.jsonl.zst は compression.zstd（Python 3.14 以上）で読む。読めない Python で動いたときは、3.14 を探して起動し直す。

検索の方式（evals/session-search の評価で決めた。README の「会話の検索」）:
  - `setup` で専用の環境（SudachiPy と ruri-v3-130m）を入れた PC: SudachiPy で単語に区切った BM25 と、
    ruri-v3-130m の埋め込みの近さを RRF（k=60）で混ぜる。埋め込みには --question の文と検索語をつないで渡す。
  - 入れていない PC: trigram の BM25（どれかの語を含めば当たる）。標準ライブラリだけで動く。
"""

import argparse
import datetime
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata

SCHEMA_VERSION = "1"
# 取り出し方・正規化・伏せ字を変えたら上げる。上がると全部を取り込み直す。
EXTRACT_VERSION = "1"

try:
    from compression import zstd  # Python 3.14+（optional module）
except ImportError:  # pragma: no cover - depends on the interpreter
    zstd = None

USER_SKIP_TYPES = {"image", "local_image", "skill", "mention"}
KINDS = ("user", "commentary", "final", "memory")
DEFAULT_KINDS = ("user", "final", "memory")
SNIPPET = 80
MAX_TEXT_IN_LIST = 400
RECENCY_WEIGHT = 0.2
RECENCY_HALF_LIFE_DAYS = 30.0
MAX_HITS_PER_THREAD = 3
RRF_K = 60
RRF_DEPTH = 200
EMBED_KINDS = ("user", "final", "memory")
EMBED_CHARS = 4000
DEFAULT_EMBED_BUDGET = 20.0  # 検索のたびに埋め込みを足す時間の上限（秒）
EMBED_MODEL = "cl-nagoya/ruri-v3-130m"
EMBED_MODEL_DIR = "ruri-v3-130m"
QUERY_PREFIX = "検索クエリ: "
DOC_PREFIX = "検索文書: "
# setup で入れる版。evals/session-search で評価したもの（どれも 2026-10-01 より前の公開）。
SEMANTIC_REQUIREMENTS = [
    "sudachipy==0.7.0", "sudachidict-core==20260723.1", "sentence-transformers==6.1.0",
    "transformers==5.18.0", "tokenizers==0.23.2", "torch==2.14.1", "numpy==2.4.6",
]

# --- 伏せ字 -----------------------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # 秘密鍵のブロック
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED-PRIVATE-KEY]"),
    # Authorization ヘッダーと Bearer トークン
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(basic|bearer|ntlm|negotiate)?\s*[A-Za-z0-9._~+/=-]{8,}"), r"\1\2 [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    # password= / "password": "..." / $password = '...' / 接続文字列の Password=
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|pat)\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"';,]{4,})"), r"\1[REDACTED]"),
    # URL に埋め込まれた資格情報
    (re.compile(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@"), r"\1[REDACTED]@"),
    # Azure DevOps の PAT らしい長い英数字（旧形式は 52 文字の小文字と数字、新形式は 84 文字前後）
    (re.compile(r"(?<![A-Za-z0-9])[a-z2-7]{52}(?![A-Za-z0-9])"), "[REDACTED-TOKEN]"),
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{76,100}(?![A-Za-z0-9])"), "[REDACTED-TOKEN]"),
]


def redact(text):
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def normalize(text):
    return unicodedata.normalize("NFKC", text or "").casefold()


# --- 場所 -----------------------------------------------------------------------------------------


def codex_home():
    return os.path.abspath(os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"))


def data_dir():
    if os.environ.get("HIRATA_CODEX_DATA"):
        return os.path.abspath(os.environ["HIRATA_CODEX_DATA"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "hirata-codex")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "hirata-codex")


def default_db_path():
    if os.environ.get("HIRATA_CODEX_SEARCH_DB"):
        return os.path.abspath(os.environ["HIRATA_CODEX_SEARCH_DB"])
    return os.path.join(data_dir(), "sessions.sqlite")


def venv_python():
    base = os.path.join(data_dir(), "venv")
    return os.path.join(base, "Scripts", "python.exe") if os.name == "nt" else os.path.join(base, "bin", "python")


def model_dir():
    return os.path.join(data_dir(), "models", EMBED_MODEL_DIR)


def rollout_files(home):
    out = []
    for sub in ("sessions", "archived_sessions"):
        root = os.path.join(home, sub)
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.startswith("rollout-") and (f.endswith(".jsonl") or f.endswith(".jsonl.zst")):
                    out.append(os.path.join(dirpath, f))
    return sorted(out)


def memory_files(home):
    root = os.path.join(home, "memories")
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".md") or f.endswith(".txt"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


# --- zstd を読める Python で起動し直す ------------------------------------------------------------------

REEXEC_ENV = "HIRATA_CODEX_SEARCH_REEXEC"


def zstd_interpreters():
    if os.environ.get("HIRATA_CODEX_PYTHON"):
        yield os.environ["HIRATA_CODEX_PYTHON"].split()
    if os.name == "nt":
        yield ["py", "-3.14"]
    yield ["python3.14"]
    yield ["mise", "exec", "python@3.14", "--", "python"]


def maybe_reexec(home, argv):
    """setup で作った専用の環境があればその Python で、無くて zstd を読めず圧縮された記録があれば 3.14 で起動し直す。"""
    if os.environ.get(REEXEC_ENV):
        return None
    venv = venv_python()
    if os.path.exists(venv) and os.path.abspath(sys.executable) != os.path.abspath(venv):
        env = dict(os.environ, **{REEXEC_ENV: venv})
        return subprocess.run([venv, os.path.abspath(__file__)] + argv, env=env).returncode
    if zstd is not None:
        return None
    if not any(p.endswith(".zst") for p in rollout_files(home)):
        return None
    for cmd in zstd_interpreters():
        try:
            probe = subprocess.run(cmd + ["-c", "import compression.zstd, sqlite3"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode != 0:
            continue
        env = dict(os.environ, **{REEXEC_ENV: " ".join(cmd)})
        return subprocess.run(cmd + [os.path.abspath(__file__)] + argv, env=env).returncode
    return None


# --- rollout を読む -------------------------------------------------------------------------------


def read_rollout_bytes(path):
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith(".zst"):
        if zstd is None:
            return None
        data = zstd.decompress(data)
    return data


def _texts(content, allowed, skipped_types, counters):
    """content の配列から文字を取り出す。知らない要素の形は counters['shape_errors'] に数える。"""
    if not isinstance(content, list):
        counters["shape_errors"] += 1
        return ""
    parts = []
    for el in content:
        if not isinstance(el, dict):
            counters["shape_errors"] += 1
            continue
        t = el.get("type")
        if t in allowed and isinstance(el.get("text"), str):
            parts.append(el["text"])
        elif t in skipped_types:
            counters["skipped_elements"] += 1
        else:
            counters["shape_errors"] += 1
    return "\n".join(parts)


def parse_rollout(path, data):
    """1 本の rollout から (meta, messages, counters) を取り出す。"""
    counters = {"lines": 0, "bad_json": 0, "shape_errors": 0, "skipped_elements": 0, "incomplete_tail": 0}
    meta = {"thread_id": None, "cwd": None, "branch": None, "originator": None, "agent": None,
            "history_mode": None, "cli_version": None}
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", os.path.basename(path))
    if m:
        meta["thread_id"] = m.group(1)
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] != "":
        # 書き込みの途中で終わった行。次の取り込みで読む。
        counters["incomplete_tail"] = 1
    lines = lines[:-1]

    messages = []
    turn_id = None
    cwd = None
    turn_seq = {}
    finals_by_turn = set()
    pending_task_complete = []

    def add(kind, body, line_no, ts, msg_id):
        if not body or not body.strip():
            return
        key = turn_id or "-"
        turn_seq[key] = turn_seq.get(key, 0) + 1
        messages.append({"kind": kind, "text": body, "line": line_no, "ts": ts, "turn_id": turn_id,
                         "seq": turn_seq[key], "msg_id": msg_id or ("L%d" % line_no), "cwd": cwd})
        if kind == "final":
            finals_by_turn.add(turn_id)

    for i, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        counters["lines"] += 1
        try:
            item = json.loads(raw)
        except ValueError:
            counters["bad_json"] += 1
            continue
        if not isinstance(item, dict):
            counters["bad_json"] += 1
            continue
        typ = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        ts = item.get("timestamp") if isinstance(item.get("timestamp"), str) else None
        if typ == "session_meta":
            meta["thread_id"] = payload.get("id") or meta["thread_id"]
            meta["cwd"] = payload.get("cwd") or meta["cwd"]
            cwd = cwd or meta["cwd"]
            git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            meta["branch"] = git.get("branch")
            meta["originator"] = payload.get("originator")
            meta["agent"] = payload.get("agent_role") or payload.get("agent_path") or None
            meta["history_mode"] = payload.get("history_mode")
            meta["cli_version"] = payload.get("cli_version")
            meta.setdefault("started", ts)
        elif typ == "turn_context":
            turn_id = payload.get("turn_id") or turn_id
            cwd = payload.get("cwd") or cwd
        elif typ == "event_msg":
            ptype = payload.get("type")
            if ptype == "item_completed":
                it = payload.get("item") if isinstance(payload.get("item"), dict) else None
                if it is None:
                    counters["shape_errors"] += 1
                    continue
                turn_id = payload.get("turn_id") or turn_id
                if it.get("type") == "UserMessage":
                    add("user", _texts(it.get("content"), {"text"}, USER_SKIP_TYPES, counters), i, ts, it.get("id"))
                elif it.get("type") == "AgentMessage":
                    kind = "final" if it.get("phase") == "final_answer" else "commentary"
                    add(kind, _texts(it.get("content"), {"Text", "text"}, set(), counters), i, ts, it.get("id"))
            elif ptype == "user_message":  # Legacy
                if isinstance(payload.get("message"), str):
                    add("user", payload["message"], i, ts, None)
                else:
                    counters["shape_errors"] += 1
            elif ptype == "agent_message":  # Legacy
                if isinstance(payload.get("message"), str):
                    add("commentary", payload["message"], i, ts, None)
                else:
                    counters["shape_errors"] += 1
            elif ptype == "task_complete":
                last = payload.get("last_agent_message")
                if last is None:
                    continue
                if not isinstance(last, str):
                    counters["shape_errors"] += 1
                    continue
                pending_task_complete.append((payload.get("turn_id") or turn_id, last, i, ts, cwd))

    # 最終の回答の印（phase=final_answer）が無いターンは、task_complete の last_agent_message を最終の回答にする。
    for tid, last, line_no, ts, tcwd in pending_task_complete:
        if tid in finals_by_turn:
            continue
        # 同じ本文の途中の回答があれば、それを最終の回答に格上げする（二重に持たない）。
        promoted = False
        for msg in messages:
            if msg["turn_id"] == tid and msg["kind"] == "commentary" and msg["text"].strip() == last.strip():
                msg["kind"] = "final"
                promoted = True
        if not promoted:
            saved_turn, saved_cwd = turn_id, cwd
            turn_id, cwd = tid, tcwd
            add("final", last, line_no, ts, None)
            turn_id, cwd = saved_turn, saved_cwd
        finals_by_turn.add(tid)
    return meta, messages, counters


def parse_memory(path, home):
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    rel = os.path.relpath(path, os.path.join(home, "memories")).replace("\\", "/")
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).isoformat()
    chunks = re.split(r"\n(?=#{1,6} )", text)
    messages = []
    for n, chunk in enumerate(chunks, 1):
        if chunk.strip():
            messages.append({"kind": "memory", "text": chunk.strip(), "line": n, "ts": mtime, "turn_id": None,
                             "seq": n, "msg_id": "M%d" % n, "cwd": None})
    meta = {"thread_id": "memory:" + rel, "cwd": None, "branch": None, "originator": "memories", "agent": None,
            "history_mode": None, "cli_version": None}
    return meta, messages


# --- 単語の区切りと埋め込み（setup で入れた PC だけ） ------------------------------------------------------


class Semantic:
    """SudachiPy と ruri-v3 を、使えるときだけ読み込む。どちらも無ければ None を返し、検索は trigram に落ちる。"""

    STOP_POS = {"助詞", "助動詞", "補助記号", "記号", "空白", "接頭辞", "接尾辞"}

    def __init__(self):
        self._tok = self._emb = None
        self._tok_error = self._emb_error = None

    def tokenizer(self):
        if self._tok is None and self._tok_error is None:
            try:
                from sudachipy import dictionary, tokenizer
                tok = dictionary.Dictionary(dict="core").tokenizer(mode=tokenizer.Tokenizer.SplitMode.B)
                stop = self.STOP_POS

                def run(text):
                    out = []
                    for m in tok.tokenize(normalize(text)[:20000]):
                        if m.part_of_speech()[0] in stop:
                            continue
                        w = (m.normalized_form() or m.surface()).strip()
                        if w:
                            out.append(w)
                    return out
                self._tok = run
            except Exception as e:  # ImportError のほか、辞書が無いなど
                self._tok_error = "%s: %s" % (type(e).__name__, e)
        return self._tok

    def embedder(self):
        if self._emb is None and self._emb_error is None:
            path = model_dir()
            if not os.path.isdir(path):
                self._emb_error = "モデルが無い（%s）。setup で取ってくる" % path
                return None
            try:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                import numpy
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(path, device="cpu")

                def encode(texts):
                    vecs = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
                    return numpy.asarray(vecs, dtype=numpy.float32)
                self._emb = encode
            except Exception as e:
                self._emb_error = "%s: %s" % (type(e).__name__, e)
        return self._emb


def _rrf(*lists):
    scores = {}
    for lst in lists:
        for rank, item in enumerate(lst[:RRF_DEPTH]):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


# --- 索引 -----------------------------------------------------------------------------------------


class Index:
    def __init__(self, path, readonly=False):
        self.path = path
        self.readonly = readonly
        if readonly:
            uri = "file:%s?mode=ro" % path.replace("\\", "/")
            self.db = sqlite3.connect(uri, uri=True, timeout=10)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.fts = False
        if not readonly:
            self._create()
        self.fts = self._has_table("msg_fts")
        self.words = self._has_table("words_fts")
        self._vec_cache = None

    def close(self):
        self.db.close()

    def _has_table(self, name):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None

    def _create(self):
        db = self.db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, codex_home TEXT, thread_id TEXT, size INTEGER, mtime_ns INTEGER,
                status TEXT, history_mode TEXT, cli_version TEXT, extract_version TEXT,
                n_user INTEGER, n_commentary INTEGER, n_final INTEGER, n_memory INTEGER,
                bad_json INTEGER, shape_errors INTEGER, skipped_elements INTEGER, ingested_at TEXT);
            CREATE TABLE IF NOT EXISTS threads (
                thread_id TEXT PRIMARY KEY, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT,
                originator TEXT, agent TEXT, started TEXT);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, codex_home TEXT, thread_id TEXT, path TEXT, line INTEGER,
                msg_id TEXT, turn_id TEXT, seq INTEGER, ts TEXT, kind TEXT, cwd TEXT, text TEXT, norm TEXT);
            CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, ts, seq);
            CREATE INDEX IF NOT EXISTS messages_path ON messages(path);
            CREATE TABLE IF NOT EXISTS forgotten (codex_home TEXT, thread_id TEXT, at TEXT,
                PRIMARY KEY (codex_home, thread_id));
            CREATE TABLE IF NOT EXISTS word_done (message_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS vectors (message_id INTEGER PRIMARY KEY, model TEXT, vec BLOB);
        """)
        if not self._has_table("words_fts"):
            try:
                db.execute("CREATE VIRTUAL TABLE words_fts USING fts5(words, tokenize='unicode61 remove_diacritics 0')")
            except sqlite3.OperationalError:
                pass
        if not self._has_table("msg_fts"):
            try:
                db.execute("CREATE VIRTUAL TABLE msg_fts USING fts5(norm, tokenize='trigram')")
            except sqlite3.OperationalError:
                pass  # FTS5 か trigram が無い SQLite。instr() だけで検索する。
        db.commit()
        versions = dict(db.execute("SELECT key, value FROM meta").fetchall())
        if versions.get("schema") != SCHEMA_VERSION or versions.get("extract") != EXTRACT_VERSION:
            self.reset_extracted()
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('extract', ?)", (EXTRACT_VERSION,))
            db.commit()

    def reset_extracted(self):
        """取り込んだ内容を捨てて、次の取り込みで全部を読み直させる。原本が読めない会話の分は残す。"""
        self.db.execute("UPDATE files SET size = -1 WHERE status IN ('ok', 'partial')")
        self.db.commit()

    # 取り込み

    def forgotten(self, home):
        return {r[0] for r in self.db.execute("SELECT thread_id FROM forgotten WHERE codex_home = ?", (home,))}

    def ingest(self, home):
        report = {"files": 0, "updated": 0, "zst_unreadable": 0, "partial": 0, "missing": 0, "errors": []}
        forgotten = self.forgotten(home)
        seen = set()
        for path in rollout_files(home):
            report["files"] += 1
            seen.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            row = self.db.execute("SELECT size, mtime_ns, status FROM files WHERE path = ?", (path,)).fetchone()
            if row and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
                if row["status"] == "zst_unreadable":
                    report["zst_unreadable"] += 1
                elif row["status"] == "partial":
                    report["partial"] += 1
                continue
            try:
                data = read_rollout_bytes(path)
            except Exception as e:  # 壊れた圧縮など。前の内容を残す。
                report["errors"].append("%s: %s" % (os.path.basename(path), e))
                continue
            if data is None:
                report["zst_unreadable"] += 1
                self._record_file(path, home, None, st, "zst_unreadable", {}, {})
                continue
            st_after = os.stat(path)
            if (st_after.st_size, st_after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                continue  # 読んでいる間に書き足された。次の検索で読む。
            meta, messages, counters = parse_rollout(path, data)
            if meta["thread_id"] in forgotten:
                self._record_file(path, home, meta, st, "forgotten", counters, {})
                continue
            status = "partial" if counters["shape_errors"] else "ok"
            if status == "partial":
                report["partial"] += 1
                prev = self.db.execute("SELECT status FROM files WHERE path = ?", (path,)).fetchone()
                if prev and prev["status"] == "ok":
                    continue  # 前に問題なく読めた内容を、形の分からない読み直しで置き換えない
            self._replace(path, home, meta, messages, counters, st, status)
            report["updated"] += 1
        for path in memory_files(home):
            report["files"] += 1
            seen.add(path)
            st = os.stat(path)
            row = self.db.execute("SELECT size, mtime_ns FROM files WHERE path = ?", (path,)).fetchone()
            if row and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
                continue
            meta, messages = parse_memory(path, home)
            self._replace(path, home, meta, messages, {}, st, "ok")
            report["updated"] += 1
        # 原本が見つからなくなったファイル（archive への移動は別のパスで取り込み済み）
        for row in self.db.execute("SELECT path, thread_id, status FROM files WHERE codex_home = ?", (home,)).fetchall():
            if row["path"] in seen:
                continue
            moved = self.db.execute(
                "SELECT 1 FROM files WHERE thread_id = ? AND path != ? AND status IN ('ok','partial')",
                (row["thread_id"], row["path"])).fetchone()
            with self.db:
                if moved or row["status"] == "forgotten":
                    self._delete_path(row["path"])
                    self.db.execute("DELETE FROM files WHERE path = ?", (row["path"],))
                elif row["status"] != "missing":
                    self.db.execute("UPDATE files SET status = 'missing' WHERE path = ?", (row["path"],))
        report["missing"] = self.db.execute(
            "SELECT COUNT(*) FROM files WHERE codex_home = ? AND status = 'missing'", (home,)).fetchone()[0]
        self._update_thread_names(home)
        return report

    def _record_file(self, path, home, meta, st, status, counters, counts):
        meta = meta or {}
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (path, home, meta.get("thread_id"), st.st_size, st.st_mtime_ns, status, meta.get("history_mode"),
                 meta.get("cli_version"), EXTRACT_VERSION, counts.get("user", 0), counts.get("commentary", 0),
                 counts.get("final", 0), counts.get("memory", 0), counters.get("bad_json", 0),
                 counters.get("shape_errors", 0), counters.get("skipped_elements", 0), _now()))

    def _delete_path(self, path):
        ids = "SELECT id FROM messages WHERE path = ?"
        if self.fts:
            self.db.execute("DELETE FROM msg_fts WHERE rowid IN (%s)" % ids, (path,))
        if self.words:
            self.db.execute("DELETE FROM words_fts WHERE rowid IN (%s)" % ids, (path,))
        self.db.execute("DELETE FROM word_done WHERE message_id IN (%s)" % ids, (path,))
        self.db.execute("DELETE FROM vectors WHERE message_id IN (%s)" % ids, (path,))
        self.db.execute("DELETE FROM messages WHERE path = ?", (path,))
        self._vec_cache = None

    def _replace(self, path, home, meta, messages, counters, st, status):
        counts = {k: 0 for k in KINDS}
        with self.db:  # 1 ファイル 1 トランザクション。失敗したら前の内容が残る。
            self._delete_path(path)
            tid = meta["thread_id"]
            # archive への移動: 同じ会話を前のパスで持っていれば、そちらを消す（パスの付け替え）。
            for old in self.db.execute("SELECT path FROM files WHERE thread_id = ? AND path != ?", (tid, path)).fetchall():
                if not os.path.exists(old["path"]):
                    self._delete_path(old["path"])
                    self.db.execute("DELETE FROM files WHERE path = ?", (old["path"],))
            self.db.execute(
                "INSERT INTO threads (thread_id, codex_home, cwd, branch, originator, agent, started) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET cwd=excluded.cwd, "
                "branch=excluded.branch, originator=excluded.originator, agent=excluded.agent, "
                "started=COALESCE(threads.started, excluded.started)",
                (tid, home, redact(meta.get("cwd")), redact(meta.get("branch")), meta.get("originator"),
                 meta.get("agent"), meta.get("started")))
            for msg in messages:
                text = redact(msg["text"])
                cur = self.db.execute(
                    "INSERT INTO messages (codex_home, thread_id, path, line, msg_id, turn_id, seq, ts, kind, cwd, text, norm) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (home, tid, path, msg["line"], msg["msg_id"], msg["turn_id"], msg["seq"], msg["ts"], msg["kind"],
                     redact(msg["cwd"]), text, normalize(text)))
                if self.fts:
                    self.db.execute("INSERT INTO msg_fts (rowid, norm) VALUES (?, ?)", (cur.lastrowid, normalize(text)))
                counts[msg["kind"]] += 1
        self._record_file(path, home, meta, st, status, counters, counts)

    def _update_thread_names(self, home):
        index_path = os.path.join(home, "session_index.jsonl")
        if not os.path.exists(index_path):
            return
        names = {}
        with open(index_path, "rb") as f:
            for raw in f:
                try:
                    item = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("id") and isinstance(item.get("thread_name"), str):
                    names[item["id"]] = redact(item["thread_name"])
        with self.db:
            self.db.executemany("UPDATE threads SET name = ? WHERE thread_id = ?", [(v, k) for k, v in names.items()])

    def fill_words(self, tokenize, home):
        """単語に区切った本文を words_fts に入れる（まだのものだけ）。"""
        if not self.words or tokenize is None:
            return 0
        rows = self.db.execute(
            "SELECT id, text FROM messages WHERE codex_home = ? AND kind IN (%s) AND id NOT IN (SELECT message_id FROM word_done)"
            % ",".join("?" * len(EMBED_KINDS)), (home,) + EMBED_KINDS).fetchall()
        with self.db:
            for r in rows:
                self.db.execute("INSERT INTO words_fts (rowid, words) VALUES (?, ?)", (r["id"], " ".join(tokenize(r["text"]))))
                self.db.execute("INSERT INTO word_done VALUES (?)", (r["id"],))
        return len(rows)

    def fill_vectors(self, encode, home, budget=None, progress=None):
        """埋め込みを足す（新しい発言から）。budget 秒を超えたら止める。残りの件数を返す。"""
        rows = self.db.execute(
            "SELECT id, text FROM messages WHERE codex_home = ? AND kind IN (%s) AND id NOT IN (SELECT message_id FROM vectors) "
            "ORDER BY ts DESC" % ",".join("?" * len(EMBED_KINDS)), (home,) + EMBED_KINDS).fetchall()
        if encode is None or not rows:
            return len(rows)
        import time
        start = time.time()
        done = 0
        for i in range(0, len(rows), 32):
            if budget is not None and time.time() - start > budget:
                break
            batch = rows[i:i + 32]
            vecs = encode([DOC_PREFIX + r["text"][:EMBED_CHARS] for r in batch])
            with self.db:
                self.db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                                    [(r["id"], EMBED_MODEL, v.tobytes()) for r, v in zip(batch, vecs)])
            done += len(batch)
            if progress:
                progress(done, len(rows))
        self._vec_cache = None
        return len(rows) - done

    def _vectors(self):
        if self._vec_cache is None:
            import numpy
            rows = self.db.execute("SELECT message_id, vec FROM vectors WHERE model = ?", (EMBED_MODEL,)).fetchall()
            ids = [r[0] for r in rows]
            mat = numpy.frombuffer(b"".join(r[1] for r in rows), dtype=numpy.float32).reshape(len(rows), -1) if rows else None
            self._vec_cache = (ids, mat)
        return self._vec_cache

    def forget(self, home, thread_id):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO forgotten VALUES (?, ?, ?)", (home, thread_id, _now()))
            for row in self.db.execute("SELECT path FROM files WHERE thread_id = ?", (thread_id,)).fetchall():
                self._delete_path(row["path"])
                self.db.execute("UPDATE files SET status = 'forgotten' WHERE path = ?", (row["path"],))
            self.db.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))

    def rebuild(self):
        """取り込んだ内容を作り直す。forget の一覧と、原本が消えた会話の分は残す。"""
        with self.db:
            self.db.execute("UPDATE files SET size = -1 WHERE status IN ('ok', 'partial', 'zst_unreadable')")
        self.db.execute("VACUUM")

    # 検索

    def search(self, home, terms, kinds, question=None, since=None, until=None, cwd=None, include_agents=False,
               limit=10, sem=None):
        """(結果, 当たりの件数, 方式の説明) を返す。結果は (点, 行) の並び。"""
        forgotten = self.forgotten(home)
        where = ["m.codex_home = ?", "m.kind IN (%s)" % ",".join("?" * len(kinds))]
        params = [home] + list(kinds)
        if since:
            where.append("m.ts >= ?")
            params.append(since)
        if until:
            where.append("m.ts < ?")
            params.append(until)
        if cwd:
            where.append("(lower(m.cwd) = ? OR lower(m.cwd) LIKE ? ESCAPE '\\')")
            c = cwd.rstrip("/\\").lower()
            params += [c, _like_escape(c) + "%"]
        if not include_agents:
            where.append("(t.agent IS NULL OR t.agent = '')")
        base_sql = ("FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id WHERE " + " AND ".join(where))
        allowed = {r[0] for r in self.db.execute("SELECT m.id " + base_sql, params)}
        allowed -= {r[0] for r in self.db.execute(
            "SELECT id FROM messages WHERE thread_id IN (SELECT thread_id FROM forgotten WHERE codex_home = ?)", (home,))}

        lists, modes = [], []
        tokenize = sem.tokenizer() if sem else None
        word_kinds_only = set(kinds) <= set(EMBED_KINDS)
        if tokenize and self.words and word_kinds_only:
            qtokens = sorted(set(tokenize(" ".join(terms))))
            if qtokens:
                match = " OR ".join('"%s"' % w.replace('"', '""') for w in qtokens)
                rows = self.db.execute("SELECT rowid, bm25(words_fts) FROM words_fts WHERE words_fts MATCH ?", (match,)).fetchall()
                lists.append([rid for rid, _ in sorted(((r[0], r[1]) for r in rows if r[0] in allowed), key=lambda x: x[1])])
                modes.append("単語の BM25（SudachiPy）")
        if not modes:
            lists.append(self._trigram_or(terms, allowed))
            modes.append("trigram の BM25" if self.fts else "部分一致")
        encode = sem.embedder() if sem else None
        if encode is not None:
            ids, mat = self._vectors()
            if mat is not None:
                q = encode([QUERY_PREFIX + ((question or "") + " " + " ".join(terms)).strip()])[0]
                sims = mat @ q
                order = sorted(range(len(ids)), key=lambda i: -sims[i])
                lists.append([ids[i] for i in order if ids[i] in allowed][:RRF_DEPTH])
                modes.append("埋め込み（ruri-v3-130m）")
        if len(lists) == 1:
            scores = {mid: 1.0 / (RRF_K + rank + 1) for rank, mid in enumerate(lists[0])}
        else:
            scores = _rrf(*lists)
        if not scores:
            return [], 0, " + ".join(modes)
        placeholders = ",".join("?" * len(scores))
        rows = {r["id"]: r for r in self.db.execute(
            "SELECT m.*, t.name, t.branch, t.originator FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id "
            "WHERE m.id IN (%s)" % placeholders, list(scores))}
        ranked = sorted(scores.items(), key=lambda x: (-x[1], -(_ts_num(rows[x[0]]["ts"]))))
        per_thread, out = {}, []
        for mid, score in ranked:
            r = rows[mid]
            n = per_thread.get(r["thread_id"], 0)
            if n >= MAX_HITS_PER_THREAD:
                continue
            per_thread[r["thread_id"]] = n + 1
            out.append((score, r))
            if len(out) >= limit:
                break
        return out, len(scores), " + ".join(modes)

    def _trigram_or(self, terms, allowed):
        """どれかの語を含めば候補にし、語ごとの点の和に新しさの補正を掛けて並べる（標準ライブラリだけの方式）。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        scores = {}
        for term in terms:
            if self.fts and len(term) >= 3:
                rows = self.db.execute(
                    "SELECT m.id, m.norm, m.ts, bm25(msg_fts) AS bm FROM msg_fts JOIN messages m ON m.id = msg_fts.rowid "
                    "WHERE msg_fts MATCH ?", ('"%s"' % term.replace('"', '""'),)).fetchall()
            else:
                rows = self.db.execute("SELECT id, norm, ts, NULL AS bm FROM messages WHERE instr(norm, ?) > 0", (term,)).fetchall()
            for r in rows:
                if r["id"] not in allowed:
                    continue
                base = -r["bm"] if r["bm"] is not None else r["norm"].count(term) / (1.0 + math.log1p(len(r["norm"]) / 500.0))
                boost = 1.0 + RECENCY_WEIGHT * math.pow(2.0, -_age_days(r["ts"], now) / RECENCY_HALF_LIFE_DAYS)
                scores[r["id"]] = scores.get(r["id"], 0.0) + base * boost
        return [mid for mid, _ in sorted(scores.items(), key=lambda x: -x[1])]

    def display_name(self, thread_id, name):
        if name:
            return name
        row = self.db.execute("SELECT text FROM messages WHERE thread_id = ? AND kind = 'user' ORDER BY ts, line LIMIT 1",
                              (thread_id,)).fetchone()
        if not row:
            return "（名前なし）"
        first = next((l.strip(" #") for l in row["text"].splitlines() if l.strip(" #")), "")
        return "（最初の依頼）" + (first[:40] + "…" if len(first) > 40 else first)

    def turn_messages(self, thread_id, around_id, context):
        rows = self.db.execute(
            "SELECT m.*, t.name, t.branch, t.originator FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id "
            "WHERE m.thread_id = ? ORDER BY m.ts, m.line", (thread_id,)).fetchall()
        turns = []
        for r in rows:
            if not turns or turns[-1][0] != (r["turn_id"] or r["msg_id"]):
                turns.append([(r["turn_id"] or r["msg_id"]), []])
            turns[-1][1].append(r)
        idx = next((i for i, (_, ms) in enumerate(turns) if any(m["msg_id"] == around_id for m in ms)), None)
        if idx is None:
            return None
        return turns[max(0, idx - context): idx + context + 1]

    def coverage(self, home):
        q = self.db.execute
        return {
            "zst_unreadable": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='zst_unreadable'", (home,)).fetchone()[0],
            "partial": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='partial'", (home,)).fetchone()[0],
            "missing": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='missing'", (home,)).fetchone()[0],
            "bad_json": q("SELECT COALESCE(SUM(bad_json),0) FROM files WHERE codex_home=?", (home,)).fetchone()[0],
            "shape_errors": q("SELECT COALESCE(SUM(shape_errors),0) FROM files WHERE codex_home=?", (home,)).fetchone()[0],
            "last_ingest": q("SELECT MAX(ingested_at) FROM files WHERE codex_home=?", (home,)).fetchone()[0],
        }


def _ts_num(ts):
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _age_days(ts, now):
    if not ts:
        return 3650.0
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 3650.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (now - t).total_seconds() / 86400.0)


def _local(ts):
    if not ts:
        return "日時不明"
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone().strftime("%Y-%m-%d %H:%M")


# --- 検索語 ---------------------------------------------------------------------------------------


def parse_query(parts):
    """空白で区切った語の AND と、"..." の語句。演算子は解釈しない。"""
    text = " ".join(parts)
    terms = []
    i = 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                raise ValueError('閉じていない " がある')
            phrase = text[i + 1:j].strip()
            if phrase:
                terms.append(phrase)
            i = j + 1
            continue
        j = i
        while j < len(text) and not text[j].isspace() and text[j] != '"':
            j += 1
        terms.append(text[i:j])
        i = j
    terms = [normalize(t) for t in terms if t.strip()]
    if not terms:
        raise ValueError("検索語が空")
    return terms


def snippet(text, terms, width=SNIPPET):
    norm = normalize(text)
    pos = min((p for p in (norm.find(t) for t in terms) if p >= 0), default=0)
    # 正規化で長さが変わる文字は少ないので、元の文の同じ位置を中心にする。
    start = max(0, pos - width)
    end = min(len(text), pos + width * 2)
    s = text[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + s + ("…" if end < len(text) else "")


# --- コマンド -------------------------------------------------------------------------------------

KIND_LABEL = {"user": "依頼", "commentary": "途中の回答", "final": "最終の回答", "memory": "memories"}


def open_index(args, update=True, sem=None, budget=None):
    """書ければ取り込んでから、書けなければ既存の索引を読み取り専用で開く。(index, report, note)"""
    path = args.db or default_db_path()
    home = codex_home()
    note = None
    try:
        idx = Index(path)
    except (sqlite3.OperationalError, OSError) as e:
        if not os.path.exists(path):
            raise SystemExit("索引を作れない（%s）。HIRATA_CODEX_SEARCH_DB で書ける場所を指定する: %s" % (e, path))
        idx = Index(path, readonly=True)
        note = "索引を更新できなかった（%s）。%s 時点の索引で検索した" % (e, idx.coverage(home)["last_ingest"])
        return idx, None, note
    report = None
    if update:
        try:
            report = idx.ingest(home)
            if sem is not None:
                idx.fill_words(sem.tokenizer(), home)
                report["embed_remaining"] = idx.fill_vectors(sem.embedder(), home, budget=budget)
        except sqlite3.OperationalError as e:
            note = "索引を更新できなかった（%s）。前の索引で検索した" % e
    return idx, report, note


def coverage_lines(idx, home, report, note):
    cov = idx.coverage(home)
    lines = []
    if note:
        lines.append("注意: " + note)
    if cov["zst_unreadable"]:
        lines.append("注意: 圧縮された記録 %d 本を読めなかった（Python 3.14 で流せば読める）" % cov["zst_unreadable"])
    if cov["partial"] or cov["shape_errors"] or cov["bad_json"]:
        lines.append("注意: 形の分からない発言 %d 件・壊れた行 %d 行（%d 本のファイル）は検索に入っていない"
                     % (cov["shape_errors"], cov["bad_json"], cov["partial"]))
    if cov["missing"]:
        lines.append("参考: 原本が見つからない会話 %d 本も索引に残して検索している" % cov["missing"])
    if report and report.get("embed_remaining"):
        lines.append("注意: 埋め込みの済んでいない発言 %d 件は、全文検索だけで探した（session_search.py index で全部を作れる）"
                     % report["embed_remaining"])
    if report and report["errors"]:
        lines.append("注意: 読めなかったファイル %d 本（%s ほか）" % (len(report["errors"]), report["errors"][0]))
    reexec = os.environ.get(REEXEC_ENV)
    if reexec:
        lines.append("参考: 圧縮された記録を読むため %s で動かした" % reexec)
    return lines


def cmd_search(args):
    try:
        terms = parse_query(args.terms)
    except ValueError as e:
        print("検索語の誤り: %s" % e, file=sys.stderr)
        return 2
    home = codex_home()
    sem = Semantic()
    idx, report, note = open_index(args, sem=sem, budget=args.budget)
    cwd = os.getcwd() if args.cwd == "here" else args.cwd
    kinds = KINDS if args.all_kinds else DEFAULT_KINDS
    results, total, mode = idx.search(home, terms, kinds, question=args.question, since=_date_arg(args.since),
                                      until=_date_arg(args.until, end=True), cwd=cwd,
                                      include_agents=args.include_agents, limit=args.limit, sem=sem)
    cov = coverage_lines(idx, home, report, note)
    if args.json:
        print(json.dumps({"terms": terms, "mode": mode, "total": total, "coverage": cov, "results": [
            {"score": round(s, 3), "ref": "%s/%s" % (r["thread_id"], r["msg_id"]), "time": _local(r["ts"]),
             "thread_name": idx.display_name(r["thread_id"], r["name"]), "cwd": r["cwd"], "branch": r["branch"], "kind": r["kind"],
             "originator": r["originator"], "snippet": snippet(r["text"], terms),
             "resume": None if r["kind"] == "memory" else "codex resume %s" % r["thread_id"]}
            for s, r in results]}, ensure_ascii=False, indent=1))
    else:
        print("検索: %s（方式: %s。候補 %d 件、上位 %d 件を表示）" % (" / ".join(terms), mode, total, len(results)))
        for line in cov:
            print(line)
        for n, (s, r) in enumerate(results, 1):
            print()
            print("[%d] %s  %s  %s" % (n, _local(r["ts"]), KIND_LABEL.get(r["kind"], r["kind"]), idx.display_name(r["thread_id"], r["name"])))
            print("    場所: %s%s" % (r["cwd"] or "-", "（%s）" % r["branch"] if r["branch"] else ""))
            print("    " + snippet(r["text"], terms))
            print("    参照: %s/%s" % (r["thread_id"], r["msg_id"]))
            if r["kind"] != "memory":
                print("    開き直す: codex resume %s" % r["thread_id"])
        if not results:
            gaps = any(line.startswith("注意") for line in cov)
            print("当たりなし。" + ("ただし上の注意のとおり、検索できていない部分がある。" if gaps else ""))
    idx.close()
    return 0


def cmd_show(args):
    if "/" not in args.ref:
        print("参照は <thread_id>/<発言 ID> の形", file=sys.stderr)
        return 2
    thread_id, msg_id = args.ref.split("/", 1)
    home = codex_home()
    idx, report, note = open_index(args)
    turns = idx.turn_messages(thread_id, msg_id, args.context)
    if thread_id in idx.forgotten(home) or turns is None:
        print("見つからない: %s" % args.ref, file=sys.stderr)
        return 1
    first = turns[0][1][0]
    print("スレッド: %s（%s）  開き直す: codex resume %s" % (idx.display_name(thread_id, first["name"]), first["cwd"] or "-", thread_id))
    for _, msgs in turns:
        for m in msgs:
            text = m["text"]
            if args.around and m["msg_id"] == msg_id:
                pos = max(0, normalize(text).find(normalize(args.around)))
                start = max(0, pos - args.length // 2)
            else:
                start = args.offset
            body = text[start:start + args.length]
            mark = " ← 参照" if m["msg_id"] == msg_id else ""
            print()
            print("--- %s %s%s" % (_local(m["ts"]), KIND_LABEL.get(m["kind"], m["kind"]), mark))
            if start > 0:
                print("（先頭から %d 文字を省略）" % start)
            print(body)
            rest = len(text) - (start + len(body))
            if rest > 0:
                print("（残り %d 文字。続きは --offset %d）" % (rest, start + len(body)))
    idx.close()
    return 0


def cmd_stats(args):
    home = codex_home()
    idx, report, note = open_index(args)
    q = idx.db.execute
    print("索引: %s" % idx.path)
    print("取り込み元: %s" % home)
    print("SQLite %s、全文検索（FTS5 trigram）: %s、zstd: %s" % (
        sqlite3.sqlite_version, "あり" if idx.fts else "なし（instr で検索）", "読める" if zstd else "読めない"))
    for status, n in q("SELECT status, COUNT(*) FROM files WHERE codex_home=? GROUP BY status", (home,)).fetchall():
        print("ファイル %s: %d" % (status, n))
    for kind, n in q("SELECT kind, COUNT(*) FROM messages WHERE codex_home=? GROUP BY kind", (home,)).fetchall():
        print("発言 %s: %d" % (KIND_LABEL.get(kind, kind), n))
    print("除外した会話: %d" % len(idx.forgotten(home)))
    for line in coverage_lines(idx, home, report, note):
        print(line)
    if os.path.exists(idx.path):
        print("索引の大きさ: %.1f MB" % (os.path.getsize(idx.path) / 1e6))
    idx.close()
    return 0


def cmd_index(args):
    """取り込みと、単語の区切りと、埋め込みを全部済ませる（最初の一度は時間がかかる）。"""
    home = codex_home()
    sem = Semantic()
    idx, report, note = open_index(args, update=False)
    if idx.readonly:
        print("索引に書けない: %s" % note, file=sys.stderr)
        return 1
    report = idx.ingest(home)
    print("取り込み: ファイル %d 本、更新 %d 本" % (report["files"], report["updated"]))
    tok = sem.tokenizer()
    print("単語の区切り: %s" % ("%d 件" % idx.fill_words(tok, home) if tok else "使えない（%s）" % sem._tok_error))
    enc = sem.embedder()
    if enc is None:
        print("埋め込み: 使えない（%s）" % sem._emb_error)
    else:
        def progress(done, total):
            if done % 320 == 0 or done == total:
                print("埋め込み: %d / %d" % (done, total), flush=True)
        left = idx.fill_vectors(enc, home, progress=progress)
        print("埋め込み: 残り %d 件" % left)
    for line in coverage_lines(idx, home, report, note):
        print(line)
    idx.close()
    return 0


def cmd_setup(args):
    """専用の環境（SudachiPy、sentence-transformers、torch）を作り、ruri-v3-130m を取ってくる。"""
    target = os.path.dirname(os.path.dirname(venv_python()))
    candidates = []
    if args.python:
        candidates.append(args.python.split())
    if os.name == "nt":
        candidates += [["py", "-3.14"], ["py", "-3.13"], ["py", "-3.11"]]
    candidates += [["python3.14"], ["mise", "exec", "python@3.14", "--", "python"], [sys.executable]]
    base = None
    for cmd in candidates:
        try:
            ok = subprocess.run(cmd + ["-c", "import sys, sqlite3, venv; assert sys.version_info >= (3, 10)"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            base = cmd
            break
    if base is None:
        print("Python 3.10 以上が見つからない", file=sys.stderr)
        return 1
    print("専用の環境: %s（元の Python: %s）" % (target, " ".join(base)))
    if not os.path.exists(venv_python()):
        if subprocess.run(base + ["-m", "venv", target]).returncode != 0:
            return 1
    pip = [venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary", ":all:"]
    if subprocess.run(pip + SEMANTIC_REQUIREMENTS).returncode != 0:
        print("ライブラリを入れられなかった。会社のプロキシや PyPI のミラーの設定を確かめる", file=sys.stderr)
        return 1
    if os.path.isdir(model_dir()):
        print("モデルはすでにある: %s" % model_dir())
    else:
        code = ("from huggingface_hub import snapshot_download; "
                "snapshot_download(%r, local_dir=%r)" % (EMBED_MODEL, model_dir()))
        if subprocess.run([venv_python(), "-c", code]).returncode != 0:
            print("モデルを取ってこられなかった。別の PC で取ってきたフォルダを %s に置けば使える（README）" % model_dir(),
                  file=sys.stderr)
            return 1
    print("できた。続けて index で、全部の会話の索引を作る（最初の一度だけ時間がかかる）")
    return 0


def cmd_forget(args):
    idx, _, note = open_index(args, update=False)
    if idx.readonly:
        print("索引に書けないので除外できない: %s" % note, file=sys.stderr)
        return 1
    idx.forget(codex_home(), args.thread_id)
    print("除外した: %s（rollout は消していない。消すなら codex delete %s）" % (args.thread_id, args.thread_id))
    idx.close()
    return 0


def cmd_rebuild(args):
    idx, _, note = open_index(args, update=False)
    if idx.readonly:
        print("索引に書けないので作り直せない: %s" % note, file=sys.stderr)
        return 1
    idx.rebuild()
    report = idx.ingest(codex_home())
    print("作り直した: ファイル %d 本、取り込み %d 本" % (report["files"], report["updated"]))
    idx.close()
    return 0


def _date_arg(value, end=False):
    if not value:
        return None
    d = datetime.date.fromisoformat(value)
    if end:
        d += datetime.timedelta(days=1)
    local = datetime.datetime(d.year, d.month, d.day).astimezone()
    return local.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="索引のファイル（既定は %s）" % default_db_path())
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="検索する")
    s.add_argument("terms", nargs="+", help="検索語（空白区切りの AND、\"...\" で語句。- で始まる語は、オプションを全部書いた後に -- を置き、その後ろに書く）")
    s.add_argument("--since", help="この日から（YYYY-MM-DD、ローカルの日付）")
    s.add_argument("--until", help="この日まで（YYYY-MM-DD、その日を含む）")
    s.add_argument("--cwd", help="作業フォルダで絞る（下の階層も含む）。here で今のフォルダ")
    s.add_argument("--all-kinds", action="store_true", help="途中の回答も検索する")
    s.add_argument("--include-agents", action="store_true", help="子のエージェントの会話も検索する")
    s.add_argument("--question", help="持ち主が言った問いの文。埋め込みの検索に検索語と一緒に渡す")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--budget", type=float, default=DEFAULT_EMBED_BUDGET, help="検索の前に埋め込みを足す時間の上限（秒）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)
    sh = sub.add_parser("show", help="参照の発言と前後を表示する")
    sh.add_argument("ref")
    sh.add_argument("--context", type=int, default=1, help="前後に出すターンの数")
    sh.add_argument("--around", help="参照の発言のうち、この語の周りを表示する")
    sh.add_argument("--offset", type=int, default=0)
    sh.add_argument("--length", type=int, default=2000)
    sh.set_defaults(func=cmd_show)
    st = sub.add_parser("stats", help="索引の状態を表示する")
    st.set_defaults(func=cmd_stats)
    f = sub.add_parser("forget", help="会話を検索に出なくし、二度と取り込まない")
    f.add_argument("thread_id")
    f.set_defaults(func=cmd_forget)
    r = sub.add_parser("rebuild", help="索引を作り直す")
    r.set_defaults(func=cmd_rebuild)
    ix = sub.add_parser("index", help="取り込み・単語の区切り・埋め込みを全部済ませる")
    ix.set_defaults(func=cmd_index)
    su = sub.add_parser("setup", help="専用の環境を作り、ライブラリとモデルを入れる")
    su.add_argument("--python", help="専用の環境の元にする Python（例: \"py -3.14\"）")
    su.set_defaults(func=cmd_setup)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command in ("search", "stats", "rebuild", "index", "show"):
        code = maybe_reexec(codex_home(), argv)
        if code is not None:
            return code
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
