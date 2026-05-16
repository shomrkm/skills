# lifelog-workflow.md — LifeLog DB への Daily Log 記録

`references/databases.md` の LifeLog セクションを先に Read していること。

## 1. このワークフローのトリガー

ユーザーがこれらの語を含むとき:

- 「Daily Log」「今日のログ」「今日の振り返り」
- 「今日学んだこと」「今日の気づき」「ライフログ」
- 「メモを残す」「LifeLog に書く」
- 「(出来事を) 記録しておきたい」

ただし、「学んだ技術的なこと→ Knowledge にまとめる」のニュアンスなら `knowledge-workflow.md` 側。LifeLog はもっと**雑な日記/出来事/気づき**寄り。

## 2. AskUserQuestion で必ず確認すること

1. **Date**: 今日 (currentDate) でよいか確認。過去日付の補足記入なら指定の日付。
2. **Tags**: ユーザーの内容に合いそうな既存タグを 1-3 個提示して選択させる
   - 既存 Tag: `cook`, `Favorite`, `smarthr`, `Travel`, `AI`, `Prompt Engineering`, `CSS`, `メルマガ`, `週次まとめ`, `Memo`
   - 「内容から推測すると `AI` と `Memo` が候補ですがどれを付けますか?」のように選択肢提示
3. **URL**: 外部記事や URL の引用元があるか (ユーザーが URL を渡していれば自動セット)
4. **Daily Log テンプレ を使うか**: 構造化 (ハイライト / 詰まった点 / 学び / 明日の一手) で書くか、フリーフォーマットか

### 「ささっと記録」のショートカット

ユーザーが詳細を述べずに「これ Daily Log に書いて」と言った場合、内容が短ければ:
- Tags 候補と Date 確認だけして即作成
- 内容を ` Name` または content 本文に入れる

## 3. 書き込み手順

### Daily Log テンプレを使う場合

content には `assets/templates/daily-log.md` の構造を展開し、ユーザーの発言を該当セクションに割り振る:

```jsonc
{
  "parent": { "data_source_id": "collection://3587b931-adde-44bb-bf56-4872c50a5ce8" },
  "pages": [{
    "properties": {
      "Name": "Daily Log",  // または "Daily Log 2026-05-11" など
      "date:Date:start": "2026-05-11",
      "date:Date:is_datetime": 0,
      "Tags": ["AI", "Memo"],
      "userDefined:URL": "(あれば)"
    },
    "content": "(daily-log.md テンプレに沿った markdown)"
  }]
}
```

### フリーフォーマットの場合

`Name` にトピックを表す短いタイトル、content にユーザーの発言をそのまま整形して書く。

### 学び (Fact / Because / Next) の埋め方

ユーザーが「XX について学んだ」と言ったら:
- **Fact**: 何があった/分かった (客観的事実)
- **Because**: なぜそれが起きた/重要か (理由・洞察)
- **Next**: 明日以降どうする (アクション)

ユーザーの発言から埋められる部分のみ埋め、不足は空欄のまま (テンプレに「全部埋めなくてOK。1行だけでもOK。」とある通り)。

## 4. 典型例

### 例1: 「Claude Code の hooks の使い方を学んだ。Daily Log に書いて」

AskUserQuestion で:
- Date = 2026-05-11 でよい？
- Tags = `AI` + `claude` のような既存タグ... `claude` は LifeLog の Tag にはないので `AI` + `Memo` を提案
- テンプレ構造で書く？

OK が出たら作成。content の **Fact** に「Claude Code の hooks の使い方を学んだ」を埋める。

### 例2: 「今日の出来事ささっと: 同僚と昼飯食べた、夕方に Cline 試した」

AskUserQuestion 簡略版:
- Date = 今日でよい？
- Tags = 候補 `Memo` + `AI` (Cline 関連) でよい？

OK なら作成。content にユーザーの発言を箇条書きで:
```
- 同僚と昼飯食べた
- 夕方に Cline 試した
```

## 5. 避けるべき間違い

- ❌ `Date` プロパティを `Date:start` ではなく `Date` で書こうとする (expanded 必須)
- ❌ Tags を新規でガンガン作る (既存タグから選ぶのが原則。新規が必要なら確認)
- ❌ Daily Log を必ずテンプレ構造で書こうとする (ユーザーが嫌がる可能性。簡素でもOKと確認)
- ❌ Knowledge にまとめるべき技術的学びを LifeLog に書く (用途を取り違える)

## 6. 出力フォーマット

```
✓ LifeLog に追加しました
  - Name: Daily Log
  - Date: 2026-05-11
  - Tags: AI, Memo
  - URL: https://www.notion.so/...
```
