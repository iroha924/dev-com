---
name: pr-evidence-pack
description: 作業ブランチの差分・関連 Work Item・実行した検査・CI の結果から、Azure DevOps Server の PR のタイトルと本文を下書きし、承認後に PR を作る。PR を作るとき、PR の説明を書くときに使う。
metadata:
  short-description: PR の本文を証拠付きで下書きして作る
---

# pr-evidence-pack

推奨の設定: gpt-6.1-sol・medium（人に読まれる PR の本文を書くため）。比べ方は AGENTS.md の「モデルと effort の案内」

ADO の読み書きは `ado` skill の手順に従う。既定は下書きまで。PR の作成は、ユーザーが作ると言ったときだけ。

## 1. 材料を集める

- 基準ブランチ: ユーザーの指定、無ければ `git symbolic-ref refs/remotes/origin/HEAD`。決まらなければ聞く。
- 差分: `git log --oneline <base>..HEAD`、`git diff --stat <base>...HEAD`、必要な箇所の `git diff <base>...HEAD`。
- Work Item: ユーザーの指定、ブランチ名やコミットメッセージの `#1234`。タイトル・説明・受け入れ条件（`Microsoft.VSTS.Common.AcceptanceCriteria`）を読む。見つからなければ聞く。推測で紐付けない。
- 検査: このセッションで実行した検査と結果。実行していないものは「未実行」として扱う。
- CI: そのブランチの最新ビルドのうち、`sourceVersion` が今の `HEAD` と一致するものだけを今回の結果として使う。一致しなければ「HEAD のビルドは未確認」と書く。
- PR テンプレート: 既定ブランチの `.azuredevops/` → `.vsts/` → `docs/` → ルートの順に `pull_request_template.md`（か `.txt`）を探し、最初に見つかったものの節立てに従う。対象ブランチ別の `pull_request_template/branches/<branch>.md` があればそちらを優先する。

## 2. 下書きする

テンプレートが無いときの節立て:

1. 目的（Work Item への参照 `#1234` と、1〜2 行の要約）
2. 変更点（レビュアーが差分を開く前に知っておくべきこと）
3. 選んだ案と棄却した案（1 行ずつ）
4. 受け入れ条件と証拠の対応（条件ごとに、満たしたことを示す検査・確認の結果。示せないものは「未確認」）
5. 検査（実行したものと結果 / 実行しなかったものと理由）
6. レビューで見てほしい点

- 本文は 4000 文字以内を目安にする（それを超えると作成に失敗するという第三者の記述がある。公式の記載は未確認）。超えるなら変更点を絞る。
- タイトルは変更の中身を表す 1 行。Work Item のタイトルをそのまま写さない。

下書きを見せて、直す点を聞く。

## 3. 作る（ユーザーが作ると言ったとき）

1. ブランチがリモートにあり、`HEAD` まで push 済みかを `git status -sb` と `git ls-remote origin <branch>` で確かめる。push していなければ、push するかをユーザーに聞く（push は別の操作として扱う）。
2. 本文を Python で JSON に書く。`sourceRefName` / `targetRefName` は `refs/heads/...`、Work Item は `workItemRefs: [{"id": "1234"}]`、下書きの PR にするなら `isDraft: true`。
3. `ado` skill の「書く」の手順で、`<project>/_apis/git/repositories/<repo>/pullrequests` へ POST する。
4. 作られた PR の番号と URL を返す。レビュアーの追加と投票はしない（頼まれたときだけ）。
