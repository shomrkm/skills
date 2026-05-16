# databases.md — shomrkm の Notion DB のSoT

各DBの ID・プロパティ・選択肢の**正確な値**を保持する。typo や末尾スペースを含めて、Notion 側の現状をそのまま記録している。書き込み時はこの値を完全一致で使うこと。

## 全DB一覧 (クイックリファレンス)

| DB名 | data source URL (書き込み時) | DB URL (ビュー閲覧時) |
|---|---|---|
| Tasks DB | `collection://b2b78cf4-0ad7-4bba-bfde-3f2691b3be25` | `https://www.notion.so/66adfdd3961e48c8a533ec01dbf24478` |
| Projects DB | `collection://19b3bba9-342b-46c3-a3ce-c7b0b8ee468a` | `https://www.notion.so/85f41c09281a44cb8883aa6ec0309402` |
| LifeLog | `collection://3587b931-adde-44bb-bf56-4872c50a5ce8` | `https://www.notion.so/43772e6fc21c4755b5c37a6ed77955a2` |
| [GTD] Reveiw DB | `collection://2c90ee4d-a0dd-4c6a-a824-ab83cb73bb9f` | `https://www.notion.so/9ebb28df4099484aa27615fcbd6bbda7` |
| Knowledge | `collection://8288e3ed-6a64-4bc5-882c-04b78b775fc1` | `https://www.notion.so/20b5ffeef46347f6991d7648f07de698` |
| Links | `collection://88bb9d19-d08f-4983-ba5a-1282c1044894` | `https://www.notion.so/e628b52db7d04ebfbde8d94671e1af5c` |
| Books | `collection://138dca21-71f3-4736-a01c-19d26ec2bf92` | `https://www.notion.so/b55e8b632f9248d79d9617dc06c52df4` |

---

## ☑️ Tasks DB

- **data source URL**: `collection://b2b78cf4-0ad7-4bba-bfde-3f2691b3be25`
- **title プロパティ**: `Name`

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `Name` | title | タスク名 |
| `task_type` | select | GTD のステータス (下記) |
| `due_date` | date | 期限。書き込みは `date:due_date:start` |
| `working date` | date | 作業日。書き込みは `date:working date:start` (スペース注意) |
| `completed_at` | date | 完了日。書き込みは `date:completed_at:start` |
| `description` | text | タスクの説明 |
| `project` | relation → Projects | 所属プロジェクト |
| `Knowledge` | relation → Knowledge | 関連ナレッジ |
| `tasks` | relation → Knowledge | 関連ナレッジ (別経路) |
| `hidden` | checkbox | `"__YES__"` / `"__NO__"` |
| ` is_archived` | checkbox | **先頭スペースあり** `" is_archived"` / `"__YES__"` / `"__NO__"` |
| `is_completed` | formula (readOnly) | 書き込み不可 |
| `is_delayed` | formula (readOnly) | 書き込み不可 |

### task_type の選択肢 (完全一致必須)

- `Today` (赤)
- `Next Actions` (青)
- `Someday / Maybe` (紫) **← スラッシュ前後にスペースあり**
- `Wait for` (黄)
- `Completed` (緑)
- `Routin` (灰) **← Routine ではなく Routin (原文ママ)**
- `Closed` (茶)

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| In-Progress | `https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=259d4af9764f8067b2e8000c4d28c973` | 1週間以内 due または Today、かつ未完了 |
| Board | `https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=252d4af9764f80bc92ea000cf4a8c1e6` | task_type 別かんばん |
| By Project | `https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=259d4af9764f804b875d000ca1894a5b` | プロジェクト別 |
| All | `https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=243175a531f54dbea1a40e279b565eef` | アクティブ全部 |

### テンプレ "Template" の content 構造

```
## Description
---

## Goal
---
- [ ] goal 1
- [ ] goal 2

## Learning Note
---
(Knowledge へのリンクボタン)
```

