#!/bin/sh
# 偽 herdr を使って、登録 → 起動 → hook → 状態遷移 → キュー の一連を確認する。
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SEC="python3 $HERE/../scripts/secretary.py"
TMP=$(mktemp -d)
export SECRETARY_HOME="$TMP/home" HERDR_BIN="$HERE/fake_herdr.py" FAKE_HERDR_STATE="$TMP/herdr.json"
REPO="$TMP/repo"; mkdir -p "$REPO"
fail() { echo "FAIL: $*"; $SEC list --all; cat "$FAKE_HERDR_STATE.log"; exit 1; }
expect() { echo "$2" | grep -q -- "$1" || fail "expected '$1' in: $2"; }
hook() { # hook TASK EVENT [extra json]
  echo "{\"hook_event_name\":\"$2\"${3:-}}" | SECRETARY_TASK_ID=$1 $SEC hook
}
watch() { $SEC watch --once; }

$SEC init >/dev/null
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["max_parallel"]=2; c["snapshot_interval_sec"]=0; c["notify"]="off"; c["review_backlog_alert"]=2; json.dump(c,open(p,"w"))
PY

echo "- myrepo: $REPO — テスト用" >> "$SECRETARY_HOME/repos.md"

# 完了条件なしは拒否
if $SEC new --repo "$REPO" --title x --request y 2>/dev/null; then fail "done なしで登録できた"; fi

# repos.md の名前で指定できる
out=$($SEC new --repo myrepo --title "N+1 修正" --done "spec が通る" --request "N+1 を直す")
expect "t001 queued→running" "$out"
$SEC new --repo "$REPO" --title "two" --done d --request r >/dev/null
out=$($SEC new --repo "$REPO" --title "three" --done d --request r)
expect "キュー待ち" "$out"
LOG="$FAKE_HERDR_STATE.log"
grep -q "\"tab\", \"create\", \"--cwd\", \"$REPO\"" "$LOG" || fail "リポジトリでタブを作っていない"
grep -q '"--env", "SECRETARY_TASK_ID=t001"' "$LOG" || fail "タスク ID を環境変数で渡していない"
grep -qE '"agent", "start", "t001-[0-9a-f]{6}", "--kind", "claude", "--pane", "w1:p1"' "$LOG" || fail "agent start が呼ばれていない"
grep -q '"--", "--settings", ".*worker-settings.json"' "$LOG" || fail "worker-settings を渡していない"
grep -qE '"agent", "prompt", "t001-[0-9a-f]{6}", "\[secretary\] .*prompt.md' "$LOG" || fail "最初の指示を送っていない"

# 秘書からの最初のプロンプトは介入扱いにしない
hook t001 UserPromptSubmit ',"prompt":"[secretary] read prompt.md"'
out=$(watch); [ -z "$out" ] || fail "secretary prompt で通知が出た: $out"

# 許可待ち（PermissionRequest）→ どのコマンドかが通知に出る。後から来る Notification は重ねない → 再開
hook t001 PreToolUse ',"tool_name":"Bash","tool_input":{"command":"bundle exec rspec spec/requests"}'
hook t001 PermissionRequest ',"tool_name":"Bash","tool_input":{"command":"bundle exec rspec spec/requests"}'
hook t001 Notification ',"message":"Claude needs your permission to use Bash"'
out=$(watch); expect 't001 running→blocked 許可待ち: Bash `bundle exec rspec spec/requests`' "$out"
[ "$(echo "$out" | wc -l)" -eq 1 ] || fail "許可待ちの通知が重複: $out"
expect "Bash(bundle exec:\*)" "$($SEC permissions)"
hook t001 PostToolUse ',"tool_name":"Bash"'
out=$(watch); [ -z "$out" ] || fail "再開は通知しない: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"

