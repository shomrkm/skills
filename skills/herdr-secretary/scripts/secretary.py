#!/usr/bin/env python3
"""herdr-secretary: 秘書エージェントが作業エージェントを管理するための CLI。

標準ライブラリだけで動く。サブコマンド:

  init                       秘書ディレクトリを作る（初回だけ）
  new                        タスクを登録し、空きがあれば起動する
  list [--json]              全タスクの状態一覧
  inbox                      人間の判断が要るもの（blocked / review）を古い順に
  show ID                    タスクフォルダの中身（prompt / status / result）
  peek ID [--lines N]        作業エージェントの画面を少しだけ読む
  reply ID TEXT [--force]    入力待ち・レビュー中の作業エージェントに秘書から返事を送る
  choose ID N | --text TEXT  選択式の質問（AskUserQuestion）で N 番の選択肢を選ぶか、自由記述で答える
  permissions                許可待ちになったコマンドの集計と、許可ルールの候補
  allow RULE...              作業エージェントの許可リストにルールを足す（例: "Bash(git worktree:*)"）
  retry ID                   最初の指示を送り直す（起動画面で止まっていたとき）
  handback ID                manual（人間が介入中）を解除して秘書に戻す
  send ID TEXT               作業エージェントから、同じ space のタスクに伝言を送る（作業エージェントが使う）
  peers                      作業エージェントから、同じ space のタスクの一覧を見る（作業エージェントが使う）
  close ID [--keep-tab]      タスクを閉じ、タブも閉じる。space の最後のタスクなら space も閉じる（作業場所は消さない）
  cleanup ID [--yes]         閉じたタスクのタブと worktree を片付ける（--yes なしは確認だけ）
  doctor                     herdr の版・サーバー、hook の向き先、スキルの更新漏れを確かめる
  watch                      監視ループ。状態が変わったときだけ 1 行出力する
  follow [--once]            監視ループを裏で動かし、その出力をまだ出していない行から出す（Monitor ではこれを使う）
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
# 複数行の agent prompt は貼り付けとして入力され、UserPromptSubmit の prompt がこのタグで包まれる
PASTED_CONTENT_RE = re.compile(r"<pasted_content[^>]*>\s*")
# bash モード（`! pwd` など）の入力。モデルのターンは始まらないが Stop は来る
BASH_MODE_PREFIXES = ("<bash-input>", "<bash-stdout>", "<bash-stderr>")

# 状態
QUEUED = "queued"      # 並列上限のため起動待ち
RUNNING = "running"    # 作業中
BLOCKED = "blocked"    # 人間の判断待ち（許可待ち / 入力待ち / 異常）
MANUAL = "manual"      # 人間がペインで直接指示している
REVIEW = "review"      # result.md ができた。人間のレビュー待ち
CLOSED = "closed"

ACTIVE = {RUNNING, BLOCKED, MANUAL}          # 並列数に数える状態
NOTIFY_TO = {BLOCKED, REVIEW, MANUAL}        # この状態に入ったら通知する

# agent start --kind / --pane、agent list の agent_status、status --json がそろう版
MIN_HERDR_VERSION = (0, 9, 5)

# Claude Code が許可リストや acceptEdits があっても毎回確認するパス
PROTECTED_DIRS = (".claude",)
# 保護パスのうち、タスクの worktree の中なら PermissionRequest hook で確認なしに許可する .claude/ 直下のディレクトリ。
# settings.json などは作業エージェントが自分の権限を広げられてしまうので含めない
AUTO_ALLOW_CLAUDE_DIRS = ("skills", "commands", "agents")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")

QUESTION_TOOL = "AskUserQuestion"

DEFAULT_CONFIG = {
    "max_parallel": 10,
    "snapshot_interval_sec": 180,
    "silence_alert_min": 30,
    "long_tool_alert_min": 120,
    "manual_timeout_min": 15,
    "first_prompt_ack_sec": 30,
    # この時間帯は通知を出さず、明けたら溜まった変化を 1 行にまとめて出す。"" で無効
    "quiet_hours": "18:00-09:00",
    "review_backlog_alert": 5,
    "permission_repeat_quiet_min": 10,
    "update_check": True,
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


def wait_for_human(meta: dict, tool: dict, cfg: dict | None = None) -> str | None:
    """ツールが人間の操作待ちになった（許可ダイアログか選択式の質問）。"""
    if tool.get("name") == QUESTION_TOOL:
        return transition(meta, BLOCKED, f"選択待ち: {question_summary(tool.get('questions'))}")
    record_permission(meta["id"], tool)
    line = transition(meta, BLOCKED, f"許可待ち: {tool['label']}{'（保護パス）' if is_protected(tool) else ''}")
    # 人間が許可した直後に同じ種類の許可待ちが続くとき（.claude/ の Edit が何回も、など）は、ペインを見ている
    # はずなので通知を重ねない。状態は blocked にするので、受信箱には出る
    if not line:
        return None
    key = permission_key(tool)
    quiet = (cfg or DEFAULT_CONFIG).get("permission_repeat_quiet_min", 0) * 60
    last = meta.get("last_permission") or {}
    if quiet and last.get("key") == key and parse_iso(meta["last_event_at"]) - parse_iso(last.get("notified_at")) < quiet:
        return None
    meta["last_permission"] = {"key": key, "notified_at": meta["last_event_at"]}
    return line


def question_summary(questions: list | None) -> str:
    """選択式の質問を 1 行にする。秘書が画面を読まずに人間へ取り次げるように。"""
    if not questions:
        return QUESTION_TOOL
    q = questions[0]
    opts = " ".join(f"[{i}] {o}" for i, o in enumerate(q.get("options") or [], 1))
    rest = f"（ほか {len(questions) - 1} 問）" if len(questions) > 1 else ""
    multi = "（複数選択）" if q.get("multiSelect") else ""
    return f"{q.get('question', '')}{multi} {opts}{rest}".strip()


def apply_event(meta: dict, ev: dict, cfg: dict | None = None) -> str | None:
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
        if ev.get("bash_mode"):
            # 人間がペインで `! pwd` などを実行しただけ。作業エージェントは何も聞いていない
            return None
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
        if status == BLOCKED and meta.get("reason", "").startswith("選択待ち"):
            return None
        if meta.get("pending_tool") and (is_permission or asking_question(meta)):
            # 直前に始めようとしたツールが許可待ちの対象。何を許可すればいいかが一目で分かるようにする
            return wait_for_human(meta, meta["pending_tool"], cfg)
        return transition(meta, BLOCKED, f"許可待ち: {msg[:80]}" if msg else "許可待ち")

    if name == "PermissionRequest":
        # Claude Code が許可ダイアログを出した（herdr 自身もこの hook で blocked を判定している）
        if status in (MANUAL, REVIEW) or ev.get("auto_allowed"):
            return None
        meta["pending_tool"] = pending_tool(ev, meta["last_event_at"])
        return wait_for_human(meta, meta["pending_tool"], cfg)

    if name == "PreToolUse":
        meta["pending_tool"] = pending_tool(ev, meta["last_event_at"])
        return None

    if name in ("PostToolUse", "PostToolUseFailure"):
        meta.pop("pending_tool", None)
        # 許可されて作業が再開した
        if status == BLOCKED:
            return transition(meta, RUNNING, "再開")
        return None

    return None


def pending_tool(ev: dict, at: str) -> dict:
    inp = ev.get("input") or {}
    tool = {"name": ev.get("tool", ""), "label": tool_label(ev), "started_at": at}
    if inp.get("file_path"):
        tool["path"] = inp["file_path"]
    if inp.get("questions"):
        tool["questions"] = inp["questions"]
    return tool


def is_protected(tool: dict) -> bool:
    path = tool.get("path") or ""
    return tool.get("name") != "Bash" and any(f"/{d}/" in f"/{path}" for d in PROTECTED_DIRS)


def permission_key(tool: dict) -> str:
    if is_protected(tool):
        return "protected"
    return suggest_rule(tool) or tool.get("label", "")


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
    if name == QUESTION_TOOL or is_protected(tool):
        return None
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


HOOK_MISSING = "hook が届いていない可能性。sec doctor で確認"


def first_prompt_not_acked(meta: dict, herdr: "Herdr | None") -> str | None:
    """最初の指示が届いていない。入力欄に残っているだけなら Enter を 1 回送り直す。

    入力欄が見えなければ、起動直後の画面（初回の案内やフォルダの信頼確認）に吸い込まれた可能性が高い。
    確認画面に Enter を送ると既定の選択肢（No, exit のこともある）を選んでしまうので、人間に画面を進めてもらう。"""
    meta.pop("awaiting_ack_since")
    text = None
    if herdr:
        with contextlib.suppress(Exception):
            text = herdr.input_text(agent_name(meta))
    if text and SECRETARY_MARK in text:
        if not meta.get("first_prompt_resent"):
            with contextlib.suppress(Exception):
                herdr.send_keys(agent_name(meta), "enter")
                meta["first_prompt_resent"] = True
                meta["awaiting_ack_since"] = now_iso()
                return None
        meta["pending_first_prompt"] = True
        return transition(meta, BLOCKED, f"最初の指示が入力欄に残ったまま送信されない（ペイン {meta.get('pane')} で Enter を押すか retry）")
    meta["pending_first_prompt"] = True
    return transition(meta, BLOCKED, f"起動画面で止まっている（ペイン {meta.get('pane')} を進めてから retry）")


def periodic_checks(meta: dict, cfg: dict, live: tuple[set[str], dict[str, str]] | None,
                    check_silence: bool = True, herdr: "Herdr | None" = None) -> str | None:
    """イベントでは拾えない異常（ペイン消失・長時間無反応・manual の放置）を見る。

    check_silence が False のときは無反応の判定をしない（herdr の状態と突き合わせられる snapshot の周だけ判定する）。"""
    status = meta["status"]
    if status not in ACTIVE:
        return None
    now = time.time()

    if status == RUNNING and meta.get("awaiting_ack_since") and now - parse_iso(meta["awaiting_ack_since"]) > cfg.get("first_prompt_ack_sec", 30):
        return first_prompt_not_acked(meta, herdr)

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

    if status == RUNNING and check_silence and not meta.get("silence_alerted"):
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
            agent_status = live[1].get(agent_name(meta)) if live else None
            if agent_status == "working":
                return None  # herdr から見て動いている。次の snapshot でまた見る
            meta["silence_alerted"] = True
            # hook が届かないと許可待ちや完了を見逃し、無反応に見える。herdr の見立てで状態を補正する
            if agent_status == "blocked":
                return transition(meta, BLOCKED, f"許可待ちか選択待ち（herdr で検知。{HOOK_MISSING}）")
            if agent_status in ("idle", "done"):
                if take_new_result(meta):
                    return transition(meta, REVIEW, f"result.md あり（herdr で検知。{HOOK_MISSING}）")
                return transition(meta, BLOCKED, f"入力待ち（herdr で検知。{HOOK_MISSING}）")
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

def trust_dirs(settings: dict, dirs: list[Path]) -> None:
    """作業エージェントが確認なしで触れるディレクトリを、worker-settings の additionalDirectories と許可リストに足す。
    `claude --add-dir` で渡すと起動のたびに信頼確認が出て止まるので、設定ファイルに書く。"""
    perms = settings["permissions"]
    extra = perms.setdefault("additionalDirectories", [])
    allow = perms.setdefault("allow", [])
    for d in dirs:
        if str(d) not in extra:
            extra.append(str(d))
        allow += [r for r in (f"Edit(/{d}/**)", f"Read(/{d}/**)") if r not in allow]


def worker_cli_rules() -> list[str]:
    """作業エージェントが確認なしで使える sec のサブコマンド（同じ space のタスクとのやり取り）。"""
    wrapper = home() / "bin" / "sec"
    return [f"Bash({wrapper} send:*)", f"Bash({wrapper} peers:*)"]


def prepare_worker_settings(meta: dict, settings_path: Path) -> None:
    """repos.md は init の後にも書き足されるので、起動のたびにそのリポジトリの worktree 置き場を設定に足す。
    sec send / peers の許可も、init をやり直していない秘書ディレクトリに足す。"""
    settings = json.loads(settings_path.read_text())
    before = json.dumps(settings)
    wt = repo_worktrees_dir(Path(meta["repo"]))
    if wt:
        wt.mkdir(parents=True, exist_ok=True)
        trust_dirs(settings, [wt])
    allow = settings["permissions"].setdefault("allow", [])
    allow += [r for r in worker_cli_rules() if r not in allow]
    if json.dumps(settings) != before:
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")


def agent_name(meta: dict) -> str:
    """herdr のエージェント名。閉じたタスクのエージェントや別の秘書ディレクトリと衝突しないよう、
    タスク ID に秘書ディレクトリごとの短い識別子を付ける（[a-z][a-z0-9_-]{0,31}）。"""
    return meta.get("agent") or f"{meta['id']}-{home_tag()}"


TAB_NAME_MAX = 24


def tab_name(text: str) -> str:
    """タブに出す英語名。小文字の kebab-case にし、単語の切れ目で TAB_NAME_MAX 文字以内に収める。
    英数字がなければ空文字を返す。"""
    words = re.findall(r"[a-z0-9]+", text.lower())
    name = ""
    for w in words:
        joined = f"{name}-{w}" if name else w
        if len(joined) > TAB_NAME_MAX:
            break
        name = joined
    return name or (words[0][:TAB_NAME_MAX] if words else "")


def tab_label(meta: dict) -> str:
    """herdr のタブのラベル。秘書も人間もタスクを ID で呼ぶので、ID の後ろに内容がわかる英語名を付ける。"""
    return f"{meta['id']} {meta['name']}" if meta.get("name") else meta["id"]


def task_space(meta: dict) -> str:
    """タスクを置く space（herdr のワークスペース）の名前。関連するタスクは同じ space に並べる。"""
    return meta.get("space") or tab_label(meta)


def home_tag() -> str:
    import hashlib
    return hashlib.sha1(str(home()).encode()).hexdigest()[:6]


INPUT_PROMPT_CHARS = ("❯", ">")
# 空の入力欄に薄く出る入力例。人間が打った文字ではない
INPUT_PLACEHOLDER_RE = re.compile(r'^Try "[^"]*"$')


def input_box(screen: str) -> str | None:
    """画面から Claude Code の入力欄の中身を取り出す。入力欄は `─` の罫線 2 本に挟まれた `❯` で始まる行で、
    会話の履歴に出る `❯` の行（送信済みの指示）とは罫線で見分ける。入力欄がなければ None。"""
    lines = screen.splitlines()
    borders = [i for i, l in enumerate(lines) if len(l.strip()) >= 10 and set(l.strip()) <= {"─", "━"}]
    for top, bottom in reversed(list(zip(borders, borders[1:]))):
        body = lines[top + 1:bottom]
        if body and body[0].lstrip().startswith(INPUT_PROMPT_CHARS):
            first = body[0].lstrip()[1:]
            text = " ".join(l.strip() for l in [first, *body[1:]] if l.strip())
            return "" if INPUT_PLACEHOLDER_RE.match(text) else text
    return None


class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def version_str(v: tuple[int, ...]) -> str:
    return ".".join(map(str, v))


RESTART_SERVER_HINT = ("herdr のサーバーを再起動してください。すべてのペインのプロセス（作業中の Claude Code も）が止まるので、"
                       "人間が作業を区切ってから `herdr server stop` を実行し、`herdr` で起動し直します。秘書からは実行しません")


class Herdr:
    def __init__(self, cfg: dict):
        self.bin = os.environ.get("HERDR_BIN", cfg.get("herdr_bin", "herdr"))

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def check(self) -> list[str]:
        """秘書が使う herdr の機能がそろっているか。足りなければ、人間がやることを 1 件ずつ返す。"""
        if not self.available():
            return [f"herdr が見つかりません（{self.bin}）。herdr {version_str(MIN_HERDR_VERSION)} 以上を入れてください"]
        try:
            r = subprocess.run([self.bin, "--version"], capture_output=True, text=True, timeout=10)
            client = parse_version(r.stdout + r.stderr)
        except Exception as e:
            return [f"herdr --version が失敗しました（{e}）"]
        if client is None or client < MIN_HERDR_VERSION:
            # 古い herdr には agent start --kind / --pane がない（0.7.3 は unknown option: --kind で失敗する）
            found = version_str(client) if client else "不明"
            return [f"herdr が古いです（{found}）。{version_str(MIN_HERDR_VERSION)} 以上に上げてください（`herdr update`）。"
                    "上げたあとはサーバーの再起動も必要です"]
        try:
            r = subprocess.run([self.bin, "status", "--json"], capture_output=True, text=True, timeout=10)
            st = json.loads(r.stdout)
        except Exception as e:
            return [f"herdr status --json が失敗しました（{e}）"]
        server = st.get("server") or {}
        if not server.get("running"):
            return ["herdr のサーバーが動いていません。herdr を起動し、そのペインの中で秘書を動かしてください"]
        if not server.get("compatible", True) or not server.get("endpoint_compatible", True):
            proto = (st.get("client") or {}).get("protocol")
            return [f"herdr のクライアントとサーバーの protocol が合いません（サーバー {server.get('version')} / protocol "
                    f"{server.get('protocol')}、クライアント {version_str(client)} / protocol {proto}）。{RESTART_SERVER_HINT}"]
        if server.get("restart_needed") or server.get("server_binary_stale"):
            return [f"herdr を更新したあと、サーバーが古いまま動いています（サーバー {server.get('version')}）。{RESTART_SERVER_HINT}"]
        return []

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

    def agent_statuses(self) -> dict[str, str]:
        """名前付きエージェントの agent_status（idle / working / blocked / done / unknown）。"""
        return {a["name"]: a.get("agent_status", "unknown")
                for a in self._run("agent", "list").get("agents", []) if a.get("name")}

    def agent_names(self) -> set[str]:
        return set(self.agent_statuses())

    def live(self) -> tuple[set[str], dict[str, str]]:
        return self.pane_ids(), self.agent_statuses()

    def send_keys(self, agent: str, *keys: str) -> None:
        self._run("agent", "send-keys", agent, *keys)

    def send_text(self, pane: str, text: str) -> None:
        """Enter を押さずに文字だけを打つ。"""
        self._run("pane", "send-text", pane, text)

    def input_text(self, agent: str) -> str | None:
        """Claude Code の入力欄に入っている文字。入力欄が見えない（起動画面やダイアログ）なら None。"""
        r = self._run("agent", "read", agent, "--source", "visible", "--lines", "60")
        return input_box(r.get("read", {}).get("text") or r.get("text") or "")

    def close_tab(self, tab: str) -> None:
        self._run("tab", "close", tab)

    def workspaces(self) -> list[dict]:
        return self._run("workspace", "list").get("workspaces", [])

    def tab_ids(self, workspace: str) -> set[str]:
        return {t["tab_id"] for t in self._run("tab", "list", "--workspace", workspace).get("tabs", [])}

    def close_workspace(self, workspace: str) -> None:
        self._run("workspace", "close", workspace)

    def space_workspace(self, meta: dict) -> str | None:
        """タスクの space にあたる herdr のワークスペース。同じ space のタスクがいるワークスペースを優先し、
        なければ同じ名前のワークスペースを使う。どちらもなければ None。"""
        live = self.workspaces()
        ids = {w["workspace_id"] for w in live}
        for m in space_peers(meta["id"], task_space(meta)):
            if m.get("workspace") in ids:
                return m["workspace"]
        return next((w["workspace_id"] for w in live if w.get("label") == task_space(meta)), None)

    def start_worker(self, meta: dict, settings: Path) -> str:
        """タスクの space にタブを作り（フォーカスは移さない）、そのペインで Claude Code を起動する。ペイン ID を返す。
        space がなければワークスペースを作り、その最初のタブをこのタスクのタブにする。"""
        env = ["--env", f"SECRETARY_TASK_ID={meta['id']}", "--env", f"SECRETARY_HOME={home()}"]
        tab = None
        workspace = self.space_workspace(meta)
        if workspace:
            try:
                tab = self._run("tab", "create", "--workspace", workspace, "--cwd", meta["repo"],
                                "--label", tab_label(meta), "--no-focus", *env)
            except HerdrError as e:
                if e.code != "workspace_not_found":
                    raise
        if tab is None:
            tab = self._run("workspace", "create", "--cwd", meta["repo"], "--label", task_space(meta), "--no-focus", *env)
            workspace = tab["workspace"]["workspace_id"]
            self._run("tab", "rename", tab["tab"]["tab_id"], tab_label(meta))
        pane = tab["root_pane"]["pane_id"]
        meta["workspace"] = workspace
        meta["pane"] = pane
        meta["tab"] = tab.get("tab", {}).get("tab_id")
        return self.start_agent(meta, settings)

    def start_agent(self, meta: dict, settings: Path) -> str:
        pane = meta["pane"]
        prepare_worker_settings(meta, settings)
        # agent start は Claude Code が入力を受け付けられる状態になってから返る。
        # 作ったばかりのタブはシェルのプロンプトが出る前だと agent_pane_busy になるので少し待つ
        for attempt in range(20):
            try:
                self._run("agent", "start", agent_name(meta), "--kind", "claude", "--pane", pane,
                          "--timeout", "60000", "--", "--settings", str(settings), timeout=90)
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
    meta.pop("first_prompt_resent", None)


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
            hint = "。sec doctor で herdr の版とサーバーを確認" if isinstance(e, HerdrError) else ""
            line = transition(m, BLOCKED, f"異常: 起動に失敗 ({e}){hint}")
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

- セッションを始めたら、すぐに Monitor ツールで `{sec} follow` を起動する（起動済みなら不要）
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
    # タスクフォルダと、リポジトリにルールがないときの作業場所と、repos.md の worktree 置き場は確認なしで触れるようにする
    trust_dirs(settings, [tasks_dir(), h / "worktrees", *repo_worktrees_dirs()])
    settings["permissions"]["allow"] += worker_cli_rules()
    ws_path = h / "worker-settings.json"
    if ws_path.exists():
        # init のやり直し（hook の向き先を直すなど）で、sec allow で育てた許可リストや足したディレクトリを消さない
        with contextlib.suppress(Exception):
            old = json.loads(ws_path.read_text())["permissions"]
            for key in ("additionalDirectories", "allow"):
                cur = settings["permissions"][key]
                cur += [r for r in old.get(key, []) if r not in cur]
    ws_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")

    bin_dir = h / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "sec"
    wrapper.write_text(f'#!/bin/sh\nexec python3 {shlex.quote(str(SCRIPT_PATH))} "$@"\n')
    wrapper.chmod(0o755)

    # `cd ~/secretary && claude` だけで秘書として動き出すようにする
    claude_md = h / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(SECRETARY_CLAUDE_MD.format(skill=SCRIPT_PATH.parent.parent / "SKILL.md", sec=wrapper))
    else:
        # Monitor で sec watch を直接動かすと、Monitor が切れたときに監視ループも止まる
        text = claude_md.read_text()
        if f"`{wrapper} watch`" in text:
            claude_md.write_text(text.replace(f"`{wrapper} watch`", f"`{wrapper} follow`"))
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
    problems = Herdr(load_config()).check()
    if problems:
        print("\nherdr について、起動の前に直すことがあります:")
        for p in problems:
            print(f"- {p}")
    return 0


def repo_worktrees_dir(repo: Path) -> Path | None:
    """repos.md でそのリポジトリに `（worktrees: <パス>）` が書かれていれば、その絶対パス。
    リポジトリのルールで worktree をリポジトリの外に作る場合、作業エージェントがそこを
    確認なしで触れるように worker-settings に足す（trust_dirs）。"""
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


def repo_worktrees_dirs() -> list[Path]:
    dirs = (repo_worktrees_dir(p) for p in load_repos().values())
    return list(dict.fromkeys(d for d in dirs if d))


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
{attachments}{peers}
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


def attachments_section(paths: list[Path]) -> str:
    """依頼に添える資料。作業エージェントが読めない場所（人間の個人 Notion など）から秘書が取り出したもの。"""
    if not paths:
        return ""
    items = "\n".join(f"- `{p}`" for p in paths)
    return f"\n## 依頼に添える資料\n\n秘書が用意した資料です。作業を始める前に読んでください。\n\n{items}\n"


def space_peers(task_id: str, space: str) -> list[dict]:
    return [m for m in all_metas() if m["id"] != task_id and task_space(m) == space and m["status"] != CLOSED]


def peer_rows(peers: list[dict]) -> str:
    return "\n".join(f"- {m['id']}: {m['title']}（{Path(m['repo']).name}）" for m in peers) or "- （今はこのタスクだけ）"


def peers_section(task_id: str, space: str) -> str:
    """関連するタスクと同じ space で動くときに、作業エージェント同士で伝言を送る方法を伝える。"""
    sec = home() / "bin" / "sec"
    return f"""
