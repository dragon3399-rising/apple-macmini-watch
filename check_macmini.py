#!/usr/bin/env python3
"""Apple整備品ページを監視し、条件に合うMac miniが出たらLINEに通知する。

条件: Mac mini / M4系チップ / メモリ24GB以上
使い方:
  python3 check_macmini.py            # 通常実行（新規在庫があればLINE通知）
  python3 check_macmini.py --debug    # 解析結果の一覧を表示（通知なし）
  python3 check_macmini.py --test-notify  # LINEにテストメッセージを送る

LINE認証情報は環境変数 LINE_CHANNEL_ID / LINE_CHANNEL_SECRET / LINE_USER_ID、
なければ config.json（このスクリプトと同じフォルダ）から読む:
  {"channel_id": "...", "channel_secret": "...", "user_id": "..."}
※チャネルIDとシークレットから15分有効のステートレストークンを毎回取得する。
  既存の長期チャネルアクセストークン（eBayシステム側）には一切影響しない。
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"

URL = "https://www.apple.com/jp/shop/refurbished/mac"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")

MIN_MEMORY_GB = 24
CHIP_PATTERN = re.compile(r"M4")  # M4 / M4 Pro どちらもマッチ

JST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).isoformat(timespec='seconds')}] {msg}", flush=True)


def fetch_html() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_tiles(html: str) -> list[dict]:
    """埋め込みJSON（REFURB_GRID_BOOTSTRAP）から商品タイルを取り出す。"""
    m = re.search(r"window\.REFURB_GRID_BOOTSTRAP\s*=\s*", html)
    if not m:
        raise RuntimeError("REFURB_GRID_BOOTSTRAP が見つからない（ページ構造変更の可能性）")
    obj, _ = json.JSONDecoder().raw_decode(html, html.index("{", m.end()))

    tiles = []

    def walk(node):
        if isinstance(node, dict):
            dims = node.get("filters", {}).get("dimensions") if isinstance(node.get("filters"), dict) else None
            if dims and node.get("title"):
                tiles.append(node)
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return tiles


def parse_memory_gb(dims: dict) -> int:
    m = re.match(r"(\d+)", str(dims.get("tsMemorySize", "")))
    return int(m.group(1)) if m else 0


def tile_info(t: dict) -> dict:
    dims = t["filters"]["dimensions"]
    price = t.get("price", {}).get("currentPrice", {}).get("amount", "価格不明")
    link = t.get("productDetailsUrl") or t.get("url") or ""
    if link and link.startswith("/"):
        link = "https://www.apple.com" + link
    return {
        "part": t.get("partNumber", ""),
        "title": t.get("title", ""),
        "model": dims.get("refurbClearModel", ""),
        "memory_gb": parse_memory_gb(dims),
        "price": price,
        "url": link,
    }


def matches(info: dict) -> bool:
    return (
        info["model"] == "macmini"
        and CHIP_PATTERN.search(info["title"]) is not None
        and info["memory_gb"] >= MIN_MEMORY_GB
    )


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def get_line_config():
    cfg = {
        "channel_id": os.environ.get("LINE_CHANNEL_ID", ""),
        "channel_secret": os.environ.get("LINE_CHANNEL_SECRET", ""),
        "user_id": os.environ.get("LINE_USER_ID", ""),
    }
    if all(cfg.values()):
        return cfg
    file_cfg = load_json(CONFIG_PATH, None)
    if file_cfg and all(file_cfg.get(k) for k in ("channel_id", "channel_secret", "user_id")):
        return file_cfg
    return None


def get_stateless_token(cfg: dict) -> str:
    """チャネルID/シークレットから15分有効のステートレストークンを取得。"""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cfg["channel_id"],
        "client_secret": cfg["channel_secret"],
    }).encode("ascii")
    req = urllib.request.Request(
        "https://api.line.me/oauth2/v3/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def send_line(text: str) -> None:
    cfg = get_line_config()
    if not cfg:
        log("LINE設定なしのため送信スキップ（dry-run）。送信予定の内容:")
        log(text)
        return
    token = get_stateless_token(cfg)
    body = json.dumps({
        "to": cfg["user_id"],
        "messages": [{"type": "text", "text": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        log(f"LINE送信 status={resp.status}")


def main() -> int:
    if "--test-notify" in sys.argv:
        send_line("【テスト】Apple整備品 Mac mini監視の通知テストです。")
        return 0

    html = fetch_html()
    tiles = extract_tiles(html)
    infos = [tile_info(t) for t in tiles]
    hits = [i for i in infos if matches(i)]

    if "--debug" in sys.argv:
        models = {}
        for i in infos:
            models[i["model"]] = models.get(i["model"], 0) + 1
        print(f"全タイル数: {len(infos)} 機種内訳: {models}")
        for i in infos:
            if i["model"] == "macmini":
                print(f"  mini: {i['title']} / {i['memory_gb']}GB / {i['price']}")
        print(f"条件一致: {len(hits)}件")
        return 0

    state = load_json(STATE_PATH, {})  # {partNumber: first_seen, "_last_check_date": ...}
    meta_keys = {k for k in state if k.startswith("_")}
    seen = {k: v for k, v in state.items() if not k.startswith("_")}

    current_parts = {i["part"] for i in hits}
    new_hits = [i for i in hits if i["part"] not in seen]

    if new_hits:
        lines = ["【Apple整備品】条件一致のMac miniが出ました！"]
        for i in new_hits:
            lines.append(f"\n・{i['title']}\n  メモリ{i['memory_gb']}GB / {i['price']}\n  {i['url'] or URL}")
        send_line("\n".join(lines))

    # 在庫が消えたものはstateから外す（再入荷時に再通知するため）
    now = datetime.now(JST)
    new_state = {p: seen.get(p, now.isoformat(timespec="seconds")) for p in current_parts}
    for i in new_hits:
        new_state[i["part"]] = now.isoformat(timespec="seconds")
    # 日次ハートビート: 1日1回はstate.jsonが変化し、リポジトリに活動が記録される
    # （GitHubは60日間活動のないリポジトリのスケジュール実行を無効化するため）
    new_state["_last_check_date"] = now.strftime("%Y-%m-%d")

    if new_state != state:
        STATE_PATH.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"チェック完了: 全{len(infos)}件 / mini条件一致{len(hits)}件 / 新規{len(new_hits)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
