"""Legacy AlertManager (backward compatibility).

Kept as a standalone class for existing callers
(``scripts/check_live_alerts.py``, ``app.py``). New alert logic lives in
the split checker modules; this class is unchanged from the original
``alerting.py``.

Module: legacy.py
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Optional

import structlog

from tw_quant_selector.monitoring.notifiers import (
    EmailNotifier,
    TelegramNotifier,
    get_alert_config,
)

log = structlog.get_logger()


class AlertManager:
    def __init__(self, db):
        self.db = db
        self._last_alert_time: dict[str, float] = {}
        self.cooldown = 3600 * 4  # 4 hours

    def _should_alert(self, key: str) -> bool:
        now = time.time()
        if key in self._last_alert_time:
            if now - self._last_alert_time[key] < self.cooldown:
                return False
        self._last_alert_time[key] = now
        return True

    def check_pl_alerts(self):
        config = get_alert_config(self.db)

        # Get current portfolio value
        # For simplicity, we assume there is a way to get current P/L
        # In a real system, we would query the portfolio module
        # Here we mock it or query backtest_runs if it's a 'live' run

        # Implementation of P/L check logic...
        # (This would be called from a scheduler)
        pass

    def _log_alert(self, stock_id: str, pnl: float, pnl_pct: float,
                    threshold_type: str, threshold_value: float,
                    avg_cost: float, current_price: float, shares: int,
                    sent: bool, reason: Optional[str] = None):
        try:
            self.db.execute(
                """INSERT INTO alert_log (log_id, stock_id, pnl, pnl_pct, threshold_type, threshold_value,
                                          avg_cost, current_price, shares, sent, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [str(uuid.uuid4()), stock_id, pnl, pnl_pct, threshold_type, threshold_value,
                 avg_cost, current_price, shares, sent, reason]
            )
        except Exception as e:
            log.error("alert.log_failed", error=str(e))

    def handle_pl_alert(self, stock_data: dict) -> dict:
        stock_id = stock_data.get("stock_id", "")
        stock_name = stock_data.get("stock_name", stock_id)
        pnl = stock_data.get("pnl", 0)
        pnl_pct = stock_data.get("pnl_pct", 0)
        threshold_type = stock_data.get("threshold_type", "amount")
        threshold_value = stock_data.get("threshold_value", 0)
        avg_cost = stock_data.get("avg_cost", 0)
        current_price = stock_data.get("current_price", 0)
        shares = stock_data.get("shares", 0)
        alert_enabled = stock_data.get("alert_enabled", True)

        if not alert_enabled:
            self._log_alert(stock_id, pnl, pnl_pct, threshold_type, threshold_value,
                            avg_cost, current_price, shares, sent=False, reason="disabled")
            return {"sent": False, "reason": "disabled"}

        cooldown_key = f"pl_alert:{stock_id}"
        now = time.time()
        if cooldown_key in self._last_alert_time:
            elapsed = now - self._last_alert_time[cooldown_key]
            if elapsed < self.cooldown:
                remaining = self.cooldown - elapsed
                cooldown_until = datetime.fromtimestamp(now + remaining).isoformat()
                self._log_alert(stock_id, pnl, pnl_pct, threshold_type, threshold_value,
                                avg_cost, current_price, shares, sent=False, reason="cooldown")
                return {"sent": False, "reason": "cooldown", "cooldown_until": cooldown_until}
        self._last_alert_time[cooldown_key] = now

        threshold_display = f"{threshold_value:.2f}%" if threshold_type == "percent" else f"{threshold_value:,.2f} 元"
        direction = "上漲" if pnl >= 0 else "下跌"
        subject = f"[tw-quant-selector] 個股損益監控 — {stock_name} ({stock_id})"
        message = (
            f"股票：{stock_name} ({stock_id})\n"
            f"損益：{'+' if pnl >= 0 else ''}{pnl:,.2f} 元（{pnl_pct:+.2f}%）\n"
            f"門檻：{threshold_display}\n"
            f"均價：{avg_cost:,.2f}\n"
            f"現價：{current_price:,.2f}\n"
            f"持有：{shares} 股\n"
            f"方向：{direction}突破門檻"
        )
        try:
            self.send_notification(subject, message)
            sent_ok = True
            reason_val = None
        except Exception as e:
            log.error("alert.pl_alert.send_failed", stock_id=stock_id, error=str(e))
            sent_ok = False
            reason_val = "send_failed"

        self._log_alert(stock_id, pnl, pnl_pct, threshold_type, threshold_value,
                        avg_cost, current_price, shares, sent=sent_ok, reason=reason_val)

        cooldown_until = datetime.fromtimestamp(
            self._last_alert_time.get(cooldown_key, now) + self.cooldown
        ).isoformat() if sent_ok else None
        return {"sent": sent_ok, "cooldown_until": cooldown_until, "reason": reason_val}

    def send_notification(self, subject: str, message: str):
        config = get_alert_config(self.db)

        tg = TelegramNotifier(config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID"))
        tg.send(f"{subject}\n\n{message}")

        em = EmailNotifier(config)
        em.send(subject, message)