"""System health alert checks.

- check_db_connection
- check_data_freshness
- check_system_health
- check_signals_empty

Module: system_checker.py
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from tw_quant_selector.monitoring.base_checker import MARKET_CLOSE, MARKET_OPEN, log
from tw_quant_selector.monitoring.notifiers import format_alert


class SystemCheckerMixin:
    def check_db_connection(self) -> bool:
        rule = "SYS_DB_UNREACHABLE"
        try:
            self.db.execute("SELECT 1").fetchone()
            return True
        except Exception as e:
            self._log_history(rule, "CRITICAL", str(e))
            if self._check_cooldown(rule, 300):  # 5 min
                msg = format_alert("CRITICAL", rule, f"PostgreSQL 无法连线: {e}",
                                   suggestion="檢查 DB 容器狀態與連線設定")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)
            return False

    def check_data_freshness(self):
        """Check if price and institutional data are up to date."""
        now = datetime.now()
        is_weekday = now.weekday() < 5 # 0-4 is Mon-Fri

        # Helper: 算出「預期應該有資料的最新交易日」
        #   平日：今天（T86 通常 18:30 後才會出今天的，所以 18:30 前算昨天；18:30 後算今天）
        #   假日：往前找最近的交易日（簡化為最多 -5 天，連假罕見但容忍）
        def expected_latest_trade_date() -> date:
            today = date.today()
            if is_weekday:
                # 18:30 後才預期今天會有；否則預期是昨天（或更早）
                cutoff = now.replace(hour=18, minute=30, second=0, microsecond=0)
                expected = today if now >= cutoff else (today - timedelta(days=1))
            else:
                # 週末：往前找最近一個交易日（簡化為週五或週一）
                offset = today.weekday() - 4  # Mon=0, Fri=4
                expected = today - timedelta(days=offset if offset > 0 else offset + 7)
            # 排除週末（萬一 expected 落在週六/日）
            while expected.weekday() >= 5:
                expected -= timedelta(days=1)
            return expected

        # 1. DATA_PRICE_DELAY
        if is_weekday and now.hour >= 19:
            rule = "DATA_PRICE_DELAY"
            # 用 MAX(trade_date) 而非 CURRENT_DATE：避免「今天還沒收盤」的 false positive
            row = self.db.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
            latest = row[0] if row and row[0] else None
            expected = expected_latest_trade_date()
            if latest is None or latest < expected:
                self._log_history(rule, "HIGH",
                                  f"19:00 後股價資料未到預期日 (latest={latest}, expected={expected})",
                                  {"latest": str(latest), "expected": str(expected)})
                if self._check_cooldown(rule, 3600):  # 1h
                    msg = format_alert(
                        "HIGH", rule,
                        f"交易日 19:00 後股價資料未到預期日 (最新: {latest}, 預期: {expected})",
                        suggestion="檢查日 K 線攝取排程 (run_pipeline_with_retry.sh)",
                    )
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        # 2. DATA_INSTITUTIONAL_DELAY
        if is_weekday and now.hour >= 18 and now.minute >= 30:
            rule = "DATA_INSTITUTIONAL_DELAY"
            row = self.db.execute("SELECT MAX(trade_date) FROM institutional_flows").fetchone()
            latest = row[0] if row and row[0] else None
            expected = expected_latest_trade_date()
            if latest is None or latest < expected:
                self._log_history(rule, "HIGH",
                                  f"18:30 後法人資料未到預期日 (latest={latest}, expected={expected})",
                                  {"latest": str(latest), "expected": str(expected)})
                if self._check_cooldown(rule, 3600):  # 1h
                    msg = format_alert(
                        "HIGH", rule,
                        f"交易日 18:30 後法人資料未到預期日 (最新: {latest}, 預期: {expected})",
                        suggestion="檢查法人資料攝取指令 (update_institutional_holdings.py)",
                    )
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        # 3. DATA_PRICE_MISSING
        rule = "DATA_PRICE_MISSING"
        row = self.db.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
        if row and row[0]:
            last = row[0]
            if isinstance(last, str):
                last = date.fromisoformat(last)
            days_since = (date.today() - last).days
            if days_since >= 3:
                self._log_history(rule, "CRITICAL", f"連續 {days_since} 日未更新股價資料", {"last_date": last.isoformat()})
                if self._check_cooldown(rule, 86400): # 1d
                    msg = format_alert("CRITICAL", rule, f"連續 {days_since} 日未更新股價資料 (最後更新: {last})",
                                       suggestion="檢查資料攝取管線與 API Token 是否過期")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

        # 4. PRICE_MIS_UNAVAILABLE
        if is_weekday and MARKET_OPEN <= now.time() <= MARKET_CLOSE:
            rule = "PRICE_MIS_UNAVAILABLE"
            row = self.db.execute(
                "SELECT COUNT(*) FROM realtime_quotes WHERE quote_time >= NOW() - INTERVAL '5 minutes'"
            ).fetchone()
            if row and row[0] == 0:
                self._log_history(rule, "HIGH", "盘中 MIS 即时报价 5 分钟内无更新")
                if self._check_cooldown(rule, 300):  # 5 min
                    msg = format_alert("HIGH", rule, "盘中 MIS 即时报价超过 5 分钟无更新",
                                       suggestion="检查 MIS API 连线状态与轮询排程")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

    def check_system_health(self):
        """Check disk space and scheduler status."""
        # 1. SYS_DISK_SPACE
        rule = "SYS_DISK_SPACE"
        try:
            # works on unix
            st = os.statvfs('/')
            free_pct = (st.f_bavail * 100) / st.f_blocks
            if free_pct < 10:
                self._log_history(rule, "HIGH", f"磁碟可用空間不足: {free_pct:.1f}%")
                if self._check_cooldown(rule, 21600): # 6h
                    msg = format_alert("HIGH", rule, f"磁碟可用空間不足 ({free_pct:.1f}%)",
                                       suggestion="清理資料庫日誌或暫存檔案", free_pct=free_pct)
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)
        except Exception as e:
            log.error("alert.disk_check_failed", error=str(e))

        # 2. SYS_SCHEDULER_STOPPED
        rule = "SYS_SCHEDULER_STOPPED"
        row = self.db.execute("SELECT MAX(last_updated) FROM ingestion_tracker").fetchone()
        if row and row[0]:
            last = row[0]
            if isinstance(last, str):
                last = date.fromisoformat(last)

            days_since = (date.today() - last).days
            if days_since >= 2:
                self._log_history(rule, "CRITICAL", f"排程器上次執行於 {last}")
                if self._check_cooldown(rule, 3600): # 1h
                    msg = format_alert("CRITICAL", rule, f"排程器上次執行已超過 24 小時 (最後更新: {last})",
                                       suggestion="手動重啟排程器腳本並檢查日誌")
                    self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)

    def check_signals_empty(self) -> bool:
        rule = "SIGNALS_EMPTY"
        row = self.db.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_date = (SELECT MAX(signal_date) FROM signals)"
        ).fetchone()
        if row and row[0] == 0:
            self._log_history(rule, "HIGH", "選股結果為空")
            if self._check_cooldown(rule, 14400): # 4h
                msg = format_alert("HIGH", rule, "今日選股結果為空（0 個標的）",
                                   suggestion="檢查策略參數設定與資料完整性")
                self.manager.send_notification(f"[tw-quant-selector] {rule}", msg)
            return False
        return True