## 同じ space のタスク

このタスクは herdr の space「{space}」で、関連するタスクと並んで動いています。いま同じ space にいるタスク:

{peer_rows(space_peers(task_id, space))}

相手に伝えたほうがいいこと（API の形が決まった、先にマージしてほしい、など）があれば、`{sec} send <タスク ID> "<伝言>"` で相手の作業エージェントに直接送ってください。伝言は秘書にも記録として届きます。相手からの伝言は `[secretary] <タスク ID> からの伝言` で始まります。後から加わったタスクも含めた今の一覧は `{sec} peers` で見られます。
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
    name = tab_name(args.name if args.name is not None else (args.title if args.title.isascii() else ""))
    if args.name is not None and not name:
        print(f"--name は英語で付けてください（例: fix-order-query）: {args.name}", file=sys.stderr)
        return 2
    repo = resolve_repo(args.repo)
    if repo is None:
        names = ", ".join(load_repos()) or "（repos.md が空）"
        print(f"リポジトリが見つかりません: {args.repo}（repos.md の名前: {names}）", file=sys.stderr)
        return 2
    attach = [Path(a).expanduser() for a in args.attach]
    missing = [str(a) for a in attach if not a.is_file()]
    if missing:
        print(f"--attach のファイルが見つかりません: {', '.join(missing)}", file=sys.stderr)
        return 2
    if len({a.name for a in attach}) != len(attach):
        print("--attach に同じ名前のファイルがあります。名前を変えてから渡してください。", file=sys.stderr)
        return 2
    space = " ".join((args.space or "").split())
    if args.space is not None and not space:
        print("--space が空です。関連するタスクと同じ space に入れるなら、その名前を付けてください。", file=sys.stderr)
        return 2

    cfg = load_config()
    if not args.no_launch:
        problems = Herdr(cfg).check()
        if problems:
            print("herdr の準備ができていないので登録しませんでした。人間に次のことを頼んでください:", file=sys.stderr)
            for p in problems:
                print(f"- {p}", file=sys.stderr)
            return 3
    with locked():
        tid = next_id()
        d = task_dir(tid)
        d.mkdir(parents=True)
        attached = []
        if attach:
            (d / "attachments").mkdir()
            for a in attach:
                attached.append(d / "attachments" / a.name)
                shutil.copyfile(a, attached[-1])
        (d / "prompt.md").write_text(PROMPT_TEMPLATE.format(
            id=tid, title=args.title, request=request, done=done, attachments=attachments_section(attached),
            peers=peers_section(tid, space) if space else "",
            status=d / "status.md", result=d / "result.md", worktree=home() / "worktrees" / tid))
        meta = {"id": tid, "title": args.title, "name": name, "repo": str(repo), "status": QUEUED,
                "reason": "", "created_at": now_iso(), "status_since": now_iso(), "pane": None,
                "history": []}
        # --space がなければ、そのタスクだけの space を作る
        meta["space"] = space or tab_label(meta)
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
        print(f"- {m['id']} [{m['status']} {ago(m.get('status_since'))}] {m['title']}{reason}  "
              f"repo={Path(m['repo']).name} space={task_space(m)}")
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


