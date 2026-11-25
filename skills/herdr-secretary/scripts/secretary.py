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
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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
    # デスクトップ版で、タブを作ってから Claude Code が動き出すまで待つ秒数
    "desktop_start_timeout_sec": 60,
    # この時間帯は通知を出さず、明けたら溜まった変化を 1 行にまとめて出す。"" で無効
    "quiet_hours": "18:00-09:00",
    "review_backlog_alert": 5,
    "permission_repeat_quiet_min": 10,
    "update_check": True,
    "notify": "auto",
    "poll_interval_sec": 1.0,
    # herdr（ターミナル版）か desktop（デスクトップアプリ）か。auto は sec を呼んだペインの環境変数で決める
    "backend": "auto",
    "herdr_bin": "herdr",
    "herdr_app_bin": "herdr-app",
    "hc_bin": "hc",
    # 人間が話題にしたタスクのペインを、秘書のペインの横に並べる。並べる向きは right か down
    "auto_bring": True,
    "bring_split": "right",
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


# ---------------------------------------------------------------------------
# 進み具合のログ（status.tsv）
# ---------------------------------------------------------------------------
#
# 作業エージェントが sec note で 1 件 1 行を追記する。上書きも削除もしない。秘書は sec show で
# まだ読んでいない行だけを読む。人間も `column -s$'\t' -t status.tsv` でそのまま読める。

STATUS_COLUMNS = ("ts", "kind", "what", "why", "evidence")
NOTE_KINDS = ("progress", "decision", "blocker", "human")  # start は sec だけが書く
JST = timezone(timedelta(hours=9), "JST")
STATUS_SHOW_MAX = 20
FORMULA_PREFIXES = ("=", "+", "-", "@")


def status_log(task_id: str) -> Path:
    return task_dir(task_id) / "status.tsv"


def tsv_cell(text: str) -> str:
    """1 つのセルを 1 行に収める。表計算ソフトで開いたときに式として動かないよう、式に見える頭に ' を付ける。"""
    v = " ".join(str(text).replace("\t", " ").split())
    return f"'{v}" if v.startswith(FORMULA_PREFIXES) else v


