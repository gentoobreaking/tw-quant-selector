from __future__ import annotations
from datetime import date, datetime
from fastapi import HTTPException
from typing import Optional
import re


STOCK_ID_PATTERN = re.compile(r'^\d{4}(\.(TW|TWO))?$')


def validate_date_format(date_str: str, field_name: str = "date") -> date:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format: '{date_str}'. Use YYYY-MM-DD"
        )


def validate_stock_id(stock_id: str) -> str:
    if not STOCK_ID_PATTERN.match(stock_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stock_id format: '{stock_id}'. Use 4 digits (e.g. 2330) or 4 digits + .TW/.TWO (e.g. 2330.TW)"
        )
    return stock_id


def normalize_stock_id(stock_id: str) -> str:
    return stock_id.split(".")[0] if "." in stock_id else stock_id


def validate_date_range(
    start_date: date,
    end_date: Optional[date] = None,
) -> tuple[date, date]:
    if end_date is None:
        end_date = date.today()

    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({start_date}) cannot be after end_date ({end_date})"
        )

    if (end_date - start_date).days > 365:
        raise HTTPException(
            status_code=400,
            detail=f"Date range too large: {(end_date - start_date).days} days. Maximum 365 days."
        )

    return start_date, end_date