PEEK_MAX_LINES = 60  # 秘書のコンテキストを守る


def cmd_peek(args) -> int:
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    lines = min(args.lines, PEEK_MAX_LINES)
    if lines < args.lines:
        print(f"（--lines は {PEEK_MAX_LINES} 行までです。{lines} 行で読みます）", file=sys.stderr)
    print(Herdr(load_config()).read(agent_name(meta), lines))
    return 0


def cmd_reply(args) -> int:
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    herdr = Herdr(load_config())
    if not args.force:
        typing = None
        with contextlib.suppress(Exception):
            typing = herdr.input_text(agent_name(meta))
        if typing:
            # agent prompt は入力欄に文字を打ち込むので、人間の打ちかけの文字と混ざって送られてしまう
            print(f"{args.id} の入力欄に人間が打ちかけの文字があります（{typing[:60]}）。人間が入力中なので送りませんでした。"
                  "人間に確かめてから、送ってよければ --force を付けてください。", file=sys.stderr)
            return 1
    try:
        herdr.prompt(agent_name(meta), f"{SECRETARY_MARK} {args.text}")
    except HerdrError as e:
        if e.code == "agent_blocked" and asking_question(meta):
            print(f"{args.id} は選択式の質問を出しているので、文字では答えられません。"
                  f"`sec choose {args.id} <番号>` で選ぶか、ペイン {meta['pane']} で直接答えてください。", file=sys.stderr)
        elif e.code == "agent_blocked":
            print(f"{args.id} は許可や選択肢のダイアログを出しているので、文字では答えられません。"
                  f"ペイン {meta['pane']} で直接答えてください。", file=sys.stderr)
        else:
            print(f"送れませんでした（{e}）。ペイン {meta['pane']} で直接入力してください。", file=sys.stderr)
        return 1
    with locked():
        meta = read_meta(args.id)
        if meta["status"] in (BLOCKED, REVIEW):
            # review 中に直しを頼んだら作業に戻る。次に result.md が書き直されたら、また review になる
            transition(meta, RUNNING, "秘書が返事を送った" if meta["status"] == BLOCKED else "秘書が追加の指示を送った")
            write_meta(meta)
    print(f"{args.id} に送りました。")
    return 0


