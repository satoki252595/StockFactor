"""合成データによるロジック単体テスト (R4)。ネット不要・コンテナ内で実行可能。

実行: python tests/test_screen.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockfactor import screen, factors  # noqa: E402


def _mk_df(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2019-01-01", periods=len(close), freq="B")
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(len(close), 1_000_000.0),
        },
        index=idx,
    )


def test_forward_max_return():
    close = pd.Series([100.0, 110.0, 90.0, 200.0])
    fwd = screen.forward_max_return(close, horizon=3)
    assert abs(fwd.iloc[0] - 1.0) < 1e-9, fwd.iloc[0]  # max future=200 → +100%
    assert np.isnan(fwd.iloc[-1])                       # 末尾は未来なし
    print("PASS test_forward_max_return")


def test_detects_doubling():
    # 250日横ばい→その後60日で2.2倍に上昇
    base = np.full(250, 100.0)
    ramp = np.linspace(100.0, 220.0, 60)
    tail = np.full(60, 220.0)
    df = _mk_df(np.concatenate([base, ramp, tail]))
    events = screen.find_doubling_events(df["Close"], horizon=126, threshold=2.0)
    assert len(events) >= 1, "2倍イベントを検出できていない"
    # 点火点は上昇開始(=index 249前後)以前であること
    ignite_pos = df.index.get_loc(events[0])
    assert ignite_pos <= 250, f"点火点が遅すぎる pos={ignite_pos}"
    print(f"PASS test_detects_doubling (events={len(events)}, first_pos={ignite_pos})")


def test_no_false_positive_on_flat():
    df = _mk_df(np.full(400, 100.0) + np.random.RandomState(0).normal(0, 0.5, 400))
    events = screen.find_doubling_events(df["Close"], horizon=126, threshold=2.0)
    assert len(events) == 0, f"横ばいで誤検出 events={len(events)}"
    print("PASS test_no_false_positive_on_flat")


def test_event_compression():
    # 長期にわたり何度も2倍条件を満たすが、min_gap で圧縮される
    close = np.concatenate([np.full(250, 100.0), np.linspace(100, 400, 200)])
    df = _mk_df(close)
    events = screen.find_doubling_events(df["Close"], horizon=126, threshold=2.0)
    assert len(events) <= 3, f"圧縮が効いていない events={len(events)}"
    print(f"PASS test_event_compression (events={len(events)})")


def test_technical_features():
    # 上昇トレンドで高値圏 → dist_52w_high≈1, sma_aligned=1, momentum>0
    close = np.linspace(100, 200, 300)
    df = _mk_df(close)
    f = factors.compute_technical(df)
    assert f, "特徴量が空"
    assert f["dist_52w_high"] > 0.98, f["dist_52w_high"]
    assert f["sma_aligned"] == 1.0
    assert f["ret_3m"] > 0
    assert f["above_sma200"] == 1.0
    print("PASS test_technical_features")


def test_liquidity_filter():
    df = _mk_df(np.full(300, 500.0))           # 株価500・出来高100万 → 代金5億 >3000万
    assert screen.passes_liquidity(df) is True
    low = _mk_df(np.full(300, 50.0))           # 株価50 < MIN_PRICE
    assert screen.passes_liquidity(low) is False
    short = _mk_df(np.full(100, 500.0))        # 履歴不足
    assert screen.passes_liquidity(short) is False
    print("PASS test_liquidity_filter")


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    main()
