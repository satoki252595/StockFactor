"""J-Quants API クライアント (R10拡張): 歴史的ファンダデータの point-in-time 取得。

yfinance では現時点スナップショットしか取れないため、過去の point-in-time ファンダ検証ができない。
J-Quants API (JPX公式) を使うと四半期開示データ + DisclosedDate が取れるため、
t0 時点で実際に公開されていた決算数値を使った検証が可能になる。

認証情報の設定:
  環境変数: JQUANTS_EMAIL, JQUANTS_PASSWORD
  または: jquants_client = JQuantsClient(email="...", password="...")

無料 Lite プラン: 直近12週の価格データのみ（ファンダ過去データ取得不可）
Light プラン(¥3,300/月〜): 財務諸表の全履歴が取得可能 ← point-in-time 検証に必要

API ドキュメント: https://jpx-jquants.com/#api-document
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd


class JQuantsClient:
    """J-Quants API v1 クライアント。トークンを自動管理する。"""

    BASE = "https://api.jquants.com/v1"

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        refresh_token: str | None = None,
    ):
        self.email = email or os.getenv("JQUANTS_EMAIL", "")
        self.password = password or os.getenv("JQUANTS_PASSWORD", "")
        self._refresh_token = refresh_token or os.getenv("JQUANTS_REFRESH_TOKEN", "")
        self._id_token: str = ""
        self._id_token_expiry: datetime = datetime.min

    # ------------------------------------------------------------------
    # 認証
    # ------------------------------------------------------------------
    def _get_refresh_token(self) -> str:
        import requests
        r = requests.post(
            f"{self.BASE}/token/auth_user",
            json={"mailaddress": self.email, "password": self.password},
            timeout=30,
        )
        r.raise_for_status()
        self._refresh_token = r.json()["refreshToken"]
        return self._refresh_token

    def _get_id_token(self) -> str:
        import requests
        if not self._refresh_token:
            self._get_refresh_token()
        r = requests.post(
            f"{self.BASE}/token/auth_refresh",
            params={"refreshtoken": self._refresh_token},
            timeout=30,
        )
        r.raise_for_status()
        self._id_token = r.json()["idToken"]
        self._id_token_expiry = datetime.now() + timedelta(hours=23)
        return self._id_token

    def _token(self) -> str:
        if not self._id_token or datetime.now() >= self._id_token_expiry:
            self._get_id_token()
        return self._id_token

    def _get(self, path: str, params: dict | None = None) -> dict:
        import requests
        headers = {"Authorization": f"Bearer {self._token()}"}
        r = requests.get(f"{self.BASE}{path}", headers=headers, params=params or {}, timeout=60)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # 財務データ
    # ------------------------------------------------------------------
    def get_statements(self, code: str) -> pd.DataFrame:
        """四半期財務諸表の全履歴を返す。DisclosedDate が point-in-time キー。

        コード形式: "13010" (5桁) または "1301" (4桁) いずれも可。
        """
        code4 = str(code).replace(".T", "").zfill(4)[:4]
        data = self._get("/fins/statements", params={"code": code4})
        stmts = data.get("statements", [])
        if not stmts:
            return pd.DataFrame()
        df = pd.DataFrame(stmts)
        # 日付型に変換
        for col in ["DisclosedDate", "CurrentPeriodEndDate", "CurrentFiscalYearEndDate"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        # 数値型に変換
        num_cols = [
            "NetSales", "GrossProfits", "OperatingProfit", "OrdinaryProfit",
            "Profit", "EarningsPerShare", "TotalAssets", "Equity",
            "BookValuePerShare", "CashAndEquivalents",
            "ResultDividendPerShareAnnual",
        ]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("DisclosedDate").reset_index(drop=True)

    def get_fins_pit(self, code: str, as_of: pd.Timestamp) -> dict[str, float]:
        """as_of 時点で公開済みの最新四半期データから point-in-time ファンダ特徴量を返す。

        YoY 成長率は「前年同期比」として計算する。
        """
        df = self.get_statements(code)
        if df.empty or "DisclosedDate" not in df.columns:
            return {}

        # as_of 以前に開示されたレコードのみ使用（point-in-time）
        avail = df[df["DisclosedDate"] <= as_of].copy()
        if avail.empty:
            return {}

        latest = avail.iloc[-1]

        # 前年同期 (約4四半期前)
        prev_rows = avail[
            avail["CurrentPeriodEndDate"] <= (latest["CurrentPeriodEndDate"] - pd.Timedelta(days=330))
        ] if "CurrentPeriodEndDate" in avail.columns else pd.DataFrame()
        prev = prev_rows.iloc[-1] if not prev_rows.empty else None

        def _safe(row, col):
            v = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
            return float(v) if (v is not None and pd.notna(v)) else np.nan

        net_sales = _safe(latest, "NetSales")
        prev_sales = _safe(prev, "NetSales") if prev is not None else np.nan
        op_profit = _safe(latest, "OperatingProfit")
        prev_op = _safe(prev, "OperatingProfit") if prev is not None else np.nan
        profit = _safe(latest, "Profit")
        prev_profit = _safe(prev, "Profit") if prev is not None else np.nan
        equity = _safe(latest, "Equity")
        total_assets = _safe(latest, "TotalAssets")

        rev_growth_yoy = (net_sales / prev_sales - 1.0) if (np.isfinite(net_sales) and np.isfinite(prev_sales) and prev_sales > 0) else np.nan
        op_growth_yoy = (op_profit / prev_op - 1.0) if (np.isfinite(op_profit) and np.isfinite(prev_op) and prev_op > 0) else np.nan
        profit_growth_yoy = (profit / prev_profit - 1.0) if (np.isfinite(profit) and np.isfinite(prev_profit) and prev_profit > 0) else np.nan
        roe_pit = (profit / equity) if (np.isfinite(profit) and np.isfinite(equity) and equity > 0) else np.nan
        op_margin = (op_profit / net_sales) if (np.isfinite(op_profit) and np.isfinite(net_sales) and net_sales > 0) else np.nan

        return {
            "jq_net_sales": net_sales,
            "jq_rev_growth_yoy": rev_growth_yoy,
            "jq_op_growth_yoy": op_growth_yoy,
            "jq_profit_growth_yoy": profit_growth_yoy,
            "jq_roe_pit": roe_pit,
            "jq_op_margin": op_margin,
            "jq_disclosed_date": latest["DisclosedDate"].isoformat() if pd.notna(latest["DisclosedDate"]) else "",
        }


# ------------------------------------------------------------------
# スタンドアロン取得ヘルパー（実験スクリプト用）
# ------------------------------------------------------------------

_client: JQuantsClient | None = None


def get_client() -> JQuantsClient | None:
    """環境変数から J-Quants クライアントを返す。認証情報がなければ None。"""
    global _client
    email = os.getenv("JQUANTS_EMAIL", "")
    password = os.getenv("JQUANTS_PASSWORD", "")
    if not email or not password:
        return None
    if _client is None:
        _client = JQuantsClient(email=email, password=password)
    return _client


def fetch_pit_fundamentals(
    ticker: str,
    dates: list[pd.Timestamp],
    client: JQuantsClient | None = None,
) -> dict[pd.Timestamp, dict]:
    """ticker の各 date における point-in-time ファンダ特徴量を返す。

    {date: {feature_name: value, ...}} 形式。
    J-Quants クライアントが None の場合は空を返す（graceful degradation）。
    """
    if client is None:
        client = get_client()
    if client is None:
        return {}
    code = ticker.replace(".T", "")
    try:
        df_stmt = client.get_statements(code)
    except Exception:
        return {}
    if df_stmt.empty:
        return {}

    result = {}
    for date in dates:
        avail = df_stmt[df_stmt["DisclosedDate"] <= date]
        if avail.empty:
            result[date] = {}
            continue
        latest = avail.iloc[-1]
        prev_rows = avail[
            avail["CurrentPeriodEndDate"] <= (latest["CurrentPeriodEndDate"] - pd.Timedelta(days=330))
        ] if "CurrentPeriodEndDate" in avail.columns else pd.DataFrame()
        prev = prev_rows.iloc[-1] if not prev_rows.empty else None

        def _s(row, col):
            v = row.get(col) if isinstance(row, dict) else (getattr(row, col, None) if row is not None else None)
            return float(v) if (v is not None and pd.notna(v)) else np.nan

        ns = _s(latest, "NetSales")
        pns = _s(prev, "NetSales")
        op = _s(latest, "OperatingProfit")
        pop = _s(prev, "OperatingProfit")
        pft = _s(latest, "Profit")
        ppft = _s(prev, "Profit")
        eq = _s(latest, "Equity")

        result[date] = {
            "jq_rev_growth_yoy": (ns / pns - 1.0) if (np.isfinite(ns) and np.isfinite(pns) and pns > 0) else np.nan,
            "jq_op_growth_yoy": (op / pop - 1.0) if (np.isfinite(op) and np.isfinite(pop) and pop > 0) else np.nan,
            "jq_profit_growth_yoy": (pft / ppft - 1.0) if (np.isfinite(pft) and np.isfinite(ppft) and ppft > 0) else np.nan,
            "jq_roe_pit": (pft / eq) if (np.isfinite(pft) and np.isfinite(eq) and eq > 0) else np.nan,
            "jq_op_margin": (op / ns) if (np.isfinite(op) and np.isfinite(ns) and ns > 0) else np.nan,
        }
        time.sleep(0.05)  # API レート制限配慮
    return result
