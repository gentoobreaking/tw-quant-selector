from __future__ import annotations
import json
import os
import re
import smtplib
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta
from email.message import EmailMessage
from typing import Any, Optional
import httpx
import structlog

import pandas as pd

from tw_quant_selector.monitoring.indicators import compute_sma, compute_kd

log = structlog.get_logger()

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)


def format_alert(severity: str, rule: str, message: str, suggestion: Optional[str] = None, **context) -> str:
    icons = {
        "CRITICAL": "🚨",
        "HIGH": "⚠️",
        "MEDIUM": "📌",
        "LOW": "📊"
    }
    icon = icons.get(severity, "🔔")
    msg = f"{icon} {severity} / {rule}\n{message}"
    
    if suggestion:
        msg += f"\n\n💡 建議行動: {suggestion}"
        
    if context:
        msg += "\n\nContext:"
        for k, v in context.items():
            msg += f"\n- {k}: {v}"
    return msg


def get_alert_config(db) -> dict[str, Any]:
    # Keys we support
    keys = [
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "EMAIL_SENDER", "EMAIL_RECIPIENT",
        "PL_THRESHOLD", "PL_PERCENT_THRESHOLD"
    ]

    db_settings = {r[0]: r[1] for r in db.execute("SELECT key, value FROM alert_settings").fetchall()}
    config = {}
    for k in keys:
        val = os.getenv(k) or db_settings.get(k)
        config[k] = val
    return config


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token
        self.chat_id = chat_id

    def send(self, message: str):
        if not self.token or not self.chat_id:
            log.warning("alert.telegram.missing_config")
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = httpx.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=10.0)
            resp.raise_for_status()
            log.info("alert.telegram.sent")
        except Exception as e:
            log.error("alert.telegram.failed", error=str(e))


class EmailNotifier:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def send(self, subject: str, body: str):
        c = self.config
        server_addr = c.get("SMTP_SERVER")
        port = int(c.get("SMTP_PORT") or 587)
        user = c.get("SMTP_USER")
        pwd = c.get("SMTP_PASSWORD")
        sender = c.get("EMAIL_SENDER") or user
        recipient = c.get("EMAIL_RECIPIENT")

        if not all([server_addr, user, pwd, recipient]):
            log.warning("alert.email.missing_config")
            return

        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        try:
            with smtplib.SMTP(server_addr, port, timeout=15) as server:
                server.starttls()
                server.login(user, pwd)
                server.send_message(msg)
            log.info("alert.email.sent", to=recipient)
        except Exception as e:
            log.error("alert.email.failed", error=str(e))


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


# ── Smart Alert Check Functions (Pandas Vectorized) ───────────────────

def check_volume_spike(df: pd.DataFrame) -> pd.DataFrame:
    median_vol = df.groupby('Category')['TradeVolume'].transform('median')
    alert_mask = df['TradeVolume'] > (median_vol * 10)
    return df[alert_mask].copy()

def check_high_vol_no_move(df: pd.DataFrame) -> pd.DataFrame:
    top_50_idx = df['TradeVolume'].nlargest(50).index
    alert_mask = df.index.isin(top_50_idx) & (df['Return_Pct'].abs() <= 0.5)
    return df[alert_mask].copy()

def check_turnover_monster(df: pd.DataFrame) -> pd.DataFrame:
    market_total_value = df['TradeValue'].sum()
    alert_mask = (df['TradeValue'] / market_total_value >= 0.02) & (df['Code'] != '2330')
    return df[alert_mask].copy()

def check_intraday_volatility(df: pd.DataFrame) -> pd.DataFrame:
    volatility = (df['HighestPrice'] - df['LowestPrice']) / df['PrevClose']
    price_position = (df['CurrentPrice'] - df['LowestPrice']) / (df['HighestPrice'] - df['LowestPrice'])
    alert_mask = (volatility > 0.08) & (price_position <= 0.1)
    return df[alert_mask].copy()

