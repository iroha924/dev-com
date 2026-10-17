---
name: session-report
description: Codex の 1 つの会話を、依頼と成果・決めたこと・残った課題・実際の会話をタブで見られる単体の HTML にする（作っている途中。今使えるのは記録の形を数える probe だけ）。「記録の形を調べて」「probe を流して」と言われたときに使う。
metadata:
  short-description: 会話を振り返る HTML を作る（作成中）
---

# session-report

推奨の設定: gpt-6-luna・low。比べ方は AGENTS.md の「モデルと effort の案内」

今使えるのは probe だけ。レポートを作る手順（extract・check・render）は、作り終えたらここに書く。

## probe

```powershell
python "$HOME\.agents\skills\session-report\scripts\session_report.py" probe
python "$HOME\.agents\skills\session-report\scripts\session_report.py" probe --since 2026-10-01
```

記録（`$CODEX_HOME\sessions` と `archived_sessions` の rollout）を全部読み、行の種類・項目の種類とキー名・件数・有無だけを、
`区分<タブ>名前<タブ>件数` の形で出す。本文・コマンド・出力・パス・引数・検索語・差分は出さない。
出す値は、Codex のコードと設定が決める種類の値（type・phase・status・MCP の server と tool の名前・版）だけ。

結果は持ち主にそのまま見せる。持ち主が中身を確かめてから、別の場所へ貼るかを決める。
