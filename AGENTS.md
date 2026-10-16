# 作業するときの規範（グローバル）

どのプロジェクトでも、最初の 1 手より前に要るものだけを置く。手順は skill（`$HOME\.agents\skills`）にある。

## 外へ書き込む前に

Azure DevOps の Work Item・PR・コメント、チャット、メールなど、送った後に取り消せないものは、
対象と本文を見せて承認を受けてから送る。ADO への書き込みは `ado` skill の手順で行う。

## Windows の文字コードと改行

- 既存のファイルは、その文字コードと改行コードに合わせる。まとめて変換しない。
- 新しく作る md / txt / json / py は UTF-8（BOM なし）。
- **日本語を含む .ps1 は UTF-8 の BOM 付きで保存する。**Windows PowerShell 5.1 は BOM の無いファイルを ANSI（cp932）として読み、日本語が化ける。ASCII だけなら BOM は要らない。
- Python で `open()` や `subprocess` の text を使うときは `encoding` を書く。Windows の既定は cp932。
- PowerShell 5.1 の `Out-File` と `>` は UTF-16、`Set-Content` は ANSI で書く。UTF-8 で書きたいファイルは Python で書く。

## 報告

実行した検査と結果、実行しなかった検査とその理由を書く。実行していないものを通ったと書かない。
