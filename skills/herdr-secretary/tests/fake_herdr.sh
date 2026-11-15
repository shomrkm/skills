#!/bin/sh
# テスト用の偽 herdr。呼び出しを記録し、ペインを JSON ファイルで管理する。
STATE="${FAKE_HERDR_STATE:?}"
[ -f "$STATE" ] || echo '{"panes":[]}' > "$STATE"
echo "$*" >> "$STATE.log"
case "$1 $2" in
  "api snapshot") cat "$STATE" ;;
  "tab create")
    n=$(python3 -c "import json;print(len(json.load(open('$STATE'))['panes'])+1)")
    python3 - "$STATE" "$n" <<'PY'
import json,sys
p,n=sys.argv[1],sys.argv[2]
d=json.load(open(p)); d["panes"].append({"id":f"w{n}:p1"}); json.dump(d,open(p,"w"))
PY
    ;;
  "pane run") ;;
  "agent prompt") ;;
  "pane read") echo "(fake screen of $3)" ;;
  *) echo "unknown: $*" >&2; exit 1 ;;
esac
