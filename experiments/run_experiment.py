"""run_experiment: 2倍株抽出 + 要素の識別力検証 (H2/H3/H4, R3/R5/R6/R8)。

GitHub Actions（フルネット）で実行。1回のダウンロードで以下を行う:
  1. ユニバースをサンプリングし 5年日足を取得
  2. 「126営業日内に2倍」の点火点 t0 を全検出（= doublers, H2）
  3. 正例(点火点t0の要素) vs 負例(明確に上がらなかった日の要素) で各要素の識別力を測定 (H3/H4)
  4. スコア(充足要素数)が正例で高いかを検証 (R9)
出力: experiments/results/{doublers.csv, factor_stats.csv, score_dist.csv, report.md}

使い方: python experiments/run_experiment.py --sample 400 --seed 42
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockfactor import config, data, factors, screen, universe  # noqa: E402
from stockfactor.score import RULES, score_features  # noqa: E402

RESULTS = config.RESULTS_DIR
NEG_MAX_FWD = 0.5  # 負例: 将来126日で+50%にも届かなかった日（明確な非上昇）


def load_sample_universe(n: int, seed: int) -> pd.DataFrame:
    try:
        uni = universe.load_universe()
    except FileNotFoundError:
        print("universe.csv が無いので JPX から取得...")
        uni = universe.refresh()
    print(f"universe size = {len(uni)}")
    if n and n < len(uni):
        uni = uni.sample(n=n, random_state=seed).reset_index(drop=True)
    return uni


def market_context():
    tp = data.fetch_ohlcv(config.TOPIX_ETF, period="6y")
    gr = data.fetch_ohlcv(config.GROWTH_ETF, period="6y")
    tp_s = tp["Close"] if tp is not None else None
    gr_s = gr["Close"] if gr is not None else None
    mc = factors.MarketContext(tp_s, gr_s)
    return mc, tp_s


def feature_vector(df, i, mc, market_series) -> dict:
    f = factors.compute_technical(df, i)
    if not f:
        return {}
    date = df.index[i]
    f["rs_6m"] = factors.relative_strength_6m(df["Close"], market_series, date)
    f.update(mc.compute_macro(date))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--neg-per-ticker", type=int, default=2)
    args = ap.parse_args()
    rng = np.random.RandomState(args.seed)

    uni = load_sample_universe(args.sample, args.seed)
    tickers = uni["ticker"].tolist()
    name_by_ticker = dict(zip(uni["ticker"], uni.get("name", uni["ticker"])))

    print("market context 取得中...")
    mc, market_series = market_context()
    print(f"  TOPIX series: {'OK' if market_series is not None else 'NONE'}")

    print(f"{len(tickers)} 銘柄を取得中...")
    panel = data.fetch_many(tickers, period="6y")
    print(f"  取得成功 {len(panel)} 銘柄")

    doubler_rows = []   # CSV: ticker,name,t0,fwd_max_ret
    pos_features = []   # 正例
    neg_features = []   # 負例

    for t, df in panel.items():
        if len(df) < config.MIN_HISTORY_TD + config.HORIZON_TD:
            continue
        close = df["Close"]
        fwd = screen.forward_max_return(close, config.HORIZON_TD)
        events = screen.find_doubling_events(close, config.HORIZON_TD, config.DOUBLE_THRESHOLD)
        event_pos = {df.index.get_loc(e) for e in events}

        for e in events:
            pos = df.index.get_loc(e)
            if pos < config.SMA_LONG:
                continue
            # 点火点での流動性を確認（実運用フィルタと整合）
            if not screen.passes_liquidity(df, as_of=e):
                continue
            fv = feature_vector(df, pos, mc, market_series)
            if not fv:
                continue
            fv["label"] = 1
            fv["ticker"] = t
            pos_features.append(fv)
            doubler_rows.append({
                "ticker": t, "name": name_by_ticker.get(t, ""),
                "t0": e.date().isoformat(),
                "fwd_max_ret": round(float(fwd.iloc[pos]), 3),
            })

        # 負例候補: 履歴十分 & 将来明確に上がらなかった & イベント近傍でない
        valid = np.arange(config.SMA_LONG, len(df) - config.HORIZON_TD - 1)
        cand = [p for p in valid
                if np.isfinite(fwd.iloc[p]) and fwd.iloc[p] < NEG_MAX_FWD
                and all(abs(p - ep) > config.HORIZON_TD for ep in event_pos)]
        if cand:
            for p in rng.choice(cand, size=min(args.neg_per_ticker, len(cand)), replace=False):
                if not screen.passes_liquidity(df, as_of=df.index[p]):
                    continue
                fv = feature_vector(df, int(p), mc, market_series)
                if not fv:
                    continue
                fv["label"] = 0
                fv["ticker"] = t
                neg_features.append(fv)

    n_pos, n_neg = len(pos_features), len(neg_features)
    print(f"\n正例(doubler点火点)={n_pos}, 負例={n_neg}, doublerイベント総数={len(doubler_rows)}")
    if n_pos == 0:
        print("正例ゼロ。サンプルを増やすか期間を見直す。")
        return

    pos_df = pd.DataFrame(pos_features)
    neg_df = pd.DataFrame(neg_features)
    all_df = pd.concat([pos_df, neg_df], ignore_index=True)

    # --- 連続特徴量の識別力 (AUC, 平均差) ---
    feat_cols = [c for c in all_df.columns if c not in ("label", "ticker")]
    stat_rows = []
    for c in feat_cols:
        p = pos_df[c].dropna().values if c in pos_df else np.array([])
        q = neg_df[c].dropna().values if c in neg_df else np.array([])
        if len(p) < 5 or len(q) < 5:
            continue
        auc = mann_whitney_auc(p, q)
        stat_rows.append({
            "feature": c, "pos_mean": np.mean(p), "neg_mean": np.mean(q),
            "pos_median": np.median(p), "neg_median": np.median(q),
            "auc": round(auc, 3), "abs_auc_lift": round(abs(auc - 0.5), 3),
            "n_pos": len(p), "n_neg": len(q),
        })
    feat_stats = pd.DataFrame(stat_rows).sort_values("abs_auc_lift", ascending=False)

    # --- ルール(しきい値)の命中率 lift ---
    rule_rows = []
    for r in RULES:
        if r.feature not in all_df.columns:
            continue
        def hit(s):
            v = s[r.feature]
            from stockfactor.score import _OPS
            return bool(np.isfinite(v)) and _OPS[r.op](v, r.threshold)
        ph = pos_df.apply(hit, axis=1).mean() if r.feature in pos_df else np.nan
        qh = neg_df.apply(hit, axis=1).mean() if r.feature in neg_df else np.nan
        rule_rows.append({
            "rule": r.key, "group": r.group, "feature": r.feature,
            "op": r.op, "threshold": r.threshold,
            "pos_hit_rate": round(float(ph), 3), "neg_hit_rate": round(float(qh), 3),
            "lift": round(float(ph - qh), 3),
        })
    rule_stats = pd.DataFrame(rule_rows).sort_values("lift", ascending=False)

    # --- スコア(充足要素数)の分布検証 ---
    def n_factors(row):
        return score_features(row.to_dict())["n_factors"]
    pos_df["n_factors"] = pos_df.apply(n_factors, axis=1)
    neg_df["n_factors"] = neg_df.apply(n_factors, axis=1)
    score_dist = pd.DataFrame({
        "stat": ["mean", "median", "p25", "p75"],
        "positive": [pos_df.n_factors.mean(), pos_df.n_factors.median(),
                     pos_df.n_factors.quantile(.25), pos_df.n_factors.quantile(.75)],
        "negative": [neg_df.n_factors.mean(), neg_df.n_factors.median(),
                     neg_df.n_factors.quantile(.25), neg_df.n_factors.quantile(.75)],
    })

    # --- 保存 ---
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(doubler_rows).to_csv(RESULTS / "doublers.csv", index=False)
    feat_stats.to_csv(RESULTS / "factor_stats.csv", index=False)
    rule_stats.to_csv(RESULTS / "rule_stats.csv", index=False)
    score_dist.to_csv(RESULTS / "score_dist.csv", index=False)
    write_report(args, uni, panel, doubler_rows, n_pos, n_neg,
                 feat_stats, rule_stats, score_dist, market_series)
    print("結果を experiments/results/ に保存しました。")


def mann_whitney_auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Mann-Whitney U 由来の AUC（pos>neg の確率）。"""
    all_v = np.concatenate([pos, neg])
    ranks = pd.Series(all_v).rank().values
    r_pos = ranks[: len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def write_report(args, uni, panel, doubler_rows, n_pos, n_neg,
                 feat_stats, rule_stats, score_dist, market_series):
    n_dbl_tickers = len({d["ticker"] for d in doubler_rows})
    lines = []
    lines.append("# 実験レポート: 2倍株の本質的要素 検証\n")
    lines.append(f"- サンプル銘柄数(取得成功): {len(panel)} / 要求 {args.sample} (seed={args.seed})")
    lines.append(f"- 市場インデックス取得: {'OK' if market_series is not None else 'NONE'}\n")
    lines.append("## H2: 2倍イベントは十分存在するか")
    lines.append(f"- doublerイベント総数: **{len(doubler_rows)}**")
    lines.append(f"- 2倍を経験した銘柄数: **{n_dbl_tickers}** / {len(panel)} "
                 f"({100*n_dbl_tickers/max(1,len(panel)):.1f}%)")
    lines.append(f"- 正例(点火点)={n_pos}, 負例={n_neg}\n")
    lines.append("## H3/H4/R8: 各要素の識別力 (AUC, 0.5=無情報)")
    lines.append("AUCが0.5から離れるほど識別力あり。pos>neg期待の要素はAUC>0.5が望ましい。\n")
    lines.append(feat_stats.to_markdown(index=False))
    lines.append("\n## ルール(しきい値)の命中率 lift (pos命中率 − neg命中率)")
    lines.append(rule_stats.to_markdown(index=False))
    lines.append("\n## R9: スコア(充足要素数)の分布 — 正例で高いか")
    lines.append(score_dist.to_markdown(index=False))
    lines.append("\n## 解釈メモ")
    lines.append("- abs_auc_lift 上位の要素を「本質的要素」として採用候補にする。")
    lines.append("- lift が正のルールは正例で当たりやすい＝有効。負/ゼロのルールは閾値見直し or 除外。")
    lines.append("- スコア分布で positive の方が高ければ、充足要素数によるランク付けが妥当。")
    (RESULTS / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
