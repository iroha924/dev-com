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
  - `setup` で専用の環境（SudachiPy、ruri-v3-130m、ruri-v3-reranker-310m）を入れた PC: SudachiPy で単語に区切った
    BM25 と、ruri-v3-130m の埋め込み（先頭 4,000 文字）の近さを RRF（k=60）で混ぜ、上位 10 件を
    ruri-v3-reranker-310m で並べ直す。埋め込みと並べ直しには --question の文を渡す。
  - 入れていない PC: trigram の BM25（どれかの言葉を文字どおり含めば候補、含む言葉の数→点の順）。標準ライブラリだけで動く。
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

SCHEMA_VERSION = "3"
# 取り出し方・正規化・伏せ字を変えたら上げる。上がると全部を取り込み直す。
EXTRACT_VERSION = "3"
# 単語の索引の作り方。変えたら単語の索引だけを作り直す（取り込み直しや埋め込みの作り直しは要らない）
WORDS_VERSION = "2"

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
EMBED_REVISION = "e3114c6ee10dbab8b4b235fbc6dcf9dd4d5ac1a6"  # 評価に使ったコミット。上流が変わっても同じものを取る
EMBED_MODEL_DIR = "ruri-v3-130m"
QUERY_PREFIX = "検索クエリ: "
DOC_PREFIX = "検索文書: "
# 上位を並べ直すモデル。評価で上位 10 件を並べ直すのがいちばん良かった（evals/session-search、README）
RERANK_MODEL = "cl-nagoya/ruri-v3-reranker-310m"
RERANK_REVISION = "bb46934ee9ed09f850b9fcff17501b3ef7ddb2b3"
RERANK_MODEL_DIR = "ruri-v3-reranker-310m"
RERANK_K = 10
RERANK_WINDOW = 1000  # 発言から、問いの言葉を最も多く含む 1,000 文字を渡す（512 トークンに収まる）
RERANK_FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
# ruri-v3-130m のフォルダに要るファイル（取得の途中で止まったフォルダを使わない）
MODEL_FILES = ("config.json", "modules.json", "model.safetensors", "sentence_bert_config.json",
               "config_sentence_transformers.json", "tokenizer.json", "tokenizer_config.json",
               os.path.join("1_Pooling", "config.json"))
# setup で入れる版。evals/session-search で評価したもの（どれも 2026-10-01 より前の公開）。
SEMANTIC_REQUIREMENTS = [
    "sudachipy==0.7.0", "sudachidict-core==20260723.1", "sentence-transformers==6.1.0",
    "transformers==5.18.0", "tokenizers==0.23.2", "torch==2.14.1", "numpy==2.4.6",
]

# --- 伏せ字 -----------------------------------------------------------------------------------------

_SECRET_KEYS = r"(?:password|passwd|pwd|secret|token|api[_-]?key|pat|accountkey|sharedaccesskey)"
_DQ = r'"(?:[^"\\`]|[\\`].){1,4000}"'       # "..."（\" と `" は閉じる引用符ではない。改行も含める）
_SQ = r"'(?:[^']|''){1,4000}'(?!')"           # '...'（PowerShell の '' は閉じる引用符ではない。\ はふつうの文字）


def _keep_quote(m):
    q = m.group(2)[0]
    return m.group(1) + q + "[REDACTED]" + q


