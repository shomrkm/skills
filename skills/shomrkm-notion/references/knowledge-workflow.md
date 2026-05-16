# knowledge-workflow.md — Knowledge DB への記録

`references/databases.md` の Knowledge セクションを先に Read していること。

ユーザーの方針: **柔軟に記録**。Cornell Note 強制ではない。

## 1. このワークフローのトリガー

ユーザーがこれらの語を含むとき:

- 「Knowledge にまとめる」「Knowledge に追加」「ナレッジ化」
- 「学習ノート」「Cornell Note」「Learning Note」
- 「(技術トピック) について整理しておきたい」
- 「(知見) をストックしたい」

「ささっと記録」「Daily Log」「今日の気づき」なら `lifelog-workflow.md` 側 (出来事系)。**体系的・再利用したい知識**は Knowledge。

## 2. AskUserQuestion で必ず確認すること

1. **`Title`**: 内容から候補を提示 (ユーザーが明示してなければ)
2. **`Tags`**: 内容から既存タグ候補を 3-5 個提示し選択させる
   - 例: 「Claude Code の hooks」→ 候補 `claude` + `AI` + `Productivity` + `手順`
3. **`description`**: 一行要約。AI が下書きして確認させる方式でOK
4. **構造**: Cornell Note を使う?それとも内容に合わせた構造?
   - 簡単な手順書なら箇条書き
   - 概念の理解なら Cornell Note (Cue/Note/Summary)
   - トラブルシューティングなら問題/原因/解決
5. **`is leaning note`** (typo そのまま): 学習ノートとしてマークするか
6. **`Status`**: デフォルト `in-progress`、完成しているなら `completed`

### Related Knowledge の提案

内容から関連がありそうな既存 Knowledge を `notion-search` で検索し、候補をユーザーに提示して relation を張るか確認。これは任意なので、ユーザーが「いい」と言えばスキップ。

## 3. 書き込み手順

### 標準的な記録 (柔軟構造)

```jsonc
{
  "parent": { "data_source_id": "collection://8288e3ed-6a64-4bc5-882c-04b78b775fc1" },
  "pages": [{
    "properties": {
      "Title": "Claude Code Hooks の基本",
      "description": "PostToolUse など各イベントで実行する hook の書き方",
      "Tags": ["claude", "AI", "手順"],
      "Status": "in-progress",
      "is leaning note": "__YES__"  // typo そのまま
    },
    "content": "(内容に合わせた markdown)"
  }]
}
```

### Cornell Note 形式

content に `assets/templates/cornell-note.md` を展開し、AI が会話内容から:
- **Cue**: 「この内容を思い出すための問い」を 2-3 個生成
- **Note**: 学んだ内容を箇条書きで整理
- **Summary**: 一段落で要約

を埋める。ユーザーに最終的な構造を確認してから書き込む。

## 4. Tags の選び方

`references/databases.md` の Knowledge Tags リストを参照。**新規 Tag は極力作らない**:

- 技術なら: `python`, `JavaScript`, `TypeScript`, `React`, `Ruby`, `Rails`, `Frontend`, `backend`, `AWS`, `Docker`, `git` など
- AI/ツール: `AI`, `claude`, `MCP`, `Prompt Engineering`, `ChatGPT`, `Cursor`
- 業務: `smarthr`, `Scrum`, `OKR`, `QA`, `CS`
- 用途: `book`, `Learning Notes`, `wip`, `trouble-shooting`, `手順`, `Stock-doc`, `flow-doc`, `Favorite`, `Like`

新規 Tag が本当に必要なら AskUserQuestion で「既存にないですが、新規で追加しますか?」と確認。

## 5. 典型例

### 例1: 「Notion API の prompt caching まとめておきたい」

AskUserQuestion:
- Title: 候補「Notion API の Prompt Caching」「Anthropic Prompt Caching」のどっち or その他
- Tags: 候補 `AI` + `claude` + `Stock-doc` + `Prompt Engineering`
- 構造: Cornell Note? シンプル箇条書き? トラブルシューティング型?
- is leaning note: true?
- Status: in-progress?

OK → create-pages

### 例2: 「Cornell Note で Notion API のページ作成ルール」

AskUserQuestion:
- Title 確認
- Tags 提案
- Status

content には `cornell-note.md` テンプレを展開し、AI が Cue/Note/Summary を会話から下書きしてユーザーに見せて承認後書き込み。

## 6. 避けるべき間違い

- ❌ `is learning note` と書く (正しくは `is leaning note`)
- ❌ Tags を勝手に新規追加する (既存優先)
- ❌ Knowledge に時系列の出来事を書く (それは LifeLog)
- ❌ Cornell Note を必ず使おうとする (ユーザーは柔軟記録派)
- ❌ Title をユーザー発言のママでつける (短く要約したタイトルを提案する)

## 7. 出力フォーマット

```
✓ Knowledge に追加しました
  - Title: Claude Code Hooks の基本
  - Tags: claude, AI, 手順
  - Status: in-progress
  - is leaning note: true
  - URL: https://www.notion.so/...
```
