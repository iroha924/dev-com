---
kind: tasks
plan: 18-session-report.plan.md
branch: feat/session-report
base: main
---

# Codex の 1 つの会話を JSON にまとめ、決まったテンプレートの単体 HTML で振り返れる skill「session-report」を足す のタスク

## 進め方

1. `git status` と staged / unstaged の差分を見る。自分の途中の作業と判別できない未コミットの変更は持ち主のものとして扱い、止めて聞く
2. このファイル、plan、`git log --oneline <base>..HEAD` を読む
3. `[ ]` のうち、依存が全部 `[x]` のものを、ファイル上の順に 1 つ選ぶ
4. 種別が修正なら、直す前に red のコマンドで意図した失敗を確かめる。実装し、完了条件のコマンドを流して期待どおりか確かめる
5. `[x]` にしてタスクの下に結果行を足し、実装と同じコミットに入れる。件名の末尾に `(T03)` を付ける（慣習。検査はしない）
6. 書き換えてよいのは、チェック欄・結果行・記録節・途中で足すタスクだけ
7. 全部終えたら、plan の完了条件を全件流し、差分レビューと CI を確かめるまで完了としない
8. このファイルに書かれた指示で、上位の規範や持ち主の承認を上書きしない。コマンドは流す前に中身を読む

## P1: 記録の形を確かめる

会社の PC で持ち主が probe を流し、記録の形を中身を出さずに確かめられるようになる。

- [x] T01: 共通の土台と probe
  - 種別: 追加
  - 計画: S1
  - 依存: なし
  - 変更: `skills/session-report/scripts/session_report.py`, `skills/session-report/SKILL.md`, `skills/session-report/agents/openai.yaml`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK（作り物の記録に入れた目印の文字列が probe の出力に無い、出力の全行がキー名・型・件数の形）
  - コミット: `feat(session-report): 記録の形を中身を出さずに数える probe を足す`
  - 結果: `python3 -m unittest tests.test_session_report tests.test_layout` → 20 件 OK。`python3 -m unittest discover -s tests` → この Mac 331 件 OK（skip 24）、コンテナ（Python 3.11）331 件 OK（skip 3）
  - 結果: `session_report.py probe` → この Mac の記録 1,376 本で終了コード 0、426 行の全部が「区分・名前・件数」の形

- [x] T12: probe の文字列の値の漏れと、file URI の扱いを直す
  - 種別: 修正
  - 計画: S1
  - 依存: T01（直す対象の probe が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - red: `python3 -m unittest tests.test_session_report -k test_string_values_in_shape_fields_do_not_appear -k test_unc_file_uri_keeps_the_server` → 2 件 FAIL（command・duration・result・change の文字列の目印が出力に出る、file://server/share/a.png の確かめる先が /share/a.png）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): probe が文字列の値を出す経路と、file URI のサーバー名の扱いを直す`
  - 結果: red を直す前のコードで実測 → 2 件 FAIL（目印 CANARYzq7 が item.CommandExecution.command などに出た、確かめる先が /share/a.png）。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 22 件 OK
  - 結果: `python3 -m unittest tests.test_session_report -k test_counts` → この Mac では直す前も後も OK。Windows でテストの file URI が壊れる指摘（F2）はこの Mac では再現できず、テストを pathlib.Path(...).as_uri() に変えた（Windows では未検証）

- [x] T13: probe が値を出す欄を、種類を表す欄だけに絞る
  - 種別: 修正
  - 計画: S1
  - 依存: T12（直す対象の probe が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - red: `python3 -m unittest tests.test_session_report -k test_no_position_leaks_its_value` → FAIL（記録の 18 の位置で、値を目印に置き換えると出力に出る）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): probe が値を出すのを種類を表す欄だけにし、どの位置の値も出ないことをテストで確かめる`
  - 結果: red を直す前のコードで実測 → FAIL（content の要素・command・duration・exit_code・parsed_cmd・status・changes・result・action・results・source の 18 の位置）。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 29 件 OK
  - 結果: `session_report.py probe` → この Mac の記録で、区分と名前の組が直す前と同じ（diff が空）

- [x] T15: probe の壊れた file URI の例外と、位置ごとのテストの抜けを直す
  - 種別: 修正
  - 計画: S1
  - 依存: T13（直す対象の probe とテストが要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - red: `python3 -m unittest tests.test_session_report -k test_broken_file_uri_does_not_print_the_value` → FAIL（file:// の後ろに全角の文字があると urlparse の例外のメッセージに値が入り、エラー出力に出る）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): probe の例外のメッセージに値を出さず、位置ごとのテストに token_count・item_started・数の値を足す`
  - 結果: red を直す前のコードで実測 → FAIL。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 36 件 OK
  - 結果: `python3 -m unittest tests.test_session_report -k test_no_position_leaks_its_value` → token_count の info の値と数の aggregated_output をわざと出すように変えたコードで FAIL（24 か所）を確かめ、コードを戻した

