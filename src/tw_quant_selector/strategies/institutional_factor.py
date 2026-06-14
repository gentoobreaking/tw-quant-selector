from __future__ import annotations
import httpx
import pandas as pd
from datetime import date, timedelta
from typing import Any
import numpy as np
import structlog

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore

log = structlog.get_logger()


def get_quarter_weight(trade_date: date) -> float:
    if trade_date.month <= 3:
        q_end = date(trade_date.year, 3, 25)
    elif trade_date.month <= 6:
        q_end = date(trade_date.year, 6, 25)
    elif trade_date.month <= 9:
        q_end = date(trade_date.year, 9, 25)
    else:
        q_end = date(trade_date.year, 12, 25)
    if trade_date > q_end:
        if q_end.month == 12:
            q_end = date(trade_date.year + 1, 3, 25)
        else:
            q_end = date(trade_date.year, q_end.month + 3, 25)
    business_days = 0
    d = trade_date
    while d <= q_end:
        if d.weekday() < 5:
            business_days += 1
        d += timedelta(days=1)
    return 0.3 if business_days <= 3 else 1.0


def calc_consecutive_days_vectorized(df: pd.DataFrame) -> pd.Series:
    """
    計算每個股票的連續買賣超天數（從最新日期往回算）
    df 必須包含 stock_id, total_net，且按 trade_date 降序排列
    """
    def _calc_single(group):
        if group.empty:
            return 0
        vals = group['total_net'].values
        first = vals[0]
        if pd.isna(first) or first == 0:
            return 0
        direction = 1 if first > 0 else -1
        count = 0
        for v in vals:
            if pd.isna(v):
                break
            if direction == 1 and v > 0:
                count += 1
            elif direction == -1 and v < 0:
                count += 1
            else:
                break
        return count * direction

    return df.groupby('stock_id', sort=False).apply(_calc_single, include_groups=False)


def calc_institutional_concurrence(df: pd.DataFrame) -> pd.Series:
    """
    計算外資與投信同時買超的布林值序列
    公式：ForeignNetShares > 0 AND TrustNetShares > 0
    
    應用：捕捉內外資同時鎖碼的強勢波段股。
    
    Args:
        df: 包含 ForeignNetShares, TrustNetShares 的 DataFrame
    """
    return (df['ForeignNetShares'] > 0) & (df['TrustNetShares'] > 0)


def calc_sitca_share_ratio(df: pd.DataFrame, shares_outstanding: pd.DataFrame) -> pd.Series:
    """
    計算投信單日淨買張數佔發行張數比例
    公式：投信單日淨買張數 / 該股發行張數
    
    應用：投信受限於單一基金持股 10% 限制，此因子可提早發現投信剛開始建倉的「中小型黑馬股」。
    
    Args:
        df: 包含 TrustNetShares 的 DataFrame（索引：股票代號）
        shares_outstanding: 包含 SharesOutstanding 的 DataFrame（索引：股票代號）
    
    Returns:
        pd.Series: 浮點數（0.0 ~ 1.0）
    """
    # 合併發行張數資料
    merged = df.merge(shares_outstanding, left_index=True, right_index=True)
    return merged['TrustNetShares'] / merged['SharesOutstanding']


TWSE_COMPANY_API = "https://openapi.twse.com.tw/v1/announcement/t187ap03_L"