def append_status(task_id: str, kind: str, what: str, why: str = "", evidence: str = "") -> None:
    p = status_log(task_id)
    ts = datetime.now(JST).isoformat(timespec="seconds")
    with open(p, "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write("\t".join(STATUS_COLUMNS) + "\n")
        f.write("\t".join([ts, *(tsv_cell(c) for c in (kind, what, why, evidence))]) + "\n")


def read_status(task_id: str) -> list[dict]:
    p = status_log(task_id)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            cells = (line.split("\t") + [""] * len(STATUS_COLUMNS))[:len(STATUS_COLUMNS)]
            rows.append(dict(zip(STATUS_COLUMNS, cells)))
    return rows


def status_line(row: dict) -> str:
    def plain(v: str) -> str:
        return v[1:] if v[:1] == "'" and v[1:2] in FORMULA_PREFIXES else v
    with contextlib.suppress(ValueError):
        row = {**row, "ts": datetime.fromisoformat(row["ts"]).astimezone(JST).strftime("%m/%d %H:%M")}
    out = f"- {row['ts']} [{row['kind']}] {plain(row['what'])}"
    for label, key in (("理由", "why"), ("根拠", "evidence")):
        if row.get(key):
            out += f"\n  - {label}: {plain(row[key])}"
    return out


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
            if meta.pop("pending_first_prompt", None) and status == BLOCKED:
                # 起動画面を人間が進めたあと、デスクトップ版はアプリが最初の指示を送る。retry は要らない
                return transition(meta, RUNNING, "最初の指示が届いた")
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


def first_prompt_not_acked(meta: dict, herdr: "Backend | None") -> str | None:
    """最初の指示が届いていない。入力欄に残っているだけなら Enter を 1 回送り直す。

    入力欄が見えなければ、起動直後の画面（初回の案内やフォルダの信頼確認）に吸い込まれた可能性が高い。
    確認画面に Enter を送ると既定の選択肢（No, exit のこともある）を選んでしまうので、人間に画面を進めてもらう。"""
    meta.pop("awaiting_ack_since")
    text = None
    if herdr:
        with contextlib.suppress(Exception):
            text = herdr.input_text(worker_addr(meta))
    if text and SECRETARY_MARK in text:
        if not meta.get("first_prompt_resent"):
            with contextlib.suppress(Exception):
                herdr.send_keys(worker_addr(meta), "enter")
                meta["first_prompt_resent"] = True
                meta["awaiting_ack_since"] = now_iso()
                return None
        meta["pending_first_prompt"] = True
        return transition(meta, BLOCKED, f"最初の指示が入力欄に残ったまま送信されない（ペイン {meta.get('pane')} で Enter を押すか retry）")
    meta["pending_first_prompt"] = True
    return transition(meta, BLOCKED, f"起動画面で止まっている（ペイン {meta.get('pane')} を進めてから retry）")


def periodic_checks(meta: dict, cfg: dict, live: tuple[set[str], dict[str, str]] | None,
                    check_silence: bool = True, herdr: "Backend | None" = None) -> str | None:
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
        if worker_addr(meta) not in agents and not meta.get("agent_gone"):
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
            agent_status = live[1].get(worker_addr(meta)) if live else None
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
# herdr を呼ぶのはここだけ。herdr には別々の CLI を持つ 2 つの版がある。
# - ターミナル版（Herdr）: `herdr` コマンド。herdr 0.9.5 のソース（src/cli, src/api/schema）と
#   公式 agent skill（skills/herdr/SKILL.md）に合わせてある
# - デスクトップ版（HerdrApp）: 画面（space・タブ・ペイン）は `herdr-app`、端末の中身は herdr-core の `hc`
#   で操作する。`herdr-app --skill` と `hc --skill` に合わせてある
# どちらで動かすかはタスクを登録したときに決めて meta.json の backend に残し、以後はそのタスクの版で操作する。
# ---------------------------------------------------------------------------

TERMINAL = "herdr"
DESKTOP = "desktop"
BACKENDS = (TERMINAL, DESKTOP)


def backend_kind(cfg: dict) -> str:
    """sec を呼んだペインがどちらの herdr の中か。ターミナル版の中は HERDR_ENV、デスクトップ版の中は
    HC_TERMINAL_ID が付く。デスクトップ版の端末の中でターミナル版を動かしていれば、内側のターミナル版を使う。"""
    if cfg.get("backend") in BACKENDS:
        return cfg["backend"]
    if os.environ.get("HERDR_ENV"):
        return TERMINAL
    if os.environ.get("HC_TERMINAL_ID"):
        return DESKTOP
    return TERMINAL


def make_backend(cfg: dict, kind: str | None = None) -> "Backend":
    return HerdrApp(cfg) if (kind or backend_kind(cfg)) == DESKTOP else Herdr(cfg)


def backend_for(meta: dict, cfg: dict) -> "Backend":
    return make_backend(cfg, task_backend(meta))

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
    """作業エージェントが確認なしで使える sec のサブコマンド（同じ space のタスクとのやり取りと、進み具合の記録）。"""
    wrapper = home() / "bin" / "sec"
    return [f"Bash({wrapper} send:*)", f"Bash({wrapper} peers:*)", f"Bash({wrapper} note:*)"]


def prepare_worker_settings(meta: dict, settings_path: Path) -> None:
    """repos.md は init の後にも書き足されるので、起動のたびにそのリポジトリの worktree 置き場を設定に足す。
    sec send / peers / note の許可も、init をやり直していない秘書ディレクトリに足す。"""
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


def task_backend(meta: dict) -> str:
    """タスクを動かしている herdr。このスキルがデスクトップ版に対応する前に起動したタスクはターミナル版。"""
    return meta.get("backend") or TERMINAL


def worker_addr(meta: dict) -> str:
    """作業エージェントの宛先。ターミナル版はエージェント名、デスクトップ版は hc の端末 ID（t7 など）。"""
    return (meta.get("terminal") or "") if task_backend(meta) == DESKTOP else agent_name(meta)


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
    return hashlib.sha1(str(home()).encode()).hexdigest()[:6]


INPUT_PROMPT_CHARS = ("❯", ">")
# 空の入力欄に薄く出る入力例。人間が打った文字ではない
INPUT_PLACEHOLDER_RE = re.compile(r'^Try "[^"]*"$')
ANSI_RE = re.compile(r"\x1b\[([0-9;:]*)([A-Za-z])")
SGR_DIM, SGR_NORMAL_INTENSITY = 2, 22
SGR_EXTENDED_COLORS = (38, 48, 58)


def sgr_dim(params: str, dim: bool) -> bool:
    """SGR の引数を読んで、薄い文字の状態を更新する。38;2;R;G;B のような色の値の 2 は薄い文字の指定ではない。"""
    codes = [int(c) if c else 0 for c in re.split("[;:]", params)] if params else [0]
    i = 0
    while i < len(codes):
        c = codes[i]
        if c in SGR_EXTENDED_COLORS:
            i += {5: 3, 2: 5}.get(codes[i + 1] if i + 1 < len(codes) else -1, 1)
            continue
        if c in (0, SGR_NORMAL_INTENSITY):
            dim = False
        elif c == SGR_DIM:
            dim = True
        i += 1
    return dim


def typed_text(line: str) -> str:
    """ANSI 付きの 1 行から、エスケープと薄い文字を除いた文字を返す。
    Claude Code は入力欄の入力候補を薄い文字で出すので、薄い文字は人間が打った文字ではない。"""
    out, dim, pos = [], False, 0
    for m in ANSI_RE.finditer(line):
        if not dim:
            out.append(line[pos:m.start()])
        pos = m.end()
        if m.group(2) == "m":
            dim = sgr_dim(m.group(1), dim)
    if not dim:
        out.append(line[pos:])
    return "".join(out)


def input_box(screen: str) -> str | None:
    """画面から Claude Code の入力欄の中身を取り出す。入力欄は `─` の罫線 2 本に挟まれた `❯` で始まる行で、
    会話の履歴に出る `❯` の行（送信済みの指示）とは罫線で見分ける。入力欄がなければ None。"""
    lines = [typed_text(l) for l in screen.splitlines()]
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


class Backend:
    """2 つの版で共通の処理。版ごとの操作は Herdr と HerdrApp が実装する。"""
    kind = ""
    # タスクのペインを秘書のタブに動かせるか（sec bring）
    can_move_panes = False

    def pane_ids(self) -> set[str]:
        return set(self.panes())

    def agent_names(self) -> set[str]:
        return set(self.agent_statuses())

    def live(self) -> tuple[set[str], dict[str, str]]:
        return self.pane_ids(), self.agent_statuses()

    def space_workspace(self, meta: dict) -> str | None:
        """タスクの space にあたるワークスペース。同じ space のタスクがいるワークスペースを優先し、
        なければ同じ名前のワークスペースを使う。どちらもなければ None。"""
        live = self.workspaces()
        ids = {w["workspace_id"] for w in live}
        for m in space_peers(meta["id"], task_space(meta)):
            # 秘書の横に表示しているタスクは、space のワークスペースにいない
            if task_backend(m) == self.kind and m.get("workspace") in ids and not m.get("shown"):
                return m["workspace"]
        return next((w["workspace_id"] for w in live if w.get("label") == task_space(meta)), None)


class Herdr(Backend):
    kind = TERMINAL
    can_move_panes = True

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

    def panes(self) -> dict[str, dict]:
        return {p["pane_id"]: p for p in self._run("pane", "list").get("panes", [])}

    def current_pane(self) -> dict | None:
        """このコマンドを呼んだペイン（秘書のペイン）。herdr のペインの外なら None。"""
        if not os.environ.get("HERDR_PANE_ID"):
            return None
        return self._run("pane", "current", "--current").get("pane")

    def move_pane(self, pane: str, *destination: str) -> dict:
        """ペインを中のプロセスごと動かす（フォーカスは移さない）。別のワークスペースに動かすとペイン ID が変わる。
        元のタブが空になればタブが閉じ、元のワークスペースが空になればワークスペースも閉じる。"""
        return self._run("pane", "move", pane, *destination, "--no-focus").get("move_result", {})

    def close_pane(self, pane: str) -> None:
        self._run("pane", "close", pane)

    def agent_statuses(self) -> dict[str, str]:
        """名前付きエージェントの agent_status（idle / working / blocked / done / unknown）。"""
        return {a["name"]: a.get("agent_status", "unknown")
                for a in self._run("agent", "list").get("agents", []) if a.get("name")}

    def send_keys(self, agent: str, *keys: str) -> None:
        self._run("agent", "send-keys", agent, *keys)

    def submit_text(self, meta: dict, text: str) -> None:
        """ダイアログの入力欄に文字を打って Enter を押す。"""
        self._run("pane", "send-text", meta["pane"], text)
        self.send_keys(worker_addr(meta), "enter")

    def input_text(self, agent: str) -> str | None:
        """Claude Code の入力欄に入っている文字。入力欄が見えない（起動画面やダイアログ）なら None。"""
        r = self._run("agent", "read", agent, "--source", "visible", "--lines", "60", "--format", "ansi")
        return input_box(r.get("read", {}).get("text") or r.get("text") or "")

    def close_tab(self, tab: str) -> None:
        self._run("tab", "close", tab)

    def workspaces(self) -> list[dict]:
        return self._run("workspace", "list").get("workspaces", [])

    def tab_ids(self, workspace: str) -> set[str]:
        return {t["tab_id"] for t in self._run("tab", "list", "--workspace", workspace).get("tabs", [])}

    def close_workspace(self, workspace: str) -> None:
        self._run("workspace", "close", workspace)

    def start_worker(self, meta: dict, settings: Path, prompt: str) -> str:
        """タスクの space にタブを作り（フォーカスは移さない）、そのペインで Claude Code を起動して最初の指示を送る。
        ペイン ID を返す。space がなければワークスペースを作り、その最初のタブをこのタスクのタブにする。"""
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
        self.start_agent(meta, settings)
        self.prompt(worker_addr(meta), prompt)
        return pane

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


# hc のエラーの kind を、ターミナル版の herdr と同じエラーコードにそろえる（呼び出し側の分岐を共通にする）
HC_ERROR_CODES = {"blocked": "agent_blocked", "not_found": "agent_not_found"}
HC_EXIT_BLOCKED = 9
CLAUDE_APP = "claude-code"


class HerdrApp(Backend):
    """デスクトップ版の herdr。ターミナル版の herdr とはサーバーも CLI も別で、
    `herdr` コマンドで作ったタブはデスクトップアプリには出ない。

    - space・タブ・ペインを作る、閉じる、一覧する: `herdr-app do <command> <JSON>` / `herdr-app ls`
    - 端末を読む、文字やキーを送る、状態を見る: `hc read` / `hc send` / `hc keys` / `hc ls`
    - 作業エージェントの宛先は hc の端末 ID（t7 など）。ペイン ID とは別物"""
    kind = DESKTOP

    def __init__(self, cfg: dict):
        self.app = os.environ.get("HERDR_APP_BIN", cfg.get("herdr_app_bin", "herdr-app"))
        self.hc = os.environ.get("HC_BIN", cfg.get("hc_bin", "hc"))
        self.start_timeout = cfg.get("desktop_start_timeout_sec", 60)

    def available(self) -> bool:
        return shutil.which(self.app) is not None and shutil.which(self.hc) is not None

    def check(self) -> list[str]:
        missing = [b for b in (self.app, self.hc) if shutil.which(b) is None]
        if missing:
            return [f"herdr デスクトップアプリのコマンドが見つかりません（{', '.join(missing)}）。"
                    "Herdr.app を起動すると ~/.local/bin に herdr-app と hc が入ります"]
        try:
            self._app("status")
        except Exception as e:
            return [f"herdr デスクトップアプリのウィンドウに接続できません（{e}）。Herdr.app を開いてください"]
        try:
            self._hc("server", "status")
        except Exception as e:
            return [f"herdr-core（hc）のサーバーに接続できません（{e}）。Herdr.app を開き直してください"]
        return []

    def _app(self, *args: str, timeout: int = 30) -> dict:
        r = subprocess.run([self.app, *args], capture_output=True, text=True, timeout=timeout)
        try:
            out = json.loads(r.stdout) if r.stdout.strip() else {}
        except json.JSONDecodeError:
            out = {}
        if r.returncode != 0 or (isinstance(out, dict) and out.get("error")):
            message = (out.get("error") if isinstance(out, dict) else None) or r.stderr.strip() or r.stdout.strip()
            raise HerdrError("", str(message) or f"herdr-app {' '.join(args)} が終了コード {r.returncode}")
        return out

    def do(self, command: str, **params) -> dict:
        """引数は JSON 1 つで渡す（`--argv '[...]'` の引用符の扱いを気にしなくて済む）。"""
        return self._app("do", command, json.dumps(params, ensure_ascii=False))

    def _hc(self, *args: str, timeout: int = 30, text: bool = False):
        r = subprocess.run([self.hc, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            kind, message = "", r.stderr.strip()
            with contextlib.suppress(Exception):
                body = json.loads(r.stderr).get("error", {})
                kind, message = body.get("kind", ""), body.get("message", message)
            if r.returncode == HC_EXIT_BLOCKED:
                kind = "blocked"
            raise HerdrError(HC_ERROR_CODES.get(kind, kind), message or f"hc {' '.join(args)} が終了コード {r.returncode}")
        if text:
            return r.stdout
        try:
            return json.loads(r.stdout) if r.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"text": r.stdout}

    def layout(self) -> list[dict]:
        return self._app("ls").get("spaces", [])

    def panes(self) -> dict[str, dict]:
        return {t["pane"]: {"pane_id": t["pane"], "tab_id": tab["id"], "workspace_id": s["id"], "terminal": t["terminal"]}
                for s in self.layout() for tab in s.get("tabs", []) for t in tab.get("terminals", []) if t.get("pane")}

    def workspaces(self) -> list[dict]:
        return [{"workspace_id": s["id"], "label": s.get("name")} for s in self.layout()]

    def tab_ids(self, workspace: str) -> set[str]:
        return {tab["id"] for s in self.layout() if s["id"] == workspace for tab in s.get("tabs", [])}

    def terminals(self) -> list[dict]:
        return self._hc("ls", "--json").get("terminals", [])

    def agent_statuses(self) -> dict[str, str]:
        """Claude Code が動いている端末の状態（idle / working / done / blocked / error / unknown）。
        Claude Code が終わってシェルに戻った端末は含めない。"""
        return {t["address"]: t.get("status") or "unknown"
                for t in self.terminals() if t.get("state") == "live" and t.get("agent") == CLAUDE_APP}

    def current_pane(self) -> dict | None:
        return None

    def send_keys(self, terminal: str, *keys: str) -> None:
        self._hc("keys", terminal, *keys)

    def submit_text(self, meta: dict, text: str) -> None:
        """hc には Enter を押さずに文字だけを打つ操作がないので、貼り付けと Enter を hc send でまとめて送る。
        選択式の質問を出している端末は blocked なので --force が要る。"""
        self._hc("send", "--force", worker_addr(meta), text)

    def input_text(self, terminal: str) -> str | None:
        return input_box(self._hc("read", terminal, "--lines", "0", text=True))

    def prompt(self, terminal: str, text: str) -> None:
        self._hc("send", terminal, text)

    def read(self, terminal: str, lines: int) -> str:
        text = self._hc("read", terminal, "--lines", str(lines), text=True).rstrip("\n")
        return "\n".join(text.splitlines()[-lines:])

    def close_pane(self, pane: str) -> None:
        self.do("pane.close", pane=pane)

    def close_tab(self, tab: str) -> None:
        self.do("tab.close", id=tab, confirmed=True)

    def close_workspace(self, workspace: str) -> None:
        self.do("space.close", id=workspace, confirmed=True)

    def peek(self, meta: dict) -> None:
        """タスクの端末を、いま表示している画面の上に重ねて出す。人間が閉じるかタブに移すまで出ている。"""
        self.do("terminal.peek", terminal=worker_addr(meta))

    def worker_argv(self, meta: dict, settings: Path) -> list[str]:
        """デスクトップ版のタブには環境変数を渡す引数がないので、env で包んで渡す。"""
        return ["env", f"SECRETARY_TASK_ID={meta['id']}", f"SECRETARY_HOME={home()}",
                "claude", "--settings", str(settings)]

    def start_worker(self, meta: dict, settings: Path, prompt: str) -> str:
        """タスクの space にタブを作り、Claude Code を起動する。表示している space とタブは切り替えない。
        最初の指示は、Claude Code が入力を受け付けられるようになったらアプリが送る（task）。"""
        prepare_worker_settings(meta, settings)
        spec = {"folder": meta["repo"], "argv": self.worker_argv(meta, settings), "task": prompt, "focus": False}
        workspace = self.space_workspace(meta)
        if workspace:
            made = self.do("tab.new", space=workspace, name=tab_label(meta), **spec)
        else:
            made = self.do("space.new", name=task_space(meta), tab=tab_label(meta), **spec)
        meta["workspace"] = made.get("space")
        meta["tab"] = made.get("tab")
        meta["terminal"] = made.get("terminal")
        meta["pane"] = next((p for p, info in self.panes().items() if info["terminal"] == meta["terminal"]), None)
        self.wait_for_agent(meta)
        return meta["pane"]

    def start_agent(self, meta: dict, settings: Path) -> str:
        """Claude Code が終わってシェルに戻った端末で、もう一度 Claude Code を起動する。"""
        prepare_worker_settings(meta, settings)
        self.prompt(worker_addr(meta), shlex.join(self.worker_argv(meta, settings)))
        self.wait_for_agent(meta)
        return meta["pane"]

    def wait_for_agent(self, meta: dict) -> None:
        """端末で Claude Code が動き出すのを待つ。hc wait はシェルのままだと終了まで待ってしまうので、hc ls を見る。"""
        deadline = time.time() + self.start_timeout
        while True:
            if worker_addr(meta) in self.agent_statuses():
                return
            if time.time() >= deadline:
                raise HerdrError("agent_not_ready", f"{self.start_timeout} 秒待っても Claude Code が起動しない")
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------

def first_prompt(meta: dict) -> str:
    return f"{SECRETARY_MARK} {task_dir(meta['id']) / 'prompt.md'} を読んで、その指示に従って作業してください。"


def launch(meta: dict, herdr: Backend) -> str | None:
    meta["agent"] = agent_name(meta)
    meta["backend"] = herdr.kind
    meta["started_at"] = now_iso()
    meta["last_event_at"] = meta["started_at"]
    try:
        herdr.start_worker(meta, home() / "worker-settings.json", first_prompt(meta))
    except HerdrError as e:
        if e.code == "agent_not_ready":
            # 起動時の確認（フォルダの信頼など）で止まっている。人間がペインで答えれば進む
            meta["pending_first_prompt"] = True
            return transition(meta, BLOCKED, f"起動時の確認待ち（ペイン {meta.get('pane')} で答えてから retry）")
        raise
    append_status(meta["id"], "start", "作業エージェントを起動した", evidence=f"pane {meta.get('pane')}")
    awaiting_first_prompt(meta)
    return transition(meta, RUNNING, "起動")


def awaiting_first_prompt(meta: dict) -> None:
    meta["awaiting_ack_since"] = now_iso()
    meta.pop("first_prompt_resent", None)


def send_first_prompt(meta: dict, herdr: Backend) -> None:
    herdr.prompt(worker_addr(meta), first_prompt(meta))
    awaiting_first_prompt(meta)


def fill_slots(cfg: dict) -> list[str]:
    """空きがあればキューの古い順に、登録したときの herdr で起動する。"""
    lines = []
    metas = all_metas()
    active = sum(1 for m in metas if m["status"] in ACTIVE)
    for m in sorted((m for m in metas if m["status"] == QUEUED), key=lambda m: m["created_at"]):
        if active >= cfg["max_parallel"]:
            break
        try:
            line = launch(m, backend_for(m, cfg))
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
    problems = make_backend(load_config()).check()
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
- 進み具合は `{sec} note <種類> "<1 行の内容>"` で記録してください。記録は `{status}` に 1 件 1 行で追記され、秘書はまだ読んでいない行を読んで人間に報告します。このファイルを直接書き換えないでください。
  - 種類は `progress`（区切りがついた）、`decision`（方針を決めた。`--why "<理由>"` を付ける）、`blocker`（詰まっている点。作業を止めて質問するほどではないとき）、`human`（人間から直接指示を受けて、何かが変わった）のどれかです。
  - PR の URL、コミット、worktree やファイルのパスは `--evidence "<URL やパス>"` に入れてください。
  - 判断の分かれ目と区切りだけを記録し、細かい操作は書かなくて大丈夫です。長い説明が要るときは、本文をタスクフォルダの別のファイルに書き、そのパスを `--evidence` に入れてください。
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
    herdr = make_backend(cfg)
    if not args.no_launch:
        problems = herdr.check()
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
            status=status_log(tid), sec=home() / "bin" / "sec", result=d / "result.md", worktree=home() / "worktrees" / tid))
        meta = {"id": tid, "title": args.title, "name": name, "repo": str(repo), "status": QUEUED,
                "reason": "", "created_at": now_iso(), "status_since": now_iso(), "pane": None,
                "backend": herdr.kind, "history": []}
        # --space がなければ、そのタスクだけの space を作る
        meta["space"] = space or tab_label(meta)
        write_meta(meta)
        lines = [] if args.no_launch else fill_slots(cfg)
    meta = read_meta(tid)
    if meta["status"] == QUEUED:
        print(f"{tid} を登録しました（キュー待ち。並列上限 {cfg['max_parallel']}）")
    for line in lines:
        print(line)
    if meta.get("pane"):
        auto_bring(tid, cfg)
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
              f"repo={Path(m['repo']).name} space={task_space(m)}{'（秘書の横に表示中）' if m.get('shown') else ''}")
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
    """status.tsv のまだ読んでいない行と result.md を出す。--all なら status.tsv を全部出す。"""
    d = task_dir(args.id)
    if not (d / "meta.json").exists():
        print(f"{args.id} というタスクはありません。", file=sys.stderr)
        return 1
    meta = read_meta(args.id)
    rows = read_status(args.id)
    seen = 0 if args.all else min(meta.get("status_seen", 0), len(rows))
    new = rows[seen:]
    print(f"# {meta['id']} [{meta['status']}] {meta['title']}  pane={meta.get('pane')}  repo={meta['repo']}")
    print("\n## 進み具合（status.tsv）\n")
    if not rows:
        print("（まだない）")
    elif not new:
        print(f"前回から増えた行はありません（全 {len(rows)} 行。全部見るなら `sec show {args.id} --all`）")
    else:
        shown = new if args.all else new[-STATUS_SHOW_MAX:]
        skipped = len(rows) - len(shown)
        if skipped:
            print(f"（前の {skipped} 行は省略。全部見るなら `sec show {args.id} --all`）")
        print("\n".join(status_line(r) for r in shown))
    # 古い形式。このスキルの更新前に起動したタスクや、作業エージェントが直接書いたときに残る
    legacy = d / "status.md"
    if legacy.exists():
        mtime = legacy.stat().st_mtime
        print("\n## status.md（古い形式）\n")
        if args.all or mtime > meta.get("status_md_seen_mtime", 0):
            print(legacy.read_text().strip() or "（空）")
        else:
            print(f"前回から変わっていません（全文は `sec show {args.id} --all`）")
    else:
        mtime = None
    p = d / "result.md"
    print("\n## result.md\n")
    print(p.read_text().strip() if p.exists() else "（まだない）")
    with locked():
        meta = read_meta(args.id)
        meta["status_seen"] = len(rows)
        if mtime is not None:
            meta["status_md_seen_mtime"] = mtime
        write_meta(meta)
    return 0


