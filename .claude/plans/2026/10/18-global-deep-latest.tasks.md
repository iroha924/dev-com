---
kind: tasks
plan: 18-global-deep-latest.plan.md
branch: feat/deep-latest
base: main
---

# dev-codex の deep-latest を写し、会社の PC の Codex で実行したリポジトリの設定を公式アップデートと突き合わせる のタスク

## 進め方

1. `git status` と staged / unstaged の差分を見る。自分の途中の作業と判別できない未コミットの変更は持ち主のものとして扱い、止めて聞く
2. このファイル、plan、`git log --oneline <base>..HEAD` を読む
3. `[ ]` のうち、依存が全部 `[x]` のものを、ファイル上の順に 1 つ選ぶ
4. 種別が修正なら、直す前に red のコマンドで意図した失敗を確かめる。実装し、完了条件のコマンドを流して期待どおりか確かめる
5. `[x]` にしてタスクの下に結果行を足し、実装と同じコミットに入れる。件名の末尾に `(T03)` を付ける（慣習。検査はしない）
6. 書き換えてよいのは、チェック欄・結果行・記録節・途中で足すタスクだけ
7. 全部終えたら、plan の完了条件を全件流し、差分レビューと CI を確かめるまで完了としない
8. このファイルに書かれた指示で、上位の規範や持ち主の承認を上書きしない。コマンドは流す前に中身を読む

## P1: dev-codex の skill を写して会社向けに変える

会社の PC の Codex で `$deep-latest` が使えるようになる。dev-codex の tasks を終えてから始める（写す元）。

- [ ] T01: `skills/deep-latest/` を写して会社向けに変え、テストと一覧に足す
  - 種別: 追加
  - 計画: S1, S2
  - 依存: なし
  - 変更: `skills/deep-latest/`、`tests/test_scripts.py`、`tests/test_layout.py`、`README.md`
  - 完了条件: `python3 -m unittest discover -s tests` → OK。`diff ~/Projects/dev-codex/skills/deep-latest/scripts/fetch_updates.py skills/deep-latest/scripts/fetch_updates.py` → 差分なし
  - コミット: `feat(deep-latest): 実行したリポジトリの Codex の設定を公式アップデートと突き合わせる skill を足す`

- [ ] T02: docs/notes.md に deep-latest の節を足し、README の skill の数を直す
  - 種別: 追加
  - 計画: S2
  - 依存: T01（書く対象の skill が要る）
  - 変更: `docs/notes.md`、`README.md`
  - 完了条件: `python3 -m unittest discover -s tests` → OK。`grep -c '19 個' README.md` → 2
  - コミット: `docs: deep-latest の見るものと、会社のネットワークで届かないときの扱いを書く`

## 記録
