# api-mode.md — REST API 経由で個人 Notion を操作する

## 目次

- いつ API モードを使うか
- セットアップ (token と Integration の接続)
- スクリプトの使い方
- MCP モードとの機能差
- よくある失敗

## いつ API モードを使うか

**環境変数 `SHOMRKM_NOTION_USE_API` が `true` のとき、MCP ではなくこのモードを使う。**

```bash
[ "${SHOMRKM_NOTION_USE_API:-}" = "true" ] && echo "API モード" || echo "MCP モード"
```

想定する状況: 会社 PC では Notion MCP が**会社のワークスペース**に接続されており、個人の Notion を読み書きできない。アカウントが別なのでページを共有しても解決しない。API token なら token を発行したワークスペースに必ず届く。

個人 Mac では MCP の方が扱いやすい (ページ本文の部分置換など API では重い操作ができる) ため、環境変数が未設定なら従来どおり MCP を使う。

## セットアップ

### 1. Integration を作る

1. **個人アカウント**で https://www.notion.so/my-integrations を開く
2. New integration → 名前は任意 (例: `shomrkm-cli`)
3. Capabilities: Read content / Update content / Insert content にチェック
4. Internal Integration Secret (`ntn_` で始まる) をコピー

### 2. token を `~/.zshrc` に置く

```bash
# ~/.zshrc
export NOTION_TOKEN=ntn_...
export SHOMRKM_NOTION_USE_API=true   # 会社 PC のみ。個人 Mac には書かない
```

追記したら `source ~/.zshrc` するか、新しいシェルを開く。

`~/.zshrc` には token が入るので権限を絞る:

```bash
chmod 600 ~/.zshrc
```

**`settings.json` の `env` には書かないこと。** 設定ファイルは人に見せたり issue に貼ったりする機会が多く、token が混ざっていると漏れやすい。

`scripts/notion_api.py` は環境変数を参照する。環境変数が未設定のときだけ `~/.zshrc` の `export` 行から拾うフォールバックを持つ (launchd のような非対話・非ログインシェルは `.zshrc` を読まないため)。`.zshrc` は実行せず単純な `export KEY=VALUE` 行だけを読むので、副作用はない。

### 3. 各 DB に Integration を接続する ★忘れやすい

**この手順を飛ばすと `object_not_found` になる。** token があっても、Integration が接続されていない DB は API から見えない。

Notion で対象の DB を開き、右上の `...` → Connections → 作成した Integration を追加する。

対象は 7 つ: Tasks / Projects / LifeLog / [GTD] Reveiw / Knowledge / Links / Books。

Tasks・Projects・[GTD] Reveiw は親ページ `Tasks` (`4e75992124e84369a2c9cb3c727521a8`) 配下にあるため、親ページで接続すれば一括で通る。Knowledge と LifeLog は別ツリーなので個別に接続する。

### 4. 疎通を確認する

```bash
scripts/notion_api.py query tasks --limit 3
```

## スクリプトの使い方

`scripts/notion_api.py` を**実行する** (中身を読む必要はない)。DB 名は
`tasks` / `projects` / `knowledge` / `lifelog` / `review` / `links` / `books`。

### 検索

```bash
# 今日のタスク
scripts/notion_api.py query tasks \
  --filter '{"property":"task_type","select":{"equals":"Today"}}'

# 進行中の Project を作成日の新しい順で
scripts/notion_api.py query projects \
  --filter '{"property":"status","status":{"equals":"in-progress"}}' \
  --sort 'Created time:desc'

# 未読の Links
scripts/notion_api.py query links \
  --filter '{"property":"Status","select":{"does_not_equal":"Finished"}}'
```

`--filter` は Notion API の filter オブジェクトをそのまま渡す。複合条件は `and` / `or` で包む:

```json
{"and":[{"property":"task_type","select":{"equals":"Next Actions"}},
        {"property":"due_date","date":{"on_or_before":"2026-07-31"}}]}
```

### ページの取得

```bash
scripts/notion_api.py get <page_id>              # プロパティのみ
scripts/notion_api.py get <page_id> --content    # 本文も markdown で
```

### 作成

`--props` は**人間が読める形**で渡す。型変換はスクリプトがライブのスキーマを見て行うため、Notion の内部表現を書く必要はない。

```bash
# タスクを追加
scripts/notion_api.py create tasks --props '{
  "Name": "カフェテリアプランを考える",
  "task_type": "Today",
  "due_date": "2026-07-31"
}'

# Knowledge を本文つきで作成
scripts/notion_api.py create knowledge \
  --props '{"Title":"XX の仕組み","Tags":["AI","claude"],"Status":"completed","is leaning note":"__YES__"}' \
  --content-file /tmp/note.md
```

日付は `"2026-07-31"` の文字列でよい (`date:due_date:start` のような expanded 記法は不要 — それは MCP 側の書き方)。

### 更新

```bash
# タスクを完了にする
scripts/notion_api.py update <page_id> --props '{
  "task_type": "Completed",
  "completed_at": "2026-07-26"
}'
```

## MCP モードとの機能差

| 操作 | MCP | API モード |
|---|---|---|
| DB の検索・一覧 | ✅ | ✅ |
| ページのプロパティ取得・更新 | ✅ | ✅ |
| ページ本文の取得 | ✅ | ✅ (markdown 化) |
| 新規ページの作成 (本文つき) | ✅ | ✅ |
| **ページ本文の部分置換** | ✅ | ❌ **未対応** |
| ワークスペース全体の semantic search | ✅ | ❌ |

**`Progress Summary` の部分置換は API モードでは行えない。** ブロック単位の特定・削除・挿入を要し、実装が重いため対象外とした。`project-digest` の週次実行は個人 Mac の MCP 経路で動かす。

API モードで本文の更新が必要になった場合は、その旨を報告して MCP 環境での実行を促すこと。黙って別の方法で書き換えない。

## よくある失敗

| 症状 | 原因と対処 |
|---|---|
| `object_not_found` | Integration が DB に接続されていない。DB の `...` → Connections から追加する |
| `unauthorized` | `NOTION_TOKEN` が無効。再発行する |
| `validation_error` | プロパティ名か値が不正。`databases.md` の正確な値 (typo 含む) を確認する |
| 警告「プロパティが存在しません」 | プロパティ名の打ち間違い。`databases.md` を参照する |
| 警告「書き込めません」 | rollup / formula を書こうとしている (`progress`, `is_completed`, `is_delayed`) |

プロパティ名は MCP モードと同じく `databases.md` が正。typo (` is_archived` の先頭スペース、`is leaning note`、`種別 ` の末尾スペース) もそのまま使う。