def cmd_note(args) -> int:
    """作業エージェントが、進み具合を status.tsv に 1 行足す。"""
    me = caller_task("note")
    if me is None:
        return 1
    what = " ".join(args.what.split())
    if not what:
        print("内容が空です。", file=sys.stderr)
        return 2
    append_status(me["id"], args.kind, what, args.why or "", args.evidence or "")
    print(f"{me['id']} の進み具合に記録しました（{args.kind}）。")
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
    print(backend_for(meta, load_config()).read(worker_addr(meta), lines))
    return 0


def cmd_reply(args) -> int:
    meta = read_meta(args.id)
    if not meta.get("pane"):
        print("まだ起動していません。", file=sys.stderr)
        return 1
    cfg = load_config()
    auto_bring(args.id, cfg)
    meta = read_meta(args.id)
    herdr = backend_for(meta, cfg)
    if not args.force:
        typing = None
        with contextlib.suppress(Exception):
            typing = herdr.input_text(worker_addr(meta))
        if typing:
            # agent prompt は入力欄に文字を打ち込むので、人間の打ちかけの文字と混ざって送られてしまう
            print(f"{args.id} の入力欄に人間が打ちかけの文字があります（{typing[:60]}）。人間が入力中なので送りませんでした。"
                  "人間に確かめてから、送ってよければ --force を付けてください。", file=sys.stderr)
            return 1
    try:
        herdr.prompt(worker_addr(meta), f"{SECRETARY_MARK} {args.text}")
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
        hint = "" if command == "note" else "秘書からタスクに送るときは sec reply を使ってください。"
        print(f"sec {command} は作業エージェントのペインから使います（タスクが分かりません）。{hint}", file=sys.stderr)
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
    herdr = backend_for(to, load_config())
    typing = None
    with contextlib.suppress(Exception):
        typing = herdr.input_text(worker_addr(to))
    if typing:
        print(f"{args.id} の入力欄に人間が打ちかけの文字があるので送りませんでした。少し待ってから送り直すか、"
              "急ぐなら `sec note blocker` で秘書に伝えてください。", file=sys.stderr)
        return 1
    sec = home() / "bin" / "sec"
    try:
        herdr.prompt(worker_addr(to), f"{SECRETARY_MARK} {me['id']} からの伝言（同じ space のタスク。"
                                     f"返事は `{sec} send {me['id']} \"...\"`）: {text}")
    except HerdrError as e:
        why = "許可や選択肢のダイアログで止まっている" if e.code == "agent_blocked" else str(e)
        print(f"{args.id} に送れませんでした（{why}）。後で送り直すか、`sec note blocker` で秘書に伝えてください。", file=sys.stderr)
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
    cfg = load_config()
    auto_bring(args.id, cfg)
    meta = read_meta(args.id)
    herdr = backend_for(meta, cfg)
    name = worker_addr(meta)
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
            herdr.submit_text(meta, text)
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
        herdr = backend_for(meta, load_config())
        try:
            if worker_addr(meta) not in herdr.agent_names():
                herdr.start_agent(meta, home() / "worker-settings.json")
                meta.pop("agent_gone", None)
                append_status(meta["id"], "start", "sec retry で作業エージェントを起動し直した",
                              evidence=f"pane {meta.get('pane')}")
            text = herdr.input_text(worker_addr(meta))
            if text and SECRETARY_MARK in text:
                # 前に送った指示が入力欄に残っている。もう一度打つと 2 つ並ぶので、Enter だけ送る
                herdr.send_keys(worker_addr(meta), "enter")
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


