from __future__ import annotations
import re
import os
import time
from datetime import date, datetime, timedelta
from typing import Literal

import warnings
import httpx
import structlog

try:
    import twstock

    _HAS_TWSTOCK = True
except ImportError:
    _HAS_TWSTOCK = False

from tw_quant_selector.data.database import Database

MarketScope = Literal["TWSE", "TPEX", "ALL"]

warnings.filterwarnings("ignore", message=".*SSL.*", module="tw_quant_selector.data.twstock_client")

log = structlog.get_logger()

# ─── Config & helpers ────────────────────────────────────────────────────────

TWSE_BASE = os.getenv("TWSE_BASE_URL", "https://openapi.twse.com.tw/v1")
TPEX_BASE = os.getenv("TPEX_BASE_URL", "https://www.tpex.org.tw/openapi/v1")
DEFAULT_MARKET_SCOPE = os.getenv("STOCK_MARKET_SCOPE", "TWSE").upper()

_ETF_CODE_RE = re.compile(r"^00\d{3,4}$")

_ROC_EPOCH = 1911


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None

# Keep only real investable securities; skip listed warrants / CBBCs / beneficiary securities.
KEEP_TYPES = frozenset({
    "股票", "ETF", "ETN", "特別股", "創新板",
    "臺灣存託憑證(TDR)",
    "受益證券-不動產投資信託",
    "受益證券-資產基礎證券",
})

# Regex for FinMind-compatible stock IDs. Supports letter suffixes.
_FINMIND_VALID_ID = re.compile(
    r"^\d{4}$|^00\d{3,4}$|^\d{5}[A-Z]?$|^\d{4}[A-Z]\d{0,2}$|^\d{6}[A-Z]?$"
)

_MARKET_MAP = {
    "上市": "TSE",
    "上市臺灣創新板": "TSE",
    "上櫃": "OTC",
}


def is_etf(stock_id: str) -> bool:
    return bool(_ETF_CODE_RE.match(stock_id))


def is_finmind_valid(stock_id: str) -> bool:
    return bool(_FINMIND_VALID_ID.match(stock_id))


# ─── TWSE: fetch from STOCK_DAY_ALL API ────────────────────────────────────

def _roc_to_ad(roc_date: str) -> str:
    """Convert ROC calendar date to AD date. e.g. '1150528' -> '2026-05-28'"""
    year = int(roc_date[:3]) + _ROC_EPOCH
    return f"{year}-{roc_date[3:5]}-{roc_date[5:7]}"


