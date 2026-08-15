"""Realtime price alert checks.

- check_price_alerts (PRICE_LIMIT_UP/DOWN, PRICE_UNUSUAL_VOLUME,
  PRICE_PE_EXTREME, PRICE_STOP_LOSS) plus helpers.

Module: price_checker.py
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from tw_quant_selector.monitoring.base_checker import MARKET_CLOSE, MARKET_OPEN
from tw_quant_selector.monitoring.notifiers import format_alert


class PriceCheckerMixin:
    def _get_latest_quotes(self, stock_ids: list[str]) -> dict[str, dict]:
        if not stock_ids:
            return {}
        placeholders = ", ".join("?" for _ in stock_ids)
        rows = self.db.execute(
            f"""SELECT r.stock_id, r.price, r.volume, r.change_pct, r.quote_time
                FROM realtime_quotes r
                INNER JOIN (
                    SELECT stock_id, MAX(quote_time) AS max_time
                    FROM realtime_quotes
                    WHERE stock_id IN ({placeholders})
                    GROUP BY stock_id
                ) latest ON latest.stock_id = r.stock_id AND r.quote_time = latest.max_time""",
            stock_ids
        ).fetchall()
        result = {}
        for r in rows:
            result[r[0]] = {"price": r[1], "volume": r[2], "change_pct": r[3], "quote_time": r[4]}
        return result

    def _get_20d_avg_volume(self, stock_id: str) -> Optional[float]:
        row = self.db.execute(
            """SELECT AVG(volume) FROM (
                SELECT volume FROM daily_prices
                WHERE stock_id = ? AND volume IS NOT NULL
                ORDER BY trade_date DESC LIMIT 20
            ) sub""",
            [stock_id]
        ).fetchone()
        return float(row[0]) if row and row[0] else None

    def _get_historical_pe_list(self, stock_id: str, days: int = 252) -> list[float]:
        rows = self.db.execute(
            """SELECT v.pe_ratio FROM valuations v
               WHERE v.stock_id = ? AND v.pe_ratio IS NOT NULL AND v.pe_ratio > 0
               ORDER BY v.trade_date DESC LIMIT ?""",
            [stock_id, days]
        ).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]

    def check_price_alerts(self, now: Optional[datetime] = None):
        now = now or datetime.now()
        t = now.time()
        if not (MARKET_OPEN <= t <= MARKET_CLOSE):
            return
        if now.weekday() >= 5:
            return

        picks = self._get_todays_picks()
        portfolio_stocks = self._get_portfolio_stocks()
        all_interest = list(set(
            [p["stock_id"] for p in picks] + portfolio_stocks
        ))
        if not all_interest:
            return

        quotes = self._get_latest_quotes(all_interest)
        if not quotes:
            return

        #────────────────────────────────────────────────────────────────
        # PRICE_LIMIT_UP / PRICE_LIMIT_DOWN
        #────────────────────────────────────────────────────────────────
        for sid in portfolio_stocks:
            q = quotes.get(sid)
            if not q or q.get("price") is None or q.get("change_pct") is None:
                continue
            cp = q["change_pct"]
            if cp >= 9.9:
                rule = "PRICE_LIMIT_UP"
                self._log_history(rule, "LOW", f"持仓股票涨停: {sid} (涨幅 {cp:+.2f}%)",
                                  {"stock_id": sid, "change_pct": cp, "price": float(q["price"])})
                if self._check_cooldown(f"{rule}:{sid}", 86400):
                    msg = format_alert("LOW", rule, f"持仓股票涨停: {sid} ({cp:+.2f}%)",
                                       suggestion="涨停封盘，留意后续开盘走势")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}:{sid}", msg)
            elif cp <= -9.9:
                rule = "PRICE_LIMIT_DOWN"
                self._log_history(rule, "HIGH", f"持仓股票跌停: {sid} (跌幅 {cp:.2f}%)",
                                  {"stock_id": sid, "change_pct": cp, "price": float(q["price"])})
                if self._check_cooldown(f"{rule}:{sid}", 86400):
                    msg = format_alert("HIGH", rule, f"持仓股票跌停: {sid} ({cp:.2f}%)",
                                       suggestion="跌停锁死，评估是否止损出场")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}:{sid}", msg)

        #────────────────────────────────────────────────────────────────
        # PRICE_UNUSUAL_VOLUME
        #────────────────────────────────────────────────────────────────
        for sid in all_interest:
            q = quotes.get(sid)
            if not q or q.get("volume") is None:
                continue
            cur_vol = q["volume"]
            avg_vol = self._get_20d_avg_volume(sid)
            if avg_vol and cur_vol > avg_vol * 3:
                rule = "PRICE_UNUSUAL_VOLUME"
                ratio = cur_vol / avg_vol
                self._log_history(rule, "LOW", f"异常放量: {sid} (当前 {cur_vol}, 20日均 {avg_vol:.0f}, {ratio:.1f}倍)",
                                  {"stock_id": sid, "current_volume": cur_vol, "avg_20d_volume": avg_vol, "ratio": round(ratio, 1)})
                if self._check_cooldown(f"{rule}:{sid}", 86400):
                    msg = format_alert("LOW", rule, f"异常放量: {sid} (当前量 {cur_vol}, 20日均 {avg_vol:.0f}, {ratio:.1f}倍)",
                                       suggestion="放量可能是趋势启动或反转讯号，请查阅个股新闻")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}:{sid}", msg)

        #────────────────────────────────────────────────────────────────
        # PRICE_PE_EXTREME
        #────────────────────────────────────────────────────────────────
        from tw_quant_selector.data.realtime_valuation import compute_realtime_valuation
        for sid in all_interest:
            q = quotes.get(sid)
            if not q or q.get("price") is None:
                continue
            pe_list = self._get_historical_pe_list(sid)
            if len(pe_list) < 20:
                continue
            pe_sorted = sorted(pe_list)
            idx = int(len(pe_sorted) * 0.95)
            p95 = pe_sorted[min(idx, len(pe_sorted) - 1)]

            val = compute_realtime_valuation(self.db, sid, q["price"])
            if val.pe_rt is None:
                continue
            cur_pe = float(val.pe_rt)
            if cur_pe > p95:
                rule = "PRICE_PE_EXTREME"
                self._log_history(rule, "MEDIUM", f"即时PE超过历史95百分位: {sid} (当前 {cur_pe:.1f}, P95 {p95:.1f})",
                                  {"stock_id": sid, "current_pe": cur_pe, "p95_pe": p95})
                if self._check_cooldown(f"{rule}:{sid}", 259200):
                    msg = format_alert("MEDIUM", rule, f"即时PE超过历史95百分位: {sid} (当前 {cur_pe:.1f}, 阈值 {p95:.1f})",
                                       suggestion="PE极端值可能暗示估值过高，评估风险")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}:{sid}", msg)

        #────────────────────────────────────────────────────────────────
        # PRICE_STOP_LOSS
        #────────────────────────────────────────────────────────────────
        stop_loss_pct = float(os.getenv("ALERT_STOP_LOSS_PCT", "0.08"))
        for sid in portfolio_stocks:
            q = quotes.get(sid)
            if not q or q.get("price") is None:
                continue
            row = self.db.execute(
                "SELECT avg_cost FROM portfolio WHERE stock_id = ? AND avg_cost IS NOT NULL",
                [sid]
            ).fetchone()
            if not row or not row[0]:
                continue
            entry_price = float(row[0])
            current_price = float(q["price"])
            if entry_price <= 0:
                continue
            drawdown = (entry_price - current_price) / entry_price
            if drawdown >= stop_loss_pct:
                rule = "PRICE_STOP_LOSS"
                self._log_history(rule, "HIGH", f"停损触发: {sid} (进场 {entry_price:.2f}, 现价 {current_price:.2f}, 跌幅 {drawdown*100:.1f}%)",
                                  {"stock_id": sid, "entry_price": entry_price, "current_price": current_price, "drawdown_pct": round(drawdown * 100, 1)})
                if self._check_cooldown(f"{rule}:{sid}", 86400):
                    msg = format_alert("HIGH", rule, f"停损触发: {sid} (进场价 {entry_price:.2f}, 现价 {current_price:.2f}, 跌幅 {drawdown*100:.1f}%)",
                                       suggestion=f"已达停损线 ({stop_loss_pct*100:.0f}%)，考虑执行止损")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}:{sid}", msg)