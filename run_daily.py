"""日次バッチ本体 (R10): ユニバース→取得→流動性→要素採点→出力。

決定論パート（0円・Pythonのみ）。Claude スキルがこの出力(output/daily_scores.csv,
output/daily_report.md)を読み、要素定義に沿って定性統合し最終ウォッチリストを作る。

効率化: 全銘柄をテクニカル+マクロで採点し、上位のみ .info(ファンダ)を取得して総合採点。

使い方:
  python run_daily.py --limit 0 --topn 80     # 0=全銘柄
  python run_daily.py --limit 300             # 動作確認用に300銘柄だけ
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from stockfactor import config, data, factors, screen, universe  # noqa: E402
from stockfactor.score import RULES, score_features  # noqa: E402


def build_market_context():
    tp = data.fetch_ohlcv(config.TOPIX_ETF, period="2y")
    gr = data.fetch_ohlcv(config.GROWTH_ETF, period="2y")
    mc = factors.MarketContext(tp["Close"] if tp is not None else None,
                               gr["Close"] if gr is not None else None)
    return mc, (tp["Close"] if tp is not None else None)


def technical_macro_features(df, mc, market_series) -> dict:
    f = factors.compute_technical(df)
    if not f:
        return {}
    date = df.index[-1]
    f["rs_6m"] = factors.relative_strength_6m(df["Close"], market_series, date)
    f.update(mc.compute_macro(date))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭からの銘柄数 (0=全銘柄)")
    ap.add_argument("--topn", type=int, default=80, help="ファンダ取得する上位件数")
    ap.add_argument("--period", default="2y")
    args = ap.parse_args()

    uni = universe.load_universe()
    if args.limit:
        uni = uni.head(args.limit)
    tickers = uni["ticker"].tolist()
    meta = uni.set_index("ticker")
    print(f"universe={len(tickers)} 取得中...")

    mc, market_series = build_market_context()
    panel = data.fetch_many(tickers, period=args.period)
    print(f"取得成功 {len(panel)} 銘柄")

    # --- 1段目: テクニカル+マクロで全銘柄採点（2トラック）---
    rows = []
    for t, df in panel.items():
        if not screen.passes_liquidity(df):
            continue
        feats = technical_macro_features(df, mc, market_series)
        if not feats:
            continue
        sc = score_features(feats)
        rows.append({
            "ticker": t,
            "name": meta.loc[t, "name"] if t in meta.index else "",
            "sector": meta.loc[t, "sector"] if t in meta.index and "sector" in meta.columns else "",
            "close": round(float(df["Close"].iloc[-1]), 1),
            "n_factors_tm": sc["n_factors"],
            "setup_type": sc["setup_type"],
            "tech": sc["by_group"].get("technical", 0),
            "macro": sc["by_group"].get("macro", 0),
            "hits_tm": ";".join(sc["hits"]),
            "_feats": feats,
        })
    if not rows:
        print("候補なし"); return
    df_all = pd.DataFrame(rows).sort_values("n_factors_tm", ascending=False).reset_index(drop=True)

    # --- 2段目: 上位のみファンダ取得して総合採点 ---
    top = df_all.head(args.topn).copy()
    print(f"上位 {len(top)} 銘柄のファンダを取得...")
    full = []
    for _, r in top.iterrows():
        feats = dict(r["_feats"])
        feats.update(factors.compute_fundamental(data.get_info(r["ticker"])))
        sc = score_features(feats)
        full.append({
            "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
            "close": r["close"],
            "setup_type": sc["setup_type"],
            "n_factors": sc["n_factors"], "weighted": sc["weighted_score"],
            "rev_score": sc["reversal_score"], "mom_score": sc["momentum_score"],
            "tech": sc["by_group"].get("technical", 0),
            "macro": sc["by_group"].get("macro", 0),
            "funda": sc["by_group"].get("fundamental", 0),
            "hits": ";".join(sc["hits"]),
        })
    out = pd.DataFrame(full).sort_values(["n_factors", "weighted"], ascending=False).reset_index(drop=True)

    today = dt.date.today().isoformat()
    csv_path = config.OUTPUT_DIR / "daily_scores.csv"
    out.to_csv(csv_path, index=False)

    n_mom = int((out["setup_type"] == "momentum").sum())
    n_rev = int((out["setup_type"] == "reversal").sum())
    mom_top = out[out["setup_type"] == "momentum"].head(15)
    rev_top = out[out["setup_type"] == "reversal"].head(15)

    md = [f"# 日次スクリーニング {today}\n",
          f"- ユニバース取得: {len(panel)} 銘柄 / 流動性通過 {len(df_all)} / ファンダ採点 {len(out)}",
          f"- 採点要素満点: 共通3 + 反転型4 + モメンタム型5 + マクロ1 + ファンダ5",
          f"- 型別内訳（ファンダ採点分）: モメンタム型 {n_mom} / 反転型 {n_rev}\n",
          "## モメンタム型（高値ブレイク・順張り）上位\n",
          mom_top.to_markdown(index=False),
          "\n## 反転型（押し目・逆張り）上位\n",
          rev_top.to_markdown(index=False),
          "\n## 次のステップ（Claude スキルが実施）",
          "- 上位銘柄について SKILL.md の要素定義に沿って型別に定性的に妥当性を確認し、",
          "  最終ウォッチリストと根拠を `output/watchlist_<date>.md` にまとめる。"]
    (config.OUTPUT_DIR / "daily_report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"出力: {csv_path}（モメンタム型 {n_mom} / 反転型 {n_rev}）")
    print(out.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
