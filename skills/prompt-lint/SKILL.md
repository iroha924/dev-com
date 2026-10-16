---
name: prompt-lint
description: プロンプト類（.md / .txt / .json / .jsonl）を変更したときに、文字コード・BOM・改行の混在・JSON の構文と重複キー・テンプレート変数の書式と定義漏れを機械的に検査する。プロンプトやプロンプト用の JSON を書いた・直した直後、PR を作る前に使う。出力の質の比較には使わない（prompt-regression を使う）。
metadata:
  short-description: プロンプト類の機械的な検査
---

# prompt-lint

推奨の設定: gpt-6-luna・low。比べ方は AGENTS.md の「モデルと effort の案内」

## 流す

```powershell
python "$HOME\.agents\skills\prompt-lint\scripts\prompt_lint.py" <ファイルかディレクトリ> [--var-style brace|dollar|double-brace] [--vars-file names.json] [--json]
```

- `python` が無ければ `py -3` で流す。
- 対象は、ユーザーが指定したもの、無ければ `git diff --name-only <基準ブランチ>...HEAD` と未コミットの変更のうち、拡張子が当たるもの。
- 終了コード 1 は error あり。warning だけなら 0。

## テンプレート変数の書式を決める

`--var-style` は推測で渡さない。そのプロンプトを読み込むコードを探して決める。

| コードの読み方 | `--var-style` |
|---|---|
| `str.format(...)` / `format_map` | `brace` |
| `string.Template(...).substitute` | `dollar` |
| Jinja 風の `{{ name }}` を置換している | `double-brace` |

見つからなければ、変数の検査は外して流し、書式が分からなかったことを報告する。
定義済みの変数名がコードから一覧にできるなら `--vars-file`（JSON 配列か 1 行 1 名）で渡すと、未定義と未使用まで出る。

## 結果の扱い

- `encoding` の error（UTF-8 で読めない）は、Shift_JIS などで保存されている可能性がある。勝手に変換しない。変換するかをユーザーに聞く。
- `bom` と `mixed-newline` は warning。リポジトリの既存ファイルの規約（周りのファイル、`.gitattributes`）に合わせて直すかを判断し、まとめて変換はしない。
- `template` の error は、`brace` なら波括弧を `{{ }}` でエスケープし忘れていることが多い。JSON の例をプロンプトに埋め込んだ箇所を見る。
- 意味の矛盾（指示どうしの衝突、禁止事項の抜け）はこのスクリプトでは見えない。気付いたら別に指摘する。
