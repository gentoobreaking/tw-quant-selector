"""Alerting package (split from the former monolithic ``alerting.py``).

Public API:
- ``AlertChecker`` (composed from per-domain mixins, with ``check_all()``)
- ``AlertManager`` (legacy, kept for compatibility)
- ``format_alert``, ``get_alert_config``
- ``TelegramNotifier``, ``EmailNotifier``
- 10 Pandas vectorized smart-alert check functions
"""

from __future__ import annotations

from tw_quant_selector.monitoring.base_checker import (
    MARKET_CLOSE,
    MARKET_OPEN,
    AlertCheckerBase,
)
from tw_quant_selector.monitoring.institutional_checker import InstitutionalCheckerMixin
from tw_quant_selector.monitoring.legacy import AlertManager
from tw_quant_selector.monitoring.notifiers import (
    EmailNotifier,
    TelegramNotifier,
    format_alert,
    get_alert_config,
)
from tw_quant_selector.monitoring.price_checker import PriceCheckerMixin
from tw_quant_selector.monitoring.smart_checker import (
    SmartCheckerMixin,
    check_active_etf_hype,
    check_against_trend,
    check_etf_premium_discount,
    check_high_vol_no_move,
    check_industry_momentum,
    check_intraday_volatility,
    check_low_price_junk_rally,
    check_turnover_monster,
    check_volume_spike,
    check_whale_move,
)
from tw_quant_selector.monitoring.system_checker import SystemCheckerMixin
from tw_quant_selector.monitoring.technical_checker import TechnicalCheckerMixin


class AlertChecker(
    AlertCheckerBase,
    SystemCheckerMixin,
    InstitutionalCheckerMixin,
    PriceCheckerMixin,
    SmartCheckerMixin,
    TechnicalCheckerMixin,
):
    """Comprehensive alert checker.

    All checks run in order via ``check_all()``:

    1. ``check_db_connection``
    2. ``check_data_freshness``
    3. ``check_system_health``
    4. ``check_signals_empty``
    5. ``check_institutional_alerts``
    6. ``check_price_alerts``
    7. ``check_all_smart_alerts``
    8. ``check_technical_alerts``
    """


__all__ = [
    "AlertChecker",
    "AlertManager",
    "TelegramNotifier",
    "EmailNotifier",
    "format_alert",
    "get_alert_config",
    "check_volume_spike",
    "check_high_vol_no_move",
    "check_turnover_monster",
    "check_intraday_volatility",
    "check_industry_momentum",
    "check_against_trend",
    "check_low_price_junk_rally",
    "check_etf_premium_discount",
    "check_whale_move",
    "check_active_etf_hype",
]