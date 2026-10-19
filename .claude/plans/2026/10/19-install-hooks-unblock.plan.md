---
kind: plan
status: approved
codex_session: 01a151ef-23bd-7461-8c70-b7f752d7a133
codex_rounds: 3
approved_at: 2026-10-19
---

# install.ps1 で、既存の hooks.json のフックの定義の違いを見分けて案内し、Unblock-File を今回入れた skill の配布物のファイルに限る

## 要点

- #6: 既存の hooks.json を JSON として読み、目印（statusMessage）のハンドラーを拾って、1 件で形が正しいときだけ command を今回のコマンドと完全一致で比べる。違う・重複・形が違う・読めない、はどれも書き換えずに案内して、使える状態と表示しない
- #7: Install-One が結果を返し、Unblock-File は installed・unchanged・replaced の skill の、配布物の一覧（Get-SourceFiles）にあるファイルだけにかける。中身の違う skill と、利用者が足したファイルには触らない
- 試験: #6 は Ubuntu の PowerShell 7 と Windows PowerShell 5.1 の両方で、#7 は Windows で、一時フォルダの HOME を確かめてから install.ps1 を流す。Zone.Identifier は対照のコピーで残ることを確かめてから見る
- 進め方: 先に試験だけを入れた PR の CI で、直す前のコードが意図した理由で落ちることを記録してから直す
- 変えないもの: 既存の hooks.json を書き換えない方針（-Force でも）、-Force の退避、-WhatIf、agents・AGENTS.md・フックのスクリプトのコピー、ほかのフックとほかの skill

## 持ち主の決定

- issue #6 と #7 を 1 つの計画にまとめ、この Mac から GitHub Actions の CI（windows-2025 / ubuntu-24.04）で確かめて進める
- 既存の hooks.json は、-Force の有無によらず書き換えない（issue #6 の採る案）
- Unblock-File は、今回コピーした対象と、配布元と中身が一致すると確かめた対象だけにかける（issue #7 の採る案）。追加の依存は入れない

## 目的

- hooks.json に古い Python のパスの目印つきハンドラーがある PC で install.ps1 を流し直すと、使えると表示されず、今の command と新しい command と、/hooks で信頼し直す手順が表示される
- 中身の違う同名の skill を -Force なしで残したとき、その skill のファイルの Zone.Identifier が消えない。今回入れた・一致した・置き換えた skill の配布物のファイルからは消える

## 対象外

- hooks.json の自動の書き換え（案内だけ）
- /hooks の信頼や有効・無効の状態を読むこと（保存の形は未確認）
- 会社 PC の cp932 環境

## 前提

- install.ps1:150-160（main a0e3be1）は、hooks.json の全文に `hirata-codex usage status` があれば command を比べずに unchanged と hookReady=true
- install.ps1:83-90 は、中身が違えば -Force なしでは skipped。install.ps1:171-181 は、Windows では結果によらず配布元の全 skill 名のフォルダを再帰で列挙して Unblock-File をかける。Get-SourceFiles（install.ps1:42）は `__pycache__` と `.pyc` を除き、Get-TreeHash と Copy-Tree はこの一覧を使う
- install.ps1:31 は StrictMode 2.0 と `$ErrorActionPreference = 'Stop'`。存在しない欄の参照はエラーになる（Microsoft の Set-StrictMode の説明）
- Codex の hooks.json は、イベント → グループ → ハンドラーの形。ハンドラーの `commandWindows` は Windows で `command` を上書きする。信頼は定義の hash に結び付き、定義が変わると /hooks で信頼し直すまで動かない（https://learn.chatgpt.com/docs/hooks 、2026-10-19 に Codex が確認）。ハンドラーごとの無効化の保存の形は未確認
- Windows の `$HOME` は、PowerShell 5.1 も 7 も USERPROFILE から決まる（about_Automatic_Variables、5.1 と 7.6）。ランナーで子に渡した USERPROFILE で変わるかは未実測。tests/test_hook_command.py:108 の「Windows の $HOME は環境変数で変えられない」に実測の根拠は無い
- Windows PowerShell 5.1 の Get-Content は、BOM の無いファイルを既定で ANSI として読む（about_Character_Encoding）。install.ps1 は hooks.json を BOM なしの UTF-8 で書く
- 5.1 の ConvertFrom-Json はコメントを受け付けず、`-AsHashtable` と `-NoEnumerate` が無い（ConvertFrom-Json の 5.1 と 7.6 の説明）
- PowerShell の文字列の比較は、既定で大文字小文字を区別しない（about_Comparison_Operators）
- `Set-Content -Stream` で代替データストリームを作れる。Unblock-File は Zone.Identifier を消す。Copy-Item がストリームを保つかは未実測
- tests.yml の CI は pull_request・main への push・workflow_dispatch で起動する。この Mac に PowerShell は無い