# ---------------------------------------------------------------------------
# 秘書の横に表示する
# ---------------------------------------------------------------------------
#
# 人間が話題にしているタスクのペインを 1 つだけ、秘書のタブに並べる。別のタスクの話になったら、
# 前のペインはそのタスクの space に新しいタブとして戻す。ペインは中の Claude Code ごと動き、止まらない。

class BringError(RuntimeError):
    pass


def place_task(meta: dict, move: dict) -> None:
    """pane move の結果で、タスクのペイン・タブ・ワークスペースを書き直す。"""
    pane = move.get("pane") or {}
    new, old = pane.get("pane_id"), meta.get("pane")
    if new and new != old:
        meta["pane_aliases"] = [*dict.fromkeys([*meta.get("pane_aliases", []), old])][-10:]
        meta["pane"] = new
    meta["tab"] = pane.get("tab_id", meta.get("tab"))
    meta["workspace"] = pane.get("workspace_id", meta.get("workspace"))


def return_to_space(meta: dict, herdr: Herdr, panes: dict[str, dict]) -> str:
    """秘書の横に表示しているタスクを、その space に新しいタブとして戻す。space がもうなければ作る。"""
    info = panes.get(meta.get("pane"))
    shown_tab = (meta.get("shown") or {}).get("tab")
    if info is None:
        meta.pop("shown", None)
        return f"{meta['id']} のペインはもうありません"
    if info.get("tab_id") != shown_tab:
        # 人間が別の場所へ動かした。そこに置いたままにする
        meta.pop("shown", None)
        place_task(meta, {"pane": info})
        return f"{meta['id']} は秘書の横から動かされていたので、そのままにしました"
    move = None
    workspace = herdr.space_workspace(meta)
    if workspace:
        try:
            move = herdr.move_pane(meta["pane"], "--new-tab", "--workspace", workspace, "--label", tab_label(meta))
        except HerdrError as e:
            if e.code != "workspace_not_found":
                raise
    if move is None:
        move = herdr.move_pane(meta["pane"], "--new-workspace", "--label", task_space(meta),
                               "--tab-label", tab_label(meta))
    place_task(meta, move)
    meta.pop("shown", None)
    return f"{meta['id']} は space「{task_space(meta)}」に戻しました"