---

## Projects DB

- **data source URL**: `collection://19b3bba9-342b-46c3-a3ce-c7b0b8ee468a`
- **title プロパティ**: `name` (小文字)

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `name` | title | プロジェクト名 |
| `status` | status | 下記 |
| `importance` | select | ⭐ の数 (下記) |
| `task` | relation → Tasks | 配下タスク |
| `🖥️ Knowledge` | relation → Knowledge | 関連ナレッジ (絵文字込みのプロパティ名) |
| `progress` | rollup (readOnly) | tasks の `is_completed` 集計 |
| `Created time` | created_time (readOnly) | |
| `Last edited time` | last_edited_time (readOnly) | |

### status の選択肢 (完全一致必須)

- `Not started` (To-do グループ)
- `planning` (To-do グループ)
- `in-progress` (In progress グループ)
- `completed` (Complete グループ)
- `closed` (Complete グループ)

### importance の選択肢

- `⭐⭐⭐⭐⭐`
- `⭐⭐⭐⭐`
- `⭐⭐⭐`
- `⭐⭐`
- `⭐`

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| In Progress | `https://www.notion.so/shomrkm/85f41c09281a44cb8883aa6ec0309402?v=bf395f58a7da4dc1ba7c83b0e01c7ea5` | To-do + In Progress のプロジェクト |
| Completed | `https://www.notion.so/shomrkm/85f41c09281a44cb8883aa6ec0309402?v=e66dd561edbb46c9ab715fc88ade6a6e` | 完了プロジェクト |
| All | `https://www.notion.so/shomrkm/85f41c09281a44cb8883aa6ec0309402?v=5799690aa4f04916b12dfc90362e24dc` | 全プロジェクト |

---

## LifeLog

- **data source URL**: `collection://3587b931-adde-44bb-bf56-4872c50a5ce8`
- **title プロパティ**: `Name`

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `Name` | title | ログのタイトル |
| `Date` | date | 記録日。書き込みは `date:Date:start` |
| `Tags` | multi_select | 下記 |
| `userDefined:URL` | url | 外部 URL (プロパティ表示名は `URL`) |
| `AI summary` | text | AI 生成サマリ |
| `Created time` | created_time (readOnly) | |
| `Last edited time` | last_edited_time (readOnly) | |

### Tags の既存値 (multi_select、新規追加も可能だが既存優先)

`cook`, `Favorite`, `smarthr`, `Travel`, `AI`, `Prompt Engineering`, `CSS`, `メルマガ`, `週次まとめ`, `Memo`

### テンプレ "@Today Daily Log" の content 構造

```
> 全部埋めなくてOK。1行だけでもOK。

### 今日のハイライト
-
### 詰まった点 / 違和感
-
### 学び（Fact / Because / Next）
- **Fact**:
- **Because**:
- **Next**:
### 明日やる一手
-
```

---

## [GTD] Reveiw DB

⚠️ DB名の typo `Reveiw` (Review ではない、原文ママ)。

- **data source URL**: `collection://2c90ee4d-a0dd-4c6a-a824-ab83cb73bb9f`
- **title プロパティ**: `名前` (日本語)

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `名前` | title | レビュー名 |
| `種別 ` | select | **末尾スペースあり** (`"種別 "`)。値: `週次レビュー` |
| `実施日` | date | 実施日時。書き込みは `date:実施日:start` |
| `振り返り対象期間` | date (range) | 対象期間。書き込みは `date:振り返り対象期間:start` / `:end` |
| `今週の成長度` | number (0-10) | バー表示 |
| `今週の挑戦度` | number (0-10) | バー表示 |

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| (default) | `https://www.notion.so/shomrkm/9ebb28df4099484aa27615fcbd6bbda7?v=d4d10563ef1e4695a6d3c1c6a165be16` | 実施日 desc で一覧 |

### テンプレ "2 0 2 3 週次レビュー 0 0" の content 構造