def caller_task(command: str) -> dict | None:
    """sec send / peers を呼んだ作業エージェントのタスク。作業エージェントのペインの外なら None。"""
    task_id = hook_task_id()
    if not task_id or not (task_dir(task_id) / "meta.json").exists():
        print(f"sec {command} は作業エージェントのペインから使います（タスクが分かりません）。"
              "秘書からタスクに送るときは sec reply を使ってください。", file=sys.stderr)
        return None
    return read_meta(task_id)


def cmd_peers(args) -> int:
    me = caller_task("peers")
    if me is None:
        return 1
    print(f"space「{task_space(me)}」のタスク（あなたは {me['id']}）:")
    print(peer_rows(space_peers(me["id"], task_space(me))))
    return 0


def cmd_send(args) -> int:
    """作業エージェントから、同じ space のタスクの作業エージェントに伝言を送る。秘書の監視ループにも記録が届く。"""
    me = caller_task("send")
    if me is None:
        return 1
    text = args.text.strip()
    if not text:
        print("伝言が空です。", file=sys.stderr)
        return 2
    peers = {m["id"]: m for m in space_peers(me["id"], task_space(me))}
    to = peers.get(args.id)
    if to is None:
        print(f"{args.id} は同じ space「{task_space(me)}」で動いているタスクではありません。送れる相手:\n"
              f"{peer_rows(list(peers.values()))}", file=sys.stderr)
        return 1
    if not to.get("pane"):
        print(f"{args.id} はまだ起動していません（{to['status']}）。起動してから送り直してください。", file=sys.stderr)
        return 1
    herdr = Herdr(load_config())
    typing = None
    with contextlib.suppress(Exception):
        typing = herdr.input_text(agent_name(to))
    if typing:
        print(f"{args.id} の入力欄に人間が打ちかけの文字があるので送りませんでした。少し待ってから送り直すか、"
              "急ぐなら status.md に書いて秘書に伝えてください。", file=sys.stderr)
        return 1
    sec = home() / "bin" / "sec"
    try:
        herdr.prompt(agent_name(to), f"{SECRETARY_MARK} {me['id']} からの伝言（同じ space のタスク。"
                                     f"返事は `{sec} send {me['id']} \"...\"`）: {text}")
    except HerdrError as e:
        why = "許可や選択肢のダイアログで止まっている" if e.code == "agent_blocked" else str(e)
        print(f"{args.id} に送れませんでした（{why}）。後で送り直すか、status.md に書いて秘書に伝えてください。", file=sys.stderr)
        return 1
    record_event({"time": now_iso(), "task_id": me["id"], "event": "Message", "to": args.id, "text": text})
    with locked():
        to = read_meta(args.id)
        if to["status"] in (BLOCKED, REVIEW):
            transition(to, RUNNING, f"{me['id']} から伝言が届いた")
            write_meta(to)
    print(f"{args.id} に送りました。秘書にも記録が届きます。")
    return 0


