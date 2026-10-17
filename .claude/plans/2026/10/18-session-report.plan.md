---
kind: plan
status: approved
codex_session: 01a147ad-465b-7900-a1ce-51142abceb47
codex_rounds: 3
approved_at: 2026-10-18
---

# Codex の 1 つの会話を JSON にまとめ、決まったテンプレートの単体 HTML で振り返れる skill「session-report」を足す

## 要点

- 新しい skill `skills/session-report` を足す。スクリプト `session_report.py` が記録から機械で取れるものを JSON にし、Codex が書いた追記（要約・依頼と成果・決定・課題）を根拠付きで足し、1 つのテンプレートで単体の HTML にする
- 最初に `probe`（記録の中身を出さず、項目の種類・件数・有無だけを数える）を作る。会社の PC の記録の形は、持ち主がこれを流した結果で確かめる
- 追記の根拠（発言・操作の ID と引用）は、スクリプトが実在と一致を確かめる。依頼の成否は条件ごとの判定から機械で集める。持ち主は `override` で判定を直せる
- 出力は「手元用」（会話と操作の詳細を上限付きで）と「共有用」（要点と根拠の抜粋だけ）の 2 つ。共有用に除いたものが残らないことをテストで確かめる。画像の本体は埋め込まない
- 見た目は 2026-10-18 に決めたデザイン（hir4ta knowledge の色、横線の区切り、角丸なし、6 つのタブ）をそのまま使う
- 変えないもの: session-search の動き（記録の読み方と伏せ字を、ファイルを写さずに import して使う）、ほかの skill、install.ps1

## 持ち主の決定

- Codex の 1 つの会話の履歴をチャットのように見せ、重要な情報をタブで切り替えて見られる HTML を作る。振り返りと 1 週間スプリントでの共有に使う
- JSON に構造化して 1 つの HTML テンプレートに当てはめる。同じ JSON からは同じ HTML になり、機械でテストできる
- 機械で取れるものは機械で取る。決めたこと・要約・残った課題のように機械で取れないものは Codex が会話を読んで JSON で書き足す。書き足しには根拠の発言 ID と引用を付け、スクリプトが実在と一致を確かめる。書き足した JSON は保存し、作り直しでは利用枠を使わない
- session-search の記録の読み方（新旧の形式、.zst、伏せ字）を使い回す
- 出力の範囲（C20、議論の後に持ち主が決定）: 手元用は詳細を上限付きで残す。画像の本体は埋め込まない。共有用は要点と根拠の抜粋だけ
- デザイン（2026-10-18）: https://claude.ai/artifact/32Z3MeBmfUtjTSWCHKAMwU の版 3。hir4ta knowledge の色、naito-otsuka.com のような横線の区切り、角丸なし。会話は持ち主が右寄せ・Codex が左寄せで、メッセージを 1px の線で囲む
- hirata-codex は PR を作らず main へ直接 push する（2026-10-17）

## 目的

