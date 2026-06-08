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


# データ検証で確定したカタログ（experiments/results/report.md・FINDINGS.md 参照）。
# 重要: 日本の「半年2倍株」は米国流のモメンタム/ブレイク型では説明できず、
# 「高ボラ × 高値から下落(押し目) × 出来高急増 × ボラ拡大」という反転型プロファイル。
# テクニカルは実データで識別力を確認済み(validated=True)。ファンダは無料の過去点別データが
# 無く過去検証できないためフォワード適用(validated=False)。マクロは識別力が弱く低ウェイト。
RULES: list[Rule] = [
    # --- テクニカル (R5) 実データ検証済み ---
    Rule("high_volatility", "technical", "atr_pct", ">=", 0.040, 2.0, True,
         desc="ATR%が高い（最強の識別子, AUC0.81）。動ける銘柄"),
    Rule("off_highs", "technical", "dist_52w_high", "<=", 0.80, 1.5, True,
         desc="52週高値の80%以下＝高値から下落・出遅れ位置から反転（AUC0.33=強い逆相関）"),
    Rule("volume_surge", "technical", "vol_ratio", ">=", 1.30, 1.5, True,
         desc="直近5日出来高が60日平均の1.3倍以上＝点火の出来高(lift+0.20)"),
    Rule("below_sma25", "technical", "px_to_sma25", "<=", 1.00, 1.0, True,
         desc="25日線の下＝押し目/底値圏から発火"),
    Rule("vol_expansion", "technical", "vcp", ">=", 1.00, 1.0, True,
         desc="直近ボラが拡大（収縮ではない, AUC0.61）"),
    # --- マクロ (R6) 識別力弱→低ウェイト ---
    Rule("smallcap_leadership", "macro", "growth_minus_mkt_6m", ">=", 0.0, 0.3, False,
         desc="小型株が市場を6Mで上回る（本サンプルでは識別力弱）"),
    # --- ファンダ×ミクロ (R7) current snapshot・過去検証不可(フォワード適用) ---
    Rule("small_cap", "fundamental", "small_cap", ">=", 1.0, 1.0, False,
         desc="時価総額500億円未満（伸びしろ）"),
    Rule("revenue_growth", "fundamental", "revenue_growth", ">=", 0.15, 1.0, False,
         desc="増収率15%以上"),
    Rule("earnings_growth", "fundamental", "earnings_growth", ">=", 0.20, 0.8, False,
         desc="増益率20%以上"),
    Rule("high_roe", "fundamental", "roe", ">=", 0.10, 0.5, False,
         desc="ROE 10%以上"),
    Rule("undervalued_growth", "fundamental", "psr", "<=", 5.0, 0.5, False,
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
