"""要素（factor）抽出 (R5/R6/R7/R10)。

すべての特徴量は「インデックス位置 i までの過去データのみ」で計算し、先読みを排除する。
- compute_technical: テクニカル要素（価格・出来高構造）。反転型・モメンタム型の両仮説を網羅。
- MarketContext / compute_macro: マクロ要素（市場・小型株レジーム）
- compute_fundamental: ファンダ×ミクロ（yfinance snapshot / J-Quants point-in-time）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# ----------------------------------------------------------------------------
# テクニカル (R5 / R10拡張)
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
    """df.iloc[:i+1] までを使ったテクニカル特徴量。i 省略時は末尾。

    反転型（off_highs, below_sma25, recent_dip, high_volatility）と
    モメンタム型（near_52w_high, at_new_high, sma_aligned, positive_rs）の
    両仮説を網羅する特徴量セット。
    """
    if i is None:
        i = len(df) - 1
    if i < config.SMA_LONG:  # 200日分の履歴が無いと算出不可
        return {}
    close = df["Close"].values.astype(float)
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    vol = df["Volume"].values.astype(float)

    sma25 = close[i - config.SMA_SHORT + 1 : i + 1].mean()
    sma50 = close[i - 50 + 1 : i + 1].mean() if i >= 50 else np.nan
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
    dd_252 = close[i] / high_252 - 1.0 if high_252 > 0 else np.nan

    # 20日実現ボラ（年率換算）
    rets20 = np.diff(np.log(np.clip(close[max(0, i - 20) : i + 1], 1e-9, None)))
    rvol_20 = float(rets20.std() * np.sqrt(252)) if len(rets20) >= 5 else np.nan

    # 売買代金トレンド（21日平均 / 63日平均）
    to_21 = (close[max(0, i - 20) : i + 1] * vol[max(0, i - 20) : i + 1]).mean()
    to_63 = (close[max(0, i - 62) : i + 1] * vol[max(0, i - 62) : i + 1]).mean()
    turnover_trend = to_21 / to_63 if to_63 > 0 else np.nan

    # 直近5日の上昇日数（モメンタム継続性）
    recent5 = close[max(0, i - 4) : i + 1]
    consec_up_5d = float(sum(1 for j in range(1, len(recent5)) if recent5[j] > recent5[j - 1]))

    # 50日線傾き（直近25日 vs 前25日の50日SMA比較）
    sma50_prev = close[i - 50 - 24 : i - 24].mean() if i >= 75 else np.nan
    sma50_slope = (sma50 / sma50_prev - 1.0) if (np.isfinite(sma50_prev) and sma50_prev > 0) else np.nan

    dist_52w = close[i] / high_252 if high_252 > 0 else np.nan

    return {
        # --- 既存（反転型）---
        "ret_1m": _ret(close, i, 21),
        "ret_3m": _ret(close, i, 63),
        "ret_6m": _ret(close, i, 126),
        "ret_12m": _ret(close, i, 252),
        "dist_52w_high": dist_52w,
        "drawdown_252": dd_252,
        "px_to_sma25": close[i] / sma25 if sma25 > 0 else np.nan,
        "px_to_sma200": close[i] / sma200 if sma200 > 0 else np.nan,
        "above_sma25": float(close[i] > sma25),
        "above_sma200": float(close[i] > sma200),
        "sma_aligned": float(sma25 > sma75 > sma200),
        "vol_ratio": vol_s / vol_l if vol_l > 0 else np.nan,
        "atr_pct": _atr_pct(high, low, close, i, config.ATR_WINDOW),
        "range_60": range_60,
        "vcp": vcp,
        # --- 新規（モメンタム/ブレイク型仮説テスト用）---
        "px_to_sma50": close[i] / sma50 if (np.isfinite(sma50) and sma50 > 0) else np.nan,
        "above_sma50": float(close[i] > sma50) if np.isfinite(sma50) else np.nan,
        "sma50_slope": sma50_slope,          # 50日線が上向きか（>0=上昇トレンド）
        "near_52w_high": float(dist_52w >= 0.90) if np.isfinite(dist_52w) else np.nan,  # 高値圏
        "at_new_high": float(dist_52w >= 1.00) if np.isfinite(dist_52w) else np.nan,    # 真のブレイク
        "rvol_20": rvol_20,                  # 20日実現ボラ（高い=動ける）
        "turnover_trend": turnover_trend,    # 売買代金の増加傾向（>1=増加中）
        "consec_up_5d": consec_up_5d,        # 直近5日の上昇日数（モメンタム継続）
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


def relative_strength_n(
    stock_close: pd.Series, market: pd.Series | None, date: pd.Timestamp, lag: int = 126
) -> float:
    """銘柄Nヶ月リターン − 市場Nヶ月リターン（TOPIX相対強さ）。lag=営業日数。"""
    if market is None or market.empty:
        return np.nan
    spos = stock_close.index.searchsorted(date, side="right") - 1
    mpos = market.index.searchsorted(date, side="right") - 1
    if spos < lag or mpos < lag:
        return np.nan
    s = stock_close.values.astype(float)
    m = market.values.astype(float)
    return _ret(s, spos, lag) - _ret(m, mpos, lag)


def relative_strength_6m(
    stock_close: pd.Series, market: pd.Series | None, date: pd.Timestamp
) -> float:
    """後方互換エイリアス。"""
    return relative_strength_n(stock_close, market, date, lag=126)


# ----------------------------------------------------------------------------
# ファンダ×ミクロ (R7 / R10拡張)
# ----------------------------------------------------------------------------
def compute_fundamental(info: dict) -> dict[str, float]:
    """yfinance .info から現時点ファンダ特徴量（日次スクリーニング用）。"""
    def g(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None and isinstance(v, (int, float)) and np.isfinite(v):
                return float(v)
        return np.nan

    mcap = g("marketCap")
    return {
        "market_cap": mcap,
        "small_cap": float(mcap < 50_000_000_000) if np.isfinite(mcap) else np.nan,
        "revenue_growth": g("revenueGrowth"),
        "earnings_growth": g("earningsGrowth", "earningsQuarterlyGrowth"),
        "roe": g("returnOnEquity"),
        "gross_margin": g("grossMargins"),
        "profit_margin": g("profitMargins"),
        "psr": g("priceToSalesTrailing12Months"),
        "trailing_pe": g("trailingPE"),
        "peg": g("pegRatio", "trailingPegRatio"),
    }


_DISCLOSURE_LAG_DAYS = 50  # 期末から開示まで典型的な遅延（日本: 45〜60日）


def build_quarterly_pit(ticker_obj) -> pd.DataFrame | None:
    """yfinance Ticker から四半期 point-in-time ファンダ表を構築する。

    yfinance の quarterly_income_stmt 列は期末日。日本企業の典型的開示遅延
    （期末 + 約50日）を加算して DisclosedDate を近似する。
    返り値: DataFrame[DisclosedDate, net_sales, op_profit, net_profit, roe_approx]
    """
    try:
        inc = ticker_obj.quarterly_income_stmt
        bs = ticker_obj.quarterly_balance_sheet
    except Exception:
        return None
    if inc is None or inc.empty:
        return None

    # カラム = 期末日（Timestamp）
    period_ends = inc.columns.tolist()
    rows = []
    for pe in period_ends:
        if not isinstance(pe, pd.Timestamp):
            try:
                pe = pd.Timestamp(pe)
            except Exception:
                continue
        disclosed = pe + pd.Timedelta(days=_DISCLOSURE_LAG_DAYS)

        def _get_row(df, *labels):
            for lbl in labels:
                if df is not None and lbl in df.index and pe in df.columns:
                    v = df.loc[lbl, pe]
                    if pd.notna(v):
                        return float(v)
            return np.nan

        net_sales = _get_row(inc, "Total Revenue", "Revenue", "Net Revenue")
        op_profit = _get_row(inc, "Operating Income", "Operating Profit", "EBIT")
        net_profit = _get_row(inc, "Net Income", "Net Income Common Stockholders")
        total_eq = _get_row(bs, "Stockholders Equity", "Total Equity Gross Minority Interest",
                             "Common Stock Equity") if bs is not None and not bs.empty else np.nan

        rows.append({
            "period_end": pe,
            "disclosed_date": disclosed,
            "net_sales": net_sales,
            "op_profit": op_profit,
            "net_profit": net_profit,
            "total_equity": total_eq,
        })

    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("disclosed_date").reset_index(drop=True)
    return df


def compute_fundamental_pit(
    pit_df: pd.DataFrame | None,
    as_of: pd.Timestamp,
    mcap_at_date: float = np.nan,
) -> dict[str, float]:
    """point-in-time ファンダ特徴量を計算する。

    pit_df: build_quarterly_pit() の出力。None の場合は空を返す。
    as_of: 評価時点（t0）。これ以前に開示された四半期データのみ使用。
    mcap_at_date: t0 時点の時価総額（円）。price * shares_outstanding の近似値でも可。
    """
    if pit_df is None or pit_df.empty:
        return {}
    avail = pit_df[pit_df["disclosed_date"] <= as_of]
    if avail.empty:
        return {}

    latest = avail.iloc[-1]
    # 前年同期（約4四半期前 = 330日以上前）
    prev_rows = avail[avail["period_end"] <= (latest["period_end"] - pd.Timedelta(days=330))]
    prev = prev_rows.iloc[-1] if not prev_rows.empty else None

    def _s(row, col):
        if row is None:
            return np.nan
        v = row[col] if isinstance(row, pd.Series) else row.get(col, np.nan)
        return float(v) if (v is not None and pd.notna(v)) else np.nan

    ns = _s(latest, "net_sales")
    pns = _s(prev, "net_sales")
    op = _s(latest, "op_profit")
    pop = _s(prev, "op_profit")
    pft = _s(latest, "net_profit")
    ppft = _s(prev, "net_profit")
    eq = _s(latest, "total_equity")

    rev_g = (ns / pns - 1.0) if (np.isfinite(ns) and np.isfinite(pns) and pns > 0) else np.nan
    op_g = (op / pop - 1.0) if (np.isfinite(op) and np.isfinite(pop) and pop > 0) else np.nan
    pft_g = (pft / ppft - 1.0) if (np.isfinite(pft) and np.isfinite(ppft) and ppft > 0) else np.nan
    roe = (pft / eq) if (np.isfinite(pft) and np.isfinite(eq) and eq > 0) else np.nan
    op_margin = (op / ns) if (np.isfinite(op) and np.isfinite(ns) and ns > 0) else np.nan
    # 時価総額 / 売上高（PSR近似）
    psr_pit = (mcap_at_date / (ns * 4)) if (np.isfinite(mcap_at_date) and np.isfinite(ns) and ns > 0) else np.nan

    return {
        "pit_rev_growth": rev_g,
        "pit_op_growth": op_g,
        "pit_profit_growth": pft_g,
        "pit_roe": roe,
        "pit_op_margin": op_margin,
        "pit_psr": psr_pit,
    }
