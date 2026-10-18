---
kind: plan
status: approved
codex_session: 01a14d67-8037-75f3-be63-8ad42a168af6
codex_rounds: 4
approved_at: 2026-10-18
---

# dev-codex の deep-latest を写し、会社の PC の Codex で実行したリポジトリの設定を公式アップデートと突き合わせる

## 要点

- dev-codex の `skills/deep-latest/` を写し、`install.ps1` が `$HOME\.agents\skills` に配る。`$deep-latest` と名前で呼んだときだけ動く
- `fetch_updates.py` は dev-codex と同じファイル。違いは SKILL.md の呼び方（`python "$HOME\.agents\skills\..."`）、推奨の設定の行、PR を作る先が ADO のときの `ado` skill の承認だけ
- テストは `tests/test_scripts.py` に写し、`tests/test_layout.py` の `MANUAL_SKILLS`、README の一覧と skill の数、docs/notes.md を直す
- Windows での実走は持ち主が会社の PC で確かめる。届かないソースは `ok: false` と出て「更新なし」とは言わない
- 変えないもの: `install.ps1`、既存の skill と agent、AGENTS.md

## 持ち主の決定

- deep-latest を dev-codex と dev-com にも持たせ、Codex 側で動く skill にする（2026-10-18）
- 3 つともグローバルにする。どのプロジェクトで実行しても、その配下の設定が対象になる（2026-10-18、議論の 1 往復目の後に持ち主が追加）

## 目的

- 会社の PC の Codex で、どのリポジトリでも `$deep-latest` を呼ぶと、そのリポジトリの Codex の設定について前回以降の公式アップデートで直すものが一覧で出て、PR を作るかを聞かれる
- 会社のネットワークで届かないソースがあっても、届かなかったと報告され、記録はそのソースだけ進まない

## 対象外

- Claude の設定とソース
- 会社のネットワークの設定を変えること

## 前提

- 仕様の全体は dev-claude の `.claude/plans/2026/10/18-global-deep-latest.plan.md`、Codex 版の作りは dev-codex の同じ名前の plan（同じ Codex の会話で 4 往復して合意した）。このリポジトリでは dev-codex の `skills/deep-latest/` を写して変える
- `install.ps1:108-109` は `skills\` の下を 1 つずつ `$HOME\.agents\skills` にコピーする。install の変更は要らない
- `tests/test_layout.py` は `skills/` の各 skill について、説明の合計が 6,000 文字以内（:88）、手動だけの skill が `MANUAL_SKILLS` と一致（:14、:95）、推奨の設定の行（:134）、README の一覧に行があること（:142-147）、SKILL.md が参照するスクリプトがあること（:149-）を見る
- AGENTS.md「Windows の文字コードと改行」: Python の `open()` と subprocess の text には `encoding` を書く。PowerShell 5.1 の `>` は UTF-16 で書くので、UTF-8 で書きたいファイルは Python で書く
- AGENTS.md「外へ書き込む前に」: ADO の PR などは本文を見せて承認を受けてから、`ado` skill の手順で送る
- README:3 と :92 が skill の数を 18 個と書いている
- CI は無い。テストは `python -m unittest discover -s tests`（docs/notes.md「テスト」）
- 未検証: 会社の PC から learn.chatgpt.com、openai.com、github.com（`git ls-remote`）に届くか。Windows・Python 3.11 でスクリプトが動くか（この Mac では流せない）

## 方針

- `skills/deep-latest/scripts/fetch_updates.py`: dev-codex のファイルをそのまま写す
- `skills/deep-latest/agents/openai.yaml`: dev-codex と同じ（`policy.allow_implicit_invocation: false`）
- `skills/deep-latest/SKILL.md`: dev-codex の SKILL.md を写して、次だけ変える
  - `推奨の設定: gpt-6.1-sol・medium` の行を足す
  - 呼び方を `python "$HOME\.agents\skills\deep-latest\scripts\fetch_updates.py"` にする（PowerShell）。`--out` で結果を書かせ、`>` で受けない
  - 手順 5 の PR の作成: 相手が ADO のリポジトリなら `ado` skill の手順で本文を見せて承認を受けてから作る。GitHub なら PR 本文を見せて承認を受ける
  - 届かないソースがあれば、会社のネットワークで止められている見込みと、そのソースの名前を報告に書く
- `tests/test_scripts.py`: dev-codex の deep-latest のテストを写す（ネットワークに出ない）
- `tests/test_layout.py`: `MANUAL_SKILLS` に `deep-latest` を足す
- README: 一覧の表に行を足し、skill の数を 19 個にする
- docs/notes.md: 「skill の詳細」に `deep-latest` の節（何を見るか、記録の場所 `~/.codex/deep-latest/`、会社のネットワークで届かないときの扱い）を足す

## 採った案と棄却した案

- 採用: dev-codex と同じスクリプトを写す。棄却: Windows 向けに書き分ける（2 つが少しずつずれる）
- 採用: 手動だけ（`MANUAL_SKILLS`）。棄却: 暗黙の起動（重い監査で、会社のアカウントの利用枠が小さい）

## 手順

- S1: スクリプトとテストを写す
- S2: SKILL.md と `agents/openai.yaml` を写して会社向けに変え、`MANUAL_SKILLS` と README と docs/notes.md を直す

## 完了条件

- A1: `python3 -m unittest discover -s tests` → OK（この Mac で。PowerShell が無いテストは skipped）
- A2: `diff ~/Projects/dev-codex/skills/deep-latest/scripts/fetch_updates.py skills/deep-latest/scripts/fetch_updates.py` → 差分なし
- A3: （持ち主が会社の PC で流す）`.\install.ps1` の後、`python "$HOME\.agents\skills\deep-latest\scripts\fetch_updates.py" check --repo . --state $env:TEMP\dl\s.json --out $env:TEMP\dl\r.json` → 終了コード 0。届いたソースは `ok: true`、届かないソースは `ok: false` と `error`。続けて `commit $env:TEMP\dl\r.json --state $env:TEMP\dl\s.json` → 終了コード 0
- A4: （持ち主が会社の PC で流す）Codex で `$deep-latest` → 相手がそのリポジトリになり、届かなかったソースを「更新なし」と言わずに報告して、選択肢で止まる

## リスク

- 会社のネットワークでソースに届かない → 届かないソースは記録が進まず、報告に名前が出る。全部届かなければこの PC では使えないと分かる
- Windows で git の出力の文字コードが合わない → encoding を `utf-8` と書いている。A3 で崩れたら、その出力を持ち帰って直す

## 未解決

なし

## 変更履歴
