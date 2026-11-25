#!/usr/bin/env python3
"""テスト用の偽のデスクトップ版 herdr。`herdr-app` と `hc` の名前でシンボリックリンクを張って使い、
呼ばれた名前でどちらとして振る舞うかを決める。引数と JSON の形は Herdr.app と herdr-core 0.1.22 に合わせてある。

状態は $FAKE_DESKTOP_STATE（JSON）に置き、呼び出しは $FAKE_DESKTOP_STATE.log に記録する（先頭は herdr-app か hc）。
- "spaces": space ID → {"name", "tabs": {タブ ID → {"name", "terminals": [端末 ID]}}}
- "terminals": 端末 ID → {"pane", "agent", "status", "state", "argv", "task"}
argv に claude を含む端末は Claude Code（agent が claude-code、status が idle）として作る。
$FAKE_DESKTOP_STATE.not_ready があると、Claude Code が起動しない（agent が zsh のまま）。
$FAKE_DESKTOP_STATE.screen に書いた内容を hc read が画面として返す。
"""
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_DESKTOP_STATE"])
role = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(f"{state_path}.log", "a") as f:
    f.write(json.dumps([role, *args], ensure_ascii=False) + "\n")
state = json.loads(state_path.read_text()) if state_path.exists() else {"spaces": {}, "terminals": {}, "n": 0}
spaces, terminals = state["spaces"], state["terminals"]


def save():
    state_path.write_text(json.dumps(state))


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


def new_id(prefix):
    state["n"] += 1
    return f"{prefix}{state['n']}"


def new_terminal(params):
    state["tn"] = state.get("tn", 0) + 1
    tid = f"t{state['tn']}"
    argv = params.get("argv") or []
    claude = "claude" in argv and not Path(f"{state_path}.not_ready").exists()
    terminals[tid] = {"pane": new_id("p"), "agent": "claude-code" if claude else "zsh",
                      "status": "idle" if claude else None, "state": "live", "argv": argv, "task": params.get("task")}
    return tid


def drop_terminals(tids):
    for t in tids:
        terminals.pop(t, None)


if role == "herdr-app":
    cmd = args[0] if args else ""
    if cmd == "status":
        out({"space": next(iter(spaces), None), "overlays": [], "errors": []})
    if cmd == "ls":
        out({"spaces": [{"id": sid, "name": s["name"], "tabs": [
            {"id": tab_id, "name": tab["name"], "terminals": [
                {"terminal": t, "pane": terminals[t]["pane"], "status": terminals[t]["status"],
                 "app": terminals[t]["agent"] if terminals[t]["agent"] == "claude-code" else None}
                for t in tab["terminals"] if t in terminals]}
            for tab_id, tab in s["tabs"].items()]} for sid, s in spaces.items()], "recent": []})
    if cmd == "do":
        name, params = args[1], json.loads(args[2]) if len(args) > 2 else {}
        if name in ("space.new", "tab.new"):
            assert params.get("focus") is False, "表示している space を切り替えてはいけない"
        if name == "space.new":
            sid, tab_id = new_id("sp"), new_id("tb")
            tid = new_terminal(params)
            spaces[sid] = {"name": params.get("name"), "tabs": {tab_id: {"name": params.get("tab"), "terminals": [tid]}}}
            save()
            out({"space": sid, "tab": tab_id, "terminal": tid})
        if name == "tab.new":
            sid = params.get("space")
            if sid not in spaces:
                print(json.dumps({"error": f"no space {sid}"}))
                sys.exit(1)
            tab_id = new_id("tb")
            tid = new_terminal(params)
            spaces[sid]["tabs"][tab_id] = {"name": params.get("name"), "terminals": [tid]}
            save()
            out({"space": sid, "tab": tab_id, "terminal": tid})
        if name == "tab.close":
            for s in spaces.values():
                if params["id"] in s["tabs"]:
                    drop_terminals(s["tabs"].pop(params["id"])["terminals"])
                    save()
                    out({"ok": True})
            print(json.dumps({"error": f"no tab {params['id']}"}))
            sys.exit(1)
        if name == "space.close":
            s = spaces.pop(params["id"])
            for tab in s["tabs"].values():
                drop_terminals(tab["terminals"])
            save()
            out({"ok": True})
        if name == "pane.close":
            drop_terminals([t for t, v in terminals.items() if v["pane"] == params["pane"]])
            save()
            out({"ok": True})
        if name == "terminal.peek":
            if params["terminal"] not in terminals:
                print(json.dumps({"error": f"no terminal {params['terminal']}"}))
                sys.exit(1)
            out({"ok": True})


def hc_err(kind, message, code):
    print(json.dumps({"error": {"kind": kind, "message": message, "hint": ""}}), file=sys.stderr)
    sys.exit(code)


def target(t):
    if t not in terminals:
        hc_err("not_found", f'no terminal "{t}"', 3)
    return terminals[t]


if role == "hc":
    cmd = args[0] if args else ""
    if args[:2] == ["server", "status"]:
        out({"version": "0.1.22", "live": len(terminals)})
    if cmd == "ls":
        out({"terminals": [{"address": t, "agent": v["agent"], "state": v["state"],
                            **({"status": v["status"]} if v["status"] else {})} for t, v in terminals.items()]})
    if cmd == "keys":
        target(args[1])
        out({"ok": True})
    if cmd == "send":
        force = "--force" in args
        rest = [a for a in args[1:] if a != "--force"]
        term = target(rest[0])
        if term["status"] == "blocked" and not force:
            hc_err("blocked", "it is waiting for a person", 9)
        if term["agent"] != "claude-code" and "claude" in rest[1]:
            term["agent"], term["status"] = "claude-code", "idle"
            save()
        out({"started": None, "status": term["status"], "turn": 0})
    if cmd == "read":
        target(args[1])
        screen = Path(f"{state_path}.screen")
        print(screen.read_text() if screen.exists() else f"(screen of {args[1]})")
        sys.exit(0)

print(f"unknown command: {role} {args}", file=sys.stderr)
sys.exit(2)
