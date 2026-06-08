"""要素（factor）抽出 (R5/R6/R7)。

すべての特徴量は「インデックス位置 i までの過去データのみ」で計算し、先読みを排除する。
- compute_technical: テクニカル要素（価格・出来高構造）
- MarketContext / compute_macro: マクロ要素（市場・小型株レジーム）
- compute_fundamental: ファンダ×ミクロ（current snapshot のみ。過去点別は無料で不可）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# ----------------------------------------------------------------------------
# テクニカル (R5)
# ----------------------------------------------------------------------------
def _ret(close: np.ndarray, i: int, lag: int) -> float:
    j = i - lag
    if j < 0 or close[j] <= 0:
        return np.nan
    return close[i] / close[j] - 1.0


def _atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int, win: int) -> float:
    if i < win:
        return np.nan
    tr = np.maximum(
        high[i - win + 1 : i + 1] - low[i - win + 1 : i + 1],
        np.maximum(
            np.abs(high[i - win + 1 : i + 1] - close[i - win : i]),
            np.abs(low[i - win + 1 : i + 1] - close[i - win : i]),
        ),
    )
    atr = tr.mean()
    return atr / close[i] if close[i] > 0 else np.nan


def compute_technical(df: pd.DataFrame, i: int | None = None) -> dict[str, float]:
    """df.iloc[:i+1] までを使ったテクニカル特徴量。i 省略時は末尾。"""
    if i is None:
        i = len(df) - 1
    if i < config.SMA_LONG:  # 200日分の履歴が無いと算出不可
        return {}
    close = df["Close"].values.astype(float)
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    vol = df["Volume"].values.astype(float)

    sma25 = close[i - config.SMA_SHORT + 1 : i + 1].mean()
    sma75 = close[i - config.SMA_MID + 1 : i + 1].mean()
    sma200 = close[i - config.SMA_LONG + 1 : i + 1].mean()

    high_252 = close[max(0, i - config.HIGH_WINDOW + 1) : i + 1].max()

    vol_s = vol[i - config.VOL_SURGE_SHORT + 1 : i + 1].mean()
    vol_l = vol[i - config.VOL_SURGE_LONG + 1 : i + 1].mean()

    rets = np.diff(np.log(np.clip(close[max(0, i - config.VCP_LONG) : i + 1], 1e-9, None)))
    vcp = (
        rets[-config.VCP_SHORT:].std() / rets.std()
        if len(rets) >= config.VCP_LONG and rets.std() > 0
        else np.nan
    )

    win60 = close[max(0, i - 59) : i + 1]
    range_60 = (win60.max() - win60.min()) / close[i] if close[i] > 0 else np.nan
    dd_252 = close[i] / high_252 - 1.0 if high_252 > 0 else np.nan  # 52週高値からの下落率(<=0)

    return {
        "ret_1m": _ret(close, i, 21),
        "ret_3m": _ret(close, i, 63),
        "ret_6m": _ret(close, i, 126),
        "ret_12m": _ret(close, i, 252),
        "dist_52w_high": close[i] / high_252 if high_252 > 0 else np.nan,  # 1.0=高値圏
        "drawdown_252": dd_252,                       # 高値からの下落（押し目度）
        "px_to_sma25": close[i] / sma25 if sma25 > 0 else np.nan,    # 25日線乖離(連続)
        "px_to_sma200": close[i] / sma200 if sma200 > 0 else np.nan,
        "above_sma25": float(close[i] > sma25),
        "above_sma200": float(close[i] > sma200),
        "sma_aligned": float(sma25 > sma75 > sma200),  # パーフェクトオーダー
        "vol_ratio": vol_s / vol_l if vol_l > 0 else np.nan,  # 出来高急増
        "atr_pct": _atr_pct(high, low, close, i, config.ATR_WINDOW),  # ボラティリティ
        "range_60": range_60,                          # 直近60日の値幅(ボラの広さ)
        "vcp": vcp,  # <1 = ボラ収縮 / >1 = 拡大
    }


# ----------------------------------------------------------------------------
# マクロ (R6)
# ----------------------------------------------------------------------------
class MarketContext:
    """市場・小型株インデックス系列を保持し、任意日の特徴量を返す。"""

    def __init__(self, topix: pd.Series | None, growth: pd.Series | None):
        self.topix = topix.astype(float) if topix is not None else None
        self.growth = growth.astype(float) if growth is not None else None

    @staticmethod
    def _asof(series: pd.Series | None, date: pd.Timestamp) -> int | None:
        if series is None or series.empty:
            return None
        pos = series.index.searchsorted(date, side="right") - 1
        return pos if pos >= config.SMA_LONG else None

    def _feat(self, series: pd.Series, pos: int) -> dict[str, float]:
        v = series.values
        sma200 = v[pos - config.SMA_LONG + 1 : pos + 1].mean()
        return {
            "above_200": float(v[pos] > sma200),
            "ret_6m": _ret(v, pos, 126),
        }

    def compute_macro(self, date: pd.Timestamp) -> dict[str, float]:
        out: dict[str, float] = {}
        tp = self._asof(self.topix, date)
        if tp is not None:
            tf = self._feat(self.topix, tp)
            out["mkt_above_200"] = tf["above_200"]      # 市場が上昇トレンド
            out["mkt_ret_6m"] = tf["ret_6m"]
        gp = self._asof(self.growth, date)
        if gp is not None:
            gf = self._feat(self.growth, gp)
            out["growth_ret_6m"] = gf["ret_6m"]          # 小型株レジーム
            if "mkt_ret_6m" in out and not np.isnan(out["mkt_ret_6m"]):
                out["growth_minus_mkt_6m"] = gf["ret_6m"] - out["mkt_ret_6m"]  # 小型株主導
        return out


def relative_strength_6m(
    stock_close: pd.Series, market: pd.Series | None, date: pd.Timestamp
) -> float:
    """銘柄6Mリターン − 市場6Mリターン（TOPIX相対強さ, R5/R6 橋渡し）。"""
    if market is None or market.empty:
        return np.nan
    spos = stock_close.index.searchsorted(date, side="right") - 1
    mpos = market.index.searchsorted(date, side="right") - 1
    if spos < 126 or mpos < 126:
        return np.nan
    s = stock_close.values.astype(float)
    m = market.values.astype(float)
    return _ret(s, spos, 126) - _ret(m, mpos, 126)


# ----------------------------------------------------------------------------
# ファンダ×ミクロ (R7) — current snapshot のみ
# ----------------------------------------------------------------------------
def compute_fundamental(info: dict) -> dict[str, float]:
    """yfinance .info から現時点ファンダ特徴量。過去点別データは無料では取得不可。"""
    def g(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None and isinstance(v, (int, float)) and np.isfinite(v):
                return float(v)
        return np.nan

    mcap = g("marketCap")
    return {
        "market_cap": mcap,
        "small_cap": float(mcap < 50_000_000_000) if np.isfinite(mcap) else np.nan,  # <500億円
        "revenue_growth": g("revenueGrowth"),       # YoY 増収率
        "earnings_growth": g("earningsGrowth", "earningsQuarterlyGrowth"),
        "roe": g("returnOnEquity"),
        "gross_margin": g("grossMargins"),
        "profit_margin": g("profitMargins"),
        "psr": g("priceToSalesTrailing12Months"),
        "trailing_pe": g("trailingPE"),
        "peg": g("pegRatio", "trailingPegRatio"),
    }
