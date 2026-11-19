#!/usr/bin/env python3
"""テスト用の偽 herdr。herdr 0.9.5 の CLI と同じ引数・同じ JSON の形で応答する。

状態は $FAKE_HERDR_STATE（JSON）に置き、呼び出しは $FAKE_HERDR_STATE.log に記録する。
$FAKE_HERDR_STATE.blocked にエージェント名を書くと、その agent prompt は agent_blocked で失敗する。
$FAKE_HERDR_STATE.not_ready にエージェント名を書くと、その agent start は agent_not_ready で失敗する。
$FAKE_HERDR_STATE.version に版を書くと、--version がその版を返す（既定は 0.9.5）。
$FAKE_HERDR_STATE.mismatch があると、status --json がサーバーとの protocol の不一致を返す。
agent list の agent_status は状態ファイルの "status"（名前 → 状態）で変えられる（既定は working）。
ワークスペースは "workspaces"（ID → ラベル）、タブは "tabs"（タブ ID → ペイン ID）に持つ。
ID は作った順の通し番号で、ワークスペース w<k> のタブとペインは w<k>:t<n> / w<k>:p<n>。
$FAKE_HERDR_STATE.screen に書いた内容を agent read が画面として返す（なければ "(screen of <名前>)"）。
"""
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_HERDR_STATE"])
args = sys.argv[1:]
with open(f"{state_path}.log", "a") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
state = json.loads(state_path.read_text()) if state_path.exists() else {"panes": [], "agents": {}, "n": 0}


def save():
    state_path.write_text(json.dumps(state))


def ok(result_type, **result):
    print(json.dumps({"id": "cli", "result": {"type": result_type, **result}}))
    sys.exit(0)


def err(code, message):
    print(json.dumps({"id": "cli", "error": {"code": code, "message": message}}), file=sys.stderr)
    sys.exit(1)


def flag(name, default=None):
    vals = [args[i + 1] for i, a in enumerate(args) if a == name and i + 1 < len(args)]
    return vals[-1] if vals else default


def listed(name):
    p = Path(f"{state_path}.{name}")
    return p.exists() and any(args[2].split("-")[0] == x for x in p.read_text().split())


group, cmd = args[0], args[1] if len(args) > 1 else ""

version_path = Path(f"{state_path}.version")
version = version_path.read_text().strip() if version_path.exists() else "0.9.5"

if group == "--version":
    print(f"herdr {version}")
    sys.exit(0)

if (group, cmd) == ("status", "--json"):
    mismatch = Path(f"{state_path}.mismatch").exists()
    print(json.dumps({
        "client": {"version": version, "protocol": 22},
        "server": {"status": "running", "running": True, "version": "0.7.3" if mismatch else version,
                   "protocol": 14 if mismatch else 22, "compatible": not mismatch,
                   "endpoint_compatible": not mismatch, "restart_needed": mismatch, "server_binary_stale": False},
    }))
    sys.exit(0)

if (group, cmd) == ("pane", "list"):
    ok("pane_list", panes=[{"pane_id": p, "terminal_id": "t", "workspace_id": p.split(":")[0],
                            "tab_id": p.replace(":p", ":t"), "focused": False, "agent_status": "idle"}
                           for p in state["panes"]])

workspaces = state.setdefault("workspaces", {})
tabs = state.setdefault("tabs", {})


def new_tab(ws):
    state["n"] += 1
    pane, tab = f"{ws}:p{state['n']}", f"{ws}:t{state['n']}"
    state["panes"].append(pane)
    tabs[tab] = pane
    state.setdefault("env", {})[pane] = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
    return tab, pane


def close_tabs(gone_tabs):
    gone = {tabs.pop(t) for t in gone_tabs if t in tabs}
    state["panes"] = [p for p in state["panes"] if p not in gone]
    state["agents"] = {n: p for n, p in state["agents"].items() if p not in gone}


if (group, cmd) == ("workspace", "list"):
    ok("workspace_list", workspaces=[{"workspace_id": w, "label": label, "number": i + 1, "focused": False,
                                      "tab_count": sum(1 for t in tabs if t.startswith(f"{w}:"))}
                                     for i, (w, label) in enumerate(workspaces.items())])

