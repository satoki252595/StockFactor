"""2倍株スクリーナ (R3): 126営業日以内に+100%となる点火点 t0 を検出。

ラベル付けにのみ未来を参照（forward max return）。要素計算は t0 までの過去のみ使う設計。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def forward_max_return(close: pd.Series, horizon: int = config.HORIZON_TD) -> pd.Series:
    """各日 t について、将来 horizon 日以内の最大終値 / 当日終値 - 1（先読みラベル）。

    末尾 horizon 日は将来データ不足のため NaN。
    """
    close = close.astype(float)
    n = len(close)
    fwd = np.full(n, np.nan)
    vals = close.values
    for t in range(n):
        end = min(n, t + horizon + 1)
        window = vals[t + 1 : end]
        if window.size:
            fwd[t] = window.max() / vals[t] - 1.0
    return pd.Series(fwd, index=close.index, name="fwd_max_ret")


def find_doubling_events(
    close: pd.Series,
    horizon: int = config.HORIZON_TD,
    threshold: float = config.DOUBLE_THRESHOLD,
    min_gap_td: int = None,
) -> list[pd.Timestamp]:
    """+100%以上となる点火点 t0 のリストを返す。

    連続して条件を満たす日が並ぶため、`min_gap_td`（既定 = horizon）以上離れた
    *最初* の点だけを採用して重複イベントを1つに圧縮する。
    """
    if min_gap_td is None:
        min_gap_td = horizon
    fwd = forward_max_return(close, horizon)
    qualifying = fwd >= (threshold - 1.0)
    events: list[pd.Timestamp] = []
    last_idx = -10**9
    idx_positions = np.where(qualifying.values)[0]
    for pos in idx_positions:
        if pos - last_idx >= min_gap_td:
            events.append(close.index[pos])
            last_idx = pos
    return events


def passes_liquidity(df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> bool:
    """流動性フィルタ (R2)。as_of 時点までの直近60日で判定（既定は系列末尾）。"""
    if df is None or df.empty:
        return False
    sub = df if as_of is None else df.loc[:as_of]
    if len(sub) < config.MIN_HISTORY_TD:
        return False
    last_close = float(sub["Close"].iloc[-1])
    if last_close < config.MIN_PRICE:
        return False
    turnover = (sub["Close"] * sub["Volume"]).tail(60).mean()
    if not np.isfinite(turnover) or turnover < config.MIN_AVG_TURNOVER_JPY:
        return False
    return True
