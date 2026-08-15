"""@deprecated — 相容性橋接檔.

``alerting.py`` 已於 T134 拆分為 ``monitoring`` 套件中的多個模組：

    monitoring/
        notifiers.py         TelegramNotifier, EmailNotifier, format_alert, get_alert_config
        legacy.py            AlertManager
        base_checker.py      AlertChecker 共用輔助 + check_all()
        system_checker.py    check_db_connection, check_data_freshness, ...
        institutional_checker.py  check_institutional_alerts
        price_checker.py     check_price_alerts
        smart_checker.py     10 個 Pandas 向量化檢查函式 + check_all_smart_alerts
        technical_checker.py check_technical_alerts

本檔僅重新匯出公開符號以維持向後相容；新程式碼請直接從
``tw_quant_selector.monitoring`` 或其子模組匯入。
"""

from __future__ import annotations

from tw_quant_selector.monitoring import (
    AlertChecker,
    AlertManager,
    EmailNotifier,
    TelegramNotifier,
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
    format_alert,
    get_alert_config,
)

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