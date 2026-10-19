---
kind: plan
status: approved
codex_session: 01a151b6-8927-7d00-9d76-70ffffafdc62
codex_rounds: 2
approved_at: 2026-10-19
---

# GitHub Actions で、Windows（Python 3.11・Windows PowerShell 5.1）と Ubuntu（PowerShell 7）の既存テストを PR と main への push のたびに流す

## 要点

- `.github/workflows/tests.yml` を足し、windows-2025 と ubuntu-24.04 の 2 つのジョブで `python -m unittest discover -s tests -v` を流す
- PowerShell が要るテストは、CI では「無ければ飛ばす」ではなく「無ければ落ちる」にする（環境変数で切り替え。手元の既定は今のまま飛ばす）
- Windows のジョブでは、テストが使う PowerShell（1 つの絶対パスに揃える）が 5.1 であることを、PowerShell が要るテストより先に確かめる。不在・起動失敗・版の取得失敗・版違いはどれも失敗
- PR では PR の先端のコミットを checkout し、CI が確かめたコミットが PR の先端と同じことをログで照らす
- 外部の action は 40 桁の SHA で固定し、`permissions: contents: read`、checkout は資格情報を残さない。secret は使わない
- docs/notes.md の「テスト」と「確かめていないこと」を、CI で確かめた事実に合わせて直す
- 変えないもの: テストが確かめる挙動（Windows で落ちたら、組み立てだけを直す。製品の不具合なら止めて戻す）、install.ps1、skill の挙動

## 持ち主の決定

- issue #10 を、この Mac から GitHub Actions の Windows ランナーで進める（会社の PC は使わない）
- リポジトリは public の iroha924/dev-com。会社の ADO・PAT・実際の会話は使わず、既存の偽サーバーと一時データで流す
- 後続の issue #6（install.ps1 の hooks.json の command 比較）と #7（Unblock-File の対象を絞り、Zone.Identifier の保持を Windows で確かめる）は、この CI の上で確かめる。この計画には入れない

## 目的

- PR と main への push のたびに、Windows PowerShell 5.1 と Python 3.11 で AdoPs1Test と、Windows で適用できる HookCommandTest が「飛ばされずに」通ったことを CI のログで確かめられる
- POSIX の PowerShell 7 で InstallHooksJsonTest が飛ばされずに通る

## 対象外

- 会社 PC の cp932 環境の再現（ランナーは英語の既定のロケール。システムロケールの変更は再起動が要る）
- SudachiPy を入れて意味の検索のテストを流すこと（任意の依存。CI では入れずに 3 件の skip を残す）
- #6・#7 の修正、install.ps1 を Windows のランナーで実際に流す試験
- Dependabot などの action の自動更新

## 前提

- テストは標準ライブラリだけ（pip install は要らない）。手元 macOS・Python 3.11.15 で 441 件 OK、skip 23（PowerShell が無い 18、POSIX だけの install 試験 2、SudachiPy が無い 3）
- PowerShell の見つけ方は `shutil.which("powershell") or shutil.which("pwsh")`（tests/test_ado_ps1.py:17、tests/test_hook_command.py:19）。Windows では powershell（5.1）が先に当たる
- tests/test_hook_command.py:80 は Windows で常に飛ばす（曲がった引用符や空白を含むフォルダ）。:108 の InstallHooksJsonTest は POSIX だけ（Windows の $HOME は環境変数で変えられない）。そのため docs/notes.md:802-803 の「Windows で skipped が 0 件なら 5.1 で確かめた」は成り立たない
- docs/notes.md:880 に「Windows PowerShell 5.1 では一度も流していない」
- runner-images（https://github.com/actions/runner-images 、2026-10-19 取得）: windows-latest = Windows Server 2025、ubuntu-latest = Ubuntu 24.04。Windows の一覧に Windows PowerShell 5.1 の行は無く PowerShell 7.6.6 がある（5.1 は OS に標準で入るはずだが未検証。CI のログで確かめる）。Python は Windows 3.11.9、Ubuntu 3.11.17 がツールキャッシュにある。Ubuntu に PowerShell 7.6.6
- actions/checkout v7.0.1 = 3d3c42e5aac5ba805825da76410c181273ba90b1、actions/setup-python v7.0.0 = 5fda3b95a4ea91299a34e894583c3862153e4b97（どちらも 2026-07-20 公開、GitHub API で確認）
- リポジトリに .github は無い。Actions は有効（allowed_actions: all、sha_pinning_required: false）

