# セットアップ

## 1. 初期化

```bash
python3 <このリポジトリ>/skills/herdr-secretary/scripts/secretary.py init
```

`~/secretary/`（`$SECRETARY_HOME` で変更できる）に次のものができる。

| ファイル | 中身 |
|---|---|
| `bin/sec` | CLI のラッパー |
| `config.json` | 並列上限などの設定 |
| `repos.md` | 作業対象のリポジトリ一覧。**自分で書く** |
| `worker-settings.json` | 作業エージェント専用の設定。hook、タスクフォルダへの書き込み許可、ファイル編集の自動承認（`acceptEdits`）が入っている。Bash などの許可はここに足していく |
| `tasks/` | タスクフォルダ |
| `events.jsonl` | hook が書くイベントのログ |
| `permissions.jsonl` | 許可待ちになったコマンドの記録（`sec permissions` で集計） |

`repos.md` は `- 名前: 絶対パス — 説明` の形で書く。名前を書いておくと `sec new --repo 名前` で指定できる。

`worker-settings.json` は作業エージェントの起動時に `--settings` で渡す。普段使っている Claude Code の設定には触らない。

## 2. 秘書用ディレクトリで Claude Code を起動する

herdr のペインで `~/secretary` に移動して `claude` を起動する。このスキルが読み込まれるようにしておく（`~/.claude/skills/herdr-secretary` にシンボリックリンクを張るなど）。

秘書には `~/secretary/bin/sec` の実行を許可しておく（`.claude/settings.json` の `permissions.allow` に `Bash(~/secretary/bin/sec:*)` を入れる）。

## 3. herdr の CLI を実機で確認する（要対応）

herdr の公式ドキュメントで確認しきれていない部分がある。herdr を呼ぶのは `scripts/secretary.py` の `Herdr` クラスだけなので、`herdr --help` を見て合わなければそこを直す。

| 使っているコマンド | 前提にしていること |
|---|---|
| `herdr api snapshot` | JSON を返す。`panes` という配列の要素に `id`（または `pane_id`）がある |
| `herdr tab create --label <id>` | 新しいタブとペインができる。snapshot を作成前後で比べて、新しいペインを特定する |
| `herdr pane run <pane> "<command>"` | そのペインのシェルでコマンドを実行する |
| `herdr pane read <pane> --source recent --lines N` | 画面の直近 N 行を返す |
| `herdr agent prompt <pane> <text>` | 作業エージェントに入力を送る（`sec reply`）。違えば config.json の `herdr_send_args` を直す |

## 4. 動作確認

偽の herdr を使ったテストがある。

```bash
sh skills/herdr-secretary/tests/test_flow.sh
```

実機では、小さいタスク 1 つで「登録 → 起動 → 許可待ちの通知 → 直接介入 → result.md → review」まで通して試す。

## 設定（config.json）

| キー | 既定値 | 意味 |
|---|---|---|
| `max_parallel` | 10 | 同時に動かす数。running / blocked / manual を数える。review は数えない |
| `snapshot_interval_sec` | 180 | ペインが消えていないかを確認する間隔 |
| `silence_alert_min` | 30 | running のままイベントがこの分数来なければ異常として通知する |
| `long_tool_alert_min` | 120 | 1 つのコマンド（テストの全件実行など）がこの分数終わらなければ異常として通知する |
| `manual_timeout_min` | 15 | 人間の入力がこの分数途切れたら自動で秘書に戻す。0 で無効 |
| `review_backlog_alert` | 5 | レビュー待ちがこの件数に達したら知らせる。0 で無効 |
| `notify` | `auto` | デスクトップ通知。`auto` は macOS なら osascript。`off` で無効。`{text}` を含むコマンドも書ける |
| `herdr_send_args` | `["agent","prompt","{pane}","{text}"]` | `sec reply` で使う herdr の引数 |
| `herdr_bin` | `herdr` | herdr のコマンド名 |
