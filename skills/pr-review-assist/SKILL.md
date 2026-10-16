---
name: pr-review-assist
description: Azure DevOps Server の他人の PR（または自分の PR の出す前の確認）をレビューする。PR の差分を正しい基準で取り、change-risk-reviewer に独立した文脈で読ませ、裏を取った指摘だけを行番号付きの下書きにし、承認後にスレッドとして投稿する。「PR #123 をレビューして」と言われたときに使う。
metadata:
  short-description: PR のレビューと指摘の投稿
---

# pr-review-assist

ADO の読み書きは `ado` skill の手順に従う。投票（承認・却下）はしない。

## 1. 差分を取る

1. PR を取り、タイトル・説明・`sourceRefName`・`targetRefName`・紐付く Work Item（受け入れ条件）を控える。
2. Iteration 一覧から最新（ユーザーが言えばその Iteration）を選び、`commonRefCommit`・`sourceRefCommit` と Iteration の ID を控える。
3. `git fetch origin <sourceRefName> <targetRefName>` のあと、`git cat-file -e <sha>` で 2 つのコミットが手元にあるかを確かめる。無ければ（ブランチの削除や force push）そう報告して止まる。
4. 差分は `git diff <commonRefCommit> <sourceRefCommit>`。ブランチの先端どうしの diff は使わない（PR 画面の差分と変わる）。
5. 前に同じ PR をレビューしていて、その後の push だけを見たいときは、Iteration の変更一覧を `$compareTo=<前に見た iterationId>` で取ってファイルを絞り、差分は前に見た Iteration の `sourceRefCommit` から今の `sourceRefCommit` までを取る。全体を見直すかはユーザーに聞く。
6. `git merge-base --all <targetRefCommit> <sourceRefCommit>` が 2 つ以上返るときは、Iteration の変更一覧（`changes`）のファイルと差分のファイルを突き合わせ、ずれを報告に書く。

## 2. 読ませる

custom agent の `change-risk-reviewer` を、`spawn_agent` に `agent_type: "change-risk-reviewer"`・`fork_turns: "none"` を付けて起動し、次を渡す。**`fork_turns` を省かない。**省くと既定の `"all"` で親の会話を全部引き継ぎ、独立して読ませる意味が無くなる。

- PR のタイトル・説明、Work Item の受け入れ条件
- `commonRefCommit` と `sourceRefCommit` の SHA、差分
- 変更されたファイルの一覧と、必要に応じて周りのコードを読んでよいこと

自分の見立ては渡さない（独立して読ませるため）。

## 3. 裏を取る

返ってきた指摘を 1 件ずつ、コードを読んで確かめる。再現できる、または該当行と発生条件をコードで示せるものだけを残す。
残さなかったものは、理由を 1 行ずつ報告の末尾に並べる。

## 4. 下書きして投稿する

- 指摘ごとに、番号・ファイル（リポジトリのルートからの `/` 始まりのパス）・`sourceRefCommit` 側の行・本文を一覧で見せる。本文は「何が・どの条件で・どうなる・どう直すとよいか」。
- ユーザーが投稿する番号を選ぶ。
- 投稿の直前に Iteration 一覧を取り直す。新しい Iteration が増えていたら、行がずれている可能性があるので、差分を取り直して行を確かめる。確かめられない指摘は行に付けず、ファイルを名指しした全体コメントにする。
- 行に付けるスレッドは、`threadContext` と `pullRequestThreadContext`（`changeTrackingId` は Iteration の変更一覧から、`secondComparingIteration` は使った Iteration）を付ける。形は `ado` skill の [references/endpoints.md](../ado/references/endpoints.md)。
- 送り方は `ado` skill の「書く」（複数件の承認のしかたも同じ）。投稿したスレッドの ID を返す。