def show_beside(meta: dict, herdr: Herdr, me: dict, cfg: dict) -> None:
    split = cfg.get("bring_split") if cfg.get("bring_split") in ("right", "down") else "right"
    move = herdr.move_pane(meta["pane"], "--tab", me["tab_id"], "--split", split, "--target-pane", me["pane_id"])
    if not move.get("changed", True) and move.get("reason") == "zoomed_tab":
        raise BringError("秘書のタブかタスクのタブがズーム中なので並べられません。ズームを解いてから、もう一度 bring してください")
    place_task(meta, move)
    meta["shown"] = {"tab": me["tab_id"], "since": now_iso()}


def bring(task_id: str | None, cfg: dict, quiet: bool = False) -> tuple[list[str], list[str]]:
    """task_id のペインを秘書の横に表示し、それまで表示していたタスクを space に戻す。
    task_id が None なら戻すだけ。(伝えること, うまくいかなかったこと) を返す。"""
    if task_id is not None and not (task_dir(task_id) / "meta.json").exists():
        return [], [f"{task_id} というタスクはありません"]
    if backend_kind(cfg) == DESKTOP:
        return peek_task(task_id, cfg)
    herdr = Herdr(cfg)
    done: list[str] = []
    with locked():
        try:
            me = herdr.current_pane()
            if me is None:
                return [], ["秘書が herdr のペインの外で動いているので、タスクのペインを横に並べられません"]
            panes = herdr.panes()
        except HerdrError as e:
            return [], [f"herdr からペインの場所を取れませんでした（{e}）"]
        target = read_meta(task_id) if task_id else None
        if target is not None:
            if target["status"] == CLOSED:
                return [], [f"{task_id} は閉じています"]
            if task_backend(target) != TERMINAL:
                return [], [f"{task_id} はデスクトップ版の herdr で動いているので、ターミナル版の秘書の横には出せません"]
            if target.get("pane") not in panes:
                return [], [f"{task_id} のペインがありません（{'まだ起動していません' if not target.get('pane') else 'ペインが閉じられています'}）"]
            if target.get("shown") and panes[target["pane"]].get("tab_id") == me["tab_id"]:
                return ([] if quiet else [f"{task_id} はもう秘書の横に表示しています"]), []
        for m in all_metas():
            if not m.get("shown") or (target is not None and m["id"] == target["id"]):
                continue
            try:
                done.append(return_to_space(m, herdr, panes))
            except HerdrError as e:
                return done, [f"{m['id']} を space に戻せなかったので、{task_id or ''} は並べていません（{e}）"
                              if target else f"{m['id']} を space に戻せませんでした（{e}）"]
            finally:
                write_meta(m)
        if target is None:
            return done or ["秘書の横に表示しているタスクはありません"], []
        target.pop("shown", None)
        try:
            show_beside(target, herdr, me, cfg)
        except (HerdrError, BringError) as e:
            return done, [f"{task_id} を秘書の横に並べられませんでした（{e}）"]
        finally:
            write_meta(target)
    return [f"{task_id} を秘書の横に表示しました", *done], []


