from __future__ import annotations
from datetime import date
from decimal import Decimal
from typing import Any
import pandas as pd
import structlog
from sqlalchemy import text

log = structlog.get_logger()


def calc_adj_factor(close_before: float, close_after: float) -> float:
    if close_after == 0 or pd.isna(close_after) or pd.isna(close_before):
        return 1.0
    return close_before / close_after


def apply_back_adjustment(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices.sort_values("trade_date", ascending=False).copy()
    adj_factor = 1.0
    factors = []
    for _, row in df.iterrows():
        raw_factor = row.get("adj_factor_raw")
        if raw_factor is not None and raw_factor != 0:
            adj_factor *= raw_factor
        factors.append(adj_factor)
    df["adj_factor"] = factors[::-1]
    df = df.sort_values("trade_date")
    df["adj_close"] = df["close"] * df["adj_factor"]
    return df


def compute_dividend_adjustments(
    db, stock_id: str, dividend_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = pd.DataFrame(
            conn.execute(text("""SELECT trade_date, close FROM daily_prices
               WHERE stock_id = :stock_id ORDER BY trade_date"""),
                {"stock_id": stock_id}).fetchall(),
            columns=["trade_date", "close"])

    if rows.empty:
        return []

    adjustments = []
    for ev in sorted(dividend_events, key=lambda x: x.get("date", "")):
        ev_date = ev.get("date", ev.get("CashExDividendDate", ""))
        if not ev_date:
            continue
        before = rows[rows["trade_date"] < pd.Timestamp(ev_date)]
        after = rows[rows["trade_date"] >= pd.Timestamp(ev_date)]
        if before.empty or after.empty:
            continue
        close_before = float(before.iloc[-1]["close"])
        close_after = float(after.iloc[0]["close"])
        factor = calc_adj_factor(close_before, close_after)
        adjustments.append({"date": ev_date, "adj_factor": factor, "stock_id": stock_id})

    return adjustments


def apply_all_adjustments(db):
    with db.connection() as conn:
        stock_ids = conn.execute("SELECT stock_id FROM stocks").fetchall()
        total = 0
        for (sid,) in stock_ids:
            df = pd.DataFrame(
                conn.execute(text("""SELECT trade_date, close, adj_factor FROM daily_prices
                   WHERE stock_id = :sid ORDER BY trade_date"""),
                    {"sid": sid}).fetchall(),
                columns=["trade_date", "close", "adj_factor"])
            if df.empty:
                continue
            cur_factor = Decimal("1.0")
            adj_col = []
            for _, row in df.iterrows():
                af = row["adj_factor"]
                if af is not None and not pd.isna(af) and af != 0:
                    cur_factor *= Decimal(str(af))
                adj_col.append(cur_factor)
            for i in range(len(df)):
                conn.execute(
                    "UPDATE daily_prices SET adj_factor = ?, adj_close = ROUND(close * ?, 4) WHERE stock_id = ? AND trade_date = ?",
                    [float(adj_col[i]), float(adj_col[i]), sid, df.iloc[i]["trade_date"].date()],
                )
            total += len(df)
        conn.commit()
        log.info("adjustment.completed", stocks=len(stock_ids), rows=total)
        return total
