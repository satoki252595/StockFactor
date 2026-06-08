"""exp01: データ取得の疎通検証 (H1) と市場インデックス・シンボルの確認。

GitHub Actions（フルネット）で実行する。yfinance で日本株5年日足とインデックスが
取れることを確認し、サンプルを表示する。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockfactor import data, config  # noqa: E402

SAMPLE_TICKERS = ["7203.T", "6758.T", "9984.T", "6920.T", "4385.T"]
INDEX_CANDIDATES = ["1306.T", "^N225", "^TPX", "1308.T", "2516.T", "1311.T"]


def main():
    print("=== exp01: data feasibility ===")
    print("\n[1] 個別株 5y 日足")
    for t in SAMPLE_TICKERS:
        df = data.fetch_ohlcv(t, period="5y")
        if df is None or df.empty:
            print(f"  {t}: FAIL (no data)")
        else:
            print(f"  {t}: rows={len(df)} {df.index[0].date()}..{df.index[-1].date()} "
                  f"last_close={df['Close'].iloc[-1]:.1f}")

    print("\n[2] バルク取得 (fetch_many)")
    bulk = data.fetch_many(SAMPLE_TICKERS, period="1y")
    print(f"  取得成功 {len(bulk)}/{len(SAMPLE_TICKERS)} 銘柄")

    print("\n[3] インデックス候補シンボルの疎通")
    for t in INDEX_CANDIDATES:
        df = data.fetch_ohlcv(t, period="1y")
        ok = df is not None and not df.empty
        print(f"  {t}: {'OK rows='+str(len(df)) if ok else 'FAIL'}")

    print("\n[4] ファンダ snapshot (.info)")
    info = data.get_info("4385.T")
    keys = ["marketCap", "revenueGrowth", "earningsGrowth", "returnOnEquity",
            "priceToSalesTrailing12Months", "sector"]
    for k in keys:
        print(f"  {k}: {info.get(k)}")

    print("\nexp01 done.")


if __name__ == "__main__":
    main()
