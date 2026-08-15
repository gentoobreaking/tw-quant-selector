"""Smart market alert checks.

Contains the 10 Pandas vectorized check functions (``check_volume_spike``,
``check_whale_move`` etc.) as module-level functions for direct import,
plus the ``SmartCheckerMixin`` with ``check_all_smart_alerts`` and its
DataFrame-building helpers.

Module: smart_checker.py
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

from tw_quant_selector.monitoring.base_checker import MARKET_CLOSE, MARKET_OPEN
from tw_quant_selector.monitoring.notifiers import format_alert


# ── Smart Alert Check Functions (Pandas Vectorized) ───────────────────

def check_volume_spike(df: pd.DataFrame) -> pd.DataFrame:
    median_vol = df.groupby('Category')['TradeVolume'].transform('median')
    alert_mask = df['TradeVolume'] > (median_vol * 10)
    return df[alert_mask].copy()

def check_high_vol_no_move(df: pd.DataFrame) -> pd.DataFrame:
    top_50_idx = df['TradeVolume'].nlargest(50).index
    alert_mask = df.index.isin(top_50_idx) & (df['Return_Pct'].abs() <= 0.5)
    return df[alert_mask].copy()

def check_turnover_monster(df: pd.DataFrame) -> pd.DataFrame:
    market_total_value = df['TradeValue'].sum()
    alert_mask = (df['TradeValue'] / market_total_value >= 0.02) & (df['Code'] != '2330')
    return df[alert_mask].copy()

def check_intraday_volatility(df: pd.DataFrame) -> pd.DataFrame:
    volatility = (df['HighestPrice'] - df['LowestPrice']) / df['PrevClose']
    price_position = (df['CurrentPrice'] - df['LowestPrice']) / (df['HighestPrice'] - df['LowestPrice'])
    alert_mask = (volatility > 0.08) & (price_position <= 0.1)
    return df[alert_mask].copy()

def check_industry_momentum(df: pd.DataFrame) -> pd.DataFrame:
    df['Is_Strong'] = df['Return_Pct'] > 4.0
    industry_strong_ratio = df.groupby('Industry')['Is_Strong'].transform('mean')
    alert_mask = industry_strong_ratio > 0.30
    return df[alert_mask].copy()

def check_against_trend(df: pd.DataFrame, market_weak: bool) -> pd.DataFrame:
    if not market_weak:
        return pd.DataFrame()
    alert_mask = (
        (df['Return_Pct'] > 2.0) &
        (df['TradeVolume'] > df['Volume_5d_avg'] * 1.2)
    )
    return df[alert_mask].copy()

def check_low_price_junk_rally(df: pd.DataFrame, market_high: bool) -> pd.DataFrame:
    if not market_high:
        return pd.DataFrame()
    limit_up_idx = df['Return_Pct'] > 9.5
    limit_up_stocks = df[limit_up_idx]
    if len(limit_up_stocks) == 0:
        return pd.DataFrame()
    low_price_ratio = (limit_up_stocks['Price'] < 30).sum() / len(limit_up_stocks)
    if low_price_ratio > 0.6:
        return pd.DataFrame([{'Alert': 'LOW_PRICE_JUNK_RALLY', 'Ratio': low_price_ratio}])
    return pd.DataFrame()

def check_etf_premium_discount(df: pd.DataFrame, nav_df: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(nav_df, on='Code')
    merged['Premium_Discount'] = (merged['Price'] - merged['Estimated_NAV']) / merged['Estimated_NAV']
    alert_mask = (merged['Premium_Discount'].abs() > 0.005) & (merged['Size_Rank'] <= 20)
    return merged[alert_mask].copy()

def check_whale_move(df: pd.DataFrame) -> pd.DataFrame:
    whale_stocks = ['2330', '2454', '2317']
    alert_mask = df['Code'].isin(whale_stocks) & (df['Return_Pct'].abs() > 3.0)
    return df[alert_mask].copy()

def check_active_etf_hype(df: pd.DataFrame) -> pd.DataFrame:
    etf_only = df[df['Category'] == 'ETF']
    if len(etf_only) == 0:
        return pd.DataFrame()
    vol_cutoff = etf_only['TradeVolume'].quantile(0.90)
    alert_mask = (
        etf_only['Code'].str.endswith(('A', 'D', 'T')) &
        (etf_only['TradeVolume'] >= vol_cutoff)
    )
    return etf_only[alert_mask].copy()


class SmartCheckerMixin:
    def _build_smart_alert_df(self) -> pd.DataFrame:
        rows = self.db.execute("""
            SELECT DISTINCT ON (r.stock_id)
                r.stock_id AS Code,
                r.price AS CurrentPrice,
                r.volume AS TradeVolume,
                r.change_pct AS Return_Pct,
                r.quote_time
            FROM realtime_quotes r
            ORDER BY r.stock_id, r.quote_time DESC
        """).fetchall()
        if not rows:
            return pd.DataFrame()
        columns = ['Code', 'CurrentPrice', 'TradeVolume', 'Return_Pct', 'quote_time']
        df = pd.DataFrame(rows, columns=columns)
        df['CurrentPrice'] = pd.to_numeric(df['CurrentPrice'], errors='coerce')
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce').fillna(0)
        df['Return_Pct'] = pd.to_numeric(df['Return_Pct'], errors='coerce').fillna(0.0)

        stock_rows = self.db.execute(
            "SELECT stock_id, industry, is_etf, stock_name FROM stocks"
        ).fetchall()
        stock_df = pd.DataFrame(stock_rows, columns=['stock_id', 'Industry', 'is_etf', 'Name'])
        stock_df['is_etf'] = stock_df['is_etf'].astype(bool)
        df = df.merge(stock_df, left_on='Code', right_on='stock_id', how='left')
        df['Industry'] = df['Industry'].fillna('Unknown')
        df['Name'] = df['Name'].fillna('')
        df['Category'] = df['is_etf'].map({True: 'ETF', False: 'Stock'})
        df['Category'] = df['Category'].fillna('Stock')

        df['TradeValue'] = df['CurrentPrice'] * df['TradeVolume']
        df['TradeValue'] = df['TradeValue'].fillna(0.0)
        df['PrevClose'] = df['CurrentPrice'] / (1 + df['Return_Pct'] / 100)
        df['PrevClose'] = df['PrevClose'].fillna(0.0)
        df['Price'] = df['CurrentPrice'].fillna(0.0)

        today = date.today()
        snap_rows = self.db.execute("""
            SELECT stock_id, MAX(price) AS high, MIN(price) AS low
            FROM intraday_snapshots
            WHERE snapshot_time::date = CURRENT_DATE
            GROUP BY stock_id
        """).fetchall()
        if snap_rows:
            snap_df = pd.DataFrame(snap_rows, columns=['stock_id', 'HighestPrice', 'LowestPrice'])
            df = df.merge(snap_df, on='stock_id', how='left')
        else:
            df['HighestPrice'] = None
            df['LowestPrice'] = None

        dp_rows = self.db.execute("""
            SELECT DISTINCT ON (stock_id)
                stock_id, high, low, volume
            FROM daily_prices
            ORDER BY stock_id, trade_date DESC
        """).fetchall()
        if dp_rows:
            dp_df = pd.DataFrame(dp_rows, columns=['stock_id', 'dp_high', 'dp_low', 'dp_volume'])
            df = df.merge(dp_df, on='stock_id', how='left')
            df['HighestPrice'] = df['HighestPrice'].fillna(df['dp_high'])
            df['LowestPrice'] = df['LowestPrice'].fillna(df['dp_low'])
        df['HighestPrice'] = pd.to_numeric(df['HighestPrice'], errors='coerce').fillna(df['CurrentPrice'])
        df['LowestPrice'] = pd.to_numeric(df['LowestPrice'], errors='coerce').fillna(df['CurrentPrice'])

        from sqlalchemy import text
        vol_rows = self.db.execute(
            text("""SELECT stock_id, AVG(volume) AS avg_vol
                     FROM (
                         SELECT stock_id, volume, trade_date,
                                ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn
                         FROM daily_prices
                         WHERE volume IS NOT NULL
                     ) sub WHERE rn <= 5
                     GROUP BY stock_id""")
        ).fetchall()
        if vol_rows:
            vol_df = pd.DataFrame(vol_rows, columns=['stock_id', 'Volume_5d_avg'])
            df = df.merge(vol_df, on='stock_id', how='left')
        df['Volume_5d_avg'] = pd.to_numeric(df['Volume_5d_avg'], errors='coerce').fillna(0.0)

        etf_df = df[df['Category'] == 'ETF'].copy()
        if not etf_df.empty:
            etf_df['Size_Rank'] = etf_df['TradeVolume'].rank(ascending=False, method='min')
            size_map = etf_df[['Code', 'Size_Rank']].set_index('Code')['Size_Rank'].to_dict()
            df['Size_Rank'] = df['Code'].map(size_map).fillna(999)

        df['Code'] = df['Code'].astype(str)
        df['Return_Pct'] = df['Return_Pct'].astype(float)
        df['TradeVolume'] = df['TradeVolume'].astype(float)
        df['TradeValue'] = df['TradeValue'].astype(float)
        return df

    def _get_market_context(self, df: pd.DataFrame) -> dict:
        total = len(df)
        if total == 0:
            return {'market_weak': False, 'market_high': False, 'nav_df': pd.DataFrame()}
        down_pct = (df['Return_Pct'] < 0).sum() / total
        up_pct = (df['Return_Pct'] > 0).sum() / total
        return {
            'market_weak': down_pct >= 0.70,
            'market_high': up_pct >= 0.60 and df['Return_Pct'].mean() > 0.5,
            'nav_df': pd.DataFrame(columns=['Code', 'Estimated_NAV']),
        }

    def check_all_smart_alerts(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now()
        t = now.time()
        if not (MARKET_OPEN <= t <= MARKET_CLOSE):
            return []
        if now.weekday() >= 5:
            return []

        df = self._build_smart_alert_df()
        if df.empty:
            return []

        triggered: list[dict] = []
        ctx = self._get_market_context(df)
        # Load smart alert templates from DB
        smart_rules = self.db.execute(
            "SELECT rule_name, message_template FROM alert_rules WHERE enabled = TRUE AND rule_name IN "
            "('VOLUME_SPIKE','HIGH_VOL_NO_MOVE','TURNOVER_MONSTER','INTRADAY_VOLATILITY','INDUSTRY_MOMENTUM',"
            "'AGAINST_TREND','LOW_PRICE_JUNK_RALLY','WHALE_MOVE','ACTIVE_ETF_HYPE','ETF_PREMIUM_DISCOUNT')"
        ).fetchall()
        smart_templates = {r[0]: r[1] for r in smart_rules}

        check_defs: list[tuple[str, str, pd.DataFrame]] = [
            ('VOLUME_SPIKE', 'LOW', check_volume_spike(df)),
            ('HIGH_VOL_NO_MOVE', 'MEDIUM', check_high_vol_no_move(df)),
            ('TURNOVER_MONSTER', 'MEDIUM', check_turnover_monster(df)),
            ('INTRADAY_VOLATILITY', 'HIGH', check_intraday_volatility(df)),
            ('INDUSTRY_MOMENTUM', 'LOW', check_industry_momentum(df)),
            ('AGAINST_TREND', 'MEDIUM', check_against_trend(df, ctx['market_weak'])),
            ('LOW_PRICE_JUNK_RALLY', 'CRITICAL', check_low_price_junk_rally(df, ctx['market_high'])),
            ('WHALE_MOVE', 'CRITICAL', check_whale_move(df)),
            ('ACTIVE_ETF_HYPE', 'LOW', check_active_etf_hype(df)),
        ]
        for rule_name, severity, result_df in check_defs:
            if result_df.empty:
                continue
            tmpl = smart_templates.get(rule_name)
            for _, row in result_df.iterrows():
                code = row.get('Code', row.get('Alert', 'N/A'))
                name = row.get('Name', '')
                default_msg = f'{rule_name}: {code}'
                vars_dict = row.to_dict()
                vars_dict.setdefault('stock_id', code)
                vars_dict.setdefault('stock_name', name)
                msg = self._format_alert_message(tmpl, default_msg, **vars_dict)
                self._log_history(rule_name, severity, msg, row.to_dict())
                triggered.append({
                    'alert_type': rule_name,
                    'severity': severity,
                    'stock_id': str(code),
                    'stock_name': str(name),
                    'message': msg,
                    'details': {k: v for k, v in row.items() if not isinstance(v, (pd.Timestamp, pd.NaT))},
                })
                if self._check_cooldown(rule_name, 1800):
                    full_msg = format_alert(severity, rule_name, msg)
                    self.manager.send_notification(f'[tw-quant-selector] {rule_name}', full_msg)

        etf_result = check_etf_premium_discount(df, ctx['nav_df'])
        if not etf_result.empty:
            etf_tmpl = smart_templates.get('ETF_PREMIUM_DISCOUNT')
            self._log_history('ETF_PREMIUM_DISCOUNT', 'MEDIUM',
                              f'ETF折溢价异常: {len(etf_result)} 档', etf_result.to_dict())
            for _, row in etf_result.iterrows():
                code = str(row.get('Code', 'N/A'))
                name = str(row.get('Name', ''))
                default_msg = f'ETF_PREMIUM_DISCOUNT: {code}'
                vars_dict = row.to_dict()
                vars_dict.setdefault('stock_id', code)
                vars_dict.setdefault('stock_name', name)
                msg = self._format_alert_message(etf_tmpl, default_msg, **vars_dict)
                triggered.append({
                    'alert_type': 'ETF_PREMIUM_DISCOUNT',
                    'severity': 'MEDIUM',
                    'stock_id': code,
                    'stock_name': name,
                    'message': msg,
                    'details': {k: v for k, v in row.items() if not isinstance(v, (pd.Timestamp, pd.NaT))},
                })
            if self._check_cooldown('ETF_PREMIUM_DISCOUNT', 1800):
                msg = format_alert('MEDIUM', 'ETF_PREMIUM_DISCOUNT',
                                   f'ETF折溢价异常 ({len(etf_result)} 档)')
                self.manager.send_notification('[tw-quant-selector] ETF_PREMIUM_DISCOUNT', msg)
        return triggered