_SECRET_PATTERNS = [
    # 秘密鍵のブロック（END が無く途中で切れたものも、鍵の本文らしい部分まで伏せる）
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9+/=\s]*)", re.S),
     "[REDACTED-PRIVATE-KEY]"),
    # Authorization ヘッダーと Bearer トークン
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(basic|bearer|ntlm|negotiate|token)?\s*[A-Za-z0-9._~+/=-]{3,}"),
     r"\1\2 [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    # PowerShell: パイプで渡す '...' | ConvertTo-SecureString、引数の '...'・"..."・引用符なし、-Password などの引数
    (re.compile(r"(?i)(" + _DQ + "|" + _SQ + r")(\s*\|\s*ConvertTo-SecureString)"), r"'[REDACTED]'\2"),
    (re.compile(r"(?i)(ConvertTo-SecureString\b[^'\"\n|;]*?)(" + _DQ + "|" + _SQ + ")"), _keep_quote),
    (re.compile(r"(?i)(ConvertTo-SecureString\b[^\n|;'\"]*?\s)(?=[^\s'\"|;-])([^\s|;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(ConvertTo-SecureString\b[^\n|;'\"]*?-String\s+)([^\s'\"|;]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(-" + _SECRET_KEYS + r"\s+)(" + _DQ + "|" + _SQ + ")"), _keep_quote),
    # 引用符なしの引数: -Password X / --password X / --token=X / az devops login --token X
    (re.compile(r"(?i)((?:^|\s)--?(?:password|passwd|pwd|secret|token|pat|api[_-]?key|access[_-]?token|client[_-]?secret)"
                r"(?:\s+|=))(?=[^\s'\"|;-])([^\s'\"|;]+)"), r"\1[REDACTED]"),
    # PowerShell の here-string: $password = @"...\n"@
    (re.compile(r"(?i)(" + _SECRET_KEYS + r"\w*\s*=\s*)(@\"[\s\S]*?\"@|@'[\s\S]*?'@)"),
     lambda m: m.group(1) + m.group(2)[:2] + "[REDACTED]" + m.group(2)[-2:]),
    # YAML のブロック: secret: | の次の行から、字下げが続くところまで
    (re.compile(r"(?im)^([ \t]*[\w.-]*(?:password|passwd|secret|token|api[_-]?key|pat)[\w.-]*[ \t]*:[ \t]*[|>][-+]?[ \t]*\n)"
                r"((?:[ \t]+\S.*(?:\n|$))+)"), lambda m: m.group(1) + "  [REDACTED]\n"),
    # 環境変数の名前: AWS_SECRET_ACCESS_KEY=... / GITHUB_TOKEN: ...（区切りの語が secret・token・password・key・pat）
    (re.compile(r"(?i)\b([a-z][a-z0-9]*(?:_[a-z0-9]+)*_(?:secret|token|password|passwd|key|pat)(?:_[a-z0-9]+)*[\"']?\s*[:=]\s*)"
                r"(?![\"'])([^\s\"';,]+)"), r"\1[REDACTED]"),
    # Azure の SAS の署名: ?sv=...&sig=... / SharedAccessSignature=...sig=...
    (re.compile(r"(?i)(\bsig=)([^&;\s\"']+)"), r"\1[REDACTED]"),
    # 接続文字列の Password=...;（空白を含んでよい。; か行末まで）
    (re.compile(r"(?im)\b((?:password|pwd|accountkey|sharedaccesskey)[ \t]*=[ \t]*)(?![\s\"'])([^;\n]{1,200}?)(?=[ \t]*(?:;|$))"),
     r"\1[REDACTED]"),
    # password = """...""" / '''...'''（Python の三重の引用符）
    (re.compile(r"(?i)(" + _SECRET_KEYS + r"[\"']?\s*[:=]\s*)(\"\"\"[\s\S]{1,4000}?\"\"\"|'''[\s\S]{1,4000}?''')"),
     lambda m: m.group(1) + m.group(2)[:3] + "[REDACTED]" + m.group(2)[:3]),
    # password= / "password": "..." / $password = '...'。引用符があれば閉じるまで伏せ、引用符の種類を保つ
    (re.compile(r"(?i)(" + _SECRET_KEYS + r"[\"']?\s*[:=]\s*)(" + _DQ + "|" + _SQ + ")"), _keep_quote),
    (re.compile(r"(?i)(" + _SECRET_KEYS + r"\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"';,]{1,})"), r"\1[REDACTED]"),
    # 日本語の書き方: パスワードは X / トークン: X。値が英数字と記号で 6 文字以上のときだけ伏せる
    # （「シークレットは使わず」「パスは C:\...」のような、ふつうの言葉やパスを消さない）
    (re.compile(r"((?:パスワード|トークン|アクセストークン|シークレット)\s*(?:は|を|[:：=])\s*)"
                r"(?=[!-~]*[A-Za-z0-9])([!-~]{6,})(?![!-~])"), r"\1[REDACTED]"),
    # URL に埋め込まれた資格情報（https のほか postgresql:// mongodb+srv:// redis:// なども。user:password@ も PAT@ も）
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/?#]*@"), r"\1[REDACTED]@"),
    # Azure DevOps の PAT らしい長い英数字（旧形式は 52 文字の小文字と数字、新形式は 84 文字前後）
    (re.compile(r"(?<![A-Za-z0-9])[a-z2-7]{52}(?![A-Za-z0-9])"), "[REDACTED-TOKEN]"),
    # 16 進だけの並び（SHA-384 などのハッシュ）は伏せない
    (re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[g-zG-Z])[A-Za-z0-9]{76,100}(?![A-Za-z0-9])"), "[REDACTED-TOKEN]"),
]


def _redact_once(text):
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact(text):
    """シークレットらしい値を伏せる。全角で書かれた値（ｐａｓｓｗｏｒｄ＝…）は、NFKC で半角にそろえた文で伏せる。"""
    if not text:
        return text
    out = _redact_once(text)
    folded = unicodedata.normalize("NFKC", text)
    if folded != text:
        folded_out = _redact_once(folded)
        if folded_out != folded:
            return folded_out if out == text else _redact_once(unicodedata.normalize("NFKC", out))
    return out


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


def model_dir(kind="embed"):
    return os.path.join(data_dir(), "models", EMBED_MODEL_DIR if kind == "embed" else RERANK_MODEL_DIR)


def model_ready(kind="embed"):
    """モデルのフォルダに、読み込みに要るファイルが揃っているか（取得の途中で止まったフォルダを使わない）。"""
    d = model_dir(kind)
    return all(os.path.exists(os.path.join(d, f)) for f in (MODEL_FILES if kind == "embed" else RERANK_FILES))


def python_command(value):
    """\"py -3.14\" のようなコマンドか、空白を含むかもしれない実行ファイルのパスを、引数の並びにする。"""
    value = value.strip().strip('"')
    if os.path.isfile(value):
        return [value]
    return value.split()


def _walk_error(failed):
    """os.walk の onerror。無いフォルダ（まだ作られていない archived_sessions など）は失敗に数えない。"""
    if failed is None:
        return None

    def onerror(e):
        if not isinstance(e, FileNotFoundError):
            failed.append(getattr(e, "filename", None) or str(e))
    return onerror


def rollout_files(home, failed=None):
    """failed にリストを渡すと、列挙できなかったフォルダを足す（その下の原本を「消えた」と見なさないため）。"""
    out = []
    onerror = _walk_error(failed)
    for sub in ("sessions", "archived_sessions"):
        root = os.path.join(home, sub)
        for dirpath, _, files in os.walk(root, onerror=onerror):
            for f in files:
                if f.startswith("rollout-") and (f.endswith(".jsonl") or f.endswith(".jsonl.zst")):
                    out.append(os.path.join(dirpath, f))
    return sorted(out)


def memory_files(home, failed=None):
    root = os.path.join(home, "memories")
    out = []
    onerror = _walk_error(failed)
    for dirpath, _, files in os.walk(root, onerror=onerror):
        for f in files:
            if f.endswith(".md") or f.endswith(".txt"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


# --- zstd を読める Python で起動し直す ------------------------------------------------------------------

REEXEC_ENV = "HIRATA_CODEX_SEARCH_REEXEC"
BROKEN_VENV_ENV = "HIRATA_CODEX_SEARCH_BROKEN_VENV"


def zstd_interpreters():
    if os.environ.get("HIRATA_CODEX_PYTHON"):
        yield python_command(os.environ["HIRATA_CODEX_PYTHON"])
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
        # 元の Python を消した環境などで止まらないように、起動できるかを先に確かめる。だめならこの Python で続ける。
        try:
            usable = subprocess.run([venv, "-c", "import sqlite3"], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            usable = False
        if usable:
            env = dict(os.environ, **{REEXEC_ENV: venv})
            return subprocess.run([venv, os.path.abspath(__file__)] + argv, env=env).returncode
        os.environ[BROKEN_VENV_ENV] = venv
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
    counters = {"lines": 0, "bad_json": 0, "shape_errors": 0, "skipped_elements": 0, "incomplete_tail": 0,
                "response_messages": 0}
    meta = {"thread_id": None, "cwd": None, "branch": None, "originator": None, "agent": None,
            "history_mode": None, "cli_version": None}
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", os.path.basename(path))
    if m:
        meta["thread_id"] = m.group(1)
    # 発言 ID の無い古い形式は行番号で ID を作る。revert で同じ会話のファイルが 2 本になると行番号がぶつかるので、
    # ファイル名の rollout ID の末尾を付ける（圧縮の前後のように同じ名前のファイルどうしは同じ ID になる）。
    rollout_tag = m.group(1)[-12:] if m else "x"
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] != "":
        try:  # 改行が無くても、JSON として閉じている行は読む
            json.loads(lines[-1])
        except ValueError:
            counters["incomplete_tail"] = 1  # 書き込みの途中で終わった行。次の取り込みで読む。
            lines = lines[:-1]
    else:
        lines = lines[:-1]

    messages = []
    turn_id = None
    cwd = None
    turn_seq = {}
    finals_by_turn = set()
    pending_task_complete = []
    meta_seen = False

    def add(kind, body, line_no, ts, msg_id):
        if not body or not body.strip():
            return
        key = turn_id or "-"
        turn_seq[key] = turn_seq.get(key, 0) + 1
        messages.append({"kind": kind, "text": body, "line": line_no, "ts": ts, "turn_id": turn_id,
                         "seq": turn_seq[key], "msg_id": msg_id or ("L%d-%s" % (line_no, rollout_tag)), "cwd": cwd})
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
            # コピー型の fork は、自分の session_meta を先頭に置き、その後ろに引き継いだ親の session_meta を持つ。
            # 引き継いだ分の metadata は見ない（Codex の core/src/session/session.rs と同じ扱い）。
            if meta_seen:
                continue
            meta_seen = True
            meta["thread_id"] = payload.get("id") or meta["thread_id"]
            meta["cwd"] = payload.get("cwd") or meta["cwd"]
            cwd = cwd or meta["cwd"]
            git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            meta["branch"] = git.get("branch")
            meta["originator"] = payload.get("originator")
            # source.subagent: review は持ち主が /review で始めた会話なので子に数えない。thread_spawn（spawn_agent の子）、
            # compact、memory_consolidation などは子に数える。
            src = payload.get("source")
            sub_agent = src.get("subagent") if isinstance(src, dict) else None
            if isinstance(sub_agent, dict):
                sub_agent = next(iter(sub_agent), "subagent")
            elif sub_agent == "review" or (sub_agent is not None and not isinstance(sub_agent, str)):
                sub_agent = None if sub_agent == "review" else "subagent"
            meta["agent"] = (payload.get("agent_role") or payload.get("agent_path") or payload.get("agent_type")
                             or sub_agent or None)
            meta["history_mode"] = payload.get("history_mode")
            meta["cli_version"] = payload.get("cli_version")
            meta.setdefault("started", ts)
        elif typ == "response_item":
            if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
                counters["response_messages"] += 1
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
                    # partial_answer は回答の確かな部分（Codex は回答の側に数える）。commentary だけが途中の回答
                    kind = "final" if it.get("phase") in ("final_answer", "partial_answer") else "commentary"
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
    # turn_id の無い古い記録は全部のターンが None になるので、ターンで済みかを見ず、その行より前の発言だけを見る。
    for tid, last, line_no, ts, tcwd in pending_task_complete:
        if tid is not None and tid in finals_by_turn:
            continue
        # 同じ本文の途中の回答があれば、それを最終の回答に格上げする（二重に持たない）。
        promoted = False
        for msg in sorted(messages, key=lambda m: m["line"], reverse=True):  # 足した最終の回答は末尾に付くので、行の順に見る
            if msg["turn_id"] != tid or msg["line"] >= line_no:
                continue
            if msg["kind"] == "user":
                break  # このターンの依頼より前は、別のターン
            if msg["text"].strip() == last.strip() and msg["kind"] in ("commentary", "final"):
                msg["kind"] = "final"
                promoted = True
                break
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
        mtime = datetime.datetime.fromtimestamp(os.fstat(f.fileno()).st_mtime, datetime.timezone.utc).isoformat()
    rel = os.path.relpath(path, os.path.join(home, "memories")).replace("\\", "/")
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
        self._tok = self._emb = self._rerank = None
        self._tok_error = self._emb_error = self._rerank_error = None

    def tokenizer(self):
        if self._tok is None and self._tok_error is None:
            try:
                from sudachipy import dictionary, tokenizer
                tok = dictionary.Dictionary(dict="core").tokenizer(mode=tokenizer.Tokenizer.SplitMode.B)
                stop = self.STOP_POS

                def run(text):
                    # SudachiPy は 1 回に 49,149 バイトまでしか受け付けないので、全文を区切って渡す
                    out = []
                    for piece in _byte_chunks(normalize(text), SUDACHI_MAX_BYTES):
                        for m in tok.tokenize(piece):
                            if m.part_of_speech()[0] in stop:
                                continue
                            # 正規化した形（緑・繰り返す）と書かれたままの形（みどり・繰り返し）の両方を入れる。
                            # 正規化は文脈で変わるので、片方だけだと文字どおり含む発言を取りこぼす
                            for w in {(m.normalized_form() or "").strip(), m.surface().strip()}:
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
            if not model_ready():
                self._emb_error = "モデルが無い（%s）。setup で取ってくる" % path
                return None
            try:
                _quiet_model_loading()
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

    def reranker(self):
        """(問い, 本文の断片) の組に点を付ける関数。モデルかライブラリが無ければ None。"""
        if self._rerank is None and self._rerank_error is None:
            if not model_ready("rerank"):
                self._rerank_error = "モデルが無い（%s）。setup で取ってくる" % model_dir("rerank")
                return None
            try:
                _quiet_model_loading()
                from sentence_transformers import CrossEncoder
                model = CrossEncoder(model_dir("rerank"), max_length=512, device="cpu")

                def score(pairs):
                    return [float(s) for s in model.predict(pairs, batch_size=16, show_progress_bar=False)]
                self._rerank = score
            except Exception as e:
                self._rerank_error = "%s: %s" % (type(e).__name__, e)
        return self._rerank


def _quiet_model_loading():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # 読み込みの進み具合の表示を出さない（Codex が毎回読むことになる）
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TQDM_DISABLE", "1")


def rerank_window(text, terms, width=RERANK_WINDOW):
    """問いの言葉を最も多く含む width 文字の窓（半分ずつずらして探す）。"""
    norm = normalize(text)
    best, best_score = 0, -1
    for start in range(0, max(1, len(norm) - width // 2), width // 2):
        score = sum(norm.count(t, start, start + width) for t in terms)
        if score > best_score:
            best, best_score = start, score
    return text[best:best + width]


SUDACHI_MAX_BYTES = 40000


def _byte_chunks(text, limit):
    """UTF-8 で limit バイトを超えないように、なるべく改行か空白で区切る。"""
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + limit // 4)
        while end < len(text) and len(text[start:end + 1000].encode("utf-8")) <= limit:
            end = min(len(text), end + 1000)
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if cut > start + (end - start) // 2:
                end = cut + 1
        out.append(text[start:end])
        start = end
    return out


def embed_chunks(text):
    """埋め込みに渡す断片。先頭の EMBED_CHARS 文字だけ。

    全文を 4,000 文字ずつ埋め込む形も評価したが、並べ直しを入れた検索では同じ値（実際の会話で MRR 0.957）で、
    最初の索引づくりが約 1.7 倍かかった。後ろの部分は単語の BM25 が全文を見る。
    """
    return [(text or "")[:EMBED_CHARS]]


def _rrf(*lists):
    scores = {}
    for lst in lists:
        for rank, item in enumerate(lst[:RRF_DEPTH]):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


# --- 索引 -----------------------------------------------------------------------------------------


class Index:
    def __init__(self, path, readonly=False):
        path = os.path.abspath(path)
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
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        old_schema = dict(db.execute("SELECT key, value FROM meta").fetchall()).get("schema")
        if old_schema in ("1", "2") and self._has_table("vectors"):
            db.execute("DROP TABLE vectors")  # 2 → 3: 長い発言を分けて埋め込む形に変えたので、作り直す
            db.commit()
        if old_schema == "1" and self._has_table("threads") and not self._has_table("threads_v1"):
            # 1 → 2: threads を CODEX_HOME ごとに分ける。原本の消えた会話の名前や子の印を失わないよう、行を移す。
            db.execute("ALTER TABLE threads RENAME TO threads_v1")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, codex_home TEXT, thread_id TEXT, size INTEGER, mtime_ns INTEGER,
                status TEXT, history_mode TEXT, cli_version TEXT, extract_version TEXT,
                n_user INTEGER, n_commentary INTEGER, n_final INTEGER, n_memory INTEGER,
                bad_json INTEGER, shape_errors INTEGER, skipped_elements INTEGER, ingested_at TEXT);
            CREATE TABLE IF NOT EXISTS threads (
                thread_id TEXT, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT,
                originator TEXT, agent TEXT, started TEXT, PRIMARY KEY (codex_home, thread_id));
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, codex_home TEXT, thread_id TEXT, path TEXT, line INTEGER,
                msg_id TEXT, turn_id TEXT, seq INTEGER, ts TEXT, kind TEXT, cwd TEXT, text TEXT, norm TEXT);
            CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, ts, seq);
            CREATE INDEX IF NOT EXISTS messages_path ON messages(path);
            CREATE TABLE IF NOT EXISTS forgotten (codex_home TEXT, thread_id TEXT, at TEXT,
                PRIMARY KEY (codex_home, thread_id));
            CREATE TABLE IF NOT EXISTS forgotten_keys (codex_home TEXT, key TEXT, PRIMARY KEY (codex_home, key));
            CREATE TABLE IF NOT EXISTS word_done (message_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS vectors (message_id INTEGER, chunk INTEGER, model TEXT, vec BLOB,
                PRIMARY KEY (message_id, chunk));
        """)
        if self._has_table("threads_v1"):
            # 旧い表は CODEX_HOME を区別していないので、その会話の本文を持つ CODEX_HOME ごとに行を作る
            db.execute("INSERT OR IGNORE INTO threads SELECT thread_id, codex_home, name, cwd, branch, originator, agent, "
                       "started FROM threads_v1")
            db.execute("INSERT OR IGNORE INTO threads SELECT DISTINCT o.thread_id, m.codex_home, o.name, o.cwd, o.branch, "
                       "o.originator, o.agent, o.started FROM threads_v1 o JOIN messages m ON m.thread_id = o.thread_id")
            db.execute("DROP TABLE threads_v1")
            db.commit()
        self._create_fts()
        versions = dict(db.execute("SELECT key, value FROM meta").fetchall())
        if versions.get("words") != WORDS_VERSION:
            with db:
                if self.words:
                    db.execute("DELETE FROM words_fts")
                db.execute("DELETE FROM word_done")
                db.execute("INSERT OR REPLACE INTO meta VALUES ('words', ?)", (WORDS_VERSION,))
        if versions.get("schema") != SCHEMA_VERSION or versions.get("extract") != EXTRACT_VERSION:
            self.reset_extracted()
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('extract', ?)", (EXTRACT_VERSION,))
            db.commit()

    def _create_fts(self, commit=True):
        db = self.db
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
        if commit:
            db.commit()
        self.fts = self._has_table("msg_fts")
        self.words = self._has_table("words_fts")

    def reset_extracted(self):
        """取り込んだ内容を捨てて、次の取り込みで全部を読み直させる。原本が読めない会話の分は残す。

        読み直せない分（原本が消えた会話など）にも新しい伏せ字が効くよう、保存済みの本文に伏せ字を掛け直す。
        """
        self.db.execute("UPDATE files SET size = -1 WHERE status IN ('ok', 'partial', 'duplicate', 'no_messages')")
        for r in self.db.execute("SELECT id, text FROM messages").fetchall():
            text = redact(r["text"])
            if text == r["text"]:
                continue
            if self.fts:
                self.db.execute("DELETE FROM msg_fts WHERE rowid = ?", (r["id"],))
                self.db.execute("INSERT INTO msg_fts (rowid, norm) VALUES (?, ?)", (r["id"], normalize(text)))
            if self.words:
                self.db.execute("DELETE FROM words_fts WHERE rowid = ?", (r["id"],))
            self.db.execute("DELETE FROM word_done WHERE message_id = ?", (r["id"],))
            self.db.execute("DELETE FROM vectors WHERE message_id = ?", (r["id"],))
            self.db.execute("UPDATE messages SET text = ?, norm = ? WHERE id = ?", (text, normalize(text), r["id"]))
        for r in self.db.execute("SELECT id, cwd FROM messages WHERE cwd IS NOT NULL").fetchall():
            if redact(r["cwd"]) != r["cwd"]:
                self.db.execute("UPDATE messages SET cwd = ? WHERE id = ?", (redact(r["cwd"]), r["id"]))
        for r in self.db.execute("SELECT rowid, name, cwd, branch FROM threads").fetchall():
            vals = [redact(r[k]) for k in ("name", "cwd", "branch")]
            if vals != [r["name"], r["cwd"], r["branch"]]:
                self.db.execute("UPDATE threads SET name = ?, cwd = ?, branch = ? WHERE rowid = ?", vals + [r["rowid"]])
        self.db.commit()

    # 取り込み

    def forgotten(self, home):
        return {r[0] for r in self.db.execute("SELECT thread_id FROM forgotten WHERE codex_home = ?", (home,))}

    def ingest(self, home):
        report = {"files": 0, "updated": 0, "zst_unreadable": 0, "partial": 0, "missing": 0, "errors": []}
        forgotten = self.forgotten(home)
        seen = set()
        failed_dirs = []
        rollouts = rollout_files(home, failed_dirs)
        memories = memory_files(home, failed_dirs)
        for d in failed_dirs:
            report["errors"].append("%s: 列挙できなかった" % d)
        for path in rollouts:
            report["files"] += 1
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue  # 一覧を作った後に消された（移された）。原本の消失として扱う
            except OSError as e:
                seen.add(path)  # 読めないだけ。前の中身を残す
                report["errors"].append("%s: %s" % (os.path.basename(path), e))
                continue
            seen.add(path)
            row = self.db.execute("SELECT size, mtime_ns, status FROM files WHERE path = ?", (path,)).fetchone()
            # 読めなかった .zst が読めるようになった、または「原本なし」にしたファイルが戻ってきたら、読み直す
            retry = row and ((row["status"] == "zst_unreadable" and zstd is not None) or row["status"] == "missing")
            if row and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns and not retry:
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
                m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", os.path.basename(path))
                self._record_file(path, home, {"thread_id": m.group(1) if m else None}, st, "zst_unreadable", {}, {})
                continue
            meta, messages, counters = parse_rollout(path, data)
            try:
                st_after = os.stat(path)
            except OSError:
                st_after = None
            if st_after is None or (st_after.st_size, st_after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                # 読んでいる間に書き足された（または移された）。読めた分は取り込み、大きさを読む前のものにしておくので、
                # 次の検索で読み直す。
                st = os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                     -1, st.st_atime, st.st_mtime, st.st_ctime))
            if meta["thread_id"] in forgotten:
                self._record_file(path, home, meta, st, "forgotten", counters, {})
                continue
            status = "partial" if counters["shape_errors"] else "ok"
            if not messages and counters["response_messages"]:
                status = "no_messages"  # 発言らしい行はあるのに、読み方の分かる形で 1 件も取れなかった
            if status == "partial":
                report["partial"] += 1  # 分かる部分は取り込み、形の分からない要素の数を出す
            self._replace(path, home, meta, messages, counters, st, status)
            report["updated"] += 1
        for path in memories:
            report["files"] += 1
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue  # 一覧を作った後に消された
            except OSError as e:
                seen.add(path)  # 読めないだけ（権限など）。前の中身を残す
                report["errors"].append("%s: %s" % (os.path.basename(path), e))
                continue
            seen.add(path)
            row = self.db.execute("SELECT size, mtime_ns FROM files WHERE path = ?", (path,)).fetchone()
            if row and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
                continue
            try:
                meta, messages = parse_memory(path, home)
            except FileNotFoundError:
                seen.discard(path)
                continue
            except OSError as e:
                report["errors"].append("%s: %s" % (os.path.basename(path), e))
                continue
            if meta["thread_id"] in forgotten:
                self._record_file(path, home, meta, st, "forgotten", {}, {})
                continue
            self._replace(path, home, meta, messages, {}, st, "ok")
            report["updated"] += 1
        # 原本が見つからなくなったファイル（archive への移動は別のパスで取り込み済み）。
        # memories は会話から作り直される要約で、Codex が消したものは古くなった要点なので、索引からも消す。
        for row in self.db.execute("SELECT path, thread_id, status FROM files WHERE codex_home = ?", (home,)).fetchall():
            if row["path"] in seen:
                continue
            if any(_under(row["path"], d) for d in failed_dirs):
                continue  # 列挙できなかったフォルダの下。消えたとは限らないので、前の中身を残す
            # 同じ名前のファイル（archive への移動や圧縮）が別の場所にあり、その中身を取り込めているときだけ移動とみなす。
            # 読めない .zst に変わっただけなら、前の中身を残す。
            moved = any(_file_stem(r["path"]) == _file_stem(row["path"]) for r in self.db.execute(
                "SELECT path FROM files WHERE codex_home = ? AND path != ? AND status IN ('ok', 'partial')",
                (home, row["path"])).fetchall() if r["path"] in seen)
            with self.db:
                if moved or row["status"] == "forgotten" or (row["thread_id"] or "").startswith("memory:"):
                    self._delete_path(row["path"])
                    self.db.execute("DELETE FROM files WHERE path = ?", (row["path"],))
                elif row["status"] != "missing":
                    self.db.execute("UPDATE files SET status = 'missing' WHERE path = ?", (row["path"],))
        report["missing"] = self.db.execute(
            "SELECT COUNT(*) FROM files WHERE codex_home = ? AND status = 'missing'", (home,)).fetchone()[0]
        error = self._update_thread_names(home)
        if error:
            report["errors"].append(error)
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

    def _delete_message(self, mid):
        if self.fts:
            self.db.execute("DELETE FROM msg_fts WHERE rowid = ?", (mid,))
        if self.words:
            self.db.execute("DELETE FROM words_fts WHERE rowid = ?", (mid,))
        for table, col in (("word_done", "message_id"), ("vectors", "message_id"), ("messages", "id")):
            self.db.execute("DELETE FROM %s WHERE %s = ?" % (table, col), (mid,))
        self._vec_cache = None

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
            # 同じ会話のファイルがほかにもあるとき（thread/revert は前半を元のファイルに残す）は両方を持ち、
            # 同じ発言は検索と show でまとめる。archive への移動で消えた前の場所の分は、ingest の最後に消す。
            self.db.execute(
                "INSERT INTO threads (thread_id, codex_home, cwd, branch, originator, agent, started) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(codex_home, thread_id) DO UPDATE SET cwd=excluded.cwd, "
                "branch=excluded.branch, originator=excluded.originator, agent=excluded.agent, "
                "started=COALESCE(threads.started, excluded.started)",
                (tid, home, redact(meta.get("cwd")), redact(meta.get("branch")), meta.get("originator"),
                 meta.get("agent"), meta.get("started")))
            hidden = self._forgotten_key_set(home)
            for msg in messages:
                text = redact(msg["text"])
                if hidden and _msg_key({"kind": msg["kind"], "ts": msg["ts"], "norm": normalize(text)}) in hidden:
                    continue  # forget した会話から写された発言（コピー型の fork）
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
        try:
            f = open(index_path, "rb")
        except OSError as e:
            return "%s: %s" % (os.path.basename(index_path), e)
        with f:
            for raw in f:
                try:
                    item = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("id") and isinstance(item.get("thread_name"), str):
                    names[item["id"]] = redact(item["thread_name"])
        with self.db:
            self.db.executemany("UPDATE threads SET name = ? WHERE thread_id = ? AND codex_home = ?",
                                [(v, k, home) for k, v in names.items()])

    def fill_words(self, tokenize, home):
        """単語に区切った本文を words_fts に入れる（まだのものだけ）。"""
        if not self.words or tokenize is None:
            return 0
        rows = self.db.execute(
            "SELECT id, text FROM messages WHERE codex_home = ? AND kind IN (%s) AND id NOT IN (SELECT message_id FROM word_done)"
            % ",".join("?" * len(EMBED_KINDS)), (home,) + EMBED_KINDS).fetchall()
        failed = 0
        with self.db:
            for r in rows:
                try:
                    words = " ".join(tokenize(r["text"]))
                except Exception:  # 区切れない発言は済みにしない（次に試す。それまでは trigram で探す）
                    failed += 1
                    continue
                self.db.execute("INSERT INTO words_fts (rowid, words) VALUES (?, ?)", (r["id"], words))
                self.db.execute("INSERT INTO word_done VALUES (?)", (r["id"],))
        self.word_failures = failed
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
        for i in range(0, len(rows), 16):
            if budget is not None and time.time() - start > budget:
                break
            batch = rows[i:i + 16]
            pieces = [(r["id"], n, c) for r in batch for n, c in enumerate(embed_chunks(r["text"]))]
            vecs = encode([DOC_PREFIX + c for _, _, c in pieces])
            with self.db:  # 1 つの発言の断片は同じトランザクションで入れる（途中までの発言を残さない）
                self.db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                    [(mid, n, EMBED_MODEL, v.tobytes()) for (mid, n, _), v in zip(pieces, vecs)])
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

    def has_thread(self, home, thread_id):
        q = self.db.execute
        return bool(q("SELECT 1 FROM files WHERE codex_home = ? AND thread_id = ? LIMIT 1", (home, thread_id)).fetchone()
                    or q("SELECT 1 FROM messages WHERE codex_home = ? AND thread_id = ? LIMIT 1", (home, thread_id)).fetchone())

    def _forgotten_key_set(self, home):
        return {r[0] for r in self.db.execute("SELECT key FROM forgotten_keys WHERE codex_home = ?", (home,))}

    def forget(self, home, thread_id):
        """会話を検索から外す。コピー型の fork が子の記録に写した同じ発言も外し、二度と取り込まない。

        同じ発言かは、種類・時刻・本文から作ったハッシュで見分ける（本文そのものは残さない）。
        """
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO forgotten VALUES (?, ?, ?)", (home, thread_id, _now()))
            keys = {_msg_key(r) for r in self.db.execute(
                "SELECT kind, ts, norm FROM messages WHERE codex_home = ? AND thread_id = ?", (home, thread_id))}
            self.db.executemany("INSERT OR IGNORE INTO forgotten_keys VALUES (?, ?)", [(home, k) for k in keys])
            copies = [r["id"] for r in self.db.execute(
                "SELECT id, kind, ts, norm FROM messages WHERE codex_home = ? AND thread_id != ?", (home, thread_id))
                if _msg_key(r) in keys]
            for mid in copies:
                self._delete_message(mid)
            for row in self.db.execute("SELECT path FROM files WHERE thread_id = ? AND codex_home = ?",
                                       (thread_id, home)).fetchall():
                self._delete_path(row["path"])
                self.db.execute("UPDATE files SET status = 'forgotten' WHERE path = ?", (row["path"],))
            self.db.execute("DELETE FROM threads WHERE thread_id = ? AND codex_home = ?", (thread_id, home))

    def rebuild(self):
        """取り込んだ内容を作り直す。forget の一覧と、原本が消えた会話の分は残す。

        全文検索の表は、消した行の言葉が内部の区画に残ることがあるので、捨てて作り直す。
        """
        with self.db:  # 捨てる・作る・埋めるを 1 つのトランザクションにする（途中で止まっても前の状態に戻る）
            self.db.execute("UPDATE files SET size = -1 WHERE status IN ('ok', 'partial', 'zst_unreadable', 'duplicate', "
                            "'no_messages')")
            self.db.execute("DROP TABLE IF EXISTS msg_fts")
            self.db.execute("DROP TABLE IF EXISTS words_fts")
            self.db.execute("DELETE FROM word_done")
            self._create_fts(commit=False)
            if self.fts:
                self.db.execute("INSERT INTO msg_fts (rowid, norm) SELECT id, norm FROM messages")
        self.db.execute("VACUUM")

    # 検索

    def search(self, home, terms, kinds, question=None, since=None, until=None, cwd=None, include_agents=False,
               limit=10, sem=None, rerank=True):
        """(結果, 当たりの件数, 方式の説明) を返す。結果は (点, 行) の並び。"""
        forgotten = self.forgotten(home)
        where = ["m.codex_home = ?", "m.kind IN (%s)" % ",".join("?" * len(kinds))]
        params = [home] + list(kinds)
        if since:  # 小数秒や +00:00 の書き方の違いがあるので、文字列ではなく時刻として比べる
            where.append("julianday(m.ts) >= julianday(?)")
            params.append(since)
        if until:
            where.append("julianday(m.ts) < julianday(?)")
            params.append(until)
        if cwd:  # そのフォルダと、その下だけ（app を指定して app-old を含めない）
            where.append("(lower(m.cwd) = ? OR lower(m.cwd) LIKE ? ESCAPE '\\' OR lower(m.cwd) LIKE ? ESCAPE '\\')")
            c = cwd.rstrip("/\\").lower()
            params += [c, _like_escape(c + "\\") + "%", _like_escape(c + "/") + "%"]
        if not include_agents:
            where.append("(t.agent IS NULL OR t.agent = '')")
        base_sql = ("FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id AND t.codex_home = m.codex_home WHERE " + " AND ".join(where))
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
                phrases = [term for term in terms if len(term.split()) > 1]
                if phrases:  # 語句をその並びのまま含む発言を、もう 1 つの並びとして混ぜる（上げるだけで、絞らない）
                    lists.append(self._trigram_or(phrases, allowed))
                # 単語の索引にまだ入っていない発言（区切れなかった、読み取り専用で足せなかった）は、文字どおりで探す
                pending = allowed - {r[0] for r in self.db.execute("SELECT message_id FROM word_done")}
                if pending:
                    lists.append(self._trigram_or(terms, pending))
                    modes.append("単語の索引に無い %d 件は trigram" % len(pending))
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
                ranked_ids, seen_ids = [], set()  # 長い発言は断片のうち一番近いもので並べる
                for i in order:
                    mid = ids[i]
                    if mid in allowed and mid not in seen_ids:
                        seen_ids.add(mid)
                        ranked_ids.append(mid)
                        if len(ranked_ids) >= RRF_DEPTH:
                            break
                lists.append(ranked_ids)
                modes.append("埋め込み（ruri-v3-130m）")
        if len(lists) == 1:
            scores = {mid: 1.0 / (RRF_K + rank + 1) for rank, mid in enumerate(lists[0])}
        else:
            scores = _rrf(*lists)
        if not scores:
            return [], 0, " + ".join(modes)
        placeholders = ",".join("?" * len(scores))
        rows = {r["id"]: r for r in self.db.execute(
            "SELECT m.*, t.name, t.branch, t.originator FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id AND t.codex_home = m.codex_home "
            "WHERE m.id IN (%s)" % placeholders, list(scores))}
        ranked = sorted(scores.items(), key=lambda x: (-x[1], -(_ts_num(rows[x[0]]["ts"]))))
        per_thread, out, seen_keys = {}, [], set()
        for mid, score in ranked:
            r = rows[mid]
            if _dedupe_key(r) in seen_keys:  # 同じ会話の別のファイルにある同じ発言
                continue
            seen_keys.add(_dedupe_key(r))
            n = per_thread.get(r["thread_id"], 0)
            if n >= MAX_HITS_PER_THREAD:
                continue
            per_thread[r["thread_id"]] = n + 1
            out.append((score, r))
            if len(out) >= max(limit, RERANK_K if rerank else 0):
                break
        score_pairs = sem.reranker() if (sem and rerank) else None
        if score_pairs is not None and len(out) > 1:
            head, tail = out[:RERANK_K], out[RERANK_K:]
            query = question or " ".join(terms)
            points = score_pairs([(query, rerank_window(r["text"], terms)) for _, r in head])
            head = [head[i] for i in sorted(range(len(head)), key=lambda i: -points[i])]
            out = head + tail
            modes.append("並べ直し（ruri-v3-reranker-310m、上位 %d 件）" % len(head))
        return out[:limit], len(scores), " + ".join(modes)

    def _trigram_or(self, terms, allowed):
        """どれかの単語を含めば候補にし、言葉ごとの点の和に新しさの補正を掛けて並べる（標準ライブラリだけの方式）。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        scores, hits = {}, {}
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
                hits.setdefault(r["id"], set()).add(term)
        # 含む言葉の数を先に比べ、同じ数なら点で比べる（多く含むほど上に来る）
        return [mid for mid, _ in sorted(scores.items(), key=lambda x: (-len(hits[x[0]]), -x[1]))]

    def display_name(self, home, thread_id, name):
        if name:
            return name
        row = self.db.execute("SELECT text FROM messages WHERE codex_home = ? AND thread_id = ? AND kind = 'user' "
                              "ORDER BY ts, line LIMIT 1", (home, thread_id)).fetchone()
        if not row:
            return "（名前なし）"
        first = next((l.strip(" #") for l in row["text"].splitlines() if l.strip(" #")), "")
        return "（最初の依頼）" + (first[:40] + "…" if len(first) > 40 else first)

    def turn_messages(self, home, thread_id, around_id, context):
        rows = self.db.execute(
            "SELECT m.*, t.name, t.branch, t.originator FROM messages m LEFT JOIN threads t ON t.thread_id = m.thread_id AND t.codex_home = m.codex_home "
            "WHERE m.codex_home = ? AND m.thread_id = ? ORDER BY julianday(m.ts), m.line", (home, thread_id)).fetchall()
        turns, seen_keys = [], set()
        for r in rows:
            if _dedupe_key(r) in seen_keys:
                continue
            seen_keys.add(_dedupe_key(r))
            if not turns or turns[-1][0] != (r["turn_id"] or r["msg_id"]):
                turns.append([(r["turn_id"] or r["msg_id"]), []])
            turns[-1][1].append(r)
        idx = next((i for i, (_, ms) in enumerate(turns) if any(m["msg_id"] == around_id for m in ms)), None)
        if idx is None:
            return None
        return turns[max(0, idx - context): idx + context + 1]

    def _missing_count(self, home):
        """原本が見つからない記録の数。同じ名前の記録（圧縮した .zst など）がほかにあるものは数えない。"""
        rows = self.db.execute("SELECT path, status FROM files WHERE codex_home = ?", (home,)).fetchall()
        present = {_file_stem(r["path"]) for r in rows if r["status"] != "missing"}
        return sum(1 for r in rows if r["status"] == "missing" and _file_stem(r["path"]) not in present)

    def coverage(self, home):
        q = self.db.execute
        return {
            "zst_unreadable": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='zst_unreadable'", (home,)).fetchone()[0],
            "partial": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='partial'", (home,)).fetchone()[0],
            "no_messages": q("SELECT COUNT(*) FROM files WHERE codex_home=? AND status='no_messages'", (home,)).fetchone()[0],
            "missing": self._missing_count(home),
            "bad_json": q("SELECT COALESCE(SUM(bad_json),0) FROM files WHERE codex_home=? AND status != 'forgotten'",
                          (home,)).fetchone()[0],
            "shape_errors": q("SELECT COALESCE(SUM(shape_errors),0) FROM files WHERE codex_home=? AND status != 'forgotten'",
                              (home,)).fetchone()[0],
            "last_ingest": q("SELECT MAX(ingested_at) FROM files WHERE codex_home=?", (home,)).fetchone()[0],
        }


def _under(path, folder):
    folder = os.path.abspath(folder)
    return os.path.abspath(path) == folder or os.path.abspath(path).startswith(folder.rstrip(os.sep) + os.sep)


def _file_stem(path):
    """rollout-...-<id>.jsonl と、その圧縮（.jsonl.zst）を同じものとして比べる名前。"""
    name = os.path.basename(path)
    return name[:-4] if name.endswith(".zst") else name


def _msg_key(r):
    import hashlib
    return hashlib.sha256(("%s\x1f%s\x1f%s" % (r["kind"], r["ts"], r["norm"])).encode("utf-8")).hexdigest()


def _dedupe_key(r):
    return (r["thread_id"], r["msg_id"], r["kind"], r["ts"], r["norm"])


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


# --- 検索ワード ---------------------------------------------------------------------------------------


def parse_query(parts):
    """空白で区切った言葉と、"..." の語句に分ける。演算子は解釈しない（どれかを含めば候補になる）。

    シェルが引用符を外して 1 つの引数にした "idle timeout" も語句として扱う。語句は絞り込みには使わず、
    その並びのまま含む発言を上げるのに使う。語句の中の言葉も、それぞれ検索ワードとして数える
    （「パス 長い」のように、語句のつもりでなく並べた言葉を取りこぼさないため）。
    """
    pre = []
    for part in parts:
        if '"' not in part and len(part.split()) > 1:
            pre.append('"%s"' % part)
        else:
            pre.append(part)
    text = " ".join(pre)
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
    out = []
    for term in (normalize(t) for t in terms if t.strip()):
        for item in [term] + (term.split() if len(term.split()) > 1 else []):
            if item not in out:
                out.append(item)
    if not out:
        raise ValueError("検索ワードが空")
    return out


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
                encode = sem.embedder()
                if encode is not None:
                    report["embed_remaining"] = idx.fill_vectors(encode, home, budget=budget)
        except sqlite3.OperationalError as e:
            note = "索引を更新できなかった（%s）。前の索引で検索した" % e
    return idx, report, note


def coverage_lines(idx, home, report, note, sem=None):
    cov = idx.coverage(home)
    lines = []
    if note:
        lines.append("注意: " + note)
    for kind, label, error in (("embed", "埋め込み", getattr(sem, "_emb_error", None)),
                               ("rerank", "並べ直し", getattr(sem, "_rerank_error", None))):
        if sem is not None and error and model_ready(kind):
            lines.append("注意: %sのモデルを読み込めなかったので、%sをせずに探した（%s）。setup で入れ直せる" % (label, label, error))
    if cov["zst_unreadable"]:
        lines.append("注意: 圧縮された記録 %d 本を読めなかった（Python 3.14 が要る。専用の環境があるなら、"
                     "setup --python で 3.14 を指定して作り直す）" % cov["zst_unreadable"])
    if cov["partial"] or cov["shape_errors"] or cov["bad_json"]:
        lines.append("注意: 形の分からない発言 %d 件・壊れた行 %d 行（%d 本のファイル）は検索に入っていない"
                     % (cov["shape_errors"], cov["bad_json"], cov["partial"]))
    if cov["no_messages"]:
        lines.append("注意: 発言を取り出せなかった記録 %d 本は検索に入っていない（Codex の記録の形が変わった可能性がある）"
                     % cov["no_messages"])
    if cov["missing"]:
        lines.append("参考: 原本が見つからない記録のファイル %d 本も索引に残して検索している" % cov["missing"])
    if report and report.get("embed_remaining"):
        lines.append("注意: 埋め込みの済んでいない発言 %d 件は、全文検索だけで探した（session_search.py index で全部を作れる）"
                     % report["embed_remaining"])
    if report and report["errors"]:
        lines.append("注意: 読めなかったファイル %d 本（%s ほか）" % (len(report["errors"]), report["errors"][0]))
    if os.environ.get(BROKEN_VENV_ENV):
        lines.append("注意: 専用の環境（%s）が起動できないので、標準ライブラリだけの方式で探した。"
                     "setup で作り直せる" % os.environ[BROKEN_VENV_ENV])
    reexec = os.environ.get(REEXEC_ENV)
    if reexec and os.path.abspath(reexec) != os.path.abspath(venv_python()):
        lines.append("参考: 圧縮された記録を読むため %s で動かした" % reexec)
    return lines


def cmd_search(args):
    try:
        terms = parse_query(args.terms)
    except ValueError as e:
        print("検索ワードの誤り: %s" % e, file=sys.stderr)
        return 2
    home = codex_home()
    sem = Semantic()
    idx, report, note = open_index(args, sem=sem, budget=args.budget)
    cwd = os.getcwd() if args.cwd == "here" else args.cwd
    kinds = KINDS if args.all_kinds else DEFAULT_KINDS
    results, total, mode = idx.search(home, terms, kinds, question=args.question, since=_date_arg(args.since),
                                      until=_date_arg(args.until, end=True), cwd=cwd,
                                      include_agents=args.include_agents, limit=args.limit, sem=sem,
                                      rerank=not args.no_rerank)
    cov = coverage_lines(idx, home, report, note, sem)
    if args.json:
        print(json.dumps({"terms": terms, "mode": mode, "total": total, "coverage": cov, "results": [
            {"score": round(s, 3), "ref": "%s/%s" % (r["thread_id"], r["msg_id"]), "time": _local(r["ts"]),
             "thread_name": redact(idx.display_name(home, r["thread_id"], r["name"])), "cwd": redact(r["cwd"]),
             "branch": redact(r["branch"]), "kind": r["kind"],
             "originator": r["originator"], "snippet": redact(snippet(r["text"], terms)),
             "resume": None if r["kind"] == "memory" else "codex resume %s" % r["thread_id"]}
            for s, r in results]}, ensure_ascii=False, indent=1))
    else:
        print("検索: %s（方式: %s。候補 %d 件、上位 %d 件を表示）" % (" / ".join(terms), mode, total, len(results)))
        for line in cov:
            print(line)
        for n, (s, r) in enumerate(results, 1):
            print()
            # 表示のときにも伏せ字を掛ける（伏せ字の規則が増える前の索引に残った値を出さない）
            print("[%d] %s  %s  %s" % (n, _local(r["ts"]), KIND_LABEL.get(r["kind"], r["kind"]),
                                       redact(idx.display_name(home, r["thread_id"], r["name"]))))
            print("    場所: %s%s" % (redact(r["cwd"]) or "-", "（%s）" % redact(r["branch"]) if r["branch"] else ""))
            print("    " + redact(snippet(r["text"], terms)))
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
    thread_id, msg_id = args.ref.rsplit("/", 1)
    home = codex_home()
    idx, report, note = open_index(args)
    turns = idx.turn_messages(home, thread_id, msg_id, args.context)
    if thread_id in idx.forgotten(home) or turns is None:
        print("見つからない: %s" % args.ref, file=sys.stderr)
        return 1
    first = turns[0][1][0]
    resume = "" if thread_id.startswith("memory:") else "  開き直す: codex resume %s" % thread_id
    print("スレッド: %s（%s）%s" % (redact(idx.display_name(home, thread_id, first["name"])), redact(first["cwd"]) or "-", resume))
    for _, msgs in turns:
        for m in msgs:
            text = redact(m["text"])
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
    lines = coverage_lines(idx, home, report, note)
    if lines:
        print()
        for line in lines:
            print(line)
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
    sem = Semantic()
    words = "単語の BM25（SudachiPy）" if sem.tokenizer() else "trigram の BM25（SudachiPy が無い: %s）" % sem._tok_error
    emb = "埋め込み（ruri-v3-130m、%d 本）" % q("SELECT COUNT(*) FROM vectors").fetchone()[0] if model_ready() else "埋め込みなし（モデルが無い）"
    rr = "並べ直し（ruri-v3-reranker-310m）" if model_ready("rerank") else "並べ直しなし（モデルが無い）"
    print("方式: %s + %s + %s" % (words, emb, rr))
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


def setup_candidates(python=None):
    """setup の元にする Python の候補。指定があればそれだけ。無ければ 3.14 を先に、3.13、3.11、今の Python の順。"""
    if python:
        return [python_command(python)]
    nt = os.name == "nt"
    first = ([["py", "-3.14"]] if nt else []) + [["python3.14"], ["mise", "exec", "python@3.14", "--", "python"]]
    rest = ([["py", "-3.13"], ["py", "-3.11"]] if nt else []) + [[sys.executable]]
    return first + rest


def _probe(cmd, code):
    try:
        return subprocess.run(cmd + ["-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _version(cmd):
    try:
        return subprocess.run(cmd + ["-c", "import sys; print(sys.version_info[:2])"], capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def venv_rebuild_reason(base, venv):
    """専用の環境を作り直す理由。使い続けてよければ None。"""
    if not os.path.exists(venv):
        return "まだ無い"
    have = _version([venv])
    if not have:
        return "今の環境が起動できない"
    if have != _version(base):
        return "今の環境は %s で、選んだ Python と違う" % have
    if _probe(base, "import compression.zstd") and not _probe([venv], "import compression.zstd"):
        return "今の環境は圧縮された記録を読めない"
    return None


def cmd_setup(args):
    """専用の環境（SudachiPy、sentence-transformers、torch）を作り、ruri-v3-130m を取ってくる。"""
    target = os.path.dirname(os.path.dirname(venv_python()))
    base = None
    for cmd in setup_candidates(args.python):
        try:
            ok = subprocess.run(cmd + ["-c", "import sys, sqlite3, venv; assert sys.version_info >= (3, 11)"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            base = cmd
            break
    if base is None:
        print("Python 3.11 以上が見つからない（%s）" % ("指定: " + args.python if args.python else "3.14 を優先して探した"),
              file=sys.stderr)
        return 1
    print("専用の環境: %s（元の Python: %s）" % (target, " ".join(base)))
    if not _probe(base, "import compression.zstd"):
        print("注意: この Python（3.14 より前）では、圧縮された記録（.jsonl.zst）を読めない。"
              "3.14 を入れて setup --python で指定し直すと読める")
    reason = venv_rebuild_reason(base, venv_python())
    if reason:
        print("専用の環境を作る（%s）" % reason)
        if subprocess.run(base + ["-m", "venv", "--clear", target]).returncode != 0:
            return 1
    pip = [venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary", ":all:"]
    if subprocess.run(pip + SEMANTIC_REQUIREMENTS).returncode != 0:
        print("ライブラリを入れられなかった。会社のプロキシや PyPI のミラーの設定を確かめる", file=sys.stderr)
        return 1
    # 取得は毎回流す（揃っているファイルは取り直さない。途中で止まった分はここで埋まる）
    for kind, repo, revision in (("embed", EMBED_MODEL, EMBED_REVISION), ("rerank", RERANK_MODEL, RERANK_REVISION)):
        code = ("from huggingface_hub import snapshot_download; "
                "snapshot_download(%r, revision=%r, local_dir=%r)" % (repo, revision, model_dir(kind)))
        if subprocess.run([venv_python(), "-c", code]).returncode != 0:
            if model_ready(kind):
                print("%s を取りに行けなかったが、%s に揃っているのでそれを使う" % (repo, model_dir(kind)))
            else:
                print("%s を取ってこられなかった。別の PC で取ってきたフォルダを %s に置けば使える（README）"
                      % (repo, model_dir(kind)), file=sys.stderr)
                return 1
    print("できた。続けて index で、全部の会話の索引を作る（最初の一度だけ時間がかかる）")
    return 0


def _thread_id_arg(value):
    """forget に渡された値を会話の ID にする。前後の空白、大文字、search の「参照:」の形（<id>/<発言 ID>）を受け付ける。"""
    value = value.strip()
    if "/" in value:
        head, tail = value.rsplit("/", 1)
        if "." not in tail:  # memory:dir/notes.md の最後の区切りは、ファイル名なので切らない
            value = head
    if re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", value):
        value = value.lower()
    return value


def cmd_forget(args):
    idx, _, note = open_index(args)  # 先に増えた分を取り込む（まだ検索していない会話も forget できるように）
    if idx.readonly:
        print("索引に書けないので除外できない: %s" % note, file=sys.stderr)
        return 1
    home = codex_home()
    thread_id = _thread_id_arg(args.thread_id)
    if not idx.has_thread(home, thread_id):
        print("索引に無い: %s（search の結果の「参照:」の前半か、codex resume の ID を渡す）" % thread_id, file=sys.stderr)
        idx.close()
        return 1
    idx.forget(home, thread_id)
    print("除外した: %s（Codex の記録そのものは消していない。Codex の記録を消しても、この索引には残るので、"
          "検索から外すのは forget で行う）" % thread_id)
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


def _date_type(value):
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD の形で書く（例: 2026-09-01）: %s" % value)
    return value


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
    s.add_argument("terms", nargs="+", help="検索ワード（どれかを含めば候補になり、多く含むものが上に来やすい。\"...\" で語句。- で始まる言葉は、オプションを全部書いた後に -- を置き、その後ろに書く）")
    s.add_argument("--since", type=_date_type, help="この日から（YYYY-MM-DD、ローカルの日付）")
    s.add_argument("--until", type=_date_type, help="この日まで（YYYY-MM-DD、その日を含む）")
    s.add_argument("--cwd", help="作業フォルダで絞る（下の階層も含む）。here で今のフォルダ")
    s.add_argument("--all-kinds", action="store_true", help="途中の回答も検索する")
    s.add_argument("--include-agents", action="store_true", help="子のエージェントの会話も検索する")
    s.add_argument("--question", help="持ち主が言った問いの文。埋め込みの検索に検索ワードと一緒に渡す")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--budget", type=float, default=DEFAULT_EMBED_BUDGET, help="検索の前に埋め込みを足す時間の上限（秒）")
    s.add_argument("--no-rerank", action="store_true", help="上位を並べ直さない（速いが、似た会話の見分けが落ちる）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)
    sh = sub.add_parser("show", help="参照の発言と前後を表示する")
    sh.add_argument("ref")
    sh.add_argument("--context", type=int, default=1, help="前後に出すターンの数")
    sh.add_argument("--around", help="参照の発言のうち、この言葉の周りを表示する")
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
