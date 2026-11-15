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

## 3. herdr との連携

秘書は herdr のペインの中（`HERDR_ENV=1`）で動かす。そうすると、`herdr` コマンドがそのセッションを操作する。herdr を呼ぶ処理は `scripts/secretary.py` の `Herdr` クラスにまとめてあり、herdr 0.9.5 のソースと公式 agent skill に合わせてある。

| やること | コマンド |
|---|---|
| タブを作る（フォーカスは移さない） | `herdr tab create --cwd <repo> --label <id> --no-focus --env SECRETARY_TASK_ID=<id> --env SECRETARY_HOME=<home>` → `.result.root_pane.pane_id` |
| Claude Code を起動 | `herdr agent start <id> --kind claude --pane <pane> --timeout 60000 -- --settings <worker-settings.json>` |
| 最初の指示・`sec reply` | `herdr agent prompt <id> "[secretary] ..."` |
| ペインの生存確認 | `herdr pane list` → `.result.panes[].pane_id` |
| 画面を読む（`sec peek`） | `herdr agent read <id> --source recent-unwrapped --lines N` |

herdr のエラーは次のように扱う。

- `agent_pane_busy`：作ったばかりのタブでシェルの準備がまだできていない。数秒だけ再試行する
- `agent_not_ready`：起動時の確認（フォルダを信頼するか、など）で止まっている。blocked として知らせ、人間が答えたら最初の指示を送り直す
- `agent_blocked`：許可や選択肢のダイアログが出ている。`sec reply` は送らず、ペインで答えるよう案内する

herdr のバージョンを上げて動かなくなったら、`herdr agent` と `herdr tab` のヘルプを見て `Herdr` クラスを直す。

## 4. 動作確認

偽の herdr（herdr 0.9.5 と同じ引数・同じ JSON を返す）を使ったテストがある。

```bash
sh skills/herdr-secretary/tests/test_flow.sh
```

本物の Claude Code で、hook から review までの流れを確かめるテストもある（`claude -p` を 1 回呼ぶ）。

```bash
sh skills/herdr-secretary/tests/test_real_hooks.sh
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
| `herdr_bin` | `herdr` | herdr のコマンド名 |