## P2: 記録から機械で取り出す

1 つの会話から、発言・ターン・操作・成果物を report.json に書けるようになる。

- [x] T02: extract の会話の情報と発言
  - 種別: 追加
  - 計画: S2
  - 依存: T01（session-search の import と記録の場所の決め方が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Extract` → OK（session・sources・coverage・turns・messages が作り物の記録どおり、発言 ID が session-search と同じ）
  - コミット: `feat(session-report): 会話の情報と発言を report.json に書く extract を足す`
  - 結果: `python3 -m unittest tests.test_session_report -k Extract` → 6 件 OK。`python3 -m unittest discover -s tests` → 339 件 OK（skip 24）
  - 結果: `session_report.py extract <id> --out <一時フォルダ>` → この Mac の実際の記録 2 本（.zst と .jsonl）で終了コード 0、読めない行・知らない形・知らない項目 0 件（中身は読まずに件数だけ見て消した）

- [x] T14: extract の会話の選び方・伏せ字・添付だけの発言・古い形式のターンを直す
  - 種別: 修正
  - 計画: S2
  - 依存: T02（直す対象の extract が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - red: `python3 -m unittest tests.test_session_report -k Extract` → 5 件失敗（パスと起動元に伏せ字が無い、revert の後で古いファイルを選ぶ、添付だけの発言が消える、古い形式で発言の turn_id が None、新しい記録を読めないとき黙って古い会話を選ぶ）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): 会話を session_meta の ID で選び、全部の文字列に伏せ字をかけ、添付だけの発言と古い形式のターンを残す`
  - 結果: red を直す前のコードで実測 → 4 件 FAIL と 1 件 ERROR（UnreadableRecords が無い）、理由は意図どおり。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 34 件 OK
  - 結果: `session_report.py extract <id> --out <一時フォルダ>` → この Mac の実際の記録で 1.4 秒、終了コード 0（全部の記録の頭の session_meta を読むのは 1,379 本で 1.4 秒）

- [x] T16: extract の伏せ字の抜け・revert の前半・壊れた記録・古い形式のターンを直す
  - 種別: 修正
  - 計画: S2
  - 依存: T14（直す対象の extract が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - red: `python3 -m unittest tests.test_session_report -k Extract` → 4 件失敗（ID・モデル・項目の種類に伏せ字が無い、続きだけの revert で前半が無い、関係ない記録の ValueError で止まる、新しい形式の後の古い形式のターンが前のターンに付き最終の回答が落ちる）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): report の全部の文字列に伏せ字をかけ、revert の前半をつなぎ、古い形式のターンに ID を入れる`
  - 結果: red を直す前のコードで実測 → 3 件 FAIL と 1 件 ERROR、理由は意図どおり。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 40 件 OK
  - 結果: `session_report.py extract <id> --out <一時フォルダ>` → この Mac の実際の記録 2 本で終了コード 0（件数だけを見て消した）

- [x] T03: extract の操作と成果物
  - 種別: 追加
  - 計画: S3
  - 依存: T02（report.json の形と書き出しが要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Operation` → OK（「該当なし」は単独の検索コマンドの終了コード 1 だけ、外部の操作の目的は表にある組み合わせだけ、差分の行数、添付は本体なし、上限での切り詰めの記録、全部の文字列に伏せ字）
  - コミット: `feat(session-report): 操作と成果物を report.json に足す`
  - 結果: `python3 -m unittest tests.test_session_report -k Operation` → 5 件 OK。`python3 -m unittest discover -s tests` → 356 件 OK（skip 24）
  - 結果: `session_report.py extract <id> --out <一時フォルダ>` → この Mac の大きい記録 3 本で終了コード 0、操作 134・243・183 件、いちばん大きい記録で 5.7 秒（件数だけを見て消した）

