---
name: shomrkm-notion
description: shomrkm の Notion ワークスペースを GTD ベースで効率的に操作する。Tasks DB / Projects DB / LifeLog / [GTD] Review / Knowledge / Links / Books の7つの主要DBへの追加・更新・検索を担う。「タスク追加して」「明日XX」「Today に入れて」「Daily Log」「週次レビュー」「Knowledge にまとめて」「未読のLinks」「読書記録」「Notion に書いて」「Notion から探して」など、shomrkm の Notion を触る依頼があれば必ずこのスキルを使うこと。DB ID やプロパティ名、GTD のステータス値は references/databases.md に正確に書かれているので、推測せず必ず参照する。
---

# shomrkm-notion

shomrkm の Notion ワークスペース専用の操作スキル。GTD ベースの運用ルールに沿って Tasks/Projects/LifeLog/[GTD] Review/Knowledge/Links/Books を扱う。

## いつ使うか

ユーザー (shomrkm) が以下のような依頼をしたとき:

- 「タスク追加して」「明日XX」「Today に入れて」「Next Actions」「Wait for」
- 「Daily Log 書く」「今日の振り返り」「学んだこと記録」
- 「週次レビュー」「今週の振り返り」
- 「Knowledge にまとめて」「ナレッジ化」「Cornell Note」
- 「読んだ記事」「Links に追加」「後で読む」「未読の Links」
- 「読書記録」「本を追加」
- 「今日のタスクは？」「進行中のプロジェクトは？」「最近の Knowledge」
- 「Notion に書いて」「Notion から探して」

## コア原則

**1. 推測しない。不明点は AskUserQuestion で必ず確認する。**
- task_type、due_date、Tags、Status などはユーザーが明示しない限り推測しない
- 1依頼あたり質問は2-3問までに絞る (暴走しない)
- 相対日付 (「明日」「今週中」) は今日基準で解決して**ユーザーに提示して確認**

**2. DB ID やプロパティ名は references/databases.md を必ず Read する。**
- 推測でハードコードしない (typo を含めて完全一致でないと書き込みが失敗する)
- 主要 typo の例: `Reveiw` (Review ではない)、`Bisiness` (Business ではない)、`Pratical` (Practical ではない)、`Raad it again` (Read ではない)、`is leaning note` (learning ではない)、`種別 ` (末尾スペースあり)

**3. 今日基準で日付を解決する。**
- 環境コンテキストの currentDate を使う (例: 2026-05-11)
- ISO-8601 形式 (`YYYY-MM-DD`) で書き込む
- `due_date` は `date:due_date:start` の expanded property で指定する

**4. GTD 用語の整合性を守る。**
- task_type の値は完全一致: `Today` / `Next Actions` / `Someday / Maybe` / `Wait for` / `Completed` / `Routin` / `Closed`
- `Someday / Maybe` はスラッシュ前後にスペースがある (`Someday/Maybe` ではない)
- `Routin` (Routine ではない、原文ママ)

**5. 作成・更新後は必ず Notion の URL を返す。**
- ユーザーがクリックして開けるようにする

## ルーティングテーブル

ユーザーの言葉から、まず読むべき reference ファイルを判断する:

| ユーザーが言ったら | 必ず Read する reference | 対象 DB |
|---|---|---|
| タスク追加 / 明日 / Today / Next Actions / Wait for / Someday / TODO | `references/tasks-workflow.md` | Tasks DB |
| タスクを完了 / Closed に / Archive | `references/tasks-workflow.md` | Tasks DB |
| プロジェクト作成 / Project 追加 / プロジェクト化 | `references/tasks-workflow.md` | Projects DB |
| Daily Log / 今日の振り返り / 学んだこと記録 | `references/lifelog-workflow.md` | LifeLog |
| 週次レビュー / 今週の振り返り | `references/review-workflow.md` | [GTD] Reveiw DB |
| Knowledge にまとめる / ナレッジ化 / Cornell Note | `references/knowledge-workflow.md` | Knowledge |
| 読んだ記事 / Links に追加 / 後で読む / URL を保存 | `references/reading-workflow.md` | Links |
| 本を追加 / 読書記録 / 読了 | `references/reading-workflow.md` | Books |
| 今日のタスクは / 未読は / 進行中のプロジェクトは / 最近の〜 | `references/query-patterns.md` | 横断 |

不明な場合は `references/databases.md` を Read して全体像を確認してから判断する。

## ワークフロー (全タイプ共通)

1. **ユーザーの依頼を分類** → 上のルーティングテーブルで該当 reference を特定
2. **`references/databases.md` を Read** (DB ID / プロパティ正確値の参照)
3. **該当 workflow reference を Read** (典型手順と必須確認事項)
4. **AskUserQuestion で不明点を確認** (推測禁止)
5. **Notion MCP ツールで書き込み or 読み取り**:
   - 書き込み: `mcp__notion__notion-create-pages` / `mcp__notion__notion-update-page`
   - 読み取り: `mcp__notion__notion-query-database-view` / `mcp__notion__notion-fetch` / `mcp__notion__notion-search`
6. **結果を端的に報告し、URL を提示**

## AskUserQuestion を使うときの一般則

- **必ず確認**: 選択肢が複数あって推測不能な項目 (task_type、Category、Tags など)
- **デフォルト提示+確認**: 相対日付 (「明日」→ `2026-05-12` で良いかを確認)
- **省略OK**: ユーザーがすでに明示した項目 (再確認しない)
- **質問の総数**: 1依頼あたり最大4問 (AskUserQuestion 1回で複数質問できる)
- **テキスト質問は避ける**: 文章で確認しない。必ず AskUserQuestion ツールを使う

## 主要 Notion MCP ツール (詳細は各 reference 参照)

- `mcp__notion__notion-create-pages`: 新規ページ作成。`data_source_url` (collection://...) と `properties` を指定
- `mcp__notion__notion-update-page`: 既存ページのプロパティ更新
- `mcp__notion__notion-query-database-view`: 既存 view を使ったクエリ (フィルタ・ソート済み)
- `mcp__notion__notion-fetch`: 個別ページ・DBの詳細取得
- `mcp__notion__notion-search`: ワークスペース全体の semantic search

書き込み時の properties の書き方:
- title プロパティ: そのプロパティ名 (DB により `Name` / `name` / `名前` / `Title` と異なる — `references/databases.md` で確認)
- 日付プロパティ: `date:<column_name>:start` の **expanded property** を使う (例: `date:due_date:start`)
- relation プロパティ: 関連ページの URL 配列 (JSON)
- select: 選択肢の値そのもの (完全一致)
- multi_select: 値の JSON 配列

## 既存の汎用 Notion スキルとの違い

`Notion:create-task`、`Notion:create-page`、`Notion:database-query` などの汎用スキルとは**併用しない**。本スキルは shomrkm の DB ID と GTD 運用ルールを内包した特化版で、汎用スキルでは shomrkm のワークフローを正確に再現できない (typo や GTD 用語の整合性を守れない)。

## 出力フォーマット

タスク完了後の報告は端的に:

```
✓ Tasks DB に追加しました
  - Name: カフェテリアプランを考える
  - task_type: Today
  - due_date: 2026-05-12 (明日)
  - URL: https://www.notion.so/35bd...
```

クエリ結果は表形式または短いリスト形式で。

## 補足: 環境コンテキスト

スキル実行時、システムコンテキストに以下があれば必ず利用する:
- `currentDate`: 今日の日付 (相対日付の解決基準)
- `userEmail`: ユーザー識別 (shomrkm の Notion のはず)
