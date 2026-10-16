# Azure DevOps Server 2020 で使うエンドポイント（REST 6.0）

公式の REST リファレンス（`?view=azure-devops-server-rest-6.0`）と 2026-10-16 に照合した。会社のサーバーでは未実行。
ページは `https://learn.microsoft.com/en-us/rest/api/azure/devops/<領域>/<操作>?view=azure-devops-server-rest-6.0`。
パスは Collection URL からの相対。クエリの値に `:` `/` `+` を含むもの（`buildUri`、`artifactId`、`startDateTime`）は、URL エンコードしてから入れる（`+` はそのままだと空白として読まれる）。PowerShell からは、外側をダブルクォート、内側をシングルクォートにする（PowerShell 5.1 と 7.2 以前は、ネイティブのコマンドへ渡す引数の中のダブルクォートを落とす）。

```powershell
python -c "import urllib.parse as u; print(u.quote('vstfs:///Build/Build/123', safe=''))"
```

`ado.ps1` は api-version を付け忘れたら 6.0（`preview` は 6.0-preview.1）を足す。
preview のバージョンが要るものは表のバージョンを `-ApiVersion` で渡す。

## Work Item

| 用途 | メソッドとパス | api-version | 注意 |
|---|---|---|---|
| WIQL | POST `{project}/{team}/_apis/wit/wiql` | 6.0 | `query` モード。本文 `{"query": "..."}`。返るのは ID だけ。`$top` で件数を絞る。結果は最大 20,000 件 |
| 一括取得 | POST `{project}/_apis/wit/workitemsbatch` | 6.0 | `query` モード。本文 `{"ids": [...], "fields": [...]}`。**ids は 200 件まで** |
| 1 件取得 | GET `{project}/_apis/wit/workitems/{id}?$expand=relations` | 6.0 | 応答の `rev` を更新の test に使う |
| 変更履歴 | GET `{project}/_apis/wit/workItems/{id}/updates` | 6.0 | `$top` / `$skip` |
| 期間中の変更をまとめて | GET `{project}/_apis/wit/reporting/workitemrevisions?startDateTime=<ISO 8601>&fields=System.Id,System.State,System.AssignedTo,System.IterationPath,System.ChangedDate` | 6.0 | `startDateTime` より後のリビジョンだけが返る。日時は UTC の `Z` 付きで書き、URL エンコードする。`types` で種別を絞る。**続きは応答の本文の `continuationToken` で取る。**2 回目からは `startDateTime` を外し、`continuationToken=<本文の値>` と `fields` だけのパスを自分で組み立てて取る（公式: `startDateTime` は continuationToken と一緒に使えない）。`ado.ps1` の `-ContinuationToken` は応答のヘッダーの値を使うものなので、ここでは使わない。`isLastBatch` が true になるまで続ける |
| コメント一覧・追加 | GET / POST `{project}/_apis/wit/workItems/{id}/comments` | 6.0-preview.3 | 本文 `{"text": "<p>...</p>"}`。**HTML で書く** |
| 作成 | POST `{project}/_apis/wit/workitems/$Task` | 6.0 | JSON Patch（`application/json-patch+json`、ado.ps1 が自動で付ける）。パスは `$` を含むのでシングルクォート |
| 更新 | PATCH `{project}/_apis/wit/workitems/{id}` | 6.0 | 先頭に `{"op":"test","path":"/rev","value":N}` |

親子のリンク（作成時に足す）:

```json
{"op": "add", "path": "/relations/-", "value": {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "<collection>/_apis/wit/workItems/<親のID>"}}
```

`Hierarchy-Reverse` は「この Work Item の親」、`Hierarchy-Forward` は「子」。

よく使うフィールド: `System.Title`、`System.State`、`System.AssignedTo`、`System.IterationPath`、`System.AreaPath`、
`System.Description`（HTML）、`Microsoft.VSTS.Common.AcceptanceCriteria`（HTML）、`Microsoft.VSTS.Scheduling.RemainingWork`。
プロセスの独自フィールドはプロジェクトごとに違うので、1 件取得して `fields` の中身を見てから使う。