## 方針

- workflow: `.github/workflows/tests.yml`。on: `pull_request`、`push`（branches: main）、`workflow_dispatch`。`pull_request_target` は使わない。トップレベルに `permissions: contents: read`。`concurrency` で同じ ref の古い実行を止める
- jobs: `windows`（runs-on: windows-2025）と `ubuntu`（runs-on: ubuntu-24.04）。label を `-latest` にしないのは、OS の切り替えで黙って環境が変わらないため
- steps: checkout（`persist-credentials: false`）→ setup-python（`python-version: "3.11"`）→ 版の表示（`python --version`、Windows は `powershell -NoProfile -Command '$PSVersionTable.PSVersion'`、Ubuntu は `pwsh -NoProfile -Command ...`）→ `python -m unittest discover -s tests -v`
- 環境変数 `DEV_COM_REQUIRE_POWERSHELL=1` のとき、PowerShell が要るテストクラスは PowerShell が無ければ skip ではなく失敗する。`DEV_COM_EXPECT_POWERSHELL=5.1` のとき PowerShell は必須になり、テストが選んだ PowerShell の `$PSVersionTable.PSVersion` の Major.Minor が 5.1 でなければ失敗する（Windows のジョブだけで付ける）
  - 実装: `tests/powershell.py` に置く。探す（`powershell` → `pwsh`、絶対パスにする）→ 必須なら不在で失敗 → 期待の版があれば同じ絶対パスで版を取り、起動失敗・取得失敗・不一致で失敗。結果はモジュールで 1 度だけ決め、test_ado_ps1.py と test_hook_command.py は同じ絶対パスを使う。パスと完全な版を stderr に出す
  - 失敗はクラスの setUpClass ではなく import の時点（discover の読み込み）で出し、skip より先に必ず止まるようにする
  - 単体テスト `tests/test_powershell.py`: 見つけた結果と版の取得を差し替え、任意で不在→skip、必須で不在→失敗、期待 5.1 に 7.6・5.0・取得失敗→失敗、5.1→通る
- Ubuntu のジョブに負の確かめの手順を置く: `DEV_COM_EXPECT_POWERSHELL=5.1 python -m unittest tests.test_ado_ps1` が実物の pwsh 7 で 0 以外で終わり、出力に版違いの文言があること（あれば手順は成功）
- checkout: PR では `ref: ${{ github.event.pull_request.head.sha }}`、push と手動実行では既定（`github.sha`）。`git rev-parse HEAD` をログに出す
- 機械の検査: tests/test_layout.py に足す。対象は `.github/workflows/` の `*.yml` と `*.yaml`。YAML のライブラリは足さず（README の「標準ライブラリだけで動く」）、許す書き方を狭く決めて行で見る。許さない形は失敗にする
  - `uses:` は `owner/repo@<40 桁の 16 進>  # <版>` の形だけ
  - `permissions:` はトップレベルの 1 か所だけで、中身は `contents: read` だけ（ジョブでの上書きは失敗）
  - `pull_request_target`、`secrets.`、`continue-on-error` を含まない
  - actions/checkout を使う手順には、続く `with:` に `persist-credentials: false` がある
