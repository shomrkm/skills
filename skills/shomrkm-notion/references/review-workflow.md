# review-workflow.md — [GTD] Reveiw DB への週次レビュー

`references/databases.md` の [GTD] Reveiw DB セクションを先に Read していること。

⚠️ DB 名 typo: `Reveiw` (Review ではない、原文ママ)。

## 1. このワークフローのトリガー

ユーザーがこれらの語を含むとき:

- 「週次レビュー」「今週の振り返り」「Weekly Review」
- 「振り返りページ作る」「レビューのページ」
- ユーザーが week ending の振り返りをしたい

## 2. AskUserQuestion で必ず確認すること

1. **`名前` (タイトル)**: デフォルト形式を提案 (例: `2026 W19 週次レビュー`)
2. **`実施日`**: 今日でよいか確認 (currentDate)
3. **`振り返り対象期間`**: 今週月曜〜日曜が一般的。currentDate から計算して提示
   - 例: currentDate = 2026-05-11 (月) → 振り返り対象期間 start=2026-05-04 end=2026-05-10 (先週ぶん)
   - 「月曜開始の週次レビューですか? それとも別の週の区切りですか?」
4. **`今週の挑戦度`** (0-10): ユーザーが評価したい数値
5. **`今週の成長度`** (0-10): 同上

挑戦度・成長度は AskUserQuestion で数値直接入力を求める (選択肢にしづらいので「Other」で入力)。

## 3. 集計サポート (補助動作)

新規レビューページを作る前に、ユーザーが希望すれば以下の補助情報を提示:

### 今週完了したタスクの集計

`mcp__notion__notion-query-database-view` で Tasks DB の `All` view を query し、
クライアント側で `task_type=Completed` かつ `completed_at` が振り返り対象期間内のものをフィルタして提示。

### 進行中プロジェクトの状況

Projects DB の `In Progress` view を query して提示。

### 今週の Daily Log

LifeLog を Date でフィルタして取得。

これらを「今週の活動サマリ」として content の冒頭に含めるか確認。

## 4. 書き込み手順

```jsonc
{
  "parent": { "data_source_id": "collection://2c90ee4d-a0dd-4c6a-a824-ab83cb73bb9f" },
  "pages": [{
    "properties": {
      "名前": "2026 W19 週次レビュー",
      "種別 ": "週次レビュー",
      "date:実施日:start": "2026-05-11",
      "date:実施日:is_datetime": 0,
      "date:振り返り対象期間:start": "2026-05-04",
      "date:振り返り対象期間:end": "2026-05-10",
      "date:振り返り対象期間:is_datetime": 0,
      "今週の挑戦度": 7,
      "今週の成長度": 6
    },
    "content": "(weekly-review.md テンプレに沿った markdown)"
  }]
}
```

⚠️ 重要 (typo を含む完全一致):
- title プロパティは `名前` (日本語)
- select プロパティは `種別 ` (末尾スペース有り)、値は `週次レビュー`
- `振り返り対象期間` は range なので `start` と `end` 両方指定

## 5. テンプレート content

`assets/templates/weekly-review.md` を展開して埋める。中身は:
- 未来・理想の確認 (自由記述)
- 現状の確認 (補助動作で取得した今週の活動サマリ)
- 来週の重点

## 6. 避けるべき間違い

- ❌ DB 名を `Review` と書く (正しくは `Reveiw` だが、書き込みは data source URL を使うので DB 名自体は影響しない。ただし発言で言及するときは原文ママ)
- ❌ `種別` (末尾スペース無し) と書く (正しくは `種別 `)
- ❌ title プロパティを `Name` で書く (正しくは `名前`)
- ❌ 振り返り対象期間に `end` を書き忘れる (range なので必須)
- ❌ 「今週」を currentDate を含む週とそうでないかで混乱する → ユーザーに確認

## 7. 出力フォーマット

```
✓ [GTD] Reveiw DB に追加しました
  - 名前: 2026 W19 週次レビュー
  - 種別: 週次レビュー
  - 実施日: 2026-05-11
  - 振り返り対象期間: 2026-05-04 〜 2026-05-10
  - 挑戦度: 7 / 成長度: 6
  - URL: https://www.notion.so/...
```
