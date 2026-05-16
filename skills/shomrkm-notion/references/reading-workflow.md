# reading-workflow.md — Links DB / Books DB の操作

`references/databases.md` の Links / Books セクションを先に Read していること。

## 1. このワークフローのトリガー

### Links

- 「この記事 Links に追加」「後で読む」「あとで読み」
- 「URL を保存」「ブックマーク」
- 「読み終わった (Links の)」「Finished にする」

### Books

- 「本を追加」「読書記録」「読了」
- 「(本のタイトル) 読んだ」

## 2. AskUserQuestion で必ず確認すること

### Links 新規追加

1. **`userDefined:URL`** (URL): ユーザーが渡した URL。複数渡されたら全部処理
2. **`Name` (タイトル)**: URL から fetch してタイトルを自動取得する (WebFetch ツール) のが理想。失敗したら AskUserQuestion でタイトル確認
3. **`Category`**: `Technology` / `Design` / `Business` / `Document Writing` / `General` / `GIS` / `ai` のどれか。内容から候補を提示
4. **`Label`**: 内容から既存 Label を 1-3 個提案 (`React`, `TypeScript` など、ユーザーが頻用するもの)
5. **`Status`**: デフォルト `Ready to Start` (まだ読んでない)。すでに読んだ場合は `Finished`
6. **`Read Date`**: Status が `Reading` or `Finished` なら今日 (確認)。`Ready to Start` なら不要
7. **`AI summary`**: WebFetch で内容を取得できた場合、AI が短いサマリを書いて確認。希望しなければスキップ

### Links ステータス更新

「読み終わった」と言われたら:
- 対象 Links 特定 (search または query-database-view "Unread")
- `Status = Finished`、`Read Date = 今日`

### Books 新規追加

1. **`Name` (タイトル)**: ユーザーが明示
2. **`Author`**: ユーザーが明示、または聞く
3. **`Category`**: `Technical` / `Novel` / `Bisiness`(原文ママ) / `Financial` / `Essays` / `Pratical Book`(原文ママ) / `Other`
4. **`Status`**: `Ready to Start` / `Reading` / `Finished` / `Close`
5. **`Score /5`** (Finished の場合): ⭐絵文字。デフォルトは聞く
6. **`Finished Date`** (Finished の場合): いつ読み終えたか
7. **`Link`**: 書籍の URL があれば

## 3. 書き込み手順

### Links 新規

```jsonc
{
  "parent": { "data_source_id": "collection://88bb9d19-d08f-4983-ba5a-1282c1044894" },
  "pages": [{
    "properties": {
      "Name": "Claude Code Hooks の公式ドキュメント",
      "userDefined:URL": "https://docs.anthropic.com/...",
      "Category": "Technology",
      "Label": ["claude code", "MCP"],
      "Status": "Ready to Start",
      "AI summary": "(AI による短い要約)"
    }
  }]
}
```

### Books 新規

```jsonc
{
  "parent": { "data_source_id": "collection://138dca21-71f3-4736-a01c-19d26ec2bf92" },
  "pages": [{
    "properties": {
      "Name": "Clean Architecture",
      "Author": "Robert C. Martin",
      "Category": "Technical",
      "Status": "Finished",
      "Score /5": "⭐️⭐️⭐️⭐️⭐️",
      "date:Finished Date:start": "2026-05-11",
      "date:Finished Date:is_datetime": 0
    }
  }]
}
```

### Status 更新 (読了)

```jsonc
{
  "page_id": "https://www.notion.so/<page-url>",
  "properties": {
    "Status": "Finished",
    "date:Read Date:start": "2026-05-11",
    "date:Read Date:is_datetime": 0
  }
}
```

## 4. WebFetch との連携

URL を渡されたら以下の順で処理:

1. WebFetch で URL を読み、タイトル・要約を取得 (失敗時は AskUserQuestion でタイトル確認)
2. 内容から `Category` と `Label` の候補を提案
3. AskUserQuestion で全項目をまとめて確認
4. create-pages

ユーザーが「サマリ要らない」と言ったら AI summary はスキップ。

## 5. 典型例

### 例1: 「これ Links に追加: https://example.com/article」

1. WebFetch で取得 → タイトル「XX について」、内容ざっくり把握
2. AskUserQuestion: Category 候補 `Technology` + `ai`、Label 候補 `LLM`, `Prompt Engineering`、Status `Ready to Start`、AI summary 要る?
3. OK → create-pages

### 例2: 「Clean Architecture 読了、5点」

1. notion-search で Books DB 内に既存があるか確認
2. 既存あれば update-page、なければ create-pages
3. AskUserQuestion: Author (デフォルト Robert C. Martin)、Category (Technical で確定)、Score /5 (`⭐️⭐️⭐️⭐️⭐️`)、Finished Date (今日でよい?)
4. 書き込み

## 6. 避けるべき間違い

- ❌ Books の Category を `Business` と書く (正しくは `Bisiness`)
- ❌ Books の Category を `Practical Book` と書く (正しくは `Pratical Book`)
- ❌ Books の `Raad it again` を `Read it again` と書く
- ❌ Books の Status を `Closed` と書く (正しくは `Close` — Tasks/Links とは違う)
- ❌ Links の URL プロパティを `URL` で書く (内部名は `userDefined:URL`)
- ❌ Score /5 を `5` のような数字で書く (正しくは `⭐️⭐️⭐️⭐️⭐️`)
- ❌ Label を勝手に新規追加 (既存優先)

## 7. 出力フォーマット

Links:
```
✓ Links に追加しました
  - Name: Claude Code Hooks
  - Category: Technology
  - Label: claude code, MCP
  - Status: Ready to Start
  - URL: https://www.notion.so/<page>
```

Books:
```
✓ Books に追加しました
  - Name: Clean Architecture
  - Author: Robert C. Martin
  - Category: Technical
  - Status: Finished
  - Score: ⭐️⭐️⭐️⭐️⭐️
  - URL: https://www.notion.so/<page>
```