- 最初の CI で Windows だけで落ちるテストが出たら: 直してよいのは、テストが確かめる挙動を変えずに、テストの組み立て（パスの区切り、改行、一時データの置き方）だけを Windows に合わせるもの。期待値を緩める・必須のテストを skip にする・`continue-on-error` は禁止。製品のコードの不具合なら、再現と理由を持ち主に報告して止める。必須のテストが通るまで #10 は終わらない
- docs/notes.md: 「テスト」の 802-803 を、CI の必須テストの考え方（skip 数ではなく、版を確かめた PowerShell で必須のクラスが走ったこと）に直す。804 の「install.ps1 はテストの対象外」を直し、POSIX では hooks.json の 2 件を流していること、Windows でのインストーラーの動作と Zone.Identifier は未検証であること、`-WhatIf` はコピーや退避の動作の検査の代わりにならないことを分けて書く。「確かめていないこと」の 880 を CI の結果に合わせる（cp932 は未検証のまま残す）

## 採った案と棄却した案

- 採用: 環境変数で PowerShell の要否を切り替える。棄却: skip の件数を CI で数える（任意の SudachiPy の skip と区別できない）
- 採用: windows-2025 / ubuntu-24.04 の固定ラベル。棄却: `-latest`（OS が黙って変わる）
- 採用: 標準ライブラリだけで流す。棄却: SudachiPy を CI で入れる（依存と時間が増え、#10 の目的に要らない）
- 採用: workflow の検査は許す書き方を狭く決めて行で見る。棄却: YAML のライブラリで構造を読む（依存が増え、README の標準ライブラリだけの前提を崩す）
- 採用: PR では PR の先端のコミットを checkout する。棄却: 既定の merge commit を検査する（CI が確かめたコミットと変えたコミットが一致しない。merge 後の整合は main への push の CI で見る）

## 手順

- S1: 直す前に、DEV_COM_REQUIRE_POWERSHELL=1 を付けても PowerShell の無い手元で対象 20 件が skip で通ってしまうことを確かめる（red）
- S2: tests/powershell.py と tests/test_powershell.py を足し、test_ado_ps1.py と test_hook_command.py をそれに寄せる。DEV_COM_REQUIRE_POWERSHELL と DEV_COM_EXPECT_POWERSHELL を実装する
- S3: tests/test_layout.py に workflow の検査を足す
- S4: .github/workflows/tests.yml を足す
- S5: 最初の CI で出た Windows だけの失敗を直す（テスト側のみ）
- S6: docs/notes.md を直す

## 完了条件

- A1: `python3.11 -m unittest discover -s tests` → OK、skip の件数は今と同じ 23（手元 macOS、環境変数なし）
- A2: `DEV_COM_REQUIRE_POWERSHELL=1 python3.11 -m unittest tests.test_ado_ps1 tests.test_hook_command` → 0 以外で終わり、skip にならない（手元 macOS、PowerShell なし）
- A3: `python3.11 -m unittest tests.test_powershell` → OK（不在・版違い・取得失敗・5.1 の各場合）
- A4: `gh pr checks <PR> --watch` と `gh run view <run> --log` → docs の直しを含む PR の最後のコミットで windows と ubuntu の両ジョブが成功し、両ジョブのログの `git rev-parse HEAD` が `gh pr view <PR> --json headRefOid` と同じ。Windows のログに PowerShell の絶対パスと 5.1.x、Python 3.11.x。Ubuntu のログに PowerShell 7.x、Python 3.11.x、負の確かめの手順が版違いで落ちたこと
- A5: `gh run view <run> --log` → Windows のジョブで AdoPs1Test と HookCommandTest（test_hook_command.py:80 の 1 件を除く）が ok、Ubuntu のジョブで InstallHooksJsonTest が ok
- A6: `python3.11 -m unittest tests.test_layout` → OK。workflow を 1 か所ずつ崩す（`uses:` を `@v7` に、ジョブに `permissions: write-all` を足す、`persist-credentials: false` を消す、`secrets.X` を足す、`pull_request_target` を足す）と、それぞれ落ちる
- A7: `gh run list --branch main --limit 1` → merge の後の main への push の CI で両ジョブが成功

## リスク

- Windows PowerShell 5.1 がランナーに無い → A4 で分かる。無ければ windows-2022 を試し、それでも無ければ持ち主に戻す
- Windows で大量に落ちる → S5 の範囲（テスト側）を超えるなら止めて持ち主に戻す

## 未解決

なし

## 変更履歴