```
> 未来と現状のギャップを認識し、来週をより良い一週間にするためのレビューの時間

> 未来・理想の確認 {gray_bg}
(linked database)

> 現状の確認 {gray_bg}
(linked database)
```

実用上は **`assets/templates/weekly-review.md` の構造**を使う。

---

## Knowledge

- **data source URL**: `collection://8288e3ed-6a64-4bc5-882c-04b78b775fc1`
- **title プロパティ**: `Title`

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `Title` | title | ナレッジタイトル |
| `description` | text | 説明 |
| `Tags` | multi_select | 大量 (下記、130件超) |
| `Status` | select | `deprecated` / `in-progress` / `in-review` / `completed` |
| `Archived` | checkbox | `"__YES__"` / `"__NO__"` |
| `Frequently Access` | checkbox | よく見るもの |
| `is leaning note` | checkbox | **typo: leaning (learning ではない)** |
| `userDefined:URL` | url | 外部 URL (表示名は `URL`) |
| `Related Knowledge` | relation → Knowledge (self) | 関連ナレッジ |
| `☑️ Tasks DB` | relation → Tasks | 関連タスク (絵文字込み) |
| `Created` | created_time (readOnly) | |
| `Updated` | last_edited_time (readOnly) | |

### Tags の既存値 (multi_select、頻出のみ抜粋)

技術系: `python`, `JavaScript`, `TypeScript`, `Ruby`, `Rails`, `React`, `Frontend`, `backend`, `DB`, `postgreSQL`, `mongodb`, `Big Query`, `Redshift`, `AWS`, `Docker`, `git`, `vim`, `vscode`, `Linux`, `mac`, `windows`, `HTML`, `CSS`, `tailwindcss`, `styled-components`, `emotion`, `Material-UI`, `mui`, `Prisma`, `GraphQL`, `Web Accessibility`, `a11y`, `OOP`, `Design Pattern`, `Clean Architecture` (など)

業務/組織系: `smarthr`, `Scrum`, `OKR`, `交渉術`, `360度評価・フィードバック`, `QA`, `CS`, `HR Analytics`, `New Relic`, `Incident`, `dashboard`, `monitoring`

AI/ツール系: `AI`, `Prompt Engineering`, `ChatGPT`, `claude`, `MCP`, `Cursor`

その他: `book`, `Favorite`, `read again`, `read it`, `bookmark`, `Like`, `template`, `Learning Notes`, `work_log`, `working log`, `wip`, `trouble-shooting`, `手順`, `Stock-doc`, `flow-doc`, `knowledge`, `memo`, `Memo`

> 完全リストはNotion本体で確認。新規Tagの追加が必要な場合はユーザーに確認すること。

### Status の選択肢

- `deprecated`
- `in-progress`
- `in-review`
- `completed`

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| All | `https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=a9c198d418be4495a4ecd6afa9a0c759` | 全 Knowledge (Archived 除く) |
| Learning Notes | `https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=ea132f5d391547ceb92ed14d93aa9452` | `is leaning note = true` の学習ノート |
| Frequently Access | `https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=32f3342a60044f4692e2ff73e009d45d` | `Frequently Access = true` |
| SmartHR | `https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=317d4af9764f807cbc71000c6baffa94` | Tag = smarthr |
| Favorites | `https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=48a1ae0d7b3b4181b60508f5c00db84a` | Tag = Favorite |

### テンプレ "Cornell Note" の content 構造

2カラムレイアウト:

```
| Cue:                          | Note:                       |
| (Note が回答になる質問を書く)  | (学んだことを思い出して書く) |
|                               | - XX                        |
|                               | - XX                        |

## Summary
(何回か復習したら、ここにサマリを書く)
```

実用上は **`assets/templates/cornell-note.md`** を使う。
柔軟記録の場合はユーザーの内容に合わせた markdown 構造でOK。

