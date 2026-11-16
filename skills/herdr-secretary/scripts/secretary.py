#!/usr/bin/env python3
"""herdr-secretary: 秘書エージェントが作業エージェントを管理するための CLI。

標準ライブラリだけで動く。サブコマンド:

  init                       秘書ディレクトリを作る（初回だけ）
  new                        タスクを登録し、空きがあれば起動する
  list [--json]              全タスクの状態一覧
  inbox                      人間の判断が要るもの（blocked / review）を古い順に
  show ID                    タスクフォルダの中身（prompt / status / result）
  peek ID [--lines N]        作業エージェントの画面を少しだけ読む
  reply ID TEXT              入力待ちの作業エージェントに秘書から返事を送る
  permissions                許可待ちになったコマンドの集計と、許可ルールの候補
  allow RULE...              作業エージェントの許可リストにルールを足す（例: "Bash(git worktree:*)"）
  retry ID                   最初の指示を送り直す（起動画面で止まっていたとき）
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
# Claude Code がユーザーターンとして差し込むもの（バックグラウンドのサブエージェントやコマンドの完了通知）。人間の入力ではない
SYSTEM_PROMPT_PREFIXES = ("<task-notification>",)

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
    "long_tool_alert_min": 120,
    "manual_timeout_min": 15,
    "first_prompt_ack_sec": 30,
    "review_backlog_alert": 5,
    "notify": "auto",
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
        # blocked のまま理由が変わった（起動画面 → エージェント終了など）のは新しい情報なので知らせる
        return format_line(meta, old) if new == BLOCKED else None
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
            meta.pop("awaiting_ack_since", None)
            meta.pop("pending_first_prompt", None)
            return None
        if ev.get("from_system"):
            return transition(meta, RUNNING, "バックグラウンド処理の完了で再開") if status == BLOCKED else None
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
        is_permission = "permission" in msg.lower() or "許可" in msg
        if status == BLOCKED and meta.get("reason", "").startswith("入力待ち") and not is_permission:
            # Stop の後の「入力待ちです」通知は同じ意味なので重ねない
            return None
        if is_permission and status == BLOCKED and meta.get("reason", "").startswith("許可待ち"):
            return None  # PermissionRequest で通知済み
        if is_permission and meta.get("pending_tool"):
            # 直前に始めようとしたツールが許可待ちの対象。何を許可すればいいかが一目で分かるようにする
            tool = meta["pending_tool"]
            record_permission(meta["id"], tool)
            return transition(meta, BLOCKED, f"許可待ち: {tool['label']}")
        return transition(meta, BLOCKED, f"許可待ち: {msg[:80]}" if msg else "許可待ち")

    if name == "PermissionRequest":
        # Claude Code が許可ダイアログを出した（herdr 自身もこの hook で blocked を判定している）
        if status in (MANUAL, REVIEW):
            return None
        tool = {"name": ev.get("tool", ""), "label": tool_label(ev), "started_at": meta["last_event_at"]}
        meta["pending_tool"] = tool
        record_permission(meta["id"], tool)
        return transition(meta, BLOCKED, f"許可待ち: {tool['label']}")

    if name == "PreToolUse":
        meta["pending_tool"] = {"name": ev.get("tool", ""), "label": tool_label(ev), "started_at": meta["last_event_at"]}
        return None

    if name in ("PostToolUse", "PostToolUseFailure"):
        meta.pop("pending_tool", None)
        # 許可されて作業が再開した
        if status == BLOCKED:
            return transition(meta, RUNNING, "再開")
        return None

    return None


def tool_label(ev: dict) -> str:
    """許可待ちの通知に出す短い説明。Bash ならコマンド、ファイル操作ならパス。"""
    tool = ev.get("tool", "")
    inp = ev.get("input") or {}
    detail = inp.get("command") or inp.get("file_path") or inp.get("url") or inp.get("pattern") or ""
    detail = " ".join(str(detail).split())
    # 先頭の `cd <パス> &&` は長いだけで判断の役に立たないので省く
    detail = re.sub(r"^cd\s+\S+\s*&&\s*", "", detail)
    if len(detail) > 100:
        detail = detail[:97] + "..."
    return f"{tool} `{detail}`" if detail else tool


# 2 語目まで見ないと意味が分からないコマンド（git status と git push は別物）
TWO_WORD_COMMANDS = {"git", "bundle", "npm", "pnpm", "yarn", "npx", "bin/rails", "rails", "gh", "docker", "make", "cargo", "go", "uv", "pip", "python", "python3"}


def suggest_rule(tool: dict) -> str | None:
    """許可待ちになったツールから、permissions.allow に入れるルールの候補を作る。"""
    name = tool.get("name", "")
    if name != "Bash":
        return name or None
    label = tool.get("label", "")
    m = re.search(r"`(.*)`", label)
    cmd = (m.group(1) if m else "").strip()
    # つながったコマンドから cd 以外の最初の本体を拾う。許可ルールは先頭一致なので、
    # 先頭の `env` や `RAILS_ENV=test` のような変数指定はルールにそのまま残す
    words: list[str] = []
    for seg in re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd.removesuffix("...")):
        ws = seg.split()
        if ws and ws[0] != "cd":
            words = ws
            break
    lead: list[str] = []
    while words and (words[0] == "env" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0])):
        lead.append(words.pop(0))
    if not words:
        return None  # `env` だけ、変数だけのルールは何でも通してしまうので出さない
    # `git -C <dir> add` や `npm --prefix <dir> ci` の場所指定は飛ばし、サブコマンドで候補を作る
    # （`Bash(git -C:*)` を許可すると push まで通ってしまう）
    rest = words[1:]
    while len(rest) >= 2 and rest[0] in ("-C", "--prefix", "--cwd", "--git-dir", "--work-tree", "--dir"):
        rest = rest[2:]
    if words[0] in TWO_WORD_COMMANDS and rest:
        body = [words[0], rest[0]]
        # `bundle exec` / `npm run` は何でも実行できるので、実行するものまで含める
        if tuple(body) in THREE_WORD_PREFIXES and len(rest) > 1:
            body.append(rest[1])
    else:
        body = words[:1]
    return f"Bash({' '.join(lead + body)}:*)"


THREE_WORD_PREFIXES = {("bundle", "exec"), ("npm", "run"), ("pnpm", "run"), ("yarn", "run"), ("npx", "--yes")}


def record_permission(task_id: str, tool: dict) -> None:
    with open(home() / "permissions.jsonl", "a") as f:
        f.write(json.dumps({"time": now_iso(), "task_id": task_id, **tool}, ensure_ascii=False) + "\n")


def periodic_checks(meta: dict, cfg: dict, live: tuple[set[str], set[str]] | None) -> str | None:
    """イベントでは拾えない異常（ペイン消失・長時間無反応・manual の放置）を見る。"""
    status = meta["status"]
    if status not in ACTIVE:
        return None
    now = time.time()

    # 最初の指示が届いていない。起動直後の画面（初回の案内やフォルダの信頼確認）に吸い込まれた可能性が高い。
    # Enter を自動で送ると確認画面の既定の選択肢を選んでしまうので、人間に画面を進めてもらう
    if status == RUNNING and meta.get("awaiting_ack_since") and now - parse_iso(meta["awaiting_ack_since"]) > cfg.get("first_prompt_ack_sec", 30):
        meta.pop("awaiting_ack_since")
        meta["pending_first_prompt"] = True
        return transition(meta, BLOCKED, f"起動画面で止まっている（ペイン {meta.get('pane')} を進めてから retry）")

    if live is not None and meta.get("pane"):
        panes, agents = live
        if meta["pane"] not in panes:
            if meta.get("pane_gone"):
                return None  # 一度知らせたら繰り返さない
            meta["pane_gone"] = True
            return transition(meta, BLOCKED, "異常: ペインが見つからない")
        if agent_name(meta) not in agents and not meta.get("agent_gone"):
            # Claude Code が終了した（/exit、確認画面で No、クラッシュなど）。ペインはシェルに戻っている
            meta["agent_gone"] = True
            return transition(meta, BLOCKED, "異常: 作業エージェントが終了した（retry で起動し直せる）")

    timeout = cfg.get("manual_timeout_min", 0)
    if status == MANUAL and timeout:
        if now - parse_iso(meta.get("last_human_at") or meta.get("status_since")) > timeout * 60:
            # 人間の入力が途切えた。作業エージェントはまだ動いているかもしれないので running に戻し、
            # 次に止まったときは秘書が通知する
            if take_new_result(meta):
                return transition(meta, REVIEW, f"{timeout} 分介入がないので秘書に戻した")
            transition(meta, RUNNING, f"{timeout} 分介入がないので秘書に戻した")
            return format_line(meta, MANUAL)

    if status == RUNNING and not meta.get("silence_alerted"):
        tool = meta.get("pending_tool")
        if tool:
            # テストの全件実行など、長いコマンドの実行中はイベントが来ないので長めに待つ
            limit, since = cfg.get("long_tool_alert_min", 0), parse_iso(tool.get("started_at"))
            msg = f"異常: {tool['label']} が {limit} 分終わらない"
        else:
            limit = cfg.get("silence_alert_min", 0)
            since = parse_iso(meta.get("last_event_at") or meta.get("started_at"))
            msg = f"異常: {limit} 分イベントなし"
        if limit and since and now - since > limit * 60:
            meta["silence_alerted"] = True
            return transition(meta, BLOCKED, msg)
    return None


# ---------------------------------------------------------------------------
# herdr アダプタ
#
# herdr を呼ぶのはここだけ。herdr 0.9.5 のソース（src/cli, src/api/schema）と
# 公式 agent skill（skills/herdr/SKILL.md）に合わせてある。
# - 制御コマンドは JSON（{"id", "result": {...}}）を返す
# - サーバーのエラーは stderr に JSON（{"error": {"code", "message"}}）、終了コード 1
# - herdr のペインの中（HERDR_ENV=1）から呼ぶと、そのセッションを操作する
# ---------------------------------------------------------------------------

def worker_extra_args(meta: dict) -> list[str]:
    extra = []
    wt = repo_worktrees_dir(Path(meta["repo"]))
    if wt:
        wt.mkdir(parents=True, exist_ok=True)
        extra += ["--add-dir", str(wt)]
    return extra


def agent_name(meta: dict) -> str:
    """herdr のエージェント名。閉じたタスクのエージェントや別の秘書ディレクトリと衝突しないよう、
    タスク ID に秘書ディレクトリごとの短い識別子を付ける（[a-z][a-z0-9_-]{0,31}）。"""
    return meta.get("agent") or f"{meta['id']}-{home_tag()}"


def home_tag() -> str:
    import hashlib
    return hashlib.sha1(str(home()).encode()).hexdigest()[:6]


class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code


class Herdr:
    def __init__(self, cfg: dict):
        self.bin = os.environ.get("HERDR_BIN", cfg.get("herdr_bin", "herdr"))

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def _run(self, *args: str, timeout: int = 30) -> dict:
        r = subprocess.run([self.bin, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            code, message = "", r.stderr.strip()
            with contextlib.suppress(Exception):
                body = json.loads(r.stderr).get("error", {})
                code, message = body.get("code", ""), body.get("message", message)
            raise HerdrError(code, message or f"herdr {' '.join(args)} が終了コード {r.returncode}")
        out = r.stdout.strip()
        try:
            return json.loads(out).get("result", {}) if out else {}
        except json.JSONDecodeError:
            return {"text": r.stdout}

    def pane_ids(self) -> set[str]:
        return {p["pane_id"] for p in self._run("pane", "list").get("panes", [])}

    def agent_names(self) -> set[str]:
        return {a["name"] for a in self._run("agent", "list").get("agents", []) if a.get("name")}

    def live(self) -> tuple[set[str], set[str]]:
        return self.pane_ids(), self.agent_names()

    def start_worker(self, meta: dict, settings: Path) -> str:
        """タブを作り（フォーカスは移さない）、そのペインで Claude Code を起動する。ペイン ID を返す。"""
        args = ["tab", "create", "--cwd", meta["repo"], "--label", meta["id"], "--no-focus",
                "--env", f"SECRETARY_TASK_ID={meta['id']}", "--env", f"SECRETARY_HOME={home()}"]
        # 秘書と同じワークスペースにタブを並べる（herdr のペインの中なら HERDR_WORKSPACE_ID がある）
        workspace = os.environ.get("HERDR_WORKSPACE_ID")
        try:
            tab = self._run(*args, *(["--workspace", workspace] if workspace else []))
        except HerdrError as e:
            if e.code != "workspace_not_found":
                raise
            # 秘書が herdr の外にいて、アクティブなワークスペースもない
            ws = self._run("workspace", "create", "--label", "secretary", "--no-focus")
            tab = self._run(*args, "--workspace", ws["workspace"]["workspace_id"])
        pane = tab["root_pane"]["pane_id"]
        meta["pane"] = pane
        meta["tab"] = tab.get("tab", {}).get("tab_id")
        return self.start_agent(meta, settings)

    def start_agent(self, meta: dict, settings: Path) -> str:
        pane = meta["pane"]
        # agent start は Claude Code が入力を受け付けられる状態になってから返る。
        # 作ったばかりのタブはシェルのプロンプトが出る前だと agent_pane_busy になるので少し待つ
        for attempt in range(20):
            try:
                self._run("agent", "start", agent_name(meta), "--kind", "claude", "--pane", pane,
                          "--timeout", "60000", "--", "--settings", str(settings),
                          *worker_extra_args(meta), timeout=90)
                return pane
            except HerdrError as e:
                if e.code != "agent_pane_busy" or attempt == 19:
                    raise
                time.sleep(0.5)
        return pane

    def prompt(self, agent: str, text: str) -> None:
        self._run("agent", "prompt", agent, text)

    def read(self, agent: str, lines: int) -> str:
        r = self._run("agent", "read", agent, "--source", "recent-unwrapped", "--lines", str(lines))
        return r.get("read", {}).get("text") or r.get("text") or json.dumps(r, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------

def first_prompt(meta: dict) -> str:
    return f"{SECRETARY_MARK} {task_dir(meta['id']) / 'prompt.md'} を読んで、その指示に従って作業してください。"


def launch(meta: dict, herdr: Herdr) -> str | None:
    meta["agent"] = agent_name(meta)
    meta["started_at"] = now_iso()
    meta["last_event_at"] = meta["started_at"]
    try:
        herdr.start_worker(meta, home() / "worker-settings.json")
    except HerdrError as e:
        if e.code == "agent_not_ready":
            # 起動時の確認（フォルダの信頼など）で止まっている。人間がペインで答えれば進む
            meta["pending_first_prompt"] = True
            return transition(meta, BLOCKED, f"起動時の確認待ち（ペイン {meta.get('pane')} で答えてから retry）")
        raise
    send_first_prompt(meta, herdr)
    return transition(meta, RUNNING, "起動")


def send_first_prompt(meta: dict, herdr: Herdr) -> None:
    herdr.prompt(agent_name(meta), first_prompt(meta))
    meta["awaiting_ack_since"] = now_iso()


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
書式: `- 名前: 絶対パス — 何のリポジトリか（worktrees: <パス>）`

リポジトリのルールで worktree をリポジトリの外に作るなら、その場所を `（worktrees: ...）` に書く
（リポジトリからの相対パスでもいい）。作業エージェントがそこを確認なしで触れるようになる。

<!-- 例
- evaluation-api: /Users/you/src/evaluation-api — 人事評価のバックエンド（Rails）（worktrees: ../evaluation-api-worktrees）
- evaluation-web: /Users/you/src/evaluation-web — 人事評価のフロントエンド（React / TypeScript）
-->
"""


