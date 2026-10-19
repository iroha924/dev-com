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
- [x] T02: #7 の Zone.Identifier の試験を Windows に足す
  - 種別: 追加
  - 計画: S1
  - 依存: T01（一時フォルダの HOME で install.ps1 を流す準備が要る）
  - 変更: `tests/test_hook_command.py`
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK、skip の件数は今と同じ（手元）。CI で意図した理由で落ちることは plan の A1 で見る
  - コミット: `test: Unblock-File が skip した skill と配布物でないファイルに触れないことを Windows で確かめる試験を足す (T02)`
  - 結果: `python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28（足した UnblockTest の 3 件が Windows だけ）。印は plan の Set-Content -Stream ではなく、Python で `<file>:Zone.Identifier` に書く（同じ代替データストリーム）。Copy-Item で印が写る対照は setUp で確かめる。直す前のコードで落ちることは CI で確かめる（plan の A1）

- [x] T06: install.ps1 を流す試験の子の PowerShell の環境から PSModulePath を除く
  - 種別: 修正
  - 計画: S1
  - 依存: T01（直す試験の準備が要る）
  - 変更: `tests/test_hook_command.py`
  - red: `gh run view 39190415176 --log` → Windows のジョブで、試験の環境でも元の環境でも子の Windows PowerShell 5.1 に Get-FileHash が無く、PSModulePath の先頭に `c:\program files\powershell\7\Modules` などの PowerShell 7 のパスが並ぶ
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。Windows のジョブで Get-FileHash が見つかり、直す前のコードで #6・#7 が意図した理由でだけ落ちることは plan の A1 で見る
  - コミット: `test: install.ps1 を流す試験の子の PowerShell から、PowerShell 7 のモジュールのパスを外す (T06)`
  - 結果: red は上のとおり run 39190415176 で実測。原因は about_PSModulePath の「Starting Windows PowerShell from PowerShell 7」にある、途中のプロセス（ここでは Python）を挟むと PowerShell 7 のパスが残り、Microsoft.PowerShell.Utility のコマンドが CommandNotFoundException になる件。PowerShell 7 が powershell.exe を直接起動するとき（持ち主がターミナルから流すとき）は取り除かれるので、install.ps1 の不具合ではない。試験の環境から PSModulePath を除いた。`python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28

## P2: install.ps1 を直す

両方のジョブで、#6 と #7 の全ケースが通る。

- [x] T03: hooks.json を JSON として読み、目印のハンドラーの定義を比べて案内する
  - 種別: 修正
  - 計画: S2
  - 依存: T01（直す前に落ちる試験が要る）
  - 変更: `install.ps1`
  - red: `gh run view <run> --log-failed` → T01 の CI で、#6 の古い command のケースなどが unchanged と出て落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`git diff HEAD~1 --stat` → 変えたのは install.ps1 と tasks だけ。両ジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): hooks.json の目印のハンドラーの定義を比べ、違えば書き換えずに案内する (T03)`
  - 結果: red は run 39190779796 で、両ジョブの #6 の目印の 9 ケースが unchanged と出て落ちたことを実測。`python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28（手元）。install.ps1 は ASCII だけ。直したコードの両ジョブでの結果は plan の A2 で見る
- [x] T04: Install-One の結果と配布物の一覧で Unblock-File の対象を決める
  - 種別: 修正
  - 計画: S3
  - 依存: T02（直す前に落ちる試験が要る）
  - 変更: `install.ps1`
  - red: `gh run view <run> --log-failed` → T02 の CI で、skipped の skill と cache.pyc の印が消えて落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`git diff HEAD~1 --stat` → 変えたのは install.ps1 と tasks だけ。Windows のジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): Unblock-File を今回入れた・一致した・置き換えた skill の配布物のファイルに限る (T04)`
  - 結果: red は run 39190779796 の test_only_installed_or_matching_package_files_are_unblocked で、利用者の cache.pyc の印が消えて落ちたことを実測。Install-One が installed / unchanged / replaced / skipped / whatif を返し、skill のループだけで受け取る（ほかは `| Out-Null`）。Unblock-File は、結果が installed・unchanged・replaced の skill の Get-SourceFiles の各ファイルにだけ -LiteralPath でかける。`python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28（手元）。Windows での結果は plan の A2 で見る

