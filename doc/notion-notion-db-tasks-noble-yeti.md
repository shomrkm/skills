# Plan: shomrkm-notion スキル作成

## Context

ユーザー shomrkm は Notion を GTD ベースで運用しており、7つの主要DB（Tasks / Projects / LifeLog / [GTD] Review / Knowledge / Links / Books）を日常的に使っている。現状、Notion 操作のたびにDBのID・プロパティ・運用ルールを Claude に毎回説明する必要があり、特に以下の判断が毎回必要になっている:

- 「明日やる」と言ったときに Tasks DB のどのプロパティ (`task_type=Next Actions`, `due_date`) に値を入れるか
- プロジェクトに属するタスクなのか、独立タスクなのかの判別
- Knowledge / LifeLog / Links のどれに記録すべきか
- Cornell Note や Daily Log テンプレの構造をどう埋めるか
- 振り返りや週次レビューでどのビューを参照するか

これを skill 化することで、ユーザーの「タスク追加して」「Daily Log 書いて」「未読のLinks見せて」といった短い指示から、shomrkm の GTD 運用ルールに沿った Notion 操作を一発で実行できる状態を目指す。

ユーザーへの確認結果 (確定事項):
- **粒度**: 単一の統合スキル `shomrkm-notion`
- **重視するワークフロー**: ①タスク追加・ステータス更新、②Daily Log / 週次レビュー、③Knowledge / Links / Books 記録、④横断クエリ・検索 (全部)
- **デフォルト挙動**: 不明点は毎回 AskUserQuestion で確認 (推測しない)
- **Knowledge 記録**: 柔軟に。Cornell Note にこだわらず内容に応じて構造を選ぶ

---

## スキル設計概要

### ファイル構成

```
~/.claude/skills/shomrkm-notion/
├── SKILL.md                          # メインの導線・全体ルール (~300行)
├── references/
│   ├── databases.md                  # 7つのDBのID・プロパティ・運用ルール一覧
│   ├── tasks-workflow.md             # Tasks/Projects への追加・更新・GTDフロー
│   ├── lifelog-workflow.md           # Daily Log テンプレと記録ルール
│   ├── review-workflow.md            # [GTD] Review への週次レビュー記録
│   ├── knowledge-workflow.md         # Knowledge への Cornell Note / 柔軟記録
│   ├── reading-workflow.md           # Links / Books の追加・ステータス更新
│   └── query-patterns.md             # よく使う検索・横断クエリパターン
└── assets/
    └── templates/
        ├── cornell-note.md           # Cornell Note 構造のテキストテンプレ
        ├── daily-log.md              # @Today Daily Log の構造
        └── weekly-review.md          # 週次レビューの構造
```

### スキル動作のフロー

1. ユーザーが「明日XXやる」「Daily Log 書いて」「未読のLinks見せて」など Notion 操作を依頼
2. SKILL.md のトリガー判定で「shomrkm の Notion 操作」と認識
3. SKILL.md 本文の **ルーティングテーブル** に従って、該当 workflow の references ファイルを Read
4. 各 reference にある「不明点リスト」を AskUserQuestion で確認 (推測しない)
5. Notion MCP で書き込み or 読み取りを実行
6. 結果をユーザーに端的に報告

### SKILL.md の主要セクション

- **frontmatter**: トリガー強化のため description は「shomrkm の Notion ワークスペース (Tasks/Projects/LifeLog/GTD Review/Knowledge/Links/Books) を GTD ベースで操作する。『タスク追加』『Daily Log』『週次レビュー』『未読を見せて』『Knowledge にまとめて』など、shomrkm の Notion に触れる依頼は必ずこのスキルを使う」と明記
- **コア原則**:
  - 推測しない (不明点は AskUserQuestion)
  - DB ID は references/databases.md を必ず引く (ハードコードしたID表記はSKILL.mdに置かない、references に集約)
  - 日付は今日基準で相対表現を解決 (例: 「明日」→`2026-05-12`)。userEmail コンテキストから currentDate を必ず参照
  - GTD 用語の整合性 (Today / Next Actions / Someday/Maybe / Wait for / Routin) を守る
- **ルーティングテーブル**: 依頼内容 → どの reference を読むか の対応表
- **共通ヘルパー**: notion-create-pages / notion-update-page / notion-search の使い方の最小例 (詳細は reference 側)

### references/databases.md のフォーマット

