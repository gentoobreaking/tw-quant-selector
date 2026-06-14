from __future__ import annotations
from decimal import Decimal
from typing import Any


def compute_ebit(row: dict[str, Any]) -> Optional[Decimal]:
    val = row.get("operating_income")
    return Decimal(str(val)) if val else None


def compute_ev(row: dict[str, Any], market_cap: Optional[Decimal] = None) -> Optional[Decimal]:
    debt_to_equity = row.get("debt_to_equity")
    total_liabilities = row.get("total_liabilities")
    cash = row.get("cash")
    if total_liabilities and cash:
        mc = Decimal(str(market_cap)) if market_cap else Decimal("0")
        return mc + Decimal(str(total_liabilities)) - Decimal(str(cash))
    if debt_to_equity and market_cap:
        equity = market_cap / (Decimal(str(debt_to_equity)) + Decimal("1"))
        debt = equity * Decimal(str(debt_to_equity))
        return market_cap + debt
    return None


def compute_roic(row: dict[str, Any]) -> Optional[Decimal]:
    ebit = compute_ebit(row)
    current_assets = row.get("current_assets")
    current_liabilities = row.get("current_liabilities")
    net_fixed = row.get("net_fixed_assets")

    if ebit is None or ebit == 0:
        return None
    nwc = None
    if current_assets and current_liabilities:
        nwc = Decimal(str(current_assets)) - Decimal(str(current_liabilities))
    capital = Decimal("0")
    if nwc:
        capital += nwc
    if net_fixed:
        capital += Decimal(str(net_fixed))
    if capital <= 0:
        return None
    return (ebit / capital).quantize(Decimal("0.0001"))


def compute_peg(pe_ratio: Optional[Decimal], eps_q: Optional[Decimal], eps_q_last_year: Optional[Decimal]) -> Optional[Decimal]:
    if not pe_ratio or not eps_q or not eps_q_last_year or eps_q_last_year == 0:
        return None
    eps_growth = (eps_q - eps_q_last_year) / abs(eps_q_last_year)
    if eps_growth <= 0:
        return None
    return (pe_ratio / (eps_growth * 100)).quantize(Decimal("0.01"))


def compute_current_ratio(row: dict[str, Any]) -> Optional[Decimal]:
    ca = row.get("current_assets")
    cl = row.get("current_liabilities")
    if ca and cl and Decimal(str(cl)) > 0:
        return (Decimal(str(ca)) / Decimal(str(cl))).quantize(Decimal("0.01"))
    return None


def compute_all_derived(row: dict[str, Any], market_cap: Optional[Decimal] = None,
                        pe_ratio: Optional[Decimal] = None,
                        eps_q: Optional[Decimal] = None,
                        eps_q_last_year: Optional[Decimal] = None,
                        ) -> dict[str, Any]:
    return {
        "ebit": compute_ebit(row),
        "enterprise_value": compute_ev(row, market_cap),
        "roic": compute_roic(row),
        "peg": compute_peg(pe_ratio, eps_q, eps_q_last_year),
        "current_ratio": compute_current_ratio(row),
    }


def update_derived_financials(db):
    rows = db.execute("SELECT * FROM financials").fetchall()
    col_rows = db.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name = 'financials' ORDER BY ordinal_position"""
    ).fetchall()
    col_names = [r[0] for r in col_rows]
    updated = 0
    for row in rows:
        row_dict = dict(zip(col_names, row))
        stock_id = row_dict["stock_id"]
        year_quarter = row_dict["year_quarter"]
        mc_row = db.execute(
            """SELECT market_cap FROM valuations WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1""",
            [stock_id],
        ).fetchone()
        market_cap = Decimal(str(mc_row[0])) if mc_row and mc_row[0] else None
        pe_row = db.execute(
            """SELECT pe_ratio FROM valuations WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1""",
            [stock_id],
        ).fetchone()
        pe_ratio = Decimal(str(pe_row[0])) if pe_row and pe_row[0] else None
        eps_current = row_dict.get("eps")
        eps_current = Decimal(str(eps_current)) if eps_current else None
        eps_last = db.execute(
            """SELECT eps FROM financials WHERE stock_id = ? AND year_quarter < ? ORDER BY year_quarter DESC LIMIT 4""",
            [stock_id, year_quarter],
        ).fetchone()
        eps_last = Decimal(str(eps_last[0])) if eps_last and eps_last[0] else None

        derived = compute_all_derived(row_dict, market_cap, pe_ratio, eps_current, eps_last)
        ebit = derived["ebit"]
        ev = derived["enterprise_value"]
        roic = derived["roic"]
        peg = derived["peg"]
        cr = derived["current_ratio"]

        db.execute(
            """UPDATE financials SET ebit = ?, enterprise_value = ?, roic = ?,
               peg = ?, current_ratio = ? WHERE stock_id = ? AND year_quarter = ?""",
            [ebit, ev, roic, peg, cr, stock_id, year_quarter],
        )
        updated += 1
    return updated
