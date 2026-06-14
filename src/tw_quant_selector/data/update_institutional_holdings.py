from __future__ import annotations
from typing import Optional, Any, Union
from datetime import date, timedelta
import structlog

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.ingestion import _upsert

log = structlog.get_logger()

HOLDING_COLUMNS = {
    "date": "snapshot_date",
    "stock_id": "stock_id",
    "ForeignInvestor": "foreign_holding_pct",
    "SITI": "trust_holding_pct",
}

MAX_STOCKS_PER_RUN = 200


def fetch_holdings(client: FinMindClient, stock_id: str, snapshot_date: str) -> list[dict]:
    raw = client.get_shareholding(stock_id, snapshot_date, snapshot_date)
    if not raw:
        return []

    rows = []
    for r in raw:
        row = {}
        for k, v in r.items():
            target = HOLDING_COLUMNS.get(k)
            if target:
                row[target] = v
        if not row:
            continue
        row.setdefault("snapshot_date", snapshot_date)
        row.setdefault("stock_id", stock_id)
        foreign_pct = row.get("foreign_holding_pct")
        trust_pct = row.get("trust_holding_pct")
        if foreign_pct is not None and trust_pct is not None:
            raw_total = foreign_pct + trust_pct
        else:
            raw_total = None
        row["dealer_holding_pct"] = round(100.0 - raw_total, 4) if raw_total is not None else None
        row["total_inst_pct"] = round(foreign_pct + trust_pct + (row.get("dealer_holding_pct") or 0), 4) if foreign_pct is not None else None
        row["data_source"] = "finmind"
        rows.append(row)
    return rows


def save_holdings(db: Database, rows: list[dict]) -> int:
    if not rows:
        return 0
    with db.connection() as conn:
        n = _upsert(conn, "institutional_holdings", rows, ["stock_id", "snapshot_date"])
        conn.commit()
    return n


def run_holdings_update(
    db: Database,
    client: FinMindClient,
    snapshot_date: Optional[date] = None,
) -> int:
    if snapshot_date is None:
        latest = db.execute(
            "SELECT MAX(trade_date) FROM institutional_flows"
        ).fetchone()
        snapshot_date = (latest[0] if latest and latest[0] else date.today())

    snapshot_str = snapshot_date.isoformat() if isinstance(snapshot_date, date) else snapshot_date

    stocks = db.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
    all_ids = [r[0] for r in stocks]

    existing = set(
        r[0] for r in db.execute(
            "SELECT DISTINCT stock_id FROM institutional_holdings WHERE snapshot_date = ?",
            [snapshot_str],
        ).fetchall()
    )

    pending = [s for s in all_ids if s not in existing]
    batch = pending[:MAX_STOCKS_PER_RUN]

    log.info("holdings.update.start", total=len(all_ids), pending=len(pending), batch=len(batch), date=snapshot_str)

    total = 0
    errors = 0
    for sid in batch:
        if client.is_banned():
            log.warning("holdings.update.banned_abort", processed=total + errors, remaining=len(batch) - total - errors)
            break
        try:
            rows = fetch_holdings(client, sid, snapshot_str)
            if rows:
                total += save_holdings(db, rows)
        except FinMindRateLimitError:
            log.warning("holdings.update.rate_limited_abort", stock_id=sid, processed=total + errors)
            break
        except Exception as exc:
            errors += 1
            log.warning("holdings.update.stock_failed", stock_id=sid, error=str(exc))

    log.info("holdings.update.done", upserted=total, errors=errors, date=snapshot_str)
    return total
