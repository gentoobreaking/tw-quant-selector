"""Unit tests for backtest custom_universe support (T130).

Tests that ``run_backtest`` propagates ``custom_universe`` into
``strategy_params`` and that ``compute_composite_scores`` restricts the
universe to the custom stock list. All DB access is mocked.
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from tw_quant_selector.backtest.engine import _rebalance_dates, run_backtest
from tw_quant_selector.strategies.combiner import compute_composite_scores

MOCK_METRICS = {
    "total_return": 0.01,
    "cagr": 0.01,
    "sharpe": 1.0,
    "max_drawdown": -0.01,
    "calmar": 1.0,
    "turnover": 0.5,
}


class TestRebalanceDates(unittest.TestCase):
    def test_only_weekdays(self):
        # 2026-07-06 Mon ~ 2026-07-10 Fri
        dates = _rebalance_dates(date(2026, 7, 6), date(2026, 7, 10))
        self.assertEqual(len(dates), 5)
        for d in dates:
            self.assertLess(d.weekday(), 5)

    def test_weekend_excluded(self):
        # 2026-07-10 Fri ~ 2026-07-13 Mon (Sat/Sun excluded)
        dates = _rebalance_dates(date(2026, 7, 10), date(2026, 7, 13))
        self.assertEqual([d.day for d in dates], [10, 13])


class TestRunBacktestCustomUniverse(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.patch_specs = [
            ("_historical_universe", patch("tw_quant_selector.backtest.engine._historical_universe",
                                           return_value=["2330", "2317", "2454"])),
            ("_get_price", patch("tw_quant_selector.backtest.engine._get_price",
                                 return_value=Decimal("100"))),
            ("_save_backtest", patch("tw_quant_selector.backtest.engine._save_backtest")),
            ("_save_trades", patch("tw_quant_selector.backtest.engine._save_trades")),
            ("_save_equity", patch("tw_quant_selector.backtest.engine._save_equity")),
            ("compute_metrics", patch("tw_quant_selector.backtest.engine.compute_metrics",
                                      return_value=dict(MOCK_METRICS))),
        ]
        self.mocks = {name: p.start() for name, p in self.patch_specs}
        self.mock_scores = patch(
            "tw_quant_selector.backtest.engine.compute_composite_scores",
            return_value={"stocks": [{"stock_id": "2330", "score": 1.0}], "etfs": []},
        ).start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for _, p in self.patch_specs:
            p.stop()
        self.mock_scores.stop()

    def _run(self, **kwargs):
        return run_backtest(
            self.db,
            start_date=date(2026, 7, 6),   # Monday
            end_date=date(2026, 7, 7),     # Tuesday
            **kwargs,
        )

    def _strategy_params(self):
        args = self.mock_scores.call_args
        return args.kwargs.get("strategy_params", {})

    def test_custom_universe_passed_through(self):
        self._run(custom_universe=["2330", "2317"])
        self.assertEqual(self._strategy_params().get("custom_universe"), ["2330", "2317"])

    def test_no_custom_universe_no_key(self):
        self._run()
        self.assertNotIn("custom_universe", self._strategy_params())

    def test_empty_custom_universe_ignored(self):
        self._run(custom_universe=[])
        self.assertNotIn("custom_universe", self._strategy_params())
        self.assertEqual(self._strategy_params(), {})

    def test_returns_metrics_with_run_id(self):
        result = self._run(custom_universe=["2330"], run_id="test-run")
        self.assertEqual(result["run_id"], "test-run")
        self.assertEqual(result["total_return"], MOCK_METRICS["total_return"])

    def test_backend_compat_runs_full_universe(self):
        self._run()
        # 2026-07-06 (Mon) + 2026-07-07 (Tue) → _historical_universe called twice
        self.assertEqual(
            [c.args[1] for c in self.mocks["_historical_universe"].call_args_list],
            [date(2026, 7, 6), date(2026, 7, 7)],
        )


class TestCombinerCustomUniverse(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.patch_specs = [
            ("_combine", patch("tw_quant_selector.strategies.combiner._combine",
                               return_value=({}, {}))),
            ("_rank_and_select", patch("tw_quant_selector.strategies.combiner._rank_and_select",
                                       side_effect=lambda x, n: x or [])),
            ("_save_signals", patch("tw_quant_selector.strategies.combiner._save_signals")),
            ("get_universe", patch("tw_quant_selector.strategies.combiner.get_universe")),
        ]
        self.mocks = {name: p.start() for name, p in self.patch_specs}
        self.addCleanup(self._stop_patches)
        self.mock_combine = self.mocks["_combine"]
        self.mock_universe = self.mocks["get_universe"]

    def _stop_patches(self):
        for _, p in self.patch_specs:
            p.stop()

    def _custom_rows(self):
        return self.db.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value

    def test_custom_universe_queries_only_selected_ids(self):
        conn = self.db.connection.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [
            ("2330", "台積電", "TSE", "半導體", date(1994, 9, 5), None, False),
            ("6756", "威鋒電子", "TSE", "半導體", date(2021, 1, 27), None, True),
        ]
        compute_composite_scores(
            self.db, date(2026, 7, 6),
            strategy_params={"custom_universe": ["2330", "6756", "9999"]},
        )
        exec_args = conn.execute.call_args
        self.assertIn("ANY(:sids)", str(exec_args.args[0]))
        self.assertEqual(exec_args.args[1]["sids"], ["2330", "6756", "9999"])
        # _combine got the custom universe: stocks from rows, nothing for missing 9999
        stock_combine_args = self.mock_combine.call_args_list[0].args
        etf_combine_args = self.mock_combine.call_args_list[1].args
        self.assertEqual(stock_combine_args[1], ["2330"])
        self.assertEqual(etf_combine_args[1], ["6756"])
        self.mock_universe.assert_not_called()

    def test_full_universe_when_no_custom(self):
        self.mock_universe.return_value = {
            "stocks": [{"stock_id": "2330"}],
            "etfs": [],
        }
        compute_composite_scores(self.db, date(2026, 7, 6))
        self.mock_universe.assert_called_once()


if __name__ == "__main__":
    unittest.main()