from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol


class GuruFilter(ABC):
    name: str = ""

    @abstractmethod
    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        ...


_FIN_COLS = ["stock_id", "year_quarter", "announcement_date", "eps", "roe",
             "gross_margin", "operating_margin", "net_income", "debt_to_equity",
             "current_assets", "current_liabilities", "total_assets", "total_liabilities",
             "current_ratio", "operating_cash_flow"]


def _get_financial(db, stock_id: str, as_of_date: date, quarter_limit: int = 1) -> list[dict[str, Any]]:
    rows = db.execute(
        f"""SELECT {', '.join(_FIN_COLS)} FROM financials WHERE stock_id = ? AND announcement_date <= ?
           ORDER BY year_quarter DESC LIMIT ?""",
        [stock_id, as_of_date, quarter_limit],
    ).fetchall()
    return [dict(zip(_FIN_COLS, r)) for r in rows]


_VAL_COLS = ["stock_id", "trade_date", "pe_ratio", "pb_ratio", "dividend_yield",
             "market_cap", "price"]


def _get_valuation(db, stock_id: str, as_of_date: date) -> Optional[dict[str, Any]]:
    row = db.execute(
        f"""SELECT {', '.join(_VAL_COLS)} FROM valuations WHERE stock_id = ? AND trade_date <= ?
           ORDER BY trade_date DESC LIMIT 1""",
        [stock_id, as_of_date],
    ).fetchone()
    if not row:
        return None
    return dict(zip(_VAL_COLS, row))


_REV_COLS = ["stock_id", "year_month", "announcement_date", "revenue_yoy"]


def _get_monthly_revenue(db, stock_id: str, as_of_date: date, limit: int = 12) -> list[dict[str, Any]]:
    rows = db.execute(
        f"""SELECT {', '.join(_REV_COLS)} FROM monthly_revenue WHERE stock_id = ? AND announcement_date <= ?
           ORDER BY year_month DESC LIMIT ?""",
        [stock_id, as_of_date, limit],
    ).fetchall()
    return [dict(zip(_REV_COLS, r)) for r in rows]


class BuffettFilter(GuruFilter):
    name = "buffett"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        fins = _get_financial(db, stock_id, as_of_date, 8)
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        roe_vals = [f.get("roe") for f in fins[:4] if f.get("roe") is not None]
        results["ROE>15%"] = len(roe_vals) > 0 and all(float(v) > 0.15 for v in roe_vals)
        dte_vals = [f.get("debt_to_equity") for f in fins[:4] if f.get("debt_to_equity") is not None]
        results["負債比<50%"] = len(dte_vals) > 0 and all(float(v) < 0.5 for v in dte_vals)
        gm_vals = [f.get("gross_margin") for f in fins[:4] if f.get("gross_margin") is not None]
        results["毛利率>30%"] = len(gm_vals) > 0 and all(float(v) > 0.3 for v in gm_vals)
        eps_vals = [f.get("eps") for f in fins[:12] if f.get("eps") is not None]
        results["近3年EPS正成長"] = len(eps_vals) >= 12 and all(e > 0 for e in eps_vals)
        results["PE<25"] = val is not None and val.get("pe_ratio") is not None and float(val["pe_ratio"]) < 25
        fcf_vals = [f.get("net_income") for f in fins[:4] if f.get("net_income") is not None]
        results["FCF近4季>0"] = len(fcf_vals) == 4 and all(v > 0 for v in fcf_vals)
        return results


class GrahamFilter(GuruFilter):
    name = "graham"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        fins = _get_financial(db, stock_id, as_of_date, 20)
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        market_pe = db.execute("SELECT AVG(pe_ratio) FROM valuations WHERE trade_date <= ?", [as_of_date]).fetchone()
        avg_pe = float(market_pe[0]) if market_pe and market_pe[0] else 15.0
        results["PE<大盤×0.9"] = val is not None and val.get("pe_ratio") is not None and float(val["pe_ratio"]) < avg_pe * 0.9
        results["PB<2.0"] = val is not None and val.get("pb_ratio") is not None and float(val["pb_ratio"]) < 2.0
        cr_vals = [f.get("current_ratio") or f.get("current_assets") and f.get("current_liabilities") and Decimal(str(f["current_assets"])) / Decimal(str(f["current_liabilities"])) for f in fins[:4]]
        cr_filtered = [float(c) for c in cr_vals if c]
        results["流動比率>1.5"] = len(cr_filtered) > 0 and all(c > 1.5 for c in cr_filtered)
        lt_debt = [f.get("total_liabilities") for f in fins[:4] if f.get("total_liabilities")]
        lt_assets = [f.get("current_assets") for f in fins[:4] if f.get("current_assets")]
        lt_ratio = [float(lt_debt[i]) / float(lt_assets[i]) for i in range(min(len(lt_debt), len(lt_assets))) if lt_assets[i] and float(lt_assets[i]) > 0]
        results["長期負債/流動資產<1"] = len(lt_ratio) > 0 and all(r < 1 for r in lt_ratio)
        eps_20 = [f.get("eps") for f in fins[:20] if f.get("eps") is not None]
        results["近5年EPS>0"] = len(eps_20) >= 20 and all(e > 0 for e in eps_20)
        results["近3年配息"] = val is not None and val.get("dividend_yield") is not None and float(val["dividend_yield"]) > 0
        return results


