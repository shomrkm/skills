#!/bin/sh
# 偽のデスクトップ版 herdr（herdr-app と hc）を使って、デスクトップアプリの中の秘書から
# 登録 → space とタブ → hook → 返事 → 選択 → 異常検知 → retry → bring → close の一連を確認する。
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SEC="python3 $HERE/../scripts/secretary.py"
TMP=$(cd "$(mktemp -d)" && pwd -P)
unset HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID SECRETARY_TASK_ID
ln -s "$HERE/fake_herdr_app.py" "$TMP/herdr-app"
ln -s "$HERE/fake_herdr_app.py" "$TMP/hc"
# 秘書はデスクトップアプリの端末 t0 にいる。ターミナル版の herdr は呼ばれてはいけない
export HC_TERMINAL_ID=t0 SECRETARY_HOME="$TMP/home" FAKE_DESKTOP_STATE="$TMP/desktop.json" \
  HERDR_APP_BIN="$TMP/herdr-app" HC_BIN="$TMP/hc" HERDR_BIN="$TMP/no-terminal-herdr"
REPO="$TMP/repo"; mkdir -p "$REPO"
git -C "$REPO" init -q
LOG="$FAKE_DESKTOP_STATE.log"
fail() { echo "FAIL: $*"; $SEC list --all; cat "$LOG"; exit 1; }
expect() { echo "$2" | grep -q -- "$1" || fail "expected '$1' in: $2"; }
meta() { python3 -c "import json,sys; print(json.load(open('$SECRETARY_HOME/tasks/$1/meta.json')).get('$2') or '')"; }
fake() { # fake PYTHON: 偽のデスクトップの状態を書き換える（s が状態）
  python3 - <<PY
import json; p="$FAKE_DESKTOP_STATE"; s=json.load(open(p))
$1
json.dump(s,open(p,"w"))
PY
}
hook() { echo "{\"hook_event_name\":\"$2\"${3:-}}" | HC_TERMINAL_ID=$1 $SEC hook; }
watch() { $SEC watch --once; }

$SEC init >/dev/null
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c.update(snapshot_interval_sec=0, notify="off", quiet_hours="", desktop_start_timeout_sec=1); json.dump(c,open(p,"w"))
PY
echo "- myrepo: $REPO — テスト用" >> "$SECRETARY_HOME/repos.md"

expect "デスクトップ版" "$($SEC doctor --no-fetch || true)"

# 登録すると、デスクトップアプリに space とタブを作る。表示中の space は切り替えない（偽物が focus を確かめる）
out=$($SEC new --repo myrepo --title "fix x" --name fix-x --space orders --done d --request r)
expect "t001 queued→running" "$out"
[ "$(meta t001 backend)" = desktop ] || fail "タスクにデスクトップ版で動かしたことを残していない"
T1=$(meta t001 terminal); P1=$(meta t001 pane)
[ -n "$T1" ] && [ -n "$P1" ] || fail "端末 ID とペインを記録していない"
grep -q '"herdr-app", "do", "space.new"' "$LOG" || fail "space を herdr-app で作っていない"
grep '"space.new"' "$LOG" | grep -q '\\"name\\": \\"orders\\"' || fail "space の名前が違う"
grep '"space.new"' "$LOG" | grep -q '\\"tab\\": \\"t001 fix-x\\"' || fail "タブ名が違う"
grep '"space.new"' "$LOG" | grep -q 'SECRETARY_TASK_ID=t001' || fail "タスク ID を渡していない"
grep '"space.new"' "$LOG" | grep -q 'worker-settings.json' || fail "worker-settings を渡していない"
grep '"space.new"' "$LOG" | grep -q '\\"task\\": \\"\[secretary\] .*prompt.md' || fail "最初の指示を渡していない"
grep -q '"herdr-app", "do", "terminal.peek"' "$LOG" && fail "sec new で秘書の画面に重ねて出した"

# 同じ space のタスクは同じ space にタブを足す
$SEC new --repo myrepo --title "two" --space orders --done d --request r >/dev/null
SP=$(meta t001 workspace)
grep '"tab.new"' "$LOG" | grep -q "\\\\\"space\\\\\": \\\\\"$SP\\\\\"" || fail "同じ space にタブを足していない"
[ "$(grep -c '"space.new"' "$LOG")" -eq 1 ] || fail "同じ space なのに space を 2 つ作った"
T2=$(meta t002 terminal)

# SECRETARY_TASK_ID がなくても、hook は HC_TERMINAL_ID からタスクを引ける
hook "$T1" UserPromptSubmit ',"prompt":"[secretary] read prompt.md"'
out=$(watch); [ -z "$out" ] || fail "秘書の最初の指示で通知: $out"
[ -z "$(meta t001 awaiting_ack_since)" ] || fail "HC_TERMINAL_ID から最初の指示の受け取りを記録していない"