def check_industry_momentum(df: pd.DataFrame) -> pd.DataFrame:
    df['Is_Strong'] = df['Return_Pct'] > 4.0
    industry_strong_ratio = df.groupby('Industry')['Is_Strong'].transform('mean')
    alert_mask = industry_strong_ratio > 0.30
    return df[alert_mask].copy()

def check_against_trend(df: pd.DataFrame, market_weak: bool) -> pd.DataFrame:
    if not market_weak:
        return pd.DataFrame()
    alert_mask = (
        (df['Return_Pct'] > 2.0) &
        (df['TradeVolume'] > df['Volume_5d_avg'] * 1.2)
    )
    return df[alert_mask].copy()

def check_low_price_junk_rally(df: pd.DataFrame, market_high: bool) -> pd.DataFrame:
    if not market_high:
        return pd.DataFrame()
    limit_up_idx = df['Return_Pct'] > 9.5
    limit_up_stocks = df[limit_up_idx]
    if len(limit_up_stocks) == 0:
        return pd.DataFrame()
    low_price_ratio = (limit_up_stocks['Price'] < 30).sum() / len(limit_up_stocks)
    if low_price_ratio > 0.6:
        return pd.DataFrame([{'Alert': 'LOW_PRICE_JUNK_RALLY', 'Ratio': low_price_ratio}])
    return pd.DataFrame()