---

## Links

- **data source URL**: `collection://88bb9d19-d08f-4983-ba5a-1282c1044894`
- **title プロパティ**: `Name`

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `Name` | title | リンクのタイトル |
| `userDefined:URL` | url | 記事 URL (表示名は `URL`) |
| `NotebookLM_URL` | url | NotebookLM の URL |
| `Category` | select | 下記 |
| `Label` | multi_select | 大量 (下記) |
| `Status` | select | `Closed` / `Finished` / `Reading` / `Ready to Start` |
| `Read Date` | date | 読んだ日。書き込みは `date:Read Date:start` |
| `AI summary` | text | AI サマリ |
| `Frequently Access` | checkbox | |

### Category の選択肢 (完全一致)

- `Technology`
- `Design`
- `Business`
- `Document Writing`
- `General`
- `GIS`
- `ai`

### Label の頻出既存値 (multi_select、抜粋)

技術: `React`, `TypeScript`, `JavaScript`, `Frontend`, `Database`, `PostgreSQL`, `Docker`, `AWS`, `GCP`, `Google Cloud`, `Nextjs`, `Rails`, `Ruby`, `Python`, `git`, `eslint`, `vim`, `CSS`, `Tailwindcss`, `Vitest`, `Playwright`, `Cypress`, `Jest`, `Storybook`, `Prisma`, `GraphQL`, `tRPC`, `MongoDB`, `Redis`, `Cloud Run`, `Cloud SQL`, `turborepo`, `pnpm`, `MCP`, `claude code`, `Cursor`, `Cline`, `Devin`, `n8n`, `OpenAI`, `AI Agent`, `Context Engineering`, `LLM`, `Performance`, `Web Accessibility`

ビジネス: `Product Owner`, `Product Manager`, `Career Design`, `Team Building`, `Facilitation`, `Leadership`, `Sales`, `Scrum`, `Agile`, `Retrospective`, `Mob Programming`, `Remote working`, `人事・労務`, `SmartHR`, `management`, `Stuff Engineer`, `CRE`

設計系: `Design Pattern`, `Clean Architecture`, `DDD`, `CQRS`, `Event Sourcing`, `OOP`, `Functional Programming`, `Microservices`, `Architecture Design`, `Onion Architecture`, `Program Design`, `UI Design`, `Design doc`

その他: `Favorite`, `Perfect`, `Habituation`, `Productivity`, `Podcast`, `youtube`, `news`, `link`, `週刊Life is beautiful`, `skills`

> 新規 Label 追加は可能だが、まず既存にないか確認すること。

### Status の選択肢

- `Ready to Start` (黄、未着手)
- `Reading` (赤、読書中)
- `Finished` (青、読了)
- `Closed` (紫、もう読まない)

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| Unread | `https://www.notion.so/shomrkm/e628b52db7d04ebfbde8d94671e1af5c?v=c0aa238bf08f4063803f9b0d3e48fd72` | Status が Finished/Closed 以外 |
| Favorites | `https://www.notion.so/shomrkm/e628b52db7d04ebfbde8d94671e1af5c?v=837e674fd53e43c69c02ce26ff59c154` | Label = Favorite |
| 2026 | `https://www.notion.so/shomrkm/e628b52db7d04ebfbde8d94671e1af5c?v=2e4d4af9764f809e9ee2000c4cadd050` | 2026 年読書 |
| 2025 | `https://www.notion.so/shomrkm/e628b52db7d04ebfbde8d94671e1af5c?v=163e20ce86064eef9e7eb2b46746d45c` | 2025 年読書 |

---

## Books

- **data source URL**: `collection://138dca21-71f3-4736-a01c-19d26ec2bf92`
- **title プロパティ**: `Name`

### プロパティ