- [x] T07: Windows PowerShell 5.1 の -WhatIf で、中身を比べる Get-FileHash に -WhatIf が効かないようにする
  - 種別: 修正
  - 計画: S5
  - 依存: T02（直す前に落ちる -WhatIf の試験が要る）
  - 変更: `install.ps1`
  - red: `gh run view 39190779796 --log` → Windows の test_whatif_changes_nothing で、install.ps1 -Force -WhatIf が `The property 'Hash' cannot be found on this object` で終了コード 1
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`git diff HEAD~1 --stat` → 変えたのは install.ps1 と tasks だけ。Windows のジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): -WhatIf でも中身の比べ方が Get-FileHash の値を受け取れるようにする (T07)`
  - 結果: red は run 39190779796 の test_whatif_changes_nothing で実測。Get-TreeHash の中だけ `$WhatIfPreference = $false` にした（関数の中の変数なので、呼び出し元の -WhatIf は変わらない）。`python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28（手元）。Windows での結果は plan の A2 で見る

- [x] T08: hooks.json の欄の値を読むとき、配列を展開せず 1 つの値のまま返す
  - 種別: 修正
  - 計画: S2
  - 依存: T03（直す読み方が要る）
  - 変更: `install.ps1`
  - red: `gh run view 39192103933 --log` → 両ジョブで、command と type が 1 要素の配列のハンドラーが skipped ではなく unchanged と出て落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。両ジョブでの成功は plan の A2 で見る
  - コミット: `fix(install): hooks.json の配列の値を展開せず、-Confirm で中の操作を聞き直さず、中身の比べ方を .NET の SHA256 にする (T08, T09, T10)`
  - 結果: red は上のとおり run 39192103933 で実測（`'skipped    …/hooks.json' not found`、出力は unchanged）。Get-JsonProperty を `return , $property.Value` にした。`python3.11 -m unittest discover -s tests` → 454 件 OK、skipped=28（手元。足したのは subTest なので件数は変わらない）
- [x] T09: 外側で承認した後の、コピーと退避の中の操作で -Confirm を聞き直さない
  - 種別: 修正
  - 計画: S3
  - 依存: T04（直す結果の返し方が要る）
  - 変更: `install.ps1`
  - red: `powershell -File install.ps1 -Force -Confirm` → 対話のホストで外側の置き換えを承認し、中のコピーを断ると、replaced と表示され、コピーしていないファイルが Unblock-File の対象に入る。機械では再現できない（断る操作は対話のホストでしかできず、CI の試験では流せない）。根拠は Codex のレビュー（316b761）の F1 と、Microsoft の Everything about ShouldProcess の「Suppressing nested confirm prompts」
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`grep -cF -- '-Confirm:$false' install.ps1` → 6（Copy-Tree の New-Item と Copy-Item の 2 か所ずつ、退避の New-Item と Move-Item）
  - コミット: `fix(install): hooks.json の配列の値を展開せず、-Confirm で中の操作を聞き直さず、中身の比べ方を .NET の SHA256 にする (T08, T09, T10)`
  - 結果: 直す前の失敗は上のとおり確かめていない。`grep -cF -- '-Confirm:$false' install.ps1` → 6
- [x] T10: 中身の比べ方を Get-FileHash ではなく .NET の SHA256 で計算し、-WhatIf でも止まらないようにする
  - 種別: 修正
  - 計画: S5
  - 依存: T07（T07 の直しが効かなかった）
  - 変更: `install.ps1`
  - red: `gh run view 39191767307 --log` → T07 を入れた後も、Windows の test_whatif_changes_nothing が `The property 'Hash' cannot be found on this object` で落ちる
  - 完了条件: `python3.11 -m unittest discover -s tests` → OK（手元）。`grep -n "Get-FileHash" install.ps1` → 説明のコメントの 1 行だけ。`grep -c "WhatIfPreference = " install.ps1` → 0。Windows での成功は plan の A2 で見る
  - コミット: `fix(install): hooks.json の配列の値を展開せず、-Confirm で中の操作を聞き直さず、中身の比べ方を .NET の SHA256 にする (T08, T09, T10)`
  - 結果: red は上のとおり run 39191767307 で実測。Get-FileSha256 を足し、T07 の `$WhatIfPreference = $false` は消した。`grep -n "Get-FileHash" install.ps1` → 53 行目のコメントだけ。`grep -c "WhatIfPreference = " install.ps1` → 0

