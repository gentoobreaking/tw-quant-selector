"""AlertChecker shared helpers.

Provides the base class with common infrastructure used by all checkers:
cooldown tracking, alert history logging, message template formatting,
and the master ``check_all()`` entrypoint.

Module: base_checker.py
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time as dtime
from typing import Any, Optional

import structlog

from tw_quant_selector.monitoring.legacy import AlertManager

log = structlog.get_logger()

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)


class AlertCheckerBase:
    def __init__(self, db):
        self.db = db
        self.manager = AlertManager(db)

    def _check_cooldown(self, rule_name: str, cooldown_seconds: int) -> bool:
        """
        Check if an alert for rule_name is in cooldown.
        Returns True if alert SHOULD be sent (not in cooldown), False otherwise.
        """
        try:
            row = self.db.execute(
                "SELECT last_alert_time FROM alert_cooldowns WHERE rule_name = ?",
                [rule_name]
            ).fetchone()

            now = datetime.now()
            if row:
                last_alert_time = row[0]
                if isinstance(last_alert_time, str):
                    last_alert_time = datetime.fromisoformat(last_alert_time)

                if (now - last_alert_time).total_seconds() < cooldown_seconds:
                    return False

            # Update or insert cooldown
            self.db.execute(
                """INSERT INTO alert_cooldowns (rule_name, last_alert_time, cooldown_seconds)
                   VALUES (?, ?, ?)
                   ON CONFLICT (rule_name) DO UPDATE SET
                       last_alert_time = EXCLUDED.last_alert_time,
                       cooldown_seconds = EXCLUDED.cooldown_seconds""",
                [rule_name, now, cooldown_seconds]
            )
            return True
        except Exception as e:
            log.error("alert.cooldown_check_failed", rule=rule_name, error=str(e))
            return True  # Default to sending if check fails

    def _log_history(self, rule_name: str, severity: str, message: str, context_data: Optional[dict] = None):
        """Log alert to alert_history table."""
        try:
            self.db.execute(
                """INSERT INTO alert_history (id, rule_name, severity, message, context_data, triggered_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [str(uuid.uuid4()), rule_name, severity, message, json.dumps(context_data or {}), datetime.now()]
            )
        except Exception as e:
            log.error("alert.log_history_failed", rule=rule_name, error=str(e))

    def _format_alert_message(self, template: Optional[str], default: str, **vars: Any) -> str:
        """Format an alert message from a template string.

        Uses templates from alert_rules.message_template. Unknown variables
        are silently left as-is.
        """
        if not template:
            return default
        try:
            return template.format(**vars)
        except (KeyError, ValueError, TypeError):
            return default

    def check_all(self):
        self.check_db_connection()
        self.check_data_freshness()
        self.check_system_health()
        self.check_signals_empty()
        self.check_institutional_alerts()
        self.check_price_alerts()
        self.check_all_smart_alerts()
        self.check_technical_alerts()