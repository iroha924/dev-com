# Markdown とテキストの機械の検査

外形上の決まりだけを見る。**文章の意味は見ない**（それは SKILL.md の基準で判定する）。
対応する形式は Markdown（`.md` / `.markdown`）とテキスト（`.txt`）だけで、見出しの検査は Markdown の下書きと Markdown のテンプレートの組でだけ行う。

下書きは Python で `encoding="utf-8"` で書いてから流す（PowerShell 5.1 の `Out-File` と `>` は UTF-16 で書く）。

```powershell
python "$HOME\.agents\skills\writing-quality\scripts\check_draft_constraints.py" <下書きのパス> [--template <テンプレートのパス>] [--max-chars <数>]
```

| 終了コード | 意味 |
|---|---|
| 0 | 決まりを満たしている（警告は出ていることがある） |
| 1 | 下書きが空、テンプレートの見出しが欠けている、明示された字数を超えている |
| 2 | **検査は走っていない。**ファイルが無い、未対応の形式（HTML など）、UTF-8 で読めない、テキストの下書きにテンプレートを渡した |

終了コード 2 は「問題なし」ではない。報告では、機械の検査を未検証と書く。

**警告（未記入マーカー、同じ段落の重複）は意図的なことがある**ので終了コードを変えない。該当箇所を見て、意図的なら残す。

`--max-chars` は、ユーザーや送り先が字数の上限を示したときだけ渡す（ADO の PR の説明の上限は `ado` skill の endpoints.md の「確かめていないこと」）。