def message_line(ev: dict) -> str:
    text = " ".join(ev.get("text", "").split())
    if len(text) > 200:
        text = text[:197] + "..."
    return f"{SECRETARY_MARK} 伝言 {ev['task_id']}→{ev.get('to')}: {text}"


def asking_question(meta: dict) -> bool:
    return (meta.get("pending_tool") or {}).get("name") == QUESTION_TOOL


def cmd_choose(args) -> int:
    """選択式の質問（AskUserQuestion）で、人間が選んだ番号の選択肢を選ぶ。
    カーソルは先頭の選択肢にあるので、下へ (番号 - 1) 回動かして Enter を送る。

    --text のときは、選択肢の後ろにある「Type something.」までカーソルを動かし、文字を打って Enter を送る。"""
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    if not asking_question(meta):
        # 許可ダイアログも番号付きの選択肢なので、取り違えて許可しないように選択式の質問だけに限る
        print(f"{args.id} は選択式の質問を出していません（{meta['status']} {meta.get('reason', '')}）。"
              "許可ダイアログはペインで人間が答えてください。", file=sys.stderr)
        return 1
    if (args.number is None) == (args.text is None):
        print("番号か --text のどちらか一方を指定してください。", file=sys.stderr)
        return 2
    if args.number is not None and args.number < 1:
        print("番号は 1 から数えます。", file=sys.stderr)
        return 2
    tool = meta["pending_tool"]
    questions = tool.get("questions") or []
    answered = tool.get("answered", 0)
    if args.text is not None:
        question = questions[answered] if answered < len(questions) else None
        if question is None or question.get("multiSelect"):
            print(f"{args.id} の今の質問には --text で答えられません（選択肢が分からないか、複数選択）。"
                  f"ペイン {meta['pane']} で直接答えてください。", file=sys.stderr)
            return 1
        text = " ".join(args.text.split())
        if not text:
            print("--text が空です。", file=sys.stderr)
            return 2
    herdr = Herdr(load_config())
    name = agent_name(meta)
    try:
        if herdr.agent_statuses().get(name) not in ("blocked", None):
            print(f"{args.id} の画面は選択待ちになっていません。`sec peek {args.id}` で確認してください。", file=sys.stderr)
            return 1
        if args.text is None:
            herdr.send_keys(name, *(["down"] * (args.number - 1)), "enter")
        else:
            downs = ["down"] * len(question.get("options") or [])
            if downs:
                herdr.send_keys(name, *downs)
            herdr.send_text(meta["pane"], text)
            herdr.send_keys(name, "enter")
    except HerdrError as e:
        print(f"送れませんでした（{e}）。ペイン {meta['pane']} で直接答えてください。", file=sys.stderr)
        return 1
    with locked():
        meta = read_meta(args.id)
        if asking_question(meta):
            meta["pending_tool"]["answered"] = answered + 1
            write_meta(meta)
    what = f"{args.number} 番を選びました" if args.text is None else "自由記述で答えました"
    print(f"{args.id} で {what}。質問が複数あるときは次の質問に進むので、"
          f"`sec peek {args.id}` で画面を確認してください。")
    return 0


