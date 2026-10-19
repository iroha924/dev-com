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

## P2: CI を入れ、Windows PowerShell 5.1 と PowerShell 7 で流す

PR と main への push のたびに、両方のジョブで必須のテストが走ったことを CI のログで確かめられる。

- [ ] T03: .github/workflows/tests.yml を足す
  - 種別: 追加
  - 計画: S4
  - 依存: T01（環境変数で必須にしないと、PowerShell が無くても CI が通ってしまう）
  - 変更: `.github/workflows/tests.yml`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。`gh run view <run> --log` → PR の CI で両ジョブがテストの手順まで進み、`git rev-parse HEAD` が PR の先端と同じ、Windows に PowerShell の絶対パスと版、Ubuntu の負の確かめの手順が版違いで落ちたことが出る
  - コミット: `ci: Windows PowerShell 5.1 と PowerShell 7 で既存のテストを流す (T03)`
- [ ] T04: 最初の CI で Windows だけで落ちたテストの組み立てを直す（落ちなければ取りやめ）
  - 種別: 修正
  - 計画: S5
  - 依存: T03（Windows で落ちるテストは CI を流すまで分からない）
  - 変更: `tests/`
  - red: `gh run view <run> --log-failed` → T03 の CI で Windows のジョブだけが落ちたテストと理由
  - 完了条件: `gh run view <run> --log` → 両ジョブが成功し、期待値の緩和・必須の skip・continue-on-error を足していない
  - コミット: `test: Windows で落ちたテストの組み立てを直す (T04)`

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
