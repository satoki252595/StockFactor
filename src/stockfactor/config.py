"""グローバル設定値（要件 R3 のしきい値・期間など）。"""
from __future__ import annotations

from pathlib import Path

# --- パス ---
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
RESULTS_DIR = ROOT / "experiments" / "results"
for _d in (DATA_DIR, OUTPUT_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- 2倍株スクリーナ (R3) ---
HORIZON_TD = 126           # 半年 ≒ 126 営業日
DOUBLE_THRESHOLD = 2.0     # 2倍 = +100%
LOOKBACK_YEARS = 5         # 直近5年

# --- 流動性フィルタ (R2) ---
MIN_PRICE = 100.0                  # 最低株価(円) 低位株ノイズ除去
MIN_AVG_TURNOVER_JPY = 30_000_000  # 直近60日平均売買代金(円) 約3000万円/日
MIN_HISTORY_TD = 252               # 最低上場履歴（1年）

# --- 市場インデックス（マクロ R6） ---
# yfinance シンボル。検証で疎通確認する（exp01）。
TOPIX_ETF = "1306.T"       # NEXT FUNDS TOPIX 連動 ETF（^TPX が不安定なため ETF を主とする）
NIKKEI = "^N225"
GROWTH_ETF = "2516.T"      # 東証グロース市場250 ETF（小型株レジーム代理）

# --- テクニカル指標パラメータ (R5) ---
SMA_SHORT, SMA_MID, SMA_LONG = 25, 75, 200
HIGH_WINDOW = 252          # 52週高値
VOL_SURGE_SHORT, VOL_SURGE_LONG = 5, 60
ATR_WINDOW = 14
VCP_SHORT, VCP_LONG = 20, 60
