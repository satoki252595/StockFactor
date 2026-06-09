"""日次レポートを Notion「レポートDB」へ投入 (R13) — GitHub Actions 用・ローカルPC不要・LLM不要・0円。

上位N銘柄について **yfinance の企業概要(longBusinessSummary)とファンダ実数** を取得し、
ランキング形式の1ページ（行=日付・本文=順位別解説）を Notion レポートDBに作成する。
銘柄コードは **バフェット・コード** へのリンク。LLM/外部APIキーは不要（NOTION_TOKEN のみ）。

環境変数:
  - NOTION_TOKEN        : 必須（Notion 内部インテグレーションのシークレット）
  - NOTION_REPORT_DB_ID : 任意（未設定/空なら既定のレポートDB）

使い方:
  PYTHONPATH=src python -m stockfactor.report_sync --top 10
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import time

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
# 既定の投入先 = ページ「小型成長株の推し銘柄」配下の「StockFactor 日次レポート」DB
DEFAULT_REPORT_DB_ID = "5fb924bcb3184a89b672e6bd1373c41b"
BUFFETT = "https://www.buffett-code.com/company/{code}"


# ---- Notion ブロック生成ヘルパー ----
def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"}


def _rt(text: str, link: str | None = None, bold: bool = False) -> dict:
    o = {"type": "text", "text": {"content": (text or "")[:1900]}}
    if link:
        o["text"]["link"] = {"url": link}
    if bold:
        o["annotations"] = {"bold": True}
    return o


def _para(rich) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}


def _h3(rich) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich}}


def _callout(text: str, emoji: str = "⚠️") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [_rt(text)], "icon": {"emoji": emoji}}}


def _fundamentals(info: dict) -> str:
    parts = []
    mc = info.get("marketCap")
    if isinstance(mc, (int, float)):
        parts.append(f"時価総額{mc/1e8:,.0f}億円")
    rg = info.get("revenueGrowth")
    if isinstance(rg, (int, float)):
        parts.append(f"増収率{rg*100:.0f}%")
    roe = info.get("returnOnEquity")
    if isinstance(roe, (int, float)):
        parts.append(f"ROE{roe*100:.0f}%")
    pm = info.get("profitMargins")
    if isinstance(pm, (int, float)):
        parts.append(f"純利益率{pm*100:.0f}%")
    psr = info.get("priceToSalesTrailing12Months")
    if isinstance(psr, (int, float)):
        parts.append(f"PSR{psr:.1f}")
    pe = info.get("trailingPE")
    if isinstance(pe, (int, float)):
        parts.append(f"PER{pe:.1f}")
    return " / ".join(parts)


def _get_info(ticker: str) -> dict:
    import yfinance as yf
    for _ in range(2):
        try:
            return yf.Ticker(ticker).info or {}
        except Exception:
            time.sleep(1)
    return {}


def build_blocks(rows: list[dict], date: str) -> list[dict]:
    blocks = [
        _callout("投資助言ではなくスクリーニング情報です。『高ボラ×押し目×出来高急増』の反転型プロファイルに"
                 "現在の全上場銘柄を機械採点し、要素の充足数で順位付けした上位です。"),
        _h3([_rt(f"{date}　半年2倍候補ランキング（上位{len(rows)}・要素充足順）", bold=True)]),
    ]
    for i, row in enumerate(rows, 1):
        code = str(row.get("ticker", "")).replace(".T", "")
        info = _get_info(row.get("ticker", ""))
        sector = row.get("sector") or info.get("sector") or ""
        nf = row.get("n_factors", "")
        close = row.get("close", "")
        head = [
            _rt(f"{i}位　{row.get('name', '')}（", bold=True),
            _rt(code, link=BUFFETT.format(code=code), bold=True),
            _rt(f"・{sector}）　{nf}要素 / 終値{close}円", bold=True),
        ]
        blocks.append(_h3(head))
        summary = (info.get("longBusinessSummary") or "").strip()
        blocks.append(_para([_rt("事業: " + (summary[:700] if summary else "（概要データなし／バフェット・コードのリンクから確認）"))]))
        funda = _fundamentals(info)
        if funda:
            blocks.append(_para([_rt("ファンダ: " + funda)]))
        hits = (row.get("hits") or "").replace(";", ", ")
        blocks.append(_para([_rt("該当要素: " + hits)]))
    blocks.append(_para([_rt("データ出所: 株価・企業概要・ファンダ=yfinance、分析リンク=バフェット・コード、"
                             "要素の検証根拠=FINDINGS.md。同じ姿でも下落する銘柄もあり“2倍になり得るセットアップ”の抽出です。")]))
    return blocks[:100]


def already_uploaded(db_id: str, token: str, date: str) -> bool:
    r = requests.post(f"{NOTION_API}/databases/{db_id}/query", headers=_headers(token),
                      json={"filter": {"property": "日付", "date": {"equals": date}}, "page_size": 1},
                      timeout=30)
    return r.status_code == 200 and len(r.json().get("results", [])) > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="output/daily_scores.csv")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--date", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_REPORT_DB_ID") or DEFAULT_REPORT_DB_ID
    if not token:
        print("NOTION_TOKEN 未設定のため レポート投入をスキップしました。")
        return 0

    date = args.date or dt.datetime.now().strftime("%Y-%m-%d")
    if not args.force and already_uploaded(db_id, token, date):
        print(f"{date} のレポートは既に存在するためスキップ（--force で再作成）。")
        return 0

    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[: args.top]
    if not rows:
        print("daily_scores.csv が空。スキップ。")
        return 0

    def _nf(r):
        try:
            return int(float(r.get("n_factors", 0)))
        except (TypeError, ValueError):
            return 0
    max_nf = max((_nf(r) for r in rows), default=0)
    sectors = [r.get("sector", "") for r in rows if r.get("sector")]
    memo = "高ボラ×押し目×出来高急増の反転型に合致した上位。" + (
        f"主な業種: {', '.join(sorted(set(sectors))[:5])}" if sectors else "")

    body = {
        "parent": {"database_id": db_id},
        "properties": {
            "タイトル": {"title": [{"text": {"content": f"{date} 半年2倍候補レポート（上位{len(rows)}）"}}]},
            "日付": {"date": {"start": date}},
            "最高要素数": {"number": max_nf},
            "候補数": {"number": len(rows)},
            "市場メモ": {"rich_text": [{"text": {"content": memo[:1900]}}]},
        },
        "children": build_blocks(rows, date),
    }
    r = requests.post(f"{NOTION_API}/pages", headers=_headers(token), json=body, timeout=60)
    if r.status_code in (200, 201):
        print(f"レポートを作成しました (date={date}, db={db_id})。")
        return 0
    print(f"レポート作成 失敗 {r.status_code}: {r.text[:500]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
