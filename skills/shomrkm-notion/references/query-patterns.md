# query-patterns.md — 横断クエリ・検索パターン

`references/databases.md` のビュー URL を併用すること。

## 1. このワークフローのトリガー

ユーザーがこれらの語を含むとき:

- 「今日のタスクは?」「Today に何がある?」「今日やること」
- 「未読の Links は?」「後で読む一覧」
- 「進行中のプロジェクトは?」「In Progress のプロジェクト」
- 「最近の Knowledge」「最近書いたメモ」
- 「今週の Daily Log」「先週の活動」
- 「(キーワード) で検索」「Notion から探して」

## 2. 主要クエリパターン

### 「今日のタスクは?」

Tasks DB の **In-Progress** view を query:

```
mcp__notion__notion-query-database-view
  view_url: https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=259d4af9764f8067b2e8000c4d28c973
```

このビューは「due_date が 1週間以内 OR task_type=Today、かつ未完了」。
取得後、クライアント側で `task_type=Today` だけにフィルタしてもよい。

### 「Next Actions は?」

同じく In-Progress view を取得後、`task_type=Next Actions` で client-side filter。

### 「未読の Links は?」

Links の **Unread** view:

```
view_url: https://www.notion.so/shomrkm/e628b52db7d04ebfbde8d94671e1af5c?v=c0aa238bf08f4063803f9b0d3e48fd72
```

`Status` が `Finished` / `Closed` 以外のもの。

### 「進行中のプロジェクト」

Projects の **In Progress** view:

```
view_url: https://www.notion.so/shomrkm/85f41c09281a44cb8883aa6ec0309402?v=bf395f58a7da4dc1ba7c83b0e01c7ea5
```

### 「最近の Knowledge」

Knowledge の **All** view (Archived 除く、Created desc):

```
view_url: https://www.notion.so/shomrkm/20b5ffeef46347f6991d7648f07de698?v=a9c198d418be4495a4ecd6afa9a0c759
```

### 「今週の Daily Log」

LifeLog の table view を query:

```
view_url: https://www.notion.so/shomrkm/43772e6fc21c4755b5c37a6ed77955a2?v=41f4b0017edd40a8862ad6d3600377dc
```

取得後、`Date` が今週 (例: 2026-05-04 〜 2026-05-10) の範囲のものだけ client-side filter。

### 「(キーワード) で検索」

ワークスペース全体: `mcp__notion__notion-search`
特定 DB 内: `mcp__notion__notion-search` の `data_source_url` 引数

```
mcp__notion__notion-search
  query: "Claude Code hooks"
  data_source_url: collection://8288e3ed-6a64-4bc5-882c-04b78b775fc1  // Knowledge 内
  page_size: 10
```

## 3. 結果整形

### 表形式 (タスク類)

```
| task_type | due_date | Name | project |
|---|---|---|---|
| Today | 2026-05-11 | Claude Code わいわいウィークの録画を見る | - |
| Today | 2026-05-11 | バグバッシュで出たバグを分析する | (m2) claude code... |
```

### 短いリスト (Links 等)

```
未読の Links (Top 10):
1. [Reading] Claude Code Hooks — Technology, claude code
   https://example.com/...
2. [Ready to Start] AI Agent パターン — ai, AI Agent
   https://example.com/...
```

## 4. AskUserQuestion を使う場面 (検索系)

- 結果が 20件超で多すぎる → 絞り込み条件 (Tag, 期間 など) を確認
- ユーザーの「最近」が曖昧 → 「1週間? 1ヶ月?」と確認
- どの DB を見るべきか曖昧 → 候補提示

検索系は AskUserQuestion を**控えめに**。まず結果を出して、必要なら絞り込みを提案する方が早い。

## 5. 補助: GTD レビュー用の集計

週次レビュー時の補助動作:

### 今週完了したタスク

Tasks の All view から `task_type=Completed` かつ `completed_at` が振り返り対象期間内のものを抽出:

```
view_url: https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=243175a531f54dbea1a40e279b565eef
```

(All view は task_type が Today/Next Actions/Someday/Wait for/Routin のみ表示するので、Completed を取るには別途取得が必要 — search または別 view を使う)

### 進行中プロジェクトの進捗

Projects In Progress view → `progress` rollup の値を見せる

### 今週の Daily Log 件数と内容

LifeLog の table view から Date 範囲フィルタ

## 6. 避けるべき間違い

- ❌ 大量に結果を返す (デフォルト Top 10 程度に絞る)
- ❌ 全プロパティをダンプする (Name, status, due_date など主要なものだけ)
- ❌ user に URL を渡し忘れる (各行に Notion URL を含める)
- ❌ query-database-view の view_url を間違える (`v=` の後の id を databases.md で正確に確認)

## 7. 出力フォーマット例

```
今日のタスク (task_type=Today, 4件):

| due | Name | project |
|---|---|---|
| 2026-05-11 | Claude Code わいわいウィークの録画を見る | - |
| 2026-05-11 | バグバッシュで出たバグを分析し skills を更新する | (m2) claude code... |
| 2026-05-11 | hooks を使って skill や rule を自動でアップデート | (m2) claude code... |
| 2026-05-16 | カフェテリアプラン考える | - |

URL: https://www.notion.so/shomrkm/66adfdd3961e48c8a533ec01dbf24478?v=259d4af9764f8067b2e8000c4d28c973
```