## Sprint（Team）

| 用途 | メソッドとパス | api-version | 注意 |
|---|---|---|---|
| Team 一覧 | GET `_apis/projects/{project}/teams` | 6.0 | サンプルは GUID。名前で通らなければ `_apis/projects/{project}` で ID を取る |
| 現在の Iteration | GET `{project}/{team}/_apis/work/teamsettings/iterations?$timeframe=current` | 6.0 | `attributes.startDate` / `finishDate` |
| Iteration の Work Item | GET `{project}/{team}/_apis/work/teamsettings/iterations/{iterationId}/workitems` | 6.0-preview.1 | `workItemRelations[]`。中身は workitemsbatch で取る |
| チームの設定 | GET `{project}/{team}/_apis/work/teamsettings` | 6.0 | `workingDays`（稼働する曜日） |
| チームの範囲 | GET `{project}/{team}/_apis/work/teamsettings/teamfieldvalues` | 6.0 | `field.referenceName`（ふつうは `System.AreaPath`）と `values[]`（`value`、`includeChildren`） |
| キャパシティ | GET `{project}/{team}/_apis/work/teamsettings/iterations/{iterationId}/capacities` | 6.0 | メンバーごとの `activities[].capacityPerDay` と `daysOff[]`。合計は自分で足す |
| チームの休日 | GET `{project}/{team}/_apis/work/teamsettings/iterations/{iterationId}/teamdaysoff` | 6.0 | `daysOff[]`（`start` / `end`） |

