"""Institutional flow alert checks.

- check_institutional_alerts (5 rules: INST_HEAVY_BUY, INST_HEAVY_SELL,
  INST_DIVERGENCE, INST_CONSEC_BUY, INST_QUARTER_END) plus helpers.

Module: institutional_checker.py
"""

from __future__ import annotations

import httpx
from datetime import date
from typing import Optional

from tw_quant_selector.monitoring.base_checker import log
from tw_quant_selector.monitoring.notifiers import format_alert


class InstitutionalCheckerMixin:
    def _calc_consecutive_days(self, values: list[Optional[float]], direction: str = "positive") -> int:
        """Count consecutive days with the same sign from most recent.

        Args:
            values: List of net values, most recent first.
            direction: "positive" counts consecutive positive values,
                       "negative" counts consecutive negative values.

        Returns:
            Number of consecutive days meeting the direction.
        """
        count = 0
        for v in values:
            if v is None:
                break
            if direction == "positive" and v > 0:
                count += 1
            elif direction == "negative" and v < 0:
                count += 1
            else:
                break
        return count

    def _get_shares_outstanding(self) -> dict[str, int]:
        """Fetch outstanding shares for all TWSE stocks from company info API.

        Returns:
            {stock_id: shares_outstanding}
        """
        TWSE_BASE = "https://openapi.twse.com.tw/v1"
        try:
            resp = httpx.get(f"{TWSE_BASE}/announcement/t187ap03_L", timeout=15)
            resp.raise_for_status()
            rows = resp.json()
            result = {}
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
            log.warning("alert.shares_fetch_failed", error=str(exc))
            return {}

    def _get_recent_flows(self, stock_ids: list[str], days: int = 20) -> dict[str, list[dict]]:
        """Batch-fetch recent institutional flows for a list of stocks.

        Returns:
            {stock_id: [{trade_date, foreign_investors_net, total_net}, ...]}
        """
        if not stock_ids:
            return {}
        placeholders = ", ".join("?" for _ in stock_ids)
        rows = self.db.execute(
            f"""SELECT stock_id, trade_date, foreign_investors_net, total_net
                FROM institutional_flows
                WHERE stock_id IN ({placeholders})
                ORDER BY stock_id, trade_date DESC""",
            stock_ids
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            sid = r[0]
            if sid not in result:
                result[sid] = []
            if len(result[sid]) < days:
                result[sid].append({
                    "trade_date": r[1],
                    "foreign_investors_net": r[2],
                    "total_net": r[3],
                })
        return result

    def _get_todays_picks(self) -> list[dict]:
        """Get today's selected stocks (is_selected=True) from signals."""
        rows = self.db.execute(
            """SELECT DISTINCT s.stock_id, st.stock_name, s.score
               FROM signals s
               JOIN stocks st ON st.stock_id = s.stock_id
               WHERE s.signal_date = (SELECT MAX(signal_date) FROM signals)
                 AND s.is_selected IS TRUE
               ORDER BY s.score DESC"""
        ).fetchall()
        return [{"stock_id": r[0], "stock_name": r[1], "score": r[2]} for r in rows]

    def _get_portfolio_stocks(self) -> list[str]:
        """Get stock_ids from portfolio."""
        rows = self.db.execute("SELECT stock_id FROM portfolio").fetchall()
        return [r[0] for r in rows]

    def _is_quarter_end_soon(self, check_date: Optional[date] = None) -> bool:
        """Check if check_date is within 5 trading days of quarter end."""
        check_date = check_date or date.today()
        q_month = ((check_date.month - 1) // 3) * 3 + 3
        quarter_end = date(check_date.year, q_month, 25)
        if check_date > quarter_end:
            # Roll to next quarter if past this quarter's 25th
            if q_month == 12:
                quarter_end = date(check_date.year + 1, 3, 25)
            else:
                quarter_end = date(check_date.year, q_month + 3, 25)
        diff = (quarter_end - check_date).days
        return 0 <= diff <= 5

    # ── institutional alert rules ──────────────────────────────────────

    def check_institutional_alerts(self):
        """Check all 5 institutional alert rules."""
        # Skip weekends
        if date.today().weekday() >= 5:
            return

        picks = self._get_todays_picks()
        portfolio_stocks = self._get_portfolio_stocks()
        all_interest = list(set(
            [p["stock_id"] for p in picks] + portfolio_stocks
        ))
        flows = self._get_recent_flows(all_interest, days=20)
        shares_map = self._get_shares_outstanding()

        #────────────────────────────────────────────────────────────────
        # INST_HEAVY_BUY
        #────────────────────────────────────────────────────────────────
        rule = "INST_HEAVY_BUY"
        heavy_buys = []
        for p in picks:
            sid = p["stock_id"]
            stock_flows = flows.get(sid, [])
            if not stock_flows:
                continue
            latest = stock_flows[0]
            foreign_net = latest.get("foreign_investors_net")
            if foreign_net is None or foreign_net <= 0:
                continue
            outstanding = shares_map.get(sid)
            if outstanding is None:
                continue
            pct = foreign_net / outstanding
            if pct > 0.005:
                heavy_buys.append(f"{p['stock_name']}({sid}): {pct*100:.2f}%")

        if heavy_buys:
            detail = "\n".join(heavy_buys)
            self._log_history(rule, "LOW", f"外资买超 > 0.5% 流通股数: {len(heavy_buys)} 档",
                              {"stocks": heavy_buys})
            if self._check_cooldown(rule, 86400):
                msg = format_alert("LOW", rule,
                                   f"今日选股中有 {len(heavy_buys)} 档外资买超 > 流通股数 0.5%:\n{detail}",
                                   suggestion="可留意外资大量买进的股票后续走势")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        #────────────────────────────────────────────────────────────────
        # INST_HEAVY_SELL
        #────────────────────────────────────────────────────────────────
        rule = "INST_HEAVY_SELL"
        heavy_sells = []
        for sid in portfolio_stocks:
            stock_flows = flows.get(sid, [])
            if not stock_flows:
                continue
            vals = [f.get("foreign_investors_net") for f in stock_flows]
            consec = self._calc_consecutive_days(vals, direction="negative")
            if consec >= 5:
                heavy_sells.append(f"{sid} (连卖 {consec} 天)")

        if heavy_sells:
            detail = "\n".join(heavy_sells)
            self._log_history(rule, "MEDIUM", f"持仓股票外资连续卖超 >= 5 天: {len(heavy_sells)} 档",
                              {"stocks": heavy_sells})
            if self._check_cooldown(rule, 86400):
                msg = format_alert("MEDIUM", rule,
                                   f"持仓股票外资连续卖超 >= 5 天 ({len(heavy_sells)} 档):\n{detail}",
                                   suggestion="评估是否减码持仓部位")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        #────────────────────────────────────────────────────────────────
        # INST_DIVERGENCE
        #────────────────────────────────────────────────────────────────
        rule = "INST_DIVERGENCE"
        divergences = []
        for p in picks:
            sid = p["stock_id"]
            score = p.get("score")
            if score is None or float(score) <= 1.0:
                continue
            stock_flows = flows.get(sid, [])
            if not stock_flows:
                continue
            vals = [f.get("foreign_investors_net") for f in stock_flows]
            consec = self._calc_consecutive_days(vals, direction="negative")
            if consec >= 3:
                divergences.append(f"{p['stock_name']}({sid}): score={float(score):.2f}, 连卖 {consec} 天")

        if divergences:
            detail = "\n".join(divergences)
            self._log_history(rule, "MEDIUM", f"选股分数高但外资连续卖超: {len(divergences)} 档",
                              {"stocks": divergences})
            if self._check_cooldown(rule, 86400):
                msg = format_alert("MEDIUM", rule,
                                   f"选股分数 > 1.0 但外资连续卖超 >= 3 天 ({len(divergences)} 档):\n{detail}",
                                   suggestion="分数与外资流向背离，建议暂缓买入或重新评估")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        #────────────────────────────────────────────────────────────────
        # INST_CONSEC_BUY
        #────────────────────────────────────────────────────────────────
        rule = "INST_CONSEC_BUY"
        consec_buys = []
        for sid in all_interest:
            stock_flows = flows.get(sid, [])
            if not stock_flows:
                continue
            vals = [f.get("total_net") for f in stock_flows]
            consec = self._calc_consecutive_days(vals, direction="positive")
            if consec >= 10:
                consec_buys.append(f"{sid} (连买 {consec} 天)")

        if consec_buys:
            detail = "\n".join(consec_buys)
            self._log_history(rule, "LOW", f"三大法人连续买超 >= 10 天: {len(consec_buys)} 档",
                              {"stocks": consec_buys})
            if self._check_cooldown(rule, 259200):
                msg = format_alert("LOW", rule,
                                   f"三大法人连续买超 >= 10 天 ({len(consec_buys)} 档):\n{detail}",
                                   suggestion="法人连买为强势讯号，可留意后续表现")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        #────────────────────────────────────────────────────────────────
        # INST_QUARTER_END
        #────────────────────────────────────────────────────────────────
        rule = "INST_QUARTER_END"
        if self._is_quarter_end_soon():
            self._log_history(rule, "LOW", "距季末 <= 5 个交易日，投信作帐效应提醒")
            if self._check_cooldown(rule, 604800):
                msg = format_alert("LOW", rule,
                                   "距本季结束 <= 5 个交易日，投信作帐效应开始发酵",
                                   suggestion="关注投信高持股个股可能的季底拉抬/结帐行情")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)