SECRETARY_CLAUDE_MD = """# 秘書エージェント

あなたは shomrkm の秘書エージェント。振る舞いはすべて {skill} に従う。最初に必ず読むこと。

- セッションを始めたら、すぐに Monitor ツールで `{sec} watch` を起動する（起動済みなら不要）
- 続けて `{sec} list` と `{sec} inbox` を確認し、判断待ちがあれば最初に伝える
- 自分ではコードを書かない
"""


def cmd_init(args) -> int:
    h = home()
    tasks_dir().mkdir(parents=True, exist_ok=True)
    (h / "worktrees").mkdir(exist_ok=True)
    events_path().touch()
    cfg_path = h / "config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    repos = h / "repos.md"
    if not repos.exists():
        repos.write_text(REPOS_TEMPLATE)

    # SECRETARY_HOME もペインの環境変数だけに頼らない（herdr の再起動で消える）
    hook_cmd = f"SECRETARY_HOME={shlex.quote(str(h))} python3 {shlex.quote(str(SCRIPT_PATH))} hook"
    hook = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}]}]
    settings = {
        "permissions": {
            # ファイル編集の確認で止まらないようにする。Bash などは許可リストで育てる（sec permissions）
            "defaultMode": "acceptEdits",
            # タスクフォルダと、リポジトリにルールがないときの作業場所は確認なしで触れるようにする
            "additionalDirectories": [str(tasks_dir()), str(h / "worktrees")],
            "allow": [f"Edit(/{tasks_dir()}/**)", f"Read(/{tasks_dir()}/**)",
                      f"Edit(/{h}/worktrees/**)", f"Read(/{h}/worktrees/**)"],
        },
        "hooks": {
            "UserPromptSubmit": hook,
            "Stop": hook,
            "Notification": hook,
            "PermissionRequest": [{"matcher": "*", "hooks": hook[0]["hooks"]}],
            "PreToolUse": [{"matcher": "*", "hooks": hook[0]["hooks"]}],
            "PostToolUse": [{"matcher": "*", "hooks": hook[0]["hooks"]}],
            "PostToolUseFailure": [{"matcher": "*", "hooks": hook[0]["hooks"]}],
        },
    }
    (h / "worker-settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")

    bin_dir = h / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "sec"
    wrapper.write_text(f'#!/bin/sh\nexec python3 {shlex.quote(str(SCRIPT_PATH))} "$@"\n')
    wrapper.chmod(0o755)

    # `cd ~/secretary && claude` だけで秘書として動き出すようにする
    claude_md = h / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(SECRETARY_CLAUDE_MD.format(skill=SCRIPT_PATH.parent.parent / "SKILL.md", sec=wrapper))
    sec_settings_dir = h / ".claude"
    sec_settings_dir.mkdir(exist_ok=True)
    sec_settings = sec_settings_dir / "settings.json"
    if not sec_settings.exists():
        sec_settings.write_text(json.dumps({"permissions": {"allow": [
            f"Bash({wrapper}:*)", f"Read(/{h}/**)",
        ]}}, ensure_ascii=False, indent=2) + "\n")

    print(f"初期化しました: {h}")
    print(f"1. repos.md に作業対象のリポジトリを書いてください: {repos}")
    print(f"2. herdr のペインで `cd {h} && claude` を実行すると、秘書として動き出します")
    return 0


