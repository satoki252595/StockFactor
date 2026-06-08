"""スコアリング (R11): 2トラック設計（反転型 / モメンタム型）。

全銘柄(3,734)・正例1,233件のクラスター分析で、日本の半年2倍株は **2つのモード** が
ほぼ拮抗して存在することが判明した（experiments/results/cluster_analysis.csv）:

  - モメンタム型 (526件, 43%): 高値圏(dist_52w_high≈0.88) × 上昇トレンド(above_sma200≈100%)
    × パーフェクトオーダー(sma_aligned≈87%) × 市場アウトパフォーム(rs_6m≈+0.52)。
    例: キオクシア等。高値ブレイク・順張り型。
  - 反転型 (707件, 57%): 高値から下落(dist_52w_high≈0.56) × 押し目(below_sma25)
    × 直近投げ(ret_1m≈-0.15) × 市場アンダーパフォーム。出遅れ・逆張り型。

両型に共通する唯一の必須級要素は **high_volatility (atr_pct≥0.04)**（AUC0.777/lift0.393）。

設計: 各銘柄について「共通要素 + 反転型要素」「共通要素 + モメンタム型要素」を別々に採点し、
両トラックの高い方を primary score とする。setup_type で型を明示する。
しきい値は exp(全銘柄)のAUC/liftに基づく。変更時は experiment 再検証が必須。
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
    validated: bool = False  # exp で識別力を確認したら True
    track: str = "common"    # common / reversal / momentum / fundamental / macro
    desc: str = ""


# === 共通要素（両トラックで加点。2倍株の地力＝動けること）===
COMMON_RULES: list[Rule] = [
    Rule("high_volatility", "technical", "atr_pct", ">=", 0.040, 2.0, True, "common",
         desc="ATR%≥4%。最強の識別子(AUC0.78/lift0.39)。両型共通の必須級"),
    Rule("volume_surge", "technical", "vol_ratio", ">=", 1.30, 1.0, True, "common",
         desc="5日出来高≥60日平均×1.3。点火の出来高(lift0.17)"),
    Rule("vol_expansion", "technical", "vcp", ">=", 1.00, 0.8, True, "common",
         desc="ボラ拡大(vcp≥1.0, lift0.14)。収縮ではない"),
]

# === 反転型トラック（出遅れ・逆張り。doublerの57%）===
REVERSAL_RULES: list[Rule] = [
    Rule("off_highs", "technical", "dist_52w_high", "<=", 0.80, 1.5, True, "reversal",
         desc="52週高値の80%以下＝高値から下落・出遅れ位置(lift0.24)"),
    Rule("below_sma25", "technical", "px_to_sma25", "<=", 1.00, 1.0, True, "reversal",
         desc="25日線の下＝押し目/底値圏(lift0.22)"),
    Rule("recent_dip", "technical", "ret_1m", "<=", 0.00, 1.0, True, "reversal",
         desc="直近1カ月は下落/投げ＝反転直前のcapitulation(lift0.19)"),
    Rule("deep_drawdown", "technical", "drawdown_252", "<=", -0.25, 0.8, True, "reversal",
         desc="52週高値から25%以上の下落＝十分な押し目(pos中央値-0.28)"),
]

# === モメンタム型トラック（高値ブレイク・順張り。doublerの43%）===
MOMENTUM_RULES: list[Rule] = [
    Rule("uptrend_200", "technical", "above_sma200", ">=", 1.0, 1.5, True, "momentum",
         desc="200日線の上＝上昇トレンド(モメンタム型99.6% vs 反転型10%)。最強の型分離"),
    Rule("trend_aligned", "technical", "sma_aligned", ">=", 1.0, 1.2, True, "momentum",
         desc="25>75>200のパーフェクトオーダー(モメンタム型87% vs 反転型10%)"),
    Rule("near_highs", "technical", "dist_52w_high", ">=", 0.85, 1.5, True, "momentum",
         desc="52週高値の85%以上＝高値圏(モメンタム型0.88 vs 反転型0.56)"),
    Rule("positive_rs", "technical", "rs_6m", ">=", 0.10, 1.0, True, "momentum",
         desc="対TOPIX 6M相対強さ＋10%以上(モメンタム型+0.52 vs 反転型-0.19)"),
    Rule("momentum_up", "technical", "ret_1m", ">=", 0.00, 0.8, True, "momentum",
         desc="直近1カ月は上昇(モメンタム型+0.18 vs 反転型-0.15)"),
]

# === マクロ（識別力弱→低ウェイトの参考。両型共通の地合い）===
MACRO_RULES: list[Rule] = [
    Rule("smallcap_leadership", "macro", "growth_minus_mkt_6m", ">=", 0.0, 0.3, False, "macro",
         desc="小型株が市場を6Mで上回る（識別力弱）"),
]

# === ファンダ×ミクロ（snapshot/PIT・フォワード適用。両型共通の質）===
FUNDAMENTAL_RULES: list[Rule] = [
    Rule("small_cap", "fundamental", "small_cap", ">=", 1.0, 1.0, False, "fundamental",
         desc="時価総額500億円未満（伸びしろ）"),
    Rule("revenue_growth", "fundamental", "revenue_growth", ">=", 0.15, 1.0, False, "fundamental",
         desc="増収率15%以上"),
    Rule("earnings_growth", "fundamental", "earnings_growth", ">=", 0.20, 0.8, False, "fundamental",
         desc="増益率20%以上"),
    Rule("high_roe", "fundamental", "roe", ">=", 0.10, 0.5, False, "fundamental",
         desc="ROE 10%以上"),
    Rule("undervalued_growth", "fundamental", "psr", "<=", 5.0, 0.5, False, "fundamental",
         desc="PSR 5倍以下（割高でない成長）"),
]

# 全ルール（後方互換: 旧 RULES を参照するコード向け）。
RULES: list[Rule] = (
    COMMON_RULES + REVERSAL_RULES + MOMENTUM_RULES + MACRO_RULES + FUNDAMENTAL_RULES
)

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _check(features: dict, r: Rule) -> bool:
    v = features.get(r.feature)
    return (
        v is not None
        and isinstance(v, (int, float))
        and math.isfinite(v)
        and _OPS[r.op](v, r.threshold)
    )


def _score_rules(features: dict, rules: list[Rule]) -> tuple[list[str], float]:
    hits, weighted = [], 0.0
    for r in rules:
        if _check(features, r):
            hits.append(r.key)
            weighted += r.weight
    return hits, weighted


def score_features(features: dict[str, float], rules: list[Rule] | None = None) -> dict:
    """2トラック採点。反転型・モメンタム型を別々に評価し、高い方を primary とする。

    返り値:
      setup_type:        "momentum" / "reversal"（primaryトラック）
      n_factors:         primary トラックの充足要素数（共通+型+ファンダ+マクロ）
      weighted_score:    primary トラックの重み付きスコア
      reversal_score / momentum_score: 各トラックの重み付きスコア（共通+型+共有）
      by_group:          群別充足数
      hits:              primary トラックで充足した要素ラベル
      detail:            全ルールの充足真偽
    """
    # 共通・マクロ・ファンダは両トラックで共有
    common_hits, common_w = _score_rules(features, COMMON_RULES)
    macro_hits, macro_w = _score_rules(features, MACRO_RULES)
    funda_hits, funda_w = _score_rules(features, FUNDAMENTAL_RULES)
    shared_hits = common_hits + macro_hits + funda_hits
    shared_w = common_w + macro_w + funda_w

    rev_hits, rev_w = _score_rules(features, REVERSAL_RULES)
    mom_hits, mom_w = _score_rules(features, MOMENTUM_RULES)

    reversal_total = shared_w + rev_w
    momentum_total = shared_w + mom_w

    if momentum_total > reversal_total:
        setup_type = "momentum"
        primary_hits = shared_hits + mom_hits
        primary_w = momentum_total
    else:
        setup_type = "reversal"
        primary_hits = shared_hits + rev_hits
        primary_w = reversal_total

    # 群別充足数（primary トラックの hits を集計）
    label_to_group = {r.key: r.group for r in RULES}
    by_group: dict[str, int] = {}
    for h in primary_hits:
        g = label_to_group.get(h, "technical")
        by_group[g] = by_group.get(g, 0) + 1

    # detail: 全ルール（重複ラベルは OR）
    detail: dict[str, bool] = {}
    for r in RULES:
        detail[r.key] = detail.get(r.key, False) or _check(features, r)

    return {
        "setup_type": setup_type,
        "n_factors": len(primary_hits),
        "weighted_score": round(primary_w, 3),
        "reversal_score": round(reversal_total, 3),
        "momentum_score": round(momentum_total, 3),
        "by_group": by_group,
        "hits": primary_hits,
        "detail": detail,
    }