def cmd_permissions(args) -> int:
    """許可待ちになったツールを、許可ルールの候補ごとに多い順に出す。"""
    p = home() / "permissions.jsonl"
    if not p.exists():
        print("まだ許可待ちの記録はありません。")
        return 0
    allowed = set(json.loads((home() / "worker-settings.json").read_text())["permissions"].get("allow", []))
    rules: dict[str, dict] = {}
    protected: dict[str, dict] = {}
    for raw in p.read_text().splitlines():
        try:
            r = json.loads(raw)
        except Exception:
            continue
        if r.get("name") == QUESTION_TOOL:
            continue
        if is_protected(r):
            bucket, key = protected, r.get("name", "?")
        else:
            bucket, key = rules, suggest_rule(r) or r.get("label", "?")
        e = bucket.setdefault(key, {"tasks": set(), "count": 0, "example": r.get("label", "")})
        e["tasks"].add(r["task_id"])
        e["count"] += 1
    rows = sorted(rules.items(), key=lambda kv: -kv[1]["count"])
    for rule, e in rows[: args.top]:
        note = ("  ← 許可済みなのに止まった。複合コマンド（; や &&）か、作業ディレクトリの外を触ったのが原因"
                if rule in allowed else "")
        print(f"- {e['count']} 回（{len(e['tasks'])} タスク）: {rule}    例: {e['example']}{note}")
    if rows:
        print("\n止まってほしくないものは `sec allow '<ルール>'` で許可リストに足せます。"
              "push やデプロイなど、人間が確認したい操作は足さないでください。")
    if protected:
        dirs = " / ".join(f"{d}/" for d in PROTECTED_DIRS)
        print(f"\n許可リストでは避けられないもの（{dirs} は Claude Code の保護パスで、allow や acceptEdits があっても毎回確認される）:")
        for name, e in sorted(protected.items(), key=lambda kv: -kv[1]["count"]):
            print(f"- {e['count']} 回（{len(e['tasks'])} タスク）: {name}    例: {e['example']}")
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
            text = herdr.input_text(agent_name(meta))
            if text and SECRETARY_MARK in text:
                # 前に送った指示が入力欄に残っている。もう一度打つと 2 つ並ぶので、Enter だけ送る
                herdr.send_keys(agent_name(meta), "enter")
                meta["awaiting_ack_since"] = now_iso()
            elif text:
                write_meta(meta)
                print(f"{args.id} の入力欄に人間が打ちかけの文字があります（{text[:60]}）。"
                      "送ると混ざるので、人間に送るか消してもらってから retry してください。", file=sys.stderr)
                return 1
            else:
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


def close_task_tab(meta: dict, herdr: Herdr) -> str:
    """タスクのタブを閉じる。space に残るのがこのタブだけで、同じ space に閉じていないタスクもなければ、
    space（ワークスペース）ごと閉じる。人間が足したタブや --keep-tab で残したタブがあれば space は残す。"""
    ws = meta.get("workspace")
    if ws and not any(m.get("workspace") == ws for m in space_peers(meta["id"], task_space(meta))):
        tabs = None
        with contextlib.suppress(HerdrError):
            tabs = herdr.tab_ids(ws)
        if tabs == {meta["tab"]}:
            herdr.close_workspace(ws)
            return f"space「{task_space(meta)}」の最後のタスクだったので、space（{ws}）ごと閉じました"
    herdr.close_tab(meta["tab"])
    return f"タブ {meta['tab']} を閉じました"


def cmd_close(args) -> int:
    """タスクを閉じ、タブも閉じる（中の Claude Code も終わる）。worktree は消さない。"""
    cfg = load_config()
    herdr = Herdr(cfg)
    with locked():
        meta = read_meta(args.id)
        transition(meta, CLOSED, args.reason or "")
        write_meta(meta)
        lines = fill_slots(cfg, herdr)
    tab_note = "タブは残しています"
    if meta.get("tab") and not args.keep_tab:
        try:
            if meta.get("pane") in herdr.pane_ids():
                tab_note = close_task_tab(meta, herdr)
            else:
                tab_note = "タブはもうありません"
        except HerdrError as e:
            tab_note = f"タブを閉じられませんでした（{e}）"
    print(f"{args.id} を閉じました。{tab_note}。作業場所（worktree）は残っています。"
          f"消すなら `sec cleanup {args.id}` で候補を人間に見せてください。")
    for line in lines:
        print(line)
    return 0


