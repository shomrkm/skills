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
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["max_parallel"]=2; c["snapshot_interval_sec"]=0; c["notify"]="off"; c["review_backlog_alert"]=2; c["quiet_hours"]=""; json.dump(c,open(p,"w"))
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

# タブの英語名に英数字がなければ登録しない
if out=$($SEC new --repo myrepo --title x --name "注文の修正" --done d --request r 2>&1); then fail "英語でない名前で登録できた"; fi
expect "--name は英語で" "$out"
[ ! -d "$SECRETARY_HOME/tasks/t001" ] || fail "英語でない名前でタスクを作った"

# repos.md の名前で指定できる
out=$($SEC new --repo myrepo --title "N+1 修正" --name "Fix Order_Query" --done "spec が通る" --request "N+1 を直す")
expect "t001 queued→running" "$out"
$SEC new --repo "$REPO" --title "two" --done d --request r >/dev/null
out=$($SEC new --repo "$REPO" --title "三つ目" --done d --request r)
expect "キュー待ち" "$out"
LOG="$FAKE_HERDR_STATE.log"
grep -q "\"tab\", \"create\", \"--cwd\", \"$REPO\"" "$LOG" || fail "リポジトリでタブを作っていない"
# タブには ID と、内容がわかる英語名（kebab-case）を出す。--name がなければ英語の題から作り、英語でない題なら ID だけ
grep -q '"--label", "t001 fix-order-query"' "$LOG" || fail "タブ名に英語名が付いていない"
grep -q '"--label", "t002 two"' "$LOG" || fail "英語の題からタブ名を作っていない"
expect '"name": ""' "$(cat $SECRETARY_HOME/tasks/t003/meta.json)"
grep -q '"--env", "SECRETARY_TASK_ID=t001"' "$LOG" || fail "タスク ID を環境変数で渡していない"
grep -qE '"agent", "start", "t001-[0-9a-f]{6}", "--kind", "claude", "--pane", "w1:p1"' "$LOG" || fail "agent start が呼ばれていない"
grep -q '"--", "--settings", ".*worker-settings.json"' "$LOG" || fail "worker-settings を渡していない"
# repos.md の worktree 置き場は --add-dir で渡すと起動のたびに信頼確認で止まるので、worker-settings に足す
grep -q '"--add-dir"' "$LOG" && fail "--add-dir で渡している（起動のたびに信頼確認が出る）"
trusted() { # trusted DIR: worker-settings の additionalDirectories と許可リストに DIR が入っているか
  python3 - "$1" <<PY
import json,sys
d=sys.argv[1]; p=json.load(open("$SECRETARY_HOME/worker-settings.json"))["permissions"]
sys.exit(0 if d in p["additionalDirectories"] and f"Edit(/{d}/**)" in p["allow"] and f"Read(/{d}/**)" in p["allow"] else 1)
PY
}
trusted "$TMP/wt" || fail "リポジトリの worktree 置き場を worker-settings に足していない"
[ -d "$TMP/wt" ] || fail "worktree 置き場を作っていない"
[ "$(grep -c "\"$TMP/wt\"" "$SECRETARY_HOME/worker-settings.json")" -eq 1 ] || fail "起動のたびに同じディレクトリを重ねて足した"
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
# 複数行の返事は貼り付けとして入力され、<pasted_content> で包まれて届く。これも秘書からの返事
hook t001 UserPromptSubmit ',"prompt":"\n\n<pasted_content id=\"b537\">\n[secretary] 1. A\n2. B\n</pasted_content id=\"b537\">\n"'
out=$(watch); [ -z "$out" ] || fail "複数行の返事を介入扱いした: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"