def repo_worktrees_dir(repo: Path) -> Path | None:
    """repos.md でそのリポジトリに `（worktrees: <パス>）` が書かれていれば、その絶対パス。
    リポジトリのルールで worktree をリポジトリの外に作る場合、作業エージェントがそこを
    確認なしで触れるように `claude --add-dir` で渡す。"""
    p = home() / "repos.md"
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        m = re.match(r"\s*-\s*[^:\s]+\s*:\s*(\S+)", line)
        if m and Path(m.group(1)).expanduser().resolve() == repo.resolve():
            w = re.search(r"worktrees:\s*([^\s）)]+)", line)
            if w:
                d = Path(w.group(1)).expanduser()
                return (d if d.is_absolute() else repo / d).resolve()
    return None


def load_repos() -> dict[str, Path]:
    """repos.md の `- 名前: パス — 説明` を読む。"""
    p = home() / "repos.md"
    repos: dict[str, Path] = {}
    if not p.exists():
        return repos
    in_comment = False
    for line in p.read_text().splitlines():
        if "<!--" in line:
            in_comment = True
        if not in_comment:
            m = re.match(r"\s*-\s*([^:\s]+)\s*:\s*(\S+)", line)
            if m:
                repos[m.group(1)] = Path(m.group(2)).expanduser()
        if "-->" in line:
            in_comment = False
    return repos


