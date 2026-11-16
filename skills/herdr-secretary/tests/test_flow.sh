#!/bin/sh
# 偽 herdr を使って、登録 → 起動 → hook → 状態遷移 → キュー の一連を確認する。
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SEC="python3 $HERE/../scripts/secretary.py"
TMP=$(cd "$(mktemp -d)" && pwd -P)
# herdr のペインの中（作業エージェント自身など）で走らせても、そのペインやタスクを触らない
unset HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID SECRETARY_TASK_ID
export SECRETARY_HOME="$TMP/home" HERDR_BIN="$HERE/fake_herdr.py" FAKE_HERDR_STATE="$TMP/herdr.json"
REPO="$TMP/repo"; mkdir -p "$REPO"
git -C "$REPO" init -q
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q --allow-empty -m init
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

echo "- myrepo: $REPO — テスト用（worktrees: ../wt）" >> "$SECRETARY_HOME/repos.md"

# 完了条件なしは拒否
if $SEC new --repo "$REPO" --title x --request y 2>/dev/null; then fail "done なしで登録できた"; fi

# herdr が古い・サーバーと protocol が合わないときは登録せず、人間がやることを示す
echo 0.7.3 > "$FAKE_HERDR_STATE.version"
if out=$($SEC new --repo myrepo --title x --done d --request r 2>&1); then fail "古い herdr で登録できた"; fi
expect "herdr が古いです（0.7.3）。0.9.5 以上" "$out"
expect "0.9.5 以上" "$($SEC doctor --no-fetch)"
rm "$FAKE_HERDR_STATE.version"
touch "$FAKE_HERDR_STATE.mismatch"
if out=$($SEC new --repo myrepo --title x --done d --request r 2>&1); then fail "protocol 不一致で登録できた"; fi
expect "protocol が合いません" "$out"
expect "herdr server stop" "$out"
rm "$FAKE_HERDR_STATE.mismatch"
[ ! -d "$SECRETARY_HOME/tasks/t001" ] || fail "herdr の準備ができていないのにタスクを作った"

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
grep -q "\"--add-dir\", \"$TMP/wt\"" "$LOG" || fail "リポジトリの worktree 置き場を --add-dir で渡していない"
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
expect "Bash(bundle exec rspec:\*)" "$($SEC permissions)"
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

# バックグラウンドのサブエージェントの完了通知もユーザーターンとして届くが、人間の介入ではない。作業は再開する
hook t001 UserPromptSubmit ',"prompt":"<task-notification><task-id>a1</task-id><status>completed</status></task-notification>"'
out=$(watch); [ -z "$out" ] || fail "完了通知を介入扱いした: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"

# 選択式の質問は、質問と選択肢を通知に出す。許可ルールの候補には出さない
# 秘書が sec choose で番号を選べる。ペインで答えても介入扱いにしない（回答では UserPromptSubmit は来ず、PostToolUse で再開する）
Q='"tool_input":{"questions":[{"question":"どちらで直す？","header":"方針","multiSelect":false,"options":[{"label":"A 案","description":"x"},{"label":"B 案","description":"y"}]}]}'
hook t001 PreToolUse ",\"tool_name\":\"AskUserQuestion\",$Q"
hook t001 PermissionRequest ",\"tool_name\":\"AskUserQuestion\",$Q"
hook t001 Notification ',"message":"Claude needs your input"'
out=$(watch); expect "running→blocked 選択待ち: どちらで直す？ \[1\] A 案 \[2\] B 案" "$out"
[ "$(echo "$out" | wc -l)" -eq 1 ] || fail "選択待ちの通知が重複: $out"
$SEC permissions | grep -q AskUserQuestion && fail "選択式の質問を許可ルールの候補に出した"
echo t001 > "$FAKE_HERDR_STATE.blocked"
out=$($SEC reply t001 "B" 2>&1) && fail "選択待ちに文字を送れてしまった"
expect "sec choose t001" "$out"
rm "$FAKE_HERDR_STATE.blocked"
if $SEC choose t001 0 2>/dev/null; then fail "0 番を受け付けた"; fi
if $SEC choose t001 2 2>/dev/null; then fail "herdr から見て選択待ちでないのにキーを送った"; fi
python3 - <<PY
import json; p="$FAKE_HERDR_STATE"; d=json.load(open(p)); a=json.load(open("$SECRETARY_HOME/tasks/t001/meta.json"))["agent"]
d.setdefault("status",{})[a]="blocked"; json.dump(d,open(p,"w"))
PY
out=$($SEC choose t001 2); expect "2 番を選びました" "$out"
grep -qE '"agent", "send-keys", "t001-[0-9a-f]{6}", "down", "enter"\]' "$LOG" || fail "2 番を選ぶキーを送っていない"
hook t001 PostToolUse ',"tool_name":"AskUserQuestion"'
out=$(watch); [ -z "$out" ] || fail "質問への回答で通知: $out"
# PermissionRequest が来ず、Notification だけで止まったときも選択待ちとして知らせる
hook t001 PreToolUse ",\"tool_name\":\"AskUserQuestion\",$Q"
hook t001 Notification ',"message":"Claude is waiting for your input"'
out=$(watch); expect "running→blocked 選択待ち: どちらで直す？" "$out"
hook t001 PostToolUse ',"tool_name":"AskUserQuestion"'
out=$(watch); [ -z "$out" ] || fail "質問への回答で通知: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"
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