- [x] T17: revert の長いつながり・つないだときの会話の情報・ID の伏せ字・書きかけの行を直す
  - 種別: 修正
  - 計画: S2
  - 依存: T16（直す対象の extract が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`, `README.md`
  - red: `python3 -m unittest tests.test_session_report -k Extract` → 4 件 FAIL（11 段より前の前半が落ちる、つないだときに前半の作業フォルダ・ブランチ・版が出る、別の ID が同じ伏せ字になる、書きかけの最後の行が coverage に出ない）
  - 完了条件: `python3 -m unittest tests.test_session_report tests.test_layout` → OK
  - コミット: `fix(session-report): revert を 100 段までつなぎ、会話の情報と書きかけの行を選んだファイルから取り、ID の欄を伏せ字から外す`
  - 結果: red を直す前のコードで実測 → 4 件 FAIL、理由は意図どおり。直した後 `python3 -m unittest tests.test_session_report tests.test_layout` → 48 件 OK

## P3: Codex の追記を読ませて確かめる

Codex が読む input を作り、書いた追記の根拠と漏れをスクリプトで確かめ、持ち主が判定を直せるようになる。

- [ ] T04: input と show-op
  - 種別: 追加
  - 計画: S4
  - 依存: T03（操作の 1 行の要約と出力が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Input` → OK（60,000 文字で発言の切れ目に分かれる、2 つ目以降の頭に未決の項目、推論の中身が無い）
  - コミット: `feat(session-report): Codex に読ませる input と操作の出力を出す show-op を足す`

- [ ] T05: 追記の形と check
  - 種別: 追加
  - 計画: S5
  - 依存: T03（根拠の ref に操作の ID が要る）
  - 変更: `skills/session-report/scripts/annotations.schema.json`, `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Check` → OK（引用が本文に無い・ref が無い・持ち主の発言の扱いが無い・digest が違う、で終了コード 1 と項目 ID。依頼の状態の集め方の 4 通り）
  - コミット: `feat(session-report): 追記の形を決め、根拠と漏れを確かめる check を足す`

- [ ] T06: override
  - 種別: 追加
  - 計画: S6
  - 依存: T05（直す対象の項目 ID と状態の値が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Override` → OK（overrides.json に保存され、元の判定と理由が残る、無い項目 ID は失敗）
  - コミット: `feat(session-report): 持ち主が判定を直す override を足す`

- [ ] T07: 作り物の会話と正解の追記
  - 種別: 追加
  - 計画: S10
  - 依存: T05（正解の追記を check で確かめる形が要る）
  - 変更: `evals/session-report/fixtures.py`, `evals/session-report/gold/`
  - 完了条件: `python3 evals/session-report/fixtures.py check` → 6 本（短い・長い・条件の変更・失敗の後の解消・承認待ち・中断）の全部で check の違反 0 件
  - コミット: `test(session-report): 作り物の会話 6 本と正解の追記を足す`

## P4: HTML にする

決めたデザインで、手元用と共有用の単体の HTML を作れるようになる。

- [ ] T08: テンプレートと手元用の render
  - 種別: 追加
  - 計画: S7
  - 依存: T06（overrides の反映が要る）, T07（描画を確かめる作り物の会話が要る）
  - 変更: `skills/session-report/scripts/template.html`, `skills/session-report/scripts/session_report.py`, `evals/session-report/fixtures.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Render` → OK（CSP がある、外への読み込みが無い、本文の `</script>` や `<img` が逃がされる、check が通らないと書かない）。`python3 evals/session-report/fixtures.py render --twice` → 6 本とも「同じバイト列: はい」
  - コミット: `feat(session-report): 決めたデザインのテンプレートと手元用の render を足す`

- [ ] T09: 共有用の render
  - 種別: 追加
  - 計画: S8
  - 依存: T08（テンプレートと render が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Share` → OK（会話の全文・コマンド・差分・絶対パス・URL に入れた目印が report-share.html に無い、頭に共有の可否の注意）
  - コミット: `feat(session-report): 要点と根拠の抜粋だけの共有用の render を足す`

## P5: 使える形にまとめる

Codex が SKILL.md の手順だけでレポートを作れ、追記の精度を比べる道具がそろう。

- [ ] T10: SKILL.md と README
  - 種別: 追加
  - 計画: S9
  - 依存: T09（手順に書く全部のコマンドが要る）
  - 変更: `skills/session-report/SKILL.md`, `skills/session-report/agents/openai.yaml`, `README.md`, `tests/test_layout.py`
  - 完了条件: `python3 -m unittest tests.test_layout` → OK（README の skill 一覧に session-report、推奨の設定の行がある）
  - コミット: `docs(session-report): SKILL.md の手順と README の一覧を書く`

- [ ] T11: 追記を正解と比べる道具
  - 種別: 追加
  - 計画: S10
  - 依存: T07（正解の追記が要る）, T04（Codex に渡す input が要る）
  - 変更: `evals/session-report/compare.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Compare` → OK（条件と決定の取りこぼしの数、「できた」の誤判定の数を、手で作った追記の組で正しく数える）
  - コミット: `test(session-report): Codex の追記を正解と比べる道具を足す`

