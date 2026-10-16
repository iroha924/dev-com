---
name: root-cause
description: git bisect で、不具合や CI の失敗を持ち込んだコミットを特定する。共有の作業ツリーで走らせると並行の作業が壊れるので、使い捨ての worktree で回す手順と、good / bad を誤判定しないための準備を持つ。「いつから壊れたか分からない」「どのコミットで落ちるようになったか」を調べるとき、bisect を始める前に読む。再現やログの追加だけで原因が分かる調査には使わない。
metadata:
  short-description: git bisect で原因のコミットを特定する
---

# root-cause

## bisect は共有の作業ツリーで走らせない

HEAD が過去のコミットへ動くので、**並行に走っている他の作業（エディタ、別の Codex）が別のバージョンのコードを読む。**
一時ディレクトリに worktree を作り、その中で回す。PowerShell では次のとおり。

```powershell
$wt = Join-Path $env:TEMP ("bisect-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
git worktree add --detach $wt HEAD
git -C $wt bisect start <bad のコミット> <good のコミット>
git -C $wt bisect run <判定のコマンド>
git -C $wt bisect reset
git worktree remove --force $wt
```

- 使い捨ての worktree なので、ログや持ち込んだ差分で汚れていても `--force` で消してよい。**消す前に、報告に要る観測を取り終えておく。**
- `bisect run` の判定のコマンドは、成功で 0、失敗で 1〜127（125 を除く）を返すものにする。125 は「このコミットは判定できない」で飛ばされる。
- Windows ではパスの長さで checkout が失敗することがある。`$env:TEMP` が深い場所なら、`C:\t` のような短い場所を使う。

## good / bad を誤判定しない

- worktree には**追跡されていないファイルが無い**（仮想環境、`.env`、ローカルの設定）。判定のコマンドの前に用意する。
- 未コミットの変更で起きる失敗なら、差分をファイルに書いてから持ち込む。
  **PowerShell のパイプ（`git diff | git apply`）と `>` で渡さない。**PowerShell 5.1 はネイティブのコマンドどうしのパイプを文字列として扱い、`>` は UTF-16 で書くので、差分が壊れる。

  ```powershell
  git diff HEAD --binary --output="$wt.patch"
  git -C $wt apply "$wt.patch"
  ```

  追跡されていない新しいファイルは、手で写す。
- **最初の good と bad の 2 点で、症状そのもので通る・落ちることを確かめてから進める。**環境の理由で落ちるコミットを bad と判定すると、無関係なコミットを原因として返すことになる。

## 報告する

原因のコミット（SHA、件名、作者、日付）、判定に使ったコマンド、最初の good と bad で確かめた結果、飛ばしたコミット（125）があればその SHA を書く。