if (group, cmd) == ("workspace", "create"):
    assert "--no-focus" in args, "フォーカスを奪ってはいけない"
    state["wn"] = state.get("wn", 0) + 1
    ws = f"w{state['wn']}"
    workspaces[ws] = flag("--label", ws)
    tab, pane = new_tab(ws)
    save()
    ok("workspace_created", workspace={"workspace_id": ws, "label": workspaces[ws]},
       tab={"tab_id": tab, "label": "1", "workspace_id": ws},
       root_pane={"pane_id": pane, "terminal_id": "t", "workspace_id": ws, "tab_id": tab, "focused": False,
                  "cwd": flag("--cwd"), "agent_status": "unknown"})

if (group, cmd) == ("workspace", "close"):
    ws = args[2]
    if ws not in workspaces:
        err("workspace_not_found", ws)
    close_tabs([t for t in list(tabs) if t.startswith(f"{ws}:")])
    del workspaces[ws]
    save()
    ok("workspace_closed", workspace_id=ws)

if (group, cmd) == ("tab", "list"):
    ws = flag("--workspace")
    ok("tab_list", tabs=[{"tab_id": t, "workspace_id": t.split(":")[0], "label": t}
                         for t in tabs if ws is None or t.startswith(f"{ws}:")])

if (group, cmd) == ("tab", "rename"):
    if args[2] not in tabs:
        err("tab_not_found", args[2])
    ok("tab_renamed", tab={"tab_id": args[2], "label": " ".join(args[3:])})

if (group, cmd) == ("agent", "list"):
    ok("agent_list", agents=[{"name": n, "pane_id": p, "agent": "claude",
                              "agent_status": state.get("status", {}).get(n, "working")}
                             for n, p in state["agents"].items()])

if (group, cmd) == ("agent", "send-keys"):
    name = args[2]
    if name not in state["agents"]:
        err("agent_not_found", name)
    ok("agent_keys_sent", agent={"name": name, "pane_id": state["agents"][name]})

if (group, cmd) == ("tab", "close"):
    if args[2] not in tabs:
        err("tab_not_found", args[2])
    close_tabs([args[2]])
    save()
    ok("tab_closed", tab_id=args[2])

if (group, cmd) == ("tab", "create"):
    assert "--no-focus" in args, "フォーカスを奪ってはいけない"
    ws = flag("--workspace")
    if ws not in workspaces:
        err("workspace_not_found", f"workspace {ws} not found")
    tab, pane = new_tab(ws)
    save()
    ok("tab_created", tab={"tab_id": tab, "label": flag("--label"), "workspace_id": ws},
       root_pane={"pane_id": pane, "terminal_id": "t", "workspace_id": ws, "tab_id": tab, "focused": False,
                  "cwd": flag("--cwd"), "agent_status": "unknown"})

if (group, cmd) == ("agent", "start"):
    name, pane = args[2], flag("--pane")
    if flag("--kind") != "claude":
        err("invalid_params", "unknown kind")
    if pane not in state["panes"]:
        err("pane_not_found", pane)
    if name in state["agents"] and state["agents"][name] != pane:
        err("agent_name_taken", f"agent name {name} is already used")
    if pane not in state.setdefault("warmed", []):
        state["warmed"].append(pane)
        save()
        err("agent_pane_busy", f"agent target pane {pane} is not an available shell")
    state["agents"][name] = pane
    save()
    if listed("not_ready"):
        err("agent_not_ready", "agent is blocked during startup")
    ok("agent_started", agent={"name": name, "pane_id": pane, "agent_status": "idle"},
       argv=["claude", *args[args.index("--") + 1:]])

if (group, cmd) == ("agent", "prompt"):
    name = args[2]
    if name not in state["agents"]:
        err("agent_not_found", name)
    if listed("blocked"):
        err("agent_blocked", "agent is waiting at an approval dialog")
    ok("agent_prompted", agent={"name": name, "pane_id": state["agents"][name], "agent_status": "working"})

if (group, cmd) == ("pane", "send-text"):
    if args[2] not in state["panes"]:
        err("pane_not_found", args[2])
    ok("pane_text_sent", pane_id=args[2])

if (group, cmd) == ("agent", "read"):
    name = args[2]
    screen = Path(f"{state_path}.screen")
    text = screen.read_text() if screen.exists() else f"(screen of {name})"
    ok("pane_read", read={"pane_id": state["agents"].get(name, "?"), "workspace_id": "w1", "tab_id": "w1:t1",
                          "source": flag("--source"), "format": "text", "text": text,
                          "revision": 1, "truncated": False})

print(f"unknown command: {args}", file=sys.stderr)
sys.exit(2)