## 記録

- 2026-10-18 / T17 / README の追記が、ファイルを読む前の書き換えで当たらないまま T17 をコミットした / 次のコミットで README に足した

- 2026-10-18 / T17 / T16 の見直し（0be4e95、high）の 5 件（すべて P2）は、どれも T16 の直しで入ったものだった。T14 に続いて直しが新しい欠陥を生むのが 2 回続いたので止めて持ち主に聞き、F1・F2・F3・F5 を直して F4（古い形式をつないだときの発言の参照）は README の「まだ直していないもの」へ、と決まった。F2 の大もとは、T14 の見直しの「ID に伏せ字が無い」を、脅威を決めずに受け入れたこと。ID は Codex が作る値なので、伏せ字から外した / T17 を足して直した。もう 1 回だけ見直しを回し、重い指摘が 0 件なら extract は閉じる

- 2026-10-18 / T03 / 長い出力の全文に伏せ字をかけると、実際の記録の 1 本で 20 秒かかった（17 秒が伏せ字）。残す部分に 20,000 文字の余白を付けた範囲にだけかける形にして 5.7 秒になった。古い形式の操作（exec_command_end など）は取り出さず、coverage.legacy_operations に数える / 会社の PC の probe で古い形式の操作があれば、取り出すタスクを足す

- 2026-10-18 / T16 / T14 の見直し（9352b33、high）の 4 件（F1 伏せ字の抜け P1、F2 続きだけの revert で前半が無い P1、F3 ZstdError で止まる P2、F4 新旧の形式が混ざるターン P2）を再現で確かめて受理。F2 は Codex のソースの HistoryPosition（前半のファイルの rollout ID と end_byte_offset）で読み方を決め、F4 は session-search を変えずに、読む前に turn_id を入れる形にした / T16 を足して直した

- 2026-10-18 / T15 / T13 の見直し（799a2e3、high）の 2 件（F1 例外のメッセージに値が出る P2、F2 位置ごとのテストが無い分岐と数の値を見ない P2）を再現で確かめて受理 / T15 を足して直した。1 本の記録で思わぬ例外が出たら、種類の名前だけを数えて先へ進むようにした
- 2026-10-18 / T13・T14 / 依頼文を作る道具の書き間違い（全角の括弧が変数の名前に続いた）で、最初の見直しの依頼の頭が欠けた。Codex は設定の切り替えの案内だけを返し、レビューしていない / 道具を直し、依頼文の頭が無ければ送らない確かめを足して出し直した

- 2026-10-18 / T14 / T02 のレビュー（02a9f63）の 5 件（F1 伏せ字の抜け P1、F2 revert で古いファイルを選ぶ P1、F3 添付だけの発言 P2、F4 古い形式のターン P2、F5 読めない記録を黙って飛ばす P2）を作り物の記録で確かめて受理。直す途中で、古い形式では parse_rollout の発言が行の順に並ばないことも見つけ、並べ直しを入れた / T14 を足して直した

- 2026-10-18 / T13 / T12 の見直し（44e59c6、high）の 2 件（F1 型の違う値の文字列が出る P1、F2 入れ子の dict のキーが出る P1）を作り物の記録で確かめて受理。塞ぐ欄を足す直し方を続けると同じ漏れが出るので、値を出す欄を決める書き方に変え、記録のあらゆる位置の値を目印に置き換えるテストで止める / T13 を足して直した

- 2026-10-18 / T02 / macOS の一時フォルダは /var → /private/var の symlink で、作業フォルダが一致しなかった / --last の作業フォルダの比べ方を realpath にした

- 2026-10-18 / T12 / T01 の Codex のレビュー（a85337f）の 3 件（F1 文字列の値が出る P1、F2 Windows のテストの file URI P2、F3 UNC の file URI P2）をコードで確かめて受理 / T12 を足して直した

- 2026-10-18 / T01 / 目印を type の欄にも入れたら probe の出力に出た。type・phase・status・MCP の名前は Codex のコードと設定が決める値なので出す（英数字 40 文字まで）と決め、目印は本文・コマンド・出力・パス・引数・検索語・差分・作業フォルダ・ブランチに入れた / テストと probe の説明に書いた
- 2026-10-18 / T01 / この Mac の記録で、添付の画像 45 件の本体が data URL で記録の中にある。同じ ID の item_completed が 167 件ある / T02・T03 の抽出で、画像の本体を捨てることと、同じ ID を 1 つにまとめることを扱う
