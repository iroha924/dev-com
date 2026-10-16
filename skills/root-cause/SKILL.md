---
name: root-cause
description: git bisect で、不具合や CI の失敗を持ち込んだコミットを特定する。共有の作業ツリーで走らせると並行の作業が壊れるので、使い捨ての worktree で回す手順と、good / bad を誤判定しないための準備を持つ。「いつから壊れたか分からない」「どのコミットで落ちるようになったか」を調べるとき、bisect を始める前に読む。再現やログの追加だけで原因が分かる調査には使わない。
metadata:
  short-description: git bisect で原因のコミットを特定する
---

# root-cause

## bisect は共有の作業ツリーで走らせない

HEAD が過去のコミットへ動くので、**並行に走っている他の作業（エディタ、別の Codex）が別のバージョンのコードを読む。**
一時ディレクトリに worktree を作り、その中で回す。

```powershell
$wt = Join-Path $env:TEMP ("bisect-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
git worktree add --detach $wt HEAD
git -C $wt bisect start <bad のコミット> <good のコミット>
git -C $wt bisect run pwsh -NoProfile -File "$wt.judge.ps1"
git -C $wt bisect log
git -C $wt bisect reset
git worktree remove --force $wt
```

- Windows では、パスが長いと checkout が失敗することがある。`$env:TEMP` が深い場所なら、`C:\t` のような短い場所を使う。
- `pwsh` が無ければ `powershell` に置き換える。
- worktree が消えないとき（判定で起動したサーバー、エディタ、ウイルス対策の検査がファイルをつかんでいる）は、そのプロセスを閉じてから `git worktree remove --force` をやり直す。それでも残れば、フォルダを消してから `git worktree prune` を流す。
- 使い捨てなので、ログや持ち込んだ差分で汚れていても `--force` で消してよい。**消す前に、報告に要る観測（`bisect log`、判定の出力）を取り終えておく。**

## 判定は .ps1 に書く

`git bisect run` は判定のコマンドを PowerShell ではなく sh（Git for Windows に入っているもの）で起動する。
PowerShell の式をそのまま渡すと動かないか、外側のシェルで変数が先に展開される。判定は worktree の外の .ps1 に書き、`-File` で起動する。

- 終了コードの意味: 0 は good、1〜127（125 を除く）は bad、**125 は「このコミットは判定できない」で飛ばされる**、128 以上は bisect そのものが止まる。
- スクリプトの最後で `exit` を明示する。ネイティブのコマンドの終了コードは `$LASTEXITCODE` に入る。
- 中身は ASCII だけで書けば、PowerShell 5.1 でも 7 でも文字コードの問題が出ない。

```powershell
# <worktree>.judge.ps1 の例: テストが通れば good、落ちれば bad
python -m pytest tests/test_retry.py -q
if ($LASTEXITCODE -eq 0) { exit 0 } else { exit 1 }
```

## good / bad を誤判定しない

- worktree には**追跡されていないファイルが無い**（仮想環境、`.env`、ローカルの設定）。判定の前に用意する。
- **最初の good と bad の 2 点で、症状そのもので通る・落ちることを確かめてから進める。**環境の理由で落ちるコミットを bad と判定すると、無関係なコミットを原因として返すことになる。
  `git -C $wt checkout <コミット>` のあと、判定の .ps1 を手で流して終了コードを見る。

## 未コミットの変更で起きる失敗を探すとき

差分を**各コミットで当て、判定の後に戻す。**一度だけ当てたままにすると、次のコミットへの checkout が「ローカルの変更が上書きされる」で止まる（[git-bisect の公式の説明](https://git-scm.com/docs/git-bisect#_bisect_run)も、試すたびに当てて戻す形を示している）。

1. 差分をファイルに書く。`git diff | git apply` のようにパイプで渡したり `>` で書いたりしない。PowerShell 5.1 と 7.3 以前は、ネイティブのコマンドどうしのパイプを文字列として扱い、5.1 の `>` は UTF-16 で書くので、差分が壊れる。`--output` ならどのバージョンでも壊れない。
   ```powershell
   git diff HEAD --binary --output="$wt.patch"
   ```
   追跡されていない新しいファイルは差分に入らない。判定の .ps1 の中で、当てる前に worktree へ写し、戻すときに消す。
2. 判定の .ps1 で、当てる → 判定する → 戻す、の順にする。当てられないコミットは 125 で飛ばす。
   ```powershell
   # <worktree>.judge.ps1
   $patch = $PSCommandPath -replace '\.judge\.ps1$', '.patch'
   git apply $patch
   if ($LASTEXITCODE -ne 0) { exit 125 }
   python -m pytest tests/test_retry.py -q
   $code = $LASTEXITCODE
   git apply -R $patch
   if ($code -eq 0) { exit 0 } else { exit 1 }
   ```
   戻すのに `git clean` を使わない。用意した仮想環境や `.env` まで消える。

## 報告する

原因のコミット（SHA、件名、作者、日付）、判定に使った .ps1 の中身、最初の good と bad で確かめた結果、飛ばしたコミット（125）があればその SHA を書く。