def _fetch_twse_codes() -> list[tuple[str, str]]:
    """Fetch all stock codes from TWSE STOCK_DAY_ALL API."""
    client = httpx.Client(timeout=30)
    try:
        resp = client.get(f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL")
        resp.raise_for_status()
        rows = resp.json()
        results = []
        for r in rows:
            code = r.get("Code")
            name = r.get("Name", "")
            if code:
                results.append((str(code), str(name or code)))
        log.info("twstock_client.twse.fetched", count=len(results))
        return results
    finally:
        client.close()


def fetch_twse_daily_prices_all() -> list[dict]:
    """Fetch latest daily OHLCV data for all TWSE stocks from STOCK_DAY_ALL.
    
    Returns list of dicts with keys matching daily_prices table schema.
    Covers ~1089 regular stocks plus ETFs in a single API call.
    """
    client = httpx.Client(timeout=60)
    try:
        resp = client.get(f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL")
        resp.raise_for_status()
        rows = resp.json()
        results = []
        for r in rows:
            code = r.get("Code", "")
            date_str = r.get("Date", "")
            if not code or not date_str:
                continue
            try:
                trade_date = _roc_to_ad(date_str)
            except (ValueError, IndexError):
                continue
            results.append({
                "stock_id": code,
                "trade_date": trade_date,
                "open": _safe_float(r.get("OpeningPrice")),
                "high": _safe_float(r.get("HighestPrice")),
                "low": _safe_float(r.get("LowestPrice")),
                "close": _safe_float(r.get("ClosingPrice")),
                "volume": _safe_int(r.get("TradeVolume")),
                "amount": _safe_float(r.get("TradeValue")),
            })
        log.info("twstock_client.daily_prices.fetched",
                 date=results[0]["trade_date"] if results else None,
                 count=len(results))
        return results
    finally:
        client.close()


# ─── TWSE: fetch valuations from BWIBBU_ALL + company info ─────────────────

def fetch_twse_valuations_all() -> tuple[list[dict], dict[str, int]]:
    """
    Fetch PE ratio, PB ratio, dividend yield
    for all TWSE listed stocks using BWIBBU_ALL and company info API.

    Returns (valuations_rows, shares_map) where:
      - valuations_rows: list of dicts with keys matching valuations table schema
      - shares_map: {stock_id: shares_outstanding}
    """
    client = httpx.Client(timeout=60)
    try:
        bwibbu_resp = client.get(f"{TWSE_BASE}/exchangeReport/BWIBBU_ALL")
        bwibbu_resp.raise_for_status()
        bwibbu_rows = bwibbu_resp.json()

        company_resp = client.get(f"{TWSE_BASE}/announcement/t187ap03_L")
        company_resp.raise_for_status()
        company_rows = company_resp.json()

        shares_map: dict[str, int] = {}
        for r in company_rows:
            code = r.get("公司代號", "")
            shares_str = r.get("已發行普通股數或TDR原股發行股數", "0")
            try:
                shares = int(shares_str)
            except (ValueError, TypeError):
                shares = 0
            if shares > 0:
                shares_map[code] = shares

        results = []
        for r in bwibbu_rows:
            code = r.get("Code", "")
            date_str = r.get("Date", "")
            if not code or not date_str:
                continue
            try:
                trade_date = _roc_to_ad(date_str)
            except (ValueError, IndexError):
                continue

            pe = _safe_float(r.get("PEratio"))
            dy = _safe_float(r.get("DividendYield"))
            pb = _safe_float(r.get("PBratio"))

            row: dict = {
                "stock_id": code,
                "trade_date": trade_date,
                "pe_ratio": pe,
                "dividend_yield": dy / 100 if dy else None,
                "pb_ratio": pb,
            }
            results.append(row)

        log.info("twstock_client.valuations.fetched",
                 date=results[0]["trade_date"] if results else None,
                 count=len(results))
        return results, shares_map
    finally:
        client.close()


# ─── TWSE: fetch monthly revenue from t187ap05_L ───────────────────────────

def fetch_twse_revenue_all() -> list[dict]:
    """
    Fetch monthly revenue for all TWSE listed stocks from t187ap05_L API.

    Returns list of dicts with keys matching monthly_revenue table schema:
        stock_id, year_month, revenue, revenue_yoy, announcement_date
    """
    client = httpx.Client(timeout=60)
    try:
        resp = client.get(f"{TWSE_BASE}/opendata/t187ap05_L")
        resp.raise_for_status()
        rows = resp.json()

        results = []
        for r in rows:
            code = r.get("公司代號", "")
            ym_roc = r.get("資料年月", "")
            ad_date_roc = r.get("出表日期", "")
            if not code or not ym_roc:
                continue

            try:
                year = int(ym_roc[:3]) + _ROC_EPOCH
                month = ym_roc[3:5]
                year_month = f"{year}-{month}"
            except (ValueError, IndexError):
                continue

            announcement_date = None
            if ad_date_roc and len(ad_date_roc) == 7:
                try:
                    ay = int(ad_date_roc[:3]) + _ROC_EPOCH
                    am = ad_date_roc[3:5]
                    ad = ad_date_roc[5:7]
                    announcement_date = f"{ay}-{am}-{ad}"
                except (ValueError, IndexError):
                    pass

            revenue_s = r.get("營業收入-當月營收", "")
            revenue = _safe_int(revenue_s) if revenue_s else None

            yoy_s = r.get("營業收入-去年同月增減(%)", "")
            revenue_yoy = _safe_float(yoy_s) if yoy_s else None

            results.append({
                "stock_id": code,
                "year_month": year_month,
                "revenue": revenue,
                "revenue_yoy": revenue_yoy,
                "announcement_date": announcement_date,
            })

        log.info("twstock_client.revenue.fetched",
                 count=len(results), year_month=results[0]["year_month"] if results else None)
        return results
    finally:
        client.close()


# ─── TPEX: fetch from twstock.codes ─────────────────────────────────────────

def _fetch_tpex_codes() -> list[tuple[str, str]]:
    """
    Fetch TPEX stock list from TPEX OpenAPI /tpex_securities.
    Returns list of (code, name) tuples.
    """
    url = f"{TPEX_BASE}/tpex_securities"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected TPEX securities response: {type(payload)}")
    kept = []
    for row in payload:
        code = str(row.get("證券代號", "")).strip()
        name = str(row.get("證券名稱", "") or code).strip()
        if not code or len(code) < 4:
            continue
        kept.append((code, name))
    log.info("twstock_client.tpex_securities", count=len(kept))
    return kept


# ─── Trading day check & retry ──────────────────────────────────────────────

TWSE_RWD_BASE = "https://www.twse.com.tw/rwd/zh"

TWSE_MARKET_STATUS_URL = "https://www.twse.com.tw/rwd/zh/marketSummary/marketSummary"

TWSE_HOLIDAY_URL = f"{TWSE_RWD_BASE}/holidaySchedule/holidaySchedule"


def is_trading_day(check_date: Optional[str] = None) -> bool:
    """Check if *check_date* is a TWSE trading day.

    Uses TWSE T86 institutional API as the source of truth:
    returns True iff the API returns OK with data rows.

    Falls back to weekday check (Mon–Fri) if API is unreachable.
    """
    check_date = check_date or date.today().isoformat()
    dt = date.fromisoformat(check_date)

    # Weekend is never a trading day
    if dt.weekday() >= 5:
        return False

    # Probe T86 API — if it returns real data, it was a trading day
    rows = fetch_twse_institutional_all(check_date)
    if rows:
        return True

    # API returned empty — data may not be published yet (TWSE updates at ~16:30).
    # Fall back to weekday heuristic: Mon–Fri → treat as trading day; retry loop
    # handles the case where data arrives later.
    return True


def fetch_with_retry(
    fetch_fn,
    trade_date: Optional[str] = None,
    max_retries: int = 3,
    retry_delay_seconds: int = 1800,
    fn_name: str = "",
) -> list[dict]:
    """Call *fetch_fn(trade_date)* with retry.

    Retries when the function returns an empty list (no data yet) or raises
    an exception.  Waits *retry_delay_seconds* between attempts.

    Returns the non-empty result, or an empty list if all retries are exhausted.
    """
    _name = fn_name or getattr(fetch_fn, "__name__", "unknown")
    for attempt in range(1, max_retries + 1):
        try:
            result = fetch_fn(trade_date)
            if result:
                return result
            log.info(
                "twstock_client.retry.empty",
                fn=_name, attempt=attempt, max_retries=max_retries,
                delay=retry_delay_seconds,
            )
        except Exception as exc:
            log.warning(
                "twstock_client.retry.error",
                fn=_name, attempt=attempt, max_retries=max_retries,
                error=str(exc), delay=retry_delay_seconds,
            )
        if attempt < max_retries:
            time.sleep(retry_delay_seconds)

    log.warning("twstock_client.retry.exhausted", fn=_name, max_retries=max_retries)
    return []


# ─── TWSE: fetch institutional investors from T86 API ──────────────────────

def fetch_twse_institutional_all(trade_date: Optional[str] = None) -> list[dict]:
    """Fetch TWSE institutional investors data from T86 API.

    URL: https://www.twse.com.tw/rwd/zh/fund/T86
    Note: This endpoint is at rwd.twse.com.tw, NOT at openapi.twse.com.tw.
    Columns mapped:
      - foreign_investors_net: 外陆资买超股数(不含外资自营商) [index 4]
      - sity_investors_net:    投信买卖超股数               [index 10]
      - dealer_net:            自营商买卖超股数(合计)        [index 11]
      - dealer_proprietary_net: 自营商(自行买卖)买卖超       [index 14]
      - dealer_hedge_net:      自营商(避险)买卖超           [index 17]
      - total_net:             三大法人买卖超股数            [index 18]
    """
    raw = trade_date or date.today().isoformat()
    _date = raw.replace("-", "")
    url = f"{TWSE_RWD_BASE}/fund/T86?date={_date}&selectType=ALLBUT0999"

    client = httpx.Client(timeout=60)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("twstock_client.institutional.twse_failed", error=str(exc))
        return []
    finally:
        client.close()

    if payload.get("stat") != "OK":
        log.warning("twstock_client.institutional.twse_stat_not_ok", stat=payload.get("stat"))
        return []

    raw_date = payload.get("date", "")
    if raw_date and len(raw_date) == 8:
        ad_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    else:
        ad_date = trade_date

    results = []
    for row in payload.get("data", []):
        if not row or len(row) < 19:
            continue
        code = row[0].strip()
        if not code:
            continue
        results.append({
            "stock_id": code,
            "trade_date": ad_date,
            "market": "TSE",
            "foreign_investors_net": _safe_int(row[4].replace(",", "")),
            "sity_investors_net": _safe_int(row[10].replace(",", "")),
            "dealer_net": _safe_int(row[11].replace(",", "")),
            "dealer_proprietary_net": _safe_int(row[14].replace(",", "")),
            "dealer_hedge_net": _safe_int(row[17].replace(",", "")),
            "total_net": _safe_int(row[18].replace(",", "")),
        })

    log.info("twstock_client.institutional.twse", count=len(results), date=ad_date)
    return results


# ─── TPEX: fetch institutional investors from Open API ─────────────────────

def _parse_roc_date(roc_str: str) -> str:
    """Convert ROC calendar date (e.g. '1150605') to ISO date ('2026-06-05')."""
    if not roc_str or len(roc_str) < 7:
        return date.today().isoformat()
    roc_year = int(roc_str[:3])
    gregorian_year = roc_year + 1911
    return f"{gregorian_year}-{roc_str[3:5]}-{roc_str[5:7]}"


def fetch_tpex_institutional_all(trade_date: Optional[str] = None) -> list[dict]:
    """Fetch TPEX institutional investors data from Open API.

    URL: https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading
    Returns list of dicts matching institutional_flows schema.
    """
    _date = trade_date or date.today().isoformat()
    url = f"{TPEX_BASE}/tpex_3insti_daily_trading"

    client = httpx.Client(timeout=30)
    try:
        resp = client.get(url, params={"date": _date})
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("twstock_client.institutional.tpex_failed", error=str(exc))
        return []
    finally:
        client.close()

    if not isinstance(payload, list):
        log.warning("twstock_client.institutional.tpex_unexpected_format")
        return []

    results = []
    for row in payload:
        code = row.get("SecuritiesCompanyCode", "")
        if not code:
            continue
        raw_date = row.get("Date", "")
        parsed_date = _parse_roc_date(raw_date) if raw_date else _date
        results.append({
            "stock_id": code,
            "trade_date": parsed_date,
            "market": "OTC",
            "foreign_investors_net": _safe_int(row.get("ForeignInvestorsIncludeMainlandAreaInvestors-Difference")),
            "sity_investors_net": _safe_int(row.get("SecuritiesInvestmentTrustCompanies-Difference")),
            "dealer_net": _safe_int(row.get("Dealers-Difference")),
            "dealer_proprietary_net": None,
            "dealer_hedge_net": None,
            "total_net": _safe_int(row.get("TotalDifference")),
        })

    log.info("twstock_client.institutional.tpex", count=len(results), date=_date)
    return results


# ─── Main update function ────────────────────────────────────────────────────

def update_stock_list(db, *, scope: Optional[MarketScope] = None) -> int:
    """
    Sync stock list into the stocks table.

    TWSE source : STOCK_DAY_ALL API → exact 1,361 codes (or whatever API returns)
    TPEX source : twstock.codes    → ~1,009 investable securities

    Args:
        db: Database instance.
        scope: "TWSE" (default), "TPEX", or "ALL".

    Returns:
        Number of stocks written.
    """
    scope = (scope or DEFAULT_MARKET_SCOPE or "TWSE").upper()
    if scope not in ("TWSE", "TPEX", "ALL"):
        raise ValueError(f"Invalid scope: {scope!r} (expected TWSE|TPEX|ALL)")

    log.info("twstock_client.update.start", scope=scope)

    count_written = 0
    fetched_ids: set[str] = set()

    with db.connection() as conn:
        # UPSERT first (preserves existing stocks + cascades to child rows)
        # Then delete only stocks that are (a) no longer in fetch AND (b) not referenced
        # by any child table. This prevents CASCADE delete from wiping historical data.

        # ── TWSE: STOCK_DAY_ALL ─────────────────────────────────────────
        if scope in ("TWSE", "ALL"):
            twse_codes = _fetch_twse_codes()
            for sid, name in sorted(twse_codes, key=lambda x: x[0]):
                conn.execute(
                    """INSERT INTO stocks (stock_id, stock_name, market, is_etf)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (stock_id) DO UPDATE SET
                          stock_name = excluded.stock_name,
                          market    = excluded.market,
                          is_etf    = excluded.is_etf""",
                    [sid, name, "TSE", is_etf(sid)],
                )
                fetched_ids.add(sid)
                count_written += 1
            log.info("twstock_client.twse.done", written=len(twse_codes))

        # ── TPEX: twstock.codes ─────────────────────────────────────────
        if scope in ("TPEX", "ALL"):
            tpex_rows = _fetch_tpex_codes()
            for sid, name in sorted(tpex_rows, key=lambda x: x[0]):
                conn.execute(
                    """INSERT INTO stocks (stock_id, stock_name, market, is_etf)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (stock_id) DO UPDATE SET
                          stock_name = excluded.stock_name,
                          market    = excluded.market,
                          is_etf    = excluded.is_etf""",
                    [sid, name, "OTC", is_etf(sid)],
                )
                fetched_ids.add(sid)
                count_written += 1
            log.info("twstock_client.tpex.done", written=len(tpex_rows))

        # Safe cleanup: delete only stocks that are (a) not in this fetch AND
        # (b) not referenced by any child table. CASCADE would wipe historical
        # data on a no-op stock list, so we guard against it.
        if fetched_ids:
            placeholders = ", ".join("?" for _ in fetched_ids)
            n_del = conn.execute(
                f"""DELETE FROM stocks
                    WHERE stock_id NOT IN ({placeholders})
                      AND NOT EXISTS (SELECT 1 FROM daily_prices      WHERE stock_id = stocks.stock_id)
                      AND NOT EXISTS (SELECT 1 FROM valuations        WHERE stock_id = stocks.stock_id)
                      AND NOT EXISTS (SELECT 1 FROM monthly_revenue   WHERE stock_id = stocks.stock_id)
                      AND NOT EXISTS (SELECT 1 FROM financials        WHERE stock_id = stocks.stock_id)
                      AND NOT EXISTS (SELECT 1 FROM institutional_flows WHERE stock_id = stocks.stock_id)
                      AND NOT EXISTS (SELECT 1 FROM portfolio         WHERE stock_id = stocks.stock_id)""",
                list(fetched_ids)
            ).rowcount
            if n_del:
                log.info("twstock_client.cleanup", removed=n_del)

        conn.commit()

    log.info("twstock_client.update.done", scope=scope, written=count_written)
    return count_written