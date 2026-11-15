#!/bin/sh
# 本物の Claude Code で、作業エージェントの hook → 監視ループ → review の流れを確認する。
# claude -p を 1 回呼ぶ（数十秒、少しだけ API を使う）。herdr は不要。
set -eu
command -v claude >/dev/null || { echo "SKIP: claude がない"; exit 0; }
HERE=$(cd "$(dirname "$0")" && pwd)
SEC="python3 $HERE/../scripts/secretary.py"
TMP=$(mktemp -d); export SECRETARY_HOME="$TMP/home" HERDR_BIN=/nonexistent
mkdir -p "$TMP/repo"
$SEC init >/dev/null
python3 - <<PY
import json; p="$SECRETARY_HOME/config.json"; c=json.load(open(p)); c["notify"]="off"; json.dump(c,open(p,"w"))
PY
$SEC new --repo "$TMP/repo" --title "hook 確認" --done "result.md に 1 行書く" --request "何もしなくてよい" --no-launch >/dev/null
python3 - <<PY
import json; p="$SECRETARY_HOME/tasks/t001/meta.json"; m=json.load(open(p)); m["status"]="running"; json.dump(m,open(p,"w"))
PY
cd "$TMP/repo"
SECRETARY_TASK_ID=t001 claude -p "[secretary] $SECRETARY_HOME/tasks/t001/prompt.md を読んで。まず bash で 'ls /etc | head -1' を試して（許可されなければ諦めてよい）。最後に完了条件どおり result.md を書いて終わって。" \
  --settings "$SECRETARY_HOME/worker-settings.json" </dev/null >/dev/null
out=$($SEC watch --once)
echo "$out"
echo "$out" | grep -q "許可待ち: Bash \`ls /etc" || { echo "FAIL: 許可待ちが検知されない"; exit 1; }
echo "$out" | grep -q "→review" || { echo "FAIL: review にならない"; exit 1; }
echo "OK"; rm -rf "$TMP"
