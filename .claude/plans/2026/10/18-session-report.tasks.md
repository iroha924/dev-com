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

## P2: 記録から機械で取り出す

1 つの会話から、発言・ターン・操作・成果物を report.json に書けるようになる。

- [ ] T02: extract の会話の情報と発言
  - 種別: 追加
  - 計画: S2
  - 依存: T01（session-search の import と記録の場所の決め方が要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Extract` → OK（session・sources・coverage・turns・messages が作り物の記録どおり、発言 ID が session-search と同じ）
  - コミット: `feat(session-report): 会話の情報と発言を report.json に書く extract を足す`

- [ ] T03: extract の操作と成果物
  - 種別: 追加
  - 計画: S3
  - 依存: T02（report.json の形と書き出しが要る）
  - 変更: `skills/session-report/scripts/session_report.py`, `tests/test_session_report.py`
  - 完了条件: `python3 -m unittest tests.test_session_report -k Operation` → OK（「該当なし」は単独の検索コマンドの終了コード 1 だけ、外部の操作の目的は表にある組み合わせだけ、差分の行数、添付は本体なし、上限での切り詰めの記録、全部の文字列に伏せ字）
  - コミット: `feat(session-report): 操作と成果物を report.json に足す`

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

- 2026-10-18 / T12 / T01 の Codex のレビュー（a85337f）の 3 件（F1 文字列の値が出る P1、F2 Windows のテストの file URI P2、F3 UNC の file URI P2）をコードで確かめて受理 / T12 を足して直した

- 2026-10-18 / T01 / 目印を type の欄にも入れたら probe の出力に出た。type・phase・status・MCP の名前は Codex のコードと設定が決める値なので出す（英数字 40 文字まで）と決め、目印は本文・コマンド・出力・パス・引数・検索語・差分・作業フォルダ・ブランチに入れた / テストと probe の説明に書いた
- 2026-10-18 / T01 / この Mac の記録で、添付の画像 45 件の本体が data URL で記録の中にある。同じ ID の item_completed が 167 件ある / T02・T03 の抽出で、画像の本体を捨てることと、同じ ID を 1 つにまとめることを扱う
