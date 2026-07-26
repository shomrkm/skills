#!/usr/bin/env python3
"""shomrkm の個人 Notion を REST API 経由で操作する。

MCP が別ワークスペース (会社アカウント) に繋がっている環境から、個人の
Notion を読み書きするために使う。token が指すワークスペースに必ず届く。

認証: 環境変数 NOTION_TOKEN (Internal Integration Secret, ntn_ で始まる)

使い方:
    notion_api.py query <db>  [--filter JSON] [--sort PROP:asc|desc] [--limit N]
    notion_api.py get <page_id> [--content]
    notion_api.py create <db> --props JSON [--content-file PATH]
    notion_api.py update <page_id> --props JSON

<db> は tasks / projects / knowledge / lifelog / review / links / books。

--props は「人間が読める形」の JSON を受け取り、Notion のプロパティ形式へ
自動変換する。型はライブで取得したスキーマから判定するため、呼び出し側が
Notion の内部表現を知る必要はない。

    --props '{"Name": "本を読む", "task_type": "Today", "due_date": "2026-07-31"}'

終了コード: 0 成功 / 1 実行時エラー / 2 使い方の誤り
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"

# 2025-09-03 で databases/{id}/query は data_sources/{id}/query に移行した。
# 以降のバージョンを使う場合は data_source_id が必須になる。
NOTION_VERSION = "2025-09-03"

# data source ID。references/databases.md と同じ値 (collection:// の UUID)。
DATA_SOURCES = {
    "tasks": "b2b78cf4-0ad7-4bba-bfde-3f2691b3be25",
    "projects": "19b3bba9-342b-46c3-a3ce-c7b0b8ee468a",
    "lifelog": "3587b931-adde-44bb-bf56-4872c50a5ce8",
    "review": "2c90ee4d-a0dd-4c6a-a824-ab83cb73bb9f",
    "knowledge": "8288e3ed-6a64-4bc5-882c-04b78b775fc1",
    "links": "88bb9d19-d08f-4983-ba5a-1282c1044894",
    "books": "138dca21-71f3-4736-a01c-19d26ec2bf92",
}


def die(msg, code=1):
    print(f"エラー: {msg}", file=sys.stderr)
    sys.exit(code)


def token():
    t = os.environ.get("NOTION_TOKEN", "").strip()
    if not t:
        die(
            "NOTION_TOKEN が未設定です。\n"
            "  https://www.notion.so/my-integrations で Integration を作り、\n"
            "  ~/.claude/.env に NOTION_TOKEN=ntn_... を設定してください。"
        )
    return t


def request(method, path, body=None):
    """Notion API を叩いて JSON を返す。失敗時は原因を添えて終了する。"""
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            parsed = json.loads(detail)
            code = parsed.get("code", "")
            message = parsed.get("message", detail)
        except json.JSONDecodeError:
            code, message = "", detail

        # よくある失敗は原因と対処を具体的に出す。エラーコードだけ返しても
        # 呼び出し側 (エージェント) が次に何をすべきか分からないため。
        if code == "object_not_found":
            die(
                f"{message}\n"
                "  → Integration が対象 DB に接続されていない可能性が高いです。\n"
                "    Notion で DB を開き [...] → Connections から Integration を追加してください。"
            )
        if code == "unauthorized":
            die(f"{message}\n  → NOTION_TOKEN が無効です。再発行してください。")
        if code == "validation_error":
            die(f"{message}\n  → プロパティ名や値の形式を確認してください。")
        die(f"HTTP {e.code} {code}: {message}")
    except urllib.error.URLError as e:
        die(f"接続失敗: {e.reason}")


def resolve_ds(name):
    key = name.lower()
    if key in DATA_SOURCES:
        return DATA_SOURCES[key]
    # UUID を直接渡された場合はそのまま使う
    if len(name.replace("-", "")) == 32:
        return name
    die(f"不明な DB: {name}\n  指定可能: {', '.join(sorted(DATA_SOURCES))}", 2)


def schema(ds_id):
    """data source のプロパティ名 → 型 のマップを返す。"""
    info = request("GET", f"/data_sources/{ds_id}")
    return {k: v.get("type") for k, v in info.get("properties", {}).items()}


def to_notion_value(prop_type, value):
    """人間が読める値を Notion のプロパティ形式に変換する。"""
    if value is None:
        return None
    if prop_type == "title":
        return {"title": [{"text": {"content": str(value)}}]}
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}
    if prop_type == "select":
        return {"select": {"name": str(value)}}
    if prop_type == "status":
        return {"status": {"name": str(value)}}
    if prop_type == "multi_select":
        items = value if isinstance(value, list) else [value]
        return {"multi_select": [{"name": str(v)} for v in items]}
    if prop_type == "date":
        # "2026-07-31" でも {"start": ..., "end": ...} でも受ける
        return {"date": value if isinstance(value, dict) else {"start": str(value)}}
    if prop_type == "checkbox":
        # databases.md の記法 (__YES__/__NO__) も受け付ける
        if isinstance(value, str):
            return {"checkbox": value.upper() in ("__YES__", "TRUE", "YES", "1")}
        return {"checkbox": bool(value)}
    if prop_type == "number":
        return {"number": float(value)}
    if prop_type == "url":
        return {"url": str(value)}
    if prop_type == "relation":
        items = value if isinstance(value, list) else [value]
        # URL でもページ ID でも受ける
        return {"relation": [{"id": str(v).rstrip("/").split("/")[-1].split("-")[-1]
                              if str(v).startswith("http") else str(v)} for v in items]}
    if prop_type in ("formula", "rollup", "created_time", "last_edited_time"):
        return None  # 計算列は書き込めない
    return {"rich_text": [{"text": {"content": str(value)}}]}


def build_props(ds_id, raw):
    """--props の JSON を Notion 形式に変換する。未知のプロパティは警告する。"""
    types = schema(ds_id)
    out = {}
    for name, value in raw.items():
        if name not in types:
            print(f"警告: プロパティ '{name}' は DB に存在しません。無視します。",
                  file=sys.stderr)
            continue
        converted = to_notion_value(types[name], value)
        if converted is None:
            print(f"警告: '{name}' ({types[name]}) は書き込めません。無視します。",
                  file=sys.stderr)
            continue
        out[name] = converted
    return out


def flatten(prop):
    """Notion のプロパティ値を、読みやすいスカラーに落とす。"""
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return None
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v) or None
    if t in ("select", "status"):
        return v.get("name")
    if t == "multi_select":
        return [x.get("name") for x in v]
    if t == "date":
        return v.get("start") if not v.get("end") else f"{v['start']}..{v['end']}"
    if t == "relation":
        return [x.get("id") for x in v]
    if t == "formula":
        return v.get(v.get("type"))
    if t == "rollup":
        return v.get(v.get("type"))
    if t == "people":
        return [x.get("name") for x in v]
    return v


def simplify(page):
    props = {k: flatten(v) for k, v in page.get("properties", {}).items()}
    return {
        "id": page.get("id"),
        "url": page.get("url"),
        "properties": {k: v for k, v in props.items() if v not in (None, [], "")},
    }


def blocks_to_markdown(blocks, depth=0):
    """ブロックを markdown に落とす。本文の参照用で、完全な変換ではない。"""
    lines = []
    pad = "  " * depth
    for b in blocks:
        t = b.get("type")
        data = b.get(t, {})
        text = "".join(x.get("plain_text", "") for x in data.get("rich_text", []))

        if t == "heading_1":
            lines.append(f"# {text}")
        elif t == "heading_2":
            lines.append(f"## {text}")
        elif t == "heading_3":
            lines.append(f"### {text}")
        elif t == "bulleted_list_item":
            lines.append(f"{pad}- {text}")
        elif t == "numbered_list_item":
            lines.append(f"{pad}1. {text}")
        elif t == "to_do":
            mark = "x" if data.get("checked") else " "
            lines.append(f"{pad}- [{mark}] {text}")
        elif t == "quote":
            lines.append(f"> {text}")
        elif t == "code":
            lines.append(f"```{data.get('language', '')}\n{text}\n```")
        elif t == "divider":
            lines.append("---")
        elif t == "child_database":
            lines.append(f"<database: {data.get('title', '')}>")
        elif t == "paragraph":
            lines.append(text)
        elif text:
            lines.append(text)

        if b.get("has_children") and t != "child_database":
            children = request("GET", f"/blocks/{b['id']}/children?page_size=100")
            lines.extend(blocks_to_markdown(children.get("results", []), depth + 1))
    return lines


def markdown_to_blocks(md):
    """markdown を Notion ブロックに変換する。新規ページ作成用。"""
    blocks = []
    in_code = False
    code_buf, code_lang = [], ""

    for line in md.split("\n"):
        if line.startswith("```"):
            if in_code:
                blocks.append({"object": "block", "type": "code", "code": {
                    "rich_text": [{"type": "text", "text": {"content": "\n".join(code_buf)[:2000]}}],
                    "language": code_lang or "plain text"}})
                code_buf, code_lang, in_code = [], "", False
            else:
                in_code, code_lang = True, line[3:].strip()
            continue
        if in_code:
            code_buf.append(line)
            continue

        def rt(s):
            return [{"type": "text", "text": {"content": s[:2000]}}]

        s = line.rstrip()
        if not s.strip():
            continue
        if s.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": rt(s[4:])}})
        elif s.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": rt(s[3:])}})
        elif s.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": rt(s[2:])}})
        elif s.strip() in ("---", "***"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif s.lstrip().startswith(("- [ ] ", "- [x] ")):
            body = s.lstrip()
            blocks.append({"object": "block", "type": "to_do", "to_do": {
                "rich_text": rt(body[6:]), "checked": body[3] == "x"}})
        elif s.lstrip().startswith(("- ", "* ")):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": rt(s.lstrip()[2:])}})
        elif s.startswith("> "):
            blocks.append({"object": "block", "type": "quote",
                           "quote": {"rich_text": rt(s[2:])}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": rt(s)}})

    if in_code and code_buf:
        blocks.append({"object": "block", "type": "code", "code": {
            "rich_text": [{"type": "text", "text": {"content": "\n".join(code_buf)[:2000]}}],
            "language": code_lang or "plain text"}})
    return blocks


def cmd_query(args):
    ds = resolve_ds(args.db)
    body = {"page_size": min(args.limit, 100)}
    if args.filter:
        body["filter"] = json.loads(args.filter)
    if args.sort:
        prop, _, direction = args.sort.partition(":")
        body["sorts"] = [{"property": prop,
                          "direction": "descending" if direction == "desc" else "ascending"}]

    results, cursor = [], None
    while len(results) < args.limit:
        if cursor:
            body["start_cursor"] = cursor
        res = request("POST", f"/data_sources/{ds}/query", body)
        results.extend(res.get("results", []))
        cursor = res.get("next_cursor")
        if not cursor or not res.get("has_more"):
            break

    print(json.dumps([simplify(p) for p in results[:args.limit]],
                     ensure_ascii=False, indent=2))


def cmd_get(args):
    page = request("GET", f"/pages/{args.page_id}")
    out = simplify(page)
    if args.content:
        blocks = request("GET", f"/blocks/{args.page_id}/children?page_size=100")
        out["content"] = "\n".join(blocks_to_markdown(blocks.get("results", [])))
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_create(args):
    ds = resolve_ds(args.db)
    body = {
        "parent": {"type": "data_source_id", "data_source_id": ds},
        "properties": build_props(ds, json.loads(args.props)),
    }
    if args.content_file:
        with open(args.content_file, encoding="utf-8") as f:
            body["children"] = markdown_to_blocks(f.read())[:100]
    page = request("POST", "/pages", body)
    print(json.dumps(simplify(page), ensure_ascii=False, indent=2))


def cmd_update(args):
    page = request("GET", f"/pages/{args.page_id}")
    parent = page.get("parent", {})
    ds = parent.get("data_source_id") or parent.get("database_id")
    if not ds:
        die("このページは DB 配下にないため、プロパティを更新できません。")
    body = {"properties": build_props(ds, json.loads(args.props))}
    updated = request("PATCH", f"/pages/{args.page_id}", body)
    print(json.dumps(simplify(updated), ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(
        description="個人 Notion を REST API で操作する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"DB 名: {', '.join(sorted(DATA_SOURCES))}")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="DB を検索する")
    q.add_argument("db")
    q.add_argument("--filter", help="Notion API の filter オブジェクト (JSON)")
    q.add_argument("--sort", help="PROP:asc | PROP:desc")
    q.add_argument("--limit", type=int, default=25)
    q.set_defaults(func=cmd_query)

    g = sub.add_parser("get", help="ページを取得する")
    g.add_argument("page_id")
    g.add_argument("--content", action="store_true", help="本文も markdown で取得する")
    g.set_defaults(func=cmd_get)

    c = sub.add_parser("create", help="ページを作成する")
    c.add_argument("db")
    c.add_argument("--props", required=True, help='例: \'{"Name":"タスク名"}\'')
    c.add_argument("--content-file", help="本文にする markdown ファイル")
    c.set_defaults(func=cmd_create)

    u = sub.add_parser("update", help="ページのプロパティを更新する")
    u.add_argument("page_id")
    u.add_argument("--props", required=True)
    u.set_defaults(func=cmd_update)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
