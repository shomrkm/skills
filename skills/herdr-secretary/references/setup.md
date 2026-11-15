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
| `worktrees/` | リポジトリに作業場所のルールがないときの worktree 置き場。作業エージェントは確認なしで触れる |
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
| タブを作る（秘書と同じワークスペース、フォーカスは移さない） | `herdr tab create --cwd <repo> --label <id> --no-focus --workspace $HERDR_WORKSPACE_ID --env SECRETARY_TASK_ID=<id> --env SECRETARY_HOME=<home>` → `.result.root_pane.pane_id` |
| Claude Code を起動 | `herdr agent start <agent> --kind claude --pane <pane> --timeout 60000 -- --settings <worker-settings.json>` |
| 最初の指示・`sec reply` | `herdr agent prompt <agent> "[secretary] ..."` |
| ペインとエージェントの生存確認 | `herdr pane list` / `herdr agent list` |
| 画面を読む（`sec peek`） | `herdr agent read <agent> --source recent-unwrapped --lines N`（JSON ではなく生のテキストが返る） |

`<agent>` は `t001-1a2b3c` のように、タスク ID に秘書ディレクトリごとの識別子を付けた名前にしている。閉じたタスクのエージェントがまだ生きていても、名前がぶつからない。

herdr のエラーは次のように扱う。

- `agent_pane_busy`：作ったばかりのタブでシェルの準備がまだできていない。数秒だけ再試行する
- `agent_not_ready`：起動時の確認（フォルダを信頼するか、など）で止まっている。blocked として知らせる。人間がペインで答えたら `sec retry` で最初の指示を送り直す
- `workspace_not_found`：アクティブなワークスペースがない（秘書が herdr の外にいる）。`secretary` ワークスペースを作ってそこにタブを置く

herdr は Claude Code の起動直後の画面（初回の案内など）を「入力待ち」と判定することがある。その場合、最初の指示はその画面に吸い込まれる。そのため、最初の指示が hook（UserPromptSubmit）で届いたことを 30 秒以内に確認できなければ、blocked として知らせる。Enter を自動で送り直すことはしない（フォルダの信頼確認は、既定の選択肢が「No, exit」）。
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

## 5. 実機での確認結果（herdr 0.9.5 + Claude Code 2.1.325）

herdr をソースからビルドし、ヘッドレスのサーバーを立てて、本物の作業エージェントでタスクを流した。

- 1 件：起動 → 起動画面での停止の検知 → retry → 許可待ちの通知（コマンド付き）→ ダイアログ中の `sec reply` はペインへ案内 → 許可 → worktree で作業 → result.md → review
- 直接介入：review 後にペインで追加の指示 → manual → result.md が書き直されて review
- 3 件の並列（各 1 ファイル作成、ルールのない小さなリポジトリ）：

| 条件 | 3 件すべて review まで | 人間の対応 |
|---|---|---|
| 最初の版 | 69 秒 | 許可 6 回 |
| 既定の作業場所を許可（`worktrees/`） | 45 秒 | 許可 6 回 |
| さらに「Bash は 1 コマンドずつ」を指示 | 39 秒 | 0 回 |

- 3 件の並列（実務に近い TypeScript リポジトリ。CLAUDE.md に「`../<repo>-worktrees/<branch>` に worktree、`npm ci`、テストを書く、`npm test` と `typecheck` を通す、Conventional Commits」のルールあり。機能追加 2 件とバグ修正 1 件）：

| 条件 | 結果 | 人間の対応 |
|---|---|---|
| repos.md に worktree の場所を書かず、許可リストは読み取り系だけ | 47 秒で許可 13 回。その後、全件がリポジトリ外の worktree での Edit 確認で止まった（秘書は「3 件 blocked、19 分待ち」と出し続けた） | 許可 13 回以上 |
| repos.md に `（worktrees: ../eval-app-worktrees）`、`sec permissions` の候補から `git add` / `git commit` / `npm ci` / `npm test` / `npm run` などを `sec allow` | 125 秒で 3 件とも review。全件ルールどおりのブランチにコミットされ、`npm test` と `typecheck` が通ることを別途確認 | **許可 0 回**。判断 3 回（既存の typecheck の不具合をどう直すか。3 件とも同じ質問）を `sec reply` で回答 |

許可で止まる原因の多くは、コマンドを `;` や `&&` でつなぐこと、`git -C` や `npm --prefix` で場所を指定すること、作業ディレクトリの外（リポジトリの外の worktree）を触ることだった。許可ルールは先頭一致なので、`git status` を許可しても `git -C <dir> status` は止まる。そこで作業エージェントには、作業場所に一度だけ `cd` してから 1 コマンドずつ実行するよう指示している。`sec permissions` は、許可済みのルールなのに止まったものも、その理由付きで出す。

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
