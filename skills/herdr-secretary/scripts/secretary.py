#!/usr/bin/env python3
"""herdr-secretary: 秘書エージェントが作業エージェントを管理するための CLI。

標準ライブラリだけで動く。サブコマンド:

  init                       秘書ディレクトリを作る（初回だけ）
  new                        タスクを登録し、空きがあれば起動する
  list [--json]              全タスクの状態一覧
  inbox                      人間の判断が要るもの（blocked / review）を古い順に
  show ID                    タスクフォルダの中身（prompt / status / result）
  peek ID [--lines N]        作業エージェントの画面を少しだけ読む
  handback ID                manual（人間が介入中）を解除して秘書に戻す
  close ID                   タスクを閉じる（タブや作業場所は消さない）
  watch                      監視ループ。状態が変わったときだけ 1 行出力する
  hook                       作業エージェントの Claude Code hook から呼ばれる

秘書ディレクトリは $SECRETARY_HOME（既定は ~/secretary）。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SECRETARY_MARK = "[secretary]"

# 状態
QUEUED = "queued"      # 並列上限のため起動待ち
RUNNING = "running"    # 作業中
BLOCKED = "blocked"    # 人間の判断待ち（許可待ち / 入力待ち / 異常）
MANUAL = "manual"      # 人間がペインで直接指示している
REVIEW = "review"      # result.md ができた。人間のレビュー待ち
CLOSED = "closed"

ACTIVE = {RUNNING, BLOCKED, MANUAL}          # 並列数に数える状態
NOTIFY_TO = {BLOCKED, REVIEW, MANUAL}        # この状態に入ったら通知する

DEFAULT_CONFIG = {
    "max_parallel": 10,
    "snapshot_interval_sec": 180,
    "silence_alert_min": 30,
    "manual_timeout_min": 0,
    "poll_interval_sec": 1.0,
    "herdr_bin": "herdr",
}


# ---------------------------------------------------------------------------
# パスと設定
# ---------------------------------------------------------------------------

def home() -> Path:
    return Path(os.environ.get("SECRETARY_HOME", "~/secretary")).expanduser().resolve()


def tasks_dir() -> Path:
    return home() / "tasks"


def task_dir(task_id: str) -> Path:
    return tasks_dir() / task_id


def events_path() -> Path:
    return home() / "events.jsonl"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    p = home() / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(s: str | None) -> float:
    if not s:
        return 0.0
    return datetime.fromisoformat(s).timestamp()


@contextlib.contextmanager
def locked():
    """meta.json を書き換えるときの排他。watcher と CLI が同時に触っても壊れないようにする。"""
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_meta(task_id: str) -> dict:
    return json.loads((task_dir(task_id) / "meta.json").read_text())


def write_meta(meta: dict) -> None:
    p = task_dir(meta["id"]) / "meta.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(p)


def all_metas() -> list[dict]:
    if not tasks_dir().exists():
        return []
    metas = []
    for d in sorted(tasks_dir().iterdir()):
        if (d / "meta.json").exists():
            metas.append(json.loads((d / "meta.json").read_text()))
    return metas


def has_result(task_id: str) -> bool:
    p = task_dir(task_id) / "result.md"
    return p.exists() and p.stat().st_size > 0


def take_new_result(meta: dict) -> bool:
    """前回 review にしたとき以降に result.md が書かれていれば True（見たことを記録する）。"""
    if not has_result(meta["id"]):
        return False
    mtime = (task_dir(meta["id"]) / "result.md").stat().st_mtime
    if mtime <= meta.get("result_seen_mtime", 0):
        return False
    meta["result_seen_mtime"] = mtime
    return True


# ---------------------------------------------------------------------------
# 状態遷移（判定はすべてここに集める）
# ---------------------------------------------------------------------------

def transition(meta: dict, new: str, reason: str = "") -> str | None:
    """状態を変える。通知すべき遷移なら 1 行の通知文を返す。"""
    old = meta["status"]
    if old == new and meta.get("reason", "") == reason:
        return None
    meta["status"] = new
    meta["reason"] = reason
    meta["status_since"] = now_iso()
    meta.setdefault("history", []).append({"at": meta["status_since"], "from": old, "to": new, "reason": reason})
    if old == new:
        return None
    if new in NOTIFY_TO or (old == QUEUED and new == RUNNING):
        return format_line(meta, old)
    return None


def format_line(meta: dict, old: str) -> str:
    reason = f" {meta['reason']}" if meta.get("reason") else ""
    return f"[secretary] {meta['id']} {old}→{meta['status']}{reason} — {meta['title']}"


def apply_event(meta: dict, ev: dict) -> str | None:
    """hook のイベント 1 件を状態に反映する。"""
    status = meta["status"]
    if status in (CLOSED, QUEUED):
        return None
    meta["last_event_at"] = ev.get("time", now_iso())
    meta.pop("silence_alerted", None)
    name = ev.get("event")

    if name == "UserPromptSubmit":
        if ev.get("from_secretary"):
            return None
        meta["last_human_at"] = meta["last_event_at"]
        if status == REVIEW:
            # レビュー中に人間が追加で頼んだ → 介入扱い。result.md が書き直されたらまた review
            return transition(meta, MANUAL, "人間がペインで追加指示")
        return transition(meta, MANUAL, "人間がペインで直接指示")

    if name == "Stop":
        if take_new_result(meta):
            return transition(meta, REVIEW, "result.md あり")
        if status == MANUAL:
            return None
        return transition(meta, BLOCKED, "入力待ち（result.md なしでターン終了）")

    if name == "Notification":
        if status in (MANUAL, REVIEW):
            return None
        msg = (ev.get("message") or "").strip()
        if status == BLOCKED and meta.get("reason", "").startswith("入力待ち"):
            # Stop の後の「入力待ちです」通知は同じ意味なので重ねない
            if "permission" not in msg.lower() and "許可" not in msg:
                return None
        return transition(meta, BLOCKED, f"許可待ち: {msg[:80]}" if msg else "許可待ち")

    if name == "PostToolUse":
        # 許可されて作業が再開した
        if status == BLOCKED:
            return transition(meta, RUNNING, "再開")
        return None

    return None


def periodic_checks(meta: dict, cfg: dict, live_panes: set[str] | None) -> str | None:
    """イベントでは拾えない異常（ペイン消失・長時間無反応・manual の放置）を見る。"""
    status = meta["status"]
    if status not in ACTIVE:
        return None
    now = time.time()

    if live_panes is not None and meta.get("pane") and meta["pane"] not in live_panes:
        return transition(meta, BLOCKED, "異常: ペインが見つからない")

    timeout = cfg.get("manual_timeout_min", 0)
    if status == MANUAL and timeout:
        if now - parse_iso(meta.get("last_human_at") or meta.get("status_since")) > timeout * 60:
            return transition(meta, BLOCKED if not has_result(meta["id"]) else REVIEW,
                              "介入が止まったので秘書に戻した")

    silence = cfg.get("silence_alert_min", 0)
    if status == RUNNING and silence and not meta.get("silence_alerted"):
        last = parse_iso(meta.get("last_event_at") or meta.get("started_at"))
        if last and now - last > silence * 60:
            meta["silence_alerted"] = True
            return transition(meta, BLOCKED, f"異常: {silence} 分イベントなし")
    return None


# ---------------------------------------------------------------------------
# herdr アダプタ
#
# herdr の CLI を呼ぶのはここだけ。書き方は herdr の公式ドキュメントで未確認の
# 部分があるので、実機で `herdr --help` を見て合わせること（README 参照）。
# ---------------------------------------------------------------------------

class Herdr:
    def __init__(self, cfg: dict):
        self.bin = os.environ.get("HERDR_BIN", cfg.get("herdr_bin", "herdr"))

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def _run(self, *args: str) -> str:
        r = subprocess.run([self.bin, *args], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"herdr {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    def snapshot(self) -> dict:
        return json.loads(self._run("api", "snapshot"))

    def pane_ids(self) -> set[str]:
        return set(_collect_pane_ids(self.snapshot()))

    def create_tab(self, label: str) -> str:
        """タブを作り、新しくできたペインの ID を返す。"""
        before = self.pane_ids()
        out = self._run("tab", "create", "--label", label)
        after = self.pane_ids()
        new = sorted(after - before)
        if len(new) == 1:
            return new[0]
        # 出力にペイン ID が出る実装ならそれを使う
        m = re.search(r"\b(w\d+:p\d+|\S+:p\d+)\b", out)
        if m:
            return m.group(1)
        raise RuntimeError(f"新しいペインを特定できなかった（候補: {new}）")

    def pane_run(self, pane: str, command: str) -> None:
        self._run("pane", "run", pane, command)

    def pane_read(self, pane: str, lines: int) -> str:
        return self._run("pane", "read", pane, "--source", "recent", "--lines", str(lines))


def _collect_pane_ids(node) -> list[str]:
    """snapshot の JSON からペイン ID を集める。スキーマが未確認なので、
    "panes" 配下の要素の id / pane_id / pane を拾う緩い実装にしている。"""
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "panes" and isinstance(v, list):
                for p in v:
                    if isinstance(p, dict):
                        for key in ("pane_id", "id", "pane"):
                            if isinstance(p.get(key), str):
                                found.append(p[key])
                                break
                    elif isinstance(p, str):
                        found.append(p)
            found.extend(_collect_pane_ids(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_collect_pane_ids(v))
    return found


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------

def worker_command(meta: dict) -> str:
    settings = home() / "worker-settings.json"
    prompt = f"{SECRETARY_MARK} {task_dir(meta['id']) / 'prompt.md'} を読んで、その指示に従って作業してください。"
    return " ".join([
        "cd", shlex.quote(meta["repo"]), "&&",
        f"SECRETARY_TASK_ID={shlex.quote(meta['id'])}",
        f"SECRETARY_HOME={shlex.quote(str(home()))}",
        "claude", "--settings", shlex.quote(str(settings)), shlex.quote(prompt),
    ])


def launch(meta: dict, herdr: Herdr) -> str | None:
    pane = herdr.create_tab(meta["id"])
    herdr.pane_run(pane, worker_command(meta))
    meta["pane"] = pane
    meta["started_at"] = now_iso()
    meta["last_event_at"] = meta["started_at"]
    return transition(meta, RUNNING, "起動")


def fill_slots(cfg: dict, herdr: Herdr) -> list[str]:
    """空きがあればキューの古い順に起動する。"""
    lines = []
    metas = all_metas()
    active = sum(1 for m in metas if m["status"] in ACTIVE)
    for m in sorted((m for m in metas if m["status"] == QUEUED), key=lambda m: m["created_at"]):
        if active >= cfg["max_parallel"]:
            break
        try:
            line = launch(m, herdr)
        except Exception as e:  # 起動失敗は人間に知らせる
            line = transition(m, BLOCKED, f"異常: 起動に失敗 ({e})")
        write_meta(m)
        if line:
            lines.append(line)
        active += 1
    return lines


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

REPOS_TEMPLATE = """# 作業対象のリポジトリ

