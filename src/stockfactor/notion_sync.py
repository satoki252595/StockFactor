"""Notion 連携 (R13): 日次スクリーニング結果を Notion データベースへ投入する。

GitHub Actions から実行。Notion REST API を直接叩く（無料）。
必要な環境変数:
  - NOTION_TOKEN : Notion 内部インテグレーションのシークレット（GitHub Secrets に登録）
任意:
  - NOTION_DB_ID : 投入先データベースID（既定 = 作成済みDB）

設計:
  - 同一日付が既に投入済みならスキップ（冪等。--force で再投入）。
  - Notion のレート制限(約3 req/s)に合わせて送信。
  - NOTION_TOKEN 未設定なら何もせず正常終了（未設定ユーザーでも日次ジョブが落ちない）。

使い方:
  PYTHONPATH=src python -m stockfactor.notion_sync --top 30
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import time

import requests

from . import config

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
# 既定の投入先（ページ「小型成長株の推し銘柄」配下に作成した DB）
DEFAULT_DB_ID = "9fd9f15ee2f24371844e2a5f8d6a5433"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _already_uploaded(db_id: str, token: str, date: str) -> bool:
    r = requests.post(
        f"{NOTION_API}/databases/{db_id}/query",
        headers=_headers(token),
        json={"filter": {"property": "日付", "date": {"equals": date}}, "page_size": 1},
        timeout=30,
    )
    return r.status_code == 200 and len(r.json().get("results", [])) > 0


def _num(row: dict, key: str):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def _row_to_properties(row: dict, date: str) -> dict:
    hits = [h for h in (row.get("hits") or "").split(";") if h]
    name = (row.get("name") or row.get("ticker") or "")[:200]
    return {
        "銘柄": {"title": [{"text": {"content": name}}]},
        "日付": {"date": {"start": date}},
        "コード": {"rich_text": [{"text": {"content": str(row.get("ticker", "")).replace(".T", "")}}]},
        "ティッカー": {"rich_text": [{"text": {"content": row.get("ticker", "")}}]},
        "業種": {"rich_text": [{"text": {"content": (row.get("sector") or "")[:200]}}]},
        "終値": {"number": _num(row, "close")},
        "要素数": {"number": _num(row, "n_factors")},
        "加重スコア": {"number": _num(row, "weighted")},
        "テクニカル": {"number": _num(row, "tech")},
        "マクロ": {"number": _num(row, "macro")},
        "ファンダ": {"number": _num(row, "funda")},
        "該当要素": {"multi_select": [{"name": h} for h in hits]},
    }


def upload(csv_path: str, db_id: str, token: str, date: str, top: int) -> int:
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[:top]
    ok = 0
    for row in rows:
        body = {"parent": {"database_id": db_id}, "properties": _row_to_properties(row, date)}
        for attempt in range(3):
            r = requests.post(f"{NOTION_API}/pages", headers=_headers(token), json=body, timeout=30)
            if r.status_code in (200, 201):
                ok += 1
                break
            if r.status_code == 429:  # レート制限
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ERROR {r.status_code}: {r.text[:300]}")
            break
        time.sleep(0.34)  # ~3 req/s
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(config.OUTPUT_DIR / "daily_scores.csv"))
    ap.add_argument("--top", type=int, default=30, help="投入する上位件数")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定=本日, TZ=Asia/Tokyo推奨）")
    ap.add_argument("--force", action="store_true", help="同日が投入済みでも再投入")
    args = ap.parse_args()

    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DB_ID", DEFAULT_DB_ID)
    if not token:
        print("NOTION_TOKEN 未設定のため Notion 連携をスキップしました。")
        return 0

    date = args.date or dt.datetime.now().strftime("%Y-%m-%d")
    if not args.force and _already_uploaded(db_id, token, date):
        print(f"{date} は既に投入済みのためスキップ（--force で再投入）。")
        return 0

    n = upload(args.csv, db_id, token, date, args.top)
    print(f"Notion へ {n} 件投入しました (date={date}, db={db_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