def resolve_repo(arg: str) -> Path | None:
    repos = load_repos()
    if arg in repos and repos[arg].is_dir():
        return repos[arg].resolve()
    p = Path(arg).expanduser()
    return p.resolve() if p.is_dir() else None


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

- 作業場所（git worktree など）は、このリポジトリのルール（CLAUDE.md など）に従って用意してください。ルールがなければ `{worktree}` に git worktree を作ってください（ここは許可なしで読み書きできます）。
- PR の作り方やテストの方法など、作業の進め方はこのリポジトリのルールに従ってください。
- 進捗・方針の変更・詰まっている点は `{status}` に書いてください。人間から直接指示を受けたときも、何が変わったかをここに書いてください。
- 完了条件を満たしたら `{result}` に、要約・変更したファイル・PR の URL（あれば）・残課題を書いてください。このファイルは完了したときにだけ作ってください。
- 判断に迷ったら、作業を止めて質問してください。秘書が人間に取り次ぎます。
- タスクフォルダのほかのファイル（meta.json など）は秘書が管理しているので、読み書きしなくて大丈夫です。
- 作業場所を用意したら、最初に 1 回だけ `cd <作業場所>` してください。以降の Bash はそこで 1 回に 1 コマンドずつ実行してください（`;` `&&` `|` でつながない、`git -C` や `npm --prefix` を使わない、変数に入れない）。こうしないと許可リストに当たらず、人間の確認待ちで止まります。
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
    repo = resolve_repo(args.repo)
    if repo is None:
        names = ", ".join(load_repos()) or "（repos.md が空）"
        print(f"リポジトリが見つかりません: {args.repo}（repos.md の名前: {names}）", file=sys.stderr)
        return 2

    cfg = load_config()
    with locked():
        tid = next_id()
        d = task_dir(tid)
        d.mkdir(parents=True)
        (d / "prompt.md").write_text(PROMPT_TEMPLATE.format(
            id=tid, title=args.title, request=request, done=done,
            status=d / "status.md", result=d / "result.md", worktree=home() / "worktrees" / tid))
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
    print(Herdr(load_config()).read(agent_name(meta), args.lines))
    return 0


