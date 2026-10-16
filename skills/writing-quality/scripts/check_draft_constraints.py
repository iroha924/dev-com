#!/usr/bin/env python3
"""下書きが外形上の契約を破っていないかだけを見る。**文章の質は測らない。**

AI 臭さの機械判定は、hir4ta-developer の実データでは修正判断に使える差が出なかった
（定型句辞書は Claude 作 58,780 字と人間編集 5,558 字の両方で 0 件、
文長の変動係数は 0.596 対 0.563・0.605）。だからここには文体統計もNG ワードの辞書も置かない。
質は SKILL.md の評価基準とレビューが担う。

  python check_draft_constraints.py <draft> [--template <path>] [--max-chars <n>]

対応する形式は Markdown（.md / .markdown）とテキスト（.txt）だけ。HTML などをここで読むと、
見出しも段落も拾えないまま「満たしている」と答えてしまうので、検査せずに終了コード 2 で止める。
その内容は SKILL.md の手順でレビューする。

終了コード 1 は、空の下書き・見出しの欠落・字数超過だけに割り当てる。未記入マーカーと
逐語的重複は意図的なことがあるので、警告として出して終了コードを変えない。
終了コード 2 は、ファイルが無い・未対応の形式・UTF-8 で読めないなど、検査が走っていないとき。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# closing sequence（`## 概要 ##`）は CommonMark では見出し名に入らない。
HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# 有限の固定リストにする。山括弧や HTML コメントを「プレースホルダーらしきもの」として
# 拾い始めると、塞ぐ面に終端が無くなり、本文中の正当な表記を誤検出する。
PLACEHOLDERS = ("TODO", "TBD", "FIXME", "XXX")

SUPPORTED = {".md": "Markdown", ".markdown": "Markdown", ".txt": "テキスト"}


def headings(text: str) -> list[tuple[int, str]]:
    """ATX 見出しを (レベル, 名前) で返す。**コードフェンスの中は数えない。**

    Setext（`概要` の下に `===`）は拾わない。GitHub の PR / issue テンプレートは
    ATX で書かれるため、状態機械を増やす価値がない。
    """
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
            elif h := HEADING.match(line):
                out.append((len(h.group(1)), h.group(2)))
            continue
        # 閉じるのは、開いたものと同じ文字で同じ長さ以上、後ろが空白だけの行（CommonMark 4.5）。
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            fence = None
    return out


def body_paragraphs(text: str) -> list[str]:
    """見出しと表を除いた段落。重複比較の対象をここに限る。"""
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        kept = [ln for ln in lines if not HEADING.match(ln) and not ln.lstrip().startswith("|")]
        if kept:
            out.append("\n".join(kept).strip())
    return out


def load(path: Path, role: str) -> tuple[str, str] | None:
    """(形式, 本文) を返す。読めなければ理由を出して None。呼び出し側は終了コード 2 で止める。"""
    try:
        exists = path.is_file()
    except OSError as e:
        print(f"{role}が読めない: {path}（{type(e).__name__}）。検査は走っていない", file=sys.stderr)
        return None
    if not exists:
        print(f"{role}が無い: {path}。検査は走っていない", file=sys.stderr)
        return None
    kind = SUPPORTED.get(path.suffix.lower())
    if kind is None:
        print(
            f"{role}は未対応の形式: {path.suffix or '拡張子なし'}（対応は .md / .markdown / .txt）。"
            "検査は走っていない。内容は SKILL.md の手順でレビューする",
            file=sys.stderr,
        )
        return None
    try:
        # BOM 付きで保存されると先頭の見出しが `\ufeff## 概要` になり、一致しなくなる。
        return kind, path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as e:
        print(f"{role}が読めない: {path}（{type(e).__name__}）。検査は走っていない", file=sys.stderr)
        return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", type=Path)
    ap.add_argument("--template", type=Path, help="この見出しが下書きに揃っているかを見る")
    ap.add_argument("--max-chars", type=int, help="ユーザーが明示した上限のときだけ渡す")
    args = ap.parse_args()

    loaded = load(args.draft, "下書き")
    if loaded is None:
        return 2
    kind, draft = loaded

    failures: list[str] = []
    warnings: list[str] = []

    if not draft.strip():
        failures.append("下書きが空である")

    if args.template:
        loaded_template = load(args.template, "テンプレート")
        if loaded_template is None:
            return 2
        if kind != "Markdown" or loaded_template[0] != "Markdown":
            print("見出しの検査は Markdown の下書きと Markdown のテンプレートでだけ行う。検査は走っていない", file=sys.stderr)
            return 2
        # 個数で比べる。同名の見出しが 2 つあるテンプレートを 1 つで満たさないため。
        # 順序と、下書き側の余分な見出しは見ない（構造の追加まで縛る根拠がない）。
        missing = Counter(headings(loaded_template[1])) - Counter(headings(draft))
        if missing:
            shown = " / ".join(f"{'#' * lv} {name}" + (f" ×{n}" if n > 1 else "") for (lv, name), n in missing.items())
            failures.append("テンプレートの見出しが欠けている: " + shown)

    n_chars = len(draft)
    if args.max_chars is not None and n_chars > args.max_chars:
        failures.append(f"字数超過: {n_chars} 字 > 上限 {args.max_chars} 字")

    for marker in PLACEHOLDERS:
        for i, line in enumerate(draft.splitlines(), 1):
            if marker in line:
                warnings.append(f"{i} 行目に未記入マーカー {marker}: {line.strip()[:60]}")

    seen: Counter[str] = Counter(body_paragraphs(draft))
    for para, n in seen.items():
        if n > 1:
            warnings.append(f"同じ段落が {n} 回: {para.splitlines()[0][:60]}")

    shown_headings = f"{len(headings(draft))} 個" if kind == "Markdown" else "対象外"
    print(f"{args.draft}: 形式 {kind} / {n_chars} 字 / 見出し {shown_headings}")
    for w in warnings:
        print(f"  warning: {w}")
    for f in failures:
        print(f"  FAIL: {f}")
    if not failures and not warnings:
        print("  外形上の契約は満たしている（質はこのスクリプトでは測らない）")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
