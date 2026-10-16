---
name: ado
description: オンプレの Azure DevOps Server 2020（REST api-version 6.0）の Work Item・Sprint・PR・ビルド・パイプラインを読み書きする手順と、そのための ado.ps1。ADO のデータを読む・書くとき、ADO への接続や認証がうまくいかないときに使う。他の skill（sprint-report、pr-evidence-pack など）が ADO に触れるときもここに従う。
metadata:
  short-description: Azure DevOps Server 2020 の読み書き
---

# ado

## 前提（2026-10-16 に一次情報で確認）

- Azure DevOps Server 2020 の REST は **6.0 まで**。7.x のサンプルを api-version だけ書き換えても動く保証は無い。エンドポイントとバージョンは [references/endpoints.md](references/endpoints.md)。
- Microsoft 公式の Azure DevOps MCP（`@azure-devops/mcp`）は Services 専用で、接続先が `dev.azure.com` に固定されている。オンプレには繋がらない。
- Work Item のコメントは Markdown にならない（`format` 指定は 7.1 から）。**HTML で書く。**PR のコメントは Markdown で書いてよい。

## 経路を決める（セッションで最初に ADO に触れるとき）

1. ADO の MCP ツールがあれば、読み取りを 1 つ試す。成功すれば読み取りはそれを使ってよい。書き込みは MCP が使えても下の「書く」の手順で `ado.ps1` から送る（承認の手順を 1 本にするため）。
2. 失敗する、`dev.azure.com` を見ている、ツールが無い、のどれかなら `ado.ps1` を使う。
3. どちらを使ったかを、最初の報告に 1 行書く。

## 準備（ユーザーの環境変数。未設定ならユーザーに頼む）

| 変数 | 値 |
|---|---|
| `ADO_COLLECTION_URL` | 例 `https://tfs.example.local/tfs/DefaultCollection` |
| `ADO_AUTH` | `default`（Windows 統合認証。既定）か `pat` |
| `ADO_PAT` | `ADO_AUTH=pat` のときだけ。**値をコマンド・ファイル・出力に書かない** |

接続の確認は `ADO get '_apis/projects'`（`ADO` は下の「読む」）。401 か 302（`location` がサインイン画面）は認証、接続エラーはプロキシか証明書を疑う。

Project と Repo は、リポジトリの中なら次で割り出す（Project と Repo が同名で URL から Project が省かれている場合も扱う）。

```powershell
python "$HOME\.agents\skills\ado\scripts\ado_context.py"
```

`project_path` / `repo_path` は URL エンコード済みなので、そのままパスに入れる。

## 読む

以下の `ADO` は `powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\ado\scripts\ado.ps1"` の略。コマンドごとに新しいシェルで走るので、変数に入れて使い回さず毎回このまま書く。

```powershell
ADO get '<project>/_apis/wit/workitems/123?$expand=relations'
ADO query '<project>/_apis/wit/wiql' -BodyFile wiql.json
```

- 応答の本文はファイルに保存され、標準出力には `{"status":200,"ok":true,"out":"<path>",...}` の 1 行だけが出る。中身は `out` のファイルを Python で読む（`encoding="utf-8"`）。
- **パスはシングルクォートで渡す。**`$expand`・`$top`・`$Task` がダブルクォートの中だと PowerShell の変数として消える。
- `continuationToken` が null でなければ、`-ContinuationToken` を付けて続きを取る。取り切れなかったものを全件として扱わない。
- 本文のファイルは Python で `encoding="utf-8"` で書く（BOM なし）。PowerShell 5.1 の `Out-File` / `Set-Content` は BOM や UTF-16 で書くので使わない。

## 書く（Work Item の作成・更新、コメント、PR の作成・スレッド）

`write` は、内容を見せて承認を取ってからでないと送れないようにしてある。

1. 本文を作る。Work Item の更新では、先頭に `{"op":"test","path":"/rev","value":<読んだときの rev>}` を入れる（承認の後に誰かが更新していたら失敗させるため）。
2. `-DryRun` で流し、出た `approval` を控える。
   ```powershell
   ADO write '<project>/_apis/wit/workitems/123' -Method PATCH -BodyFile patch.json -DryRun
   ```
3. ユーザーに、対象（URL と、人が読める名前: Work Item の番号とタイトル、PR の番号）・本文の中身・件数を見せて、承認を待つ。
4. 承認されたら、同じ引数に `-Approve <approval>` を付けて流す。本文や引数を変えたらハッシュが合わずに止まるので、2 からやり直す。

- 終了コード 3（送ったが応答が無い）のときは、**送り直さない**。対象を読み直して、反映されたかを確かめる。確かめられなければユーザーに戻す。
- `/rev` の test で 400 が返ったら、読み直し、変わった点を見せて承認を取り直す。新しい rev に差し替えて黙って送らない。
- 複数件（子タスク、PR のスレッド）は、全件の対象と本文を一覧で見せて一度に承認を取ってよい。送るときは 1 件ずつ `-DryRun` → `-Approve` を流し、一覧に載せた内容と違う本文は送らない。途中で失敗したら残りを送らずに、送れたものの番号を報告する。

## サンドボックスと承認

Codex のサンドボックスでネットワークが止められていると、`ado.ps1` の実行ごとに承認を求められる。
「常に許可」にしてよいのは `get` と `query` と `preview` だけ。**`write` は常に許可にしない。**
