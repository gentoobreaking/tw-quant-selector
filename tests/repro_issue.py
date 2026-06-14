
import pandas as pd
import numpy as np
from tw_quant_selector.data.ingestion import _pivot_balance_sheet, BALANCE_SHEET_TYPE_MAP

def test_pivot_partial_balance_sheet():
    # Simulate case where 'equity' is missing from raw data
    rows = [
        {"stock_id": "2330", "date": "2023-03-31", "type": "Liabilities", "value": 1000},
        {"stock_id": "2330", "date": "2023-03-31", "type": "TotalAssets", "value": 3000},
    ]
    bs_df = _pivot_balance_sheet(rows)
    print("Columns in bs_df:", bs_df.columns.tolist())
    
    expected_selection = ["stock_id", "date", "equity", "liabilities", "total_assets"]
    try:
        subset = bs_df[expected_selection]
        print("Selection successful")
    except KeyError as e:
        print(f"Caught expected error: {e}")

if __name__ == "__main__":
    test_pivot_partial_balance_sheet()
