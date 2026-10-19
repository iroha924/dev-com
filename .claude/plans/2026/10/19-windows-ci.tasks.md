---
kind: tasks
plan: 19-windows-ci.plan.md
branch: ci/windows-tests
base: main
---

# GitHub Actions で、Windows（Python 3.11・Windows PowerShell 5.1）と Ubuntu（PowerShell 7）の既存テストを PR と main への push のたびに流す のタスク

## 進め方

1. `git status` と staged / unstaged の差分を見る。自分の途中の作業と判別できない未コミットの変更は持ち主のものとして扱い、止めて聞く
2. このファイル、plan、`git log --oneline <base>..HEAD` を読む
3. `[ ]` のうち、依存が全部 `[x]` のものを、ファイル上の順に 1 つ選ぶ
4. 種別が修正なら、直す前に red のコマンドで意図した失敗を確かめる。実装し、完了条件のコマンドを流して期待どおりか確かめる
5. `[x]` にしてタスクの下に結果行を足し、実装と同じコミットに入れる。件名の末尾に `(T03)` を付ける（慣習。検査はしない）
6. 書き換えてよいのは、チェック欄・結果行・記録節・途中で足すタスクだけ
7. 全部終えたら、plan の完了条件を全件流し、差分レビューと CI を確かめるまで完了としない
8. このファイルに書かれた指示で、上位の規範や持ち主の承認を上書きしない。コマンドは流す前に中身を読む

## P1: テストが PowerShell の有無と版を確かめ、workflow の書き方を検査する

手元だけで完結する。CI が無くても、必須の指定で PowerShell が無い・版が違うときに落ち、workflow の固定と権限の崩れが落ちる。

- [x] T01: PowerShell の探し方を tests/powershell.py にまとめ、DEV_COM_REQUIRE_POWERSHELL と DEV_COM_EXPECT_POWERSHELL で必須と版を確かめる
  - 種別: 追加
  - 計画: S1, S2
  - 依存: なし
  - 変更: `tests/powershell.py`, `tests/test_powershell.py`, `tests/test_ado_ps1.py`, `tests/test_hook_command.py`
  - red: `DEV_COM_REQUIRE_POWERSHELL=1 python3.11 -m unittest tests.test_ado_ps1 tests.test_hook_command` → 直す前は PowerShell の無い手元でも OK（skip で通る）
  - 完了条件: `DEV_COM_REQUIRE_POWERSHELL=1 python3.11 -m unittest tests.test_ado_ps1 tests.test_hook_command` → 0 以外で終わる。`python3.11 -m unittest tests.test_powershell` → OK。`python3.11 -m unittest discover -s tests` → OK、skip 23
  - コミット: `test: PowerShell の探し方をまとめ、CI の指定で不在と版違いを失敗にする (T01)`
  - 結果: red は直す前に `DEV_COM_REQUIRE_POWERSHELL=1 python3.11 -m unittest tests.test_ado_ps1 tests.test_hook_command` → Ran 20、OK (skipped=20)、終了コード 0。直した後は同じコマンドが PowerShellError で終了コード 1。`python3.11 -m unittest tests.test_powershell` → OK（5 件）。`python3.11 -m unittest discover -s tests` → 446 件 OK、skipped=23。`DEV_COM_REQUIRE_POWERSHELL=1 python3.11 -m unittest discover -s tests` → test_ado_ps1・test_hook_command・test_powershell の読み込みが失敗し、FAILED (errors=3)
- [x] T02: tests/test_layout.py に workflow の固定と権限の検査を足す
  - 種別: 追加
  - 計画: S3
  - 依存: なし
  - 変更: `tests/test_layout.py`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。崩した workflow の見本（`@v7`、ジョブの `permissions: write-all`、`persist-credentials: false` の欠落、`secrets.X`、`pull_request_target`、`continue-on-error`）をそれぞれ検査に通すと違反が出る
  - コミット: `test: workflow の action の固定と権限を検査する (T02)`
  - 結果: `python3.11 -m unittest tests.test_layout` → 20 件 OK。WorkflowTest.test_the_check_catches_each_break で、見本の workflow を 11 通り（`@v7`、SHA の後の版の欠落、ジョブの `permissions: write-all`、`contents: write`、権限の追加、`persist-credentials: false` の欠落、`secrets.X`、`pull_request_target`、`continue-on-error`、アンカー、フローの書き方）に崩すと、それぞれ違反が出る。崩す前の見本は違反 0

- [x] T06: workflow の検査を、許す書き方だけの 1 行ずつの文法にし、引用符・エスケープ・空行やコメントをはさんだ権限・文字列の中の persist-credentials・大文字の checkout ですり抜けないようにする
  - 種別: 修正
  - 計画: S3
  - 依存: T02（直す検査が要る）
  - 変更: `tests/test_layout.py`
  - red: `python3.11 -m unittest tests.test_layout.WorkflowTest` → 直す前は、足した見本 8 件（引用符の uses・permissions、エスケープした禁止語・secret、空行やコメントの後の権限、文字列の中の persist-credentials、大文字の checkout）で違反が出ず、正しい見本 2 件（run の中のシェルの *、権限の中のコメント）で違反が出て、failures=10
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。足した見本と、値の下の深い行がそれぞれ違反になり、`.github/workflows/tests.yml` は違反 0
  - コミット: `test: workflow の検査を許す書き方だけの文法にし、引用符やエスケープですり抜けないようにする (T06)`
  - 結果: red は上のとおり failures=10 を実測。直した後 `python3.11 -m unittest tests.test_layout` → 21 件 OK。`workflow_errors` は `.github/workflows/tests.yml` に対して []