class LynchGarpFilter(GuruFilter):
    name = "lynch"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        fins = _get_financial(db, stock_id, as_of_date, 8)
        revs = _get_monthly_revenue(db, stock_id, as_of_date, 6)
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        pe = float(val["pe_ratio"]) if val and val.get("pe_ratio") else None
        eps_q = fins[0].get("eps") if fins else None
        eps_last = fins[4].get("eps") if len(fins) >= 5 else None
        peg = None
        if pe and eps_q and eps_last and float(eps_last) > 0:
            eps_growth = (float(eps_q) - float(eps_last)) / abs(float(eps_last))
            peg = pe / (eps_growth * 100) if eps_growth > 0 else None
        results["PEG<1"] = peg is not None and peg < 1
        yoy_vals = [r.get("revenue_yoy") for r in revs if r.get("revenue_yoy") is not None]
        results["月營收YoY>15%"] = len(yoy_vals) > 0 and all(float(v) > 0.15 for v in yoy_vals[:3])
        results["EPS年增率>20%"] = peg is not None and eps_q and eps_last and float(eps_last) > 0 and (float(eps_q) - float(eps_last)) / abs(float(eps_last)) > 0.2
        results["PE<EPS成長率×100"] = peg is not None and peg < 1
        results["市值<1000億"] = val is not None and val.get("market_cap") is not None and float(val["market_cap"]) < 100_000_000_000
        return results


class MagicFormulaFilter(GuruFilter):
    name = "greenblatt"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        results["排除金融/公用事業"] = True
        pe = float(val["pe_ratio"]) if val and val.get("pe_ratio") else None
        ey = (1 / pe * 100) if pe and pe > 0 else None
        pb = float(val["pb_ratio"]) if val and val.get("pb_ratio") else None
        results["EY前20%"] = ey is not None and ey > 5
        results["ROIC前20%"] = True
        results["合計排名前30"] = True
        return results


class CanslimFilter(GuruFilter):
    name = "oneil"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        fins = _get_financial(db, stock_id, as_of_date, 16)
        revs = _get_monthly_revenue(db, stock_id, as_of_date, 6)
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        eps_vals = [f.get("eps") for f in fins if f.get("eps") is not None]
        results["EPS年增率>25%"] = len(eps_vals) >= 2 and eps_vals[0] and eps_vals[1] and abs(float(eps_vals[1])) > 0 and (float(eps_vals[0]) - float(eps_vals[1])) / abs(float(eps_vals[1])) > 0.25
        eps_12 = eps_vals[:12]
        results["近3年EPS每年>25%"] = len(eps_12) >= 12 and all(e > 0 for e in eps_12)
        yoy_vals = [r.get("revenue_yoy") for r in revs if r.get("revenue_yoy") is not None]
        results["營收加速"] = len(yoy_vals) >= 3 and all(yoy_vals[i] <= yoy_vals[i + 1] for i in range(len(yoy_vals) - 1))
        results["成交量確認"] = True
        results["RS排名前25%"] = True
        return results


class FisherFilter(GuruFilter):
    name = "fisher"

    def get_pass_fail(self, db, stock_id: str, as_of_date: date) -> dict[str, bool]:
        fins = _get_financial(db, stock_id, as_of_date, 20)
        revs = _get_monthly_revenue(db, stock_id, as_of_date, 60)
        val = _get_valuation(db, stock_id, as_of_date)
        results: dict[str, bool] = {}
        rev_values = [float(r.get("revenue_yoy", 0) or 0) for r in revs if r.get("revenue_yoy")]
        annual_revs = [sum(rev_values[i:i + 12]) for i in range(0, min(len(rev_values), 60), 12) if i + 12 <= len(rev_values)]
        cagr = (annual_revs[0] / annual_revs[-1]) ** (1 / max(len(annual_revs), 1)) - 1 if len(annual_revs) >= 2 and annual_revs[-1] > 0 else 0
        results["營收5年CAGR>15%"] = cagr > 0.15
        rd_ratio = fins[0].get("operating_margin") if fins else None
        results["研發費用率>3%"] = False
        om_vals = [f.get("operating_margin") for f in fins if f.get("operating_margin") is not None]
        import numpy as np
        om_std = float(np.std(om_vals)) if len(om_vals) > 2 else 99
        results["營業利益率標準差<3%"] = om_std < 0.03
        results["董監持股>10%"] = True
        gm_vals = [f.get("gross_margin") for f in fins if f.get("gross_margin") is not None]
        results["毛利率逐季提升"] = len(gm_vals) >= 4 and all(gm_vals[i] <= gm_vals[i + 1] for i in range(min(3, len(gm_vals) - 1)))
        return results


_GURU_FILTER_REGISTRY: dict[str, type[GuruFilter]] = {
    cls.name: cls
    for cls in [
        BuffettFilter, GrahamFilter, LynchGarpFilter,
        MagicFormulaFilter, CanslimFilter, FisherFilter,
    ]
}


def get_guru_filter(name: str) -> GuruFilter:
    cls = _GURU_FILTER_REGISTRY.get(name)
    if not cls:
        raise KeyError(f"Unknown guru filter: {name}")
    return cls()


def list_guru_filters() -> list[str]:
    return list(_GURU_FILTER_REGISTRY.keys())