def cmd_reply(args) -> int:
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    try:
        Herdr(load_config()).prompt(agent_name(meta), f"{SECRETARY_MARK} {args.text}")
    except HerdrError as e:
        if e.code == "agent_blocked":
            print(f"{args.id} は許可や選択肢のダイアログを出しているので、文字では答えられません。"
                  f"ペイン {meta['pane']} で直接答えてください。", file=sys.stderr)
        else:
            print(f"送れませんでした（{e}）。ペイン {meta['pane']} で直接入力してください。", file=sys.stderr)
        return 1
    with locked():
        meta = read_meta(args.id)
        if meta["status"] == BLOCKED:
            transition(meta, RUNNING, "秘書が返事を送った")
            write_meta(meta)
    print(f"{args.id} に送りました。")
    return 0


def cmd_permissions(args) -> int:
    """許可待ちになったツールを、許可ルールの候補ごとに多い順に出す。"""
    p = home() / "permissions.jsonl"
    if not p.exists():
        print("まだ許可待ちの記録はありません。")
        return 0
    allowed = set(json.loads((home() / "worker-settings.json").read_text())["permissions"].get("allow", []))
    rules: dict[str, dict] = {}
    for raw in p.read_text().splitlines():
        try:
            r = json.loads(raw)
        except Exception:
            continue
        rule = suggest_rule(r) or r.get("label", "?")
        e = rules.setdefault(rule, {"tasks": set(), "count": 0, "example": r.get("label", "")})
        e["tasks"].add(r["task_id"])
        e["count"] += 1
    rows = sorted(rules.items(), key=lambda kv: -kv[1]["count"])
    for rule, e in rows[: args.top]:
        note = ("  ← 許可済みなのに止まった。複合コマンド（; や &&）か、作業ディレクトリの外を触ったのが原因"
                if rule in allowed else "")
        print(f"- {e['count']} 回（{len(e['tasks'])} タスク）: {rule}    例: {e['example']}{note}")
    print("\n止まってほしくないものは `sec allow '<ルール>'` で許可リストに足せます。"
          "push やデプロイなど、人間が確認したい操作は足さないでください。")
    return 0


