# tasks-workflow.md — Tasks DB / Projects DB の操作

`references/databases.md` の Tasks DB / Projects DB セクションを先に Read していること。

## 1. このワークフローのトリガー

ユーザーがこれらの語を含むとき:

- 「タスク追加」「タスクを作る」「TODO に追加」
- 「明日 / 今日 / 来週 XX やる」
- 「Today に」「Next Actions に」「Wait for に」「Someday に」
- 「Routin に」(ルーティン)
- 「完了にする」「Completed」「Closed にする」「Archive」
- 「プロジェクト作成」「Project に追加」「プロジェクト化」

## 2. AskUserQuestion で必ず確認すること

### 新規タスク追加時 (推測禁止)

ユーザーが明示していない場合、以下を **AskUserQuestion 一発で**まとめて聞く:

1. **task_type**: `Today` / `Next Actions` / `Someday / Maybe` / `Wait for` / `Routin` のどれか
   - 「明日やる」と言われたら `Today` か `Next Actions` か微妙 → 確認する
2. **due_date**: 「明日」「来週中」などの相対表現があれば**今日基準で解決して確認**
   - 例: 「明日」と言われたら currentDate (例: 2026-05-11) を見て「`2026-05-12` でよいか」と聞く
3. **project**: 既存のプロジェクトに紐付けるか、独立タスクか
   - 既存プロジェクトに紐付けるなら、まず Projects DB の In Progress view を query して候補を提示
4. **description**: ユーザーがタスクの中身を一言で述べていない場合のみ確認 (任意でOK)

すでにユーザーが明示した項目は再確認しない。

### ステータス更新時

- 「完了」と言われたら `task_type = Completed` + `date:completed_at:start = 今日` がデフォルト
- 「もうやらない」「Closed」と言われたら `task_type = Closed`
- 対象タスクが曖昧な場合は AskUserQuestion で確認 (タイトルキーワードや In-Progress view から候補提示)

## 3. 書き込み手順

### 新規タスク作成

`mcp__notion__notion-create-pages` で:

```jsonc
{
  "parent": { "data_source_id": "collection://b2b78cf4-0ad7-4bba-bfde-3f2691b3be25" },
  "pages": [{
    "properties": {
      "Name": "カフェテリアプランを考える",
      "task_type": "Today",
      "date:due_date:start": "2026-05-12",
      "date:due_date:is_datetime": 0,
      "description": "(ユーザーが明示した場合のみ)"
      // project relation はあれば追加: "project": ["https://www.notion.so/<project-page-id>"]
    }
  }]
}
```

Notion MCP の実際の引数構造は tool スキーマに従う (`data_source_url` か `parent.data_source_id` のいずれか、tool 仕様による)。

### ステータス更新

`mcp__notion__notion-update-page` で:

```jsonc
{
  "page_id": "https://www.notion.so/<task-page-url>",
  "properties": {
    "task_type": "Completed",
    "date:completed_at:start": "2026-05-11",
    "date:completed_at:is_datetime": 0
  }
}
```

### 「Today に上げる」

既存タスクの `task_type` を `Today` に変更し、`due_date` を今日に揃える:

```jsonc
{
  "properties": {
    "task_type": "Today",
    "date:due_date:start": "2026-05-11",
    "date:due_date:is_datetime": 0
  }
}
```

## 4. プロジェクトの新規作成

ユーザーが「プロジェクト作る」と言ったとき、Tasks DB ではなく Projects DB に作成する。

```jsonc
{
  "parent": { "data_source_id": "collection://19b3bba9-342b-46c3-a3ce-c7b0b8ee468a" },
  "pages": [{
    "properties": {
      "name": "(プロジェクト名 — title プロパティは小文字 name)",
      "status": "planning",
      "importance": "⭐⭐⭐"
    }
  }]
}
```

AskUserQuestion で確認:
- `status`: `Not started` / `planning` / `in-progress` のどれでスタートするか
- `importance`: ⭐ の数

## 5. 典型例

### 例1: 「明日カフェテリアプラン考える、Today で」

1. currentDate = `2026-05-11`、つまり明日は `2026-05-12`
2. `task_type=Today` はユーザーが明示済み
3. AskUserQuestion で `description` の有無、`project` の紐付けを確認
4. create-pages

### 例2: 「『XX を調べる』完了にして」

1. `notion-search` または `notion-query-database-view` (In-Progress view) で対象タスクを特定
2. 候補が複数なら AskUserQuestion で選択
3. update-page で `task_type=Completed`、`completed_at=今日`

### 例3: 「Claude Code 設定プロジェクトに『XX 試す』追加」

