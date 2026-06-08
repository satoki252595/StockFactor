"""ユニバース管理 (R2): JPX 公開の上場銘柄一覧を取得し CSV 化／読込。

JPX 「東証上場銘柄一覧 (data_j.xls)」は無料公開。Full/Custom ネットワークで取得可能。
取得結果は data/universe.csv にコミットしておけば、以後はネット不要で再利用できる。
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from . import config

JPX_XLS_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)
UNIVERSE_CSV = config.DATA_DIR / "universe.csv"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; StockFactor/0.1)"}

# 普通株を含む市場区分のみ（ETF/REIT/出資証券などを除外）
_KEEP_MARKETS = {
    "プライム（内国株式）",
    "スタンダード（内国株式）",
    "グロース（内国株式）",
}


def fetch_universe_from_jpx() -> pd.DataFrame:
    """JPX から最新の上場銘柄一覧を取得して整形 DataFrame を返す。"""
    resp = requests.get(JPX_XLS_URL, headers=_UA, timeout=60)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content))
    # 列名は日本語。必要列を抽出して英語化。
    rename = {
        "コード": "code",
        "銘柄名": "name",
        "市場・商品区分": "market",
        "33業種区分": "sector",
        "規模区分": "size",
    }
    df = df.rename(columns=rename)
    df = df[df["market"].isin(_KEEP_MARKETS)].copy()
    df["code"] = df["code"].astype(str).str.zfill(4)
    df["ticker"] = df["code"] + ".T"  # yfinance 形式
    cols = ["code", "ticker", "name", "market", "sector", "size"]
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)


def save_universe(df: pd.DataFrame) -> None:
    df.to_csv(UNIVERSE_CSV, index=False)


def load_universe() -> pd.DataFrame:
    """コミット済み CSV から読込（ネット不要）。無ければ例外。"""
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(
            f"{UNIVERSE_CSV} が無い。先に fetch_universe_from_jpx()→save_universe() を実行。"
        )
    df = pd.read_csv(UNIVERSE_CSV, dtype={"code": str})
    df["code"] = df["code"].str.zfill(4)
    return df


def refresh() -> pd.DataFrame:
    """取得→保存をまとめて実行。"""
    df = fetch_universe_from_jpx()
    save_universe(df)
    return df
