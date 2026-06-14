from __future__ import annotations
import os
import time
from datetime import date, datetime, timedelta
from typing import Any
import httpx
import structlog

log = structlog.get_logger()

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
RATE_LIMIT_PER_HOUR = 600  # 認證後每小時 600 次
MAX_DAILY_CALLS = 10000    # 每日總上限


class FinMindRateLimitError(Exception):
    """Raised when FinMind 402 rate-limit is exhausted after retries.

    Caller (scheduler) should catch this, save state, and either continue
    with other datasets or schedule a retry in 1 hour.
    """
    def __init__(self, dataset: str, attempts: int, message: str = ""):
        self.dataset = dataset
        self.attempts = attempts
        self.message = message
        super().__init__(
            f"FinMind rate-limit exhausted for {dataset} after {attempts} attempts. {message}"
        )


class FinMindClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("FINMIND_TOKEN", "")
        if not self.token:
            raise ValueError("FINMIND_TOKEN is required")
        self._client = httpx.Client(timeout=60)
        self._headers = {"Authorization": f"Bearer {self.token}"}
        self._hourly_call_count = 0
        self._last_reset_hour = datetime.now().hour
        self._daily_call_count = 0
        self._reset_date = date.today()
        self._banned_until: Optional[datetime] = None
        self._banned_logged: float = 0

    def is_banned(self) -> bool:
        return self._check_banned()

    def set_banned_until(self, dt: datetime) -> None:
        self._banned_until = dt

    def _check_banned(self) -> bool:
        """Check if currently banned from rate limiting. Returns True if banned."""
        if self._banned_until is None:
            return False
        if datetime.now() >= self._banned_until:
            self._banned_until = None
            return False
        # Log once per minute when banned
        now_ts = time.time()
        if now_ts - self._banned_logged > 60:
            remaining = (self._banned_until - datetime.now()).total_seconds()
            log.warning("finmind.banned", remaining_sec=int(remaining))
            self._banned_logged = now_ts
        return True

    def _check_rate_limit(self):
        now = datetime.now()
        # Hourly reset
        if now.hour != self._last_reset_hour:
            self._hourly_call_count = 0
            self._last_reset_hour = now.hour
        
        # Daily reset
        if now.date() != self._reset_date:
            self._daily_call_count = 0
            self._reset_date = now.date()

        self._hourly_call_count += 1
        self._daily_call_count += 1

        if self._hourly_call_count > RATE_LIMIT_PER_HOUR * 0.9:
            log.warning("finmind.rate_limit.hourly_high", usage=self._hourly_call_count)
        
        if self._hourly_call_count >= RATE_LIMIT_PER_HOUR:
            # We don't necessarily want to raise error, 
            # but let the API 402 handler handle the backoff.
            pass

    def _request(self, dataset: str, params: Optional[dict[str, Any]] = None) -> list[dict]:
        if self._check_banned():
            return []
        self._check_rate_limit()
        params = {"dataset": dataset, **(params or {})}
        
        retry_402_count = 0
        max_402_retries = 5
        
        while True:
            try:
                resp = self._client.get(FINMIND_BASE, headers=self._headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("msg") == "success":
                    return data.get("data", [])
                log.warning("finmind.api_error", dataset=dataset, msg=data.get("msg"))
                return []
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                body = {}
                try:
                    body = e.response.json()
                except Exception:
                    pass
                retry_after = body.get("retry_after", 0)
                detail = body.get("msg", e.response.text[:200])
                
                if status in (402, 403):
                    retry_402_count += 1

                    wait_sec = retry_after if retry_after > 0 else 60
                    # If retry_after >= 5 min, treat as hard ban — no point retrying
                    if wait_sec >= 300:
                        log.error("finmind.hard_ban", dataset=dataset, wait_sec=wait_sec, msg=detail)
                        self._banned_until = datetime.now() + timedelta(hours=1)
                        raise FinMindRateLimitError(dataset, retry_402_count, f"hard ban: {detail}")

                    if retry_402_count > max_402_retries:
                        log.error("finmind.rate_limit_exhausted", dataset=dataset, count=retry_402_count)
                        self._banned_until = datetime.now() + timedelta(hours=1)
                        raise FinMindRateLimitError(dataset, retry_402_count, detail)

                    log.warning("finmind.rate_limited_402", dataset=dataset,
                                attempt=retry_402_count, wait_sec=wait_sec)
                    time.sleep(wait_sec)
                    continue  # Retry

                # detect permanent permission errors (e.g. free tier can't access certain datasets)
                if "register" in detail.lower() or "level" in detail.lower():
                    log.error("finmind.permission_denied", dataset=dataset, status=status, msg=detail)
                    # Mark banned for 24h so caller won't keep trying
                    self._banned_until = datetime.now() + timedelta(hours=24)
                    raise FinMindRateLimitError(dataset, 0, f"permission denied: {detail}")

                # Other errors (400, 404, etc.)
                log.warning("finmind.skipped", dataset=dataset, status=status, msg=detail)
                return []

            except (httpx.TimeoutException, httpx.TransportError) as e:
                log.error("finmind.network_failed", dataset=dataset, error=str(e))
                return []

    def get_daily_prices(self, stock_id: str, start: date, end: date) -> list[dict]:
        return self._request("TaiwanStockPrice", {
            "data_id": stock_id, "start_date": start.isoformat(), "end_date": end.isoformat()
        })

    def get_financials(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockFinancialStatements", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_monthly_revenue(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockMonthRevenue", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_shareholding(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockHoldingSharesPer", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_dividend(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockDividend", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_per_pbr(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockPER", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_balance_sheet(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockBalanceSheet", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def get_cash_flows(self, stock_id: str, start: str, end: str) -> list[dict]:
        return self._request("TaiwanStockCashFlowsStatement", {
            "data_id": stock_id, "start_date": start, "end_date": end
        })

    def close(self):
        self._client.close()