秘書はこの一覧から依頼の対象を選ぶ。1 行に 1 リポジトリ。
書式: `- 名前: 絶対パス — 何のリポジトリか`

<!-- 例
- evaluation-api: /Users/you/src/evaluation-api — 人事評価のバックエンド（Rails）
- evaluation-web: /Users/you/src/evaluation-web — 人事評価のフロントエンド（React / TypeScript）
-->
"""


def cmd_init(args) -> int:
    h = home()
    tasks_dir().mkdir(parents=True, exist_ok=True)
    events_path().touch()
    cfg_path = h / "config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    repos = h / "repos.md"
    if not repos.exists():
        repos.write_text(REPOS_TEMPLATE)

    hook_cmd = f"python3 {shlex.quote(str(SCRIPT_PATH))} hook"
    hook = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}]}]
    settings = {
        "permissions": {
            "additionalDirectories": [str(tasks_dir())],
            "allow": [f"Edit(/{tasks_dir()}/**)", f"Read(/{tasks_dir()}/**)"],
        },
        "hooks": {
            "UserPromptSubmit": hook,
            "Stop": hook,
            "Notification": hook,
            "PostToolUse": [{"matcher": "*", "hooks": hook[0]["hooks"]}],
        },
    }
    (h / "worker-settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")

    bin_dir = h / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "sec"
    wrapper.write_text(f'#!/bin/sh\nexec python3 {shlex.quote(str(SCRIPT_PATH))} "$@"\n')
    wrapper.chmod(0o755)
    print(f"初期化しました: {h}")
    print(f"- repos.md に作業対象のリポジトリを書いてください: {repos}")
    print(f"- CLI: {wrapper}")
    return 0


def next_id() -> str:
    nums = [int(m.group(1)) for d in (tasks_dir().iterdir() if tasks_dir().exists() else [])
            if (m := re.fullmatch(r"t(\d+)", d.name))]
    return f"t{(max(nums) + 1) if nums else 1:03d}"


PROMPT_TEMPLATE = """# タスク {id}: {title}