def cmd_allow(args) -> int:
    p = home() / "worker-settings.json"
    with locked():
        settings = json.loads(p.read_text())
        allow = settings["permissions"].setdefault("allow", [])
        added = [r for r in args.rules if r not in allow]
        allow.extend(added)
        p.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    print(f"追加しました: {', '.join(added) or '（すでに入っていました）'}。次に起動する作業エージェントから効きます。")
    return 0


def cmd_retry(args) -> int:
    with locked():
        meta = read_meta(args.id)
        if not meta.get("pane"):
            print("まだ起動していません。", file=sys.stderr)
            return 1
        herdr = Herdr(load_config())
        try:
            if agent_name(meta) not in herdr.agent_names():
                herdr.start_agent(meta, home() / "worker-settings.json")
                meta.pop("agent_gone", None)
            send_first_prompt(meta, herdr)
        except HerdrError as e:
            write_meta(meta)
            if e.code in ("agent_not_ready", "agent_blocked"):
                print(f"{args.id} は起動時の確認画面（フォルダの信頼など）で止まっています。"
                      f"ペイン {meta['pane']} で答えてから、もう一度 retry してください。", file=sys.stderr)
            else:
                print(f"送れませんでした（{e}）。ペイン {meta['pane']} の画面を確認してください。", file=sys.stderr)
            return 1
        meta.pop("pending_first_prompt", None)
        transition(meta, RUNNING, "最初の指示を送り直した")
        write_meta(meta)
    print(f"{args.id} に最初の指示を送り直しました。届かなければ 30 秒後にまた知らせます。")
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