1. Projects DB から「Claude Code 設定」を特定 (search または query-database-view)
2. ユーザーが既存プロジェクトを指している→ relation を張る
3. AskUserQuestion で task_type / due_date を確認
4. create-pages で project relation を含めて作成

## 6. 避けるべき間違い

- ❌ `task_type` を勝手に決める (推測禁止)
- ❌ `Routine` と書く (正しくは `Routin`)
- ❌ `Someday/Maybe` (スペース無し) と書く (正しくは `Someday / Maybe` スペース有り)
- ❌ `Tasks DB` のタイトルプロパティを `name` (小文字) と書く (正しくは `Name` 大文字)
- ❌ Projects DB のタイトルを `Name` と書く (正しくは `name` 小文字)
- ❌ 日付を `"due_date": "2026-05-12"` のように生プロパティで書く (正しくは expanded `date:due_date:start`)
- ❌ ユーザーが「Today」と言ったのに `due_date` を聞かない (Today なら通常 due_date も今日)
- ❌ 完了時に `completed_at` を書き忘れる

## 7. 出力フォーマット

成功時:

```
✓ Tasks DB に追加しました
  - Name: カフェテリアプランを考える
  - task_type: Today
  - due_date: 2026-05-12 (明日)
  - project: なし
  - URL: https://www.notion.so/35bd...
```

ステータス更新時:

```
✓ 完了にしました
  - Name: XX を調べる
  - task_type: Next Actions → Completed
  - completed_at: 2026-05-11
  - URL: https://www.notion.so/...
```

## 8. Projects DB のページ本文構造

Projects DB の各ページは以下のテンプレート構造を持つ。**セクションごとに書き手が決まっている**ので、書き込み時は担当セクション以外に触らないこと。

| セクション | 書き手 | 内容 |
|---|---|---|
| `## Progress Summary` | AI (自動更新) | 進捗の集約サマリ。全置換してよい唯一のセクション |
| `## As-Is` | 人間 | 現状認識 |
| `## To-Be` | 人間 | 目指す状態 |
| `## ToDos` | inline database | Tasks DB のビュー。直接編集しない |
| `## References` | 人間 / AI | 参考リンク。追記のみ |

各見出しの直下に区切り線 `---` がある。

### Progress Summary の更新手順

`mcp__notion__notion-update-page` の `update_content` を使い、サマリ本文だけを差し替える。

**サマリ本文は必ず `> 🤖 自動更新: <YYYY-MM-DD>` の引用行から始める。** この行が置換のアンカーになる。

#### 初回 (Progress Summary が空のとき)

空セクションは空行ではなく `<empty-block/>` なので、`## Progress Summary\n---\n\n## As-Is` のような文字列はマッチしない。**次のセクションの見出し行をアンカーにして、その手前に挿入する。**

```jsonc
{
  "command": "update_content",
  "content_updates": [{
    "old_str": "## As-Is\n---\n- (As-Is の最初の1行をそのまま書く)",
    "new_str": "> 🤖 自動更新: 2026-07-25\n\n**進捗**: ...\n\n## As-Is\n---\n- (同じ1行)"
  }]
}
```

As-Is が空の Project では代わりに `## Progress Summary\n---` をアンカーにし、その直後に挿入する。

#### 2回目以降 (既にサマリがあるとき)

`> 🤖 自動更新:` から最後の行までを丸ごと `old_str` に入れて差し替える。これで重複せず内容だけが入れ替わる (冪等)。

```jsonc
{
  "command": "update_content",
  "content_updates": [{
    "old_str": "> 🤖 自動更新: 2026-07-25\n**進捗**: ...(既存のサマリ全文)",
    "new_str": "> 🤖 自動更新: 2026-08-01\n**進捗**: ...(新しいサマリ全文)"
  }]
}
```

**必ず `notion-fetch` で現在の本文を取得してから `old_str` を組み立てる。** 記憶や前回の書き込み内容から組み立てると、実際の本文と1文字でも違えばマッチせず失敗する。

追記ではなく置換にする理由: サマリは最新状態のみを保持すべきで、履歴を溜めると肥大化して読まれなくなる。

### 避けるべき間違い

- ❌ `As-Is` / `To-Be` / `ToDos` を書き換える (人間の領域)
- ❌ Progress Summary に追記する (全置換が正しい。追記すると実行のたびにサマリが積み上がる)
- ❌ セクション見出しや `---` を消す (テンプレ構造を壊す)
- ❌ ページ本文を丸ごと置換する (`replace_content` は使わない。他セクションが消える)
- ❌ `## Progress Summary\n---\n\n## As-Is` をアンカーにする (空セクションは `<empty-block/>` でマッチしない)
- ❌ fetch せずに `old_str` を組み立てる (実際の本文と一致しないと失敗する)