# review 中に秘書が直しを頼んだら running に戻る。result.md が書き直されたらまた review
out=$($SEC reply t001 "テスト名を直して"); expect "送りました" "$out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"
hook t001 UserPromptSubmit ',"prompt":"[secretary] テスト名を直して"'
out=$(watch); [ -z "$out" ] || fail "秘書の追加指示で通知: $out"
sleep 1; echo "fixed" > $SECRETARY_HOME/tasks/t001/result.md
hook t001 Stop
out=$(watch); expect "running→review" "$out"

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

# herdr サーバーを再起動するとペインのシェルが作り直され、タブに渡した SECRETARY_TASK_ID が消える。
# そのペインで retry しても、hook は HERDR_PANE_ID からタスクを引いて最初の指示の受領を記録する
grep -q "SECRETARY_HOME=$SECRETARY_HOME python3 .*secretary.py hook" "$SECRETARY_HOME/worker-settings.json" || fail "hook コマンドに SECRETARY_HOME がない"
P4=$(python3 -c "import json; print(json.load(open('$SECRETARY_HOME/tasks/t004/meta.json'))['pane'])")
pane_hook() { # pane_hook EVENT [extra json]
  echo "{\"hook_event_name\":\"$1\"${2:-}}" | env -u SECRETARY_TASK_ID HERDR_PANE_ID="$P4" $SEC hook
}
watch >/dev/null
$SEC retry t004 >/dev/null
pane_hook UserPromptSubmit ',"prompt":"[secretary] prompt.md を読んで"'
out=$(watch); [ -z "$out" ] || fail "環境変数が消えたペインで最初の指示が届いたのに通知: $out"
# イベントが届かず blocked になっていても、作業が進めば running に戻る
$SEC retry t004 >/dev/null
out=$(watch); expect "t004 running→blocked 起動画面で止まっている" "$out"
pane_hook PostToolUse ',"tool_name":"mcp__figma__get_design_context"'
watch >/dev/null
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t004/meta.json)"

# 許可ルールの候補と sec allow
hook t004 PermissionRequest ',"tool_name":"Bash","tool_input":{"command":"cd /x/y && git worktree add ../wt -b task/t004"}'
out=$(watch); expect 't004 running→blocked 許可待ち: Bash `git worktree add ../wt -b task/t004`' "$out"
expect "Bash(git worktree:\*)" "$($SEC permissions)"
$SEC allow "Bash(git worktree:*)" >/dev/null
grep -q 'Bash(git worktree:\*)' "$SECRETARY_HOME/worker-settings.json" || fail "allow が書かれていない"
expect "Bash(git worktree:\*).*許可済みなのに止まった" "$($SEC permissions)"
# 許可ダイアログも番号付きだが、秘書が choose で許可してはいけない
if $SEC choose t004 1 2>/dev/null; then fail "許可ダイアログを choose で選べた"; fi
grep -q '"send-keys", "t004' "$LOG" && fail "許可ダイアログにキーを送った"
hook t004 PostToolUse ',"tool_name":"Bash"'

