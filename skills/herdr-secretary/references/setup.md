# セットアップ

## 0. 必要なもの

- **herdr 0.9.5 以上**。0.7.3 では `agent start` に `--kind` / `--pane` がなく、`unknown option: --kind` で起動に失敗する
- herdr を上げたら、**サーバーも再起動する**。古いサーバーが動いたままだと、クライアントとの protocol が合わずに `protocol_mismatch` で失敗する。サーバーを止めるとすべてのペインのプロセスが止まるので、作業を区切ってから `herdr server stop` し、`herdr` で起動し直す

`sec init` と `sec new` は、先に `herdr --version` と `herdr status --json` を見て、足りなければ何をすればいいかを出す。このとき `sec new` は登録せずに、終了コード 3 で終わる。いつでも `sec doctor` で確かめられる。

## 1. 初期化

```bash
python3 <このリポジトリ>/skills/herdr-secretary/scripts/secretary.py init
```

`~/secretary/` に次のものができる。場所は `$SECRETARY_HOME` で変えられる。

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

`worker-settings.json` は作業エージェントの起動時に `--settings` で渡す。普段使っている Claude Code の設定には触らない。hook の向き先が変わったときなどは、`init` をやり直してよい。`sec allow` で足した許可リストは残る。

`.claude/` 配下の Edit / Write は Claude Code の保護パスで、`allow` や `acceptEdits` があっても確認される。PreToolUse hook の `permissionDecision: "allow"` でも確認は消えない。確認なしで通るのは、PermissionRequest hook が `decision.behavior: "allow"` を返したときだけだ。そこで作業エージェントの hook（`sec hook`）は、タスクの worktree にある `.claude/skills/`・`.claude/commands/`・`.claude/agents/` への Edit / Write だけを許可する。worktree の置き場は、`worktrees/`、repos.md の `（worktrees: ...）`、`<repo>/.claude/worktrees/` の 3 つだ。`.claude/settings.json` など、作業エージェントが自分の権限を広げられるものは許可しない。

### スキルを更新したとき

hook は、`init` を実行したこのリポジトリの `scripts/secretary.py` を直接読む。PR をマージしただけでは手元のファイルは変わらないので、**マージしたら手元で `git pull` する**。pull すれば、動いている作業エージェントの hook にもすぐ効く。監視ループの `sec watch` は、`sec follow` が裏で起動し、起動したときのコードで動き続ける。そのため、pull したら `kill $(cat ~/secretary/.watch.pid)` で止める。動いている `sec follow` が、10 秒ほどで監視ループを新しいコードで起動し直す。

監視ループは秘書のセッションから切り離して動き、秘書を終えても止まらない。止めたいときも `kill $(cat ~/secretary/.watch.pid)` する。`sec follow` が動いていれば起動し直されるので、先に秘書のセッションを終える。出力は `~/secretary/watch.log`、エラーは `~/secretary/watch.err` に残る。

`sec watch` は起動時に upstream を fetch し、手元が遅れていれば `[secretary] スキルのリポジトリ（...）が upstream より N コミット遅れています` と知らせる。hook が別の場所のスクリプトを呼んでいるときも知らせる。`sec doctor` でも同じことを確かめられる。止めたいときは config.json の `update_check` を `false` にする。

## 2. 秘書の起動

herdr のペインで `~/secretary` に移動して `claude` を起動する。このスキルが読み込まれるようにしておく。たとえば `~/.claude/skills/herdr-secretary` にシンボリックリンクを張る。

秘書には `~/secretary/bin/sec` の実行を許可しておく。`.claude/settings.json` の `permissions.allow` に `Bash(~/secretary/bin/sec:*)` を入れる。

## 3. herdr との連携

秘書は herdr のペインの中（`HERDR_ENV=1`）で動かす。そうすると、`herdr` コマンドがそのセッションを操作する。herdr を呼ぶ処理は `scripts/secretary.py` の `Herdr` クラスにまとめてあり、herdr 0.9.5 のソースと公式 agent skill に合わせてある。