各DBについて:
- DB の URL と data source URL (collection://...)
- title プロパティ名 (`Name`, `name`, `名前`, `Title` がDBごとに異なる)
- 必須プロパティと選択肢の正確な値 (例: `task_type` の値は完全一致が必要)
- relation 先のDBと、relation を張るときの典型パターン
- View URL とその用途 (In-Progress, By Project, Unread など)

### 各 workflow reference の構造

共通フォーマット:
1. **このワークフローのトリガー** (ユーザーがどう言ったらこの workflow か)
2. **AskUserQuestion で必ず確認すること** のリスト
3. **書き込み手順** (notion-create-pages / notion-update-page の具体例)
4. **典型的な値の例** (今日のユーザー操作で頻出のパターン)
5. **避けるべき間違い** (過去に間違えやすかった点)

---

## 主要ファイルと内容の要点

### SKILL.md (新規作成 — `~/.claude/skills/shomrkm-notion/SKILL.md`)

```yaml
---
name: shomrkm-notion
description: shomrkm の Notion ワークスペースを GTD ベースで効率的に操作する。Tasks DB / Projects DB / LifeLog / [GTD] Review / Knowledge / Links / Books の7つの主要DBへの追加・更新・検索を担う。「タスク追加して」「明日XX」「Today に入れて」「Daily Log」「週次レビュー」「Knowledge にまとめて」「未読のLinks」「読書記録」など、shomrkm の Notion を触る依頼は必ずこのスキルを使うこと。DB ID やプロパティ名、GTD のステータス値は references/databases.md に正確に書かれているので、ハルシネーションせず必ず参照する。
---
```

本文には以下を含める:
- コア原則 (推測しない、references を必ず Read、今日基準の日付解決)
- **ルーティングテーブル** (下記)
- AskUserQuestion を使うべき場面の一般則
- 出力フォーマット (作成後は URL を必ず提示)

ルーティングテーブル (例):

| ユーザーの言葉 | 読むべきreference | DB |
|---|---|---|
| タスク追加、明日やる、Today に、Next Actions に | tasks-workflow.md | Tasks |
| プロジェクト作成、Project に追加 | tasks-workflow.md | Projects |
| Daily Log、今日の振り返り、学んだこと | lifelog-workflow.md | LifeLog |
| 週次レビュー、今週の振り返り | review-workflow.md | [GTD] Review |
| Knowledge にまとめる、ナレッジ化、Cornell Note | knowledge-workflow.md | Knowledge |
| 読んだ記事、後で読む、Links に追加 | reading-workflow.md | Links |
| 本の記録、読書記録 | reading-workflow.md | Books |
| 今日のタスクは、未読は、進行中のプロジェクトは | query-patterns.md | 横断 |

### references/databases.md (新規作成)

各DBのスキーマを正確に保存。これが**スキル全体の真実の源** (SoT):

- **Tasks DB**: data source `collection://b2b78cf4-0ad7-4bba-bfde-3f2691b3be25`, DB URL `https://www.notion.so/66adfdd3961e48c8a533ec01dbf24478`
  - title プロパティ: `Name`
  - 必須: `task_type` (select: `Today` / `Next Actions` / `Someday / Maybe` / `Wait for` / `Completed` / `Routin` / `Closed`), `due_date` (date)
  - 任意: `working date`, `completed_at`, `description` (text), `project` (relation → Projects), `Knowledge` / `tasks` (relation → Knowledge)
  - 主要 view: In-Progress `https://www.notion.so/.../?v=259d4af9764f8067b2e8000c4d28c973`, Board, By Project, All
  - GTD 注意: `Someday / Maybe` はスラッシュ前後にスペース有り (完全一致必須)

- **Projects DB**: `collection://19b3bba9-342b-46c3-a3ce-c7b0b8ee468a`
  - title: `name`
  - `status` (status型: `Not started` / `planning` / `in-progress` / `completed` / `closed`)
  - `importance` (select: `⭐⭐⭐⭐⭐` 〜 `⭐`)
  - relation: `task` (→ Tasks), `🖥️ Knowledge` (→ Knowledge)

- **LifeLog**: `collection://3587b931-adde-44bb-bf56-4872c50a5ce8`
  - title: `Name`
  - `Date` (date), `Tags` (multi-select), `URL` (url), `AI summary` (text)
  - Tags 既存値: `cook`, `Favorite`, `smarthr`, `Travel`, `AI`, `Prompt Engineering`, `CSS`, `メルマガ`, `週次まとめ`, `Memo`
  - テンプレ "@Today Daily Log" の構造を内包

- **[GTD] Reveiw DB**: `collection://2c90ee4d-a0dd-4c6a-a824-ab83cb73bb9f` (typo はそのまま)
  - title: `名前` (日本語)
  - `種別 ` (末尾スペース有り、値: `週次レビュー`), `実施日` (date), `振り返り対象期間` (date range), `今週の成長度` / `今週の挑戦度` (number 0-10)

- **Knowledge**: `collection://8288e3ed-6a64-4bc5-882c-04b78b775fc1`
  - title: `Title`
  - `description` (text), `Tags` (multi-select 大量), `Status` (`deprecated` / `in-progress` / `in-review` / `completed`), `Archived`, `Frequently Access`, `is leaning note` (checkbox; note: typo "leaning" not "learning")
  - relation: `Related Knowledge` (self), `☑️ Tasks DB` (→ Tasks)
  - Tags は references に全リストを保持 (130件超)

- **Links**: `collection://88bb9d19-d08f-4983-ba5a-1282c1044894`
  - title: `Name`
  - `URL` (`userDefined:URL`), `NotebookLM_URL`, `Category` (select: 7値), `Label` (multi-select 大量), `Status` (`Closed` / `Finished` / `Reading` / `Ready to Start`), `Read Date`, `AI summary`, `Frequently Access`

- **Books**: `collection://138dca21-71f3-4736-a01c-19d26ec2bf92`
  - title: `Name`
  - `Author`, `Category` (`Technical` / `Novel` / `Bisiness`(原文ママ) / `Financial` / `Essays` / `Pratical Book`(原文ママ) / `Other`), `Status`, `Score /5` (⭐絵文字), `Finished Date`, `Link`, `Raad it again` (原文ママ)

注: スペル/typo (`Reveiw`, `Bisiness`, `Pratical`, `Raad`, `leaning`, `種別 `(末尾スペース)) は Notion 側の現状なので、references でも**そのまま**保持して書き込み時に正確にマッチさせる。

### references/tasks-workflow.md (新規作成)

- トリガー: 「タスク」「やる」「Today」「Next Actions」「明日」「TODO」など
- AskUserQuestion で確認すべきこと:
  - task_type は何か (推測しない)
  - due_date はいつか (「明日」等の相対表現は今日基準で解決して提示し確認)
  - プロジェクトに属するか (Projects DB のどれか)
  - description を書くか
- 書き込み手順:
  - `mcp__notion__notion-create-pages` で `data_source_url: collection://b2b78cf4-...` を指定
  - properties: `Name`, `task_type`, `date:due_date:start` の expanded property を使う
- ステータス更新: `notion-update-page` で `task_type` を `Completed` / `Closed` に変える例
- 「Today に上げる」=`task_type` を `Today` に変更、`due_date` を今日に揃える

### references/lifelog-workflow.md (新規作成)

- Daily Log テンプレ (ハイライト / 詰まった点 / 学び (Fact-Because-Next) / 明日の一手) を content として作成
- AskUserQuestion で確認:
  - 今日の Date は今日でよいか (default: 今日)
  - Tags はどれか (既存リストから選択 or 新規)
  - URL があるか

### references/review-workflow.md (新規作成)

- 既存テンプレ "2023 週次レビュー 00" の構造を踏襲して新規ページ作成
- 実施日 / 振り返り対象期間 / 成長度 / 挑戦度 を AskUserQuestion で確認
- 集計サポート: 今週完了したタスクを Tasks DB から検索して表示する補助動作も含める

### references/knowledge-workflow.md (新規作成)

- 「柔軟に記録」のため、Cornell Note 強制はしない
- ユーザーがテンプレを希望したときのみ assets/templates/cornell-note.md を展開
- Tags は AskUserQuestion で既存リストから複数選択 (新規 Tag が必要なら確認)
- `is leaning note` (typo そのまま) のチェックは AskUserQuestion で確認
- Related Knowledge の relation は、内容から AI が候補を提案 → ユーザー承認

### references/reading-workflow.md (新規作成)

- Links: URL を渡されたら自動で fetch + AI summary 生成 (ユーザーが希望した場合)。Category, Label, Status を確認
- Books: タイトル / 著者 / Category / Status / Score を確認

### references/query-patterns.md (新規作成)

頻出クエリ:
- 「今日のタスク」→ Tasks DB "In-Progress" view を `notion-query-database-view` で取得
- 「進行中のプロジェクト」→ Projects "In Progress" view
- 「未読のLinks」→ Links "Unread" view
- 「最近のKnowledge」→ Knowledge "All" view を created desc で取得
- 「今週の Daily Log」→ LifeLog を Date でフィルタ

各クエリの view_url を references に直書きしておく。

### assets/templates/

- `cornell-note.md`: 既存テンプレを markdown 化
- `daily-log.md`: ハイライト/詰まった点/学び(Fact-Because-Next)/明日の一手
- `weekly-review.md`: 既存ページ構造

これらは Notion ページ作成時の `content` パラメータに展開する素材として使う。

---

## 既存資産の利用

- **Notion MCP ツール**: `mcp__notion__notion-fetch`, `notion-search`, `notion-create-pages`, `notion-update-page`, `notion-query-database-view`, `notion-update-data-source`
- **既存スキル参考**: `Notion:create-task`, `Notion:create-page`, `Notion:database-query` などが既に存在するが、これらは汎用で shomrkm 専用ではない。本スキルは「shomrkm のDB IDとGTD運用ルールを内包した特化版」として位置づけ、これらのスキルとは併用しない (混乱を避けるため SKILL.md で明示)
- **skill-creator のテスト/評価フロー**: 作成後、evals/evals.json に 2-3 個のテストケース (例: 「明日カフェテリアプラン考えるをタスクに追加して」「今日の学びを Daily Log に書きたい」「未読のLinks見せて」) を用意し、`scripts/aggregate_benchmark.py` と `eval-viewer/generate_review.py` で動作確認・iteration を回す

---

## 修正すべきファイル (=新規作成)

すべて新規。既存ファイルへの変更は無し。

1. `~/.claude/skills/shomrkm-notion/SKILL.md`
2. `~/.claude/skills/shomrkm-notion/references/databases.md`
3. `~/.claude/skills/shomrkm-notion/references/tasks-workflow.md`
4. `~/.claude/skills/shomrkm-notion/references/lifelog-workflow.md`
5. `~/.claude/skills/shomrkm-notion/references/review-workflow.md`
6. `~/.claude/skills/shomrkm-notion/references/knowledge-workflow.md`
7. `~/.claude/skills/shomrkm-notion/references/reading-workflow.md`
8. `~/.claude/skills/shomrkm-notion/references/query-patterns.md`
9. `~/.claude/skills/shomrkm-notion/assets/templates/cornell-note.md`
10. `~/.claude/skills/shomrkm-notion/assets/templates/daily-log.md`
11. `~/.claude/skills/shomrkm-notion/assets/templates/weekly-review.md`
12. `~/.claude/skills/shomrkm-notion/evals/evals.json` (テストケース)

---

## 検証 (Verification)

実装後、以下で end-to-end 動作確認する。

### 機能テスト (skill-creator の test loop)

evals/evals.json に最低3ケース:
1. **タスク追加**: 「明日のカフェテリアプランを考えるってタスク、Todayに入れて」
   - 期待: Tasks DB に `task_type=Today`, `due_date=明日`, `Name=カフェテリアプランを考える` で作成。description は AskUserQuestion で確認 (空でも可)
2. **Daily Log**: 「今日学んだこと記録したい。XX について新しく分かった」
   - 期待: LifeLog DB に Date=今日, Tags=AskUserQuestion で確認, Fact-Because-Next を content に含めて作成
3. **横断クエリ**: 「今日やるタスク見せて」
   - 期待: Tasks DB の In-Progress view を query して、`task_type=Today` の行を整形して表示
4. **Knowledge 記録**: 「Notion API の prompt caching についてまとめておきたい」
   - 期待: Knowledge DB に作成。Cornell Note を使うか柔軟構造かを AskUserQuestion で確認

各ケースについて `scripts/aggregate_benchmark.py` でグレーディングし、`eval-viewer/generate_review.py` で結果を可視化してユーザーレビュー。

### 手動確認項目

- [ ] スキルが「タスク追加して」「Daily Log」など主要トリガーで自動起動するか (description 最適化が必要なら `scripts/run_loop.py` で description optimization を回す)
- [ ] DB ID やプロパティ名のtypo (`Reveiw`, `Bisiness`, `leaning`, `Raad`, `種別 `) が references の通り正確に書き込まれるか
- [ ] 「明日」「今週」など相対日付が今日 (`2026-05-11`) 基準で正しく解決されるか
- [ ] AskUserQuestion が暴走せず、本当に不明な点だけに絞られているか (1依頼あたり最大2-3問)
- [ ] 作成後に Notion URL が返ってきて、ユーザーがクリックして開けるか

### Notion 側の確認 (実 DB を汚さない方法)

iteration 中はテスト用に Inbox 的なエリアに作成するか、作成後すぐ削除する運用にして本番データを汚さない。必要なら一時的なテスト用DBを作って差し替える案も検討。

---

## 進め方の提案

1. このプランを承認いただいたら ExitPlanMode して実装フェーズに入る
2. SKILL.md と references/databases.md (SoT) を先に書く
3. 4つの主要 workflow reference を書く
4. evals/evals.json を書いて、skill-creator の test loop を1回実行
5. ユーザーレビュー後、改善 iteration
6. 最後に description optimization を回してトリガー精度を上げる
