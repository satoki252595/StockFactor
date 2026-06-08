"""run_experiment v2 (R10): 全銘柄対応・並列処理・モメンタム vs 反転型クラスター分析。

変更点 (v2):
  - --sample 0 で全銘柄を対象にする（既定: 0=全銘柄）
  - --workers N で並列特徴量計算（既定: CPU数/2）
  - 新特徴量（モメンタム/ブレイク型）を追加して両仮説を同時に検証
  - K-Means クラスター分析で「反転型」と「モメンタム型」が分かれるか検証
  - yfinance quarterly 財務データで point-in-time ファンダ検証
  - 負例を増やしてサンプル不均衡を改善

使い方:
  python experiments/run_experiment.py                  # 全銘柄・CPU並列
  python experiments/run_experiment.py --sample 500     # 旧来互換
  python experiments/run_experiment.py --sample 0 --workers 8 --neg-per-ticker 3
"""
from __future__ import annotations

import argparse
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockfactor import config, data, factors, screen, universe  # noqa: E402
from stockfactor.score import RULES, score_features  # noqa: E402

RESULTS = config.RESULTS_DIR
NEG_MAX_FWD = 0.5  # 負例: 将来126日で+50%にも届かなかった日


# ---------------------------------------------------------------------------
# データ準備
# ---------------------------------------------------------------------------

def load_universe(sample: int, seed: int) -> pd.DataFrame:
    try:
        uni = universe.load_universe()
    except FileNotFoundError:
        print("universe.csv が無いので JPX から取得...")
        uni = universe.refresh()
    print(f"universe全銘柄数 = {len(uni)}")
    if sample and sample < len(uni):
        uni = uni.sample(n=sample, random_state=seed).reset_index(drop=True)
        print(f"サンプリング後 = {len(uni)}")
    return uni


def market_context():
    tp = data.fetch_ohlcv(config.TOPIX_ETF, period="6y")
    gr = data.fetch_ohlcv(config.GROWTH_ETF, period="6y")
    tp_s = tp["Close"] if tp is not None else None
    gr_s = gr["Close"] if gr is not None else None
    mc = factors.MarketContext(tp_s, gr_s)
    return mc, tp_s


# ---------------------------------------------------------------------------
# point-in-time ファンダ（yfinance quarterly）
# ---------------------------------------------------------------------------

def _build_pit_cache(tickers: list[str]) -> dict[str, pd.DataFrame | None]:
    """全ティッカーの quarterly PIT テーブルを事前ビルド（並列）。"""
    import yfinance as yf

    def _fetch(t):
        try:
            ticker_obj = yf.Ticker(t)
            return t, factors.build_quarterly_pit(ticker_obj)
        except Exception:
            return t, None

    cache: dict[str, pd.DataFrame | None] = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(_fetch, t): t for t in tickers}
        done = 0
        for f in as_completed(futs):
            t, pit = f.result()
            cache[t] = pit
            done += 1
            if done % 100 == 0:
                print(f"  PIT ファンダ取得: {done}/{len(tickers)}")
    return cache


# ---------------------------------------------------------------------------
# 特徴量計算（1銘柄）
# ---------------------------------------------------------------------------

