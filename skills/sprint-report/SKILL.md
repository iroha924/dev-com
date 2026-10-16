---
name: sprint-report
description: Azure DevOps Server のスプリントの状況を読み取り専用でまとめる。daily はデイリースクラム前の自分の進捗・レビュー待ち・失敗ビルド・停滞候補、review はスプリントレビューと振り返り用の成果・未完了・振り返りの材料。「デイリーの準備」「今日話すこと」「スプリントのまとめ」「振り返りの材料」と言われたときに使う。
metadata:
  short-description: デイリーとスプリントレビューの下書き
---

# sprint-report

ADO の読み取りは `ado` skill の手順に従う。**このスキルは何も書き込まない。**
Project と Team は、リポジトリの中なら `ado_context.py` と Team 一覧から決める。Team が複数あって決まらなければ聞く。

## daily

対象期間は、前の営業日の 0 時から今まで（月曜なら金曜から）。ユーザーが期間を言えばそれに従う。祝日は考慮しないので、休み明けは期間を確かめる。

集めるもの:

1. 自分の Work Item（WIQL `[System.AssignedTo] = @Me AND [System.IterationPath] = @CurrentIteration`）と、期間中の変更。変更は Reporting の `workitemrevisions?startDateTime=<期間の始まり>` で一度に取り、自分の Work Item の ID で絞る（1 件ずつ updates を取るより呼び出しが少ない）。続きの取り方と日時の書き方は endpoints.md の「期間中の変更をまとめて」。コメントは updates か comments で取る
2. 自分がレビュアーで、自分の `vote` が 0 の active な PR
3. 自分が作った active な PR と、レビュアーの投票（-5 作成者待ち、-10 却下を目立たせる）と、ポリシーの評価（`policy/evaluations`）。`configuration.isBlocking` が true のもののうち、`rejected` と `broken` はマージを止めている、`queued` と `running` はマージを待たせている。どちらも「困っていること」の候補にする
4. 3 の PR の source ブランチの、期間中に失敗したビルド（`branchName` と `resultFilter=failed` で絞る）

出すもの:

- デイリーで話す 3 行（昨日やったこと / 今日やること / 困っていること）。各行に Work Item か PR の番号を付ける。
- 停滞の候補: 状態が進行中のまま期間中に変更が無く、紐付く active な PR も無いもの。「候補」として出し、停滞と断定しない。
- 取得した範囲（期間、Team、Iteration、自分の ID）を末尾に 1 行。取得できなかったものはそう書く。

## review

対象は現在の Iteration（ユーザーが言えばその Iteration）。スプリントゴールは ADO に標準の欄が無いので、ユーザーに聞くか、無いまま進める。

集めるもの: Iteration の Work Item 全件（Team の `iterations/{iterationId}/workitems` で ID を取り、workitemsbatch で状態・種別・親を取る）、期間中に完了した PR と紐付く Work Item、未完了の Work Item のコメントと変更履歴、チームのキャパシティ（`capacities`、`teamdaysoff`、`teamsettings` の `workingDays`）。

**スプリント中に足された Work Item** は、次の差で数える。初日のプランニングで入れたものと、スプリントの終了後に入れたものは数えず、途中で足してから外したものは数える。

1. Iteration の `path`（Team の Iteration 一覧の値）を P、`attributes.startDate` と `finishDate` の日付の部分を開始日と終了日とする（どちらも日付だけの値で、時刻は意味を持たない）。初日の終わりの T0 は開始日の翌日の 0 時、終わりの T1 は終了日の翌日の 0 時を、手元のタイムゾーンから UTC にしたもの（日本時間なら前日の 15 時、`2026-10-13T15:00:00Z` の形）。
2. チームの範囲: `teamsettings/teamfieldvalues` の `values[]` が、そのチームの Area のパス。`includeChildren` が true なら下の階層も含む。同じ Iteration を別のチームも使うので、Area で絞らないと他のチームの分が混ざる。
3. A: 初日の終わりにチームの Iteration にあったもの。Team のパスへ WIQL `SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] = '<P>' AND (<Area の条件>) ASOF '<T0>'` を POST する。Area の条件は、値ごとに `[System.AreaPath] UNDER '<値>'`（`includeChildren` が true）か `[System.AreaPath] = '<値>'`（false）を `OR` でつなぐ。
4. B: T0 から T1 までに一度でもチームの Iteration にいたもの。Reporting の `workitemrevisions?startDateTime=<T0>&fields=System.Id,System.IterationPath,System.AreaPath,System.WorkItemType,System.ChangedDate` を最後まで取り、`System.ChangedDate` が T1 より前で、`System.IterationPath` が P、`System.AreaPath` がチームの範囲に入るリビジョンを持つ ID を集める。
5. 足されたもの = B − A。種別（PBI、バグ、タスク）ごとに数える。

キャパシティは、メンバーごとに「`activities[].capacityPerDay` の合計 × 働ける日の数」を求め、チーム全体で足す。働ける日は、開始日から終了日までの日のうち、`workingDays` の曜日に当たり、そのメンバーの `daysOff` の範囲にもチームの休日（`teamdaysoff`）の範囲にも入らない日。休みを日数で引き算しない（重なった日を二重に引き、土日も引いてしまう）。

出すもの:

| 節 | 中身 |
|---|---|
| ゴールに対する成果 | 完了した PBI。PR がマージされたことと、受け入れ条件を満たしたこと（状態が Done）を分ける |
| デモの順番の案 | 利用者から見える変化が大きい順 |
| 未完了 | 残った理由（コメントと履歴に書かれていること。書かれていなければ「理由の記録なし」） |
| 振り返りの材料 | 着手から完了までの日数の分布、PR の作成からマージまでの日数、スプリント中に足された Work Item の数（種別ごと）、チームのキャパシティの合計（時間）と、休日で減った分 |

**個人ごとの件数や速さの比較は出さない。**チームの単位で数える。