| やること | コマンド |
|---|---|
| space を探す | `herdr workspace list`（同じ space の閉じていないタスクがいるワークスペース、なければ `label` が space 名のもの） |
| space にタブを足す（フォーカスは移さない） | `herdr tab create --workspace <ws> --cwd <repo> --label <id 名前> --no-focus --env SECRETARY_TASK_ID=<id> --env SECRETARY_HOME=<home>`。返り値の `.result.root_pane.pane_id` を使う |
| space を作る（space がなかったとき） | `herdr workspace create --cwd <repo> --label <space> --no-focus --env ...`。返り値の `.result.workspace` / `.result.tab` / `.result.root_pane` を使う。最初のタブをそのタスクのタブにし、`herdr tab rename <tab> <id 名前>` |
| 伝言を送る（`sec send`） | `herdr agent prompt <宛先の agent> "[secretary] <送り手 ID> からの伝言 ..."` |
| Claude Code を起動 | `herdr agent start <agent> --kind claude --pane <pane> --timeout 60000 -- --settings <worker-settings.json>` |
| 最初の指示・`sec reply` | `herdr agent prompt <agent> "[secretary] ..."` |
| ペインとエージェントの生存確認 | `herdr pane list` / `herdr agent list` |
| 画面を読む（`sec peek`） | `herdr agent read <agent> --source recent-unwrapped --lines N`（JSON ではなく生のテキストが返る） |
| 選択肢を選ぶ（`sec choose`） | `herdr agent send-keys <agent> down ... enter`（カーソルは先頭の選択肢にあるので、番号 - 1 回 down） |
| タスクを秘書の横に出す（`sec bring`） | `herdr pane current --current` で秘書のペインとタブを取り、`herdr pane move <pane> --tab <秘書のタブ> --split right --target-pane <秘書のペイン> --no-focus`。結果の `.result.move_result.pane` で、新しいペイン ID・タブ・ワークスペースを記録する |
| 横に出していたタスクを space に戻す | `herdr pane move <pane> --new-tab --workspace <space> --label <id 名前> --no-focus`。space がなければ `herdr pane move <pane> --new-workspace --label <space> --tab-label <id 名前> --no-focus` |
| 横に出したタスクを閉じる（`sec close`） | `herdr pane close <pane>`。秘書のタブは閉じない |
| タブを閉じる（`sec close` / `sec cleanup`） | `herdr tab close <tab>`。space に残るのがそのタブだけで、同じ space に閉じていないタスクもなければ `herdr workspace close <ws>`（`herdr tab list --workspace <ws>` で確かめる） |
| 版とサーバーの確認（`sec doctor`） | `herdr --version` / `herdr status --json`（`server.compatible`、`restart_needed`） |

スクリプトが古い、hook の向き先が違うなどの理由で hook が届かないと、秘書は許可待ちや作業の終了に気づけない。タスクは running のままで、「30 分イベントなし」「コマンドが 120 分終わらない」に見える。そこで、無反応と判定する前に `herdr agent list` の `agent_status` を見る。`working` なら判定しない。`blocked` なら「許可待ちか選択待ち」、`idle` / `done` なら「入力待ち」（result.md があれば review）として、`hook が届いていない可能性` を添えて知らせる。この判定は、`snapshot_interval_sec` ごとに回る snapshot の周にだけする。

`sec choose` は、直前のツールが AskUserQuestion で、herdr から見て `blocked` のときだけ動く。許可ダイアログも番号付きの選択肢なので、取り違えて秘書が許可しないようにしている。