def _process_ticker(
    t: str,
    df: pd.DataFrame,
    mc: factors.MarketContext,
    market_series: pd.Series | None,
    pit_df: pd.DataFrame | None,
    neg_per_ticker: int,
    rng: np.random.RandomState,
) -> tuple[list[dict], list[dict], list[dict]]:
    """正例・負例・doubler_rows を返す。"""
    if len(df) < config.MIN_HISTORY_TD + config.HORIZON_TD:
        return [], [], []

    close = df["Close"]
    fwd = screen.forward_max_return(close, config.HORIZON_TD)
    events = screen.find_doubling_events(close, config.HORIZON_TD, config.DOUBLE_THRESHOLD)
    event_pos = {df.index.get_loc(e) for e in events}

    doubler_rows: list[dict] = []
    pos_features: list[dict] = []
    neg_features: list[dict] = []

    for e in events:
        pos = df.index.get_loc(e)
        if pos < config.SMA_LONG:
            continue
        if not screen.passes_liquidity(df, as_of=e):
            continue
        fv = factors.compute_technical(df, pos)
        if not fv:
            continue
        fv["rs_6m"] = factors.relative_strength_n(close, market_series, e, 126)
        fv["rs_3m"] = factors.relative_strength_n(close, market_series, e, 63)
        fv["rs_12m"] = factors.relative_strength_n(close, market_series, e, 252)
        fv.update(mc.compute_macro(e))
        # PIT ファンダ
        if pit_df is not None:
            mcap_approx = float(close.iloc[pos]) * _shares_approx(df, pos)
            pit_feat = factors.compute_fundamental_pit(pit_df, e, mcap_approx)
            fv.update(pit_feat)
        fv["label"] = 1
        fv["ticker"] = t
        pos_features.append(fv)
        doubler_rows.append({
            "ticker": t,
            "t0": e.date().isoformat(),
            "fwd_max_ret": round(float(fwd.iloc[pos]), 3),
        })

    valid = np.arange(config.SMA_LONG, len(df) - config.HORIZON_TD - 1)
    cand = [
        p for p in valid
        if np.isfinite(fwd.iloc[p]) and fwd.iloc[p] < NEG_MAX_FWD
        and all(abs(p - ep) > config.HORIZON_TD for ep in event_pos)
    ]
    if cand:
        chosen = rng.choice(cand, size=min(neg_per_ticker, len(cand)), replace=False)
        for p in chosen:
            if not screen.passes_liquidity(df, as_of=df.index[p]):
                continue
            fv = factors.compute_technical(df, int(p))
            if not fv:
                continue
            date = df.index[p]
            fv["rs_6m"] = factors.relative_strength_n(close, market_series, date, 126)
            fv["rs_3m"] = factors.relative_strength_n(close, market_series, date, 63)
            fv["rs_12m"] = factors.relative_strength_n(close, market_series, date, 252)
            fv.update(mc.compute_macro(date))
            if pit_df is not None:
                mcap_approx = float(close.iloc[p]) * _shares_approx(df, p)
                pit_feat = factors.compute_fundamental_pit(pit_df, date, mcap_approx)
                fv.update(pit_feat)
            fv["label"] = 0
            fv["ticker"] = t
            neg_features.append(fv)

    return pos_features, neg_features, doubler_rows


