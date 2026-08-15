"""Tests for intraday technical alerts (T132/T133):
compute_sma / compute_kd / build_intraday_kline / check_technical_alerts."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from tw_quant_selector.monitoring.alerting import AlertChecker, AlertManager
from tw_quant_selector.monitoring.indicators import compute_kd, compute_sma
from tw_quant_selector.data.realtime_quotes import build_intraday_kline


class TestComputeSma(unittest.TestCase):
    def test_basic(self):
        vals = [1, 2, 3, 4, 5]
        out = compute_sma(vals, 3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertEqual(out[2], 2.0)  # (1+2+3)/3
        self.assertEqual(out[3], 3.0)
        self.assertEqual(out[4], 4.0)

    def test_period_larger_than_length(self):
        out = compute_sma([1, 2], 3)
        self.assertEqual(out, [None, None])

    def test_period_one(self):
        self.assertEqual(compute_sma([5, 7], 1), [5.0, 7.0])

    def test_empty_and_zero_period(self):
        self.assertEqual(compute_sma([], 3), [])
        self.assertEqual(compute_sma([1, 2], 0), [None, None])


class TestComputeKd(unittest.TestCase):
    def test_uptrend_k_approaches_100(self):
        highs = [10, 11, 12, 13, 14]
        lows = [9, 10, 11, 12, 13]
        closes = [10, 11, 12, 13, 14]
        rsv, k, d = compute_kd(highs, lows, closes, n=3, k1=3, d1=3)
        self.assertIsNone(rsv[0])
        self.assertIsNone(rsv[1])
        self.assertAlmostEqual(rsv[2], 100.0)
        self.assertAlmostEqual(k[4], 100.0)
        self.assertAlmostEqual(d[4], 100.0)

    def test_downtrend_k_approaches_0(self):
        highs = [14, 13, 12, 11, 10]
        lows = [13, 12, 11, 10, 9]
        closes = [13, 12, 11, 10, 9]
        rsv, k, d = compute_kd(highs, lows, closes, n=3)
        self.assertAlmostEqual(rsv[2], 0.0)
        self.assertAlmostEqual(k[4], 0.0)
        self.assertAlmostEqual(d[4], 0.0)

    def test_flat_range_rsv_50(self):
        highs = [10, 10, 10]
        lows = [10, 10, 10]
        closes = [10, 10, 10]
        rsv, k, d = compute_kd(highs, lows, closes, n=3)
        self.assertEqual(rsv[2], 50.0)
        self.assertEqual(k[2], 50.0)

    def test_insufficient_data(self):
        rsv, k, d = compute_kd([1], [1], [1], n=3)
        self.assertEqual(rsv, [None])
        self.assertEqual(k, [None])
        self.assertEqual(d, [None])


class TestBuildIntradayKline(unittest.TestCase):
    def test_market_closed_skips(self):
        with patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=False):
            result = build_intraday_kline(["2330"])
        self.assertEqual(result["status"], "skipped")

    @patch("tw_quant_selector.data.database.get_session")
    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=True)
    def test_aggregates_ohlc(self, mock_open, mock_get_session):
        session = mock_get_session.return_value
        # (price, volume, open_price, high_price, low_price)
        rows = [
            (100.0, 1000, 100.0, 101.0, 99.0),
            (102.0, 5000, 100.0, 102.0, 100.0),
            (103.0, 8000, 100.0, 103.0, 101.0),
        ]
        session.execute.return_value.fetchall.return_value = rows

        result = build_intraday_kline(["2330"])

        self.assertEqual(result["status"], "ok")
        kline_calls = [c for c in session.execute.call_args_list if "INSERT INTO intraday_kline" in str(c)]
        self.assertEqual(len(kline_calls), 5)  # 09:00 ~ 13:00 completed hours
        args = kline_calls[-1].args
        self.assertEqual(args[1][0], "2330")
        self.assertEqual(args[1][5], 103.0)  # close = last price
        self.assertEqual(args[1][6], 7000)   # volume = vol_end - vol_start
        session.commit.assert_called()

    @patch("tw_quant_selector.data.database.get_session")
    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=True)
    def test_skips_hour_without_quotes(self, mock_open, mock_get_session):
        session = mock_get_session.return_value
        session.execute.return_value.fetchall.return_value = []

        result = build_intraday_kline(["0050"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 0)


class TestCheckTechnicalAlerts(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.checker = AlertChecker(self.db)
        self.now = datetime(2026, 6, 5, 10, 0)  # Friday 10:00
        self._send_patch = patch.object(AlertManager, "send_notification")
        self.mock_send = self._send_patch.start()
        self.addCleanup(self._send_patch.stop)

    def _set_side_effects(self, rules, stocks, *kline_lists):
        kline_mocks = [kline_lists[i] if i < len(kline_lists) else [] for i in range(len(stocks))]

        def _execute(sql, params=None):
            mock = MagicMock()
            text = str(sql)
            if "FROM alert_rules" in text:
                mock.fetchall.return_value = rules
            elif "SELECT DISTINCT stock_id" in text:
                mock.fetchall.return_value = stocks
            elif "FROM intraday_kline" in text:
                if kline_mocks:
                    mock.fetchall.return_value = kline_mocks.pop(0)
            return mock

        self.db.execute.side_effect = _execute

    def _rule(self, name, threshold, enabled=True, cooldown=3600, severity="MEDIUM",
              config=None, template=None):
        return (name, enabled, threshold, cooldown, severity, config or "", template)

    def _kline_row(self, open_, high, low, close, hour=9):
        return (datetime(2026, 6, 5, hour, 0), open_, high, low, close, 0)

    def test_skipped_outside_market_hours(self):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0)],
            [("2330",)],
            [self._kline_row(100, 100, 100, 110)],
        )
        result = self.checker.check_technical_alerts(now=datetime(2026, 6, 5, 8, 0))
        self.assertEqual(result, [])

    def test_skipped_weekend(self):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0)],
            [("2330",)],
            [self._kline_row(100, 100, 100, 110)],
        )
        result = self.checker.check_technical_alerts(now=datetime(2026, 6, 6, 10, 0))  # Saturday
        self.assertEqual(result, [])

    def test_no_rules_returns_empty(self):
        self._set_side_effects([], [("2330",)])
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(result, [])

    def test_no_kline_stocks_returns_empty(self):
        self._set_side_effects([self._rule("TECH_MA_CROSS", 0)], [])
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(result, [])

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    def test_ma_cross_above_triggers(self, mock_cooldown):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0, config='{"period": 60, "direction": "above"}')],
            [("2330",)],
            [self._kline_row(100, 100, 100, 100), self._kline_row(102, 102, 102, 102),
             self._kline_row(110, 110, 110, 110)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # closes [100,102,110], sma(3)=106, 110 >= 106*1.0 ✓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_MA_CROSS")
        self.assertEqual(result[0]["stock_id"], "2330")

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    def test_ma_cross_below_triggers(self, mock_cooldown):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0, config='{"direction": "below"}')],
            [("2330",)],
            [self._kline_row(110, 110, 110, 110), self._kline_row(108, 108, 108, 108),
             self._kline_row(90, 90, 90, 90)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # closes [110,108,90], sma(3) ≈ 102.7, 90 <= 102.7*1.0 ✓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_MA_CROSS")

    def test_ma_cross_not_triggered_when_close_on_sma(self):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0, config='{"period": 60, "direction": "above"}')],
            [("2330",)],
            [self._kline_row(100, 100, 100, 100), self._kline_row(101, 101, 101, 101),
             self._kline_row(102, 102, 102, 102)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # closes [100,101,102], sma(3)=101, 102 >= 101 ✓ triggers actually
        self.assertEqual(len(result), 1)

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    def test_kd_cross_triggers(self, mock_cooldown):
        self._set_side_effects(
            [self._rule("TECH_KD_CROSS", 50)],
            [("2330",)],
            [self._kline_row(10, 10, 10, 10), self._kline_row(11, 11, 11, 11),
             self._kline_row(12, 12, 12, 12)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # uptrend → K = 100 >= 50 ✓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_KD_CROSS")
        self.assertEqual(result[0]["details"]["k"], 100.0)

    def test_kd_cross_not_triggered(self):
        self._set_side_effects(
            [self._rule("TECH_KD_CROSS", 50)],
            [("2330",)],
            [self._kline_row(12, 12, 12, 12), self._kline_row(11, 11, 11, 11),
             self._kline_row(10, 10, 10, 10)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # downtrend → K = 0 < 50
        self.assertEqual(result, [])

    def test_index_ma_cross_triggers(self):
        self._set_side_effects(
            [self._rule("TECH_INDEX_MA", 0, config='{"period": 20, "direction": "above"}')],
            [("^TWII",)],
            [self._kline_row(20000, 20000, 20000, 20000), self._kline_row(20200, 20200, 20200, 20200),
             self._kline_row(21000, 21000, 21000, 21000)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_INDEX_MA")
        self.assertEqual(result[0]["stock_id"], "^TWII")
        self.assertEqual(result[0]["stock_name"], "加權指數")

    def test_index_kd_overbought(self):
        self._set_side_effects(
            [self._rule("TECH_INDEX_KD", 80, config='{"kd_n": 9, "zone": "overbought"}')],
            [("^TWII",)],
            [self._kline_row(100, 100, 100, 100), self._kline_row(110, 110, 110, 110),
             self._kline_row(120, 120, 120, 120)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # uptrend → K = 100 >= 80 ✓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_INDEX_KD")

    def test_index_kd_oversold(self):
        self._set_side_effects(
            [self._rule("TECH_INDEX_KD", 80, config='{"kd_n": 9, "zone": "oversold"}')],
            [("^TWII",)],
            [self._kline_row(120, 120, 120, 120), self._kline_row(110, 110, 110, 110),
             self._kline_row(100, 100, 100, 100)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        # downtrend → K = 0 <= 20 ✓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert_type"], "TECH_INDEX_KD")

    def test_regular_stock_never_triggers_index_rules(self):
        self._set_side_effects(
            [self._rule("TECH_INDEX_MA", 0), self._rule("TECH_INDEX_KD", 80)],
            [("2330",)],
            [self._kline_row(10, 10, 10, 10), self._kline_row(11, 11, 11, 11),
             self._kline_row(12, 12, 12, 12)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(result, [])

    def test_multiple_stocks_all_processed(self):
        # Regression test: the per-stock body used to sit outside the for loop.
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0)],
            [("2330",), ("2317",)],
            [self._kline_row(100, 100, 100, 110), self._kline_row(110, 110, 110, 112)],
            [self._kline_row(50, 50, 50, 55), self._kline_row(55, 55, 55, 57)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        stocks = {r["stock_id"] for r in result}
        self.assertEqual(stocks, {"2330", "2317"})

    @patch.object(AlertChecker, "_check_cooldown", return_value=False)
    def test_cooldown_blocks_notification_but_still_logs(self, mock_cooldown):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0)],
            [("2330",)],
            [self._kline_row(100, 100, 100, 110), self._kline_row(100, 100, 100, 100)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(len(result), 1)
        self.mock_send.assert_not_called()

    def test_message_template_used(self):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0, template="{stock_name}（{stock_id}）{direction}穿 {period}MA")],
            [("2330",)],
            [self._kline_row(100, 100, 100, 110), self._kline_row(100, 100, 100, 100)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["message"], "（2330）上穿 60MA")

    def test_invalid_config_json_falls_back_to_defaults(self):
        with patch.object(AlertChecker, "_check_cooldown", return_value=True):
            self._set_side_effects(
                [self._rule("TECH_MA_CROSS", 0, config="not-json")],
                [("2330",)],
                [self._kline_row(100, 100, 100, 110), self._kline_row(100, 100, 100, 100)],
            )
            result = self.checker.check_technical_alerts(now=self.now)
            self.assertEqual(len(result), 1)

    def test_short_kline_series_skipped(self):
        self._set_side_effects(
            [self._rule("TECH_MA_CROSS", 0)],
            [("2330",)],
            [self._kline_row(100, 100, 100, 100)],
        )
        result = self.checker.check_technical_alerts(now=self.now)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()