def hook_task_id() -> str | None:
    """hook を呼んだ作業エージェントのタスク ID。

    herdr のサーバーを再起動するとペインのシェルが作り直され、タブを作ったときに --env で渡した
    SECRETARY_TASK_ID が消える。そのペインで retry すると hook が何も記録できず、最初の指示が
    届いていないと誤判定してしまうので、herdr が付ける HERDR_PANE_ID からも引けるようにする。"""
    task_id = os.environ.get("SECRETARY_TASK_ID")
    if task_id:
        return task_id
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        return None
    owners = [m for m in all_metas() if m.get("pane") == pane and m["status"] != CLOSED]
    return max(owners, key=lambda m: parse_iso(m.get("started_at")))["id"] if owners else None


def cmd_hook(args) -> int:
    """作業エージェントの hook。何も出力しない（UserPromptSubmit の出力は文脈に混ざるため）。"""
    task_id = hook_task_id()
    if not task_id:
        return 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    ev = {"time": now_iso(), "task_id": task_id, "event": data.get("hook_event_name", "")}
    if ev["event"] == "UserPromptSubmit":
        prompt = (data.get("prompt") or "").lstrip()
        ev["from_secretary"] = prompt.startswith(SECRETARY_MARK)
        ev["from_system"] = prompt.startswith(SYSTEM_PROMPT_PREFIXES)
    elif ev["event"] == "Notification":
        ev["message"] = data.get("message", "")
    elif ev["event"] in ("PreToolUse", "PermissionRequest"):
        ev["tool"] = data.get("tool_name", "")
        inp = data.get("tool_input") or {}
        ev["input"] = {k: inp[k] for k in ("command", "file_path", "url", "pattern") if k in inp}
    elif ev["event"] in ("PostToolUse", "PostToolUseFailure"):
        ev["tool"] = data.get("tool_name", "")
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    fd = os.open(events_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
    return 0


def notify(line: str, cfg: dict) -> None:
    """席を外していても気づけるように、デスクトップ通知も出す（macOS は osascript）。"""
    mode = cfg.get("notify", "auto")
    if not mode or mode == "off":
        return
    text = line.removeprefix("[secretary] ")
    try:
        if mode == "auto":
            if shutil.which("osascript"):
                script = f"display notification {json.dumps(text)} with title \"秘書\""
                subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", "秘書", text], timeout=5, capture_output=True)
        else:
            subprocess.run(mode.replace("{text}", shlex.quote(text)), shell=True, timeout=5, capture_output=True)
    except Exception:
        pass


def take_over_watch() -> None:
    """監視ループは 1 本だけにする。秘書を再起動したら、新しい監視が古いほうを止めて引き継ぐ
    （古いほうの出力は、もういない秘書のセッションに流れているため）。"""
    import signal
    pid_path = home() / ".watch.pid"
    if pid_path.exists():
        try:
            old = int(pid_path.read_text())
            if old != os.getpid():
                os.kill(old, signal.SIGTERM)
                for _ in range(50):
                    os.kill(old, 0)
                    time.sleep(0.1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    pid_path.write_text(str(os.getpid()))


def cmd_watch(args) -> int:
    if not args.once:
        take_over_watch()
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
            if offset_path.exists():
                offset = int(offset_path.read_text() or 0)
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
                    live = herdr.live()
                except Exception as e:
                    lines.append(f"[secretary] snapshot に失敗: {e}")
            for meta in all_metas():
                line = periodic_checks(meta, cfg, live)
                if line:
                    write_meta(meta)
                    lines.append(line)

            # 3. 空きがあればキューから起動
            lines.extend(fill_slots(cfg, herdr))

        backlog = sum(1 for m in all_metas() if m["status"] == REVIEW)
        limit = cfg.get("review_backlog_alert", 0)
        flag = home() / ".backlog-alerted"
        if limit and backlog >= limit and not flag.exists():
            flag.touch()
            lines.append(f"[secretary] レビュー待ちが {backlog} 件たまっています")
        elif flag.exists() and backlog < limit:
            flag.unlink()

        for line in lines:
            print(line, flush=True)
            notify(line, cfg)
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

    for name, func in (("show", cmd_show), ("handback", cmd_handback), ("retry", cmd_retry)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(func=func)

    s = sub.add_parser("peek")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=30)
    s.set_defaults(func=cmd_peek)

    s = sub.add_parser("reply")
    s.add_argument("id")
    s.add_argument("text")
    s.set_defaults(func=cmd_reply)

    s = sub.add_parser("permissions")
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(func=cmd_permissions)

    s = sub.add_parser("allow")
    s.add_argument("rules", nargs="+")
    s.set_defaults(func=cmd_allow)

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