def peek_task(task_id: str | None, cfg: dict) -> tuple[list[str], list[str]]:
    """デスクトップ版の sec bring。デスクトップ版には、別のタブの端末を秘書のタブへ動かす操作がない
    （pane.split と terminal.show は表示中の space を切り替えてしまう）。そこでタスクの端末を、
    秘書の画面の上に重ねて出す（terminal.peek）。人間が × で閉じるか、go to でタスクのタブに移る。"""
    if task_id is None:
        return ["デスクトップ版では秘書の横にタスクを並べていないので、戻すものはありません。重ねて出した画面は × で閉じられます"], []
    meta = read_meta(task_id)
    if meta["status"] == CLOSED:
        return [], [f"{task_id} は閉じています"]
    if task_backend(meta) != DESKTOP:
        return [], [f"{task_id} はターミナル版の herdr で動いているので、デスクトップ版の秘書からは出せません"]
    herdr = HerdrApp(cfg)
    try:
        if meta.get("pane") not in herdr.pane_ids():
            return [], [f"{task_id} のペインがありません（{'まだ起動していません' if not meta.get('pane') else 'ペインが閉じられています'}）"]
        herdr.peek(meta)
    except HerdrError as e:
        return [], [f"{task_id} の画面を出せませんでした（{e}）"]
    return [f"{task_id} の画面を秘書の画面の上に重ねて出しました。× で閉じ、go to でタスクのタブに移れます"], []


