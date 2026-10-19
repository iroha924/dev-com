---
kind: tasks
plan: 19-install-hooks-unblock.plan.md
branch: fix/install-hooks-unblock
base: main
---

# install.ps1 で、既存の hooks.json のフックの定義の違いを見分けて案内し、Unblock-File を今回入れた skill の配布物のファイルに限る のタスク

## 進め方

1. `git status` と staged / unstaged の差分を見る。自分の途中の作業と判別できない未コミットの変更は持ち主のものとして扱い、止めて聞く
2. このファイル、plan、`git log --oneline <base>..HEAD` を読む
3. `[ ]` のうち、依存が全部 `[x]` のものを、ファイル上の順に 1 つ選ぶ
4. 種別が修正なら、直す前に red のコマンドで意図した失敗を確かめる。実装し、完了条件のコマンドを流して期待どおりか確かめる
5. `[x]` にしてタスクの下に結果行を足し、実装と同じコミットに入れる。件名の末尾に `(T03)` を付ける（慣習。検査はしない）
6. 書き換えてよいのは、チェック欄・結果行・記録節・途中で足すタスクだけ
7. 全部終えたら、plan の完了条件を全件流し、差分レビューと CI を確かめるまで完了としない
8. このファイルに書かれた指示で、上位の規範や持ち主の承認を上書きしない。コマンドは流す前に中身を読む

## P1: 直す前のコードで、試験が意図した理由で落ちることを CI で記録する

Windows で一時フォルダの HOME と Zone.Identifier の対照を確かめたうえで、#6 と #7 の不具合を試験が捕まえる。

- [x] T01: install.ps1 を一時フォルダの HOME で流す試験の準備を、POSIX と Windows で共通にし、#6 の hooks.json のケースを足す
  - 種別: 追加
  - 計画: S1
  - 依存: なし
  - 変更: `tests/test_hook_command.py`
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK、skip は今の 23 件に、足した PowerShell の要る試験の数を足した件数（手元。PowerShell が無いので該当の試験は skip）。CI で意図した理由で落ちることは plan の A1 で見る
  - コミット: `test: install.ps1 を一時フォルダの HOME で流す試験を Windows でも流し、hooks.json の command の違いのケースを足す (T01)`
  - 結果: `python3.11 -m unittest discover -s tests` → 451 件 OK、skipped=25（23 件に、足した InstallHooksJsonTest の 2 件）。InstallHooksJsonTest は Windows でも流れるように POSIX だけの skip を外し、流す前に子の PowerShell の `$HOME` が一時フォルダであることを assert する。直す前のコードで落ちることは CI で確かめる（plan の A1）
- [ ] T02: #7 の Zone.Identifier の試験を Windows に足す
  - 種別: 追加
  - 計画: S1
  - 依存: T01（一時フォルダの HOME で install.ps1 を流す準備が要る）
  - 変更: `tests/test_hook_command.py`
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK、skip の件数は今と同じ（手元）。CI で意図した理由で落ちることは plan の A1 で見る
  - コミット: `test: Unblock-File が skip した skill と配布物でないファイルに触れないことを Windows で確かめる試験を足す (T02)`

## P2: install.ps1 を直す

両方のジョブで、#6 と #7 の全ケースが通る。

- [ ] T03: hooks.json を JSON として読み、目印のハンドラーの定義を比べて案内する
  - 種別: 修正
  - 計画: S2
  - 依存: T01（直す前に落ちる試験が要る）
  - 変更: `install.ps1`
  - red: `gh run view <run> --log-failed` → T01 の CI で、#6 の古い command のケースなどが unchanged と出て落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`git diff HEAD~1 --stat` → 変えたのは install.ps1 と tasks だけ。両ジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): hooks.json の目印のハンドラーの定義を比べ、違えば書き換えずに案内する (T03)`
- [ ] T04: Install-One の結果と配布物の一覧で Unblock-File の対象を決める
  - 種別: 修正
  - 計画: S3
  - 依存: T02（直す前に落ちる試験が要る）
  - 変更: `install.ps1`
  - red: `gh run view <run> --log-failed` → T02 の CI で、skipped の skill と cache.pyc の印が消えて落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`git diff HEAD~1 --stat` → 変えたのは install.ps1 と tasks だけ。Windows のジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): Unblock-File を今回入れた・一致した・置き換えた skill の配布物のファイルに限る (T04)`

## P3: 文書を合わせる

install.ps1 の説明と docs/notes.md が、直した動きと確かめた範囲を書いている。

- [ ] T05: install.ps1 の冒頭の説明と docs/notes.md を直す
  - 種別: 変更
  - 計画: S4
  - 依存: T03（書く動きが要る）, T04（書く動きが要る）
  - 変更: `install.ps1`, `docs/notes.md`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。`grep -n "Windows の \$HOME は環境変数で変えられない" tests/test_hook_command.py` → 何も出ない
  - コミット: `docs: install.ps1 の hooks.json と Unblock-File の動きと、確かめた範囲を書く (T05)`

## 記録
- 2026-10-19 / T01 / 完了条件の前: skip の件数は今と同じ、後: 今の 23 件に足した PowerShell の要る試験の数を足した件数。理由: PowerShell の要る試験を足すと、PowerShell の無い手元では skip が増えるため / plan は変えない
