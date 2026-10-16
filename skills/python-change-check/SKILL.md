---
name: python-change-check
description: Python の変更について、そのリポジトリが実際に使っている検査（CI の YAML・pyproject.toml・tox.ini などに書かれたもの）を探して流し、Windows で壊れやすい箇所（文字コード、パス、外部コマンドの終了コード）を差分から拾い、実行したものとしなかったものを報告する。Python のコードを変更した後、PR を出す前に使う。
metadata:
  short-description: Python の変更の検査
---

# python-change-check

推奨の設定: gpt-6-luna・medium。比べ方は AGENTS.md の「モデルと effort の案内」

## 1. 検査を探す

上から順に探し、最初に見つかったものを正とする。

1. CI の YAML（`azure-pipelines.yml` とテンプレート）の `script:` に書かれた検査と、`UsePythonVersion` の `versionSpec`
2. `pyproject.toml`、`setup.cfg`、`tox.ini`、`Makefile` の検査の設定
3. README や CONTRIBUTING に書かれた手順

手元の `python --version` が CI のバージョンと違えば、そう報告する。見つからなければ、`python -m compileall -q <変更したファイル>` と、既存のテストの実行だけにする。

## 2. 流す

- 変更したモジュールのテストを先に、通れば全体を流す。全体が重いなら、流した範囲を報告に書く。
- 足りないパッケージを入れない。入れるかはユーザーに聞く（パッケージを足すのは環境の変更なので）。
- テストが外部のサービスや本番のデータに触れるなら、流す前に聞く。

## 3. 差分から拾う（直さず、指摘として出す）

| 見るもの | 理由 |
|---|---|
| `open()` / `read_text()` / `write_text()` に `encoding` が無い | Windows の既定は cp932 で、UTF-8 のファイルが読めないか化ける |
| `subprocess` の `text=True` に `encoding` が無い | 上と同じ |
| `subprocess` の戻り値を見ていない、`check=True` が無い | 失敗しても先へ進む |
| パスを `/` や `\\` の文字列でつないでいる | `pathlib` か `os.path.join` で組み立てる |
| `shell=True` に外から来た値を渡している | コマンドインジェクション |

## 4. 報告する

実行した検査とその結果、実行しなかった検査とその理由、差分から拾った指摘、を分けて書く。実行していないものを「通った」と書かない。
