"""score.py 2トラック設計の回帰テスト (R11)。ネット不要。

実行: python -m pytest tests/test_score.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockfactor.score import score_features, COMMON_RULES, MOMENTUM_RULES, REVERSAL_RULES  # noqa: E402


# 典型的なモメンタム型 doubler の特徴量（高値圏・上昇トレンド・市場アウトパフォーム）
MOMENTUM_FEATURES = {
    "atr_pct": 0.06,           # high_volatility (common)
    "vol_ratio": 1.5,          # volume_surge (common)
    "vcp": 1.1,                # vol_expansion (common)
    "above_sma200": 1.0,       # uptrend_200 (momentum)
    "sma_aligned": 1.0,        # trend_aligned (momentum)
    "dist_52w_high": 0.92,     # near_highs (momentum), off_highs は不成立
    "rs_6m": 0.40,             # positive_rs (momentum)
    "ret_1m": 0.10,            # momentum_up (momentum), recent_dip は不成立
    "px_to_sma25": 1.05,       # below_sma25 不成立
    "drawdown_252": -0.05,     # deep_drawdown 不成立
}

# 典型的な反転型 doubler の特徴量（高値から下落・押し目・直近投げ）
REVERSAL_FEATURES = {
    "atr_pct": 0.06,           # high_volatility (common)
    "vol_ratio": 1.5,          # volume_surge (common)
    "vcp": 1.1,                # vol_expansion (common)
    "above_sma200": 0.0,       # uptrend_200 不成立
    "sma_aligned": 0.0,        # trend_aligned 不成立
    "dist_52w_high": 0.55,     # off_highs 成立, near_highs 不成立
    "rs_6m": -0.20,            # positive_rs 不成立
    "ret_1m": -0.12,           # recent_dip 成立, momentum_up 不成立
    "px_to_sma25": 0.90,       # below_sma25 成立
    "drawdown_252": -0.45,     # deep_drawdown 成立
}


def test_momentum_classified_as_momentum():
    s = score_features(MOMENTUM_FEATURES)
    assert s["setup_type"] == "momentum"
    assert s["momentum_score"] > s["reversal_score"]
    # モメンタム型トラックの全要素が hits に入る
    for r in MOMENTUM_RULES:
        assert r.key in s["hits"], f"{r.key} should hit for momentum setup"


def test_reversal_classified_as_reversal():
    s = score_features(REVERSAL_FEATURES)
    assert s["setup_type"] == "reversal"
    assert s["reversal_score"] > s["momentum_score"]
    for r in REVERSAL_RULES:
        assert r.key in s["hits"], f"{r.key} should hit for reversal setup"


def test_common_rules_hit_in_both():
    for feats in (MOMENTUM_FEATURES, REVERSAL_FEATURES):
        s = score_features(feats)
        for r in COMMON_RULES:
            assert r.key in s["hits"], f"common {r.key} should hit in both setups"


def test_output_schema():
    s = score_features(MOMENTUM_FEATURES)
    for key in ("setup_type", "n_factors", "weighted_score",
                "reversal_score", "momentum_score", "by_group", "hits", "detail"):
        assert key in s, f"missing output key: {key}"
    assert isinstance(s["n_factors"], int)
    assert s["n_factors"] == len(s["hits"])


def test_empty_features_safe():
    # 全要素 NaN/欠損でもクラッシュしない
    s = score_features({})
    assert s["n_factors"] == 0
    assert s["setup_type"] in ("momentum", "reversal")  # 同点なら reversal


def test_conflicting_rules_not_double_counted():
    # off_highs と near_highs は相反。両方同時に primary に入らない。
    s_mom = score_features(MOMENTUM_FEATURES)
    s_rev = score_features(REVERSAL_FEATURES)
    assert not ("off_highs" in s_mom["hits"] and "near_highs" in s_mom["hits"])
    assert not ("off_highs" in s_rev["hits"] and "near_highs" in s_rev["hits"])


if __name__ == "__main__":
    test_momentum_classified_as_momentum()
    test_reversal_classified_as_reversal()
    test_common_rules_hit_in_both()
    test_output_schema()
    test_empty_features_safe()
    test_conflicting_rules_not_double_counted()
    print("all score tests passed")
