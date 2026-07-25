---
name: project-research
description: shomrkm の GTD の Project について、達成に必要な情報を調査し、学習ノートとして Knowledge DB にまとめて Project に紐づける。「このプロジェクトについて調べて」「Project の前提知識をまとめて」「何を学べばいいか整理して」と言われたときに使う。開始時に限らず、進行中の Project の追加調査にも使う。
---

# project-research

GTD の Project の達成に必要な知識を調査し、構造化して Knowledge に蓄積する。

## 責務

**やること**: Project の内容から調査すべき論点を抽出し、調査結果を Project に紐づける。

**やらないこと**:
- 知識の構造化そのもの → `semantic-tree-learning` スキルに委譲する
- Notion の読み書きの手順 → `shomrkm-notion` スキルに委譲する
- Slack の投稿先や書式 → `shomrkm-slack-notify` スキルに委譲する

## 必ず対話モードで実行する

このスキルは shomrkm との対話を前提とする。**ユーザーの応答が得られない状況では中断すること。**

理由: 調査の深さと方向は、shomrkm の学習目的と現在の知識レベルによって決まる。これを確認せずに進めると、既に知っていることを長々と説明するか、前提を飛ばした使えないノートができる。

スケジュール実行などの無人の文脈で呼ばれた場合は、調査せずに「対話が必要」と報告して終了する。

## ワークフロー

```
- [ ] 1. 対象 Project を特定
- [ ] 2. Project の内容から調査論点を抽出
- [ ] 3. 論点を提示して合意を取る
- [ ] 4. semantic-tree-learning で調査・構造化・保存
- [ ] 5. Project に紐づける
- [ ] 6. Slack に通知
```

### 1. 対象 Project を特定

引数で指定されていればそれを使う。未指定なら、アクティブな Project を取得して AskUserQuestion で選ばせる。

Project の取得は `shomrkm-notion` スキルを使う。

### 2. Project の内容から調査論点を抽出

Project ページの `## As-Is` / `## To-Be` / `## ToDos` を読む (取得は `shomrkm-notion` スキル経由)。

**論点は `To-Be` と `As-Is` のギャップから抽出する。** 「この Project を達成するために、shomrkm が知らなければいけないことは何か」を問う。

`To-Be` が空の場合、調査の的が絞れない。その旨を伝え、まず `To-Be` を書くことを提案して終了する。

### 3. 論点を提示して合意を取る

抽出した論点を提示し、AskUserQuestion で以下を確認する:

1. **調査する論点** — 抽出したもののどれを調べるか (複数可)
2. **現在の知識レベル** — その論点について既にどこまで知っているか
3. **求める深さ** — 概要を掴みたいのか、実践できるレベルまで理解したいのか

ここで得た答えが、次のステップの調査の深さを決める。

### 4. semantic-tree-learning で調査・構造化・保存

`semantic-tree-learning` スキルを呼ぶ。前ステップで得た学習目的・知識レベル・求める深さをそのまま渡す。

保存先は Knowledge DB。学習ノートとして保存する (保存手順とプロパティは `shomrkm-notion` スキルに従う)。

### 5. Project に紐づける

作成した Knowledge を Project に紐づける。**書き込み前に shomrkm の承認を得ること。**

- Projects の `🖥️ Knowledge` relation に追加
- Project ページの `## References` セクションにリンクを追記

いずれも手順は `shomrkm-notion` スキルに従う。`## References` は追記であって置換ではない。

### 6. Slack に通知

`shomrkm-slack-notify` スキルを使う。完了報告型の書式で、Knowledge のタイトル・対象 Project・URL を伝える。

## 避けるべき間違い

- ❌ 学習目的や知識レベルを確認せずに調査を始める (対話が前提)
- ❌ `To-Be` が空のまま調査を進める (的が絞れない)
- ❌ 調査論点を Project の内容と無関係に決める (`To-Be` とのギャップから導く)
- ❌ セマンティックツリーの構造を自前で組み立てる (`semantic-tree-learning` に委譲)
- ❌ 承認なしに Project ページを書き換える
- ❌ `## References` を置換する (追記が正しい)
- ❌ DB ID やプロパティ名をこのスキル内にハードコードする (`shomrkm-notion` に委譲)