- 持ち主が会社の PC の Codex で「このセッションのレポートを作って」と言うと、`%LOCALAPPDATA%\hirata-codex\session-report\<thread_id>\` に手元用と共有用の HTML ができる。ブラウザで開くと、6 つのタブで、何を頼み・何ができ・何を決め・何が残ったかと、実際の会話が読める
- 同じ記録と同じ追記からは、何度作っても同じバイト列の HTML になる
- 追記の引用が会話に無い、ID が無い、持ち主の発言の扱いが抜けている、のどれかがあると、`check` が失敗して HTML を作らない

## 対象外

- トークン数・利用枠の表示（C18・C33。応答単位と累積値の関係を probe で確かめるまで載せない）
- 推論の内容（Reasoning）、システムと開発者の指示、子のエージェントとの通信の全文、文脈の圧縮の全文（C19。出来事として「子による調査あり」「文脈の圧縮あり」だけ出す）
- HTML の上での編集（静的な単体の HTML から JSON へ書き戻せない。訂正は `override`）
- 複数の会話をまとめたレポート（スプリント全体は sprint-report の役目）
- 共有してよいかの判断（共有用は除外を機械でかけるだけで、共有の可否は持ち主が確かめる。HTML にもそう出す）

## 前提

- 記録の項目の型: `codex-rs/protocol/src/items.rs`（scratchpad の codex-repo、0.162 系）。CommandExecutionItem は command（配列）・cwd・parsed_cmd・status（InProgress / Completed / Failed / Declined）・aggregated_output・exit_code・duration。FileChangeItem は changes（パスごとの変更）・status。McpToolCallItem は server・tool・arguments・status ほか。WebSearchItem は query・action・results。ImageViewItem は path だけ。UserMessageItem の content は Text / Image / LocalImage / Audio / LocalAudio / Skill / Mention（`user_input.rs:16`）
- この Mac の記録 1,327 本の行の種類と件数（2026-10-17 に数えた。議論の依頼文に記載）。item_completed の item.type は Reasoning 29,356、CommandExecution 26,195、AgentMessage 6,718、McpToolCall 5,085、UserMessage 1,948、FileChange 1,336、WebSearch 102、ImageView 74 ほか
- session-search の `parse_rollout`（`skills/session-search/scripts/session_search.py:580`）は発言だけを取り出し、発言 ID は session-search の参照と同じ形。`read_rollout_bytes`・`_byte_lines`・`redact`・`_INJECTED` も同じファイルにある
- install.ps1 は `skills\<name>\` を `$HOME\.agents\skills\<name>\` に並べて入れる（`install.ps1:108`）。session-report から `..\session-search\scripts` を import できる
- 会社の PC の記録の形（Codex の版、旧形式の割合、ado.ps1 の引数と結果の形、添付の画像がパスか本体か）: 未検証。月曜に持ち主が probe を流すまで確かめられない
- 会社の PC の Python は 3.8 以上（install.ps1 の案内）。この Mac は 3.9.6。標準ライブラリだけで書く

## 方針

### ファイル

- `skills/session-report/SKILL.md`、`agents/openai.yaml`（allow_implicit_invocation: true。書き込みは手元のファイルだけ）、`scripts/session_report.py`、`scripts/template.html`、`scripts/annotations.schema.json`
- 出力の置き場所: `%LOCALAPPDATA%\hirata-codex\session-report\<thread_id>\`（mac・linux は session-search の data_dir と同じ決め方）。中身は `report.json`（機械の分）、`input-<n>.md`（Codex に読ませる分）、`annotations.json`（Codex の追記）、`overrides.json`（持ち主の訂正）、`report.html`（手元用）、`report-share.html`（共有用）
- session-search は `sys.path` に `../../session-search/scripts` を足して import する。見つからなければ「session-search も入れてください」と出して止める

### コマンド

- `probe [--since 日付]`: 全部の rollout を読み、行の種類・item.type・各 item のキー名・UserMessage の content の種類・AgentMessage の phase の値・exit_code と aggregated_output の有無と長さの分布・ImageView の path が今あるか・ID の重複・MCP の server と tool の名前の件数を出す。値（本文・コマンド・パス・引数）は出さない。テストで、作り物の記録に入れた目印の文字列が出力に無いことを確かめる
- `extract <thread_id|--last|--current>`: report.json を書く。`--current` は環境変数 CODEX_THREAD_ID の会話（その時点まで）。`--last` は今の作業フォルダの、今の会話を除いた最新の会話
- `input`: report.json から、Codex に読ませる `input-<n>.md` を書く。持ち主の発言と Codex の公開回答は原文（伏せ字の後）、操作は ID・順序・種類・対象・成否・短い結果の 1 行。1 ファイル 60,000 文字を上限に、発言の切れ目で連続した範囲に分ける。2 つ目以降の頭に、前の範囲までの追記で未決の項目（できた以外の依頼、未回答の質問）を並べる
- `show-op <操作 ID>`: 達成判定に要る操作の出力を、手元用の上限の範囲で出す
- `check`: annotations.json を検査する（下の「追記の検査」）。違反は `<項目 ID>: <何が違う>。<どう直す>` の形で出し、0 件なら終了コード 0
- `render [--share]`: check を通した追記と overrides を合わせて HTML を書く。check が通らなければ書かない
- `override <項目 ID> --status <状態> --reason <理由>`: overrides.json に保存する。HTML は訂正後の状態を出し、元の判定と理由もたどれるようにする

### 機械で取るもの（report.json）

- `schema_version`、`renderer_version`、`redaction_version`（session-search の `_RULES_VERSION`）、`tz_offset`（extract を流した PC の時差。表示はこれで固定）
- `session`: id、名前（session_index.jsonl）、記録の始まりと最後の時刻（「実働時間」とは書かない）、作業フォルダ、ブランチ（session_meta の時点）、起動元、forked_from_id
- `sources[]`: rollout のパス・大きさ・SHA-256。`coverage`: 読めない行・知らない形・最後の行が途中・子の会話を含まないこと
- `turns[]`: ID、始まり・終わり、モデルと effort（turn_context）、状態（完了 / 中断）
- `messages[]`: session-search の parse_rollout の結果（ID・種類・時刻・本文）。添付は名前と「本体なし」
- `operations[]`: ID、時刻、経路（command / mcp / web / file / image / extension）、対象、成否、所要時間、結果（手元用の上限: 出力は先頭 2,000 文字と末尾 2,000 文字、差分は 1 ファイル 20,000 文字。切ったことを記録する）
  - コマンドの「該当なし」: パイプ・`&&`・`;` の無い 1 つの rg / grep / findstr / Select-String を直接流し、終了コードが 1 のときだけ。オプションはテストで確かめたものだけ通す。ほかの失敗は終了コードのまま示し、解消したかは Codex の追記
  - 外部の操作の目的（参照 / 作成 / 更新 / 削除 / 不明）: 経路・メソッド・対象を別に持ち、表に書いた組み合わせ（ADO MCP の tool 名、ado.ps1 の `get` / `write` とメソッドと API の道、`az repos` / `az boards` / `az pipelines` のサブコマンド）だけを分類する。ほかは不明
  - ファイル: FileChange のパスごとの追加・変更・削除と、差分の増えた行と減った行の数（操作の時点のもので、最後の git の差分とは書かない）
- `artifacts[]`: MCP の結果とコマンドの出力にはっきり出た PR・Work Item の番号と、最後に見えた状態と時刻（「今の状態」とは書かない）
- 全部の文字列に、HTML に入れる前に session-search の `redact` をかける。Codex に読ませる input も伏せ字の後のもの

### 追記（annotations.json）と検査

- 形は `annotations.schema.json` に固定する: `summary`、`chapters[]`（発言が 40 件を超える会話だけ）、`requests[]`（ID、本文、lifecycle: 有効 / 取り下げ、`criteria[]`: 本文・出どころ（持ち主が言った / Codex が補った）・状態（できた / 一部 / できていない / 未確認）・根拠の種類・根拠）、`questions[]`（状態: 答えた / 一部回答 / 未回答、答えの発言）、`decisions[]`（決めた人: 持ち主 / Codex の提案を持ち主が承認 / Codex の提案のまま / 保留、理由、採らなかった案、置き換えた決定）、`lessons[]`、`issues[]`（未解決 / 次に / 判断待ち）、`command_notes[]`（失敗と解消した操作の ID）、`owner_messages`（持ち主の発言 ID ごとに、対応する項目 ID か「訂正」「了承のみ」「資料提供」）、`source_digest`
- 根拠は `{ref: 発言か操作の ID, quote}`。check は、ref が report.json にあること、quote が ref の本文（伏せ字の後、空白を詰めて比べる）に含まれること、全部の持ち主の発言に owner_messages があること、ID の重複が無いこと、`source_digest` が今の report.json と同じことを確かめる。digest が違えば、追記を読み替えずに「記録が変わった。追記を作り直すか、古いまま出すか」と出して止める（`render --stale-ok` で古いまま出し、HTML にそう書く）
- 依頼の状態は条件から機械で決める: 全部できた → できた / できたか一部が 1 つ以上で全部できたではない → 一部 / できたも一部も無くできていないが 1 つ以上 → できていない / それ以外（条件 0 件を含む）→ 未確認。取り下げた依頼は数えない
- Codex の追記の手順は SKILL.md に書く: extract → input → input を順に読み annotations.json を書く（足りない出力は show-op で読む）→ check → 違反を直す → render。推奨の設定は gpt-6.1-sol・medium（人に読まれる文章と判断を含む）

### HTML

- `template.html` は決めたデザインの版 3 を元に、見本のデータを抜いた 1 ファイル。データは `<script type="application/json" id="data">` に入れ、`<` `>` `&` U+2028 U+2029 を `\u` の形に逃がす。描画は template の中の JS が JSON から組み立てる。会話などの本文は textContent で入れ、innerHTML に本文を入れない
- `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">` で外への読み込みを全部止める。外部の CDN、フォントの読み込み、画像を使わない
- 同じバイト列にするため、JSON は `sort_keys` と区切りを固定し、作った時刻を入れない
- 共有用は、手元用とは別の JSON を作ってから同じ template に入れる。残すのは要約・依頼と成果・決定・学び・課題・成果物（PR と Work Item の番号と題名）と、それらの根拠の引用。除くのは会話の全文・操作の詳細・差分・コマンド・絶対パス（作業フォルダは最後のフォルダ名だけ）・URL・画像のパス。会話のタブと成果物と操作の詳細は出さず、「共有用のため省略」と出す。頭に「共有してよいかは持ち主が確かめる」と出す