hook はどのタスクのイベントかを `SECRETARY_TASK_ID` で知る。ただし `--env` で渡した変数は、そのタブのシェルにしか残らない。herdr のサーバーを再起動すると、ペインのシェルが作り直されて変数が消える。そのペインで `sec retry` すると、起動し直した Claude Code には `SECRETARY_TASK_ID` がない。そこで hook は、`SECRETARY_TASK_ID` がなければ herdr が付ける `HERDR_PANE_ID` を見て、そのペインを使っている閉じていないタスクを探す。`SECRETARY_HOME` は hook のコマンドに直接書いてある。

`<agent>` は `t001-1a2b3c` のように、タスク ID に秘書ディレクトリごとの識別子を付けた名前にしている。閉じたタスクのエージェントがまだ生きていても、名前がぶつからない。

herdr のエラーは次のように扱う。

- `agent_pane_busy` は、作ったばかりのタブでシェルの準備がまだできていない。数秒だけ再試行する
- `agent_not_ready` は、フォルダを信頼するかなどの、起動時の確認で止まっている。blocked として知らせる。人間がペインで答えたら `sec retry` で最初の指示を送り直す
- `workspace_not_found` は、space のワークスペースが一覧を取った後に閉じられた。space を作り直してそこにタブを置く
- `agent_blocked` は、許可や選択肢のダイアログが出ている。`sec reply` は送らない。選択式の質問なら `sec choose`、許可ダイアログならペインで答えるよう案内する
- `protocol_mismatch` は、herdr を上げたあとも、サーバーが古いままになっている。サーバーの再起動が要る。0. を参照する

herdr は、Claude Code の起動直後に出る初回の案内などの画面を「入力待ち」と判定することがある。その場合、最初の指示はその画面に送られて消える。そこで監視ループは、最初の指示が hook（UserPromptSubmit）で届いたことを 30 秒以内に確かめられなければ、画面を見る。指示が入力欄に残っていれば、Enter を 1 回だけ送り直す。入力欄が見えなければ、Enter は送らずに blocked として知らせる。フォルダの信頼確認では、既定の選択肢が「No, exit」のことがあるからだ。

UserPromptSubmit が来たら、prompt の先頭で送り手を見分ける。`[secretary]` は秘書、`<task-notification>` は Claude Code 自身（バックグラウンドのサブエージェントやコマンドの完了通知）、それ以外は人間の直接指示として manual にする。許可ダイアログや選択式の質問（AskUserQuestion）にペインで答えても、UserPromptSubmit は来ない。この場合は PostToolUse でタスクが running に戻る。

herdr の版を上げて動かなくなったら、`herdr agent`・`herdr tab`・`herdr pane` のヘルプを見て `Herdr` クラスを直す。

## 4. 動作確認

偽の herdr を使ったテストがある。偽の herdr は、herdr 0.9.5 と同じ引数を受け付け、同じ形の JSON を返す。テストは bash の構文を使うので、`bash` で動かす。

```bash
bash skills/herdr-secretary/tests/test_flow.sh
```

本物の Claude Code で、hook から review までの流れを確かめるテストもある。このテストは `claude -p` を 1 回呼ぶ。

```bash
sh skills/herdr-secretary/tests/test_real_hooks.sh
```

## 5. 実機での確認結果（herdr 0.9.5 + Claude Code 2.1.325）

herdr をソースからビルドし、ヘッドレスのサーバーを立てて、本物の作業エージェントでタスクを流した。

- 1 件の流れ。起動し、起動画面での停止を検知し、retry した。コマンド付きの許可待ちが通知され、ダイアログ中の `sec reply` はペインへ案内された。許可したあと、作業エージェントが worktree で作業し、result.md を書いて review になった
- 直接介入。review のあとにペインで追加の指示を出すと manual になり、result.md が書き直されて review に戻った
- 3 件の並列。ルールのない小さなリポジトリで、各タスクが 1 ファイルを作る。結果は次のとおり:

| 条件 | 3 件すべて review まで | 人間の対応 |
|---|---|---|
| 最初の版 | 69 秒 | 許可 6 回 |
| 既定の作業場所を許可（`worktrees/`） | 45 秒 | 許可 6 回 |
| 前の行の設定のまま、「Bash は 1 コマンドずつ」とも指示 | 39 秒 | 0 回 |

