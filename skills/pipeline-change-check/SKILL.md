---
name: pipeline-change-check
description: Azure Pipelines の YAML（azure-pipelines.yml とテンプレート）を変更したときに、Azure DevOps Server にテンプレートを展開させ（previewRun。ビルドは作られない）、変更前後の展開結果を比べて、ステージ・ジョブ・ステップ・条件・変数の変化と YAML の誤りを報告する。あわせて、再試行の合計時間とタイムアウトの関係や、PR から動くジョブへのシークレットの渡し方を差分から拾う。パイプラインの YAML を書いた・直したとき、PR を出す前に使う。
metadata:
  short-description: パイプライン YAML の展開結果を比べる
---

# pipeline-change-check

推奨の設定: gpt-6-luna・medium。比べ方は AGENTS.md の「モデルと effort の案内」

ADO の読み取りは `ado` skill の手順に従う。**サーバーへ送るのは `ado.ps1 preview` だけ。**`write` でパイプラインを実行しない。
YAML の解析器は自分で書かない。展開と構文の判定はサーバーに任せる。

## 1. 対象のパイプラインを決める

- 変更したのがルートの YAML なら、`build/definitions?yamlFilename=<リポジトリ内のパス>` で定義を引く。
- 変更したのがテンプレートなら、リポジトリの中で `template:` からそのファイルを参照しているルートの YAML を探し、それぞれの定義を引く。
- パイプラインの ID はビルド定義の ID と同じ値として扱う（未確認。preview が 404 なら `_apis/pipelines` の一覧で名前から探す）。

## 2. 展開させる

本文は同梱のスクリプトで作る（`previewRun` は必ず true になる）。

```powershell
python "$HOME\.agents\skills\pipeline-change-check\scripts\preview_body.py" --ref refs/heads/main --out before.json
python "$HOME\.agents\skills\pipeline-change-check\scripts\preview_body.py" --ref refs/heads/main --yaml azure-pipelines.yml --out after.json
ADO preview '<project>/_apis/pipelines/<id>/runs' -BodyFile before.json -OutFile before-result.json
ADO preview '<project>/_apis/pipelines/<id>/runs' -BodyFile after.json -OutFile after-result.json
```

| 変更した場所 | 変更後の展開のさせ方 |
|---|---|
| ルートの YAML だけ | `--yaml` に変更後のファイル、`--ref` は変更前と同じ |
| テンプレート | `yamlOverride` はルートしか置き換えないので、変更を push したブランチを `--ref` に渡す。push していなければ、push するかをユーザーに聞く。しないなら「テンプレートの変更は未検証」と書く |

パラメーターがあるパイプラインは、`--param name=value` で変更前と同じ値を渡す。

## 3. 比べて返す

- 応答の `finalYaml` どうしを Python の `difflib.unified_diff` で比べる。
- 400 などで展開に失敗したら、応答のメッセージが YAML の誤りの説明なので、そのまま該当箇所と一緒に示す。
- 報告するもの: 増えた・消えたステージ / ジョブ / ステップ、`condition` の変化、変数とテンプレートの引数の変化、意図していなさそうな変化。

**確かめられるのは展開の結果まで。**タスクが実行時に成功するか、実行時の変数で条件がどう評価されるかは分からない。そう書き添える。

## 4. 差分から拾う（直さず、指摘として出す）

展開の結果では分からないが、YAML を読めば分かるもの。

| 見るもの | 理由 |
|---|---|
| ネットワークの再試行（`curl --retry`、`Invoke-WebRequest` のループ、`pip` の `--retries`）の最悪の合計時間が、ジョブの `timeoutInMinutes` の半分を超える | 相手が一度遅れただけでジョブがタイムアウトで止まり、成果物が残らない。curl なら合計は `(N+1) × max-time + 待ち時間の合計`。待ち時間は `--retry-delay D` があれば `N × D`、無ければ 1 秒から倍々（1, 2, 4…、10 分で頭打ち）の合計。`--retry-max-time` は「次の再試行を始めるか」の期限で、始めた転送は `--max-time` まで続くので、上限は `--retry-max-time` + `--max-time` |
| 失敗したら止める処理（`exit 1`、`failOnStderr`、`set -e`、`$ErrorActionPreference = 'Stop'`）を足したが、その経路を通すテストが無い | 書いただけでは、本当に止まるかは分からない。初めて止まるのが本番になる |
| `powershell:` / `pwsh:` のステップで、`git` や `python` などのネイティブのコマンドの失敗を `$ErrorActionPreference = 'Stop'` で止めるつもりになっている | ネイティブのコマンドが 0 以外で終わっても、`$ErrorActionPreference` では止まらない。PowerShell 7.4 以降で `$PSNativeCommandUseErrorActionPreference = $true` にしたときだけ止まる（既定は `$false`）。それ以外は `if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` を書く |
| CI で足した・変えた検査（lint、シークレットの検出）と、ローカルの手順やフックが別の道具・別の設定 | ローカルでは通るのに CI で落ちる、が繰り返される。揃えられないなら、その理由を YAML のコメントに書く |
| PR から起動するジョブに、書き込みの権限やシークレットの変数グループを渡している | PR の内容（スクリプト、テスト）がそのシークレットで動く |