# result.md なしで Stop → 入力待ち。直後の idle Notification は重ねない
hook t001 Stop
hook t001 Notification ',"message":"Claude is waiting for your input"'
out=$(watch); expect "running→blocked 入力待ち" "$out"
[ "$(echo "$out" | wc -l)" -eq 1 ] || fail "通知が重複: $out"

# 秘書から返事を送ると running に戻り、介入扱いにならない
out=$($SEC reply t001 "方針 A で進めて"); expect "送りました" "$out"
grep -qE '"agent", "prompt", "t001-[0-9a-f]{6}", "\[secretary\] 方針 A' "$LOG" || fail "送信されていない"
hook t001 UserPromptSubmit ',"prompt":"[secretary] 方針 A で進めて"'
out=$(watch); [ -z "$out" ] || fail "秘書の返事で通知: $out"
hook t001 Stop
out=$(watch); expect "running→blocked 入力待ち" "$out"

# ダイアログ中は文字で答えられないので、ペインに誘導する
echo t001 > "$FAKE_HERDR_STATE.blocked"
if out=$($SEC reply t001 "yes" 2>&1); then fail "ダイアログ中に送れてしまった"; fi
expect "ペイン w1:p1 で直接答えてください" "$out"
rm "$FAKE_HERDR_STATE.blocked"

# 人間が直接指示 → manual。manual 中の Stop / Notification は黙る
hook t001 UserPromptSubmit ',"prompt":"いや、別の方法で"'
out=$(watch); expect "blocked→manual" "$out"
hook t001 Stop; hook t001 Notification ',"message":"Claude needs your permission"'
out=$(watch); [ -z "$out" ] || fail "manual 中に通知: $out"

# result.md を書いて Stop → review
echo "done: PR https://example.com/pr/1" > $SECRETARY_HOME/tasks/t001/result.md
hook t001 Stop
out=$(watch); expect "manual→review" "$out"
expect "t001 \[review" "$($SEC inbox)"
# review は並列数に数えないので、空いた枠で t003 が起動する
expect "t003 queued→running" "$out"

# review 後の追加指示 → manual。古い result.md では review に戻らない
hook t001 UserPromptSubmit ',"prompt":"テストも足して"'
out=$(watch); expect "review→manual" "$out"
hook t001 Stop
out=$(watch); [ -z "$out" ] || fail "古い result.md で review に戻った: $out"
sleep 1; echo "updated" > $SECRETARY_HOME/tasks/t001/result.md
hook t001 Stop
out=$(watch); expect "manual→review" "$out"

# close しても作業場所は消さない
out=$($SEC close t001)
expect "タブや作業場所は残っています" "$out"

# レビュー待ちが溜まったら知らせる（テストでは 2 件）
echo r > $SECRETARY_HOME/tasks/t003/result.md; hook t003 Stop
echo r > $SECRETARY_HOME/tasks/t001/result.md
python3 - <<PY
import json; p="$SECRETARY_HOME/tasks/t001/meta.json"; m=json.load(open(p)); m["status"]="review"; json.dump(m,open(p,"w"))
PY
out=$(watch); expect "レビュー待ちが 2 件" "$out"
out=$(watch); [ -z "$out" ] || fail "レビュー待ちの通知が重複: $out"
$SEC close t001 >/dev/null

# 長いコマンドの実行中は 30 分では騒がない。介入の放置は自動で秘書に戻す
python3 - <<PY
import json,time
from datetime import datetime,timezone
old=datetime.fromtimestamp(time.time()-45*60,timezone.utc).isoformat()
p="$SECRETARY_HOME/tasks/t002/meta.json"; m=json.load(open(p))
m.update(status="running", last_event_at=old, pending_tool={"name":"Bash","label":"Bash \`rspec\`","started_at":old}); json.dump(m,open(p,"w"))
p="$SECRETARY_HOME/tasks/t003/meta.json"; m=json.load(open(p))
m.update(status="manual", last_human_at=old, result_seen_mtime=9e12); json.dump(m,open(p,"w"))
PY
out=$(watch)
echo "$out" | grep -q "t002" && fail "長いコマンド中に異常通知: $out"
expect "t003 manual→running 15 分介入がないので秘書に戻した" "$out"

