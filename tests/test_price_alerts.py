import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import json

from tw_quant_selector.monitoring.alerting import AlertChecker, AlertManager


MARKET_TIME = datetime(2026, 6, 4, 10, 30, 0)  # Thu, market hours


class TestPriceAlerts(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.checker = AlertChecker(self.db)

    def _patch_deps(self, **overrides):
        defaults = dict(
            _get_todays_picks=MagicMock(return_value=[]),
            _get_portfolio_stocks=MagicMock(return_value=["2330"]),
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 895.0, "volume": 50_000_000, "change_pct": 1.5, "quote_time": MARKET_TIME},
            }),
        )
        defaults.update(overrides)
        return patch.multiple(self.checker, **defaults)

    def _call(self):
        self.checker.check_price_alerts(now=MARKET_TIME)

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_limit_up_triggers(self, mock_send, mock_cooldown):
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 990.0, "volume": 10_000_000, "change_pct": 10.0, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertTrue(any("PRICE_LIMIT_UP" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_limit_down_triggers(self, mock_send, mock_cooldown):
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 810.0, "volume": 20_000_000, "change_pct": -10.0, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertTrue(any("PRICE_LIMIT_DOWN" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_limit_up_no_trigger_normal(self, mock_send, mock_cooldown):
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 920.0, "volume": 10_000_000, "change_pct": 2.5, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertFalse(any("PRICE_LIMIT_UP" in a for a in args))
            self.assertFalse(any("PRICE_LIMIT_DOWN" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_unusual_volume_triggers(self, mock_send, mock_cooldown):
        self.db.execute.return_value.fetchone.return_value = [1_000_000]
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 895.0, "volume": 5_000_000, "change_pct": 1.5, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertTrue(any("PRICE_UNUSUAL_VOLUME" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_unusual_volume_no_trigger(self, mock_send, mock_cooldown):
        self.db.execute.return_value.fetchone.return_value = [1_000_000]
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 895.0, "volume": 2_000_000, "change_pct": 1.5, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertFalse(any("PRICE_UNUSUAL_VOLUME" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_pe_extreme_triggers(self, mock_send, mock_cooldown):
        self.db.execute.return_value.fetchall.return_value = [
            [10.0], [11.0], [12.0], [13.0], [14.0], [15.0], [16.0], [17.0], [18.0], [19.0],
            [20.0], [21.0], [22.0], [23.0], [24.0], [25.0], [26.0], [27.0], [28.0], [29.0],
            [30.0],
        ]
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 5000.0, "volume": 10_000_000, "change_pct": 5.0, "quote_time": MARKET_TIME},
            }),
        ):
            with patch("tw_quant_selector.data.realtime_valuation.compute_realtime_valuation") as mock_val:
                mock_val.return_value.pe_rt = 100.0
                self._call()
                args = [str(a) for a in self.db.execute.call_args_list]
                self.assertTrue(any("PRICE_PE_EXTREME" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_pe_extreme_no_trigger(self, mock_send, mock_cooldown):
        self.db.execute.return_value.fetchall.return_value = [
            [10.0], [11.0], [12.0], [13.0], [14.0], [15.0], [16.0], [17.0], [18.0], [19.0],
            [20.0], [21.0], [22.0], [23.0], [24.0], [25.0], [26.0], [27.0], [28.0], [29.0],
            [30.0],
        ]
        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 200.0, "volume": 10_000_000, "change_pct": 1.0, "quote_time": MARKET_TIME},
            }),
        ):
            with patch("tw_quant_selector.data.realtime_valuation.compute_realtime_valuation") as mock_val:
                mock_val.return_value.pe_rt = 15.0
                self._call()
                args = [str(a) for a in self.db.execute.call_args_list]
                self.assertFalse(any("PRICE_PE_EXTREME" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_stop_loss_triggers(self, mock_send, mock_cooldown):
        def execute_side_effect(sql, params=None):
            m = MagicMock()
            if "avg_cost" in sql:
                m.fetchone.return_value = [1000.0]
            else:
                m.fetchone.return_value = None
                m.fetchall.return_value = []
                m.fetchone.return_value = [1_000_000]
            return m
        self.db.execute.side_effect = execute_side_effect

        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 800.0, "volume": 10_000_000, "change_pct": -20.0, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertTrue(any("PRICE_STOP_LOSS" in a for a in args))

    @patch.object(AlertChecker, "_check_cooldown", return_value=True)
    @patch.object(AlertManager, "send_notification")
    def test_stop_loss_no_trigger(self, mock_send, mock_cooldown):
        def execute_side_effect(sql, params=None):
            m = MagicMock()
            if "avg_cost" in sql:
                m.fetchone.return_value = [1000.0]
            else:
                m.fetchone.return_value = None
                m.fetchall.return_value = []
                m.fetchone.return_value = [1_000_000]
            return m
        self.db.execute.side_effect = execute_side_effect

        with self._patch_deps(
            _get_latest_quotes=MagicMock(return_value={
                "2330": {"price": 950.0, "volume": 10_000_000, "change_pct": -5.0, "quote_time": MARKET_TIME},
            }),
        ):
            self._call()
            args = [str(a) for a in self.db.execute.call_args_list]
            self.assertFalse(any("PRICE_STOP_LOSS" in a for a in args))

    def test_skips_outside_market_hours(self):
        with self._patch_deps():
            self.checker.check_price_alerts(now=datetime(2026, 6, 4, 20, 0, 0))
            self.db.execute.assert_not_called()

    def test_skips_weekend(self):
        with self._patch_deps():
            self.checker.check_price_alerts(now=datetime(2026, 6, 6, 10, 30, 0))
            self.db.execute.assert_not_called()

    def test_get_latest_quotes_empty(self):
        result = self.checker._get_latest_quotes([])
        self.assertEqual(result, {})

    def test_get_20d_avg_volume_none(self):
        self.db.execute.return_value.fetchone.return_value = [None]
        result = self.checker._get_20d_avg_volume("2330")
        self.assertIsNone(result)

    def test_get_20d_avg_volume_ok(self):
        self.db.execute.return_value.fetchone.return_value = [1_500_000]
        result = self.checker._get_20d_avg_volume("2330")
        self.assertEqual(result, 1_500_000)

    def test_get_historical_pe_list(self):
        self.db.execute.return_value.fetchall.return_value = [[15.0], [20.0], [25.0]]
        result = self.checker._get_historical_pe_list("2330")
        self.assertEqual(result, [15.0, 20.0, 25.0])

    def test_get_historical_pe_list_filters_none(self):
        self.db.execute.return_value.fetchall.return_value = [[15.0], [None], [25.0]]
        result = self.checker._get_historical_pe_list("2330")
        self.assertEqual(result, [15.0, 25.0])


if __name__ == "__main__":
    unittest.main()