# 人間がペインで bash モード（! pwd）を使うと Stop だけが来る。作業エージェントは何も聞いていないので通知しない
TR="$TMP/transcript.jsonl"
cat > "$TR" <<'JSONL'
{"type":"assistant","message":{"content":[{"type":"text","text":"作業します"}]}}
{"type":"user","message":{"content":"<bash-input>pwd</bash-input>"}}
{"type":"user","message":{"content":"<bash-stdout>/x</bash-stdout><bash-stderr></bash-stderr>"}}
{"type":"file-history-snapshot"}
JSONL
hook t001 Stop ",\"transcript_path\":\"$TR\""
out=$(watch); [ -z "$out" ] || fail "bash モードで入力待ちと通知した: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t001/meta.json)"
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"どちらにしますか？"}]}}' >> "$TR"
hook t001 Stop ",\"transcript_path\":\"$TR\""
out=$(watch); expect "running→blocked 入力待ち" "$out"

# 人間が入力欄に打ちかけているときは、秘書から送らない（混ざるため）。空の入力欄の入力例は人間の文字ではない
screen() { # screen TEXT: 入力欄に TEXT が入った画面にする
  printf '❯ [secretary] prompt.md を読んで\n\n⏺ 作業しました\n\n%s\n❯ %s\n%s\n  ⏵⏵ accept edits on\n' \
    "──────────────────────" "$1" "──────────────────────" > "$FAKE_HERDR_STATE.screen"
}
screen "いや、やっぱり"
if out=$($SEC reply t001 "方針 B" 2>&1); then fail "人間の入力中に送った"; fi
expect "人間が入力中なので送りませんでした" "$out"
grep -q '"\[secretary\] 方針 B"' "$LOG" && fail "人間の入力中に送った"
expect "送りました" "$($SEC reply t001 "方針 B" --force)"
screen 'Try "fix lint errors"'
expect "送りました" "$($SEC reply t001 "方針 C")"
rm "$FAKE_HERDR_STATE.screen"
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
# 選択肢にない答えは --text で「Type something.」に打ち込む。質問が複数あれば、答えた数から今の質問の選択肢を数える
Q2='"tool_input":{"questions":[{"question":"q1","multiSelect":false,"options":[{"label":"A"},{"label":"B"}]},{"question":"q2","multiSelect":false,"options":[{"label":"X"},{"label":"Y"},{"label":"Z"}]},{"question":"q3","multiSelect":true,"options":[{"label":"M"}]}]}'
hook t001 PreToolUse ",\"tool_name\":\"AskUserQuestion\",$Q2"
hook t001 PermissionRequest ",\"tool_name\":\"AskUserQuestion\",$Q2"
watch >/dev/null
if $SEC choose t001 2 --text x 2>/dev/null; then fail "番号と --text を両方受け付けた"; fi
if $SEC choose t001 --text "  " 2>/dev/null; then fail "空の --text を受け付けた"; fi
$SEC choose t001 1 >/dev/null
out=$($SEC choose t001 --text "W 案で
進めて"); expect "自由記述で答えました" "$out"
tail -3 "$LOG" | grep -qE '"agent", "send-keys", "t001-[0-9a-f]{6}", "down", "down", "down"\]' || fail "q2 の Type something. まで動かしていない"
tail -2 "$LOG" | grep -q '"pane", "send-text", "w1:p1", "W 案で 進めて"' || fail "自由記述を 1 行にして打っていない"
tail -1 "$LOG" | grep -qE '"agent", "send-keys", "t001-[0-9a-f]{6}", "enter"\]' || fail "自由記述の後に Enter を送っていない"
if out=$($SEC choose t001 --text "M も" 2>&1); then fail "複数選択の質問に --text で答えた"; fi
expect "ペイン w1:p1 で直接答えてください" "$out"
hook t001 PostToolUse ',"tool_name":"AskUserQuestion"'
watch >/dev/null
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
grep -q '"--label", "t003", ' "$LOG" || fail "英語でない題のタブ名が ID だけになっていない"

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

# close するとタブも閉じる。作業場所は消さない
out=$($SEC close t001)
expect "タブ w1:t1 を閉じました。作業場所（worktree）は残っています" "$out"
grep -q '"tab", "close", "w1:t1"' "$LOG" || fail "close でタブを閉じていない"
expect "タブはもうありません" "$($SEC close t001)"

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
out=$($SEC new --repo myrepo --title four --name "Show descriptive English names on herdr tabs" --done d --request r)
expect "t004 queued→blocked 起動時の確認待ち" "$out"
grep -q '"--label", "t004 show-descriptive-english"' "$LOG" || fail "長い英語名を単語の切れ目で縮めていない"
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

# 最初の指示が入力欄に残ったまま送信されていないだけなら、確認画面とは区別して Enter を 1 回送り直す
$SEC retry t004 >/dev/null
prompts=$(grep -cE '"agent", "prompt", "t004-' "$LOG")
screen "[secretary] $SECRETARY_HOME/tasks/t004/prompt.md を読んで、その指示に従って作業してください。"
out=$(watch); [ -z "$out" ] || fail "入力欄に残った指示を起動画面と通知した: $out"
tail -1 "$LOG" | grep -qE '"agent", "send-keys", "t004-[0-9a-f]{6}", "enter"\]' || fail "入力欄に残った指示に Enter を送っていない"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t004/meta.json)"
# Enter を送っても残っていたら、起動画面ではなく入力欄に残っていると知らせる
out=$(watch); expect "t004 running→blocked 最初の指示が入力欄に残ったまま送信されない" "$out"
# retry は指示を打ち直さず（2 つ並ぶので）、Enter だけ送る
$SEC retry t004 >/dev/null
[ "$(grep -cE '"agent", "prompt", "t004-' "$LOG")" -eq "$prompts" ] || fail "入力欄に残っているのに指示を打ち直した"
tail -1 "$LOG" | grep -qE '"agent", "send-keys", "t004-[0-9a-f]{6}", "enter"\]' || fail "retry で Enter を送っていない"
# 人間が入力欄に打ちかけていたら retry しない
screen "ちょっと待って"
if out=$($SEC retry t004 2>&1); then fail "人間の入力中に retry した"; fi
expect "人間に送るか消してもらってから" "$out"
rm "$FAKE_HERDR_STATE.screen"
hook t004 UserPromptSubmit ',"prompt":"[secretary] prompt.md を読んで"'
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
keys=$(grep -c '"send-keys", "t004' "$LOG")
if $SEC choose t004 1 2>/dev/null; then fail "許可ダイアログを choose で選べた"; fi
[ "$(grep -c '"send-keys", "t004' "$LOG")" -eq "$keys" ] || fail "許可ダイアログにキーを送った"
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

# タスクの worktree の中の .claude/skills/ などは、PermissionRequest hook が allow を返して確認なしで通す
for f in "$TMP/wt/t004/.claude/skills/a/SKILL.md" "$SECRETARY_HOME/worktrees/t004/sub/.claude/commands/x.md" "$REPO/.claude/worktrees/t004/.claude/agents/y.md"; do
  out=$(hook t004 PermissionRequest ",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$f\"}")
  expect '"behavior": "allow"' "$out"
done
out=$(watch); [ -z "$out" ] || fail "自動で許可したのに通知した: $out"
expect '"status": "running"' "$(cat $SECRETARY_HOME/tasks/t004/meta.json)"
# settings.json や worktree の置き場そのものの .claude/、置き場の外、タスクフォルダは自動で許可しない
for f in "$TMP/wt/t004/.claude/settings.json" "$TMP/wt/.claude/skills/a/SKILL.md" "$TMP/wt/t004/.claude/skills/../../../../etc/.claude/skills/a/x.md" \
         "/elsewhere/t004/.claude/skills/a/SKILL.md" "$SECRETARY_HOME/tasks/t004/.claude/skills/a/SKILL.md"; do
  out=$(hook t004 PermissionRequest ",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$f\"}")
  [ -z "$out" ] || fail "自動で許可してはいけないものを許可した: $f"
done
out=$(hook t004 PermissionRequest ',"tool_name":"Bash","tool_input":{"command":"rm -rf .claude/skills"}')
[ -z "$out" ] || fail "Bash を自動で許可した"
out=$(watch); expect 't004 blocked→blocked 許可待ち: Bash `rm -rf .claude/skills`' "$out"
hook t004 PostToolUse ',"tool_name":"Bash"'
watch >/dev/null

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
expect "タブは残しています" "$($SEC close t004 --keep-tab)"
T4=$(python3 -c "import json; print(json.load(open('$SECRETARY_HOME/tasks/t004/meta.json'))['tab'])")
grep -q "\"tab\", \"close\", \"$T4\"" "$LOG" && fail "--keep-tab なのにタブを閉じた"
git -C "$REPO" worktree add -q "$SECRETARY_HOME/worktrees/t001" -b task/t001
git -C "$REPO" worktree add -q "$TMP/wt/other" -b other
out=$($SEC cleanup t001)
expect "閉じるタブはありません" "$out"
expect "worktree $SECRETARY_HOME/worktrees/t001（ブランチ task/t001 は残す）" "$out"
echo "$out" | grep -q "other" && fail "関係ない worktree を候補にした: $out"
expect "人間の確認を取ってから" "$out"
[ -d "$SECRETARY_HOME/worktrees/t001" ] || fail "--yes なしで消した"
$SEC cleanup t001 --yes >/dev/null || fail "cleanup --yes が失敗"
[ ! -d "$SECRETARY_HOME/worktrees/t001" ] || fail "worktree が消えていない"
git -C "$REPO" rev-parse -q --verify task/t001 >/dev/null || fail "ブランチまで消した"
# close --keep-tab で残したタブは cleanup で閉じる
expect "タブ $T4 を閉じる" "$($SEC cleanup t004 --keep-worktree)"
grep -q "\"tab\", \"close\", \"$T4\"" "$LOG" && fail "--yes なしでタブを閉じた"
$SEC cleanup t004 --yes --keep-worktree >/dev/null || fail "cleanup --yes でタブを閉じられない"
grep -q "\"tab\", \"close\", \"$T4\"" "$LOG" || fail "cleanup でタブを閉じていない"
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
# init のやり直しでは、足したディレクトリを残し、repos.md の worktree 置き場もまとめて足す
REPO2="$TMP/repo2"; mkdir -p "$REPO2"
echo "- other: $REPO2 — テスト用（worktrees: $TMP/wt2）" >> "$SECRETARY_HOME/repos.md"
$SEC init >/dev/null
trusted "$TMP/wt" || fail "init のやり直しで worktree 置き場が消えた"
trusted "$TMP/wt2" || fail "init で repos.md の worktree 置き場を足していない"
trusted "$SECRETARY_HOME/tasks" || fail "init でタスクフォルダを足していない"
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

# 依頼に添える資料はタスクフォルダにコピーし、prompt.md にパスを書く
echo "個人 Notion の本文" > "$TMP/notion-page.md"
if $SEC new --repo myrepo --title five --done d --request r --attach "$TMP/nothing.md" 2>/dev/null; then fail "ない資料で登録できた"; fi
out=$($SEC new --repo myrepo --title five --done d --request r --attach "$TMP/notion-page.md")
expect "t005 queued→running" "$out"
[ "$(cat $SECRETARY_HOME/tasks/t005/attachments/notion-page.md)" = "個人 Notion の本文" ] || fail "資料をコピーしていない"
expect "依頼に添える資料" "$(cat $SECRETARY_HOME/tasks/t005/prompt.md)"
expect "- \`$SECRETARY_HOME/tasks/t005/attachments/notion-page.md\`" "$(cat $SECRETARY_HOME/tasks/t005/prompt.md)"
grep -q "依頼に添える資料" "$SECRETARY_HOME/tasks/t001/prompt.md" && fail "資料がないのに見出しを書いた"
hook t005 UserPromptSubmit ',"prompt":"[secretary] prompt.md を読んで"'
watch >/dev/null

# 静かな時間帯（既定 18:00〜09:00）は通知を出さず、明けたら溜まった変化を 1 行にまとめて出す
python3 - "$HERE/../scripts" <<'PY'
import sys; sys.path.insert(0, sys.argv[1])
from datetime import datetime
from secretary import in_quiet_hours
c = {"quiet_hours": "18:00-09:00"}
at = lambda h, m: in_quiet_hours(c, datetime(2026, 11, 18, h, m))
assert at(18, 0) and at(23, 30) and at(0, 0) and at(8, 59), "夜の間が静かになっていない"
assert not at(9, 0) and not at(12, 0) and not at(17, 59), "昼に静かになった"
assert in_quiet_hours({"quiet_hours": "12:00-13:00"}, datetime(2026, 11, 18, 12, 30))
assert not in_quiet_hours({"quiet_hours": ""}, datetime(2026, 11, 18, 23, 0))
PY
quiet() { # quiet on|off: 今を含む・含まない時間帯にする
  python3 - "$1" <<PY
import json,sys
from datetime import datetime,timedelta
n=datetime.now(); f=lambda d: (n+timedelta(hours=d)).strftime("%H:%M")
p="$SECRETARY_HOME/config.json"; c=json.load(open(p))
c["quiet_hours"]=f"{f(-1)}-{f(1)}" if sys.argv[1]=="on" else f"{f(2)}-{f(3)}"; json.dump(c,open(p,"w"))
PY
}
quiet on
hook t005 Stop
out=$(watch); [ -z "$out" ] || fail "静かな時間帯に通知した: $out"
hook t005 UserPromptSubmit ',"prompt":"自分でやる"'
out=$(watch); [ -z "$out" ] || fail "静かな時間帯に通知した: $out"
expect '"status": "manual"' "$(cat $SECRETARY_HOME/tasks/t005/meta.json)"
quiet off
out=$(watch)
[ "$(echo "$out" | wc -l)" -eq 1 ] || fail "まとめて 1 行で出していない: $out"
expect "通知を止めていた間（[0-9:]*〜[0-9:]*）の変化 2 件: t005 running→blocked 入力待ち.* / t005 blocked→manual" "$out"
out=$(watch); [ -z "$out" ] || fail "まとめを 2 回出した: $out"

# follow は裏の監視ループの出力を、まだ出していない行から出す（Monitor が切れても取りこぼさない）
sleep 60 & SLEEP=$!
echo $SLEEP > "$SECRETARY_HOME/.watch.pid"
printf '[secretary] a\n' > "$SECRETARY_HOME/watch.log"
echo 0 > "$SECRETARY_HOME/.follow-offset"
[ "$($SEC follow --once)" = "[secretary] a" ] || fail "follow が最初の行を出していない"
printf '[secretary] b\n[secretary] c\n' >> "$SECRETARY_HOME/watch.log"
[ "$($SEC follow --once)" = "$(printf '[secretary] b\n[secretary] c')" ] || fail "follow が続きから出していない"
kill $SLEEP
# 監視ループが動いていなければ、切り離して起動する
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["update_check"]=False; json.dump(c,open(p,"w"))
PY
$SEC follow --once > "$TMP/follow.out" & FOLLOW=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  WPID=$(cat "$SECRETARY_HOME/.watch.pid" 2>/dev/null || true)
  [ -n "$WPID" ] && [ "$WPID" != "$SLEEP" ] && kill -0 "$WPID" 2>/dev/null && break
  sleep 0.5
done
kill $FOLLOW 2>/dev/null || true
[ "$WPID" != "$SLEEP" ] && kill -0 "$WPID" 2>/dev/null || fail "follow が監視ループを起動していない"
kill "$WPID"
grep -q "follow" "$SECRETARY_HOME/CLAUDE.md" || fail "秘書の CLAUDE.md が sec follow を使っていない"

$SEC list
echo "OK"
rm -rf "$TMP"