def git(*args: str, cwd: Path, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def task_worktrees(meta: dict) -> list[dict]:
    """タスクの作業場所らしい worktree（リポジトリ本体は除く）。

    作業エージェントはリポジトリのルールで場所を決めるので、既定の置き場、タスクフォルダの文書に出てくるパス、
    パスやブランチ名にタスク ID を含むもの、を候補にする。"""
    repo = Path(meta["repo"])
    try:
        r = git("worktree", "list", "--porcelain", cwd=repo)
    except Exception:
        return []
    entries, cur = [], {}
    for line in r.stdout.splitlines() + [""]:
        if not line:
            if cur:
                entries.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line.removeprefix("worktree ")
        elif line.startswith("branch "):
            cur["branch"] = line.removeprefix("branch refs/heads/")
    docs = "".join((task_dir(meta["id"]) / n).read_text() for n in ("status.md", "result.md")
                   if (task_dir(meta["id"]) / n).exists())
    id_re = re.compile(rf"(?<![0-9a-z]){re.escape(meta['id'])}(?![0-9])")
    found = []
    for e in entries[1:]:
        path = e.get("path", "")
        if (Path(path) == home() / "worktrees" / meta["id"] or path in docs
                or id_re.search(Path(path).name) or id_re.search(e.get("branch", ""))):
            with contextlib.suppress(Exception):
                e["dirty"] = bool(git("status", "--porcelain", cwd=Path(path)).stdout.strip())
            found.append(e)
    return found


def cmd_cleanup(args) -> int:
    """閉じたタスクのタブと worktree を片付ける。--yes がなければ、何を消すかを出すだけ。"""
    cfg = load_config()
    meta = read_meta(args.id)
    if meta["status"] != CLOSED:
        print(f"{args.id} はまだ閉じていません（{meta['status']}）。先に `sec close {args.id}` してください。", file=sys.stderr)
        return 1
    herdr = Herdr(cfg)
    tab_alive = False
    if meta.get("tab") and not args.keep_tab:
        with contextlib.suppress(Exception):
            tab_alive = meta.get("pane") in herdr.pane_ids()
    worktrees = [] if args.keep_worktree else task_worktrees(meta)

    print(f"{args.id} の片付け:")
    print(f"- タブ {meta['tab']} を閉じる（中の Claude Code も終わる）" if tab_alive else "- 閉じるタブはありません")
    for w in worktrees:
        note = "  ← 未コミットの変更あり。消せません" if w.get("dirty") else ""
        print(f"- worktree {w['path']}（ブランチ {w.get('branch', '-')} は残す）を git worktree remove で消す{note}")
    if not args.keep_worktree and not worktrees:
        print("- タスクの worktree は見つかりませんでした（リポジトリのルールで別の名前にしているかもしれません）")
    if not args.yes:
        print(f"\n人間の確認を取ってから `sec cleanup {args.id} --yes` で実行してください。"
              "リポジトリに worktree の消し方のルールがあれば、そちらに従ってください。")
        return 0

    failed = False
    if tab_alive:
        try:
            print(f"{close_task_tab(meta, herdr)}。")
        except HerdrError as e:
            failed = True
            print(f"タブを閉じられませんでした（{e}）。", file=sys.stderr)
    for w in worktrees:
        r = git("worktree", "remove", w["path"], cwd=Path(meta["repo"]))
        if r.returncode == 0:
            print(f"worktree {w['path']} を消しました。")
        else:
            failed = True
            print(f"worktree {w['path']} を消せませんでした（{r.stderr.strip()}）。", file=sys.stderr)
    with locked():
        meta = read_meta(args.id)
        meta["cleaned_at"] = now_iso()
        write_meta(meta)
    return 1 if failed else 0


def skill_update_problems(fetch: bool = True) -> list[str]:
    """hook はこのスクリプトを直接読む。修正がマージされても、手元のリポジトリを pull するまで効かない。"""
    repo = SCRIPT_PATH.parent
    try:
        root = git("rev-parse", "--show-toplevel", cwd=repo).stdout.strip()
        if not root:
            return []
        if fetch:
            git("fetch", "--quiet", cwd=repo)
        r = git("rev-list", "--count", "HEAD..@{u}", cwd=repo)
    except Exception:
        return []
    behind = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
    if not behind:
        return []
    return [f"スキルのリポジトリ（{root}）が upstream より {behind} コミット遅れています。hook はここのスクリプトを"
            f"直接読むので、マージされた修正は pull するまで効きません。{root} で git pull したら、sec watch も起動し直してください"]


def hook_script_problems() -> list[str]:
    p = home() / "worker-settings.json"
    if not p.exists():
        return [f"{p} がありません。sec init を実行してください"]
    commands = {h.get("command", "") for entries in json.loads(p.read_text()).get("hooks", {}).values()
                for e in entries for h in e.get("hooks", [])}
    stale = sorted(c for c in commands if str(SCRIPT_PATH) not in c)
    if stale:
        return [f"作業エージェントの hook が別の場所のスクリプトを呼んでいます（{stale[0]}）。"
                f"{SCRIPT_PATH} で sec init をやり直してください（許可リストは残ります）"]
    return []


def cmd_doctor(args) -> int:
    problems = Herdr(load_config()).check() + hook_script_problems() + skill_update_problems(fetch=not args.no_fetch)
    if not problems:
        print("問題ありません。")
        return 0
    for p in problems:
        print(f"- {p}")
    return 1


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


def worktree_roots() -> list[Path]:
    """タスクの worktree が置かれる場所。worker-settings で信頼したディレクトリ（タスクフォルダは除く）と、
    Claude Code 自身の worktree 置き場（<repo>/.claude/worktrees）。"""
    roots: list[Path] = []
    with contextlib.suppress(Exception):
        settings = json.loads((home() / "worker-settings.json").read_text())
        roots += [Path(d) for d in settings["permissions"].get("additionalDirectories", [])]
    roots += [p / ".claude" / "worktrees" for p in load_repos().values()]
    return [r.expanduser().resolve() for r in roots if r.expanduser().resolve() != tasks_dir()]


def auto_allowed_edit(tool: str, inp: dict) -> bool:
    """タスクの worktree の中の .claude/skills/ などへの編集か。

    .claude/ は保護パスで、許可リストや acceptEdits、PreToolUse hook の permissionDecision: allow でも
    確認が出る。PermissionRequest hook が allow を返したときだけ確認なしで通る。"""
    if tool not in EDIT_TOOLS or not inp.get("file_path"):
        return False
    path = Path(inp["file_path"]).expanduser().resolve()
    for root in worktree_roots():
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        # <置き場>/<worktree>/.../.claude/<skills など>/<ファイル>
        if ".claude" in parts[1:]:
            i = parts.index(".claude", 1)
            if len(parts) > i + 2 and parts[i + 1] in AUTO_ALLOW_CLAUDE_DIRS:
                return True
    return False


def last_turn_is_bash_mode(transcript_path: str | None) -> bool:
    """会話の最後のやりとりが bash モードの入力か。bash モードでは UserPromptSubmit が来ず Stop だけが来る。"""
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256 * 1024))
            tail = f.read().decode(errors="replace").splitlines()
    except OSError:
        return False
    for raw in reversed(tail):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") not in ("user", "assistant") or entry.get("isMeta"):
            continue
        if entry["type"] == "assistant":
            return False
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            content = next((c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"), "")
        return isinstance(content, str) and content.lstrip().startswith(BASH_MODE_PREFIXES)
    return False


def cmd_hook(args) -> int:
    """作業エージェントの hook。PermissionRequest で自動許可するときだけ判定を出力する
    （UserPromptSubmit の出力は文脈に混ざるため、ほかは何も出さない）。"""
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
        if m := PASTED_CONTENT_RE.match(prompt):
            prompt = prompt[m.end():]
        ev["from_secretary"] = prompt.startswith(SECRETARY_MARK)
        ev["from_system"] = prompt.startswith(SYSTEM_PROMPT_PREFIXES)
    elif ev["event"] == "Stop":
        if last_turn_is_bash_mode(data.get("transcript_path")):
            ev["bash_mode"] = True
    elif ev["event"] == "Notification":
        ev["message"] = data.get("message", "")
    elif ev["event"] in ("PreToolUse", "PermissionRequest"):
        ev["tool"] = data.get("tool_name", "")
        inp = data.get("tool_input") or {}
        ev["input"] = {k: inp[k] for k in ("command", "file_path", "url", "pattern") if k in inp}
        if isinstance(inp.get("questions"), list):
            ev["input"]["questions"] = [
                {"question": q.get("question", ""), "multiSelect": bool(q.get("multiSelect")),
                 "options": [o.get("label", "") for o in q.get("options") or [] if isinstance(o, dict)]}
                for q in inp["questions"] if isinstance(q, dict)]
        if ev["event"] == "PermissionRequest" and auto_allowed_edit(ev["tool"], inp):
            ev["auto_allowed"] = True
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                     "decision": {"behavior": "allow"}}}))
    elif ev["event"] in ("PostToolUse", "PostToolUseFailure"):
        ev["tool"] = data.get("tool_name", "")
    record_event(ev)
    return 0