WIQL では `@Me`、`@Today`、`@CurrentIteration`（Team を含むパスに POST したとき）が使える。
`ASOF '<日時>'` を末尾に付けると、その時点の状態で問い合わせられる。時刻を書かなければ 0 時、タイムゾーンを書かなければ手元の PC のタイムゾーンで読まれる（公式）ので、UTC の `Z` 付きで書く。使い方の例は `sprint-report` の review。`EVER` は「一度でもその値だったことがある」。クエリの文は 32K 文字まで（[WIQL の構文（2020 のページ）](https://learn.microsoft.com/en-us/azure/devops/boards/queries/wiql-syntax?view=azure-devops-2020)）。

自分の ID: `_apis/connectionData` は REST リファレンスに載っておらず、6.0 でのバージョンが未確認。
取れなければ、WIQL の `[System.AssignedTo] = @Me` で 1 件取り、`System.AssignedTo` の `id` を使う。

## Pull Request

`{repo}` は名前でも ID でもよい。パスの前半は `{project}/_apis/git/repositories/{repo}`。

| 用途 | メソッドとパス（前半は省略） | api-version | 注意 |
|---|---|---|---|
| 一覧 | GET `/pullrequests?searchCriteria.status=active&searchCriteria.reviewerId={id}` | 6.0 | `creatorId` も使える。status の既定は active |
| 1 件 | GET `/pullrequests/{prId}` | 6.0 | `lastMergeSourceCommit` / `lastMergeTargetCommit`、`reviewers[].vote`（10 承認、5 提案付き承認、0 未投票、-5 作成者待ち、-10 却下） |
| Iteration | GET `/pullrequests/{prId}/iterations` | 6.0 | `commonRefCommit` / `sourceRefCommit` / `targetRefCommit` |
| Iteration の変更 | GET `/pullrequests/{prId}/iterations/{iterationId}/changes` | 6.0 | `changeEntries[].changeTrackingId`。`$top` 既定 100、最大 2000。`$compareTo=<前の iterationId>` でその Iteration からの変更だけ（既定 0 は共通の祖先から） |
| スレッド一覧・作成 | GET / POST `/pullrequests/{prId}/threads` | 6.0 | 下の本文 |
| 紐付く Work Item | GET `/pullrequests/{prId}/workitems` | 6.0 | |
| ポリシーの評価 | GET `{project}/_apis/policy/evaluations?artifactId=vstfs:///CodeReview/CodeReviewId/{projectId}/{prId}` | 6.0-preview.1 | パスの前半は付けない。`projectId` は GUID（`_apis/projects/{project}` の `id`）。`status` は queued / running / approved / rejected / notApplicable / broken。`configuration.isBlocking` と `configuration.type.displayName` でどの決まりかが分かる |
| 作成 | POST `/pullrequests` | 6.0 | `sourceRefName`、`targetRefName`、`title`、`description`、`workItemRefs`、`isDraft` |

行に付けるスレッドの本文:

```json
{
  "comments": [{"parentCommentId": 0, "content": "指摘の本文（Markdown 可）", "commentType": 1}],
  "status": 1,
  "threadContext": {"filePath": "/src/app.py", "rightFileStart": {"line": 42, "offset": 1}, "rightFileEnd": {"line": 42, "offset": 1}},
  "pullRequestThreadContext": {
    "changeTrackingId": 3,
    "iterationContext": {"firstComparingIteration": 1, "secondComparingIteration": 4}
  }
}
```

`line` は 1 始まり。`offset` は公式の説明（0 始まり）とサンプル（1）が食い違うので、公式のサンプルどおり 1 を使う。
`status` 1 は active。

## ビルドとパイプライン

| 用途 | メソッドとパス | api-version | 注意 |
|---|---|---|---|
| ビルド一覧 | GET `{project}/_apis/build/builds?definitions={id}&branchName=refs/heads/x&statusFilter=completed&resultFilter=succeeded&queryOrder=finishTimeDescending&$top=1` | 6.0 | 最新を取るときは `queryOrder` を明示する |
| ビルド 1 件 | GET `{project}/_apis/build/builds/{buildId}` | 6.0 | `sourceBranch`、`sourceVersion`（コミット）、`definition.id`、`uri` |
| ビルドに入った変更 | GET `{project}/_apis/build/builds/{buildId}/changes` | 6.0 | コミットの `id`（SHA）・`author`・`message`。公式の説明は「ビルドに紐付く変更」だけで、どのビルドからの差分かは書かれていない。間のビルドが落ちていると全部は出ないことがあるので、`git log` でも確かめる |
| ビルドのテスト実行 | GET `{project}/_apis/test/runs?buildUri=<ビルドの uri>` | 6.0 | `uri` は `vstfs:///Build/Build/{buildId}` の形 |
| 失敗したテスト | GET `{project}/_apis/test/Runs/{runId}/results?outcomes=Failed` | 6.0 | `errorMessage`、`stackTrace`（1000 文字まで）、**`failingSince`**（いつ・どのビルドから落ち続けているか）。`$top` は最大 1000 |
| タイムライン | GET `{project}/_apis/build/builds/{buildId}/timeline` | 6.0 | `records[]` の `result`（failed など）、`log.id`、`issues[]` |
| ログ | GET `{project}/_apis/build/builds/{buildId}/logs/{logId}` | 6.0 | `-Accept text/plain` で取る。`startLine` / `endLine` |
| YAML から定義を引く | GET `{project}/_apis/build/definitions?yamlFilename=<path>` | 6.0 | |
| パイプライン 1 件 | GET `{project}/_apis/pipelines/{id}` | 6.0-preview.1 | |
| 展開の確認 | POST `{project}/_apis/pipelines/{id}/runs` | 6.0-preview.1 | `preview` モードだけで送る。応答の `finalYaml` |

ビルドの再実行（PATCH `builds/{id}?retry=true`）は 6.0 にあるが、失敗したジョブだけが再実行されるかは公式に書かれていない。
再実行はユーザーに画面から頼む。

## 確かめていないこと

- `_apis/connectionData` の 6.0 でのバージョン
- `logs/{id}` を `application/json` で取ったときの応答の形
- パイプライン ID とビルド定義 ID が同じ値であること（同じとして扱っているが、公式の明記は見ていない）
- PR の説明の文字数の上限（4000 文字という第三者の記述だけがある）
- WIQL の `ASOF` に `2026-10-13T15:00:00Z` の形（UTC の `Z` 付き）を書いたときに、Server 2020 がそのとおり読むか
- Reporting の `workitemrevisions` で、2 回目以降に `startDateTime` を外して `continuationToken` だけで取る形が、Server 2020 で期待どおりに続きを返すか