def auto_bring(task_id: str, cfg: dict) -> None:
    """人間がタスクに返事をしたり依頼したりしたら、そのタスクを秘書の横に出す。秘書が herdr の外なら何もしない。
    デスクトップ版では、重ねて出す画面が秘書の画面を覆ってしまうので自動では出さない。"""
    if not cfg.get("auto_bring", True) or not os.environ.get("HERDR_PANE_ID") or backend_kind(cfg) != TERMINAL:
        return
    if task_backend(read_meta(task_id)) != TERMINAL:
        return
    done, failed = bring(task_id, cfg, quiet=True)
    if done or failed:
        print("。".join(done + failed) + "。")


def cmd_bring(args) -> int:
    if (args.id is None) == (not args.back):
        print("タスク ID か --back のどちらか一方を指定してください。", file=sys.stderr)
        return 2
    done, failed = bring(args.id, load_config())
    if done:
        print("。".join(done) + "。")
    for f in failed:
        print(f"{f}。", file=sys.stderr)
    return 1 if failed else 0


def close_shown(task_id: str, herdr: Backend, keep: bool) -> str:
    """秘書の横に表示しているタスクを閉じるとき。秘書のタブは閉じず、タスクのペインだけを閉じる。
    --keep-tab なら、ペインを space に戻して残す。"""
    with locked():
        meta = read_meta(task_id)
        try:
            panes = herdr.panes()
            if meta.get("pane") not in panes:
                meta.pop("shown", None)
                return "ペインはもうありません"
            if keep:
                return f"{return_to_space(meta, herdr, panes)}。タブは残しています"
            herdr.close_pane(meta["pane"])
            meta.pop("shown", None)
            return f"秘書の横に表示していたペイン {meta['pane']} を閉じました"
        except HerdrError as e:
            return f"秘書の横のペインを閉じられませんでした（{e}）"
        finally:
            write_meta(meta)