def _fetch_shares_outstanding() -> dict[str, int]:
    try:
        resp = httpx.get(TWSE_COMPANY_API, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        result: dict[str, int] = {}
        for r in rows:
            code = r.get("公司代號", "")
            shares_str = r.get("已發行普通股數或TDR原股發行股數", "0")
            try:
                shares = int(shares_str)
            except (ValueError, TypeError):
                shares = 0
            if shares > 0:
                result[code] = shares
        return result
    except Exception as exc:
        log.warning("inst_factor.shares_fetch_failed", error=str(exc))
        return {}


@register_strategy
class InstitutionalFactor(BaseStrategy):
    name = "institutional"

    def __init__(self, foreign_weight: float = 0.4, trust_weight: float = 0.2,
                 consec_weight: float = 0.2, concurrence_weight: float = 0.1,
                 sitca_ratio_weight: float = 0.1, lookback_days: int = 20,
                 min_flow_days: int = 5):
        self.foreign_weight = foreign_weight
        self.trust_weight = trust_weight
        self.consec_weight = consec_weight
        self.concurrence_weight = concurrence_weight
        self.sitca_ratio_weight = sitca_ratio_weight
        self.lookback_days = lookback_days
        self.min_flow_days = min_flow_days

    def get_required_data(self) -> list[str]:
        return ["institutional_flows"]

    def compute_score(self, universe: list[str], as_of_date: date, db=None) -> dict[str, float]:
        if not universe or db is None:
            return {}

        q_weight = get_quarter_weight(as_of_date)
        shares_dict = _fetch_shares_outstanding()
        shares_df = pd.DataFrame.from_dict(
            shares_dict, orient='index', columns=['SharesOutstanding']
        )
        shares_df.index.name = 'stock_id'

        # Batch query for all stocks in universe
        sql = """
            SELECT stock_id, trade_date, foreign_investors_net, sity_investors_net, total_net
            FROM (
                SELECT stock_id, trade_date, foreign_investors_net, sity_investors_net, total_net,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) as rn
                FROM institutional_flows
                WHERE stock_id = ANY(:universe) AND trade_date <= :as_of_date
            ) t
            WHERE rn <= :lookback_days
        """
        rows = db.execute(sql, {"universe": universe, "as_of_date": as_of_date, "lookback_days": self.lookback_days}).fetchall()
        if not rows:
            return {}

        df = pd.DataFrame(rows, columns=['stock_id', 'trade_date', 'ForeignNetShares', 'TrustNetShares', 'total_net'])
        df['ForeignNetShares'] = df['ForeignNetShares'].fillna(0).astype(float)
        df['TrustNetShares'] = df['TrustNetShares'].fillna(0).astype(float)
        df['total_net'] = df['total_net'].fillna(0).astype(float)

        # Filter by min_flow_days
        counts = df.groupby('stock_id')['trade_date'].count()
        valid_sids = counts[counts >= self.min_flow_days].index
        if valid_sids.empty:
            return {}
        df = df[df['stock_id'].isin(valid_sids)]

        # 1. Foreign Flow (Sum / SharesOutstanding)
        grouped = df.groupby('stock_id')
        foreign_sum = grouped['ForeignNetShares'].sum()
        shares_reindexed = shares_df['SharesOutstanding'].reindex(foreign_sum.index)
        foreign_flow = foreign_sum / shares_reindexed.fillna(1e12) 
        # Fallback if SharesOutstanding is missing
        foreign_flow[shares_reindexed.isna()] = foreign_sum[shares_reindexed.isna()]

        # 2. Trust Flow (Sum * q_weight)
        trust_sum = grouped['TrustNetShares'].sum()
        trust_flow = trust_sum * q_weight

        # 3. Consecutive Days
        consec = calc_consecutive_days_vectorized(df)
        consec_norm = (consec.clip(-20, 20) / 20.0).astype(float)

        # Latest day data for Concurrence and SITCA Ratio
        latest_idx = df.groupby('stock_id')['trade_date'].idxmax()
        latest_df = df.loc[latest_idx].copy()
        latest_df.set_index('stock_id', inplace=True)

        # 4. Institutional Concurrence
        concurrence_bool = calc_institutional_concurrence(latest_df)
        concurrence_score = pd.Series(0.0, index=latest_df.index)
        concurrence_score[concurrence_bool] = 1.0

        # 5. SITCA Share Ratio
        sitca_ratio = calc_sitca_share_ratio(latest_df, shares_df)

        # Combine scores
        common_ids = foreign_flow.index.intersection(trust_flow.index).intersection(consec_norm.index)
        final_scores = (
            foreign_flow.reindex(common_ids) * self.foreign_weight +
            trust_flow.reindex(common_ids) * self.trust_weight +
            consec_norm.reindex(common_ids) * self.consec_weight +
            concurrence_score.reindex(common_ids).fillna(0) * self.concurrence_weight +
            sitca_ratio.reindex(common_ids).fillna(0) * self.sitca_ratio_weight
        )

        if final_scores.empty:
            return {}

        vals = final_scores.values
        if np.std(vals) < 1e-12:
            return {sid: 0.0 for sid in final_scores.index}

        z = safe_zscore(vals)
        return {sid: float(z[i]) for i, sid in enumerate(final_scores.index)}
