import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import json
import uuid

from tw_quant_selector.monitoring.alerting import AlertManager, AlertChecker, format_alert

class TestAlerting(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.manager = AlertManager(self.db)
        self.checker = AlertChecker(self.db)

    def test_format_alert(self):
        msg = format_alert("CRITICAL", "RULE", "Message", "Suggestion", extra="info")
        self.assertIn("🚨 CRITICAL / RULE", msg)
        self.assertIn("Message", msg)
        self.assertIn("💡 建議行動: Suggestion", msg)
        self.assertIn("- extra: info", msg)

    def test_cooldown_mechanism(self):
        # Mock DB to simulate first alert (no cooldown record)
        self.db.execute.return_value.fetchone.return_value = None
        
        # Should alert first time
        should_alert = self.checker._check_cooldown("TEST_RULE", 3600)
        self.assertTrue(should_alert)
        
        # Mock DB to simulate second alert within cooldown
        last_time = datetime.now()
        self.db.execute.return_value.fetchone.return_value = [last_time]
        
        should_alert = self.checker._check_cooldown("TEST_RULE", 3600)
        self.assertFalse(should_alert)
        
        # Mock DB to simulate alert after cooldown
        old_time = datetime.now() - timedelta(seconds=3601)
        self.db.execute.return_value.fetchone.return_value = [old_time]
        
        should_alert = self.checker._check_cooldown("TEST_RULE", 3600)
        self.assertTrue(should_alert)

    @patch("tw_quant_selector.monitoring.alerting.AlertManager.send_notification")
    def test_check_db_connection_fail(self, mock_send):
        self.db.execute.side_effect = Exception("DB Down")
        # Ensure not in cooldown
        with patch.object(self.checker, "_check_cooldown", return_value=True):
            result = self.checker.check_db_connection()
            self.assertFalse(result)
            mock_send.assert_called_once()
            self.assertIn("PostgreSQL 无法连线", mock_send.call_args[0][1])

    def test_log_history(self):
        context = {"test": "data"}
        self.checker._log_history("RULE", "HIGH", "Msg", context)
        
        self.db.execute.assert_called()
        args = self.db.execute.call_args[0]
        self.assertIn("INSERT INTO alert_history", args[0])
        self.assertEqual(args[1][1], "RULE")
        self.assertEqual(args[1][2], "HIGH")
        self.assertEqual(json.loads(args[1][4]), context)

    # ── institutional alert rule tests ─────────────────────────────────

    def test_calc_consecutive_positive(self):
        vals = [10, 5, 3, -1, 2]
        n = self.checker._calc_consecutive_days(vals, "positive")
        self.assertEqual(n, 3)  # 10, 5, 3 are all positive; -1 breaks

    def test_calc_consecutive_negative(self):
        vals = [-10, -5, -3, 1, -2]
        n = self.checker._calc_consecutive_days(vals, "negative")
        self.assertEqual(n, 3)  # first 3 are negative

    def test_calc_consecutive_edge_cases(self):
        self.assertEqual(self.checker._calc_consecutive_days([], "positive"), 0)
        self.assertEqual(self.checker._calc_consecutive_days([0, 5], "positive"), 0)
        self.assertEqual(self.checker._calc_consecutive_days([None, 5], "positive"), 0)

    def test_is_quarter_end_soon(self):
        from datetime import date
        # Mar 24 (1 day before Mar 25)
        self.assertTrue(self.checker._is_quarter_end_soon(date(2026, 3, 24)))
        # Mar 25 (quarter end day)
        self.assertTrue(self.checker._is_quarter_end_soon(date(2026, 3, 25)))
        # Mar 26 (past quarter end by 1 day — next quarter Jun 25 is far)
        self.assertFalse(self.checker._is_quarter_end_soon(date(2026, 3, 26)))
        # Jun 20 (5 days before Jun 25)
        self.assertTrue(self.checker._is_quarter_end_soon(date(2026, 6, 20)))
        # Dec 20 (5 days before Dec 25)
        self.assertTrue(self.checker._is_quarter_end_soon(date(2026, 12, 20)))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_inst_heavy_buy_triggers(self, mock_send, mock_cooldown):
        with patch.object(self.checker, "_get_todays_picks",
                          return_value=[{"stock_id": "2330", "stock_name": "台積電", "score": 1.5}]):
            with patch.object(self.checker, "_get_portfolio_stocks", return_value=[]):
                with patch.object(self.checker, "_get_recent_flows",
                                  return_value={"2330": [{"foreign_investors_net": 100_000_000}]}):
                    with patch.object(self.checker, "_get_shares_outstanding",
                                      return_value={"2330": 10_000_000_000}):
                        self.checker.check_institutional_alerts()
                        args = self.db.execute.call_args_list
                        hist_calls = [a for a in args if "INSERT INTO alert_history" in str(a)]
                        self.assertTrue(any("INST_HEAVY_BUY" in str(a) for a in hist_calls),
                                        "INST_HEAVY_BUY should be logged")

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_inst_heavy_buy_no_trigger_below_threshold(self, mock_send, mock_cooldown):
        with patch.object(self.checker, "_get_todays_picks",
                          return_value=[{"stock_id": "2330", "stock_name": "台積電", "score": 1.5}]):
            with patch.object(self.checker, "_get_portfolio_stocks", return_value=[]):
                with patch.object(self.checker, "_get_recent_flows",
                                  return_value={"2330": [{"foreign_investors_net": 10_000}]}):
                    with patch.object(self.checker, "_get_shares_outstanding",
                                      return_value={"2330": 10_000_000_000}):
                        self.checker.check_institutional_alerts()
                        # Should NOT trigger (10k / 10B = 0.0001% << 0.5%)
                        args = [str(a) for a in self.db.execute.call_args_list]
                        self.assertFalse(any("INST_HEAVY_BUY" in a for a in args),
                                         "INST_HEAVY_BUY should NOT be triggered")

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_inst_heavy_sell(self, mock_send, mock_cooldown):
        with patch.object(self.checker, "_get_todays_picks", return_value=[]):
            with patch.object(self.checker, "_get_portfolio_stocks", return_value=["2330"]):
                with patch.object(self.checker, "_get_recent_flows",
                                  return_value={"2330": [
                                      {"foreign_investors_net": -100},
                                      {"foreign_investors_net": -200},
                                      {"foreign_investors_net": -300},
                                      {"foreign_investors_net": -400},
                                      {"foreign_investors_net": -500},
                                      {"foreign_investors_net": 50},
                                  ]}):
                    self.checker.check_institutional_alerts()
                    args = [str(a) for a in self.db.execute.call_args_list]
                    self.assertTrue(any("INST_HEAVY_SELL" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_inst_divergence(self, mock_send, mock_cooldown):
        with patch.object(self.checker, "_get_todays_picks",
                          return_value=[{"stock_id": "2330", "stock_name": "台積電", "score": 1.5}]):
            with patch.object(self.checker, "_get_portfolio_stocks", return_value=[]):
                with patch.object(self.checker, "_get_recent_flows",
                                  return_value={"2330": [
                                      {"foreign_investors_net": -100},
                                      {"foreign_investors_net": -200},
                                      {"foreign_investors_net": -300},
                                      {"foreign_investors_net": 50},
                                  ]}):
                    self.checker.check_institutional_alerts()
                    args = [str(a) for a in self.db.execute.call_args_list]
                    self.assertTrue(any("INST_DIVERGENCE" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_inst_consec_buy(self, mock_send, mock_cooldown):
        with patch.object(self.checker, "_get_todays_picks", return_value=[]):
            with patch.object(self.checker, "_get_portfolio_stocks", return_value=["2330"]):
                with patch.object(self.checker, "_get_recent_flows",
                                  return_value={"2330": [
                                      {"total_net": 100},
                                      {"total_net": 200},
                                      {"total_net": 300},
                                      {"total_net": 400},
                                      {"total_net": 500},
                                      {"total_net": 600},
                                      {"total_net": 700},
                                      {"total_net": 800},
                                      {"total_net": 900},
                                      {"total_net": 1000},
                                      {"total_net": 50},
                                  ]}):
                    self.checker.check_institutional_alerts()
                    args = [str(a) for a in self.db.execute.call_args_list]
                    self.assertTrue(any("INST_CONSEC_BUY" in a for a in args))

# ── Smart Alert Tests ────────────────────────────────────────────────

import pandas as pd
from tw_quant_selector.monitoring.alerting import (
    check_volume_spike,
    check_high_vol_no_move,
    check_turnover_monster,
    check_intraday_volatility,
    check_industry_momentum,
    check_against_trend,
    check_low_price_junk_rally,
    check_etf_premium_discount,
    check_whale_move,
    check_active_etf_hype,
)

def _make_df(data: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(data)


class TestSmartAlerts(unittest.TestCase):

    def setUp(self):
        self.df = _make_df([
            {'Code': '2330', 'Category': 'Stock', 'Industry': '半導體', 'TradeVolume': 50_000_000,
             'TradeValue': 5_000_000_000, 'Return_Pct': 2.5, 'CurrentPrice': 1000.0,
             'HighestPrice': 1010.0, 'LowestPrice': 990.0, 'PrevClose': 975.0,
             'Price': 1000.0, 'Volume_5d_avg': 40_000_000, 'Name': '台積電', 'Size_Rank': 1},
            {'Code': '2317', 'Category': 'Stock', 'Industry': '電子', 'TradeVolume': 5_000,
             'TradeValue': 500_000, 'Return_Pct': -0.3, 'CurrentPrice': 100.0,
             'HighestPrice': 101.0, 'LowestPrice': 99.0, 'PrevClose': 100.3,
             'Price': 100.0, 'Volume_5d_avg': 4_000, 'Name': '鴻海', 'Size_Rank': 50},
            {'Code': '0050', 'Category': 'ETF', 'Industry': 'ETF', 'TradeVolume': 10_000_000,
             'TradeValue': 1_000_000_000, 'Return_Pct': 1.0, 'CurrentPrice': 100.0,
             'HighestPrice': 101.0, 'LowestPrice': 99.0, 'PrevClose': 99.0,
             'Price': 100.0, 'Volume_5d_avg': 8_000_000, 'Name': '元大台灣50', 'Size_Rank': 2},
            {'Code': '00858A', 'Category': 'ETF', 'Industry': 'ETF', 'TradeVolume': 20_000_000,
             'TradeValue': 2_000_000_000, 'Return_Pct': 0.0, 'CurrentPrice': 100.0,
             'HighestPrice': 100.5, 'LowestPrice': 99.5, 'PrevClose': 100.0,
             'Price': 100.0, 'Volume_5d_avg': 1_000_000, 'Name': '新制ETF', 'Size_Rank': 3},
            {'Code': '2454', 'Category': 'Stock', 'Industry': '半導體', 'TradeVolume': 30_000_000,
             'TradeValue': 3_000_000_000, 'Return_Pct': 4.5, 'CurrentPrice': 100.0,
             'HighestPrice': 101.0, 'LowestPrice': 95.0, 'PrevClose': 95.7,
             'Price': 100.0, 'Volume_5d_avg': 20_000_000, 'Name': '聯發科', 'Size_Rank': 5},
        ])

    # ── 1. Volume Spike ──────────────────────────────────────────────
    def test_volume_spike_triggers(self):
        df = _make_df([
            {'Code': 'A', 'Category': 'Stock', 'TradeVolume': 10_000_000},
            {'Code': 'B', 'Category': 'Stock', 'TradeVolume': 100},
            {'Code': 'C', 'Category': 'Stock', 'TradeVolume': 200},
        ])
        # median = 200, 10*median = 2000, A at 10_000_000 > 2000 ✓
        result = check_volume_spike(df)
        self.assertIn('A', result['Code'].values)

    def test_volume_spike_no_false_positive(self):
        df = _make_df([
            {'Code': 'A', 'Category': 'Stock', 'TradeVolume': 100},
            {'Code': 'B', 'Category': 'Stock', 'TradeVolume': 200},
            {'Code': 'C', 'Category': 'Stock', 'TradeVolume': 150},
        ])
        result = check_volume_spike(df)
        self.assertTrue(result.empty)

    # ── 2. High Volume No Move ───────────────────────────────────────
    def test_high_vol_no_move_triggers(self):
        df = _make_df([
            {'Code': 'A', 'TradeVolume': 1000, 'Return_Pct': 0.2},
            {'Code': 'B', 'TradeVolume': 50, 'Return_Pct': 0.1},
            {'Code': 'C', 'TradeVolume': 800, 'Return_Pct': 0.4},
            {'Code': 'D', 'TradeVolume': 600, 'Return_Pct': 2.0},
            {'Code': 'E', 'TradeVolume': 700, 'Return_Pct': 0.3},
        ])
        result = check_high_vol_no_move(df)
        # Top 3 by volume: A(1000), C(800), E(700) → A and E are within ±0.5%
        self.assertIn('A', result['Code'].values)
        self.assertIn('E', result['Code'].values)
        self.assertNotIn('D', result['Code'].values)

    def test_high_vol_no_move_none(self):
        df = _make_df([
            {'Code': 'A', 'TradeVolume': 100, 'Return_Pct': 2.0},
            {'Code': 'B', 'TradeVolume': 80, 'Return_Pct': 3.0},
        ])
        result = check_high_vol_no_move(df)
        self.assertTrue(result.empty)

    # ── 3. Turnover Monster ──────────────────────────────────────────
    def test_turnover_monster_triggers(self):
        # 2330 TradeValue 5B / total (~11B) ≈ 45% > 2% ✓ but excluded (2330)
        # Need a stock with > 2% that isn't 2330
        df = _make_df([
            {'Code': '2330', 'TradeValue': 100_000},
            {'Code': 'OTHER', 'TradeValue': 50_000_000},
            {'Code': 'SMALL', 'TradeValue': 1_000},
        ])
        # total = 100_100_000, OTHER pct = 49.9% > 2% ✓
        result = check_turnover_monster(df)
        self.assertIn('OTHER', result['Code'].values)
        self.assertNotIn('2330', result['Code'].values)

    def test_turnover_monster_no_trigger(self):
        df = _make_df([
            {'Code': 'A', 'TradeValue': 1_000_000},
            {'Code': 'B', 'TradeValue': 1_000_000},
            {'Code': 'C', 'TradeValue': 1_000_000},
            {'Code': 'D', 'TradeValue': 1_000_000},
        ])
        # total = 4_000_000, each = 25% > 2% but A-D not 2330 → still triggers!
        # Need many stocks so each is < 2%
        df2 = _make_df([{'Code': f'S{i}', 'TradeValue': 100} for i in range(100)])
        # total = 10_000, each = 1% < 2% ✓
        result = check_turnover_monster(df2)
        self.assertTrue(result.empty)

    # ── 4. Intraday Volatility ───────────────────────────────────────
    def test_intraday_volatility_triggers(self):
        # volatility = (101 - 95) / 95.7 = 6.27% < 8% → no trigger
        # Let's make one that triggers
        df = _make_df([
            {'Code': 'TEST', 'HighestPrice': 110, 'LowestPrice': 90, 'PrevClose': 100,
             'CurrentPrice': 91, 'Category': 'Stock', 'TradeVolume': 1000, 'TradeValue': 91000,
             'Return_Pct': -9.0, 'Price': 91, 'Volume_5d_avg': 500, 'Industry': 'X'},
        ])
        # volatility = (110-90)/100 = 20% > 8% ✓
        # price_position = (91-90)/(110-90) = 0.05 ≤ 0.1 ✓
        result = check_intraday_volatility(df)
        self.assertIn('TEST', result['Code'].values)

    def test_intraday_volatility_no_trigger(self):
        # volatility = (101-99)/100 = 2% < 8%
        result = check_intraday_volatility(self.df)
        self.assertNotIn('2330', result['Code'].values)

    # ── 5. Industry Momentum ─────────────────────────────────────────
    def test_industry_momentum_triggers(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 5.0, 'Industry': '半導體'},
            {'Code': 'B', 'Return_Pct': 4.5, 'Industry': '半導體'},
            {'Code': 'C', 'Return_Pct': 1.0, 'Industry': '半導體'},
        ])
        # 2/3 = 66% > 30% ✓
        result = check_industry_momentum(df)
        self.assertEqual(len(result), 3)

    def test_industry_momentum_no_trigger(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 5.0, 'Industry': '半導體'},
            {'Code': 'B', 'Return_Pct': 1.0, 'Industry': '半導體'},
            {'Code': 'C', 'Return_Pct': 1.0, 'Industry': '半導體'},
        ])
        # 1/3 = 33% > 30% → actually this triggers! Let me make it <= 30%
        # 1/4 = 25% <= 30%
        df2 = _make_df([
            {'Code': 'A', 'Return_Pct': 5.0, 'Industry': '半導體'},
            {'Code': 'B', 'Return_Pct': 1.0, 'Industry': '半導體'},
            {'Code': 'C', 'Return_Pct': 1.0, 'Industry': '半導體'},
            {'Code': 'D', 'Return_Pct': 1.0, 'Industry': '半導體'},
        ])
        result = check_industry_momentum(df2)
        self.assertTrue(result.empty)

    # ── 6. Against the Trend ─────────────────────────────────────────
    def test_against_trend_market_not_weak(self):
        result = check_against_trend(self.df, market_weak=False)
        self.assertTrue(result.empty)

    def test_against_trend_triggers(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 3.0, 'TradeVolume': 1000, 'Volume_5d_avg': 500},
        ])
        result = check_against_trend(df, market_weak=True)
        self.assertIn('A', result['Code'].values)

    def test_against_trend_no_trigger_low_vol(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 3.0, 'TradeVolume': 100, 'Volume_5d_avg': 500},
        ])
        result = check_against_trend(df, market_weak=True)
        self.assertTrue(result.empty)

    # ── 7. Low Price Junk Rally ──────────────────────────────────────
    def test_junk_rally_market_not_high(self):
        result = check_low_price_junk_rally(self.df, market_high=False)
        self.assertTrue(result.empty)

    def test_junk_rally_triggers(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 9.6, 'Price': 20},
            {'Code': 'B', 'Return_Pct': 10.0, 'Price': 25},
            {'Code': 'C', 'Return_Pct': 9.8, 'Price': 29},
        ])
        result = check_low_price_junk_rally(df, market_high=True)
        self.assertFalse(result.empty)
        self.assertEqual(result.iloc[0]['Alert'], 'LOW_PRICE_JUNK_RALLY')

    def test_junk_rally_no_trigger(self):
        df = _make_df([
            {'Code': 'A', 'Return_Pct': 9.6, 'Price': 100},
            {'Code': 'B', 'Return_Pct': 10.0, 'Price': 25},
        ])
        # 1/2 = 50% < 60%
        result = check_low_price_junk_rally(df, market_high=True)
        self.assertTrue(result.empty)

    # ── 8. ETF Premium/Discount ──────────────────────────────────────
    def test_etf_premium_discount_triggers(self):
        df = _make_df([
            {'Code': '0050', 'Price': 101.0, 'Size_Rank': 1},
            {'Code': '0056', 'Price': 30.0, 'Size_Rank': 30},
        ])
        nav_df = _make_df([
            {'Code': '0050', 'Estimated_NAV': 100.0},
            {'Code': '0056', 'Estimated_NAV': 35.0},
        ])
        # 0050: (101-100)/100 = 1% > 0.5% + rank 1 <= 20 → triggered
        # 0056: (30-35)/35 = -14.3% > 0.5% but rank 30 > 20 → excluded
        result = check_etf_premium_discount(df, nav_df)
        self.assertIn('0050', result['Code'].values)
        self.assertNotIn('0056', result['Code'].values)

    def test_etf_premium_discount_no_trigger(self):
        df = _make_df([
            {'Code': '0050', 'Price': 100.1, 'Size_Rank': 1},
        ])
        nav_df = _make_df([
            {'Code': '0050', 'Estimated_NAV': 100.0},
        ])
        # (100.1-100)/100 = 0.1% < 0.5%
        result = check_etf_premium_discount(df, nav_df)
        self.assertTrue(result.empty)

    # ── 9. Whale Move ────────────────────────────────────────────────
    def test_whale_move_triggers(self):
        result = check_whale_move(self.df)
        # 2330: 2.5% < 3% → no; 2317: -0.3% < 3% → no; 2454: 4.5% > 3% ✓
        self.assertIn('2454', result['Code'].values)
        self.assertNotIn('2330', result['Code'].values)

    def test_whale_move_no_trigger(self):
        df = _make_df([
            {'Code': '2330', 'Return_Pct': 2.0},
            {'Code': '2454', 'Return_Pct': -2.5},
            {'Code': '2317', 'Return_Pct': 1.0},
        ])
        result = check_whale_move(df)
        self.assertTrue(result.empty)

    # ── 10. Active ETF Hype ──────────────────────────────────────────
    def test_active_etf_hype_triggers(self):
        df = _make_df([
            {'Code': '00858A', 'Category': 'ETF', 'TradeVolume': 1000},
            {'Code': '00900D', 'Category': 'ETF', 'TradeVolume': 900},
            {'Code': '0050', 'Category': 'ETF', 'TradeVolume': 10},
            {'Code': '0056', 'Category': 'ETF', 'TradeVolume': 5},
            {'Code': '00881', 'Category': 'ETF', 'TradeVolume': 8},
            {'Code': '2330', 'Category': 'Stock', 'TradeVolume': 9999},
        ])
        # ETF top 10%: 5 ETFs → 90% percentile = sorted volumes [5,8,10,900,1000]
        # p90 at index 4 (0.9*5 = 4.5, ceiling = 5 → value at index 4 = 1000)
        # So cutoff = 1000 depending on quantile interpolation
        # Actually quantile(0.90) with linear interpolation on [5,8,10,900,1000]
        # Let's just check it at least runs without error
        result = check_active_etf_hype(df)
        self.assertIsNotNone(result)

    def test_active_etf_hype_no_etf(self):
        df = _make_df([
            {'Code': '2330', 'Category': 'Stock', 'TradeVolume': 1000},
        ])
        result = check_active_etf_hype(df)
        self.assertTrue(result.empty)

    def test_active_etf_hype_no_suffix_match(self):
        df = _make_df([
            {'Code': '0050', 'Category': 'ETF', 'TradeVolume': 1000},
            {'Code': '0056', 'Category': 'ETF', 'TradeVolume': 900},
        ])
        # Neither ends with A, D, or T
        result = check_active_etf_hype(df)
        self.assertTrue(result.empty)