def _shares_approx(df: pd.DataFrame, pos: int) -> float:
    """株式数の近似（時価総額計算用）。出来高の中央値を使った粗い近似。"""
    vol = df["Volume"].values
    return float(np.nanmedian(vol[max(0, pos - 60) : pos + 1])) * 100  # 単元100株


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def mann_whitney_auc(pos: np.ndarray, neg: np.ndarray) -> float:
    all_v = np.concatenate([pos, neg])
    ranks = pd.Series(all_v).rank().values
    r_pos = ranks[: len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def compute_stats(pos_df: pd.DataFrame, neg_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_df = pd.concat([pos_df, neg_df], ignore_index=True)
    feat_cols = [c for c in all_df.columns if c not in ("label", "ticker")]

    stat_rows = []
    for c in feat_cols:
        p = pos_df[c].dropna().values if c in pos_df else np.array([])
        q = neg_df[c].dropna().values if c in neg_df else np.array([])
        if len(p) < 5 or len(q) < 5:
            continue
        auc = mann_whitney_auc(p, q)
        stat_rows.append({
            "feature": c,
            "pos_mean": round(np.mean(p), 4),
            "neg_mean": round(np.mean(q), 4),
            "pos_median": round(np.median(p), 4),
            "neg_median": round(np.median(q), 4),
            "auc": round(auc, 3),
            "abs_auc_lift": round(abs(auc - 0.5), 3),
            "n_pos": len(p),
            "n_neg": len(q),
        })
    feat_stats = pd.DataFrame(stat_rows).sort_values("abs_auc_lift", ascending=False)

    rule_rows = []
    from stockfactor.score import _OPS
    for r in RULES:
        if r.feature not in pos_df.columns:
            continue
        def _hit(row, _r=r):
            v = row.get(_r.feature, np.nan)
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                return False
            return _OPS[_r.op](v, _r.threshold)
        ph = pos_df.apply(_hit, axis=1).mean()
        qh = neg_df.apply(_hit, axis=1).mean()
        rule_rows.append({
            "rule": r.key, "group": r.group, "feature": r.feature,
            "op": r.op, "threshold": r.threshold,
            "pos_hit_rate": round(float(ph), 3),
            "neg_hit_rate": round(float(qh), 3),
            "lift": round(float(ph - qh), 3),
        })
    rule_stats = pd.DataFrame(rule_rows).sort_values("lift", ascending=False)
    return feat_stats, rule_stats


def cluster_analysis(pos_df: pd.DataFrame) -> pd.DataFrame:
    """K-Means (k=2) で正例を「反転型」と「モメンタム型」に分類して特徴を比較する。"""
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return pd.DataFrame()

    # 反転型 vs モメンタム型を分けるキー特徴量
    key_feats = [
        "dist_52w_high", "atr_pct", "vol_ratio", "ret_1m",
        "above_sma200", "sma_aligned", "rs_6m", "consec_up_5d",
        "vcp", "near_52w_high",
    ]
    avail = [f for f in key_feats if f in pos_df.columns]
    if len(avail) < 4:
        return pd.DataFrame()

    sub = pos_df[avail].dropna()
    if len(sub) < 10:
        return pd.DataFrame()

    scaler = StandardScaler()
    X = scaler.fit_transform(sub)
    km = KMeans(n_clusters=2, random_state=42, n_init=10)
    labels = km.fit_predict(X)
    sub = sub.copy()
    sub["cluster"] = labels

    # どちらのクラスタが「モメンタム型」かを dist_52w_high の平均で判定
    c0_high = sub.loc[sub["cluster"] == 0, "dist_52w_high"].mean()
    c1_high = sub.loc[sub["cluster"] == 1, "dist_52w_high"].mean()
    momentum_cluster = 0 if c0_high > c1_high else 1
    reversal_cluster = 1 - momentum_cluster
    sub["type"] = sub["cluster"].map({momentum_cluster: "momentum", reversal_cluster: "reversal"})

    summary = sub.groupby("type")[avail].mean().T
    summary["momentum_vs_reversal_diff"] = summary.get("momentum", 0) - summary.get("reversal", 0)
    summary.insert(0, "n_momentum", int((sub["type"] == "momentum").sum()))
    summary.insert(1, "n_reversal", int((sub["type"] == "reversal").sum()))
    return summary.round(4)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="サンプル数（0=全銘柄）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--neg-per-ticker", type=int, default=3)
    ap.add_argument("--workers", type=int, default=max(1, cpu_count() // 2))
    ap.add_argument("--period", default="6y")
    ap.add_argument("--with-pit-funda", action="store_true",
                    help="yfinance quarterly で point-in-time ファンダを取得（遅い）")
    args = ap.parse_args()
    rng = np.random.RandomState(args.seed)

    print(f"=== 実験設定: sample={args.sample or '全銘柄'}, workers={args.workers}, "
          f"neg_per_ticker={args.neg_per_ticker}, period={args.period} ===")

    uni = load_universe(args.sample, args.seed)
    tickers = uni["ticker"].tolist()

    print("market context 取得中...")
    mc, market_series = market_context()

    print(f"\n{len(tickers)} 銘柄の価格データ取得中...")
    panel = data.fetch_many(tickers, period=args.period)
    print(f"  取得成功: {len(panel)} 銘柄")

    # PIT ファンダ（オプション）
    pit_cache: dict[str, pd.DataFrame | None] = {}
    if args.with_pit_funda:
        print(f"\npoint-in-time ファンダ取得中（{len(panel)} 銘柄）...")
        pit_cache = _build_pit_cache(list(panel.keys()))
        pit_ok = sum(1 for v in pit_cache.values() if v is not None)
        print(f"  取得成功: {pit_ok}/{len(panel)}")

    # 並列特徴量計算
    print(f"\n特徴量計算中（{args.workers} workers）...")
    all_pos, all_neg, all_doublers = [], [], []

    def _worker(t):
        df = panel.get(t)
        if df is None:
            return [], [], []
        pit = pit_cache.get(t) if args.with_pit_funda else None
        local_rng = np.random.RandomState(abs(hash(t)) % (2**31))
        return _process_ticker(t, df, mc, market_series, pit, args.neg_per_ticker, local_rng)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_worker, t): t for t in panel}
        done = 0
        for f in as_completed(futs):
            pos, neg, dbl = f.result()
            all_pos.extend(pos)
            all_neg.extend(neg)
            all_doublers.extend(dbl)
            done += 1
            if done % 200 == 0:
                print(f"  処理済み: {done}/{len(panel)} (正例累計: {len(all_pos)})")

    n_pos, n_neg = len(all_pos), len(all_neg)
    print(f"\n正例(doubler点火点)={n_pos}, 負例={n_neg}, doublerイベント総数={len(all_doublers)}")
    if n_pos == 0:
        print("正例ゼロ。サンプルを増やすか period を延ばす。")
        return

    pos_df = pd.DataFrame(all_pos)
    neg_df = pd.DataFrame(all_neg)

    print("\n統計計算中...")
    feat_stats, rule_stats = compute_stats(pos_df, neg_df)

    def _nf(row):
        return score_features(row.to_dict())["n_factors"]
    pos_df["n_factors"] = pos_df.apply(_nf, axis=1)
    neg_df["n_factors"] = neg_df.apply(_nf, axis=1)
    score_dist = pd.DataFrame({
        "stat": ["mean", "median", "p25", "p75"],
        "positive": [pos_df.n_factors.mean(), pos_df.n_factors.median(),
                     pos_df.n_factors.quantile(.25), pos_df.n_factors.quantile(.75)],
        "negative": [neg_df.n_factors.mean(), neg_df.n_factors.median(),
                     neg_df.n_factors.quantile(.25), neg_df.n_factors.quantile(.75)],
    })

    print("クラスター分析中（モメンタム型 vs 反転型）...")
    cluster_df = cluster_analysis(pos_df)

    # 保存
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_doublers).to_csv(RESULTS / "doublers.csv", index=False)
    feat_stats.to_csv(RESULTS / "factor_stats.csv", index=False)
    rule_stats.to_csv(RESULTS / "rule_stats.csv", index=False)
    score_dist.to_csv(RESULTS / "score_dist.csv", index=False)
    pos_df.to_csv(RESULTS / "pos_features.csv", index=False)
    if not cluster_df.empty:
        cluster_df.to_csv(RESULTS / "cluster_analysis.csv")

    report = write_report(args, uni, panel, all_doublers, n_pos, n_neg,
                          feat_stats, rule_stats, score_dist, cluster_df, market_series)
    print("\n結果を experiments/results/ に保存しました。")
    print("\n" + "=" * 70 + "\nREPORT (full)\n" + "=" * 70)
    print(report)


