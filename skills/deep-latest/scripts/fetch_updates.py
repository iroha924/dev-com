#!/usr/bin/env python3
"""Codex の公式アップデートを、日付付きのソースから前回の記録以降の分だけ集める。

  fetch_updates.py check --repo PATH --out result.json [--days N] [--state PATH]
  fetch_updates.py commit result.json [--state PATH] [--hold SOURCE ...] [--full-done]
  fetch_updates.py targets --repo PATH [--host codex|claude]

記録は相手のリポジトリ（--repo の git ルート）ごとに持つ。同じ PC で別のリポジトリを回しても、
片方で進めた記録のせいでもう片方が項目を見落とさないため。
check は記録を書き換えない。commit は check が見た最新の値だけを書く。check と commit の間に出た項目を
見たことにしないためで、取得に失敗したソースと --hold で指定したソースの記録は進めない。
記録が無いソースは直近 N 日（既定 7）を見る。日時を持たない Codex CLI のタグは、大きい順に 5 件を見る。
全体の突き合わせを終えた回だけ --full-done で full_done_at を書き、それが無い間は check が full_required を返す。
targets は突き合わせの候補を git ls-files から形で集め、置き場所で分かるものだけホストに分ける。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath

STATE_DIR = Path.home() / ".codex" / "deep-latest"
SOURCES = [
    ("codex", "rss", "https://learn.chatgpt.com/docs/changelog/rss.xml"),
    ("openai-news", "rss", "https://openai.com/news/rss.xml"),
    # CLI のリリースノートは上の changelog に載らない。GitHub の API は 1000 件より先を返さない（2026-09-27 に超えていた）ので
    # タグを列挙し、見たタグの一覧（seen_tags）に無い正式タグを出す。タグは日時を持たない。
    ("codex-cli", "git-tags", "https://github.com/openai/codex"),
]
HOST = "codex"
# 意図的に壊した fixture や同梱のライブラリは、設定ではない。
EXCLUDED_DIRS = {"evals", "fixtures", "vendor", "node_modules"}
PLACEMENT = {
    "claude": ({".claude"}, {"CLAUDE.md", "CLAUDE.local.md", ".mcp.json"}),
    "codex": ({".agents", ".codex"}, {"AGENTS.md", "AGENTS.override.md"}),
}
RELEASE_TAG = re.compile(r"refs/tags/(rust-v(\d+)\.(\d+)\.(\d+))$")
FIRST_RUN_TAGS = 5


class UsageError(Exception):
    """相手のリポジトリか記録先が決まらない。記録を書かずに止める。"""


def find_repo_root(repo: str) -> Path:
    if not repo:
        raise UsageError("--repo が空")
    # text=True は CRLF を LF に変えるので、名前の末尾の CR まで失う。bytes で受ける。
    found = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True, timeout=60)
    if found.returncode != 0:
        raise UsageError(found.stderr.decode("utf-8", "replace").strip() or f"{repo} は git のリポジトリの中ではない")
    # 末尾の空白や改行もディレクトリ名の一部になりうるので、git が足した最後の改行 1 つだけを落とす。
    out = found.stdout
    return Path((out[:-1] if out.endswith(b"\n") else out).decode("utf-8")).resolve()


def is_candidate(root: Path, path: PurePosixPath) -> bool:
    dirs, name = path.parts[:-1], path.name
    if EXCLUDED_DIRS & set(dirs):
        return False
    if name in {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", "SKILL.md", ".mcp.json", "hooks.json"}:
        return True
    parent = dirs[-1] if dirs else ""
    if name.endswith(".md"):
        return "rules" in dirs or parent == "agents"
    if name == "openai.yaml":
        return parent == "agents"
    if name.startswith("settings") and name.endswith(".json"):
        return parent in {".claude", "claude"}
    if name.endswith(".toml"):
        if ".codex" in dirs:
            return True
        try:
            return "developer_instructions" in (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
    return False


def host_by_placement(path: PurePosixPath) -> str | None:
    """置き場所だけで決まるホスト。決まらない・両方に当たるなら None（SKILL.md の手順でモデルが分ける）。"""
    hits = {host for host, (dirs, names) in PLACEMENT.items() if dirs & set(path.parts[:-1]) or path.name in names}
    if path.name == "openai.yaml":
        hits.add("codex")
    return hits.pop() if len(hits) == 1 else None


def targets(repo_root: Path, host: str) -> dict:
    listed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"], capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if listed.returncode != 0:
        raise UsageError(listed.stderr.strip() or "git ls-files が失敗した")
    out = {"repo_root": str(repo_root), "host": host, "classified": [], "other_host": [], "unclassified": []}
    for name in sorted(n for n in listed.stdout.split("\0") if n):
        path = PurePosixPath(name)
        if not is_candidate(repo_root, path):
            continue
        found = host_by_placement(path)
        out["unclassified" if found is None else "classified" if found == host else "other_host"].append(name)
    return out


def same_file(a: Path, b: Path) -> bool:
    # resolve() は symlink と相対パスを揃えるが、hard link と大文字小文字の別名は samefile() でしか分からない。
    if a.resolve() == b.resolve():
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def default_state_path(repo_root: Path) -> Path:
    # 名前は人が見分けるためだけのもの。同じ名前の別の clone は hash で分かれる。
    name = re.sub(r"[^A-Za-z0-9._-]", "_", repo_root.name)[:40] or "repo"
    digest = hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:12]
    return STATE_DIR / f"{name}-{digest}.json"


def parse_time(text: str) -> datetime:
    text = text.strip()
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        value = parsedate_to_datetime(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fetch(url: str) -> ET.Element:
    request = urllib.request.Request(url, headers={"User-Agent": "deep-latest"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return ET.fromstring(response.read())


def release_tags(repo: str) -> list[tuple[list[int], str]]:
    listed = subprocess.run(
        ["git", "ls-remote", "--refs", "--tags", repo, "rust-v*"], capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if listed.returncode != 0:
        raise RuntimeError(listed.stderr.strip() or f"git ls-remote が {listed.returncode} で終わった")
    tags = []
    for line in listed.stdout.splitlines():
        match = RELEASE_TAG.search(line)
        if match:
            tags.append(([int(n) for n in match.groups()[1:]], match.group(1)))
    if not tags:
        raise RuntimeError("正式バージョンのタグが 1 つも無い")
    return tags


def items(root: ET.Element) -> list[dict]:
    out = []
    for item in root.iter("item"):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "date": item.findtext("pubDate") or "",
            "link": (item.findtext("link") or "").strip(),
        })
    return [i for i in out if i["date"]]


def load_state(state_path: Path) -> dict:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError は壊れた JSON と UTF-8 の両方
        return {}
    return state if isinstance(state, dict) and isinstance(state.get("sources"), dict) else {}


def load_seen(state: dict, name: str) -> tuple[datetime, set[str]] | None:
    """記録の形が崩れていれば None を返し、呼び出し側は直近 N 日に戻る。"""
    record = state.get("sources", {}).get(name)
    try:
        return parse_time(record["newest"]), set(record.get("links_at_newest", []))
    except (TypeError, KeyError, ValueError, AttributeError):
        return None


def full_done(state: dict) -> bool:
    try:
        parse_time(state["full_done_at"])
    except (TypeError, KeyError, ValueError, AttributeError):
        return False
    return True


def check(days: int, state_path: Path, repo_root: Path) -> dict:
    state = load_state(state_path)
    fallback = datetime.now(timezone.utc) - timedelta(days=days)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "state_path": str(state_path.absolute()),
        "full_required": not full_done(state),
        "sources": [],
    }
    for name, kind, url in SOURCES:
        if kind == "git-tags":
            result["sources"].append(tag_entry(name, url, state))
            continue
        seen = load_seen(state, name)
        since, seen_links = seen if seen else (fallback, set())
        entry = {"name": name, "url": url, "since": since.isoformat(), "since_from": "record" if seen else f"{days} days"}
        try:
            found = items(fetch(url))
        except Exception as error:  # 取得や解析の失敗はソースごとに記録し、他のソースは続ける
            entry.update(ok=False, error=f"{type(error).__name__}: {error}")
            result["sources"].append(entry)
            continue
        dated = sorted(((parse_time(i["date"]), i) for i in found), key=lambda pair: pair[0], reverse=True)
        # 同じ日時で後から増えた項目を落とさないよう、記録と同じ日時の項目はリンクで見分ける。
        new = [dict(i, date=d.isoformat()) for d, i in dated if d > since or (d == since and i["link"] not in seen_links)]
        newest = dated[0][0] if dated else (since if seen else None)
        entry.update(
            ok=True,
            newest=newest.isoformat() if newest else None,
            # 最新の日時が記録と同じなら、一度消えて戻った項目を再報告しないよう既知のリンクを残す。
            links_at_newest=sorted({i["link"] for d, i in dated if d == newest} | (seen_links if newest == since else set())),
            new_count=len(new),
            new_items=new,
        )
        result["sources"].append(entry)
    result["any_new"] = any(s.get("new_count") for s in result["sources"])
    result["all_ok"] = all(s["ok"] for s in result["sources"])
    return result


def load_seen_tags(state: dict, name: str) -> set[str] | None:
    """記録の形が崩れていれば None を返し、呼び出し側は初回の見方に戻る。"""
    record = state.get("sources", {}).get(name)
    tags = record.get("seen_tags") if isinstance(record, dict) else None
    return set(tags) if isinstance(tags, list) and all(isinstance(t, str) for t in tags) else None


def tag_entry(name: str, url: str, state: dict) -> dict:
    """正式タグを大きい順に並べ、記録に無いものを出す。記録が無ければ大きい順に FIRST_RUN_TAGS 件。"""
    seen = load_seen_tags(state, name)
    entry = {"name": name, "url": url, "since": None,
             "since_from": "record" if seen is not None else f"記録なし（大きい順に {FIRST_RUN_TAGS} 件）"}
    try:
        tags = [tag for _, tag in sorted(release_tags(url), reverse=True)]
    except Exception as error:
        entry.update(ok=False, error=f"{type(error).__name__}: {error}")
        return entry
    new = [t for t in tags if t not in seen] if seen is not None else tags[:FIRST_RUN_TAGS]
    entry.update(
        ok=True, newest=None, links_at_newest=[], tags=tags, new_count=len(new),
        new_items=[{"title": t, "date": None, "link": f"{url}/releases/tag/{t}"} for t in new],
    )
    return entry


def commit(
    result_path: Path, state_path: Path | None = None, hold: list[str] | tuple[str, ...] = (), full_done_now: bool = False,
) -> None:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # 結果の state_path だけを信じず、repo_root から計算し直して照らす。結果を書き換えて別の記録を進めないため。
    expected = (state_path or default_state_path(Path(result["repo_root"]))).absolute()
    if str(expected) != result["state_path"]:
        raise UsageError(f"記録先 {expected} が、check の結果の {result['state_path']} と違う")
    state_path = expected
    state = load_state(state_path) or {"sources": {}}
    sources = state["sources"]
    for source in result["sources"]:
        if not source["ok"] or source["name"] in hold:
            continue
        if "tags" in source:
            # check が見た一覧と既読の和。commit の時点で取り直さず、消えて戻ったタグも既読のまま残す。
            seen = load_seen_tags(state, source["name"]) or set()
            sources[source["name"]] = {"seen_tags": sorted(seen | set(source["tags"]))}
        elif source.get("newest"):
            sources[source["name"]] = {"newest": source["newest"], "links_at_newest": source["links_at_newest"]}
    state["checked_at"] = result["checked_at"]
    if full_done_now:
        state["full_done_at"] = result["checked_at"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--repo", required=True, help="相手のリポジトリの中のパス。git ルートに直す")
    c.add_argument("--out", type=Path, required=True, help="結果の JSON を UTF-8 で書く先")
    c.add_argument("--days", type=int, default=7)
    c.add_argument("--state", type=Path, help="記録先。省くと相手のリポジトリから決める")
    m = sub.add_parser("commit")
    m.add_argument("result", type=Path)
    m.add_argument("--state", type=Path, help="check に渡した記録先。省くと結果の repo_root から決める")
    m.add_argument("--hold", action="append", default=[], help="記録を進めないソース（ノートを読み切れなかったときなど）")
    m.add_argument("--full-done", action="store_true", help="全体の突き合わせを終えた回だけ付ける")
    t = sub.add_parser("targets")
    t.add_argument("--repo", required=True, help="相手のリポジトリの中のパス。git ルートに直す")
    t.add_argument("--host", choices=sorted(PLACEMENT), default=HOST)
    args = parser.parse_args()
    try:
        if args.command == "check":
            root = find_repo_root(args.repo)
            state = args.state or default_state_path(root)
            if same_file(args.out, state):
                raise UsageError(f"--out が記録先 {state} と同じ。check は記録を書き換えない")
            result = check(args.days, state, root)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif args.command == "commit":
            commit(args.result, args.state, args.hold, args.full_done)
        else:
            # Windows のコンソールの文字コードに左右されないよう、ASCII で出す。
            json.dump(targets(find_repo_root(args.repo), args.host), sys.stdout, indent=2)
            print()
    except UsageError as error:
        print(f"fetch_updates.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