def check_etf_premium_discount(df: pd.DataFrame, nav_df: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(nav_df, on='Code')
    merged['Premium_Discount'] = (merged['Price'] - merged['Estimated_NAV']) / merged['Estimated_NAV']
    alert_mask = (merged['Premium_Discount'].abs() > 0.005) & (merged['Size_Rank'] <= 20)
    return merged[alert_mask].copy()

def check_whale_move(df: pd.DataFrame) -> pd.DataFrame:
    whale_stocks = ['2330', '2454', '2317']
    alert_mask = df['Code'].isin(whale_stocks) & (df['Return_Pct'].abs() > 3.0)
    return df[alert_mask].copy()

def check_active_etf_hype(df: pd.DataFrame) -> pd.DataFrame:
    etf_only = df[df['Category'] == 'ETF']
    if len(etf_only) == 0:
        return pd.DataFrame()
    vol_cutoff = etf_only['TradeVolume'].quantile(0.90)
    alert_mask = (
        etf_only['Code'].str.endswith(('A', 'D', 'T')) &
        (etf_only['TradeVolume'] >= vol_cutoff)
    )
    return etf_only[alert_mask].copy()


class AlertChecker:
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
            import json
            self.db.execute(
                """INSERT INTO alert_history (id, rule_name, severity, message, context_data, triggered_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [str(uuid.uuid4()), rule_name, severity, message, json.dumps(context_data or {}), datetime.now()]
            )
        except Exception as e:
            log.error("alert.log_history_failed", rule=rule_name, error=str(e))

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

    # ── institutional helpers ──────────────────────────────────────────

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
        import httpx
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

    # ── realtime price alert rules ──────────────────────────────────────

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

            from tw_quant_selector.data.realtime_valuation import compute_realtime_valuation
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

    def _build_smart_alert_df(self) -> pd.DataFrame:
        from datetime import date
        import pandas as pd

        rows = self.db.execute("""
            SELECT DISTINCT ON (r.stock_id)
                r.stock_id AS Code,
                r.price AS CurrentPrice,
                r.volume AS TradeVolume,
                r.change_pct AS Return_Pct,
                r.quote_time
            FROM realtime_quotes r
            ORDER BY r.stock_id, r.quote_time DESC
        """).fetchall()
        if not rows:
            return pd.DataFrame()
        columns = ['Code', 'CurrentPrice', 'TradeVolume', 'Return_Pct', 'quote_time']
        df = pd.DataFrame(rows, columns=columns)
        df['CurrentPrice'] = pd.to_numeric(df['CurrentPrice'], errors='coerce')
        df['TradeVolume'] = pd.to_numeric(df['TradeVolume'], errors='coerce').fillna(0)
        df['Return_Pct'] = pd.to_numeric(df['Return_Pct'], errors='coerce').fillna(0.0)

        stock_rows = self.db.execute(
            "SELECT stock_id, industry, is_etf, stock_name FROM stocks"
        ).fetchall()
        stock_df = pd.DataFrame(stock_rows, columns=['stock_id', 'Industry', 'is_etf', 'Name'])
        stock_df['is_etf'] = stock_df['is_etf'].astype(bool)
        df = df.merge(stock_df, left_on='Code', right_on='stock_id', how='left')
        df['Industry'] = df['Industry'].fillna('Unknown')
        df['Name'] = df['Name'].fillna('')
        df['Category'] = df['is_etf'].map({True: 'ETF', False: 'Stock'})
        df['Category'] = df['Category'].fillna('Stock')

        df['TradeValue'] = df['CurrentPrice'] * df['TradeVolume']
        df['TradeValue'] = df['TradeValue'].fillna(0.0)
        df['PrevClose'] = df['CurrentPrice'] / (1 + df['Return_Pct'] / 100)
        df['PrevClose'] = df['PrevClose'].fillna(0.0)
        df['Price'] = df['CurrentPrice'].fillna(0.0)

        today = date.today()
        snap_rows = self.db.execute("""
            SELECT stock_id, MAX(price) AS high, MIN(price) AS low
            FROM intraday_snapshots
            WHERE snapshot_time::date = CURRENT_DATE
            GROUP BY stock_id
        """).fetchall()
        if snap_rows:
            snap_df = pd.DataFrame(snap_rows, columns=['stock_id', 'HighestPrice', 'LowestPrice'])
            df = df.merge(snap_df, on='stock_id', how='left')
        else:
            df['HighestPrice'] = None
            df['LowestPrice'] = None

        dp_rows = self.db.execute("""
            SELECT DISTINCT ON (stock_id)
                stock_id, high, low, volume
            FROM daily_prices
            ORDER BY stock_id, trade_date DESC
        """).fetchall()
        if dp_rows:
            dp_df = pd.DataFrame(dp_rows, columns=['stock_id', 'dp_high', 'dp_low', 'dp_volume'])
            df = df.merge(dp_df, on='stock_id', how='left')
            df['HighestPrice'] = df['HighestPrice'].fillna(df['dp_high'])
            df['LowestPrice'] = df['LowestPrice'].fillna(df['dp_low'])
        df['HighestPrice'] = pd.to_numeric(df['HighestPrice'], errors='coerce').fillna(df['CurrentPrice'])
        df['LowestPrice'] = pd.to_numeric(df['LowestPrice'], errors='coerce').fillna(df['CurrentPrice'])

        from sqlalchemy import text
        vol_rows = self.db.execute(
            text("""SELECT stock_id, AVG(volume) AS avg_vol
                     FROM (
                         SELECT stock_id, volume, trade_date,
                                ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn
                         FROM daily_prices
                         WHERE volume IS NOT NULL
                     ) sub WHERE rn <= 5
                     GROUP BY stock_id""")
        ).fetchall()
        if vol_rows:
            vol_df = pd.DataFrame(vol_rows, columns=['stock_id', 'Volume_5d_avg'])
            df = df.merge(vol_df, on='stock_id', how='left')
        df['Volume_5d_avg'] = pd.to_numeric(df['Volume_5d_avg'], errors='coerce').fillna(0.0)

        etf_df = df[df['Category'] == 'ETF'].copy()
        if not etf_df.empty:
            etf_df['Size_Rank'] = etf_df['TradeVolume'].rank(ascending=False, method='min')
            size_map = etf_df[['Code', 'Size_Rank']].set_index('Code')['Size_Rank'].to_dict()
            df['Size_Rank'] = df['Code'].map(size_map).fillna(999)

        df['Code'] = df['Code'].astype(str)
        df['Return_Pct'] = df['Return_Pct'].astype(float)
        df['TradeVolume'] = df['TradeVolume'].astype(float)
        df['TradeValue'] = df['TradeValue'].astype(float)
        return df

    def _get_market_context(self, df: pd.DataFrame) -> dict:
        total = len(df)
        if total == 0:
            return {'market_weak': False, 'market_high': False, 'nav_df': pd.DataFrame()}
        down_pct = (df['Return_Pct'] < 0).sum() / total
        up_pct = (df['Return_Pct'] > 0).sum() / total
        return {
            'market_weak': down_pct >= 0.70,
            'market_high': up_pct >= 0.60 and df['Return_Pct'].mean() > 0.5,
            'nav_df': pd.DataFrame(columns=['Code', 'Estimated_NAV']),
        }

    def check_all_smart_alerts(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now()
        t = now.time()
        if not (MARKET_OPEN <= t <= MARKET_CLOSE):
            return []
        if now.weekday() >= 5:
            return []

        df = self._build_smart_alert_df()
        if df.empty:
            return []

        triggered: list[dict] = []
        ctx = self._get_market_context(df)
        # Load smart alert templates from DB
        smart_rules = self.db.execute(
            "SELECT rule_name, message_template FROM alert_rules WHERE enabled = TRUE AND rule_name IN "
            "('VOLUME_SPIKE','HIGH_VOL_NO_MOVE','TURNOVER_MONSTER','INTRADAY_VOLATILITY','INDUSTRY_MOMENTUM',"
            "'AGAINST_TREND','LOW_PRICE_JUNK_RALLY','WHALE_MOVE','ACTIVE_ETF_HYPE','ETF_PREMIUM_DISCOUNT')"
        ).fetchall()
        smart_templates = {r[0]: r[1] for r in smart_rules}

        check_defs: list[tuple[str, str, pd.DataFrame]] = [
            ('VOLUME_SPIKE', 'LOW', check_volume_spike(df)),
            ('HIGH_VOL_NO_MOVE', 'MEDIUM', check_high_vol_no_move(df)),
            ('TURNOVER_MONSTER', 'MEDIUM', check_turnover_monster(df)),
            ('INTRADAY_VOLATILITY', 'HIGH', check_intraday_volatility(df)),
            ('INDUSTRY_MOMENTUM', 'LOW', check_industry_momentum(df)),
            ('AGAINST_TREND', 'MEDIUM', check_against_trend(df, ctx['market_weak'])),
            ('LOW_PRICE_JUNK_RALLY', 'CRITICAL', check_low_price_junk_rally(df, ctx['market_high'])),
            ('WHALE_MOVE', 'CRITICAL', check_whale_move(df)),
            ('ACTIVE_ETF_HYPE', 'LOW', check_active_etf_hype(df)),
        ]
        for rule_name, severity, result_df in check_defs:
            if result_df.empty:
                continue
            tmpl = smart_templates.get(rule_name)
            for _, row in result_df.iterrows():
                code = row.get('Code', row.get('Alert', 'N/A'))
                name = row.get('Name', '')
                default_msg = f'{rule_name}: {code}'
                vars_dict = row.to_dict()
                vars_dict.setdefault('stock_id', code)
                vars_dict.setdefault('stock_name', name)
                msg = self._format_alert_message(tmpl, default_msg, **vars_dict)
                self._log_history(rule_name, severity, msg, row.to_dict())
                triggered.append({
                    'alert_type': rule_name,
                    'severity': severity,
                    'stock_id': str(code),
                    'stock_name': str(name),
                    'message': msg,
                    'details': {k: v for k, v in row.items() if not isinstance(v, (pd.Timestamp, pd.NaT))},
                })
                if self._check_cooldown(rule_name, 1800):
                    full_msg = format_alert(severity, rule_name, msg)
                    self.manager.send_notification(f'[tw-quant-selector] {rule_name}', full_msg)

        etf_result = check_etf_premium_discount(df, ctx['nav_df'])
        if not etf_result.empty:
            etf_tmpl = smart_templates.get('ETF_PREMIUM_DISCOUNT')
            self._log_history('ETF_PREMIUM_DISCOUNT', 'MEDIUM',
                              f'ETF折溢价异常: {len(etf_result)} 档', etf_result.to_dict())
            for _, row in etf_result.iterrows():
                code = str(row.get('Code', 'N/A'))
                name = str(row.get('Name', ''))
                default_msg = f'ETF_PREMIUM_DISCOUNT: {code}'
                vars_dict = row.to_dict()
                vars_dict.setdefault('stock_id', code)
                vars_dict.setdefault('stock_name', name)
                msg = self._format_alert_message(etf_tmpl, default_msg, **vars_dict)
                triggered.append({
                    'alert_type': 'ETF_PREMIUM_DISCOUNT',
                    'severity': 'MEDIUM',
                    'stock_id': code,
                    'stock_name': name,
                    'message': msg,
                    'details': {k: v for k, v in row.items() if not isinstance(v, (pd.Timestamp, pd.NaT))},
                })
            if self._check_cooldown('ETF_PREMIUM_DISCOUNT', 1800):
                msg = format_alert('MEDIUM', 'ETF_PREMIUM_DISCOUNT',
                                   f'ETF折溢价异常 ({len(etf_result)} 档)')
                self.manager.send_notification('[tw-quant-selector] ETF_PREMIUM_DISCOUNT', msg)
        return triggered

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

    def check_technical_alerts(self, now: Optional[datetime] = None) -> list[dict]:
        """Check intraday K-line indicators (MA, KD) and trigger alerts.

        Reads from intraday_kline table (built by build_intraday_kline),
        computes SMA(60) on 60-min close prices, and KD(60,3,3), then
        evaluates conditions defined in alert_rules.
        """
        now = now or datetime.now()
        t = now.time()
        if not (MARKET_OPEN <= t <= MARKET_CLOSE):
            return []
        if now.weekday() >= 5:
            return []

        triggered: list[dict] = []

        # Load rules from DB (include config_json and message_template)
        rules = self.db.execute(
            "SELECT rule_name, enabled, threshold, cooldown_seconds, severity, config_json, message_template FROM alert_rules WHERE enabled = TRUE"
        ).fetchall()
        rule_map = {}
        for r in rules:
            cj = {}
            if r[5]:
                try:
                    cj = json.loads(r[5])
                except (json.JSONDecodeError, TypeError):
                    cj = {}
            rule_map[r[0]] = {"threshold": r[2], "cooldown_seconds": r[3] or 3600, "severity": r[4], "config": cj, "message_template": r[6]}

        if not rule_map:
            return triggered

        # Get stocks with recent intraday kline data
        today = now.date()
        stock_rows = self.db.execute("""
            SELECT DISTINCT stock_id FROM intraday_kline
            WHERE k_time::date = ?
        """, [today]).fetchall()

        kline_stocks = [r[0] for r in stock_rows]
        if not kline_stocks:
            return triggered

        for stock_id in kline_stocks:
            is_index = stock_id == '^TWII'
        kline_rows = self.db.execute("""
            SELECT k_time, open, high, low, close, volume
            FROM intraday_kline
            WHERE stock_id = ? AND k_time::date = ?
            ORDER BY k_time ASC
        """, [stock_id, today]).fetchall()

        if len(kline_rows) < 2:
            continue

        closes = [float(r[3]) for r in kline_rows]
        highs = [float(r[2]) for r in kline_rows]
        lows = [float(r[1]) for r in kline_rows]

        stock_name = '加權指數' if is_index else ''

        # ── Stock-level rules (TECH_MA_CROSS, TECH_KD_CROSS) ──
        if not is_index:
            ma_config = rule_map.get('TECH_MA_CROSS', {}).get('config', {})
            ma_period = int(ma_config.get('period', 60))
            ma_direction = ma_config.get('direction', 'above')

            sma_values = compute_sma(closes, min(ma_period, len(closes)))
            latest_close = closes[-1]
            latest_sma = sma_values[-1] if sma_values and sma_values[-1] is not None else None

            kd_config = rule_map.get('TECH_KD_CROSS', {}).get('config', {})
            kd_n = int(kd_config.get('kd_n', 60))
            kd_k1 = int(kd_config.get('kd_k1', 3))
            kd_d1 = int(kd_config.get('kd_d1', 3))
            kd_n = min(kd_n, len(closes))

            _, k_vals, d_vals = compute_kd(highs, lows, closes, n=kd_n, k1=kd_k1, d1=kd_d1)
            latest_k = k_vals[-1] if k_vals and k_vals[-1] is not None else None

            # Check TECH_MA_CROSS
            ma_rule = rule_map.get('TECH_MA_CROSS')
            if ma_rule and latest_sma is not None and latest_sma > 0:
                threshold = ma_rule['threshold'] or 0
                crossed = (
                    (ma_direction == 'above' and latest_close >= latest_sma * (1 + threshold / 100)) or
                    (ma_direction == 'below' and latest_close <= latest_sma * (1 - threshold / 100))
                )
                if crossed:
                    dir_label = '上' if ma_direction == 'above' else '下'
                    default_msg = f"{dir_label}穿{ma_period}MA: {stock_id} 收盤 {latest_close:.2f} MA {latest_sma:.2f}"
                    msg = self._format_alert_message(
                        ma_rule.get('message_template'), default_msg,
                        stock_id=stock_id, stock_name=stock_name, close=latest_close, sma=latest_sma,
                        period=ma_period, direction=dir_label, threshold=threshold,
                    )
                    self._log_history('TECH_MA_CROSS', ma_rule['severity'], msg, {
                        'stock_id': stock_id, 'close': latest_close, 'sma': latest_sma,
                    })
                    triggered.append({
                        'alert_type': 'TECH_MA_CROSS',
                        'severity': ma_rule['severity'],
                        'stock_id': stock_id,
                        'stock_name': stock_name,
                        'message': msg,
                        'details': {'close': latest_close, 'sma': latest_sma},
                    })
                    if self._check_cooldown('TECH_MA_CROSS', ma_rule['cooldown_seconds']):
                        self.manager.send_notification(
                            f'[tw-quant-selector] TECH_MA_CROSS',
                            format_alert(ma_rule['severity'], 'TECH_MA_CROSS', msg),
                        )

            # Check TECH_KD_CROSS
            kd_rule = rule_map.get('TECH_KD_CROSS')
            if kd_rule and latest_k is not None:
                threshold = kd_rule['threshold'] or 50
                if latest_k >= threshold:
                    default_msg = f"K值站上{threshold:.0f}: {stock_id} K {latest_k:.1f}"
                    msg = self._format_alert_message(
                        kd_rule.get('message_template'), default_msg,
                        stock_id=stock_id, stock_name=stock_name, k=latest_k, threshold=threshold,
                    )
                    self._log_history('TECH_KD_CROSS', kd_rule['severity'], msg, {
                        'stock_id': stock_id, 'k': latest_k,
                    })
                    triggered.append({
                        'alert_type': 'TECH_KD_CROSS',
                        'severity': kd_rule['severity'],
                        'stock_id': stock_id,
                        'stock_name': stock_name,
                        'message': msg,
                        'details': {'k': latest_k},
                    })
                    if self._check_cooldown('TECH_KD_CROSS', kd_rule['cooldown_seconds']):
                        self.manager.send_notification(
                            f'[tw-quant-selector] TECH_KD_CROSS',
                            format_alert(kd_rule['severity'], 'TECH_KD_CROSS', msg),
                        )

        # ── Index-level rules (TECH_INDEX_MA, TECH_INDEX_KD) ──
        if is_index:
            # TECH_INDEX_MA
            idx_ma_rule = rule_map.get('TECH_INDEX_MA')
            if idx_ma_rule:
                ma_cfg = idx_ma_rule.get('config', {})
                idx_ma_period = int(ma_cfg.get('period', 20))
                idx_ma_dir = ma_cfg.get('direction', 'above')
                idx_sma_vals = compute_sma(closes, min(idx_ma_period, len(closes)))
                idx_close = closes[-1]
                idx_sma = idx_sma_vals[-1] if idx_sma_vals and idx_sma_vals[-1] is not None else None
                if idx_sma is not None and idx_sma > 0:
                    idx_threshold = idx_ma_rule['threshold'] or 0
                    crossed = (
                        (idx_ma_dir == 'above' and idx_close >= idx_sma * (1 + idx_threshold / 100)) or
                        (idx_ma_dir == 'below' and idx_close <= idx_sma * (1 - idx_threshold / 100))
                    )
                    if crossed:
                        dir_label = '上' if idx_ma_dir == 'above' else '下'
                        default_msg = f"大盤{dir_label}穿{idx_ma_period}MA: {idx_close:.0f} MA {idx_sma:.0f}"
                        msg = self._format_alert_message(
                            idx_ma_rule.get('message_template'), default_msg,
                            stock_id=stock_id, stock_name=stock_name, close=idx_close, sma=idx_sma,
                            period=idx_ma_period, direction=dir_label, threshold=idx_threshold,
                        )
                        self._log_history('TECH_INDEX_MA', idx_ma_rule['severity'], msg, {
                            'stock_id': stock_id, 'close': idx_close, 'sma': idx_sma,
                        })
                        triggered.append({
                            'alert_type': 'TECH_INDEX_MA',
                            'severity': idx_ma_rule['severity'],
                            'stock_id': stock_id,
                            'stock_name': stock_name,
                            'message': msg,
                            'details': {'close': idx_close, 'sma': idx_sma},
                        })
                        if self._check_cooldown('TECH_INDEX_MA', idx_ma_rule['cooldown_seconds']):
                            self.manager.send_notification(
                                f'[tw-quant-selector] TECH_INDEX_MA',
                                format_alert(idx_ma_rule['severity'], 'TECH_INDEX_MA', msg),
                            )

            # TECH_INDEX_KD
            idx_kd_rule = rule_map.get('TECH_INDEX_KD')
            if idx_kd_rule:
                kd_cfg = idx_kd_rule.get('config', {})
                idx_kd_n = int(kd_cfg.get('kd_n', 9))
                idx_kd_k1 = int(kd_cfg.get('kd_k1', 3))
                idx_kd_d1 = int(kd_cfg.get('kd_d1', 3))
                idx_kd_n = min(idx_kd_n, len(closes))
                _, idx_k_vals, idx_d_vals = compute_kd(highs, lows, closes, n=idx_kd_n, k1=idx_kd_k1, d1=idx_kd_d1)
                idx_k = idx_k_vals[-1] if idx_k_vals and idx_k_vals[-1] is not None else None
                idx_d = idx_d_vals[-1] if idx_d_vals and idx_d_vals[-1] is not None else None
                if idx_k is not None:
                    zone = kd_cfg.get('zone', 'overbought')
                    idx_threshold = idx_kd_rule['threshold'] or 80
                    triggered_flag = (
                        (zone == 'overbought' and idx_k >= idx_threshold) or
                        (zone == 'oversold' and idx_k <= (100 - idx_threshold))
                    )
                    if triggered_flag:
                        zone_label = '超買' if zone == 'overbought' else '超賣'
                        default_msg = f"大盤KD{zone_label}: K {idx_k:.1f}"
                        msg = self._format_alert_message(
                            idx_kd_rule.get('message_template'), default_msg,
                            stock_id=stock_id, stock_name=stock_name, k=idx_k, d=idx_d,
                            zone=zone_label, threshold=idx_threshold,
                        )
                        self._log_history('TECH_INDEX_KD', idx_kd_rule['severity'], msg, {
                            'stock_id': stock_id, 'k': idx_k, 'd': idx_d,
                        })
                        triggered.append({
                            'alert_type': 'TECH_INDEX_KD',
                            'severity': idx_kd_rule['severity'],
                            'stock_id': stock_id,
                            'stock_name': stock_name,
                            'message': msg,
                            'details': {'k': idx_k, 'd': idx_d},
                        })
                        if self._check_cooldown('TECH_INDEX_KD', idx_kd_rule['cooldown_seconds']):
                            self.manager.send_notification(
                                f'[tw-quant-selector] TECH_INDEX_KD',
                                format_alert(idx_kd_rule['severity'], 'TECH_INDEX_KD', msg),
                            )

        return triggered

    def check_all(self):
        self.check_db_connection()
        self.check_data_freshness()
        self.check_system_health()
        self.check_signals_empty()
        self.check_institutional_alerts()
        self.check_price_alerts()
        self.check_all_smart_alerts()
        self.check_technical_alerts()