# ペインが消えたら異常
python3 - <<PY
import json; p="$FAKE_HERDR_STATE"; d=json.load(open(p)); d["panes"]=[x for x in d["panes"] if x!="w1:p2"]; json.dump(d,open(p,"w"))
PY
out=$(watch); expect "t002 running→blocked 異常: ペインが見つからない" "$out"

# 起動時の確認（フォルダの信頼など）で止まったら知らせ、答えた後に最初の指示を送り直す
echo t004 > "$FAKE_HERDR_STATE.not_ready"
$SEC close t003 >/dev/null
out=$($SEC new --repo myrepo --title four --done d --request r)
expect "t004 queued→blocked 起動時の確認待ち" "$out"
grep -q '"agent", "prompt", "t004' "$LOG" && fail "確認待ちなのに指示を送った"
# 確認画面に Enter を送らないよう、自動では送り直さない
out=$(watch); grep -q '"agent", "prompt", "t004' "$LOG" && fail "勝手に送り直した"
rm "$FAKE_HERDR_STATE.not_ready"
out=$($SEC retry t004); expect "送り直しました" "$out"
grep -qE '"agent", "prompt", "t004-[0-9a-f]{6}", "\[secretary\]' "$LOG" || fail "retry で最初の指示を送っていない"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t004/meta.json)"

# 最初の指示が届かない（起動画面に吸い込まれた）と知らせる
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["first_prompt_ack_sec"]=0; json.dump(c,open(p,"w"))
PY
out=$(watch); expect "t004 running→blocked 起動画面で止まっている" "$out"
out=$($SEC retry t004); hook t004 UserPromptSubmit ',"prompt":"[secretary] prompt.md を読んで"'
out=$(watch); [ -z "$out" ] || fail "届いたのに通知: $out"

# 作業エージェントが終了したら知らせ、retry で同じペインに起動し直す
python3 - <<PY
import json; p="$FAKE_HERDR_STATE"; d=json.load(open(p)); d["agents"]={k:v for k,v in d["agents"].items() if not k.startswith("t004")}; json.dump(d,open(p,"w"))
PY
out=$(watch); expect "t004 running→blocked 異常: 作業エージェントが終了した" "$out"
$SEC retry t004 >/dev/null
[ "$(grep -cE '"agent", "start", "t004-' "$LOG")" -ge 2 ] || fail "retry で起動し直していない"
hook t004 UserPromptSubmit ',"prompt":"[secretary] prompt.md を読んで"'

# 許可ルールの候補と sec allow
hook t004 PermissionRequest ',"tool_name":"Bash","tool_input":{"command":"cd /x/y && git worktree add ../wt -b task/t004"}'
out=$(watch); expect 't004 running→blocked 許可待ち: Bash `git worktree add ../wt -b task/t004`' "$out"
expect "Bash(git worktree:\*)" "$($SEC permissions)"
$SEC allow "Bash(git worktree:*)" >/dev/null
grep -q 'Bash(git worktree:\*)' "$SECRETARY_HOME/worker-settings.json" || fail "allow が書かれていない"
expect "Bash(git worktree:\*).*許可済みなのに止まった" "$($SEC permissions)"

# peek は agent read で直近の画面を読む
expect "(screen of t004-" "$($SEC peek t004)"

# SECRETARY_TASK_ID がなければ hook は何もしない
before=$(wc -l < $SECRETARY_HOME/events.jsonl)
echo '{"hook_event_name":"Stop"}' | env -u SECRETARY_TASK_ID $SEC hook
[ "$(wc -l < $SECRETARY_HOME/events.jsonl)" -eq "$before" ] || fail "タスク外の hook が記録された"

$SEC list
echo "OK"
rm -rf "$TMP"