## P3: 文書を合わせる

install.ps1 の説明と docs/notes.md が、直した動きと確かめた範囲を書いている。

- [x] T05: install.ps1 の冒頭の説明と docs/notes.md を直す
  - 種別: 変更
  - 計画: S4
  - 依存: T03（書く動きが要る）, T04（書く動きが要る）
  - 変更: `install.ps1`, `docs/notes.md`
  - 完了条件: `python3.11 -m unittest tests.test_layout` → OK。`grep -n "Windows の \$HOME は環境変数で変えられない" tests/test_hook_command.py` → 何も出ない
  - コミット: `docs: install.ps1 の hooks.json と Unblock-File の動きと、確かめた範囲を書く (T05)`
  - 結果: `python3.11 -m unittest tests.test_layout` → 21 件 OK。`grep -n "Windows の \$HOME は環境変数で変えられない" tests/test_hook_command.py` → 何も出ず終了コード 1（T01 で skip の理由ごと消えている）。install.ps1 の冒頭の説明と docs/notes.md の hooks.json・Unblock-File・テスト・確かめていないこと を直した。install.ps1 は ASCII だけ

## 記録
- 2026-10-19 / T01 / 完了条件の前: skip の件数は今と同じ、後: 今の 23 件に足した PowerShell の要る試験の数を足した件数。理由: PowerShell の要る試験を足すと、PowerShell の無い手元では skip が増えるため / plan は変えない
- 2026-10-19 / T01 / 1012042 の CI（run 39189958694）: Ubuntu は #6 の目印のケースだけが意図どおり落ちた。Windows は `$HOME` の assert は通ったが、2 回目の install.ps1 が `Get-FileHash` を見つけられず止まり、直す前でも通るはずのケースまで落ちた / 試験の準備に、子の PowerShell で Get-FileHash が見つかるかの確かめを足し、見つからなければ試験の環境と元の環境の結果と PSModulePath を出して止めるようにした（T02 のコミットに入れた）。原因を確かめてから進め方を決める
- 2026-10-19 / A1 / 直す前のコード（4fef51a、run 39190779796）: 両ジョブで #6 の目印の 9 ケースが unchanged と出る今の判定で落ちた。Windows で #7 の test_only_installed_or_matching_package_files_are_unblocked が、利用者の cache.pyc の印が消えて落ちた。test_force_unblocks_the_new_copy_and_keeps_the_backup_marked は今のコードでも通った（守りの試験） / red を記録した
- 2026-10-19 / T07 / 同じ run で test_whatif_changes_nothing が install.ps1 自体の -WhatIf の不具合で落ちた。持ち主が同じ PR で直す案を選んだ / plan に S5 を足し、修正タスク T07 を足した
- 2026-10-19 / T03 / Codex のタスクごとのレビュー（b56db1f）が、1 要素の配列の command と type を一致と判定する欠陥を読みで見つけた（F1）。受け入れ / 試験を足して CI で red を確かめ（run 39192103933）、修正タスク T08 で直した
- 2026-10-19 / T07 / Codex のレビュー（54fad6e）は指摘なしだったが、CI（run 39191767307）で -WhatIf の試験がまだ落ちた。関数の中の `$WhatIfPreference = $false` は Get-FileHash に効かなかった / 修正タスク T10 で、Get-FileHash を使わない形に直した
- 2026-10-19 / T04 / Codex のタスクごとのレビュー（316b761）が、-Confirm で中の操作を断っても成功の結果を返す欠陥を読みで見つけた（F1）。受け入れ / 修正タスク T09 で直した。断る操作は CI で流せないので red は確かめていない
