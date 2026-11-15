#!/bin/sh
# 偽 herdr を使って、登録 → 起動 → hook → 状態遷移 → キュー の一連を確認する。
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SEC="python3 $HERE/../scripts/secretary.py"
TMP=$(mktemp -d)
export SECRETARY_HOME="$TMP/home" HERDR_BIN="$HERE/fake_herdr.sh" FAKE_HERDR_STATE="$TMP/herdr.json"
REPO="$TMP/repo"; mkdir -p "$REPO"
fail() { echo "FAIL: $*"; $SEC list --all; cat "$FAKE_HERDR_STATE.log"; exit 1; }
expect() { echo "$2" | grep -q -- "$1" || fail "expected '$1' in: $2"; }
hook() { # hook TASK EVENT [extra json]
  echo "{\"hook_event_name\":\"$2\"${3:-}}" | SECRETARY_TASK_ID=$1 $SEC hook
}
watch() { $SEC watch --once; }

$SEC init >/dev/null
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["max_parallel"]=2; c["snapshot_interval_sec"]=0; json.dump(c,open(p,"w"))
PY

# 完了条件なしは拒否
if $SEC new --repo "$REPO" --title x --request y 2>/dev/null; then fail "done なしで登録できた"; fi

out=$($SEC new --repo "$REPO" --title "N+1 修正" --done "spec が通る" --request "N+1 を直す")
expect "t001 queued→running" "$out"
$SEC new --repo "$REPO" --title "two" --done d --request r >/dev/null
out=$($SEC new --repo "$REPO" --title "three" --done d --request r)
expect "キュー待ち" "$out"
grep -q "pane run w1:p1 cd" "$FAKE_HERDR_STATE.log" || fail "pane run が呼ばれていない"
grep -q "claude --settings" "$FAKE_HERDR_STATE.log" || fail "claude 起動コマンドがない"

# 秘書からの最初のプロンプトは介入扱いにしない
hook t001 UserPromptSubmit ',"prompt":"[secretary] read prompt.md"'
out=$(watch); [ -z "$out" ] || fail "secretary prompt で通知が出た: $out"

# 許可待ち → 再開
hook t001 Notification ',"message":"Claude needs your permission to use Bash"'
out=$(watch); expect "t001 running→blocked 許可待ち" "$out"
hook t001 PostToolUse ',"tool_name":"Bash"'
out=$(watch); [ -z "$out" ] || fail "再開は通知しない: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"

# result.md なしで Stop → 入力待ち。直後の idle Notification は重ねない
hook t001 Stop
hook t001 Notification ',"message":"Claude is waiting for your input"'
out=$(watch); expect "running→blocked 入力待ち" "$out"
[ "$(echo "$out" | wc -l)" -eq 1 ] || fail "通知が重複: $out"

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

# ペインが消えたら異常
python3 - <<PY
import json; p="$FAKE_HERDR_STATE"; d=json.load(open(p)); d["panes"]=[x for x in d["panes"] if x["id"]!="w2:p1"]; json.dump(d,open(p,"w"))
PY
out=$(watch); expect "t002 running→blocked 異常: ペインが見つからない" "$out"

# SECRETARY_TASK_ID がなければ hook は何もしない
before=$(wc -l < $SECRETARY_HOME/events.jsonl)
echo '{"hook_event_name":"Stop"}' | env -u SECRETARY_TASK_ID $SEC hook
[ "$(wc -l < $SECRETARY_HOME/events.jsonl)" -eq "$before" ] || fail "タスク外の hook が記録された"

$SEC list
echo "OK"
rm -rf "$TMP"