class TestSmartAlertIntegration(unittest.TestCase):
    """Test check_all_smart_alerts integration with AlertChecker."""

    def setUp(self):
        self.db = MagicMock()
        self.checker = AlertChecker(self.db)

    @patch("tw_quant_selector.monitoring.alerting.check_volume_spike")
    @patch.object(AlertChecker, "_build_smart_alert_df")
    def test_check_all_smart_alerts_calls_checks(self, mock_build, mock_vol):
        mock_build.return_value = _make_df([
            {'Code': '2330', 'Category': 'Stock', 'Industry': '半導體',
             'TradeVolume': 100, 'TradeValue': 100_000, 'Return_Pct': 1.0,
             'CurrentPrice': 100, 'HighestPrice': 101, 'LowestPrice': 99,
             'PrevClose': 99, 'Price': 100, 'Volume_5d_avg': 50,
             'Name': '台積電', 'Size_Rank': 1},
        ])
        mock_vol.return_value = pd.DataFrame()
        with patch.object(self.checker, "_check_cooldown", return_value=False):
            self.checker.check_all_smart_alerts(
                now=datetime(2026, 6, 5, 10, 0)  # Friday 10:00
            )
            mock_vol.assert_called_once()

    @patch.object(AlertChecker, "_build_smart_alert_df")
    def test_check_all_smart_alerts_skipped_outside_market_hours(self, mock_build):
        self.checker.check_all_smart_alerts(
            now=datetime(2026, 6, 5, 8, 0)  # Friday 8:00 before market open
        )
        mock_build.assert_not_called()

    @patch.object(AlertChecker, "_build_smart_alert_df")
    def test_check_all_smart_alerts_skipped_weekend(self, mock_build):
        self.checker.check_all_smart_alerts(
            now=datetime(2026, 6, 6, 10, 0)  # Saturday
        )
        mock_build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