def record_event(ev: dict) -> None:
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    fd = os.open(events_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


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
    if not args.once and cfg.get("update_check", True):
        for p in hook_script_problems() + skill_update_problems():
            print(f"[secretary] {p}", flush=True)

    while True:
        lines: list[str] = []
        records: list[str] = []
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
                        if ev.get("event") == "Message":
                            records.append(message_line(ev))
                            continue
                        meta = read_meta(ev["task_id"])
                    except Exception:
                        continue
                    line = apply_event(meta, ev, cfg)
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
                before = json.dumps(meta, sort_keys=True)
                line = periodic_checks(meta, cfg, live, check_silence=live is not None or not use_snapshot,
                                       herdr=herdr if use_snapshot else None)
                if json.dumps(meta, sort_keys=True) != before:
                    write_meta(meta)
                if line:
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

        emit(lines, cfg, records)
        if args.once:
            return 0
        time.sleep(cfg["poll_interval_sec"])


def quiet_window(cfg: dict) -> tuple[str, str] | None:
    m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*", cfg.get("quiet_hours") or "")
    return (m.group(1), m.group(2)) if m else None


def in_quiet_hours(cfg: dict, now: datetime | None = None) -> bool:
    window = quiet_window(cfg)
    if not window:
        return False
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    start, end = (int(h) * 60 + int(m) for h, m in (t.split(":") for t in window))
    if start == end:
        return False
    return start <= minutes < end if start < end else minutes >= start or minutes < end


def emit(lines: list[str], cfg: dict, records: list[str] = ()) -> None:
    """通知を出す。静かな時間帯（quiet_hours）は溜めておき、明けたら 1 行にまとめて出す。
    records（作業エージェント同士の伝言）は秘書への記録なので、デスクトップ通知は出さない。"""
    pending = home() / ".quiet-pending"
    if in_quiet_hours(cfg):
        if lines or records:
            with open(pending, "a") as f:
                f.writelines(line + "\n" for line in [*lines, *records])
        return
    if pending.exists():
        held = [l for l in pending.read_text().splitlines() if l]
        pending.unlink()
        if held:
            window = "〜".join(quiet_window(cfg) or ()) or "静かな時間帯"
            joined = " / ".join(l.removeprefix(f"{SECRETARY_MARK} ") for l in held)
            lines = [f"{SECRETARY_MARK} 通知を止めていた間（{window}）の変化 {len(held)} 件: {joined}", *lines]
    for line in lines:
        print(line, flush=True)
        notify(line, cfg)
    for line in records:
        print(line, flush=True)


def watch_alive() -> bool:
    try:
        os.kill(int((home() / ".watch.pid").read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def ensure_watch() -> None:
    """監視ループを裏で動かす。Monitor が切れても止まらないよう、秘書のセッションから切り離して起動する。"""
    if watch_alive():
        return
    with open(home() / "watch.log", "ab") as out, open(home() / "watch.err", "ab") as err:
        subprocess.Popen([sys.executable, str(SCRIPT_PATH), "watch"], stdin=subprocess.DEVNULL, stdout=out,
                         stderr=err, cwd=home(), start_new_session=True)
    for _ in range(50):
        if watch_alive():
            return
        time.sleep(0.1)


def cmd_follow(args) -> int:
    """監視ループ（裏で動く sec watch）の出力を、まだ出していない行から出す。
    Monitor が 30 分で切れても監視ループは動き続け、張り直したときに切れていた間の行が出る。"""
    log, offset_path = home() / "watch.log", home() / ".follow-offset"
    ensure_watch()
    if offset_path.exists():
        offset = int(offset_path.read_text() or 0)
    else:
        offset = log.stat().st_size if log.exists() else 0
    last_check = time.time()
    while True:
        size = log.stat().st_size if log.exists() else 0
        if size < offset:
            offset = 0
        if size > offset:
            with open(log, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            complete = chunk[: chunk.rfind(b"\n") + 1]
            if complete:
                sys.stdout.write(complete.decode(errors="replace"))
                sys.stdout.flush()
                offset += len(complete)
                offset_path.write_text(str(offset))
                if args.once:
                    return 0
        if time.time() - last_check > 10:
            last_check = time.time()
            ensure_watch()
        time.sleep(0.5)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sec", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("new")
    s.add_argument("--repo", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--name", help="タブに出す短い英語名（例: fix-order-query）。省略すると英語の題から作る")
    s.add_argument("--done", help="完了条件（人間から受け取ったものをそのまま）")
    s.add_argument("--request", help="依頼内容。省略すると標準入力から読む")
    s.add_argument("--attach", action="append", default=[], metavar="FILE",
                   help="依頼に添える資料。タスクフォルダにコピーし、prompt.md にパスを書く（何度でも指定できる）")
    s.add_argument("--space", help="タスクを置く herdr の space（ワークスペース）の名前。同じ名前の space があればそこにタブを足し、"
                                   "なければ作る。関連するタスクは同じ space にする。省略するとそのタスクだけの space を作る")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("send")
    s.add_argument("id")
    s.add_argument("text")
    s.set_defaults(func=cmd_send)

    sub.add_parser("peers").set_defaults(func=cmd_peers)

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
    s.add_argument("--force", action="store_true", help="人間が入力欄に打ちかけていても送る（人間が OK したときだけ）")
    s.set_defaults(func=cmd_reply)

    s = sub.add_parser("choose")
    s.add_argument("id")
    s.add_argument("number", type=int, nargs="?", help="選択肢の番号（1 から）")
    s.add_argument("--text", help="選択肢にない答え。「Type something.」に打ち込んで送る")
    s.set_defaults(func=cmd_choose)

    s = sub.add_parser("cleanup")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true", help="人間が OK したら付ける。付けなければ何を消すかを出すだけ")
    s.add_argument("--keep-tab", action="store_true")
    s.add_argument("--keep-worktree", action="store_true")
    s.set_defaults(func=cmd_cleanup)

    s = sub.add_parser("doctor")
    s.add_argument("--no-fetch", action="store_true", help="upstream を fetch せずに比べる")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("permissions")
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(func=cmd_permissions)

    s = sub.add_parser("allow")
    s.add_argument("rules", nargs="+")
    s.set_defaults(func=cmd_allow)

    s = sub.add_parser("close")
    s.add_argument("id")
    s.add_argument("--reason")
    s.add_argument("--keep-tab", action="store_true", help="タブを閉じずに残す")
    s.set_defaults(func=cmd_close)

    sub.add_parser("hook").set_defaults(func=cmd_hook)

    s = sub.add_parser("watch")
    s.add_argument("--once", action="store_true", help="1 周だけ回して終わる（テスト用）")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("follow")
    s.add_argument("--once", action="store_true", help="新しい行が出たら、それを出して終わる")
    s.set_defaults(func=cmd_follow)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