# .claude/ 配下は保護パス。許可ルールの候補に出さず、続けて来た同じ許可待ちは通知を重ねない
hook t004 PermissionRequest ',"tool_name":"Edit","tool_input":{"file_path":"/x/wt/.claude/settings.json"}'
out=$(watch); expect 't004 running→blocked 許可待ち: Edit `/x/wt/.claude/settings.json`（保護パス）' "$out"
hook t004 PostToolUse ',"tool_name":"Edit"'
hook t004 PermissionRequest ',"tool_name":"Write","tool_input":{"file_path":"/x/wt/.claude/skills/a/SKILL.md"}'
hook t004 PermissionRequest ',"tool_name":"Edit","tool_input":{"file_path":"/x/wt/.claude/skills/b/SKILL.md"}'
out=$(watch); [ -z "$out" ] || fail "保護パスの許可待ちを続けて通知した: $out"
hook t004 PostToolUse ',"tool_name":"Write"'
hook t004 PermissionRequest ',"tool_name":"Edit","tool_input":{"file_path":"/x/wt/.claude/skills/c/SKILL.md"}'
out=$(watch); [ -z "$out" ] || fail "同じ許可待ちを続けて通知した: $out"
expect '"status": "blocked"' "$(cat $SECRETARY_HOME/tasks/t004/meta.json)"
hook t004 PostToolUse ',"tool_name":"Edit"'
watch >/dev/null
perm=$($SEC permissions)
expect "許可リストでは避けられないもの" "$perm"
expect "3 回（1 タスク）: Edit" "$perm"
echo "$perm" | sed -n '1,/避けられない/p' | grep -qE ': (Edit|Write) ' && fail "保護パスを許可ルールの候補に出した: $perm"

# peek は agent read で直近の画面を読む
expect "(screen of t004-" "$($SEC peek t004)"

# hook が届かないと running のまま時間だけが過ぎる。判定の前に herdr の agent_status を見て補正する
stale() { # stale HERDR_STATUS [pending_tool]
  python3 - "$1" "${2:-}" <<PY
import json,sys,time
from datetime import datetime,timezone
old=datetime.fromtimestamp(time.time()-200*60,timezone.utc).isoformat()
p="$SECRETARY_HOME/tasks/t004/meta.json"; m=json.load(open(p))
m.update(status="running", reason="", last_event_at=old); m.pop("silence_alerted",None); m.pop("pending_tool",None)
if sys.argv[2]: m["pending_tool"]={"name":"Bash","label":"Bash \`npm test\`","started_at":old}
json.dump(m,open(p,"w"))
h="$FAKE_HERDR_STATE"; d=json.load(open(h)); d.setdefault("status",{})[m["agent"]]=sys.argv[1]; json.dump(d,open(h,"w"))
PY
}
stale working; out=$(watch); [ -z "$out" ] || fail "herdr が working なのに異常通知: $out"
stale working tool; out=$(watch); [ -z "$out" ] || fail "herdr が working なのに長いコマンドの異常通知: $out"
stale blocked; out=$(watch); expect "t004 running→blocked 許可待ちか選択待ち（herdr で検知。hook が届いていない可能性" "$out"
stale idle tool; out=$(watch); expect "t004 running→blocked 入力待ち（herdr で検知。hook が届いていない可能性" "$out"
sleep 1; echo r > $SECRETARY_HOME/tasks/t004/result.md
stale done; out=$(watch); expect "t004 running→review result.md あり（herdr で検知" "$out"
stale unknown; out=$(watch); expect "t004 running→blocked 異常: 30 分イベントなし" "$out"

