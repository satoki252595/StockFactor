"""スコアリング (R9): 要素カタログに基づき「何個の本質的要素が当てはまるか」を算出。

しきい値は実験 (exp03/R8) のデータ検証で更新する。ここでは初期値を置き、
`validated` フラグで「データで識別力を確認済みか」を示す。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Rule:
    key: str            # 出力ラベル
    group: str          # technical / macro / fundamental
    feature: str        # features dict のキー
    op: str             # ">=", "<=", ">", "<"
    threshold: float
    weight: float = 1.0
    validated: bool = False  # exp03 で識別力を確認したら True
    desc: str = ""


# 初期カタログ（テクニカル・マクロは exp03 で検証→閾値/採用を更新）
RULES: list[Rule] = [
    # --- テクニカル (R5) ---
    Rule("near_52w_high", "technical", "dist_52w_high", ">=", 0.90, 1.0,
         desc="52週高値の90%以上＝高値圏ブレイク準備"),
    Rule("volume_surge", "technical", "vol_ratio", ">=", 1.5, 1.0,
         desc="直近5日出来高が60日平均の1.5倍以上"),
    Rule("sma_perfect_order", "technical", "sma_aligned", ">=", 1.0, 1.0,
         desc="25>75>200 パーフェクトオーダー"),
    Rule("uptrend_above_200", "technical", "above_sma200", ">=", 1.0, 1.0,
         desc="200日線上"),
    Rule("momentum_3m", "technical", "ret_3m", ">=", 0.10, 1.0,
         desc="3カ月で+10%以上の初動"),
    Rule("vol_contraction", "technical", "vcp", "<=", 0.85, 1.0,
         desc="直近ボラが収縮（VCP的）"),
    Rule("rel_strength", "technical", "rs_6m", ">=", 0.0, 1.0,
         desc="TOPIX相対で6Mアウトパフォーム"),
    # --- マクロ (R6) ---
    Rule("market_uptrend", "macro", "mkt_above_200", ">=", 1.0, 1.0,
         desc="市場(TOPIX)が200日線上"),
    Rule("smallcap_leadership", "macro", "growth_minus_mkt_6m", ">=", 0.0, 1.0,
         desc="小型株が市場を6Mで上回る"),
    # --- ファンダ×ミクロ (R7) current snapshot ---
    Rule("small_cap", "fundamental", "small_cap", ">=", 1.0, 1.0,
         desc="時価総額500億円未満（伸びしろ）"),
    Rule("revenue_growth", "fundamental", "revenue_growth", ">=", 0.15, 1.0,
         desc="増収率15%以上"),
    Rule("earnings_growth", "fundamental", "earnings_growth", ">=", 0.20, 1.0,
         desc="増益率20%以上"),
    Rule("high_roe", "fundamental", "roe", ">=", 0.10, 1.0,
         desc="ROE 10%以上"),
    Rule("undervalued_growth", "fundamental", "psr", "<=", 5.0, 1.0,
         desc="PSR 5倍以下（割高でない成長）"),
]

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def score_features(features: dict[str, float], rules: list[Rule] = RULES) -> dict:
    """features から各要素の充足を判定し、充足数・重み付きスコア・内訳を返す。"""
    hits: list[str] = []
    weighted = 0.0
    by_group: dict[str, int] = {}
    detail: dict[str, bool] = {}
    for r in rules:
        v = features.get(r.feature)
        ok = v is not None and isinstance(v, (int, float)) and math.isfinite(v) and _OPS[r.op](v, r.threshold)
        detail[r.key] = bool(ok)
        if ok:
            hits.append(r.key)
            weighted += r.weight
            by_group[r.group] = by_group.get(r.group, 0) + 1
    return {
        "n_factors": len(hits),
        "weighted_score": round(weighted, 3),
        "by_group": by_group,
        "hits": hits,
        "detail": detail,
    }