# 返事は hc send で送る
hook "$T1" Stop
out=$(watch); expect "t001 running→blocked 入力待ち" "$out"
out=$($SEC reply t001 "方針 A で"); expect "送りました" "$out"
grep -q "\"hc\", \"send\", \"$T1\", \"\[secretary\] 方針 A で\"" "$LOG" || fail "hc send で返事を送っていない"
# 入力欄に人間の打ちかけがあれば送らない
printf '─────────────\n❯ 打ちかけ\n─────────────\n' > "$FAKE_DESKTOP_STATE.screen"
if $SEC reply t001 "x" 2>/dev/null; then fail "打ちかけの文字があるのに送った"; fi
rm "$FAKE_DESKTOP_STATE.screen"

# 選択式の質問。hc keys で選び、自由記述は hc send --force で打つ
fake "s['terminals']['$T1']['status']='blocked'"
Q=',"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"どちら？","options":[{"label":"A"},{"label":"B"}]}]}'
hook "$T1" PreToolUse "$Q"; hook "$T1" PermissionRequest "$Q"
out=$(watch); expect "選択待ち: どちら？ \[1\] A \[2\] B" "$out"
if $SEC reply t001 "B で" 2>/dev/null; then fail "選択待ちに文字で返事を送った"; fi
$SEC choose t001 2 >/dev/null
grep -q "\"hc\", \"keys\", \"$T1\", \"down\", \"enter\"" "$LOG" || fail "hc keys で選んでいない"
hook "$T1" PreToolUse "$Q"; hook "$T1" PermissionRequest "$Q"
watch >/dev/null
$SEC choose t001 --text "C にして" >/dev/null
grep -q "\"hc\", \"send\", \"--force\", \"$T1\", \"C にして\"" "$LOG" || fail "自由記述を hc send --force で送っていない"
fake "s['terminals']['$T1']['status']='working'"
hook "$T1" PostToolUse ',"tool_name":"AskUserQuestion"'
watch >/dev/null

# 画面は hc read で読む
expect "(screen of $T1)" "$($SEC peek t001 --lines 5)"

# Claude Code が終わってシェルに戻ったら知らせ、retry で同じ端末に起動し直す
fake "s['terminals']['$T1'].update(agent='zsh', status=None)"
out=$(watch); expect "t001 running→blocked 異常: 作業エージェントが終了した" "$out"
out=$($SEC retry t001); expect "送り直しました" "$out"
grep -q "\"hc\", \"send\", \"$T1\", \"env SECRETARY_TASK_ID=t001 .*claude --settings" "$LOG" || fail "シェルで Claude Code を起動し直していない"
grep -q "\"hc\", \"send\", \"$T1\", \"\[secretary\] .*prompt.md" "$LOG" || fail "retry で最初の指示を送っていない"

# sec bring は、タスクの端末を秘書の画面に重ねて出す
out=$($SEC bring t002); expect "重ねて出しました" "$out"
grep -q "\"terminal.peek\", \"{\\\\\"terminal\\\\\": \\\\\"$T2\\\\\"}\"" "$LOG" || fail "terminal.peek で出していない"
expect "戻すものはありません" "$($SEC bring --back)"

# ペインが消えたら知らせる
fake "s['terminals'].pop('$T2'); [sp['tabs'].pop(k) for sp in s['spaces'].values() for k in [k for k, t in sp['tabs'].items() if '$T2' in t['terminals']]]"
out=$(watch); expect "t002 running→blocked 異常: ペインが見つからない" "$out"

# Claude Code が起動しなければ、起動時の確認待ちとして知らせる
touch "$FAKE_DESKTOP_STATE.not_ready"
out=$($SEC new --repo myrepo --title "three" --done d --request r)
expect "t003 queued→blocked 起動時の確認待ち" "$out"
rm "$FAKE_DESKTOP_STATE.not_ready"
# 人間が確認画面を進めると、アプリが最初の指示を送る。届いたら running に戻し、retry させない
fake "s['terminals']['$(meta t003 terminal)'].update(agent='claude-code', status='working')"
hook "$(meta t003 terminal)" UserPromptSubmit ',"prompt":"[secretary] read prompt.md"'
watch >/dev/null
[ "$(meta t003 status)" = running ] || fail "最初の指示が届いたのに blocked のまま"

# close はタブを閉じる。space に残るのがそのタブだけなら space ごと閉じる
$SEC close t002 >/dev/null
out=$($SEC close t001); expect "space（${SP}）ごと閉じました" "$out"
grep -q '"herdr-app", "do", "space.close"' "$LOG" || fail "space を閉じていない"

# ターミナル版で起動していたタスクは、デスクトップ版の秘書からもターミナル版で操作する
python3 - <<PY
import json; p="$SECRETARY_HOME/tasks/t003/meta.json"; m=json.load(open(p)); m.update(backend="herdr", pane="w1:p1"); json.dump(m,open(p,"w"))
PY
if out=$($SEC peek t003 2>&1); then fail "ターミナル版の herdr がないのに読めた"; fi
expect "no-terminal-herdr" "$out"

echo "OK"
rm -rf "$TMP"