## 採った案と棄却した案

- 採用: 依頼を持ち主の発言の一覧と分け、Codex が根拠付きで抜き出す。棄却: 持ち主の発言をそのまま依頼とする（質問・訂正・承認が混ざる、C4）
- 採用: 質問は機械の候補を Codex が確定する。棄却: 機械だけで質問を見分ける（疑問符の無い問いを落とす、C5）
- 採用: 6 タブ（概要 / 会話 / 依頼と成果 / 判断と学び / 成果物と操作 / 課題と次の一手）。棄却: 10 タブ（細かすぎる、C25）
- 採用: 条件ごとに判定し、依頼の状態は機械で集める。棄却: 確信度の数値（根拠の種類のほうが直しやすい、C27・C28）
- 採用: 会話の本文を原文で渡し、操作は要約して必要な分だけ読ませる。棄却: 依頼と最終の回答と操作の要約だけを渡す（途中の回答にだけある決定を落とす、C29・C30）
- 採用: 訂正は CLI で overrides.json に保存。棄却: HTML の上で編集（単体の HTML から書き戻せない、C28）
- 採用: 共有用を別の JSON から作る。棄却: 手元用を隠して共有用にする（畳んでも HTML の中に残る、C10・C32）
- 採用: 「該当なし」と外部の操作の目的は、確かめた形だけ機械で分類し、ほかは不明。棄却: コマンド名や HTTP メソッドだけで決める（複合コマンドの終了コード、問い合わせの POST、C7・C8）
- 採用: probe を最初に作り、会社の記録の形を中身を出さずに確かめる。棄却: 実装の後に会社の PC で初めて流す（形の違いで抽出の範囲を作り直す、C33）
- 採用: 引用の照合は伏せ字の後の本文どうしで行う。棄却: 伏せ字の前の原文と照合する（Codex に伏せ字の前の文を読ませることになる、C23 の案からの変更）
- 採用: session-search を import する。棄却: 読み方と伏せ字を写す（伏せ字の規則が分かれて、片方だけ直ることになる）