## 方針

- #6 の判定（install.ps1）
  - `Get-Content -LiteralPath $hooksJson -Raw -Encoding UTF8` で読む。空・null・JSON として読めない（5.1 でのコメントを含む）は「読めない」と案内して skipped
  - 欄は PSObject.Properties で有無と型を確かめてから読む。`hooks.UserPromptSubmit[].hooks[]` から、statusMessage が目印と一致するハンドラーを先にすべて拾う（@() は件数の正規化だけに使う）
  - 0 件: 今までどおり足すハンドラーを表示して skipped（目印が別のイベントや別の欄にあるだけ、ほかのハンドラーだけ、もここ）
  - 2 件以上: 重複として skipped。1 つにするよう案内する
  - 1 件: type が 'command'、command が文字列、commandWindows の欄が無い、をすべて満たすときだけ、`[string]::Equals($old, $hookCommand, [StringComparison]::Ordinal)` で比べる。満たさなければ「要確認」で skipped
  - 一致: `unchanged  <path>  (the hirata-codex hook definition matches; check in /hooks that it is enabled and trusted)`。hookReady=true（定義が一致しただけで、信頼と有効は読まない）
  - 不一致: skipped。今の command と新しい command を並べ、hooks.json のその command を差し替えてから /hooks で信頼し直すよう案内する
  - skipped はどれも hookReady=false で、ファイルの中身を変えない
- #7 の対象（install.ps1）
  - Install-One は各分岐から結果を 1 つだけ返す: installed / unchanged / replaced / skipped / whatif（ShouldProcess が許さなかった）。Write-Host の表示は今のまま
  - skill のループだけで結果を受け取る。agents・AGENTS.md・フックのスクリプトの呼び出しは `| Out-Null` で捨てる
  - Windows かつ -WhatIf でないとき、結果が installed・unchanged・replaced の skill について、配布元の Get-SourceFiles の一覧を入れた先のパスにしたファイルだけに Unblock-File をかける。フォルダの再帰の列挙はやめる
- 試験の共通の準備（tests/test_hook_command.py）
  - 一時フォルダの HOME を作り、子の PowerShell に HOME（POSIX）・USERPROFILE・HOMEDRIVE・HOMEPATH（Windows）を向け、CODEX_HOME を除いて渡す
  - install.ps1 を流す前に、同じ環境の PowerShell で `$HOME` が一時フォルダであることを assert する。違えば試験を失敗させ、本物の HOME では流さない
