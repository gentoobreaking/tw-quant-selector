from __future__ import annotations
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
import structlog

log = structlog.get_logger()


@dataclass
class RealtimeValuationResult:
    stock_id: str
    current_price: Optional[Decimal]
    pe_rt: Optional[Decimal]
    pb_rt: Optional[Decimal]
    yield_rt: Optional[Decimal]
    ttm_eps: Optional[Decimal]
    bvps: Optional[Decimal]
    data_as_of: Optional[date]
    pe_detail: Optional[str] = None
    pb_detail: Optional[str] = None


def _sum_eps_ttm(
    db, stock_id: str, as_of_date: date,
) -> tuple[Optional[Decimal], Optional[date]]:
    rows = db.execute(
        """SELECT eps, announcement_date FROM financials
           WHERE stock_id = ? AND announcement_date IS NOT NULL AND announcement_date <= ?
           ORDER BY year_quarter DESC LIMIT 4""",
        [stock_id, as_of_date],
    ).fetchall()
    if not rows:
        return None, None

    eps_values: list[Decimal] = []
    latest_ann: Optional[date] = None
    for r in rows:
        eps_val = r[0]
        ann_date = r[1]
        if isinstance(ann_date, str):
            ann_date = date.fromisoformat(ann_date)
        if eps_val is not None:
            eps_values.append(Decimal(str(eps_val)))
        if ann_date and (latest_ann is None or ann_date > latest_ann):
            latest_ann = ann_date

    if not eps_values:
        return None, latest_ann
    return sum(eps_values, Decimal("0")), latest_ann


def _get_bvps(
    db, stock_id: str, as_of_date: date,
) -> Optional[Decimal]:
    row = db.execute(
        """SELECT total_assets, total_liabilities, year_quarter FROM financials
           WHERE stock_id = ? AND announcement_date IS NOT NULL AND announcement_date <= ?
           ORDER BY year_quarter DESC LIMIT 1""",
        [stock_id, as_of_date],
    ).fetchone()
    if not row:
        return None
    total_assets = row[0]
    total_liabilities = row[1]
    if total_assets is None or total_liabilities is None:
        return None
    book_equity = Decimal(str(total_assets)) - Decimal(str(total_liabilities))
    if book_equity <= 0:
        return None

    shares_row = db.execute(
        """SELECT v.market_cap, dp.close
           FROM valuations v
           JOIN daily_prices dp ON dp.stock_id = v.stock_id AND dp.trade_date = v.trade_date
           WHERE v.stock_id = ? AND v.market_cap IS NOT NULL AND dp.close IS NOT NULL AND dp.close > 0
           ORDER BY v.trade_date DESC LIMIT 1""",
        [stock_id],
    ).fetchone()
    if not shares_row:
        return None
    market_cap = Decimal(str(shares_row[0]))
    close_price = Decimal(str(shares_row[1]))
    shares = market_cap / close_price
    if shares <= 0:
        return None
    return (book_equity / shares).quantize(Decimal("0.01"))


def _get_annual_dividend(
    db, stock_id: str, as_of_date: date,
) -> Optional[Decimal]:
    row = db.execute(
        """SELECT dividend_yield FROM valuations
           WHERE stock_id = ? AND trade_date <= ? AND dividend_yield IS NOT NULL
           ORDER BY trade_date DESC LIMIT 1""",
        [stock_id, as_of_date],
    ).fetchone()
    if not row:
        return None
    dy = Decimal(str(row[0]))
    price_row = db.execute(
        """SELECT close FROM daily_prices
           WHERE stock_id = ? AND trade_date <= ?
           ORDER BY trade_date DESC LIMIT 1""",
        [stock_id, as_of_date],
    ).fetchone()
    if not price_row or price_row[0] is None:
        return None
    last_close = Decimal(str(price_row[0]))
    return (dy * last_close).quantize(Decimal("0.01"))


def compute_realtime_valuation(
    db,
    stock_id: str,
    current_price: Decimal | Optional[float],
    as_of_datetime: Optional[datetime] = None,
) -> RealtimeValuationResult:
    if current_price is None:
        return RealtimeValuationResult(
            stock_id=stock_id, current_price=None,
            pe_rt=None, pb_rt=None, yield_rt=None,
            ttm_eps=None, bvps=None, data_as_of=None,
        )

    price = Decimal(str(current_price))
    as_of_date = (as_of_datetime or datetime.now()).date()

    try:
        eps_ttm, latest_ann = _sum_eps_ttm(db, stock_id, as_of_date)
        bvps = _get_bvps(db, stock_id, as_of_date)
        annual_div = _get_annual_dividend(db, stock_id, as_of_date)
    except Exception as exc:
        log.warning("rtv.compute_failed", stock_id=stock_id, error=str(exc))
        eps_ttm = latest_ann = bvps = annual_div = None

    pe_rt: Optional[Decimal] = None
    pe_detail: Optional[str] = None
    if eps_ttm is not None and eps_ttm > 0:
        pe_val = (price / eps_ttm).quantize(Decimal("0.01"))
        if pe_val > 200:
            pe_rt = None
            pe_detail = ">200"
        else:
            pe_rt = pe_val
    elif eps_ttm is not None and eps_ttm <= 0:
        pe_detail = "虧損"

    pb_rt: Optional[Decimal] = None
    pb_detail: Optional[str] = None
    if bvps is not None and bvps > 0:
        pb_val = (price / bvps).quantize(Decimal("0.01"))
        pb_rt = pb_val
    elif bvps is not None and bvps <= 0:
        pb_detail = "淨值為負"

    yield_rt: Optional[Decimal] = None
    if annual_div is not None and price > 0:
        yield_rt = (annual_div / price).quantize(Decimal("0.0001"))

    return RealtimeValuationResult(
        stock_id=stock_id,
        current_price=price,
        pe_rt=pe_rt,
        pb_rt=pb_rt,
        yield_rt=yield_rt,
        ttm_eps=eps_ttm,
        bvps=bvps,
        data_as_of=latest_ann or as_of_date,
        pe_detail=pe_detail,
        pb_detail=pb_detail,
    )
