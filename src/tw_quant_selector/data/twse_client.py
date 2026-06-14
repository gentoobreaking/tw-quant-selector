from __future__ import annotations
import os
import re
import warnings
import httpx
import structlog
from datetime import date
from typing import Literal

MarketScope = Literal["TWSE", "TPEX", "ALL"]

warnings.filterwarnings("ignore", message=".*SSL.*", module="tw_quant_selector.data.twse_client")

log = structlog.get_logger()

TWSE_BASE = os.getenv("TWSE_BASE_URL", "https://openapi.twse.com.tw/v1")
TPEX_BASE = os.getenv("TPEX_BASE_URL", "https://www.tpex.org.tw/openapi/v1")
DEFAULT_MARKET_SCOPE = os.getenv("STOCK_MARKET_SCOPE", "TWSE").upper()


class TWSEClient:
    def __init__(self):
        self._client = httpx.Client(timeout=30)

    def get_stock_list(self) -> list[dict]:
        resp = self._client.get(f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL")
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._client.close()


class TPEXClient:
    def __init__(self):
        self._client = httpx.Client(timeout=30, verify=False)

    def get_stock_list(self) -> list[dict]:
        resp = self._client.get(f"{TPEX_BASE}/tpex_mainboard_daily_close_quotes")
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._client.close()


ETF_CODE_PATTERN = re.compile(r"^00\d{3,4}$")


def _is_etf_stock_id(sid: str) -> bool:
    return bool(ETF_CODE_PATTERN.match(sid))


def update_stock_list(
    db,
    twse: Optional[TWSEClient] = None,
    tpex: Optional[TPEXClient] = None,
    *,  # keyword-only after this
    scope: Optional[MarketScope] = None,
):
    """
    Sync stock list from TWSE / TPEX into the stocks table.

    Args:
        db: Database instance
        twse: TWSE client (default TWSEClient())
        tpex: TPEX client (default TPEXClient())
        scope: Which market(s) to sync. Defaults to env var STOCK_MARKET_SCOPE, or "TWSE".
               "TWSE"   — 上市股票 only
               "TPEX"   — 上櫃股票 only
               "ALL"    — both TWSE and TPEX
    """
    scope = (scope or DEFAULT_MARKET_SCOPE or "TWSE").upper()
    if scope not in ("TWSE", "TPEX", "ALL"):
        raise ValueError(f"Invalid STOCK_MARKET_SCOPE: {scope!r} (expected TWSE|TPEX|ALL)")

    twse = twse or TWSEClient()
    tpex = tpex or TPEXClient()

    count = 0
    markets: list[tuple[str, object, str]] = []
    if scope in ("TWSE", "ALL"):
        markets.append(("TWSE", twse, "TSE"))
    if scope in ("TPEX", "ALL"):
        markets.append(("TPEX", tpex, "OTC"))

    with db.connection() as conn:
        for source, client, market in markets:
            try:
                rows = client.get_stock_list()
                for r in rows:
                    sid = str(
                        r.get("Code") or r.get("SecuritiesCompanyCode") or r.get("code", "")
                    )
                    name = (
                        str(r.get("Name") or r.get("CompanyName") or r.get("name", "")) or sid
                    )
                    is_etf = _is_etf_stock_id(sid)
                    conn.execute(
                        """INSERT INTO stocks (stock_id, stock_name, market, is_etf)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT (stock_id) DO UPDATE SET
                              stock_name = excluded.stock_name,
                              market    = excluded.market,
                              is_etf    = excluded.is_etf""",
                        [sid, name, market, is_etf],
                    )
                count += len(rows)
                etf_n = sum(
                    1
                    for r in rows
                    if _is_etf_stock_id(
                        str(r.get("Code") or r.get("SecuritiesCompanyCode") or "")
                    )
                )
                log.info(
                    "stock_list.updated",
                    market=market,
                    scope=scope,
                    fetched=len(rows),
                    etfs=etf_n,
                )
                conn.commit()
            except Exception as e:
                log.error("stock_list.failed", market=market, scope=scope, error=str(e))

    if scope in ("TWSE", "ALL"):
        twse.close()
    if scope in ("TPEX", "ALL"):
        tpex.close()

    log.info("stock_list.done", scope=scope, total=count)
    return count