- 試験（#6、Ubuntu の PowerShell 7 と Windows PowerShell 5.1 の両方）: 目印つきで古い command、同じ command、大文字小文字だけ違う command、別のイベントの目印、目印 2 件、正しい 1 件 + type 違いの目印つき 1 件、type 違い、command の型違い、commandWindows あり、空ファイル、null、`{}`、hooks なし、UserPromptSubmit なし、hooks 配列なし、statusMessage の無いほかのハンドラー、壊れた JSON。どれも正常終了と表示、hooks.json の中身が変わらないこと（-Force でも）を確かめる
- 試験（#7、Windows）
  - 配布元の写しを一時フォルダに作り、ファイルに `Set-Content -LiteralPath <file> -Stream Zone.Identifier -Encoding ASCII` で `[ZoneTransfer]` と `ZoneId=3` を書き、`Get-Content -Stream` で付いたことを確かめる。同じ Copy-Item で対照のコピーを作り、印が残ることを確かめる
  - installed: 入れた先に本文があり、印だけ消える
  - unchanged: 入れた先に本文が一致し印だけ付いたファイル → 印が消える。利用者が足した `cache.pyc`（印つき）は印が残る
  - skipped: 中身の違う同名の skill → 本文と印が残る
  - replaced（-Force）: 退避先の旧ファイルの印が残り、新しいファイルの印が消える
  - -WhatIf: 本文・印・退避がどれも変わらない
  - 配布元に無い名前の skill: 触らない
- 進め方: 先に試験だけを足した PR を作り、直す前のコードで、#6 の古い command が unchanged と出ることと、#7 の skipped の skill の印が消えることで落ちる CI を記録してから直す
- 文書: install.ps1 の冒頭の説明、docs/notes.md の「hooks.json」「テスト」「確かめていないこと」、tests/test_hook_command.py の skip の理由を合わせる

## 採った案と棄却した案

- 採用: hooks.json を JSON として読み、目印のハンドラーを先に拾ってから形を確かめる。棄却: 全文の文字列の検索（目印が別の場所にあるだけで使えると表示する）。棄却: 型で除いてから目印を探す（正しい 1 件と型違いが混ざると一件一致になる）
- 採用: Install-One の結果と配布物の一覧で、Unblock-File のファイルを決める。棄却: スキル名が一致すれば全部解除（中身の違うものを残す方針と矛盾）。棄却: 対象の skill のフォルダを再帰で解除（比べていない利用者のファイルまで解除する）
- 採用: Windows の試験は、子に渡した USERPROFILE で $HOME を一時フォルダに向け、流す前に確かめる。棄却: install.ps1 に試験のための入れ先の指定を足す（公開のインターフェースが増える。今は要らない見込み）
- 採用: 一致したときの表示は定義の一致までに限る。棄却: 「使える」と表示する（信頼と有効の状態は読んでいない）

## 手順

- S1: 試験だけを足す（HOME の隔離の確かめ、ADS の対照を含む）。PR を作り、直す前のコードで意図した理由で落ちる CI を記録する
- S2: #6 の hooks.json の判定を直す
- S3: #7 の Install-One の結果と Unblock-File の対象を直す
- S4: 文書と skip の理由を直す

## 完了条件

- A1: `gh run view <run> --log` → S1 のコミットの CI で、Windows の `$HOME` が一時フォルダだった assert が通り、#6 の古い command のケースと #7 の skipped の印のケースが、意図した理由で落ちている
- A2: `gh run view <run> --log` → PR の最後のコミットの CI で両ジョブが成功し、`git rev-parse HEAD` が PR の先端と同じ。Ubuntu と Windows で #6 の全ケース、Windows で #7 の全ケースが ok
- A3: `python3.11 -m unittest discover -s tests` → OK（手元。PowerShell が無いので該当の試験は skip）
- A4: `gh run list --branch main --limit 1` → merge の後の main への push の CI で両ジョブが成功

## リスク

- Windows で USERPROFILE を向けても $HOME が変わらない → 試験は assert で落ち、本物の HOME では流さない。S1 で止めて持ち主に戻す（入れ先の指定を足すか、ランナーの HOME で流すかを決めてもらう）
- Copy-Item が代替データストリームを保たない → 対照の確かめで分かる。そのときは installed のケースの見方を持ち主に戻す
- Windows で試験が大量に落ちる → 直してよいのは試験の組み立てだけ。製品の不具合なら止めて持ち主に戻す

## 未解決

なし

## 変更履歴