def close_task_tab(meta: dict, herdr: Backend) -> str:
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
    with locked():
        meta = read_meta(args.id)
        transition(meta, CLOSED, args.reason or "")
        write_meta(meta)
        lines = fill_slots(cfg)
    herdr = backend_for(meta, cfg)
    tab_note = "タブは残しています"
    if meta.get("shown"):
        tab_note = close_shown(args.id, herdr, keep=args.keep_tab)
    elif meta.get("tab") and not args.keep_tab:
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
    docs = "".join((task_dir(meta["id"]) / n).read_text() for n in ("status.tsv", "status.md", "result.md")
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
    herdr = backend_for(meta, cfg)
    tab_alive = False
    if meta.get("tab") and not args.keep_tab:
        with contextlib.suppress(Exception):
            tab_alive = meta.get("pane") in herdr.pane_ids()
    worktrees = [] if args.keep_worktree else task_worktrees(meta)

    plan = [f"{args.id} の片付け:"]
    plan.append(f"- タブ {meta['tab']} を閉じる（中の Claude Code も終わる）" if tab_alive else "- 閉じるタブはありません")
    for w in worktrees:
        note = "  ← 未コミットの変更あり。消せません" if w.get("dirty") else ""
        plan.append(f"- worktree {w['path']}（ブランチ {w.get('branch', '-')} は残す）を git worktree remove で消す{note}")
    if not args.keep_worktree and not worktrees:
        plan.append("- タスクの worktree は見つかりませんでした（リポジトリのルールで別の名前にしているかもしれません）")
    plan_id = hashlib.sha256("\n".join(plan).encode()).hexdigest()[:12]
    if not args.yes:
        print("\n".join(plan))
        with locked():
            meta = read_meta(args.id)
            meta["cleanup_preview"] = plan_id
            write_meta(meta)
        print(f"\n人間の確認を取ってから `sec cleanup {args.id} --yes` で実行してください。"
              "リポジトリに worktree の消し方のルールがあれば、そちらに従ってください。")
        return 0
    if meta.get("cleanup_preview") != plan_id:
        # 消す前に、人間に見せる内容を一度出させる。見せた内容と今の内容が違えば、もう一度見せる
        print("\n".join(plan))
        print(f"\nこの内容を人間に見せていません。`sec cleanup {args.id}` で内容を出して人間の確認を取ってから、"
              "同じオプションで `--yes` を付けてください。", file=sys.stderr)
        return 1

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
    cfg = load_config()
    kind = backend_kind(cfg)
    print(f"このペインからは{'デスクトップ版' if kind == DESKTOP else 'ターミナル版'}の herdr でタスクを起動します。")
    problems = make_backend(cfg, kind).check() + hook_script_problems() + skill_update_problems(fetch=not args.no_fetch)
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
    pane, terminal = os.environ.get("HERDR_PANE_ID"), os.environ.get("HC_TERMINAL_ID")
    if pane:
        # ペインを別のワークスペースに動かすと ID が変わるが、中のプロセスの HERDR_PANE_ID は起動したときのまま
        owns = lambda m: task_backend(m) == TERMINAL and pane in (m.get("pane"), *m.get("pane_aliases", []))
    elif terminal:
        # デスクトップ版の端末 ID は、端末が生きている間変わらない
        owns = lambda m: task_backend(m) == DESKTOP and m.get("terminal") == terminal
    else:
        return None
    owners = [m for m in all_metas() if owns(m) and m["status"] != CLOSED]
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
            if old != os.getpid() and is_watch_process(old):
                os.kill(old, signal.SIGTERM)
                for _ in range(50):
                    if not is_watch_process(old):
                        break
                    time.sleep(0.1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    pid_path.write_text(str(os.getpid()))


def cmd_watch(args) -> int:
    if not args.once:
        take_over_watch()
    cfg = load_config()
    # ターミナル版とデスクトップ版のタスクが混ざっていてもよいように、両方の版を用意しておく
    backends = {k: make_backend(cfg, k) for k in BACKENDS}
    usable = {k: b.available() for k, b in backends.items()}
    offset_path = home() / ".watch-offset"
    offset = int(offset_path.read_text()) if offset_path.exists() else 0
    last_snapshot = 0.0
    if not any(usable.values()):
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

            # 2. 定期チェック（ペイン消失・無反応・manual 放置）。snapshot は動いているタスクがいる版だけ取る
            lives: dict[str, tuple[set[str], dict[str, str]]] = {}
            if time.time() - last_snapshot > cfg["snapshot_interval_sec"]:
                last_snapshot = time.time()
                for kind in sorted({task_backend(m) for m in all_metas() if m["status"] in ACTIVE}):
                    if not usable.get(kind):
                        continue
                    try:
                        lives[kind] = backends[kind].live()
                    except Exception as e:
                        lines.append(f"[secretary] snapshot に失敗: {e}")
            for meta in all_metas():
                kind = task_backend(meta)
                live = lives.get(kind)
                before = json.dumps(meta, sort_keys=True)
                line = periodic_checks(meta, cfg, live, check_silence=live is not None or not usable.get(kind),
                                       herdr=backends[kind] if usable.get(kind) else None)
                if json.dumps(meta, sort_keys=True) != before:
                    write_meta(meta)
                if line:
                    lines.append(line)

            # 3. 空きがあればキューから起動
            lines.extend(fill_slots(cfg))

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


def is_watch_process(pid: int) -> bool:
    """pid が動いている監視ループか。kill -0 は終わったのに親に回収されていないプロセス（ゾンビ）にも成功し、
    pid が別のプロセスに使い回されていても成功するので、ps で状態とコマンドも見る。"""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        r = subprocess.run(["ps", "-o", "stat=,command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except Exception:
        return True
    stat, _, command = r.stdout.strip().partition(" ")
    return bool(stat) and not stat.startswith("Z") and SCRIPT_PATH.name in command and " watch" in command


def watch_alive() -> bool:
    try:
        return is_watch_process(int((home() / ".watch.pid").read_text()))
    except (OSError, ValueError):
        return False


def reap_children() -> None:
    """終わった監視ループを回収する。sec follow が起動した監視ループは sec follow の子なので、
    回収しないと sec follow が動いている間ゾンビとして残る。"""
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass


def ensure_watch() -> None:
    """監視ループを裏で動かす。Monitor が切れても止まらないよう、秘書のセッションから切り離して起動する。"""
    reap_children()
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

    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--all", action="store_true", help="status.tsv を読んだ行も含めて全部出す")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("note", help="作業エージェントが進み具合を 1 行記録する")
    s.add_argument("kind", choices=NOTE_KINDS)
    s.add_argument("what", help="したこと、決めたこと、詰まっていることを 1 行で")
    s.add_argument("--why", help="理由")
    s.add_argument("--evidence", help="PR の URL、コミット、パスなど")
    s.set_defaults(func=cmd_note)

    for name, func in (("handback", cmd_handback), ("retry", cmd_retry)):
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

    s = sub.add_parser("bring", help="タスクのペインを秘書の横に表示し、前に表示していたタスクを space に戻す")
    s.add_argument("id", nargs="?")
    s.add_argument("--back", action="store_true", help="表示しているタスクを space に戻すだけ")
    s.set_defaults(func=cmd_bring)

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