## 依頼

{request}

## 完了条件

{done}

## 作業のルール（秘書から）

- 作業場所（git worktree など）は、このリポジトリのルール（CLAUDE.md など）に従って用意してください。ルールがなければ、ほかのタスクと衝突しない作業場所を自分で用意してください。
- PR の作り方やテストの方法など、作業の進め方はこのリポジトリのルールに従ってください。
- 進捗・方針の変更・詰まっている点は `{status}` に書いてください。人間から直接指示を受けたときも、何が変わったかをここに書いてください。
- 完了条件を満たしたら `{result}` に、要約・変更したファイル・PR の URL（あれば）・残課題を書いてください。このファイルは完了したときにだけ作ってください。
- 判断に迷ったら、作業を止めて質問してください。秘書が人間に取り次ぎます。
"""


def cmd_new(args) -> int:
    request = args.request if args.request is not None else sys.stdin.read()
    request, done = request.strip(), (args.done or "").strip()
    if not done:
        print("完了条件がありません。依頼者に完了条件を確認してください（秘書が補わないこと）。", file=sys.stderr)
        return 2
    if not request:
        print("依頼内容が空です。", file=sys.stderr)
        return 2
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"リポジトリが見つかりません: {repo}", file=sys.stderr)
        return 2

    cfg = load_config()
    with locked():
        tid = next_id()
        d = task_dir(tid)
        d.mkdir(parents=True)
        (d / "prompt.md").write_text(PROMPT_TEMPLATE.format(
            id=tid, title=args.title, request=request, done=done,
            status=d / "status.md", result=d / "result.md"))
        meta = {"id": tid, "title": args.title, "repo": str(repo), "status": QUEUED,
                "reason": "", "created_at": now_iso(), "status_since": now_iso(), "pane": None,
                "history": []}
        write_meta(meta)
        lines = [] if args.no_launch else fill_slots(cfg, Herdr(cfg))
    meta = read_meta(tid)
    if meta["status"] == QUEUED:
        print(f"{tid} を登録しました（キュー待ち。並列上限 {cfg['max_parallel']}）")
    for line in lines:
        print(line)
    return 0


def summary_line(metas: list[dict]) -> str:
    order = [BLOCKED, REVIEW, MANUAL, RUNNING, QUEUED]
    counts = {s: sum(1 for m in metas if m["status"] == s) for s in order}
    return " / ".join(f"{s}: {counts[s]}" for s in order)


def ago(iso: str | None) -> str:
    if not iso:
        return "-"
    mins = int((time.time() - parse_iso(iso)) // 60)
    return f"{mins // 60}時間{mins % 60}分" if mins >= 60 else f"{mins}分"


def cmd_list(args) -> int:
    metas = [m for m in all_metas() if args.all or m["status"] != CLOSED]
    if args.json:
        print(json.dumps(metas, ensure_ascii=False, indent=2))
        return 0
    print(summary_line(metas))
    for m in metas:
        reason = f"（{m['reason']}）" if m.get("reason") and m["status"] in (BLOCKED, MANUAL) else ""
        print(f"- {m['id']} [{m['status']} {ago(m.get('status_since'))}] {m['title']}{reason}  repo={Path(m['repo']).name}")
    return 0


def cmd_inbox(args) -> int:
    items = [m for m in all_metas() if m["status"] in (BLOCKED, REVIEW)]
    items.sort(key=lambda m: parse_iso(m.get("status_since")))
    if not items:
        print("受信箱は空です。")
        return 0
    for m in items:
        line = f"- {m['id']} [{m['status']} {ago(m.get('status_since'))}] {m['title']} — {m.get('reason', '')}"
        print(line)
    return 0


def cmd_show(args) -> int:
    d = task_dir(args.id)
    meta = read_meta(args.id)
    print(f"# {meta['id']} [{meta['status']}] {meta['title']}  pane={meta.get('pane')}  repo={meta['repo']}")
    for name in ("status.md", "result.md"):
        p = d / name
        print(f"\n## {name}\n")
        print(p.read_text().strip() if p.exists() else "（まだない）")
    return 0


def cmd_peek(args) -> int:
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    print(Herdr(load_config()).pane_read(meta["pane"], args.lines))
    return 0


def cmd_handback(args) -> int:
    with locked():
        meta = read_meta(args.id)
        if meta["status"] != MANUAL:
            print(f"{args.id} は manual ではありません（{meta['status']}）")
            return 0
        line = transition(meta, REVIEW if has_result(args.id) else RUNNING, "秘書に戻した")
        write_meta(meta)
    print(line or f"{args.id} を秘書に戻しました（{meta['status']}）")
    return 0


def cmd_close(args) -> int:
    cfg = load_config()
    with locked():
        meta = read_meta(args.id)
        transition(meta, CLOSED, args.reason or "")
        write_meta(meta)
        lines = fill_slots(cfg, Herdr(cfg))
    print(f"{args.id} を閉じました。タブや作業場所は残っています（pane={meta.get('pane')}）。")
    for line in lines:
        print(line)
    return 0


def cmd_hook(args) -> int:
    """作業エージェントの hook。何も出力しない（UserPromptSubmit の出力は文脈に混ざるため）。"""
    task_id = os.environ.get("SECRETARY_TASK_ID")
    if not task_id:
        return 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    ev = {"time": now_iso(), "task_id": task_id, "event": data.get("hook_event_name", "")}
    if ev["event"] == "UserPromptSubmit":
        ev["from_secretary"] = (data.get("prompt") or "").lstrip().startswith(SECRETARY_MARK)
    elif ev["event"] == "Notification":
        ev["message"] = data.get("message", "")
    elif ev["event"] == "PostToolUse":
        ev["tool"] = data.get("tool_name", "")
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    fd = os.open(events_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
    return 0


def cmd_watch(args) -> int:
    cfg = load_config()
    herdr = Herdr(cfg)
    offset_path = home() / ".watch-offset"
    offset = int(offset_path.read_text()) if offset_path.exists() else 0
    last_snapshot = 0.0
    use_snapshot = herdr.available()
    if not use_snapshot:
        print("[secretary] herdr が見つからないので snapshot の保険は無効です", flush=True)

    while True:
        lines: list[str] = []
        with locked():
            # 1. hook のイベントを反映
            size = events_path().stat().st_size if events_path().exists() else 0
            if size < offset:
                offset = 0
            if size > offset:
                with open(events_path(), "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                complete = chunk[: chunk.rfind(b"\n") + 1]
                offset += len(complete)
                for raw in complete.decode().splitlines():
                    try:
                        ev = json.loads(raw)
                        meta = read_meta(ev["task_id"])
                    except Exception:
                        continue
                    line = apply_event(meta, ev)
                    write_meta(meta)
                    if line:
                        lines.append(line)
                offset_path.write_text(str(offset))

            # 2. 定期チェック（ペイン消失・無反応・manual 放置）
            live = None
            if use_snapshot and time.time() - last_snapshot > cfg["snapshot_interval_sec"]:
                last_snapshot = time.time()
                try:
                    live = herdr.pane_ids()
                except Exception as e:
                    lines.append(f"[secretary] snapshot に失敗: {e}")
            for meta in all_metas():
                line = periodic_checks(meta, cfg, live)
                if line:
                    write_meta(meta)
                    lines.append(line)

            # 3. 空きがあればキューから起動
            lines.extend(fill_slots(cfg, herdr))

        for line in lines:
            print(line, flush=True)
        if args.once:
            return 0
        time.sleep(cfg["poll_interval_sec"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sec", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("new")
    s.add_argument("--repo", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--done", help="完了条件（人間から受け取ったものをそのまま）")
    s.add_argument("--request", help="依頼内容。省略すると標準入力から読む")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("list")
    s.add_argument("--json", action="store_true")
    s.add_argument("--all", action="store_true", help="closed も出す")
    s.set_defaults(func=cmd_list)

    sub.add_parser("inbox").set_defaults(func=cmd_inbox)

    for name, func in (("show", cmd_show), ("handback", cmd_handback)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(func=func)

    s = sub.add_parser("peek")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=30)
    s.set_defaults(func=cmd_peek)

    s = sub.add_parser("close")
    s.add_argument("id")
    s.add_argument("--reason")
    s.set_defaults(func=cmd_close)

    sub.add_parser("hook").set_defaults(func=cmd_hook)

    s = sub.add_parser("watch")
    s.add_argument("--once", action="store_true", help="1 周だけ回して終わる（テスト用）")
    s.set_defaults(func=cmd_watch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