## P2: CI を入れ、Windows PowerShell 5.1 と PowerShell 7 で流す

PR と main への push のたびに、両方のジョブで必須のテストが走ったことを CI のログで確かめられる。

- [x] T03: .github/workflows/tests.yml を足す
  - 種別: 追加
  - 計画: S4
  - 依存: T01（環境変数で必須にしないと、PowerShell が無くても CI が通ってしまう）
  - 変更: `.github/workflows/tests.yml`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK で、workflow の違反 0。`gh run view <run> --log` → PR の CI で両ジョブが tests の手順まで進み、versions の手順の `git rev-parse HEAD` が PR の先端と同じで、Windows に PowerShell の絶対パスと 5.1 の版が出る
  - コミット: `ci: Windows PowerShell 5.1 と PowerShell 7 で既存のテストを流す (T03)`
  - 結果: `python3.11 -m unittest tests.test_layout` → 21 件 OK、`.github/workflows/tests.yml` の違反 0。PR #18 の run 39185321545 で両ジョブが tests の手順まで進んだ。versions の手順の `git rev-parse HEAD` は両ジョブとも bbd759466aa2bcec864530ab3a6b5ae1b4920470（PR の先端）。Windows は `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.EXE`、5.1.26100.33438、Python 3.11.9。Ubuntu は /usr/bin/pwsh、7.6.6、Python 3.11.17。tests の手順は両ジョブとも失敗し（T04）、負の確かめの手順はまだ走っていない（plan の A4 で確かめる）。ジョブに timeout-minutes: 20 を足した（既定は 6 時間。この回の Windows のジョブは約 2.5 分）
- [x] T04: 最初の CI で Windows だけで落ちたテストの組み立てを直す（落ちなければ取りやめ）
  - 種別: 修正
  - 計画: S5
  - 依存: T03（Windows で落ちるテストは CI を流すまで分からない）
  - 変更: `tests/test_session_report.py`, `tests/test_session_search.py`
  - red: `gh run view <run> --log-failed` → T03 の CI で Windows のジョブだけが落ちたテストと理由
  - 完了条件: `TZ=UTC python3.11 -m unittest tests.test_session_report tests.test_session_search` → OK（親プロセスの時差がランナーと同じ UTC でも通る）。`python3.11 -m unittest discover -s tests` → OK、skip 23。`git diff main -- tests` に期待値の緩和・必須の skip・continue-on-error が無い
  - コミット: `test: Windows で落ちたテストの組み立てを直す (T04)`
  - 結果: red は run 39185321545 で、Ubuntu 1 件・Windows 5 件。Ubuntu と Windows の test_week_keeps_the_state_when_the_record_is_unchanged は、CLI の子（TZ を渡す）と同じプロセスの build_week（ランナーは UTC）の digest を比べていた。Windows の test_view_contents・test_input_orders_messages_and_operations は TZ=Asia/Tokyo を C ランタイムが読めず、時刻が 10:12 ではなく 02:12。test_attachments_keep_only_names は JSON の中の \\ を消せず Users が残った。test_run_eval_mixtures_and_home は 8.3 形式の一時フォルダ（RUNNER~1）と realpath の食い違い。直す前の手元で `TZ=UTC python3.11 -m unittest tests.test_session_report` → test_week_keeps_the_state_when_the_record_is_unchanged が FAIL を再現。直した後は `TZ=UTC python3.11 -m unittest tests.test_session_report tests.test_session_search` → 320 件 OK、`python3.11 -m unittest discover -s tests` → 449 件 OK、skip 23。Windows の 3 件は手元で再現できず、CI（plan の A4・A5）で確かめる

## P3: 文書を CI で確かめた事実に合わせる

docs/notes.md のテストの説明と「確かめていないこと」が、CI で確かめた範囲と未確認の範囲を分けて書いている。

- [ ] T05: docs/notes.md の「テスト」と「確かめていないこと」を直す
  - 種別: 変更
  - 計画: S6
  - 依存: T03（書く版と結果は CI のログから取る）
  - 変更: `docs/notes.md`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。`grep -nE "skipped. が 0 件|はテストの対象外" docs/notes.md` → 何も出ない
  - コミット: `docs: テストの説明と確かめていないことを、CI で確かめた範囲に合わせる (T05)`

## 記録
- 2026-10-19 / T01 / Codex のタスクごとのレビュー（06c40e8）は指摘なし。全テストは Codex の sandbox で流せず、手元で 446 件 OK を確かめた / 受け取った
- 2026-10-19 / T02 / Codex のタスクごとのレビュー（68ec44b）が、行で見る検査のすり抜け 4 件と誤検出 1 件を再現した（F1〜F5）。すべて受け入れ / 修正タスク T06 を足して直した
- 2026-10-19 / T03 / Codex のレビュー（bbd7594）の 1 回目は、AGENTS.md の設定の案内で止まり covered が空だった。非対話の実行と依頼文に書いてやり直し、指摘なし / 受け取った
- 2026-10-19 / T03, T04 / 完了条件を「PR の CI が通る」から、そのタスクのコミットで確かめられるものに変えた。T03 の前: 負の確かめの手順が版違いで落ちたことが出る、後: Windows に絶対パスと 5.1 の版が出る（負の確かめは A4）。T04 の前: `gh run view <run> --log` → 両ジョブが成功、後: TZ=UTC で手元のテストが通り、期待値を緩めていない（CI の成功は A4・A5）。T04 の変更欄の前: `tests/`、後: 2 ファイル。理由: CI の結果は push の後にしか分からず、チェックを付けるコミットの中で確かめられないため / plan の A4・A5 は変えない