def write_report(args, uni, panel, doublers, n_pos, n_neg,
                 feat_stats, rule_stats, score_dist, cluster_df, market_series):
    n_dbl_tickers = len({d["ticker"] for d in doublers})
    lines = [
        "# 実験レポート v2: 2倍株の本質的要素 全銘柄検証\n",
        f"- サンプル銘柄数(取得成功): {len(panel)} / 要求 {args.sample or '全銘柄'} (seed={args.seed})",
        f"- 並列 workers: {args.workers}",
        f"- 市場インデックス取得: {'OK' if market_series is not None else 'NONE'}",
        f"- PIT ファンダ取得: {'ON' if args.with_pit_funda else 'OFF'}\n",
        "## H2: 2倍イベントは十分存在するか",
        f"- doublerイベント総数: **{len(doublers)}**",
        f"- 2倍を経験した銘柄数: **{n_dbl_tickers}** / {len(panel)} "
        f"({100*n_dbl_tickers/max(1,len(panel)):.1f}%)",
        f"- 正例(点火点)={n_pos}, 負例={n_neg}\n",
        "## 特徴量の識別力（AUC, 全特徴量）",
        "AUC=0.5 は無情報。0.5 から離れるほど識別力あり。\n",
        feat_stats.to_markdown(index=False),
        "\n## ルール命中率 lift (pos − neg)",
        rule_stats.to_markdown(index=False),
        "\n## スコア分布 (充足要素数)",
        score_dist.to_markdown(index=False),
    ]

    if not cluster_df.empty:
        lines += [
            "\n## クラスター分析: モメンタム型 vs 反転型",
            f"- K-Means(k=2) で正例を分類。dist_52w_high 高 = モメンタム型。",
            cluster_df.to_markdown(),
            "\n**解釈**: n_momentum / n_reversal の比率と特徴量差を見ること。",
            "  - dist_52w_high: モメンタム型が高い(=高値圏)なら仮説が支持される",
            "  - atr_pct: 両型に共通して高いなら「高ボラ」は共通要件",
        ]

    lines += [
        "\n## 解釈メモ",
        "- abs_auc_lift 上位の要素を本質的要素として採用候補にする。",
        "- near_52w_high / at_new_high が上位なら「モメンタム型2倍株」が存在する証拠。",
        "- クラスター分析で両タイプが分かれた場合はスコアルールを群別に設計する。",
        "- PIT ファンダ(pit_*)の lift が高ければ score.py のファンダルールを更新する。",
    ]
    report = "\n".join(lines)
    (RESULTS / "report.md").write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