- 3 件の並列。実務に近い TypeScript リポジトリで、機能追加 2 件とバグ修正 1 件を流した。CLAUDE.md には、`../<repo>-worktrees/<branch>` に worktree を作る、`npm ci` する、テストを書く、`npm test` と `typecheck` を通す、Conventional Commits で書く、というルールがある。結果は次のとおり:

| 条件 | 結果 | 人間の対応 |
|---|---|---|
| repos.md に worktree の場所を書かず、許可リストは読み取り系だけ | 47 秒で許可 13 回。その後、全件がリポジトリ外の worktree での Edit 確認で止まった（秘書は「3 件 blocked、19 分待ち」と出し続けた） | 許可 13 回以上 |
| repos.md に `（worktrees: ../eval-app-worktrees）`、`sec permissions` の候補から `git add` / `git commit` / `npm ci` / `npm test` / `npm run` などを `sec allow` | 125 秒で 3 件とも review。全件ルールどおりのブランチにコミットされ、`npm test` と `typecheck` が通ることを別途確認 | **許可 0 回**。判断 3 回（既存の typecheck の不具合をどう直すか。3 件とも同じ質問）を `sec reply` で回答 |

- 2 件の並列を 3 回。Rails 7.2 の API と RSpec と SQLite のリポジトリで、バリデーションや API の追加などを流した。CLAUDE.md には、worktree の場所、`bundle install`、`bin/rails db:prepare`、`bundle exec rspec`、Conventional Commits のルールがある。結果は次のとおり:

| 回 | 許可リスト | 2 件すべて review まで | 人間の許可 |
|---|---|---|---|
| 1 回目 | Rails 用は何もない | 166 秒 | 23 回 |
| 2 回目 | `sec permissions` の候補から `bundle install` / `bundle exec rspec` / `bin/rails db:prepare` / `env RAILS_ENV=test bin/rails db:prepare` などを `sec allow` | 111 秒 | 9 回（うち 8 回は、この検証環境で rspec にパスが通っていない問題を作業エージェントが回避しようとしたもの） |
| 3 回目 | 環境の問題を直した（作業エージェントが残課題に書いた方法） | 64 秒 | 2 回（`bin/rails generate migration` と `bin/rails db:migrate`。初めて出てきた DB を変える操作で、確認するのが妥当） |

どの回も全件ルールどおりのブランチにコミットされ、`bundle exec rspec` が通ることを別途確認した。

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
| `first_prompt_ack_sec` | 30 | 最初の指示を送ってからこの秒数で届いたことが確かめられなければ、画面を見る。入力欄に残っていれば Enter を 1 回送り直し、入力欄が見えなければ起動画面で止まっていると通知する |
| `quiet_hours` | `18:00-09:00` | この時間帯は通知（会話とデスクトップ）を出さずに溜め、明けたら 1 行にまとめて出す。日をまたいでよい。`""` で無効 |
| `permission_repeat_quiet_min` | 10 | 同じタスクで同じ種類の許可待ち（同じルール候補、または保護パス）が続いたら、最初の通知からこの分数は通知を重ねない。0 で無効 |
| `update_check` | `true` | `sec watch` の起動時に、スキルのリポジトリが upstream より遅れていないか、hook の向き先が合っているかを確かめる |
| `notify` | `auto` | デスクトップ通知。`auto` は macOS なら osascript。`off` で無効。`{text}` を含むコマンドも書ける |
| `auto_bring` | `true` | `sec new` で起動したタスクと、`sec reply`・`sec choose` で返事を送ったタスクを、秘書の横に出す |
| `bring_split` | `right` | `sec bring` でタスクのペインを秘書のペインのどちら側に並べるか。`right` か `down` |
| `herdr_bin` | `herdr` | herdr のコマンド名 |