## 手順

- S1: 共通の土台と probe。session-search の import、記録の場所の決め方、probe と、目印の文字列が出ないテスト
- S2: extract。report.json の session・sources・coverage・turns・messages
- S3: extract の operations と artifacts。コマンドの成否と「該当なし」、外部の操作の分類、ファイルの差分の数、添付と画像、上限での切り詰め、伏せ字
- S4: input と show-op。範囲の分け方と、未決の項目の引き継ぎ
- S5: annotations.schema.json と check。根拠の照合、owner_messages の漏れ、ID の重複、digest、依頼の状態の集め方
- S6: override と、overrides の反映
- S7: template.html と render（手元用）。CSP、逃がし、同じバイト列、6 タブ、流れのタイムライン、会話のタブ
- S8: render --share。共有用の JSON と、除いたものが HTML のどこにも残らないテスト
- S9: SKILL.md・agents/openai.yaml・README の skill 一覧・tests/test_layout.py の更新
- S10: 作り物の会話（短い・長い・条件の変更・失敗の後の解消・承認待ち・中断）の rollout と、その正解の追記を `evals/session-report/` に置き、Codex の追記と正解を比べる道具を作る（流すのは持ち主に規模を聞いてから）

## 完了条件

- A1: `python3 -m unittest discover -s tests` → この Mac とコンテナの両方で OK。失敗 0
- A2: `python3 evals/session-report/fixtures.py render --twice` → 作り物の会話 6 本の全部で「同じバイト列: はい」と出て、終了コード 0
- A3: `python3 -m unittest tests.test_session_report -k Share` → OK（会話の全文・コマンド・差分・絶対パス・URL に入れた目印の文字列が report-share.html に無い）
- A4: `python3 skills/session-report/scripts/session_report.py probe` → この Mac の記録で終了コード 0。出力の全行が「キー名・型・件数」の形（`python3 -m unittest tests.test_session_report -k ProbeShape` が同じ形の検査を作り物の記録で持つ）
- A5: `python3 evals/session-report/fixtures.py render` → できた HTML を Chrome で開き、幅 1280px（13 インチのノート PC が最小）、ライトとダークで、6 タブの切り替え・流れの横スクロール・会話の絞り込みが動き、コンソールにエラーが 0 件
- A6: `python3 -m unittest tests.test_session_report -k Check` → OK（引用が本文に無い・ref が無い・持ち主の発言の扱いが無い・digest が違う、の 4 つで check が終了コード 1 と該当の項目 ID を出す）
- A7: `git diff b324e62..HEAD` → `codex` スキルの新しい会話でレビューさせ、重い指摘が 0 件

## リスク

- 会社の PC の記録の形がこの Mac と違う → S1 を先に main へ push し、持ち主が月曜に probe を流す。違いが出たら、該当する抽出のタスクを足す
- Codex の追記が条件や決定を落とす、「できた」と誤って判定する → S10 の比べ方で数える。原因を見てから、SKILL.md の手順か検査を足す。check は意味の取り違えまでは止められないと README に書く
- 伏せ字が共有用の引用に残った秘密を拾えない → 共有の可否は持ち主が確かめると HTML に出す。session-search の伏せ字の規則を直したら両方に効く
- 長い会話で input が多くなり、利用枠を使う → 範囲の上限と件数を SKILL.md で先に見せ、持ち主が止められるようにする
- HTML が大きくなる → 手元用の上限で切る。作り物の長い会話で大きさを測り、5MB を超えたら上限を見直す

## 未解決

なし

## 変更履歴
- 2026-10-18 / A5 の幅を「375px と 1280px」から「1280px」にした / 持ち主の指示（見るのは 13 インチのノート PC が最小で、それより小さい幅では見ない）/ 持ち主の指示なので Go の取り直しは要らない
