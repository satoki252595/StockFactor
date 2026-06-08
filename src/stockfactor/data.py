"""データ取得基盤 (R1): yfinance による日本株 OHLCV 取得・キャッシュ・リトライ。"""
from __future__ import annotations

import time
from typing import Iterable

import pandas as pd

from . import config


def _import_yf():
    import yfinance as yf  # 遅延 import（合成データ単体テストはネット不要にするため）
    return yf


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """インデックスを tz-naive に統一（銘柄ごとに tz 有無が混在するのを防ぐ）。"""
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df


def fetch_ohlcv(
    ticker: str,
    period: str = "5y",
    interval: str = "1d",
    retries: int = 3,
) -> pd.DataFrame | None:
    """単一銘柄の OHLCV を取得。失敗時は指数バックオフでリトライ。"""
    yf = _import_yf()
    delay = 1.0
    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            if df is not None and not df.empty:
                df = df.rename(columns=str.title)
                return _normalize(df[["Open", "High", "Low", "Close", "Volume"]].dropna())
        except Exception:
            pass
        time.sleep(delay)
        delay *= 2
    return None


def fetch_many(
    tickers: Iterable[str],
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 50,
    pause: float = 1.0,
) -> dict[str, pd.DataFrame]:
    """複数銘柄を一括取得（yf.download をバッチ利用）。返り値: {ticker: OHLCV df}。"""
    yf = _import_yf()
    tickers = list(dict.fromkeys(tickers))  # 重複除去・順序維持
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            raw = yf.download(
                batch,
                period=period,
                interval=interval,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            raw = None
        for t in batch:
            df = None
            try:
                if raw is not None and isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0):
                    sub = raw[t].dropna()
                    if not sub.empty:
                        df = sub[["Open", "High", "Low", "Close", "Volume"]]
                elif raw is not None and len(batch) == 1 and not raw.empty:
                    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
            except Exception:
                df = None
            if df is None or df.empty:
                df = fetch_ohlcv(t, period=period, interval=interval)  # 個別フォールバック
            if df is not None and not df.empty:
                out[t] = _normalize(df)
        time.sleep(pause)
    return out


def get_info(ticker: str) -> dict:
    """ファンダ用スナップショット (R7)。current snapshot のみ（過去点別は無料では不可）。"""
    yf = _import_yf()
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}