# 閉じたタスクのタブと worktree を、確認を取ってから片付ける
if $SEC cleanup t004 2>/dev/null; then fail "閉じていないタスクを片付けようとした"; fi
git -C "$REPO" worktree add -q "$SECRETARY_HOME/worktrees/t001" -b task/t001
git -C "$REPO" worktree add -q "$TMP/wt/other" -b other
out=$($SEC cleanup t001)
expect "タブ w1:t1 を閉じる" "$out"
expect "worktree $SECRETARY_HOME/worktrees/t001（ブランチ task/t001 は残す）" "$out"
echo "$out" | grep -q "other" && fail "関係ない worktree を候補にした: $out"
expect "人間の確認を取ってから" "$out"
[ -d "$SECRETARY_HOME/worktrees/t001" ] || fail "--yes なしで消した"
grep -q '"tab", "close"' "$LOG" && fail "--yes なしでタブを閉じた"
$SEC cleanup t001 --yes >/dev/null || fail "cleanup --yes が失敗"
[ ! -d "$SECRETARY_HOME/worktrees/t001" ] || fail "worktree が消えていない"
grep -q '"tab", "close", "w1:t1"' "$LOG" || fail "タブを閉じていない"
git -C "$REPO" rev-parse -q --verify task/t001 >/dev/null || fail "ブランチまで消した"
# 未コミットの変更がある worktree は git worktree remove が断るので、消さずに知らせる
git -C "$REPO" worktree add -q "$TMP/wt/fix-t003" -b fix-t003
echo dirty > "$TMP/wt/fix-t003/a.txt"
expect "未コミットの変更あり" "$($SEC cleanup t003 --keep-tab)"
if $SEC cleanup t003 --yes --keep-tab 2>/dev/null; then fail "変更のある worktree の片付けが成功扱い"; fi
[ -f "$TMP/wt/fix-t003/a.txt" ] || fail "変更のある worktree を消した"

# hook の向き先が古いスクリプトなら知らせる。init をやり直すと直り、許可リストは残る
python3 - <<PY
import json; p="$SECRETARY_HOME/worker-settings.json"; s=json.load(open(p))
for es in s["hooks"].values():
    for e in es:
        for h in e["hooks"]: h["command"]=h["command"].replace("secretary.py","old/secretary.py")
json.dump(s,open(p,"w"))
PY
expect "別の場所のスクリプトを呼んでいます" "$($SEC doctor --no-fetch)"
$SEC init >/dev/null
$SEC doctor --no-fetch | grep -q "別の場所" && fail "init をやり直しても hook の向き先が直らない"
grep -q 'Bash(git worktree:\*)' "$SECRETARY_HOME/worker-settings.json" || fail "init のやり直しで許可リストが消えた"

# スキルのリポジトリが upstream より遅れていたら知らせる（hook は pull するまで古いまま）
git init -q --bare "$TMP/up.git"
git clone -q "$TMP/up.git" "$TMP/skill" 2>/dev/null
mkdir -p "$TMP/skill/scripts"; cp "$HERE/../scripts/secretary.py" "$TMP/skill/scripts/"
git -C "$TMP/skill" add -A
git -C "$TMP/skill" -c user.email=t@example.com -c user.name=t commit -q -m one
git -C "$TMP/skill" push -q origin HEAD 2>/dev/null
git clone -q "$TMP/up.git" "$TMP/skill2"
git -C "$TMP/skill2" -c user.email=t@example.com -c user.name=t commit -q --allow-empty -m two
git -C "$TMP/skill2" push -q origin HEAD 2>/dev/null
expect "upstream より 1 コミット遅れています" "$(python3 "$TMP/skill/scripts/secretary.py" doctor)"
git -C "$TMP/skill" pull -q
python3 "$TMP/skill/scripts/secretary.py" doctor | grep -q "遅れています" && fail "pull 後も遅れていると出た"

# SECRETARY_TASK_ID がなく、タスクのペインでもなければ hook は何もしない
before=$(wc -l < $SECRETARY_HOME/events.jsonl)
echo '{"hook_event_name":"Stop"}' | env -u SECRETARY_TASK_ID -u HERDR_PANE_ID $SEC hook
echo '{"hook_event_name":"Stop"}' | env -u SECRETARY_TASK_ID HERDR_PANE_ID=w9:p9 $SEC hook
[ "$(wc -l < $SECRETARY_HOME/events.jsonl)" -eq "$before" ] || fail "タスク外の hook が記録された"
# 閉じたタスクのペインは引かない
P1=$(python3 -c "import json; print(json.load(open('$SECRETARY_HOME/tasks/t001/meta.json'))['pane'])")
echo '{"hook_event_name":"Stop"}' | env -u SECRETARY_TASK_ID HERDR_PANE_ID="$P1" $SEC hook
[ "$(wc -l < $SECRETARY_HOME/events.jsonl)" -eq "$before" ] || fail "閉じたタスクのペインの hook が記録された"

$SEC list
echo "OK"
rm -rf "$TMP"