| プロパティ名 | 型 | 注記 |
|---|---|---|
| `Name` | title | 書籍タイトル |
| `Author` | text | 著者 |
| `Category` | select | 下記 (typo あり) |
| `Status` | select | `Ready to Start` / `Reading` / `Finished` / `Close` |
| `Score /5` | select | ⭐絵文字 1〜5 (下記) |
| `Raad it again` | select | **typo: Raad (Read ではない)**。値: `✓` のみ |
| `Finished Date` | date | 読了日。書き込みは `date:Finished Date:start` |
| `Link` | url | 書籍リンク |

### Category の選択肢 (typo 注意)

- `Technical`
- `Novel`
- `Bisiness` **← Business ではなく Bisiness (原文ママ)**
- `Financial`
- `Essays`
- `Pratical Book` **← Practical ではなく Pratical (原文ママ)**
- `Other`

### Status の選択肢

- `Ready to Start`
- `Reading`
- `Finished`
- `Close` (Closed ではなく Close)

### Score /5 の選択肢

- `⭐️⭐️⭐️⭐️⭐️`
- `⭐️⭐️⭐️⭐️`
- `⭐️⭐️⭐️`
- `⭐️⭐️`
- `⭐️`

### 主要 View

| View 名 | URL | 用途 |
|---|---|---|
| All | `https://www.notion.so/shomrkm/b55e8b632f9248d79d9617dc06c52df4?v=81e75fcca3ff4deeb94645c50063c97b` | 全書籍 |
| Technical | `https://www.notion.so/shomrkm/b55e8b632f9248d79d9617dc06c52df4?v=4b21559374ce400585f68f5ff648cb22` | Category = Technical |
| Novel | `https://www.notion.so/shomrkm/b55e8b632f9248d79d9617dc06c52df4?v=7744290a1eb74532bd8d382d23b3d438` | Category = Novel |

---

## typo / 注意ポイントの早見表

書き込み時に**完全一致**で必要なため、絶対に修正しない:

| DB | 値 | 注意 |
|---|---|---|
| Tasks | `Routin` | Routine ではない |
| Tasks | `Someday / Maybe` | スラッシュ前後にスペース |
| Tasks | ` is_archived` | 先頭にスペース |
| [GTD] Review | DB 名 `Reveiw` | Review ではない |
| [GTD] Review | プロパティ `種別 ` | 末尾にスペース |
| Knowledge | `is leaning note` | learning ではない |
| Books | DB 内 `Bisiness` | Business ではない |
| Books | DB 内 `Pratical Book` | Practical ではない |
| Books | `Raad it again` | Read ではない |
| Books | Status `Close` | Closed ではない |
| Projects | プロパティ `🖥️ Knowledge` | 絵文字込み |

## 日付プロパティの書き込みパターン (重要)

Notion MCP の create-pages / update-page で日付を書くときは、**expanded property** を使う:

```jsonc
{
  "data_source_url": "collection://b2b78cf4-0ad7-4bba-bfde-3f2691b3be25",
  "properties": {
    "Name": "カフェテリアプランを考える",
    "task_type": "Today",
    "date:due_date:start": "2026-05-12",
    "date:due_date:is_datetime": 0
  }
}
```

- `date:<col>:start`: 開始日 (ISO-8601)
- `date:<col>:end`: 終了日 (range の場合のみ。単発の場合は省略)
- `date:<col>:is_datetime`: `0` = 日付のみ、`1` = 日時

## relation プロパティの書き込みパターン

relation は関連ページの URL の配列 (JSON 文字列):

```jsonc
{
  "properties": {
    "project": "[\"https://www.notion.so/2f2d4af9764f807ea1e0d0d988c7587a\"]"
  }
}
```

ただし MCP tool によっては配列を直接受け取る場合もあるので、tool のスキーマに従う。

## multi_select の書き込みパターン

```jsonc
{
  "properties": {
    "Tags": ["AI", "claude", "Prompt Engineering"]
  }
}
```

または JSON 文字列。tool のスキーマに従う。
